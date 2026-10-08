#!/usr/bin/env python3
"""
scripts/github_to_anything.py — Generic 'sign into ANY site with GitHub, else temp-mail signup' adapter.

Walks authentication on arbitrary target sites:
  1. SOCIAL-FIRST: If 'Continue with GitHub' exists, clicks it and completes GitHub OAuth authorization,
     capturing final session cookies / localStorage / token into results/<host>_session.json.
  2. Else if 'Continue with Google' exists, reports honestly (requires manual Google profile).
  3. Else EMAIL/TEMP-MAIL FALLBACK: Detects signup form, generates usr_<rand>@kancalabs.biz.id (or myid),
     submits, polls KancaHub mail relay for verification link or OTP, confirms, and captures session.

RATE-LIMIT & SAFETY:
  - Concurrency 1 (strictly sequential).
  - 20-45s random delay between accounts, 1.5-4s between page actions.
  - Exponential backoff on 429/403 (30s start, double, cap 10m; stop after 3 consecutive).
  - Stop immediately and report if Cloudflare/Turnstile/Arkose/DataDome challenge or 2FA appears.
  - Default max 3 accounts/run. NEVER fake success.

CLI:
  python3 scripts/github_to_anything.py <target_url> [--account EMAIL] [--domain {bizid,myid}]
                                       [--proxy auto] [--headless] [--inject-9router BASEURL]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import string
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import urllib.parse

# Conditional Camoufox & Playwright import so pure helpers/tests run outside camoufox-venv
try:
    from camoufox.async_api import AsyncCamoufox  # type: ignore
except Exception as _camoufox_err:  # pragma: no cover
    AsyncCamoufox = None
    _CAMOUFOX_IMPORT_ERROR = _camoufox_err
else:
    _CAMOUFOX_IMPORT_ERROR = None

try:
    import camoufox_helpers
except ImportError:
    from scripts import camoufox_helpers

# Paths & Defaults
HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
DEFAULT_ACCOUNTS_FILE = AUTO_FREECF / "github_accounts.json"
RESULTS_DIR = AUTO_FREECF / "results"

DEFAULT_MAX_ACCOUNTS = 3
DEFAULT_DELAY_MIN = 20.0
DEFAULT_DELAY_MAX = 45.0
DEFAULT_ACTION_DELAY_MIN = 1.5
DEFAULT_ACTION_DELAY_MAX = 4.0

RATE_LIMIT_BASE_BACKOFF = 30.0
RATE_LIMIT_MAX_BACKOFF = 600.0
RATE_LIMIT_MAX_CONSECUTIVE = 3

# Relay mail defaults
DEFAULT_DOMAINS = {
    "bizid": "kancalabs.biz.id",
    "myid": "kancalabs.my.id",
}


# ─────────────────────────────────────────────────────────── Account Loader
def load_github_accounts(path: Path | str) -> list[dict[str, Any]]:
    """Load valid GitHub accounts from a JSON file.

    Supports both wrapped format `{"accounts": [...]}` and raw list `[...]`.
    Filters for records containing a password and at least one login identifier.
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []

    if isinstance(data, dict):
        accounts = data.get("accounts", [])
    elif isinstance(data, list):
        accounts = data
    else:
        accounts = []

    valid: list[dict[str, Any]] = []
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        login = (
            acc.get("username")
            or acc.get("login")
            or acc.get("email")
            or ""
        )
        password = acc.get("password") or ""
        if login.strip() and password.strip():
            valid.append(acc)
    return valid


# ─────────────────────────────────────────────────────────── Pure Helpers
def detect_social_target(labels: list[str]) -> str:
    """Detect auth method from a list of button/link/control labels.

    Returns:
        'github' | 'google' | 'email'
    """
    # 1. Social-first: GitHub
    for raw in labels:
        text = str(raw or "").lower()
        if "github" in text:
            return "github"

    # 2. Google
    for raw in labels:
        text = str(raw or "").lower()
        if "google" in text:
            return "google"

    # 3. Email / standard signup
    for raw in labels:
        text = str(raw or "").lower()
        if any(k in text for k in ("email", "sign up", "signup", "register", "create account", "get started")):
            return "email"

    return "email"


def resolve_domain(domain_choice: str = "bizid") -> str:
    """Normalize domain choices: bizid -> kancalabs.biz.id, myid -> kancalabs.my.id."""
    dc = str(domain_choice or "bizid").strip().lower()
    if dc in ("bizid", "biz.id", "kancalabs.biz.id"):
        return "kancalabs.biz.id"
    if dc in ("myid", "my.id", "kancalabs.my.id"):
        return "kancalabs.my.id"
    return dc


