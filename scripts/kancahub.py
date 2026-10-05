#!/usr/bin/env python3
"""
KancaHub — one CLI for the whole account-farming toolkit.

Unifies four projects behind a single command surface, exposing their real
features (not just pass-throughs):

  stack   Auto-FreeCF + PetaniProxy-aware Cloudflare pipeline
            signup (create accounts) · login (existing accounts/Google) ·
            cookie-import (Cookie-Editor JSON -> token) ·
            validate · sync (prune dead 9Router conns) · manage · web
  proxy   PetaniProxy
            harvest · fast · gateway/serve · daemon · residential (Webshare) ·
            res-gateway (bridge for authenticated proxies) ·
            warp · grok (xAI farm) · pipeline · sync9r · export · stats · api
  warp    Cloudflare WARP tunnel (clean egress IPs for signup)
  region  signup region profiles (promo/bonus targeting: US/UK/SG/ID/…)
  thk     TokenHarbor (harbor): create keys + inject/sync with 9Router
  grok    Grok xAI farm (grok-register: SSO risk gate, 5 mail providers, pool)
            run · web · gui · retry · pool · inject (SSO tokens -> 9Router via grok2api)
  github  GitHub Education account farm (signup + Education form helper)
            farm (--index N) · check   (CAPTCHA / ID-photo steps stay manual)
  mail    School mailbox (BINUS M365) via browser — read signup OTPs
            test (selftest) · otp (--timeout) · login
  gmail   Gmail account farm (gmail-account-creator, nodriver)
            farm (--count/--headless/--proxy/--out) · check
  k12     ChatGPT K-12 teacher verification (SheerID)
            auto (full account+verify) · verify (URL) · inject · sync ·
            link-finder (find SheerID links) · modes
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
  kancahub stack cookie-import cookies.json akun-1   # Cookie-Editor export -> accounts.json
  kancahub proxy daemon                       # 24/7 auto-healing gateway :8888
  kancahub proxy residential -n 2             # Webshare hunter
  kancahub proxy res-gateway --pool res.txt --port 8899
  kancahub thk batch 3 && kancahub thk inject # TokenHarbor keys -> 9Router
  kancahub grok run                           # grok-register farm (CLI)
  kancahub grok inject --base-url http://127.0.0.1:8000 --dry-run
  kancahub github farm --index 1 --dry-run    # GitHub signup + Education helper
  kancahub k12 auto                           # ChatGPT signup + SheerID verify
  kancahub k12 link-finder                    # find SheerID verification links
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
import urllib.error
import urllib.request
from pathlib import Path

try:
    import colorama
    colorama.init()
except Exception:
    pass

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
CAMOUFOX_PY = HOME / ".local" / "share" / "auto-freecf" / "camoufox-venv" / "bin" / "python"
CAMOUFOX_CACHE = HOME / ".cache" / "camoufox"
TEMPIK_URL = "https://tempik.kancalabs.workers.dev/api/config"
THK_NODE_ID = "openai-compatible-chat-1d39647b-193d-4f65-b38b-03d80c92460a"
SCHOOL_PROFILE = HOME / ".config" / "auto-freecf" / "school-profile"
ENV_FILE = HOME / ".config" / "auto-freecf" / ".env"

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


def get_ascii_banner() -> str:
    cyan = C["cyan"]
    bold = C["bold"]
    reset = C["reset"]

    art = f"""{cyan}
 ██╗  ██╗ █████╗ ███╗   ██╗ ██████╗ █████╗ ██╗  ██╗██╗   ██╗██████╗ 
 ██║ ██╔╝██╔══██╗████╗  ██║██╔════╝██╔══██╗██║  ██║██║   ██║██╔══██╗
 █████╔╝ ███████║██╔██╗ ██║██║     ███████║███████║██║   ██║██████╔╝
 ██╔═██╗ ██╔══██║██║╚██╗██║██║     ██╔══██║██╔══██║██║   ██║██╔══██╗
 ██║  ██╗██║  ██║██║ ╚████║╚██████╗██║  ██║██║  ██║╚██████╔╝██████╔╝
 ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝{reset}"""
    box_top = f"{cyan} ╔══════════════════════════════════════════════════════════════════════╗{reset}"
    tagline = "one CLI for the whole account-farming toolkit"
    box_mid = f"{cyan} ║ {bold}{tagline.center(68)}{reset}{cyan} ║{reset}"
    box_bot = f"{cyan} ╚══════════════════════════════════════════════════════════════════════╝{reset}"
    return f"{art}\n{box_top}\n{box_mid}\n{box_bot}"


class KancaHubParser(argparse.ArgumentParser):
    """Custom parser displaying the big ASCII banner at the top of root --help."""

    def format_help(self) -> str:
        text = super().format_help()
        if self.prog == "kancahub":
            return get_ascii_banner() + "\n\n" + text
        return text


CAMOUFOX_VENV_PY = HOME / ".local" / "share" / "auto-freecf" / "camoufox-venv" / "bin" / "python"


def pick_python(camoufox: bool = False) -> str:
    """Interpreter for child scripts.

    camoufox=True returns the isolated python3.11 venv that has camoufox +
    playwright (scripts converted to Camoufox MUST run there; the main venv is
    python3.13 and has neither). Falls back to the main venv if absent.
    """
    if camoufox:
        cv = str(CAMOUFOX_VENV_PY)
        if CAMOUFOX_VENV_PY.exists() and os.access(cv, os.X_OK):
            return cv
        print(col("yellow", "⚠️ camoufox venv not found; falling back to the main venv "
                            "(camoufox/playwright may be missing)"))
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

def _http_status(url: str, timeout: float = 6.0) -> int | None:
    """GET a URL and return the HTTP status code, or None on any failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kancahub-doctor/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode() or 200
    except urllib.error.HTTPError as e:  # reachable, just not 2xx
        return e.code
    except Exception:
        return None

