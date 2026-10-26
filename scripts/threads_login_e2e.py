#!/usr/bin/env python3
"""threads_login_e2e.py — log an existing ledger Instagram account into Threads.com.

Threads profiles ride on Instagram accounts; the signup already exists
(scripts/instagram_signup_e2e.py), so this script only logs in:
  1. read creds from ~/.config/auto-freecf/instagram_accounts.json
  2. Camoufox via the same residential(proxy) egress the signup used
  3. threads.com → "Log in with Instagram" → username/password
  4. clear the Save-login prompt, prove the feed, capture cookies
  5. append a record to ~/.config/auto-freecf/threads_accounts.jsonl

Usage:  python3 scripts/threads_login_e2e.py [username] [--headed]
"""
from __future__ import annotations

import argparse, json, re, sys, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.mailboxes as _  # noqa: F401  (keeps repo module path warm)
import scripts.persona as persona
import scripts.urban_vpn as uv
from camoufox.sync_api import Camoufox

LEDGER = Path.home() / ".config/auto-freecf/instagram_accounts.json"
OUT = Path.home() / ".config/auto-freecf/threads_accounts.jsonl"
SCRATCH = Path.home() / ".config/auto-freecf/threads_scratch"


def pick_account(username: str = "") -> dict:
    accs = json.loads(
        [p for p in [LEDGER]][0].read_text()) if LEDGER.exists() else []
    if username:
        for a in accs:
            if a.get("username") == username:
                return a
    return accs[0]


def run_threads_login(username: str = "", headless: bool = True, country: str = "SG",
                      use_email: bool = False) -> None:
    acc = pick_account(username)
    print(f"[1/5] account : {acc['username']}")

    print(f"[2/5] residential proxy ({country})…")
    p = uv.get_proxy(country=country)
    proxy_cfg = {"server": f"http://{p['host']}:{p['port']}",
                 "username": p["username"], "password": p["password"]}
    print(f"      exit: {p['host']}:{p['port']}")

    SCRATCH.mkdir(parents=True, exist_ok=True)
    with Camoufox(headless=headless, humanize=True, proxy=proxy_cfg) as browser:
        page = browser.new_page()
        print("[3/5] threads.com…")
        page.goto("https://www.threads.com/", timeout=45000)
        time.sleep(5)

        btn = None
        for sel in ('div[role="button"]:has-text("Log in")',
                    'a:has-text("Log in")', 'button:has-text("Log in")'):
            el = page.locator(sel).first
            if el.count() > 0:
                btn = el
                break
        if btn:
            btn.click()
            time.sleep(4)

        # the login box is an embedded Instagram credential form
        for attempt in range(4):
            if page.locator('input[name="username"], input[type="text"]').count() and \
               page.locator('input[type="password"]').count():
                break
            log_in = page.locator('div[role="button"]:has-text("Log in with Instagram"), '
                                  'a:has-text("Log in with Instagram")').first
            if log_in.count() > 0:
                log_in.click()
                time.sleep(4)
            else:
                time.sleep(3)
        (SCRATCH / "fields_login.json").write_text(
            json.dumps([{"name": el.get_attribute("name"),
                         "type": el.get_attribute("type"),
                         "placeholder": el.get_attribute("placeholder")}
                        for el in page.query_selector_all("input")], indent=2))

        user_inp = (page.locator('input[name="username"], input[type="text"]').first)
        pass_inp = page.locator('input[type="password"]').first
        ident = acc.get("email", acc["username"]) if use_email else acc["username"]
        user_inp.fill(ident)
        time.sleep(0.3)
        pass_inp.fill(acc["password"])
        time.sleep(0.5)
        submit = page.locator('div[role="button"]:has-text("Log in"), '
                              'button:has-text("Log in"), '
                              'button[type="submit"]').last
        submit.click()
        time.sleep(7)

        for label in ("Save", "Not now", "Save login info"):
            b = page.locator(f'div[role="button"]:has-text("{label}"), '
                             f'button:has-text("{label}")').first
            if b.count() > 0:
                b.click()
                time.sleep(4)
                break

        page.screenshot(path=str(SCRATCH / "06_after_login.png"))
        body = page.inner_text("body")
        (SCRATCH / "dom_threads.txt").write_text(body[:8000])
        print(f"      url now: {page.url}")
        logged_in = any(k in body.lower() for k in ("following", "for you", "suggested"))
        cookies = {c["name"] for c in page.context.cookies()}
        have = [c for c in ("sessionid", "ds_user_id", "csrftoken") if c in cookies]
        print(f"[4/5] feed visible: {logged_in}   session cookies: {have}")

        print("[5/5] recording…")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT, "a") as f:
            f.write(json.dumps({"ts": int(time.time()),
                                "username": acc["username"],
                                "logged_in": logged_in, "cookies": have,
                                "threads_profile": acc["username"]}) + "\n")
        print("saved ->", OUT)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("username", nargs="?", default="")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--country", default="SG")
    ap.add_argument("--use-email", action="store_true",
                    help="log in with the ledger email instead of the username")
    a = ap.parse_args()
    run_threads_login(a.username, headless=not a.headed, country=a.country,
                      use_email=a.use_email)
