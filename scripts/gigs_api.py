#!/usr/bin/env python3
"""Gigs control API — drive the bounty pipeline over HTTP instead of SSH.

WHY THIS EXISTS
---------------
The bounty pipeline already works from a shell (`bounty_run.py discover|new|
dispatch|triage|report`, `gig_finder.py` across 4 platforms), but managing it
from a phone means typing over SSH. grok-bot would be the natural console, but
its sidebar depends on a Cursor backend we only partially emulate, so it is not
a dependable control path yet.

So the control surface is plain HTTP on blu: any client that can reach the
tailnet can list jobs, start a scan, and read results. grok-bot can call it as a
tool later without waiting on the sidebar, and `curl` works today.

    GET  /health                      liveness + what is reachable
    GET  /gigs/jobs                   list jobs (status, program, counts)
    GET  /gigs/jobs/<id>              one job in full
    POST /gigs/discover               run platform discovery
    POST /gigs/new                    create a job from a brief
    POST /gigs/dispatch               scan a job's in-scope hosts
    POST /gigs/triage                 adjudicate a job's findings
    POST /gigs/report                 render a report for a job
    GET  /gigs/findings/<id>          findings for a job
    GET  /gigs/router                 9router model liveness from here

DESIGN NOTES
------------
* Read endpoints are fast and synchronous. Anything that scans is a POST that
  returns 202 with the command echoed, because a real dispatch runs nmap/httpx
  and would hold an HTTP connection open for many minutes — far past any client
  timeout, and past the reverse-tunnel patience.
* Every long action is rate-limited per endpoint. One tap in noVNC can send
  five clicks; without a guard that is five concurrent scans burning the same
  budget and the same account quota.
* Nothing here shells out with a string. Every invocation is argv-list, so a
  program name from a request body can never become a shell token.
* Loopback + tailnet only. There is no auth beyond that, exactly like the
  noVNC/VNC ports already on this box: the tailnet IS the boundary. Binding
  0.0.0.0 here would put an unauthenticated bug-bounty console on the internet.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path.home()
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
BOUNTY_DIR = Path(os.environ.get("BOUNTY_JOBS", str(HOME / ".config" / "auto-freecf" / "bounty")))
PY = os.environ.get("BOUNTY_PY", sys.executable)
ROUTER = os.environ.get("BOUNTY_R9_BASE", "http://127.0.0.1:20128/v1")
BOUNTY = str(REPO / "scripts" / "bounty_run.py")
GIGS = str(REPO / "scripts" / "gig_finder.py")

# One scan per cooldown per endpoint. A dispatch is minutes of nmap/httpx plus
# model calls; stacking several just starves the router and the account quota.
COOLDOWN = {
    "dispatch": int(os.environ.get("GIGS_CD_DISPATCH", "900")),
    "triage": int(os.environ.get("GIGS_CD_TRIAGE", "300")),
    "report": int(os.environ.get("GIGS_CD_REPORT", "120")),
    "discover": int(os.environ.get("GIGS_CD_DISCOVER", "300")),
    "new": int(os.environ.get("GIGS_CD_NEW", "30")),
}
_last: dict[str, float] = {}
_lock = threading.Lock()

# Job ids become path segments and get passed to a CLI; keep them to what our
# own job_dir() can produce.
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def job_path(jid: str) -> Path:
    """Resolve a job id inside BOUNTY_DIR, refusing traversal.

    job.json content is attacker-controlled only if the caller can write there,
    but this endpoint is unauthenticated inside the tailnet, so a job id of
    "../../.ssh" must never escape the jobs directory."""
    if not SAFE_ID.match(jid or ""):
        raise ValueError("bad job id")
    p = (BOUNTY_DIR / jid).resolve()
    root = BOUNTY_DIR.resolve()
    if root not in p.parents and p != root:
        raise ValueError("job id escapes jobs dir")
    return p


def run(argv, timeout=900):
    """Run a pipeline command. argv-list only, never a shell string."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout, cwd=str(REPO))
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %ss" % timeout
    except FileNotFoundError as e:
        return 127, "", str(e)
    return p.returncode, (p.stdout or "")[-20000:], (p.stderr or "")[-4000:]


