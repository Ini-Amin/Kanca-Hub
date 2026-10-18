#!/usr/bin/env python3
"""Bounty job lifecycle: discover -> scope -> new -> dispatch -> triage -> report.

ONE home for bounty execution. State lives on the LAPTOP
(~/.config/auto-freecf/bounty/<id>/); blu is stateless compute over ssh.

  python3 scripts/bounty_run.py discover [--platform both] [--limit N]
      # public program feeds -> prints candidates; nothing is scanned yet
  python3 scripts/bounty_run.py scope --platform bugcrowd --program URL_OR_SLUG
      # render the brief page, split in-scope / out-of-scope, print them
  python3 scripts/bounty_run.py new --platform bugcrowd --program slug \\
      --in-scope a.com,b.com --out-of-scope c.com --target https://a.com
  python3 scripts/bounty_run.py dispatch <id> [--dry-run]
  python3 scripts/bounty_run.py triage <id>
  python3 scripts/bounty_run.py report <id>     # draft -> Telegram, NO auto-submit
  python3 scripts/bounty_run.py auto [--limit N] [--platform both]
      # discover -> scope -> new -> dispatch -> triage -> report, unattended

Gates (SKILLMAP pre-submit, fail-closed):
  1. SCOPE    : tools only ever receive in-scope targets (enforced in dispatch).
  2. DUPCHECK : cve.db FTS + local findings; platform hacktivity still human.
  3. VM-PoC   : v1 MARKS findings PoC-required; nothing is auto-validated.
  4. CHALLENGER: different model family from the drafter must fail to break it.

Model roles (AGENTS.md, all verified): planner nr/step-5-preview,
drafter Zcode/GLM-5.3 (AFTER planner, sequential), challenger
ocz/step-5-preview-free + THK/deepseek-v4.1-flash:free.
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

HOME = Path.home()
HERE = Path(__file__).resolve().parent
JOBS = Path(os.environ.get("BOUNTY_JOBS", str(HOME / ".config" / "auto-freecf" / "bounty")))
CveDB = HOME / ".config" / "auto-freecf" / "cve.db"
R9 = os.environ.get("BOUNTY_R9_BASE", "http://127.0.0.1:20128/v1")
BOX = os.environ.get("BOUNTY_BOX", "blu")

PLANNER = os.environ.get("BOUNTY_PLANNER", "cbai/deepseek-v4.1-flash")
DRAFTER = os.environ.get("BOUNTY_DRAFTER", "Zcode/GLM-5.3")
CHALLENGERS = os.environ.get(
    "BOUNTY_CHALLENGERS", "THK/deepseek-v4.1-flash:free,ag/gemini-3.8-flash-high").split(",")


def r9_key():
    if os.environ.get("BOUNTY_R9_KEY"):
        return os.environ["BOUNTY_R9_KEY"]
    d = json.load(open(HOME / ".config/opencode/opencode.json"))
    return d["provider"]["9router"]["options"]["apiKey"]


def chat(model, prompt, max_tokens=600, timeout=240):
    """Chat via laptop 9Router. Handles JSON and SSE (ocz/) bodies."""
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        R9 + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + r9_key(), "Content-Type": "application/json"})
    raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    # 9Router quirks: SSE (data:/:) for ocz/*, leading whitespace, and a
    # trailing "data: [DONE]" appended to plain JSON bodies. Normalize first.
    if "\ndata: " in raw[:600] or raw.lstrip().startswith(("data:", ":")):
        out = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data: ") and line != "data: [DONE]":
                try:
                    out.append((json.loads(line[6:])["choices"][0].get("delta") or {}).get("content", ""))
                except Exception:
                    pass
        if out:
            return "".join(out)
    start = raw.find("{")
    if start < 0:
        raise RuntimeError("non-JSON upstream reply: %r" % raw[:160])
    try:
        d, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError:
        raise RuntimeError("non-JSON upstream reply: %r" % raw[:160])
    return (d["choices"][0]["message"] or {}).get("content") or ""


def ssh(box, remote, timeout=600):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", box, remote],
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "")[-6000:], (p.stderr or "")[-1000:]


def job_dir(jid):
    d = JOBS / jid
    if not d.is_dir():
        sys.exit("no such job: %s" % jid)
    return d


# ---------- program discovery ----------
# Bugcrowd's public feed is a plain JSON endpoint; no login, no scraping.
BC_FEED = "https://bugcrowd.com/engagements.json"


def _http_json(url, timeout=40):
    req = urllib.request.Request(url, headers={"accept": "application/json",
                                               "User-Agent": "Mozilla/5.0 bounty_run/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))


def _render_text(platform, url, timeout=220):
    """Rendered body text via bounty_platform.py (camoufox, reuses the saved session)."""
    p = subprocess.run([sys.executable, str(HERE / "bounty_platform.py"),
                        "fetch", "--platform", platform, "--url", url],
                       capture_output=True, text=True, timeout=timeout)
    return (p.stdout or "") if p.returncode == 0 else ""


def discover_bugcrowd(limit=25):
    """Public engagements feed -> candidates. Reward-sorted, no auth needed."""
    d = _http_json(BC_FEED)
    out = []
    for e in d.get("engagements", []):
        if e.get("isPrivate") or e.get("isDemo") or e.get("isBanned"):
            continue
        if e.get("accessStatus") not in (None, "open"):
            continue
        rs = e.get("rewardSummary") or {}
        out.append({
            "platform": "bugcrowd",
            "name": (e.get("name") or "").strip(),
            "brief": "https://bugcrowd.com" + (e.get("briefUrl") or ""),
            "min": rs.get("minReward") or "",
            "max": rs.get("maxReward") or "",
            "industry": e.get("industryName") or "",
            "type": (e.get("productEngagementType") or {}).get("label") or "",
        })

    def money(v):
        digits = "".join(ch for ch in str(v) if ch.isdigit())
        return int(digits or 0)

    out.sort(key=lambda r: money(r["max"]), reverse=True)
    return out[:limit]


H1_NOISE = {
    "filter options", "clear all", "program type", "bounty", "response", "private",
    "severity", "min. bounty ($)", "low", "medium", "high", "critical", "sort",
    "skip to main content >", "learn more about hackerone", "all programs",
}


def discover_hackerone(limit=25):
    """HackerOne discovery via its PUBLIC GraphQL endpoint.

    Replaces the old rendered-directory scrape. That version could only prove
    "the listing is reachable" — it never produced a usable handle, so every
    HackerOne run needed a hand-supplied program URL. The blocker was assumed to
    be an API token: api.hackerone.com/v1/me returns 401 in every auth shape we
    tried (user:token, token:, Bearer, raw Basic), which is why H1 sat dead.

    It was the wrong API. POST https://hackerone.com/graphql answers WITHOUT any
    credential and returns live structured scope:

        { team(handle:"security") {
            structured_scopes(first:10) {
              edges { node { asset_identifier asset_type
                             eligible_for_submission } } } } }

    So reading scope never needed a token; only *submitting* a report does.
    Discovery still needs handles, and the public mirror gives us the full list
    keylessly — that is what `_h1_handles()` reads.
    """
    out = []
    for handle, name, lo, hi in _h1_handles()[:limit]:
        out.append({"platform": "hackerone", "name": name or handle,
                    "brief": f"https://hackerone.com/{handle}",
                    "min": lo, "max": hi, "industry": "", "type": ""})
    return out


H1_MIRROR = ("https://raw.githubusercontent.com/arkadiyt/"
             "bounty-targets-data/main/data/hackerone_data.json")


def _money(v):
    """Coerce a bounty bound to int; the mirror mixes numbers and strings."""
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "").replace("$", "")
    if not s:
        return ""
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return ""


def _h1_handles():
    """(handle, name, min, max) for every public HackerOne program, keyless."""
    try:
        d = _http_json(H1_MIRROR, timeout=60)
    except Exception:
        return []
    if isinstance(d, dict):
        d = list(d.values())
    rows = []
    for x in d:
        h = (x.get("handle") or "").strip()
        if not h or x.get("disabled"):
            continue
        rows.append((h, (x.get("name") or h).strip(),
                     _money(x.get("min_bounty")), _money(x.get("max_bounty"))))
    return rows


def _h1_graphql(query, timeout=45):
    """POST to HackerOne's PUBLIC GraphQL endpoint. No token required.

    This is the correction to a long-standing wrong assumption: we treated
    HackerOne as dead because api.hackerone.com/v1/me returns 401 in every auth
    shape (user:token, token:, Bearer, raw Basic). But the site's own GraphQL
    endpoint answers unauthenticated for public programs, so *reading* scope was
    never blocked — only *submitting* a report needs a token.
    """
    req = urllib.request.Request(
        "https://hackerone.com/graphql",
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"},
        method="POST")
    raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    return json.loads(raw)


def h1_scope(handle, limit=200):
    """Live in-scope assets for one HackerOne program, via public GraphQL.

    Returns (in_scope, out_of_scope) as lists of (asset_identifier, asset_type).
    """
    q = ('{ team(handle:"%s") { structured_scopes(first:%d) { edges { node '
         '{ asset_identifier asset_type eligible_for_submission } } } } }'
         % (handle, limit))
    try:
        d = _h1_graphql(q)
        edges = (d.get("data", {}).get("team", {})
                 .get("structured_scopes", {}).get("edges", []))
    except Exception:
        return [], []
    ins, outs = [], []
    for e in edges:
        n = e.get("node") or {}
        aid = (n.get("asset_identifier") or "").strip()
        if not aid:
            continue
        (ins if n.get("eligible_for_submission") else outs).append(
            (aid, (n.get("asset_type") or "").strip()))
    return ins, outs


def cmd_discover(a):
    rows = []
    if a.platform in ("bugcrowd", "both"):
        rows += discover_bugcrowd(a.limit)
    if a.platform in ("hackerone", "both"):
        rows += discover_hackerone(a.limit)
    if not rows:
        print("no candidates")
        return
    for r in rows:
        print("%-10s %-56s %7s-%-8s %s" %
              (r["platform"], r["name"][:56], r["min"], r["max"], r["brief"]))


# ---------- scope extraction ----------
# Bugcrowd renders one CARD PER TARGET GROUP, not one global in/out section.
# Each card is:  <group name> / "In scope"|"Out of scope" / ... / "Name / Location"
#                / <target lines>
# Okta has 4 such cards (3 in, 1 out); bitso has 2. A parser that assumes a
# single "In scope ... Out of scope" run silently loses whole programs (Okta
# came back with 0 in-scope), so parse the cards instead.
IN_SCOPE = re.compile(r"^\s*in\s*scope\s*$", re.I)
OUT_SCOPE = re.compile(r"^\s*out\s*of\s*scope\s*$", re.I)
NAME_LOC = re.compile(r"^name\s*/\s*location", re.I)
URLISH = re.compile(r"^(https?://|[\w*][\w.*#-]*\.[a-z]{2,})", re.I)


def parse_scope(text):
    """Split a rendered Bugcrowd brief into in-scope / out-of-scope targets.

    Target hostnames appear only inside "Name / Location" tables. The label that
    classifies a table is the closest preceding scope heading, so walk the page
    in order, remember the latest label, and collect table rows under it.

    A target row never contains whitespace (the page prints descriptions as
    separate wrapped lines), which is what keeps prose out of the list.
    """
    ins, outs = [], []
    mode = None          # scope declared by the CURRENT card
    mode_locked = False  # this card's scope label has already been consumed
    in_table = False     # inside a Name / Location table

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if NAME_LOC.match(line):
            in_table = True
            continue

        # A scope label only counts OUTSIDE a table; inside a table a line like
        # "In scope" would be data, not a heading.
        if not in_table:
            if IN_SCOPE.match(line) or OUT_SCOPE.match(line):
                # FIRST label in the card wins. Later "Out of Scope" headings
                # inside the same card are prose subsections describing what
                # testing is forbidden (DoS, automation) — not the card's
                # classification. Taking the LAST label instead flipped Okta's
                # "Okta Workflows" card (declared In scope) to out-of-scope
                # because its target table is printed after a "Testing for
                # Denial of Service ... is STRICTLY out of scope" paragraph.
                if not mode_locked:
                    mode = "in" if IN_SCOPE.match(line) else "out"
                    mode_locked = True
                continue
            continue

        # ---- inside a table ----
        if IN_SCOPE.match(line) or OUT_SCOPE.match(line):
            # the previous card ended; this label opens the next one
            in_table = False
            mode = "in" if IN_SCOPE.match(line) else "out"
            mode_locked = True
            continue
        if not URLISH.match(line) or re.search(r"\s", line) or len(line) > 200:
            continue
        if mode is None:
            continue
        if line.startswith("http"):
            line = line.rstrip("/")
        (ins if mode == "in" else outs).append(line)

    def dedupe(seq):
        out, seen = [], set()
        for x in seq:
            k = x.lower()
            if k not in seen:
                seen.add(k)
                out.append(x)
        return out

    return dedupe(ins), dedupe(outs)


def get_scope(platform, program):
    """(in_scope, out_of_scope, page_text).

    page_text is returned so callers can tell "session expired" (empty page)
    from "program really lists nothing" (page rendered, no target tables).
    """
    url = program
    if not url.startswith("http"):
        url = "https://bugcrowd.com/engagements/" + url.strip("/")
    text = _render_text(platform, url)
    if not text:
        return [], [], ""
    ins, outs = parse_scope(text)
    return ins, outs, text


def cmd_scope(a):
    ins, outs, text = get_scope(a.platform, a.program)
    if not text:
        sys.exit("empty page - session expired? run: bounty_platform.py login --platform %s" % a.platform)
    if not ins:
        print("WARNING: page rendered but no in-scope targets parsed - layout may have changed",
              file=sys.stderr)
    print("IN  (%d): %s" % (len(ins), ", ".join(ins) or "-"))
    print("OUT (%d): %s" % (len(outs), ", ".join(outs) or "-"))
    if a.json:
        print(json.dumps({"in_scope": ins, "out_of_scope": outs}, indent=1))


def create_job(platform, program, in_scope, out_of_scope="", targets=""):
    """Create a job dir + job.json. Refuses to create a job with no in-scope list."""
    ins = [x.strip() for x in in_scope.split(",") if x.strip()] if isinstance(in_scope, str) else list(in_scope)
    outs = ([x.strip() for x in out_of_scope.split(",") if x.strip()]
            if isinstance(out_of_scope, str) else list(out_of_scope))
    tgt = ([x.strip() for x in targets.split(",") if x.strip()]
           if isinstance(targets, str) else list(targets))
    if not ins:
        raise ValueError("refusing job without in-scope list (gate 1)")
    jid = "%s-%s-%d" % (platform, program, int(time.time()) % 100000)
    d = JOBS / jid
    (d / "recon").mkdir(parents=True, exist_ok=True)
    job = {"id": jid, "platform": platform, "program": program,
           "in_scope": ins, "out_of_scope": outs, "targets": tgt,
           "status": "new", "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (d / "job.json").write_text(json.dumps(job, indent=1))
    return jid, job


def cmd_new(a):
    jid, _ = create_job(a.platform, a.program, a.in_scope, a.out_of_scope, a.target)
    print("job", jid)


# ---------- scope enforcement (gate 1, host level) ----------
def _host_of(entry):
    """Normalise a scope entry or URL to a bare lowercase host."""
    e = (entry or "").strip().lower()
    if not e:
        return ""
    if "://" in e:
        e = e.split("://", 1)[1]
    e = e.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    e = e.split("@")[-1].split(":")[0]
    return e.strip(".")


def _matches(host, pattern, include_apex=False):
    """Glob-ish host match.

    'x.com'      -> matches x.com AND *.x.com (a scope entry names an asset,
                    not a single label).
    '*.x.com'    -> matches subdomains only, unless include_apex is set.
                    For DENY lists include_apex=True: '*.efx.x.com' plainly
                    means "the whole efx subtree", so leaving the apex open
                    would be a hole a hunter falls into.
    """
    host, pattern = host.lower().strip("."), pattern.lower().strip(".")
    if not host or not pattern:
        return False
    if pattern.startswith("*."):
        base = pattern[2:]
        if include_apex and host == base:
            return True
        return host.endswith("." + base)
    return host == pattern or host.endswith("." + pattern)


def host_in_scope(host, in_scope, out_of_scope):
    """Fail-closed: deny wins, and anything unmatched is OUT.

    Returns (allowed: bool, reason: str).
    """
    h = _host_of(host)
    if not h:
        return False, "empty host"
    for pat in out_of_scope:
        p = _host_of(pat)
        if p and _matches(h, p, include_apex=True):
            return False, "out-of-scope %s" % pat
    for pat in in_scope:
        p = _host_of(pat)
        if p and _matches(h, p):
            return True, "in-scope %s" % pat
    return False, "not in scope"


# Out-of-scope entries whose leading label also appears in live hostnames are a
# classic trap (sandbox.bitso.com vs sandbox-rfq.bitso.com): not carved out by
# the letter of the rules, but almost certainly not what the program wants
# tested. We surface these instead of silently scanning them.
def scope_warnings(hosts, out_of_scope):
    tokens = set()
    for pat in out_of_scope:
        p = _host_of(pat).lstrip("*.")
        if p:
            tokens.add(p.split(".")[0])
    tokens.discard("")
    out = []
    for h in hosts:
        for t in tokens:
            if len(t) >= 4 and t in h and not _matches(h, t):
                out.append((h, "name contains out-of-scope token %r" % t))
                break
    return out


def filter_hosts(hosts, in_scope, out_of_scope):
    """Split discovered hosts into (allowed, rejected). Never silently drops."""
    allowed, rejected = [], []
    seen = set()
    for h in hosts:
        host = _host_of(h)
        if not host or host in seen:
            continue
        seen.add(host)
        ok, why = host_in_scope(host, in_scope, out_of_scope)
        (allowed if ok else rejected).append((host, why))
    return allowed, rejected


def _scannable(targets, in_scope, out_of_scope):
    """Seed hosts we can actually enumerate/scan.

    Skips app-store URLs, and normalises wildcards: subfinder takes a registrable
    domain (`bitso.com`), never `*.bitso.com`. A wildcard entry therefore
    contributes its base domain as a seed — which is legitimate, because a seed
    is only an enumeration root; gate 1b still filters every host that comes back.
    """
    # Bases of in-scope wildcards are valid enumeration roots even though the
    # apex itself does not strictly match '*.base'.
    wildcard_bases = set()
    for pat in in_scope:
        p = _host_of(pat)
        if p.startswith("*."):
            wildcard_bases.add(p[2:])

    out = []
    for t in targets:
        h = _host_of(t)
        if not h or h in ("apps.apple.com", "play.google.com", "itunes.apple.com"):
            continue
        if h.startswith("*."):
            h = h[2:]
        ok, _ = host_in_scope(h, in_scope, out_of_scope)
        if ok or (h in wildcard_bases and not any(
                _matches(h, _host_of(p), include_apex=True) for p in out_of_scope if _host_of(p))):
            out.append(h)
    return sorted(set(out))


def parse_markers(out):
    """Split marker-delimited stdout into sections: {'LIVE': [...], 'NUCLEI': [...]}.

    Returns None if no marker was found at all - callers use that to detect a
    truncated/lost payload (dropped ssh, timeout) rather than an empty result.
    """
    res, mode = {}, None
    for line in out.splitlines():
        t = line.strip()
        if t.startswith("---") and t.endswith("---") and t[3:-3].strip().isalpha():
            if t[3:-3].strip() == "END":
                break
            mode = t[3:-3].strip()
            res.setdefault(mode, [])
            continue
        if not t:
            continue
        if mode:
            res[mode].append(t)
    return res if res else None


def _as_args(a, default_dry=False):
    """Accept either an argparse Namespace or a bare job-id string.

    A bare string must NOT imply a live scan: callers (and future me) reach for
    cmd_dispatch("job-id") to inspect a job, and silently launching subfinder
    against a third party is exactly the mistake to make impossible. A string
    therefore means dry-run; a real scan needs an explicit Namespace.
    """
    if isinstance(a, str):
        return argparse.Namespace(id=a, dry_run=default_dry, allow_stale_scope=False)
    if not hasattr(a, "dry_run"):
        a.dry_run = default_dry
    if not hasattr(a, "allow_stale_scope"):
        a.allow_stale_scope = False
    return a


def cmd_dispatch(a):
    a = _as_args(a, default_dry=True)
    jid = a.id
    dry = a.dry_run
    d = job_dir(jid)
    job = json.loads((d / "job.json").read_text())
    ins, outs = job["in_scope"], job["out_of_scope"]

    if dry:
        toolcheck = ("export PATH=\"$HOME/go/bin:$HOME/.local/bin:$PATH\"; "
                     "for t in subfinder httpx nuclei opengrep katana; do "
                     "command -v $t >/dev/null && echo -n \"$t \"; done; echo")
        if os.environ.get("BOUNTY_REMOTE"):
            r = subprocess.run(["sh", "-c", toolcheck], capture_output=True, text=True, timeout=120)
            print("box tools:", (r.stdout or "").strip())
        else:
            rc, out, err = ssh(BOX, toolcheck)
            print("blu tools:", out.strip() or ("ERR " + err.strip()))
        seeds = _scannable(job["targets"], ins, outs)
        print("gate 1 seeds in scope:", ", ".join(seeds) or "-")
        for t in job["targets"]:
            h = _host_of(t)
            if h and h not in seeds:
                print("  REJECTED seed:", t, "->", host_in_scope(h, ins, outs)[1])
        return

    # ---- Gate 1a: only in-scope SEEDS are ever enumerated ----
    seeds = _scannable(job["targets"], ins, outs)
    if not seeds:
        sys.exit("gate 1: no in-scope scannable seed targets")

    # ---- Gate 0: refuse to scan on a stale scope snapshot ----
    # A job stores its scope at creation time. If the parser improved (or the
    # program edited its brief) afterwards, the stored exclusion list can be
    # MISSING entries the page actually lists — and every host matching a missing
    # entry is then scanned as if it were in scope. That really happened: this
    # job was created with 7 exclusions while the brief listed 19, so
    # sandbox.bitso.com and quetzal.bitso.com were scanned despite being
    # explicitly out of scope. Re-parse and compare before touching the network.
    if not a.allow_stale_scope:
        try:
            fresh_in, fresh_out, page = get_scope(job["platform"], job["program"])
        except Exception as e:
            sys.exit("gate 0: could not re-verify scope (%s) - refusing to scan" % e)
        if not page:
            sys.exit("gate 0: scope page did not render - refusing to scan blind")
        stored = {_host_of(x) for x in (job["out_of_scope"] or []) if _host_of(x)}
        current = {_host_of(x) for x in fresh_out if _host_of(x)}
        missing = current - stored
        if missing:
            sys.exit("gate 0: stored scope is STALE, %d exclusion(s) missing from job.json: %s\n"
                     "Re-create the job (or refresh out_of_scope) before scanning. "
                     "Override with --allow-stale-scope only if you have verified the "
                     "difference by hand." % (len(missing), ", ".join(sorted(missing)[:10])))
        if len(fresh_in) > len(job["in_scope"]):
            print("gate 0: note - brief lists %d in-scope entries, job has %d"
                  % (len(fresh_in), len(job["in_scope"])))

    # ---- Phase A: passive enumeration only (subfinder sends no traffic to targets) ----
    roots = " ".join(seeds)
    phase_a = ("export PATH=\"$HOME/go/bin:$HOME/.local/bin:$PATH\"; "
               "cd /tmp && rm -rf bj && mkdir bj && cd bj; "
               "for h in %s; do subfinder -silent -d \"$h\" 2>/dev/null; done | sort -u > subs_raw.txt; "
               "echo ---SUBS---; cat subs_raw.txt; echo ---END---" % roots)
    if os.environ.get("BOUNTY_REMOTE"):
        r = subprocess.run(["sh", "-c", phase_a], capture_output=True, text=True, timeout=1500)
        raw = r.stdout or ""
    else:
        # not ssh(): that helper keeps only the last 6000 chars, which would
        # silently truncate a large subdomain list. Phase A output is data.
        _p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", BOX, phase_a],
                            capture_output=True, text=True, timeout=1500)
        raw = _p.stdout or ""
        if _p.returncode:
            print("phase A rc=%d: %s" % (_p.returncode, (_p.stderr or "").strip()[:200]))

    # Split the marker-delimited payload. Anything outside ---SUBS---/---END---
    # is shell noise (subfinder progress, ssh banners) and must not be parsed.
    lines, mode = [], None
    for line in raw.splitlines():
        t = line.strip()
        if t == "---SUBS---":
            mode = "subs"
            continue
        if t == "---END---":
            break
        if mode == "subs" and t:
            lines.append(t)
    hosts = [x for x in lines if re.match(r"^[\w*][\w.*-]*\.[a-z]{2,}$", x, re.I)]
    (d / "recon").mkdir(parents=True, exist_ok=True)
    (d / "recon" / "subs_raw.txt").write_text("\n".join(hosts) + ("\n" if hosts else ""))

    # ---- Gate 1b: filter discovered hosts BEFORE any active tool sees them ----
    allowed, rejected = filter_hosts(hosts, ins, outs)
    warns = scope_warnings([h for h, _ in allowed], outs)
    (d / "recon" / "scope_rejected.txt").write_text(
        "\n".join("%s\t%s" % (h, why) for h, why in rejected) + ("\n" if rejected else ""))
    (d / "recon" / "scope_warnings.txt").write_text(
        "\n".join("%s\t%s" % (h, why) for h, why in warns) + ("\n" if warns else ""))
    print("gate 1: %d discovered, %d in scope, %d rejected" % (len(hosts), len(allowed), len(rejected)))
    for h, why in rejected[:15]:
        print("  REJECTED", h, "->", why)
    if len(rejected) > 15:
        print("  ... %d more in recon/scope_rejected.txt" % (len(rejected) - 15))
    if warns:
        print("  WARNING: in-scope by rule but looks related to out-of-scope assets:")
        for h, why in warns[:10]:
            print("   ", h, "->", why)
    if not allowed:
        job["status"] = "recon-empty"
        (d / "job.json").write_text(json.dumps(job, indent=1))
        sys.exit("gate 1: nothing survived scope filtering")

    # ---- Phase B: active scanning, ONLY on the filtered allow-list ----
    allow_file = "\n".join(h for h, _ in allowed) + "\n"
    # Per-job remote workdir. A shared /tmp/bj let one job's allow-list be read
    # by another job's scan, i.e. scanning hosts never filtered for that job.
    wd = "/tmp/bj-" + re.sub(r"[^A-Za-z0-9_.-]", "_", jid)
    # nuclei has no default deadline: on a slow target it will run indefinitely,
    # the ssh child never exits, and the python-side timeout cannot fire because
    # subprocess.run is blocked waiting on that child. The scan then hangs past
    # its own timeout and writes nothing — observed as a 30-min run that produced
    # zero files. Bound nuclei itself so the pipeline always returns.
    NUCLEI_SECS = int(os.environ.get("BOUNTY_NUCLEI_SECS", "900"))
    phase_b = (("export PATH=\"$HOME/go/bin:$HOME/.local/bin:$PATH\"; mkdir -p %s; cd %s; " % (wd, wd))
               + "httpx -silent -mc 200,201,301,302,403 -timeout 10 < allow.txt 2>/dev/null | head -80 > live.txt; "
               "timeout %d nuclei -silent -l live.txt -severity high,critical "
               "-timeout 10 -retries 1 2>/dev/null | head -60 > nuclei.txt; " % NUCLEI_SECS
               + "echo ---LIVE---; cat live.txt; echo ---NUCLEI---; cat nuclei.txt; echo ---END---")
    out = ""
    try:
        if os.environ.get("BOUNTY_REMOTE"):
            Path(wd).mkdir(parents=True, exist_ok=True); Path(wd, "allow.txt").write_text(allow_file)
            r = subprocess.run(["sh", "-c", phase_b], capture_output=True, text=True, timeout=1800)
            out = r.stdout or ""
        else:
            # ship the allow-list, then scan; single ssh round-trip via stdin
            cmd = ("mkdir -p %s && cd %s && cat > allow.txt && " % (wd, wd) + phase_b)
            p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", BOX, cmd],
                               input=allow_file, capture_output=True, text=True, timeout=1800)
            out = p.stdout or ""
            if p.returncode:
                print("phase B rc=%d: %s" % (p.returncode, (p.stderr or "").strip()[:200]))
    except subprocess.TimeoutExpired:
        # nuclei can outrun the clock; live.txt is already on disk. Pull below.
        print("phase B timed out; pulling whatever reached the remote files")

    res = parse_markers(out)
    if res is None:
        if os.environ.get("BOUNTY_REMOTE"):
            # local mode: the scan wrote straight to /tmp/bj; read the files
            print("phase B stdout had no markers; reading local %s files" % wd)
            res = {}
            for key, fn in (("LIVE", "live.txt"), ("NUCLEI", "nuclei.txt")):
                p = Path(wd) / fn
                res[key] = [l.strip() for l in p.read_text().splitlines() if l.strip()] if p.is_file() else []
        elif True:
            # stdout was lost (dropped connection / timeout) but the remote files
            # are the ground truth - fetch them in a fresh, cheap round-trip.
            print("phase B stdout had no markers; fetching remote live.txt/nuclei.txt")
            _f = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", BOX,
                                 "cd %s && echo ---LIVE---; cat live.txt; " % wd +
                                 "echo ---NUCLEI---; cat nuclei.txt; echo ---END---"],
                                capture_output=True, text=True, timeout=120)
            res = parse_markers(_f.stdout or "")
    if res is None:
        res = {}
    live, nuclei = res.get("LIVE", []), res.get("NUCLEI", [])

    (d / "recon" / "live.txt").write_text("\n".join(live) + ("\n" if live else ""))
    (d / "recon" / "nuclei.txt").write_text("\n".join(nuclei) + ("\n" if nuclei else ""))
    (d / "recon" / "subs.txt").write_text("\n".join(h for h, _ in allowed) + "\n")
    print("live: %d, nuclei: %d" % (len(live), len(nuclei)))
    for x in live[:15]:
        print("  live", x)
    for x in nuclei[:10]:
        print("  FINDING", x)

    job["status"] = "recon-done"
    job["recon_counts"] = {"discovered": len(hosts), "in_scope": len(allowed),
                           "rejected": len(rejected), "live": len(live), "nuclei": len(nuclei)}
    (d / "job.json").write_text(json.dumps(job, indent=1))


def cmd_triage(a):
    jid = getattr(a, "id", a)
    d = job_dir(jid)
    job = json.loads((d / "job.json").read_text())

    def read(name, cap=4000):
        p = d / "recon" / name
        return p.read_text()[-cap:] if p.exists() else ""

    subs = read("subs.txt", 2500)
    live = read("live.txt", 2000)
    nuclei = read("nuclei.txt", 2500)
    warns = read("scope_warnings.txt", 800)
    recon = ("## In-scope hosts\n%s\n\n## Live (httpx)\n%s\n\n## nuclei findings\n%s\n"
             % (subs, live, nuclei))
    if warns:
        recon += "\n## Scope warnings (do NOT test without human OK)\n%s\n" % warns
    counts = job.get("recon_counts") or {}
    # Tell the models how the scope list is actually evaluated, or they re-derive
    # it wrongly. The klaviyo drafter rejected all 80 live hosts as "out of scope"
    # because it read the list as a literal set of hostnames: `klaviyo.com` is an
    # APEX entry, so every *.klaviyo.com host IS in scope. Gate 1 already made
    # this decision in code; handing the models a different rule makes them
    # self-kill correct work on a false premise.
    scope_note = (
        "SCOPE SEMANTICS (already enforced in code — do not re-derive it):\n"
        "- An entry like `example.com` covers the apex AND every subdomain.\n"
        "- `*.example.com` covers subdomains (and, for deny entries, the apex too).\n"
        "- A host is in scope if it matches ANY in-scope entry and NO out-of-scope entry.\n"
        "- Every host in the recon below has ALREADY passed that test. Do not claim a\n"
        "  listed host is out of scope unless an explicit deny entry matches it.\n")
    plan = chat(PLANNER,
                "Bug bounty hunt plan. Program: %s. In scope: %s. Out of scope (NEVER touch): %s.\n"
                "%sRecon counts: %s\n%s\n"
                "List the top 5 checks in order, each with an exact tool command. "
                "Only ever touch the in-scope hosts above."
                % (job["program"], job["in_scope"], job["out_of_scope"], scope_note,
                   counts, recon[:4000]))
    (d / "plan.md").write_text(plan)
    # The drafter can come back empty on a transient upstream hiccup (429/bridge
    # busy). An empty draft then goes to the challengers, which correctly say
    # "no draft supplied" and KILL it — so a transport blip is recorded as a
    # finding that failed review. That is a false negative dressed as a verdict.
    # Retry, and treat an empty draft as an ERROR rather than a draft.
    draft = ""
    last_err = None
    for attempt in range(3):
        try:
            draft = chat(DRAFTER,
                         "Draft ONE candidate finding from this recon (or say NONE). Only use in-scope hosts.\n"
                         "%s\nPlan:\n%s" % (recon[:4000], plan[:2000]), timeout=300)
            if draft.strip():
                break
            last_err = "empty reply"
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, str(e)[:120])
        time.sleep(8 * (attempt + 1))
    if not draft.strip():
        (d / "draft.md").write_text(
            "# DRAFT FAILED (no draft produced)\n\n"
            "The drafter (%s) returned nothing after 3 attempts: %s\n\n"
            "This is an UPSTREAM failure, not a review verdict. No finding was\n"
            "evaluated, so nothing can be concluded about the target. Re-run triage.\n"
            % (DRAFTER, last_err))
        job["status"] = "triage-failed"
        (d / "job.json").write_text(json.dumps(job, indent=1))
        print("triage-failed: drafter produced no content (%s)" % last_err)
        return
    # Gate 4: challenger from a DIFFERENT family must fail to break the draft.
    verdicts = []
    for ch in CHALLENGERS:
        try:
            v = chat(ch, "Challenge this draft finding. Try to prove it is a duplicate, "
                     "out of scope, unexploitable, or a hallucination. Draft:\n%s\n"
                     "Reply VERDICT: KILL:<reason> or VERDICT: SURVIVES:<why>"
                     % draft[:2500], timeout=300)
            verdicts.append("%s -> %s" % (ch, v[:300]))
            time.sleep(5)
        except Exception as e:
            verdicts.append("%s -> ERROR %s" % (ch, type(e).__name__))
    killed = [v for v in verdicts if "KILL" in v.upper()]
    (d / "draft.md").write_text("# DRAFT (needs gates 2+3 + human)\n\n%s\n\n## Challenger verdicts\n%s\n" %
                                (draft, "\n".join(verdicts)))
    job["status"] = "challenged-killed" if killed else "draft"
    (d / "job.json").write_text(json.dumps(job, indent=1))
    print("status:", job["status"])
    print("\n".join(verdicts))


def cmd_report(a):
    jid = getattr(a, "id", a)
    d = job_dir(jid)
    sys.path.insert(0, str(HERE))
    from notify import send
    text = (d / "draft.md").read_text()

    # A null result must not look like a finding. The bitso run produced a draft
    # whose body is literally "NONE" with challenger verdicts attached; sending
    # that as "BOUNTY DRAFT" invites a human to treat a no-op as a lead.
    job = json.loads((d / "job.json").read_text())
    body = text.split("## Challenger verdicts")[0]
    null_result = (job.get("status") == "challenged-killed"
                   or "**NONE.**" in body or body.strip().endswith("NONE."))
    counts = job.get("recon_counts") or {}

    if null_result:
        head = ("BOUNTY NULL RESULT %s — nothing to submit\n"
                "recon: %s discovered, %s in scope, %s live, %s nuclei\n"
                "status: %s\n"
                % (jid, counts.get("discovered", "?"), counts.get("in_scope", "?"),
                   counts.get("live", "?"), counts.get("nuclei", "?"), job.get("status")))
    else:
        head = "BOUNTY DRAFT %s (manual submit only):\n" % jid

    ids = send((head + text)[:3500])
    print("telegram:", ids, "| null_result:", null_result)


def cmd_remote(a):
    """Run a stage ON blu: ship script+job, execute via tunnel models, pull back.

    Models resolve through blu-tunnel (:20128 -> laptop 9Router); artifacts
    return to the laptop, which stays the state owner + Telegram sender.
    """
    d = job_dir(a.id)
    key = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", BOX,
         "python3 -c \"import json;print(json.load(open('.config/opencode/opencode.json'))"
         "['provider']['9router']['options']['apiKey'])\""],
        capture_output=True, text=True, timeout=60).stdout.strip()
    if len(key) < 20:
        sys.exit("cannot read router key on %s" % BOX)
    subprocess.run(["scp", "-q", str(Path(__file__).resolve()),
                    "%s:/tmp/bounty_run.py" % BOX], check=True, timeout=120)
    subprocess.run(["ssh", "-o", "BatchMode=yes", BOX, "mkdir -p /tmp/bounty"],
                   check=True, timeout=60)
    # no trailing slash: scp -r DIR remote:/tmp/bounty/ creates /tmp/bounty/<id>/
    subprocess.run(["scp", "-q", "-r", str(d), "%s:/tmp/bounty/" % BOX],
                   check=True, timeout=300)
    env = "BOUNTY_R9_BASE=http://127.0.0.1:20128/v1 BOUNTY_R9_KEY=%s BOUNTY_JOBS=/tmp/bounty BOUNTY_REMOTE=1" % key
    r = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", BOX,
         "%s python3 /tmp/bounty_run.py %s %s" % (env, a.stage, a.id)],
        capture_output=True, text=True, timeout=2400)
    print(r.stdout[-2000:] if r.stdout else "")
    print(r.stderr[-500:] if r.returncode else "", file=sys.stderr)
    if r.returncode:
        sys.exit("remote stage failed rc=%d" % r.returncode)
    # pull back via tar (exact paths, no scp -r nesting surprises)
    r2 = subprocess.run(
        "ssh -o BatchMode=yes %s 'tar -C /tmp/bounty -cf - %s' | tar -C %s -xf -"
        % (BOX, a.id, JOBS), shell=True, capture_output=True, text=True, timeout=300)
    if r2.returncode:
        sys.exit("pull-back failed: %s" % r2.stderr.strip()[:200])
    print("pulled back", a.id)


def _ns(**kw):
    """Namespace for calling cmd_* internally."""
    return argparse.Namespace(**kw)


def _worked_programs(platform):
    """Program slugs already attempted (job created OR skipped), so auto moves on.

    Without the attempted-log, auto re-picks the same dead program (no-seed /
    no-scope) on every run and never reaches the rest of the candidate list.
    """
    done = set()
    if not JOBS.is_dir():
        return done
    for j in JOBS.iterdir():
        p = j / "job.json"
        if not p.is_file():
            continue
        try:
            job = json.loads(p.read_text())
        except Exception:
            continue
        if job.get("platform") == platform and job.get("program"):
            done.add(str(job["program"]).strip("/").lower())
    log = JOBS / (".attempted-" + platform)
    if log.is_file():
        done |= {l.strip().lower() for l in log.read_text().splitlines() if l.strip()}
    return done


def _mark_attempted(platform, slug):
    JOBS.mkdir(parents=True, exist_ok=True)
    log = JOBS / (".attempted-" + platform)
    have = {l.strip().lower() for l in log.read_text().splitlines() if l.strip()} if log.is_file() else set()
    if slug not in have:
        with log.open("a") as f:
            f.write(slug + "\n")


def _stage(fn, jid, label):
    """Run one stage, absorbing SystemExit so one bad program cannot kill the run."""
    try:
        fn(jid)
        return True, ""
    except SystemExit as e:
        msg = str(e.code) if e.code not in (None, 0) else "exit"
        print("  [%s] stopped: %s" % (label, msg))
        return False, msg
    except Exception as e:
        print("  [%s] ERROR %s: %s" % (label, type(e).__name__, str(e)[:200]))
        return False, "%s: %s" % (type(e).__name__, str(e)[:200])


def cmd_auto(a):
    """Unattended pipeline: discover -> scope -> new -> dispatch -> triage -> report.

    Each program is isolated: a gate rejection, an empty scope page, or a model
    error aborts only that program. One Telegram digest at the end.
    """
    sys.path.insert(0, str(HERE))
    from notify import send

    worked = _worked_programs(a.platform)
    cands = discover_bugcrowd(a.limit if a.platform == "bugcrowd" else max(a.limit, 40))
    if a.min_reward:
        def money(v):
            dg = "".join(ch for ch in str(v) if ch.isdigit())
            return int(dg or 0)
        cands = [c for c in cands if money(c["max"]) >= a.min_reward]

    picked, skipped = [], []
    for c in cands:
        slug = c["brief"].rstrip("/").split("/")[-1].lower()
        if slug in worked:
            skipped.append(slug)
            continue
        if len(picked) >= a.max_programs:
            break
        picked.append((slug, c))

    print("auto: %d candidates, %d already worked, %d to run"
          % (len(cands), len(skipped), len(picked)))
    if not picked:
        print("nothing new to do")
        return

    summary = []
    for n, (slug, c) in enumerate(picked, 1):
        print("\n=== [%d/%d] %s (%s) ===" % (n, len(picked), c["name"], slug))
        line = {"program": slug, "name": c["name"], "status": "unknown", "note": ""}
        try:
            _mark_attempted(a.platform, slug)
            ins, outs, page = get_scope(a.platform, slug)
            if not page:
                print("  page did not render - session expired? skipping")
                line.update(status="no-session", note="empty page (re-login bugcrowd)")
                summary.append(line)
                continue
            if not ins:
                print("  page rendered but no in-scope targets parsed - layout change?")
                line.update(status="no-scope", note="0 parsed in-scope (layout?)")
                summary.append(line)
                continue
            print("  scope: %d in, %d out" % (len(ins), len(outs)))
            # seed only from real registrable hosts in the in-scope list
            seeds = _scannable(ins, ins, outs)
            if not seeds:
                print("  no scannable in-scope seed (app-store only?) - skipping")
                line.update(status="no-seed", note="no scannable host")
                summary.append(line)
                continue
            jid, job = create_job(a.platform, slug, ins, outs, ",".join(seeds))
            print("  job", jid)
            line["job"] = jid

            ok, why = _stage(lambda j: cmd_dispatch(_ns(id=j, dry_run=False, allow_stale_scope=False)), jid, "dispatch")
            if not ok:
                line.update(status="recon-failed", note=why)
                summary.append(line)
                continue
            job = json.loads((JOBS / jid / "job.json").read_text())
            counts = job.get("recon_counts") or {}
            line["counts"] = counts
            if not counts.get("live"):
                print("  no live hosts - nothing to triage")
                line.update(status="no-live", note="0 live hosts")
                summary.append(line)
                continue

            ok, why = _stage(cmd_triage, jid, "triage")
            if not ok:
                line.update(status="triage-failed", note=why)
                summary.append(line)
                continue
            job = json.loads((JOBS / jid / "job.json").read_text())
            line["status"] = job.get("status", "?")

            draft = (JOBS / jid / "draft.md")
            body = draft.read_text() if draft.exists() else ""
            if "NONE" in body.upper()[:400] or job.get("status") == "challenged-killed":
                print("  draft rejected by challengers - not reporting")
                line["note"] = "killed by challengers"
                summary.append(line)
                continue
            _stage(cmd_report, jid, "report")
            line["reported"] = True
        except Exception as e:
            print("  UNEXPECTED %s: %s" % (type(e).__name__, str(e)[:200]))
            line.update(status="error", note="%s: %s" % (type(e).__name__, str(e)[:120]))
        summary.append(line)
        if n < len(picked) and a.sleep:
            # jittered, not metronome-like: 0.6x..1.6x base
            time.sleep(a.sleep * random.uniform(0.6, 1.6))

    # one digest, not one message per program
    rows = []
    for s in summary:
        c = s.get("counts") or {}
        rows.append("%-28s %-16s live=%s nuclei=%s %s" %
                    (s["program"][:28], s["status"], c.get("live", "-"),
                     c.get("nuclei", "-"), ("REPORTED" if s.get("reported") else s.get("note", ""))[:40]))
    text = "BOUNTY AUTO RUN\n" + "\n".join(rows)
    if skipped:
        text += "\n\nskipped (already worked): " + ", ".join(skipped[:10])
    print("\n" + text)
    try:
        send(text)
    except Exception as e:
        print("telegram failed: %s" % e)


def main():
    ap = argparse.ArgumentParser(prog="bounty_run.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ds = sub.add_parser("discover")
    ds.add_argument("--platform", default="both", choices=["bugcrowd", "hackerone", "both"])
    ds.add_argument("--limit", type=int, default=25)
    sc = sub.add_parser("scope")
    sc.add_argument("--platform", default="bugcrowd", choices=["bugcrowd", "hackerone"])
    sc.add_argument("--program", required=True, help="brief URL or Bugcrowd slug")
    sc.add_argument("--json", action="store_true")
    n = sub.add_parser("new")
    n.add_argument("--platform", required=True)
    n.add_argument("--program", required=True)
    n.add_argument("--in-scope", required=True)
    n.add_argument("--out-of-scope", default="")
    n.add_argument("--target", default="")
    dp = sub.add_parser("dispatch")
    dp.add_argument("id")
    dp.add_argument("--dry-run", action="store_true")
    dp.add_argument("--allow-stale-scope", action="store_true",
                    help="scan even if job.json is missing exclusions the brief now lists")
    tr = sub.add_parser("triage")
    tr.add_argument("id")
    rp = sub.add_parser("report")
    rp.add_argument("id")
    rm = sub.add_parser("remote")
    rm.add_argument("stage", choices=["dispatch", "triage"])
    rm.add_argument("id")
    au = sub.add_parser("auto")
    au.add_argument("--platform", default="bugcrowd", choices=["bugcrowd"])
    au.add_argument("--limit", type=int, default=12, help="candidates to consider")
    au.add_argument("--max-programs", type=int, default=3, help="programs to actually work")
    au.add_argument("--min-reward", type=int, default=0, help="skip programs below this max reward")
    au.add_argument("--sleep", type=int, default=20, help="pause between programs (jittered)")
    a = ap.parse_args()
    {"discover": cmd_discover, "scope": cmd_scope, "new": cmd_new, "dispatch": cmd_dispatch,
     "triage": cmd_triage, "report": cmd_report, "remote": cmd_remote, "auto": cmd_auto}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
