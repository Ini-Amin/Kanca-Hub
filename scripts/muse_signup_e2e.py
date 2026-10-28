#!/usr/bin/env python3
"""scripts/muse_signup_e2e.py — Meta Muse (muse.ai) end-to-end onboarding.

One resumable state machine instead of ad-hoc probing. Every stage writes the
browser cookies plus the egress identity to a per-account state file, so a
re-run resumes where the last one stopped instead of re-registering and burning
a fresh OTP (the failure mode that got this account rate-limited).

Stages, in order:
  signup   new Meta identity: invite link -> email -> OTP -> profile details
  agegate  /access/verification: link an adult Instagram/Facebook account
           (credit card is refused by design: it needs a real card)
  onboard  accept TOS -> name the agent -> wait for the Secure VM
  chat     send a first message in the real UI and capture the agent's reply

The Muse chat itself is an end-to-end encrypted gateway (noise x25519 +
attestation), so it is driven through the browser rather than reimplemented.
The API probes below are only used to *assert* a stage actually completed.

Usage:
  python3 scripts/muse_signup_e2e.py --via facebook --until chat
  python3 scripts/muse_signup_e2e.py --resume satriakencang81 --until chat
  python3 scripts/muse_signup_e2e.py --resume satriakencang81 --until agegate --headed
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.mailboxes as M
import scripts.persona as persona

HOME = Path.home()
LEDGER_PATH = HOME / ".config/auto-freecf/muse_accounts.json"
FB_LEDGER_PATH = HOME / ".config/auto-freecf/facebook_accounts.json"
IG_LEDGER_PATH = HOME / ".config/auto-freecf/instagram_accounts.json"
STATE_DIR = HOME / ".config/auto-freecf/muse_state"
SHOT_DIR = HOME / ".config/auto-freecf/muse_shots"

STAGES = ("signup", "agegate", "onboard", "chat")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


# ---------------------------------------------------------------- state file

def _state_path(key: str) -> Path:
    return STATE_DIR / f"{key}.json"


def load_state(key: str) -> dict:
    p = _state_path(key)
    if p.exists():
        st = json.loads(p.read_text())
        # A state file written before the jar existed only has the per-domain
        # map, and `run()` used to inject `state["cookies"]` — a key that never
        # existed — so a "resume" silently ran anonymous (401 on every API).
        if not st.get("cookie_jar"):
            st["cookie_jar"] = _jar_from_map(st.get("cookies_by_domain") or {})
        return st
    # Adopt an account that predates this state file from the shared ledger.
    for a in _ledger(LEDGER_PATH):
        if a.get("email", "").split("@")[0] == key:
            print(f"[adopt] {a['email']} from {LEDGER_PATH.name}")
            jar = a.get("cookie_jar") or _jar_from_map(
                {"muse.ai": a.get("cookies") or {},
                 "facebook.com": a.get("fb_cookies") or {}})
            return {
                "key": key,
                "email": a["email"],
                "name": a.get("name") or "Muse User",
                "dob": ("June", "15", "1998"),
                "via": a.get("auth_via") or "email",
                "invite": a.get("invite_code"),
                "stage": a.get("stage") or "agegate",
                "cookie_jar": jar,
                "created_at": a.get("created_at") or _now(),
            }
    return {"key": key, "stage": "signup", "cookie_jar": [], "created_at": _now()}


def _jar_from_map(by_domain: dict) -> list[dict]:
    """{domain: {name: value}} -> Playwright cookie jar.

    `__Host-*` / `__Secure-*` cookies must carry NO Domain attribute (Chromium
    rejects them otherwise) and the session then looks logged-out, which is a
    maddening failure because every other cookie looks fine.
    """
    jar: list[dict] = []
    for dom, cookies in by_domain.items():
        if not cookies:
            continue
        for name, value in cookies.items():
            c = {"name": name, "value": value, "path": "/", "secure": True}
            if not (name.startswith("__Host") or name.startswith("__Secure")):
                c["domain"] = dom if dom.startswith(".") else f".{dom}"
            jar.append(c)
    return jar


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now()
    p = _state_path(state["key"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- ledgers

def _ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def pick_facebook(email: str = "") -> dict:
    accounts = _ledger(FB_LEDGER_PATH)
    if not accounts:
        raise RuntimeError(f"no Facebook accounts in {FB_LEDGER_PATH}")
    if not email:
        for a in accounts:
            if a.get("email") == "satriakencang81@kancalabs.my.id":
                return a
        return accounts[0]
    for a in accounts:
        if a["email"].lower() == email.lower():
            return a
    raise RuntimeError(f"Facebook account {email} not in {FB_LEDGER_PATH}")


def pick_instagram(email: str = "") -> dict:
    """An Instagram login we own; it is the free age anchor (birth_year > 18)."""
    accounts = _ledger(IG_LEDGER_PATH)
    if not accounts:
        raise RuntimeError(f"no Instagram accounts in {IG_LEDGER_PATH}")
    if email:
        for a in accounts:
            if a["email"].lower() == email.lower():
                return a
    for a in accounts:
        if a.get("password") and int(a.get("birth_year") or 0) <= datetime.now().year - 18:
            return a
    raise RuntimeError(f"no usable Instagram account (needs password + adult birth_year) in {IG_LEDGER_PATH}")


# ---------------------------------------------------------------- browser

def _load_env_files() -> None:
    """Pull provider keys from the shared env files (tinyfish.env, .env)."""
    for name in ("tinyfish.env", ".env"):
        p = HOME / ".config" / "auto-freecf" / name
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def open_browser(headless: bool, sticky: bool, country: str):
    """Return (playwright, browser, context, page, egress_meta)."""
    if not os.environ.get("XAUTHORITY"):
        xauth = list(Path("/run/user/1000").glob("xauth_*"))
        if xauth:
            os.environ["XAUTHORITY"] = str(xauth[0])
    if not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":0"

    meta: dict = {"provider": "direct", "country": ""}
    pw = None
    if sticky:
        _load_env_files()
        from scripts.github_bd_signup import _tinyfish_session
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        ws = _tinyfish_session(country)
        browser = pw.chromium.connect_over_cdp(ws)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        meta = {"provider": "TinyFish stealth residential (sticky)", "country": country}
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
    else:
        from camoufox.sync_api import Camoufox
        kwargs: dict = {"headless": headless, "humanize": True, "locale": "en-US", "geoip": False}
        proxy = None
        if country:
            import scripts.proxy_pool as pp
            best = pp.best(country=country)
            if not best or not best.get("proxy"):
                raise RuntimeError(f"no working {country} proxy in proxy_pool")
            proxy = best["proxy"]
            kwargs["proxy"] = proxy
            meta = {"provider": best.get("provider", "proxy_pool"), "country": country,
                    "egress_ip": best.get("egress_ip"), "region": best.get("region")}
        browser = Camoufox(**kwargs).__enter__()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                                  extra_http_headers={"Accept-Language": "en-US,en;q=0.9"})
        page = ctx.new_page()
    ctx.set_default_timeout(30000)
    page.set_default_timeout(30000)
    return pw, browser, ctx, page, meta


def record_egress(page, meta: dict) -> dict:
    """Read the exit IP/ASN once and store it with the account (no rotation later)."""
    try:
        page.goto("https://ipinfo.io/json", timeout=25000)
        info = json.loads(page.locator("body").inner_text())
        meta.update({
            "egress_ip": info.get("ip"),
            "isp": (info.get("org") or "").split(" ", 1)[-1] or info.get("org"),
            "asn": (info.get("org") or "").split(" ")[0],
            "city": info.get("city"),
            "region": info.get("region"),
            "hostname": info.get("hostname"),
        })
    except Exception as e:
        meta["egress_error"] = str(e)
    return meta


def shot(page, name: str) -> str:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    p = SHOT_DIR / f"{name}-{int(time.time())}.png"
    try:
        page.screenshot(path=str(p), full_page=False)
    except Exception:
        pass
    return str(p)


def body_text(page) -> str:
    try:
        return page.locator("body").inner_text()[:4000]
    except Exception:
        return ""


# ---------------------------------------------------------------- api asserts

def api_get(page, path: str):
    return page.evaluate("""async (p) => {
        const r = await fetch(p, {credentials: 'include', cache: 'no-store'});
        let body = null;
        try { body = await r.json(); } catch (e) { body = await r.text(); }
        return {status: r.status, body};
    }""", path)


def ccv_status(page) -> dict:
    return api_get(page, "/api/hatch/ccv-status")


def linking_options(page) -> list[str]:
    r = api_get(page, "/api/hatch/age-confirmation/linking-options")
    if r["status"] != 200 or not isinstance(r["body"], dict):
        return []
    return list(r["body"].get("linkable_account_types") or [])


# ---------------------------------------------------------------- stages

def stage_signup(page, ctx, state, *, invite: str, via: str, fb: dict | None) -> None:
    """Create the Meta identity. Only runs when there is no session yet."""
    if via == "facebook" and fb:
        _inject(page, ctx, fb.get("cookies", {}), ".facebook.com")
    url = f"https://muse.ai/invite/{invite}" if invite else "https://muse.ai/"
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    time.sleep(2)
    print(f"    landed: {page.url}")
    shot(page, "signup-01-landing")

    if "muse.ai/access" in page.url or "/app" in page.url:
        print("    already authenticated — skipping signup")
        state["stage"] = "agegate"
        return

    email = state["email"]
    box = M.open_mailbox("tempik", domain=email.split("@")[1], local_part=email.split("@")[0])
    seen = {m.get("id") for m in box.read() if m.get("id")}

    inp = page.wait_for_selector('input[placeholder*="Mobile number or email"], input[type="email"]',
                                 timeout=45000, state="attached")
    inp.fill(email)
    time.sleep(0.5)
    page.locator('button:has-text("Continue")').first.click()
    page.wait_for_selector("text=Enter your code to sign up", timeout=25000)
    print("    OTP screen reached")

    otp = _await_otp(box, seen, 90)
    if not otp:
        raise RuntimeError("no Meta OTP arrived in 90s")
    print(f"    OTP {otp}")

    pins = page.locator('[data-slot="pin-digit"]').all()
    if pins:
        for i, d in enumerate(otp):
            pins[i].click()
            page.keyboard.press(d)
            time.sleep(0.15)
    else:
        page.wait_for_selector('input[autocomplete="one-time-code"], input[name*="code" i]',
                               timeout=15000, state="attached").fill(otp)
    time.sleep(1.0)
    box_cb = page.query_selector('input[type="checkbox"]')
    if box_cb and not box_cb.is_checked():
        box_cb.check()
    for label in ("Confirm", "Continue", "Next"):
        btn = page.locator(f'[role="button"]:has-text("{label}"), button:has-text("{label}")').first
        if btn.count() and btn.is_visible():
            btn.click()
            break
    time.sleep(5)
    shot(page, "signup-02-after-otp")

    if "Finish creating your account" in body_text(page):
        print("    profile screen")
        _fill_profile(page, state)
        shot(page, "signup-03-profile")
        page.locator('button:has-text("Confirm"), [role="button"]:has-text("Confirm")').first.click()
        time.sleep(6)
        shot(page, "signup-04-after-profile")

    time.sleep(3)
    if "A few things to know" in body_text(page):
        print("    onboarding intro screen — clicking Get started")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
        btn = page.locator('button:has-text("Get started")').first
        if btn.count() and btn.is_visible():
            btn.click()
            time.sleep(5)
    state["stage"] = "agegate"


def _await_otp(box, seen: set, seconds: float) -> str | None:
    end = time.time() + seconds
    while time.time() < end:
        for m in box.read():
            if m.get("id") and m.get("id") in seen:
                continue
            subj = str(m.get("subject") or "")
            blob = str(m.get("body") or m.get("text") or "")
            for pat in (r"(?:use code|code)\s*(\d{6})\b", r"\b(\d{6})\b"):
                hit = re.search(pat, subj, re.IGNORECASE)
                if hit:
                    return hit.group(1)
            hit = re.search(r"(?:code|verify|verification|otp)[^\d]{0,30}(\d{6})\b", blob, re.IGNORECASE)
            if hit:
                return hit.group(1)
        time.sleep(2)
    return None


def _fill_profile(page, state: dict) -> None:
    first, _, last = state["name"].partition(" ")
    last = last or "Kanca"
    page.locator('input[aria-label="First name"], input[placeholder*="First name"]').first.fill(first)
    page.locator('input[aria-label="Last name"], input[placeholder*="Last name"]').first.fill(last)
    month, day, year = state.get("dob", ("June", "15", "1998"))
    for label, value in (("Month", month), ("Day", day), ("Year", year)):
        try:
            page.locator(f'button[aria-label="{label}"]').first.click()
            time.sleep(0.4)
            page.evaluate("""(v) => {
                const el = Array.from(document.querySelectorAll('[role="option"],[data-slot="select-item"]'))
                    .find(e => e.innerText.trim() === v);
                if (el) el.click();
            }""", value)
            time.sleep(0.3)
        except Exception as e:
            print(f"    [!] {label}: {e}")


def stage_agegate(page, ctx, state, *, via: str, ig: dict | None, fb: dict | None) -> None:
    """Pass /access/verification by linking an adult social account.

    Meta decides which anchor is still usable per identity: it answers
    `{"url": null}` for a type that is already consumed (an identity created
    through Facebook cannot then be age-attested by that same Facebook), so
    every offered type is tried in preference order rather than assumed.
    """
    page.goto("https://muse.ai/app", wait_until="domcontentloaded", timeout=45000)
    time.sleep(3)
    print(f"    landed: {page.url}")
    shot(page, "age-01-landing")

    st = ccv_status(page)
    print(f"    ccv-status: {st['status']} {st['body']}")
    if st["status"] == 200 and isinstance(st["body"], dict) and st["body"].get("ccv_status") not in ("ccv_required", "blocked"):
        print("    age gate already clear")
        state["stage"] = "onboard"
        return
    if st["status"] == 200 and isinstance(st["body"], dict) and st["body"].get("ccv_status") == "blocked":
        raise RuntimeError("age verification is blocked for this identity — needs a real card")

    options = linking_options(page)
    print(f"    linkable: {options}")
    if not options:
        raise RuntimeError(f"no social account can be linked (status {st['status']}) — the session is dead, re-run signup")

    # Preference: the anchors we hold live cookies for first, then whatever is offered.
    prefer = [t for t in ("facebook", "instagram") if t in options] or options
    if via in prefer:
        prefer.remove(via)
        prefer.insert(0, via)

    for want in prefer:
        if want == "facebook" and fb:
            _inject(page, ctx, fb.get("cookies", {}), ".facebook.com")
        auth = api_get(page, f"/api/hatch/age-confirmation/linking-web-auth?account_type={want}")
        body = auth["body"] if isinstance(auth["body"], dict) else {}
        if auth["status"] != 200 or not body.get("url"):
            print(f"    [{want}] unavailable (url={body.get('url')!r}) — next anchor")
            continue
        print(f"    linking via {want}")
        attempt_id = body["attempt_id"]
        popup = ctx.new_page()
        popup.goto(body["url"], wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        print(f"    popup: {popup.url[:120]}")
        shot(popup, "age-02-popup")

        if want == "instagram" and ig:
            _instagram_login(popup, ig, state)
        elif want == "facebook" and fb:
            _facebook_confirm(popup, state)

        shot(popup, "age-03-after-login")
        print(f"    popup now: {popup.url[:120]}")

        nxt = _poll_link_completion(page, attempt_id)
        try:
            popup.close()
        except Exception:
            pass
        if nxt == "continue":
            break
        if nxt in ("blocked", "use_credit_card"):
            raise RuntimeError(f"age linking refused by Meta: {nxt}")
        print(f"    [{want}] did not resolve ({nxt}) — next anchor")

    st = ccv_status(page)
    print(f"    ccv-status after link: {st['body']}")
    if isinstance(st["body"], dict) and st["body"].get("ccv_status") == "ccv_required":
        raise RuntimeError("age gate still required after linking — inspect age-03 screenshot")
    state["stage"] = "onboard"


def _poll_link_completion(page, attempt_id: str, tries: int = 15) -> str | None:
    """The UI polls this every 2s while the popup is open; same contract here."""
    nxt = None
    for i in range(tries):
        r = page.evaluate("""async (aid) => {
            const r = await fetch('/api/hatch/age-confirmation/linking-web-auth-completion',
                {method:'POST', headers:{'Content-Type':'application/json'},
                 body: JSON.stringify({attempt_id: aid}), credentials:'include', cache:'no-store'});
            let b=null; try { b = await r.json(); } catch(e) { b = await r.text(); }
            return {status:r.status, body:b};
        }""", attempt_id)
        nxt = (r["body"] or {}).get("next_action") if isinstance(r["body"], dict) else None
        print(f"      [{i}] {r['status']} next_action={nxt}")
        if nxt and nxt != "pending":
            return nxt
        time.sleep(2)
    return nxt


def _instagram_login(page, ig: dict, state: dict) -> None:
    """Log in to the Instagram account that anchors the age check."""
    if page.locator('input[name="email"], input[name="username"]').count():
        login_id = ig.get("email") or ig["username"]
        print(f"    IG login as {login_id}")
        page.locator('input[name="email"], input[name="username"]').first.fill(login_id)
        page.locator('input[name="pass"], input[name="password"]').first.fill(ig["password"])
        page.locator('button[type="submit"], div[role="button"]:has-text("Log in")').first.click()
        time.sleep(6)
        shot(page, "age-02b-ig-login")
        txt = body_text(page)
        if "challenge" in page.url or "Confirm" in txt and "identity" in txt:
            raise RuntimeError(f"Instagram checkpoint for {ig['username']} — needs a human")
    for label in ("Continue", "Allow", "Authorize", "Log in", "Next", "Confirm"):
        btn = page.locator(f'div[role="button"]:has-text("{label}"), button:has-text("{label}")').first
        try:
            if btn.count() and btn.is_visible():
                print(f"    clicking {label}")
                btn.click()
                time.sleep(4)
        except Exception:
            pass
    state.setdefault("anchors", {})["instagram"] = ig["email"]


def _facebook_confirm(page, state: dict) -> None:
    time.sleep(3)
    for _ in range(4):
        for label in ("Continue as", "Continue", "Allow", "Confirm", "OK", "Yes"):
            btn = page.locator(f'div[role="button"]:has-text("{label}"), button:has-text("{label}")').first
            try:
                if btn.count() and btn.is_visible():
                    print(f"    clicking {label}")
                    btn.click()
                    time.sleep(4)
                    return
            except Exception:
                pass
        time.sleep(2)


def stage_onboard(page, ctx, state) -> None:
    """TOS, agent name, VM provisioning — all in the real UI."""
    page.goto("https://muse.ai/app", wait_until="domcontentloaded", timeout=45000)
    time.sleep(3)
    print(f"    landed: {page.url}")
    shot(page, "onboard-01")

    for _ in range(6):
        txt = body_text(page)
        clicked = False
        for label in ("Get started", "Continue", "I agree", "Agree", "Accept", "Next", "Start"):
            btn = page.locator(f'[role="button"]:has-text("{label}"), button:has-text("{label}")').first
            try:
                if btn.count() and btn.is_visible():
                    btn.click()
                    clicked = True
                    time.sleep(3)
                    break
            except Exception:
                pass
        if re.search(r"name your|agent'?s name|name your muse", txt, re.IGNORECASE):
            break
        if not clicked:
            time.sleep(3)
    shot(page, "onboard-02")

    name = state.get("agent_name") or "muse"
    for sel in ('input[placeholder*="name" i]', 'textarea[placeholder*="name" i]', "input[type=text]"):
        el = page.locator(sel).first
        try:
            if el.count() and el.is_visible():
                el.fill(name)
                time.sleep(0.5)
                for label in ("Continue", "Create", "Start", "Done", "Next"):
                    btn = page.locator(f'[role="button"]:has-text("{label}"), button:has-text("{label}")').first
                    if btn.count() and btn.is_visible():
                        btn.click()
                        break
                time.sleep(5)
                break
        except Exception:
            continue
    state["agent_name"] = name
    shot(page, "onboard-03")
    print(f"    url after naming: {page.url}")
    state["stage"] = "chat"


def stage_chat(page, state) -> str | None:
    """Send one message and return the agent's reply — the real end-to-end proof."""
    page.goto("https://muse.ai/app", wait_until="domcontentloaded", timeout=45000)
    time.sleep(5)
    shot(page, "chat-01")
    print(f"    url: {page.url}")

    box = None
    for sel in ('textarea', 'div[contenteditable="true"]', 'input[placeholder*="essage" i]',
                'textarea[placeholder*="sk" i]'):
        el = page.locator(sel).last
        try:
            if el.count() and el.is_visible():
                box = el
                break
        except Exception:
            continue
    if box is None:
        print("    no composer found")
        return None

    prompt = "Reply with exactly: MUSE_E2E_OK"
    box.click()
    box.fill(prompt) if box.evaluate("e => e.tagName") != "DIV" else page.keyboard.type(prompt)
    page.keyboard.press("Enter")
    print("    sent; waiting for reply…")
    end = time.time() + 120
    while time.time() < end:
        time.sleep(4)
        txt = body_text(page)
        if "MUSE_E2E_OK" in txt.split(prompt)[-1]:
            shot(page, "chat-02-reply")
            print("    reply captured")
            return txt[-800:]
    shot(page, "chat-02-timeout")
    return None


