#!/usr/bin/env python3
"""
scripts/zmine.py — ZMINE (zmine.si) miner farm + daily SI task runner.

The SINGLE home for zmine.si work: register a miner, start its free 24 h mining
session, and answer the 12 daily SI tasks (+5.00 H each) that keep the session
paid. There is no captcha on register; the API is a plain Next.js backend.

API (mapped live 2026-10-14):
  POST /api/v1/auth/register  {displayName, referralCode}
        -> {user:{handle,...}, loginKey, recoveryPhrase}
  POST /api/v1/auth/login     {loginKey}          -> Set-Cookie zmine_session
  POST /api/v1/mining/activate                    -> start/renew the 24 h session
  GET  /api/v1/si/tasks/next                      -> {task:{token,prompt,options,kind}, stats}
  POST /api/v1/si/tasks/answer {token, answer}    -> {correct, creditedMh, stats}

Reward rules (from the UI):
  * free session   = +1.00 H / 24 h, must be renewed (activate)
  * 12 tasks/day   = +5.00 H for 24 h per correct answer, cap 12/day
  * 3 wrong in a row -> cooldown, so only answer when confident
  * referral bonus = +2.00 H per active referral, max 25 referrals

Be GENTLE (the user asked for no hammering): a global file lock serialises every
account operation, and `--pace` sleeps between accounts. Multiple processes /
subagents can therefore run safely in parallel.

Usage:
  python3 scripts/zmine.py register --ref RCFZ9GA7
  python3 scripts/zmine.py farm --count 24 --ref RCFZ9GA7 --pace 45
  python3 scripts/zmine.py tick                 # login+activate+tasks for every stored account
  python3 scripts/zmine.py status
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import http.cookiejar
from contextlib import contextmanager
from pathlib import Path

BASE = "https://zmine.si/api/v1"
ORIGIN = "https://zmine.si"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

STATE_DIR = Path(os.environ.get("ZMINE_STATE_DIR", Path.home() / ".config" / "auto-freecf"))
ACCOUNTS = STATE_DIR / "zmine_accounts.jsonl"
LOCKFILE = STATE_DIR / "zmine.lock"
SCRIPTS = Path(__file__).resolve().parent

TASK_MODEL = os.environ.get("ZMINE_TASK_MODEL", "ag/gemini-3.8-flash-high")
R9_BASE = os.environ.get("NINE_ROUTER_BASE", "http://localhost:20128")


def log(msg: str) -> None:
    print(msg, flush=True)


# ─────────────────────────────────────────────────────────────── gentle locking
@contextmanager
def _lock():
    """Serialise account operations across processes/subagents so we never
    hammer zmine.si from several workers at once."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCKFILE, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


# ─────────────────────────────────────────────────────────────── http plumbing
def _opener(jar=None):
    jar = jar if jar is not None else http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("user-agent", UA), ("origin", ORIGIN), ("referer", ORIGIN + "/")]
    return op, jar


