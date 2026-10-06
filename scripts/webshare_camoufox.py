#!/usr/bin/env python3
"""
scripts/webshare_camoufox.py — Webshare residential proxy hunter ported to Camoufox.

Automates Webshare free residential proxy harvesting:
1. Launches Camoufox (Async API with Windows OS fingerprint, humanize cursor movements).
2. Navigates to https://proxy.webshare.io/register.
3. Fills email (domain from --domain via our relay or temp provider), strong password, accepts ToS.
4. Solves Google reCAPTCHA v2 (invisible challenge) via free audio challenge path:
   - Identifies the challenge frame across page.frames.
   - Clicks audio headphone button (#recaptcha-audio-button).
   - Downloads audio payload (#audio-source).
   - Transcribes audio using SpeechRecognition (recognize_google) + pydub/ffmpeg.
   - Types transcription with human-like delays into #audio-response.
   - Submits verification (#recaptcha-verify-button).
   - (Optionally accepts CAPSOLVER_API_KEY if configured and valid).
5. Navigates to Dashboard / Proxy List, active-polls for proxies (via download link API or DOM table).
6. Formats proxies as http://user:pass@ip:port and appends to target output file without clobbering.
7. Enforces rate-limiting and safety (1 account default, 20-45s delay between accounts, stops on rate-limit).

Usage:
  python3 scripts/webshare_camoufox.py [--count 1] [--headless] [--domain kancalabs.biz.id] [--out FILE]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import string
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Optional external dependencies with graceful fallbacks
try:
    from camoufox.async_api import AsyncCamoufox  # type: ignore
except Exception as _cf_err:  # pragma: no cover
    AsyncCamoufox = None
    _CAMOUFOX_IMPORT_ERROR = _cf_err
else:
    _CAMOUFOX_IMPORT_ERROR = None

try:
    import speech_recognition as sr  # type: ignore
    from pydub import AudioSegment  # type: ignore
except Exception as _audio_err:  # pragma: no cover
    sr = None
    AudioSegment = None
    _AUDIO_IMPORT_ERROR = _audio_err
else:
    _AUDIO_IMPORT_ERROR = None

# Default paths
DEFAULT_OUTPUT_FILE = Path("/home/amen/petani-proxy/output/webshare_residential.txt")
REGISTER_URL = "https://proxy.webshare.io/register"


# ─────────────────────────────────────────────────────────── Pure Helpers
def resolve_domain(domain_choice: str = "kancalabs.biz.id") -> str:
    """Normalize and resolve domain name from short alias or full domain string."""
    dc = str(domain_choice or "kancalabs.biz.id").strip().lower().lstrip("@")
    if dc in ("bizid", "biz.id", "kancalabs.biz.id"):
        return "kancalabs.biz.id"
    if dc in ("myid", "my.id", "kancalabs.my.id"):
        return "kancalabs.my.id"
    return dc


def generate_webshare_email(
    domain: str = "kancalabs.biz.id", rand_suffix: str | None = None
) -> str:
    """Generate a disposable email address for Webshare signup on the given domain."""
    dom = resolve_domain(domain)
    suffix = rand_suffix if rand_suffix is not None else "".join(
        random.choices(string.ascii_lowercase + string.digits, k=10)
    )
    return f"ws{suffix}@{dom}"


def generate_webshare_password() -> str:
    """Generate a strong compliant password for Webshare account registration."""
    special = random.choice("!@#$%^&*")
    rand_mid = "".join(random.choices(string.ascii_letters + string.digits, k=8))
    return f"Wsh{special}{rand_mid}99!"


def parse_proxy_line(raw: str) -> dict[str, str] | None:
    """
    Parse a raw proxy string into a structured dict with user, password, ip, port, url.
    Supports:
      - user:pass@ip:port
      - http://user:pass@ip:port / https://user:pass@ip:port
      - ip:port:user:pass (Webshare raw format)
    Returns None if line does not contain a valid 4-tuple.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None

    clean = s
    if clean.startswith("http://"):
        clean = clean[7:]
    elif clean.startswith("https://"):
        clean = clean[8:]

    # Format 1: user:pass@ip:port
    if "@" in clean:
        creds, endpoint = clean.split("@", 1)
        if ":" in creds and ":" in endpoint:
            u, pw = creds.split(":", 1)
            ip, port = endpoint.split(":", 1)
            if ip and port and u and pw:
                return {
                    "user": u,
                    "password": pw,
                    "ip": ip,
                    "port": port,
                    "url": f"http://{u}:{pw}@{ip}:{port}",
                }

    # Format 2: ip:port:user:pass
    parts = clean.split(":")
    if len(parts) == 4:
        ip, port, u, pw = parts
        if ip and port and u and pw:
            return {
                "user": u,
                "password": pw,
                "ip": ip,
                "port": port,
                "url": f"http://{u}:{pw}@{ip}:{port}",
            }

    return None


