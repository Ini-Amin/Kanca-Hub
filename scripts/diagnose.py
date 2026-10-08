#!/usr/bin/env python3
"""
scripts/diagnose.py — "know where it breaks": one-line verdicts per capability.

Not another feature — the opposite. Each capability (egress, DataDome on GitHub,
Google's phone gate, OTP readiness, proxy pool, harbor keys, cloud browsers,
webhook) gets a short verdict + the exact next action when it's blocked, so you
see the WALL without running a full farm.

Pure checks (no network) live in classify_* so they're unit-testable; the
optional live probes are separate and opt-in.

Usage:
  python scripts/diagnose.py            # fast (local only)
  python scripts/diagnose.py --live     # + egress/DataDome/API probes
  python scripts/diagnose.py --json     # JSON output {name: {status, note}}
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

HOME = Path.home()
AUTO = HOME / "Auto-FreeCF"
CFG = HOME / ".config" / "auto-freecf"
NINE_DB = HOME / ".9router" / "db" / "data.sqlite"


def _env(k: str, default: str = "") -> str:
    v = os.environ.get(k)
    if v:
        return v
    try:
        for line in (CFG / ".env").read_text().splitlines():
            if line.startswith(k + "="):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return default


# ── pure classifiers ─────────────────────────────────────────────────
def classify_egress(info: dict) -> tuple[str, str]:
    if info.get("proxy"):
        return "FLAGGED", "IP flagged as proxy/VPN — DataDome/GitHub will 403; use phone mobile IP"
    if info.get("hosting"):
        return "WEAK", "datacenter IP — sites discount these; prefer residential/mobile"
    if info.get("mobile"):
        return "STRONG", "mobile carrier IP — best trust"
    return "OK", "residential/ISP IP — good with a clean browser"


def classify_github(http: int | str) -> tuple[str, str]:
    if http == 403:
        return "BLOCKED", "DataDome 403 on /signup — need a trusted residential/mobile IP"
    if http == 200:
        return "OK", "/signup reachable"
    return "UNKNOWN", f"unexpected response ({http})"


def classify_phone(verdict: str) -> tuple[str, str]:
    return {
        "ok": ("READY", "phone on mobile data (carrier IP is egress)"),
        "wifi": ("OFF", "phone on Wi-Fi — turn Wi-Fi off so the carrier IP is used"),
        "no_data": ("EMPTY", "mobile data quota exhausted / no bearer — top up"),
        "data_off": ("OFF", "mobile data switched off"),
        "airplane": ("OFF", "airplane mode on"),
        "no_sim": ("NONE", "no SIM"),
    }.get(verdict, ("UNKNOWN", f"phone verdict: {verdict}"))


def summarize(checks: dict) -> tuple[int, list[str]]:
    """(blocked_count, [headline lines])."""
    lines = []
    blocked = 0
    for name, (status, note) in checks.items():
        if status in ("BLOCKED", "FLAGGED", "EMPTY", "MISSING", "NONE"):
            blocked += 1
        lines.append(f"  {name:14} {status:8} {note}")
    return blocked, lines


def to_json(checks: dict) -> dict[str, dict[str, str]]:
    """Format checks dict as {name: {"status": status, "note": note}}."""
    return {name: {"status": status, "note": note} for name, (status, note) in checks.items()}


# ── local probes ─────────────────────────────────────────────────────
def check_env() -> dict:
    return {
        "litensi_keys": bool(_env("LITENSI_API_ID") and _env("LITENSI_API_KEY")),
        "brightdata": bool(_env("BRIGHTDATA_SCRAPING_BROWSER")),
        "tinyfish": bool(_env("TINYFISH_API_KEY")),
        "gmail_imap": bool(_env("GMAIL_FARM_APP_PASSWORD")),
    }


def check_webhook() -> tuple[str, str]:
    run = CFG / "otp_webhook"
    url = (run / "public_url.txt")
    if url.exists() and url.read_text().strip():
        return "UP", f"tunnel {url.read_text().strip()}"
    return "DOWN", "run: kancahub otp webhook up  (then paste URL into Litensi Callback SMS)"


def check_proxy_pool() -> tuple[str, str]:
    try:
        con = sqlite3.connect(NINE_DB)
        n = con.execute("SELECT COUNT(*) FROM proxyPools").fetchone()[0]
        con.close()
        return ("OK", f"{n} pool(s)") if n else ("EMPTY", "run: kancahub 9router proxy add <url>")
    except Exception as e:
        return "MISSING", f"9Router DB unreadable: {str(e)[:50]}"


def check_harbor_keys() -> tuple[str, str]:
    try:
        accts = json.loads((AUTO / "harbor" / "account.json").read_text())
        accts = accts if isinstance(accts, list) else accts.get("accounts", [])
        n = sum(1 for a in accts if a.get("api_key") or a.get("key"))
        return ("OK", f"{n} key(s)") if n else ("EMPTY", "run: kancahub thk batch")
    except Exception:
        return "MISSING", "no harbor/account.json"


def check_cloud_browsers() -> tuple[str, str]:
    have = []
    if _env("BRIGHTDATA_SCRAPING_BROWSER"):
        have.append("brightdata")
    if _env("TINYFISH_API_KEY"):
        have.append("tinyfish")
    if not have:
        return "NONE", "no cloud browser endpoint — GitHub /signup will DataDome-block"
    return "OK", "cloud browser(s): " + ", ".join(have) + " (note: BD blocks password typing; TinyFish ok)"


def check_tools() -> dict:
    return {
        "cloudflared": bool(shutil.which("cloudflared") or (HOME / ".local/bin/cloudflared").exists()),
        "adb": bool(shutil.which("adb")),
        "venv": (HOME / ".local/share/auto-freecf/venv/bin/python").exists(),
    }


def collect_checks(*, live: bool = False) -> dict[str, tuple[str, str]]:
    checks: dict[str, tuple[str, str]] = {}

    env = check_env()
    checks["litensi-otp"] = ("OK", "keys present") if env["litensi_keys"] else ("MISSING", "set LITENSI_API_ID/KEY in .env")
    checks["cloud-browser"] = check_cloud_browsers()
    checks["sms-webhook"] = check_webhook()
    checks["proxy-pool"] = check_proxy_pool()
    checks["harbor-keys"] = check_harbor_keys()
    try:
        sys.path.insert(0, str(AUTO / "scripts"))
        import mobile_rotate
        s = mobile_rotate.first_device()
        checks["phone"] = classify_phone(mobile_rotate.phone_state(s)["verdict"]) if s else ("NONE", "no phone via adb")
    except Exception:
        checks["phone"] = ("UNKNOWN", "mobile_rotate unavailable")
    tools = check_tools()
    checks["tools"] = ("OK", f"cloudflared={tools['cloudflared']} adb={tools['adb']} venv={tools['venv']}")

    if live:
        try:
            import urllib.request
            with urllib.request.urlopen("http://ip-api.com/json/?fields=query,isp,hosting,proxy,mobile", timeout=15) as r:
                info = json.loads(r.read().decode())
            checks["egress"] = classify_egress(info)
            checks["  egress-ip"] = ("--", info.get("query", "?"))
        except Exception as e:
            checks["egress"] = ("UNKNOWN", f"probe failed: {str(e)[:40]}")
        try:
            import urllib.error, urllib.request
            req = urllib.request.Request("https://github.com/signup", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                checks["github-signup"] = classify_github(r.status)
        except urllib.error.HTTPError as he:
            checks["github-signup"] = classify_github(he.code)
        except Exception as e:
            checks["github-signup"] = ("UNKNOWN", str(e)[:50])

    return checks


def run(*, live: bool = False, as_json: bool = False, **kwargs) -> int:
    if kwargs.get("json"):
        as_json = True
    checks = collect_checks(live=live)
    if as_json:
        print(json.dumps(to_json(checks), indent=2))
        return 0

    print("  ── diagnose: where each tool breaks ──\n")
    blocked, lines = summarize(checks)
    print("\n".join(lines))
    print(f"\n  {blocked} blocking issue(s). Fix the FLAGGED/EMPTY/MISSING ones top-down.")
    print("  fast mode: add --live to probe egress + github.com/signup.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Where each tool breaks (one-line verdicts)")
    ap.add_argument("--live", action="store_true", help="also probe egress IP + github.com/signup")
    ap.add_argument("--json", action="store_true", help="output as JSON object {name: {status, note}}")
    a = ap.parse_args(argv)
    return run(live=a.live, as_json=a.json)


if __name__ == "__main__":
    sys.exit(main())