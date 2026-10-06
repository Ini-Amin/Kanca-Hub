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
import subprocess
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

# Reuse the proven, strictly-gated helpers from github_to_anything (pure
# functions + loader). Import is defensive so autofarm still runs if that
# module is unavailable — the fallbacks below keep the honesty contract.
try:
    from github_to_anything import (  # type: ignore
        capture_page_session as _gta_capture_session,
        is_post_login_signal as _gta_post_login_signal,
        load_github_accounts as _gta_load_github_accounts,
    )
except Exception:  # pragma: no cover - exercised only on a broken install
    _gta_capture_session = None
    _gta_post_login_signal = None
    _gta_load_github_accounts = None

DEFAULT_GITHUB_ACCOUNTS_FILE = ROOT / "github_accounts.json"

try:
    from proxy_sync import sync_now, prioritize_fresh
except ImportError:
    def sync_now(*a, **kw): return 0
    def prioritize_fresh(l, **kw): return l

TEMPIK_BASE = "https://tempik.kancalabs.workers.dev"

# ── auth inspection (pure, unit-testable) ───────────────────────────────
GITHUB_TEXT_HINTS = (
    "continue with github", "sign in with github", "sign up with github",
    "login with github", "log in with github", "github",
)
GOOGLE_TEXT_HINTS = (
    "continue with google", "sign in with google", "sign up with google",
    "login with google", "log in with google", "with google",
)
GITHUB_HREF_HINTS = ("github.com/login/oauth", "github.com/login", "github.com/signup")
GOOGLE_HREF_HINTS = ("accounts.google.com/o/oauth2", "accounts.google.com")

def classify_auth(
    labels: list[str],
    hrefs: list[str],
    has_email: bool,
    has_pass: bool,
    has_next: bool = False,
) -> dict:
    """Classify available auth methods from DOM evidence. Pure — no I/O.

    Args:
        labels: visible text of buttons/links/controls on the page.
        hrefs: href/action URLs of links and oauth anchors.
        has_email: an email input exists.
        has_pass: a password input exists.
        has_next: a 'Next' or 'Continue' button exists (multi-step email start).

    Returns:
        {"methods": [...], "has_email_form": bool, "preferred": str|None, "step": str|None}

    `methods` is ordered by routing preference: **email first**, then github,
    then google. Rationale (user directive): if the site offers an email+password
    signup, use it first (it's self-service); fall back to GitHub/Google social
    only when no email form exists. has_email_form is True if (email AND password)
    OR (email AND has_next).
    """
    text = " ".join(str(x or "").lower() for x in labels)
    links = " ".join(str(x or "").lower() for x in hrefs)

    has_github = any(h in text for h in GITHUB_TEXT_HINTS) or \
        any(h in links for h in GITHUB_HREF_HINTS)
    has_google = any(h in text for h in GOOGLE_TEXT_HINTS) or \
        any(h in links for h in GOOGLE_HREF_HINTS)

    effective_has_next = bool(has_next)
    if not effective_has_next:
        next_hints = ("next", "continue", "lanjut", "selanjutnya", "berikutnya", "berikut", "proceed")
        for lbl in labels:
            l = str(lbl or "").strip().lower()
            if any(h == l or l.startswith(h + " ") or l.endswith(" " + h) or f" {h} " in l for h in next_hints):
                effective_has_next = True
                break

    has_email_form = bool((has_email and has_pass) or (has_email and effective_has_next))

    step = None
    if has_email_form:
        if has_email and not has_pass:
            step = "email_only"
        else:
            step = "email_password"

    methods: list[str] = []
    if has_email_form:          # email/password FIRST (self-service signup)
        methods.append("email")
    if has_github:              # GitHub as fallback
        methods.append("github")
    if has_google:              # Google last
        methods.append("google")

    return {
        "methods": methods,
        "has_email_form": has_email_form,
        "preferred": methods[0] if methods else None,
        "step": step,
    }


