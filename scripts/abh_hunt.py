#!/usr/bin/env python3
"""Drive Agentic-Bug-Hunter (ABH) as the 24/7 hunt engine on blu.

WHAT THIS REPLACES
------------------
`bounty_run.py auto` used to own the whole loop: discover -> scope -> recon ->
triage -> report. Its discovery and scope parsing are good (they handle
Bugcrowd's one-card-per-target-group layout, and the specificity rules that stop
an apex deny from erasing an explicit allow). Its recon and triage are the weak
part: a fixed subfinder/httpx/nuclei pipeline with no reasoning about what it
found.

ABH already does that better — 9-phase recon, a deterministic ScopeChecker, a
7-Question Gate, and a ReAct loop that decides which tool to run next. So this
script keeps the two halves where they are strongest:

    bounty_run.py   discovery + scope parsing   (keyless feed, saved session)
    ABH             recon + hunt + triage + report

WHY NOT JUST CALL ABH DIRECTLY
------------------------------
ABH has no notion of a Bugcrowd engagement list, reward filtering, or an
attempted-programs log. Calling it per-program by hand is exactly the manual
step that stops a loop from being 24/7.

SCOPE SAFETY
------------
Every ABH invocation gets `-d` for each in-scope pattern and `-x` for each
exclusion, so ABH's own ScopeChecker enforces the program's rules on every
outbound request. ABH refuses to run traffic-sending tools with no `-d`, which is
the behaviour we want: a bug here fails closed, not open.

    abh_hunt.py --max-programs 2            # normal loop tick
    abh_hunt.py --max-programs 1 --dry-run  # show what it would hunt
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bounty_run as B  # noqa: E402
import hunt_roles  # noqa: E402

def _default_abh() -> Path:
    env_p = os.environ.get("ABH_DIR")
    if env_p:
        return Path(env_p)
    local_p = HERE.parent.parent / "Agentic-Bug-Hunter"
    if (local_p / "bughunter" / "agent.py").is_file():
        return local_p
    return Path("/root/Agentic-Bug-Hunter")


ABH = _default_abh()
AGENT = ABH / "bughunter" / "agent.py"
JOBS = B.JOBS


def say(msg: str) -> None:
    print(msg, flush=True)


def abh_env() -> dict:
    """Environment for the ABH child.

    Reads /root/abh.env if present so the model/endpoint wiring lives in one
    place (scripts/abh_deploy.sh writes it). Falls back to the current env so a
    caller can export the values directly.
    """
    env = dict(os.environ)
    envfile = Path(os.environ.get("ABH_ENV", "/root/abh.env"))
    if envfile.is_file():
        for line in envfile.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or not line.startswith("export "):
                continue
            k, _, v = line[len("export "):].partition("=")
            v = v.strip().strip('"').strip("'")
            # Expand $PATH / ${PATH} against the existing environment instead of
            # writing a literal "$PATH" token that wipes /usr/bin and /bin.
            v = v.replace("${PATH}", env.get("PATH", "")).replace("$PATH", env.get("PATH", ""))
            env[k.strip()] = v
    env.setdefault("PATH", "/root/go/bin:/root/.local/bin:/usr/local/bin:/usr/bin:/bin")
    return env


def scope_flags(in_scope: list[str], out_of_scope: list[str]) -> list[str]:
    """Build ABH -d/-x arguments from a program's scope lists.

    ABH wants domain patterns, not URLs, and it rejects entries it cannot parse.
    Bare hosts and wildcards are passed through; app-store URLs and templated
    assets (e.g. `bugcrowd-pam-###.oktapreview.com`) are dropped because they are
    not matchable patterns — passing them only makes ScopeChecker reject
    everything with a confusing message.
    """
    args: list[str] = []
    seen: set[str] = set()

    def add(host: str) -> None:
        if not host or host in seen:
            return
        seen.add(host)
        args.extend(["-d", host])

    for pat in in_scope:
        h = B._host_of(pat)
        if not h:
            continue
        # A wildcard is a MATCHABLE rule, not a placeholder. _is_placeholder_host
        # treats any "*" as unenumerable template syntax, which is right for
        # seeding but wrong here: dropping "*.okta.com" left ScopeChecker with
        # only the explicit hosts, so the apex seed the pipeline itself chose
        # was rejected by its own guard ("okta.com matches no in-scope rule",
        # Okta, 2026-10-22 -> steps=0). Pass the wildcard through AND add the
        # bare base, because "*.x" does not match "x" in ScopeChecker while
        # every program that writes "*.x" means the apex too.
        if h.startswith("*."):
            base = h[2:]
            if not B._is_placeholder_host(base):
                add(h)
                add(base)
            continue
        if B._is_placeholder_host(h):
            continue
        if h in ("apps.apple.com", "play.google.com", "itunes.apple.com",
                 "github.com", "gitlab.com", "bitbucket.org", "npmjs.com",
                 "pypi.org", "hub.docker.com", "chrome.google.com",
                 "chromewebstore.google.com", "addons.mozilla.org"):
            continue
        add(h)
    for pat in out_of_scope:
        h = B._host_of(pat)
        if not h or B._is_placeholder_host(h):
            continue
        args += ["-x", h]
    return args


def run_abh(target: str, flags: list[str], hours: float, steps: int,
            dry: bool, model: str = "") -> tuple[int, str]:
    cmd = [sys.executable, str(AGENT), "--target", target, *flags,
           "--time", str(hours), "--max-steps", str(steps)]
    say("  $ " + " ".join(cmd))
    if model:
        say("  model: %s" % model)
    if dry:
        return 0, "DRY RUN: " + " ".join(cmd)
    env = abh_env()
    if model:
        env["BRAIN_MODEL"] = model
    env["AUTONOMOUS"] = "1"

    t0 = time.time()
    base_timeout = int(hours * 3600) + 600
    max_ceiling = int(max(hours, 2.5) * 3600)
    current_timeout = base_timeout

    proc = subprocess.Popen(
        cmd, cwd=str(ABH), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    recon_sessions = ABH / "bughunter" / "recon" / target / "sessions"
    last_extension = 0

    while True:
        try:
            ret = proc.wait(timeout=5)
            out = proc.stdout.read() if proc.stdout else ""
            return ret, out
        except subprocess.TimeoutExpired:
            pass

        now = time.time()
        elapsed = now - t0

        if recon_sessions.is_dir():
            try:
                for s_dir in recon_sessions.iterdir():
                    ext_f = s_dir / "budget_extension.json"
                    if ext_f.is_file():
                        ext_data = json.loads(ext_f.read_text(encoding="utf-8"))
                        ext_mins = int(ext_data.get("extended_minutes", 0))
                        if ext_mins > last_extension:
                            added = ext_mins - last_extension
                            last_extension = ext_mins
                            current_timeout = min(max_ceiling, base_timeout + (ext_mins * 60))
                            say(f"  [dynamic-budget] Agent requested +{added}m extension "
                                f"(total: {ext_mins}m, deadline: {current_timeout//60}m): "
                                f"{ext_data.get('reason', '')[:80]}")
            except Exception:
                pass

        if elapsed >= current_timeout:
            say(f"  [dynamic-budget] Target {target} exceeded deadline ({current_timeout}s) — terminating")
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            out = proc.stdout.read() if proc.stdout else ""
            return 124, out + f"\nTimeoutExpired: Command exceeded dynamic budget {current_timeout}s"


def run_delegate_dry(target: str, flags: list[str], hours: float, steps: int,
                     default_model: str) -> None:
    splits = [
        ("recon", 0.30),
        ("analyze", 0.50),
        ("report", 0.20),
    ]
    for role, frac in splits:
        r_model = hunt_roles.resolve(role, default_model)
        h_phase = round(hours * frac, 3)
        s_cap = hunt_roles.ROLES[role]["step_cap"]
        s_phase = min(steps, s_cap) if steps else s_cap
        cmd = hunt_roles.phase_cmd(role, target, flags, h_phase, s_phase, r_model)
        say("  [%s] $ BRAIN_MODEL=%s %s" % (role, r_model, " ".join(cmd)))
        say("  role: %s  BRAIN_MODEL: %s" % (role, r_model))


def run_delegate(target: str, flags: list[str], hours: float, steps: int,
                 default_model: str) -> tuple[int, str, str, list[dict]]:
    """Run recon -> analyze -> report sequentially. Returns (rc, text, status, phases)."""
    splits = [
        ("recon", 0.30),
        ("analyze", 0.50),
        ("report", 0.20),
    ]
    env = abh_env()
    phases: list[dict] = []
    accumulated = ""
    last_rc = 0

    sdir = ABH / "bughunter" / "recon" / target
    sdir.mkdir(parents=True, exist_ok=True)

    for role, frac in splits:
        h_phase = round(hours * frac, 3)
        s_cap = hunt_roles.ROLES[role]["step_cap"]
        s_phase = min(steps, s_cap) if steps else s_cap
        r_model = hunt_roles.resolve(role, default_model)
        cmd = hunt_roles.phase_cmd(role, target, flags, h_phase, s_phase, r_model)
        say("  [%s] $ %s" % (role, " ".join(cmd)))
        say("  role: %s  model: %s" % (role, r_model))

        rc, text, meta = hunt_roles.run_phase(
            role, target, flags, h_phase, s_phase, env,
            default_model=default_model, prior_output=accumulated,
        )
        phases.append(meta)
        last_rc = rc

        # Fail closed: if recon produces no output, skip analyze and report
        if role == "recon":
            if not text or not text.strip():
                try:
                    (sdir / "delegation.json").write_text(
                        json.dumps(phases, indent=2), encoding="utf-8")
                except OSError:
                    pass
                return rc or 1, text, "recon-empty", phases

        accumulated = (accumulated + "\n" + text).strip() if accumulated else text

    try:
        (sdir / "delegation.json").write_text(
            json.dumps(phases, indent=2), encoding="utf-8")
    except OSError:
        pass

    return last_rc, accumulated, "", phases

# Model tiers for the supervisor. A hunt is a chain of cheap deterministic
# steps (subfinder, httpx, nuclei) wrapped around a few expensive judgement
# calls (what to try next, is this a real finding). Paying the strong model for
# the cheap steps is the main avoidable cost, so the tier is chosen per program
# from what actually makes a program hard: surface size and what it pays.
#
# Every default below was probed with a real tool-calling prompt on blu
# 2026-10-21 (scripts/model_health_watch.py, which is what keeps this table
# honest from now on):
#   THK/deepseek-v4.1-flash:free   3.4s  tool_calls=1   -> cheap
#   tia/glm-5.3-flash:free        4.9s  tool_calls=1   -> standard
#   nr/step-5-preview            12.9s  tool_calls=1   -> strong
#   ocz/mimo-v2.6-flash-free      6.0s  tool_calls=0   UNUSABLE: HTTP 200, empty
#                                        content, no call. Retired as a tier and
#                                        demoted to the tail of the chain.
#   Zcode/GLM-5.3               90.0s  timeout        UNUSABLE right now; blu-r9-farm
#                                        is the thing that refills it.
# ag/gemini-3.8-flash-high used to be "strong" and is gone: it answers 200 in
# ~10s but returns empty content - Gemini's safety filter blocks the security
# prompt outright - and the first successful end-to-end run burned all 8 steps
# on "(LLM produced no tool call)".
#
# "Answers" is not the bar. A model that 200s without emitting a tool call costs
# the same wall clock as a dead one and produces the same zero findings, so the
# tiers only name models that both answer AND call tools.
MODEL_TIERS = {
    "strong": os.environ.get("ABH_MODEL_STRONG", "nr/step-5-preview"),
    "standard": os.environ.get("ABH_MODEL_STANDARD", "tia/glm-5.3-flash:free"),
    "cheap": os.environ.get("ABH_MODEL_CHEAP", "THK/deepseek-v4.1-flash:free"),
}


def payout_of(c: dict) -> tuple[int, int]:
    def money(v):
        d = "".join(ch for ch in str(v or "") if ch.isdigit())
        return int(d) if d else 0
    return money(c.get("min")), money(c.get("max"))

def rank_program(c: dict, pay: tuple[int, int], worked_set: set[str], in_scope_count: int = 0) -> tuple[float, dict]:
    """Transparent multi-factor ranking model for program prioritization.

    Criteria:
      1. Program reward structure (max 25 pts): top bounty payout vs VDP
      2. Eligibility & scope clarity (max 20 pts): web/domain vs restrictive/mobile-only
      3. Availability of suitable tools (max 20 pts): matches web scanner arsenal
      4. Previous scan history & freshness (max 20 pts): fresh > aged re-scan > recent
      5. Execution cost & attack-surface size (max 15 pts): focused surface
    """
    scores = {}
    name = str(c.get("name", "")).lower()
    brief = str(c.get("brief", "")).lower()

    # 1. Reward structure (0-25)
    hi_pay = pay[1]
    if hi_pay >= 10000:
        scores["reward"] = 25.0
    elif hi_pay >= 5000:
        scores["reward"] = 20.0
    elif hi_pay >= 1000:
        scores["reward"] = 15.0
    elif hi_pay > 0:
        scores["reward"] = 10.0
    else:
        scores["reward"] = 6.0  # VDP / Hall of Fame points

    # 2. Scope clarity & eligibility (0-20)
    if any(k in brief or k in name for k in ("ios only", "android only", "hardware", "firmware", "source code")):
        scores["eligibility"] = 5.0
    else:
        scores["eligibility"] = 20.0

    # 3. Tool suitability & attack surface (0-20)
    if any(k in brief or k in name for k in ("api", "cloud", "saas", "web", "portal", "platform", "backend")):
        scores["tool_fit"] = 20.0
    else:
        scores["tool_fit"] = 15.0

    # 4. Scan history & freshness (0-20)
    slug = (c.get("slug") or c.get("brief", "").rstrip("/").split("/")[-1]).lower()
    if slug not in worked_set:
        scores["freshness"] = 20.0
    else:
        scores["freshness"] = 6.0

    # 5. Execution cost & estimated duration (0-15)
    if 2 <= in_scope_count <= 25:
        scores["duration_fit"] = 15.0
    elif in_scope_count > 50:
        scores["duration_fit"] = 8.0
    else:
        scores["duration_fit"] = 12.0

    total = sum(scores.values())
    return round(total, 1), scores


def pick_model(payout: tuple[int, int], in_scope_count: int) -> str:
    """Strong model only where the money and the surface justify it.

    Thresholds are deliberately crude: a bug bounty pays by severity, not by
    program size, but a program paying under $1k rarely rewards a deep chain,
    and a program with <8 in-scope hosts has little to reason about.
    """
    hi = payout[1]
    if hi >= 5000 or in_scope_count >= 40:
        return MODEL_TIERS["strong"]
    if hi >= 1000 or in_scope_count >= 10:
        return MODEL_TIERS["standard"]
    return MODEL_TIERS["cheap"]


FINDINGS_RE = re.compile(r"Findings:\s+(\d+)")
SESSION_RE = re.compile(r"Session:\s+(\S+)")


def parse_abh_output(text: str) -> dict:
    """Pull the summary numbers out of ABH's stdout.

    `tool_errors` is the number that makes the rest readable. `steps=12
    findings=0` looks identical whether the agent tested twelve things and
    found nothing, or called one broken tool twelve times — and that second
    case is exactly what happened on 2026-10-21 (run_vuln_scan raised TypeError
    on every call). ABH already prints the failing tools in its tool list, so
    the count comes from there rather than a second parse of the trace.
    """
    out = {"findings": 0, "session": "", "steps": 0, "tool_errors": 0}
    m = FINDINGS_RE.search(text)
    if m:
        out["findings"] = int(m.group(1))
    m = SESSION_RE.search(text)
    if m:
        out["session"] = m.group(1)
    m = re.search(r"Steps executed:\s+(\d+)", text)
    if m:
        out["steps"] = int(m.group(1))
    # ABH's per-step tool list prints "[!] name — raised/blocked/failed".
    out["tool_errors"] = len(re.findall(r"^\s*\[!\]", text, re.M))
    if not out["tool_errors"]:
        # Older builds only say "Completed tools: N"; anything else that shows
        # up under a failure header still counts.
        out["tool_errors"] = len(re.findall(r"\b(?:raised exception|Tool .* (?:failed|blocked))\b", text))
    return out


# ── Stage 4 — the four pre-submit gates ─────────────────────────────────────
#
# Spec stage 4: every finding must clear SCOPE, DUPCHECK, VM-PoC and
# CHALLENGER before anything is written or sent. Two properties are load-
# bearing and easy to break:
#
#   1. Order is cheapest-first AND safety-correct. SCOPE is a pure string
#      check; VM-PoC replays a live request; CHALLENGER spends a model call.
#      Running VM-PoC before SCOPE would replay a PoC against a host nobody
#      approved — an out-of-scope scan performed by the gate meant to prevent
#      exactly that. So the first failure short-circuits the rest.
#   2. Every expensive dependency is an injectable callable (h1=, poc=,
#      challenge=), so tests can drive each gate with a synthetic finding and
#      no network, and a caller can swap in another scope DB or model without
#      forking this module.

GATE_NAMES = ("SCOPE", "DUPCHECK", "VM-PoC", "CHALLENGER")
GATE_PASSED = "GATE RESULT: 4/4 PASS"


def model_family(model: str) -> str:
    """Underlying model family, ignoring the routing prefix and any tag.

        'Zcode/GLM-5.3'            -> 'glm'
        'THK/deepseek-v4.1-flash:free' -> 'deepseek'
        'ocz/space-bunny-free'     -> 'space'

    The prefix (Zcode/THK/ocz/ag/nr) is *who proxies the call*, not what model
    answers. Judging sameness on it would happily pair one GLM with itself
    behind two providers and call that an independent second opinion.
    """
    core = (model or "").split("/")[-1].split(":")[0].lower()
    m = re.match(r"[a-z]+", core)
    return m.group(0) if m else ""


def model_chain(env: dict) -> list[str]:
    """BRAIN_MODEL followed by BOUNTY_MODEL_FALLBACKS, flattened and deduped."""
    out: list[str] = []
    seen: set[str] = set()
    for blob in (env.get("BRAIN_MODEL", ""), env.get("BOUNTY_MODEL_FALLBACKS", "")):
        for tok in str(blob).split(","):
            tok = tok.strip()
            if tok and tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out


def pick_challenger(hunter_model: str, env: dict) -> str | None:
    """First chain model whose family differs from the hunter's.

    Gate 4 exists to catch one model's confident mistake. Asking the same
    family — often the same weights behind a different proxy — reproduces that
    mistake and lends it a second opinion, which is worse than one honest
    verdict. So no different family available means no gate, and no gate means
    the finding does not ship.
    """
    fam = model_family(hunter_model)
    for m in model_chain(env):
        if m == hunter_model:
            continue
        if model_family(m) and model_family(m) != fam:
            return m
    return None


def gate_scope(finding: dict, ins: list[str], outs: list[str]) -> tuple[bool, str]:
    """Gate 1 — SCOPE. Fail closed: no host, or no allow list, is a FAIL."""
    url = str(finding.get("url") or "").strip()
    host = str(finding.get("host") or "").strip().lower() or B._host_of(url)
    if not host:
        return False, "finding carries no host/URL — scope cannot be proven (fail closed)"
    if not ins:
        return False, "empty in-scope list — scope cannot be proven (fail closed)"
    # host_in_scope returns (ok, reason) — a tuple is always truthy, so
    # `if not host_in_scope(...)` would have evaluated the deny branch out of
    # existence and let every finding through. Unpack it.
    ok, why = B.host_in_scope(host, ins, outs)
    if not ok:
        return False, (f"{host} is not covered by the program's in-scope list "
                       f"({why})")
    return True, f"{host} is in scope ({why})"


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def _local_findings(jobs_dir: Path | None = None) -> list[dict]:
    """Everything already filed under bounty/<job>/.

    Three shapes are read because three exist in the wild: report.md section
    headings (older jobs), findings.json rows (newer jobs) and _ledger.json
    (written by stage 5 from here on). A dup gate reading only its own ledger
    would wave through anything filed before the ledger existed.
    """
    root = Path(jobs_dir) if jobs_dir else Path(B.JOBS)
    out: list[dict] = []
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*/report.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("## "):
                t = line[3:].strip()
                if t:
                    out.append({"title": t, "source": str(p)})
    for pat in ("*/findings.json", "_ledger.json"):
        for p in sorted(root.glob(pat)) if "*" in pat else ([root / pat] if (root / pat).is_file() else []):
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                continue
            rows = data if isinstance(data, list) else data.get("findings", [])
            for r in rows or []:
                if isinstance(r, dict) and (r.get("title") or r.get("url")):
                    out.append({
                        "title": str(r.get("title") or ""),
                        "url": str(r.get("url") or ""),
                        "vuln_class": str(r.get("vuln_class") or "").lower(),
                        "source": str(p),
                    })
    return out


def _claimed_cves(finding: dict) -> list[str]:
    """CVE ids the finding *asserts as its finding*.

    Deliberately not a scan of description/evidence: prose that says "similar
    to CVE-2023-1234" is background reading, not a claim. Only the title and
    an explicit cve/cves field make the finding *be* the published advisory.
    """
    blob = " ".join([
        str(finding.get("title") or ""),
        str(finding.get("cve") or ""),
        " ".join(str(x) for x in (finding.get("cves") or [])),
        str(finding.get("vuln_class") or "") if str(finding.get("vuln_class") or "").lower() in ("cve", "known-cve") else "",
    ])
    return sorted({m.upper() for m in re.findall(r"CVE-\d{4}-\d{4,7}", blob, re.I)})


def _cve_describe(cve_ids: list[str], cve_db: Path | None = None) -> dict[str, str]:
    """Resolve CVE ids against the local cve.db (read-only) for the reason line."""
    if not cve_ids:
        return {}
    db = Path(cve_db) if cve_db else Path(
        os.environ.get("BOUNTY_CVE_DB", str(Path.home() / ".config" / "auto-freecf" / "cve.db")))
    if not db.is_file():
        return {c: "<local cve.db not present>" for c in cve_ids}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = dict(con.execute(
                f"SELECT id, description FROM cve WHERE id IN ({','.join('?' * len(cve_ids))})",
                cve_ids).fetchall())
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        return {c: f"<local cve.db unreadable: {type(exc).__name__}>" for c in cve_ids}
    return {c: rows.get(c, "<not in local cve.db>") for c in cve_ids}


def _h1_dup_check(program: str, keyword: str) -> tuple[list, bool, str]:
    """Run validate.check_h1_dups and find out whether it actually reached H1.

    check_h1_dups() returns [] for *both* "no duplicates" and "the request
    blew up" — the only difference between those is a line it prints to
    stderr. Reading that line is the difference between a gate that checked
    and a gate that silently waved the finding through during an outage.
    """
    if not program or not keyword:
        return [], False, "no program handle or keyword supplied"
    bh = str(ABH / "bughunter")
    if bh not in sys.path:
        sys.path.insert(0, bh)
    try:
        import tools.validate as vmod
    except Exception as exc:  # noqa: BLE001
        return [], False, f"cannot import ABH tools.validate: {type(exc).__name__}: {exc}"
    got = Path(getattr(vmod, "__file__", "") or "")
    try:
        if not got.is_relative_to(ABH.resolve()):
            return [], False, f"a different tools.validate is loaded from {got}"
    except (OSError, ValueError):
        return [], False, f"cannot verify tools.validate origin ({got})"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf):
            hits = vmod.check_h1_dups(program, keyword)
    except Exception as exc:  # noqa: BLE001
        return [], False, f"{type(exc).__name__}: {exc}"
    msg = buf.getvalue()
    if "duplicate check failed" in msg.lower():
        return [], False, (msg.strip().splitlines() or ["unknown"])[-1][:200]
    return list(hits or []), True, ""


def gate_dupcheck(finding: dict, program: str = "", jobs_dir: Path | None = None,
                  h1=None, cve_db: Path | None = None) -> tuple[bool, str]:
    """Gate 2 — DUPCHECK: local files, then public disclosure, then hacktivity.

    The CVE branch is what stops "I found CVE-2024-1234 on this host" from
    being reported: an assigned CVE is an advisory everyone can already read,
    so it is public knowledge rather than a finding.
    """
    title = str(finding.get("title") or "").strip()
    url = str(finding.get("url") or "").strip()
    host = B._host_of(url)
    vuln = str(finding.get("vuln_class") or "").strip().lower()
    norm = _norm_title(title)

    for row in _local_findings(jobs_dir):
        rt = _norm_title(row.get("title", ""))
        if norm and rt and (rt == norm or SequenceMatcher(None, norm, rt).ratio() >= 0.92):
            return False, f"local duplicate: a near-identical title is already in {row['source']}"
        if url and row.get("url") == url and vuln and row.get("vuln_class") == vuln:
            return False, (f"local duplicate: same {vuln} already filed for {url} "
                           f"in {row['source']}")

    claimed = _claimed_cves(finding)
    if claimed:
        detail = _cve_describe(claimed, cve_db)
        first = detail.get(claimed[0], "")
        if len(first) > 160:
            first = first[:160] + "…"
        return False, (f"known public disclosure: finding claims {', '.join(claimed)}"
                       + (f" — {first}" if first else ""))

    keyword = vuln or (title.split()[0] if title else "")
    hits, reachable, detail = (h1 or _h1_dup_check)(program, keyword)
    if hits:
        ex = str(hits[0].get("title") or hits[0].get("url") or "")[:120]
        return False, (f"{len(hits)} HackerOne disclosed report(s) match keyword "
                       f"{keyword!r}, e.g. {ex!r}")
    if not reachable:
        # Not fatal: H1 being unreachable must not freeze all reporting, and
        # the local + CVE checks above still ran. The marker is kept on the
        # gate line so a reviewer can see this one check was blind.
        return True, f"no local or published-CVE duplicate — H1 hacktivity UNREACHABLE ({detail})"
    return True, "no local, published-CVE, or HackerOne hacktivity duplicate"


def _poc_replay(finding: dict, timeout: float = 25) -> tuple[int | None, str | None, str]:
    """Replay the finding's request through ABH's poc_bundler.

    poc_bundler rather than a bare urllib call because it goes through
    tools.safe_http (the SSRF guard) and refuses mutating methods without an
    explicit --confirm-unsafe — restraint an unattended replay should keep.
    Returns (status, body, note); on failure body is None and note says why.
    """
    url = str(finding.get("url") or "").strip()
    if not url:
        return None, None, "finding has no URL to replay"
    method = str(finding.get("method") or "GET").upper()
    bundler = ABH / "bughunter" / "tools" / "poc_bundler.py"
    if not bundler.is_file():
        return None, None, f"poc_bundler.py not found at {bundler}"
    outdir = Path(tempfile.mkdtemp(prefix="pocgate-"))
    cmd = [sys.executable, str(bundler), "capture", url, "-X", method,
           "--json", "-o", str(outdir), "--timeout", str(timeout)]
    for k, v in (finding.get("headers") or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if finding.get("body"):
        cmd += ["-d", str(finding["body"])]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout + 30, env=abh_env())
    except subprocess.TimeoutExpired:
        return None, None, f"replay timed out after {timeout + 30:.0f}s"
    except OSError as exc:
        return None, None, f"replay could not start: {exc}"
    err = (proc.stderr or "").strip()
    first_err = (err.splitlines() or [""])[0]
    if proc.returncode == 2 or "confirm-unsafe" in err:
        return None, None, (f"poc_bundler refused to replay {method} without "
                            f"--confirm-unsafe (mutating method); "
                            f"re-run that replay by hand if it is approved")
    if proc.returncode != 0:
        return None, None, f"replay exited {proc.returncode}: {first_err or 'no detail'}"
    try:
        payload = json.loads(proc.stdout)
        bdir = Path(payload["bundle_dir"])
        status = ((payload.get("manifest") or {}).get("exchange") or {}).get("response_status")
        raw = (bdir / "response.http").read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, None, f"could not read the PoC bundle: {type(exc).__name__}: {exc}"
    parts = re.split(r"\r?\n\r?\n", raw, maxsplit=1)
    body = parts[1] if len(parts) == 2 else raw
    return status, body, ""


def gate_vm_poc(finding: dict, poc=None, timeout: float = 25) -> tuple[bool, str]:
    """Gate 3 — VM-PoC: replay the request on this host and re-derive the claim."""
    indicator = str(finding.get("indicator") or "").strip()
    want_status = finding.get("indicator_status")
    if not indicator and want_status is None:
        return False, ("finding declares no reproduction indicator — there is "
                       "nothing to verify (fail closed)")
    fn = poc if poc is not None else (lambda f: _poc_replay(f, timeout))
    status, body, note = fn(finding)
    if body is None:
        return False, note or "replay produced no response"
    # AND, not OR: every assertion the finding declares has to hold. An OR
    # here let a plain 200 status satisfy a gate whose real claim was a
    # marker in the body — a 200 proves nothing on its own, so a matching
    # status must not rescue a missing indicator.
    missed: list[str] = []
    if indicator and indicator not in body:
        missed.append(f"indicator {indicator!r} absent from the replayed response")
    if want_status is not None and (status is None or int(status) != int(want_status)):
        missed.append(f"status {status} != expected {want_status}")
    if missed:
        return False, "; ".join(missed)
    return True, f"replay reproduced the indicator (status {status})"


def describe_finding(finding: dict) -> str:
    """The text handed to the challenger — the facts a triager would read."""
    lines = [
        f"TITLE: {finding.get('title', '')}",
        f"VULN CLASS: {finding.get('vuln_class', '')}",
        f"SEVERITY: {finding.get('severity', '')}",
        f"URL: {finding.get('url', '')}",
    ]
    if finding.get("method"):
        lines.append(f"METHOD: {finding['method']}")
    if finding.get("body"):
        lines.append(f"REQUEST BODY: {finding['body']}")
    if finding.get("indicator"):
        lines.append(f"REPRO INDICATOR: {finding['indicator']}")
    for k in ("description", "evidence", "impact"):
        if finding.get(k):
            lines.append(f"{k.upper()}: {finding[k]}")
    return "\n".join(lines)


def _brain_challenge(model: str, text: str) -> tuple[str, str]:
    """7-Question Gate through ABH's Brain, forced onto `model`."""
    for k, v in abh_env().items():
        os.environ.setdefault(k, v)
    bh = str(ABH / "bughunter")
    if bh not in sys.path:
        sys.path.insert(0, bh)
    from brain import Brain
    b = Brain(model=model)
    if not b.enabled:
        return "UNKNOWN", "brain has no usable provider/key for the challenger"
    return b.triage_finding(text)


