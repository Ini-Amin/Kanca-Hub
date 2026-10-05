#!/usr/bin/env python3
"""
KancaHub Universal AutoFarm — adapt existing pipelines to farm any website.

Usage:
  kancahub autofarm <url> [--domain kancalabs.biz.id|kancalabs.my.id] [--inject-9router] [--headless]
  python3 scripts/autofarm.py https://example.com/signup --domain kancalabs.my.id --inject-9router
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sqlite3
import string
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
OUTPUT_DIR = ROOT / "results"
FARMS_DIR = SCRIPTS / "custom_farms"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

try:
    from proxy_sync import sync_now, prioritize_fresh
except ImportError:
    def sync_now(*a, **kw): return 0
    def prioritize_fresh(l, **kw): return l

TEMPIK_BASE = "https://tempik.kancalabs.workers.dev"


def random_string(n: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def generate_password() -> str:
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(random.choices(chars, k=14)) + "A1!"


def normalize_domain(d: str | None) -> str:
    if not d:
        return "kancalabs.biz.id"
    d = d.strip().lower()
    if d in ("my", "my.id", "kancalabs.my.id"):
        return "kancalabs.my.id"
    if d in ("biz", "biz.id", "kancalabs.biz.id"):
        return "kancalabs.biz.id"
    return d


def get_fresh_proxy(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    pool_file = ROOT / "signup_from_scratch" / "proxies.txt"
    if pool_file.exists():
        lines = [l.strip() for l in pool_file.read_text().splitlines() if l.strip() and not l.startswith("#")]
        if lines:
            return lines[0]
    return None


def inject_to_9router(account_info: dict, base_url: str | None = None) -> bool:
    """Inject credentials or generated API key into 9Router SQLite DB."""
    nine_router_db = Path.home() / ".9router" / "db" / "data.sqlite"
    if not nine_router_db.exists():
        print(f"  ⚠ 9Router database not found at {nine_router_db} (skipping injection)")
        return False

    domain = urllib.parse.urlparse(account_info["url"]).netloc
    conn_id = str(uuid.uuid4())
    node_id = f"autofarm-{domain.replace('.', '-')}"
    node_name = f"AutoFarm ({domain})"
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    data = {
        "defaultModel": "auto",
        "apiKey": account_info.get("api_key") or account_info.get("password"),
        "testStatus": "active",
        "providerSpecificData": {
            "prefix": "af",
            "apiType": "chat",
            "baseUrl": base_url or account_info["url"],
            "nodeName": node_name,
            "connectionProxyEnabled": False,
            "connectionProxyUrl": "",
            "connectionNoProxy": "",
        },
        "errorCode": None,
        "backoffLevel": 0,
    }

    try:
        con = sqlite3.connect(nine_router_db)
        cur = con.cursor()
        cur.execute(
            "INSERT INTO providerConnections "
            "(id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (conn_id, f"openai-compatible-chat-{node_id}", "apikey", node_name, account_info["email"], 1, 1, json.dumps(data), ts, ts),
        )
        con.commit()
        con.close()
        print(f"  ✅ Injected into 9Router: {node_name} -> {nine_router_db}")
        return True
    except Exception as e:
        print(f"  ⚠ Failed to inject into 9Router: {e}")
        return False


async def run_autofarm(
    url: str,
    headless: bool = False,
    proxy: str | None = None,
    mail_domain: str = "kancalabs.biz.id",
    inject_9r: bool = False,
    out_json: Path | str | None = None,
) -> dict:
    domain = normalize_domain(mail_domain)
    # 1. Sync fresh proxies
    sync_now(quiet=True)
    chosen_proxy = get_fresh_proxy(proxy)

    print(f"\n  ▶ Target   : {url}")
    print(f"  • Domain   : {domain}")
    print(f"  • Proxy    : {chosen_proxy or 'Direct (no proxy)'}")

    # 2. Prepare identity
    username = f"usr_{random_string(8)}"
    email = f"{username}@{domain}"
    password = generate_password()

    print(f"  • Email    : {email}")
    print(f"  • Password : {password}")

    # 3. Launch Camoufox
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        from playwright.async_api import async_playwright
        AsyncCamoufox = None

    result = {
        "url": url,
        "email": email,
        "password": password,
        "username": username,
        "proxy": chosen_proxy,
        "success": False,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    from camoufox_helpers import to_camoufox_proxy
    proxy_cfg = to_camoufox_proxy(chosen_proxy) if chosen_proxy else None

    browser_cm = AsyncCamoufox(headless=headless, proxy=proxy_cfg, geoip=True, humanize=True) if AsyncCamoufox else None

    if browser_cm:
        async with browser_cm as browser:
            page = await browser.new_page()
            result = await _drive_page(page, url, email, password, username, result)
    else:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=headless, proxy=proxy_cfg)
            page = await browser.new_page()
            result = await _drive_page(page, url, email, password, username, result)
            await browser.close()

    # 4. Save results to JSON
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = Path(out_json) if out_json else OUTPUT_DIR / "autofarm_accounts.json"
    existing = []
    if out_file.exists():
        try:
            existing = json.loads(out_file.read_text())
        except Exception:
            existing = []
    existing.append(result)
    out_file.write_text(json.dumps(existing, indent=2))
    print(f"  ✅ Saved account credentials to {out_file}")

    # 5. Inject into 9Router if requested
    if inject_9r:
        inject_to_9router(result)

    # 6. Scaffold reusable pipeline script
    _scaffold_pipeline(url, email, password, result, domain)

    return result


async def _drive_page(page, url: str, email: str, password: str, username: str, result: dict) -> dict:
    print(f"  • Navigating to {url}…")
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)

    # Detect form inputs
    email_sel = "input[type='email'], input[name*='email' i], input[id*='email' i]"
    pass_sel = "input[type='password'], input[name*='pass' i], input[id*='pass' i]"
    user_sel = "input[name*='user' i], input[name*='name' i], input[id*='user' i]"
    submit_sel = "button[type='submit'], input[type='submit'], button:has-text('Sign'), button:has-text('Register'), button:has-text('Daftar'), button:has-text('Submit')"

    has_email = await page.query_selector(email_sel)
    has_pass = await page.query_selector(pass_sel)

    if has_email:
        print("  • Found email input, filling…")
        await page.fill(email_sel, email)
        await page.wait_for_timeout(500)

    if await page.query_selector(user_sel) and not has_email:
        await page.fill(user_sel, username)
        await page.wait_for_timeout(500)

    if has_pass:
        print("  • Found password input, filling…")
        await page.fill(pass_sel, password)
        await page.wait_for_timeout(500)
        # Check confirm password
        confirm_sel = "input[name*='confirm' i], input[id*='confirm' i]"
        if await page.query_selector(confirm_sel):
            await page.fill(confirm_sel, password)

    # Check terms checkbox
    chk_sel = "input[type='checkbox']"
    chk = await page.query_selector(chk_sel)
    if chk and not await chk.is_checked():
        await chk.check()
        await page.wait_for_timeout(500)

    # Check for Turnstile
    cf_turnstile = await page.query_selector(".cf-turnstile, iframe[src*='challenges.cloudflare.com']")
    if cf_turnstile:
        print("  • Cloudflare Turnstile detected! Waiting for challenge…")
        await page.wait_for_timeout(5000)

    # Click submit
    submit_btn = await page.query_selector(submit_sel)
    if submit_btn:
        print("  • Clicking submit button…")
        await submit_btn.click()
        await page.wait_for_timeout(5000)
        result["success"] = True
        print("  ✓ Form submitted successfully.")
    else:
        print("  ⚠ Could not find automatic submit button.")

    return result


def _scaffold_pipeline(url: str, email: str, password: str, result: dict, domain: str) -> None:
    d_clean = urllib.parse.urlparse(url).netloc.replace(".", "_").replace(":", "_")
    FARMS_DIR.mkdir(parents=True, exist_ok=True)
    target_py = FARMS_DIR / f"farm_{d_clean}.py"
    content = f"""#!/usr/bin/env python3
