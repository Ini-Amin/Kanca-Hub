#!/usr/bin/env python3
"""
scripts/r9_modelcheck.py — probe every MODEL the local 9Router advertises.

WHY THIS EXISTS
---------------
`r9_conncheck.py` proves each *connection* answers, using one cheap model per
provider. That is not the same question as "will this model answer?" -- measured
2026-10-19 on blu: `/v1/models` listed 663 entries and never mentioned
`beam-free` or `glm-5.3-flash-free`, yet BOTH answered HTTP 200 when asked for
directly. So the advertised catalogue is not a reliable statement of capacity,
in either direction: it under-reports what works and (because a provider only
updates `testStatus` on a real request) it over-reports what does not.

Every third-party tool we surveyed ships a `selftest`/`check` command that
verifies its own pool before use. Ours did not have the per-model equivalent.
This does, and it is the input model *selection* should use: route to a model
that answered, not to a model that was merely listed.

Usage:
  python scripts/r9_modelcheck.py                      # all free models
  python scripts/r9_modelcheck.py --all                # every advertised model (slow)
  python scripts/r9_modelcheck.py --filter THK         # only ids containing a substring
  python scripts/r9_modelcheck.py --models a,b,c       # an explicit list
  python scripts/r9_modelcheck.py --emit-fallbacks     # print a live-model list for BOUNTY_MODEL_FALLBACKS

Writes ~/.config/auto-freecf/r9_model_health.json (atomic replace) with, per
model: ok, status, ms, and the first line of any error.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROUTER = os.environ.get("NINE_ROUTER_BASE", "http://127.0.0.1:20128")
OUT = Path(os.environ.get("R9_MODEL_HEALTH", Path.home() / ".config" / "auto-freecf" / "r9_model_health.json"))
OPENCODE_CFG = Path(os.environ.get("OPENCODE_CFG", Path.home() / ".config" / "opencode" / "opencode.json"))


def api_key() -> str:
    """The router's own client key, as the rest of the pipeline reads it."""
    env = os.environ.get("NINE_ROUTER_KEY")
    if env:
        return env
    try:
        d = json.loads(OPENCODE_CFG.read_text())
        return d["provider"]["9router"]["options"]["apiKey"]
    except Exception:  # noqa: BLE001
        return ""


def catalogue(key: str, timeout: int = 60) -> list[str]:
    req = urllib.request.Request(f"{ROUTER}/v1/models", headers={"authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return [m["id"] for m in json.load(r).get("data", [])]


def is_free(model: str) -> bool:
    m = model.lower()
    return m.endswith(":free") or m.endswith("-free") or ":free/" in m or "/free" in m


def probe(model: str, key: str, timeout: float, retries: int = 2) -> dict:
    """One tiny non-streaming completion, retried before calling a model dead.

    A single probe is NOT enough to label a model dead: two consecutive sweeps
    11 minutes apart scored the same catalogue 25/63 and 21/63, because
    ocz/nemotron-3.5-lightning-free answered in 0.9s in the first and timed out
    at 45s in the second. Without retries this tool would quietly delete healthy
    models from the failover chain.
    """
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
        "stream": False,
    }).encode()
    last: dict = {"ok": False, "status": 0, "ms": 0, "error": "not attempted"}
    for attempt in range(1, max(1, retries) + 1):
        req = urllib.request.Request(
            f"{ROUTER}/v1/chat/completions", data=body,
            headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                r.read(2048)
                return {"ok": True, "status": r.status, "ms": int((time.time() - t0) * 1000),
                        "error": "", "attempts": attempt}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(300).decode("utf-8", "ignore").replace("\n", " ")
            except Exception:  # noqa: BLE001
                pass
            last = {"ok": False, "status": e.code, "ms": int((time.time() - t0) * 1000),
                    "error": detail[:200]}
        except Exception as e:  # noqa: BLE001 - timeouts, resets: all "does not answer"
            last = {"ok": False, "status": 0, "ms": int((time.time() - t0) * 1000),
                    "error": f"{type(e).__name__}: {e}"[:200]}
        if attempt < retries:
            time.sleep(2 * attempt)
    last["attempts"] = retries
    return last


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="probe every advertised model, not just free ones")
    ap.add_argument("--filter", default=None, help="only ids containing this substring")
    ap.add_argument("--models", default=None, help="comma-separated explicit ids to probe")
    ap.add_argument("--concurrency", type=int, default=6, help="parallel probes (default 6)")
    ap.add_argument("--timeout", type=float, default=45.0, help="per-model timeout in seconds")
    ap.add_argument("--retries", type=int, default=2, help="attempts before a model is called dead (default 2)")
    ap.add_argument("--limit", type=int, default=0, help="probe at most N (0 = no limit)")
    ap.add_argument("--out", default=str(OUT), help="health JSON path")
    ap.add_argument("--emit-fallbacks", action="store_true",
                    help="print only the live models as a comma list (for BOUNTY_MODEL_FALLBACKS)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    key = api_key()
    if not key:
        print("x no router client key found (set NINE_ROUTER_KEY or fix opencode.json)", file=sys.stderr)
        return 2

    if a.models:
        models = [m.strip() for m in a.models.split(",") if m.strip()]
    else:
        try:
            models = catalogue(key)
        except Exception as e:  # noqa: BLE001
            print(f"x cannot read {ROUTER}/v1/models: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        if not a.all:
            models = [m for m in models if is_free(m)]
        if a.filter:
            models = [m for m in models if a.filter.lower() in m.lower()]
        if a.limit:
            models = models[:a.limit]

    if not models:
        print("x nothing to probe", file=sys.stderr)
        return 1

    if not a.quiet:
        print(f"probing {len(models)} model(s) through {ROUTER} "
              f"(concurrency {a.concurrency}, timeout {a.timeout:g}s)")

    results: dict[str, dict] = {}
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=max(1, a.concurrency)) as pool:
        futs = {pool.submit(probe, m, key, a.timeout, a.retries): m for m in models}
        for fut in cf.as_completed(futs):
            m = futs[fut]
            results[m] = fut.result()
            if not a.quiet:
                r = results[m]
                mark = "OK  " if r["ok"] else ("DEAD" if r.get("attempts", 1) > 1 else "ERR ")
                print(f"  {mark} {m:<46} {r['ms']:>6}ms  {r['error'][:60]}")

    live = sorted([m for m, r in results.items() if r["ok"]], key=lambda m: results[m]["ms"])
    dead = [m for m, r in results.items() if not r["ok"]]

    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "router": ROUTER,
        "total": len(results),
        "live": len(live),
        "dead": len(dead),
        "live_models": live,
        "results": results,
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1))
    tmp.replace(out)

    if a.emit_fallbacks:
        print(",".join(live))
        return 0 if live else 1

    if not a.quiet:
        print(f"\n{len(live)}/{len(results)} answered in {time.time() - t0:.0f}s -> {out}")
        if dead:
            print("dead:", ", ".join(sorted(dead)[:12]) + (" ..." if len(dead) > 12 else ""))
    return 0 if live else 1


if __name__ == "__main__":
    sys.exit(main())