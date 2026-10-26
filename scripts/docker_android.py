#!/usr/bin/env python3
"""scripts/docker_android.py — Manage Headless Containerized Android (budtmo/docker-android).

Uses Podman (rootless with /dev/kvm hardware acceleration) on the local workstation.
Provides:
  - noVNC Web UI on http://127.0.0.1:6080
  - ADB over TCP on 127.0.0.1:5555
  - Hardware accelerated KVM emulation
  - Google Play Services (trusted device attestation)

Commands:
  python3 scripts/docker_android.py up       # Start container & connect ADB
  python3 scripts/docker_android.py status   # Check container & ADB status
  python3 scripts/docker_android.py down     # Stop and remove container
  python3 scripts/docker_android.py web      # Show noVNC web viewer URL
  python3 scripts/docker_android.py adb <cmd># Run adb shell command directly
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

CONTAINER_NAME = "docker-android"
IMAGE_NAME = "docker.io/budtmo/docker-android:emulator_9.0"
ADB_PORT = 5555
VNC_PORT = 6080
from pathlib import Path
MOCK_CHOWN = str(Path(__file__).resolve().parent / "docker_assets/mock_chown")


def _run(cmd: list[str], check: bool = False, capture: bool = True) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, text=True, capture_output=capture)
        return res.returncode, (res.stdout or "") + (res.stderr or "")
    except Exception as e:
        return 1, str(e)


def is_running() -> bool:
    code, out = _run(["podman", "ps", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"])
    return CONTAINER_NAME in out


def cmd_status():
    running = is_running()
    print("=== Docker-Android (budtmo) Status ===")
    print(f"Container '{CONTAINER_NAME}': {'RUNNING' if running else 'STOPPED'}")

    # Check ADB devices
    code, adb_out = _run(["adb", "devices"])
    print("\nADB Connected Devices:")
    for line in adb_out.splitlines()[1:]:
        if line.strip():
            print(f"  {line.strip()}")

    if running:
        print(f"\n[✓] noVNC Web Viewer : http://127.0.0.1:{VNC_PORT}")
        print(f"[✓] ADB TCP Endpoint : 127.0.0.1:{ADB_PORT}")


def cmd_up(ram: int = 2048, device: str = "Nexus 5"):
    if is_running():
        print(f"[!] Container '{CONTAINER_NAME}' is already running.")
        cmd_status()
        return

    # Check /dev/kvm
    if not os.path.exists("/dev/kvm"):
        print("[!] ERROR: /dev/kvm is not accessible on this host. KVM is required.")
        sys.exit(1)

    # Always clean existing container
    _run(["podman", "rm", "-f", CONTAINER_NAME])

    print(f"[*] Launching container '{CONTAINER_NAME}' using image {IMAGE_NAME}...")
    cmd = [
        "podman", "run", "-d",
        "--name", CONTAINER_NAME,
        "--device", "/dev/kvm",
        "--device", "/dev/dri",
        "--security-opt", "seccomp=unconfined",
        "--security-opt", "label=disable",
        "--shm-size", "2g",
        "-v", f"{MOCK_CHOWN}:/usr/bin/chown:ro,z",
        "-p", f"{VNC_PORT}:6080",
        "-p", f"{ADB_PORT}:5555",
        "-p", "5554:5554",
        "-e", f"EMULATOR_DEVICE={device}",
        "-e", "WEB_VNC=true",
        "-e", f"EMULATOR_RAM={ram}",
        IMAGE_NAME
    ]
    code, out = _run(cmd)
    if code != 0:
        print(f"[!] Failed to launch container: {out}")
        sys.exit(code)

    print("[*] Waiting for container initialization (10s)...")
    time.sleep(10)

    # Connect ADB
    print(f"[*] Connecting ADB to 127.0.0.1:{ADB_PORT}...")
    for _ in range(5):
        c, out = _run(["adb", "connect", f"127.0.0.1:{ADB_PORT}"])
        if "connected" in out.lower():
            print(f"  [✓] ADB connected to 127.0.0.1:{ADB_PORT}")
            break
        time.sleep(3)

    cmd_status()


def cmd_down():
    if not is_running():
        print(f"[*] Container '{CONTAINER_NAME}' is not running.")
    else:
        print(f"[*] Stopping container '{CONTAINER_NAME}'...")
        _run(["podman", "stop", CONTAINER_NAME])

    print(f"[*] Removing container '{CONTAINER_NAME}'...")
    _run(["podman", "rm", "-f", CONTAINER_NAME])
    _run(["adb", "disconnect", f"127.0.0.1:{ADB_PORT}"])
    print("[✓] Container stopped and removed cleanly.")


def cmd_adb_shell(args: list[str]):
    subprocess.run(["adb", "-s", f"127.0.0.1:{ADB_PORT}", "shell", *args])


def main():
    parser = argparse.ArgumentParser(description="Docker-Android (budtmo) Manager")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    up_p = subparsers.add_parser("up", help="Start the Android container and connect ADB")
    up_p.add_argument("--ram", type=int, default=2048, help="Emulator RAM in MB (default: 2048)")
    up_p.add_argument("--device", default="Nexus 5", help="Device profile (default: Nexus 5)")

    subparsers.add_parser("down", help="Stop and remove the Android container")
    subparsers.add_parser("status", help="Show status of container and ADB connection")
    subparsers.add_parser("web", help="Open or display the noVNC web viewer URL")

    adb_p = subparsers.add_parser("shell", help="Run adb shell command on the container device")
    adb_p.add_argument("args", nargs=argparse.REMAINDER, help="Command to run in adb shell")

    args = parser.parse_args()

    if args.cmd == "status":
        cmd_status()
    elif args.cmd == "up":
        cmd_up(ram=args.ram, device=args.device)
    elif args.cmd == "down":
        cmd_down()
    elif args.cmd == "web":
        print(f"noVNC Web Viewer: http://127.0.0.1:{VNC_PORT}")
    elif args.cmd == "shell":
        cmd_adb_shell(args.args)


if __name__ == "__main__":
    main()
