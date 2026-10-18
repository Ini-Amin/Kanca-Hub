#!/usr/bin/env python3
"""One-shot: insert discover/scope into bounty_run.py before cmd_new."""
from pathlib import Path

P = Path("/home/amen/Auto-FreeCF/scripts/bounty_run.py")
s = P.read_text()

BLOCK = '''
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
    """HackerOne has no public program JSON; read the rendered directory instead.

    The listing is JS-rendered and its handles are not in the body text, so this
    is a best-effort signal only: it proves the directory is reachable and how
    many rows we can see. Scope always comes from an explicit program URL.
    """
    txt = _render_text("hackerone", "https://hackerone.com/opportunities/all/search")
    if not txt:
        return []
    out, seen = [], set()
    for line in txt.splitlines():
        v = line.strip()
        if not v or len(v) > 90 or v.lower() in H1_NOISE or v in seen:
            continue
        if re.search(r"\\b(bug bounty|vulnerability disclosure)\\b", v, re.I):
            seen.add(v)
            out.append({"platform": "hackerone", "name": v,
                        "brief": "https://hackerone.com/opportunities/all/search",
                        "min": "", "max": "", "industry": "", "type": ""})
        if len(out) >= limit:
            break
    return out


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
IN_SCOPE = re.compile(r"^\\s*in scope\\s*$", re.I)
OUT_SCOPE = re.compile(r"^\\s*out of scope\\s*$", re.I)
URLISH = re.compile(r"^(https?://|[\\w*][\\w.*-]*\\.[a-z]{2,})", re.I)
SCOPE_STOP = re.compile(r"^(known issues|what.s new|recent activity|crowd highlights|"
                        r"things to know|vulnerabilities rewarded|overview|description)\\s*$", re.I)


def parse_scope(text):
    """Split a rendered Bugcrowd brief into in-scope / out-of-scope target lists.

    The page prints a literal "In scope" heading, then the targets, then
    "Out of scope". Everything URL-ish inside a region is a target; the page
    prints each target twice (label + value), so dedupe case-insensitively.
    """
    ins, outs, mode = [], [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if IN_SCOPE.match(line):
            mode = "in"
            continue
        if OUT_SCOPE.match(line):
            mode = "out"
            continue
        if SCOPE_STOP.match(line):
            mode = None
            continue
        if mode is None or not URLISH.match(line):
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


def cmd_scope(a):
    url = a.program
    if not url.startswith("http"):
        url = "https://bugcrowd.com/engagements/" + url.strip("/")
    text = _render_text(a.platform, url)
    if not text:
        sys.exit("empty page - session expired? run: bounty_platform.py login --platform %s" % a.platform)
    ins, outs = parse_scope(text)
    print("IN  (%d): %s" % (len(ins), ", ".join(ins) or "-"))
    print("OUT (%d): %s" % (len(outs), ", ".join(outs) or "-"))
    if a.json:
        print(json.dumps({"in_scope": ins, "out_of_scope": outs}, indent=1))


'''

ANCHOR = "def cmd_new(a):"
i = s.index(ANCHOR)
s = s[:i] + BLOCK.lstrip("\n") + s[i:]
P.write_text(s)
print("inserted, new length", len(s))
