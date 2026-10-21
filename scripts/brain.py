#!/usr/bin/env python3
"""brain — one supervisor that plans, then delegates to the existing executors.

WHY THIS EXISTS
---------------
Today each capability is its own CLI with its own argument shape: abh_hunt for
bounties, gig_finder for offers, gig_exec for delivery, plus raw shell. Choosing
between them, in the right order, is the part a human has been doing by hand —
and it is the only part that is still hand-driven. That choice belongs to one
brain, not to four scripts that do not know about each other.

DESIGN
------
* The brain does NOT execute the work. It produces a plan and dispatches each
  step to a worker that already exists. One model call, N subprocesses.
* The worker registry is a CLOSED allowlist. A plan naming anything else is
  rejected at validation, so a model that hallucinates `rm -rf` gets an error,
  not a shell. This is the same rule the gateway applies to client tools.
* Every step's stdout/stderr lands in a run directory, with the plan that
  produced it. A run is reconstructable and re-runnable without the model.
* Argv is a list. There is no shell string anywhere, so a program name from a
  plan cannot become a shell token.

USAGE
    brain.py run "cari gig yang bisa dieksekusi agent, lalu kerjakan satu"
    brain.py run "..." --dry-run        # plan only, run nothing
    brain.py run "..." --max-steps 3
    brain.py show                       # list recent runs
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HOME = Path.home()
RUNS = HOME / ".config" / "auto-freecf" / "brain"
ROUTER = os.environ.get("BOUNTY_R9_BASE", "http://127.0.0.1:20128/v1")
PY = os.environ.get("BOUNTY_PY", sys.executable)

# Planner model: strong but fast. Measured 2026-10-20 on blu: 3.7s.
PLANNER = os.environ.get("BRAIN_PLANNER", "ag/gemini-3.8-flash-high")
PLANNER_FALLBACKS = os.environ.get(
    "BRAIN_PLANNER_FALLBACKS",
    "ocz/mimo-v2.6-flash-free,THK/deepseek-v4.1-flash:free",
).split(",")


def _load_env() -> None:
    for path in (os.environ.get("ABH_ENV", ""), str(HOME / "abh.env"),
                 str(HOME / ".config" / "auto-freecf" / ".env")):
        if not path:
            continue
        p = Path(path)
        if not p.is_file():
            continue
        for line in p.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.removeprefix("export ").split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def say(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- workers
# Each entry: how to build argv from a plan step, and the cap on its runtime.
WORKERS = {
    "gig_find": {
        "argv": lambda a: [PY, str(HERE / "gig_finder.py")],
        "timeout": 420,
        "about": "discover gigs/bounties into the local store",
    },
    "gig_exec": {
        "argv": lambda a: [PY, str(HERE / "gig_exec.py"), "--id", str(a.get("id", ""))]
                           + (["--dry-run"] if a.get("dry_run") else []),
        "timeout": 2400,
        "about": "execute one gig from the store into a workspace",
    },
    "hunt": {
        "argv": lambda a: [PY, str(HERE / "abh_hunt.py"),
                           "--platform", str(a.get("platform", "all")),
                           "--limit", str(int(a.get("limit", 40))),
                           "--max-programs", str(int(a.get("max_programs", 1))),
                           "--hours", str(a.get("hours", 0.5)),
                           "--steps", str(int(a.get("steps", 10)))]
                           + (["--dry-run"] if a.get("dry_run") else []),
        "timeout": 3600,
        "about": "hunt bug-bounty programs with the ABH engine",
    },
    "gigs_status": {
        "argv": lambda a: [PY, "-c", (
            "import json,pathlib;"
            "p=pathlib.Path.home()/'.config/auto-freecf/gigs/latest.json';"
            "d=json.loads(p.read_text()) if p.exists() else {};"
            "print(len(d),'gigs');"
            "print(json.dumps(sorted(d.values(),key=lambda g:-(g.get('usd') or [0,0])[1])[:5],ensure_ascii=False)[:1500])")],
        "timeout": 60,
        "about": "report the current gig store (read-only)",
    },
    "abuse_check": {
        "argv": lambda a: [PY, str(HERE / "r9_conncheck.py")],
        "timeout": 300,
        "about": "check which model routes actually answer",
    },
}


def _decode(raw: str) -> dict:
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except ValueError:
        pass
    for ln in reversed(raw.splitlines()):
        if ln.startswith("data:"):
            body = ln[5:].strip()
            if body and body != "[DONE]":
                try:
                    return json.loads(body)
                except ValueError:
                    continue
    i = raw.find("{")
    if i >= 0:
        try:
            return json.JSONDecoder().raw_decode(raw[i:])[0]
        except ValueError:
            pass
    raise RuntimeError(f"unparseable plan response: {raw[:200]!r}")


def plan(goal: str, max_steps: int) -> dict:
    catalog = "\n".join(f"- {k}: {v['about']} (args: {sorted(set(['id','platform','limit','max_programs','hours','steps','dry_run']))})"
                        for k, v in WORKERS.items())
    prompt = f"""You are the planner of an autonomous engineering pipeline. Decompose the goal
into at most {max_steps} steps, each delegated to exactly one worker below.

WORKERS
{catalog}

GOAL
{goal}

Reply with ONLY a JSON object, no markdown fence:
{{"reasoning": "<one sentence>",
  "steps": [{{"worker": "<name from WORKERS>", "args": {{}}, "why": "<short>"}}]}}

Rules:
- Use only the workers listed. Never invent a worker.
- Prefer the smallest plan that reaches the goal. One step is a valid plan.
- If the goal needs an id that is not known yet, run the discovery step first,
  then use the literal string "$TOP_GIG_ID" as the id: the supervisor replaces
  it with the highest-payout gig id seen in earlier step output.