def next_step_action(visible_fields: set[str], buttons: set[str]) -> str:
    """Decide the next action in a multi-step signup flow. Pure — no I/O.

    Args:
        visible_fields: set of input field types/names visible on current step.
        buttons: set of button texts visible on current step.

    Returns:
        One of:
          - 'stop_captcha': captcha challenge present
          - 'stop_phone_otp': phone / OTP verification required
          - 'fill_password': password field needs to be filled
          - 'fill_name': name fields need to be filled
          - 'fill_dob': date of birth fields need to be filled
          - 'fill_email': email field needs to be filled
          - 'click_next': no unfilled fields, next/continue button present
          - 'stop_unknown': no recognized fields or buttons
    """
    fields_norm = {str(f).strip().lower() for f in (visible_fields or set()) if f}
    buttons_norm = {str(b).strip().lower() for b in (buttons or set()) if b}

    # 1. Captcha / Bot protection check
    captcha_hints = ("captcha", "arkose", "funcaptcha", "recaptcha", "hcaptcha", "turnstile", "puzzle", "robot", "human")
    if any(any(hint in f for hint in captcha_hints) for f in fields_norm) or \
       any(any(hint in b for hint in captcha_hints) for b in buttons_norm):
        return "stop_captcha"

    # 2. Phone / OTP / Verification code check
    phone_otp_hints = ("phone", "tel", "otp", "code", "sms", "verification", "verify")
    if any(any(hint in f for hint in phone_otp_hints) for f in fields_norm):
        return "stop_phone_otp"

    # 3. Fillable fields: password
    pass_hints = ("password", "pass", "passwd", "confirm_password", "confirm_pass")
    if any(any(hint in f for hint in pass_hints) for f in fields_norm):
        return "fill_password"

    # 4. Fillable fields: name
    name_hints = ("name", "first_name", "last_name", "firstname", "lastname")
    if any(any(hint in f for hint in name_hints) for f in fields_norm):
        return "fill_name"

    # 5. Fillable fields: DOB
    dob_hints = ("dob", "birthdate", "birth", "birth_year", "birth_month", "birth_day", "month", "day", "year")
    if any(any(hint in f for hint in dob_hints) for f in fields_norm):
        return "fill_dob"

    # 6. Fillable fields: email
    if any("email" in f for f in fields_norm):
        return "fill_email"

    # 7. Next / Continue / Submit button
    next_btn_hints = (
        "next", "continue", "create account", "create_account", "sign up", "sign_up",
        "submit", "lanjut", "selanjutnya", "berikutnya", "berikut"
    )
    if any(any(hint in b for hint in next_btn_hints) for b in buttons_norm):
        return "click_next"

    # 8. Unrecognized / broken step
    return "stop_unknown"

