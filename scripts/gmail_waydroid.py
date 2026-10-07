#!/usr/bin/env python3
"""
gmail_waydroid.py — drive a Gmail signup on Waydroid (or any Android) via Kiwi CDP.

Why Waydroid: it is a FRESH Android 13 device with Google Play Services, ADB input works
(no MIUI block), and it shares the host's network (so rotating the host/mobile IP rotates it).
Unlike the SDK emulator (which SEGV-crashes on new Fedora kernels), Waydroid is an LXC
container and runs.

Pipeline (verified live to the final gate):
  name -> birthday -> username -> password -> Google's device verification (mophoneverification).

CRITICAL TECHNIQUE: Google's SPA only accepts **real CDP mouse events** for its Material
dropdowns/buttons — JS `.click()` is ignored. This module dispatches
Input.dispatchMouseEvent (mousePressed + mouseReleased) for every click, which is what makes
the birthday month/gender dropdowns actually work.

Setup (once):
  sudo dnf install waydroid
  # /usr/share/waydroid-extra/channels.cfg: system_channel=https://ota.waydro.id/system,
  #   vendor_channel=.../vendor, rom_type=lineage, system_type=GAPPS
  sudo waydroid init
  XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 waydroid session start
  adb connect <waydroid-ip>:5555         # push adbkey to authorize
  # install Kiwi x64 + TAP its "Continue" onboarding (this exposes chrome_devtools_remote)
  adb forward tcp:9222 localabstract:chrome_devtools_remote

Usage:
  python3 scripts/gmail_waydroid.py --cdp http://127.0.0.1:9222 --first Victor --last Hayes
"""
from __future__ import annotations

import argparse
import json
import random
import string
import time
import urllib.request
from pathlib import Path

try:
    from websocket import create_connection
except Exception:  # noqa: BLE001
    create_connection = None

DEFAULT_MD = Path.home() / "gmail_accounts.md"


def _pages(cdp: str) -> list[dict]:
    return [p for p in json.loads(urllib.request.urlopen(cdp.rstrip("/") + "/json", timeout=8).read())
            if p.get("type") == "page"]


class CDP:
    def __init__(self, ws_url: str):
        self.c = create_connection(ws_url, timeout=25, suppress_origin=True)
        self._id = 0
        self.cmd("Page.enable")
        self.cmd("Runtime.enable")

    def cmd(self, method: str, params: dict | None = None):
        self._id += 1
        mid = self._id
        self.c.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        self.c.settimeout(25)
        while True:
            try:
                r = json.loads(self.c.recv())
            except Exception:  # noqa: BLE001
                return {}
            if r.get("id") == mid:
                return r.get("result", {})

    def js(self, expr: str):
        r = self.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return (r or {}).get("result", {}).get("value")

    def click(self, x: int, y: int):
        self.cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        self.cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})

    def rect(self, sel: str):
        r = self.js(f"""(()=>{{const d=document.querySelector({json.dumps(sel)});if(!d)return null;d.scrollIntoView({{block:'center'}});const x=d.getBoundingClientRect();return JSON.stringify({{x:Math.round(x.x+x.width/2),y:Math.round(x.y+x.height/2)}});}})()""")
        return json.loads(r) if r else None

    def click_label(self, label: str) -> bool:
        r = self.js(f"""(()=>{{const b=[...document.querySelectorAll('button,div[jsname=LgbsSe],div[role=button]')].find(x=>(x.innerText||'').trim()==={json.dumps(label)});if(!b)return null;b.scrollIntoView({{block:'center'}});const x=b.getBoundingClientRect();return JSON.stringify({{x:Math.round(x.x+x.width/2),y:Math.round(x.y+x.height/2)}});}})()""")
        if not r:
            return False
        p = json.loads(r)
        self.click(p["x"], p["y"])
        return True

    def setf(self, sel: str, val: str):
        return self.js(f"""(()=>{{const e=document.querySelector({json.dumps(sel)});if(!e)return 'no';e.focus();const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(e,{json.dumps(str(val))});e.dispatchEvent(new InputEvent('input',{{bubbles:true,data:{json.dumps(str(val))},inputType:'insertText'}}));e.dispatchEvent(new Event('change',{{bubbles:true}}));return 'ok';}})()""")

    def state(self) -> str:
        return self.js("""(()=>{const u=location.href;if(/myaccount|mail.google/.test(u))return 'success';if(/mophoneverification|device check|scan the qr/i.test(u+(document.body.innerText||'')))return 'device_verify';if(document.querySelector('input[name=Passwd]'))return 'password';if(document.querySelectorAll('input[name=usernameRadio]').length)return 'username_radio';if(document.querySelector('input[name=Username]'))return 'username';if(document.querySelector('input[name=day]')||document.querySelector('input#day'))return 'birthday';if(document.querySelector('input[name=firstName]'))return 'name';return 'other';})()""")

    def select_material(self, sel: str, text: str) -> bool:
        """Open a Material dropdown with a REAL mouse click, then click the option."""
        p = self.rect(sel)
        if not p:
            return False
        self.click(p["x"], p["y"])
        time.sleep(1.3)
        r = self.js(f"""(()=>{{const o=[...document.querySelectorAll('div[role=option],li[role=option]')].filter(x=>x.offsetParent!==null).find(x=>(x.innerText||'').trim()==={json.dumps(text)});if(!o)return null;o.scrollIntoView({{block:'center'}});const x=o.getBoundingClientRect();return JSON.stringify({{x:Math.round(x.x+x.width/2),y:Math.round(x.y+x.height/2)}});}})()""")
        if not r:
            return False
        p = json.loads(r)
        self.click(p["x"], p["y"])
        time.sleep(0.8)
        return True