def _req(op, path, *, method="GET", body=None, cookie=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"} if body is not None else {}
    if cookie:
        headers["cookie"] = cookie
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with op.open(req, timeout=timeout) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, {"raw": raw[:300]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": f"{type(e).__name__}: {e}"}
    try:
        return 200, json.loads(raw)
    except Exception:  # noqa: BLE001
        return 200, {"raw": raw[:300]}


# ─────────────────────────────────────────────────────────────── account ops
def register(display_name: str, ref: str | None = None, op=None, jar=None):
    op, jar = _opener() if op is None else (op, jar)
    code, j = _req(op, "/auth/register", method="POST",
                   body={"displayName": display_name, "referralCode": ref or None})
    return code, j


def login(login_key: str):
    """Return (code, user, cookie_header)."""
    op, jar = _opener()
    code, j = _req(op, "/auth/login", method="POST", body={"loginKey": login_key})
    cookie = "; ".join(f"{c.name}={c.value}" for c in jar)
    return code, j, cookie


def activate(cookie: str, op=None):
    op, _ = _opener() if op is None else (op, None)
    return _req(op, "/mining/activate", method="POST", body={}, cookie=cookie)


def tasks_next(cookie: str, op=None):
    op, _ = _opener() if op is None else (op, None)
    return _req(op, "/si/tasks/next", cookie=cookie)


def tasks_answer(cookie: str, token: str, answer: int, op=None):
    op, _ = _opener() if op is None else (op, None)
    return _req(op, "/si/tasks/answer", method="POST",
                body={"token": token, "answer": answer}, cookie=cookie)


def mining_overview(cookie: str, op=None):
    op, _ = _opener() if op is None else (op, None)
    return _req(op, "/mining/overview", cookie=cookie)


# ─────────────────────────────────────────────────────────────── task solving
def _r9_key() -> str:
    for p in (Path.home() / ".9router", Path.home() / ".config" / "9router"):
        for name in ("api_key", "key", "apikey"):
            f = p / name
            if f.exists():
                v = f.read_text().strip()
                if v:
                    return v
    try:
        sys.path.insert(0, str(SCRIPTS))
        import captcha_slider as CS  # noqa: PLC0415
        return CS._r9_key()
    except Exception:  # noqa: BLE001
        return os.environ.get("NINE_ROUTER_KEY", "")


ASK = ("Answer this multiple-choice question. Think step by step, then reply with "
       "ONLY the option number (1-based), no words.\n\nQuestion: {q}\nOptions:\n{opts}")


def _ask_model(prompt: str, options: list, model: str = TASK_MODEL) -> int | None:
    key = _r9_key()
    if not key:
        return None
    opts = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(options))
    body = {"model": model, "stream": False, "max_tokens": 200, "temperature": 0,
            "messages": [{"role": "user", "content": ASK.format(q=prompt, opts=opts)}]}
    req = urllib.request.Request(f"{R9_BASE.rstrip('/')}/v1/chat/completions",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"authorization": f"Bearer {key}",
                                          "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode()
    except Exception:  # noqa: BLE001
        return None
    s = ""
    if txt.lstrip().startswith("data:"):
        for ln in txt.splitlines():
            ln = ln.strip()
            if ln.startswith("data:") and ln[5:].strip() not in ("", "[DONE]"):
                try:
                    for ch in json.loads(ln[5:].strip()).get("choices", []):
                        s += ((ch.get("delta") or {}).get("content")) or ""
                except Exception:  # noqa: BLE001
                    pass
    else:
        try:
            s = (json.loads(txt).get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        except Exception:  # noqa: BLE001
            return None
    m = re.search(r"\d+", s or "")
    if not m:
        return None
    idx = int(m.group(0)) - 1
    return idx if 0 <= idx < len(options) else None


def _local_solve(prompt: str, options: list) -> int | None:
    """Cheap deterministic fallback for the arithmetic the bank is full of."""
    p = prompt.lower()
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", prompt)]
    # "what is N% of M"
    mp = re.search(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)", p)
    if mp:
        val = float(mp.group(1)) / 100.0 * float(mp.group(2))
        return _pick(options, val)
    # "one block every S seconds ... per day"
    if "second" in p and ("per day" in p or "a day" in p or "24 h" in p) and len(nums) >= 1:
        val = 86400.0 / nums[0] if nums[0] else None
        if val is not None:
            return _pick(options, val)
    if "per hour" in p and ("per day" in p or "a day" in p) and len(nums) >= 1:
        return _pick(options, nums[0] * 24)
    if "per minute" in p and ("per hour" in p) and len(nums) >= 1:
        return _pick(options, nums[0] * 60)
    return None


def _pick(options: list, val: float) -> int | None:
    best, bi = None, None
    for i, o in enumerate(options):
        d = re.sub(r"[^0-9.\-]", "", str(o))
        if not d:
            continue
        try:
            f = float(d)
        except ValueError:
            continue
        diff = abs(f - val)
        if best is None or diff < best:
            best, bi = diff, i
    if best is None:
        return None
    # refuse a guess that is off by more than 2% (avoid a wrong answer)
    d = re.sub(r"[^0-9.\-]", "", str(options[bi]))
    try:
        if abs(float(d) - val) > max(1e-9, abs(val) * 0.02):
            return None
    except ValueError:
        return None
    return bi


def solve(prompt: str, options: list, model: str = TASK_MODEL, log_fn=log) -> int | None:
    idx = _local_solve(prompt, options)
    if idx is not None:
        return idx
    idx = _ask_model(prompt, options, model=model)
    if idx is not None:
        return idx
    return None


# ─────────────────────────────────────────────────────────────── pipelines
def run_tasks(cookie: str, *, want: int = 12, model: str = TASK_MODEL,
              delay=(4.0, 8.0), log_fn=log) -> dict:
    """Answer up to `want` SI tasks. Stops on the first unsolvable task (never
    gamble: 3 wrong in a row = cooldown). Returns a small stats dict."""
    done = ok = 0
    stats = {}
    for i in range(want):
        code, j = tasks_next(cookie)
        if code != 200 or not j.get("task"):
            reason = (j or {}).get("reason")
            log_fn(f"    tasks: stop ({code} reason={reason})")
            stats = j.get("stats") or stats
            break
        t = j["task"]
        stats = j.get("stats") or stats
        # Both kinds render the same option grid; "verify" just means the answer
        # is checkable arithmetic. Refuse only a kind with no options to pick.
        if t.get("kind") not in (None, "choose", "verify") or not t.get("options"):
            log_fn(f"    task kind={t.get('kind')} unsupported -> stop")
            break
        idx = solve(t.get("prompt", ""), t.get("options") or [], model=model, log_fn=log_fn)
        if idx is None:
            log_fn(f"    task {i + 1}: could not solve -> stop (avoid cooldown)")
            break
        time.sleep(random.uniform(*delay))
        code, r = tasks_answer(cookie, t["token"], idx)
        done += 1
        if code == 200 and r.get("correct"):
            ok += 1
            log_fn(f"    task {i + 1}: correct +{r.get('creditedMh')} mH "
                   f"({r.get('stats', {}).get('correctToday')}/{r.get('stats', {}).get('cap')})")
        else:
            log_fn(f"    task {i + 1}: wrong/err {r.get('correctAnswer')} ({code}) -> stop")
            break
        if (r.get("stats") or {}).get("remainingToday") == 0:
            break
    return {"answered": done, "correct": ok, "stats": stats}


def do_account(login_key: str, *, model: str = TASK_MODEL, want: int = 12, log_fn=log) -> dict:
    """login -> activate session -> answer the daily tasks."""
    code, j, cookie = login(login_key)
    if code != 200 or not cookie:
        log_fn(f"  login failed ({code}) {str(j)[:120]}")
        return {"ok": False, "stage": "login"}
    who = (j.get("user") or {}).get("displayName")
    log_fn(f"  logged in as {who} ({(j.get('user') or {}).get('handle')})")
    code, m = activate(cookie)
    if code == 200:
        hp = m.get("hashpower") or {}
        log_fn(f"  session active={m.get('sessionActive')} total={hp.get('totalMh')} mH")
    else:
        log_fn(f"  activate -> {code} {str(m)[:120]}")
    r = run_tasks(cookie, want=want, model=model, log_fn=log_fn)
    log_fn(f"  tasks: {r['correct']}/{r['answered']} correct")
    return {"ok": True, "display": who, **r}


def _load_accounts() -> list:
    if not ACCOUNTS.exists():
        return []
    out = []
    for ln in ACCOUNTS.read_text().splitlines():
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:  # noqa: BLE001
                pass
    return out


def _save_account(rec: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(ACCOUNTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _rand_name() -> str:
    return "kanca" + "".join(random.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(5))


def farm(count: int, ref: str | None, *, pace: float = 45.0, model: str = TASK_MODEL,
         want: int = 12, log_fn=log) -> list:
    """Register `count` miners (one at a time, under the global lock), start each
    session and answer its tasks. Never hammers: `pace` seconds between accounts."""
    made = []
    for i in range(count):
        if i:
            time.sleep(pace + random.uniform(0, pace * 0.3))
        with _lock():
            name = _rand_name()
            code, j = register(name, ref)
            if code not in (200, 201) or not j.get("loginKey"):
                log_fn(f"[{i + 1}/{count}] register {name} -> {code} {str(j)[:160]}")
                if "already" in str(j).lower() or "unique" in str(j).lower() \
                        or "validation_error" in str(j):
                    time.sleep(20)
                    continue
                time.sleep(30)
                continue
            log_fn(f"[{i + 1}/{count}] registered {name} handle={(j.get('user') or {}).get('handle')}")
            rec = {"displayName": name, "handle": (j.get("user") or {}).get("handle"),
                   "loginKey": j.get("loginKey"), "recoveryPhrase": j.get("recoveryPhrase"),
                   "ts": time.time()}
            _save_account(rec)
            made.append(rec)
            do_account(rec["loginKey"], model=model, want=want, log_fn=log_fn)
    return made


def tick(*, model: str = TASK_MODEL, want: int = 12, pace: float = 20.0, log_fn=log) -> None:
    """Daily maintenance: activate + answer tasks for every stored account."""
    accts = _load_accounts()
    log_fn(f"tick: {len(accts)} stored account(s)")
    for i, a in enumerate(accts):
        if i:
            time.sleep(pace)
        log_fn(f"[{i + 1}/{len(accts)}] {a.get('displayName')} ({(a.get('user') or {}).get('handle') or a.get('handle')})")
        with _lock():
            do_account(a["loginKey"], model=model, want=want, log_fn=log_fn)


# ─────────────────────────────────────────────────────────────── CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="zmine.py", description="ZMINE miner farm + SI tasks")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("register"); s.add_argument("--name"); s.add_argument("--ref", default="RCFZ9GA7")
    s = sub.add_parser("farm")
    s.add_argument("--count", type=int, default=1)
    s.add_argument("--ref", default="RCFZ9GA7")
    s.add_argument("--pace", type=float, default=45.0)
    s.add_argument("--model", default=TASK_MODEL)
    s.add_argument("--want", type=int, default=12)
    s = sub.add_parser("tick")
    s.add_argument("--pace", type=float, default=20.0)
    s.add_argument("--model", default=TASK_MODEL)
    s.add_argument("--want", type=int, default=12)
    s = sub.add_parser("tasks"); s.add_argument("--login-key")
    s = sub.add_parser("status")

    a = ap.parse_args(argv)
    if a.cmd == "register":
        name = a.name or _rand_name()
        code, j = register(name, a.ref)
        if code in (200, 201) and j.get("loginKey"):
            rec = {"displayName": name, "handle": (j.get("user") or {}).get("handle"),
                   "loginKey": j.get("loginKey"), "recoveryPhrase": j.get("recoveryPhrase"),
                   "ts": time.time()}
            _save_account(rec)
            log(f"registered {name} handle={rec['handle']} loginKey={rec['loginKey']}")
        else:
            log(f"register failed {code}: {json.dumps(j)[:300]}")
            return 1
    elif a.cmd == "farm":
        made = farm(a.count, a.ref, pace=a.pace, model=a.model, want=a.want)
        log(f"farm done: {len(made)}/{a.count} created")
    elif a.cmd == "tick":
        tick(model=a.model, want=a.want, pace=a.pace)
    elif a.cmd == "tasks":
        keys = [a.login_key] if a.login_key else [x["loginKey"] for x in _load_accounts()]
        for k in keys:
            with _lock():
                do_account(k)
    elif a.cmd == "status":
        accts = _load_accounts()
        log(f"{len(accts)} account(s) in {ACCOUNTS}")
        for x in accts:
            log(f"  {x.get('displayName'):14s} handle={x.get('handle')} key={str(x.get('loginKey'))[:18]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