def load_github_accounts(path: Path | str | None = None) -> list[dict]:
    """Load usable GitHub accounts (github_accounts.json). Empty list if none."""
    target = Path(path) if path else DEFAULT_GITHUB_ACCOUNTS_FILE
    if _gta_load_github_accounts is not None:
        try:
            return _gta_load_github_accounts(target)
        except Exception:
            return []
    # Fallback loader (kept minimal; mirrors github_to_anything's contract).
    if not target.exists():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return []
    accounts = data.get("accounts", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    out = []
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        login = acc.get("username") or acc.get("login") or acc.get("email") or ""
        if login.strip() and str(acc.get("password") or "").strip():
            out.append(acc)
    return out


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


def get_fresh_proxy(explicit: str | None = None, mode: str | None = None) -> str | None:
    """Resolve the proxy.

    - explicit URL            -> use it.
    - mode 'none'/'direct'    -> return None (truly direct; do NOT grab the pool).
    - otherwise               -> first live-ish line of the signup pool (legacy default).
    """
    if explicit and explicit.lower() not in ("none", "direct", "off", "no"):
        return explicit
    if (mode or "").lower() in ("none", "direct", "off", "no") or (explicit or "").lower() in ("none", "direct"):
        return None
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
    inspect_only: bool = False,
) -> dict:
    domain = normalize_domain(mail_domain)
    # 1. Resolve egress (only harvest the pool when a pool proxy is actually wanted)
    chosen_proxy = get_fresh_proxy(proxy)
    if chosen_proxy is None and (proxy or "").lower() not in ("none", "direct", "off", "no"):
        sync_now(quiet=True)
        chosen_proxy = get_fresh_proxy(proxy)

    print(f"\n  ▶ Target   : {url}")
    print(f"  • Domain   : {domain}")
    print(f"  • Proxy    : {chosen_proxy or 'Direct (no proxy)'}")
    if inspect_only:
        print("  • Mode     : INSPECT ONLY (no filling, no submit, nothing written)")

    # 2. Prepare identity (only needed for the email path, but harmless to print)
    username = f"usr_{random_string(8)}"
    email = f"{username}@{domain}"
    password = generate_password()

    print(f"  • Email    : {email}")
    print(f"  • Password : {password}")

    # 3. Launch Camoufox (delegated to _run_browser_flow)
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.split(":")[0] or "target"

    result = {
        "url": url,
        "email": email,
        "password": password,
        "username": username,
        "proxy": chosen_proxy,
        "success": False,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "auth_methods": [],
        "auth_preferred": None,
        "stopped_at": None,
        "final_url": None,
    }

    from camoufox_helpers import to_camoufox_proxy
    proxy_cfg = to_camoufox_proxy(chosen_proxy) if chosen_proxy else None

    result = await _run_browser_flow(
        url, headless, proxy_cfg, email, password, username, host, result,
        inspect_only=inspect_only,
    )

    # 4. Inspect-only: print the finding and write NOTHING.
    if inspect_only:
        print("\n  ── Inspect result (no account was created) ──")
        print(json.dumps({
            "url": url,
            "methods": result.get("auth_methods", []),
            "preferred": result.get("auth_preferred"),
            "has_email_form": result.get("has_email_form", False),
            "step": result.get("step"),
            "stopped_at": result.get("stopped_at"),
        }, indent=2))
        return result

    # 5. Save results to JSON — ALWAYS persisted (success true or false), so the
    #    operator sees exactly what happened. Scaffolding is gated below.
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
    if result.get("success"):
        print(f"  ✅ Saved VERIFIED account to {out_file}")
    else:
        print(f"  ⚠ Saved FAILED record to {out_file} (success=false — nothing was created)")

    # 6. Inject into 9Router only on a real success
    if inject_9r and result.get("success"):
        inject_to_9router(result)

    # 7. Scaffold a reusable pipeline ONLY after a real success
    if result.get("success"):
        _scaffold_pipeline(url, email, password, result, domain)
    else:
        print("  • No scaffold written (nothing was created).")

    return result


async def _drive_page(
    page,
    url: str,
    email: str,
    password: str,
    username: str,
    result: dict,
    host: str,
    *,
    inspect_only: bool = False,
) -> dict:
    print(f"  • Navigating to {url}…")
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if resp is not None and getattr(resp, "status", None) in (403, 429):
            result["stopped_at"] = f"http_{resp.status}"
            result["error"] = f"target returned HTTP {resp.status} (egress blocked)"
            print(f"  ✗ Target returned HTTP {resp.status}; stopping (no fill attempted).")
            return result
    except Exception as exc:
        result["stopped_at"] = "page_load_error"
        result["error"] = str(exc)
        print(f"  ✗ Navigation failed: {exc}")
        return result

    await page.wait_for_timeout(3000)
    result["final_url"] = page.url

    # ── PHASE 1: INSPECT (always, before any filling) ──────────────────
    print("  [inspect] Detecting available auth methods…")
    classification = await inspect_auth_methods(page, url)
    methods = classification.get("methods", [])
    result["auth_methods"] = methods
    result["auth_preferred"] = classification.get("preferred")
    result["has_email_form"] = classification.get("has_email_form", False)
    result["step"] = classification.get("step")
    step_str = f" step={result['step']}" if result.get("step") else ""
    print(f"  [inspect] methods={methods or ['none']} "
          f"has_email_form={result['has_email_form']} "
          f"preferred={result['auth_preferred']}{step_str}")

    if not methods:
        result["stopped_at"] = "no_supported_auth_method"
        result["error"] = "no supported auth method found"
        print("  ✗ No supported auth method found — refusing to fill or write a success file.")
        return result

    if inspect_only:
        result["stopped_at"] = "inspect_only"
        print("  ✓ Inspect-only: stopping before any fill/submit.")
        return result

    # ── PHASE 2: ROUTE (email FIRST per user directive, then github, then google)
    if result.get("has_email_form") or "email" in methods:
        print("  [route] Email/password form selected (preferred).")
        return await _route_email(page, url, email, password, username, host, result)

    if "github" in methods:
        accounts = load_github_accounts()
        if accounts:
            print(f"  [route] GitHub OAuth selected ({len(accounts)} account(s) available).")
            return await _route_github(page, url, host, accounts[0], result)
        print("  [route] GitHub method present but no GitHub account available "
              "(github_accounts.json empty) — not attempting GitHub.")

    if "google" in methods:
        result["stopped_at"] = "google_requires_account"
        result["error"] = "Google login available but no Google account configured"
        print("  [route] Google login detected, but no Google credentials are configured — "
              "honest failure (no fake success).")
        return result

    # Methods existed but none is routable (e.g. github present with no account
    # and no email form) — honest stop.
    result["stopped_at"] = result.get("stopped_at") or "no_routable_method"
    result.setdefault("error", "no routable auth method (no GitHub account, no email form)")
    print(f"  ✗ Nothing routable — stopping. ({result.get('error')})")
    return result