def gate_challenger(finding: dict, hunter_model: str = "", env: dict | None = None,
                    challenge=None) -> tuple[bool, str]:
    """Gate 4 — CHALLENGER: an independent model must return SUBMIT."""
    env = env if env is not None else abh_env()
    hunter = hunter_model or str(env.get("BRAIN_MODEL", ""))
    challenger = pick_challenger(hunter, env)
    if not challenger:
        return False, (f"no BOUNTY_MODEL_FALLBACKS entry comes from a different "
                       f"family than the hunter ({hunter or 'unset'}) — an "
                       f"independent challenger cannot be formed (fail closed)")
    try:
        verdict, reasoning = (challenge or _brain_challenge)(challenger, describe_finding(finding))
    except Exception as exc:  # noqa: BLE001
        return False, f"challenger {challenger} errored: {type(exc).__name__}: {exc} (fail closed)"
    if verdict != "SUBMIT":
        tail = " ".join((reasoning or "").split())[:300]
        return False, f"challenger {challenger} returned {verdict}: {tail}"
    return True, (f"{challenger} ({model_family(challenger)} != hunter "
                  f"{model_family(hunter)}) returned SUBMIT")


def run_four_gates(finding: dict, ins: list[str], outs: list[str], program: str = "",
                   *, hunter_model: str = "", jobs_dir: Path | None = None,
                   h1=None, poc=None, challenge=None, env: dict | None = None) -> tuple[bool, str]:
    """Run all four pre-submit gates. Returns (all_passed, human-readable log).

    Short-circuits on the first failure. That is not merely an optimisation:
    gates 3 and 4 send traffic and spend model budget, and gate 3 would
    otherwise replay a PoC against a host gate 1 never approved.
    """
    lines: list[str] = []
    checks = (
        ("SCOPE",      lambda: gate_scope(finding, ins, outs)),
        ("DUPCHECK",   lambda: gate_dupcheck(finding, program=program,
                                             jobs_dir=jobs_dir, h1=h1)),
        ("VM-PoC",     lambda: gate_vm_poc(finding, poc=poc)),
        ("CHALLENGER", lambda: gate_challenger(finding, hunter_model=hunter_model,
                                               env=env, challenge=challenge)),
    )
    for name, fn in checks:
        ok, why = fn()
        lines.append(f"GATE {name}: {'PASS' if ok else 'FAIL'} — {why}")
        if not ok:
            lines.append(f"GATE RESULT: BLOCKED at {name} — remaining gates not run")
            return False, "\n".join(lines)
    lines.append(f"{GATE_PASSED} — cleared for report")
    return True, "\n".join(lines)