# ---------------------------------------------------------------- cookies

def _add_cookies(ctx, cookies: list[dict], url: str) -> None:
    """Playwright wants a URL per cookie for CDP contexts; domain+path is rejected."""
    payload = [{"name": c["name"], "value": c["value"], "url": url} for c in cookies if c.get("name")]
    for i in range(0, len(payload), 50):
        ctx.add_cookies(payload[i:i + 50])


def _inject(page, ctx, cookies: dict, domain: str) -> None:
    if not cookies:
        return
    url = f"https://{domain.lstrip('.')}/"
    try:
        _add_cookies(ctx, [{"name": k, "value": v} for k, v in cookies.items()], url)
    except Exception as e:
        print(f"    [!] cookie inject {domain}: {e}")


_JAR_FIELDS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")


def harvest(ctx, state: dict) -> dict:
    """Persist every cookie the run produced — verbatim, attributes included.

    Storing only name/value loses `__Host-` scope and expiry, and a jar rebuilt
    from names alone logs in as nobody.
    """
    jar = [{k: c[k] for k in _JAR_FIELDS if k in c} for c in ctx.cookies()]
    state["cookie_jar"] = jar
    buckets: dict[str, dict] = {}
    for c in jar:
        dom = c.get("domain") or ""
        key = ("facebook.com" if "facebook.com" in dom else
               "instagram.com" if "instagram.com" in dom else
               "muse.ai" if "muse.ai" in dom else None)
        if key:
            buckets.setdefault(key, {})[c["name"]] = c["value"]
    state["cookies_by_domain"] = buckets
    state["hatch_sess"] = buckets.get("muse.ai", {}).get("hatch_sess")
    state["cookie_header"] = "; ".join(f"{k}={v}" for k, v in buckets.get("muse.ai", {}).items())
    return buckets


