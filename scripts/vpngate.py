#!/usr/bin/env python3
"""VPN Gate relay manager: free, no signup, ~100 relays = IP diversity for Cloudflare-gated signups.

  vpngate.py up [--country JP] [--index N]   full-tunnel to the Nth fastest relay
  vpngate.py down | status

System-wide route, so camoufox geoip=True follows the new exit IP automatically.
ponytail: volunteer relays (logged, untrusted) - signup farming only, never real creds.
Not for Google reCAPTCHA audio (verified refused even on residential relays).
"""
import argparse, base64, csv, io, json, os, re, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

API = "http://www.vpngate.net/api/iphone/"
PID = Path.home() / ".cache" / "vpngate.pid"

# The relay list must be fetched through a working egress on hosts whose direct
# path to vpngate.net is intercepted. Measured 2026-10-19 on the farm box:
# direct -> HTTP 200 but a 636-byte ASP.NET form (an interception page); through
# the WARP proxy -> the real 1.3 MB CSV. Without this the farm printed
# "no relays" and could never rotate IPs, which is the one thing THK's
# per-IP signup cap requires.
API_PROXY_ENV = "VPNGATE_API_PROXY"
# The farm box's WARP wireproxy (documented port). Auto-used when the env var is
# unset and something is listening there, so the farm needs no extra config.
WARP_PROXY = "http://127.0.0.1:25344"


def _tunnel_up() -> bool:
    """True while a relay tunnel is live (up() writes its pid on connect)."""
    try:
        return bool(PID.read_text().strip())
    except OSError:
        return False


def _api_proxy() -> str | None:
    """Explicit VPNGATE_API_PROXY, else a locally listening WARP proxy, else None."""
    if os.environ.get(API_PROXY_ENV):
        return os.environ[API_PROXY_ENV]
    # A live tunnel IS the egress: routing the API fetch through the WARP proxy
    # then stalls (measured 2026-10-19: the farm's rotating chunk timed out in
    # getresponse() and killed the batch). Go direct instead -- the tunnel
    # carries it, and the relay's own egress reaches vpngate.net.
    if _tunnel_up():
        return None
    try:
        with socket.create_connection(("127.0.0.1", 25344), timeout=0.5):
            return WARP_PROXY
    except OSError:
        return None


def _api_opener():
    proxy = _api_proxy()
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}))


# One fetch per country per process. A rotating batch calls up() per chunk;
# re-fetching each time is both slow and fragile (see _api_proxy above).
_RELAY_CACHE: dict = {}


def relays(country=None):
    key = (country or "").upper()
    if key in _RELAY_CACHE:
        return _RELAY_CACHE[key]
    raw = _api_opener().open(API, timeout=30).read().decode("utf-8", "ignore")
    rows = csv.DictReader(io.StringIO("\n".join(l for l in raw.splitlines()
                                                if not l.startswith("*")).lstrip("#")))
    out = [r for r in rows if r.get("OpenVPN_ConfigData_Base64")
           and (not country or r["CountryShort"].upper() == country.upper())]
    out = sorted(out, key=lambda r: -int(r["Speed"] or 0))
    _RELAY_CACHE[key] = out
    return out


# Several echo services: one being rate-limited on a shared egress must not be
# read as "the tunnel never came up" (measured: api.ipify.org answered
# "Too Many Requests" from the farm box while ifconfig.me answered fine).
_IP_ECHOES = ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com")


def exit_ip():
    for url in _IP_ECHOES:
        try:
            ip = urllib.request.urlopen(url, timeout=10).read().decode().strip()
        except Exception:
            continue
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
            return ip
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
        # untrusted volunteer config runs as root: drop anything that can execute or load code
        cfg = "\n".join(l for l in cfg.splitlines()
                        if not re.match(r"\s*(script-security|up|down|route-up|route-pre-down|ipchange|client-connect|tls-verify|auth-user-pass-verify|learn-address|plugin|setenv|push-peer-info|management|ifconfig-noexec|dev-node|cd|chroot|daemon|log|log-append|writepid)\b", l, re.I))
        auth = Path(tempfile.mkstemp()[1]); auth.write_text("vpn\nvpn\n")
        f = Path(tempfile.mkstemp(suffix=".ovpn")[1]); f.write_text(cfg)
        PID.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["sudo", "-n", "openvpn", "--config", str(f), "--auth-user-pass", str(auth),
                        "--script-security", "0", "--daemon", "--writepid", str(PID), "--connect-timeout", "10",
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