async def _run_browser_flow(
    url: str,
    headless: bool,
    proxy_cfg: dict | None,
    email: str,
    password: str,
    username: str,
    host: str,
    result: dict,
    *,
    inspect_only: bool = False,
) -> dict:
    """Launch the browser and drive one target page (Camoufox, else Playwright)."""
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        AsyncCamoufox = None

    if AsyncCamoufox is not None:
        async with AsyncCamoufox(headless=headless, proxy=proxy_cfg, geoip=True, humanize=True) as browser:
            page = await browser.new_page()
            return await _drive_page(
                page, url, email, password, username, result, host,
                inspect_only=inspect_only,
            )

    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=headless, proxy=proxy_cfg)
        try:
            page = await browser.new_page()
            return await _drive_page(
                page, url, email, password, username, result, host,
                inspect_only=inspect_only,
            )
        finally:
            await browser.close()

async def _find_clickable_next_button(page):
    selectors = [
        "button:has-text('Next')",
        "button:has-text('Continue')",
        "button:has-text('Create account')",
        "button:has-text('Create Account')",
        "button:has-text('Sign up')",
        "button:has-text('Sign Up')",
        "button:has-text('Submit')",
        "button:has-text('Register')",
        "button:has-text('Daftar')",
        "button:has-text('Lanjut')",
        "button:has-text('Selanjutnya')",
        "button:has-text('Berikutnya')",
        "button:has-text('Berikut')",
        "input[type='submit']",
        "button[type='submit']",
        "input[type='button'][value*='Next' i]",
        "input[type='button'][value*='Continue' i]",
        "input[type='button'][value*='Berikutnya' i]",
        "[role='button']:has-text('Next')",
        "[role='button']:has-text('Continue')",
        "[role='button']:has-text('Berikutnya')",
    ]
    for sel in selectors:
        try:
            elements = await page.query_selector_all(sel)
            for el in elements:
                if await el.is_visible() and await el.is_enabled():
                    return el
        except Exception:
            continue
    return None


async def _is_captcha_present(page) -> bool:
    captcha_selectors = [
        "iframe[src*='arkoselabs']",
        "iframe[src*='funcaptcha']",
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "#enforcementFrame",
        "[data-e2e='enforcement-frame']",
        ".cf-turnstile",
        "iframe[src*='challenges.cloudflare.com']",
    ]
    for sel in captcha_selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return True
        except Exception:
            continue
    try:
        body_text = await page.evaluate("() => document.body ? document.body.innerText.toLowerCase() : ''")
        captcha_phrases = (
            "solve the puzzle",
            "solve a puzzle",
            "are you a human",
            "verify you are human",
            "verify that you are human",
            "human verification",
            "security challenge",
            "not a robot",
            "arkose",
        )
        if any(p in body_text for p in captcha_phrases):
            return True
    except Exception:
        pass
    return False


async def _is_phone_otp_present(page) -> bool:
    try:
        otp_selectors = [
            "input[name*='otp' i]",
            "input[id*='otp' i]",
            "input[name*='code' i]",
            "input[id*='code' i]",
            "input[placeholder*='code' i]",
            "input[type='tel']",
            "input[name*='phone' i]",
            "input[id*='phone' i]",
        ]
        for sel in otp_selectors:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return True
        body_text = await page.evaluate("() => document.body ? document.body.innerText.toLowerCase() : ''")
        otp_phrases = (
            "enter code",
            "verification code",
            "verify your email",
            "verify email",
            "we sent a code",
            "enter the code",
            "phone number",
            "enter your phone",
        )
        if any(p in body_text for p in otp_phrases):
            return True
    except Exception:
        pass
    return False


