"""commands_9router — 9Router, adb, and mobile commands for kancahub."""
from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.error
import urllib.request


def cmd_adb(a) -> int:
    """Android/ADB helper (connect a phone for trusted Google signups)."""
    import kancahub
    py = kancahub.pick_python()
    tool = kancahub.AUTO_FREECF / "scripts" / "adb_tool.py"
    if not tool.exists():
        print(kancahub.col("red", "✗ adb_tool.py not found"))
        return 1
    sub = getattr(a, "adb_cmd", None) or "status"
    cmd = [py, str(tool), sub]
    if sub == "connect" and getattr(a, "addr", None):
        cmd.append(a.addr)
    return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)


def cmd_mobile(a) -> int:
    """Rotate/expose the tethered phone's MOBILE carrier IP (a real mobile IP is
    accepted by TokenHarbor/GitHub where datacenter/WARP are blocked)."""
    import kancahub
    py = kancahub.pick_python()
    sub = getattr(a, "mobile_cmd", None) or "status"
    if sub == "saving":
        tool = kancahub.AUTO_FREECF / "scripts" / "mobile_saving.py"
        if not tool.exists():
            print(kancahub.col("red", "✗ mobile_saving.py not found"))
            return 1
        cmd = [py, str(tool), getattr(a, "action", "status")]
        if getattr(a, "action", "") == "start":
            cmd += ["--cap-mb", str(getattr(a, "cap_mb", 50))]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)
    tool = kancahub.AUTO_FREECF / "scripts" / "mobile_rotate.py"
    if not tool.exists():
        print(kancahub.col("red", "✗ mobile_rotate.py not found"))
        return 1
    cmd = [py, str(tool)]
    if sub == "status":
        cmd.append("--status")
    elif sub == "rotate":
        cmd.append("--rotate")
        if getattr(a, "until", None):
            cmd += ["--rotate-until", a.until]
        if getattr(a, "wait", None):
            cmd += ["--wait", str(a.wait)]
        if getattr(a, "force", False):
            cmd.append("--force")
    return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)


def _r9_cli_token() -> str:
    """9Router x-9r-cli-token = sha256(machineId + '9r-cli-auth' + cliSecret)[:16]."""
    import hashlib
    env_token = os.environ.get("R9_TOKEN") or os.environ.get("NINE_ROUTER_CLI_TOKEN")
    if env_token:
        return env_token.strip()
    try:
        mid = (Path.home() / ".9router" / "machine-id").read_text().strip()
        sec = (Path.home() / ".9router" / "auth" / "cli-secret").read_text().strip()
        return hashlib.sha256((mid + "9r-cli-auth" + sec).encode()).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return ""


