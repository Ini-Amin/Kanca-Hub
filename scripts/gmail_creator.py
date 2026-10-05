#!/usr/bin/env python3
"""
gmail_creator.py - Linux-native Gmail signup assistant (nodriver).

Clean-room reconstruction of the Windows-only ``auto_gmail_creator.exe`` from
/home/amen/gmail-account-creator.  That project ships NO Python source, only an
.exe plus README/config/data, so this file was written from the README and the
config files, NOT ported from code.  Selenium/chromedriver are not used;
browser automation is done with nodriver (CDP) against the system Chrome.

STATUS: BEST-EFFORT / SEMI-AUTOMATED.  It has NOT been verified end to end
against live Google; Google changes the signup DOM often.  Do not expect
unattended account creation to work.

What is automated
-----------------
  * Launch a fresh, throw-away Chrome profile per account (optional proxy).
  * Open https://accounts.google.com/signup (forced to English UI).
  * Step 1  name        : first/last name (from data/names.txt).
  * Step 2  basic info  : birthday + gender (config/config.py, or CLI).
  * Step 3  username    : generated from the name + digits; retries with a new
                          candidate if Google reports it as taken; picks
                          "Create your own Gmail address" if suggestions appear.
  * Step 4  password    : config/password.txt, --password, or random per account.
  * Post-verification screens (recovery email "Skip", review info, "I agree").
  * Results are appended to a JSON list [{email,password,created_at,status}]
    (file mode 0600, atomic write, saved after every account).

What will need a human (and WILL in practice)
---------------------------------------------
  * Phone / SMS verification - Google asks for it on the vast majority of
    signups, especially from datacenter / VPN / repeated IPs.  This script does
    NOT bypass it.  In headed mode it pauses and waits (--verify-timeout) for you
    to enter the number and code in the browser window, then carries on.
  * reCAPTCHA / "Verify it's you" / "Couldn't create your account" blocks -
    detected and reported, never solved.  Account is recorded as ``failed``.
  * Headless mode (--headless): nothing can be done by hand, so any account that
    reaches verification is recorded as ``pending_verification``.  Headless
    Chrome is also more likely to be blocked.  Use headed mode (needs a display).
  * Proxies with credentials: Chrome ignores user:pass in --proxy-server; use an
    unauthenticated http/socks5 endpoint (e.g. a local relay).

Phone-verification hook (optional, stub only)
---------------------------------------------
config/5sim_config.txt is read if present (the shipped placeholder value is
treated as "not configured").  ``PhoneProvider`` / ``FiveSimStub`` mark the
integration point, but ``FiveSimStub.get_number`` deliberately raises
NotImplementedError: no SMS-purchase logic is implemented here, and nothing
requires a key.  Phone verification stays manual.

Status values written to the JSON file
--------------------------------------
  created               - landed on a signed-in Google page (myaccount / mail).
  pending_verification  - password set, but verification not completed in time
                          (account most likely does NOT exist yet).
  failed                - Google blocked / error after username was submitted.
Attempts that never got past the username step are only logged, not saved.

Usage
-----
  venv=/home/amen/.local/share/auto-freecf/venv/bin/python
  $venv scripts/gmail_creator.py --help
  $venv scripts/gmail_creator.py --check              # dependency report
  $venv scripts/gmail_creator.py --count 2 --dry-run  # no browser, no network
  $venv scripts/gmail_creator.py --count 1            # headed, you do the phone step
  $venv scripts/gmail_creator.py --count 3 --proxy socks5://127.0.0.1:9050 \\
        --out ~/gmail_accounts.json

Use only for accounts you are entitled to create; automated signup is likely
against Google's Terms of Service and may get accounts / IPs restricted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import secrets
import shutil
import string
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

HOME = Path.home()
DEFAULT_SRC = Path(os.environ.get("GMAIL_CREATOR_SRC", HOME / "gmail-account-creator"))
DEFAULT_OUT = HOME / ".local" / "share" / "auto-freecf" / "gmail_accounts.json"
SIGNUP_URL = "https://accounts.google.com/signup/v2/webcreateaccount?flowName=GlifWebSignIn&flowEntry=SignUp&hl=en"
FIVESIM_PLACEHOLDER = "YOUR_5SIM_API_KEY_HERE"
BROWSER_CANDIDATES = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
)
# README: "1=Male, 2=Female, 3=Other"  ->  Google <select id=gender> values
GENDER_TO_GOOGLE = {"1": "2", "2": "1", "3": "3"}
GENDER_LABEL = {"1": "male", "2": "female", "3": "other"}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------- data/config

def read_lines(path: Path) -> list[str]:
    """Non-empty, stripped lines; tolerant of CRLF (the source files are CRLF)."""
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [ln.strip() for ln in text.replace("\r", "\n").split("\n") if ln.strip()]


def parse_config_py(path: Path) -> dict[str, str]:
    """Read simple NAME = "value" assignments from config.py WITHOUT executing it."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r'^\s*([A-Z_0-9]+)\s*=\s*(["\'])(.*?)\2', line)
        if m:
            out[m.group(1)] = m.group(3)
    return out