def gen_username(first: str, last: str) -> str:
    base = (first + last).lower()[:8]
    return base + "".join(random.choices(string.digits, k=5))


def save_markdown(rec: dict, path: str) -> None:
    """Append one created account to a Markdown table (creates the file/table once)."""
    import os
    from datetime import datetime, timezone
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Gmail accounts (grok/gmail farm)\n\n"
        "| # | email | password | status | created (UTC) | source |\n"
        "|---|-------|----------|--------|---------------|--------|\n"
    )
    row_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    if not p.exists():
        p.write_text(header, encoding="utf-8")
    # count existing data rows for the index
    n = sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("| ") and "---" not in ln and not ln.startswith("| #"))
    row = f"| {n+1} | {rec.get('email','')} | {rec.get('password','')} | {rec.get('final_state','')} | {row_ts} | waydroid/kiwi |\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(row)


def run(cdp_url: str, first: str, last: str, password: str | None, month: str, day: int, year: int) -> dict:
    pg = _pages(cdp_url)
    ws = ([p["webSocketDebuggerUrl"] for p in pg if "signup" in (p.get("url") or "")]
          or [pg[0]["webSocketDebuggerUrl"]])[0]
    d = CDP(ws)
    rec: dict = {"first": first, "last": last}
    d.cmd("Page.navigate", {"url": "https://accounts.google.com/signup"})
    time.sleep(10)

    if d.state() == "name":
        d.setf("input[name=firstName]", first); time.sleep(0.3)
        d.setf("input[name=lastName]", last); time.sleep(0.4)
        d.click_label("Next"); time.sleep(5)
    if d.state() == "birthday":
        d.select_material("#month", month)
        d.setf("input[name=day]", str(day)); d.setf("input#day", str(day)); time.sleep(0.3)
        d.setf("input[name=year]", str(year)); d.setf("input#year", str(year)); time.sleep(0.4)
        d.select_material("#gender", "Male")
        d.click_label("Next"); time.sleep(6)
    if d.state() in ("username", "username_radio"):
        un = gen_username(first, last)
        rec["username"] = un; rec["email"] = un + "@gmail.com"
        # Google's username step has two shapes:
        #  (a) a visible text field  -> type there, click Next
        #  (b) suggested addresses (radio buttons) + 'Create your own Gmail address'
        typed = d.setf("input[name=Username]", un)
        if not typed or d.state() == "username_radio":
            # click a suggestion radio that matches, else pick "Create your own"
            picked = d.js(f"""(()=>{{const rs=[...document.querySelectorAll('input[name=usernameRadio]')].filter(x=>x.value!=='custom');
              const m=rs.find(x=>({json.dumps(un)}).startsWith(x.value.slice(0,6)));
              (m||rs[0])?.click(); return true;}})()""")
            time.sleep(1)
            # if that didn't take, try "Create your own Gmail address" then type
            if d.state() == "username_radio":
                d.click_label("Create your own Gmail address"); time.sleep(1.5)
                d.setf("input[name=Username]", un)
        d.click_label("Next"); time.sleep(5)
    if d.state() == "password":
        pw = password or ("Kx" + "".join(random.choices(string.ascii_letters + string.digits, k=9)) + "#7")
        d.setf("input[name=Passwd]", pw)
        if not d.setf("input[name=PasswdAgain]", pw):
            d.setf("input[name=ConfirmPasswd]", pw)
        rec["password"] = pw
        d.click_label("Next"); time.sleep(8)

    rec["final_state"] = d.state()
    rec["final_url"] = (d.js("location.href") or "")[:120]
    d.c.close() if hasattr(d.c, "close") else None
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="Gmail signup on Waydroid via Kiwi CDP")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    ap.add_argument("--first", default="Victor")
    ap.add_argument("--last", default="Hayes")
    ap.add_argument("--password", default=None)
    ap.add_argument("--month", default="May")
    ap.add_argument("--day", type=int, default=15)
    ap.add_argument("--year", type=int, default=1991)
    ap.add_argument("--md", default=str(DEFAULT_MD), help="Markdown store for created accounts")
    a = ap.parse_args()
    if create_connection is None:
        print("✗ websocket-client not installed")
        return 1
    rec = run(a.cdp, a.first, a.last, a.password, a.month, a.day, a.year)
    print(json.dumps(rec, indent=2))
    if rec.get("email"):
        try:
            save_markdown(rec, a.md)
            print(f"  ✓ stored in {a.md}")
        except Exception as e:  # noqa: BLE001
            print(f"  ! markdown store failed: {e}")
    print("\n  Honest note: Google finishes signup behind a DEVICE VERIFICATION gate "
          "(mophoneverification). Reaching it means the whole form pipeline works; the gate "
          "itself needs a real device/phone attestation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
