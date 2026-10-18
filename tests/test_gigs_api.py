#!/usr/bin/env python3
"""gigs_api.py — the HTTP control surface for the 24/7 bounty pipeline (Task 4).

This API is the only way to drive ABH without SSH, so its guards are all
load-bearing. Three of them had silently rotted or never worked at all:

* **The cooldown never blocked anything.** `_deny()` did
  `return self._json(429, ...)`, and `_json()` writes the response and returns
  **None** — so every `if (d := self._deny(...))` was falsy. The client was
  told "cooldown, retry in 900s" while the throttled dispatch/hunt ran anyway.
  Pre-existing, and it means the rate limiter on `dispatch` (900s) had been
  firing duplicate scans the whole time. The test below pins the return value.

* **A rejected request consumed the cooldown.** `_deny()` stamped the window
  before validation ran, so a typo (bad platform, a 400 that did no work) locked
  the operator out of `hunt` for the full 15 minutes. Validation now runs first.

* `target`/`session` come from an UNAUTHENTICATED tailnet request and become a
  filesystem path, so `../../.ssh` has to be refused.

Sessions live under `bughunter/recon/<target>/sessions/<run>/`, which is why
this is filesystem reading rather than IPC: a hunt is a detached `agent.py` that
nobody holds a handle to.

Standalone (no pytest):  python3 tests/test_gigs_api.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

# Forced, not setdefault: on blu ABH_DIR points at the REAL recon tree, and a
# test that wrote into a live session's agent_bump.txt would silently steer a
# running hunt. These paths are always under a throwaway directory.
_TMP = Path(tempfile.mkdtemp(prefix="gigs_api_test_"))
os.environ["ABH_DIR"] = str(_TMP / "abh")
os.environ["BOUNTY_JOBS"] = str(_TMP / "jobs")

import gigs_api as G  # noqa: E402

fails = 0


def check(cond, label):
    global fails
    print("%-4s %s" % ("OK" if cond else "FAIL", label))
    if not cond:
        fails += 1


def serve():
    srv = G.ThreadingHTTPServer(("127.0.0.1", 0), G.H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def call(port, method, path, payload=None):
    """Return (status, json-body). HTTP errors are returned, not raised."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), data=data, method=method,
        headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except ValueError:
            body = {}
        return e.code, body


def make_abh():
    """A synthetic recon tree with the three shapes the listing must survive."""
    root = G.ABH / "bughunter" / "recon"

    # Full session: session json + trace + a bump waiting to be read.
    s1 = root / "test.example.com" / "sessions" / "20261019T000000000000-aaaa1111"
    s1.mkdir(parents=True)
    (s1 / "agent_session.json").write_text(json.dumps({
        "step_count": 7,
        "findings_log": [
            # Our own annotation, not a vulnerability.
            {"tool": "run_recon", "severity": "HIGH",
             "text": "[SURFACE RANKING] High-value endpoints for this target (ranked):"},
            {"tool": "run_vuln_scan", "severity": "CRITICAL",
             "text": "SQL injection in /login param q"},
            {"tool": "run_cors_check", "severity": "MEDIUM",
             "text": "origin reflected with credentials"},
        ],
    }))
    (s1 / "agent_trace.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"event": "tool_call", "step": 0, "tool": "run_recon", "args": {}},
        {"event": "tool_call", "step": 1, "tool": "run_cors_check", "args": {}},
        {"event": "tool_call", "step": 2, "tool": "run_jwt_audit", "args": {}},
    ]) + "\n")
    (s1 / "agent_bump.txt").write_text("focus on the upload endpoint")

    # A killed run: trace only, no session json, no bump file.
    s2 = root / "test.example.com" / "sessions" / "20261019T010000000000-bbbb2222"
    s2.mkdir(parents=True)
    (s2 / "agent_trace.jsonl").write_text(
        json.dumps({"event": "tool_call", "step": 0, "tool": "run_recon", "args": {}}) + "\n")

    # Corrupt session json must not take the whole listing down with it.
    s3 = root / "other.example.com" / "sessions" / "20261019T020000000000-cccc3333"
    s3.mkdir(parents=True)
    (s3 / "agent_session.json").write_text("{not json")
    (s3 / "agent_trace.jsonl").write_text("")
    (s3 / "stray-file.txt").write_text("noise")   # not a directory: must be skipped

    # A target with no sessions dir at all.
    (root / "nologs.example.com").mkdir(parents=True, exist_ok=True)
    return s1