def throttled(kind: str) -> int:
    """Seconds left on a cooldown, or 0 when the action may run."""
    with _lock:
        now = time.time()
        prev = _last.get(kind, 0.0)
        left = COOLDOWN[kind] - (now - prev)
        if left > 0:
            return int(left) + 1
        _last[kind] = now
        return 0


def summarize_job(d: Path) -> dict:
    """One job as the list view needs it. Never raises: a corrupt job must not
    take down the whole listing."""
    out = {"id": d.name, "path": str(d)}
    try:
        j = json.loads((d / "job.json").read_text())
    except Exception as e:
        out["error"] = "job.json unreadable: %s" % str(e)[:120]
        return out
    out.update({
        "platform": j.get("platform"),
        "program": j.get("program"),
        "status": j.get("status"),
        "created": j.get("created"),
        "in_scope": len(j.get("in_scope") or []),
        "out_of_scope": len(j.get("out_of_scope") or []),
        "targets": j.get("targets") or [],
        "recon_counts": j.get("recon_counts") or {},
    })
    try:
        mtime = (d / "job.json").stat().st_mtime
        out["age_s"] = int(time.time() - mtime)
    except Exception:
        pass
    return out


def findings(jid: str) -> list:
    """Findings for a job, tolerating every layout the pipeline has used."""
    d = job_path(jid)
    found = []
    for sub in ("findings", "triage", "recon"):
        p = d / sub
        if not p.is_dir():
            continue
        for f in sorted(p.rglob("*.json")):
            try:
                found.append({"file": str(f.relative_to(d)), "data": json.loads(f.read_text())})
            except Exception:
                found.append({"file": str(f.relative_to(d)), "error": "unparseable"})
        if found:
            break
    return found


_router_cache: tuple[float, dict] = (0.0, {})


def router_models(max_age=60) -> dict:
    """Is the model path up? Called by /health, so it must never raise.

    Cached because the router takes ~8 s to answer /v1/models over the tunnel,
    which made every /health call (the thing a client polls to decide whether
    the API is alive) block for 8 s. A stale-by-60 s answer is still the right
    answer for a liveness probe."""
    global _router_cache
    at, cached = _router_cache
    if cached and time.time() - at < max_age:
        return cached
    import urllib.request
    out = {"base": ROUTER, "ok": False}
    try:
        key = os.environ.get("BOUNTY_R9_KEY", "")
        req = urllib.request.Request(ROUTER.rstrip("/") + "/models",
                                     headers={"Authorization": "Bearer " + key} if key else {})
        with urllib.request.urlopen(req, timeout=25) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        out["ok"] = True
        out["models"] = [m.get("id") for m in (body.get("data") or [])][:12]
    except Exception as e:
        out["error"] = str(e)[:160]
    _router_cache = (time.time(), out)
    return out


