#!/usr/bin/env python3
"""
scripts/github_to_kiro.py — Drive 9Router Kiro GitHub social OAuth from github_accounts.json.

This script performs the Kiro AI native GitHub social login flow using stored GitHub
credentials (from scripts/github_farm.py), captures the Kiro authorization code via
Camoufox browser automation, and registers the account as a Kiro connection in 9Router.

REAL ENDPOINTS & PARAMETERS:
1. 9Router Social Authorize (local daemon):
   GET http://127.0.0.1:20128/api/oauth/kiro/social-authorize?provider=github
   Returns: { "authUrl": ..., "state": ..., "codeVerifier": ..., "codeChallenge": ..., "provider": "github" }

2. Kiro Desktop Auth Entry (upstream):
   GET https://prod.us-east-1.auth.desktop.kiro.dev/login?idp=Github&redirect_uri=kiro%3A%2F%2Fkiro.kiroAgent%2Fauthenticate-success&code_challenge={challenge}&code_challenge_method=S256&state={state}&prompt=select_account
   Redirects (302) to AWS Cognito:
   https://kiro-prod-us-east-1.auth.us-east-1.amazoncognito.com/oauth2/authorize?...&identity_provider=Github
   Which redirects to Kiro's GitHub authorizer:
   https://prod.us-east-1.auth.desktop.kiro.dev/github/authorize?client_id=Ov23lilbEuhqkZak4Bfh&...
   Which redirects to GitHub OAuth authorize:
   https://github.com/login/oauth/authorize?client_id=Ov23lilbEuhqkZak4Bfh&redirect_uri=https%3A%2F%2Fkiro-prod-us-east-1.auth.us-east-1.amazoncognito.com%2Foauth2%2Fidpresponse&scope=read%3Auser+user%3Aemail+openid&response_type=code&state=...

3. GitHub OAuth Approval & Callback (upstream):
   User signs in on GitHub and approves "Authorize Kiro" (button[name="authorize"] / #js-oauth-authorize-btn).
   GitHub redirects to Cognito idpresponse, which redirects to the desktop custom scheme:
   kiro://kiro.kiroAgent/authenticate-success?code={AUTHORIZATION_CODE}&state={STATE}
   Captured by Camoufox route abort / request listening.

4. 9Router Social Exchange & Connection Creation (local daemon):
   POST http://127.0.0.1:20128/api/oauth/kiro/social-exchange
   Header: Content-Type: application/json, x-9r-cli-token: <16-char sha256(machineId + "9r-cli-auth" + cliSecret)[:16]>
   Body:   { "code": "{code}", "codeVerifier": "{codeVerifier}", "provider": "github" }
   9Router internally exchanges with:
     POST https://prod.us-east-1.auth.desktop.kiro.dev/oauth/token
     Body: { "code": "{code}", "code_verifier": "{verifier}", "redirect_uri": "kiro://kiro.kiroAgent/authenticate-success" }
   and inserts the new connection into SQLite (~/.9router/db/data.sqlite) as provider "kiro", authType "oauth".

RATE-LIMIT & SAFETY POLICY:
- Concurrency 1 (strictly sequential execution).
- Random delay 20-45s between accounts; 1.5-4s between browser actions.
- Exponential backoff on HTTP 429 / 403 (30s initial, doubling, capped at 10m / 600s).
- STOP immediately after 3 consecutive 429/403 errors.
- STOP and report immediately if any Cloudflare / Arkose / DataDome / 2FA challenge is encountered.
- Default max 3 accounts per run unless overridden by --max-accounts.

HONEST TODOS / UNCONFIRMED STEPS:
- TODO: GitHub 2FA / Device Verification. If GitHub prompts for two-factor authentication
  (SMS, app TOTP, or new-location email verification code), the flow cannot proceed autonomously.
  Per rate/safety policy, the script stops immediately and reports the challenge.
- TODO: Captcha / Arkose / Turnstile. If an interactive challenge appears, the script halts
  rather than attempting bypasses.
- TODO: Custom URI handler. The kiro:// scheme is intercepted directly within the browser
  network layer; no native desktop app installation or OS protocol registration is required.

Run:
  /home/amen/.local/share/auto-freecf/camoufox-venv/bin/python scripts/github_to_kiro.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import sqlite3
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid

# Conditional Camoufox import so unit tests / check can run outside camoufox-venv
try:
    from camoufox.async_api import AsyncCamoufox  # type: ignore
except Exception as _camoufox_err:  # pragma: no cover
    AsyncCamoufox = None
    _CAMOUFOX_IMPORT_ERROR = _camoufox_err
else:
    _CAMOUFOX_IMPORT_ERROR = None

# Paths & Defaults
HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
DEFAULT_ACCOUNTS_FILE = AUTO_FREECF / "github_accounts.json"
NINE_ROUTER_HOME = Path(os.environ.get("NINE_ROUTER_HOME", HOME / ".9router"))
DEFAULT_DB_PATH = NINE_ROUTER_HOME / "db" / "data.sqlite"
DEFAULT_PORT = 20128
DEFAULT_R9_BASE = f"http://127.0.0.1:{DEFAULT_PORT}"

KIRO_AUTH_BASE = "https://prod.us-east-1.auth.desktop.kiro.dev"
KIRO_REDIRECT_URI = "kiro://kiro.kiroAgent/authenticate-success"

# Rate Limit Constants
DEFAULT_MAX_ACCOUNTS = 3
DEFAULT_DELAY_MIN = 20.0
DEFAULT_DELAY_MAX = 45.0
DEFAULT_ACTION_DELAY_MIN = 1.5
DEFAULT_ACTION_DELAY_MAX = 4.0
RATE_LIMIT_BASE_BACKOFF = 30.0
RATE_LIMIT_MAX_BACKOFF = 600.0
RATE_LIMIT_MAX_CONSECUTIVE = 3


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


# ─────────────────────────────────────────────────────────── PKCE & URL Helpers
def generate_pkce(num_bytes: int = 32) -> tuple[str, str, str]:
    """Generate PKCE code_verifier, code_challenge, and state.

    Matches 9Router Node.js crypto implementation:
    - codeVerifier: 32 random bytes -> base64url (no padding)
    - codeChallenge: sha256(codeVerifier) -> base64url (no padding)
    - state: 32 random bytes -> base64url (no padding)
    """
    verifier_bytes = os.urandom(num_bytes)
    code_verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")

    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    state_bytes = os.urandom(num_bytes)
    state = base64.urlsafe_b64encode(state_bytes).rstrip(b"=").decode("ascii")

    return code_verifier, code_challenge, state


def build_kiro_auth_url(code_challenge: str, state: str, prompt: str = "select_account") -> str:
    """Build upstream Kiro desktop login URL."""
    params = {
        "idp": "Github",
        "redirect_uri": KIRO_REDIRECT_URI,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "prompt": prompt,
    }
    qs = urllib.parse.urlencode(params)
    return f"{KIRO_AUTH_BASE}/login?{qs}"


def extract_code_from_redirect_url(
    url: str, expected_state: str | None = None
) -> tuple[str | None, str | None]:
    """Extract authorization code from kiro:// custom scheme redirect URL.

    Returns:
        (code, error): Tuple where code is present on success, or error is present on failure.
    """
    if not url:
        return None, "Empty redirect URL"

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as e:
        return None, f"Failed to parse URL: {e}"

    if parsed.scheme != "kiro":
        return None, f"Invalid URL scheme: {parsed.scheme} (expected 'kiro')"

    qs = urllib.parse.parse_qs(parsed.query)

    if "error" in qs:
        err_msg = qs["error"][0]
        desc = qs.get("error_description", [""])[0]
        return None, f"{err_msg}: {desc}".strip(": ")

    code = qs.get("code", [None])[0]
    if not code:
        return None, "No 'code' parameter found in redirect URL"

    url_state = qs.get("state", [None])[0]
    if expected_state is not None and url_state != expected_state:
        return None, f"State mismatch: expected '{expected_state}', got '{url_state}'"

    return code, None


# ─────────────────────────────────────────────────────────── Rate Limiting & Backoff
class BackoffManager:
    """Manages rate-limiting and exponential backoff on HTTP 429 / 403."""

    def __init__(
        self,
        base_delay: float = RATE_LIMIT_BASE_BACKOFF,
        max_delay: float = RATE_LIMIT_MAX_BACKOFF,
        max_consecutive: int = RATE_LIMIT_MAX_CONSECUTIVE,
    ) -> None:
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_consecutive = max_consecutive
        self.consecutive_rate_limits = 0

    def should_stop(self) -> bool:
        """True if maximum consecutive rate limits reached."""
        return self.consecutive_rate_limits >= self.max_consecutive

    def record_rate_limit(self) -> float:
        """Record a 429/403 event and calculate wait delay in seconds."""
        self.consecutive_rate_limits += 1
        if self.consecutive_rate_limits > self.max_consecutive:
            return 0.0
        # 1st: base * 2^0 = 30s
        # 2nd: base * 2^1 = 60s
        # 3rd: base * 2^2 = 120s
        delay = min(self.base_delay * (2 ** (self.consecutive_rate_limits - 1)), self.max_delay)
        return delay

    def record_success(self) -> None:
        """Reset consecutive rate limits counter upon successful action."""
        self.consecutive_rate_limits = 0


def get_account_delay(min_s: float = DEFAULT_DELAY_MIN, max_s: float = DEFAULT_DELAY_MAX) -> float:
    """Return randomized delay in seconds between account processing runs."""
    return random.uniform(min_s, max_s)


def get_action_delay(
    min_s: float = DEFAULT_ACTION_DELAY_MIN, max_s: float = DEFAULT_ACTION_DELAY_MAX
) -> float:
    """Return randomized delay in seconds between browser page actions."""
    return random.uniform(min_s, max_s)


# ─────────────────────────────────────────────────────────── 9Router REST & SQLite
def derive_cli_token(r9_dir: Path | None = None) -> str:
    """Derive 9Router CLI token (x-9r-cli-token).

    Algorithm from 9Router:
    sha256(machineId + "9r-cli-auth" + cliSecret)[:16]
    """
    env_token = os.environ.get("R9_TOKEN") or os.environ.get("NINE_ROUTER_CLI_TOKEN")
    if env_token:
        return env_token.strip()

    if r9_dir is None:
        r9_dir = NINE_ROUTER_HOME

    mid_file = r9_dir / "machine-id"
    sec_file = r9_dir / "auth" / "cli-secret"

    if not mid_file.exists() or not sec_file.exists():
        return ""

    try:
        mid = mid_file.read_text(encoding="utf-8").strip()
        sec = sec_file.read_text(encoding="utf-8").strip()
        if not mid or not sec:
            return ""
        return hashlib.sha256((mid + "9r-cli-auth" + sec).encode("utf-8")).hexdigest()[:16]
    except Exception:
        return ""


def build_exchange_request(
    code: str, code_verifier: str, provider: str = "github"
) -> dict[str, str]:
    """Build request payload for 9Router social-exchange endpoint."""
    return {
        "code": code,
        "codeVerifier": code_verifier,
        "provider": provider,
    }


def exchange_via_9router_api(
    base_url: str,
    token: str,
    code: str,
    code_verifier: str,
    timeout: int = 30,
) -> tuple[int, dict[str, Any]]:
    """Call 9Router REST /api/oauth/kiro/social-exchange endpoint."""
    url = f"{base_url.rstrip('/')}/api/oauth/kiro/social-exchange"
    payload = build_exchange_request(code, code_verifier, provider="github")
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-9r-cli-token": token,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"error": str(e.reason)}
    except Exception as e:
        return 0, {"error": str(e)}


def extract_email_from_jwt(token: str) -> str | None:
    """Extract email claim from JWT payload without verification (matching 9Router)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        rem = len(payload) % 4
        if rem > 0:
            payload += "=" * (4 - rem)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
        data = json.loads(decoded)
        return data.get("email") or data.get("preferred_username") or data.get("sub")
    except Exception:
        return None