async def _fill_known_fields(page, email: str, password: str, username: str) -> bool:
    filled_any = False

    # Email
    email_sel = "input[type='email'], input[name*='email' i], input[id*='email' i]"
    try:
        el = await page.query_selector(email_sel)
        if el and await el.is_visible():
            val = (await el.input_value() or "").strip()
            if not val:
                print("  • Found email input, filling…")
                await el.fill(email)
                await page.wait_for_timeout(random.randint(400, 800))
                filled_any = True
    except Exception:
        pass

    # Password
    pass_sel = "input[type='password'], input[name*='pass' i], input[id*='pass' i]"
    try:
        el = await page.query_selector(pass_sel)
        if el and await el.is_visible():
            val = (await el.input_value() or "").strip()
            if not val:
                print("  • Found password input, filling…")
                await el.fill(password)
                await page.wait_for_timeout(random.randint(400, 800))
                filled_any = True

                confirm_sel = "input[name*='confirm' i], input[id*='confirm' i], input[name*='repeat' i], input[placeholder*='confirm' i]"
                c_el = await page.query_selector(confirm_sel)
                if c_el and await c_el.is_visible():
                    c_val = (await c_el.input_value() or "").strip()
                    if not c_val:
                        await c_el.fill(password)
                        await page.wait_for_timeout(400)
    except Exception:
        pass

    # First & Last Name
    first_sel = "input[name*='first' i], input[id*='first' i], input[placeholder*='first' i], input[name*='fname' i]"
    last_sel = "input[name*='last' i], input[id*='last' i], input[placeholder*='last' i], input[name*='lname' i]"
    try:
        fn_el = await page.query_selector(first_sel)
        ln_el = await page.query_selector(last_sel)
        if fn_el and await fn_el.is_visible():
            if not (await fn_el.input_value() or "").strip():
                print("  • Found first name input, filling…")
                await fn_el.fill("Kanca")
                filled_any = True
                await page.wait_for_timeout(400)
        if ln_el and await ln_el.is_visible():
            if not (await ln_el.input_value() or "").strip():
                print("  • Found last name input, filling…")
                await ln_el.fill("Labs")
                filled_any = True
                await page.wait_for_timeout(400)
    except Exception:
        pass

    # Single username / name (if not first/last and not email)
    name_sel = "input[name*='user' i], input[name*='name' i], input[id*='user' i]"
    try:
        n_el = await page.query_selector(name_sel)
        if n_el and await n_el.is_visible():
            t = (await n_el.get_attribute("type") or "").lower()
            if t not in ("email", "password", "hidden", "submit", "checkbox"):
                if not (await n_el.input_value() or "").strip():
                    print("  • Found username/name input, filling…")
                    await n_el.fill(username)
                    filled_any = True
                    await page.wait_for_timeout(400)
    except Exception:
        pass

    # DOB: Year
    year_sel = "select[name*='year' i], select[id*='year' i], input[name*='year' i], input[id*='year' i], input[placeholder*='year' i]"
    try:
        y_el = await page.query_selector(year_sel)
        if y_el and await y_el.is_visible():
            tag = await y_el.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                try:
                    await y_el.select_option(value="1995")
                except Exception:
                    await y_el.select_option(index=10)
            else:
                if not (await y_el.input_value() or "").strip():
                    await y_el.fill("1995")
            filled_any = True
            await page.wait_for_timeout(400)
    except Exception:
        pass

    # DOB: Month
    month_sel = "select[name*='month' i], select[id*='month' i], input[name*='month' i], input[id*='month' i]"
    try:
        m_el = await page.query_selector(month_sel)
        if m_el and await m_el.is_visible():
            tag = await m_el.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                await m_el.select_option(index=1)
            else:
                if not (await m_el.input_value() or "").strip():
                    await m_el.fill("01")
            filled_any = True
            await page.wait_for_timeout(400)
    except Exception:
        pass

    # DOB: Day
    day_sel = "select[name*='day' i], select[id*='day' i], input[name*='day' i], input[id*='day' i], input[placeholder*='day' i]"
    try:
        d_el = await page.query_selector(day_sel)
        if d_el and await d_el.is_visible():
            tag = await d_el.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                try:
                    await d_el.select_option(value="15")
                except Exception:
                    await d_el.select_option(index=15)
            else:
                if not (await d_el.input_value() or "").strip():
                    await d_el.fill("15")
            filled_any = True
            await page.wait_for_timeout(400)
    except Exception:
        pass

    # Checkboxes
    try:
        chks = await page.query_selector_all("input[type='checkbox']")
        for chk in chks:
            if await chk.is_visible() and not await chk.is_checked():
                await chk.check()
                filled_any = True
                await page.wait_for_timeout(400)
    except Exception:
        pass

    return filled_any