def build_temp_email(domain_choice: str = "bizid", rand_suffix: str | None = None) -> str:
    """Generate a disposable signup email on our relay domain: usr_<rand>@kancalabs.<domain>."""
    dom = resolve_domain(domain_choice)
    suffix = rand_suffix if rand_suffix is not None else "".join(
        random.choices(string.ascii_lowercase + string.digits, k=7)
    )
    return f"usr_{suffix}@{dom}"


def gen_password() -> str:
    """Generate a compliant strong password."""
    tail = "".join(random.choices(string.ascii_letters + string.digits, k=12))
    special = random.choice("!@#$%^&*-_")
    return f"Kanca_{tail}{special}9"


def extract_verification_link(text: str, host: str | None = None) -> str | None:
    """Extract verification link from email body text or HTML."""
    if not text:
        return None
    clean = text.replace("\\/", "/")
    urls = re.findall(r'https?://[^\s"\'<>)]+', clean)
    candidates: list[str] = []
    for u in urls:
        trimmed = u.rstrip(".,;)\"'")
        low = trimmed.lower()
        if any(k in low for k in ("verify", "confirm", "activate", "token", "signup", "auth")):
            candidates.append(trimmed)

    if host:
        # Prioritize matching target host
        for c in candidates:
            if host in c:
                return c

    return candidates[0] if candidates else (urls[0].rstrip(".,;)\"'") if urls else None)


def extract_otp_code(text: str) -> str | None:
    """Extract 6 or 8 digit verification code from email text."""
    if not text:
        return None
    # Check common OTP patterns
    for pat in (
        r'code to continue:?\s*(\d{6,8})',
        r'verification code:?\s*(\d{6,8})',
        r'launch code:?\s*(\d{6,8})',
        r'security code:?\s*(\d{6,8})',
        r'\b(\d{6,8})\b',
    ):
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1)
    return None


# ─────────────────────────────────────────────────────────── Rate Limit & Backoff
class BackoffManager:
    """Exponential backoff manager for 429/403 HTTP codes."""

    def __init__(
        self,
        initial: float = RATE_LIMIT_BASE_BACKOFF,
        factor: float = 2.0,
        cap: float = RATE_LIMIT_MAX_BACKOFF,
        max_consecutive: int = RATE_LIMIT_MAX_CONSECUTIVE,
    ):
        self.initial = initial
        self.factor = factor
        self.cap = cap
        self.max_consecutive = max_consecutive
        self.current_delay = initial
        self.consecutive_count = 0

    def record_error(self) -> float:
        self.consecutive_count += 1
        delay = self.current_delay
        self.current_delay = min(self.current_delay * self.factor, self.cap)
        return delay

    def record_success(self) -> None:
        self.current_delay = self.initial
        self.consecutive_count = 0

    def should_stop(self) -> bool:
        return self.consecutive_count >= self.max_consecutive


def get_action_delay(
    min_sec: float = DEFAULT_ACTION_DELAY_MIN,
    max_sec: float = DEFAULT_ACTION_DELAY_MAX,
) -> float:
    return random.uniform(min_sec, max_sec)


def get_account_delay(
    min_sec: float = DEFAULT_DELAY_MIN,
    max_sec: float = DEFAULT_DELAY_MAX,
) -> float:
    return random.uniform(min_sec, max_sec)


def _proxy_dict(proxy: str | None) -> dict | None:
    """Build a Camoufox/Playwright-style proxy dict from URL string."""
    return camoufox_helpers.to_camoufox_proxy(proxy)


# ─────────────────────────────────────────────────────────── Challenge Detector
async def detect_challenge(page: Any) -> str | None:
    """Check page state for Cloudflare, Arkose, DataDome, or 2FA challenges."""
    try:
        url = page.url.lower()
        title = (await page.title()).lower()

        # 1. Cloudflare / DataDome challenges
        if "cf-challenge" in url or "turnstile" in url:
            return "Cloudflare Turnstile / Challenge detected in URL"
        if "datadome" in url:
            return "DataDome challenge detected in URL"
        if "just a moment..." in title or "attention required" in title:
            return "Cloudflare challenge detected in page title"

        turnstile = await page.locator("iframe[src*='challenges.cloudflare.com']").count()
        if turnstile > 0:
            return "Cloudflare Turnstile iframe present"

        # 2. Arkose Labs / Octocaptcha
        arkose = await page.locator("iframe[src*='arkose'], #octocaptcha, iframe[src*='funcaptcha']").count()
        if arkose > 0:
            return "Arkose Labs / Octocaptcha challenge present"

        # 3. GitHub 2FA / Device Verification
        if "/sessions/two-factor" in url:
            return "GitHub Two-Factor Authentication (2FA) prompt required"
        if "/sessions/verified-device" in url:
            return "GitHub Device Verification email prompt required"

        otp_input = await page.locator("input[name='otp'], #app_totp, #sms_totp").count()
        if otp_input > 0:
            return "GitHub OTP/2FA input field detected"

    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────── Relay Mail Client