def is_login_url(url: str) -> bool:
    """Return True if url indicates a registration or login page."""
    u = (url or "").lower()
    if not u:
        return False
    return any(p in u for p in ("/register", "/login", "/signin", "/signup"))


def is_dashboard_url(url: str) -> bool:
    """Return True if url indicates that user has reached Webshare authenticated dashboard."""
    u = (url or "").lower()
    if not u or is_login_url(u):
        return False
    return (
        "dashboard.webshare.io" in u
        or "proxy/list" in u
        or ("proxy.webshare.io" in u and "register" not in u)
    )


def should_keep_polling(elapsed: float, timeout: float, found_count: int) -> bool:
    """Determine whether active proxy harvest polling should continue."""
    if found_count > 0:
        return False
    if elapsed >= timeout:
        return False
    return True


def pick_signup_button(labels: list[str]) -> str | None:
    """
    Select the email registration button label from candidate button labels.
    Explicitly rejects Google/social signup buttons.
    Requires exact or normalized case-insensitive match for 'Sign Up With Email'.
    """
    if not labels:
        return None
    for label in labels:
        if not label or not isinstance(label, str):
            continue
        clean = label.strip()
        lower = clean.lower()
        if "google" in lower:
            continue
        if lower == "sign up with email":
            return clean
        if " ".join(lower.split()) == "sign up with email":
            return clean
    return None


def append_proxies_to_file(proxies: list[str], filepath: str | Path) -> int:
    """Append harvested proxies to target file without duplicating or overwriting existing entries."""
    if not proxies:
        return 0
    p = Path(filepath)
    p.parent.mkdir(parents=True, exist_ok=True)

    existing: set[str] = set()
    if p.exists():
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    existing.add(line)
        except Exception:
            pass

    added = 0
    with p.open("a", encoding="utf-8") as f:
        for proxy in proxies:
            proxy_clean = proxy.strip()
            if proxy_clean and proxy_clean not in existing:
                f.write(proxy_clean + "\n")
                existing.add(proxy_clean)
                added += 1

    return added


def sync_to_kancahub_tools(proxies: list[str]) -> None:
    """Sync harvested residential proxies to known local tool pools if present."""
    if not proxies:
        return
    home = Path.home()
    targets = [
        home / "Auto-FreeCF" / "signup_from_scratch" / "proxies.txt",
        home / "harbor" / "tools" / "proxies.txt",
        Path("/home/amen/petani-proxy/output/live_elite.txt"),
    ]
    for target in targets:
        if target.parent.exists():
            try:
                added = append_proxies_to_file(proxies, target)
                if added > 0:
                    print(f"  ✓ Auto-synced {added} fresh proxies -> {target}", flush=True)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────── reCAPTCHA Solvers
async def try_solve_capsolver(page: Any, capsolver_key: str) -> bool:
    """Optional CapSolver API solver. Skips if key is invalid, dead, or empty."""
    if not capsolver_key or capsolver_key.startswith("CAP-dead") or len(capsolver_key) < 10:
        return False

    try:
        import requests
        sitekey = await page.evaluate("""() => {
            const el = document.querySelector('[data-sitekey]');
            if (el) return el.getAttribute('data-sitekey');
            const iframe = document.querySelector('iframe[src*="recaptcha"]');
            if (iframe) {
                const match = iframe.src.match(/[?&]k=([^&]+)/);
                if (match) return match[1];
            }
            return '';
        }""")
        if not sitekey:
            return False

        print(f"  [*] [CapSolver] Found sitekey: {sitekey}. Creating task…", flush=True)
        task_res = requests.post(
            "https://api.capsolver.com/createTask",
            json={
                "clientKey": capsolver_key,
                "task": {
                    "type": "ReCaptchaV2TaskProxyLess",
                    "websiteURL": page.url,
                    "websiteKey": sitekey,
                },
            },
            timeout=10,
        ).json()

        task_id = task_res.get("taskId")
        if not task_id:
            return False

        for _ in range(25):
            await asyncio.sleep(2.0)
            res = requests.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": capsolver_key, "taskId": task_id},
                timeout=10,
            ).json()
            if res.get("status") == "ready":
                token = res.get("solution", {}).get("gRecaptchaResponse")
                if token:
                    await page.evaluate(f"""() => {{
                        const el = document.getElementById('g-recaptcha-response');
                        if (el) el.value = "{token}";
                    }}""")
                    print("  [+] [CapSolver] reCAPTCHA token applied successfully!", flush=True)
                    return True
            elif res.get("status") == "failed":
                return False

    except Exception:
        pass

    return False


