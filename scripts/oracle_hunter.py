#!/usr/bin/env python3
"""oracle_hunter.py — Hunt Oracle Cloud Always Free A1 Ampere capacity.

Two-phase strategy (default, "gated" mode):

  1. POLL the capacity-report API every --poll seconds. This endpoint is cheap
     and is not subject to the launch rate limit, so it can be hammered.
  2. LAUNCH only when the report flips to AVAILABLE. A blind launch is still
     attempted every --blind-every seconds as a safety net, because the report
     is advisory and can lag behind reality.

The old behaviour (launch blindly in a loop) is still available with --no-gate.

Exit codes (--once / cron mode):
    0  instance created, or an A1 instance already exists
    1  out of host capacity
    2  permanent error (bad config, free-tier violation, service limit)
    3  rate limited
    4  transient/unknown error (network, 5xx, ...)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request
from pathlib import Path

try:
    import oci
except ImportError:
    sys.exit("oci SDK not installed. Run: pip install oci")

# Defaults for ap-singapore-1 Always Free A1
DEFAULT_AD = "rbVP:AP-SINGAPORE-1-AD-1"
DEFAULT_SUBNET = "ocid1.subnet.oc1.ap-singapore-1.aaaaaaaaozykdjhoze57a5cqfiommgmfhf36tgleuwng4vrjqankhrq7kbaq"
DEFAULT_IMAGE = "ocid1.image.oc1.ap-singapore-1.aaaaaaaaw75ef2chh5goomskvdznqz36cadczqelqvndbqcozc5icyig57za"  # Ubuntu 24.04 aarch64
DEFAULT_SHAPE = "VM.Standard.A1.Flex"
DEFAULT_NAME = "oracle-a1-main"
DEFAULT_OCPUS = 2.0
DEFAULT_RAM_GB = 12.0
DEFAULT_DISK_GB = 200

# Always Free A1 allowance (per tenancy, summed across all A1 instances)
FREE_TIER_OCPUS = 4.0
FREE_TIER_RAM_GB = 24.0
FREE_TIER_DISK_GB = 200

STATE_FILE = Path.home() / ".config/auto-freecf/oracle_hunter_state.json"
SSH_KEY_FILE = Path.home() / ".ssh/id_ed25519.pub"

EXIT_OK = 0
EXIT_OOC = 1
EXIT_PERMANENT = 2
EXIT_RATE_LIMITED = 3
EXIT_TRANSIENT = 4


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def telegram_notify(text: str) -> bool:
    """Send alert via Telegram using local credentials if available."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    env_file = Path.home() / ".config/auto-freecf/.env"
    if env_file.is_file() and (not token or not chat_id):
        try:
            for line in env_file.read_text().splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                elif line.startswith("TELEGRAM_CHAT_ID="):
                    chat_id = line.split("=", 1)[1].strip()
        except Exception:
            pass

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


def classify_service_error(e: "oci.exceptions.ServiceError") -> str:
    """Map an OCI ServiceError to a stable outcome token."""
    msg = e.message or ""
    low = msg.lower()
    if "out of host capacity" in low or "out of capacity" in low:
        return "OUT_OF_CAPACITY"
    if e.status == 429:
        return "RATE_LIMITED"
    if e.code in ("LimitExceeded", "QuotaExceeded") or ("limit" in low and "exceed" in low):
        return f"LIMIT_EXCEEDED: {msg[:120]}"
    return f"ERROR: {e.status} {e.code}: {msg[:120]}"