async def _extract_visible_text(page) -> str:
    try:
        return await page.evaluate("""() => {
            const body = document.body;
            return body ? body.innerText.slice(0, 300).replace(/\\s+/g, ' ') : '';
        }""")
    except Exception:
        return ""


async def _route_email(page, url, email, password, username, host, result, max_steps: int = 8) -> dict:
    """Fill email/password signup and drive multi-step flows up to max_steps."""
    print("  • Starting email signup flow (supporting multi-step)…")

    # Step 1: fill initial fields (email, and password if present)
    try:
        await _fill_known_fields(page, email, password, username)
        if await page.query_selector(".cf-turnstile, iframe[src*='challenges.cloudflare.com']"):
            print("  • Cloudflare Turnstile detected! Waiting for challenge…")
            await page.wait_for_timeout(5000)
    except Exception as exc:
        result["stopped_at"] = "fill_failed"
        result["error"] = f"form fill failed at step 1: {exc}"
        print(f"  ✗ Form fill failed at step 1: {exc}")
        return result

    submit_btn = await _find_clickable_next_button(page)
    if not submit_btn:
        result["stopped_at"] = "no_submit_button"
        result["error"] = "no submit or Next button found on step 1"
        print("  ⚠ No submit/Next button found on step 1 — not claiming success.")
        return result

    print("  • [step 1] Clicking Next/Submit button…")
    try:
        await submit_btn.click()
    except Exception as exc:
        result["stopped_at"] = "submit_failed"
        result["error"] = str(exc)
        print(f"  ✗ Step 1 click failed: {exc}")
        return result

    # Respect rate limits: 1-3s between actions
    delay = random.uniform(1.5, 3.0)
    await page.wait_for_timeout(int(delay * 1000))

    session = await _capture_session(page, host)
    result["final_url"] = page.url
    result["session"] = session
    if _post_login_signal(page.url, session, url, host):
        result["success"] = True
        result["stopped_at"] = "post_login_verified"
        print(f"  ✓ Verified post-login signal at {page.url}")
        return result

    # Multi-step loop: step 2 up to max_steps
    for step_idx in range(2, max_steps + 1):
        print(f"  [step {step_idx}/{max_steps}] Inspecting page at {page.url}…")

        if await _is_captcha_present(page):
            result["stopped_at"] = "stop_captcha"
            result["error"] = "captcha or human verification required"
            print("  ⚠ Captcha / verification challenge detected — stopping honestly.")
            return result

        if await _is_phone_otp_present(page):
            result["stopped_at"] = "stop_phone_otp"
            result["error"] = "phone or email OTP verification required"
            print("  ⚠ Phone or email OTP verification required — stopping honestly.")
            return result

        filled = await _fill_known_fields(page, email, password, username)

        next_btn = await _find_clickable_next_button(page)
        if not next_btn and not filled:
            vis_text = await _extract_visible_text(page)
            result["stopped_at"] = "stop_unknown"
            result["error"] = f"no known field or button at step {step_idx}: {vis_text[:120]}"
            print(f"  ✗ Step {step_idx} stopped at unknown view: {vis_text[:120]}")
            return result

        if not next_btn:
            result["stopped_at"] = "no_submit_button"
            result["error"] = f"filled fields at step {step_idx} but no Next button found"
            print(f"  ⚠ Step {step_idx} filled fields but no Next button found.")
            return result

        print(f"  • [step {step_idx}] Clicking Next/Submit button…")
        try:
            await next_btn.click()
        except Exception as exc:
            result["stopped_at"] = "submit_failed"
            result["error"] = str(exc)
            print(f"  ✗ Step {step_idx} click failed: {exc}")
            return result

        delay = random.uniform(1.5, 3.0)
        await page.wait_for_timeout(int(delay * 1000))

        session = await _capture_session(page, host)
        result["final_url"] = page.url
        result["session"] = session
        if _post_login_signal(page.url, session, url, host):
            result["success"] = True
            result["stopped_at"] = "post_login_verified"
            print(f"  ✓ Verified post-login signal at {page.url}")
            return result

    result["stopped_at"] = "max_steps_exceeded"
    result["error"] = f"exceeded maximum multi-step steps ({max_steps})"
    print(f"  ✗ Exceeded {max_steps} steps without post-login signal.")
    return result

