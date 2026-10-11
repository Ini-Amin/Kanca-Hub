"""Solve a reCAPTCHA v2 demo via the audio challenge (free speech-to-text). Needs a mobile/residential IP: datacenter exits get "automated queries"."""
import asyncio, os, tempfile, urllib.request, random
from camoufox.async_api import AsyncCamoufox
from pydub import AudioSegment
import speech_recognition as sr
async def main():
    async with AsyncCamoufox(headless="virtual", geoip=True, humanize=True, os="windows") as b:
        p = await b.new_page()
        await p.goto("https://www.google.com/recaptcha/api2/demo", wait_until="domcontentloaded", timeout=45000)
        await p.wait_for_timeout(3000)
        a = next(f for f in p.frames if "recaptcha/api2/anchor" in f.url)
        await a.locator("#recaptcha-anchor").click(timeout=10000)
        await p.wait_for_timeout(4000)
        bf = next(f for f in p.frames if "recaptcha/api2/bframe" in f.url)
        await bf.locator("#recaptcha-audio-button").click(timeout=8000)
        await p.wait_for_timeout(3500)
        if await bf.locator(".rc-doscaptcha-body").count(): print("BLOCKED automated queries"); return
        src = await bf.locator("a.rc-audiochallenge-tdownload-link").get_attribute("href")
        with tempfile.TemporaryDirectory() as d:
            m, w = os.path.join(d,"a.mp3"), os.path.join(d,"a.wav")
            urllib.request.urlretrieve(src, m); AudioSegment.from_file(m).export(w, format="wav")
            r = sr.Recognizer()
            with sr.AudioFile(w) as s: text = r.recognize_google(r.record(s))
        print("transcript:", text)
        await bf.locator("#audio-response").click()
        await bf.locator("#audio-response").press_sequentially(text, delay=random.randint(90,180))
        await bf.locator("#recaptcha-verify-button").click()
        await p.wait_for_timeout(4000)
        ok = await a.locator("#recaptcha-anchor[aria-checked=true]").count()
        print("SOLVED" if ok else "NOT SOLVED")
        if ok:
            await p.locator("#recaptcha-demo-submit").click(); await p.wait_for_timeout(2500)
            print("demo page says:", (await p.locator(".recaptcha-success").inner_text())[:60] if await p.locator(".recaptcha-success").count() else "no success text")
asyncio.run(main())