def get_relay_client() -> tuple[Any, Any, Any]:
    """Import relay mail functions from github_farm or fallback."""
    sys_scripts = Path(__file__).resolve().parent
    if str(sys_scripts) not in sys.path:
        sys.path.insert(0, str(sys_scripts))

    try:
        from github_farm import create_relay_mailbox, get_relay_config, poll_relay_inbox
        return get_relay_config, create_relay_mailbox, poll_relay_inbox
    except ImportError:
        try:
            from scripts.github_farm import create_relay_mailbox, get_relay_config, poll_relay_inbox
            return get_relay_config, create_relay_mailbox, poll_relay_inbox
        except ImportError:
            pass

    # Fallback minimal relay client
    def _fallback_config() -> dict:
        import os
        return {
            "mail_base": os.environ.get("K12_MAIL_API", ""),
            "mail_key": os.environ.get("K12_MAIL_KEY", ""),
            "domains": ["kancalabs.biz.id", "kancalabs.my.id"],
        }

    def _fallback_create(domain: str = "kancalabs.biz.id", local_part: str | None = None) -> dict:
        import requests
        cfg = _fallback_config()
        if not cfg["mail_base"] or not cfg["mail_key"]:
            raise RuntimeError("Relay configuration missing (K12_MAIL_API/K12_MAIL_KEY unset)")
        payload: dict = {"domain": domain}
        if local_part:
            payload["name"] = local_part
        r = requests.post(f"{cfg['mail_base']}/new_address", json=payload, headers={"x-api-key": cfg["mail_key"]}, timeout=60)
        r.raise_for_status()
        d = r.json()
        addr = d.get("address") or d.get("email") or ""
        return {"email": addr, "address": addr, "jwt": d.get("jwt", ""), "domain": domain}

    def _fallback_poll(jwt: str) -> list[dict]:
        import requests
        cfg = _fallback_config()
        if not cfg["mail_base"] or not cfg["mail_key"]:
            return []
        r = requests.get(f"{cfg['mail_base']}/parsed_mails", headers={"Authorization": f"Bearer {jwt}", "x-api-key": cfg["mail_key"]}, timeout=30)
        r.raise_for_status()
        d = r.json()
        return d if isinstance(d, list) else d.get("results", [])

    return _fallback_config, _fallback_create, _fallback_poll


# ─────────────────────────────────────────────────────────── Session Capture
async def capture_page_session(page: Any, host: str) -> dict[str, Any]:
    """Capture cookies and localStorage from current page state."""
    cookies = []
    try:
        cookies = await page.context.cookies()
    except Exception:
        pass

    storage: dict[str, str] = {}
    try:
        storage = await page.evaluate("""() => {
            const out = {};
            try {
                for (let i = 0; i < localStorage.length; i++) {
                    const k = localStorage.key(i);
                    out[k] = localStorage.getItem(k);
                }
            } catch (e) {}
            return out;
        }""")
    except Exception:
        pass

    return {
        "host": host,
        "url": page.url,
        "cookies": cookies,
        "storage": storage,
    }


THIRD_PARTY_AUTH_DOMAINS = {
    "github.com",
    "google.com",
    "cloudflare.com",
    "live.com",
    "microsoft.com",
    "apple.com",
    "hcaptcha.com",
    "recaptcha.net",
}


def is_target_domain(domain: str, target_host: str) -> bool:
    """Check if domain or cookie domain matches the target host, excluding 3rd party auth domains."""
    dom = domain.lstrip(".").lower()
    thost = target_host.lstrip(".").lower()
    if not dom or not thost:
        return False

    # Cookies/domains from github.com / google.com (or any third party) must NEVER count
    for tpd in THIRD_PARTY_AUTH_DOMAINS:
        if dom == tpd or dom.endswith("." + tpd):
            return False

    # Exact host match
    if dom == thost:
        return True

    # dom is an apex/parent domain of target_host, e.g. dom='tiarina.cloud', target_host='console.tiarina.cloud'
    # or cookie domain ends with target host, e.g. dom='console.tiarina.cloud', target_host='tiarina.cloud'
    if thost.endswith("." + dom) or dom.endswith("." + thost):
        return True

    return False