def _env_get(key: str, env_file: Path) -> str:
    """Read a single KEY=value out of a dotenv file (no interpolation)."""
    if not env_file.exists():
        return ""
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(key + "=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""

def _count_lines(p: Path) -> int:
    try:
        return sum(1 for ln in p.read_text().splitlines() if ln.strip())
    except OSError:
        return 0


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
        ("  proxy_gateway.py", (AUTO_FREECF / "scripts" / "proxy_gateway.py").exists()),
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

    # ── Camoufox (isolated venv + fetched browser binary) ──────────
    print(col("bold", "\n  Camoufox:"))
    cf_py = CAMOUFOX_PY
    cf_py_ok = cf_py.exists() and os.access(str(cf_py), os.X_OK)
    print(f"  {'✅' if cf_py_ok else '❌'} camoufox venv python ({cf_py})")
    if cf_py_ok:
        r = subprocess.run(
            [str(cf_py), "-c", "import camoufox, playwright"],
            capture_output=True, text=True,
        )
        imp_ok = r.returncode == 0
        print(f"  {'✅' if imp_ok else '❌'} import camoufox + playwright")
        if not imp_ok:
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            if tail:
                print(col("dim", f"      {tail[-1][:100]}"))
    else:
        print("  ❌ import camoufox + playwright (venv python missing)")
    cf_cache = CAMOUFOX_CACHE
    try:
        cf_fetched = cf_cache.exists() and any(cf_cache.iterdir())
    except OSError:
        cf_fetched = False
    if cf_fetched:
        n_entries = len(list(cf_cache.iterdir()))
        print(f"  ✅ browser binary fetched (~/.cache/camoufox, {n_entries} entries)")
    else:
        print("  ❌ browser binary fetched (~/.cache/camoufox empty — run: camoufox fetch)")

    # ── Tempik mail worker (HTTP reachability) ─────────────────────
    print(col("bold", "\n  Tempik:"))
    tempik_code = _http_status(TEMPIK_URL, timeout=6.0)
    if tempik_code == 200:
        print(f"  ✅ GET {TEMPIK_URL} -> 200")
    elif tempik_code is None:
        print(f"  ❌ GET {TEMPIK_URL} -> unreachable")
    else:
        print(f"  ❌ GET {TEMPIK_URL} -> HTTP {tempik_code} (expected 200)")

    # ── School mailbox (M365 / BINUS) ──────────────────────────────
    print(col("bold", "\n  School mailbox:"))
    env_file = ENV_FILE
    school_email = _env_get("SCHOOL_EMAIL", env_file)
    print(f"  {'✅' if school_email else '❌'} SCHOOL_EMAIL "
          f"{school_email if school_email else '(not set in ' + str(env_file) + ')'}")
    school_pw = bool(_env_get("SCHOOL_MAIL_PASSWORD", env_file))
    print(f"  {'✅' if school_pw else '❌'} SCHOOL_MAIL_PASSWORD set")
    prof = SCHOOL_PROFILE
    prof_ok = prof.is_dir() and any(prof.iterdir())
    if prof_ok:
        print(f"  ✅ school profile dir ({prof.name}, logged-in session cached)")
    elif prof.is_dir():
        print(f"  ➖ school profile dir ({prof.name} exists but empty — run: kancahub mail test)")
    else:
        print(f"  ❌ school profile dir ({prof} missing)")

    # ── Outputs / state files ──────────────────────────────────────
    print(col("bold", "\n  Outputs & state:"))
    state = [
        ("results.json (CF signup)", AUTO_FREECF / "results.json"),
        ("results.json (signup_from_scratch)", AUTO_FREECF / "signup_from_scratch" / "results.json"),
        ("github_accounts.json", AUTO_FREECF / "github_accounts.json"),
        ("k12_sessions.json", K12_DIR / "k12_sessions.json"),
        ("region.json", HOME / ".config" / "auto-freecf" / "region.json"),
    ]
    for label, path in state:
        if path.exists():
            size = path.stat().st_size
            extra = ""
            if path.name == "github_accounts.json" or path.name == "results.json":
                try:
                    data = json.loads(path.read_text())
                    n = len(data) if isinstance(data, list) else len(data.get("accounts", [])) if isinstance(data, dict) else 0
                    extra = f", {n} entries"
                except Exception:
                    extra = ""
            print(f"  ✅ {label} ({size} bytes{extra})")
        else:
            print(f"  ➖ {label} (absent)")

    proxies = AUTO_FREECF / "signup_from_scratch" / "proxies.txt"
    if proxies.exists():
        n_prox = _count_lines(proxies)
        print(f"  {'✅' if n_prox else '➖'} proxies.txt ({n_prox} proxies)")
    else:
        print("  ❌ proxies.txt (missing)")

    # ── 9Router connection inventory ───────────────────────────────
    print(col("bold", "\n  9Router connections:"))
    if NINE_ROUTER_DB.exists():
        import sqlite3
        try:
            con = sqlite3.connect(f"file:{NINE_ROUTER_DB}?mode=ro", uri=True)
            def _cnt(where: str, params: tuple = ()) -> tuple[int, int]:
                row = con.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(isActive), 0) FROM providerConnections {where}",
                    params,
                ).fetchone()
                return int(row[0]), int(row[1])
            cf_t, cf_a = _cnt("WHERE provider = ?", ("cloudflare-ai",))
            cx_t, cx_a = _cnt("WHERE provider = ?", ("codex",))
            thk_t, thk_a = _cnt("WHERE provider = ?", (THK_NODE_ID,))
            xai_t, xai_a = _cnt("WHERE provider = ?", ("xai",))
            print(f"  {'✅' if cf_t else '➖'} cloudflare-ai : {cf_t} total, {cf_a} active")
            print(f"  {'✅' if cx_t else '➖'} codex (ChatGPT): {cx_t} total, {cx_a} active")
            print(f"  {'✅' if thk_t else '➖'} TokenHarbor (thk): {thk_t} total, {thk_a} active")
            print(f"  {'✅' if xai_t else '➖'} xai (Grok OAuth): {xai_t} total, {xai_a} active")
            con.close()
        except Exception as e:  # noqa: BLE001
            print(col("red", f"  ❌ could not read DB: {e}"))
    else:
        print(col("red", f"  ❌ 9Router DB not found: {NINE_ROUTER_DB}"))

    # ── Scripts built since the last doctor pass ───────────────────
    print(col("bold", "\n  Scripts:"))
    wanted = [
        "proxy_gateway.py", "grok_driver.py", "grok_9router.py", "github_farm.py",
        "sheerid_link_finder.py", "school_mail_browser.py", "gmail_creator.py",
        "harbor_config.py", "camoufox_helpers.py",
    ]
    for name in wanted:
        p = AUTO_FREECF / "scripts" / name
        print(f"  {'✅' if p.exists() else '❌'} {name}")

    print()
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

    if sub == "res-gateway":
        gw = AUTO_FREECF / "scripts" / "proxy_gateway.py"
        cmd = [py, str(gw), "--pool", a.pool, "--port", str(a.port)]
        if getattr(a, "scheme", None):
            cmd += ["--scheme", a.scheme]
        return run(cmd, cwd=AUTO_FREECF)

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

    if sub == "cookie-import":
        # process_cookies.py usage: process_cookies.py <cookies.json> <account_label>
        # (sys.argv[1]=cookie file, sys.argv[2]=label; writes accounts.json beside itself).
        pc = AUTO_FREECF / "process_cookies.py"
        if not pc.exists():
            print(col("red", f"✗ process_cookies.py not found at {pc}"))
            return 1
        cookies = Path(a.cookies).expanduser().resolve()  # absolute: subprocess runs in AUTO_FREECF
        if not cookies.exists():
            print(col("red", f"✗ cookies file not found: {cookies}"))
            return 1
        return run([py, str(pc), str(cookies), a.label], cwd=AUTO_FREECF)

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

    if sub == "setup-env":
        helper = AUTO_FREECF / "scripts" / "harbor_config.py"
        if not helper.exists():
            print(col("red", f"✗ harbor_config.py not found at {helper}"))
            return 1
        cmd = [py, str(helper)]
        for flag, val in (("--harbor-dir", getattr(a, "harbor_dir", None)),
                          ("--env-file", getattr(a, "env_file", None)),
                          ("--proxies-src", getattr(a, "proxies_src", None)),
                          ("--tempik-url", getattr(a, "tempik_url", None))):
            if val:
                cmd += [flag, val]
        if getattr(a, "no_proxies", False):
            cmd.append("--no-proxies")
        if getattr(a, "status", False):
            cmd.append("--status")
        if getattr(a, "dry_run", False):
            cmd.append("--dry-run")
        return run(cmd, cwd=AUTO_FREECF)

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
        driver = AUTO_FREECF / "scripts" / "grok_driver.py"
        if driver.exists() and not a.petani:
            cmd = [py, str(driver), "-n", str(getattr(a, "accounts", 1))]
            if getattr(a, "headless", False):
                cmd += ["--headless"]
            if getattr(a, "proxy_pool", None):
                cmd += ["--proxy-pool", a.proxy_pool]
            if getattr(a, "proxy", None):
                cmd += ["--proxy", a.proxy]
            if getattr(a, "workers", None):
                cmd += ["--workers", str(a.workers)]
            print(col("cyan", "Using grok non-interactive driver (SSO risk gate, relay mail, pool export)"))
            return run(cmd, cwd=AUTO_FREECF)
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

    if sub == "inject":
        inj = AUTO_FREECF / "scripts" / "grok_9router.py"
        if not inj.exists():
            print(col("red", f"✗ grok_9router.py not found at {inj}"))
            return 1
        cmd = [py, str(inj)]
        if a.input:
            cmd += ["-i", str(Path(a.input).expanduser())]
        if a.base_url:
            cmd += ["--base-url", a.base_url]
        if a.verify:
            cmd.append("--verify")
        if a.dry_run:
            cmd.append("--dry-run")
        return run(cmd, cwd=AUTO_FREECF)

    print(col("red", "✗ unknown grok command"))
    return 1


