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
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bounty_run as B  # noqa: E402

ABH = Path(os.environ.get("ABH_DIR", "/root/Agentic-Bug-Hunter"))
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
    for pat in in_scope:
        h = B._host_of(pat)
        if not h or h in seen or B._is_placeholder_host(h):
            continue
        if h in ("apps.apple.com", "play.google.com", "itunes.apple.com"):
            continue
        seen.add(h)
        args += ["-d", h]
    for pat in out_of_scope:
        h = B._host_of(pat)
        if not h or B._is_placeholder_host(h):
            continue
        args += ["-x", h]
    return args


def run_abh(target: str, flags: list[str], hours: float, steps: int,
            dry: bool) -> tuple[int, str]:
    cmd = [sys.executable, str(AGENT), "--target", target, *flags,
           "--time", str(hours), "--max-steps", str(steps)]
    if dry:
        return 0, "DRY RUN: " + " ".join(cmd)
    say("  $ " + " ".join(cmd))
    p = subprocess.run(cmd, cwd=str(ABH), env=abh_env(),
                       capture_output=True, text=True, timeout=int(hours * 3600) + 600)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


FINDINGS_RE = re.compile(r"Findings:\s+(\d+)")
SESSION_RE = re.compile(r"Session:\s+(\S+)")


def parse_abh_output(text: str) -> dict:
    """Pull the summary numbers out of ABH's stdout."""
    out = {"findings": 0, "session": "", "steps": 0}
    m = FINDINGS_RE.search(text)
    if m:
        out["findings"] = int(m.group(1))
    m = SESSION_RE.search(text)
    if m:
        out["session"] = m.group(1)
    m = re.search(r"Steps executed:\s+(\d+)", text)
    if m:
        out["steps"] = int(m.group(1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="24/7 hunt loop: bounty_run discovery + ABH engine")
    ap.add_argument("--platform", default="bugcrowd", choices=["bugcrowd", "hackerone", "both"])
    ap.add_argument("--limit", type=int, default=12, help="candidates to consider from the feed")
    ap.add_argument("--max-programs", type=int, default=2, help="programs to actually hunt")
    ap.add_argument("--min-reward", type=int, default=0, help="skip programs below this max reward")
    ap.add_argument("--hours", type=float, default=0.5, help="ABH time budget per program")
    ap.add_argument("--steps", type=int, default=10, help="ABH max ReAct steps per program")
    ap.add_argument("--dry-run", action="store_true", help="print commands, send no traffic")
    a = ap.parse_args()

    if not AGENT.is_file():
        say("ABH not deployed at %s — run scripts/abh_deploy.sh on blu" % AGENT)
        return 2

    worked = B._worked_programs(a.platform)
    cands = []
    if a.platform in ("bugcrowd", "both"):
        cands += B.discover_bugcrowd(a.limit)
    if a.platform in ("hackerone", "both"):
        cands += B.discover_hackerone(a.limit)
    if a.min_reward:
        def money(v):
            return int("".join(ch for ch in str(v) if ch.isdigit()) or 0)
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

    say("abh: %d candidates, %d already worked, %d to hunt"
        % (len(cands), len(skipped), len(picked)))
    if not picked:
        say("nothing new to do")
        return 0

    summary = []
    for n, (slug, c) in enumerate(picked, 1):
        say("\n=== [%d/%d] %s (%s) ===" % (n, len(picked), c["name"], slug))
        line = {"program": slug, "name": c["name"], "status": "unknown", "note": ""}
        try:
            ins, outs, page = B.get_scope(a.platform, slug)
            if not page:
                # Transient: count it, retire only after repeated failures. See
                # bounty_run.MAX_TRANSIENT for why a single blank render must not
                # permanently retire a paying program.
                n_fail = B._bump_transient(a.platform, slug)
                if n_fail >= B.MAX_TRANSIENT:
                    B._mark_attempted(a.platform, slug)
                    line.update(status="no-session",
                                note="empty page %d/%d, retired" % (n_fail, B.MAX_TRANSIENT))
                else:
                    line.update(status="retry",
                                note="empty page %d/%d" % (n_fail, B.MAX_TRANSIENT))
                say("  " + line["note"])
                summary.append(line)
                continue
            B._clear_transient(a.platform, slug)

            flags = scope_flags(ins, outs)
            if not any(f == "-d" for f in flags):
                B._mark_attempted(a.platform, slug)
                line.update(status="no-scope-pattern",
                            note="no matchable in-scope domain (app-store/templated only)")
                say("  " + line["note"])
                summary.append(line)
                continue

            # ABH targets one primary host (while all -d/-x flags remain active
            # for scope enforcement). Prefer apex / www.* over obscure regional
            # subdomains that sort first alphabetically (e.g. badger.it.vinted.com).
            seeds = B._scannable(ins, ins, outs)
            if not seeds:
                B._mark_attempted(a.platform, slug)
                line.update(status="no-seed", note="no enumerable in-scope host")
                say("  " + line["note"])
                summary.append(line)
                continue

            seeds.sort(key=lambda h: (0 if h.count(".") == 1 else (1 if h.startswith("www.") else 2), h))
            target = seeds[0]
            say("  target=%s  (%d in-scope, %d exclusions)" % (target, len(ins), len(outs)))
            rc, text = run_abh(target, flags, a.hours, a.steps, a.dry_run)
            if a.dry_run:
                # A dry run sends no traffic and proves nothing about the
                # program, so it must never retire it. It used to: run_abh()
                # returns rc=0 for a dry run, the completion check below saw a
                # success, and _mark_attempted() consumed the program. Two dry
                # runs quietly burned two real targets out of the rotation.
                line.update(status="dry-run", note="no traffic sent, not retired")
                say("  " + line["note"])
                summary.append(line)
                continue
            got = parse_abh_output(text)
            completed = (rc == 0 and got["steps"] > 0)
            line.update(status="hunted" if completed else ("hunt-empty" if rc == 0 else "hunt-failed"),
                        note="rc=%d steps=%d findings=%d" % (rc, got["steps"], got["findings"]),
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
                B._mark_attempted(a.platform, slug)
        except Exception as e:  # noqa: BLE001
            line.update(status="error", note="%s: %s" % (type(e).__name__, e))
            say("  error: %s" % line["note"])
        summary.append(line)
        time.sleep(5)

    say("\nABH HUNT RUN")
    for l in summary:
        say("  %-28s %-18s %s" % (l["program"], l["status"], l.get("note", "")))

    # One digest per run, same contract as bounty_run.py auto.
    try:
        from notify import send
        body = "ABH hunt run\n\n" + "\n".join(
            "%-28s %-18s %s" % (l["program"], l["status"], l.get("note", ""))
            for l in summary)
        send(body)
    except Exception as e:  # noqa: BLE001
        say("telegram failed: %s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