def is_login_path(path: str) -> bool:
    """Check if a URL path indicates a login/signin/signup/auth page."""
    p = path.lower().rstrip("/")
    if not p:
        return False

    exact_matches = {
        "/login",
        "/signin",
        "/sign-in",
        "/sign_in",
        "/signup",
        "/sign-up",
        "/sign_up",
        "/register",
        "/registration",
        "/auth",
        "/auth/login",
        "/auth/signin",
        "/auth/signup",
        "/account/login",
        "/accounts/login",
        "/session/new",
        "/sessions/new",
        "/oauth/authorize",
        "/oauth/login",
    }
    if p in exact_matches:
        return True

    segments = [s for s in p.split("/") if s]
    for seg in segments:
        seg_base = seg.split(".")[0]
        if seg_base in (
            "login",
            "signin",
            "sign-in",
            "sign_in",
            "signup",
            "sign-up",
            "sign_up",
            "register",
            "registration",
        ):
            return True
        if seg in ("oauth", "auth") and seg == segments[-1]:
            return True

    return False


def is_post_login_signal(
    page_url: str,
    session: dict[str, Any],
    initial_url: str,
    host: str | None = None,
) -> bool:
    """Determine if session demonstrates an honest post-login state strictly on the target host."""
    parsed_init = urllib.parse.urlparse(initial_url)
    target_host = host or parsed_init.netloc.split(":")[0] or session.get("host") or ""
    target_host = target_host.lower()
    if not target_host:
        return False

    parsed_page = urllib.parse.urlparse(page_url)
    page_host = parsed_page.netloc.split(":")[0].lower()

    # Must be on the target host (never stuck on github.com or third-party auth provider)
    if not is_target_domain(page_host, target_host):
        return False

    # Negative guard: If still on a login/signin/auth URL of the target host, it must return False
    if is_login_path(parsed_page.path):
        return False

    # (a) URL is on target host AND clearly NOT a login/signin/signup/auth page
    #     AND has changed from the initial URL path (e.g. /login -> /dashboard, /console, /ai, /overview, or root)
    init_path = parsed_init.path.rstrip("/")
    cur_path = parsed_page.path.rstrip("/")
    if cur_path != init_path and not is_login_path(cur_path):
        return True

    # (b) At least ONE cookie whose domain ENDS WITH the target host (e.g. tiarina.cloud)
    #     AND whose name/use indicates a session/token (excluding csrf/xsrf and logged-out flags)
    cookies = session.get("cookies", [])
    for c in cookies:
        cdom = c.get("domain", "")
        if not is_target_domain(cdom, target_host):
            continue
        cname = c.get("name", "").lower()
        if "csrf" in cname or "xsrf" in cname:
            continue
        val = str(c.get("value", "")).lower()
        if not val or val in ("no", "false", "0", "null", "undefined", "deleted"):
            continue
        if any(k in cname for k in ("token", "session", "auth", "jwt", "sid", "logged_in", "access_token", "refresh_token", "id_token")):
            return True

    # (c) LocalStorage contains an auth token clearly not from third-party domains
    storage = session.get("storage", {})
    if isinstance(storage, dict):
        for k, v in storage.items():
            k_low = k.lower()
            if "csrf" in k_low or "xsrf" in k_low:
                continue
            if not v or str(v).lower() in ("false", "null", "undefined", ""):
                continue
            if any(kw in k_low for kw in ("token", "auth", "jwt", "session")):
                return True

    return False


