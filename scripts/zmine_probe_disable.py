#!/usr/bin/env python3
"""Controlled probe: what disables a fresh zmine.si account?

Registers 2 accounts (one referred by RCFZ9GA7, one not), then walks each
through login -> activate -> 2 tasks with human pacing, checking /mining/overview
after every step for the "disabled" state. Prints a timeline so we can see the
exact step that flips the account off.

Read-only wrt production state: writes nothing to zmine_accounts.jsonl.
"""
import json
import sys
import time
import pathlib
import random

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import zmine  # noqa: E402


def probe(name, ref):
    print(f"\n=== {name} (ref={ref}) ===", flush=True)
    code, j = zmine.register(name, ref)
    print(f"  register -> {code} {json.dumps(j)[:120]}", flush=True)
    if code not in (200, 201) or not j.get("loginKey"):
        return
    lk = j["loginKey"]
    handle = (j.get("user") or {}).get("handle")
    print(f"  handle={handle}", flush=True)

    def step(label, fn):
        code, body = fn()
        # a disabled account answers every authed route with 401 + "disabled"
        txt = json.dumps(body)
        disabled = "disabled" in txt
        print(f"  {label} -> {code} disabled={disabled} {txt[:140]}", flush=True)
        return code, body

    time.sleep(random.uniform(8, 14))
    c, u, cookie = zmine.login(lk)
    print(f"  login -> {c} {json.dumps(u)[:100]}", flush=True)
    if c != 200:
        return
    time.sleep(random.uniform(6, 11))
    op, _ = zmine._opener()
    step("activate", lambda: zmine._req(op, "/mining/activate", method="POST", body={}, cookie=cookie))
    time.sleep(random.uniform(5, 9))
    step("overview", lambda: zmine._req(op, "/mining/overview", cookie=cookie))
    for k in (1, 2):
        code, jj = zmine._req(op, "/si/tasks/next", cookie=cookie)
        if code != 200 or not jj.get("task"):
            print(f"  task {k}: next -> {code} {json.dumps(jj)[:120]}", flush=True)
            break
        t = jj["task"]
        idx = zmine.solve(t["prompt"], t["options"])
        print(f"  task {k}: {t['kind']} q={t['prompt'][:60]!r} pick={idx}", flush=True)
        if idx is None:
            break
        time.sleep(random.uniform(5, 9))
        code, r = zmine._req(op, "/si/tasks/answer", method="POST",
                             body={"token": t["token"], "answer": idx}, cookie=cookie)
        print(f"  task {k}: answer -> {code} correct={r.get('correct')} {json.dumps(r)[:120]}", flush=True)
        time.sleep(random.uniform(4, 8))
        step(f"overview after task{k}",
             lambda: zmine._req(op, "/mining/overview", cookie=cookie))


if __name__ == "__main__":
    tag = "".join(random.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(4))
    probe(f"probe{tag}a", "RCFZ9GA7")
    time.sleep(30)
    probe(f"probe{tag}b", None)