# ═══════════════════════════════════════════════════════════════ github

def cmd_github(a) -> int:
    """GitHub Education / account farm — wraps scripts/github_farm.py."""
    py = pick_python(camoufox=True)
    farm = AUTO_FREECF / "scripts" / "github_farm.py"
    if not farm.exists():
        print(col("red", f"✗ github_farm.py not found at {farm}"))
        return 1
    sub = a.github_cmd

    if sub == "check":
        return run([py, str(farm), "--check"], cwd=AUTO_FREECF)

    if sub == "farm":
        cmd = [py, str(farm), "--index", str(a.index)]
        if a.headless:
            cmd.append("--headless")
        if a.proxy:
            cmd += ["--proxy", a.proxy]
        if a.dry_run:
            cmd.append("--dry-run")
        print(col("yellow", "Note: CAPTCHA and the Education ID/photo attestation remain manual steps."))
        return run(cmd, cwd=AUTO_FREECF)

    print(col("red", "✗ unknown github command"))
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
    py_camo = pick_python(camoufox=True)
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

    if sub == "link-finder":
        finder = AUTO_FREECF / "scripts" / "sheerid_link_finder.py"
        if not finder.exists():
            print(col("red", f"✗ sheerid_link_finder.py not found at {finder}"))
            print(col("dim", "  It has not been created yet — add scripts/sheerid_link_finder.py, then re-run."))
            return 1
        extra = [x for x in (a.extra or [])]
        if extra and extra[0] == "--":
            extra = extra[1:]
        return run([py_camo, str(finder)] + extra, cwd=AUTO_FREECF)

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
    p = KancaHubParser(
        prog="kancahub",
        description="KancaHub — unified CLI: Cloudflare farming, proxies, K-12 verification & docs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="group")
    sub.add_parser("doctor", help="health + dependency check across all tools, services & proxies")

    # ---- warp ----
    wp = sub.add_parser("warp", help="Cloudflare WARP: manage clean egress IPs to prevent signup blocks")
    ws = wp.add_subparsers(dest="warp_cmd")
    ws.add_parser("up", help="bring the WARP tunnel up")
    ws.add_parser("down", help="bring the WARP tunnel down")
    ws.add_parser("gen", help="generate a fresh WARP profile")
    ws.add_parser("status", help="show tunnel state + egress IP (default)")

    # ---- region ----
    rp = sub.add_parser("region", help="region profiles: target geo-specific signup promos & bonuses (US/UK/SG/ID)")
    rs = rp.add_subparsers(dest="region_cmd")
    rs.add_parser("list", help="list available regions")
    rs.add_parser("current", help="show the active region (default)")
    rset = rs.add_parser("set", help="set the active region")
    rset.add_argument("name")
    rsh = rs.add_parser("show", help="show one region's details")
    rsh.add_argument("name")
    rs.add_parser("clear", help="reset to auto (nearest)")

    # ---- thk (TokenHarbor via harbor) ----
    tp = sub.add_parser("thk", help="TokenHarbor: generate free API keys and inject/sync with 9Router")
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
    gp = sub.add_parser("grok", help="Grok xAI farm: automated account creation with multi-provider mail")
    gs = gp.add_subparsers(dest="grok_cmd")
    gr = gs.add_parser("run", help="run the registration flow")
    gr.add_argument("-n", "--accounts", type=int, default=1)
    gr.add_argument("--headless", action="store_true")
    gr.add_argument("--proxy-pool", default=None, help="path to proxy pool file")
    gr.add_argument("--proxy", default=None, help="single proxy URL (e.g. http://127.0.0.1:8888)")
    gr.add_argument("--workers", type=int, default=1, help="concurrent worker threads")
    gr.add_argument("--petani", action="store_true", help="use PetaniProxy farm instead")
    gs.add_parser("web", help="launch the WebUI (127.0.0.1:8092)")
    gs.add_parser("gui", help="launch the Tk GUI")
    grt = gs.add_parser("retry", help="retry a pending file")
    grt.add_argument("--pending", default=None)
    grt.add_argument("--out", default=None)
    gs.add_parser("pool", help="show the grok2api token pool")
    gin = gs.add_parser("inject", help="inject Grok SSO tokens into 9Router via a grok2api bridge",
                        description="Wraps scripts/grok_9router.py. 9Router's built-in 'xai' provider is OAuth-only, "
                                    "so SSO tokens go through a grok2api openai-compatible node "
                                    "(--base-url or env GROK2API_BASE).")
    gin.add_argument("-i", "--input", default=None,
                     help="token.json, accounts_*.txt, or a directory (default: ~/grok-register)")
    gin.add_argument("--base-url", default=None, help="grok2api base URL (default: env GROK2API_BASE)")
    gin.add_argument("--dry-run", action="store_true", help="show planned rows, write nothing")
    gin.add_argument("--verify", action="store_true", help="test each key against the bridge first")

    # ---- github (Education / account farm) ----
    ghp = sub.add_parser(
        "github", help="GitHub Education: account farm + Student Pack application helper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "GitHub Education / account-farm flow (scripts/github_farm.py, nodriver):\n"
            "\n"
            "  1. signup    new GitHub account via github.com/signup using a plus-addressed\n"
            "               school mailbox (raymondi+gh<N>@binus.ac.id, --index N)\n"
            "  2. verify    reads GitHub's 8-digit launch code live from the school M365\n"
            "               mailbox (Outlook Web)\n"
            "  3. education opens the GitHub Education application form and fills the fields\n"
            "               it can (school, school email, name)\n"
            "  4. save      account saved to ~/Auto-FreeCF/github_accounts.json\n"
            "\n"
            "NOT automated (finish by hand): Arkose/CAPTCHA puzzles, the student-ID photo /\n"
            "identity attestation on the Education form, MFA/device checks, GitHub's manual review.\n"
            "It stops before any attestation or upload. Treat it as a helper, not a turnkey farmer.\n"
            "\n"
            "Config: ~/.config/auto-freecf/.env  (SCHOOL_EMAIL, SCHOOL_MAIL_PASSWORD, SCHOOL_MAIL_URL)\n"
            "\n"
            "Examples:\n"
            "  kancahub github check\n"
            "  kancahub github farm --index 1 --dry-run     # walk the flow, no submit\n"
            "  kancahub github farm --index 2 --headless"),
    )
    ghs = ghp.add_subparsers(dest="github_cmd")
    gf = ghs.add_parser("farm", help="sign up a GitHub account + start the Education application")
    gf.add_argument("--index", type=int, default=1, help="N for raymondi+gh<N>@binus.ac.id (default 1)")
    gf.add_argument("--headless", action="store_true", help="run the browser headless")
    gf.add_argument("--dry-run", action="store_true", help="walk the flow + screenshot, do not submit")
    gf.add_argument("--proxy", default=None, help="proxy URL, e.g. http://user:pass@host:port")
    ghs.add_parser("check", help="check deps + school mailbox config, then exit")

    # ---- proxy ----
    pp = sub.add_parser("proxy", help="PetaniProxy: harvest proxies, rotating gateway & background daemon")
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

    rg = ps.add_parser("res-gateway", help="start PetaniProxy bridge gateway for residential/authenticated proxies")
    rg.add_argument("--pool", required=True, help="file with proxy URLs (one per line)")
    rg.add_argument("--port", type=int, default=8899, help="local port to listen on (default 8899)")
    rg.add_argument("--scheme", choices=["auto", "http", "socks5", "socks4", "https"], default="auto")

    # ---- stack ----
    sp = sub.add_parser("stack", help="Auto-FreeCF: create Cloudflare accounts, generate tokens & manage pool")
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

    sci = ss.add_parser("cookie-import",
                        help="import a Cookie-Editor JSON export -> extract account_id + mint token -> accounts.json",
                        description="Wraps Auto-FreeCF/process_cookies.py: loads a Cookie-Editor JSON export, "
                                    "verifies the session, extracts account_id, mints a token and appends to "
                                    "accounts.json.")
    sci.add_argument("cookies", help="Cookie-Editor JSON export file")
    sci.add_argument("label", help="account label (e.g. azisjati92, akun-1)")

    # ---- k12 ----
    kp = sub.add_parser("k12", help="ChatGPT K-12: automate teacher verification & SheerID approval")
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
    klf = ks.add_parser("link-finder", help="find SheerID verification links (scripts/sheerid_link_finder.py)",
                        description="Find SheerID verification links. Extra args after `--` are passed through "
                                    "to scripts/sheerid_link_finder.py.")
    klf.add_argument("extra", nargs=argparse.REMAINDER,
                     help="arguments passed straight to sheerid_link_finder.py (e.g. -- --help)")

    # ---- yowes ----
    yp = sub.add_parser("yowes", help="teacher document generator: create ID cards & letters (13 countries)")
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