# ─────────────────────────────────────────────────────────── Browser Flow
async def run_single_site_flow(
    target_url: str,
    account: dict[str, Any],
    *,
    domain_choice: str = "bizid",
    proxy: str | None = None,
    headless: bool = False,
    inject_9router: str | None = None,
    dry_run: bool = False,
) -> tuple[int, dict[str, Any]]:
    """Execute the full social-first login or temp-mail fallback flow against target_url."""
    if AsyncCamoufox is None:
        print(f"✗ Camoufox is not installed or import failed: {_CAMOUFOX_IMPORT_ERROR}", file=sys.stderr)
        return 1, {"error": "camoufox_unavailable"}

    parsed_target = urllib.parse.urlparse(target_url)
    host = parsed_target.netloc.split(":")[0] or "target"
    login_id = account.get("username") or account.get("login") or account.get("email") or ""
    password = account.get("password") or ""

    camoufox_kwargs: dict[str, Any] = {
        "headless": headless,
        "geoip": True,
        "humanize": True,
        "os": "windows",
    }
    pd = _proxy_dict(proxy)
    if pd:
        camoufox_kwargs["proxy"] = pd

    print(f"\n[Flow] Target URL : {target_url} ({host})")
    print(f"[Flow] GitHub user: {login_id}")
    print(f"[Flow] Proxy      : {proxy or '(direct)'}")
    print(f"[Flow] Headless   : {headless}")
    if dry_run:
        print("[Flow] Dry-run    : ENABLED (no final submit)")

    async with AsyncCamoufox(**camoufox_kwargs) as browser:
        page = await browser.new_page()

        # 1. Open Target Page
        print(f"  [1/5] Navigating to target: {target_url}…", flush=True)
        try:
            resp = await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            if resp and getattr(resp, "status", None) in (403, 429):
                print(f"  ✗ Target returned HTTP {resp.status} (network/egress blocked).", file=sys.stderr)
                return 1, {"stage": f"http_{resp.status}", "error": "egress_blocked"}
        except Exception as e:
            print(f"  ✗ Navigation error: {e}", file=sys.stderr)
            return 1, {"stage": "page_load_error", "error": str(e)}

        await asyncio.sleep(get_action_delay())

        challenge = await detect_challenge(page)
        if challenge:
            print(f"  ✗ Challenge detected: {challenge}. Halting per safety policy.", file=sys.stderr)
            return 1, {"stage": "challenge", "challenge": challenge}

        # 2. Inspect Controls & Detect Auth Method
        print("  [2/5] Inspecting login & signup controls…", flush=True)
        labels: list[str] = []
        try:
            elements = await page.locator("button, a, input[type='submit'], [role='button']").all()
            for el in elements[:40]:
                try:
                    txt = (await el.inner_text()).strip()
                    aria = (await el.get_attribute("aria-label") or "").strip()
                    title = (await el.get_attribute("title") or "").strip()
                    combined = f"{txt} {aria} {title}".strip()
                    if combined:
                        labels.append(combined)
                except Exception:
                    pass
        except Exception:
            pass

        auth_method = detect_social_target(labels)
        print(f"  [+] Detected primary auth method: {auth_method.upper()}")

        # ── 3. SOCIAL-FIRST: GitHub ──
        if auth_method == "github":
            print("  [3/5] SOCIAL-FIRST: Initiating 'Continue with GitHub' flow…", flush=True)
            gh_btn = page.locator(
                "button:has-text('github'), a:has-text('github'), "
                "[aria-label*='github' i], button:has-text('GitHub'), a:has-text('GitHub')"
            ).first

            if dry_run:
                print("  [dry-run] Would click GitHub social button. Stopping here.", flush=True)
                return 0, {"stage": "dry_run_github_detected", "auth_method": "github"}

            try:
                await gh_btn.click()
            except Exception as e:
                print(f"  ✗ Failed to click GitHub button: {e}", file=sys.stderr)
                return 1, {"stage": "github_click_failed", "error": str(e)}

            await asyncio.sleep(get_action_delay())

            # Walk GitHub login if redirected to github.com
            cur_url = page.url
            if "github.com/login" in cur_url:
                print(f"      Entering GitHub credentials for {login_id}…", flush=True)
                login_input = page.locator("#login_field, input[name='login']").first
                if await login_input.count() > 0:
                    await login_input.fill(login_id)
                    await asyncio.sleep(get_action_delay(1.0, 2.0))

                pass_input = page.locator("#password, input[name='password']").first
                if await pass_input.count() > 0:
                    await pass_input.fill(password)
                    await asyncio.sleep(get_action_delay(1.0, 2.0))

                commit_btn = page.locator("input[name='commit'], button[type='submit']").first
                if await commit_btn.count() > 0:
                    await commit_btn.click()
                    await asyncio.sleep(get_action_delay())

                ch = await detect_challenge(page)
                if ch:
                    print(f"  ✗ GitHub challenge detected: {ch}. Halting.", file=sys.stderr)
                    return 1, {"stage": "github_login_challenge", "challenge": ch}

            # Walk GitHub OAuth consent screen
            await asyncio.sleep(get_action_delay())
            auth_btn = page.locator(
                "#js-oauth-authorize-btn, button[name='authorize'], button:has-text('Authorize')"
            ).first
            if await auth_btn.count() > 0:
                print("      Clicking GitHub 'Authorize' consent button…", flush=True)
                await auth_btn.click()
                await asyncio.sleep(get_action_delay())

            # Await redirect back to target site
            print("  [4/5] Awaiting target callback and session capture…", flush=True)
            for _ in range(20):
                if host in page.url and "github.com" not in page.url:
                    break
                await asyncio.sleep(1.0)

            session = await capture_page_session(page, host)
            if is_post_login_signal(page.url, session, target_url, host=host):
                print(f"  ✓ Honest success: Post-login verified at {page.url}!")
                RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                out_file = RESULTS_DIR / f"{host}_session.json"
                record = {
                    "target": target_url,
                    "host": host,
                    "auth_method": "github",
                    "account": login_id,
                    "final_url": page.url,
                    "session": session,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "success": True,
                }
                out_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
                print(f"  [+] Saved session -> {out_file}")

                if inject_9router:
                    print(f"  [+] 9Router injection target: {inject_9router}")
                return 0, record
            else:
                fail_msg = f"login did not complete — still on {page.url}; no target session cookie"
                print(f"  ✗ {fail_msg}", file=sys.stderr, flush=True)
                RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                failed_file = RESULTS_DIR / f"{host}_failed.json"
                record = {
                    "target": target_url,
                    "host": host,
                    "auth_method": "github",
                    "account": login_id,
                    "final_url": page.url,
                    "session": session,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "success": False,
                    "error": "login_incomplete",
                    "message": fail_msg,
                }
                failed_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
                print(f"  [!] Saved failure record -> {failed_file}", flush=True)

                # Ensure no stale false-success session file remains on disk
                success_file = RESULTS_DIR / f"{host}_session.json"
                if success_file.exists():
                    try:
                        existing = json.loads(success_file.read_text(encoding="utf-8"))
                        if not is_post_login_signal(
                            existing.get("final_url", ""),
                            existing.get("session", {}),
                            target_url,
                            host=host,
                        ):
                            success_file.unlink(missing_ok=True)
                    except Exception:
                        pass
                return 1, record

        # ── 4. Google Social Notice ──
        if auth_method == "google":
            print("  [!] Google social login detected ('Continue with Google').", flush=True)
            print("      Automated Google credentials not configured. Falling back to email signup…", flush=True)

        # ── 5. EMAIL/TEMP-MAIL FALLBACK ──
        print("  [3/5] EMAIL FALLBACK: Preparing disposable address on our relay…", flush=True)
        temp_email = build_temp_email(domain_choice=domain_choice)
        temp_password = gen_password()
        resolved_dom = resolve_domain(domain_choice)
        local_part = temp_email.split("@")[0]

        _cfg_fn, create_mb_fn, poll_inbox_fn = get_relay_client()
        relay_jwt = None

        if dry_run:
            print(f"  [dry-run] Generated email: {temp_email} ({resolved_dom})", flush=True)
            return 0, {"stage": "dry_run_email_fallback", "email": temp_email}

        try:
            mb = create_mb_fn(domain=resolved_dom, local_part=local_part)
            relay_jwt = mb.get("jwt")
            if mb.get("email"):
                temp_email = mb["email"]
            print(f"      [mail] Relay mailbox ready: {temp_email}", flush=True)
        except Exception as e:
            print(f"  ✗ Failed to create relay mailbox: {e}", file=sys.stderr)
            return 1, {"stage": "relay_creation_failed", "error": str(e)}

        # If on a login page, search for a link to switch to signup
        cur_url = page.url.lower()
        if any(k in cur_url for k in ("/login", "/signin", "login")):
            signup_link = page.locator(
                "a:has-text('Sign up'), a:has-text('Register'), a:has-text('Create account'), "
                "button:has-text('Sign up'), button:has-text('Register')"
            ).first
            if await signup_link.count() > 0:
                print("      Switching to signup page…", flush=True)
                await signup_link.click()
                await asyncio.sleep(get_action_delay())

        # Find signup fields
        email_inp = page.locator("input[type='email'], input[name*='email' i], input[id*='email' i]").first
        pass_inp = page.locator("input[type='password'], input[name*='password' i]").first
        name_inp = page.locator("input[name*='name' i], input[autocomplete='name']").first

        if await email_inp.count() == 0 or await pass_inp.count() == 0:
            print("  ✗ Could not find email/password inputs on signup form. Reporting honest stop.", file=sys.stderr)
            return 1, {"stage": "signup_inputs_not_found", "url": page.url}

        print(f"      Filling signup form with {temp_email}…", flush=True)
        if await name_inp.count() > 0:
            await name_inp.fill("Kanca User")
            await asyncio.sleep(get_action_delay(1.0, 2.0))

        await email_inp.fill(temp_email)
        await asyncio.sleep(get_action_delay(1.0, 2.0))

        await pass_inp.fill(temp_password)
        await asyncio.sleep(get_action_delay(1.0, 2.0))

        # Check for confirm password field
        all_pass_inputs = await page.locator("input[type='password']").all()
        if len(all_pass_inputs) > 1:
            await all_pass_inputs[1].fill(temp_password)
            await asyncio.sleep(get_action_delay(1.0, 2.0))

        # Submit form
        submit_btn = page.locator(
            "button[type='submit'], input[type='submit'], button:has-text('Sign up'), button:has-text('Register')"
        ).first
        if await submit_btn.count() > 0:
            await submit_btn.click()
            await asyncio.sleep(get_action_delay())

        ch_sub = await detect_challenge(page)
        if ch_sub:
            print(f"  ✗ Challenge encountered on form submit: {ch_sub}. Halting.", file=sys.stderr)
            return 1, {"stage": "submit_challenge", "challenge": ch_sub}

        # Poll relay for verification email
        print("  [4/5] Polling KancaHub relay for verification email/link…", flush=True)
        verified = False
        start_poll = time.time()
        while time.time() - start_poll < 150:
            try:
                mails = poll_inbox_fn(relay_jwt)
                for m in mails:
                    blob = " ".join(str(m.get(k, "")) for k in ("subject", "text", "body", "html", "snippet"))
                    vlink = extract_verification_link(blob, host=host)
                    if vlink:
                        print(f"\n      [+] Verification link found: {vlink}", flush=True)
                        await page.goto(vlink, wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(get_action_delay())
                        verified = True
                        break

                    otp = extract_otp_code(blob)
                    if otp:
                        print(f"\n      [+] Verification OTP found: {otp}", flush=True)
                        otp_field = page.locator("input[name*='code' i], input[name*='otp' i], input[type='tel']").first
                        if await otp_field.count() > 0:
                            await otp_field.fill(otp)
                            otp_submit = page.locator("button[type='submit'], button:has-text('Verify')").first
                            if await otp_submit.count() > 0:
                                await otp_submit.click()
                                await asyncio.sleep(get_action_delay())
                        verified = True
                        break
                if verified:
                    break
            except Exception as e:
                print(f"      [mail] Polling error: {e}", flush=True)

            print(".", end="", flush=True)
            await asyncio.sleep(5)
        print()

        session = await capture_page_session(page, host)
        if is_post_login_signal(page.url, session, target_url, host=host):
            print(f"  ✓ Honest success: Email signup verified at {page.url}!")
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            out_file = RESULTS_DIR / f"{host}_session.json"
            record = {
                "target": target_url,
                "host": host,
                "auth_method": "email",
                "email": temp_email,
                "final_url": page.url,
                "session": session,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "success": True,
            }
            out_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
            print(f"  [+] Saved session -> {out_file}")
            return 0, record
        else:
            fail_msg = f"login did not complete — still on {page.url}; no target session cookie"
            print(f"  ✗ {fail_msg}", file=sys.stderr, flush=True)
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            failed_file = RESULTS_DIR / f"{host}_failed.json"
            record = {
                "target": target_url,
                "host": host,
                "auth_method": "email",
                "email": temp_email,
                "final_url": page.url,
                "session": session,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "success": False,
                "error": "login_incomplete",
                "message": fail_msg,
            }
            failed_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
            print(f"  [!] Saved failure record -> {failed_file}", flush=True)

            success_file = RESULTS_DIR / f"{host}_session.json"
            if success_file.exists():
                try:
                    existing = json.loads(success_file.read_text(encoding="utf-8"))
                    if not is_post_login_signal(
                        existing.get("final_url", ""),
                        existing.get("session", {}),
                        target_url,
                        host=host,
                    ):
                        success_file.unlink(missing_ok=True)
                except Exception:
                    pass
            return 1, record


# ─────────────────────────────────────────────────────────── CLI & Main Entry
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Sign into ANY site with GitHub OAuth, else temp-mail signup fallback"
    )
    ap.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Target site login or signup URL (positional)",
    )
    ap.add_argument(
        "--target",
        dest="target_opt",
        default=None,
        help="Target site login or signup URL (flag alias)",
    )
    ap.add_argument(
        "--account",
        default=None,
        help="Specific GitHub email/username from github_accounts.json to use",
    )
    ap.add_argument(
        "--domain",
        choices=["bizid", "myid"],
        default="bizid",
        help="Temp-mail domain for fallback signup (default: bizid -> kancalabs.biz.id)",
    )
    ap.add_argument(
        "--proxy",
        default="auto",
        help="Proxy mode: 'auto' (smart egress auto-wire), 'none' (direct), or explicit URL (default: auto)",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        help="Run Camoufox browser in headless mode",
    )
    ap.add_argument(
        "--inject-9router",
        dest="inject_9router",
        metavar="BASEURL",
        default=None,
        help="Register/inject resulting session token into 9Router (OpenAI-compatible base URL)",
    )
    ap.add_argument(
        "--max-accounts",
        type=int,
        default=DEFAULT_MAX_ACCOUNTS,
        help=f"Max accounts to process per run (default: {DEFAULT_MAX_ACCOUNTS})",
    )
    ap.add_argument(
        "--delay-min",
        type=float,
        default=DEFAULT_DELAY_MIN,
        help=f"Min delay between accounts in seconds (default: {DEFAULT_DELAY_MIN})",
    )
    ap.add_argument(
        "--delay-max",
        type=float,
        default=DEFAULT_DELAY_MAX,
        help=f"Max delay between accounts in seconds (default: {DEFAULT_DELAY_MAX})",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Navigate and detect auth methods without submitting final credentials",
    )
    ap.add_argument(
        "--accounts-file",
        default=str(DEFAULT_ACCOUNTS_FILE),
        help="Path to github_accounts.json",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    target_url = args.target_opt or args.target
    if not target_url:
        print("✗ Error: Target URL is required (e.g. scripts/github_to_anything.py https://console.tiarina.cloud/login)", file=sys.stderr)
        parser.print_help(sys.stderr)
        return 1

    # 1. Load GitHub Account(s)
    accounts = load_github_accounts(args.accounts_file)
    if args.account:
        accounts = [
            a for a in accounts
            if args.account.lower() in (
                str(a.get("email", "")).lower(),
                str(a.get("username", "")).lower(),
                str(a.get("login", "")).lower(),
            )
        ]

    if not accounts:
        print("no GitHub account yet — run kancahub github farm first", file=sys.stderr)
        return 2

    # Limit accounts per rate limit policy
    accounts = accounts[:args.max_accounts]
    print(f"Loaded {len(accounts)} GitHub account(s) for run against {target_url}")

    # 2. Proxy Egress Resolution
    proxy_url = None
    gw_proc = None
    if args.proxy.lower() == "none" or args.proxy.lower() == "direct":
        proxy_url = None
    elif "://" in args.proxy:
        proxy_url = args.proxy
    else:
        # Use egress auto-wire against target_url
        try:
            from egress import auto_egress
            proxy_url, gw_proc, _ = auto_egress(target_url=target_url, mode="auto", verbose=True)
        except Exception:
            try:
                from scripts.egress import auto_egress
                proxy_url, gw_proc, _ = auto_egress(target_url=target_url, mode="auto", verbose=True)
            except Exception as e:
                print(f"[!] Warning: egress auto_egress unavailable ({e}); proceeding with direct egress", file=sys.stderr)
                proxy_url = None

    backoff = BackoffManager()
    exit_code = 0

    try:
        for idx, acc in enumerate(accounts):
            if idx > 0:
                delay = get_account_delay(args.delay_min, args.delay_max)
                print(f"\n[Pacing] Waiting {delay:.1f}s between accounts…")
                time.sleep(delay)

            rc, _ = asyncio.run(run_single_site_flow(
                target_url=target_url,
                account=acc,
                domain_choice=args.domain,
                proxy=proxy_url,
                headless=args.headless,
                inject_9router=args.inject_9router,
                dry_run=args.dry_run,
            ))

            if rc != 0:
                exit_code = rc
                wait_sec = backoff.record_error()
                print(f"[Backoff] Rate/error recorded. Backing off {wait_sec:.1f}s…")
                if backoff.should_stop():
                    print("✗ Stopped after consecutive failures / challenges per safety policy.", file=sys.stderr)
                    break
            else:
                backoff.record_success()

    finally:
        if gw_proc is not None:
            try:
                from proxy_lib import stop_gateway
                stop_gateway(gw_proc)
            except Exception:
                try:
                    from scripts.proxy_lib import stop_gateway
                    stop_gateway(gw_proc)
                except Exception:
                    pass

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
