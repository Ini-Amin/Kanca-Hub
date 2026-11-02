#!/usr/bin/env python3
"""scripts/freebuff_manager.py — Slot-based manager for Freebuff with sticky IP & persistent fingerprint.

Manages isolated Freebuff profiles (slots) so each slot preserves its unique device
fingerprint (`anonymousId`) and egress IP throughout its active session.
When quota is exhausted, rotating creates a fresh slot with a new fingerprint & IP
without overwriting or corrupting previous sessions.

Storage:
  ~/.config/auto-freecf/freebuff_slots/
    active.txt
    <slot_id>/
      analytics-id.json    (locked fingerprintId)
      credentials.json     (captured auth session)
      slot_meta.json       (egress proxy, created_at, status)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

BASE_DIR = Path.home() / ".config" / "auto-freecf" / "freebuff_slots"
ACTIVE_FILE = BASE_DIR / "active.txt"
FREEBUFF_BIN = shutil.which("freebuff") or str(Path.home() / ".local" / "bin" / "freebuff")


def get_active_slot_name() -> str:
    if ACTIVE_FILE.exists():
        name = ACTIVE_FILE.read_text("utf-8").strip()
        if name:
            return name
    return "slot_1"


def set_active_slot_name(name: str) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(name.strip() + "\n", "utf-8")


def get_slot_dir(slot_name: str) -> Path:
    return BASE_DIR / slot_name


def init_slot(slot_name: str, proxy: str | None = None) -> Path:
    slot_dir = get_slot_dir(slot_name)
    slot_dir.mkdir(parents=True, exist_ok=True)

    analytics_file = slot_dir / "analytics-id.json"
    if not analytics_file.exists():
        # Generate stable UUID for fingerprintId
        anon_id = uuid.uuid4().hex
        analytics_file.write_text(json.dumps({"anonymousId": anon_id}, indent=2), "utf-8")

    meta_file = slot_dir / "slot_meta.json"
    meta = {}
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text("utf-8"))
        except Exception:
            meta = {}

    if not meta:
        meta = {
            "slot": slot_name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "active",
            "proxy": proxy or "",
        }
    elif proxy is not None:
        meta["proxy"] = proxy

    meta_file.write_text(json.dumps(meta, indent=2), "utf-8")
    return slot_dir


def load_slot_meta(slot_name: str) -> dict:
    meta_file = get_slot_dir(slot_name) / "slot_meta.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text("utf-8"))
        except Exception:
            pass
    return {}


def list_slots() -> list[dict]:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    active = get_active_slot_name()
    slots = []
    for d in sorted(BASE_DIR.iterdir()):
        if d.is_dir():
            meta = load_slot_meta(d.name)
            anon_id = "-"
            anon_file = d / "analytics-id.json"
            if anon_file.exists():
                try:
                    anon_id = json.loads(anon_file.read_text("utf-8")).get("anonymousId", "-")
                except Exception:
                    pass

            cred_file = d / "credentials.json"
            logged_in = False
            user_info = ""
            if cred_file.exists():
                try:
                    cdata = json.loads(cred_file.read_text("utf-8"))
                    def_user = cdata.get("default", {})
                    if def_user.get("authToken") or def_user.get("id"):
                        logged_in = True
                        user_info = def_user.get("email") or def_user.get("name") or def_user.get("id")
                except Exception:
                    pass

            slots.append({
                "name": d.name,
                "is_active": d.name == active,
                "fingerprint": anon_id,
                "proxy": meta.get("proxy", "") or "(direct)",
                "status": meta.get("status", "active"),
                "logged_in": logged_in,
                "user": user_info,
                "created_at": meta.get("created_at", "-"),
            })
    return slots

def run_in_slot(slot_name: str, extra_args: list[str]) -> int:
    slot_dir = init_slot(slot_name)
    meta = load_slot_meta(slot_name)
    env = dict(os.environ)
    env["FREEBUFF_CONFIG_DIR"] = str(slot_dir.resolve())

    # Strip leading '--' delimiter if passed by CLI
    clean_args = [x for x in extra_args]
    if clean_args and clean_args[0] == "--":
        clean_args = clean_args[1:]

    proxy = meta.get("proxy")
    if proxy and proxy != "(direct)":
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        env["ALL_PROXY"] = proxy

    cmd = [FREEBUFF_BIN] + clean_args
    return subprocess.call(cmd, env=env)


def cmd_status(_args: argparse.Namespace) -> None:
    slots = list_slots()
    if not slots:
        active = get_active_slot_name()
        init_slot(active)
        slots = list_slots()

    print("\n=== Freebuff Sticky-IP & Fingerprint Slots ===")
    print(f"{'SLOT':<10} {'ACTIVE':<8} {'STATUS':<10} {'LOGGED_IN':<10} {'USER':<24} {'PROXY':<22} {'FINGERPRINT'}")
    print("-" * 115)
    for s in slots:
        act = "✓ YES" if s["is_active"] else ""
        login_str = "✓ Yes" if s["logged_in"] else "No"
        print(f"{s['name']:<10} {act:<8} {s['status']:<10} {login_str:<10} {s['user'][:22]:<24} {s['proxy'][:20]:<22} {s['fingerprint'][:16]}...")
    print()


def cmd_login(args: argparse.Namespace) -> None:
    slot_name = args.slot or get_active_slot_name()
    if args.proxy:
        init_slot(slot_name, proxy=args.proxy)
    else:
        init_slot(slot_name)
    set_active_slot_name(slot_name)
    meta = load_slot_meta(slot_name)
    print(f"[*] Launching Freebuff Login in [{slot_name}]")
    print(f"    Config Dir:  {get_slot_dir(slot_name)}")
    print(f"    Proxy:       {meta.get('proxy') or '(direct)'}")
    code = run_in_slot(slot_name, ["login"])
    sys.exit(code)


def cmd_run(args: argparse.Namespace) -> None:
    slot_name = args.slot or get_active_slot_name()
    init_slot(slot_name)
    code = run_in_slot(slot_name, args.extra_args)
    sys.exit(code)


def cmd_rotate(args: argparse.Namespace) -> None:
    slots = list_slots()
    current = get_active_slot_name()
    # Mark current as exhausted if requested
    cur_meta_file = get_slot_dir(current) / "slot_meta.json"
    if cur_meta_file.exists():
        try:
            m = json.loads(cur_meta_file.read_text("utf-8"))
            m["status"] = "exhausted"
            cur_meta_file.write_text(json.dumps(m, indent=2), "utf-8")
        except Exception:
            pass

    # Find next slot number
    existing_nums = []
    for s in slots:
        name = s["name"]
        if name.startswith("slot_"):
            try:
                existing_nums.append(int(name.split("_")[1]))
            except ValueError:
                pass
    next_num = (max(existing_nums) + 1) if existing_nums else 2
    new_slot = f"slot_{next_num}"

    init_slot(new_slot, proxy=args.proxy)
    set_active_slot_name(new_slot)
    meta = load_slot_meta(new_slot)
    anon_file = get_slot_dir(new_slot) / "analytics-id.json"
    anon_id = json.loads(anon_file.read_text("utf-8")).get("anonymousId")

    print(f"\n[✓] Rotated from [{current}] -> [{new_slot}]")
    print(f"    New Fingerprint ID: {anon_id}")
    print(f"    New Egress Proxy:   {meta.get('proxy') or '(direct)'}")
    print(f"    Run 'kancahub freebuff login' to authenticate the new slot.\n")


def cmd_switch(args: argparse.Namespace) -> None:
    slot_name = args.slot
    init_slot(slot_name)
    set_active_slot_name(slot_name)
    print(f"[✓] Switched active slot to [{slot_name}]")


def cmd_set_proxy(args: argparse.Namespace) -> None:
    slot_name = args.slot or get_active_slot_name()
    init_slot(slot_name, proxy=args.proxy)
    print(f"[✓] Proxy for [{slot_name}] updated to: {args.proxy}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Freebuff slot manager: sticky IP & isolated fingerprint")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="List all slots, status, fingerprint & proxy")

    p_log = sub.add_parser("login", help="Run login within the active or specified slot")
    p_log.add_argument("--slot", default=None, help="Target slot name")
    p_log.add_argument("--proxy", default=None, help="Bind slot to proxy URL (http/socks5)")

    p_run = sub.add_parser("run", help="Run freebuff CLI with arguments in active slot")
    p_run.add_argument("--slot", default=None, help="Target slot name")
    p_run.add_argument("extra_args", nargs=argparse.REMAINDER, help="Arguments passed to freebuff")

    p_rot = sub.add_parser("rotate", help="Retire current slot and create a fresh one")
    p_rot.add_argument("--proxy", default=None, help="Proxy for the new slot")

    p_sw = sub.add_parser("switch", help="Switch active slot")
    p_sw.add_argument("slot", help="Slot name (e.g. slot_1)")

    p_sp = sub.add_parser("set-proxy", help="Set proxy for a slot")
    p_sp.add_argument("proxy", help="Proxy URL (http://... or socks5://...)")
    p_sp.add_argument("--slot", default=None, help="Target slot")

    args = ap.parse_args()
    if args.cmd == "status":
        cmd_status(args)
    elif args.cmd == "login":
        cmd_login(args)
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "rotate":
        cmd_rotate(args)
    elif args.cmd == "switch":
        cmd_switch(args)
    elif args.cmd == "set-proxy":
        cmd_set_proxy(args)


if __name__ == "__main__":
    main()
