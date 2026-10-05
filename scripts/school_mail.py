#!/usr/bin/env python3
"""
School-mail inbox reader for the K-12 flow — reads OpenAI OTP from a real
education mailbox (e.g. you@binus.ac.id, hosted on Microsoft 365 / Outlook).

Why: OpenAI now requires a school-issued email for teacher verification. A real
.edu/.ac.id mailbox we control is the reliable path.

Access methods (tried in order):
  1. IMAP basic auth (imap-mail.outlook.com:993 / outlook.office365.com:993)
     Works if the tenant still allows basic auth (some .ac.id do).
  2. IMAP XOAUTH2 (needs an OAuth2 access token; M365 typically requires this).

Config (~/.config/auto-freecf/.env or env):
    SCHOOL_EMAIL=you@binus.ac.id
    SCHOOL_MAIL_PASSWORD=...            # for basic auth
    SCHOOL_IMAP_HOST=outlook.office365.com   # optional, default below
    SCHOOL_OAUTH_TOKEN=...              # optional, for XOAUTH2

Usage:
    python3 school_mail.py test                       # connect + list last 5
    python3 school_mail.py otp                        # wait for latest OpenAI OTP
    python3 school_mail.py otp --timeout 180
"""

from __future__ import annotations

import argparse
import email
import imaplib
import os
import re
import ssl
import sys
import time
from email.header import decode_header
from pathlib import Path

DEFAULT_HOST = "outlook.office365.com"


def _load_env() -> dict:
    env = {}
    p = Path.home() / ".config" / "auto-freecf" / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_ENV = _load_env()


def cfg(key: str, default: str = "") -> str:
    return os.environ.get(key) or _ENV.get(key, default)


def _decode(s) -> str:
    if not s:
        return ""
    out = []
    for part, enc in decode_header(s):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out)


def connect() -> imaplib.IMAP4_SSL:
    """Connect + login. Tries basic auth, then XOAUTH2."""
    host = cfg("SCHOOL_IMAP_HOST", DEFAULT_HOST)
    user = cfg("SCHOOL_EMAIL")
    pw = cfg("SCHOOL_MAIL_PASSWORD")
    token = cfg("SCHOOL_OAUTH_TOKEN")
    if not user:
        raise SystemExit("SCHOOL_EMAIL not set in ~/.config/auto-freecf/.env")

    ctx = ssl.create_default_context()
    m = imaplib.IMAP4_SSL(host, 993, ssl_context=ctx)

    if pw:
        try:
            m.login(user, pw)
            print(f"      [school] IMAP basic auth OK ({user} @ {host})", file=sys.stderr)
            return m
        except imaplib.IMAP4.error as e:
            print(f"      [school] basic auth failed: {e}", file=sys.stderr)
    if token:
        auth = f"user={user}\x01auth=Bearer {token}\x01\x01"
        try:
            m.authenticate("XOAUTH2", lambda _: auth.encode())
            print(f"      [school] IMAP XOAUTH2 OK ({user})", file=sys.stderr)
            return m
        except imaplib.IMAP4.error as e:
            print(f"      [school] XOAUTH2 failed: {e}", file=sys.stderr)

    raise SystemExit(
        "Could not authenticate to the school mailbox.\n"
        "  - Ensure SCHOOL_MAIL_PASSWORD (basic auth) or SCHOOL_OAUTH_TOKEN (XOAUTH2) is set.\n"
        "  - M365 tenants often disable IMAP basic auth; an OAuth2 app token may be required."
    )


def _bodies(msg) -> str:
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                try:
                    parts.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace"))
                except Exception:
                    pass
    else:
        try:
            parts.append(msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", "replace"))
        except Exception:
            parts.append(str(msg.get_payload()))
    return "\n".join(parts)


def fetch_recent(limit: int = 10, mailbox: str = "INBOX") -> list[dict]:
    m = connect()
    try:
        m.select(mailbox)
        typ, data = m.search(None, "ALL")
        ids = data[0].split()[-limit:]
        out = []
        for i in reversed(ids):
            typ, d = m.fetch(i, "(RFC822)")
            if not d or not d[0]:
                continue
            msg = email.message_from_bytes(d[0][1])
            out.append({
                "uid": i.decode(),
                "from": _decode(msg.get("From")),
                "subject": _decode(msg.get("Subject")),
                "date": msg.get("Date", ""),
                "body": _bodies(msg),
            })
        return out
    finally:
        try:
            m.logout()
        except Exception:
            pass


def wait_for_otp(timeout: int = 180, poll: int = 8) -> str | None:
    """Poll the mailbox for a recent OpenAI/ChatGPT 6-digit code."""
    start = time.time()
    seen = set()
    while time.time() - start < timeout:
        try:
            for m in fetch_recent(limit=8):
                key = m["uid"]
                if key in seen:
                    continue
                blob = f"{m['subject']} {m['body']}"
                low = blob.lower()
                if any(k in low for k in ("openai", "chatgpt", "verification code", "verify", "code")):
                    for pat in (r'code to continue:?\s*(\d{6})',
                                r'verification code:?\s*(\d{6})',
                                r'\b(\d{6})\b'):
                        mm = re.search(pat, blob, re.I)
                        if mm:
                            print(f"      [school] OTP found: {mm.group(1)}", file=sys.stderr)
                            return mm.group(1)
                seen.add(key)
        except Exception as e:  # noqa: BLE001
            print(f"      [school] {e}", file=sys.stderr)
        time.sleep(poll)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Read OTP from a school mailbox (binus.ac.id etc.)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("test", help="connect + list recent mail")
    otp = sub.add_parser("otp", help="wait for an OpenAI OTP")
    otp.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args()

    if a.cmd == "test":
        try:
            for m in fetch_recent(limit=5):
                print(f"  [{m['uid']}] {m['from'][:40]:40s} | {m['subject'][:50]}")
            return 0
        except SystemExit as e:
            print(e); return 1
    if a.cmd == "otp":
        code = wait_for_otp(timeout=a.timeout)
        print(code or "(no OTP found)")
        return 0 if code else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