def get_public_ip(compute: "oci.core.ComputeClient", net: "oci.core.VirtualNetworkClient",
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


def _compartment_ids(config: dict) -> list[str]:
    """Tenancy root plus every active sub-compartment (best effort)."""
    root = config["tenancy"]
    ids = [root]
    try:
        ident = oci.identity.IdentityClient(config)
        comps = ident.list_compartments(root, compartment_id_in_subtree=True,
                                        access_level="ACCESSIBLE").data
        ids += [c.id for c in comps if c.lifecycle_state == "ACTIVE"]
    except Exception as e:
        print(f"[warning] could not enumerate compartments: {type(e).__name__}: {e}", file=sys.stderr)
    return ids


def check_existing_instance(compute: "oci.core.ComputeClient", net: "oci.core.VirtualNetworkClient",
                            config: dict, shape: str = DEFAULT_SHAPE) -> tuple[bool, str]:
    """Check if a live A1 instance already exists anywhere in the tenancy."""
    for comp in _compartment_ids(config):
        try:
            instances = compute.list_instances(comp).data
        except oci.exceptions.ServiceError as e:
            print(f"[warning] could not list instances in {comp[-12:]}: {classify_service_error(e)}",
                  file=sys.stderr)
            continue
        except Exception as e:
            print(f"[warning] could not list instances: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        for inst in instances:
            if inst.shape == shape and inst.lifecycle_state not in ("TERMINATED", "TERMINATING"):
                ip = get_public_ip(compute, net, comp, inst.id)
                return True, f"{inst.display_name} ({ip}) [state={inst.lifecycle_state}]"
    return False, ""


def build_launch_details(config: dict, ssh_key: str, ocpus: float, ram_gb: float,
                         disk_gb: int, opts: dict):
    return oci.core.models.LaunchInstanceDetails(
        compartment_id=config["tenancy"],
        availability_domain=opts["ad"],
        display_name=opts["name"],
        shape=DEFAULT_SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=float(ocpus),
            memory_in_gbs=float(ram_gb),
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            source_type="image",
            image_id=opts["image"],
            boot_volume_size_in_gbs=int(disk_gb),
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=opts["subnet"],
            assign_public_ip=True,
        ),
        metadata={"ssh_authorized_keys": ssh_key},
    )


def check_capacity(compute: "oci.core.ComputeClient", config: dict,
                   ocpus: float, ram_gb: float, opts: dict) -> str:
    """Ask OCI whether the shape would fit right now. Cheap, not rate limited."""
    details = oci.core.models.CreateComputeCapacityReportDetails(
        compartment_id=config["tenancy"],
        availability_domain=opts["ad"],
        shape_availabilities=[
            oci.core.models.CreateCapacityReportShapeAvailabilityDetails(
                instance_shape=DEFAULT_SHAPE,
                instance_shape_config=oci.core.models.CapacityReportInstanceShapeConfig(
                    ocpus=float(ocpus), memory_in_gbs=float(ram_gb)),
            )
        ],
    )
    try:
        report = compute.create_compute_capacity_report(details).data
    except oci.exceptions.ServiceError as e:
        return classify_service_error(e)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {str(e)[:120]}"

    for sa in getattr(report, "shape_availabilities", []) or []:
        if (sa.availability_status or "").upper() == "AVAILABLE":
            return "AVAILABLE"
    return "OUT_OF_CAPACITY"


def attempt_launch(compute: "oci.core.ComputeClient", net: "oci.core.VirtualNetworkClient",
                   config: dict, ssh_key: str, ocpus: float, ram_gb: float, disk_gb: int,
                   opts: dict | None = None, ip_wait: int = 10,
                   notify: bool = True) -> tuple[bool, str]:
    """Try to launch an instance once. Returns (success, outcome_or_message)."""
    opts = opts or {"ad": DEFAULT_AD, "subnet": DEFAULT_SUBNET,
                    "image": DEFAULT_IMAGE, "name": DEFAULT_NAME}
    details = build_launch_details(config, ssh_key, ocpus, ram_gb, disk_gb, opts)

    try:
        resp = compute.launch_instance(details)
    except oci.exceptions.ServiceError as e:
        return False, classify_service_error(e)
    except Exception as e:
        # ConnectionError, timeouts, TLS errors, ... must never kill the loop.
        return False, f"ERROR: {type(e).__name__}: {str(e)[:120]}"

    inst = resp.data
    try:
        if ip_wait:
            time.sleep(ip_wait)
        ip = get_public_ip(compute, net, config["tenancy"], inst.id)
    except Exception:
        ip = "pending-ip"

    msg = (f"🎉 *Oracle A1 Created!*\n- Name: `{inst.display_name}`\n- IP: `{ip}`\n"
           f"- Shape: `{ocpus} OCPU / {ram_gb} GB RAM / {disk_gb} GB SSD`\n"
           f"- State: `{inst.lifecycle_state}`")
    if notify:
        telegram_notify(msg)
    return True, msg


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"total_attempts": 0, "last_report_ts": 0, "last_attempt_ts": 0,
            "out_of_capacity": 0, "capacity_checks": 0, "rate_limited": 0,
            "last_status": ""}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"[warning] could not save state: {e}", file=sys.stderr)


def exit_code_for(ok: bool, reason: str) -> int:
    if ok:
        return EXIT_OK
    if reason.startswith("OUT_OF_CAPACITY"):
        return EXIT_OOC
    if reason.startswith("RATE_LIMITED"):
        return EXIT_RATE_LIMITED
    if reason.startswith("LIMIT_EXCEEDED"):
        return EXIT_PERMANENT
    return EXIT_TRANSIENT