async def try_solve_audio_challenge(page: Any) -> bool:
    """
    Detect and solve Google reCAPTCHA v2 challenge via the free audio challenge path.
    Inspects all attached page.frames to find the bframe, clicks the headphone button,
    downloads the MP3 audio file, transcribes with speech_recognition, and submits.
    """
    if sr is None or AudioSegment is None:
        print("  ✗ SpeechRecognition or pydub not available in environment.", file=sys.stderr)
        return False

    try:
        # 1. Search frames for reCAPTCHA challenge elements
        target_frame = None
        for frame in page.frames:
            try:
                has_recaptcha = await frame.evaluate("""() => {
                    return !!(
                        document.getElementById('recaptcha-audio-button') ||
                        document.querySelector('.rc-button-audio') ||
                        document.getElementById('audio-response') ||
                        document.getElementById('audio-source')
                    );
                }""")
                if has_recaptcha:
                    target_frame = frame
                    break
            except Exception:
                continue

        if not target_frame:
            return False

        # 2. Check if audio input is already open in this frame
        has_input = await target_frame.evaluate("() => !!document.getElementById('audio-response')")

        # 3. If audio input not yet open, click headphone icon
        if not has_input:
            clicked_audio = await target_frame.evaluate("""() => {
                const btn = document.getElementById('recaptcha-audio-button') ||
                            document.querySelector('.rc-button-audio');
                if (btn && btn.offsetParent !== null) {
                    btn.click();
                    return true;
                }
                return false;
            }""")
            if clicked_audio:
                print("  [*] Clicked reCAPTCHA headphone button, awaiting audio challenge…", flush=True)
                await asyncio.sleep(3.5)
                # Click play if needed
                await target_frame.evaluate("""() => {
                    const playBtn = Array.from(document.querySelectorAll('button')).find(
                        b => b.innerText && b.innerText.includes('PLAY')
                    );
                    if (playBtn) playBtn.click();
                }""")
                await asyncio.sleep(2.0)
                has_input = await target_frame.evaluate("() => !!document.getElementById('audio-response')")

        if not has_input:
            return False

        # 4. Check for Google automated query rate-limit
        is_limited = await target_frame.evaluate("""() => {
            const el = document.querySelector('.rc-doscaptcha-header-text') ||
                       Array.from(document.querySelectorAll('div, p')).find(
                           e => e.innerText && e.innerText.includes('automated queries')
                       );
            return el ? el.innerText : '';
        }""")
        if is_limited and "automated queries" in is_limited.lower():
            print(f"  [!] Google reCAPTCHA blocked audio challenge: '{is_limited.strip()}'", file=sys.stderr)
            return False

        # 5. Extract audio source URL
        mp3_url = await target_frame.evaluate("""() => {
            const src = document.getElementById('audio-source');
            if (src && src.src) return src.src;
            const a = document.querySelector('a.rc-audiochallenge-tdownload-link') ||
                      document.querySelector('a[href*=".mp3"]');
            return a ? a.href : '';
        }""")

        if not mp3_url:
            await asyncio.sleep(2.0)
            return False

        print(f"  [*] Downloading reCAPTCHA audio stream: {mp3_url[:65]}…", flush=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            mp3_path = os.path.join(tmpdir, "audio.mp3")
            wav_path = os.path.join(tmpdir, "audio.wav")

            # Download MP3 file
            urllib.request.urlretrieve(mp3_url, mp3_path)

            # Convert to WAV for speech recognition
            sound = AudioSegment.from_file(mp3_path)
            duration_sec = sound.duration_seconds
            sound.export(wav_path, format="wav")

            rec = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio_data = rec.record(source)
                text = rec.recognize_google(audio_data)

            print(f"  [+] reCAPTCHA transcribed ({duration_sec:.1f}s): '{text}'", flush=True)

            # Wait natural listening time
            listen_wait = max(2.5, min(duration_sec + 0.8, 6.0))
            await asyncio.sleep(listen_wait)

            # Fill audio response field safely
            try:
                audio_inp = target_frame.locator("#audio-response")
                if await audio_inp.count() > 0:
                    await audio_inp.click()
                    await audio_inp.fill(text)
                else:
                    await target_frame.evaluate("""(val) => {
                        const inp = document.getElementById('audio-response');
                        if (inp) {
                            inp.focus();
                            inp.value = val;
                            inp.dispatchEvent(new Event('input', { bubbles: true }));
                            inp.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }""", text)
            except Exception as e_type:
                print(f"  [Debug Audio Type] {e_type}", flush=True)

            await asyncio.sleep(random.uniform(1.0, 1.8))

            # Click verify button
            try:
                vbtn = target_frame.locator("#recaptcha-verify-button")
                if await vbtn.count() > 0:
                    await vbtn.click()
                    print("  [+] reCAPTCHA 'Verify' button clicked!", flush=True)
                    await asyncio.sleep(4.0)
                    return True
                else:
                    verified = await target_frame.evaluate("""() => {
                        const btn = document.getElementById('recaptcha-verify-button');
                        if (btn) { btn.click(); return true; }
                        return false;
                    }""")
                    if verified:
                        print("  [+] reCAPTCHA 'Verify' button clicked (JS)!", flush=True)
                        await asyncio.sleep(4.0)
                        return True
            except Exception as e_v:
                print(f"  [Debug Audio Verify] {e_v}", flush=True)

    except Exception as e:
        print(f"  [Debug Audio] {e}", flush=True)

    return False


# ─────────────────────────────────────────────────────────── Dashboard Harvester
async def harvest_proxies(page: Any, max_wait: float = 60.0) -> list[str]:
    """
    Poll the Webshare Dashboard / Proxy List to extract live residential proxies.
    Extracts from either the direct Download Link API or the rendered DOM table.
    """
    poll_start = time.time()
    proxies: list[str] = []

    while should_keep_polling(time.time() - poll_start, max_wait, len(proxies)):
        url = page.url or ""

        # A. Dismiss any onboarding / welcome dialogs
        await page.evaluate("""() => {
            const buttons = Array.from(document.querySelectorAll('button'));
            const btnGo = buttons.find(b => b.innerText && b.innerText.includes('Go To Proxy List'));
            if (btnGo) btnGo.click();
            const closeBtn = document.querySelector('button[aria-label="Close"], svg[data-testid="CloseIcon"]');
            if (closeBtn) closeBtn.click();
        }""")

        # B. Check for Download Link in input elements
        dl_url = await page.evaluate(r"""() => {
            const input = document.querySelector('input[value*="proxy/list/download"]');
            if (input) return input.value;
            const anyInput = Array.from(document.querySelectorAll('input')).find(
                i => i.value && i.value.includes('/download/')
            );
            return anyInput ? anyInput.value : '';
        }""")

        if dl_url:
            print(f"  [*] Detected Webshare Download Link API: {dl_url}", flush=True)
            try:
                import requests
                r = requests.get(dl_url, timeout=15)
                if r.status_code == 200 and r.text.strip():
                    for line in r.text.strip().splitlines():
                        parsed = parse_proxy_line(line)
                        if parsed and parsed.get("url"):
                            proxies.append(parsed["url"])
                    if proxies:
                        print(f"  [+] Harvested {len(proxies)} proxies via Download Link API!", flush=True)
                        break
            except Exception as e:
                print(f"  [!] Download API error: {e}", flush=True)

        # C. Extract directly from rendered DOM table
        extracted = await page.evaluate(r"""() => {
            const out = [];
            const rows = Array.from(document.querySelectorAll('tr'));
            for (const r of rows) {
                const cells = Array.from(r.querySelectorAll('td, th')).map(c => c.innerText.trim());
                if (cells.length >= 5) {
                    const ip = cells[1];
                    const port = cells[2];
                    const user = cells[3];
                    const pass = cells[4];
                    if (ip && port && user && pass && ip.match(/^\d+\.\d+\.\d+\.\d+$/) && port.match(/^\d+$/)) {
                        out.push(`http://${user}:${pass}@${ip}:${port}`);
                    }
                }
            }
            return out;
        }""")
        if extracted and isinstance(extracted, list) and len(extracted) > 0:
            proxies = extracted
            print(f"  [+] Harvested {len(proxies)} proxies directly from DOM table!", flush=True)
            break

        # D. Click Download button if visible to spawn download modal
        download_btn = page.locator("button:has-text('Download')").first
        if await download_btn.count() > 0 and not dl_url:
            try:
                await download_btn.click()
                await asyncio.sleep(1.5)
                continue
            except Exception:
                pass

        # E. Navigate from main dashboard to Proxy List if needed
        if "proxy/list" not in url:
            view_btn = page.locator(
                "button:has-text('Go To Proxy List'), button:has-text('View My Proxy List'), a:has-text('Proxy List')"
            ).first
            if await view_btn.count() > 0:
                try:
                    await view_btn.click()
                    await asyncio.sleep(2.0)
                    continue
                except Exception:
                    pass

        await asyncio.sleep(2.0)

    return proxies


# ─────────────────────────────────────────────────────────── Single Account Flow
async def hunt_single_account(
    index: int,
    total: int,
    *,
    headless: bool = False,
    domain_choice: str = "kancalabs.biz.id",
    proxy: str | None = None,
) -> list[str]:
    """Execute single Webshare account registration and proxy harvest."""
    if AsyncCamoufox is None:
        print(f"✗ Camoufox is not available: {_CAMOUFOX_IMPORT_ERROR}", file=sys.stderr)
        return []

    resolved_domain = resolve_domain(domain_choice)
    email = generate_webshare_email(domain=resolved_domain)
    password = generate_webshare_password()

    print("\n" + "=" * 65)
    print(f"  WEBSHARE CAMOUFOX HUNTER — ACCOUNT [{index}/{total}]" + (" [HEADLESS]" if headless else ""))
    print("=" * 65)
    print(f"  [*] Email prepared   : {email}")
    print(f"  [*] Password prepared: {password}")
    if proxy:
        print(f"  [*] Egress proxy     : {proxy}")

    camoufox_kwargs: dict[str, Any] = {
        "headless": headless,
        "os": "windows",
        "humanize": True,
    }
    if proxy:
        camoufox_kwargs["proxy"] = {"server": proxy}

    async with AsyncCamoufox(**camoufox_kwargs) as browser:
        page = await browser.new_page()

        print("  [1/4] Navigating to https://proxy.webshare.io/register…", flush=True)
        try:
            await page.goto(REGISTER_URL, timeout=45000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2.0, 3.5))
        except Exception as e:
            print(f"  ✗ Failed to load registration page: {e}", file=sys.stderr)
            return []

        # Fill Email (#email-input, input[name='email'], input[type='email'])
        email_inp = page.locator("#email-input, input[type='email'], input[name='email']").first
        if await email_inp.count() > 0:
            await email_inp.click()
            await email_inp.fill(email)
            await asyncio.sleep(random.uniform(0.3, 0.6))
            val_e = await email_inp.input_value()
            if val_e != email:
                await email_inp.fill(email)
        else:
            print("  ✗ Email input field not found.", file=sys.stderr)
            return []

        # Fill Password (input[type='password'], input[name='password'])
        pass_inp = page.locator("input[type='password'], input[name='password']").first
        if await pass_inp.count() > 0:
            await pass_inp.click()
            await pass_inp.fill(password)
            await asyncio.sleep(random.uniform(0.3, 0.6))
            val_p = await pass_inp.input_value()
            if val_p != password:
                await pass_inp.fill(password)
        else:
            print("  ✗ Password input field not found.", file=sys.stderr)
            return []

        # Check Terms of Service (input[type='checkbox'], .PrivateSwitchBase-input)
        chk = page.locator("input[type='checkbox'], .PrivateSwitchBase-input").first
        if await chk.count() > 0:
            try:
                is_checked = await chk.is_checked()
                if not is_checked:
                    await chk.click()
            except Exception:
                pass
            checked_ok = False
            try:
                checked_ok = await chk.is_checked()
            except Exception:
                pass
            if not checked_ok:
                await page.evaluate("""() => {
                    const c = document.querySelector("input[type='checkbox'], input.PrivateSwitchBase-input");
                    if (c && !c.checked) {
                        c.click();
                        c.checked = true;
                        c.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }""")
        await asyncio.sleep(random.uniform(0.4, 0.8))

        # Re-verify all field values stuck before clicking submit
        val_email_final = await email_inp.input_value()
        val_pass_final = await pass_inp.input_value()
        if not val_email_final:
            print("  [!] Email empty before submit, refilling…", flush=True)
            await email_inp.fill(email)
        if not val_pass_final:
            print("  [!] Password empty before submit, refilling…", flush=True)
            await pass_inp.fill(password)

        # Inspect candidate button labels for diagnostics
        btn_labels = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('button')).map(b => (b.innerText || b.textContent || '').trim()).filter(Boolean);
        }""")
        chosen_label = pick_signup_button(btn_labels)
        if chosen_label:
            print(f"  [2/4] Found exact signup button '{chosen_label}' (ignoring Google social button)", flush=True)

        # Click Sign Up With Email (EXACT, never Google)
        clicked_btn = False

        # Strategy 1: page.get_by_role("button", name="Sign Up With Email", exact=True)
        try:
            role_btn = page.get_by_role("button", name="Sign Up With Email", exact=True)
            if await role_btn.count() > 0:
                print("  [2/4] Clicking 'Sign Up With Email' (role=button)…", flush=True)
                await role_btn.first.click()
                clicked_btn = True
        except Exception:
            pass

        # Strategy 2: page.locator("button:has-text('Sign Up With Email')")
        if not clicked_btn:
            try:
                loc_btn = page.locator("button:has-text('Sign Up With Email')")
                if await loc_btn.count() > 0:
                    print("  [2/4] Clicking 'Sign Up With Email' (locator)…", flush=True)
                    await loc_btn.first.click()
                    clicked_btn = True
            except Exception:
                pass

        # Strategy 3: JS fallback (exact text match, excludes Google)
        if not clicked_btn:
            clicked_btn = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                for (const b of btns) {
                    const txt = (b.innerText || b.textContent || '').trim();
                    if (txt.toLowerCase().includes('google')) continue;
                    if (txt.toLowerCase() === 'sign up with email') {
                        b.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked_btn:
                print("  [2/4] Clicked 'Sign Up With Email' (JS fallback)…", flush=True)

        if not clicked_btn:
            print(f"  ✗ Exact 'Sign Up With Email' button not found. Detected buttons: {btn_labels}", file=sys.stderr)
            return []

        # Monitor reCAPTCHA and await Dashboard
        print("  [3/4] Monitoring reCAPTCHA & awaiting Dashboard…", flush=True)
        logged_in = False
        start_time = time.time()
        last_attempt_time = 0.0
        badge_passed = False

        capsolver_key = os.environ.get("CAPSOLVER_API_KEY", "").strip()

        while time.time() - start_time < 180:
            if page.is_closed():
                print("  [!] Browser page was closed.", file=sys.stderr)
                return []

            try:
                cur_url = page.url or ""
                if is_dashboard_url(cur_url):
                    logged_in = True
                    print(f"\n  [+] Confirmed: Reached Dashboard at {cur_url}!", flush=True)
                    break

                # Check for "Too many attempts" rate limiting
                has_rate_limit = await page.evaluate("""() => {
                    const alert = Array.from(document.querySelectorAll('div, p, span')).find(
                        el => el.innerText && el.innerText.includes('Too many attempts')
                    );
                    return !!alert;
                }""")
                if has_rate_limit:
                    print("  [!] Webshare rate limit detected: 'Too many attempts'. Stopping honestly.", file=sys.stderr)
                    return []

                # Check for generic error alert
                form_error = await page.evaluate("""() => {
                    const alert = document.querySelector('.MuiAlert-message, [role="alert"]');
                    return alert ? (alert.innerText || alert.textContent || '').trim() : '';
                }""")
                if form_error and "too many attempts" not in form_error.lower():
                    print(f"  [!] Webshare registration alert: '{form_error}'", file=sys.stderr)

                # Path A: Check invisible reCAPTCHA auto-solve / badge pass (grecaptcha.getResponse())
                recaptcha_resp = await page.evaluate("""() => {
                    try {
                        if (window.grecaptcha && typeof window.grecaptcha.getResponse === 'function') {
                            return window.grecaptcha.getResponse() || '';
                        }
                    } catch (e) {}
                    return '';
                }""")
                if recaptcha_resp and not badge_passed:
                    print(f"  [+] reCAPTCHA auto-pass confirmed (token len={len(recaptcha_resp)}). Awaiting Dashboard redirect…", flush=True)
                    badge_passed = True

                # Path B: Audio solver challenge (if interactive bframe challenge is present)
                if time.time() - last_attempt_time > 10:
                    last_attempt_time = time.time()
                    solved = False
                    if capsolver_key and not capsolver_key.startswith("CAP-dead") and len(capsolver_key) > 10:
                        solved = await try_solve_capsolver(page, capsolver_key)
                    if not solved:
                        await try_solve_audio_challenge(page)

            except Exception as e_mon:
                if "closed" in str(e_mon).lower():
                    print(f"  [!] Page or target closed during monitoring: {e_mon}", file=sys.stderr)
                    return []
                print(f"  [Debug Monitor] {e_mon}", flush=True)

            await asyncio.sleep(2.0)

        if not logged_in:
            print(f"  ✗ Account [{index}/{total}] registration timed out or did not enter dashboard.", file=sys.stderr)
            return []

        # Harvest proxies
        print("  [4/4] Harvesting residential proxies from Dashboard…", flush=True)
        proxies = await harvest_proxies(page, max_wait=60.0)
        return proxies


# ─────────────────────────────────────────────────────────── Multi-Account Hunter
async def run_hunter(
    total: int = 1,
    *,
    headless: bool = False,
    domain_choice: str = "kancalabs.biz.id",
    out_file: str | Path | None = None,
    proxy: str | None = None,
) -> list[str]:
    """Execute full multi-account hunter with rate safety pauses between iterations."""
    target_out = Path(out_file or DEFAULT_OUTPUT_FILE)
    all_proxies: list[str] = []
    dom = resolve_domain(domain_choice)

    print("\n" + "=" * 65)
    print("  🌾 WEBSHARE CAMOUFOX RESIDENTIAL HUNTER (FREE AUDIO SOLVER)")
    print(f"  • Accounts Target : {total}")
    print(f"  • Mode            : {'Headless' if headless else 'Visible'}")
    print(f"  • Email Domain    : @{dom}")
    print(f"  • Output File     : {target_out}")
    print("=" * 65)

    for i in range(1, total + 1):
        proxies = await hunt_single_account(
            i,
            total,
            headless=headless,
            domain_choice=domain_choice,
            proxy=proxy,
        )
        if proxies:
            added = append_proxies_to_file(proxies, target_out)
            print(f"  ✓ Account [{i}/{total}] yielded {len(proxies)} proxies ({added} new appended to {target_out})")
            all_proxies.extend(proxies)
            sync_to_kancahub_tools(proxies)
        else:
            print(f"  ⚠️ Account [{i}/{total}] yielded 0 proxies.")

        if i < total:
            delay = random.uniform(20.0, 45.0)
            print(f"  [*] Rate limit safety: pausing {delay:.1f}s before next account…", flush=True)
            await asyncio.sleep(delay)

    print("\n" + "=" * 65)
    print(f"  🎉 HUNT COMPLETE: {len(all_proxies)} total proxies gathered across {total} account(s).")
    print(f"  • File: {target_out}")
    print("=" * 65 + "\n")
    return all_proxies


# ─────────────────────────────────────────────────────────── CLI Entrypoint
def build_parser() -> argparse.ArgumentParser:
    """Build command-line parser."""
    ap = argparse.ArgumentParser(
        description="Webshare residential proxy hunter using Camoufox (free reCAPTCHA audio solver)"
    )
    ap.add_argument(
        "--count", "-n",
        type=int,
        default=1,
        help="Number of accounts to hunt (default: 1)",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run Camoufox browser headlessly",
    )
    ap.add_argument(
        "--domain", "-d",
        default="kancalabs.biz.id",
        choices=["kancalabs.biz.id", "kancalabs.my.id", "bizid", "myid"],
        help="Email domain for registration (default: kancalabs.biz.id)",
    )
    ap.add_argument(
        "--out", "-o",
        default=str(DEFAULT_OUTPUT_FILE),
        help=f"Target file to append harvested proxies (default: {DEFAULT_OUTPUT_FILE})",
    )
    ap.add_argument(
        "--proxy",
        default=None,
        help="Optional egress proxy URL for Camoufox connection",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    """CLI main function."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.count < 1:
        print("Error: --count must be at least 1", file=sys.stderr)
        return 2

    proxies = asyncio.run(
        run_hunter(
            total=args.count,
            headless=args.headless,
            domain_choice=args.domain,
            out_file=args.out,
            proxy=args.proxy,
        )
    )

    return 0 if len(proxies) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
