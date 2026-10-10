#!/usr/bin/env python3
"""
WARP tunnel manager for KancaHub.

Cloudflare WARP (WireGuard) gives free, clean Cloudflare egress IPs that are
NOT pre-flagged — proven to pass the full Cloudflare Workers AI signup flow
(account + email verify + token).

This module generates a WARP profile (via PetaniProxy's generator) and manages
the tunnel with wireguard-tools (wg-quick).

Public API:
    generate()              -> create output/warp/warp.conf
    up()                    -> install config + bring tunnel up
    down()                  -> bring tunnel down
    status()                -> dict with tunnel state + egress IP
    is_up()                 -> bool
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
PETANI = HOME / "petani-proxy"
WARP_CONF = PETANI / "output" / "warp" / "warp.conf"
WG_NAME = "warp"
WG_SYS = Path("/etc/wireguard") / f"{WG_NAME}.conf"
VENV_PY = HOME / ".local" / "share" / "auto-freecf" / "venv" / "bin" / "python"

C_GREEN, C_RED, C_YEL, C_DIM, C_RST = "\x1b[32m", "\x1b[31m", "\x1b[33m", "\x1b[2m", "\x1b[0m"


def _pick_python() -> str:
    if VENV_PY.exists():
        return str(VENV_PY)
    return shutil.which("python3") or sys.executable


def _sudo(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["sudo", *cmd], capture_output=True, text=True)


def generate() -> bool:
    """Generate a fresh WARP profile via PetaniProxy (honours the region profile)."""
    if not (PETANI / "core" / "warp_generator.py").exists():
        print(f"{C_RED}✗{C_RST} PetaniProxy warp_generator not found")
        return False
    py = _pick_python()
    code = (
        "import sys; sys.path.insert(0,'.');"
        "from core.warp_generator import generate_and_save_warp;"
        "r=generate_and_save_warp(output_dir=None, sync_db=False);"
        "print('OK' if r else 'FAIL')"
    )
    r = subprocess.run([py, "-c", code], cwd=str(PETANI), capture_output=True, text=True)
    ok = "OK" in r.stdout
    print(f"{C_GREEN+'✓'+C_RST if ok else C_RED+'✗'+C_RST} WARP profile -> {WARP_CONF}")

    # Apply the region profile's endpoint override if set.
    ep = _region_endpoint()
    if ok and ep:
        try:
            txt = WARP_CONF.read_text()
            lines = [l for l in txt.splitlines() if not l.startswith("Endpoint =")]
            lines.append(f"Endpoint = {ep}")
            WARP_CONF.write_text("\n".join(lines) + "\n")
            print(f"{C_GREEN}✓{C_RST} endpoint pinned to {ep} (region profile)")
        except Exception as e:  # noqa: BLE001
            print(f"{C_YEL}•{C_RST} endpoint override skipped: {e}")

    if not ok:
        print(C_DIM + (r.stdout + r.stderr)[-400:] + C_RST)
    return ok


def _region_endpoint() -> str | None:
    """Read the current region profile's WARP endpoint (if any)."""
    try:
        cfg = Path.home() / ".config" / "auto-freecf" / "region.json"
        if not cfg.exists():
            return None
        name = json.loads(cfg.read_text()).get("profile", "any")
        table = {
            "us": "162.159.192.1:2408", "uk": "162.159.192.1:2408",
            "sg": "162.159.193.10:2408", "id": "162.159.193.10:2408",
            "de": "162.159.192.1:2408", "jp": "162.159.193.10:2408",
            "in": "162.159.193.10:2408", "br": "162.159.192.1:2408",
            "au": "162.159.193.10:2408", "ca": "162.159.192.1:2408",
        }
        return table.get(name)
    except Exception:
        return None


def is_up() -> bool:
    # `wg show` needs root; try plain then sudo.
    r = subprocess.run(["wg", "show", WG_NAME], capture_output=True, text=True)
    if r.returncode != 0 or "interface:" not in r.stdout:
        r = subprocess.run(["sudo", "wg", "show", WG_NAME], capture_output=True, text=True)
    return r.returncode == 0 and "interface:" in r.stdout


