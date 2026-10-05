#!/usr/bin/env python3
"""
KancaHub proxy verify — cheap "does it actually hide me?" proof.

Shows your REAL IP, then the IP every proxy path exits from, so you can SEE
whether a mode works before running a signup.  (PetaniProxy's 'Live Proof
Masking' [T], simplified.)

Usage:
  python3 proxy_verify.py                 # check WARP + any local gateway
  python3 proxy_verify.py --proxy http://127.0.0.1:8888
  python3 proxy_verify.py --pool pool.txt # check each proxy in a file
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
WARP = HOME / "Auto-FreeCF" / "scripts" / "warp_manager.py"
VENV_PY = HOME / ".local" / "share" / "auto-freecf" / "venv" / "bin" / "python"

C = {"reset": "\x1b[0m", "bold": "\x1b[1m", "green": "\x1b[32m", "red": "\x1b[31m",
     "yellow": "\x1b[33m", "dim": "\x1b[2m", "cyan": "\x1b[36m"}


def col(n: str, t: str) -> str:
    return f"{C.get(n,'')}{t}{C['reset']}"


def ip_via(proxy: str | None = None, timeout: int = 12) -> str | None:
    cmd = ["curl", "-s", "-m", str(timeout)]
    if proxy:
        cmd += ["-x", proxy]
    cmd += ["https://api.ipify.org"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5).stdout.strip()
        return out or None
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Prove your proxy actually changes your IP")
    ap.add_argument("--proxy", default=None, help="proxy URL to test (e.g. http://127.0.0.1:8888)")
    ap.add_argument("--pool", default=None, help="file of proxies to test")
    ap.add_argument("--gateway", default="http://127.0.0.1:8888", help="default gateway to probe")
    a = ap.parse_args()

    real = ip_via()
    print(col("bold", "\n  Egress proof — your real IP vs masked\n"))
    print(f"  real IP              : {col('yellow', real or 'unknown')}")

    # WARP status (informational)
    try:
        st = subprocess.run([str(VENV_PY), str(WARP), "status"], capture_output=True, text=True, timeout=20).stdout
        warp_on = "🟢 up" in st
        print(f"  WARP tunnel          : {col('green','up') if warp_on else col('dim','down')}")
    except Exception:
        pass

    targets: list[tuple[str, str]] = []
    if a.pool:
        for line in Path(a.pool).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                targets.append((line[:40], line))
    elif a.proxy:
        targets.append((a.proxy, a.proxy))
    else:
        targets.append((a.gateway, a.gateway))

    ok = 0
    for label, url in targets:
        got = ip_via(url)
        if got and got != real:
            print(f"  {col('green','✓ masked')} via {label:42s} -> {col('cyan', got)}")
            ok += 1
        elif got:
            print(f"  {col('red','✗ NOT masked')} via {label:38s} -> {got} (same as real)")
        else:
            print(f"  {col('dim','… no answer')} via {label}")

    print()
    if ok:
        print(col("green", f"  {ok} path(s) successfully hide your IP — good to go."))
    else:
        print(col("yellow", "  No masking yet. Start a gateway first:  kancahub proxy start"))
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
