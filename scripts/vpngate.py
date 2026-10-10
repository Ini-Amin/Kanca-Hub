#!/usr/bin/env python3
"""VPN Gate relay manager: free, no signup, ~100 relays = IP diversity for Cloudflare-gated signups.

  vpngate.py up [--country JP] [--index N]   full-tunnel to the Nth fastest relay
  vpngate.py down | status

System-wide route, so camoufox geoip=True follows the new exit IP automatically.
ponytail: volunteer relays (logged, untrusted) - signup farming only, never real creds.
Not for Google reCAPTCHA audio (verified refused even on residential relays).
"""
import argparse, base64, csv, io, json, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

API = "http://www.vpngate.net/api/iphone/"
PID = Path.home() / ".cache" / "vpngate.pid"


def relays(country=None):
    raw = urllib.request.urlopen(API, timeout=30).read().decode("utf-8", "ignore")
    rows = csv.DictReader(io.StringIO("\n".join(l for l in raw.splitlines()
                                                if not l.startswith("*")).lstrip("#")))
    out = [r for r in rows if r.get("OpenVPN_ConfigData_Base64")
           and (not country or r["CountryShort"].upper() == country.upper())]
    return sorted(out, key=lambda r: -int(r["Speed"] or 0))


def exit_ip():
    try:
        return urllib.request.urlopen("https://api.ipify.org", timeout=10).read().decode()
    except Exception:
        return None


def down():
    if PID.exists():
        subprocess.run(["sudo", "-n", "kill", PID.read_text().strip()])
        PID.unlink()
    print("down; exit", exit_ip())


def up(country, index):
    rs = relays(country)
    if not rs:
        sys.exit("no relays")
    down() if PID.exists() else None
    for r in rs[index:index + 5]:  # relays die often; try a few
        cfg = base64.b64decode(r["OpenVPN_ConfigData_Base64"]).decode()
        auth = Path(tempfile.mkstemp()[1]); auth.write_text("vpn\nvpn\n")
        f = Path(tempfile.mkstemp(suffix=".ovpn")[1]); f.write_text(cfg)
        PID.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["sudo", "-n", "openvpn", "--config", str(f), "--auth-user-pass", str(auth),
                        "--daemon", "--writepid", str(PID), "--connect-timeout", "10",
                        "--connect-retry-max", "1", "--data-ciphers", "AES-128-CBC:AES-256-GCM:AES-128-GCM:AES-256-CBC"])
        for _ in range(15):
            time.sleep(1)
            ip = exit_ip()
            if ip and ip == r["IP"]:
                print(f"up {r['CountryShort']} {r['IP']} exit {ip}"); return 0
        print("relay failed", r["IP"]); down()
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["up", "down", "status"])
    ap.add_argument("--country"); ap.add_argument("--index", type=int, default=0)
    a = ap.parse_args()
    if a.cmd == "up": sys.exit(up(a.country, a.index))
    elif a.cmd == "down": down()
    else: print("pid", PID.read_text().strip() if PID.exists() else None, "exit", exit_ip())
