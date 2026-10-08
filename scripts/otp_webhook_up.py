#!/usr/bin/env python3
"""
scripts/otp_webhook_up.py — bring up the Litensi SMS webhook + public tunnel.

Starts the local listener (sms_webhook.py) and a cloudflared quick tunnel to it,
waits for the public https URL, prints it (and saves it), so you can paste it
into Litensi -> Callback SMS.

Quick-tunnel URLs are EPHEMERAL — a new random URL each start, and it 404/1033s
the moment cloudflared stops. Keep this running while you use it.

Usage:
  python scripts/otp_webhook_up.py up        # start listener + tunnel, print URL
  python scripts/otp_webhook_up.py url       # print the current public URL
  python scripts/otp_webhook_up.py down      # stop both
  python scripts/otp_webhook_up.py status
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

CFG = Path.home() / ".config" / "auto-freecf"
RUN = CFG / "otp_webhook"
PIDF = RUN / "pids"
URLF = RUN / "public_url.txt"
LISTEN_LOG = RUN / "listener.log"
CF_LOG = RUN / "cloudflared.log"
PORT = 8799


def _py() -> str:
    for p in (Path.home() / ".local/share/auto-freecf/venv/bin/python", sys.executable):
        if Path(p).exists():
            return str(p)
    return sys.executable


def _cloudflared() -> str | None:
    for c in (Path.home() / ".local/bin/cloudflared", "/usr/local/bin/cloudflared", "/usr/bin/cloudflared"):
        if Path(c).exists():
            return str(c)
    from shutil import which
    return which("cloudflared")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def up() -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    down(quiet=True)  # clean any previous
    scripts = Path(__file__).resolve().parent
    logs = {"listener": LISTEN_LOG.open("w"), "cf": CF_LOG.open("w")}
    listener = subprocess.Popen([_py(), str(scripts / "sms_webhook.py"), "--port", str(PORT)],
                                stdout=logs["listener"], stderr=subprocess.STDOUT)
    cf = _cloudflared()
    if not cf:
        print("  ✗ cloudflared not found. Install: curl -L -o ~/.local/bin/cloudflared "
              "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x ~/.local/bin/cloudflared")
        listener.terminate()
        return 1
    cfp = subprocess.Popen([cf, "tunnel", "--url", f"http://127.0.0.1:{PORT}", "--no-autoupdate"],
                           stdout=logs["cf"], stderr=subprocess.STDOUT)
    PIDF.write_text(f"{listener.pid}\n{cfp.pid}\n")
    url = None
    for _ in range(40):
        time.sleep(1.5)
        try:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", CF_LOG.read_text())
        except Exception:
            m = None
        if m:
            url = m.group(0)
            break
        if not _alive(cfp.pid):
            print("  ✗ cloudflared exited; see", CF_LOG)
            return 1
    if not url:
        print("  ✗ no tunnel URL after 60s; see", CF_LOG)
        return 1
    URLF.write_text(url)
    print(f"  ✓ listener : http://127.0.0.1:{PORT}")
    print(f"  ✓ public   : {url}")
    print(f"\n  Paste this into Litensi -> Callback SMS -> URL webhook, then Simpan:\n    {url}/sms")
    print(f"\n  (ephemeral — new URL every start; stop with: python {Path(__file__).name} down)")
    return 0


def down(quiet: bool = False) -> int:
    n = 0
    if PIDF.exists():
        for line in PIDF.read_text().split():
            try:
                pid = int(line)
                os.kill(pid, signal.SIGTERM)
                n += 1
            except Exception:
                pass
        PIDF.unlink(missing_ok=True)
    URLF.unlink(missing_ok=True)
    if not quiet:
        print(f"  stopped {n} process(es)")
    return 0


def status() -> int:
    pids = PIDF.read_text().split() if PIDF.exists() else []
    alive = [p for p in pids if _alive(int(p))]
    url = URLF.read_text().strip() if URLF.exists() else "(none)"
    print(f"  running: {len(alive)}/{len(pids)} processes | public: {url}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the SMS webhook + tunnel")
    ap.add_argument("action", nargs="?", default="up", choices=["up", "down", "url", "status"])
    a = ap.parse_args(argv)
    if a.action == "up":
        return up()
    if a.action == "down":
        return down()
    if a.action == "url":
        print(URLF.read_text().strip() if URLF.exists() else "(no tunnel running)")
        return 0
    return status()


if __name__ == "__main__":
    sys.exit(main())