def load_5sim_key(src: Path) -> Optional[str]:
    lines = read_lines(src / "config" / "5sim_config.txt")
    key = lines[0] if lines else ""
    if not key or key == FIVESIM_PLACEHOLDER or key.upper().startswith("YOUR_"):
        return None
    return key


class PhoneProvider:
    """Interface for an optional automatic phone-verification provider."""

    def get_number(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def wait_code(self, timeout: int = 120) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class FiveSimStub(PhoneProvider):
    """STUB for the optional 5sim hook.  Intentionally not implemented.

    The original tool used 5sim (config/5sim_config.txt, FIVESIM_COUNTRY,
    FIVESIM_OPERATOR) to rent SMS numbers.  Wire a provider in here if you
    decide to; the signup flow currently never calls it - the phone step is
    handled manually (see ``handle_phone_step``).
    """

    def __init__(self, api_key: str, country: str = "usa", operator: str = "any"):
        self.api_key, self.country, self.operator = api_key, country, operator

    def get_number(self) -> str:
        raise NotImplementedError("5sim integration is a stub; verify the phone manually")

    def wait_code(self, timeout: int = 120) -> str:
        raise NotImplementedError("5sim integration is a stub; verify the phone manually")


@dataclass
class Settings:
    src: Path
    names: list[str]
    user_agents: list[str]
    file_password: Optional[str]
    birthday: tuple[int, int, int]  # month, day, year
    gender: str                     # "1" | "2" | "3"
    fivesim_key: Optional[str]


def parse_birthday(s: str) -> tuple[int, int, int]:
    parts = s.replace("/", " ").replace("-", " ").split()
    if len(parts) != 3:
        raise ValueError(f"birthday must be 'month day year', got {s!r}")
    m, d, y = (int(p) for p in parts)
    if not (1 <= m <= 12 and 1 <= d <= 31 and 1900 <= y <= 2010):
        raise ValueError(f"birthday out of range (need 1900-2010, 18+): {s!r}")
    return m, d, y


def load_settings(args: argparse.Namespace) -> Settings:
    src = Path(args.src).expanduser()
    cfg = parse_config_py(src / "config" / "config.py")
    names = read_lines(Path(args.names).expanduser() if args.names else src / "data" / "names.txt")
    if not names:
        names = ["Alex Morgan", "Sam Taylor", "Jamie Carter", "Chris Parker", "Robin Hayes"]
    pw_lines = read_lines(src / "config" / "password.txt")
    gender = str(args.gender or cfg.get("YOUR_GENDER", "1"))
    if gender not in GENDER_TO_GOOGLE:
        raise ValueError(f"gender must be 1, 2 or 3, got {gender!r}")
    return Settings(
        src=src,
        names=names,
        user_agents=read_lines(src / "config" / "user_agents.txt"),
        file_password=pw_lines[0] if pw_lines else None,
        birthday=parse_birthday(args.birthday or cfg.get("YOUR_BIRTHDAY", "2 4 1990")),
        gender=gender,
        fivesim_key=load_5sim_key(src),
    )


# ------------------------------------------------------------- identity gen

def ascii_slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def split_name(full: str) -> tuple[str, str]:
    parts = full.split()
    return (parts[0], " ".join(parts[1:])) if len(parts) > 1 else (full, "")


def gen_username(first: str, last: str) -> str:
    base = (ascii_slug(first) + ascii_slug(last)) or "user"
    base = base[:20]
    u = f"{base}{random.randint(100, 99999)}"
    if len(u) < 6:
        u += "".join(random.choices(string.digits, k=6 - len(u)))
    return u[:30]


def gen_password(length: int = 16) -> str:
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%^&*"]
    chars = [secrets.choice(p) for p in pools]
    chars += [secrets.choice("".join(pools)) for _ in range(length - len(chars))]
    random.SystemRandom().shuffle(chars)
    return "".join(chars)


def pick_password(st: Settings, args: argparse.Namespace) -> str:
    if args.password:
        pw = args.password
    elif st.file_password and not args.random_password:
        pw = st.file_password
    else:
        pw = gen_password()
    if len(pw) < 8:
        raise ValueError("password must be at least 8 characters")
    return pw


# ------------------------------------------------------------ results store

def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_result(out: Path, record: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    data: list = []
    if out.exists():
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("not a list")
        except Exception:
            backup = out.with_suffix(f".corrupt-{int(time.time())}")
            out.rename(backup)
            log(f"existing {out.name} unreadable; moved to {backup.name}")
            data = []
    data.append(record)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, out)


# ----------------------------------------------------------- environment

def find_browser(explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit if Path(explicit).exists() else shutil.which(explicit)
    for name in BROWSER_CANDIDATES:
        p = shutil.which(name)
        if p:
            return p
    return None


def validate_proxy(proxy: Optional[str]) -> Optional[str]:
    if not proxy:
        return None
    u = urlparse(proxy)
    if u.scheme not in ("http", "https", "socks4", "socks5") or not u.hostname or not u.port:
        raise ValueError("proxy must look like scheme://host:port (http, https, socks4, socks5)")
    if u.username or u.password:
        raise ValueError(
            "Chrome ignores credentials in --proxy-server. Use an unauthenticated "
            "endpoint (e.g. a local relay that adds the upstream auth)."
        )
    return f"{u.scheme}://{u.hostname}:{u.port}"


def run_check(args: argparse.Namespace) -> int:
    """Report dependencies.  Exit 1 if a REQUIRED one is missing."""
    ok = True

    def row(state: str, what: str, detail: str = "") -> None:
        print(f"  [{state:^7}] {what}" + (f" - {detail}" if detail else ""))

    print("gmail_creator dependency check")
    v = sys.version_info
    if v >= (3, 10):
        row("OK", "python", f"{v.major}.{v.minor}.{v.micro} ({sys.executable})")
    else:
        row("MISSING", "python>=3.10", f"found {v.major}.{v.minor}")
        ok = False
    try:
        import nodriver  # noqa: F401
        row("OK", "nodriver", getattr(nodriver, "__version__", "installed"))
    except Exception as e:
        row("MISSING", "nodriver", f"{e}  (pip install nodriver in the venv)")
        ok = False
    b = find_browser(args.chrome)
    if b:
        row("OK", "chrome/chromium", b)
    else:
        row("MISSING", "chrome/chromium", "install google-chrome or chromium")
        ok = False
    if not args.headless and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        row("WARN", "display", "no DISPLAY/WAYLAND_DISPLAY; headed mode (needed for manual verification) won't start")
    src = Path(args.src).expanduser()
    if src.is_dir():
        row("OK", "source dir", str(src))
    else:
        row("WARN", "source dir", f"{src} not found; built-in fallback names will be used")
    for label, rel in (("names", "data/names.txt"), ("user agents", "config/user_agents.txt"),
                       ("password", "config/password.txt"), ("config.py", "config/config.py")):
        n = len(read_lines(src / rel)) if rel.endswith(".txt") else int((src / rel).is_file())
        row("OK" if n else "OPTIONAL", label, f"{rel}: {n} {'entries' if rel.endswith('.txt') else 'file'}" if n else f"{rel} missing/empty")
    key = load_5sim_key(src)
    row("OPTIONAL", "5sim key", "present but hook is a STUB (unused)" if key else "not configured (not required)")
    try:
        validate_proxy(args.proxy)
        row("OK", "proxy", args.proxy or "none")
    except ValueError as e:
        row("ERROR", "proxy", str(e))
        ok = False
    out = Path(args.out).expanduser()
    probe = out.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    row("OK" if os.access(probe, os.W_OK) else "ERROR", "output file", str(out))
    print("\nResult:", "ready (phone verification will still be manual)" if ok else "NOT ready - fix MISSING items")
    return 0 if ok else 1


# ------------------------------------------------------------ browser flow

BUTTON_JS = """
(labels) => {
  const re = new RegExp('^(' + labels.join('|') + ')$', 'i');
  const b = [...document.querySelectorAll('button, [role=button]')].find(
    x => re.test((x.innerText || '').trim()) && !x.disabled && x.offsetParent !== null);
  if (b) { b.click(); return true; }
  return false;
}
"""

STATE_JS = """
(() => {
  const vis = s => { const e = document.querySelector(s); return !!(e && e.offsetParent !== null); };
  const url = location.href, text = (document.body && document.body.innerText || '').toLowerCase();
  if (/myaccount\\.google\\.com|mail\\.google\\.com|\\/\\/www\\.google\\.com\\/?(\\?|$)/.test(url)) return 'success';
  if (/couldn.t create your account|can.t create your account|cannot create your account|too many/.test(text)) return 'blocked';
  if (vis('input[name=firstName]')) return 'name';
  if (vis('input[name=Username]')) return 'username';
  if (vis('input[name=Passwd]')) return 'password';
  if (vis('input#phoneNumberId') || vis('input[type=tel]')) return 'phone';
  if (vis('input[name=code]') || vis('input[name=smsUserPin]') || /enter the code|verification code/.test(text)) return 'code';
  if (vis('#day') || vis('input[name=day]')) return 'birthday';
  if (vis('iframe[src*="recaptcha"]') || /not a robot|captcha/.test(text)) return 'captcha';
  if (/recovery email|review your account info|privacy and terms|welcome to google|add recovery/.test(text)) return 'post';
  return 'unknown';
})()
"""

SET_FIELD_JS = """
(args) => {
  const [sel, val, label] = args;
  const el = document.querySelector(sel);
  if (!el) return false;
  if (el.tagName === 'SELECT') {
    el.value = String(val);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return el.value === String(val);
  }
  // custom Material dropdown fallback: open it, click option by data-value / label
  el.click();
  const opts = [...document.querySelectorAll('[role=option], li[data-value]')];
  const o = opts.find(x => x.getAttribute('data-value') === String(val) ||
                           (x.innerText || '').trim().toLowerCase() === String(label).toLowerCase());
  if (o) { o.click(); return true; }
  return false;
}
"""

CUSTOM_ADDRESS_JS = """
(() => {
  const el = [...document.querySelectorAll('[role=radio], [data-value], label, div')]
    .find(x => /create your own gmail address/i.test((x.innerText || '').trim()) && (x.innerText || '').length < 60);
  if (el) { el.click(); return true; }
  return false;
})()
"""

USERNAME_ERROR_JS = """
(() => /already taken|that username is taken|already used|username is taken|try another/i.test(
  (document.body.innerText || ''))
)()
"""

SKIP_LABELS = ["skip", "not now", "no thanks"]
NEXT_LABELS = ["next", "continue", "i agree", "agree", "accept all", "confirm"]


class FlowError(Exception):
    pass


async def sleep(a: float = 0.6, b: float = 1.4) -> None:
    await asyncio.sleep(random.uniform(a, b))


async def type_into(tab, selector: str, text: str, timeout: float = 15) -> None:
    el = await tab.select(selector, timeout=timeout)
    await el.click()
    try:
        await el.clear_input()
    except Exception:
        pass
    for ch in text:
        await el.send_keys(ch)
        await asyncio.sleep(random.uniform(0.02, 0.09))
    await sleep(0.3, 0.7)


async def click_button(tab, labels: list[str]) -> bool:
    import json as _json
    return bool(await tab.evaluate(f"({BUTTON_JS})({_json.dumps(labels)})"))


async def next_step(tab) -> None:
    if not await click_button(tab, ["next"]):
        raise FlowError("could not find the 'Next' button (selectors may have changed)")
    await sleep(1.5, 2.5)


async def state(tab) -> str:
    try:
        return str(await tab.evaluate(STATE_JS))
    except Exception:
        return "unknown"


async def wait_state(tab, wanted: set[str], timeout: float = 30) -> str:
    end = time.time() + timeout
    cur = "unknown"
    while time.time() < end:
        cur = await state(tab)
        if cur in wanted or cur in ("blocked", "captcha", "success"):
            return cur
        await asyncio.sleep(0.8)
    return cur


async def set_field(tab, selector: str, value, label: str = "") -> bool:
    import json as _json
    return bool(await tab.evaluate(f"({SET_FIELD_JS})({_json.dumps([selector, value, label])})"))


async def handle_phone_step(tab, st: Settings, args: argparse.Namespace) -> str:
    """Phone/SMS verification is MANUAL.  Waits for the human to finish it.

    A provider (see FiveSimStub) could be plugged in here; it is intentionally
    not called.  Returns 'success', 'blocked', or 'timeout'.
    """
    if args.headless:
        log("  phone verification required; headless -> cannot wait for a human")
        return "timeout"
    log(f"  >>> PHONE VERIFICATION: complete it in the browser window "
        f"(waiting up to {args.verify_timeout}s) <<<")
    end = time.time() + args.verify_timeout
    while time.time() < end:
        cur = await state(tab)
        if cur == "success":
            return "success"
        if cur == "blocked":
            return "blocked"
        if cur == "post":
            await click_button(tab, SKIP_LABELS + NEXT_LABELS)
        await asyncio.sleep(2)
    return "timeout"


async def finish_post_screens(tab, timeout: float = 60) -> str:
    """Walk through recovery-email / review / terms screens after verification."""
    end = time.time() + timeout
    while time.time() < end:
        cur = await state(tab)
        if cur == "success":
            return "success"
        if cur in ("blocked", "captcha"):
            return cur
        if not await click_button(tab, SKIP_LABELS):
            await click_button(tab, NEXT_LABELS)
        await asyncio.sleep(2)
    return "timeout"


async def create_one(uc, st: Settings, args: argparse.Namespace, proxy: Optional[str],
                     chrome: Optional[str]) -> Optional[dict]:
    """Run one signup attempt.  Returns a result record, or None if never saved."""
    full = random.choice(st.names)
    first, last = split_name(full)
    password = pick_password(st, args)
    username = gen_username(first, last)
    month, day, year = st.birthday
    email = f"{username}@gmail.com"
    log(f"account: {full!r} -> {email}")

    profile = tempfile.mkdtemp(prefix="gmail-creator-")
    browser_args = ["--lang=en-US", "--no-first-run", "--no-default-browser-check"]
    if proxy:
        browser_args.append(f"--proxy-server={proxy}")
    if args.use_ua_file and st.user_agents:
        browser_args.append(f"--user-agent={random.choice(st.user_agents)}")
    browser = None
    record: Optional[dict] = None
    status = "failed"
    reached_password = False
    try:
        browser = await uc.start(
            headless=args.headless,
            user_data_dir=profile,
            browser_executable_path=chrome,
            browser_args=browser_args,
            sandbox=(os.geteuid() != 0),
        )
        tab = await browser.get(SIGNUP_URL)
        await sleep(2, 3)

        # Step 1: name
        cur = await wait_state(tab, {"name"})
        if cur != "name":
            raise FlowError(f"name step not reached (state={cur})")
        await type_into(tab, "input[name=firstName]", first)
        if last:
            await type_into(tab, "input[name=lastName]", last)
        await next_step(tab)

        # Step 2: birthday + gender
        cur = await wait_state(tab, {"birthday"})
        if cur != "birthday":
            raise FlowError(f"birthday step not reached (state={cur})")
        if not await set_field(tab, "#month", month, datetime(2000, month, 1).strftime("%B")):
            log("  warning: month field could not be set")
        await type_into(tab, "#day", str(day))
        await type_into(tab, "#year", str(year))
        if not await set_field(tab, "#gender", GENDER_TO_GOOGLE[st.gender], GENDER_LABEL[st.gender]):
            log("  warning: gender field could not be set")
        await next_step(tab)

        # Step 3: username (retry if taken / suggestions shown)
        cur = await wait_state(tab, {"username"})
        if cur == "unknown":
            await tab.evaluate(CUSTOM_ADDRESS_JS)
            await sleep()
            cur = await wait_state(tab, {"username"}, 10)
        if cur != "username":
            raise FlowError(f"username step not reached (state={cur})")
        for attempt in range(4):
            await type_into(tab, "input[name=Username]", username)
            await next_step(tab)
            cur = await wait_state(tab, {"password", "username"}, 15)
            if cur == "password":
                break
            if cur == "username" and await tab.evaluate(USERNAME_ERROR_JS):
                username = gen_username(first, last)
                email = f"{username}@gmail.com"
                log(f"  username taken; retrying with {username}")
                continue
            if cur == "unknown" and await tab.evaluate(CUSTOM_ADDRESS_JS):
                await sleep()
                continue
            raise FlowError(f"username rejected / unexpected state={cur}")
        else:
            raise FlowError("could not find a free username")

        # Step 4: password
        await type_into(tab, "input[name=Passwd]", password)
        await type_into(tab, "input[name=PasswdAgain]", password)
        reached_password = True
        await next_step(tab)

        # Step 5+: verification (manual) and trailing screens
        cur = await wait_state(tab, {"phone", "code", "post"}, 30)
        if cur == "success":
            status = "created"
        elif cur in ("phone", "code"):
            res = await handle_phone_step(tab, st, args)
            if res == "success":
                status = "created"
            elif res == "blocked":
                status = "failed"
            else:
                status = "pending_verification"
            if status != "failed" and res != "success" and res != "timeout":
                pass
        elif cur == "post":
            res = await finish_post_screens(tab)
            status = "created" if res == "success" else "pending_verification"
        elif cur in ("captcha", "blocked"):
            log(f"  Google blocked the signup ({cur}); not solved by this script")
            status = "failed"
        else:
            log(f"  unexpected state after password: {cur}")
            status = "pending_verification"

        if status == "created":
            # a few accounts still show Skip/Agree screens before landing
            await finish_post_screens(tab, 20)
    except FlowError as e:
        log(f"  flow stopped: {e}")
    except Exception as e:  # nodriver/CDP/timeouts
        log(f"  error: {type(e).__name__}: {e}")
    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                pass
        shutil.rmtree(profile, ignore_errors=True)

    if reached_password:
        record = {"email": email, "password": password, "created_at": utcnow(), "status": status}
    else:
        log("  never got past the username step; nothing saved")
    return record


async def run(args: argparse.Namespace, st: Settings, proxy: Optional[str], chrome: Optional[str]) -> int:
    import nodriver as uc

    out = Path(args.out).expanduser()
    counts: dict[str, int] = {}
    for i in range(args.count):
        log(f"=== attempt {i + 1}/{args.count} ===")
        rec = await create_one(uc, st, args, proxy, chrome)
        if rec:
            save_result(out, rec)
            counts[rec["status"]] = counts.get(rec["status"], 0) + 1
            log(f"  saved {rec['email']} [{rec['status']}] -> {out}")
        else:
            counts["not_saved"] = counts.get("not_saved", 0) + 1
        if i + 1 < args.count:
            await asyncio.sleep(args.delay)
    log(f"done: {counts}")
    return 0 if counts.get("created") else 2


# ------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gmail_creator.py",
        description="Semi-automated Gmail signup via nodriver. Phone verification / captcha "
                    "are NOT bypassed and normally require a human. See module docstring.",
    )
    p.add_argument("--count", type=int, default=1, metavar="N", help="accounts to attempt (default 1)")
    p.add_argument("--headless", action="store_true",
                   help="run without a window (no manual verification possible; likely blocked)")
    p.add_argument("--proxy", metavar="URL", help="scheme://host:port (no credentials; http/https/socks4/socks5)")
    p.add_argument("--out", default=str(DEFAULT_OUT), metavar="FILE",
                   help=f"JSON results file, appended to (default {DEFAULT_OUT})")
    p.add_argument("--check", action="store_true", help="report dependencies and exit")
    p.add_argument("--dry-run", action="store_true",
                   help="show the identities/plan that would be used; no browser, no network, no file writes")
    p.add_argument("--src", default=str(DEFAULT_SRC), metavar="DIR",
                   help=f"original project dir with data/ and config/ (default {DEFAULT_SRC})")
    p.add_argument("--names", metavar="FILE", help="names file override (one 'First Last' per line)")
    p.add_argument("--birthday", metavar="'M D YYYY'", help="override YOUR_BIRTHDAY from config.py")
    p.add_argument("--gender", choices=["1", "2", "3"], help="1=Male 2=Female 3=Other (override config.py)")
    p.add_argument("--password", help="use this password for every account (overrides password.txt)")
    p.add_argument("--random-password", action="store_true",
                   help="ignore config/password.txt and generate a random password per account")
    p.add_argument("--use-ua-file", action="store_true",
                   help="pick a UA from config/user_agents.txt (off by default: those are Windows UAs "
                        "and mismatch a Linux browser)")
    p.add_argument("--chrome", metavar="PATH", help="browser executable (default: auto-detect)")
    p.add_argument("--verify-timeout", type=int, default=600, metavar="SEC",
                   help="seconds to wait for manual phone verification (default 600)")
    p.add_argument("--delay", type=float, default=30.0, metavar="SEC", help="pause between accounts (default 30)")
    return p


def run_dry(args: argparse.Namespace, st: Settings, proxy: Optional[str]) -> int:
    m, d, y = st.birthday
    print("DRY RUN - no browser started, nothing written\n")
    print(f"  signup url   : {SIGNUP_URL}")
    print(f"  results file : {Path(args.out).expanduser()}")
    print(f"  browser      : {find_browser(args.chrome) or 'NOT FOUND'}   headless={args.headless}")
    print(f"  proxy        : {proxy or 'none'}")
    print(f"  birthday     : {m}/{d}/{y}   gender={GENDER_LABEL[st.gender]}")
    print(f"  names loaded : {len(st.names)}   user agents: {len(st.user_agents)} "
          f"(used: {'yes' if args.use_ua_file else 'no'})")
    src = "--password" if args.password else ("password.txt" if st.file_password and not args.random_password else "random")
    print(f"  password src : {src}")
    print(f"  5sim hook    : {'key present, STUB (unused)' if st.fivesim_key else 'not configured'}")
    print(f"  verification : MANUAL (wait up to {args.verify_timeout}s{'; impossible in headless' if args.headless else ''})")
    print("\n  sample identities:")
    for _ in range(min(args.count, 5)):
        first, last = split_name(random.choice(st.names))
        print(f"    {first} {last}".rstrip() + f"  ->  {gen_username(first, last)}@gmail.com")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.count < 1:
        print("error: --count must be >= 1", file=sys.stderr)
        return 2
    if args.check:
        return run_check(args)
    try:
        proxy = validate_proxy(args.proxy)
        st = load_settings(args)
        if st.file_password is not None and not args.password and not args.random_password and len(st.file_password) < 8:
            raise ValueError("config/password.txt password is shorter than 8 characters")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.dry_run:
        return run_dry(args, st, proxy)

    chrome = find_browser(args.chrome)
    if not chrome:
        print("error: no Chrome/Chromium found (try --check)", file=sys.stderr)
        return 1
    if not args.headless and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print("error: no display for headed mode; set DISPLAY or use --headless (see docs)", file=sys.stderr)
        return 1
    if args.headless:
        log("warning: headless cannot complete phone verification; expect pending_verification/failed")
    try:
        import nodriver  # noqa: F401
    except ImportError:
        print("error: nodriver not installed; run with the auto-freecf venv python (see --check)", file=sys.stderr)
        return 1
    try:
        return asyncio.run(run(args, st, proxy, chrome))
    except KeyboardInterrupt:
        log("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
