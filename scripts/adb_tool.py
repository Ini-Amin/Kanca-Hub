#!/usr/bin/env python3
"""
KancaHub ADB helper — connect an Android phone and use it for Google signups.

Why ADB: a real Android device with Google Play Services is already a
"trusted device" for Google, so the account-creation flow usually SKIPS the
phone-number verification step that blocks a plain desktop browser.

Usage:
  python3 adb_tool.py status                 # is a device connected?
  python3 adb_tool.py connect <ip[:port]>    # connect over Wi-Fi (adb tcpip first)
  python3 adb_tool.py usb                    # what to do for USB
  python3 adb_tool.py devices                # list + browser packages present
  python3 adb_tool.py setup                  # one-time: enable tcpip + show IP
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

C = {"reset": "\x1b[0m", "bold": "\x1b[1m", "green": "\x1b[32m", "red": "\x1b[31m",
     "yellow": "\x1b[33m", "dim": "\x1b[2m", "cyan": "\x1b[36m"}


def col(n: str, t: str) -> str:
    return f"{C.get(n,'')}{t}{C['reset']}"


def adb(*args: str, timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 127, "adb not installed (sudo dnf install -y android-tools)"
    except subprocess.TimeoutExpired:
        return 124, "adb timed out"


def devices() -> list[str]:
    rc, out = adb("devices")
    return [l.split("\t")[0] for l in out.splitlines()[1:] if "\t" in l and l.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="KancaHub Android/ADB helper (trusted-device Google signups)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sub.add_parser("devices")
    sub.add_parser("usb")
    sub.add_parser("setup")
    c = sub.add_parser("connect", help="connect over Wi-Fi")
    c.add_argument("addr", help="phone IP or IP:port (default port 5555)")
    a = ap.parse_args()

    if a.cmd in (None, "status"):
        rc, ver = adb("version")
        print(col("bold", "\n  Android device status\n"))
        print(f"  adb            : {col('green','installed') if rc == 0 else col('red','missing')}")
        devs = devices()
        if devs:
            for d in devs:
                print(f"  device         : {col('green', d)}")
            print(col("green", "\n  ✅ A phone is connected — you can run: kancahub gmail adb"))
        else:
            print(f"  device         : {col('yellow','none connected')}")
            print(col("dim", "\n  To connect a phone:"))
            print(col("dim", "    1) USB: plug in, enable Developer Options + USB debugging,"))
            print(col("dim", "       accept the 'Allow USB debugging' prompt on the phone."))
            print(col("dim", "    2) Wi-Fi: with it on USB, run  kancahub adb setup"))
            print(col("dim", "       then  kancahub adb connect <phone-ip>"))
        print()
        return 0

    if a.cmd == "devices":
        rc, out = adb("devices", "-l")
        print(out)
        devs = devices()
        if devs:
            d = devs[0]
            print(col("bold", "\n  Browsers on the device:"))
            for pkg in ("com.android.chrome", "com.kiwibrowser.browser", "com.brave.browser"):
                rc2, o2 = adb("-s", d, "shell", "pm", "list", "packages", pkg, timeout=15)
                mark = "✅" if "package:" in o2 else "➖"
                print(f"  {mark} {pkg}")
        return 0

    if a.cmd == "usb":
        print(col("bold", "\n  Connect over USB\n"))
        print("  1. On the phone: Settings → About phone → tap 'Build number' 7×")
        print("  2. Settings → Developer options → enable 'USB debugging'")
        print("  3. Plug the phone into this computer with a data cable")
        print("  4. On the phone, tap 'Allow' on the 'Allow USB debugging?' prompt")
        print("  5. Run: kancahub adb status   (should now show the device)")
        return 0

    if a.cmd == "setup":
        devs = devices()
        if not devs:
            print(col("red", "  ✗ connect the phone by USB first (kancahub adb usb)"))
            return 1
        d = devs[0]
        rc, o = adb("-s", d, "tcpip", "5555")
        print(col("dim", f"  {o}"))
        rc, ip = adb("-s", d, "shell", "ip", "route")
        m = re.search(r"src (\d+\.\d+\.\d+\.\d+)", ip)
        phone_ip = m.group(1) if m else "<phone-ip>"
        print(col("green", f"\n  ✅ tcpip mode on. Now UNPLUG USB and run:"))
        print(col("bold", f"     kancahub adb connect {phone_ip}"))
        return 0

    if a.cmd == "connect":
        addr = a.addr if ":" in a.addr else f"{a.addr}:5555"
        rc, o = adb("connect", addr, timeout=25)
        print(o)
        devs = devices()
        if addr in devs or [d for d in devs if addr.split(":")[0] in d]:
            print(col("green", f"\n  ✅ connected to {addr}"))
        else:
            print(col("yellow", "\n  ⚠ not connected — make sure the phone and this computer are on the same Wi-Fi"))
            print(col("dim", "     and you ran 'adb tcpip 5555' while it was on USB."))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
