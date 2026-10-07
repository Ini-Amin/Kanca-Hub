#!/usr/bin/env python3
"""x_mail_code.py - mint a relay address, wait for X's verification code.

Manual-phone flow (MIUI blocks adb input, X rejects Waydroid):
  1. run this; it prints an address
  2. type it into the X app on the Redmi ("Use email")
  3. the 6-digit code is printed here when X's mail lands

Usage: python3 scripts/x_mail_code.py [--domain kancalabs.my.id] [--timeout 300]
Log of addresses: ~/x_accounts.md
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import github_farm as g  # reuse the relay client

LOG = Path.home() / "x_accounts.md"
# X codes are 6 digits, also seen in the subject "123456 is your X verification code"
CODE_RE = re.compile(r"\b(\d{6})\b")


def find_code(mails: list[dict]) -> str | None:
    for m in mails:
        text = " ".join(str(m.get(k) or "") for k in ("subject", "text", "body", "html", "source"))
        if re.search(r"\b(x|twitter)\b", text, re.I) and (c := CODE_RE.search(text)):
            return c.group(1)
    return None


def log(email: str, status: str) -> None:
    if not LOG.exists():
        LOG.write_text("# X accounts\n\n| time (UTC) | email | status |\n|---|---|---|\n")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with LOG.open("a") as f:
        f.write(f"| {ts} | {email} | {status} |\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=g.get_relay_config()["domains"][0])
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()

    mb = g.create_relay_mailbox(domain=a.domain)
    print(f"\n  Type this into X on the phone:  {mb['email']}\n  Waiting up to {a.timeout}s for the code...\n")
    log(mb["email"], "minted")
    end = time.time() + a.timeout
    while time.time() < end:
        try:
            code = find_code(g.poll_relay_inbox(mb["jwt"]))
        except Exception as e:  # relay hiccup: keep polling
            print(f"  (poll error: {e})")
            code = None
        if code:
            print(f"  CODE: {code}")
            log(mb["email"], f"code {code}")
            return 0
        time.sleep(4)
    print("  timed out, no code")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
