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
from pathlib import Path
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


def has(cmd: str) -> bool:
    import shutil
    return shutil.which(cmd) is not None


def waydroid_installed() -> bool:
    return has("waydroid")


def find_android_emulator() -> str | None:
    import shutil
    e = shutil.which("emulator")
    if e:
        return e
    sdk = Path.home() / "Android" / "Sdk" / "emulator" / "emulator"
    if sdk.exists():
        return str(sdk)
    return None


def cmd_emulator() -> int:
    """Detect or start an Android emulator (Android Studio AVD or Waydroid)."""
    print(col("bold", "\n  Android Emulator (Android Studio AVD / Waydroid)\n"))
    print(col("dim", "  Note: Both emulators and real phones communicate via ADB."))
    print(col("dim", "  Once running, ADB detects it automatically.\n"))

    devs = [d for d in devices() if "emulator" in d or "192.168." in d or "127.0.0.1" in d]
    if devs:
        print(col("green", f"  ✅ Emulator already running: {devs[0]}"))
        print(col("dim", "     Ready to run: kancahub gmail adb\n"))
        return 0

    import os
    if not os.path.exists("/dev/kvm"):
        print(col("yellow", "  ⚠ /dev/kvm missing — hardware virtualization required for smooth emulation."))

    emu_bin = find_android_emulator()
    if emu_bin:
        try:
            r = subprocess.run([emu_bin, "-list-avds"], capture_output=True, text=True, timeout=10)
            avds = [l.strip() for l in r.stdout.splitlines() if l.strip()]
            if avds:
                print(col("green", f"  Found Android Studio AVD(s): {', '.join(avds)}"))
                print(col("cyan", f"  Starting emulator: {avds[0]} in background…"))
                subprocess.Popen([emu_bin, "-avd", avds[0], "-no-snapshot-load"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(col("dim", "  Waiting for emulator to appear in ADB…"))
                import time as _t
                for _ in range(15):
                    _t.sleep(2)
                    if devices():
                        print(col("green", f"  ✅ Emulator started: {devices()[0]}"))
                        return 0
                print(col("yellow", "  Emulator is launching. Check 'kancahub adb status' in a moment."))
                return 0
        except Exception:
            pass

    if waydroid_installed():
        print("  Waydroid is installed. Starting session…")
        subprocess.run(["waydroid", "session", "start"], check=False)
        subprocess.run(["adb", "connect", "192.168.240.112:5555"], check=False, timeout=15)
        if devices():
            print(col("green", f"\n  ✅ Waydroid ready: {devices()[0]}"))
            return 0

    print(col("yellow", "  No emulator actively running. Two easy ways:"))
    print(col("bold", "    1. Android Studio:"))
    print(col("dim", "       Open Android Studio → Device Manager → Start your Virtual Device (AVD)."))
    print(col("dim", "       (Choose an image with 'Google Play' for best Google account compatibility)"))
    print(col("bold", "    2. Native Linux Waydroid (Fedora):"))
    print(col("dim", "       sudo dnf install -y waydroid"))
    print(col("dim", "       waydroid init && waydroid session start"))
    print(col("dim", "\n  Once running, it connects to ADB automatically (emulator-5554)."))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="KancaHub Android/ADB helper (trusted-device Google signups)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sub.add_parser("devices")
    sub.add_parser("usb")
    sub.add_parser("phone", help="connect physical Android phone via USB/Wi-Fi")
    sub.add_parser("setup")
    sub.add_parser("emulator", help="install/run an Android emulator (Android Studio AVD / Waydroid)")
    c = sub.add_parser("connect", help="connect over Wi-Fi or an emulator")
    c.add_argument("addr", help="phone IP or IP:port (use 192.168.240.112:5555 for Waydroid)")
    a = ap.parse_args()

    if a.cmd in (None, "status"):
        rc, ver = adb("version")
        print(col("bold", "\n  Android device status\n"))
        print(f"  adb            : {col('green','installed') if rc == 0 else col('red','missing')}")
        _emul = waydroid_installed() or find_android_emulator() is not None
        print(f"  emulator       : {col('green','available') if _emul else col('dim','not configured (kancahub adb emulator)')}")
        devs = devices()
        if devs:
            for d in devs:
                print(f"  device         : {col('green', d)}")
            print(col("green", "\n  ✅ A device is connected — you can run: kancahub gmail adb"))
        else:
            print(f"  device         : {col('yellow','none connected')}")
            print(col("dim", "\n  Choose an option:"))
            print(col("bold", "    1. Physical Phone (Best - skips Google phone verification)"))
            print(col("dim", "       kancahub adb phone            (USB/Wi-Fi setup)"))
            print(col("bold", "    2. Android Studio / Emulator (No phone needed)"))
            print(col("dim", "       kancahub adb emulator         (AVD / Waydroid)"))
        print()
        return 0

    if a.cmd == "emulator":
        return cmd_emulator()

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

    if a.cmd in ("usb", "phone"):
        print(col("bold", "\n  Connect over USB / Wi-Fi\n"))
        print("  1. On the phone: Settings → About phone → tap 'Build number' 7×")
        print("  2. Settings → Developer options → enable 'USB debugging'")
        print("  3. Plug the phone into this computer with a data cable")
        print("  4. On the phone, tap 'Allow' on the 'Allow USB debugging?' prompt")
        print("  5. Run: kancahub adb status   (should now show the device)")
        print(col("dim", "\n  Tip for Wi-Fi: after plugging in once, run 'kancahub adb setup'"))
        print(col("dim", "  to switch to wireless mode, then you can unplug the cable."))
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