\"\"\"Auto-generated pipeline farm for {url}\"\"\"

import asyncio
from autofarm import run_autofarm

async def main():
    res = await run_autofarm({repr(url)}, headless=True, mail_domain={repr(domain)})
    print("Farm completed:", res.get("email"))

if __name__ == "__main__":
    asyncio.run(main())
"""
    target_py.write_text(content)
    target_py.chmod(0o755)
    print(f"  • Created reusable pipeline script: {target_py}")


def main() -> int:
    ap = argparse.ArgumentParser(description="KancaHub AutoFarm — adapt website to existing pipelines")
    ap.add_argument("url", nargs="?", default=None, help="Target website signup/login URL")
    ap.add_argument("--domain", choices=["kancalabs.biz.id", "kancalabs.my.id", "biz.id", "my.id"], default="kancalabs.biz.id",
                    help="Disposable email domain (default: kancalabs.biz.id)")
    ap.add_argument("--inject-9router", action="store_true", help="Inject credentials into 9Router SQLite DB")
    ap.add_argument("--out", default=None, help="Output JSON path (default: results/autofarm_accounts.json)")
    ap.add_argument("--headless", action="store_true", help="run headless without browser UI")
    ap.add_argument("--proxy", default=None, help="proxy URL (default: auto from pool)")
    args = ap.parse_args()

    url = args.url
    domain = args.domain
    inject = args.inject_9router

    if not url:
        print("\n  KancaHub AutoFarm — Customize your site farm\n")
        url = input("  Website signup/login URL: ").strip()
        if not url:
            print("  ✗ No URL provided.")
            return 1
        print("\n  Choose disposable email domain:")
        print("    [1] kancalabs.biz.id (Cloudflare Email Routing)")
        print("    [2] kancalabs.my.id  (Tempik D1 Worker Catch-all)")
        c_dom = input("  Select [1/2] (default 1): ").strip()
        domain = "kancalabs.my.id" if c_dom == "2" else "kancalabs.biz.id"

        c_inj = input("  Inject credentials into 9Router? [y/N]: ").strip().lower()
        inject = c_inj in ("y", "yes", "true", "1")

    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    asyncio.run(run_autofarm(
        url,
        headless=args.headless,
        proxy=args.proxy,
        mail_domain=domain,
        inject_9r=inject,
        out_json=args.out,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
