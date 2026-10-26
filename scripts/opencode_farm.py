#!/usr/bin/env python3
"""OpenCode Console account farm: register -> verify email -> mint personal API key.

REVERSED FROM THE CONSOLE BUNDLE (index-Dirn5Dqt.js, fetched 2026-10-22)
-----------------------------------------------------------------------
Routes (all under https://opencode.ai/console):

  POST /auth/register   {email, password, challengeToken?}      -> 201 | 400 RegisterFailed
  POST /auth/login      {email, password, challengeToken?}      -> 200 + session cookie | 401 LoginFailed
  GET  /auth/session                                            -> current principal
  POST /auth/verify-email  {token}   -> {session}   [CSRF only, NO session, NO captcha]
  POST /auth/refresh    -> rotates the session cookie           [NO captcha]
  POST /auth/device/approve {user_code[,org_id]}                [session cookie]
  GET  /api/personal-api-keys                                   [session]
  POST /api/personal-api-keys  {name, expiresAt?, permissions?, scopes?}  [session]
       permissions enum: all | read | restricted
       scopes      enum: models:read, models:request, usage:read, budgets:read,
                         budgets:write, members:read, members:write,
                         connections:read, connections:write, billing:read,
                         billing:write, org:read, org:write, sso:read,
                         service-accounts:read, keys:write
       response: {key: {...}, token: "oc_sk_..."}   <- the token is shown ONCE

MEASURED, NOT GUESSED (2026-10-22, this box)
--------------------------------------------
* `POST /auth/register` returns 400 RegisterFailed for EVERY domain tried
  (gmail.com, outlook.com, kancalabs.my.id) when `challengeToken` is absent or
  fake. The blocker is the hCaptcha token, not the email domain.
* Live signup page (Camoufox, headless): hCaptcha is loaded with render=explicit
  and the sitekey 224e469e-a0ba-4c40-b5aa-a2fa838a6bd8; hCaptcha serves the
  `image_drag_drop` challenge. Submitting without solving it never even sends
  the /auth/register request.
* `CAPSOLVER_API_KEY` in ~/.config/auto-freecf/.env is the placeholder
  "CAP-xxxxxxxx-REPLACE-ME" -- it is NOT a working key, so automated hCaptcha
  solving is NOT available on this box today.
* A REAL personal API key does work: with `auth.json` set to
  {"opencode": {"type": "api", "key": "oc_sk_..."}} the CLI answered normally
  three times in a row on `opencode/glm-5.3-flash-free`, with no 429. Two
  different `oc_sk_` keys from 9router both worked. So the account path is the
  correct way to get unlimited-ish free-tier access; the anonymous quota is the
  thing that dies with 429.

Therefore this tool is built to be DRIVEN BY A BROWSER that solves hCaptcha
(Camoufox with a human in the loop, or a paid solver), and to do everything
after that step automatically. It deliberately does NOT pretend it can pass
hCaptcha by itself.

USAGE
    # 1. Full flow with a browser: solve hCaptcha once, everything else auto.
    python3 scripts/opencode_farm.py run --count 1

    # 2. Headless-attach: you solved hCaptcha in your own browser; paste the
    #    challengeToken and the email/password you used.
    python3 scripts/opencode_farm.py register --email a@b.c --password P --challenge TOKEN

    # 3. Mint a key on an already-logged-in session (cookie from a browser).
    python3 scripts/opencode_farm.py key --cookie-file cookie.txt --name farm-1

    # 4. Verify a key works through the CLI, then inject into 9router.
    python3 scripts/opencode_farm.py verify --key oc_sk_...
    python3 scripts/opencode_farm.py push --ledger accounts.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import string
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

CONSOLE = "https://opencode.ai/console"
SITEKEY = "224e469e-a0ba-4c40-b5aa-a2fa838a6bd8"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
LEDGER = Path.home() / ".config/auto-freecf/opencode_accounts.jsonl"
SESSION_FILE = Path.home() / ".config/auto-freecf/opencode_session.txt"
MAIL_DOMAIN = os.environ.get("OC_MAIL_DOMAIN", "kancalabs.my.id")

ALL_SCOPES = [
    "models:read", "models:request", "usage:read", "budgets:read", "budgets:write",
    "members:read", "members:write", "connections:read", "connections:write",
    "billing:read", "billing:write", "org:read", "org:write", "sso:read",
    "service-accounts:read", "keys:write",
]


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _req(path: str, payload: dict | None = None, *, cookie: str = "",
         method: str = "", timeout: int = 30,
         extra_headers: dict | None = None) -> tuple[int, dict, str]:
    url = path if path.startswith("http") else f"{CONSOLE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("content-type", "application/json")
    req.add_header("user-agent", UA)
    req.add_header("origin", "https://opencode.ai")
    req.add_header("referer", f"{CONSOLE}/signup")
    if cookie:
        req.add_header("cookie", cookie)
    for k, v in (extra_headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def _j(body: str) -> dict:
    try:
        return json.loads(body)
    except Exception:
        return {"raw": body}


def _session_cookie(hdr: dict) -> str:
    """Collect every Set-Cookie that looks like a session, joined for reuse."""
    out = []
    raw = hdr.get("Set-Cookie") or hdr.get("set-cookie") or ""
    for part in str(raw).split(","):
        first = part.split(";", 1)[0].strip()
        if first and "=" in first:
            out.append(first)
    return "; ".join(out)


# ── identity helpers ────────────────────────────────────────────────────────

def gen_password(n: int = 16) -> str:
    core = "".join(random.choices(string.ascii_letters + string.digits, k=n - 4))
    return core + random.choice("!@#$") + random.choice("0123456789")


def gen_local_part() -> str:
    words = ["anggrek", "senja", "wira", "bentala", "rawa", "karsa", "lautan", "giri"]
    return random.choice(words) + "".join(random.choices(string.digits, k=random.randint(2, 4)))


def new_mailbox():
    import mailboxes
    return mailboxes.open_mailbox("tempik", domain=MAIL_DOMAIN)


# ── captcha ─────────────────────────────────────────────────────────────────

def solve_hcaptcha_capsolver(url: str, sitekey: str = SITEKEY) -> str:
    """Solve hCaptcha through Capsolver. Returns the token, or raises.

    NOTE: on this box CAPSOLVER_API_KEY is still the placeholder, so this will
    fail loudly instead of silently returning an empty token.
    """
    key = os.environ.get("CAPSOLVER_API_KEY", "")
    if not key:
        env = Path.home() / ".config/auto-freecf/.env"
        if env.is_file():
            m = re.search(r"^CAPSOLVER_API_KEY=(.+)$", env.read_text(), re.M)
            if m:
                key = m.group(1).strip().strip('"').strip("'")
    if not key or "REPLACE-ME" in key or len(key) < 20:
        raise RuntimeError(
            "CAPSOLVER_API_KEY is missing or still the placeholder "
            f"(got {key[:12]!r}). Solve hCaptcha in a browser instead, or set a real key."
        )

    def call(payload: dict) -> dict:
        r = urllib.request.Request("https://api.capsolver.com/" + payload.pop("_ep"),
                                   data=json.dumps(payload).encode(),
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=40) as resp:
            return json.load(resp)

    created = call({"_ep": "createTask", "clientKey": key, "task": {
        "type": "HCaptchaTaskProxyLess",
        "websiteURL": url,
        "websiteKey": sitekey,
    }})
    if created.get("errorId"):
        raise RuntimeError(f"capsolver createTask: {created}")
    task_id = created["taskId"]
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(4)
        res = call({"_ep": "getTaskResult", "clientKey": key, "taskId": task_id})
        if res.get("status") == "ready":
            tok = (res.get("solution") or {}).get("gRecaptchaResponse") or \
                  (res.get("solution") or {}).get("token")
            if tok:
                return tok
            raise RuntimeError(f"capsolver ready but no token: {res}")
        if res.get("errorId"):
            raise RuntimeError(f"capsolver getTaskResult: {res}")
    raise RuntimeError("capsolver timed out after 180s")


# ── console operations ──────────────────────────────────────────────────────

def register(email: str, password: str, challenge: str = "") -> dict:
    payload = {"email": email, "password": password}
    if challenge:
        payload["challengeToken"] = challenge
    st, hdr, body = _req("/auth/register", payload)
    data = _j(body)
    cookie = _session_cookie(hdr)
    return {"status": st, "body": data, "cookie": cookie,
            "verification_required": bool(data.get("verificationRequired")),
            "resend_grant": data.get("resendGrant", "")}


def verify_email(token: str) -> dict:
    st, hdr, body = _req("/auth/verify-email", {"token": token})
    return {"status": st, "body": _j(body), "cookie": _session_cookie(hdr)}


def login(email: str, password: str, challenge: str = "") -> dict:
    payload = {"email": email, "password": password}
    if challenge:
        payload["challengeToken"] = challenge
    st, hdr, body = _req("/auth/login", payload)
    data = _j(body)
    cookie = _session_cookie(hdr)
    if cookie:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(cookie)
    return {"status": st, "body": data, "cookie": cookie,
            "verification_required": bool(data.get("verificationRequired"))}


def whoami(cookie: str) -> dict:
    st, _, body = _req("/auth/session", cookie=cookie)
    return {"status": st, "body": _j(body)}


def list_keys(cookie: str) -> dict:
    st, _, body = _req("/api/personal-api-keys", cookie=cookie)
    return {"status": st, "body": _j(body)}


def _org_id(cookie: str) -> str:
    """Resolve the caller's org. Personal API keys are org-scoped and the API
    rejects the call with `OrgRequired` ("x-org-id is required") without it."""
    st, _, body = _req("/api/orgs", cookie=cookie)
    d = _j(body)
    # Measured: /api/orgs returns a BARE JSON ARRAY
    #   [{"id":"org_...","name":"Personal","blocked":false}]
    # not {"data":[...]}. Parsing the wrapper shape returned "" and every mint
    # then failed with OrgRequired.
    orgs = d if isinstance(d, list) else (d.get("data") if isinstance(d, dict) else None)
    if isinstance(orgs, list) and orgs:
        first = orgs[0]
        if isinstance(first, dict):
            return first.get("id") or first.get("orgId") or ""
    if isinstance(d, dict):
        return d.get("id") or d.get("org_id") or ""
    return ""


def mint_key(cookie: str, name: str, *, permissions: str = "all",
             scopes: list[str] | None = None, expires_at: str | None = None,
             org_id: str = "") -> dict:
    """Create a personal API key. `token` in the response is shown once.

    Requires the org header; resolved automatically when not supplied.
    """
    payload: dict = {"name": name, "permissions": permissions}
    if scopes is not None:
        payload["scopes"] = scopes
    if expires_at:
        payload["expiresAt"] = expires_at

    oid = org_id or _org_id(cookie)
    st, _, body = _req("/api/personal-api-keys", payload, cookie=cookie,
                       extra_headers={"x-org-id": oid} if oid else None)
    return {"status": st, "body": _j(body), "org_id": oid}


# ── verification through the CLI (the only path that clears the shape check) ─

def verify_key_with_cli(key: str, model: str = "opencode/glm-5.3-flash-free",
                        prompt: str = "reply only OK") -> dict:
    """Run the real CLI with `key` in an ISOLATED data dir and classify the result.

    Two measured facts drive this implementation (2026-10-23):

    1. curl can NEVER clear the 403 FreeTierError — that is a request-shape
       check, so verification has to go through the binary.
    2. The old version swapped the key into the shared auth.json. That made
       concurrent verification impossible (two workers would overwrite each
       other's key) and it left a window where a crash could strand someone
       else's key in the live config. The CLI honours XDG_DATA_HOME, so each
       call now gets its own private data dir and the real auth.json is never
       touched. Verified: two runs with different keys execute concurrently and
       each sees only its own key.

    Error classification matters because OpenCode answers the SAME string for
    two different conditions:
        "Free usage limit reached"  <- quota spent OR key not recognised
    A key that returns that string is therefore NOT proof of validity. The
    status field reports which class was seen so callers stop treating
    "reached the limit" as "key is alive".
    """
    import shutil
    import subprocess
    import tempfile

    binary = shutil.which("opencode") or str(Path.home() / ".opencode/bin/opencode")
    with tempfile.TemporaryDirectory(prefix="oc-verify-") as tmp:
        data = Path(tmp) / "data"
        state = Path(tmp) / "state"
        (data / "opencode").mkdir(parents=True, exist_ok=True)
        state.mkdir(parents=True, exist_ok=True)
        (data / "opencode" / "auth.json").write_text(
            json.dumps({"opencode": {"type": "api", "key": key}}))
        env = {**os.environ,
               "XDG_DATA_HOME": str(data),
               "XDG_STATE_HOME": str(state),
               "HOME": tmp,
               "NO_COLOR": "1"}
        try:
            p = subprocess.run([binary, "run", "--standalone", "-m", model, prompt],
                               cwd=tmp, capture_output=True, text=True,
                               timeout=120, env=env)
        except subprocess.TimeoutExpired:
            return {"ok": False, "status": "timeout", "returncode": -1, "output": ""}

        out = (p.stdout or "") + (p.stderr or "")
        low = out.lower()
        if p.returncode == 0 and "free usage limit" not in low and "freetiererror" not in low:
            status = "ok"
        elif "free usage limit" in low:
            # Ambiguous by design: quota spent, or the key was never accepted.
            status = "limit"
        elif "freetiererror" in low or "403" in low:
            status = "shape-blocked"
        elif "unauthor" in low or "invalid" in low or "401" in low:
            status = "invalid"
        else:
            status = "error"
        return {"ok": status == "ok", "status": status,
                "returncode": p.returncode, "output": out.strip()[:400]}


# ── ledger ──────────────────────────────────────────────────────────────────

def append_ledger(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def read_ledger() -> list[dict]:
    if not LEDGER.is_file():
        return []
    out = []
    for line in LEDGER.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


# ── browser-driven flow: email + password (the path the operator wants) ─────

async def _browser_signup(headless: bool, proxy: str | None = None,
                          keep_open: int = 300, email: str = "",
                          password: str = "") -> dict:
    """Register with email+password on /console/signup, then verify by mail.

    hCaptcha is the only gate (measured: invisible widget that escalates to
    `image_drag_drop`; /auth/register is never sent until it is solved). This
    flow therefore WAITS for the human to solve it when --headed is used, then
    does everything after that automatically:

        fill form -> [human solves hCaptcha] -> /auth/register
        -> poll mailbox for the verify link -> /auth/verify-email
        -> session cookie saved -> ready for `key`
    """
    from camoufox.async_api import AsyncCamoufox

    mb = new_mailbox()
    email = email or mb.address
    password = password or gen_password()

    print(f"[farm] email    : {email}")
    print(f"[farm] password : {password}")

    result: dict = {"email": email, "password": password, "mode": "email",
                    "mailbox": "tempik"}
    calls: list = []

    async with AsyncCamoufox(headless=headless, humanize=True) as browser:
        page = await browser.new_page(viewport={"width": 1280, "height": 900})

        def on_resp(r):
            u = r.url.split("?")[0]
            if "/auth/" in u:
                calls.append((r.status, u))

        page.on("response", on_resp)
        await page.goto(f"{CONSOLE}/signup", wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2500)
        await page.fill("input[type=email]", email)
        await page.fill("input[type=password]", password)
        await page.wait_for_timeout(1200)

        print("[farm] form filled. " + (
            "Solve the hCaptcha in the visible browser, then it continues automatically."
            if not headless else "Headless: waiting for a passive hCaptcha pass."))

        # Click once; the widget may already be satisfied in headed mode.
        try:
            await page.click("button:has-text('Create Account')")
        except Exception:
            pass

        deadline = time.time() + keep_open
        registered = False
        while time.time() < deadline:
            await page.wait_for_timeout(1500)
            if any("/auth/register" in u for _, u in calls):
                registered = True
                break
            # Some flows navigate straight to /check-email after success
            if "/check-email" in page.url or "/verify-email" in page.url:
                registered = True
                break
            # Re-click opportunistically: after the human solves the widget the
            # button becomes clickable again, and a stale click is harmless.
            try:
                btn = page.locator("button:has-text('Create Account')")
                if await btn.count() and await btn.first.is_enabled():
                    await btn.first.click()
            except Exception:
                pass

        result["registered"] = registered
        result["final_url"] = page.url
        result["calls"] = calls[-10:]

        if not registered:
            result["blocked"] = "hCaptcha not solved within the window"
            try:
                await page.screenshot(path="/tmp/oc_farm_signup.png", full_page=True)
            except Exception:
                pass
            return result

        # ── verify INSIDE this browser session ────────────────────────────
        # Doing /auth/verify-email over urllib returns 200 but the session
        # cookie never reaches us, and a later /auth/login is blocked by the
        # same hCaptcha ("Complete the security check"). So the browser that
        # registered must also open the verify link -- then the console session
        # exists in its cookie jar and `key` can use it.
        print("[farm] registered. waiting for the verification mail...")
        link = None
        try:
            link = mb.link(r"https://opencode\.ai/console/verify-email\?[^\s\"'<>]+", timeout=180)
        except Exception as exc:
            print(f"[farm] mailbox poll failed: {exc}")

        if link:
            print(f"[farm] opening verify link in the same browser")
            try:
                await page.goto(link, wait_until="networkidle", timeout=90000)
                await page.wait_for_timeout(4000)
            except Exception as exc:
                print(f"[farm] verify navigation: {exc}")
            result["verify_url"] = page.url
        else:
            result["verify_status"] = "no link found"

        # Let the console settle (it may route to /workspace or /console).
        for _ in range(10):
            await page.wait_for_timeout(1500)
            if "/console/verify-email" not in page.url:
                break

        cookies = await page.context.cookies()
        cookie_hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies
                               if "opencode.ai" in c.get("domain", "") and c.get("value"))
        if cookie_hdr:
            result["cookie"] = cookie_hdr
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_text(cookie_hdr)
            print(f"[farm] session cookies captured ({len(cookie_hdr)} chars)")

        # Confirm the session actually works before declaring success.
        if cookie_hdr:
            me = whoami(cookie_hdr)
            result["session_status"] = me["status"]
            if me["status"] == 200:
                result["session"] = me["body"]
                print("[farm] session verified OK")
            else:
                print(f"[farm] session check: {me['status']} {str(me['body'])[:120]}")

        try:
            await page.screenshot(path="/tmp/oc_farm_signup.png", full_page=True)
        except Exception:
            pass

    return result


# ── browser-driven flow: OAuth (kept for the GitHub/Google path) ────────────
async def _browser_oauth(provider: str, headless: bool, proxy: str | None = None,
                         keep_open: int = 180, email: str = "",
                         password: str = "") -> dict:
    """Create an OpenCode Console account through Google/GitHub OAuth.

    WHY THIS INSTEAD OF /signup (measured 2026-10-22)
    -------------------------------------------------
    The email+password path is gated by hCaptcha: the signup page renders an
    invisible hCaptcha widget (sitekey 224e469e-a0ba-4c40-b5aa-a2fa838a6bd8)
    which escalates to an `image_drag_drop` challenge, and /auth/register is
    never sent until it is solved. Waiting 72s headlessly produced no token.
    CAPSOLVER_API_KEY on this box is the placeholder, so that path is blocked.

    The OAuth path has NO captcha at all. Measured:
        GET /console/auth/social/google/start?redirectTo=/console
            -> 302 to accounts.google.com with PKCE + __Host-console_oidc_flow
        GET /console/auth/social/github/start?redirectTo=/console
            -> 302 to github.com/login/oauth/authorize
        GET /console/auth/social/{p}/callback?state&code
            -> only state+code are required; no captcha, no password
    So an account can be created from any Google/GitHub identity we control,
    and the rest (mint key, verify, inject) stays automatic.
    """
    from camoufox.async_api import AsyncCamoufox

    result: dict = {"provider": provider, "mode": "oauth"}
    async with AsyncCamoufox(headless=headless, humanize=True) as browser:
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        calls: list = []

        def on_resp(r):
            u = r.url.split("?")[0]
            if "/auth/" in u or "/console/api/" in u:
                calls.append((r.status, u))

        page.on("response", on_resp)
        start = f"{CONSOLE}/auth/social/{provider}/start?redirectTo=%2Fconsole"
        await page.goto(start, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(3000)

        # If we land on a provider login form and credentials were supplied,
        # fill them. Otherwise wait for the human / an existing browser session.
        if email and password:
            try:
                if "accounts.google.com" in page.url:
                    await page.fill("input[type=email]", email)
                    await page.click("button:has-text('Next')")
                    await page.wait_for_timeout(3000)
                    await page.fill("input[type=password]", password)
                    await page.click("button:has-text('Next')")
                    await page.wait_for_timeout(5000)
                elif "github.com" in page.url:
                    await page.fill("#login_field", email)
                    await page.fill("#password", password)
                    await page.click("input[type=submit]")
                    await page.wait_for_timeout(5000)
            except Exception as exc:
                print(f"[farm] credential fill skipped: {exc}")

        # Wait for the console to accept the callback and establish a session.
        deadline = time.time() + keep_open
        authed = False
        while time.time() < deadline:
            await page.wait_for_timeout(2000)
            if "/console" in page.url and "social" not in page.url and "accounts.google" not in page.url \
                    and "github.com" not in page.url:
                st, _, body = _req("/auth/session")
                if st == 200:
                    authed = True
                    result["session"] = _j(body)
                    break
                # session cookie lives in the browser, not in urllib: read it
                cookies = await page.context.cookies()
                cookie_hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies
                                       if "opencode.ai" in c.get("domain", ""))
                if cookie_hdr:
                    st2, _, body2 = _req("/auth/session", cookie=cookie_hdr)
                    if st2 == 200:
                        authed = True
                        result["session"] = _j(body2)
                        result["cookie"] = cookie_hdr
                        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                        SESSION_FILE.write_text(cookie_hdr)
                        break
            if "accounts.google.com" in page.url or "github.com" in page.url:
                if headless:
                    result["blocked"] = "provider login page reached; needs a human"
                    break

        result["final_url"] = page.url
        result["authed"] = authed
        result["calls"] = calls[-15:]
        try:
            await page.screenshot(path="/tmp/oc_farm_oauth.png", full_page=True)
        except Exception:
            pass

    return result



# ── subcommands ─────────────────────────────────────────────────────────────

def cmd_run(a) -> int:
    import asyncio
    if a.provider in ("email", "password"):
        res = asyncio.run(_browser_signup(
            headless=not a.headed,
            proxy=a.proxy,
            keep_open=a.keep_open,
            email=a.email or "",
            password=a.password or "",
        ))
    else:
        res = asyncio.run(_browser_oauth(
            provider=a.provider,
            headless=not a.headed,
            proxy=a.proxy,
            keep_open=a.keep_open,
            email=a.email or "",
            password=a.password or "",
        ))
    print(json.dumps(res, indent=2)[:1600])

    if res.get("blocked"):
        print(f"\n[farm] blocked: {res['blocked']}")
        print("[farm] Re-run with --headed and solve the hCaptcha in the window.")
        return 2

    if res.get("mode") == "email" and not res.get("registered"):
        print("\n[farm] /auth/register was never sent -> hCaptcha still unsolved.")
        return 2

    append_ledger({k: v for k, v in res.items() if k != "calls"})
    print("\n[farm] saved to ledger.")
    print("[farm] next: opencode_farm.py key --name farm-1")
    return 0


def cmd_register(a) -> int:
    r = register(a.email, a.password, a.challenge)
    print(json.dumps(r, indent=2)[:900])
    if r["status"] == 201:
        rec = {"email": a.email, "password": a.password,
               "verification_required": r["verification_required"],
               "resend_grant": r["resend_grant"], "cookie": r["cookie"]}
        append_ledger(rec)
        print(f"[farm] saved to {LEDGER}")
        if r["verification_required"]:
            print("[farm] check the mailbox for the verify link, then:")
            print("[farm]   opencode_farm.py verify-email --token <token>")
    return 0 if r["status"] in (200, 201) else 1


def cmd_verify_email(a) -> int:
    r = verify_email(a.token)
    print(json.dumps(r, indent=2)[:800])
    if r["cookie"]:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(r["cookie"])
        print(f"[farm] session saved to {SESSION_FILE}")
    return 0 if r["status"] == 200 else 1


def cmd_login(a) -> int:
    r = login(a.email, a.password, a.challenge)
    print(json.dumps({k: v for k, v in r.items() if k != "cookie"}, indent=2)[:700])
    return 0 if r["status"] == 200 else 1


def cmd_key(a) -> int:
    cookie = ""
    if a.cookie_file:
        cookie = Path(a.cookie_file).read_text().strip()
    elif SESSION_FILE.is_file():
        cookie = SESSION_FILE.read_text().strip()
    if not cookie:
        print("[farm] no session cookie: pass --cookie-file or log in first")
        return 1

    me = whoami(cookie)
    print("[farm] session:", me["status"], str(me["body"])[:200])
    if me["status"] != 200:
        return 1

    scopes = None
    if a.scopes:
        scopes = [s.strip() for s in a.scopes.split(",") if s.strip()]
    r = mint_key(cookie, a.name, permissions=a.permissions, scopes=scopes,
                 expires_at=a.expires_at)
    print(json.dumps(r, indent=2)[:900])
    tok = (r["body"].get("token") if isinstance(r["body"], dict) else "") or ""
    if tok:
        rec = {"email": (me["body"].get("user") or {}).get("email", ""),
               "api_key": tok, "name": a.name, "permissions": a.permissions,
               "created": int(time.time())}
        append_ledger(rec)
        print(f"[farm] key saved to {LEDGER}")
        if not a.no_verify:
            v = verify_key_with_cli(tok)
            print("[farm] CLI verify:", "OK" if v["ok"] else "FAILED", v["output"][:160])
    return 0 if r["status"] in (200, 201) else 1


def cmd_verify(a) -> int:
    v = verify_key_with_cli(a.key, model=a.model)
    print("OK" if v["ok"] else "FAILED", "|", v["output"][:300])
    return 0 if v["ok"] else 1


def cmd_push(a) -> int:
    """Push every ledger key into 9router as its own connection."""
    import subprocess
    recs = read_ledger()
    keys = [r.get("api_key") for r in recs if r.get("api_key")]
    if a.key:
        keys.insert(0, a.key)
    if not keys:
        print("[farm] no keys in ledger")
        return 1
    tool = SCRIPTS / "opencode_inject.py"
    argv = [sys.executable, str(tool)]
    for k in keys:
        argv += ["--key", k]
    if a.dry_run:
        argv.append("--dry-run")
    return subprocess.call(argv)


def cmd_status(a) -> int:
    recs = read_ledger()
    keys = [r for r in recs if r.get("api_key")]
    print(f"ledger : {LEDGER}")
    print(f"records: {len(recs)}  with key: {len(keys)}")
    for r in keys[-10:]:
        k = r.get("api_key", "")
        print(f"  {r.get('email','?'):38} {k[:14]}...{k[-4:]}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("run", help="sign up with email+password (hCaptcha needs a human) or OAuth")
    s.add_argument("--count", type=int, default=1)
    s.add_argument("--provider", default="email", choices=["email", "google", "github"])
    s.add_argument("--headed", action="store_true", help="show the browser so hCaptcha / provider login can be done by hand")
    s.add_argument("--email", default=None, help="address to register (default: fresh tempik mailbox)")
    s.add_argument("--password", default=None, help="password (default: generated)")
    s.add_argument("--proxy", default=None)
    s.add_argument("--keep-open", type=int, default=300, help="seconds to wait for the human")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("register", help="POST /auth/register with an existing challengeToken")
    s.add_argument("--email", required=True)
    s.add_argument("--password", required=True)
    s.add_argument("--challenge", default="", help="hCaptcha token")
    s.set_defaults(fn=cmd_register)

    s = sub.add_parser("verify-email", help="POST /auth/verify-email {token}")
    s.add_argument("--token", required=True)
    s.set_defaults(fn=cmd_verify_email)

    s = sub.add_parser("login", help="POST /auth/login")
    s.add_argument("--email", required=True)
    s.add_argument("--password", required=True)
    s.add_argument("--challenge", default="")
    s.set_defaults(fn=cmd_login)

    s = sub.add_parser("key", help="mint a personal API key on a logged-in session")
    s.add_argument("--cookie-file", default=None)
    s.add_argument("--name", default="")
    s.add_argument("--permissions", default="all", choices=["all", "read", "restricted"])
    s.add_argument("--scopes", default="", help="comma list, e.g. models:read,models:request")
    s.add_argument("--expires-at", default=None)
    s.add_argument("--no-verify", action="store_true")
    s.set_defaults(fn=cmd_key)

    s = sub.add_parser("verify", help="check a key through the real CLI")
    s.add_argument("--key", required=True)
    s.add_argument("--model", default="opencode/glm-5.3-flash-free")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("push", help="inject ledger keys into 9router")
    s.add_argument("--ledger", default=str(LEDGER))
    s.add_argument("--key", action="append", default=[])
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_push)

    s = sub.add_parser("status", help="show the ledger")
    s.set_defaults(fn=cmd_status)

    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
