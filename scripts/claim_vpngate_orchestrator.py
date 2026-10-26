#!/usr/bin/env python3
"""VPN Gate auto-rotating claim orchestrator.

Claims the 1024 Dev Day coupon across all farmed accounts using VPN Gate
volunteer/residential relays to bypass z.ai rate-limits and IP flagging.

Invariants:
- Max 4 successful claims per relay before proactive rotation
- Immediate rotation on HTTP 3012 (rate-limit) or relay network drops
- LAN and Tailscale preserved via net_gateway routing
- Full teardown of openvpn on completion or exit
"""
from __future__ import annotations

import base64
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# Add repo scripts/ to path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import vpngate  # noqa: E402
import zcode    # noqa: E402

PLAN_ID = "zcode-v3-start-plan-1024"
MAX_PER_RELAY = 3
LOG_PATH = Path.home() / ".config/auto-freecf/logs/vpngate_claim.log"
LEDGER_PATH = Path.home() / ".config/auto-freecf/vpngate_claim_ledger.json"
PID_FILE = Path.home() / ".cache/vpngate.pid"

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


active_relay_connected = False


def cleanup_vpn() -> None:
    global active_relay_connected
    active_relay_connected = False
    try:
        subprocess.run(["sudo", "-n", "pkill", "-9", "-f", "openvpn"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    PID_FILE.unlink(missing_ok=True)
    time.sleep(1.5)


def is_vpn_up() -> bool:
    if not active_relay_connected:
        return False
    ip = vpngate.exit_ip()
    # Home IP is 182.8.255.73
    return bool(ip and not ip.startswith("182.8.255."))

def connect_relay(r: dict) -> bool:
    global active_relay_connected
    cleanup_vpn()
    cfg_raw = base64.b64decode(r["OpenVPN_ConfigData_Base64"]).decode("utf-8", "ignore")
    
    # TCP reachability check
    m_remote = re.search(r"^remote\s+(\S+)\s+(\d+)", cfg_raw, re.M)
    if not m_remote:
        return False
    host, port = m_remote.group(1), int(m_remote.group(2))
    
    probe = socket.socket()
    probe.settimeout(3.5)
    try:
        probe.connect((host, port))
        probe.close()
    except OSError:
        probe.close()
        return False

    # Filter unsafe script directives
    safe_lines = [
        l for l in cfg_raw.splitlines()
        if not re.match(
            r"\s*(script-security|up|down|route-up|route-pre-down|ipchange|client-connect|"
            r"tls-verify|auth-user-pass-verify|learn-address|plugin|setenv|push-peer-info|"
            r"management|ifconfig-noexec|dev-node|cd|chroot|daemon|log|log-append|writepid)\b",
            l, re.I
        )
    ]
    cfg = "\n".join(safe_lines)
    # Exclude tailnet and LAN from redirect-gateway
    cfg += (
        "\nroute 100.64.0.0 255.192.0.0 net_gateway"
        "\nroute 192.168.0.0 255.255.0.0 net_gateway\n"
    )

    auth = Path(tempfile.mkstemp()[1])
    auth.write_text("vpn\nvpn\n")
    ovpn_file = Path(tempfile.mkstemp(suffix=".ovpn")[1])
    ovpn_file.write_text(cfg)
    
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "sudo", "-n", "openvpn",
        "--config", str(ovpn_file),
        "--auth-user-pass", str(auth),
        "--script-security", "0",
        "--daemon",
        "--writepid", str(PID_FILE),
        "--connect-timeout", "10",
        "--connect-retry-max", "1",
        "--data-ciphers", "AES-128-CBC:AES-256-GCM:AES-128-GCM:AES-256-CBC"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(12):
        time.sleep(1)
        cur_ip = vpngate.exit_ip()
        if cur_ip and cur_ip == r["IP"]:
            active_relay_connected = True
            return True
        if cur_ip and not cur_ip.startswith("182.8.255."):
            active_relay_connected = True
            return True
    cleanup_vpn()
    return False


def claim_account(email: str) -> dict:
    jwt = zcode.jwt_for(email)
    if not jwt:
        return {"code": "no_jwt", "msg": "Missing JWT"}

    headers = {
        "Authorization": "Bearer " + jwt,
        "Content-Type": "application/json",
        "User-Agent": "ZCode/3.16.3",
        "http-referer": "https://zcode.z.ai",
        "x-zcode-app-version": "3.16.3",
        "x-release-channel": "production",
        "x-client-language": "en-US",
        "x-client-timezone": "Asia/Jakarta",
        "x-os-category": "linux",
        "x-os-version": "7.2.8",
        "x-title": "Z Code@electron",
        "x-platform": "linux-x64",
        "x-device-mid": zcode.device_mid(email),
        "x-request-id": "00000000-0000-4000-8000-%012d" % (time.time() % 1e12),
    }

    req = urllib.request.Request(
        "https://zcode.z.ai/api/v1/zcode-plan/billing/claim?app_version=3.16.3",
        data=json.dumps({"plan_id": PLAN_ID}).encode(),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"code": e.code, "msg": f"HTTP {e.code}"}
    except Exception as e:
        return {"code": "NET_ERR", "msg": str(e)}


def has_claimed_plan(email: str) -> bool:
    try:
        d = zcode.billing(email, "balance")
        return any((b.get("remaining_units") or 0) >= 200_000_000 for b in d.get("balances") or [])
    except Exception:
        return False


def run():
    # Handle graceful exit
    def _sig_handler(sig, frame):
        log("Signal received, tearing down VPN...")
        cleanup_vpn()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    # Load all candidate accounts
    jwt_dir = Path.home() / ".zcode-bridge/jwt"
    all_emails = sorted(p.stem.strip().lower() for p in jwt_dir.glob("*.txt"))
    
    # Load ledger if exists
    ledger = {}
    if LEDGER_PATH.exists():
        try:
            ledger = json.loads(LEDGER_PATH.read_text())
        except Exception:
            ledger = {}

    log(f"Starting claim orchestrator: {len(all_emails)} accounts in pool")

    # Fetch relays
    relays = vpngate.relays()
    if not relays:
        log("ERROR: No relays returned from VPN Gate API!")
        return 1
    log(f"Fetched {len(relays)} candidate relays from VPN Gate")

    # Queue of accounts still needing claim
    queue = []
    for em in all_emails:
        if ledger.get(em, {}).get("status") == "claimed":
            continue
        queue.append(em)

    log(f"Queue initialized: {len(queue)} accounts to check/claim ({len(all_emails) - len(queue)} already in ledger)")
    retry_counts = {}
    relay_idx = 0
    consecutive_relay_fails = 0
    try:
        while queue:
            # Must connect to next relay if not connected
            if not active_relay_connected:
                if relay_idx >= len(relays):
                    log("Cycled through all relays, refreshing live list from VPN Gate...")
                    vpngate._RELAY_CACHE.clear()
                    relays = vpngate.relays()
                    relay_idx = 0
                    if not relays:
                        log("No relays returned on refresh, sleeping 15s...")
                        time.sleep(15)
                        continue

                relay = relays[relay_idx % len(relays)]
                relay_country = relay.get("CountryShort", "??")
                relay_ip = relay.get("IP", "??")
                log(f"Connecting to relay [{relay_idx + 1}/{len(relays)}] {relay_country} {relay_ip}...")
                
                ok = connect_relay(relay)
                if not ok:
                    log(f"Relay {relay_ip} connection failed, advancing...")
                    relay_idx += 1
                    consecutive_relay_fails += 1
                    if consecutive_relay_fails > 15:
                        log("Too many relay fails in a row, refreshing relay list...")
                        vpngate._RELAY_CACHE.clear()
                        relays = vpngate.relays()
                        relay_idx = 0
                        consecutive_relay_fails = 0
                    time.sleep(1)
                    continue

                consecutive_relay_fails = 0
                claims_on_this_relay = 0
                log(f"Connected to {relay_country} {relay_ip}! Exit IP: {vpngate.exit_ip()}")
            
            while queue and claims_on_this_relay < MAX_PER_RELAY:
                email = queue.pop(0)

                # Check if already claimed
                if has_claimed_plan(email):
                    log(f"[ALREADY] {email} already holds 1024 plan")
                    ledger[email] = {"status": "claimed", "time": time.time(), "note": "verified_existing"}
                    LEDGER_PATH.write_text(json.dumps(ledger, indent=2))
                    continue

                log(f"[CLAIMING] {email} (relay claims: {claims_on_this_relay}/{MAX_PER_RELAY})...")
                res = claim_account(email)
                code = res.get("code")

                if code == 0:
                    claims_on_this_relay += 1
                    log(f"[SUCCESS] {email} claimed 500M tokens! (Total remaining in queue: {len(queue)})")
                    ledger[email] = {"status": "claimed", "time": time.time(), "plan": "zcode-v3-start-plan-1024"}
                    LEDGER_PATH.write_text(json.dumps(ledger, indent=2))
                    time.sleep(2.5)

                elif code == 3012:
                    log(f"[3012 BLOCKED] Relay {vpngate.exit_ip()} flagged. Re-queuing {email} and rotating relay...")
                    retry_counts[email] = retry_counts.get(email, 0) + 1
                    if retry_counts[email] <= 5:
                        queue.append(email)
                    else:
                        log(f"[ABANDON] {email} hit 5 retries on 3012")
                        ledger[email] = {"status": "failed", "code": 3012, "note": "max_retries"}
                        LEDGER_PATH.write_text(json.dumps(ledger, indent=2))
                    cleanup_vpn()
                    relay_idx += 1
                    time.sleep(2)
                    break  # Break inner loop to trigger relay reconnect

                elif code == 1003 or "already" in str(res.get("msg", "")).lower():
                    log(f"[HAD] {email} code={code} ({res.get('msg')})")
                    ledger[email] = {"status": "claimed", "time": time.time(), "note": "server_says_already"}
                    LEDGER_PATH.write_text(json.dumps(ledger, indent=2))
                    time.sleep(1)

                elif code == "no_jwt":
                    log(f"[NO_JWT] {email} has no valid JWT token")
                    ledger[email] = {"status": "failed", "code": "no_jwt", "time": time.time()}
                    LEDGER_PATH.write_text(json.dumps(ledger, indent=2))
                    time.sleep(0.5)

                elif code == "NET_ERR" or str(code).startswith("5"):
                    log(f"[ERR] {email} code={code} ({res.get('msg')}). Re-queuing...")
                    retry_counts[email] = retry_counts.get(email, 0) + 1
                    if retry_counts[email] <= 4:
                        queue.append(email)  # Put at back of queue
                    else:
                        log(f"[ABANDON] {email} hit max network retries")
                        ledger[email] = {"status": "failed", "code": code, "note": "max_net_retries"}
                        LEDGER_PATH.write_text(json.dumps(ledger, indent=2))
                    cleanup_vpn()
                    relay_idx += 1
                    break

                else:
                    log(f"[FAIL] {email} code={code} msg={res.get('msg')}")
                    ledger[email] = {"status": "failed", "code": code, "msg": res.get("msg"), "time": time.time()}
                    LEDGER_PATH.write_text(json.dumps(ledger, indent=2))
                    time.sleep(2)
            # Proactive rotation after MAX_PER_RELAY
            if claims_on_this_relay >= MAX_PER_RELAY:
                log(f"Reached {MAX_PER_RELAY} claims on this relay. Proactively rotating to maintain fresh IP...")
                cleanup_vpn()
                relay_idx += 1
                time.sleep(1.5)

    except Exception as e:
        log(f"FATAL ERROR in orchestrator loop: {type(e).__name__}: {e}")
        import traceback
        log(traceback.format_exc())
    finally:
        cleanup_vpn()
        claimed_cnt = sum(1 for v in ledger.values() if v.get("status") == "claimed")
        log(f"Orchestrator finished. Total claimed in ledger: {claimed_cnt}/{len(all_emails)}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
