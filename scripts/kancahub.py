#!/usr/bin/env python3
"""
KancaHub — one CLI for the whole account-farming toolkit.

Unifies four projects behind a single command surface, exposing their real
features (not just pass-throughs):

  stack   Auto-FreeCF + PetaniProxy-aware Cloudflare pipeline
            signup (create accounts) · login (existing accounts/Google) ·
            validate · sync (prune dead 9Router conns) · manage · web
  proxy   PetaniProxy
            harvest · fast · gateway/serve · daemon · residential (Webshare) ·
            warp · grok (xAI farm) · pipeline · sync9r · export · stats · api
  warp    Cloudflare WARP tunnel (clean egress IPs for signup)
  region  signup region profiles (promo/bonus targeting: US/UK/SG/ID/…)
  thk     TokenHarbor (harbor): create keys + inject/sync with 9Router
  grok    Grok xAI farm (grok-register: SSO risk gate, 5 mail providers, pool)
  k12     ChatGPT K-12 teacher verification (SheerID)
            auto (full account+verify) · verify (URL) · modes
  yowes   13-country teacher document generator
            list · schools · make · k12 (US teacher docs) · gui · mcp
  doctor  health/dependency check across everything

Examples
--------
  kancahub doctor
  kancahub warp up                          # clean Cloudflare egress
  kancahub region set us                     # target US promos
  kancahub stack signup -n 3 --warp          # full pipeline on WARP
  kancahub stack sync --prune                # drop dead 9Router connections
  kancahub proxy daemon                       # 24/7 auto-healing gateway :8888
  kancahub proxy residential -n 2             # Webshare hunter
  kancahub thk batch 3 && kancahub thk inject # TokenHarbor keys -> 9Router
  kancahub grok run                           # grok-register farm (CLI)
  kancahub k12 auto                           # ChatGPT signup + SheerID verify
  kancahub yowes make --country us --first John --last Doe \
      --school "Norton Elementary"

Run `kancahub <group> <cmd> --help` for options.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
PETANI = HOME / "petani-proxy"
K12_ROOT = PETANI / "Farm-Acc-ChatGPT-K-12-Teachers"
K12_DIR = K12_ROOT / "PyRuntime_64"
YOWES = PETANI / "yowes"
HARBOR = HOME / "harbor"
GROK_REG = HOME / "grok-register"
NINE_ROUTER_DB = HOME / ".9router" / "db" / "data.sqlite"
VENV_PY = HOME / ".local" / "share" / "auto-freecf" / "venv" / "bin" / "python"

GATEWAY_DEFAULT = "http://127.0.0.1:8888"
RES_GW_DEFAULT = "http://127.0.0.1:8899"

C = {
    "reset": "\x1b[0m", "bold": "\x1b[1m", "dim": "\x1b[2m",
    "cyan": "\x1b[36m", "green": "\x1b[32m", "yellow": "\x1b[33m",
    "red": "\x1b[31m", "magenta": "\x1b[35m",
}


def col(name: str, text: str) -> str:
    return f"{C.get(name, '')}{text}{C['reset']}"


def banner(title: str) -> None:
    print()
    print(col("cyan", "═" * 68))
    print(col("bold", f"  {title}"))
    print(col("cyan", "═" * 68))


def pick_python() -> str:
    v = str(VENV_PY)
    if VENV_PY.exists() and os.access(v, os.X_OK):
        return v
    return shutil.which("python3") or sys.executable


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> int:
    print(col("dim", f"$ {' '.join(str(c) for c in cmd)}" + (f"   (cwd={cwd})" if cwd else "")))
    e = os.environ.copy()
    if env:
        e.update(env)
    try:
        return subprocess.call([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=e)
    except FileNotFoundError as ex:
        print(col("red", f"✗ {ex}"))
        return 127


def _gw_get(path: str, gateway: str = GATEWAY_DEFAULT) -> dict | None:
    try:
        with urllib.request.urlopen(gateway.rstrip("/") + path, timeout=6) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        print(col("red", f"✗ gateway {path}: {e}"))
        return None


def _gateway_alive(gateway: str = GATEWAY_DEFAULT) -> bool:
    try:
        with urllib.request.urlopen(gateway.rstrip("/") + "/api/status", timeout=4):
            return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════ doctor

def cmd_doctor(_a) -> int:
    banner("KancaHub doctor")
    py = pick_python()
    print(f"  python : {py}\n")

    files = [
        ("Auto-FreeCF dir", AUTO_FREECF.exists()),
        ("  signup main.py", (AUTO_FREECF / "signup_from_scratch" / "main.py").exists()),
        ("  cli.js (moycf)", (AUTO_FREECF / "cli.js").exists()),
        ("  web_ui.py", (AUTO_FREECF / "web_ui.py").exists()),
        ("  pipeline.py", (AUTO_FREECF / "scripts" / "pipeline.py").exists()),
        ("  inject_9router.py", (AUTO_FREECF / "scripts" / "inject_9router.py").exists()),
        ("  residential_gateway.py", (AUTO_FREECF / "scripts" / "residential_gateway.py").exists()),
        ("  cf_workerai_manager.py", (AUTO_FREECF / "cf_workerai_manager.py").exists()),
        ("PetaniProxy dir", PETANI.exists()),
        ("  main.py", (PETANI / "main.py").exists()),
        ("  core/server.py", (PETANI / "core" / "server.py").exists()),
        ("K-12 script.py", (K12_DIR / "script.py").exists()),
        ("K-12 auto_k12_flow.py", (K12_DIR / "auto_k12_flow.py").exists()),
        ("K-12 gen doc bridge", (K12_ROOT / "generate_teacher_doc.py").exists()),
        ("Yowes dir", YOWES.exists()),
        ("  countries/", (YOWES / "countries").exists()),
        ("  main_gui.py", (YOWES / "main_gui.py").exists()),
        ("harbor (TokenHarbor)", (HARBOR / "tools" / "tokenharbor").exists()),
        ("grok-register", (GROK_REG / "grok_register_ttk.py").exists()),
        ("  registration_flow", (GROK_REG / "registration_flow.py").exists()),
        ("turnstilePatch", (PETANI / "core" / "turnstilePatch").exists()),
        ("9Router DB", NINE_ROUTER_DB.exists()),
        ("Secrets .env", (HOME / ".config" / "auto-freecf" / ".env").exists()),
        ("Proxies pool", (AUTO_FREECF / "signup_from_scratch" / "proxies.txt").exists()),
        ("WARP config", (PETANI / "output" / "warp" / "warp.conf").exists()),
        ("Region profile", (HOME / ".config" / "auto-freecf" / "region.json").exists()),
    ]
    for label, ok in files:
        print(f"  {'✅' if ok else '❌'} {label}")

    print(col("bold", "\n  Python modules:"))
    for mod in ("nodriver", "patchright", "httpx", "requests", "curl_cffi",
                "cloudscraper", "DrissionPage", "speech_recognition", "pydub",
                "PIL", "mcp", "customtkinter", "rich", "tomllib", "fastapi"):
        r = subprocess.run([py, "-c", f"import {mod}"], capture_output=True)
        print(f"  {'✅' if r.returncode == 0 else '❌'} {mod}")

    print(col("bold", "\n  Binaries:"))
    for b in ("google-chrome", "ffmpeg", "git", "adb", "wg", "sing-box"):
        print(f"  {'✅' if shutil.which(b) else '➖'} {b}")

    petani_gw = _gateway_alive(GATEWAY_DEFAULT)
    print(f"\n  {'✅' if petani_gw else '➖'} PetaniProxy gateway :8888 {'(running)' if petani_gw else '(not running)'}")

    # WARP
    try:
        import subprocess as _sp
        wm = AUTO_FREECF / "scripts" / "warp_manager.py"
        if wm.exists():
            r = _sp.run([py, str(wm), "status"], capture_output=True, text=True)
            up = "🟢 up" in r.stdout
            print(f"  {'✅' if up else '➖'} WARP tunnel {'(up)' if up else '(down)'}")
    except Exception:
        pass
    return 0


# ═══════════════════════════════════════════════════════════════ proxy

def cmd_proxy(a) -> int:
    py = pick_python()
    petani = PETANI / "main.py"
    if not petani.exists():
        print(col("red", f"✗ PetaniProxy not found at {PETANI}"))
        return 1
    sub = a.proxy_cmd

    def petani_cmd(extra: list[str]) -> int:
        return run([py, str(petani)] + [str(x) for x in extra], cwd=PETANI)

    if sub == "harvest":
        cmd = ["--target", a.target, "--max", a.max, "--workers", a.workers,
               "--timeout", a.timeout]
        if a.protocol:
            cmd += ["--protocol", a.protocol]
        if a.country:
            cmd += ["--country", a.country]
        if a.anonymity:
            cmd += ["--anonymity", a.anonymity]
        if a.target_url:
            cmd += ["--target-url", a.target_url]
        if a.loop:
            cmd += ["--loop", a.loop]
        if a.sync_9router:
            cmd += ["--sync-9router", a.sync_9router]
        if a.serve:
            cmd += ["--serve", a.serve]
        return petani_cmd(cmd)

    if sub == "fast":
        cmd = ["--fast-harvest", a.target, "--max-latency", a.max_latency]
        return petani_cmd(cmd)

    if sub == "serve" or sub == "gateway":
        print(col("yellow", f"Rotating gateway + REST API + dashboard on :{a.port}"))
        print(col("dim", f"  dashboard: http://127.0.0.1:{a.port}/dashboard"))
        print(col("dim", f"  PAC:       http://127.0.0.1:{a.port}/proxy.pac"))
        return petani_cmd(["--serve", a.port, "--target", a.target])

    if sub == "daemon":
        print(col("yellow", "24/7 auto-healing gateway on :8888 (Ctrl-C to stop)"))
        return petani_cmd(["--daemon-gateway"])

    if sub == "residential":
        if a.headless:
            return petani_cmd(["--webshare", a.accounts, "--headless"])
        return petani_cmd(["--webshare", a.accounts])

    if sub == "warp":
        return petani_cmd(["--warp"])

    if sub == "grok":
        cmd = ["--grok-farm", a.accounts]
        if a.headless:
            cmd += ["--headless"]
        if a.mail_provider:
            cmd += ["--mail-provider", a.mail_provider]
        return petani_cmd(cmd)

    if sub == "pipeline":
        cmd = ["--pipeline", a.accounts]
        if a.headless:
            cmd += ["--headless"]
        return petani_cmd(cmd)

    if sub == "sync9r":
        path = a.db or "auto"
        print(col("cyan", f"Syncing harvested proxies into 9Router DB ({path})"))
        return petani_cmd(["--target", a.target, "--sync-9router", path])

    if sub == "export":
        return _proxy_export(a)

    if sub == "test":
        return _proxy_test_pool(a)

    if sub == "stats":
        return _proxy_stats(a)

    if sub == "api":
        return _proxy_api(a)

    print(col("red", "✗ unknown proxy command"))
    return 1


def _proxy_export(a) -> int:
    src = PETANI / "output" / "live_elite.txt"
    if not src.exists():
        src = PETANI / "output" / "live_all.txt"
    if not src.exists():
        print(col("red", "✗ no harvested proxies — run `kancahub proxy harvest`"))
        return 1
    dst = AUTO_FREECF / "signup_from_scratch" / "proxies.txt"
    lines = [l.strip() for l in src.read_text().splitlines() if l.strip()]
    if a.to_pool:
        dst.write_text("\n".join(lines) + "\n")
        print(col("green", f"✓ {len(lines)} proxies -> {dst}"))
    else:
        for l in lines[:a.limit]:
            print(" ", l)
        print(col("dim", f"({len(lines)} total; use --to-pool to write the Auto-FreeCF pool)"))
    return 0


def _proxy_test_pool(a) -> int:
    dst = Path(a.pool) if a.pool else (AUTO_FREECF / "signup_from_scratch" / "proxies.txt")
    if not dst.exists():
        print(col("red", f"✗ pool not found: {dst}"))
        return 1
    lines = [l.strip() for l in dst.read_text().splitlines() if l.strip()]
    ok = 0
    for p in lines:
        ip = subprocess.run(["curl", "-s", "-m", "10", "-x", p, "https://api.ipify.org"],
                            capture_output=True, text=True).stdout.strip()
        print(f"  {'✅' if ip else '❌'} {ip or 'dead'}")
        ok += bool(ip)
    print(col("green", f"\n  {ok}/{len(lines)} live"))
    return 0


def _proxy_stats(a) -> int:
    gw = a.gateway
    banner(f"Gateway stats ({gw})")
    st = _gw_get("/api/status", gw)
    if st:
        print(json.dumps(st, indent=2)[:2000])
    else:
        print(col("yellow", "Gateway not reachable. Start it: kancahub proxy daemon"))
    return 0


def _proxy_api(a) -> int:
    gw = a.gateway
    path = a.path
    if not path.startswith("/"):
        path = "/" + path
    data = _gw_get(path, gw)
    if data is not None:
        print(json.dumps(data, indent=2)[:4000])
        return 0
    return 1


# ═══════════════════════════════════════════════════════════════ stack

def cmd_stack(a) -> int:
    py = pick_python()
    pip = AUTO_FREECF / "scripts" / "pipeline.py"
    inj = AUTO_FREECF / "scripts" / "inject_9router.py"
    signup_main = AUTO_FREECF / "signup_from_scratch" / "main.py"
    sub = a.stack_cmd

    if sub == "signup":
        return _stack_signup(a, py, pip)

    if sub == "login":
        return _stack_login(a, py)

    if sub == "inject":
        cmd = [py, str(inj)]
        if a.input:
            cmd += ["-i", a.input]
        if a.model:
            cmd += ["--model", a.model]
        if a.db:
            cmd += ["--db", a.db]
        if a.dry_run:
            cmd += ["--dry-run"]
        if not a.no_verify:
            cmd += ["--verify"]
        return run(cmd, cwd=AUTO_FREECF)

    if sub == "validate":
        if not a.token or not a.account_id:
            print(col("red", "✗ validate needs --token and --account-id"))
            return 1
        cmd = [py, str(signup_main), "--validate-only",
               "--token", a.token, "--account-id", a.account_id]
        return run(cmd, cwd=AUTO_FREECF / "signup_from_scratch")

    if sub == "sync":
        sync = AUTO_FREECF / "scripts" / "sync_9router.py"
        cmd = [py, str(sync)]
        if a.db:
            cmd += ["--db", a.db]
        if a.prune:
            cmd += ["--prune"]
        if a.deactivate:
            cmd += ["--deactivate"]
        if a.export_clean:
            cmd += ["--export-clean", a.export_clean]
        return run(cmd, cwd=AUTO_FREECF)

    if sub == "manage":
        return _stack_manage(a, py)

    if sub == "web":
        print(col("cyan", f"Starting Auto-FreeCF Web UI on :{a.port}"))
        cmd = [py, "web_ui.py", "--port", str(a.port)]
        if a.open:
            cmd += ["--open"]
        return run(cmd, cwd=AUTO_FREECF)

    print(col("red", "✗ unknown stack command"))
    return 1


def _stack_signup(a, py, pip) -> int:
    # Optionally bring WARP up for a clean Cloudflare egress, tear it down after.
    warp_was_down = False
    if getattr(a, "warp", False):
        wm = AUTO_FREECF / "scripts" / "warp_manager.py"
        st = subprocess.run([py, str(wm), "status"], capture_output=True, text=True)
        if "up" not in st.stdout:
            warp_was_down = True
        print(col("cyan", "Bringing up WARP tunnel for clean egress…"))
        if run([py, str(wm), "up"]) != 0:
            print(col("yellow", "⚠️ WARP up failed — continuing without it"))
        else:
            # Let routing/DNS settle, then warm up the exact hosts the run will
            # use (WARP's first connection on a fresh tunnel is often reset).
            import time as _t
            print(col("dim", "Waiting for WARP route to settle…"))
            _t.sleep(8)
            hosts = [
                "https://api.ipify.org",
                "https://dash.cloudflare.com/sign-up",
            ]
            # include the configured mail backend host if available
            try:
                cfg = json.loads((AUTO_FREECF / "signup_from_scratch" / "config.json").read_text())
                api = cfg.get("mail_api") or ""
                if api:
                    from urllib.parse import urlparse
                    hosts.append(f"{urlparse(api).scheme}://{urlparse(api).netloc}")
            except Exception:
                pass
            for h in hosts:
                for attempt in range(3):
                    try:
                        req = urllib.request.Request(h, headers={"User-Agent": "Mozilla/5.0"})
                        urllib.request.urlopen(req, timeout=15).read(1)
                        break
                    except Exception:
                        _t.sleep(2)
            print(col("green", "✓ egress warmed up"))

    try:
        # Prefer the one-shot pipeline (signup -> verify -> inject) when injecting.
        if not a.no_inject:
            cmd = [py, str(pip), "-n", str(a.accounts), "--output", a.output]
            if a.gateway:
                cmd += ["--gateway", a.gateway]
            if a.proxy:
                cmd += ["--proxy", a.proxy]
            if a.proxy_pool:
                cmd += ["--proxy-pool", a.proxy_pool]
            if a.headless:
                cmd += ["--headless"]
            if a.fast:
                cmd += ["--fast"]
            if a.workers:
                cmd += ["--workers", str(a.workers)]
            if getattr(a, "delay", None) is not None:
                cmd += ["--delay", str(a.delay)]
            if getattr(a, "retry", None) is not None:
                cmd += ["--retry", str(a.retry)]
            return run(cmd, cwd=AUTO_FREECF)

        # Signup-only path: call signup main directly (supports --export-txt).
        cmd = [py, "main.py", "--accounts", str(a.accounts), "--output", a.output]
        if a.proxy:
            cmd += ["--proxy", a.proxy]
        if a.proxy_pool:
            cmd += ["--proxy-pool", a.proxy_pool]
        if a.headless:
            cmd += ["--headless"]
        if a.fast:
            cmd += ["--fast"]
        if a.workers:
            cmd += ["--workers", str(a.workers)]
        if a.export_txt:
            cmd += ["--export-txt", a.export_txt]
        return run(cmd, cwd=AUTO_FREECF / "signup_from_scratch")
    finally:
        if getattr(a, "warp", False) and warp_was_down:
            print(col("dim", "Tearing down WARP tunnel…"))
            run([py, str(AUTO_FREECF / "scripts" / "warp_manager.py"), "down"])


def _stack_login(a, py) -> int:
    """Login to EXISTING Cloudflare accounts (email/password or Google)."""
    browser_bot = AUTO_FREECF / "browser_bot.py"
    if a.bulk:
        cmd = [py, str(browser_bot), "--accounts", a.bulk]
    elif a.account:
        cmd = [py, str(browser_bot), "--single", a.account]
    else:
        print(col("red", "✗ provide an account (email:password) or --bulk <file>"))
        return 1
    if a.google:
        cmd += ["--login-method", "google"]
    if a.proxy:
        cmd += ["--proxy", a.proxy]
    if a.visible:
        cmd += ["--visible"]
    return run(cmd, cwd=AUTO_FREECF)


def _stack_manage(a, py) -> int:
    mgr = AUTO_FREECF / "cf_workerai_manager.py"
    cmd = [py, str(mgr)]
    if a.token:
        cmd += ["--token", a.token]
    if a.token_file:
        cmd += ["--token-file", a.token_file]
    if a.model:
        cmd += ["--model", a.model]
    if a.out_json:
        cmd += ["--out-json", a.out_json]
    if a.out_csv:
        cmd += ["--out-csv", a.out_csv]
    if a.no_test:
        cmd += ["--no-test"]
    return run(cmd, cwd=AUTO_FREECF)


# ═══════════════════════════════════════════════════════════════ thk

def cmd_thk(a) -> int:
    py = pick_python()
    sub = a.thk_cmd
    harbor = HARBOR / "tools" / "tokenharbor"

    if sub in ("setup", "batch", "create-key", "test-key", "enable-free",
               "check-proxies", "status"):
        if not harbor.exists():
            print(col("red", f"✗ harbor not found at {HARBOR}"))
            return 1
        cfg = harbor / "config.toml"
        if not cfg.exists():
            (harbor / "config.toml").write_text((harbor / "example.config.toml").read_text())
            print(col("yellow", f"• created {cfg} from example — set [tempik].base_url + capsolver key"))
        cmd = [py, "-m", "tools.tokenharbor.cli", sub]
        # pass through common optionals
        for flag, val in (("--email", getattr(a, "email", None)),
                          ("--password", getattr(a, "password", None)),
                          ("--label", getattr(a, "label", None))):
            if val:
                cmd += [flag, val]
        if sub == "batch" and getattr(a, "count", None):
            cmd = [py, "-m", "tools.tokenharbor.cli", "batch", str(a.count)]
        if sub == "test-key" and getattr(a, "key", None):
            cmd = [py, "-m", "tools.tokenharbor.cli", "test-key", a.key]
        return run(cmd, cwd=HARBOR)

    if sub == "inject":
        inj = AUTO_FREECF / "scripts" / "inject_thk_9router.py"
        cmd = [py, str(inj)]
        if a.input:
            cmd += ["-i", a.input]
        if a.model:
            cmd += ["--model", a.model]
        if a.verify:
            cmd.append("--verify")
        if a.dry_run:
            cmd.append("--dry-run")
        return run(cmd, cwd=AUTO_FREECF)

    if sub == "sync":
        return _thk_sync(a, py)

    print(col("red", "✗ unknown thk command"))
    return 1


def _thk_sync(a, py) -> int:
    """Verify + prune TokenHarbor connections in 9Router."""
    import sqlite3
    db = Path(a.db or NINE_ROUTER_DB)
    if not db.exists():
        print(col("red", f"✗ 9Router DB not found: {db}"))
        return 1
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    node = None
    for nid, data in con.execute("SELECT id, data FROM providerNodes"):
        try:
            if json.loads(data or "{}").get("prefix") == "THK":
                node = nid
        except Exception:
            pass
    if not node:
        print(col("yellow", "No TokenHarbor node in 9Router — run `kancahub thk inject` first."))
        con.close()
        return 0
    rows = list(con.execute("SELECT id, name, data FROM providerConnections WHERE provider=?", (node,)))
    print(f"Testing {len(rows)} TokenHarbor connection(s)…")
    dead = []
    for r in rows:
        dd = json.loads(r["data"]); key = dd["apiKey"]; model = dd.get("defaultModel") or "deepseek-v4.1-flash:free"
        url = f"https://tokenharbor.ai/v1/chat/completions"
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                ok = resp.status == 200
        except Exception as e:
            ok = False
        print(f"  {'✅' if ok else '❌'} {r['name']:34s}")
        if not ok:
            dead.append(r["id"])
    if a.prune and dead:
        for cid in dead:
            con.execute("DELETE FROM providerConnections WHERE id=?", (cid,))
        con.commit()
        print(f"  ✓ removed {len(dead)} dead")
    con.close()
    return 0


# ═══════════════════════════════════════════════════════════════ grok

def cmd_grok(a) -> int:
    """Grok farm — grok-register backend (preferred) or PetaniProxy fallback."""
    py = pick_python()
    sub = a.grok_cmd or "run"

    if sub == "run":
        if (GROK_REG / "grok_register_ttk.py").exists() and not a.petani:
            cmd = [py, "grok_register_ttk.py", "cli"]
            print(col("cyan", "Using grok-register backend (SSO risk gate, 5 mail providers, pool export)"))
            return run(cmd, cwd=GROK_REG)
        # fallback to PetaniProxy
        petani = PETANI / "main.py"
        cmd = [py, str(petani), "--grok-farm", str(getattr(a, "accounts", 1))]
        if getattr(a, "headless", False):
            cmd += ["--headless"]
        print(col("yellow", "Using PetaniProxy grok farm fallback"))
        return run(cmd, cwd=PETANI)

    if sub == "web":
        if not (GROK_REG / "web").exists():
            print(col("red", "✗ grok-register web/ not found"))
            return 1
        print(col("cyan", "Grok-register WebUI -> http://127.0.0.1:8092"))
        return run([py, "-m", "web.server"], cwd=GROK_REG)

    if sub == "gui":
        return run([py, "grok_register_ttk.py"], cwd=GROK_REG)

    if sub == "retry":
        if not a.pending:
            print(col("red", "✗ retry needs --pending <file.jsonl>"))
            return 1
        cmd = [py, "grok_register_ttk.py", "retry-pending", a.pending]
        if a.out:
            cmd.append(a.out)
        return run(cmd, cwd=GROK_REG)

    if sub == "pool":
        # show grok2api local token pool if present
        tk = GROK_REG / "token.json"
        if tk.exists():
            data = json.loads(tk.read_text())
            n = len(data.get("ssoBasic", []))
            print(col("green", f"grok2api local pool: {n} token(s) -> {tk}"))
        else:
            print(col("dim", f"no grok2api pool yet ({tk})"))
        return 0

    print(col("red", "✗ unknown grok command"))
    return 1


# ═══════════════════════════════════════════════════════════════ region

def cmd_region(a) -> int:
    py = pick_python()
    reg = AUTO_FREECF / "scripts" / "regions.py"
    if not reg.exists():
        print(col("red", f"✗ regions.py not found"))
        return 1
    if a.region_cmd in (None, "current"):
        return run([py, str(reg), "current"])
    if a.region_cmd == "list":
        return run([py, str(reg), "list"])
    if a.region_cmd == "set":
        return run([py, str(reg), "set", a.name])
    if a.region_cmd == "clear":
        return run([py, str(reg), "clear"])
    if a.region_cmd == "show":
        return run([py, str(reg), "show", a.name])
    print(col("red", "✗ unknown region command"))
    return 1


# ═══════════════════════════════════════════════════════════════ k12

def cmd_k12(a) -> int:
    py = pick_python()
    sub = a.k12_cmd

    if sub == "verify":
        script = K12_DIR / "script.py"
        if not script.exists():
            print(col("red", f"✗ {script} not found"))
            return 1
        if not a.url:
            print(col("red", "✗ url required: kancahub k12 verify <sheerid-url>"))
            return 1
        cmd = [py, str(script), a.url]
        proxy = a.proxy
        if not proxy and a.gateway:
            proxy = "127.0.0.1:8888"
        if proxy:
            cmd += ["--proxy", proxy]
        if a.debug:
            cmd += ["--debug"]
        if a.email:
            cmd += ["--email", a.email]
        if a.no_temp_email:
            cmd += ["--no-temp-email"]
        if a.ask_email:
            cmd += ["--ask-email"]
        return run(cmd, cwd=K12_DIR)

    if sub == "auto":
        flow = AUTO_FREECF / "scripts" / "auto_k12_flow_kancahub.py"
        if not flow.exists():
            flow = K12_DIR / "auto_k12_flow.py"
        if not flow.exists():
            print(col("red", "✗ auto_k12_flow not found"))
            return 1
        print(col("cyan", "Full auto flow: ChatGPT signup -> OTP -> session capture -> SheerID verify"))
        print(col("dim", "  sessions -> k12_sessions.json  |  creds -> created_k12_accounts.txt"))
        # run from the K-12 dir so `from script import K12Verifier` resolves
        return run([py, str(flow)], cwd=K12_DIR)

    if sub == "inject":
        inj = AUTO_FREECF / "scripts" / "chatgpt_9router.py"
        if not inj.exists():
            print(col("red", "✗ chatgpt_9router.py not found"))
            return 1
        sess = a.session
        if not sess:
            for cand in (K12_DIR / "k12_sessions.json", Path.cwd() / "k12_sessions.json"):
                if cand.exists():
                    sess = str(cand); break
        if not sess:
            print(col("red", "✗ no session file. Run `kancahub k12 auto` first, or pass --session <file>"))
            return 1
        cmd = [py, str(inj), "inject", "--session", sess]
        if a.dry_run:
            cmd.append("--dry-run")
        return run(cmd, cwd=AUTO_FREECF)

    if sub == "sync":
        inj = AUTO_FREECF / "scripts" / "chatgpt_9router.py"
        cmd = [py, str(inj), "sync"]
        if a.prune:
            cmd.append("--prune")
        return run(cmd, cwd=AUTO_FREECF)

    if sub == "modes":
        print(col("bold", "\nK-12 connection modes (maps to run_cmd.bat [1]-[12]):"))
        rows = [
            ("1", "direct + temp email", "kancahub k12 verify <url>"),
            ("2", "proxy ip:port", "kancahub k12 verify <url> --proxy IP:PORT"),
            ("3", "proxy auth", "kancahub k12 verify <url> --proxy user:pass@IP:PORT"),
            ("4", "debug no proxy", "kancahub k12 verify <url> --debug"),
            ("7", "no temp email", "kancahub k12 verify <url> --no-temp-email"),
            ("10", "manual email", "kancahub k12 verify <url> --email you@x.com"),
            ("--", "use local gateway", "kancahub k12 verify <url> --gateway"),
            ("--", "full auto account", "kancahub k12 auto"),
        ]
        for n, label, ex in rows:
            print(f"  [{n:>2}] {label:22s} {col('dim', ex)}")
        return 0

    print(col("red", "✗ unknown k12 command"))
    return 1


# ═══════════════════════════════════════════════════════════════ warp

def cmd_warp(a) -> int:
    py = pick_python()
    wm = AUTO_FREECF / "scripts" / "warp_manager.py"
    if not wm.exists():
        print(col("red", f"✗ warp_manager.py not found at {wm}"))
        return 1
    sub = a.warp_cmd or "status"
    if sub == "up":
        return run([py, str(wm), "up"])
    if sub == "down":
        return run([py, str(wm), "down"])
    if sub == "gen":
        return run([py, str(wm), "gen"])
    # status
    return run([py, str(wm), "status"])


# ═══════════════════════════════════════════════════════════════ yowes

def cmd_yowes(a) -> int:
    py = pick_python()
    if not YOWES.exists():
        print(col("red", f"✗ Yowes not found at {YOWES}"))
        return 1
    sub = a.yowes_cmd

    if sub == "list":
        script = (
            "import sys; sys.path.insert(0,'.');"
            "from countries import list_countries, get_country;"
            "print('Countries & document types:');"
            "[print(f'  {c:14s} {get_country(c)().get_country_name():14s} -> ' + ', '.join(get_country(c)().get_document_types())) for c in list_countries()]"
        )
        return run([py, "-c", script], cwd=YOWES)

    if sub == "schools":
        script = (
            "import sys; sys.path.insert(0,'.');"
            "from countries import get_country;"
            f"gen=get_country('{a.country}')();"
            "print(f'{len(gen.schools)} schools for', gen.get_country_name());"
            "[print('  ', s['name']) for s in gen.schools]"
        )
        return run([py, "-c", script], cwd=YOWES)

    if sub == "make":
        types = a.types.split(",") if a.types else None
        script = f"""
