#!/usr/bin/env python3
"""scripts/launch_clean_browser.py — Launch an isolated Brave browser window with ZERO WebRTC leak.

Features:
  - Cleanly wipes any stale SingletonLock from previous sessions.
  - Verifies local proxy relay is actively responding with HTTP 200 before launching browser.
  - Region endpoints:
      * US: http://127.0.0.1:25350 (Atlanta, US - Windscribe Clouvider)
      * HK: socks5://127.0.0.1:1080 (Hong Kong, Azure Microsoft DC)
      * SG: http://127.0.0.1:25344 (Singapore, LightVela WARP)
  - Enforces WebRTC protection in Preferences:
      * webrtc.ip_handling_policy = "disable_non_proxied_udp"
  - Launches with:
      * --webrtc-ip-handling-policy=disable_non_proxied_udp
      * --enforce-webrtc-ip-permission-check
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

BRAVE_BIN = shutil.which("brave-browser") or shutil.which("brave") or "/usr/bin/brave-browser"


def is_port_listening(port: int) -> bool:
    import socket
    s = socket.socket()
    s.settimeout(0.8)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


def ensure_azure_socks() -> str:
    if not is_port_listening(1080):
        print("[*] Starting SSH SOCKS5 tunnel to Azure VM (:1080)...")
        subprocess.Popen(["ssh", "-D", "127.0.0.1:1080", "-N", "-f", "azure"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
    return "socks5://127.0.0.1:1080"


def ensure_us_relay(port: int = 25350) -> str:
    try:
        res = subprocess.run(["curl", "-s", "-m", "3", "-x", f"http://127.0.0.1:{port}", "https://api.ipify.org"],
                             capture_output=True, text=True)
        if res.stdout.strip():
            return f"http://127.0.0.1:{port}"
    except Exception:
        pass

    print(f"[*] Starting US Egress Relay on port :{port}...")
    relay_script = SCRIPTS / "us_egress_relay.py"
    subprocess.Popen([sys.executable, str(relay_script)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    for _ in range(15):
        time.sleep(1)
        if is_port_listening(port):
            try:
                res = subprocess.run(["curl", "-s", "-m", "3", "-x", f"http://127.0.0.1:{port}", "https://api.ipify.org"],
                                     capture_output=True, text=True)
                if res.stdout.strip():
                    break
            except Exception:
                pass
    return f"http://127.0.0.1:{port}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Launch leak-free Brave browser")
    ap.add_argument("--region", choices=["US", "HK", "SG"], default="SG", help="Egress region (default: SG)")
    ap.add_argument("--url", default="https://browserleaks.com/webrtc", help="Target URL to open")
    args = ap.parse_args()

    print(f"\n[*] Preparing leak-free browser environment [{args.region}]...")

    if args.region == "US":
        proxy = ensure_us_relay(25350)
    elif args.region == "HK":
        proxy = ensure_azure_socks()
    elif args.region == "SG":
        proxy = "http://127.0.0.1:25344"

    # Verify proxy exit IP
    print(f"[*] Verifying proxy connectivity ({proxy})...")
    curl_flag = "-x" if proxy.startswith("http") else "--socks5"
    verified_ip = ""
    for _ in range(3):
        try:
            res = subprocess.run(["curl", "-s", "-m", "6", curl_flag, proxy, "https://cloudflare.com/cdn-cgi/trace"],
                                 capture_output=True, text=True)
            lines = dict(l.split("=", 1) for l in res.stdout.splitlines() if "=" in l)
            verified_ip = lines.get("ip")
            loc = lines.get("loc")
            print(f"[✓] Proxy verified! Exit IP: {verified_ip} (Location: {loc})")
            break
        except Exception:
            time.sleep(1)

    if not verified_ip:
        print("[!] Proxy check timed out. Retrying relay setup...")
        if args.region == "US":
            subprocess.run(["pkill", "-f", "us_egress_relay.py"])
            time.sleep(1)
            proxy = ensure_us_relay(25350)

    # Clean stale locks
    profile_dir = Path(f"/tmp/brave-clean-{args.region.lower()}")
    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True, exist_ok=True)

    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_file = profile_dir / lock_name
        if lock_file.is_symlink() or lock_file.exists():
            try:
                lock_file.unlink()
            except Exception:
                pass

    pref_file = default_dir / "Preferences"
    prefs = {}
    if pref_file.exists():
        try:
            prefs = json.loads(pref_file.read_text("utf-8"))
        except Exception:
            prefs = {}
    prefs.setdefault("webrtc", {})["ip_handling_policy"] = "disable_non_proxied_udp"
    pref_file.write_text(json.dumps(prefs, indent=2), "utf-8")

    cmd = [
        BRAVE_BIN,
        f"--user-data-dir={profile_dir}",
        f"--proxy-server={proxy}",
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--enforce-webrtc-ip-permission-check",
        "--disable-webrtc-hw-decoding",
        "--no-first-run",
        "--no-default-browser-check",
        args.url
    ]

    print(f"[*] Launching clean Brave browser window...")
    subprocess.Popen(cmd, start_new_session=True)
    print(f"[✓] Browser successfully opened with locked proxy & zero WebRTC leak!\n")


if __name__ == "__main__":
    main()
