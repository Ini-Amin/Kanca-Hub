#!/usr/bin/env python3
"""
mobile_rotate.py — rotate the egress IP of a tethered Android phone (mobile carrier).

Why: datacenter / WARP / campus IPs are rejected by GitHub & TokenHarbor, but a REAL
mobile-carrier IP is accepted (TokenHarbor proved this live). This helper rotates the
phone's carrier IP on demand and reports the new external IP, so farm commands can
retry with a fresh mobile IP when they get blocked.

How it works (Android via ADB — needs "USB debugging", which you already have):
  1. Detect the device (adb devices).
  2. Toggle airplane mode:  adb shell cmd connectivity airplane-mode enable/disable
     (this forces the modem to re-register → the carrier usually assigns a NEW IP).
     Falls back to `svc data disable/enable` if the airplane command is unavailable.
  3. Wait for the network to come back, then read the new external IP.

NOTE: only rotates when the laptop's egress IS the phone (USB tether / phone hotspot on
mobile data). If the phone is sharing Wi-Fi, the IP will not change (it is the Wi-Fi's).

Usage:
  python3 scripts/mobile_rotate.py --status          # show current egress + phone
  python3 scripts/mobile_rotate.py --rotate          # rotate once, print old/new IP
  python3 scripts/mobile_rotate.py --rotate --wait 25
  python3 scripts/mobile_rotate.py --rotate --rotate-until 182.2.   # keep rotating until prefix
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import urllib.request


def _adb(*args: str, serial: str | None = None, timeout: int = 20) -> str:
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return f"ERR {e}"


def first_device() -> str | None:
    for line in _adb("devices").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def get_ip(timeout: float = 10.0) -> str | None:
    for url in ("https://api.ipify.org", "https://icanhazip.com"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                ip = r.read().decode().strip()
            if ip:
                return ip
        except Exception:  # noqa: BLE001
            continue
    return None


def airplane(serial: str, enable: bool) -> bool:
    state = "enable" if enable else "disable"
    out = _adb("shell", "cmd", "connectivity", "airplane-mode", state, serial=serial)
    if "ERR" in out or "Exception" in out or "Permission" in out:
        # Fallback: some phones only honor svc data toggling.
        _adb("shell", "svc", "data", "disable" if enable else "enable", serial=serial)
        _adb("shell", "settings", "put", "global", "airplane_mode_on", "1" if enable else "0", serial=serial)
        return True
    return True



def _setting(serial: str, name: str) -> str:
    return _adb("shell", "settings", "get", "global", name, serial=serial).strip()


def classify_phone(*, airplane: str, mobile_data: str, sim: str, data_reg_state: str,
                   default_transport: str) -> tuple[str, str]:
    """(verdict, advice) from raw phone facts. Pure - no I/O.

    verdict: ok | wifi | no_data | no_sim | airplane | data_off | unknown
    `no_data` = SIM attached but the data bearer is down (typically an exhausted quota).
    """
    if airplane == "1":
        return "airplane", "airplane mode is ON - turn it off."
    if sim and "ABSENT" in sim.upper():
        return "no_sim", "no SIM detected."
    if mobile_data == "0":
        return "data_off", "mobile data is switched OFF - turn it on."
    if default_transport == "CELLULAR":
        return "ok", "phone is on mobile data: the carrier IP is the egress."
    if data_reg_state in ("1", "-1") or data_reg_state.startswith("OUT_OF_SERVICE"):
        return "no_data", ("mobile data has no service (often an empty data quota) and the phone is on "
                           "Wi-Fi. Rotating will NOT change the IP. Top up / fix data first.")
    if default_transport == "WIFI":
        return "wifi", "phone is using Wi-Fi, not mobile data - turn Wi-Fi off so the carrier IP is used."
    return "unknown", "could not tell which network the phone uses."


def phone_state(serial: str) -> dict:
    """Read-only probe of the phone's connectivity. Never changes any setting."""
    conn = _adb("shell", "dumpsys", "connectivity", serial=serial, timeout=30)
    m = re.search(r"Active default network:\s*(\d+)", conn)
    transport = "NONE"
    if m:
        # the NetworkAgentInfo line for that network id names its type: ni{WIFI ...} / ni{MOBILE ...}
        n = re.search(r"NetworkAgentInfo\{network\{%s\}.*?ni\{([A-Z_]+)" % m.group(1), conn)
        kind = n.group(1) if n else ""
        transport = {"WIFI": "WIFI", "MOBILE": "CELLULAR", "ETHERNET": "ETHERNET"}.get(kind, kind or "UNKNOWN")
    reg = _adb("shell", "dumpsys", "telephony.registry", serial=serial, timeout=30)
    d = re.search(r"mDataRegState=(-?\d+)", reg)
    facts = dict(
        airplane=_setting(serial, "airplane_mode_on"),
        mobile_data=_setting(serial, "mobile_data"),
        sim=_adb("shell", "getprop", "gsm.sim.state", serial=serial).split(",")[0].strip(),
        data_reg_state=d.group(1) if d else "",
        default_transport=transport,
    )
    verdict, advice = classify_phone(**facts)
    return {**facts, "verdict": verdict, "advice": advice,
            "operator": _adb("shell", "getprop", "gsm.operator.alpha", serial=serial).strip(" ,")}


