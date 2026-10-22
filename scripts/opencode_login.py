#!/usr/bin/env python3
"""Automate the OpenCode device-flow login and store the resulting account.

WHAT THIS DOES
--------------
Drives the same OAuth 2.0 device flow the CLI uses, end to end:

  1. POST /console/auth/device/code   -> device_code + user_code
  2. print the verification URL and poll /console/auth/device/token
  3. on success, store access/refresh token + expiry where OpenCode reads them

The ONLY human step is opening the URL and approving the code -- that is the
authorization the whole flow exists to obtain. Nothing here bypasses the gate:
it is the gate's own front door, automated so a login does not need the TUI.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not create accounts, does not solve captchas, and does not loop over
emails. One run == one login the operator approves by hand. Automating *that*
would be farming, which is a different thing and not what this is for.

PROTOCOL FACTS (reverse-engineered from the 2.0.32 binary, verified live)
-------------------------------------------------------------------------
  client_id       "opencode-cli"
  device code     POST https://opencode.ai/console/auth/device/code
                  {"client_id":"opencode-cli","supports_org_scope":true}
                  -> {device_code,user_code,verification_uri_complete,expires_in,interval}
  token poll      POST https://opencode.ai/console/auth/device/token
                  {"grant_type":"urn:ietf:params:oauth:grant-type:device_code",
                   "device_code":..., "client_id":"opencode-cli"}
                  -> 400 {"_tag":"DeviceTokenError","error":"authorization_pending"}
                  -> 400 {"error":"slow_down"}   (interval +5s)
                  -> 200 {access_token,refresh_token,expires_in,org_id}
  refresh         same URL, {"grant_type":"refresh_token","refresh_token":...}
  User-Agent      opencode/<channel>/<version>/<name> -- WITHOUT it the device
                  endpoint answers 403, measured 2026-10-22. The UA is part of
                  the gate, not decoration.

WHERE THE TOKEN GOES
--------------------
~/.local/share/opencode/opencode.db, tables `account` (access_token,
refresh_token, token_expiry, selected_org_id) and `account_state`
(active_account_id, active_org_id). `auth.json` only holds `type: api` keys,
which is why an API key never satisfies the free tier.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://opencode.ai/console"
CLIENT_ID = "opencode-cli"
UA = "opencode/stable/2.0.32/opencode"
DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def post(path: str, body: dict, timeout: int = 30) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE + path, json.dumps(body).encode(),
        {"Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:200]}


def request_code() -> dict:
    code, d = post("/auth/device/code",
                   {"client_id": CLIENT_ID, "supports_org_scope": True})
    if code != 200:
        raise SystemExit("device/code failed: HTTP %s %s" % (code, d))
    return d


def poll(device_code: str, interval: int, expires_in: int) -> dict:
    """Poll until the operator approves, the code expires, or the server errors."""
    deadline = time.time() + expires_in
    wait = max(1, interval)
    while time.time() < deadline:
        code, d = post("/auth/device/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": CLIENT_ID,
        })
        if code == 200 and "access_token" in d:
            return d
        err = d.get("error") or d.get("_tag") or ""
        if err == "authorization_pending":
            time.sleep(wait)
            continue
        if err == "slow_down":
            wait += 5
            time.sleep(wait)
            continue
        raise SystemExit("device/token failed: HTTP %s %s" % (code, d))
    raise SystemExit("device code expired before approval")


def store(token: dict, email: str = "", url: str = BASE) -> str:
    """Write the account row the CLI reads. Returns a short status line."""
    if not DB.is_file():
        return "db not found at %s (token not stored)" % DB
    now = int(time.time())
    expires = now + int(token.get("expires_in") or 3600)
    org = token.get("org_id")
    con = sqlite3.connect(str(DB))
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(account)")]
        if not cols:
            return "no `account` table (token not stored)"
        row_id = "acc_" + str(int(time.time() * 1000))
        values = {
            "id": row_id,
            "email": email or None,
            "url": url,
            "access_token": token["access_token"],
            "refresh_token": token.get("refresh_token"),
            "token_expiry": expires,
            "selected_org_id": org,
            "time_created": now,
            "time_updated": now,
        }
        values = {k: v for k, v in values.items() if k in cols}
        con.execute(
            "INSERT INTO account (%s) VALUES (%s)" % (
                ",".join(values), ",".join("?" * len(values))),
            list(values.values()))
        # account_state points at the active account/org
        scols = [r[1] for r in con.execute("PRAGMA table_info(account_state)")]
        if scols:
            st = {"active_account_id": row_id, "active_org_id": org}
            st = {k: v for k, v in st.items() if k in scols}
            if st:
                existing = con.execute("SELECT COUNT(*) FROM account_state").fetchone()[0]
                if existing:
                    con.execute("UPDATE account_state SET %s" %
                                ",".join("%s=?" % k for k in st), list(st.values()))
                else:
                    con.execute("INSERT INTO account_state (%s) VALUES (%s)" % (
                        ",".join(st), ",".join("?" * len(st))), list(st.values()))
        con.commit()
        return "stored account %s (org=%s, expires in %ss)" % (
            row_id, org, token.get("expires_in"))
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenCode device-flow login")
    ap.add_argument("--email", default="", help="label to store with the account")
    ap.add_argument("--no-store", action="store_true", help="print the token shape, write nothing")
    ap.add_argument("--timeout", type=int, default=0, help="override the code's expires_in")
    a = ap.parse_args()

    d = request_code()
    verify = "https://opencode.ai" + d["verification_uri_complete"]
    print("1. Open this URL and approve the code:")
    print("   %s" % verify)
    print("   code: %s" % d["user_code"])
    print("2. Waiting up to %ds (polling every %ds)..."
          % (d.get("expires_in", 600), d.get("interval", 5)), flush=True)

    token = poll(d["device_code"], int(d.get("interval") or 5),
                 a.timeout or int(d.get("expires_in") or 600))
    print("3. Approved. access_token=%s… refresh=%s org=%s"
          % (token["access_token"][:10],
             "yes" if token.get("refresh_token") else "no",
             token.get("org_id")))
    if a.no_store:
        print("   (--no-store: nothing written)")
        return 0
    print("4.", store(token, email=a.email))
    return 0


if __name__ == "__main__":
    sys.exit(main())