import sys; sys.path.insert(0,'.')
from mcp_server import generate_documents
res = generate_documents(
    country={a.country!r}, first_name={a.first!r}, last_name={a.last!r},
    school_name={a.school!r}, position={a.position!r}, date_of_birth={a.dob!r},
    gender={a.gender!r}, document_types={types!r}, output_dir={a.out!r},
)
print('✓ generated', res['count'], 'document(s) ->', res['output_dir'])
for f in res['files']: print('   ', f)
"""
        return run([py, "-c", script], cwd=YOWES)

    if sub == "k12":
        # Bridge: use Tool A's generate_teacher_doc.py (wraps yowes USGenerator)
        bridge = K12_ROOT / "generate_teacher_doc.py"
        if not bridge.exists():
            print(col("red", f"✗ {bridge} not found"))
            return 1
        cmd = [py, str(bridge)]
        if a.first:
            cmd += ["--first", a.first]
        if a.last:
            cmd += ["--last", a.last]
        if a.school:
            cmd += ["--school", a.school]
        if a.out:
            cmd += ["--out", a.out]
        return run(cmd, cwd=K12_ROOT)

    if sub == "gui":
        gui = YOWES / "main_gui.py"
        return run([py, str(gui)], cwd=YOWES)

    if sub == "mcp":
        return run([py, "mcp_server.py"], cwd=YOWES)

    print(col("red", "✗ unknown yowes command"))
    return 1


# ═══════════════════════════════════════════════════════════════ parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kancahub",
        description="KancaHub — unified CLI: Cloudflare farming, proxies, K-12 verification & docs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="group")
    sub.add_parser("doctor", help="health + dependency check")

    # ---- warp ----
    wp = sub.add_parser("warp", help="Cloudflare WARP tunnel (clean egress IPs)")
    ws = wp.add_subparsers(dest="warp_cmd")
    ws.add_parser("up", help="bring the WARP tunnel up")
    ws.add_parser("down", help="bring the WARP tunnel down")
    ws.add_parser("gen", help="generate a fresh WARP profile")
    ws.add_parser("status", help="show tunnel state + egress IP (default)")

    # ---- region ----
    rp = sub.add_parser("region", help="signup region profile (promo/bonus targeting)")
    rs = rp.add_subparsers(dest="region_cmd")
    rs.add_parser("list", help="list available regions")
    rs.add_parser("current", help="show the active region (default)")
    rset = rs.add_parser("set", help="set the active region")
    rset.add_argument("name")
    rsh = rs.add_parser("show", help="show one region's details")
    rsh.add_argument("name")
    rs.add_parser("clear", help="reset to auto (nearest)")

    # ---- thk (TokenHarbor via harbor) ----
    tp = sub.add_parser("thk", help="TokenHarbor (harbor): create keys + inject to 9Router")
    ts = tp.add_subparsers(dest="thk_cmd")
    ts.add_parser("setup", help="full setup on TokenHarbor (interactive)")
    tb = ts.add_parser("batch", help="create N TokenHarbor accounts")
    tb.add_argument("count", nargs="?", type=int, default=1)
    ts.add_parser("create-key", help="create an API key for an existing account")
    tk = ts.add_parser("test-key", help="test a thk_ key")
    tk.add_argument("key")
    ts.add_parser("enable-free", help="enable free models for an account")
    ts.add_parser("check-proxies", help="scan configured proxies")
    ts.add_parser("status", help="account free-tier status")
    ti = ts.add_parser("inject", help="inject thk_ keys into 9Router")
    ti.add_argument("-i", "--input", default=None)
    ti.add_argument("--model", default=None)
    ti.add_argument("--verify", action="store_true")
    ti.add_argument("--dry-run", action="store_true")
    tsy = ts.add_parser("sync", help="verify + prune TokenHarbor connections")
    tsy.add_argument("--db", default=None)
    tsy.add_argument("--prune", action="store_true")

    # ---- grok (xAI) ----
    gp = sub.add_parser("grok", help="Grok xAI account farm (grok-register)")
    gs = gp.add_subparsers(dest="grok_cmd")
    gr = gs.add_parser("run", help="run the registration flow")
    gr.add_argument("-n", "--accounts", type=int, default=1)
    gr.add_argument("--headless", action="store_true")
    gr.add_argument("--petani", action="store_true", help="use PetaniProxy farm instead")
    gs.add_parser("web", help="launch the WebUI (127.0.0.1:8092)")
    gs.add_parser("gui", help="launch the Tk GUI")
    grt = gs.add_parser("retry", help="retry a pending file")
    grt.add_argument("--pending", default=None)
    grt.add_argument("--out", default=None)
    gs.add_parser("pool", help="show the grok2api token pool")

    # ---- proxy ----
    pp = sub.add_parser("proxy", help="PetaniProxy: harvest/gateway/warp/farm/...")
    ps = pp.add_subparsers(dest="proxy_cmd")

    h = ps.add_parser("harvest", help="harvest + validate public proxies")
    h.add_argument("--target", type=int, default=15)
    h.add_argument("--max", type=int, default=250)
    h.add_argument("--workers", type=int, default=50)
    h.add_argument("--timeout", type=float, default=3.0)
    h.add_argument("--protocol", choices=["all", "http", "socks4", "socks5"], default=None)
    h.add_argument("--country", default=None, help="ISO code (US, SG, ID…)")
    h.add_argument("--anonymity", choices=["all", "elite", "anonymous", "transparent"], default=None)
    h.add_argument("--target-url", default=None)
    h.add_argument("--loop", type=int, default=None, help="auto-refresh every N minutes")
    h.add_argument("--sync-9router", default=None, help="9Router data.sqlite path or 'auto'")
    h.add_argument("--serve", type=int, default=None, help="also start gateway on port")

    f = ps.add_parser("fast", help="ultra-fast aiohttp harvester")
    f.add_argument("--target", type=int, default=15)
    f.add_argument("--max-latency", type=int, default=1200)

    for name in ("serve", "gateway"):
        g = ps.add_parser(name, help="start rotating gateway + REST API + dashboard")
        g.add_argument("--port", type=int, default=8888)
        g.add_argument("--target", type=int, default=30)

    ps.add_parser("daemon", help="24/7 auto-healing gateway on :8888")

    r = ps.add_parser("residential", help="Webshare residential hunter")
    r.add_argument("-n", "--accounts", type=int, default=1)
    r.add_argument("--headless", action="store_true")

    ps.add_parser("warp", help="generate Cloudflare WARP WireGuard profile")

    gr = ps.add_parser("grok", help="farm Grok xAI accounts")
    gr.add_argument("-n", "--accounts", type=int, default=1)
    gr.add_argument("--headless", action="store_true")
    gr.add_argument("--mail-provider", choices=["duckmail", "gmail"], default=None)

    pl = ps.add_parser("pipeline", help="async pipeline: Webshare + Grok concurrently")
    pl.add_argument("-n", "--accounts", type=int, default=10)
    pl.add_argument("--headless", action="store_true")

    s9 = ps.add_parser("sync9r", help="harvest and sync straight into 9Router DB")
    s9.add_argument("--target", type=int, default=20)
    s9.add_argument("--db", default=None)

    e = ps.add_parser("export", help="export harvested proxies")
    e.add_argument("--to-pool", action="store_true", help="write Auto-FreeCF proxies.txt")
    e.add_argument("--limit", type=int, default=20)

    t = ps.add_parser("test", help="validate a proxy pool")
    t.add_argument("--pool", default=None, help="pool file (default: Auto-FreeCF pool)")

    st = ps.add_parser("stats", help="live gateway stats")
    st.add_argument("--gateway", default=GATEWAY_DEFAULT)

    ap = ps.add_parser("api", help="call a gateway REST endpoint")
    ap.add_argument("path", help="e.g. /api/all or /api/leak-test")
    ap.add_argument("--gateway", default=GATEWAY_DEFAULT)

    # ---- stack ----
    sp = sub.add_parser("stack", help="Auto-FreeCF: signup/login/validate/manage")
    ss = sp.add_subparsers(dest="stack_cmd")

    sg = ss.add_parser("signup", help="create new Cloudflare accounts + tokens")
    sg.add_argument("-n", "--accounts", type=int, default=1)
    sg.add_argument("--gateway", nargs="?", const=GATEWAY_DEFAULT, default=None)
    sg.add_argument("--proxy", default=None)
    sg.add_argument("--proxy-pool", default=None)
    sg.add_argument("--workers", type=int, default=None)
    sg.add_argument("--headless", action="store_true")
    sg.add_argument("--fast", action="store_true")
    sg.add_argument("--warp", action="store_true",
                    help="bring WARP tunnel up first (clean Cloudflare egress), down after")
    sg.add_argument("--no-inject", action="store_true", help="skip 9Router injection")
    sg.add_argument("--export-txt", default=None, help="9Router-friendly txt (signup-only mode)")
    sg.add_argument("--output", default="results.json")
    sg.add_argument("--delay", type=int, default=None, help="seconds between accounts")
    sg.add_argument("--retry", type=int, default=None, help="retry attempts per account")

    sl = ss.add_parser("login", help="login to EXISTING accounts (email or Google)")
    sl.add_argument("account", nargs="?", help="email:password")
    sl.add_argument("--bulk", default=None, help="file of email:password lines")
    sl.add_argument("--google", action="store_true", help="Google OAuth login")
    sl.add_argument("--proxy", default=None, help="proxy config JSON file")
    sl.add_argument("--visible", action="store_true", help="show browser window")

    si = ss.add_parser("inject", help="inject existing results into 9Router")
    si.add_argument("-i", "--input", default=None)
    si.add_argument("--model", default=None)
    si.add_argument("--db", default=None)
    si.add_argument("--no-verify", action="store_true")
    si.add_argument("--dry-run", action="store_true")

    sv = ss.add_parser("validate", help="validate a single cfut_ token")
    sv.add_argument("--token", required=True)
    sv.add_argument("--account-id", required=True)

    sy = ss.add_parser("sync", help="verify + prune dead 9Router connections")
    sy.add_argument("--db", default=None)
    sy.add_argument("--prune", action="store_true", help="remove dead connections")
    sy.add_argument("--deactivate", action="store_true", help="with --prune: deactivate instead of delete")
    sy.add_argument("--export-clean", default=None, help="write working keys to a file")

    sm = ss.add_parser("manage", help="verify/list CF tokens (cf_workerai_manager)")
    sm.add_argument("--token", default=None)
    sm.add_argument("--token-file", default=None)
    sm.add_argument("--model", default=None)
    sm.add_argument("--out-json", default=None)
    sm.add_argument("--out-csv", default=None)
    sm.add_argument("--no-test", action="store_true")

    sw = ss.add_parser("web", help="launch Auto-FreeCF web UI")
    sw.add_argument("--port", type=int, default=8080)
    sw.add_argument("--open", action="store_true")

    # ---- k12 ----
    kp = sub.add_parser("k12", help="ChatGPT K-12 teacher verification")
    ks = kp.add_subparsers(dest="k12_cmd")
    kv = ks.add_parser("verify", help="verify a SheerID URL")
    kv.add_argument("url", nargs="?")
    kv.add_argument("--proxy", default=None, help="IP:port or user:pass@ip:port")
    kv.add_argument("--gateway", action="store_true", help="use 127.0.0.1:8888")
    kv.add_argument("--debug", action="store_true")
    kv.add_argument("--email", default=None)
    kv.add_argument("--no-temp-email", action="store_true")
    kv.add_argument("--ask-email", action="store_true")
    ks.add_parser("auto", help="full auto: ChatGPT signup + OTP + session capture + SheerID verify")
    ki = ks.add_parser("inject", help="inject captured ChatGPT sessions into 9Router (codex)")
    ki.add_argument("--session", default=None, help="session json (default: auto-detect k12_sessions.json)")
    ki.add_argument("--dry-run", action="store_true")
    ksy = ks.add_parser("sync", help="verify + prune ChatGPT (codex) connections")
    ksy.add_argument("--prune", action="store_true")
    ks.add_parser("modes", help="show the 12 connection modes")

    # ---- yowes ----
    yp = sub.add_parser("yowes", help="teacher document generator (13 countries)")
    ys = yp.add_subparsers(dest="yowes_cmd")
    ys.add_parser("list", help="list countries + document types")
    ysch = ys.add_parser("schools", help="list schools for a country")
    ysch.add_argument("--country", required=True)
    ym = ys.add_parser("make", help="generate documents")
    ym.add_argument("--country", required=True)
    ym.add_argument("--first", required=True)
    ym.add_argument("--last", required=True)
    ym.add_argument("--school", required=True)
    ym.add_argument("--position", default="Teacher")
    ym.add_argument("--dob", default="1985-03-15")
    ym.add_argument("--gender", default="Random", choices=["Random", "Male", "Female"])
    ym.add_argument("--types", default=None, help="comma list e.g. teacher_id,employment_letter")
    ym.add_argument("--out", default="")
    yk = ys.add_parser("k12", help="US teacher docs via the K-12 bridge")
    yk.add_argument("--first", default=None)
    yk.add_argument("--last", default=None)
    yk.add_argument("--school", default=None)
    yk.add_argument("--out", default=None)
    ys.add_parser("gui", help="launch the legacy desktop GUI")
    ys.add_parser("mcp", help="run the yowes MCP server (stdio)")

    return p


def main() -> int:
    p = build_parser()
    args = p.parse_args()
    g = args.group

    if g == "doctor":
        return cmd_doctor(args)
    if g == "warp":
        return cmd_warp(args)
    if g == "region":
        return cmd_region(args)
    if g == "thk":
        if not getattr(args, "thk_cmd", None):
            p.parse_args(["thk", "--help"]); return 1
        return cmd_thk(args)
    if g == "grok":
        return cmd_grok(args)
    if g == "proxy":
        if not getattr(args, "proxy_cmd", None):
            p.parse_args(["proxy", "--help"])
            return 1
        return cmd_proxy(args)
    if g == "stack":
        if not getattr(args, "stack_cmd", None):
            p.parse_args(["stack", "--help"])
            return 1
        return cmd_stack(args)
    if g == "k12":
        if not getattr(args, "k12_cmd", None):
            p.parse_args(["k12", "--help"])
            return 1
        return cmd_k12(args)
    if g == "yowes":
        if not getattr(args, "yowes_cmd", None):
            p.parse_args(["yowes", "--help"])
            return 1
        return cmd_yowes(args)

    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
