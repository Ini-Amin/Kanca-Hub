#!/usr/bin/env python3
"""IPLoop Node Runner on Railway VM.
Runs on a host (laptop, LightVela, or ASUS) to spawn throwaway 60-min Railway VMs,
deploy the ultronloop2026/iploop-node binary, and share Railway bandwidth to earn
IPLoop residential proxy credits without using the host's bandwidth.

Usage:
  rail_iploop_node.py once       # spawn/use box, deploy node, verify running
  rail_iploop_node.py daemon     # run continuous rolling rotation (~55m/box)
  rail_iploop_node.py status     # check box expiration, node process, and log
  rail_iploop_node.py stop       # wipe current box and stop node
"""

import argparse, json, os, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import railbox

CONFIG_DIR = Path.home() / ".config" / "auto-freecf"
ACCOUNT_FILE = CONFIG_DIR / "iploop_account.json"
BINARY_LOCAL = ROOT / "assets" / "iploop-node"
if not BINARY_LOCAL.exists():
    BINARY_LOCAL = Path("/tmp/iploop-node")

API_BASE = "https://api.iploop.io/api/v1"

def get_token():
    if not ACCOUNT_FILE.exists():
        raise FileNotFoundError(f"Missing account file: {ACCOUNT_FILE}")
    acc = json.loads(ACCOUNT_FILE.read_text())
    data = json.dumps({"email": acc["email"], "password": acc["password"]}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/auth/login",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())["token"]

def get_active_box(min_margin=7):
    boxes = railbox.load()
    now = time.time()
    valid = {k: v for k, v in boxes.items() if (v.get("expires", 0) - now) > (min_margin * 60)}
    if valid:
        return max(valid, key=lambda k: valid[k]["expires"])
    return None

def spawn_box():
    name = f"rb_ip_{int(time.time())}"
    print(f"[*] Spawning fresh 60-min Railway VM: {name}...")
    try:
        box = railbox.create_via_pool(name, limit=6)
        print(f"[✓] Box created via VPN pool: {box}")
        return box
    except Exception as e:
        print(f"[!] Pool creation failed ({e}), trying direct/relay...")
        box = railbox.create_any(name)
        print(f"[✓] Box created: {box}")
        return box

def deploy_node_to_box(box_name, token):
    key_path = railbox.ROOT / box_name / "key"
    if not key_path.exists():
        raise FileNotFoundError(f"Missing SSH key for box: {key_path}")

    print(f"[*] Uploading iploop-node binary to Railway VM ({box_name})...")
    scp_cmd = [
        "scp", "-i", str(key_path),
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "LogLevel=ERROR",
        str(BINARY_LOCAL),
        "railway.new:/app/iploop-node"
    ]
    subprocess.run(scp_cmd, check=True)

    print("[*] Starting iploop-node background daemon on Railway VM...")
    launch_cmd = (
        f"chmod +x /app/iploop-node && "
        f"nohup /app/iploop-node -token {token} -gateway wss://gateway.iploop.io/ws </dev/null >/app/iploop.log 2>&1 & "
        f"sleep 1"
    )
    railbox.run(box_name, launch_cmd, timeout=35)
    print(f"[✓] Node running on Railway VM {box_name}!")

def status_box():
    box = get_active_box(min_margin=0)
    if not box:
        print("No active Railway box found.")
        return
    d = railbox.load().get(box, {})
    rem = int((d.get("expires", 0) - time.time()) / 60)
    print(f"=== Active Railway Box: {box} ({rem} min remaining) ===")
    print("Claim URL :", d.get("claim"))
    print("Preview   :", d.get("preview"))
    print("Egress Via:", d.get("via"))
    print("=== Remote Node Process & Logs ===")
    r = railbox.run(box, "ps aux | grep '[i]ploop-node'; echo '--- LOG ---'; tail -n 10 /app/iploop.log 2>/dev/null || echo '(no log)'", stdout=subprocess.PIPE, text=True)
    print(r.stdout)

def stop_box():
    box = get_active_box(min_margin=0)
    if not box:
        print("No active box to stop.")
        return
    print(f"[*] Wiping box: {box}...")
    railbox.wipe(box)
    print(f"[✓] Box {box} wiped.")

def run_once(margin=7):
    token = get_token()
    box = get_active_box(min_margin=margin)
    if not box:
        box = spawn_box()
    deploy_node_to_box(box, token)
    status_box()

def daemon_loop(margin=7):
    print(f"[*] Starting continuous Railway IPLoop Node daemon (margin: {margin}m)...")
    token = get_token()
    while True:
        try:
            box = get_active_box(min_margin=margin)
            if not box:
                # Wipe any expired boxes first
                boxes = railbox.load()
                for old_name, old_data in list(boxes.items()):
                    if (old_data.get("expires", 0) - time.time()) <= (margin * 60):
                        print(f"[*] Cleaning up expired box: {old_name}")
                        railbox.wipe(old_name)
                box = spawn_box()
                deploy_node_to_box(box, token)
            
            d = railbox.load().get(box, {})
            rem_sec = d.get("expires", 0) - time.time()
            sleep_sec = max(30, rem_sec - (margin * 60))
            print(f"[*] Node running on {box}. Sleeping {int(sleep_sec/60)}m until next rotation...")
            time.sleep(sleep_sec)
        except Exception as e:
            print(f"[!] Error in daemon loop: {e}. Retrying in 60s...")
            time.sleep(60)
def run_swarm(count=3):
    token = get_token()
    print(f"[*] Spawning swarm of {count} Railway VMs for IPLoop...")
    active = []
    for i in range(count):
        name = f"rb_ip_sw_{int(time.time())}_{i}"
        try:
            print(f"--- Launching box {i+1}/{count} ---")
            try:
                box = railbox.create_via_pool(name, limit=6)
            except Exception:
                box = railbox.create_any(name)
            deploy_node_to_box(box, token)
            active.append(box)
        except Exception as e:
            print(f"[!] Swarm box {i+1} failed: {e}")
    print(f"\n[✓] Swarm deployed! {len(active)} active nodes: {active}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Railway IPLoop Node Runner")
    parser.add_argument("mode", choices=["once", "daemon", "status", "stop", "swarm"])
    parser.add_argument("--count", "-n", type=int, default=3, help="number of boxes for swarm (default: 3)")
    parser.add_argument("--margin", type=int, default=7, help="rotation margin in minutes (default: 7)")
    args = parser.parse_args()

    if args.mode == "once":
        run_once(args.margin)
    elif args.mode == "swarm":
        run_swarm(args.count)
    elif args.mode == "daemon":
        daemon_loop(args.margin)
    elif args.mode == "status":
        status_box()
    elif args.mode == "stop":
        stop_box()