def dispatch(p: argparse.ArgumentParser, args: argparse.Namespace) -> int:
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
    if g == "github":
        if not getattr(args, "github_cmd", None):
            p.parse_args(["github", "--help"])
            return 1
        return cmd_github(args)
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


def interactive_mode(p: argparse.ArgumentParser) -> int:
    """Beginner-friendly interactive menu when run without arguments."""
    print(get_ascii_banner())
    print(f" {C['bold']}Quick Tasks Menu:{C['reset']}\n")
    menu = [
        ("1", "Create Cloudflare accounts + tokens", "stack signup", ["stack", "signup"]),
        ("2", "Check everything is healthy", "doctor", ["doctor"]),
        ("3", "Start the proxy gateway", "proxy daemon", ["proxy", "daemon"]),
        ("4", "Create TokenHarbor keys", "thk batch", ["thk", "batch"]),
        ("5", "Grok farm", "grok run", ["grok", "run"]),
        ("6", "K-12 teacher verification", "k12 auto", ["k12", "auto"]),
        ("7", "Manage WARP tunnel", "warp", ["warp"]),
        ("8", "GitHub Education account farm", "github farm", ["github", "farm"]),
        ("9", "Inject Grok tokens into 9Router", "grok inject", ["grok", "inject"]),
        ("10", "Help & command reference", "--help", ["--help"]),
        ("0", "Exit", "", []),
    ]
    for key, desc, cmd_str, _ in menu:
        cmd_part = f" {C['cyan']}({cmd_str}){C['reset']}" if cmd_str else ""
        print(f"  [{col('bold', key)}] {desc:<42}{cmd_part}")
    print()

    while True:
        try:
            choice = input(f" {C['bold']}Select an option [0-10]: {C['reset']}").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            return 0

        if not choice or choice == "0":
            return 0

        match = next((item for item in menu if item[0] == choice), None)
        if not match:
            print(col("yellow", "  Please enter a valid option between 0 and 10."))
            continue

        key, desc, cmd_str, cmd_args = match
        if key == "10":
            p.print_help()
            return 0

        print(col("cyan", f"\n▶ Running: kancahub {cmd_str}\n"))
        return dispatch(p, p.parse_args(cmd_args))


def main() -> int:
    p = build_parser()
    if len(sys.argv) <= 1:
        return interactive_mode(p)
    args = p.parse_args()
    return dispatch(p, args)


if __name__ == "__main__":
    sys.exit(main())
