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

# hl=en forces Google's UI to English regardless of the phone's locale; without
# it a non-English phone renders localised buttons ("Berikutnya") that the
# English-text click helpers below would never match. See click_text_js labels.
WEB = ("https://accounts.google.com/signup/v2/createaccount"
       "?flowName=GlifWebSignIn&flowEntry=SignUp&hl=en")
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


def _devtools_socket_present(serial: str, sock: str) -> bool:
    """True if the browser's devtools abstract socket exists on the device.

    A stale/old Chrome process may be running WITHOUT this socket, so a plain
    launch can leave the adb forward pointing at nothing. Checking the socket
    lets us force-stop + relaunch instead of silently failing.
    """
    out = adb("-s", serial, "shell", "cat /proc/net/unix", timeout=15)
    return sock in out


def preflight(pkg: str) -> str:
    if not devices():
        print("✗ No Android device connected.")
        print("  Connect a phone first:  kancahub adb usb   (then follow the prompts)")
        print("  Or over Wi-Fi:          kancahub adb setup  ->  kancahub adb connect <ip>")
        sys.exit(2)
    serial = devices()[0]
    adb("forward", "--remove-all", timeout=10)
    sock = PKG_SOCKETS.get(pkg, "chrome_devtools_remote")

    # Launch the browser and make sure its devtools socket is actually up. A
    # device that already had Chrome running stale can leave us with no socket,
    # so we force-stop + relaunch a few times before giving up.
    launched = False
    for attempt in range(1, 4):
        if attempt > 1 or not _devtools_socket_present(serial, sock):
            adb("-s", serial, "shell", "am", "force-stop", pkg, timeout=15)
            time.sleep(1)
        adb("-s", serial, "shell", "monkey", "-p", pkg, "-c",
            "android.intent.category.LAUNCHER", "1", timeout=20)
        # poll for the socket for ~10s
        for _ in range(5):
            time.sleep(2)
            if _devtools_socket_present(serial, sock):
                launched = True
                break
        if launched:
            break
        print(f"  (devtools socket not up yet; retry {attempt}/3)")
    if not launched:
        print("✗ The phone's browser is not exposing a devtools socket.")
        print(f"  Open {pkg} on the phone once, then re-run.")
        sys.exit(2)

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
        # Modern Chrome rejects any WebSocket whose request carries an Origin
        # header unless it was launched with --remote-allow-origins. websocket-
        # client sends Origin by default, so the handshake 403s on a phone's
        # Chrome 100+. Suppressing the Origin header makes Chrome accept it.
        # Fall back to the old call if the installed websocket-client is too old
        # to know the suppress_origin kwarg.
        try:
            self.ws = create_connection(url, timeout=30, suppress_origin=True)
        except TypeError:
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

    def click_at(self, x: int, y: int):
        """Dispatch a real left-click at viewport coordinates (x, y).

        Google's Material controls open on genuine input events, not on JS
        element.click(); CDP Input mouse events are the reliable trigger.
        """
        for typ, buttons in (("mouseMoved", 0), ("mousePressed", 1), ("mouseReleased", 0)):
            self.send("Input.dispatchMouseEvent",
                      {"type": typ, "x": x, "y": y, "button": "left",
                       "clickCount": 1, "buttons": buttons})


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


# Localised fallbacks so a non-English phone still works even if hl=en is
# ignored. Keyed by the canonical English label used at call sites.
CLICK_FALLBACKS = {
    "next": ["next", "berikutnya", "lanjut", "siguiente", "suivant", "weiter"],
    "skip": ["skip", "lewati", "omitir", "ignorer", "überspringen"],
    "not now": ["not now", "nanti saja", "ahora no", "pas maintenant", "später"],
    "i agree": ["i agree", "saya setuju", "acepto", "j'accepte", "ich stimme zu"],
    "agree": ["agree", "setuju", "aceptar", "accepter", "zustimmen"],
}


def click_text_js(text: str) -> str:
    labels = CLICK_FALLBACKS.get(text.lower().strip(), [text.lower()])
    return f"""
    (()=>{{
      const labels={json.dumps([l.lower() for l in labels])};
      const els=[...document.querySelectorAll('button,a,div[role=button],span')];
      const norm=e=>(e.innerText||'').trim().toLowerCase();
      for(const t of labels){{
        const el=els.find(e=>norm(e)===t) || els.find(e=>norm(e).includes(t));
        if(el){{ el.click(); return true; }}
      }}
      return false;
    }})()"""


def select_material(cdp: "CDP", dropdown_selector: str, value: str) -> str:
    """Robustly pick an option in a Google Material dropdown (jsname=O1htCb).

    Sequence that actually works on a real device:
      1. scroll the control into view,
      2. open it with a REAL mouse event at its centre (JS .click() is unreliable),
      3. click the visible li[role=option] whose data-value matches.

    Returns 'ok' or a short reason ('no-dropdown'/'no-option').
    """
    rect = cdp.js(
        "(sel=>{const e=document.querySelector(sel);if(!e)return null;"
        "e.scrollIntoView({block:'center'});"
        "const b=e.getBoundingClientRect();"
        "return JSON.stringify({x:Math.round(b.x+b.width/2),y:Math.round(b.y+b.height/2)});})"
        f"({json.dumps(dropdown_selector)})")
    if not rect:
        return "no-dropdown"
    pos = json.loads(rect)
    cdp.click_at(pos["x"], pos["y"])
    time.sleep(1.0)
    # click the matching VISIBLE option (li[role=option] with data-value)
    res = cdp.js(
        "(args=>{const [want]=args;"
        "const opts=[...document.querySelectorAll('li[role=option],div[role=option]')]"
        ".filter(o=>o.offsetParent!==null);"
        "const opt=opts.find(o=>(o.getAttribute('data-value')||'')===want)"
        "||opts.find(o=>(o.innerText||'').trim()===want);"
        "if(!opt)return 'no-option';opt.click();return 'ok';})"
        f"({json.dumps([str(value)])})")
    return res or "no-option"