def persist_ledger(state: dict, egress: dict) -> None:
    entry = {
        "email": state["email"],
        "name": state["name"],
        "hatch_sess": state.get("hatch_sess"),
        "cookie_header": state.get("cookie_header"),
        "cookies": state.get("cookies_by_domain", {}).get("muse.ai", {}),
        "fb_cookies": state.get("cookies_by_domain", {}).get("facebook.com", {}),
        "cookie_jar": state.get("cookie_jar") or [],
        "created_at": state.get("created_at") or _now(),
        "stage": state["stage"],
        "invite_code": state.get("invite"),
        "auth_via": state.get("via"),
        "agent_name": state.get("agent_name"),
        "chat_proof": state.get("chat_proof"),
        **{k: v for k, v in egress.items() if k in
           ("provider", "country", "egress_ip", "isp", "asn", "city", "region", "hostname")},
    }
    import fcntl
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = LEDGER_PATH.with_suffix(".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        ledger = _ledger(LEDGER_PATH)
        ledger = [a for a in ledger if a.get("email") != entry["email"]]
        ledger.append(entry)
        tmp = LEDGER_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(ledger, indent=2))
        tmp.replace(LEDGER_PATH)
        fcntl.flock(lf, fcntl.LOCK_UN)


# ---------------------------------------------------------------- driver

