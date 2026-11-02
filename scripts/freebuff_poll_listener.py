#!/usr/bin/env python3
"""scripts/freebuff_poll_listener.py — Background listener waiting for user's Google/Email auth approval.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path.home() / ".config" / "auto-freecf" / "freebuff_slots"


def listen(slot_name: str, fingerprint_id: str, fingerprint_hash: str, expires_at: int, timeout_s: int = 1800) -> None:
    slot_dir = BASE_DIR / slot_name
    slot_dir.mkdir(parents=True, exist_ok=True)

    url = (f"https://freebuff.com/api/auth/cli/status?fingerprintId={fingerprint_id}"
           f"&fingerprintHash={fingerprint_hash}&expiresAt={expires_at}")

    print(f"[*] Freebuff Listener active for [{slot_name}]")
    print(f"    Waiting for approval at: https://freebuff.com/login?auth_code=...")
    print(f"    Listener will stay alive for {timeout_s // 60} minutes.")

    t0 = time.time()
    while time.time() - t0 < timeout_s:
        req = urllib.request.Request(url, headers={
            "User-Agent": "freebuff-cli/0.2.34",
            "Accept": "application/json"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                user = data.get("user")
                if user and isinstance(user, dict):
                    # Captured!
                    creds_file = slot_dir / "credentials.json"
                    creds_data = {"default": {**user, "name": user.get("name") or ""}}
                    creds_file.write_text(json.dumps(creds_data, indent=2), "utf-8")
                    os.chmod(creds_file, 0o600)

                    meta_file = slot_dir / "slot_meta.json"
                    meta = {}
                    if meta_file.exists():
                        try: meta = json.loads(meta_file.read_text("utf-8"))
                        except Exception: pass
                    meta["status"] = "active"
                    meta["user_email"] = user.get("email", "")
                    meta["user_id"] = user.get("id", "")
                    meta_file.write_text(json.dumps(meta, indent=2), "utf-8")

                    print(f"\n[✓✓✓] AUTH SUCCESS! Logged in as: {user.get('name') or user.get('email')}")
                    print(f"      Saved credentials to: {creds_file}")
                    return
        except urllib.error.HTTPError as e:
            if e.code != 401:
                print(f"[poll] HTTP {e.code}", flush=True)
        except Exception as e:
            pass

        time.sleep(3)

    print("\n[!] Listener timed out after 30 minutes.")


if __name__ == "__main__":
    if len(sys.argv) < 5:
        sys.exit("usage: freebuff_poll_listener.py <slot> <fingerprint_id> <fingerprint_hash> <expires_at>")
    listen(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]))
