#!/usr/bin/env python3
"""oracle_hunter.py — Hunt Oracle Cloud Always Free A1 Ampere capacity.

Periodically requests creation of an A1.Flex (2 OCPU / 12 GB RAM) instance until
capacity becomes available in the target Availability Domain.
Sends Telegram alerts on progress and on successful creation.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import oci
except ImportError:
    sys.exit("oci SDK not installed. Run: pip install oci")

# Default Parameters for ap-singapore-1 Always Free A1
DEFAULT_AD = "rbVP:AP-SINGAPORE-1-AD-1"
DEFAULT_SUBNET = "ocid1.subnet.oc1.ap-singapore-1.aaaaaaaaozykdjhoze57a5cqfiommgmfhf36tgleuwng4vrjqankhrq7kbaq"
DEFAULT_IMAGE = "ocid1.image.oc1.ap-singapore-1.aaaaaaaaw75ef2chh5goomskvdznqz36cadczqelqvndbqcozc5icyig57za"  # Ubuntu 24.04 aarch64
DEFAULT_SHAPE = "VM.Standard.A1.Flex"
DEFAULT_NAME = "oracle-a1-main"
DEFAULT_OCPUS = 2.0
DEFAULT_RAM_GB = 12.0
DEFAULT_DISK_GB = 200

STATE_FILE = Path.home() / ".config/auto-freecf/oracle_hunter_state.json"
SSH_KEY_FILE = Path.home() / ".ssh/id_ed25519.pub"


def telegram_notify(text: str) -> bool:
    """Send alert via Telegram using local credentials if available."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    env_file = Path.home() / ".config/auto-freecf/.env"
    if env_file.is_file() and (not token or not chat_id):
        for line in env_file.read_text().splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip()
            elif line.startswith("TELEGRAM_CHAT_ID="):
                chat_id = line.split("=", 1)[1].strip()

    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[telegram error] {e}", file=sys.stderr)
        return False


def get_public_ip(compute: oci.core.ComputeClient, net: oci.core.VirtualNetworkClient,
                  compartment_id: str, instance_id: str) -> str:
    """Resolve the assigned public IP for an instance."""
    try:
        vnics = compute.list_vnic_attachments(compartment_id, instance_id=instance_id).data
        for va in vnics:
            if va.lifecycle_state == "ATTACHED" and va.vnic_id:
                vnic = net.get_vnic(va.vnic_id).data
                if vnic.public_ip:
                    return vnic.public_ip
    except Exception:
        pass
    return "pending-ip"


def check_existing_instance(compute: oci.core.ComputeClient, net: oci.core.VirtualNetworkClient,
                            compartment_id: str, shape: str = DEFAULT_SHAPE) -> tuple[bool, str]:
    """Check if an active A1 instance is already running."""
    try:
        instances = compute.list_instances(compartment_id).data
        for inst in instances:
            if inst.shape == shape and inst.lifecycle_state not in ("TERMINATED", "TERMINATING"):
                ip = get_public_ip(compute, net, compartment_id, inst.id)
                return True, f"{inst.display_name} ({ip}) [state={inst.lifecycle_state}]"
    except Exception as e:
        print(f"[warning] could not list instances: {e}", file=sys.stderr)
    return False, ""


