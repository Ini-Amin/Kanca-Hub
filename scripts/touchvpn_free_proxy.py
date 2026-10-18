#!/usr/bin/env python3
"""Touch VPN (Pango/Aura) free HTTPS-proxy flow.
Reverse-engineered using REA (analyze-javascript-application) from Touch VPN Chrome extension 5.0.23.

Usage:
  touchvpn_free_proxy.py locations          # list all available regions
  touchvpn_free_proxy.py get [location]     # get credentials and server for location (default: optimal / us)
  touchvpn_free_proxy.py test [location]    # test live egress IP
"""

import json, secrets, sys, urllib.parse, urllib.request

LOGIN_URL = "https://api.pango-cloud.com/user/login"
LOCATIONS_URL = "https://api.pango-cloud.com/user/locations"
PROVIDE_URL = "https://api.pango-cloud.com/user/provide"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def login():
    dev_hash = "TC" + secrets.token_hex(16)
    params = {
        "auth_method": "anonymous",
        "device_id": f"touchvpn_{dev_hash}",
        "device_type": "EXTENSION_CHROME",
        "carrier_id": "touchvpn"
    }
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        LOGIN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        if res.get("result") != "OK":
            raise RuntimeError(f"Login failed: {res}")
        return res["access_token"]

def locations(token=None):
    if not token:
        token = login()
    url = f"{LOCATIONS_URL}?access_token={urllib.parse.quote(token)}&type=proxy"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res.get("locations", [])

def provide(location=None, token=None):
    if not token:
        token = login()
    url = f"{PROVIDE_URL}?access_token={urllib.parse.quote(token)}&type=proxy"
    if location:
        url += f"&location={urllib.parse.quote(location)}"
    req = urllib.request.Request(url, data=b"", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        if res.get("result") != "OK":
            raise RuntimeError(f"Provide failed: {res}")
        return res

def test_egress(location=None):
    try:
        from curl_cffi import requests as cr
    except ImportError:
        import subprocess
        data = provide(location)
        server = data["servers"][0]
        proxy = f"https://{data['username']}:{data['password']}@{server['address']}:{server['port']}"
        cmd = ["curl", "-s", "-x", proxy, "https://api.ipify.org?format=json"]
        out = subprocess.check_output(cmd, timeout=15).decode()
        return json.loads(out).get("ip")

    data = provide(location)
    server = data["servers"][0]
    proxy = f"https://{data['username']}:{data['password']}@{server['address']}:{server['port']}"
    r = cr.get("https://api.ipify.org?format=json", proxy=proxy, impersonate="chrome131", timeout=15)
    return r.json().get("ip")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "locations"
    if cmd == "locations":
        locs = locations()
        print(f"Total locations: {len(locs)}")
        for l in sorted(locs, key=lambda x: x.get("name", "")):
            lbl = l.get("labels", {})
            print(f"  {l.get('name'):<25} ({lbl.get('country', '')} - {lbl.get('city', '')})")
    elif cmd == "get":
        loc = sys.argv[2] if len(sys.argv) > 2 else None
        res = provide(loc)
        print(json.dumps(res, indent=2))
    elif cmd == "test":
        loc = sys.argv[2] if len(sys.argv) > 2 else None
        ip = test_egress(loc)
        print(f"Egress IP ({loc or 'default'}): {ip}")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