async def _route_github(page, url, host, account: dict, result: dict) -> dict:
    """Delegate the GitHub OAuth flow to the proven github_to_anything driver."""
    tool = SCRIPTS / "github_to_anything.py"
    if not tool.exists():
        result["stopped_at"] = "github_driver_missing"
        result["error"] = f"github_to_anything.py not found at {tool}"
        print(f"  ✗ {result['error']}")
        return result

    login = account.get("username") or account.get("login") or account.get("email") or ""
    proxy_arg = result.get("proxy") or "none"
    cmd = [
        sys.executable, str(tool), url,
        "--account", str(login),
        "--proxy", str(proxy_arg),
    ]
    print(f"  [route] Delegating to github_to_anything.py (account={login}, proxy={proxy_arg})…")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception as exc:
        result["stopped_at"] = "github_delegate_failed"
        result["error"] = str(exc)
        print(f"  ✗ GitHub delegate failed to run: {exc}")
        return result

    tail = (proc.stdout or "").strip().splitlines()[-6:]
    for line in tail:
        print(f"      │ {line}")
    if proc.stderr.strip():
        for line in proc.stderr.strip().splitlines()[-3:]:
            print(f"      ⚠ {line}")

    result["stopped_at"] = "github_delegate_exit"
    result["github_exit_code"] = proc.returncode
    if proc.returncode == 0:
        result["success"] = True
        result["error"] = None
        print("  ✓ GitHub OAuth path reported success (verified by its own strict gate).")
    else:
        result["success"] = False
        result["error"] = f"github OAuth path failed (exit {proc.returncode})"
        print(f"  ✗ GitHub OAuth path did not complete (exit {proc.returncode}); success=false.")
    return result

async def _capture_session(page, host: str) -> dict:
    if _gta_capture_session is not None:
        try:
            return await _gta_capture_session(page, host)
        except Exception:
            pass
    cookies = []
    try:
        cookies = await page.context.cookies()
    except Exception:
        pass
    storage: dict = {}
    try:
        storage = await page.evaluate("""() => { const o = {};
            try { for (let i=0;i<localStorage.length;i++){const k=localStorage.key(i);o[k]=localStorage.getItem(k);} } catch(e){}
            return o; }""")
    except Exception:
        pass
    return {"host": host, "url": page.url, "cookies": cookies, "storage": storage}