class H(BaseHTTPRequestHandler):
    server_version = "gigs/1.0"

    def log_message(self, *a):
        pass

    # ---------- helpers ----------
    def _json(self, status, obj):
        b = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("content-length") or 0)
            if n > 1_000_000:
                return {}
            return json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return {}

    def _deny(self, kind):
        left = throttled(kind)
        if left:
            say("throttled", kind, left, "s left")
            return self._json(429, {"error": "cooldown", "retry_in_s": left,
                                    "endpoint": kind})
        return None

    # ---------- GET ----------
    def do_GET(self):
        path = (self.path or "/").split("?")[0].rstrip("/") or "/"
        try:
            if path in ("/", "/health"):
                return self._json(200, {
                    "ok": True, "service": "gigs-api",
                    "jobs_dir": str(BOUNTY_DIR), "router": router_models(),
                    "cooldowns": COOLDOWN,
                })
            if path == "/gigs/jobs":
                if not BOUNTY_DIR.is_dir():
                    return self._json(200, {"jobs": [], "note": "no jobs dir"})
                jobs = [summarize_job(d) for d in sorted(BOUNTY_DIR.iterdir()) if d.is_dir()]
                return self._json(200, {"count": len(jobs), "jobs": jobs})
            if path == "/gigs/router":
                return self._json(200, router_models())
            m = re.match(r"^/gigs/jobs/([A-Za-z0-9][A-Za-z0-9._-]{0,120})$", path)
            if m:
                d = job_path(m.group(1))
                if not d.is_dir():
                    return self._json(404, {"error": "no such job", "id": m.group(1)})
                return self._json(200, summarize_job(d))
            m = re.match(r"^/gigs/findings/([A-Za-z0-9][A-Za-z0-9._-]{0,120})$", path)
            if m:
                f = findings(m.group(1))
                return self._json(200, {"id": m.group(1), "count": len(f), "findings": f})
            return self._json(404, {"error": "no such endpoint", "path": path})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": str(e)[:200]})

    # ---------- POST ----------
    def do_POST(self):
        path = (self.path or "/").split("?")[0].rstrip("/") or "/"
        body = self._body()
        try:
            if path == "/gigs/discover":
                if (d := self._deny("discover")):
                    return d
                argv = [PY, BOUNTY, "discover",
                        "--platform", str(body.get("platform") or "both"),
                        "--limit", str(int(body.get("limit") or 25))]
                rc, out, err = run(argv, timeout=420)
                return self._json(200, {"cmd": argv[2:], "rc": rc, "stdout": out, "stderr": err})

            if path == "/gigs/new":
                if (d := self._deny("new")):
                    return d
                argv = [PY, BOUNTY, "new",
                        "--platform", str(body.get("platform") or ""),
                        "--program", str(body.get("program") or ""),
                        "--in-scope", str(body.get("in_scope") or "")]
                if body.get("out_of_scope"):
                    argv += ["--out-of-scope", str(body["out_of_scope"])]
                if body.get("target"):
                    argv += ["--target", str(body["target"])]
                if not body.get("platform") or not body.get("program") or not body.get("in_scope"):
                    return self._json(400, {"error": "platform, program and in_scope are required"})
                rc, out, err = run(argv, timeout=180)
                return self._json(200, {"cmd": argv[2:], "rc": rc, "stdout": out, "stderr": err})

            if path in ("/gigs/dispatch", "/gigs/triage", "/gigs/report"):
                kind = path.rsplit("/", 1)[1]
                jid = str(body.get("id") or "")
                try:
                    job_path(jid)
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                if not (BOUNTY_DIR / jid).is_dir():
                    return self._json(404, {"error": "no such job", "id": jid})
                if (d := self._deny(kind)):
                    return d
                argv = [PY, BOUNTY, kind, jid]
                if kind == "dispatch" and body.get("dry_run"):
                    argv.append("--dry-run")
                if kind == "dispatch" and body.get("allow_stale_scope"):
                    argv.append("--allow-stale-scope")
                # Synchronous, because the caller asked for the outcome and the
                # UI has nothing to poll yet. Generous but bounded; a hung scan
                # returns 124 rather than pinning a thread forever.
                say("start", kind, jid)
                rc, out, err = run(argv, timeout=int(body.get("timeout") or 1800))
                say("done", kind, jid, "rc", rc)
                return self._json(200, {"cmd": argv[2:], "rc": rc, "stdout": out[-12000:],
                                        "stderr": err[-3000:]})

            return self._json(404, {"error": "no such endpoint", "path": path})
        except Exception as e:
            return self._json(500, {"error": str(e)[:200]})


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="gigs_api.py")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8795)
    a = ap.parse_args()
    BOUNTY_DIR.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((a.host, a.port), H)
    srv.daemon_threads = True
    say("gigs-api on http://%s:%d (jobs=%s)" % (a.host, a.port, BOUNTY_DIR))
    srv.serve_forever()


if __name__ == "__main__":
    main()