def attempt_launch(compute: oci.core.ComputeClient, net: oci.core.VirtualNetworkClient,
                   config: dict, ssh_key: str, ocpus: float, ram_gb: float, disk_gb: int) -> tuple[bool, str]:
    """Try to launch an instance once. Returns (success, message)."""
    details = oci.core.models.LaunchInstanceDetails(
        compartment_id=config["tenancy"],
        availability_domain=DEFAULT_AD,
        display_name=DEFAULT_NAME,
        shape=DEFAULT_SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=float(ocpus),
            memory_in_gbs=float(ram_gb)
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            source_type="image",
            image_id=DEFAULT_IMAGE,
            boot_volume_size_in_gbs=int(disk_gb)
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=DEFAULT_SUBNET,
            assign_public_ip=True
        ),
        metadata={
            "ssh_authorized_keys": ssh_key
        }
    )

    try:
        resp = compute.launch_instance(details)
        inst = resp.data
        time.sleep(10)
        ip = get_public_ip(compute, net, config["tenancy"], inst.id)
        msg = f"🎉 *Oracle A1 Created!*\n- Name: `{inst.display_name}`\n- IP: `{ip}`\n- Shape: `{ocpus} OCPU / {ram_gb} GB RAM / {disk_gb} GB SSD`\n- State: `{inst.lifecycle_state}`"
        telegram_notify(msg)
        return True, msg
    except oci.exceptions.ServiceError as e:
        if "Out of host capacity" in (e.message or ""):
            return False, "OUT_OF_CAPACITY"
        if e.status == 429:
            return False, "RATE_LIMITED"
        return False, f"ERR_{e.status}_{e.code}: {e.message[:80]}"


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"total_attempts": 0, "last_report_ts": 0, "last_attempt_ts": 0, "out_of_capacity": 0}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description="Oracle Cloud A1 Capacity Hunter")
    ap.add_argument("--once", action="store_true", help="run single attempt then exit (cron mode)")
    ap.add_argument("--interval", type=int, default=60, help="seconds between attempts in loop mode (default: 60)")
    ap.add_argument("--report-hours", type=float, default=4.0, help="hours between Telegram summary reports (default: 4.0)")
    ap.add_argument("--ocpus", type=float, default=DEFAULT_OCPUS, help=f"OCPUs to request (default: {DEFAULT_OCPUS})")
    ap.add_argument("--ram", type=float, default=DEFAULT_RAM_GB, help=f"RAM in GB (default: {DEFAULT_RAM_GB})")
    ap.add_argument("--disk", type=int, default=DEFAULT_DISK_GB, help=f"boot volume disk in GB (default: {DEFAULT_DISK_GB})")
    a = ap.parse_args()

    config = oci.config.from_file("~/.oci/config")
    compute = oci.core.ComputeClient(config)
    net = oci.core.VirtualNetworkClient(config)

    ssh_key = ""
    if SSH_KEY_FILE.is_file():
        ssh_key = SSH_KEY_FILE.read_text().strip()
    if not ssh_key:
        sys.exit(f"SSH public key not found at {SSH_KEY_FILE}")

    # Check if instance already exists
    exists, info = check_existing_instance(compute, net, config["tenancy"])
    if exists:
        print(f"[success] Instance already exists: {info}")
        return 0

    state = load_state()

    while True:
        state["total_attempts"] += 1
        state["last_attempt_ts"] = time.time()

        ok, reason = attempt_launch(compute, net, config, ssh_key, a.ocpus, a.ram, a.disk)

        if ok:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] SUCCESS: {reason}")
            save_state(state)
            return 0

        if reason == "OUT_OF_CAPACITY":
            state["out_of_capacity"] += 1
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Attempt {state['total_attempts']}: OUT_OF_CAPACITY (waiting next slot)")
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Attempt {state['total_attempts']}: {reason}")

        # Send Telegram summary report every N hours
        now = time.time()
        if now - state.get("last_report_ts", 0) >= a.report_hours * 3600:
            state["last_report_ts"] = now
            report = (
                f"🛰 *Oracle A1 — Laporan {a.report_hours:g} Jam*\n"
                f"• Region: `{config.get('region', 'ap-singapore-1')}` (AD: `{DEFAULT_AD}`)\n"
                f"• Target: `{a.ocpus} OCPU / {a.ram:g} GB RAM / {a.disk} GB SSD`\n"
                f"• Status: *OUT_OF_CAPACITY* ({state['out_of_capacity']}x)\n"
                f"• Total Percobaan: `{state['total_attempts']}`\n"
                f"• Instance: belum dapat (hunting aktif ✅)"
            )
            telegram_notify(report)

        save_state(state)

        if a.once:
            break

        # Jittered sleep (e.g. 60s ± 15s) to avoid metronome detection
        sleep_secs = max(20, a.interval + random.randint(-15, 20))
        if reason == "RATE_LIMITED":
            sleep_secs = 180
        time.sleep(sleep_secs)

    return 0


if __name__ == "__main__":
    sys.exit(main())