def exchange_via_kiro_direct(
    code: str,
    code_verifier: str,
    timeout: int = 30,
) -> tuple[int, dict[str, Any]]:
    """Direct token exchange against Kiro desktop auth API (fallback)."""
    url = f"{KIRO_AUTH_BASE}/oauth/token"
    payload = {
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": KIRO_REDIRECT_URI,
    }
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"error": str(e.reason)}
    except Exception as e:
        return 0, {"error": str(e)}


def build_sqlite_connection_record(
    tokens: dict[str, Any], email: str | None = None
) -> dict[str, Any]:
    """Construct providerConnections record for SQLite insertion."""
    now_ts = (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        + f".{int((time.time() % 1) * 1000):03d}Z"
    )
    expires_in = int(tokens.get("expiresIn") or 3600)
    exp_ts = (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + expires_in))
        + f".{int((time.time() % 1) * 1000):03d}Z"
    )

    conn_id = str(uuid.uuid4())
    user_email = email or extract_email_from_jwt(tokens.get("accessToken", "")) or "kiro-user"
    name = f"{user_email} (Kiro Github)"

    data_payload = {
        "accessToken": tokens.get("accessToken"),
        "refreshToken": tokens.get("refreshToken"),
        "expiresAt": exp_ts,
        "providerSpecificData": {
            "profileArn": tokens.get("profileArn"),
            "authMethod": "github",
            "provider": "Github",
        },
        "testStatus": "active",
    }

    return {
        "id": conn_id,
        "provider": "kiro",
        "authType": "oauth",
        "name": name,
        "email": user_email,
        "priority": 1,
        "isActive": 1,
        "data": json.dumps(data_payload),
        "createdAt": now_ts,
        "updatedAt": now_ts,
    }