async def inspect_auth_methods(page, target_url: str) -> dict:
    """Read the DOM and classify available auth methods (no filling, no clicks)."""
    labels: list[str] = []
    hrefs: list[str] = []

    try:
        elements = await page.locator(
            "button, a, input[type='submit'], input[type='button'], [role='button']"
        ).all()
        for el in elements[:60]:
            try:
                txt = (await el.inner_text()).strip()
                aria = (await el.get_attribute("aria-label") or "").strip()
                title = (await el.get_attribute("title") or "").strip()
                val = (await el.get_attribute("value") or "").strip()
                combined = " ".join(x for x in (txt, aria, title, val) if x)
                if combined:
                    labels.append(combined)
                href = (await el.get_attribute("href") or "").strip()
                if href:
                    hrefs.append(href)
            except Exception:
                continue
    except Exception:
        pass

    # OAuth anchors are often plain <a href="https://github.com/login/oauth/...">
    for sel in ("a[href*='github.com']", "a[href*='accounts.google.com']"):
        try:
            for el in (await page.locator(sel).all())[:10]:
                href = (await el.get_attribute("href") or "").strip()
                if href:
                    hrefs.append(href)
        except Exception:
            continue

    has_email = False
    has_pass = False
    try:
        has_email = await page.query_selector(
            "input[type='email'], input[name*='email' i], input[id*='email' i]"
        ) is not None
    except Exception:
        pass
    try:
        has_pass = await page.query_selector(
            "input[type='password'], input[name*='pass' i], input[id*='pass' i]"
        ) is not None
    except Exception:
        pass

    has_next = False
    next_hints = ("next", "continue", "lanjut", "selanjutnya", "berikutnya", "berikut", "proceed")
    for lbl in labels:
        l_lower = (lbl or "").strip().lower()
        if any(h == l_lower or l_lower.startswith(h + " ") or l_lower.endswith(" " + h) or f" {h} " in l_lower for h in next_hints):
            has_next = True
            break
    if not has_next:
        try:
            next_btn = await page.query_selector(
                "button:has-text('Next'), button:has-text('Continue'), "
                "button:has-text('Lanjut'), button:has-text('Selanjutnya'), "
                "button:has-text('Berikutnya'), button:has-text('Berikut'), "
                "input[type='submit'][value*='Next' i], input[type='button'][value*='Next' i], "
                "input[type='submit'][value*='Continue' i], input[type='button'][value*='Continue' i], "
                "input[type='submit'][value*='Berikutnya' i], input[type='button'][value*='Berikutnya' i], "
                "[role='button']:has-text('Next'), [role='button']:has-text('Continue'), "
                "[role='button']:has-text('Berikutnya')"
            )
            if next_btn:
                has_next = True
        except Exception:
            pass

    classification = classify_auth(labels, hrefs, has_email, has_pass, has_next=has_next)
    classification["target"] = target_url
    return classification

def _post_login_signal(page_url: str, session: dict, target_url: str, host: str) -> bool:
    """Strict post-login check — delegated to the proven github_to_anything gate."""
    if _gta_post_login_signal is not None:
        try:
            return bool(_gta_post_login_signal(page_url, session, target_url, host=host))
        except Exception:
            return False
    # Conservative fallback: target host, off the login path, not a provider.
    try:
        pu = urllib.parse.urlparse(page_url)
        tu = urllib.parse.urlparse(target_url)
        page_host = pu.netloc.split(":")[0].lower()
        target_host = (host or tu.netloc.split(":")[0]).lower()
        if not page_host or not target_host:
            return False
        if any(bad in page_host for bad in ("github.com", "google.com", "accounts.google")):
            return False
        if not (page_host == target_host or page_host.endswith("." + target_host)):
            return False
        login_words = ("login", "signin", "sign-in", "signup", "sign-up", "auth", "register")
        if any(w in pu.path.lower() for w in login_words):
            return False
        return pu.path.rstrip("/") != tu.path.rstrip("/")
    except Exception:
        return False

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


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="KancaHub AutoFarm — adapt website to existing pipelines")
    ap.add_argument("url", nargs="?", default=None, help="Target website signup/login URL")
    ap.add_argument("--domain", choices=["kancalabs.biz.id", "kancalabs.my.id", "biz.id", "my.id"], default="kancalabs.biz.id",
                    help="Disposable email domain (default: kancalabs.biz.id)")
    ap.add_argument("--inject-9router", action="store_true", help="Inject credentials into 9Router SQLite DB")
    ap.add_argument("--out", default=None, help="Output JSON path (default: results/autofarm_accounts.json)")
    ap.add_argument("--headless", action="store_true", help="run headless without browser UI")
    ap.add_argument("--proxy", default=None,
                    help="proxy URL, or 'none'/'auto' (default: auto from pool)")
    ap.add_argument("--no-proxy", action="store_true", help="force a direct connection (no proxy)")
    ap.add_argument("--inspect-only", action="store_true",
                    help="detect available auth methods (GitHub/Google/email) and exit — "
                         "no filling, no submit, nothing written")
    return ap

def main() -> int:
    ap = build_parser()
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

    result = asyncio.run(run_autofarm(
        url,
        headless=args.headless,
        proxy=("none" if getattr(args, "no_proxy", False) else args.proxy),
        mail_domain=domain,
        inject_9r=inject,
        out_json=args.out,
        inspect_only=args.inspect_only,
    ))
    if args.inspect_only:
        return 0
    return 0 if result.get("success") else 2


if __name__ == "__main__":
    sys.exit(main())
