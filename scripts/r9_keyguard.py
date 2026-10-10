#!/usr/bin/env python3
"""
r9_keyguard — keep 9Router stocked: watch healthy keys, auto-farm when low.

9Router already fails over on its own (a 429 always falls through to the next
account, then the next combo model — verified in chunks/4049.js and 8910.js of
the installed build). What it does NOT do is notice that you are running out of
accounts. This module is that missing half.

"Healthy key" = a providerConnection that could serve a request right now:
  - isActive
  - testStatus == "active"
  - no modelLock_* timestamp still in the future (a per-model rate-limit lock)
  - no rateLimitedUntil in the future
  - errorCode not a hard-dead code (401/402/403/404 = the credential itself is
    gone; those never come back on their own)

Anything with errorCode 429 IS counted as unhealthy while locked, because that
is the "free daily quota used up" state -- exactly what should trigger a farm.

Usage (CLI):
  python3 scripts/r9_keyguard.py status              # one-shot table, never farms
  python3 scripts/r9_keyguard.py watch               # loop; farms at <= --threshold
  python3 scripts/r9_keyguard.py farm                # force one autofarm run now
  python3 scripts/r9_keyguard.py reset               # forget the cooldown

Stdlib only, no network needed for the pure helpers (unit-tested).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

NINE_ROUTER_HOME = Path(os.environ.get("NINE_ROUTER_HOME", Path.home() / ".9router"))
AUTO_FREECF = Path(os.environ.get("AUTO_FREECF", Path.home() / "Auto-FreeCF"))
STATE_PATH = Path(os.environ.get(
    "R9_KEYGUARD_STATE",
    Path.home() / ".config" / "auto-freecf" / "r9_keyguard.json",
))

DEFAULT_BASE = "http://localhost:20128"

# A credential that answers 401/402/403/404 is dead, not busy. It will not come
# back by waiting, so it must not be counted as a usable key.
DEAD_ERROR_CODES = frozenset({401, 402, 403, 404})

# How the 429 text is phrased by different free providers. Used only to label a
# locked key in `status`; the lock itself is detected from errorCode/timestamps.
QUOTA_HINTS = ("quota", "used up", "rate limit", "rate_limit", "exceeded", "reset")


def _now_ms() -> float:
    return time.time() * 1000.0


def _parse_ts(value) -> float:
    """ISO-8601 (or epoch ms) -> epoch ms; 0.0 when absent/unparseable."""
    if value in (None, "", 0):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.isdigit():
        return float(text)
    try:
        from datetime import datetime

        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000.0
    except Exception:  # noqa: BLE001
        return 0.0


def active_lock_until(conn: dict, now_ms: float | None = None) -> float:
    """Earliest future modelLock_* timestamp, else 0.0."""
    now = _now_ms() if now_ms is None else now_ms
    best = 0.0
    for key, value in (conn or {}).items():
        if not str(key).startswith("modelLock_") or not value:
            continue
        ts = _parse_ts(value)
        if ts > now and (best == 0.0 or ts < best):
            best = ts
    return best


def is_locked(conn: dict, now_ms: float | None = None) -> bool:
    """True when a rate limit (429) currently blocks this connection."""
    now = _now_ms() if now_ms is None else now_ms
    if active_lock_until(conn, now) > 0:
        return True
    return _parse_ts((conn or {}).get("rateLimitedUntil")) > now


def classify(conn: dict, now_ms: float | None = None) -> str:
    """healthy | quota | dead | inactive -- pure, so tests can pin it."""
    now = _now_ms() if now_ms is None else now_ms
    c = conn or {}
    if not c.get("isActive"):
        return "inactive"
    code = c.get("errorCode")
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    if code in DEAD_ERROR_CODES:
        return "dead"
    if is_locked(c, now) or code == 429:
        return "quota"
    if c.get("testStatus") in ("error", "unavailable"):
        return "quota"
    return "healthy"


def summarize(connections, now_ms: float | None = None) -> dict:
    """Group connections by health. Used by both `status` and `watch`."""
    now = _now_ms() if now_ms is None else now_ms
    buckets: dict[str, list] = {"healthy": [], "quota": [], "dead": [], "inactive": []}
    for c in connections or []:
        buckets[classify(c, now)].append(c)
    return {
        "counts": {k: len(v) for k, v in buckets.items()},
        "healthy": len(buckets["healthy"]),
        "quota": buckets["quota"],
        "dead": buckets["dead"],
        "by_error": dict(Counter(
            str(c.get("errorCode")) for c in buckets["quota"] + buckets["dead"]
        )),
    }


def should_farm(healthy: int, threshold: int) -> bool:
    """Farm when healthy keys are at or below the floor."""
    return healthy <= threshold


def quota_reason(conn: dict) -> str:
    """Short human label for why a key is locked."""
    err = str((conn or {}).get("lastError") or "")
    low = err.lower()
    if any(h in low for h in QUOTA_HINTS):
        return "quota exhausted"
    if (conn or {}).get("errorCode") == 429:
        return "rate limited (429)"
    return "unavailable"


# ── 9Router HTTP (CLI token auth, same scheme as kancahub._r9_cli_token) ──

def cli_token(home: Path = NINE_ROUTER_HOME) -> str:
    """sha256(machineId + '9r-cli-auth' + cliSecret)[:16]."""
    env = os.environ.get("R9_TOKEN") or os.environ.get("NINE_ROUTER_CLI_TOKEN")
    if env:
        return env.strip()
    try:
        mid = (home / "machine-id").read_text().strip()
        sec = (home / "auth" / "cli-secret").read_text().strip()
        return hashlib.sha256((mid + "9r-cli-auth" + sec).encode()).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return ""


def api_get(path: str, base: str = DEFAULT_BASE, timeout: float = 15.0) -> tuple[int, str]:
    req = urllib.request.Request(base.rstrip("/") + path, method="GET")
    req.add_header("x-9r-cli-token", cli_token())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def fetch_connections(base: str = DEFAULT_BASE):
    """-> (connections, error). error is a string when the router is unreachable."""
    code, body = api_get("/api/providers", base=base)
    if code != 200:
        return None, f"HTTP {code}: {body[:150]}"
    try:
        return json.loads(body).get("connections", []), None
    except Exception as e:  # noqa: BLE001
        return None, f"bad JSON: {e}"


# ── cooldown state (so a farm run does not retrigger every loop) ──────────

def load_state(path: Path = STATE_PATH) -> dict:
    try:
        return json.loads(path.read_text()) or {}
    except Exception:  # noqa: BLE001
        return {}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, sort_keys=True))
    except Exception:  # noqa: BLE001
        pass


def cooldown_left(state: dict, cooldown_s: float, now: float | None = None) -> float:
    """Seconds left before another farm is allowed; 0.0 when allowed now."""
    now = time.time() if now is None else now
    last = float((state or {}).get("lastFarmAt") or 0)
    return max(0.0, (last + cooldown_s) - now)


def mark_farmed(state: dict, healthy: int, now: float | None = None) -> dict:
    state = dict(state or {})
    state["lastFarmAt"] = time.time() if now is None else now
    state["lastFarmHealthyCount"] = healthy
    return state


# ── the actual farm trigger ──────────────────────────────────────────────

def run_autofarm(url: str | None, *, dry_run: bool = False, headless: bool = True,
                 timeout: float = 3600.0) -> int:
    """Shell out to scripts/autofarm.py. Returns its exit code (or 1 on spawn fail)."""
    script = AUTO_FREECF / "scripts" / "autofarm.py"
    if not script.exists():
        print(f"✗ autofarm.py not found at {script}", file=sys.stderr)
        return 1
    cmd = [sys.executable, str(script)]
    if url:
        cmd.append(url)
    if headless:
        cmd.append("--headless")
    if dry_run:
        print("  [dry-run] " + " ".join(cmd))
        return 0
    print("  [farm] " + " ".join(cmd), flush=True)
    try:
        return subprocess.call(cmd, cwd=str(AUTO_FREECF), timeout=timeout)
    except subprocess.TimeoutExpired:
        print("  [farm] timed out", file=sys.stderr)
        return 124
    except Exception as e:  # noqa: BLE001
        print(f"  [farm] failed to start: {e}", file=sys.stderr)
        return 1


# ── rendering ────────────────────────────────────────────────────────────

def _label(conn: dict) -> str:
    return str(conn.get("name") or conn.get("email") or conn.get("id", "")[:8])


def render_status(summ: dict, *, threshold: int, show_locked: bool = True) -> list[str]:
    counts = summ["counts"]
    lines = [
        "── 9router keyguard ──────────────────────────",
        f"  healthy keys : {summ['healthy']}   (farm at <= {threshold})",
        f"  quota-locked : {counts['quota']}",
        f"  dead creds   : {counts['dead']}",
        f"  inactive     : {counts['inactive']}",
    ]
    if summ["by_error"]:
        lines.append("  errors       : " + ", ".join(
            f"{k}x{v}" for k, v in sorted(summ["by_error"].items(), key=lambda kv: -kv[1])
        ))
    if show_locked and summ["quota"]:
        lines.append("")
        for c in summ["quota"][:15]:
            psd = c.get("providerSpecificData") or {}
            tag = psd.get("prefix") or str(c.get("provider", ""))[:14]
            lines.append(f"   • {_label(c):<34} {tag:<14} {quota_reason(c)}")
        if len(summ["quota"]) > 15:
            lines.append(f"   … +{len(summ['quota']) - 15} more")
    return lines


# ── commands ─────────────────────────────────────────────────────────────

def cmd_status(a) -> int:
    conns, err = fetch_connections(base=a.base)
    if err:
        print(f"✗ 9Router unreachable at {a.base} — {err}")
        print("  is it running?  kancahub doctor")
        return 1
    summ = summarize(conns)
    for line in render_status(summ, threshold=a.threshold):
        print(line)
    return 0


def cmd_farm(a) -> int:
    headless = not getattr(a, "no_headless", False)
    rc = run_autofarm(getattr(a, "url", None), dry_run=a.dry_run, headless=headless)
    if rc == 0 and not a.dry_run:
        save_state(mark_farmed(load_state(getattr(a, "state", STATE_PATH)), 0))
    return rc


def cmd_watch(a) -> int:
    print(f"  watching {a.base} every {a.interval}s — farming at <= {a.threshold} healthy keys")
    print("  Ctrl-C to stop\n")
    try:
        while True:
            conns, err = fetch_connections(base=a.base)
            if err:
                print(f"  ! router unreachable: {err}", flush=True)
            else:
                summ = summarize(conns)
                left = cooldown_left(load_state(a.state), a.cooldown)
                flag = "FARM" if (should_farm(summ["healthy"], a.threshold) and left == 0.0) else "ok"
                print(f"  healthy={summ['healthy']:<3} locked={summ['counts']['quota']:<3} "
                      f"dead={summ['counts']['dead']:<3} cooldown={int(left)}s  [{flag}]", flush=True)
                if should_farm(summ["healthy"], a.threshold) and left == 0.0:
                    print(f"  → {summ['healthy']} healthy keys (<= {a.threshold}) — farming", flush=True)
                    save_state(mark_farmed(load_state(a.state), summ["healthy"]))
                    run_autofarm(getattr(a, "url", None), dry_run=a.dry_run,
                                 headless=not getattr(a, "no_headless", False))
            time.sleep(max(5.0, a.interval))
    except KeyboardInterrupt:
        print("\n  stopped")
        return 0


def cmd_reset(a) -> int:
    state_path = getattr(a, "state", STATE_PATH)
    try:
        state_path.unlink()
        print(f"  ✓ cleared {state_path}")
    except FileNotFoundError:
        print(f"  (nothing to clear at {state_path})")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ {e}")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="9Router keyguard: watch healthy keys, auto-farm when low")
    sub = ap.add_subparsers(dest="action")

    def common(p, *, farm_url=None):
        p.add_argument("--base", default=DEFAULT_BASE, help="9Router base URL")
        p.add_argument("--threshold", type=int, default=2, help="farm when healthy <= this (default 2)")
        p.add_argument("--url", default=farm_url, help="target URL for autofarm")
        p.add_argument("--state", type=Path, default=STATE_PATH, help="cooldown state file")
        p.add_argument("--dry-run", action="store_true", help="print actions, do nothing")

    s = sub.add_parser("status", help="one-shot table; never farms")
    common(s)

    w = sub.add_parser("watch", help="loop and farm automatically when keys run low")
    common(w)
    w.add_argument("--interval", type=float, default=300.0, help="seconds between polls (default 300)")
    w.add_argument("--cooldown", type=float, default=3600.0, help="min seconds between farms (default 3600)")
    w.add_argument("--no-headless", action="store_true", help="show the browser during a farm run")

    f = sub.add_parser("farm", help="force one autofarm run now")
    common(f)

    r = sub.add_parser("reset", help="clear the cooldown so the next check farms immediately")
    r.add_argument("--state", type=Path, default=STATE_PATH)
    r.add_argument("--base", default=DEFAULT_BASE, help=argparse.SUPPRESS)
    r.add_argument("--threshold", type=int, default=2, help=argparse.SUPPRESS)
    r.add_argument("--url", default=None, help=argparse.SUPPRESS)
    r.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if not getattr(a, "action", None):
        ap.print_help()
        return 0
    return {"status": cmd_status, "watch": cmd_watch, "farm": cmd_farm,
            "reset": cmd_reset}[a.action](a)


if __name__ == "__main__":
    raise SystemExit(main())