def main():
    global fails
    fails = 0
    s1 = make_abh()

    print("1. the cooldown must actually stop the action")
    # _json() returns None; if _deny() returns that, `if (d := self._deny(...))`
    # never fires and the throttled action runs while the client is told to
    # wait. A stub whose _json returns None the way the real one does makes the
    # failure mode reproducible instead of accidental.
    class Stub:
        def __init__(self):
            self.sent = []

        def _json(self, status, obj):
            self.sent.append((status, obj))
            return None

    stub = Stub()
    G._last.clear()
    check(G.H._deny(stub, "hunt") is None, "first request is allowed")
    check(bool(G.H._deny(stub, "hunt")),
          "second is truthy so the caller stops (was None while _json's return leaked through)")
    check(stub.sent and stub.sent[0][0] == 429 and stub.sent[0][1]["endpoint"] == "hunt",
          "the 429 was still written to the client")
    check(G.H._deny(stub, "triage") is None,
          "an unrelated endpoint has its own window")

    print()
    print("2. over HTTP: one tap starts one hunt, the second starts none")
    srv, port = serve()
    started = []
    real_bg = G._hunt_bg
    G._hunt_bg = lambda argv: started.append(argv)
    try:
        G._last.clear()
        st1, b1 = call(port, "POST", "/gigs/abh/hunt",
                       {"platform": "intigriti", "dry_run": True, "steps": 3})
        deadline = time.time() + 3
        while len(started) == 0 and time.time() < deadline:
            time.sleep(0.05)
        st2, b2 = call(port, "POST", "/gigs/abh/hunt", {"platform": "bugcrowd"})
        time.sleep(0.3)
        check(st1 == 202 and b1.get("accepted") is True, "first tap accepted (202)")
        check(len(started) == 1, "exactly ONE hunt started, not two (%d)" % len(started))
        check(st2 == 429 and b2.get("endpoint") == "hunt" and b2.get("retry_in_s", 0) > 800,
              "second tap inside the window is refused with a real retry hint")
        check(len(started) == 1, "the refused tap did not start a hunt")

        print()
        print("3. a rejected request must not consume the cooldown")
        # Otherwise one typo (a 400 that did no work) locks hunt out for 900s.
        G._last.clear()
        for payload, why in (
                ({"platform": "metasploit"}, "bad platform"),
                ({"program": "../evil"}, "bad program slug"),
                ({"target": "../../etc"}, "bad target")):
            st, body = call(port, "POST", "/gigs/abh/hunt", payload)
            check(st == 400, "%s -> 400" % why)
        st, body = call(port, "POST", "/gigs/abh/hunt", {"platform": "yeswehack"})
        check(st == 202, "the next VALID request is still accepted, not 429")

        print()
        print("4. a bump lands in the right session, and nowhere else")
        G._last.clear()
        st, body = call(port, "POST", "/gigs/abh/bump", {"text": "steer me"})
        check(st == 409, "no agent running -> 409, not a silent write into a dead session")
        st, body = call(port, "POST", "/gigs/abh/bump",
                        {"text": "", "target": "test.example.com"})
        check(st == 400, "empty guidance -> 400")
        st, body = call(port, "POST", "/gigs/abh/bump",
                        {"text": "x" * (G.MAX_BUMP_CHARS + 1)})
        check(st == 400, "oversized guidance -> 400")
        for tgt, ses, why in (("../../.ssh", "", "target traversal"),
                              ("test.example.com", "../x", "session traversal")):
            st, body = call(port, "POST", "/gigs/abh/bump",
                            {"text": "hi", "target": tgt, "session": ses})
            check(st == 400 and "bad" in body.get("error", ""), "%s -> 400" % why)
        st, body = call(port, "POST", "/gigs/abh/bump",
                        {"text": "hi", "target": "test.example.com",
                         "session": "nosuchsession"})
        check(st == 404, "unknown session -> 404")

        st, body = call(port, "POST", "/gigs/abh/bump",
                        {"text": "focus on the upload endpoint",
                         "target": "test.example.com",
                         "session": "20261019T000000000000-aaaa1111"})
        check(st == 200 and body.get("bumped") is True, "valid bump accepted")
        check((s1 / "agent_bump.txt").read_text() == "focus on the upload endpoint",
              "guidance written where agent.py polls for it")

        print()
        print("5. reads: listing, filters and the status endpoint")
        st, body = call(port, "GET", "/gigs/abh/sessions")
        check(st == 200 and body["count"] == 3,
              "3 sessions listed, stray file and session-less target skipped")
        st, body = call(port, "GET", "/gigs/abh/sessions?target=test.example.com")
        check(st == 200 and body["count"] == 2
              and {r["target"] for r in body["sessions"]} == {"test.example.com"},
              "?target= filters to one target")
        st, body = call(port, "GET", "/gigs/abh/sessions?target=../../etc")
        check(st == 400, "?target= traversal -> 400")
        st, body = call(port, "GET", "/gigs/abh/hunt")
        check(st == 200 and body.get("argv", [None])[0] == "--platform",
              "status endpoint echoes the argv that actually ran")
    finally:
        G._hunt_bg = real_bg
        srv.shutdown()

    print()
    print("6. session summary: the ranking note is not counted as a finding")
    rows = {(r["target"], r["session"]): r for r in G.abh_sessions(limit=50)}
    a = rows[("test.example.com", "20261019T000000000000-aaaa1111")]
    check(a["steps"] == 7 and a["trace_lines"] == 3,
          "step_count wins, trace length kept for comparison")
    check(a["findings"] == 2 and a["log_rows"] == 3,
          "3 log rows, 2 real findings (%d vs %d)" % (a["findings"], a["log_rows"]))
    check(a["bump_pending"] is True, "a waiting bump is visible")
    check(a["last_tool"]["tool"] == "run_jwt_audit", "last tool call read from the trace")
    b = rows[("test.example.com", "20261019T010000000000-bbbb2222")]
    check(b["steps"] == 1 and b["findings"] == 0 and b["bump_pending"] is False,
          "a killed run falls back to the trace and reports no findings")
    c = rows[("other.example.com", "20261019T020000000000-cccc3333")]
    check("session_error" in c and c["steps"] == 0,
          "corrupt session json is reported, not raised")
    check(list(rows)[0][1].endswith("cccc3333"), "newest first")

    print()
    print("7. abh_path refuses to escape the recon root")
    for bad in ("../../.ssh", "..", "a/b", "", "/etc", "a b", "."):
        try:
            G.abh_path(bad)
            check(False, "%r refused" % bad)
        except ValueError:
            check(True, "%r refused" % bad)
    good = G.abh_path("test.example.com")
    check(G.abh_recon_root().resolve() in good.parents, "a real target stays inside the root")
    print()
    print("RESULT:", "ALL PASS" if fails == 0 else "%d FAILURES" % fails)
    return fails


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