def run(args) -> int:
    if args.resume:
        state = load_state(args.resume)
        if not state.get("email"):
            sys.exit(f"state {args.resume} has no email")
        print(f"[resume] {state['email']} at stage {state['stage']}")
    else:
        fb = pick_facebook(args.fb_email) if args.via == "facebook" else None
        email = fb["email"] if fb else (
            M.open_mailbox("tempik", domain="kancalabs.my.id", local_part=args.local_part or "").address)
        state = {
            "key": email.split("@")[0],
            "email": email,
            "name": (fb.get("name") if fb else persona.full_name()) or persona.full_name(),
            "dob": ("June", "15", "1998"),
            "via": args.via,
            "invite": args.invite,
            "stage": "signup",
            "created_at": _now(),
        }
        save_state(state)
        print(f"[new] {email} ({state['name']})")

    # Both anchors are resolved up front: an identity created by email OTP is
    # still age-attested by the Facebook cookies we hold, and vice versa.
    fb = _try(pick_facebook, state["email"] if state.get("via") == "facebook" else "")
    ig = _try(pick_instagram, args.ig_email)

    pw, browser, ctx, page, egress = open_browser(not args.headed, args.sticky, args.country)
    try:
        if state.get("cookies_by_domain"):
            for dom, jar in state["cookies_by_domain"].items():
                _inject(page, ctx, jar, dom)
            print(f"[cookies] restored {sum(len(v) for v in state['cookies_by_domain'].values())} "
                  f"across {list(state['cookies_by_domain'])}")
        if args.sticky or args.country:
            egress = record_egress(page, egress)
            print(f"[egress] {egress.get('provider')} {egress.get('egress_ip')} "
                  f"{egress.get('asn')} {egress.get('city')},{egress.get('region')}")
            state["egress"] = egress
            save_state(state)

        order = list(STAGES)
        start, end = order.index(state["stage"]), order.index(args.until)
        for name in order[start:end + 1]:
            print(f"[stage] {name}")
            if name == "signup":
                stage_signup(page, ctx, state, invite=state.get("invite") or "",
                             via=state.get("via", ""), fb=fb)
            elif name == "agegate":
                stage_agegate(page, ctx, state, via=args.via, ig=ig, fb=fb)
            elif name == "onboard":
                stage_onboard(page, ctx, state)
            elif name == "chat":
                proof = stage_chat(page, state)
                state["chat_proof"] = proof
                harvest(ctx, state)
                save_state(state)
                persist_ledger(state, egress)
                if not proof:
                    print("[!] chat proof NOT captured")
                    return 1
                print("[✓] MUSE_E2E_OK")
            harvest(ctx, state)
            save_state(state)

        harvest(ctx, state)
        save_state(state)
        persist_ledger(state, egress)
        print(f"[done] stage={state['stage']} hatch_sess={'yes' if state.get('hatch_sess') else 'no'}")
        return 0
    finally:
        try:
            if args.sticky:
                browser.close()
            else:
                browser.__exit__(None, None, None)
        except Exception:
            pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        print(f"    [skip] {fn.__name__}: {e}")
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Meta Muse onboarding (resumable)")
    ap.add_argument("--resume", default="", help="state key (email local part) to continue")
    ap.add_argument("--until", default="chat", choices=STAGES, help="stop after this stage")
    ap.add_argument("--via", default="facebook", choices=("facebook", "instagram", "email", "auto"),
                    help="identity for Muse signup and the age anchor")
    ap.add_argument("--fb-email", default="", help="specific Facebook account")
    ap.add_argument("--ig-email", default="", help="specific Instagram account")
    ap.add_argument("--invite", default="H9BZ8A", help="Muse invite code")
    ap.add_argument("--local-part", default="", help="mailbox local part for --via email")
    ap.add_argument("--country", default="US", help="residential proxy country (default US)")
    ap.add_argument("--sticky", action="store_true", help="TinyFish sticky residential session")
    ap.add_argument("--headed", action="store_true", help="visible browser (Camoufox only)")
    ap.add_argument("-n", "--count", type=int, default=1)
    args = ap.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