def main() -> int:
    ap = argparse.ArgumentParser(description="Oracle Cloud A1 Capacity Hunter")
    ap.add_argument("--once", action="store_true", help="one launch attempt then exit (cron mode)")
    ap.add_argument("--dry-run", action="store_true", help="check capacity only, never launch")
    ap.add_argument("--no-gate", action="store_true", help="disable capacity gate; launch blindly in a loop")
    ap.add_argument("--interval", type=int, default=120,
                    help="seconds between blind launches in --no-gate mode, and floor for blind retries (default: 120)")
    ap.add_argument("--poll", type=int, default=30,
                    help="seconds between capacity-report checks in gated mode (default: 30)")
    ap.add_argument("--blind-every", type=int, default=600,
                    help="gated mode: also attempt a blind launch every N seconds, 0 disables (default: 600)")
    ap.add_argument("--backoff-start", type=int, default=60, help="initial 429 backoff seconds (default: 60)")
    ap.add_argument("--backoff-max", type=int, default=900, help="max 429 backoff seconds (default: 900)")
    ap.add_argument("--report-hours", type=float, default=4.0, help="hours between Telegram summaries (default: 4.0)")
    ap.add_argument("--ocpus", type=float, default=DEFAULT_OCPUS, help=f"OCPUs to request (default: {DEFAULT_OCPUS})")
    ap.add_argument("--ram", type=float, default=DEFAULT_RAM_GB, help=f"RAM in GB (default: {DEFAULT_RAM_GB})")
    ap.add_argument("--disk", type=int, default=DEFAULT_DISK_GB, help=f"boot volume disk in GB (default: {DEFAULT_DISK_GB})")
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"instance display name (default: {DEFAULT_NAME})")
    ap.add_argument("--ad", default=DEFAULT_AD, help=f"availability domain (default: {DEFAULT_AD})")
    ap.add_argument("--subnet", default=DEFAULT_SUBNET, help="subnet OCID")
    ap.add_argument("--image", default=DEFAULT_IMAGE, help="image OCID")
    ap.add_argument("--config-file", default="~/.oci/config", help="OCI config path (default: ~/.oci/config)")
    ap.add_argument("--profile", default="DEFAULT", help="OCI config profile (default: DEFAULT)")
    ap.add_argument("--ssh-key", default=str(SSH_KEY_FILE), help="SSH public key path")
    ap.add_argument("--ip-wait", type=int, default=10, help="seconds to wait for the public IP after launch (default: 10)")
    ap.add_argument("--allow-paid", action="store_true", help="skip the Always Free A1 limit check")
    ap.add_argument("--no-telegram", action="store_true", help="never send Telegram messages")
    a = ap.parse_args()

    opts = {"ad": a.ad, "subnet": a.subnet, "image": a.image, "name": a.name}
    notify = (lambda text: telegram_notify(text)) if not a.no_telegram else (lambda text: False)

    # Refuse targets that can only ever fail with LimitExceeded.
    if not a.allow_paid:
        over = []
        if a.ocpus > FREE_TIER_OCPUS:
            over.append(f"ocpus {a.ocpus:g} > {FREE_TIER_OCPUS:g}")
        if a.ram > FREE_TIER_RAM_GB:
            over.append(f"ram {a.ram:g} GB > {FREE_TIER_RAM_GB:g} GB")
        if a.disk > FREE_TIER_DISK_GB:
            over.append(f"disk {a.disk} GB > {FREE_TIER_DISK_GB} GB")
        if over:
            print("ERROR: request exceeds the Always Free A1 allowance: " + ", ".join(over), file=sys.stderr)
            print("       pass --allow-paid to override (requires paid capacity).", file=sys.stderr)
            return EXIT_PERMANENT

    try:
        config = oci.config.from_file(a.config_file, a.profile)
    except Exception as e:
        print(f"ERROR: cannot load OCI config {a.config_file}: {e}", file=sys.stderr)
        return EXIT_PERMANENT

    ssh_key = ""
    try:
        ssh_key = Path(a.ssh_key).expanduser().read_text().strip()
    except Exception:
        pass
    if not ssh_key:
        print(f"ERROR: SSH public key not found at {a.ssh_key}", file=sys.stderr)
        return EXIT_PERMANENT

    compute = oci.core.ComputeClient(config)
    net = oci.core.VirtualNetworkClient(config)

    exists, info = check_existing_instance(compute, net, config)
    if exists:
        log(f"instance already exists: {info}")
        return EXIT_OK

    state = load_state()
    state.setdefault("capacity_checks", 0)
    state.setdefault("rate_limited", 0)
    state.setdefault("last_status", "")

    # ---- one-shot / cron mode ------------------------------------------------
    if a.once or a.dry_run:
        status = check_capacity(compute, config, a.ocpus, a.ram, opts)
        state["capacity_checks"] += 1
        state["last_status"] = status
        log(f"capacity report: {status}")
        if a.dry_run:
            save_state(state)
            return EXIT_OK if status == "AVAILABLE" else exit_code_for(False, status)

        ok, reason = attempt_launch(compute, net, config, ssh_key, a.ocpus, a.ram, a.disk,
                                    opts, a.ip_wait, notify=not a.no_telegram)
        state["total_attempts"] += 1
        state["last_attempt_ts"] = time.time()
        state["last_status"] = reason if not ok else "CREATED"
        if ok:
            log(f"SUCCESS: {reason}")
        elif reason.startswith("OUT_OF_CAPACITY"):
            state["out_of_capacity"] += 1
            log(f"Attempt {state['total_attempts']}: OUT_OF_CAPACITY")
        else:
            if reason.startswith("RATE_LIMITED"):
                state["rate_limited"] += 1
            log(f"Attempt {state['total_attempts']}: {reason}")
        save_state(state)
        return exit_code_for(ok, reason)

    # ---- long-running loop ---------------------------------------------------
    # First blind safety-net launch happens only after --blind-every seconds,
    # never at startup (the report already told us capacity is missing).
    next_blind = time.time() + a.blind_every if a.blind_every > 0 else float("inf")
    backoff = a.backoff_start

    while True:
        launch_now = False
        blind = False
        status = ""

        if not a.no_gate:
            status = check_capacity(compute, config, a.ocpus, a.ram, opts)
            state["capacity_checks"] += 1
            state["last_status"] = status

            if status == "AVAILABLE":
                log("capacity AVAILABLE -> launching")
                launch_now = True
            elif status == "RATE_LIMITED":
                state["rate_limited"] += 1
                log(f"capacity check RATE_LIMITED -> backoff {backoff}s")
                time.sleep(backoff + random.randint(0, 15))
                backoff = min(backoff * 2, a.backoff_max)
                save_state(state)
                continue
            elif status.startswith("LIMIT_EXCEEDED"):
                log(f"PERMANENT: {status} — stopping")
                notify(f"🛑 *Oracle A1 hunter stopped*\n{status}")
                save_state(state)
                return EXIT_PERMANENT
            elif status.startswith("ERROR"):
                log(f"capacity check failed: {status}")
            else:
                backoff = a.backoff_start
                log(f"capacity: {status} (check #{state['capacity_checks']})")
                if a.blind_every > 0 and time.time() >= next_blind:
                    launch_now = True
                    blind = True

            if not launch_now:
                save_state(state)
                time.sleep(max(5, a.poll))
                continue
        else:
            launch_now = True

        next_blind = time.time() + a.blind_every if a.blind_every > 0 else float("inf")
        state["total_attempts"] += 1
        state["last_attempt_ts"] = time.time()

        ok, reason = attempt_launch(compute, net, config, ssh_key, a.ocpus, a.ram, a.disk,
                                    opts, a.ip_wait, notify=not a.no_telegram)
        state["last_status"] = reason if not ok else "CREATED"

        if ok:
            log(f"SUCCESS: {reason}")
            save_state(state)
            return EXIT_OK

        if reason.startswith("OUT_OF_CAPACITY"):
            state["out_of_capacity"] += 1
            log(f"Attempt {state['total_attempts']}: OUT_OF_CAPACITY" + (" (blind)" if blind else ""))
        elif reason.startswith("RATE_LIMITED"):
            state["rate_limited"] += 1
            log(f"Attempt {state['total_attempts']}: RATE_LIMITED -> backoff {backoff}s")
        elif reason.startswith("LIMIT_EXCEEDED"):
            log(f"PERMANENT: {reason} — stopping")
            notify(f"🛑 *Oracle A1 hunter stopped*\n{reason}")
            save_state(state)
            return EXIT_PERMANENT
        else:
            log(f"Attempt {state['total_attempts']}: {reason}")

        now = time.time()
        if now - state.get("last_report_ts", 0) >= a.report_hours * 3600:
            state["last_report_ts"] = now
            if not a.no_telegram:
                telegram_notify(
                    f"🛰 *Oracle A1 — Laporan {a.report_hours:g} Jam*\n"
                    f"• Region: `{config.get('region', 'ap-singapore-1')}` (AD: `{opts['ad']}`)\n"
                    f"• Target: `{a.ocpus:g} OCPU / {a.ram:g} GB RAM / {a.disk} GB SSD`\n"
                    f"• Status terakhir: `{state['last_status']}`\n"
                    f"• Launch dicoba: `{state['total_attempts']}` (OOC `{state['out_of_capacity']}`, 429 `{state['rate_limited']}`)\n"
                    f"• Cek kapasitas: `{state['capacity_checks']}`"
                )

        save_state(state)

        if reason.startswith("RATE_LIMITED"):
            time.sleep(backoff + random.randint(0, 15))
            backoff = min(backoff * 2, a.backoff_max)
        else:
            backoff = a.backoff_start
            time.sleep(a.interval + random.randint(0, 4))

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
