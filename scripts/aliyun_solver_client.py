#!/usr/bin/env python3
"""aliyun_solver_client.py — Python client for the Happy-DOM Aliyun Captcha Solver.

Solves Aliyun Captcha 2.0 / 3.0 in ~1.5s in memory without headless browsers or Playwright.
Supports both running daemon (http://127.0.0.1:8783/solve) and direct CLI fallback via Bun.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

SOLVER_PORT = 8783
SOLVER_URL = f"http://127.0.0.1:{SOLVER_PORT}/solve"
SCRIPT_TS = Path(__file__).resolve().parent / "aliyun_happy_solver.ts"


def solve_happy_aliyun(
    scene: str = "11xygtvd",
    region: str = "sgp",
    prefix: str = "no8xfe",
    proxy: str | None = None,
    timeout: float = 12.0,
) -> dict | None:
    """Solve Aliyun Captcha via Happy-DOM and return {'ok': True, 'verifyParam': '...', 'region': '...'}.

    Returns None if solving fails or times out.
    """
    # 1. Try local daemon if running
    try:
        body = {"scene": scene, "region": region, "prefix": prefix}
        if proxy:
            body["proxy"] = proxy
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            SOLVER_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            if data.get("ok"):
                return data
    except Exception:
        pass

    # 2. Fall back to CLI execution via Bun
    bun_bin = shutil.which("bun") or "/home/amen/.bun/bin/bun"
    if Path(bun_bin).exists() and SCRIPT_TS.exists():
        try:
            cmd = [
                bun_bin,
                "run",
                str(SCRIPT_TS),
                "--scene",
                scene,
                "--region",
                region,
                "--prefix",
                prefix,
            ]
            if proxy:
                cmd.extend(["--proxy", proxy])
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 3)
            if res.returncode == 0 and res.stdout.strip():
                # Filter out [captcha-solver] info lines
                lines = [l for l in res.stdout.splitlines() if not l.startswith("[")]
                data = json.loads("\n".join(lines))
                if data.get("ok"):
                    return data
        except Exception as e:
            print(f"[aliyun_solver_client] Bun CLI error: {e}", file=sys.stderr)

    return None


if __name__ == "__main__":
    print("Testing solve_happy_aliyun from Python...")
    result = solve_happy_aliyun()
    if result:
        print(f"✓ Solved successfully in {result.get('durationMs')}ms!")
        print(f"  Region: {result.get('region')}")
        print(f"  Param:  {result.get('verifyParam', '')[:50]}...")
    else:
        print("✗ Failed to solve captcha.")
