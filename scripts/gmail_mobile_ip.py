"""Gmail signup via camoufox on the phone's Telkomsel tether IP.
Walks the real signup form; when Google throws a reCAPTCHA, solves it with the
verified audio-challenge flow (recaptcha_audio.py logic). STOPS and dumps a
screenshot the moment a phone-number/verification gate appears. One attempt
per IP; caller enforces the 3-attempt budget.
"""
import asyncio, os, random, sys, tempfile, urllib.request
from pathlib import Path
from camoufox.async_api import AsyncCamoufox
from pydub import AudioSegment
import speech_recognition as sr

REPORTS = Path.home() / "Auto-FreeCF-vendor-refs/reports"
SIGNUP = ("https://accounts.google.com/signup/v2/webcreateaccount"
          "?flowName=GlifWebSignIn&flowEntry=SignUp&hl=en")
FIRST, LAST = "Rizky", "Santoso"
UNAME = "rizkysantoso" + str(random.randint(1000, 9999))
PASSWORD = "Kv" + "".join(random.choices("abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789", k=10)) + "!7a"


async def solve_recaptcha(page) -> bool:
    """Checkbox -> audio challenge -> transcribe -> verify. Returns anchor-checked."""
    a = next((f for f in page.frames if "recaptcha/api2/anchor" in f.url), None)
    if not a:
        return True  # no captcha shown
    await a.locator("#recaptcha-anchor").click(timeout=10000)
    await page.wait_for_timeout(4000)
    bf = next((f for f in page.frames if "recaptcha/api2/bframe" in f.url), None)
    if not bf:
        return bool(await a.locator("#recaptcha-anchor[aria-checked=true]").count())
    for attempt in range(3):  # audio can fail; retry button exists
        try:
            await bf.locator("#recaptcha-audio-button").click(timeout=8000)
            await page.wait_for_timeout(3000)
            if await bf.locator(".rc-doscaptcha-body").count():
                print("  recaptcha: BLOCKED (doscaptcha)"); return False
            src = await bf.locator("a.rc-audiochallenge-tdownload-link").get_attribute("href")
            with tempfile.TemporaryDirectory() as d:
                m, w = os.path.join(d, "a.mp3"), os.path.join(d, "a.wav")
                urllib.request.urlretrieve(src, m)
                AudioSegment.from_file(m).export(w, format="wav")
                r = sr.Recognizer()
                with sr.AudioFile(w) as s:
                    text = r.recognize_google(r.record(s))
            print(f"  recaptcha transcript: {text}")
            await bf.locator("#audio-response").click()
            await bf.locator("#audio-response").press_sequentially(text, delay=random.randint(90, 180))
            await bf.locator("#recaptcha-verify-button").click()
            await page.wait_for_timeout(4000)
            if await a.locator("#recaptcha-anchor[aria-checked=true]").count():
                return True
            await bf.locator(".rc-audiochallenge-reload-button").click(timeout=5000)
            await page.wait_for_timeout(2000)
        except Exception as e:
            print(f"  recaptcha attempt {attempt+1}: {str(e)[:80]}")
    return False


async def click_next(page):
    for sel in ("button:has-text('Next')", "[jsname='LgbsSe']:has-text('Next')"):
        try:
            await page.locator(sel).first.click(timeout=4000)
            return
        except Exception:
            continue
    await page.keyboard.press("Enter")


async def main():
    shot = REPORTS / f"gmail_mobile_ip_attempt1.png"
    async with AsyncCamoufox(headless="virtual", geoip=True, humanize=True, os="windows") as b:
        p = await b.new_page()
        await p.goto(SIGNUP, wait_until="domcontentloaded", timeout=60000)
        await p.wait_for_timeout(4000)
        # name
        await p.locator("input[name='firstName']").fill(FIRST)
        await p.locator("input[name='lastName']").fill(LAST)
        await click_next(p); await p.wait_for_timeout(5000)
        if "signup/name" in p.url:  # slow load retry
            await p.locator("input[name='firstName']").fill(FIRST)
            await p.locator("input[name='lastName']").fill(LAST)
            await click_next(p); await p.wait_for_timeout(6000)
        print("  after name:", p.url[:80])
        if await p.locator("input[name='day']").count():
            await p.locator("input[name='day']").fill(str(random.randint(2, 27)))
            await p.locator("input[name='year']").fill(str(random.randint(1985, 1999)))
            # Google MD comboboxes have no aria-label; match on placeholder text
            for label, val in (("Month", "May"), ("Gender", "Male")):
                try:
                    cb = p.locator("div[role='combobox']").filter(has_text=label).first
                    if not await cb.count():
                        cb = p.locator(f"[role='combobox'][aria-label='{label}']").first
                    await cb.click(timeout=8000)
                    await p.wait_for_timeout(1200)
                    opt = p.locator("ul[role='listbox'] li[role='option']", has_text=val)
                    n = await opt.count()
                    for i in range(n):
                        t = (await opt.nth(i).inner_text()).strip()
                        if t == val:
                            await opt.nth(i).click()
                            break
                    await p.wait_for_timeout(800)
                except Exception as e:
                    print(f"  {label} select: {str(e)[:60]}")
            await click_next(p); await p.wait_for_timeout(5000)
            print("  after birthday:", p.url[:80])
        # username (radio form: pick 'Create your own')
        if "username" in p.url:
            try:
                await p.get_by_text("Create your own Gmail address", exact=False).first.click(timeout=6000)
                await p.wait_for_timeout(1500)
            except Exception:
                pass
            u = p.locator("input[name='Username'], input#username").first
            await u.wait_for(state="visible", timeout=15000)
            await u.fill(UNAME)
            await click_next(p); await p.wait_for_timeout(5000)
            print("  after username:", p.url[:80])
        # password
        if "password" in p.url or await p.locator("input[name='Passwd']").count():
            n = await p.locator("input[type='password']").count()
            for i in range(max(1, n)):
                try: await p.locator("input[type='password']").nth(i).fill(PASSWORD)
                except Exception: pass
            await click_next(p); await p.wait_for_timeout(6000)
            print("  after password:", p.url[:80])
        # captcha may appear anywhere from here; solve if anchor present
        if any("recaptcha" in f.url for f in p.frames):
            print("  recaptcha present — solving via audio …")
            ok = await solve_recaptcha(p)
            print("  recaptcha solved:", ok)
            await p.wait_for_timeout(3000)
        # where are we?
        txt = (await p.evaluate("() => (document.body&&document.body.innerText||'')"))[:400]
        url = p.url
        print("FINAL URL:", url[:100])
        print("PAGE TEXT:", txt[:300].replace("\n", " | "))
        # phone gate?
        phone_gate = any(k in (url + txt).lower() for k in
                         ("phoneverification", "crossflowverification", "mophoneverification",
                          "enter your phone", "verify your phone"))
        await p.screenshot(path=str(shot), full_page=True)
        print("SCREENSHOT:", shot)
        print("PHONE_GATE:", phone_gate)
        print("EMAIL:", f"{UNAME}@gmail.com", "PASSWORD:", PASSWORD)
asyncio.run(main())