def inject_sqlite_connection(db_path: Path, record: dict[str, Any]) -> str:
    """Insert or update Kiro connection in 9Router SQLite DB."""
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        # Find existing connection for this email/provider
        cur.execute(
            "SELECT id FROM providerConnections WHERE provider = 'kiro' AND email = ?",
            (record["email"],),
        )
        row = cur.fetchone()
        if row:
            cid = row[0]
            cur.execute(
                "UPDATE providerConnections SET data = ?, name = ?, isActive = 1, updatedAt = ? WHERE id = ?",
                (record["data"], record["name"], record["updatedAt"], cid),
            )
            con.commit()
            return cid

        # Insert new
        cur.execute(
            """INSERT INTO providerConnections
            (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["id"],
                record["provider"],
                record["authType"],
                record["name"],
                record["email"],
                record["priority"],
                record["isActive"],
                record["data"],
                record["createdAt"],
                record["updatedAt"],
            ),
        )
        con.commit()
        return record["id"]
    finally:
        con.close()


# ─────────────────────────────────────────────────────────── Challenge Detection
async def detect_challenge(page: Any) -> str | None:
    """Detect bot detection or interactive security challenges on the current page.

    Returns:
        Challenge description string if detected, otherwise None.
    """
    try:
        url = page.url.lower()
        title = (await page.title()).lower()

        # 1. Cloudflare / DataDome challenges
        if "cf-challenge" in url or "turnstile" in url:
            return "Cloudflare Turnstile / Challenge detected in URL"
        if "datadome" in url:
            return "DataDome challenge detected in URL"
        if "just a moment..." in title or "attention required" in title:
            return "Cloudflare waiting room / challenge detected in page title"

        # Check for Cloudflare Turnstile iframe
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


# ─────────────────────────────────────────────────────────── Browser OAuth Flow
async def authorize_github_kiro(
    page: Any,
    account: dict[str, Any],
    auth_url: str,
    state: str,
) -> tuple[str | None, str | None]:
    """Execute Kiro GitHub OAuth in Camoufox and capture the kiro:// launch code.

    Returns:
        (code, error): Extracted code or descriptive error.
    """
    login_id = account.get("username") or account.get("login") or account.get("email") or ""
    password = account.get("password") or ""

    captured: dict[str, Any] = {"code": None, "error": None}

    # Intercept custom scheme kiro://
    async def route_handler(route: Any) -> None:
        u = route.request.url
        code, err = extract_code_from_redirect_url(u, expected_state=state)
        if code:
            captured["code"] = code
        if err:
            captured["error"] = err
        try:
            await route.abort()
        except Exception:
            pass

    def request_handler(req: Any) -> None:
        u = req.url
        if u.startswith("kiro://"):
            code, err = extract_code_from_redirect_url(u, expected_state=state)
            if code:
                captured["code"] = code
            if err:
                captured["error"] = err

    await page.route("kiro://**", route_handler)
    page.on("request", request_handler)

    print(f"      [1/4] Navigating to Kiro social auth URL...", flush=True)
    try:
        await page.goto(auth_url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        # If navigation redirected to kiro://, it may raise an unknown protocol error in Firefox
        if captured["code"]:
            return captured["code"], None
        if "kiro://" in str(e):
            code, err = extract_code_from_redirect_url(str(e), expected_state=state)
            if code:
                return code, None
        print(f"      [!] Page load note: {e}", flush=True)

    await asyncio.sleep(get_action_delay())

    # Check challenges
    challenge = await detect_challenge(page)
    if challenge:
        return None, f"CHALLENGE_STOP: {challenge}"

    # Check if already captured immediately
    if captured["code"]:
        return captured["code"], None

    # Step 2: Handle GitHub login if prompted
    current_url = page.url
    if "github.com/login" in current_url:
        print(f"      [2/4] Entering GitHub credentials for {login_id}...", flush=True)

        login_input = page.locator("#login_field, input[name='login']").first
        if await login_input.count() > 0:
            await login_input.fill(login_id)
            await asyncio.sleep(get_action_delay())

        pass_input = page.locator("#password, input[name='password']").first
        if await pass_input.count() > 0:
            await pass_input.fill(password)
            await asyncio.sleep(get_action_delay())

        submit_btn = page.locator("input[name='commit'], button[type='submit']").first
        if await submit_btn.count() > 0:
            await submit_btn.click()
            await asyncio.sleep(get_action_delay())

        # Check challenges after login submit
        challenge_post_login = await detect_challenge(page)
        if challenge_post_login:
            return None, f"CHALLENGE_STOP: {challenge_post_login}"

    if captured["code"]:
        return captured["code"], None

    # Step 3: Handle OAuth consent / authorization screen
    await asyncio.sleep(get_action_delay())
    authorize_btn = page.locator(
        "#js-oauth-authorize-btn, button[name='authorize'], button:has-text('Authorize Kiro')"
    ).first

    if await authorize_btn.count() > 0:
        print("      [3/4] Clicking 'Authorize Kiro'...", flush=True)
        await asyncio.sleep(get_action_delay())
        await authorize_btn.click()

    # Step 4: Wait for redirect and capture code
    print("      [4/4] Awaiting Kiro redirect code...", flush=True)
    for _ in range(25):
        if captured["code"]:
            return captured["code"], None
        if captured["error"]:
            return None, captured["error"]
        await asyncio.sleep(1.0)

    # If code not caught, report honest diagnostic
    current_title = await page.title()
    return None, f"Flow stalled at {page.url} ('{current_title}') without receiving kiro:// code"


# ─────────────────────────────────────────────────────────── Execution Runner
async def run(
    accounts_path: Path,
    max_accounts: int = DEFAULT_MAX_ACCOUNTS,
    delay_min: float = DEFAULT_DELAY_MIN,
    delay_max: float = DEFAULT_DELAY_MAX,
    port: int = DEFAULT_PORT,
    headless: bool = False,
    dry_run: bool = False,
    direct_sqlite: bool = False,
) -> int:
    """Drive the Kiro GitHub social OAuth flow across accounts sequentially."""
    accounts = load_github_accounts(accounts_path)
    if not accounts:
        print(f"✗ No valid accounts found in {accounts_path}")
        return 1

    accounts_to_process = accounts[:max_accounts]
    print("=" * 65)
    print(f"  GitHub -> Kiro Social OAuth Runner")
    print(f"  Accounts loaded:    {len(accounts)} (processing up to {len(accounts_to_process)})")
    print(f"  Concurrency:        1 (strictly sequential)")
    print(f"  Rate Pacing:        {delay_min:.0f}s - {delay_max:.0f}s between accounts")
    print(f"  9Router Port:       {port}")
    print(f"  Dry Run:            {dry_run}")
    print("=" * 65)

    if dry_run:
        print("\n[DRY RUN] Simulating pipeline with pure builders...")
        backoff = BackoffManager()
        cli_token = derive_cli_token()
        print(f"  [+] CLI token derived: {'yes (' + cli_token[:6] + '…)' if cli_token else 'none'}")
        for i, acc in enumerate(accounts_to_process, 1):
            login_id = acc.get("username") or acc.get("email") or f"acc-{i}"
            verifier, challenge, state = generate_pkce()
            auth_url = build_kiro_auth_url(challenge, state)
            sample_code = "mock_auth_code_12345"
            req_body = build_exchange_request(sample_code, verifier)
            print(f"  [{i}/{len(accounts_to_process)}] Account {login_id}")
            print(f"      Auth URL: {auth_url[:80]}…")
            print(f"      Exchange Request: {json.dumps(req_body)}")
            backoff.record_success()
        print("\n[DRY RUN] Completed successfully without network or browser actions.")
        return 0

    if AsyncCamoufox is None:
        print("✗ Camoufox is not importable in this environment.", file=sys.stderr)
        print(f"  Error: {_CAMOUFOX_IMPORT_ERROR}", file=sys.stderr)
        print("  Please run with camoufox-venv:", file=sys.stderr)
        print(
            "  /home/amen/.local/share/auto-freecf/camoufox-venv/bin/python scripts/github_to_kiro.py",
            file=sys.stderr,
        )
        return 2

    base_url = f"http://127.0.0.1:{port}"
    cli_token = derive_cli_token()
    backoff_mgr = BackoffManager()

    success_count = 0
    failure_count = 0

    for idx, account in enumerate(accounts_to_process, 1):
        login_id = account.get("username") or account.get("email") or f"account-{idx}"
        print(f"\n▶ [{idx}/{len(accounts_to_process)}] Starting OAuth for {login_id}...", flush=True)

        verifier, challenge, state = generate_pkce()
        auth_url = build_kiro_auth_url(challenge, state)

        # Setup Camoufox
        camoufox_kwargs: dict[str, Any] = {
            "headless": "virtual" if headless else False,
            "geoip": True,
            "humanize": True,
            "os": "windows",
        }

        captured_code: str | None = None
        error_msg: str | None = None

        try:
            async with AsyncCamoufox(**camoufox_kwargs) as browser:
                page = await browser.new_page()
                captured_code, error_msg = await authorize_github_kiro(
                    page, account, auth_url, state
                )
        except Exception as e:
            error_msg = f"Browser exception: {type(e).__name__}: {e}"

        if error_msg and error_msg.startswith("CHALLENGE_STOP"):
            print(f"\n⛔ {error_msg}")
            print("Per hard rate-limit policy, terminating immediately on challenge.")
            return 3

        if not captured_code:
            print(f"  ✗ Failed to obtain launch code for {login_id}: {error_msg}")
            failure_count += 1
            continue

        print(f"  ✓ Captured Kiro authorization code ({captured_code[:12]}…)")

        # Exchange code
        exchange_done = False
        if not direct_sqlite:
            print(f"  [exchange] Calling 9Router API ({base_url})...", flush=True)
            status, res = exchange_via_9router_api(base_url, cli_token, captured_code, verifier)
            if status == 200 and res.get("success"):
                conn_id = res.get("connection", {}).get("id", "ok")
                print(f"  ✅ 9Router connection created successfully (ID: {conn_id})")
                backoff_mgr.record_success()
                success_count += 1
                exchange_done = True
            elif status in (429, 403):
                wait_sec = backoff_mgr.record_rate_limit()
                print(f"  ⚠️ HTTP {status} Rate Limit encountered from 9Router: {res}")
                if backoff_mgr.should_stop():
                    print("  ⛔ 3 consecutive 429/403 rate limits reached. Stopping runner.")
                    return 4
                print(f"  Backing off for {wait_sec:.1f}s before proceeding...")
                await asyncio.sleep(wait_sec)
                failure_count += 1
            else:
                print(f"  [!] 9Router API returned {status}: {res}. Attempting direct fallback...")

        # Fallback to direct exchange + SQLite if 9Router daemon is offline / requested
        if not exchange_done:
            print("  [fallback] Exchanging code directly with Kiro auth API...", flush=True)
            st, tokens = exchange_via_kiro_direct(captured_code, verifier)
            if st == 200 and tokens.get("accessToken"):
                if DEFAULT_DB_PATH.exists():
                    rec = build_sqlite_connection_record(tokens, email=account.get("email"))
                    cid = inject_sqlite_connection(DEFAULT_DB_PATH, rec)
                    print(f"  ✅ Injected Kiro connection directly into SQLite (ID: {cid})")
                    backoff_mgr.record_success()
                    success_count += 1
                    exchange_done = True
                else:
                    print(f"  ✗ SQLite DB not found at {DEFAULT_DB_PATH}")
                    failure_count += 1
            else:
                print(f"  ✗ Direct exchange failed ({st}): {tokens}")
                failure_count += 1

        # Inter-account pacing delay (except after last account)
        if idx < len(accounts_to_process):
            delay = get_account_delay(delay_min, delay_max)
            print(f"  ⏳ Pacing delay: waiting {delay:.1f}s before next account...", flush=True)
            await asyncio.sleep(delay)

    print("\n" + "=" * 65)
    print(f"  Run Complete: {success_count} succeeded, {failure_count} failed.")
    print("=" * 65)
    return 0 if failure_count == 0 else 1


# ─────────────────────────────────────────────────────────── CLI Entrypoint
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Drive 9Router Kiro GitHub social OAuth from stored accounts."
    )
    ap.add_argument(
        "--accounts",
        default=str(DEFAULT_ACCOUNTS_FILE),
        help=f"Path to github_accounts.json (default: {DEFAULT_ACCOUNTS_FILE})",
    )
    ap.add_argument(
        "--max-accounts",
        type=int,
        default=DEFAULT_MAX_ACCOUNTS,
        help=f"Max accounts to process in this run (default: {DEFAULT_MAX_ACCOUNTS})",
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
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"9Router port (default: {DEFAULT_PORT})",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        help="Run Camoufox in headless mode ('virtual' Xvfb)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate account loading, PKCE, and request building without browser",
    )
    ap.add_argument(
        "--direct-sqlite",
        action="store_true",
        help="Directly exchange with Kiro and upsert SQLite DB instead of REST daemon",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="Check prerequisites, token derivation, and configuration then exit",
    )

    args = ap.parse_args()

    if args.check:
        print("=" * 60)
        print("  github_to_kiro --check")
        print("=" * 60)
        print(f"  Accounts file:    {args.accounts} ({'exists' if Path(args.accounts).exists() else 'missing'})")
        token = derive_cli_token()
        print(f"  CLI Token:        {'derived (' + token[:6] + '…)' if token else 'missing / not configured'}")
        print(f"  SQLite DB:        {DEFAULT_DB_PATH} ({'exists' if DEFAULT_DB_PATH.exists() else 'missing'})")
        print(f"  Camoufox:         {'available' if AsyncCamoufox is not None else 'not available (' + str(_CAMOUFOX_IMPORT_ERROR) + ')'}")
        print("=" * 60)
        return 0

    return asyncio.run(
        run(
            accounts_path=Path(args.accounts),
            max_accounts=args.max_accounts,
            delay_min=args.delay_min,
            delay_max=args.delay_max,
            port=args.port,
            headless=args.headless,
            dry_run=args.dry_run,
            direct_sqlite=args.direct_sqlite,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
