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
    GET  /gigs/abh/sessions           ABH hunt sessions (steps, findings, running)
    GET  /gigs/abh/hunt               result of the last /gigs/abh/hunt
    POST /gigs/abh/hunt               start abh_hunt.py in the background
    POST /gigs/abh/bump               steer the live agent mid-hunt

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
ABH = Path(os.environ.get("ABH_DIR", "/root/Agentic-Bug-Hunter"))
ABH_HUNT = str(REPO / "scripts" / "abh_hunt.py")
ABH_PLATFORMS = ("bugcrowd", "hackerone", "intigriti", "yeswehack", "both", "all")
# Guidance is a steering message, not a scan: it is one file write and the
# agent consumes it on its next step. Cooldown stays short on purpose -- the
# point of the endpoint is to correct a hunt that is going wrong, and a 15
# minute lockout would make that impossible. The write itself is idempotent.
MAX_BUMP_CHARS = 4000

# One scan per cooldown per endpoint. A dispatch is minutes of nmap/httpx plus
# model calls; stacking several just starves the router and the account quota.
COOLDOWN = {
    "dispatch": int(os.environ.get("GIGS_CD_DISPATCH", "900")),
    "triage": int(os.environ.get("GIGS_CD_TRIAGE", "300")),
    "report": int(os.environ.get("GIGS_CD_REPORT", "120")),
    "discover": int(os.environ.get("GIGS_CD_DISCOVER", "300")),
    "new": int(os.environ.get("GIGS_CD_NEW", "30")),
    # One hunt at a time. A run is minutes of recon plus model calls, so a
    # double-tap would double the spend against the same program.
    "hunt": int(os.environ.get("GIGS_CD_HUNT", "900")),
    "bump": int(os.environ.get("GIGS_CD_BUMP", "5")),
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


# ---------- ABH (Agentic-Bug-Hunter) control surface ----------
#
# ABH keeps its state on disk under bughunter/recon/<target>/sessions/<run>/,
# which is why this is all filesystem reading rather than an IPC channel: the
# hunt is a detached `agent.py` process nobody keeps a handle to.


def abh_recon_root() -> Path:
    return ABH / "bughunter" / "recon"


def abh_path(target: str, session: str = "") -> Path:
    """Resolve a target (and optionally one session) under the recon root.

    `target` arrives from an UNAUTHENTICATED request inside the tailnet and is
    used to build a path, so a target of "../../.ssh" must never escape the
    recon directory. SAFE_ID rejects it outright, and the resolve check catches
    anything that still walks out."""
    if not SAFE_ID.match(target or ""):
        raise ValueError("bad target")
    root = abh_recon_root().resolve()
    p = (root / target).resolve()
    if p != root and root not in p.parents:
        raise ValueError("target escapes recon dir")
    if session:
        if not SAFE_ID.match(session):
            raise ValueError("bad session id")
        sp = (p / "sessions" / session).resolve()
        if p not in sp.parents:
            raise ValueError("session escapes target dir")
        return sp
    return p


def abh_processes() -> dict:
    """{agents: {target: pid}, loop: [pid]} — who is running right now.

    Read from /proc rather than `pgrep -f`: the command that runs pgrep carries
    the same search text on its own command line, so it matches itself (that
    already produced a phantom agent in an earlier probe). Requiring argv[0] to
    be a python interpreter additionally drops the ssh/bash wrappers quoting
    the same string."""
    agents: dict = {}
    loop: list = []
    me = os.getpid()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return {"agents": agents, "loop": loop}
    for ent in entries:
        if not ent.isdigit():
            continue
        pid = int(ent)
        if pid == me:
            continue
        try:
            raw = open("/proc/%s/cmdline" % ent, "rb").read().decode("utf-8", "replace")
        except OSError:
            continue
        argv = [a for a in raw.split("\0") if a]
        if not argv or not os.path.basename(argv[0]).startswith("python"):
            continue
        joined = raw.replace("\0", " ")
        if "bughunter/agent.py" in joined:
            m = re.search(r"--target\s+(\S+)", joined)
            if m:
                agents[m.group(1)] = str(pid)
        elif "abh_hunt.py" in joined:
            loop.append(str(pid))
    return {"agents": agents, "loop": loop}


def summarize_session(target: str, sd: Path, running: dict) -> dict:
    """One ABH session as the list view needs it. Never raises: a half-written
    session from a killed hunt must not take down the whole listing."""
    out = {"target": target, "session": sd.name, "path": str(sd),
           "running": target in running}
    try:
        mtime = sd.stat().st_mtime
        # Sub-second precision matters: mtime is the sort key for "newest
        # first", and truncating to int() made two sessions created in the same
        # second tie. A stable sort then fell back to alphabetical, so the
        # "newest" session was really just the one whose name sorted first --
        # and /gigs/abh/bump steered that one instead of the live run.
        out["mtime"] = round(mtime, 3)
        out["age_s"] = int(time.time() - mtime)
    except OSError:
        out["mtime"] = 0

    steps = log_rows = findings = None
    try:
        d = json.loads((sd / "agent_session.json").read_text())
        steps = d.get("step_count")
        fl = d.get("findings_log") or []
        log_rows = len(fl)
        # Our own annotation, never a vulnerability: run_recon's observation is
        # appended with the Stage 2 block whose first line reads "High-value
        # endpoints", and _classify_obs matched the substring "high". Those rows
        # are guidance for the model, so counting them would tell an operator a
        # hunt has findings before any scanner has run. Sessions written before
        # that was fixed still carry them, hence the filter lives here too.
        findings = sum(1 for r in fl
                       if not str((r or {}).get("text", "")).startswith("[SURFACE RANKING]"))
    except FileNotFoundError:
        pass  # only persisted periodically; a live or killed run may have none
    except Exception as e:
        out["session_error"] = str(e)[:120]

    trace_lines, last_tool = 0, None
    try:
        with open(sd / "agent_trace.jsonl", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                trace_lines += 1
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("tool"):
                    last_tool = {"tool": d["tool"], "step": d.get("step")}
    except OSError:
        pass

    out["steps"] = steps if steps is not None else trace_lines
    out["trace_lines"] = trace_lines
    out["findings"] = findings if findings is not None else 0
    out["log_rows"] = log_rows if log_rows is not None else 0
    if last_tool:
        out["last_tool"] = last_tool
    try:
        b = sd / "agent_bump.txt"
        out["bump_pending"] = bool(b.exists() and b.read_text().strip())
    except OSError:
        out["bump_pending"] = False
    return out


def abh_sessions(limit=40, target="") -> list:
    """Every ABH session, newest first."""
    root = abh_recon_root()
    if not root.is_dir():
        return []
    running = abh_processes()["agents"]
    rows = []
    try:
        targets = sorted(root.iterdir())
    except OSError:
        return []
    for tdir in targets:
        if not tdir.is_dir():
            continue
        if target and tdir.name != target:
            continue
        sdir = tdir / "sessions"
        if not sdir.is_dir():
            continue
        try:
            subs = sorted(sdir.iterdir())
        except OSError:
            continue
        for sd in subs:
            if sd.is_dir():
                rows.append(summarize_session(tdir.name, sd, running))
    rows.sort(key=lambda r: r.get("mtime") or 0, reverse=True)
    return rows[:limit]


def abh_active_session() -> Path:
    """Newest session belonging to a RUNNING agent, else None.

    Writing guidance into a session that already finished would be a silent
    no-op -- agent.py reads the bump file on its next step, and a finished run
    has no next step -- so the caller is told there is nothing to steer instead
    of being handed a success message for a write nobody will ever read."""
    running = abh_processes()["agents"]
    if not running:
        return None
    rows = [r for r in abh_sessions(limit=200) if r.get("running")]
    if not rows:
        return None
    return Path(rows[0]["path"])


_last_hunt: dict = {}


def _hunt_bg(argv: list) -> None:
    say("hunt start", argv[2:])
    rc, out, err = run(argv, timeout=7200)
    say("hunt done rc=%s" % rc)
    _last_hunt.update(rc=rc, ended=int(time.time()), stdout=out[-20000:], stderr=err[-4000:])


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
        """Rate-limit one POST endpoint. Truthy means the caller must STOP.

        The 429 is written here; the return value is only a stop-signal. This
        used to `return self._json(429, ...)`, and _json() writes the response
        and returns None -- so every `if (d := self._deny(...))` was falsy and
        the throttled action ran anyway while the client was told to wait. In
        practice that meant a second dispatch or hunt fired inside the window
        that was supposed to prevent exactly that: the API reported "cooldown"
        and started the scan. Pre-existing, not introduced here -- the same
        `return self._json(429, ...)` is in the committed version.
        """
        left = throttled(kind)
        if left:
            say("throttled", kind, left, "s left")
            self._json(429, {"error": "cooldown", "retry_in_s": left,
                             "endpoint": kind})
            return True
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
            if path == "/gigs/abh/sessions":
                q = dict(kv.split("=", 1) for kv in
                         (self.path.split("?", 1)[1].split("&")
                          if "?" in self.path else []) if "=" in kv)
                limit = min(int(q.get("limit", 40) or 40), 200)
                tgt = (q.get("target") or "").strip()
                if tgt and not SAFE_ID.match(tgt):
                    return self._json(400, {"error": "bad target"})
                rows = abh_sessions(limit=limit, target=tgt)
                procs = abh_processes()
                return self._json(200, {
                    "count": len(rows),
                    "abh_dir": str(ABH),
                    "agent_running": procs["agents"],
                    "hunt_loop_pids": procs["loop"],
                    "sessions": rows,
                })
            if path == "/gigs/abh/hunt":
                return self._json(200, _last_hunt or
                                  {"note": "no hunt started from this API yet"})
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
        # Every arrival, including ones that end in a rejection: this API runs
        # unattended, so "which requests actually reached the server, in what
        # order" is the only way to explain a hunt that started when nobody
        # thinks they started one. thread id + client port distinguishes a
        # duplicated connection from a duplicated dispatch.
        say("REQ", "thr=%d" % threading.get_ident(), "port=%s" % self.client_address[1],
            self.command, path)
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

            if path == "/gigs/abh/hunt":
                # Validate BEFORE the cooldown is touched. _deny() stamps the
                # cooldown the moment it runs, so calling it first meant a typo
                # -- a bad platform or slug, a 400 that did no work at all --
                # locked the operator out of hunt for the full 900s. Rate limits
                # exist to stop repeated *work*, not repeated rejections.
                platform = str(body.get("platform") or "bugcrowd").strip().lower()
                if platform not in ABH_PLATFORMS:
                    return self._json(400, {"error": "bad platform",
                                            "allowed": list(ABH_PLATFORMS)})

                # Clamped, because these come from an unauthenticated body and
                # become a subprocess's budget: --hours is a float (0.5 = 30
                # minutes), the rest are ints.
                def _num(key, default, lo, hi):
                    try:
                        v = float(body.get(key) or default)
                    except (TypeError, ValueError):
                        return default
                    return max(lo, min(hi, v))

                def _int(key, default, lo, hi):
                    return int(_num(key, default, lo, hi))

                argv = [PY, ABH_HUNT, "--platform", platform,
                        "--limit", str(_int("limit", 12, 1, 200)),
                        "--max-programs", str(_int("max_programs", 1, 1, 20)),
                        "--hours", "%g" % _num("hours", 0.5, 0.05, 24.0),
                        "--steps", str(_int("steps", 10, 1, 100))]
                program = str(body.get("program") or "").strip()
                if program:
                    if not SAFE_ID.match(program):
                        return self._json(400, {"error": "bad program slug"})
                    argv += ["--program", program]
                tgt = str(body.get("target") or "").strip()
                if tgt:
                    if not SAFE_ID.match(tgt):
                        return self._json(400, {"error": "bad target"})
                    argv += ["--target", tgt]
                if body.get("dry_run"):
                    argv.append("--dry-run")

                if (d := self._deny("hunt")):
                    return d

                _last_hunt.clear()
                _last_hunt.update(argv=argv[2:], started=int(time.time()), rc=None)
                threading.Thread(target=_hunt_bg, args=(argv,), daemon=True).start()
                # 202, not 200: nothing has been hunted yet. A hunt is minutes
                # of recon plus model calls; the caller polls /gigs/abh/hunt or
                # /gigs/abh/sessions for the outcome.
                return self._json(202, {"accepted": True, "cmd": argv[2:],
                                        "cooldown_s": COOLDOWN["hunt"],
                                        "status_url": "/gigs/abh/hunt"})

            if path == "/gigs/abh/bump":
                # Same ordering as hunt: everything that can be rejected is
                # rejected before the cooldown is stamped, so a missing `text`
                # or a traversal attempt costs the caller nothing.
                text = str(body.get("text") or body.get("guidance") or "").strip()
                if not text:
                    return self._json(400, {"error": "text is required"})
                if len(text) > MAX_BUMP_CHARS:
                    return self._json(400, {"error": "guidance too long",
                                            "max": MAX_BUMP_CHARS})
                tgt = str(body.get("target") or "").strip()
                ses = str(body.get("session") or "").strip()
                if tgt:
                    try:
                        sd = abh_path(tgt, ses)
                    except ValueError as e:
                        return self._json(400, {"error": str(e)})
                    if not sd.is_dir():
                        return self._json(404, {"error": "no such session",
                                                "target": tgt, "session": ses})
                else:
                    sd = abh_active_session()
                    if sd is None:
                        return self._json(409, {
                            "error": "no agent is running, so there is nothing to steer",
                            "hint": "pass target+session to write into a specific session, "
                                    "or start one with POST /gigs/abh/hunt"})
                if (d := self._deny("bump")):
                    return d
                # agent.py reads this on every step, injects [OPERATOR GUIDANCE]
                # into working memory, and clears the file once consumed -- so a
                # non-empty file is exactly "a bump is waiting".
                (sd / "agent_bump.txt").write_text(text)
                running = abh_processes()["agents"]
                say("bump", len(text), "chars ->", sd)
                return self._json(200, {
                    "bumped": True, "session": str(sd), "chars": len(text),
                    "target_running": sd.parent.parent.name in running,
                    "note": "agent.py injects this on its next step and clears the file",
                })

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