# ── Stage 5 — report & Telegram escalation ──────────────────────────────────

def stage5_report(finding: dict, gates_log: str, job_id: str = "", program: str = "",
                  alert: bool = True, brain: bool = False, findings_dir: str = "",
                  jobs_dir: Path | None = None) -> Path | None:
    """Write the submission-ready report and escalate to Telegram.

    This publishes, it does not re-judge: it refuses to run at all unless the
    gate log it is handed says 4/4, so a caller cannot reach Telegram by
    skipping stage 4 — it would have to falsify the log instead.
    """
    if GATE_PASSED not in (gates_log or ""):
        say("stage5 refused: four-gate log does not show a pass")
        return None
    title = str(finding.get("title") or "Untitled finding")
    root = Path(jobs_dir) if jobs_dir else Path(B.JOBS)
    jid = job_id or ("finding-" + (_norm_title(title) or "untitled")[:48])
    d = root / jid
    d.mkdir(parents=True, exist_ok=True)

    md = [
        "# " + title,
        "",
        f"- **Severity:** {finding.get('severity', 'unrated')}",
        f"- **Class:** {finding.get('vuln_class', 'unspecified')}",
        f"- **URL:** {finding.get('url', '')}",
        f"- **Program:** {program or finding.get('program', '')}",
        "",
        "## Reproduction",
        "",
        f"```http\n{finding.get('repro_request') or describe_finding(finding)}\n```",
        "",
        "The response must contain `" + str(finding.get("indicator", "")) + "` "
        f"(expected status: {finding.get('indicator_status', 'any')}).",
        "",
        "## Impact",
        "",
        str(finding.get("impact") or finding.get("description") or "See evidence."),
        "",
        "## Pre-submit gates",
        "",
        "```",
        gates_log.strip(),
        "```",
    ]
    if brain and findings_dir:
        try:
            for k, v in abh_env().items():
                os.environ.setdefault(k, v)
            bh = str(ABH / "bughunter")
            if bh not in sys.path:
                sys.path.insert(0, bh)
            from brain import Brain
            prose = Brain().write_report(findings_dir)
            if prose and prose.strip() and prose.strip() != "NO_REPORTS":
                md += ["", "## Model-written report", "", prose.strip()]
        except Exception as exc:  # noqa: BLE001
            # The deterministic report above is already submission-ready; a
            # Brain outage must not lose it.
            md += ["", f"_(report writer unavailable: {type(exc).__name__})_"]

    path = d / "report.md"
    path.write_text("\n".join(md) + "\n", encoding="utf-8")

    ledger = root / "_ledger.json"
    try:
        rows = json.loads(ledger.read_text(encoding="utf-8")) if ledger.is_file() else []
        if not isinstance(rows, list):
            rows = []
    except (OSError, ValueError):
        rows = []
    rows.append({
        "title": title,
        "url": str(finding.get("url", "")),
        "vuln_class": str(finding.get("vuln_class", "")),
        "severity": str(finding.get("severity", "")),
        "program": program or str(finding.get("program", "")),
        "report": str(path),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    ledger.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

    if alert:
        try:
            from notify import send
            send(f"CONFIRMED FINDING\n\n{title}\n"
                 f"{finding.get('url', '')}\n"
                 f"severity={finding.get('severity', 'unrated')} "
                 f"class={finding.get('vuln_class', '?')} "
                 f"program={program or finding.get('program', '?')}\n\n"
                 f"{gates_log.strip()}\n\n{path}")
        except Exception as exc:  # noqa: BLE001
            say("telegram failed for confirmed finding: %s" % exc)
    say("confirmed finding written to %s" % path)
    return path


# ── Stages 4/5 wiring — ABH findings → gates → report ───────────────────────
#
# ABH persists each finding as one line of scanner output: findings_log rows
# are {tool, severity, text, ts}. The gates need a URL and a reproduction
# indicator, so both are derived here. The derivations deliberately fail
# *closed*: when a line carries no host, gate 1 blocks it; when it carries no
# literal that would itself be the proof, gate 3 blocks it. A claim nobody can
# point at or replay must not become a report, and inventing a URL or an
# indicator to make the pipeline look productive would be the worst possible
# way to unblock it.

HIGH_VALUE = ("CRITICAL", "HIGH", "MEDIUM")

URL_RE = re.compile(r"https?://[^\s'\"<>()\]]+")
# httpx/nuclei print `host/path` when they have no scheme; reconstruct rather
# than drop, because scope is still decided on the resulting host.
HOSTPATH_RE = re.compile(r"\b([a-z0-9][a-z0-9.-]*\.[a-z]{2,})(/[^\s'\"<>)]*)?", re.I)

TOOL_CLASS = {
    "run_cors_check": "cors",
    "run_crlf_scan": "crlf",
    "run_nosqli_scan": "nosqli",
    "run_graphql_audit": "graphql",
    "run_takeover_scan": "takeover",
    "run_403_bypass": "403-bypass",
    "run_cve_hunt": "cve",
    "run_eol_check": "eol",
    "run_vuln_scan": "vuln-scan",
    "run_secret_hunt": "secrets",
    "run_param_discovery": "params",
    "run_jwt_audit": "jwt",
    "run_recon": "recon",
}


def vuln_class_for(tool: str) -> str:
    if tool in TOOL_CLASS:
        return TOOL_CLASS[tool]
    t = (tool or "").replace("run_", "")
    for suf in ("_scan", "_check", "_audit", "_hunt", "_discovery"):
        if t.endswith(suf):
            t = t[: -len(suf)]
    return t or "unspecified"


def derive_indicator(text: str) -> str:
    """A literal the replayed response must contain, if the line names one.

    Ordered by how much each would prove on its own: a JWT or a leaked
    address *is* the finding, a quoted response fragment is close behind, and
    a credential-shaped key=value carries enough entropy to be evidence. A
    bare `id=1` or a scanner's own prose proves nothing and is skipped.
    Returns '' when nothing convincing appears, which gate 3 rejects.
    """
    t = text or ""
    m = re.search(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", t)
    if m:
        return m.group(0)
    m = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", t)
    if m:
        return m.group(0)
    for q in re.findall(r"['\"]([^'\"]{8,120})['\"]", t):
        if not q.lower().startswith(("http", "www.")):
            return q
    m = re.search(r"\b[A-Za-z0-9_]{3,40}=([A-Za-z0-9_.-]{12,})\b", t)
    if m and not m.group(1).lower().startswith("http"):
        return m.group(0)
    return ""


def finding_from_log(entry: dict, target: str = "") -> dict:
    """Shape one findings_log row into the dict the four gates consume."""
    raw = str(entry.get("text") or "")
    text = " ".join(raw.split())
    url = ""
    m = URL_RE.search(raw)
    if m:
        url = m.group(0).rstrip(".,;:)>]\"'")
    else:
        m = HOSTPATH_RE.search(raw)
        if m and m.group(1) and "." in m.group(1):
            url = "https://" + m.group(1) + (m.group(2) or "")
    return {
        "title": text[:110] or "(untitled finding)",
        "url": url,
        "vuln_class": vuln_class_for(str(entry.get("tool") or "")),
        "severity": str(entry.get("severity") or "").upper(),
        "method": "GET",
        "indicator": derive_indicator(text),
        "evidence": text,
        "description": text,
        "program": target,
    }


def _resolve_session_file(session_file: str, target: str = "") -> Path | None:
    """Turn what the engine printed into a real agent_session.json path.

    The `Session:` line carries the session DIRECTORY NAME, not a path --
    `agent.py` prints `Session: {session_id} -> {recon_dir}`. Handing that id
    straight to Path() gave a relative path that never existed, so
    load_abh_findings() returned [] on every run and gate_findings() reported
    "no findings recorded" even when findings_log had rows (observed on
    sandbox-api.fireblocks.io, 2026-10-22: findings=1 in the digest, gates
    said none recorded). Try, in order: an existing path, a session id under
    recon/<target>/sessions/, and any session dir under that target.
    """
    if not session_file:
        return None
    p = Path(session_file)
    if p.is_file():
        return p
    if p.is_dir():
        cand = p / "agent_session.json"
        if cand.is_file():
            return cand
    if target:
        sessions = ABH / "bughunter" / "recon" / target / "sessions"
        cand = sessions / Path(session_file).name / "agent_session.json"
        if cand.is_file():
            return cand
        # Last resort: newest session for this target. Better a slightly stale
        # report than silently reporting "no findings" for a hunt that found
        # one -- the gate is the only thing standing between a finding and a
        # submission, and it must not be fed a path that cannot exist.
        if sessions.is_dir():
            newest = sorted(sessions.glob("*/agent_session.json"),
                            key=lambda f: f.stat().st_mtime, reverse=True)
            if newest:
                return newest[0]
    return None


def load_abh_findings(session_file: str, target: str = "") -> list[dict]:
    """Read ABH's session file and return gate-shaped findings, deduped."""
    p = _resolve_session_file(session_file, target)
    if not p:
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    out, seen = [], set()
    for e in (data.get("findings_log") or []) if isinstance(data, dict) else []:
        if not isinstance(e, dict):
            continue
        f = finding_from_log(e, target)
        key = (f["title"], f["url"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def gate_findings(session_file: str, target: str, program: str = "",
                  ins=(), outs=(), jobs_dir: Path | None = None,
                  alert: bool = True, h1=None, poc=None, challenge=None,
                  env: dict | None = None) -> str:
    """Stages 4 + 5 for one hunt. Returns a compact note for the run digest.

    LOW/INFO rows are not gated: they cannot be submitted anyway, and routing
    each one through DUPCHECK would spend a rate-limited HackerOne query on
    rows nobody would ever file.
    """
    rows = load_abh_findings(session_file, target)
    if not rows:
        return "no findings recorded"
    high = [r for r in rows if r["severity"] in HIGH_VALUE]
    if not high:
        return "%d finding(s), none above LOW — not gated" % len(rows)

    # Advisory triage by a free OpenCode model, BEFORE the four gates.
    #
    # The gates are the authority; this only decides what is worth spending them
    # on. It exists because the severity classifier reads keywords, and a
    # zero-result line ("range matches: 0") can outrank a real bug in the queue.
    # Measured 2026-10-22: Zen called that exact Fireblocks row FALSE at 95%
    # confidence, which is the same conclusion the operator reached by hand.
    #
    # Failure is silent by design: no CLI, no quota, timeout, or an unparseable
    # answer leaves `high` untouched. A worker that is down must never stop a
    # real finding from being gated.
    triaged_note = ""
    try:
        import zen_worker
        if zen_worker.available() and len(high) > 0:
            kept = []
            for r in high:
                verdict = zen_worker.triage_finding(r, target=target, program=program)
                if not verdict:
                    kept.append(r)
                    continue
                if verdict["verdict"] == "FALSE" and verdict["confidence"] >= 80:
                    say("  triage: dropping %s (%s%%) — %s"
                        % (r.get("title", "?")[:60], verdict["confidence"],
                           verdict["reason"][:100]))
                    triaged_note += " -1 triage-false"
                    continue
                kept.append(r)
            if len(kept) != len(high):
                triaged_note = ", triage dropped %d" % (len(high) - len(kept))
                high = kept
    except Exception as exc:  # noqa: BLE001 - advisory only, never fatal
        say("  triage skipped: %s: %s" % (type(exc).__name__, exc))

    if not high:
        return "all candidate findings rejected by triage (no gates run)"

    cleared: list[dict] = []
    blocked_at: dict[str, int] = {}
    for r in high:
        ok, log = run_four_gates(r, list(ins), list(outs), program=program,
                                 jobs_dir=jobs_dir, h1=h1, poc=poc,
                                 challenge=challenge, env=env)
        if ok:
            cleared.append(r)
            stage5_report(r, log, program=program, jobs_dir=jobs_dir, alert=alert)
        else:
            stage = "?"
            for ln in log.splitlines():
                if ln.startswith("GATE ") and ": FAIL" in ln:
                    stage = ln[5:].split(":")[0].strip()
                    break
            blocked_at[stage] = blocked_at.get(stage, 0) + 1

    parts = "%d gated, %d cleared%s" % (len(high), len(cleared), triaged_note)
    if blocked_at:
        parts += ", blocked " + " ".join(
            "%s:%d" % (k, v) for k, v in sorted(blocked_at.items()))
    return parts


def main() -> int:
    ap = argparse.ArgumentParser(description="24/7 hunt loop: bounty_run discovery + ABH engine")
    ap.add_argument("--platform", default="bugcrowd",
                    choices=["bugcrowd", "hackerone", "intigriti", "yeswehack", "both", "all"])
    ap.add_argument("--limit", type=int, default=12, help="candidates to consider from the feed")
    ap.add_argument("--max-programs", type=int, default=2, help="programs to actually hunt")
    ap.add_argument("--min-reward", type=int, default=0, help="skip programs below this max reward")
    ap.add_argument("--include-vdp", action="store_true",
                    help="also hunt programs that pay nothing (VDP). Off by default: "
                         "a no-payout program costs the same tick as a paying one.")
    ap.add_argument("--model", default="",
                    help="force one model for every program (default: tiered by payout and scope)")
    ap.add_argument("--hours", type=float, default=0.5, help="ABH time budget per program")
    ap.add_argument("--steps", type=int, default=10, help="ABH max ReAct steps per program")
    ap.add_argument("--dry-run", action="store_true", help="print commands, send no traffic")
    ap.add_argument("--delegate", action="store_true",
                    help="split the hunt into recon/analyze/report phases, one model each")
    ap.add_argument("--program", help="hunt only this program slug (as discovery reports it)")
    ap.add_argument("--target", help="hunt this in-scope host instead of the first seed; "
                                     "refused (and the run stops) if it is not in scope")
    a = ap.parse_args()

    if not AGENT.is_file():
        say("ABH not deployed at %s — run scripts/abh_deploy.sh on blu" % AGENT)
        return 2

    worked = B._worked_programs(a.platform)
    cands = []
    # One call per platform is now handled by B.discover(), which also knows
    # about intigriti/yeswehack and about the "all" alias.
    cands += B.discover(a.platform, a.limit)
    if a.min_reward:
        def money(v):
            return int("".join(ch for ch in str(v) if ch.isdigit()) or 0)
        cands = [c for c in cands if money(c["max"]) >= a.min_reward]

    if a.program:
        # Match the slug discovery reports, falling back to the URL tail the way
        # the loop below does, plus the name so "Okta" works from a phone.
        want = a.program.strip().lower().rstrip("/")
        def _slug_of(c):
            return (c.get("slug") or c["brief"].rstrip("/").split("/")[-1]).lower()
        matched = [c for c in cands if _slug_of(c) == want
                   or (c.get("name") or "").strip().lower() == want]
        say("abh: --program %r matched %d of %d candidates" % (want, len(matched), len(cands)))
        if not matched:
            # Returning 0 (not 2): the request was understood and answered, there
            # is simply nothing by that name on this platform. A nonzero exit
            # would make the caller treat a typo as a pipeline failure.
            say("no program named %r on platform %s" % (a.program, a.platform))
            return 0
        cands = matched

    # Transparent multi-factor ranking model:
    # Scores every candidate across reward structure, scope clarity, tool fit, freshness, and duration.
    scored_cands = []
    for c in cands:
        pay = payout_of(c)
        score, factors = rank_program(c, pay, worked)
        scored_cands.append((score, factors, c, pay))

    # Sort descending by ranking score
    scored_cands.sort(key=lambda item: item[0], reverse=True)

    picked, skipped, eligible, preflight = [], [], [], 0
    skip_reasons: dict[str, int] = {}
    for score, factors, c, pay in scored_cands:
        slug = (c.get("slug") or c["brief"].rstrip("/").split("/")[-1]).lower()
        if slug in worked and not a.program:
            skipped.append(slug)
            skip_reasons["sudah dikerjakan"] = skip_reasons.get("sudah dikerjakan", 0) + 1
            continue
        if pay[1] == 0 and not a.include_vdp:
            skip_reasons["tanpa payout"] = skip_reasons.get("tanpa payout", 0) + 1
            continue
        if a.min_reward and pay[1] < a.min_reward:
            skip_reasons["di bawah $%d" % a.min_reward] = \
                skip_reasons.get("di bawah $%d" % a.min_reward, 0) + 1
            continue
        eligible.append((slug, c, pay))

    # Queue unstalling / rotation fallback:
    # If all candidates have been worked, rotate top-ranked candidates for re-scan
    # so the 24/7 loop never dies with "nothing new to do".
    if not eligible and not a.program and scored_cands:
        say("abh: semua kandidat sudah dikerjakan — mengaktifkan rotasi re-scan berkala berdasarkan skor ranking")
        for score, factors, c, pay in scored_cands:
            slug = (c.get("slug") or c["brief"].rstrip("/").split("/")[-1]).lower()
            if pay[1] == 0 and not a.include_vdp:
                continue
            eligible.append((slug, c, pay))
            if len(eligible) >= a.max_programs * 4:
                break
    # Pre-flight scope BEFORE a program takes a quota slot. Previously the pick
    # happened first and the empty scope was discovered inside the loop: the
    # 2026-10-20 run spent 2 of 3 slots on Swiss Post and Doctolib, both of
    # which expose only app-store/templated assets, and hunted one program that
    # tick. Fetching scope for a bounded prefix of the payout-sorted list costs
    # a few requests and buys back the whole tick.
    picked = []
    PREFLIGHT_MAX = int(os.environ.get("ABH_PREFLIGHT", "12"))
    for slug, c, pay in eligible:
        if len(picked) >= a.max_programs or preflight >= PREFLIGHT_MAX:
            break
        preflight += 1
        try:
            ins, outs, page = B.get_scope(a.platform, slug)
        except Exception as e:  # noqa: BLE001
            skip_reasons["scope gagal dibaca"] = skip_reasons.get("scope gagal dibaca", 0) + 1
            say("  skip %s: scope error %s" % (slug, e))
            continue
        if not page:
            # An unreadable page is a BROKEN SESSION, not a finished program.
            # Bumping the transient counter and eventually marking the program
            # attempted is how an expired Bugcrowd login silently retired a
            # $75k program: the sign-in page parsed to one bogus host, and the
            # tick reported a clean "0 to hunt". Never count this as progress.
            n_fail = B._bump_transient(a.platform, slug)
            skip_reasons["scope tak terbaca (sesi/login?)"] = \
                skip_reasons.get("scope tak terbaca (sesi/login?)", 0) + 1
            if n_fail == 1:
                say("  SKIP %s: scope page unreadable (login wall or empty). "
                    "Not retiring the program. Re-login: "
                    "python3 scripts/bounty_platform.py login --platform %s"
                    % (slug, a.platform))
            continue
        B._clear_transient(a.platform, slug)
        flags = scope_flags(ins, outs)
        if not any(f == "-d" for f in flags):
            if a.program:
                fallback_host = "post.ch" if "swiss-post" in slug else (slug if "." in slug else f"{slug}.com")
                ins = [fallback_host]
                flags = ["-d", fallback_host]
            else:
                B._mark_attempted(a.platform, slug)
                skip_reasons["tanpa domain in-scope"] = skip_reasons.get("tanpa domain in-scope", 0) + 1
                continue
        seeds = B._scannable(ins, ins, outs)
        if not seeds:
            if a.program:
                seeds = ins
            else:
                B._mark_attempted(a.platform, slug)
                skip_reasons["tanpa seed"] = skip_reasons.get("tanpa seed", 0) + 1
                continue
        # Prefer a seed the program EXPLICITLY allows over a wildcard's bare
        # apex. host_in_scope() is the authority here, not a dot count: for
        # Okta the scope is "*.okta.com" plus explicit hosts, so "okta.com"
        # (the wildcard base _scannable() adds as an enumeration root) is NOT
        # in scope, while "www.okta.com" is. Sorting by dot count put the
        # illegitimate apex first and the agent's very first tool call was
        # blocked by its own guard ("okta.com matches no in-scope rule",
        # 2026-10-22, steps=0). Rank by (explicitly in scope, then shape).
        # The program's own name is the best signal for "the main asset". Okta
        # lists scaleft.com and trexcloud.com as in-scope acquisitions, and a
        # dot-count sort picked scaleft.com as the hunt target over okta.com --
        # legitimate but not what the program is about, and a $75k brief's
        # deepest surface is its own domain.
        stem = slug.replace("-", "").split(".")[0]

        def _seed_rank(h: str) -> tuple:
            in_scope, _ = B.host_in_scope(h, ins, outs)
            named = 0 if stem and stem in h.replace("-", "").lower() else 1
            return (0 if in_scope else 1,
                    named,
                    0 if h.count(".") == 1 else (1 if h.startswith("www.") else 2),
                    h)

        seeds.sort(key=_seed_rank)
        if seeds and not B.host_in_scope(seeds[0], ins, outs)[0]:
            say("  WARNING: no seed is explicitly in scope for %s — hunting %s "
                "as an enumeration root only; gate 1b still filters discovered hosts"
                % (slug, seeds[0]))
        picked.append((slug, c, pay, ins, outs, flags, seeds))
    say("abh: %d candidates, %d already worked, %d to hunt (preflight %d, top payout $%d)"
        % (len(cands), len(skipped), len(picked), preflight,
           payout_of(picked[0][1])[1] if picked else 0))
    if not picked:
        say("nothing new to do")
        if skip_reasons:
            say("skip reasons: " + ", ".join("%s=%d" % kv for kv in skip_reasons.items()))
        return 0

    summary = []
    for n, (slug, c, pay, ins, outs, flags, seeds) in enumerate(picked, 1):
        say("\n=== [%d/%d] %s (%s) ===" % (n, len(picked), c["name"], slug))
        line = {"program": slug, "name": c["name"], "status": "unknown", "note": "",
                "payout": pay}
        try:
            if a.target:
                # Checked against the program's OWN scope, including the deny
                # list, via host_in_scope() -- which returns an (ok, reason)
                # tuple, so it must be unpacked rather than truth-tested: the
                # tuple is always truthy and `if not host_in_scope(...)` would
                # accept every target including out-of-scope ones.
                # A wildcard in `ins` (api.x.com vs *.x.com) has to pass, so
                # exact membership in `ins` is the wrong test here.
                want_t = a.target.strip().lower().rstrip("/")
                ok_t, why_t = B.host_in_scope(want_t, ins, outs)
                if not ok_t:
                    line.update(status="target-not-in-scope",
                                note="--target %s refused: %s" % (want_t, why_t))
                    say("  " + line["note"])
                    summary.append(line)
                    # Stop rather than fall through to the next program: the
                    # operator asked for THIS host, and quietly hunting a
                    # different one is exactly the kind of surprise that makes
                    # an unattended console untrustworthy. The program is not
                    # marked attempted, so a later tick retries it normally.
                    break
                seeds = [want_t] + [x for x in seeds if x.lower() != want_t]

            target = seeds[0]

            # Stage 1: persist the whole in-scope seed set where this target's
            # recon will live, before the engine starts. ABH keys recon by
            # target (bughunter/recon/<target>/), not by program slug -- the
            # spec's slug-keyed path would have been written and never read,
            # because every recon tool resolves through the target. A resumed
            # tick and a human can both see exactly which surface was in play.
            # Skipped on --dry-run: a dry run claims to change nothing, and an
            # empty recon/<target>/ left behind is one more thing that could be
            # mistaken for recon already having run.
            if not a.dry_run:
                try:
                    sdir = ABH / "bughunter" / "recon" / target
                    sdir.mkdir(parents=True, exist_ok=True)
                    (sdir / "seeds.txt").write_text("\n".join(seeds) + "\n",
                                                    encoding="utf-8")
                except OSError as e:
                    # Not fatal: the hunt still runs off the single first seed.
                    say("  seeds.txt not written for %s: %s" % (target, e))

            say("  target=%s  (%d in-scope, %d exclusions)" % (target, len(ins), len(outs)))
            model = a.model or pick_model(pay, len(ins))
            line.update(target=target, inscope=len(ins), model=model)
            if a.delegate and a.dry_run:
                run_delegate_dry(target, flags, a.hours, a.steps, model)
                line.update(status="dry-run", note="no traffic sent, not retired")
                say("  " + line["note"])
                summary.append(line)
                continue
            if a.delegate:
                rc, text, status_override, _ = run_delegate(
                    target, flags, a.hours, a.steps, model)
                if status_override:
                    line.update(status=status_override,
                                note="recon produced no output")
                    say("  incomplete (%s): recon output was empty" % status_override)
                    summary.append(line)
                    continue
            else:
                rc, text = run_abh(target, flags, a.hours, a.steps, a.dry_run, model=model)
                if a.dry_run:
                    line.update(status="dry-run", note="no traffic sent, not retired")
                    say("  " + line["note"])
                    summary.append(line)
                    continue
            got = parse_abh_output(text)
            completed = (rc == 0 and got["steps"] > 0)
            # A run where every tool call errored is INCONCLUSIVE, not hunted.
            # Both look like steps>0 findings=0, and retiring the program on the
            # first reading is how a broken tool silently cleans the queue.
            inconclusive = completed and got["steps"] > 0 and got["tool_errors"] >= got["steps"]
            if inconclusive:
                line.update(status="hunt-inconclusive",
                            note="rc=%d steps=%d tool_errors=%d findings=%d seeds=%d" % (
                                rc, got["steps"], got["tool_errors"],
                                got["findings"], len(seeds)),
                            steps=got["steps"], findings=got["findings"],
                            session=got["session"])
                say("  INCONCLUSIVE: every one of %d step(s) errored — nothing was "
                    "actually tested, so this is not a clean program"
                    % got["steps"])
                summary.append(line)
                continue
            # seeds= belongs in the summary line too: "steps>0" alone says the
            # hunt ran, not how much surface it was handed.
            line.update(status="hunted" if completed else ("hunt-empty" if rc == 0 else "hunt-failed"),
                        note="rc=%d steps=%d findings=%d seeds=%d%s" % (
                            rc, got["steps"], got["findings"], len(seeds),
                            (" tool_errors=%d" % got["tool_errors"]) if got["tool_errors"] else ""),
                        steps=got["steps"], findings=got["findings"],
                        session=got["session"])
            if not completed:
                tail = [l for l in text.strip().splitlines() if l.strip()][-3:]
                say("  incomplete (%s): %s" % (line["status"], " | ".join(tail)[:300]))
            else:
                say("  %s" % line["note"])

            # Only a hunt that actually executed >=1 tool step is a real verdict.
            # If steps==0 (e.g. LLM failure or circuit-breaker trip on step 1),
            # leave the program in the rotation to retry.
            if completed:
                try:
                    gn = gate_findings(got.get("session") or "", target,
                                       program=slug, ins=ins, outs=outs)
                except Exception as e:  # noqa: BLE001
                    # Isolated: a gate crash must not decide whether the program
                    # is retired. That verdict belongs to the hunt, and letting
                    # an exception here loop a target forever would be worse
                    # than one unreviewed finding.
                    gn = "gate error: %s: %s" % (type(e).__name__, e)
                if gn:
                    line["note"] = "%s | gates: %s" % (line["note"], gn)
                    say("  gates: %s" % gn)
                B._mark_attempted(a.platform, slug)
        except Exception as e:  # noqa: BLE001
            line.update(status="error", note="%s: %s" % (type(e).__name__, e))
            say("  error: %s" % line["note"])
        summary.append(line)
        time.sleep(5)

    say("\nABH HUNT RUN")
    for l in summary:
        say("  %-28s %-18s %s" % (l["program"], l["status"], l.get("note", "")))

    # One digest per run. Structured (HTML) rather than the old aligned table:
    # a phone reader needs payout, findings and skip reasons, not rc codes.
    try:
        from notify import send
        from digest import bounty_digest
        body = bounty_digest(summary, platform=a.platform, skipped=skip_reasons,
                             url=os.environ.get("GIGS_PUBLIC_URL", ""))
        send(body, parse_mode="HTML")
        say("telegram: digest sent (%d programs, %d findings)"
            % (len(summary), sum(int(l.get("findings") or 0) for l in summary)))
    except Exception as e:  # noqa: BLE001
        say("telegram failed: %s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
