#!/usr/bin/env python3
"""IPLoop Node Host Manager.
Deploys and manages the ultronloop2026/iploop-node binary on remote boxes (LightVela / ASUS).

Usage:
  iploop_host.py login                   # login & update cached JWT token
  iploop_host.py balance                 # check earned balance & proxy usage stats
  iploop_host.py deploy [host]           # deploy binary + systemd user service (default: lightvela)
  iploop_host.py status [host]           # check remote service status & logs
  iploop_host.py stop [host]             # stop remote node
"""

import argparse, json, os, subprocess, sys, urllib.request
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "auto-freecf"
ACCOUNT_FILE = CONFIG_DIR / "iploop_account.json"
TOKEN_CACHE = Path.home() / ".cache" / "auto-freecf" / "iploop_token.txt"
API_BASE = "https://api.iploop.io/api/v1"

def load_account():
    if not ACCOUNT_FILE.exists():
        raise FileNotFoundError(f"Missing account file: {ACCOUNT_FILE}")
    return json.loads(ACCOUNT_FILE.read_text())

def get_token(force_refresh=False):
    if not force_refresh and TOKEN_CACHE.exists():
        tok = TOKEN_CACHE.read_text().strip()
        if tok:
            return tok
    acc = load_account()
    data = json.dumps({"email": acc["email"], "password": acc["password"]}).encode()
    req = urllib.request.Request(f"{API_BASE}/auth/login", data=data, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode())
        tok = res["token"]
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(tok)
        return tok

def get_balance():
    tok = get_token()
    req = urllib.request.Request(f"{API_BASE}/earn/balance", headers={"Authorization": f"Bearer {tok}", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        bal = json.loads(resp.read().decode())
    ureq = urllib.request.Request(f"{API_BASE}/usage", headers={"Authorization": f"Bearer {tok}", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(ureq, timeout=15) as uresp:
        usage = json.loads(uresp.read().decode())
    return {"balance": bal, "usage": usage}

def deploy_remote(host="lightvela"):
    tok = get_token()
    local_bin = Path("/tmp/iploop-node")
    if not local_bin.exists():
        raise FileNotFoundError("Local binary /tmp/iploop-node missing")

    print(f"[*] Copying iploop-node to {host}:~/bin/...")
    subprocess.run(["ssh", host, "mkdir -p ~/bin ~/.config/systemd/user"], check=True)
    subprocess.run(["scp", "-q", str(local_bin), f"{host}:~/bin/iploop-node"], check=True)
    subprocess.run(["ssh", host, "chmod +x ~/bin/iploop-node"], check=True)

    service_unit = f"""[Unit]
Description=IPLoop Bandwidth Sharing Node
After=network.target

[Service]
Type=simple
ExecStart=%h/bin/iploop-node -token {tok} -gateway wss://gateway.iploop.io/ws
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""
    print(f"[*] Installing systemd service on {host}...")
    proc = subprocess.run(["ssh", host, "cat > ~/.config/systemd/user/iploop-node.service"], input=service_unit.encode(), check=True)
    subprocess.run(["ssh", host, "systemctl --user daemon-reload && systemctl --user enable --now iploop-node.service"], check=True)
    print(f"[✓] iploop-node deployed and started on {host}!")

def status_remote(host="lightvela"):
    print(f"=== Service Status on {host} ===")
    res = subprocess.run(["ssh", host, "systemctl --user status iploop-node.service --no-pager | head -n 15"], stdout=subprocess.PIPE, text=True)
    print(res.stdout)
    print("=== Journal Logs ===")
    res2 = subprocess.run(["ssh", host, "journalctl --user -u iploop-node.service -n 10 --no-pager"], stdout=subprocess.PIPE, text=True)
    print(res2.stdout)
    try:
        stats = get_balance()
        print("=== Account Earnings & Quota ===")
        print(json.dumps(stats, indent=2))
    except Exception as e:
        print(f"Failed to fetch account balance: {e}")

def stop_remote(host="lightvela"):
    subprocess.run(["ssh", host, "systemctl --user stop iploop-node.service"], check=True)
    print(f"[✓] Stopped iploop-node on {host}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IPLoop Node Host Manager")
    parser.add_argument("action", choices=["login", "balance", "deploy", "status", "stop"])
    parser.add_argument("host", nargs="?", default="lightvela", help="remote host (default: lightvela)")
    args = parser.parse_args()

    if args.action == "login":
        tok = get_token(force_refresh=True)
        print("Token refreshed:", tok[:30] + "...")
    elif args.action == "balance":
        print(json.dumps(get_balance(), indent=2))
    elif args.action == "deploy":
        deploy_remote(args.host)
        status_remote(args.host)
    elif args.action == "status":
        status_remote(args.host)
    elif args.action == "stop":
        stop_remote(args.host)