def up() -> bool:
    """Install the config into /etc/wireguard and bring the tunnel up."""
    if not WARP_CONF.exists():
        print(f"{C_YEL}•{C_RST} no profile yet — generating…")
        if not generate():
            return False
    if shutil.which("wg-quick") is None:
        print(f"{C_RED}✗{C_RST} wireguard-tools not installed. Run: sudo dnf install -y wireguard-tools")
        return False

    _sudo(["mkdir", "-p", "/etc/wireguard"])
    cp = _sudo(["cp", str(WARP_CONF), str(WG_SYS)])
    if cp.returncode != 0:
        print(f"{C_RED}✗{C_RST} copy failed: {cp.stderr.strip()}")
        return False

    # Re-up to pick up a fresh profile.
    _sudo(["wg-quick", "down", WG_NAME])
    r = _sudo(["wg-quick", "up", WG_NAME])
    if r.returncode != 0:
        print(f"{C_RED}✗{C_RST} wg-quick up failed:\n{r.stderr.strip()}")
        return False
    print(f"{C_GREEN}✓{C_RST} WARP tunnel up")
    return True


def down() -> bool:
    r = _sudo(["wg-quick", "down", WG_NAME])
    ok = r.returncode == 0
    print(f"{C_GREEN+'✓'+C_RST if ok else C_YEL+'•'+C_RST} WARP tunnel down")
    return ok


def status() -> dict:
    """Return tunnel state + egress info."""
    info: dict = {"up": is_up(), "interface": None, "endpoint": None, "egress_ip": None, "warp": None, "loc": None}
    if info["up"]:
        show = subprocess.run(["sudo", "wg", "show", WG_NAME], capture_output=True, text=True).stdout
        for line in show.splitlines():
            line = line.strip()
            if line.startswith("endpoint:"):
                info["endpoint"] = line.split("endpoint:", 1)[1].strip()
    # egress (prefer IPv4; also capture IPv6)
    try:
        r = subprocess.run(["curl", "-s", "-4", "-m", "12", "https://www.cloudflare.com/cdn-cgi/trace"],
                           capture_output=True, text=True)
        for line in r.stdout.splitlines():
            if line.startswith("ip="):
                info["egress_ip"] = line[3:].strip()
            elif line.startswith("warp="):
                info["warp"] = line[5:].strip()
            elif line.startswith("loc="):
                info["loc"] = line[4:].strip()
    except Exception:
        pass
    return info


ROTATE_ENDPOINTS = ["188.114.97.1:2408", "188.114.96.1:2408", "162.159.192.1:2408",
                    "162.159.193.10:2408", "188.114.98.1:2408", "188.114.99.1:2408"]

def rotate() -> bool:
    """Cycle WARP endpoints until the exit IP changes (resets per-IP caps).

    ponytail: the exit IP follows the Cloudflare endpoint, not the account, so
    regenerating the profile alone keeps the same IP. Some endpoints do not
    answer; those are skipped. Upgrade path: none needed for a handful of IPs.
    """
    import time, urllib.request
    def ip():
        try:
            return urllib.request.urlopen("https://api.ipify.org", timeout=6).read().decode().strip()
        except Exception:
            return ""
    before = ip()
    if not WARP_CONF.exists() and not generate():
        return False
    base = [l for l in WARP_CONF.read_text().splitlines() if not l.startswith("Endpoint =")]
    for ep in ROTATE_ENDPOINTS:
        WARP_CONF.write_text("\n".join(base + [f"Endpoint = {ep}"]) + "\n")
        _sudo(["cp", str(WARP_CONF), str(WG_SYS)])
        _sudo(["wg-quick", "down", WG_NAME])
        if _sudo(["wg-quick", "up", WG_NAME]).returncode != 0:
            continue
        time.sleep(3)
        after = ip()
        if after and after != before:
            print(f"{C_GREEN}✓{C_RST} egress {before} -> {after} via {ep}")
            return True
    print(f"{C_YEL}•{C_RST} no endpoint gave a new IP (still {before})")
    return False

def print_status() -> int:
    s = status()
    on = s["up"] and s["warp"] == "on"
    print()
    print(f"  WARP tunnel : {'🟢 up' if s['up'] else '⚪ down'}")
    print(f"  warp flag   : {s['warp'] or '-'}")
    print(f"  egress IP   : {s['egress_ip'] or '-'}  (loc {s['loc'] or '-'})")
    print(f"  endpoint    : {s['endpoint'] or '-'}")
    if on:
        print(f"\n  {C_GREEN}Clean Cloudflare egress active — safe to run signup.{C_RST}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "up":
        sys.exit(0 if up() else 1)
    if cmd == "down":
        sys.exit(0 if down() else 1)
    if cmd == "rotate":
        sys.exit(0 if rotate() else 1)
    if cmd == "gen":
        sys.exit(0 if generate() else 1)
    sys.exit(print_status())