def rotate(serial: str, wait: float = 20.0, verbose: bool = True) -> str | None:
    """Rotate the mobile IP once; return the new IP (or None)."""
    before = get_ip()
    if verbose:
        print(f"  [mobile] IP before: {before or '?'}")
    airplane(serial, True)
    time.sleep(max(3.0, wait * 0.25))
    airplane(serial, False)
    # wait for connectivity to return
    deadline = time.time() + wait
    after = None
    while time.time() < deadline:
        time.sleep(2)
        after = get_ip()
        if after:
            break
    if verbose:
        print(f"  [mobile] IP after : {after or '?'}"
              + ("  (unchanged — is the phone on Wi-Fi instead of mobile data?)"
                 if after and after == before else ""))
    return after


def main() -> int:
    ap = argparse.ArgumentParser(description="Rotate a tethered Android phone's mobile IP")
    ap.add_argument("--serial", default=None, help="adb serial (default: first device)")
    ap.add_argument("--rotate", action="store_true", help="rotate the carrier IP once")
    ap.add_argument("--status", action="store_true", help="show device + current egress IP")
    ap.add_argument("--wait", type=float, default=20.0, help="max seconds to wait for the new IP")
    ap.add_argument("--tries", type=int, default=1, help="number of rotations (default 1)")
    ap.add_argument("--rotate-until", default=None, help="rotate until the new IP startswith this prefix")
    ap.add_argument("--max-rotates", type=int, default=5, help="cap for --rotate-until")
    ap.add_argument("--force", action="store_true", help="rotate even if the phone is not on mobile data")
    args = ap.parse_args()

    serial = args.serial or first_device()
    if not serial:
        print("✗ no Android device via adb. Connect the phone (USB debugging) first.", file=sys.stderr)
        return 2

    if args.status or not (args.rotate or args.rotate_until):
        st = phone_state(serial)
        mark = {"ok": "OK ", "wifi": "!! ", "no_data": "XX ", "data_off": "XX ", "airplane": "XX ",
                "no_sim": "XX "}.get(st["verdict"], "?? ")
        print(f"  device    : {serial}   operator: {st['operator'] or '?'}   SIM: {st['sim'] or '?'}")
        print(f"  egress IP : {get_ip() or '?'}")
        print(f"  phone net : {st['default_transport']}  (data service state {st['data_reg_state'] or '?'}, "
              f"mobile_data={st['mobile_data']}, airplane={st['airplane']})")
        print(f"  verdict   : {mark}{st['verdict']} - {st['advice']}")
        return 0 if st["verdict"] == "ok" else 3

    if not args.force:
        st = phone_state(serial)
        if st["verdict"] != "ok":
            print(f"  [mobile] not rotating: {st['advice']}\n"
                  f"           (override with --force)", file=sys.stderr)
            return 3

    if args.rotate_until:
        for i in range(args.max_rotates):
            ip = rotate(serial, wait=args.wait)
            if ip and ip.startswith(args.rotate_until):
                print(f"  [mobile] ✓ got desired prefix: {ip}")
                return 0
            print(f"  [mobile] not matching {args.rotate_until!r} yet ({ip}); rotating again…")
        print(f"  [mobile] ✗ never matched {args.rotate_until!r} after {args.max_rotates} tries")
        return 1

    for _ in range(max(1, args.tries)):
        rotate(serial, wait=args.wait)
    return 0


if __name__ == "__main__":
    sys.exit(main())
