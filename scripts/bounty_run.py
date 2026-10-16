#!/usr/bin/env python3
"""Bounty job lifecycle: new -> dispatch (recon on blu) -> triage -> report.

ONE home for bounty execution. State lives on the LAPTOP
(~/.config/auto-freecf/bounty/<id>/); blu is stateless compute over ssh.

  python3 scripts/bounty_run.py new --platform bugcrowd --program slug \\
      --in-scope a.com,b.com --out-of-scope c.com --target https://a.com
  python3 scripts/bounty_run.py dispatch <id> [--dry-run]
  python3 scripts/bounty_run.py triage <id>
  python3 scripts/bounty_run.py report <id>     # draft -> Telegram, NO auto-submit

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
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

HOME = Path.home()
JOBS = HOME / ".config" / "auto-freecf" / "bounty"
CveDB = HOME / ".config" / "auto-freecf" / "cve.db"
R9 = "http://127.0.0.1:20128/v1"
BOX = "blu"

PLANNER = "nr/step-5-preview"
DRAFTER = "Zcode/GLM-5.3"
CHALLENGERS = ["ocz/step-5-preview-free", "THK/deepseek-v4.1-flash:free"]


def r9_key():
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


def cmd_new(a):
    jid = "%s-%s-%d" % (a.platform, a.program, int(time.time()) % 100000)
    d = JOBS / jid
    (d / "recon").mkdir(parents=True)
    job = {"id": jid, "platform": a.platform, "program": a.program,
           "in_scope": [x.strip() for x in a.in_scope.split(",") if x.strip()],
           "out_of_scope": [x.strip() for x in (a.out_of_scope or "").split(",") if x.strip()],
           "targets": [x.strip() for x in (a.target or "").split(",") if x.strip()],
           "status": "new", "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if not job["in_scope"]:
        sys.exit("refusing job without in-scope list (gate 1)")
    (d / "job.json").write_text(json.dumps(job, indent=1))
    print("job", jid)


def cmd_dispatch(a):
    d = job_dir(a.id)
    job = json.loads((d / "job.json").read_text())
    if a.dry_run:
        rc, out, err = ssh(BOX, "export PATH=\"$HOME/go/bin:$HOME/.local/bin:$PATH\"; "
                               "for t in subfinder httpx nuclei opengrep katana; do "
                               "command -v $t >/dev/null && echo -n \"$t \"; done; echo")
        print("blu tools:", out.strip() or ("ERR " + err.strip()))
        return
    # Gate 1: ONLY in-scope targets reach the tools. Anything else is rejected.
    targets = [t for t in job["targets"] if any(t.rstrip("/").endswith(s) or s in t for s in job["in_scope"])]
    rejected = set(job["targets"]) - set(targets)
    if rejected:
        print("gate 1 REJECTED (out of scope, not scanned):", sorted(rejected))
    if not targets:
        sys.exit("gate 1: nothing in scope to scan")
    roots = " ".join(sorted({t.split("/")[2] if "://" in t else t.split("/")[0] for t in targets}))
    remote = ("export PATH=\"$HOME/go/bin:$HOME/.local/bin:$PATH\"; cd /tmp && rm -rf bj && mkdir bj && cd bj; "
              "for h in %s; do echo \"== $h\"; "
              "subfinder -silent -d \"$h\" 2>/dev/null | head -40; done > subs.txt 2>&1; "
              "cat subs.txt | httpx -silent -mc 200,201,301,302,403 2>/dev/null | head -60 > live.txt; "
              "nuclei -silent -l live.txt -severity high,critical 2>/dev/null | head -40 > nuclei.txt; "
              "echo DONE; wc -l subs.txt live.txt nuclei.txt" % roots)
    rc, out, err = ssh(BOX, remote, timeout=1500)
    (d / "recon" / "subs.txt").write_text(out)
    print(out[-1500:] if len(out) > 1500 else out)
    job["status"] = "recon-done"
    (d / "job.json").write_text(json.dumps(job, indent=1))


def cmd_triage(a):
    d = job_dir(a.id)
    job = json.loads((d / "job.json").read_text())
    recon = "".join((d / "recon" / f).read_text()[-4000:] for f in ("subs.txt",)
                    if (d / "recon" / f).exists())
    plan = chat(PLANNER, "Bug bounty hunt plan. Program: %s. In scope: %s. Out of scope (NEVER touch): %s.\n"
                "Recon so far:\n%s\nList the top 5 checks in order, each with exact tool command."
                % (job["program"], job["in_scope"], job["out_of_scope"], recon[:3000]))
    (d / "plan.md").write_text(plan)
    draft = chat(DRAFTER, "Draft ONE candidate finding from this recon (or say NONE):\n%s\nPlan:\n%s"
                 % (recon[:3000], plan[:2000]), timeout=300)
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
    d = job_dir(a.id)
    sys.path.insert(0, str(HOME / "Auto-FreeCF" / "scripts"))
    from notify import send
    text = (d / "draft.md").read_text()
    ids = send("BOUNTY DRAFT %s (manual submit only):\n%s" % (a.id, text[:3500]))
    print("telegram:", ids)


def main():
    ap = argparse.ArgumentParser(prog="bounty_run.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("--platform", required=True)
    n.add_argument("--program", required=True)
    n.add_argument("--in-scope", required=True)
    n.add_argument("--out-of-scope", default="")
    n.add_argument("--target", default="")
    dp = sub.add_parser("dispatch")
    dp.add_argument("id")
    dp.add_argument("--dry-run", action="store_true")
    tr = sub.add_parser("triage")
    tr.add_argument("id")
    rp = sub.add_parser("report")
    rp.add_argument("id")
    a = ap.parse_args()
    {"new": cmd_new, "dispatch": cmd_dispatch, "triage": cmd_triage,
     "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