def _r9_api(method: str, path: str, *, body: dict | None = None, base: str = "http://localhost:20128") -> tuple[int, str]:
    import kancahub
    import urllib.request as _u
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = _u.Request(url, data=data, method=method)
    req.add_header("x-9r-cli-token", kancahub._r9_cli_token())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _u.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def cmd_9router(a) -> int:
    """9Router helpers — including the MITM proxy (route Antigravity/Copilot/Kiro
    IDE traffic through 9Router). Mirrors the web /dashboard/mitm page in the CLI."""
    import kancahub
    py = kancahub.pick_python(camoufox=True)  # browser helpers here use nodriver/CDP
    sub = getattr(a, "r9_cmd", None) or "status"
    base = getattr(a, "base", None) or "http://localhost:20128"

    if sub == "proxy":
        tool = kancahub.AUTO_FREECF / "scripts" / "inject_9router_proxy.py"
        if not tool.exists():
            print(kancahub.col("red", "✗ inject_9router_proxy.py not found"))
            return 1
        action = getattr(a, "action", "list")
        cmd = [kancahub.pick_python(), str(tool), action]
        if action == "add" and getattr(a, "proxy_url", None):
            cmd.append(a.proxy_url)
            if getattr(a, "name", ""):
                cmd += ["--name", a.name]
            cmd += ["--type", getattr(a, "type", "http")]
        elif action == "add-file" and getattr(a, "proxy_url", None):
            cmd.append(a.proxy_url)
            if getattr(a, "name", ""):
                cmd += ["--name", a.name]
            cmd += ["--type", getattr(a, "type", "http")]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    if sub == "mitm":
        action = getattr(a, "action", "status")
        tool = getattr(a, "tool", "antigravity")
        if action == "status":
            code, resp = kancahub._r9_api("GET", "/api/cli-tools/antigravity-mitm", base=base)
            if code != 200:
                print(kancahub.col("red", f"✗ 9Router mitm status failed (HTTP {code}): {resp[:200]}"))
                print(kancahub.col("dim", "  is 9Router running on :20128?  kancahub doctor"))
                return 1
            try:
                d = json.loads(resp)
            except Exception:  # noqa: BLE001
                print(resp)
                return 0
            print(kancahub.col("bold", "\n  9Router MITM proxy status:\n"))
            print(f"   running      : {'✅ yes' if d.get('running') else '➖ no'}")
            print(f"   cert exists  : {'✅' if d.get('certExists') else '➖'}   trusted: {'✅' if d.get('certTrusted') else '➖'}")
            dns = d.get("dnsStatus", {})
            print(f"   dns redirects: " + ", ".join(f"{k}={'on' if v else 'off'}" for k, v in dns.items()))
            print(f"   needs sudo   : {d.get('needsSudoPassword')}")
            print(kancahub.col("dim", f"   router URL   : {d.get('mitmRouterBaseUrl')}"))
            print(kancahub.col("dim", "   enable: kancahub 9router mitm enable --tool antigravity --sudo-password <pw>"))
            return 0
        if action in ("enable", "disable", "trust-cert"):
            # 9Router writes ~/.9router/mitm/.mitm.lock WITHOUT creating the dir ->
            # ENOENT. Pre-create it (the known fix/workaround).
            try:
                (Path.home() / ".9router" / "mitm" / "logs").mkdir(parents=True, exist_ok=True)
            except Exception:  # noqa: BLE001
                pass
            body = {"tool": tool, "action": action}
            # The enable endpoint requires an apiKey. Reuse the caller's --api-key,
            # else auto-fetch a 9Router API key.
            api_key = getattr(a, "api_key", None) or os.environ.get("GROK2API_KEY")
            if not api_key:
                kcode, kresp = kancahub._r9_api("GET", "/api/keys", base=base)
                if kcode == 200:
                    try:
                        ks = json.loads(kresp).get("keys", [])
                        if ks:
                            api_key = ks[0].get("key")
                    except Exception:  # noqa: BLE001
                        pass
            if api_key:
                body["apiKey"] = api_key
            pw = getattr(a, "sudo_password", None) or os.environ.get("KANCAHUB_SUDO_PASSWORD")
            if pw:
                body["sudoPassword"] = pw
            code, resp = kancahub._r9_api("POST", "/api/cli-tools/antigravity-mitm", body=body, base=base)
            ok = 200 <= code < 300
            print(kancahub.col("green" if ok else "red", f"  {'✓' if ok else '✗'} mitm {action} ({tool}) -> HTTP {code}"))
            if resp:
                try:
                    d = json.loads(resp)
                    if d.get("running") is not None:
                        print(kancahub.col("dim", f"  running={d.get('running')} pid={d.get('pid')}"))
                except Exception:  # noqa: BLE001
                    print(kancahub.col("dim", f"  {resp[:200]}"))
            if not ok and "sudo" in resp.lower():
                print(kancahub.col("yellow", "  needs a sudo password: pass --sudo-password or set KANCAHUB_SUDO_PASSWORD"))
            return 0 if ok else 1
        print(kancahub.col("yellow", f"unknown mitm action '{action}'"))
        return 1

    if sub == "combo":
        action = getattr(a, "action", "list") or "list"
        if action == "list":
            code, resp = kancahub._r9_api("GET", "/api/combos", base=base)
            if code != 200:
                print(kancahub.col("red", f"✗ combo list failed (HTTP {code}): {resp[:150]}"))
                return 1
            try:
                combos = json.loads(resp).get("combos", [])
            except Exception:  # noqa: BLE001
                combos = []
            print(kancahub.col("bold", f"\n  9Router combos ({len(combos)}):\n"))
            for c in combos:
                print(f"   • {c.get('name'):<20} {len(c.get('models', []))} models  id={c.get('id','')[:8]}")
            return 0
        if action == "make-thk-fallback":
            models = getattr(a, "models", None)
            if not models:
                models = "THK/deepseek-v4.1-flash:free,THK/qwen3.8-flash:free,THK/mimo-v2.6-flash:free,THK/mimo-v2.5:free"
            body = {"name": getattr(a, "name", "thk-fallback"), "models": [m.strip() for m in models.split(",") if m.strip()], "kind": "chat"}
            code, resp = kancahub._r9_api("POST", "/api/combos", body=body, base=base)
            ok = 200 <= code < 300
            print(kancahub.col("green" if ok else "red", f"  {'✓' if ok else '✗'} combo {body['name']} -> HTTP {code}"))
            if not ok:
                print(kancahub.col("dim", f"  {resp[:200]}"))
            return 0 if ok else 1
        print(kancahub.col("yellow", f"unknown combo action '{action}' (list|make-thk-fallback)"))
        return 1

    if sub == "keyguard":
        return _r9_keyguard(a, base)

    print(kancahub.col("bold", "\n  9Router CLI\n"))
    print("   [status] 9Router health")
    print("   mitm status|enable|disable|trust-cert   (route IDE traffic through 9Router)")
    print("   combo list|make-thk-fallback             (fallback model groups)")
    print("   keyguard status|watch|farm|reset         (auto-farm when healthy keys run low)")
    return 0


def _r9_keyguard(a, base: str) -> int:
    """Thin wrapper over scripts/r9_keyguard.py so it shares kancahub's python/env."""
    import kancahub
    tool = kancahub.AUTO_FREECF / "scripts" / "r9_keyguard.py"
    if not tool.exists():
        print(kancahub.col("red", "✗ r9_keyguard.py not found"))
        return 1
    action = getattr(a, "kg_action", None) or "status"
    cmd = [kancahub.pick_python(), str(tool), action, "--base", base,
           "--threshold", str(getattr(a, "threshold", 2) or 2)]
    if getattr(a, "farm_url", None):
        cmd += ["--url", a.farm_url]
    if action == "watch":
        cmd += ["--interval", str(getattr(a, "interval", 300) or 300),
                "--cooldown", str(getattr(a, "cooldown", 3600) or 3600)]
        if getattr(a, "no_headless", False):
            cmd.append("--no-headless")
    if getattr(a, "dry_run", False):
        cmd.append("--dry-run")
    return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)