"""
    key = os.environ.get("OPENAI_API_KEY", "")
    err = None
    for model in [PLANNER] + [m.strip() for m in PLANNER_FALLBACKS if m.strip()]:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            ROUTER.rstrip("/") + "/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}" if key else ""})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                text = ((_decode(r.read().decode("utf-8", "replace")).get("choices")
                         or [{}])[0].get("message") or {}).get("content", "")
            parsed = _decode(text)
            parsed["model"] = model
            validate(parsed, max_steps)
            return parsed
        except Exception as e:  # noqa: BLE001
            err = e
            say(f"planner {model} failed: {type(e).__name__}: {e}")
    raise RuntimeError(f"no planner produced a valid plan: {err}")


def validate(p: dict, max_steps: int) -> None:
    steps = p.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("plan has no steps")
    if len(steps) > max_steps:
        raise ValueError(f"plan has {len(steps)} steps, cap is {max_steps}")
    for s in steps:
        w = s.get("worker")
        if w not in WORKERS:
            raise ValueError(f"unknown worker {w!r}")
        if not isinstance(s.get("args", {}), dict):
            raise ValueError("step args must be an object")


def _top_gig_id(text: str) -> str:
    """Pull the highest-payout gig id out of a worker's output.

    gigs_status prints a JSON array sorted by payout, so the first object is the
    answer. This is the one piece of hand-off state the supervisor needs today;
    without it a plan cannot act on what a previous step found.
    """
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            items = json.loads(line)
        except ValueError:
            continue
        for it in items:
            if isinstance(it, dict) and it.get("id"):
                return str(it["id"])
    return ""


def run_step(step: dict, outdir: Path, idx: int, context: dict) -> dict:
    args = dict(step.get("args") or {})
    if args.get("id") == "$TOP_GIG_ID":
        args["id"] = context.get("TOP_GIG_ID", "")
        if not args["id"]:
            return {"index": idx, "worker": step["worker"], "args": args, "rc": 126,
                    "seconds": 0.0, "why": step.get("why", ""), "argv": [],
                    "stdout_tail": "", "stderr_tail": "no TOP_GIG_ID in context"}
    argv = WORKERS[step["worker"]]["argv"](args)
    timeout = WORKERS[step["worker"]]["timeout"]
    say(f"[{idx}] {step['worker']} {step.get('args') or {}}  ({step.get('why','')})")
    t0 = time.time()
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           cwd=str(REPO))
        rc, out, err = p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        rc, out, err = 124, "", f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        rc, out, err = 125, "", f"{type(e).__name__}: {e}"
    rec = {"index": idx, "worker": step["worker"], "args": args,
           "why": step.get("why", ""), "argv": argv[1:], "rc": rc,
           "seconds": round(time.time() - t0, 1),
           "stdout_tail": out[-8000:], "stderr_tail": err[-3000:]}
    (outdir / f"step-{idx}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False))
    say(f"    rc={rc} in {rec['seconds']}s")
    return rec


def run(goal: str, max_steps: int, dry: bool) -> int:
    RUNS.mkdir(parents=True, exist_ok=True)
    runid = time.strftime("%Y%m%d-%H%M%S")
    outdir = RUNS / runid
    outdir.mkdir()
    (outdir / "goal.txt").write_text(goal)

    p = plan(goal, max_steps)
    (outdir / "plan.json").write_text(json.dumps(p, indent=2, ensure_ascii=False))
    say(f"plan ({p['model']}): {p.get('reasoning','')}")
    for i, s in enumerate(p["steps"], 1):
        say(f"  {i}. {s['worker']} {s.get('args') or {}} — {s.get('why','')}")
    if dry:
        say("dry run: nothing executed")
        return 0

    context: dict = {}
    results = []
    for i, s in enumerate(p["steps"], 1):
        rec = run_step(s, outdir, i, context)
        gid = _top_gig_id(rec.get("stdout_tail", ""))
        if gid:
            context["TOP_GIG_ID"] = gid
            say(f"    context: TOP_GIG_ID={gid}")
        results.append(rec)
    ok = all(r["rc"] == 0 for r in results)
    summary = ["# Brain run " + runid, "", f"**Goal:** {goal}", "",
               f"**Planner:** {p['model']} — {p.get('reasoning','')}", "",
               "| # | worker | rc | seconds | args |", "|---|---|---|---|---|"]
    for r in results:
        summary.append(f"| {r['index']} | {r['worker']} | {r['rc']} | {r['seconds']} | "
                       f"`{json.dumps(r['args'], ensure_ascii=False)}` |")
    (outdir / "summary.md").write_text("\n".join(summary) + "\n")
    say(f"run dir: {outdir}  ({'ok' if ok else 'FAILED'})")
    return 0 if ok else 1


def show() -> int:
    if not RUNS.is_dir():
        print("no runs yet")
        return 0
    for d in sorted(RUNS.iterdir(), reverse=True)[:10]:
        s = d / "summary.md"
        head = s.read_text().splitlines()[0] if s.exists() else "(no summary)"
        print(f"{d.name}  {head}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="brain.py", description="supervisor: plan, then delegate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="plan and execute")
    r.add_argument("goal")
    r.add_argument("--max-steps", type=int, default=3)
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("show", help="list recent runs")
    sub.add_parser("workers", help="list delegated workers")
    a = ap.parse_args(argv)
    if a.cmd == "show":
        return show()
    if a.cmd == "workers":
        for k, v in WORKERS.items():
            print(f"{k:14s} {v['about']}")
        return 0
    return run(a.goal, a.max_steps, a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