def select_material_js(dropdown_selector: str, value: str) -> str:
    """Select an option in a Google Material dropdown (jsname=O1htCb), not <select>.

    Google's birthday 'Month' and 'Gender' controls are <div jsname="O1htCb">
    custom dropdowns. Writing HTMLSelectElement.prototype.value onto them is a
    no-op, which is why the form kept failing with 'Please enter a month'.
    Correct interaction: click the control to open it, then click the
    div[role=option] whose data-value matches.
    """
    return f"""
    (()=>{{
      const dd=document.querySelector({json.dumps(dropdown_selector)});
      if(!dd) return 'no-dropdown';
      dd.click();
      const want={json.dumps(str(value))};
      const opts=[...document.querySelectorAll('div[role=option],li[role=option]')];
      const opt=opts.find(o=>(o.getAttribute('data-value')||'')===want)
             || opts.find(o=>(o.innerText||'').trim()===want);
      if(!opt) return 'no-option';
      opt.click();
      return 'ok';
    }})()"""


def state(cdp: CDP) -> str:
    return cdp.js("""
    (()=>{
      const vis=s=>{const e=document.querySelector(s);return !!(e&&e.offsetParent!==null);};
      const url=location.href, txt=(document.body&&document.body.innerText||'').toLowerCase();
      if(/myaccount\\.google\\.com|mail\\.google\\.com/.test(url)) return 'success';
      if(/couldn.t create|can.t create|too many|verify it.s you/.test(txt)) return 'blocked';
      if(vis('input[name=firstName]')) return 'name';
      // Username step has two shapes: a plain visible text field, OR a list of
      // suggested addresses (radio buttons) with the text field hidden.
      if(vis('input[name=Username]') || (document.querySelectorAll('input[name=usernameRadio]').length && /create an email address|choose a gmail address|create a gmail address/i.test(txt))) return 'username';
      if(vis('input[name=Passwd]')) return 'password';
      // Birthday/gender screen FIRST. Google renders a stray (but "visible" by
      // offsetParent) input[type=tel] on the birthday page, which previously
      // tripped the phone check below and made every run bail with 'phone'.
      const birthday = vis('#day') || vis('input[name=day]') || vis('#year') || /birthday and gender|enter your birthday/.test(txt);
      if(birthday) return 'birthday';
      // Genuine phone gate: the explicit phone field, or a tel input that is NOT
      // accompanied by the birthday fields.
      if(vis('input#phoneNumberId')) return 'phone';
      if(vis('input[type=tel]') && !birthday) return 'phone';
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
    # Month + Gender are Material dropdowns (custom divs), NOT native selects.
    # Open with a real mouse event and click the matching visible option.
    for sel, val in (("#month", str(month)), ("#gender", "1")):
        res = select_material(cdp, sel, val)
        if res != "ok":
            res = cdp.js(select_material_js(sel, val))
        time.sleep(0.6)
    cdp.js(type_js("#day", str(day)))
    cdp.js(type_js("#year", str(year)))
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
    # Username step: either a plain text field, or a list of Google-suggested
    # addresses (radio buttons) with the field hidden. Handle both.
    def _pick_suggested():
        """Click a suggested usernameRadio (prefer one matching our base)."""
        return cdp.js(
            "(args=>{const [base]=args;"
            "const rs=[...document.querySelectorAll('input[name=usernameRadio]')]"
            ".filter(r=>(r.value||'')!=='custom');"
            "if(!rs.length)return null;"
            "const pick=rs.find(r=>r.value&&base&&base.startsWith(r.value.slice(0,6)))||rs[0];"
            "pick.click();return pick.value;})"
            f"({[username]})")

    if cdp.js("document.querySelectorAll('input[name=usernameRadio]').length"):
        chosen = _pick_suggested()
        cdp.js(click_text_js("next"))
        time.sleep(3)
        s = wait_state(cdp, {"password", "username"})
        if s == "password" and chosen:
            email = f"{chosen}@gmail.com"
            rec["email"] = email
            print(f"  picked suggested address: {email}")
    else:
        # type a unique username
        cdp.js(type_js("input[name=Username]", username))
        cdp.js(click_text_js("next"))
        time.sleep(2)
        s = wait_state(cdp, {"password", "username"})
        if s == "username":
            # suggestions may have appeared, or the name is taken -> handle both
            if cdp.js("document.querySelectorAll('input[name=usernameRadio]').length"):
                chosen = _pick_suggested()
                cdp.js(click_text_js("next"))
                time.sleep(3)
                if chosen:
                    rec["email"] = f"{chosen}@gmail.com"
                s = wait_state(cdp, {"password", "username"})
            if s != "password":
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
    # The confirm field is 'PasswdAgain' on the current flow; some older flows
    # used 'ConfirmPasswd'. Try both so neither variant breaks us.
    confirm = cdp.js(type_js("input[name=PasswdAgain]", password))
    if not confirm:
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
