#!/usr/bin/env python3
"""
scripts/residential_proxy_signup.py — Webshare-trick for residential-proxy vendors.

RapidProxy and SwiftProxy hand out free-trial residential proxies behind an
email-verification-code signup (with a Cloudflare Turnstile on login). This script
automates: mint a relay mailbox -> fill the signup form -> click "Send" -> read the
code from the relay -> submit -> reach the dashboard -> harvest the proxy list into
the local pools.

Both vendors share a near-identical Vue form (same placeholders, "Send"/"Sign up"
buttons), so the field discovery is shared and the difference is only the host.

Usage (must run under the camoufox venv — it drives a browser):
  camoufox-venv/bin/python scripts/residential_proxy_signup.py rapidproxy --headless
  camoufox-venv/bin/python scripts/residential_proxy_signup.py swiftproxy --inspect-only

skipped: no login-form Turnstile solver here — login is a separate flow. add when
a *free account creation* starts showing Turnstile (today login does, signup doesn't).
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SITES = {
    "rapidproxy": {
        "register": "https://www.rapidproxy.io/user/register",
        "login": "https://www.rapidproxy.io/user/login",
        "dashboard": "https://www.rapidproxy.io/user/ucenter/index",
        "default_out": Path.home() / "petani-proxy" / "output" / "rapidproxy_residential.txt",
    },
    "swiftproxy": {
        "register": "https://www.swiftproxy.net/user/register",
        "login": "https://www.swiftproxy.net/user/login",
        "dashboard": "https://www.swiftproxy.net/user/ucenter/index",
        "default_out": Path.home() / "petani-proxy" / "output" / "swiftproxy_residential.txt",
    },
}

CODE_RE = re.compile(r"\b(\d{4,8})\b")
PROXY_QUAD_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})")


# ────────────────────────────────────────────────────────────── pure helpers
def generate_email(domain: str, rand_suffix: str | None = None) -> str:
    suffix = rand_suffix or "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"rp{suffix}@{domain}"


def generate_password() -> str:
    special = random.choice("!@#$%^&*")
    mid = "".join(random.choices(string.ascii_letters + string.digits, k=10))
    return f"Rp{special}{mid}9!"


def find_code(mails: list[dict], *, vendor: str | None = None) -> str | None:
    """First 4-8 digit code found in subject/body; prefer vendor-branded mail."""
    if not mails:
        return None
    branded, other = [], []
    for m in mails:
        text = " ".join(str(m.get(k) or "") for k in ("subject", "text", "body", "html", "source"))
        (branded if vendor and vendor.lower() in text.lower() else other).append(text)
    for text in branded + other:
        hit = CODE_RE.search(text)
        if hit:
            return hit.group(1)
    return None


def parse_proxies(text: str) -> list[str]:
    """Extract http://user:pass@ip:port entries from a proxy-list payload.

    Accepts the vendor's `ip:port:user:pass` CSV and `ip:port` lines. Username/
    password from the account are attached by the caller when the list is bare.
    """
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = PROXY_QUAD_RE.search(line)
        if not m:
            continue
        ip, port = m.group(1), m.group(2)
        # ip:port:user:pass
        tail = line[m.end():].lstrip(":,")
        parts = [p for p in re.split(r"[:,\s]+", tail) if p]
        if len(parts) >= 2:
            out.append(f"http://{parts[0]}:{parts[1]}@{ip}:{port}")
        else:
            out.append(f"{ip}:{port}")
    return out


LEDGER = Path.home() / ".config" / "auto-freecf" / "residential_proxy_accounts.jsonl"


def log_account(vendor: str, email: str, password: str, status: str) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps({"ts": int(time.time()), "vendor": vendor, "email": email,
                            "password": password, "status": status}) + "\n")


def append_to_file(lines: list[str], path: Path) -> int:
    if not lines:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: set[str] = set()
    if path.exists():
        existing = {l.strip() for l in path.read_text().splitlines() if l.strip()}
    added = 0
    with path.open("a") as f:
        for l in lines:
            if l not in existing:
                f.write(l + "\n")
                existing.add(l)
                added += 1
    return added


# ────────────────────────────────────────────────────────────── form filling
async def _dismiss_overlays(page) -> None:
    """Click consent buttons and hide chat/consent overlays that intercept clicks."""
    try:
        await page.evaluate("""() => {
            const labels = ['accept all', 'accept', 'i agree', 'agree', 'allow all', 'got it'];
            for (const b of [...document.querySelectorAll('button, a, [role=button]')]) {
                const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                if (labels.includes(t)) { try { b.click(); } catch (e) {} }
            }
            for (const sel of ['#usercentrics-cmp-ui', '#s-chat-plugin', '[id*=chat]', '[class*=cookie]']) {
                document.querySelectorAll(sel).forEach(e => e.style.display = 'none');
            }
        }""")
    except Exception:
        pass
    await asyncio.sleep(0.5)


async def _fill_by_placeholder(page, needle: str, value: str) -> bool:
    """Fill the first input whose placeholder contains `needle` (case-insensitive)."""
    loc = page.locator(f"input[placeholder*='{needle}' i]").first
    if await loc.count() == 0:
        return False
    if not await loc.is_editable():
        return False
    try:
        await loc.click(timeout=5000)
        await loc.fill(value)
    except Exception:
        # Overlay intercepts the click: set the value + fire events via JS instead.
        await loc.evaluate("""(el, v) => {
            el.focus(); el.value = v;
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
        }""", value)
    return True


async def _click_text(page, *labels: str) -> str | None:
    for label in labels:
        for loc in (page.get_by_role("button", name=label, exact=True),
                    page.locator(f"button:has-text('{label}')"),
                    page.locator(f"a:has-text('{label}')")):
            try:
                if await loc.count() == 0:
                    continue
            except Exception:
                continue
            try:
                await loc.first.click(timeout=6000, no_wait_after=True)
                return label
            except Exception:
                # Overlay intercepts, or the click navigated before resolving:
                # try a JS click on the first match without blocking.
                try:
                    hit = await loc.first.evaluate(
                        "el => { el.scrollIntoView(); el.click(); return true; }",
                        timeout=3000)
                    if hit:
                        return label
                except Exception:
                    return label  # a navigation already happened — treat as clicked
    return None


async def signup_once(site: dict, *, domain: str, relay, headless: bool,
                      inspect_only: bool, timeout: float, proxy: str | None) -> dict:
    from camoufox.async_api import AsyncCamoufox

    email = relay.email  # the address we can actually poll for the code
    password = relay.password
    result: dict = {"email": email, "password": password, "proxies": [], "status": "start"}

    print(f"\n  ▶ Vendor   : {site['register']}")
    print(f"  • Email    : {email}")
    print(f"  • Password : {password}")

    kwargs: dict = {"headless": headless, "os": "windows", "humanize": True}
    if proxy and proxy.lower() not in ("none", "direct"):
        kwargs["proxy"] = {"server": proxy}

    async with AsyncCamoufox(**kwargs) as browser:
        page = await browser.new_page()
        if os.environ.get("RPSIGN_DEBUG"):
            net: list[dict] = []
            page.on("response", lambda r: net.append({"s": r.status, "u": r.url}))

            async def _cap(resp):
                if any(k in resp.url for k in ("register", "send_email", "login")):
                    try:
                        net.append({"BODY": resp.url, "text": (await resp.text())[:300]})
                    except Exception:
                        pass
            page.on("response", lambda r: asyncio.create_task(_cap(r)))
            page.on("console", lambda m: net.append({"console": m.type, "text": m.text[:200]}))
            result["_net"] = net
        await page.goto(site["register"], wait_until="domcontentloaded", timeout=45000)

        # Cloudflare may show a "Just a moment" interstitial first. In a real
        # (non-headless) browser it usually clears on its own; wait it out.
        waited = 0.0
        while waited < 45:
            await asyncio.sleep(2.0)
            waited += 2.0
            has_email = await page.locator("input[placeholder*='email' i]").count() > 0
            if has_email:
                break
            state = await page.evaluate(
                "() => ({cf: /just a moment|verifying|attention required/i"
                ".test(document.title + ' ' + ((document.body && document.body.innerText) || ''))})")
            if state.get("cf"):
                print(f"  … Cloudflare challenge present (+{waited:.0f}s), waiting…")
        await asyncio.sleep(random.uniform(1.5, 3.0))
        await _dismiss_overlays(page)

        btn_labels = await page.evaluate(
            "() => [...document.querySelectorAll('button, input[type=submit]')]"
            ".map(b => (b.innerText||b.value||'').trim()).filter(Boolean)")
        has_email = await _fill_by_placeholder(page, "email", email)
        if not has_email:
            result["status"] = "no_form"
            print(f"  ✗ No email field found. Buttons: {btn_labels} | title={await page.title()}")
            if os.environ.get("RPSIGN_DEBUG"):
                await _dump_debug(page, result)
            return result

        await _fill_by_placeholder(page, "verification code", "")  # focus/prime
        await _fill_by_placeholder(page, "password", password)
        await _fill_by_placeholder(page, "other contacts", "none")  # swiftproxy extra (optional)
        await _fill_by_placeholder(page, "invitation", "")          # optional

        await page.evaluate("""() => {
            const c = document.querySelector("input[type='checkbox']");
            if (c && !c.checked) { c.click(); c.checked = true;
                c.dispatchEvent(new Event('change', {bubbles:true})); }
        }""")

        if inspect_only:
            result["status"] = "inspect_only"
            print(f"  ✓ Inspect-only: form reached ({btn_labels}). Stopping before Send.")
            return result

        sent = await _click_text(page, "Send")
        if not sent:
            result["status"] = "no_send_button"
            print(f"  ✗ 'Send' button not found. Buttons: {btn_labels}")
            return result
        print("  → Clicked 'Send'; waiting for the verification code email...")

        code = None
        end = time.time() + timeout
        while time.time() < end and not code:
            try:
                code = find_code(relay.poll(), vendor=site["register"].split("//")[1].split(".")[1])
            except Exception as e:  # relay hiccup: keep polling
                print(f"    (poll error: {e})")
            if not code:
                await asyncio.sleep(4)
        if not code:
            result["status"] = "no_code"
            print("  ✗ Timed out waiting for the code.")
            return result
        print(f"  ✓ Code received: {code}")

        if not await _fill_by_placeholder(page, "verification code", code):
            result["status"] = "no_code_field"
            print("  ✗ Verification-code field vanished before we could fill it.")
            return result
        await asyncio.sleep(random.uniform(0.5, 1.0))

        clicked = await _click_text(page, "Sign Up", "Sign up")
        if not clicked:
            result["status"] = "no_submit_button"
            print("  ✗ Submit button not found.")
            return result
        print(f"  → Clicked '{clicked}'; awaiting dashboard...")
        await _wait_leave(page, ("register",), timeout=30)

        # A created account often bounces to /user/login: sign in with the same creds.
        if _at_login(page.url):
            print("  → Landed on login; signing in with the new account...")
            if not await _login(page, site, email, password, timeout):
                result["status"] = "login_failed"
                print(f"  ✗ Could not log in (at {page.url}).")
                return result

        alert = await _page_alert(page)
        if _alert_text(alert) and _at_login(page.url):
            result["status"] = "signup_alert"
            result["error"] = alert
            print(f"  ✗ Signup alert: {alert}")
            return result

        if _at_login(page.url) or "register" in (page.url or ""):
            result["status"] = "no_dashboard"
            print(f"  ✗ Did not reach dashboard (still at {page.url}).")
            if os.environ.get("RPSIGN_DEBUG"):
                await _dump_debug(page, result)
            return result

        result["status"] = "dashboard"
        print(f"  ✓ Dashboard reached: {page.url}")
        if os.environ.get("RPSIGN_DEBUG"):
            nav = await page.evaluate("""() => ({
                links: [...document.querySelectorAll('a')].map(a => (a.innerText||'').trim()+' -> '+a.href)
                          .filter(x => x.length > 4).slice(0,60),
                menu: [...document.querySelectorAll('[class*=menu] *, [class*=nav] *, [class*=aside] *')]
                          .map(e => (e.innerText||'').trim()).filter(Boolean).slice(0,40),
                body: document.body.innerText.slice(0, 1200),
            })""")
            result["nav"] = nav
            print("  ── DASHBOARD NAV ──")
            print("  links:", nav["links"][:40])
            print("  body:", nav["body"][:800].replace("\n", " | "))
        proxies, raw = await harvest(page)
        result["proxies"] = proxies
        result["raw"] = raw
        print(f"  ✓ Harvested {len(proxies)} proxy line(s).")
        return result


def _at_login(url: str) -> bool:
    return any(s in (url or "").lower() for s in ("/login", "/signin", "/sign-in"))


async def _wait_leave(page, bad: tuple[str, ...], *, timeout: float) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if not any(b in (page.url or "").lower() for b in bad):
            return
        await asyncio.sleep(1.5)


async def _login(page, site: dict, email: str, password: str, timeout: float) -> bool:
    """Sign in on the vendor login form (email + password, Turnstile auto-passes)."""
    if not _at_login(page.url):
        await page.goto(site["login"], wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(random.uniform(2.0, 3.0))
    if not await _fill_by_placeholder(page, "email", email):
        return False
    await _fill_by_placeholder(page, "password", password)
    await page.evaluate("""() => {
        const c = document.querySelector("input[type='checkbox']");
        if (c && !c.checked) { c.click(); c.checked = true;
            c.dispatchEvent(new Event('change', {bubbles:true})); }
    }""")
    if not await _click_text(page, "Log in", "Login", "Sign in"):
        return False
    await _wait_leave(page, ("login", "signin", "sign-in"), timeout=timeout)
    return not _at_login(page.url)


async def _dump_debug(page, result: dict) -> None:
    try:
        info = await page.evaluate("""() => ({
            url: location.href,
            alerts: [...document.querySelectorAll('[role=alert], .el-message, .ant-message, .toast, .error, .el-form-item__error')]
                       .map(e => (e.innerText||'').trim()).filter(Boolean),
            buttons: [...document.querySelectorAll('button, input[type=submit], a.btn')]
                       .map(b => (b.innerText||b.value||'').trim()).filter(Boolean).slice(0,20),
            inputs: [...document.querySelectorAll('input')].map(i => ({ph:i.placeholder, type:i.type, dis:i.disabled})),
            body: document.body.innerText.slice(0, 1500),
        })""")
        result["debug"] = info
        print("  ── DEBUG ──")
        print("  url:", info["url"])
        print("  alerts:", info["alerts"])
        print("  buttons:", info["buttons"])
        print("  inputs:", info["inputs"])
        print("  body:", info["body"][:600].replace("\n", " | "))
        for ev in result.get("_net", [])[-15:]:
            print("  net:", ev)
    except Exception as e:  # noqa: BLE001
        print(f"  (debug dump failed: {e})")


async def _page_alert(page) -> str:
    return await page.evaluate("""() => {
        const a = document.querySelector('.el-message, [role=alert], .ant-message, .toast, .error');
        return a ? (a.innerText||'').trim() : '';
    }""")


def _alert_text(text: str) -> bool:
    t = (text or "").lower()
    return bool(t) and any(w in t for w in ("already", "exist", "invalid", "failed", "error", "incorrect"))


async def harvest(page) -> tuple[list[str], str]:
    """Pull the proxy list from the dashboard. Prefer an API/download URL, else DOM."""
    dumped = await page.evaluate("""() => {
        const out = [];
        const grab = (t) => (t||'').trim();
        document.querySelectorAll('textarea, pre, code').forEach(e => out.push(grab(e.value||e.innerText)));
        document.querySelectorAll('input, a').forEach(e => {
            const v = e.value || e.href || '';
            if (/\\d{1,3}(\\.\\d{1,3}){3}:\\d+/.test(v)) out.push(v);
        });
        document.querySelectorAll('tr').forEach(r => {
            const cells = [...r.querySelectorAll('td,th')].map(c => grab(c.innerText));
            if (cells.length) out.push(cells.join(':'));
        });
        return out.join('\\n');
    }""")
    proxies = parse_proxies(dumped)
    # If the list is bare ip:port, attach the account credentials if the page shows them.
    if proxies and all("@" not in p for p in proxies):
        creds = await page.evaluate("""() => {
            const t = document.body.innerText;
            const u = t.match(/[Uu]sername[:\\s]+([A-Za-z0-9._-]{3,})/);
            const p = t.match(/[Pp]assword[:\\s]+([A-Za-z0-9._-]{3,})/);
            return u && p ? `${u[1]}:${p[1]}` : '';
        }""")
        if creds:
            proxies = [f"http://{creds}@{p}" if ":" in p else p for p in proxies]
    return proxies, dumped


class Relay:
    """Thin wrapper over github_farm's relay client (mint + poll)."""

    def __init__(self, domain: str):
        import github_farm as g  # reuse the proven relay client
        self._g = g
        self.mailbox = g.create_relay_mailbox(domain=domain)
        self.password = generate_password()

    @property
    def email(self) -> str:
        return self.mailbox["email"]

    def poll(self) -> list[dict]:
        return self._g.poll_relay_inbox(self.mailbox["jwt"])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Sign up for RapidProxy/SwiftProxy free "
                                             "trial and harvest residential proxies")
    ap.add_argument("vendor", choices=sorted(SITES), help="which vendor to farm")
    ap.add_argument("--domain", default=None, help="relay email domain (default: first configured)")
    ap.add_argument("--count", "-n", type=int, default=1, help="accounts to create (default 1)")
    ap.add_argument("--headless", action="store_true", help="hide the browser window")
    ap.add_argument("--out", default=None, help="output file (default: vendor pool)")
    ap.add_argument("--timeout", type=float, default=180.0, help="per-step timeout seconds")
    ap.add_argument("--proxy", default=None, help="egress proxy URL for the signup browser")
    ap.add_argument("--inspect-only", action="store_true",
                    help="fill the form up to 'Send' then stop (nothing created)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    site = SITES[args.vendor]
    out = Path(args.out) if args.out else site["default_out"]

    import github_farm as g
    domain = args.domain or g.get_relay_config()["domains"][0]

    relay = Relay(domain)
    print(f"  • Mailbox  : {relay.email}")

    total = 0
    for i in range(1, args.count + 1):
        print(f"\n  ── Account [{i}/{args.count}] ──")
        res = asyncio.run(signup_once(site, domain=domain, relay=relay,
                                      headless=args.headless,
                                      inspect_only=args.inspect_only,
                                      timeout=args.timeout, proxy=args.proxy))
        if not args.inspect_only:
            log_account(args.vendor, res["email"], res["password"], res["status"])
        if args.inspect_only:
            return 0 if res["status"] == "inspect_only" else 2
        if res["proxies"]:
            added = append_to_file(res["proxies"], out)
            print(f"  ✓ {len(res['proxies'])} proxies ({added} new) -> {out}")
            total += added
        else:
            print(f"  ⚠ status={res['status']} (no proxies)")
        if i < args.count:
            await_s = random.uniform(20.0, 45.0)
            print(f"  (pausing {await_s:.0f}s between accounts)")
            time.sleep(await_s)
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())