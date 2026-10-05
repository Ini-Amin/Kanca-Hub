#!/usr/bin/env python3
"""
Gmail creator via a real Android phone (ADB + CDP) — the trusted-device path.

Why this works when a desktop browser does not:
  A real Android device with Google Play Services is already a "trusted device"
  to Google. Account creation on it normally SKIPS the phone-number step that
  blocks a plain desktop Chrome session.

How it works:
  1. adb forward tcp:9222 localabstract:<browser-devtools-socket>
  2. Connect to the page over the DevTools WebSocket (CDP)
  3. Drive accounts.google.com/signup with the same steps as gmail_creator.py,
     but on the PHONE.

Requirements:
  - A physical Android phone (or emulator) connected (kancahub adb status)
  - A Chromium browser on it with remote debugging reachable via ADB, e.g.
    Chrome / Kiwi Browser (com.kiwibrowser.browser)

Usage:
  python3 gmail_adb.py --count 1
  python3 gmail_adb.py --package com.kiwibrowser.browser
  python3 gmail_adb.py --dry-run            # open the form, create nothing

If the phone STILL shows a phone-verification step, we stop and tell you to
finish it on the phone (we never fake a result).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import string
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

WEB = "https://accounts.google.com/signup/v2/createaccount?flowName=GlifWebSignIn&flowEntry=SignUp"
CDP_PORT = 9222
PKG_SOCKETS = {
    "com.android.chrome": "chrome_devtools_remote",
    "com.kiwibrowser.browser": "chrome_devtools_remote",
    "com.brave.browser": "chrome_devtools_remote",
    "org.chromium.chrome": "chrome_devtools_remote",
}
OUT = Path.home() / "Auto-FreeCF" / "gmail_accounts.json"


# ───────────────────────────────────────────── adb

def adb(*args: str, timeout: int = 20) -> str:
    try:
        r = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return f"ERR {e}"


def devices() -> list[str]:
    out = adb("devices")
    return [l.split("\t")[0] for l in out.splitlines()[1:] if "\t" in l and l.strip()]


def preflight(pkg: str) -> str:
    if not devices():
        print("✗ No Android device connected.")
        print("  Connect a phone first:  kancahub adb usb   (then follow the prompts)")
        print("  Or over Wi-Fi:          kancahub adb setup  ->  kancahub adb connect <ip>")
        sys.exit(2)
    serial = devices()[0]
    adb("forward", "--remove-all", timeout=10)
    # launch browser
    adb("-s", serial, "shell", "monkey", "-p", pkg, "-c",
        "android.intent.category.LAUNCHER", "1", timeout=20)
    time.sleep(3)
    sock = PKG_SOCKETS.get(pkg, "chrome_devtools_remote")
    adb("-s", serial, "forward", f"tcp:{CDP_PORT}", f"localabstract:{sock}", timeout=15)
    # verify CDP
    for i in range(6):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=4) as r:
                data = json.loads(r.read())
            print(f"✓ CDP ready on {data.get('Browser','?')} ({serial})")
            return serial
        except Exception:
            time.sleep(2)
    print("✗ Could not reach the phone's DevTools.")
    print(f"  Make sure {pkg} is installed and open on the phone.")
    sys.exit(2)


# ───────────────────────────────────────────── CDP over websocket

class CDP:
    def __init__(self, url: str):
        from websocket import create_connection
        self.ws = create_connection(url, timeout=30)
        self._id = 0
        self.send("Page.enable")
        self.send("Runtime.enable")

    def send(self, method: str, params: dict | None = None, timeout: int = 30):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        self.ws.settimeout(timeout)
        while True:
            try:
                resp = json.loads(self.ws.recv())
            except Exception:
                return None
            if resp.get("id") == mid:
                return resp.get("result", {}) if "error" not in resp else None

    def js(self, expr: str, timeout: int = 30):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True, "awaitPromise": True},
                      timeout=timeout)
        if r and "result" in r and "value" in r["result"]:
            return r["result"]["value"]
        return None

    def goto(self, url: str):
        self.send("Page.navigate", {"url": url})


def page_ws_url() -> str | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=5) as r:
            pages = json.loads(r.read())
        for p in pages:
            if p.get("type") == "page":
                return p["webSocketDebuggerUrl"]
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/new?about:blank", timeout=5) as r:
            return json.loads(r.read())["webSocketDebuggerUrl"]
    except Exception as e:  # noqa: BLE001
        print(f"  (no page: {e})")
        return None


# ───────────────────────────────────────────── form helpers

def type_js(selector: str, value: str) -> str:
    return f"""
    (()=>{{
      const el=document.querySelector({json.dumps(selector)});
      if(!el) return false;
      el.focus();
      const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
      s.call(el,{json.dumps(value)});
      el.dispatchEvent(new InputEvent('input',{{bubbles:true,data:{json.dumps(value)},inputType:'insertText'}}));
      el.dispatchEvent(new Event('change',{{bubbles:true}}));
      return el.value;
    }})()"""


def click_text_js(text: str) -> str:
    return f"""
    (()=>{{
      const t={json.dumps(text.lower())};
      const els=[...document.querySelectorAll('button,a,div[role=button],span')];
      const el=els.find(e=>(e.innerText||'').trim().toLowerCase()===t)
            || els.find(e=>(e.innerText||'').trim().toLowerCase().includes(t));
      if(!el) return false; el.click(); return true;
    }})()"""


def state(cdp: CDP) -> str:
    return cdp.js("""
    (()=>{
      const vis=s=>{const e=document.querySelector(s);return !!(e&&e.offsetParent!==null);};
      const url=location.href, txt=(document.body&&document.body.innerText||'').toLowerCase();
      if(/myaccount\\.google\\.com|mail\\.google\\.com/.test(url)) return 'success';
      if(/couldn.t create|can.t create|too many|verify it.s you/.test(txt)) return 'blocked';
      if(vis('input[name=firstName]')) return 'name';
      if(vis('input[name=Username]')) return 'username';
      if(vis('input[name=Passwd]')) return 'password';
      if(vis('input#phoneNumberId')||vis('input[type=tel]')) return 'phone';
      if(vis('#day')||vis('input[name=day]')) return 'birthday';
      if(/recovery email|review your|privacy and terms|welcome to google/.test(txt)) return 'post';
      return 'unknown';
    })()""") or "unknown"


def wait_state(cdp: CDP, wanted: set, timeout: int = 30) -> str:
    end = time.time() + timeout
    while time.time() < end:
        s = state(cdp)
        if s in wanted or s in ("blocked",):
            return s
        time.sleep(1.5)
    return state(cdp)


FAIL_MARKERS = ("verify you are human", "captcha", "unusual traffic", "couldn't create")


def detect_blocked(cdp: CDP) -> str | None:
    txt = (cdp.js("(document.body&&document.body.innerText||'').toLowerCase()") or "")
    for m in FAIL_MARKERS:
        if m in txt:
            return m
    return None


def gen_password(n: int = 14) -> str:
    a = string.ascii_letters
    return (random.choice(a) + random.choice(a)
            + "".join(random.choices(a + string.digits + "!@#$%", k=n - 2)))


def gen_username(first: str, last: str) -> str:
    return (first.lower() + last.lower()
            + str(random.randint(100, 99999)))


def save(rec: dict) -> None:
    data = []
    if OUT.exists():
        try:
            data = json.loads(OUT.read_text())
        except Exception:
            data = []
    data.append(rec)
    OUT.write_text(json.dumps(data, indent=2))
    print(f"  saved -> {OUT}")


# ───────────────────────────────────────────── main flow

def run_one(cdp: CDP, args) -> dict:
    first = random.choice(["Oliver", "James", "Michael", "David", "Robert", "Ethan", "Noah", "Liam"])
    last = random.choice(["Johnson", "Smith", "Brown", "Wilson", "Taylor", "Anderson", "Thomas"])
    username = gen_username(first, last)
    password = gen_pw if (gen_pw := getattr(args, "password", None)) else gen_password()
    month, day, year = random.randint(1, 12), random.randint(1, 28), random.randint(1985, 2000)
    rec = {"email": f"{username}@gmail.com", "password": password, "status": "started",
           "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    print(f"  account: {first} {last} -> {rec['email']}")

    cdp.goto(WEB)
    time.sleep(6)

    if args.dry_run:
        print("  [dry-run] opened the signup form; not submitting.")
        rec["status"] = "dry-run"
        return rec

    s = wait_state(cdp, {"name"})
    if s != "name":
        rec["status"] = f"failed:{s}"
        rec["error"] = detect_blocked(cdp) or s
        return rec
    cdp.js(type_js("input[name=firstName]", first))
    cdp.js(type_js("input[name=lastName]", last))
    cdp.js(click_text_js("next"))
    time.sleep(3)

    s = wait_state(cdp, {"birthday"})
    if s == "phone":
        rec["status"] = "phone_required"
        rec["note"] = "Finish on the phone (unlock it and complete the number). Device path usually skips this."
        return rec
    if s != "birthday":
        rec["status"] = f"failed:{s}"
        rec["error"] = detect_blocked(cdp) or s
        return rec
    cdp.js(f"""(()=>{{const e=document.querySelector('#month');if(e){{
        const s=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;s.call(e,{month});
        e.dispatchEvent(new Event('change',{{bubbles:true}}));}}return true;}})()""")
    cdp.js(type_js("#day", str(day)))
    cdp.js(type_js("#year", str(year)))
    cdp.js(f"""(()=>{{const e=document.querySelector('#gender');if(e){{
        const s=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;s.call(e,'1');
        e.dispatchEvent(new Event('change',{{bubbles:true}}));}}return true;}})()""")
    cdp.js(click_text_js("next"))
    time.sleep(3)

    s = wait_state(cdp, {"username"})
    if s == "phone":
        rec["status"] = "phone_required"
        return rec
    if s != "username":
        rec["status"] = f"failed:{s}"
        rec["error"] = detect_blocked(cdp) or s
        return rec
    # type a unique username
    cdp.js(type_js("input[name=Username]", username))
    cdp.js(click_text_js("next"))
    time.sleep(2)
    s = wait_state(cdp, {"password", "username"})
    if s == "username":
        # username taken -> regenerate once
        username = gen_username(first, last)
        rec["email"] = f"{username}@gmail.com"
        cdp.js(type_js("input[name=Username]", username))
        cdp.js(click_text_js("next"))
        time.sleep(2)
        s = wait_state(cdp, {"password"})

    if s != "password":
        rec["status"] = f"failed:{s}"
        rec["error"] = detect_blocked(cdp) or s
        return rec
    cdp.js(type_js("input[name=Passwd]", password))
    cdp.js(type_js("input[name=ConfirmPasswd]", password))
    cdp.js(click_text_js("next"))
    time.sleep(4)

    # Try to skip any phone prompt; else finish post screens.
    for _ in range(4):
        s = state(cdp)
        if s == "phone":
            cdp.js(click_text_js("not now"))
            cdp.js(click_text_js("skip"))
            time.sleep(2)
        elif s in ("success", "post"):
            cdp.js(click_text_js("skip"))
            cdp.js(click_text_js("i agree"))
            cdp.js(click_text_js("agree"))
            time.sleep(2)
            if state(cdp) == "success":
                break
        else:
            break

    s = state(cdp)
    if s == "phone":
        rec["status"] = "phone_required"
        rec["note"] = "Google still wants a number. Complete it on the phone, then re-run."
    elif s in ("success", "post"):
        rec["status"] = "created"
    else:
        rec["status"] = f"failed:{s}"
        rec["error"] = detect_blocked(cdp) or s
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="Create Gmail accounts via a real Android phone (ADB + CDP)")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--package", default="com.android.chrome",
                    help="browser package on the phone (default com.android.chrome; try com.kiwibrowser.browser)")
    ap.add_argument("--password", default=None, help="use this password for every account")
    ap.add_argument("--delay", type=float, default=25.0, help="pause between accounts")
    ap.add_argument("--dry-run", action="store_true", help="open the form, create nothing")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    print("=" * 60)
    print("  Gmail via Android phone (ADB + CDP) — trusted-device path")
    print("=" * 60)
    preflight(args.package)

    ws_url = page_ws_url()
    if not ws_url:
        print("✗ no debuggable page on the phone")
        return 1
    cdp = CDP(ws_url)

    results = []
    for i in range(1, args.count + 1):
        print(f"\n--- account {i}/{args.count} ---")
        rec = run_one(cdp, args)
        results.append(rec)
        print(f"  status: {rec.get('status')}")
        if rec.get("status") in ("created", "phone_required") and not args.dry_run:
            save(rec)
        if i < args.count:
            time.sleep(args.delay)

    ok = sum(1 for r in results if r.get("status") == "created")
    print(f"\n{'=' * 60}\n  done: {ok}/{args.count} created\n{'=' * 60}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
