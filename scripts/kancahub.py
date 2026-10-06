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
  github  GitHub account farm (our domain / BINUS) + SheerID verification
            farm · verify · edu · check   (SheerID link extraction & verifier handoff)
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
  kancahub github farm --domain bizid --dry-run # GitHub signup via mail relay
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

# ══════════════════════════════════════════════════ egress auto-wire

SCRIPTS_DIR = Path(__file__).resolve().parent
PROXY_AUTO = "auto"
PROXY_NONE = "none"

# Probe target per farm group; only used by --proxy auto, resolved lazily at run
# time (never at import/parse time).
EGRESS_TARGETS = {
    "github": "https://github.com/signup",
    "grok": "https://accounts.x.ai/sign-up",
    "thk": "https://tokenharbor.ai/",
    "k12": "https://chatgpt.com/",
}

_AUTO_GATEWAYS: list = []          # gateways spawned by auto_egress (kept alive)
_egress_module = None              # scripts/egress.py once imported
_egress_unavailable = False        # import failed -> keep legacy behavior

PROXY_HELP = (
    "egress mode (default: auto). auto = smart auto-wire (local gateway -> pool "
    "gateway -> WARP -> residential -> direct, verified per target); none = force "
    "direct; WARP = force the Cloudflare WARP tunnel; or an explicit proxy URL "
    "such as http://127.0.0.1:8888 (an explicit URL always wins over auto)"
)

def _looks_like_proxy(mode: str) -> bool:
    """True for an explicit proxy URL or host:port (never 'auto'/'none')."""
    m = (mode or "").strip()
    if "://" in m:
        return True
    host, _, port = m.rpartition(":")
    return bool(host) and port.isdigit()

def _load_egress():
    """Lazily import scripts/egress.py; returns the module or None on failure.

    Import is deliberately deferred: `--help` and parsing must never touch the
    network or import optional proxy machinery.
    """
    global _egress_module, _egress_unavailable
    if _egress_module is not None or _egress_unavailable:
        return _egress_module
    try:
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        import egress as _egress  # noqa: PLC0415 - deferred on purpose
        _egress_module = _egress
    except Exception as exc:  # noqa: BLE001 - defensive: never break farming
        _egress_unavailable = True
        print(col("yellow", f"⚠ egress auto-wire unavailable ({exc}); "
                            "farm commands keep their built-in proxy behavior"))
    return _egress_module

def _stop_auto_gateways() -> None:
    """Stop auto-wire gateways we spawned, so the CLI leaves nothing running."""
    mod = _load_egress()
    stop = getattr(mod, "stop_gateway", None) if mod is not None else None
    while _AUTO_GATEWAYS:
        proc = _AUTO_GATEWAYS.pop()
        if stop is not None:
            try:
                stop(proc)
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass

class EgressChoice:
    """Outcome of resolving a --proxy mode for one farm command."""

    __slots__ = ("proxy", "source", "direct", "warp", "unavailable")

    def __init__(self, proxy, source, *, direct=False, warp=False, unavailable=False):
        self.proxy = proxy
        self.source = source
        self.direct = direct
        self.warp = warp
        self.unavailable = unavailable

def _choose_egress(mode: str, target_url: str) -> EgressChoice:
    """Resolve a --proxy mode into a concrete egress decision.

    auto        -> smart ladder (local gateway -> pool gateway -> WARP ->
                   residential -> direct) via scripts/egress.py
    none/direct -> force a direct connection
    URL/WARP    -> use it explicitly (user choice always wins over auto)

    Import failure is NOT fatal: it yields `unavailable`, and the caller leaves
    the child's built-in behavior untouched (with the warning already printed).
    """
    raw = (mode or PROXY_AUTO).strip()
    low = raw.lower()

    if low in (PROXY_NONE, "direct", "off", "no"):
        return EgressChoice(None, "none", direct=True)

    # Scheme-less proxies (127.0.0.1:8888, user:pass@host:port) are explicit
    # hops: give them a scheme so auto_egress's matcher sees them as such.
    if low not in (PROXY_AUTO, "warp") and "://" not in raw and _looks_like_proxy(raw):
        raw = f"http://{raw}"
        low = raw.lower()

    mod = _load_egress()
    if mod is None:
        if _looks_like_proxy(raw):        # explicit URL still honoured
            return EgressChoice(raw, "explicit")
        return EgressChoice(None, "unavailable", unavailable=True)

    try:
        proxy, proc, source = mod.auto_egress(
            target_url=target_url, mode=raw, verbose=True
        )
    except Exception as exc:  # noqa: BLE001 - defensive: fall back to legacy
        print(col("yellow", f"⚠ egress auto-wire failed ({exc}); "
                            "farm commands keep their built-in proxy behavior"))
        return EgressChoice(None, "unavailable", unavailable=True)

    if proc is not None:
        _AUTO_GATEWAYS.append(proc)

    if proxy:
        label = f"auto:{source}" if low == PROXY_AUTO else source
        print(col("green", f"  [proxy] {label} -> {proxy}"))
        return EgressChoice(proxy, label)

    if source == "warp":
        print(col("green", "  [proxy] WARP tunnel active (system-wide, no --proxy needed)"))
        return EgressChoice(None, "warp", warp=True)

    print(col("yellow", "  [proxy] auto: no usable proxy — using a direct connection"))
    return EgressChoice(None, "direct", direct=True)

def _resolve_proxy(mode: str, target_url: str) -> str | None:
    """Return the proxy URL for `mode`, or None when the connection is direct."""
    return _choose_egress(mode, target_url).proxy

def _proxy_env(choice: EgressChoice) -> dict | None:
    """Environment overrides for children that take no --proxy flag."""
    if not choice.proxy:
        return None
    return {
        "HTTP_PROXY": choice.proxy, "HTTPS_PROXY": choice.proxy,
        "http_proxy": choice.proxy, "https_proxy": choice.proxy,
    }

def _proxy_flags(choice: EgressChoice, *, supports_no_proxy: bool) -> list[str]:
    """CLI flags for children that accept --proxy / --no-proxy."""
    if choice.proxy:
        return ["--proxy", choice.proxy]
    if supports_no_proxy and not choice.unavailable:
        # none, auto->direct, or an active WARP tunnel: do not acquire a gateway.
        return ["--no-proxy"]
    return []


# ═══════════════════════════════════════════════════════════════ doctor

def cmd_adb(a) -> int:
    """Android/ADB helper (connect a phone for trusted Google signups)."""
    py = pick_python()
    tool = AUTO_FREECF / "scripts" / "adb_tool.py"
    if not tool.exists():
        print(col("red", "✗ adb_tool.py not found"))
        return 1
    sub = getattr(a, "adb_cmd", None) or "status"
    cmd = [py, str(tool), sub]
    if sub == "connect" and getattr(a, "addr", None):
        cmd.append(a.addr)
    return run(cmd, cwd=AUTO_FREECF)


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

    # ── proxy backend ─────────────────────────────────────────────
    native_lib = AUTO_FREECF / "scripts" / "proxy_lib.py"
    native_gateway = AUTO_FREECF / "scripts" / "proxy_gateway.py"
    native_ok = native_lib.exists() and native_gateway.exists()
    petani_ok = (PETANI / "main.py").exists()
    backend = ("native (scripts/proxy_lib.py)" if native_ok else
               "petani (petani-proxy/main.py)" if petani_ok else "MISSING")
    print(col("bold", f"\n  Proxy backend: {backend}"))
    print(f"  {'✅' if native_ok else '❌'} native proxy_lib.py ({native_lib})")
    print(f"  {'✅' if native_gateway.exists() else '❌'} native proxy_gateway.py ({native_gateway})")
    print(f"  {'✅' if petani_ok else '➖'} PetaniProxy legacy fallback ({PETANI})")
    print(col("dim", "    native commands: kancahub proxy nharvest | nhealth | ngateway"))

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
            print(f"  {'✅' if cf_t else '➖'} cloudflare-ai : {cf_t} total, {cf_a} active  (farm: Auto-FreeCF)")
            print(f"  {'✅' if cx_t else '➖'} codex (ChatGPT): {cx_t} total, {cx_a} active  (YOUR account)")
            print(f"  {'✅' if thk_t else '➖'} TokenHarbor (thk): {thk_t} total, {thk_a} active  (farm: harbor)")
            print(f"  {'✅' if xai_t else '➖'} xai (Grok OAuth): {xai_t} total, {xai_a} active  (YOUR account)")
            print(col("dim", "  (codex/xai are the user's own accounts — not farm output)"))
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
    sub = a.proxy_cmd

    # ── proof of masking (real IP vs gateway) ──
    if sub == "verify":
        v = AUTO_FREECF / "scripts" / "proxy_verify.py"
        cmd = [py, str(v)]
        if getattr(a, "proxy", None):
            cmd += ["--proxy", a.proxy]
        if getattr(a, "pool", None):
            cmd += ["--pool", a.pool]
        if getattr(a, "gateway_out", None):
            cmd += ["--gateway", a.gateway_out]
        return run(cmd, cwd=AUTO_FREECF)

    # ── one-command guided proxy mode (PetaniProxy power, one question) ──
    if sub == "start":
        return _proxy_start(a, py)

    # ── NATIVE backend (scripts/proxy_lib.py): self-contained, no PetaniProxy TUI ──
    if sub == "nharvest":
        return _proxy_native_harvest(a, py)
    if sub == "nhealth":
        return _proxy_native_health(a, py)
    if sub == "ngateway":
        return _proxy_native_gateway(a, py)

    petani = PETANI / "main.py"
    if not petani.exists():
        print(col("red", f"✗ PetaniProxy not found at {PETANI}"))
        return 1

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
        cmd = ["--webshare", str(a.accounts)]
        if a.headless:
            cmd.append("--headless")
        rc = petani_cmd(cmd)
        try:
            from proxy_sync import sync_now
            sync_now(quiet=False)
        except Exception:
            pass
        return rc

    if sub == "sync":
        from proxy_sync import sync_now
        return sync_now(source=getattr(a, "src", None), validate_live=getattr(a, "validate", False))

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


def _proxy_start(a, py) -> int:
    """One-command guided proxy: pick a mode, it starts and prints one line.

    Mirrors PetaniProxy's modes with a beginner-proof prompt and a single
    result line ('Proxy ready: http://127.0.0.1:8888').
    """
    petani = PETANI / "main.py"
    if not petani.exists():
        print(col("red", f"✗ PetaniProxy not found at {PETANI}"))
        return 1

    mode = getattr(a, "mode", None)

    if not mode:
        print()
        print(col("bold", "  Choose proxy mode:"))
        print(f"   [{C['green']}1{C['reset']}] {col('bold', 'WARP')}          — clean Cloudflare egress, zero captcha, unlimited (best for signups that get blocked)")
        print(f"   [{C['green']}2{C['reset']}] {col('bold', 'Gateway')}       — rotate thousands of free public proxies on 127.0.0.1:8888 (auto-refill, dashboard)")
        print(f"   [{C['green']}3{C['reset']}] {col('bold', 'Residential')}   — hunt real residential IPs via Webshare (best vs Cloudflare/Turnstile; may need Setup)")
        print(f"   [{C['green']}4{C['reset']}] {col('bold', 'Daemon')}        — 24/7 auto-healing gateway on :8888 (leave running in background)")
        print()
        try:
            mode = input(f"  {col('bold','Select mode [1-4]')} {col('dim','(default: 2)')}: ").strip() or "2"
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

    mode = str(mode).strip().lower()
    if mode in ("1", "warp", "c"):
        wm = AUTO_FREECF / "scripts" / "warp_manager.py"
        print(col("cyan", "\n  Starting Cloudflare WARP (clean egress)…\n"))
        # ponytail: shells out to petani-proxy main.py -C, then reports status with warp_manager.py
        rc = run([py, str(petani), "-C"], cwd=PETANI)
        if wm.exists():
            run([py, str(wm), "up"])
            run([py, str(wm), "status"])
        print(col("green", "\n  Proxy ready: WARP tunnel active"))
        print(col("dim", "  (turn off later with: kancahub warp down)\n"))
        return rc

    if mode in ("2", "gateway"):
        port = str(getattr(a, "port", 8888) or 8888)
        target = str(getattr(a, "target", 30) or 30)
        print(col("green", f"\n  Proxy ready: http://127.0.0.1:{port}"))
        print(col("cyan",  f"  Dashboard:   http://127.0.0.1:{port}/dashboard"))
        print(col("dim",   f"  PAC URL:     http://127.0.0.1:{port}/proxy.pac\n"))
        # ponytail: shells out to petani-proxy main.py --serve 8888 --target 30
        return run([py, str(petani), "--serve", port, "--target", target], cwd=PETANI)

    if mode in ("3", "residential", "w"):
        accs = str(getattr(a, "accounts", 1) or 1)
        print(col("cyan", f"\n  Hunting real residential IPs via Webshare (target {accs} accounts)…\n"))
        # ponytail: shells out to petani-proxy main.py -W 1
        return run([py, str(petani), "-W", accs], cwd=PETANI)

    if mode in ("4", "daemon", "g"):
        print(col("green", "\n  Proxy ready: http://127.0.0.1:8888"))
        print(col("cyan",  "  Dashboard:   http://127.0.0.1:8888/dashboard"))
        print(col("yellow", "  24/7 auto-healing gateway on :8888 (leave running in background, Ctrl-C to stop)…\n"))
        # ponytail: shells out to petani-proxy main.py --daemon-gateway
        return run([py, str(petani), "--daemon-gateway"], cwd=PETANI)

    print(col("red", f"✗ unknown mode '{mode}'. Choose 1 (WARP), 2 (Gateway), 3 (Residential), or 4 (Daemon)."))
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


# ── native proxy backend (scripts/proxy_lib.py) ──────────────────

def _native_proxy_lib() -> Path:
    return AUTO_FREECF / "scripts" / "proxy_lib.py"


def _proxy_native_harvest(a, py: str) -> int:
    lib = _native_proxy_lib()
    if not lib.exists():
        print(col("red", f"✗ native proxy lib not found at {lib}"))
        return 1
    cmd = [py, str(lib), "harvest", "--target", str(a.target),
           "--protocol", a.protocol, "--timeout", str(a.timeout), "--workers", str(a.workers)]
    if a.out_txt:
        cmd += ["--out-txt", a.out_txt]
    if a.out_json:
        cmd += ["--out-json", a.out_json]
    print(col("cyan", f"Native harvest ({a.protocol}) target={a.target}"))
    return run(cmd, cwd=AUTO_FREECF)


def _proxy_native_health(a, py: str) -> int:
    lib = _native_proxy_lib()
    if not lib.exists():
        print(col("red", f"✗ native proxy lib not found at {lib}"))
        return 1
    print(col("cyan", f"Native pool health: {a.pool}"))
    return run([py, str(lib), "health", str(a.pool)], cwd=AUTO_FREECF)


def _proxy_native_gateway(a, py: str) -> int:
    lib = _native_proxy_lib()
    if not lib.exists():
        print(col("red", f"✗ native proxy lib not found at {lib}"))
        return 1
    cmd = [py, str(lib), "gateway", "--pool", str(a.pool), "--port", str(a.port),
           "--scheme", a.scheme]
    print(col("cyan", f"Native rotating gateway on 127.0.0.1:{a.port} (Ctrl-C to stop)"))
    return run(cmd, cwd=AUTO_FREECF)


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
        # Harbor's free Turnstile solver needs camoufox + playwright, which live
        # only in the isolated camoufox-venv. Run the CLI there.
        py = pick_python(camoufox=True)
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
        # Farm commands (setup/batch/create-key): auto-wire a verified egress.
        # Harbor picks its own proxy from tools/tokenharbor/proxy_list.txt, so an
        # explicit/auto hop is only inherited through the environment.
        env = None
        if sub in ("setup", "batch", "create-key"):
            mode = getattr(a, "proxy", None) or PROXY_AUTO
            if getattr(a, "no_proxy", False):
                mode = PROXY_NONE
            choice = _choose_egress(mode, EGRESS_TARGETS["thk"])
            if choice.direct:
                print(col("dim", "  [proxy] direct connection (no egress gateway acquired)"))
            env = _proxy_env(choice)
        try:
            return run(cmd, cwd=HARBOR, env=env)
        finally:
            _stop_auto_gateways()

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
        mode = getattr(a, "proxy", None) or PROXY_AUTO
        if getattr(a, "no_proxy", False):
            mode = PROXY_NONE
        choice = _choose_egress(mode, EGRESS_TARGETS["grok"])
        if driver.exists() and not a.petani:
            cmd = [py, str(driver), "-n", str(getattr(a, "accounts", 1))]
            if getattr(a, "headless", False):
                cmd += ["--headless"]
            if getattr(a, "proxy_pool", None):
                cmd += ["--proxy-pool", a.proxy_pool]
            # An explicit hop must win over the child's auto/pool mode.
            if choice.proxy:
                cmd += ["--proxy", choice.proxy]
            if getattr(a, "workers", None):
                cmd += ["--workers", str(a.workers)]
            print(col("cyan", "Using grok non-interactive driver (SSO risk gate, relay mail, pool export)"))
            try:
                return run(cmd, cwd=AUTO_FREECF)
            finally:
                _stop_auto_gateways()
        if (GROK_REG / "grok_register_ttk.py").exists() and not a.petani:
            cmd = [py, "grok_register_ttk.py", "cli"]
            print(col("cyan", "Using grok-register backend (SSO risk gate, 5 mail providers, pool export)"))
            print(col("dim", "  (backend takes no --proxy; egress is inherited from the environment)"))
            try:
                return run(cmd, cwd=GROK_REG, env=_proxy_env(choice))
            finally:
                _stop_auto_gateways()
        # fallback to PetaniProxy
        petani = PETANI / "main.py"
        cmd = [py, str(petani), "--grok-farm", str(getattr(a, "accounts", 1))]
        if getattr(a, "headless", False):
            cmd += ["--headless"]
        print(col("yellow", "Using PetaniProxy grok farm fallback"))
        try:
            return run(cmd, cwd=PETANI, env=_proxy_env(choice))
        finally:
            _stop_auto_gateways()

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
        choice = _choose_egress(getattr(a, "proxy", None) or PROXY_AUTO,
                                EGRESS_TARGETS["grok"])
        cmd = [py, str(inj)]
        if a.input:
            cmd += ["-i", str(Path(a.input).expanduser())]
        if a.base_url:
            cmd += ["--base-url", a.base_url]
        if a.verify:
            cmd.append("--verify")
        if a.dry_run:
            cmd.append("--dry-run")
        # grok_9router.py has no --proxy flag; inherit the hop via the environment.
        try:
            return run(cmd, cwd=AUTO_FREECF, env=_proxy_env(choice))
        finally:
            _stop_auto_gateways()

    print(col("red", "✗ unknown grok command"))
    return 1


# ═══════════════════════════════════════════════════════════════ github

def map_github_farm_args(a, choice: "EgressChoice | None" = None) -> list[str]:
    """Map kancahub github farm CLI arguments to scripts/github_farm.py flags.

    Pure string work, no network: the caller resolves the egress once via
    `_choose_egress(...)` and passes it in as `choice`. Legacy default values
    (proxy=None/auto) mean "leave the child's built-in egress alone", which is
    what keeps offline callers (tests, other tools) side-effect free.
    """
    cmd: list[str] = []
    if getattr(a, "index", None) is not None:
        cmd += ["--index", str(a.index)]
    # Translate --domain to github_farm.py's --email-domain
    domain = getattr(a, "domain", None)
    if domain:
        cmd += ["--email-domain", domain]
    inbox = getattr(a, "inbox", None)
    if inbox:
        cmd += ["--inbox", inbox]
    max_acc = getattr(a, "max_accounts", None)
    if max_acc is not None:
        cmd += ["--max-accounts", str(max_acc)]
    delay_min = getattr(a, "delay_min", None)
    if delay_min is not None:
        cmd += ["--delay-min", str(delay_min)]
    delay_max = getattr(a, "delay_max", None)
    if delay_max is not None:
        cmd += ["--delay-max", str(delay_max)]
    retries = getattr(a, "retries", None)
    if retries:
        cmd += ["--retries", str(retries)]
    if choice is None:
        mode = getattr(a, "proxy", None)
        if getattr(a, "no_proxy", False):
            choice = EgressChoice(None, "none", direct=True)
        elif mode and mode.lower() not in (PROXY_AUTO, PROXY_NONE, "direct"):
            # An explicit proxy URL is pure data — forward it without probing.
            choice = EgressChoice(mode, "explicit")
        elif mode and mode.lower() == PROXY_NONE:
            choice = EgressChoice(None, "none", direct=True)
        # else: legacy call (no resolved choice) -> do not touch the child's egress
    # An explicit hop must win over the child's pool, or github_farm.py would
    # silently pick a different (pool) egress than the one we just verified.
    if choice is not None and choice.proxy and getattr(a, "pool", None):
        print(col("yellow", "• --pool ignored: an explicit/auto proxy already selects the egress"))
    elif getattr(a, "pool", None):
        cmd += ["--pool", a.pool]
    if choice is not None:
        cmd += _proxy_flags(choice, supports_no_proxy=True)
    if getattr(a, "headless", False):
        cmd.append("--headless")
    if getattr(a, "dry_run", False):
        cmd.append("--dry-run")
    return cmd


def build_github_parser(sub: Any = None) -> argparse.ArgumentParser:
    """Build the argument parser for 'kancahub github' and its subcommands."""
    if sub is None:
        ghp = argparse.ArgumentParser(
            prog="kancahub github",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="GitHub account farm (our domain / BINUS) + Student Pack SheerID verification",
        )
    elif hasattr(sub, "add_parser"):
        ghp = sub.add_parser(
            "github",
            help="GitHub account farm (our domain / BINUS) + Student Pack SheerID verification",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description=(
                "GitHub account farm & Student Pack verification:\n"
                "\n"
                "  1. farm      Create GitHub account(s) using our domain (kancalabs.biz.id / kancalabs.my.id)\n"
                "               via KancaHub mail relay OR the BINUS school mailbox.\n"
                "  2. verify    Automate SheerID / GitHub Student Pack verification from a direct URL or\n"
                "               by extracting the verification link from the school M365 inbox.\n"
                "  3. edu       One-shot flow: farm -> verify -> human pause (camera/student-ID stays human).\n"
                "  4. check     Verify environment dependencies, Camoufox, and mailbox/relay credentials.\n"
                "\n"
                "Examples:\n"
                "  kancahub github farm --domain bizid --max-accounts 3\n"
                "  kancahub github farm --domain binus --index 1 --headless\n"
                "  kancahub github verify --from-mail\n"
                "  kancahub github verify --url 'https://services.sheerid.com/verify/<id>/'\n"
                "  kancahub github edu --interactive\n"
                "  kancahub github check"
            ),
        )
    else:
        ghp = sub

    ghs = ghp.add_subparsers(dest="github_cmd")

    # ---- farm ----
    gf = ghs.add_parser(
        "farm",
        help="sign up GitHub account(s) using our domain or BINUS mailbox",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Create GitHub account(s) with automated form fill & OTP handling.\n"
            "\n"
            "Domain choices:\n"
            "  --domain bizid   gh<rand>@kancalabs.biz.id (via KancaHub mail relay, default relay)\n"
            "  --domain myid    gh<rand>@kancalabs.my.id  (via KancaHub mail relay)\n"
            "  --domain binus   raymondi+gh<N>@binus.ac.id (school mailbox, default)\n"
            "\n"
            "Rate limiting policy: 20-45s random delay, concurrency 1, max 5 accounts/run by default.\n"
            "Arkose / CAPTCHA puzzles remain manual if triggered by anti-bot.\n"
            "\n"
            "Examples:\n"
            "  kancahub github farm --domain bizid --max-accounts 3 --headless\n"
            "  kancahub github farm --domain binus --index 2 --dry-run\n"
            "  kancahub github farm --domain myid --proxy http://127.0.0.1:8888"
        ),
    )
    gf.add_argument("--index", type=int, default=1, help="starting N for account address (default: 1)")
    gf.add_argument(
        "--domain",
        choices=["binus", "bizid", "myid"],
        default="binus",
        help="email domain choice: binus (default, school mailbox), bizid (kancalabs.biz.id), myid (kancalabs.my.id)",
    )
    gf.add_argument(
        "--inbox",
        choices=["binus", "relay"],
        default="binus",
        help="inbox source: binus (default, Outlook school mailbox) or relay (KancaHub mail relay)",
    )
    gf.add_argument("--headless", action="store_true", help="run browser headless (virtual display / Xvfb)")
    gf.add_argument("--dry-run", action="store_true", help="walk the signup flow, screenshot, do not submit/create")
    gf.add_argument("--proxy", default="auto", metavar="PROXY", help=PROXY_HELP)
    gf.add_argument("--pool", default=None, help="proxy list file for rotation")
    gf.add_argument("--retries", type=int, default=0, metavar="N", help="retries on access_restricted block (default: 0)")
    gf.add_argument("--no-proxy", action="store_true", help="alias for --proxy none: force direct connection")
    gf.add_argument("--delay-min", type=float, default=20.0, metavar="SEC", help="min delay between accounts (default: 20s)")
    gf.add_argument("--delay-max", type=float, default=45.0, metavar="SEC", help="max delay between accounts (default: 45s)")
    gf.add_argument("--max-accounts", type=int, default=5, metavar="N", help="max accounts to process in this run (default: 5)")

    # ---- verify ----
    gv = ghs.add_parser(
        "verify",
        help="automate SheerID / GitHub Student Pack verification (direct URL or from mailbox)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Automate SheerID / GitHub Student Pack verification.\n"
            "\n"
            "Verification sources:\n"
            "  --from-mail     Read newest SheerID verification link from the school M365 mailbox\n"
            "                  using Camoufox (unwraps SafeLinks and extracts the verify URL).\n"
            "  --url <URL>     Direct SheerID verification link (https://services.sheerid.com/verify/...).\n"
            "\n"
            "HONEST DISCLOSURE:\n"
            "  GitHub Student Developer Pack uses SheerID for select academic partner verifications.\n"
            "  The underlying automated verifier was originally developed for K-12 teacher verification.\n"
            "  If GitHub requests a student program or requires student ID upload / camera capture,\n"
            "  the solver may report a program mismatch and require manual document upload.\n"
            "\n"
            "Examples:\n"
            "  kancahub github verify --from-mail\n"
            "  kancahub github verify --url 'https://services.sheerid.com/verify/<id>/'\n"
            "  kancahub github verify --from-mail --gateway --headless"
        ),
    )
    gv.add_argument("--url", default=None, help="direct SheerID verification URL")
    gv.add_argument("--from-mail", action="store_true", help="find and extract SheerID link from school M365 mailbox")
    gv.add_argument("--timeout", type=int, default=180, help="mailbox polling timeout in seconds (default: 180)")
    gv.add_argument("--proxy", default=None, help="proxy URL (e.g. http://127.0.0.1:8888)")
    gv.add_argument("--gateway", action="store_true", help="use local gateway 127.0.0.1:8888")
    gv.add_argument("--headless", action="store_true", help="run browser headless when inspecting mailbox")
    gv.add_argument("--open", action="store_true", help="open verification link in browser and inspect resulting page")
    gv.add_argument("--debug", action="store_true", help="enable debug mode for SheerID verifier")
    gv.add_argument("--email", default=None, help="manual email to supply to SheerID verifier")

    # ---- edu (one-shot: farm -> verify -> human pause) ----
    ge = ghs.add_parser(
        "edu",
        help="one-shot GitHub Education flow: farm -> SheerID verify -> human pause",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "One-shot GitHub Education (Student Pack) flow.\n"
            "\n"
            "Stages (stops + reports at the first failure):\n"
            "  1. farm          Create ONE GitHub account (github_farm.py) with the chosen\n"
            "                   --domain/--inbox, through --proxy auto egress.\n"
            "  2. verify        SheerID verification: uses --url, else extracts the link from\n"
            "                   the school mailbox (--from-mail). Skipped with --no-verify.\n"
            "  3. human pause   If verification needs a real camera/student-ID/phone upload:\n"
            "                   with --interactive, print instructions and WAIT for you;\n"
            "                   otherwise stop and tell you a human is required.\n"
            "\n"
            "HONEST: camera / student-ID / phone steps cannot be automated — this flow is\n"
            "human-assisted by design. No fake documents are ever generated here.\n"
            "\n"
            "Examples:\n"
            "  kancahub github edu --interactive\n"
            "  kancahub github edu --domain binus --inbox binus --url 'https://services.sheerid.com/verify/<id>/'\n"
            "  kancahub github edu --no-verify            # account only"
        ),
    )
    ge.add_argument(
        "--domain",
        choices=["binus", "bizid", "myid"],
        default="bizid",
        help="email domain for the new account: bizid (default, kancalabs.biz.id), "
             "myid (kancalabs.my.id), binus (school mailbox)",
    )
    ge.add_argument(
        "--inbox",
        choices=["binus", "relay"],
        default="relay",
        help="inbox source: relay (default, KancaHub mail relay) or binus (Outlook school mailbox)",
    )
    ge.add_argument("--proxy", default="auto", metavar="PROXY", help=PROXY_HELP)
    ge.add_argument("--interactive", action="store_true",
                    help="pause and wait for the human at the camera/student-ID/phone step")
    ge.add_argument("--no-verify", action="store_true",
                    help="skip the SheerID verify stage (account creation only)")
    ge.add_argument("--url", default=None,
                    help="direct SheerID verification URL (skips mailbox extraction)")
    ge.add_argument("--max-accounts", type=int, default=1, metavar="N",
                    help="max accounts for the farm stage (default: 1)")

    # ---- check ----
    gc = ghs.add_parser(
        "check",
        help="check dependencies, browser setup, and mailbox/relay credentials",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Check dependencies and configuration for GitHub farming and verification.\n"
            "\n"
            "Examples:\n"
            "  kancahub github check"
        ),
    )
    gc.add_argument(
        "--domain",
        choices=["binus", "bizid", "myid"],
        default="binus",
        help="domain to check: binus (school mailbox) or bizid/myid (mail relay)",
    )
    gc.add_argument(
        "--inbox",
        choices=["binus", "relay"],
        default="binus",
        help="inbox to check: binus (Outlook) or relay (KancaHub mail relay)",
    )

    return ghp


def build_edu_steps(no_verify: bool = False) -> list[str]:
    """Ordered stage list for the one-shot github edu flow (pure, testable).

    Returns ['farm'] with --no-verify, else ['farm', 'verify', 'human_pause'].
    """
    if no_verify:
        return ["farm"]
    return ["farm", "verify", "human_pause"]

# --- markers in the SheerID verifier output that mean "a human is required" ---
# (camera capture / student-ID photo / phone verification cannot be automated)
_EDU_HUMAN_MARKERS = (
    "camera", "selfie", "student id", "student-id", "photo of", "upload a photo",
    "upload photo", "document upload", "docupload", "phone verification",
    "verify your phone", "id upload",
)

def edu_needs_human(output: str) -> bool:
    """True when verifier output indicates a physical human step is required."""
    low = (output or "").lower()
    return any(m in low for m in _EDU_HUMAN_MARKERS)

# --- markers that the verifier reached the document upload / auto-pass stage ---
_EDU_DOC_MARKERS = ("langkah 4", "docupload", "dokumen diupload", "dokumen guru",
                    "auto-pass", "auto_pass", "upload selesai")

def edu_doc_upload_attempted(output: str) -> bool:
    """True when verifier output shows the docUpload step was attempted (or auto-passed)."""
    low = (output or "").lower()
    return any(m in low for m in _EDU_DOC_MARKERS)

def _github_accounts_count() -> int:
    """Number of accounts recorded in github_accounts.json (0 on any error)."""
    try:
        data = json.loads((AUTO_FREECF / "github_accounts.json").read_text())
    except Exception:  # noqa: BLE001 - missing/corrupt file just means "no account"
        return 0
    accounts = data.get("accounts") if isinstance(data, dict) else data
    return len(accounts or [])

def _sheerid_find_url(py_camo: str, a: Any, proxy_override: str | None = None) -> str | None:
    """Extract the SheerID verification link from the school mailbox.

    Prints its own errors (same messages as the historical inline code).
    Returns the URL, or None on failure. `proxy_override` (a concrete URL from
    an already-resolved egress choice) wins over a.proxy when given.
    """
    finder = AUTO_FREECF / "scripts" / "sheerid_link_finder.py"
    if not finder.exists():
        print(col("red", f"✗ sheerid_link_finder.py not found at {finder}"))
        return None
    print(col("cyan", "Searching school mailbox for SheerID verification link via Camoufox…"))
    finder_cmd = [
        py_camo,
        str(finder),
        "--json",
        "--timeout",
        str(getattr(a, "timeout", 180)),
    ]
    if getattr(a, "headless", False):
        finder_cmd.append("--headless")
    proxy = proxy_override or getattr(a, "proxy", None) or (
        "127.0.0.1:8888" if getattr(a, "gateway", False) else None)
    # The finder takes a concrete proxy URL; never forward mode keywords.
    if proxy and proxy.strip().lower() in ("auto", "none", "direct", "warp", "off"):
        proxy = None
    if proxy:
        finder_cmd += ["--proxy", proxy]
    if getattr(a, "open", False):
        finder_cmd.append("--open")

    try:
        proc = subprocess.run(
            finder_cmd,
            capture_output=True,
            text=True,
            cwd=str(AUTO_FREECF),
        )
    except Exception as e:  # noqa: BLE001
        print(col("red", f"✗ Failed to execute sheerid_link_finder.py: {e}"))
        return None

    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except Exception:
        data = {}

    if proc.returncode != 0 or not data.get("success"):
        err_msg = data.get("error") if isinstance(data, dict) else None
        if not err_msg:
            err_msg = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        print(col("red", f"✗ SheerID link finder failed: {err_msg}"))
        return None

    url = data.get("url")
    print(col("green", f"✓ Found SheerID verification URL: {url}"))
    if getattr(a, "open", False):
        print(col("green", "Verification link inspected in browser."))
    return url

def _sheerid_run(py: str, url: str, *, proxy: str | None = None,
                 debug: bool = False, email: str | None = None,
                 capture: bool = False) -> tuple[int, str]:
    """Run the K-12 SheerID verifier against `url`. Returns (exit_code, output).

    capture=False streams output live (plain `verify`); capture=True captures it
    (used by `edu` to detect camera/ID/phone steps in the output).
    """
    script = K12_DIR / "script.py"
    if not script.exists():
        print(col("red", f"✗ SheerID verifier not found at {script}"))
        print(col("yellow", f"  Extracted verification URL is: {url}"))
        print(col("yellow", "  You can open and verify this URL manually in a browser."))
        return 1, ""

    print(col("bold", "\n  GitHub SheerID Verification Handoff"))
    print(col("yellow", "  ⚠️  NOTE: GitHub Student Pack uses SheerID for select academic partner verifications."))
    print(col("yellow", "     The underlying solver (script.py) was built for K-12 Teacher verification."))
    print(col("yellow", "     If GitHub requires a student-specific program or student ID document upload,"))
    print(col("yellow", "     the automated solver may report a program mismatch and require manual upload.\n"))

    vcmd = [py, str(script), url]
    if proxy:
        vcmd += ["--proxy", proxy]
    if debug:
        vcmd.append("--debug")
    if email:
        vcmd += ["--email", email]

    if capture:
        try:
            proc = subprocess.run(vcmd, cwd=str(K12_DIR), capture_output=True, text=True)
        except Exception as e:  # noqa: BLE001
            print(col("red", f"✗ Failed to execute SheerID verifier: {e}"))
            return 1, ""
        out = (proc.stdout or "") + (proc.stderr or "")
        if out:
            print(out, end="" if out.endswith("\n") else "\n")
        return proc.returncode, out
    return run(vcmd, cwd=K12_DIR), ""

def _print_edu_summary(summary: dict) -> None:
    """Final honest report for the github edu flow."""
    print(col("bold", "\n  GitHub Edu flow summary"))
    print(f"    account created : {'yes' if summary.get('account') else 'no'}")
    print(f"    sheerid reached : {'yes' if summary.get('sheerid') else 'no'}")
    print(f"    docUpload       : {'attempted' if summary.get('doc_upload') else 'not reached'}")
    pending = summary.get("human_pending") or []
    if pending:
        print(f"    human steps     : {', '.join(pending)}")
    else:
        print("    human steps     : none detected (GitHub's manual review may still apply)")

def cmd_github(a) -> int:
    """GitHub account farm & Student Pack verification."""
    py = pick_python()
    py_camo = pick_python(camoufox=True)
    sub = getattr(a, "github_cmd", None)
    if not sub:
        print(col("yellow", "• no github subcommand specified — run 'kancahub github --help' for usage"))
        return 1

    farm = AUTO_FREECF / "scripts" / "github_farm.py"

    if sub == "check":
        if not farm.exists():
            print(col("red", f"✗ github_farm.py not found at {farm}"))
            return 1
        cmd = [py_camo, str(farm), "--check"]
        if getattr(a, "domain", None):
            cmd += ["--email-domain", a.domain]
        if getattr(a, "inbox", None):
            cmd += ["--inbox", a.inbox]
        return run(cmd, cwd=AUTO_FREECF)

    if sub == "farm":
        if not farm.exists():
            print(col("red", f"✗ github_farm.py not found at {farm}"))
            return 1
        # Resolve the egress once, at run time (never at parse/import time).
        mode = getattr(a, "proxy", None) or PROXY_AUTO
        if getattr(a, "no_proxy", False):
            mode = PROXY_NONE
        choice = _choose_egress(mode, EGRESS_TARGETS["github"])
        cmd = [py_camo, str(farm)] + map_github_farm_args(a, choice)
        print(col("yellow", "Note: Anti-bot CAPTCHA puzzles and Education attestation remain manual steps if encountered."))
        try:
            return run(cmd, cwd=AUTO_FREECF)
        finally:
            _stop_auto_gateways()

    if sub == "verify":
        url = getattr(a, "url", None)
        if getattr(a, "from_mail", False):
            url = _sheerid_find_url(py_camo, a)
            if not url:
                return 1

        if not url:
            print(col("red", "✗ SheerID verification URL required."))
            print(col("yellow", "  Specify --url <sheerid-url> OR use --from-mail to extract it from the school inbox."))
            print(col("yellow", "  Example: kancahub github verify --from-mail"))
            print(col("yellow", "  Example: kancahub github verify --url https://services.sheerid.com/verify/..."))
            return 1

        proxy = getattr(a, "proxy", None) or ("127.0.0.1:8888" if getattr(a, "gateway", False) else None)
        rc, _ = _sheerid_run(py, url, proxy=proxy,
                             debug=getattr(a, "debug", False),
                             email=getattr(a, "email", None))
        return rc

    if sub == "edu":
        steps = build_edu_steps(no_verify=getattr(a, "no_verify", False))
        print(col("bold", f"\n  GitHub Edu one-shot flow — stages: {' -> '.join(steps)}"))
        summary = {
            "account": False,
            "sheerid": False,
            "doc_upload": False,
            "human_pending": [],
        }
        # Check the farm tool BEFORE resolving egress, so a missing tool is a
        # clear, offline error (no gateway spawned, no network touched).
        if "farm" in steps and not farm.exists():
            print(col("red", f"✗ github_farm.py not found at {farm}"))
            print(col("yellow", f"  Expected at: {farm}"))
            print(col("yellow", "  Install/restore the Auto-FreeCF repo, then re-run."))
            return 1

        # Resolve the egress ONCE; the same concrete proxy serves every stage
        # (farm, mailbox finder, SheerID verifier). The gateway stays alive for
        # the whole flow and is torn down in the finally below.
        mode = getattr(a, "proxy", None) or PROXY_AUTO
        choice = _choose_egress(mode, EGRESS_TARGETS["github"])
        resolved_proxy = choice.proxy if choice is not None else None
        try:
            # ── [1] farm: create ONE account ────────────────────────────
            if "farm" in steps:
                print(col("bold", "\n  [1/3] farm: creating a GitHub account…"))
                before = _github_accounts_count()
                cmd = [py_camo, str(farm)] + map_github_farm_args(a, choice)
                rc = run(cmd, cwd=AUTO_FREECF)
                if rc != 0:
                    print(col("red", f"\n✗ STOP: farm stage failed (exit {rc}). Nothing else was attempted."))
                    print(col("yellow", "  Anti-bot/CAPTCHA or a blocked egress IP are the usual causes — see the log above."))
                    _print_edu_summary(summary)
                    return rc
                after = _github_accounts_count()
                summary["account"] = after > before
                if not summary["account"]:
                    print(col("yellow", "\n⚠ farm exited 0 but no NEW account landed in github_accounts.json."))

            # ── [2] verify: SheerID ─────────────────────────────────────
            if "verify" in steps:
                print(col("bold", "\n  [2/3] verify: SheerID Student Pack verification…"))
                url = getattr(a, "url", None)
                if not url:
                    url = _sheerid_find_url(py_camo, a, proxy_override=resolved_proxy)
                    if not url:
                        print(col("red", "\n✗ STOP: could not obtain a SheerID verification URL."))
                        print(col("yellow", "  Pass --url <sheerid-url>, or fix the school-mailbox extraction."))
                        _print_edu_summary(summary)
                        return 1
                summary["sheerid"] = True
                # A missing verifier is a tool problem, not a human step: clear
                # error + exit 1 (no traceback, nothing else attempted).
                if not (K12_DIR / "script.py").exists():
                    print(col("red", f"✗ SheerID verifier not found at {K12_DIR / 'script.py'}"))
                    print(col("yellow", f"  Extracted verification URL: {url}"))
                    print(col("yellow", "  Restore the K-12 tool or open that URL manually in a browser."))
                    _print_edu_summary(summary)
                    return 1
                rc, out = _sheerid_run(py, url, proxy=resolved_proxy, capture=True)
                summary["doc_upload"] = edu_doc_upload_attempted(out)
                needs_human = edu_needs_human(out) or (rc != 0 and not summary["doc_upload"])
                if needs_human:
                    summary["human_pending"].append("camera / student-ID / phone step (SheerID)")
                if rc != 0 and not needs_human:
                    print(col("red", f"\n✗ STOP: SheerID verifier failed (exit {rc})."))
                    _print_edu_summary(summary)
                    return rc

            # ── [3] human pause ─────────────────────────────────────────
            if "human_pause" in steps and summary["human_pending"]:
                print(col("bold", "\n  [3/3] human pause: a physical step is required"))
                print(col("yellow", "  SheerID/GitHub needs something only a human can provide:"))
                print(col("yellow", "    • a live camera capture / selfie, or"))
                print(col("yellow", "    • a photo of a real student ID, or"))
                print(col("yellow", "    • phone/SMS verification."))
                print(col("yellow", "  No script can do this — and we never fake documents."))
                if getattr(a, "interactive", False):
                    print(col("cyan", "\n  Complete the physical step now (in the open browser / on your phone)."))
                    try:
                        input("  Press Enter when done (or Ctrl+C to stop): ")
                    except (EOFError, KeyboardInterrupt):
                        print(col("yellow", "\n  No input received — stopping; the human step remains pending."))
                        _print_edu_summary(summary)
                        return 1
                    print(col("green", "  ✓ Continuing after human step."))
                else:
                    print(col("yellow", "  Re-run with --interactive to be walked through it, or finish it manually."))
        finally:
            _stop_auto_gateways()

        _print_edu_summary(summary)
        if summary["human_pending"] and not getattr(a, "interactive", False):
            print(col("yellow", "  Result: flow reached the human step; finish it manually to complete verification."))
            return 0 if summary["account"] else 1
        return 0 if summary["account"] or summary["sheerid"] else 1

    print(col("red", f"✗ unknown github command: {sub}"))
    return 1


# ═══════════════════════════════════════════════════════════════ mail

def cmd_mail(a) -> int:
    """School mailbox (BINUS M365) via browser — wraps scripts/school_mail_browser.py."""
    py = pick_python()
    smb = AUTO_FREECF / "scripts" / "school_mail_browser.py"
    if not smb.exists():
        print(col("red", f"✗ school_mail_browser.py not found at {smb}"))
        return 1

    sub = getattr(a, "mail_cmd", None)
    if not sub:
        print(col("yellow", "• no mail subcommand — showing help"))
        return run([py, str(smb), "--help"], cwd=AUTO_FREECF)

    if sub in ("test", "login", "otp"):
        cmd = [py, str(smb), sub]
        if sub == "otp":
            cmd += ["--timeout", str(getattr(a, "timeout", 180) or 180)]
        if sub == "login":
            print(col("dim", "  (login leaves the browser open — Ctrl-C when done inspecting)"))
        return run(cmd, cwd=AUTO_FREECF)

    print(col("red", f"✗ unknown mail command: {sub}"))
    return 1

# ═══════════════════════════════════════════════════════════════ gmail

def cmd_gmail(a) -> int:
    """Gmail account farm — wraps scripts/gmail_creator.py (and gmail_adb.py)."""
    py = pick_python()
    gc = AUTO_FREECF / "scripts" / "gmail_creator.py"
    sub = getattr(a, "gmail_cmd", None)

    # Android/ADB path (real device skips Google's phone gate).
    if sub == "adb":
        adbg = AUTO_FREECF / "scripts" / "gmail_adb.py"
        if not adbg.exists():
            print(col("red", "✗ gmail_adb.py not found"))
            return 1
        cmd = [py, str(adbg), "--count", str(getattr(a, "count", 1) or 1)]
        if getattr(a, "package", None):
            cmd += ["--package", a.package]
        if getattr(a, "password", None):
            cmd += ["--password", a.password]
        if getattr(a, "dry_run", False):
            cmd.append("--dry-run")
        print(col("cyan", "Using the Android phone (ADB) — trusted-device path"))
        return run(cmd, cwd=AUTO_FREECF)

    if not gc.exists():
        print(col("red", f"✗ gmail_creator.py not found at {gc}"))
        return 1

    if sub == "check":
        return run([py, str(gc), "--check"], cwd=AUTO_FREECF)

    if sub == "farm":
        cmd = [py, str(gc), "--count", str(getattr(a, "count", 1) or 1)]
        if getattr(a, "headless", False):
            cmd.append("--headless")
        if getattr(a, "proxy", None):
            cmd += ["--proxy", a.proxy]
        if getattr(a, "out", None):
            cmd += ["--out", a.out]
        if getattr(a, "dry_run", False):
            cmd.append("--dry-run")
        if getattr(a, "random_password", False):
            cmd.append("--random-password")
        return run(cmd, cwd=AUTO_FREECF)

    if sub == "dry-run":
        cmd = [py, str(gc), "--count", str(getattr(a, "count", 1) or 1), "--dry-run"]
        if getattr(a, "proxy", None):
            cmd += ["--proxy", a.proxy]
        if getattr(a, "out", None):
            cmd += ["--out", a.out]
        return run(cmd, cwd=AUTO_FREECF)

    print(col("red", f"✗ unknown gmail command: {sub}"))
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

def _k12_guided(py) -> int:
    """Guided K-12 verification, mirroring run_cmd.bat's [1]-[13] modes."""
    script = K12_DIR / "script.py"
    if not script.exists():
        print(col("red", f"✗ {script} not found"))
        return 1

    def _ask(prompt, default=""):
        d = f" [Enter = {default}]" if default else ""
        try:
            return input(f"  {col('bold', prompt)}{d}: ").strip() or default
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)

    print(col("bold", "\n  K-12 / SheerID verification (guided)\n"))
    url = _ask("Paste the SheerID verification URL")
    if not url:
        print(col("red", "  ✗ need a URL"))
        return 1

    print(col("bold", "\n  Connection mode:"))
    print("   [1] direct + temp email        (default, simplest)")
    print("   [2] proxy ip:port")
    print("   [3] proxy user:pass@ip:port")
    print("   [4] debug, no proxy")
    print("   [7] no temp email")
    print("   [10] manual email (send to your inbox)")
    print("   [g] use local gateway 127.0.0.1:8888 (run 'kancahub proxy start' first)")
    mode = _ask("Pick", "1").lower()

    cmd = [py, str(script), url]
    if mode == "2":
        cmd += ["--proxy", _ask("proxy IP:PORT")]
    elif mode == "3":
        cmd += ["--proxy", _ask("user:pass@IP:PORT")]
    elif mode == "4":
        cmd += ["--debug"]
    elif mode == "7":
        cmd += ["--no-temp-email"]
    elif mode == "10":
        cmd += ["--email", _ask("your email")]
    elif mode == "g":
        cmd += ["--proxy", "127.0.0.1:8888"]

    return run(cmd, cwd=K12_DIR)


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
        mode = getattr(a, "proxy", None) or PROXY_AUTO
        choice = _choose_egress("127.0.0.1:8888" if getattr(a, "gateway", False) else mode,
                                EGRESS_TARGETS["k12"])
        cmd = [py, str(script), a.url]
        if choice.proxy:
            cmd += ["--proxy", choice.proxy]
        elif choice.unavailable and mode not in (PROXY_AUTO, PROXY_NONE):
            cmd += ["--proxy", mode]  # explicit URL, egress helper unavailable
        if a.debug:
            cmd += ["--debug"]
        if a.email:
            cmd += ["--email", a.email]
        if a.no_temp_email:
            cmd += ["--no-temp-email"]
        if a.ask_email:
            cmd += ["--ask-email"]
        try:
            return run(cmd, cwd=K12_DIR)
        finally:
            _stop_auto_gateways()

    if sub == "auto":
        # Prefer the tool's ORIGINAL proven flow (DrissionPage + temp.tf), which
        # reliably walks ChatGPT signup -> SheerID and hands off to K12Verifier
        # (auto-pass). Our experimental relay/nodriver flow is only a fallback.
        original = K12_DIR / "auto_k12_flow.py"
        experimental = AUTO_FREECF / "scripts" / "auto_k12_flow_kancahub.py"
        # Neither auto flow takes --proxy; inherit the verified hop via the env.
        mode = getattr(a, "proxy", None) or PROXY_AUTO
        if getattr(a, "no_proxy", False):
            mode = PROXY_NONE
        choice = _choose_egress(mode, EGRESS_TARGETS["k12"])
        env = _proxy_env(choice)
        if original.exists():
            print(col("cyan", "Full auto flow (original tool): ChatGPT signup -> SheerID -> K12Verifier"))
            print(col("dim", "  uses DrissionPage + temp.tf edu mailbox (proven auto-pass path)"))
            try:
                return run([py, str(original)], cwd=K12_DIR, env=env)
            finally:
                _stop_auto_gateways()
        if experimental.exists():
            print(col("yellow", "Original auto flow missing; using experimental relay flow"))
            try:
                return run([py, str(experimental)], cwd=K12_DIR, env=env)
            finally:
                _stop_auto_gateways()
        print(col("red", "✗ no auto_k12_flow found"))
        return 1

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

    if sub == "run":
        # Guided menu that mirrors the original run_cmd.bat [1]-[13].
        return _k12_guided(py)

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
    sub.add_parser("beginner", help="guided, plain-English mode — start here if you're new")
    # ---- adb (Android device for trusted Google signups) ----
    adp = sub.add_parser("adb", help="Android device (ADB) — connect a phone for trusted Google signups")
    ads = adp.add_subparsers(dest="adb_cmd")
    ads.add_parser("status", help="is a device connected? (default)")
    ads.add_parser("devices", help="list devices + browsers")
    ads.add_parser("phone", help="connect physical Android phone via USB/Wi-Fi")
    ads.add_parser("usb", help="how to connect over USB (alias for phone)")
    ads.add_parser("emulator", help="detect/launch Android Studio AVD or Waydroid emulator")
    ads.add_parser("setup", help="enable Wi-Fi (tcpip) mode and show the phone IP")
    adc = ads.add_parser("connect", help="connect over Wi-Fi")
    adc.add_argument("addr", help="phone IP or IP:port")
    sub.add_parser("menu", help="classic numbered command menu (advanced users)")

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
    tb = ts.add_parser("batch", help="create N TokenHarbor accounts")
    tb.add_argument("count", nargs="?", type=int, default=1)
    tsetup = ts.add_parser("setup", help="full setup on TokenHarbor (interactive)")
    tck = ts.add_parser("create-key", help="create an API key for an existing account")
    # Farm commands: wire kancahub's smart egress in by default (fallback: harbor's
    # own proxy_list.txt, untouched).
    for _farm_parser in (tb, tsetup, tck):
        _farm_parser.add_argument(
            "--proxy", default="auto", metavar="PROXY",
            help=PROXY_HELP + "\n  (inherited by harbor via the environment)")
        _farm_parser.add_argument("--no-proxy", action="store_true", help="alias for --proxy none")
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
    tse = ts.add_parser("setup-env", help="wire harbor: config.toml + Tempik base_url + capsolver + proxies (harbor_config.py)")
    tse.add_argument("--harbor-dir", default=None, help="harbor repo dir (default ~/harbor)")
    tse.add_argument("--env-file", default=None, help="auto-freecf .env path")
    tse.add_argument("--proxies-src", default=None, help="source proxies.txt to copy into harbor/tools")
    tse.add_argument("--tempik-url", default=None, help="Tempik base URL override")
    tse.add_argument("--no-proxies", action="store_true", help="skip refreshing harbor's proxy list")
    tse.add_argument("--status", action="store_true", help="only report current harbor config")
    tse.add_argument("--dry-run", action="store_true", help="show actions without writing")

    # ---- grok (xAI) ----
    gp = sub.add_parser("grok", help="Grok xAI farm: automated account creation with multi-provider mail")
    gs = gp.add_subparsers(dest="grok_cmd")
    gr = gs.add_parser("run", help="run the registration flow")
    gr.add_argument("-n", "--accounts", type=int, default=1)
    gr.add_argument("--headless", action="store_true")
    gr.add_argument("--proxy-pool", default=None, help="path to proxy pool file")
    gr.add_argument("--proxy", default="auto", metavar="PROXY", help=PROXY_HELP)
    gr.add_argument("--no-proxy", action="store_true", help="alias for --proxy none")
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
    gin.add_argument("--proxy", default="auto", metavar="PROXY",
                     help="egress mode for the bridge call: auto (default), none/direct, "
                          "warp, or an explicit proxy URL (inherited via the environment)")

    # ---- github (account farm + SheerID verification) ----
    build_github_parser(sub)

    # ---- mail (school mailbox / BINUS M365) ----
    mp = sub.add_parser(
        "mail", help="School mailbox (BINUS M365) via browser: read signup OTPs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Read the school Outlook inbox from a real browser session (nodriver).\n"
            "M365 blocks IMAP basic auth, so the OTP is scraped from the web UI.\n"
            "The session lives in ~/.config/auto-freecf/school-profile and survives runs.\n"
            "\n"
            "  test   log in and print recent inbox subjects (do this once by hand if\n"
            "         the tenant enforces MFA — the profile then carries the session)\n"
            "  otp    wait for an OpenAI/ChatGPT verification code\n"
            "  login  log in and leave the browser open for inspection\n"
            "\n"
            "Config: ~/.config/auto-freecf/.env  (SCHOOL_EMAIL, SCHOOL_MAIL_PASSWORD,\n"
            "SCHOOL_MAIL_URL)\n"
            "\n"
            "Examples:\n"
            "  kancahub mail test\n"
            "  kancahub mail otp --timeout 300"),
    )
    ms = mp.add_subparsers(dest="mail_cmd")
    ms.add_parser("test", help="log in and list recent inbox subjects (selftest)")
    mo = ms.add_parser("otp", help="wait for an OpenAI OTP in the school inbox")
    mo.add_argument("--timeout", type=int, default=180, help="seconds to wait (default 180)")
    ms.add_parser("login", help="log in and leave the browser open for inspection")

    # ---- gmail (Gmail account farm) ----
    gmp = sub.add_parser(
        "gmail", help="Gmail account farm (nodriver, phone step stays manual)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Gmail signup farm — wraps scripts/gmail_creator.py (nodriver + Chrome).\n"
            "\n"
            "  farm     create N accounts, append results to the JSON out file\n"
            "  dry-run  walk the flow without submitting\n"
            "  check    report dependencies and exit\n"
            "\n"
            "Note: headless cannot complete phone verification — expect\n"
            "pending_verification/failed rows unless you run headed and finish by hand.\n"
            "\n"
            "Examples:\n"
            "  kancahub gmail check\n"
            "  kancahub gmail farm --count 2 --proxy http://127.0.0.1:8888\n"
            "  kancahub gmail farm --count 1 --headless --out ~/gmail.json"),
    )
    gms = gmp.add_subparsers(dest="gmail_cmd")
    gmf = gms.add_parser("farm", help="create N Gmail accounts")
    gmf.add_argument("--count", type=int, default=1, help="accounts to attempt (default 1)")
    gmf.add_argument("--headless", action="store_true", help="run the browser headless")
    gmf.add_argument("--proxy", default=None, help="scheme://host:port (no credentials)")
    gmf.add_argument("--out", default=None, help="JSON results file (appended to)")
    gmf.add_argument("--dry-run", action="store_true", help="walk the flow, do not submit")
    gmf.add_argument("--random-password", action="store_true", help="generate a password per account")
    gmd = gms.add_parser("dry-run", help="walk the flow without submitting")
    gmd.add_argument("--count", type=int, default=1)
    gmd.add_argument("--proxy", default=None)
    gmd.add_argument("--out", default=None)
    gms.add_parser("check", help="report dependencies and exit")
    gma = gms.add_parser("adb", help="create Gmail via a real Android phone (ADB) — skips the phone gate")
    gma.add_argument("--count", type=int, default=1)
    gma.add_argument("--package", default="com.android.chrome",
                     help="browser package on the phone (default com.android.chrome)")
    gma.add_argument("--password", default=None)
    gma.add_argument("--dry-run", action="store_true")

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

    ps.add_parser("sync", help="sync fresh proxies into all tool pools")
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

    # ---- native backend (scripts/proxy_lib.py) ----
    nh = ps.add_parser("nharvest", help="[native] harvest + validate public proxies")
    nh.add_argument("--target", type=int, default=20, help="live proxies to collect")
    nh.add_argument("--protocol", choices=["http", "socks4", "socks5"], default="http")
    nh.add_argument("--timeout", type=float, default=4.0)
    nh.add_argument("--workers", type=int, default=100)
    nh.add_argument("--out-txt", default=None)
    nh.add_argument("--out-json", default=None)

    nhe = ps.add_parser("nhealth", help="[native] check a proxy pool file")
    nhe.add_argument("--pool", required=True, help="pool file (one proxy per line)")

    ng = ps.add_parser("ngateway", help="[native] start rotating gateway for a pool")
    ng.add_argument("--pool", required=True, help="pool file (one proxy per line)")
    ng.add_argument("--port", type=int, default=8899, help="local port to listen on (default 8899)")
    ng.add_argument("--scheme", choices=["auto", "http", "socks5", "socks4", "https"], default="auto")

    pv = ps.add_parser("verify", help="prove your IP is masked (real vs gateway)")
    pv.add_argument("--proxy", default=None, help="proxy URL to test (default http://127.0.0.1:8888)")
    pv.add_argument("--pool", default=None, help="file of proxies to test")
    pv.add_argument("--gateway", dest="gateway_out", default=None,
                    help="gateway to probe (default http://127.0.0.1:8888)")

    pst = ps.add_parser("start", help="guided: pick a proxy mode and start it (one question)")
    pst.add_argument("mode", nargs="?", default=None,
                     help="1=WARP 2=Gateway 3=Residential 4=Daemon (skip the prompt)")
    pst.add_argument("--port", type=int, default=8888, help="gateway port (mode 2)")
    pst.add_argument("--target", type=int, default=30, help="proxies to collect (mode 2)")
    pst.add_argument("-n", "--accounts", type=int, default=1, help="accounts (mode 3, Webshare)")

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
    kv.add_argument("--proxy", default="auto", metavar="PROXY",
                    help=PROXY_HELP + "\n  URL may be IP:port or user:pass@ip:port")
    kv.add_argument("--gateway", action="store_true", help="use 127.0.0.1:8888")
    kv.add_argument("--debug", action="store_true")
    kv.add_argument("--email", default=None)
    kv.add_argument("--no-temp-email", action="store_true")
    kv.add_argument("--ask-email", action="store_true")
    ks.add_parser("run", help="guided: paste URL + pick mode (mirrors the original [1]-[13] menu)")
    ka = ks.add_parser("auto", help="full auto: ChatGPT signup + OTP + session capture + SheerID verify")
    ka.add_argument("--proxy", default="auto", metavar="PROXY",
                    help=PROXY_HELP + "\n  (inherited by the flow via the environment)")
    ka.add_argument("--no-proxy", action="store_true", help="alias for --proxy none")
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

    # ---- autofarm ----
    af = sub.add_parser("autofarm", help="paste any website URL to adapt and autofarm with clean proxies")
    af.add_argument("url", nargs="?", default=None, help="website signup/login URL")
    af.add_argument("--domain", choices=["kancalabs.biz.id", "kancalabs.my.id", "biz.id", "my.id"], default="kancalabs.biz.id",
                    help="disposable email domain (default: kancalabs.biz.id)")
    af.add_argument("--inject-9router", action="store_true", help="inject credentials into 9Router SQLite DB")
    af.add_argument("--out", default=None, help="output JSON path (default: results/autofarm_accounts.json)")
    af.add_argument("--headless", action="store_true", help="run without showing browser UI")
    af.add_argument("--proxy", default=PROXY_AUTO,
                    help="egress mode (default: auto). auto = smart auto-wire (local gateway -> "
                         "pool gateway -> WARP -> residential -> direct, verified per target); "
                         "none = force direct; WARP = force Cloudflare WARP; or an explicit "
                         "proxy URL such as http://127.0.0.1:8888")
    af.add_argument("--no-proxy", action="store_true", help="alias for --proxy none: force direct")

    return p


def dispatch(p: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    g = args.group

    if g == "doctor":
        return cmd_doctor(args)
    if g == "beginner":
        return beginner_entry(p)
    if g == "adb":
        return cmd_adb(args)
    if g == "menu":
        return interactive_mode(p)
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
    if g == "mail":
        if not getattr(args, "mail_cmd", None):
            p.parse_args(["mail", "--help"])
            return 1
        return cmd_mail(args)
    if g == "gmail":
        if not getattr(args, "gmail_cmd", None):
            p.parse_args(["gmail", "--help"])
            return 1
        return cmd_gmail(args)
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
    if g == "autofarm":
        return cmd_autofarm(args)

    p.print_help()
    return 0


def cmd_autofarm(a) -> int:
    # autofarm drives Camoufox (falls back to Playwright) — both live ONLY in the
    # camoufox venv. The main venv has neither, so it crashed with ModuleNotFoundError.
    py = pick_python(camoufox=True)
    tool = AUTO_FREECF / "scripts" / "autofarm.py"
    cmd = [py, str(tool)]
    if getattr(a, "url", None):
        cmd.append(a.url)
    if getattr(a, "domain", None):
        cmd += ["--domain", a.domain]
    if getattr(a, "inject_9router", False):
        cmd.append("--inject-9router")
    if getattr(a, "out", None):
        cmd += ["--out", a.out]
    if getattr(a, "headless", False):
        cmd.append("--headless")
    # Smart egress auto-wire: resolve --proxy auto|none|URL against the target.
    force_none = bool(getattr(a, "no_proxy", False)) or str(getattr(a, "proxy", "") or "").lower() in ("none", "direct", "off", "no")
    mode = PROXY_NONE if force_none else (getattr(a, "proxy", None) or PROXY_AUTO)
    target = getattr(a, "url", None) or "https://example.com"
    choice = _choose_egress(mode, target)
    try:
        if force_none or not choice.proxy:
            # Tell autofarm explicitly to stay direct (it otherwise grabs pool[0]).
            cmd.append("--no-proxy")
            return run(cmd, cwd=AUTO_FREECF)
        cmd += ["--proxy", choice.proxy]
        return run(cmd, cwd=AUTO_FREECF, env=_proxy_env(choice))
    finally:
        _stop_auto_gateways()


UNIFIED_MENU: list[tuple[str, str, str, list[str] | None]] = [
    # Top option: guided pipeline
    ("0", "Run end-to-end setup (guided pipeline)", "end-to-end", None),

    # Group 1-2: Egress & Diagnostics
    ("1", "Check everything is healthy", "doctor", ["doctor"]),
    ("2", "Start the proxy gateway", "proxy start", ["proxy", "start"]),
    ("3", "Manage WARP tunnel", "warp", ["warp"]),
    ("4", "Harvest proxies", "proxy harvest", ["proxy", "harvest"]),

    # Group 3: Accounts (github/thk/gmail/k12/grok/stack)
    ("5", "GitHub Education account farm", "github farm", ["github", "farm"]),
    ("6", "Create Cloudflare accounts + tokens", "stack signup", ["stack", "signup"]),
    ("7", "Login to existing Cloudflare accounts", "stack login", ["stack", "login"]),
    ("8", "Create TokenHarbor keys", "thk batch", ["thk", "batch"]),
    ("9", "Grok farm", "grok run", ["grok", "run"]),
    ("10", "K-12 teacher verification", "k12 auto", ["k12", "auto"]),
    ("11", "Gmail account farm", "gmail farm", ["gmail", "farm"]),

    # Group 4: 9Router (inject/sync)
    ("12", "Inject Grok tokens into 9Router", "grok inject", ["grok", "inject"]),
    ("13", "Prune dead 9Router connections", "stack sync --prune", ["stack", "sync", "--prune"]),
    ("14", "Wire TokenHarbor env (harbor)", "thk setup-env", ["thk", "setup-env"]),

    # Group 5: Mail & Docs
    ("15", "School mailbox: test login", "mail test", ["mail", "test"]),
    ("16", "School mailbox: wait for OTP", "mail otp", ["mail", "otp"]),
    ("17", "Find SheerID links", "k12 link-finder", ["k12", "link-finder"]),
    ("18", "List teacher-doc countries", "yowes list", ["yowes", "list"]),
]

MENU_STAGE_HEADERS: dict[str, str] = {
    "0": "[0] Guided Pipeline Setup",
    "1": "[1-2] Egress & Diagnostics (proxy · WARP · doctor)",
    "5": "[3] Account Farms (GitHub · Cloudflare · TokenHarbor · Grok · K-12 · Gmail)",
    "12": "[4] 9Router Integration (inject · sync · prune)",
    "15": "[5] Mailbox & Verification Docs (mail · SheerID · docs)",
}


def render_menu() -> str:
    """Render the unified menu as text (non-interactive, testable)."""
    lines = []
    lines.append(f" {C['bold']}Unified Command Menu:{C['reset']}")
    for key, desc, cmd_str, _ in UNIFIED_MENU:
        if key in MENU_STAGE_HEADERS:
            lines.append(f"\n {C['bold']}{C['cyan']}── {MENU_STAGE_HEADERS[key]} ──{C['reset']}")
        cmd_part = f" {C['cyan']}({cmd_str}){C['reset']}" if cmd_str else ""
        lines.append(f"  [{col('bold', key)}] {desc:<42}{cmd_part}")
    lines.append("")
    lines.append(f"  [{col('bold', 'h')}] Help & command reference")
    lines.append(f"  [{col('bold', 'q')}] Exit")
    return "\n".join(lines)


def run_end_to_end_flow(p: argparse.ArgumentParser, farm_choice: str | None = None) -> int:
    """
    Execute the safe end-to-end pipeline in order:
      (a) Proxy verify / start (egress)
      (b) Chosen account farm
      (c) Inject to 9Router
      (d) Sync / health check

    Stops and reports honest error if any step fails.
    """
    print(col("bold", "\n════════════════════════════════════════════════════════════════"))
    print(col("bold", "               KancaHub End-to-End Setup Pipeline                "))
    print(col("bold", "════════════════════════════════════════════════════════════════"))
    print(col("dim",  "  Safe sequential flow: Egress -> Farm -> 9Router Inject -> Sync\n"))

    farms = [
        ("1", "GitHub Education", ["github", "farm"], None, ["stack", "sync"]),
        ("2", "Cloudflare Workers AI", ["stack", "signup"], ["stack", "inject"], ["stack", "sync"]),
        ("3", "TokenHarbor AI", ["thk", "batch"], ["thk", "inject"], ["thk", "sync"]),
        ("4", "Grok / xAI", ["grok", "run"], ["grok", "inject"], ["stack", "sync"]),
        ("5", "K-12 Teacher Verification", ["k12", "auto"], ["k12", "inject"], ["k12", "sync"]),
        ("6", "Gmail Farm", ["gmail", "farm"], None, ["stack", "sync"]),
    ]

    if farm_choice is None:
        print(col("bold", "  Choose target farm for this pipeline run:"))
        for f_key, f_name, f_cmd, _, _ in farms:
            print(f"    [{col('bold', f_key)}] {f_name:<28} {col('cyan', f'({chr(32).join(f_cmd)})')}")
        print(f"    [{col('bold', 'c')}] Cancel / Back to menu\n")

        try:
            f_choice = input(f"  {col('bold', 'Select farm [1-6, c]')}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nPipeline cancelled.")
            return 0
    else:
        f_choice = str(farm_choice).strip().lower()

    if f_choice in ("c", "cancel", "q", "quit", ""):
        print("\nPipeline cancelled.")
        return 0

    selected_farm = next((f for f in farms if f[0] == f_choice), None)
    if not selected_farm:
        print(col("yellow", f"✗ Invalid selection '{f_choice}'. Pipeline aborted."))
        return 1

    _, farm_label, farm_args, inject_args, sync_args = selected_farm
    print(col("cyan", f"\n▶ Selected pipeline target: {farm_label}\n"))

    # ── Step (a): Proxy Egress Verification / Start ──
    print(col("bold", "[Step 1/4] Egress check / proxy verification…"))
    rc_egress = dispatch(p, p.parse_args(["proxy", "verify"]))
    if rc_egress != 0:
        print(col("yellow", "\n• No active proxy masking detected."))
        try:
            start_ans = input(f" {col('bold', 'Start rotating proxy gateway on 127.0.0.1:8888 now? [Y/n]')}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            start_ans = "n"
        if start_ans in ("", "y", "yes"):
            rc_start = dispatch(p, p.parse_args(["proxy", "start"]))
            if rc_start != 0:
                print(col("red", "\n✗ Step 1 failed: Proxy gateway did not start. Stopping pipeline to avoid IP block."))
                return rc_start
        else:
            print(col("red", "\n✗ Step 1 stopped: Clean proxy egress is required for farm operations."))
            return 1
    print(col("green", "✓ Step 1 complete: Egress proxy verified.\n"))

    # ── Step (b): Account Farm ──
    print(col("bold", f"[Step 2/4] Running {farm_label} ({' '.join(farm_args)})…"))
    rc_farm = dispatch(p, p.parse_args(farm_args))
    if rc_farm != 0:
        print(col("red", f"\n✗ Step 2 failed: {farm_label} returned exit code {rc_farm}."))
        print(col("yellow", "  Stopping pipeline. Resolve issues before injecting to 9Router."))
        return rc_farm
    print(col("green", f"✓ Step 2 complete: {farm_label} completed successfully.\n"))

    # ── Step (c): Inject to 9Router ──
    if inject_args:
        print(col("bold", f"[Step 3/4] Injecting credentials into 9Router ({' '.join(inject_args)})…"))
        rc_inject = dispatch(p, p.parse_args(inject_args))
        if rc_inject != 0:
            print(col("red", f"\n✗ Step 3 failed: 9Router injection returned exit code {rc_inject}."))
            print(col("yellow", "  Stopping pipeline. Credentials were not registered."))
            return rc_inject
        print(col("green", "✓ Step 3 complete: Injected credentials into 9Router.\n"))
    else:
        print(col("dim", f"[Step 3/4] 9Router injection not required for {farm_label}. Skipping.\n"))

    # ── Step (d): Sync ──
    if sync_args:
        print(col("bold", f"[Step 4/4] Syncing 9Router status ({' '.join(sync_args)})…"))
        rc_sync = dispatch(p, p.parse_args(sync_args))
        if rc_sync != 0:
            print(col("red", f"\n✗ Step 4 failed: Sync returned exit code {rc_sync}."))
            return rc_sync
        print(col("green", "✓ Step 4 complete: Synced with 9Router.\n"))

    print(col("green", "════════════════════════════════════════════════════════════════"))
    print(col("green", f"✓ End-to-end setup for {farm_label} completed successfully!"))
    print(col("green", "════════════════════════════════════════════════════════════════\n"))
    return 0


def interactive_mode(p: argparse.ArgumentParser) -> int:
    """Beginner-friendly unified interactive menu when run without arguments."""
    print(get_ascii_banner())

    while True:
        print(render_menu())
        print()

        try:
            choice = input(f" {C['bold']}Select an option [0-{len(UNIFIED_MENU)-1}, h, q]: {C['reset']}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            return 0

        if not choice or choice in ("q", "quit", "exit"):
            return 0
        if choice == "h":
            p.print_help()
            return 0
        if choice == "0":
            try:
                run_end_to_end_flow(p)
            except Exception as e:
                print(col("red", f"Error in end-to-end setup: {e}"))
            print()
            try:
                input(f" {C['bold']}Press Enter to return to menu...{C['reset']}")
            except (EOFError, KeyboardInterrupt):
                return 0
            print()
            continue

        match = next((item for item in UNIFIED_MENU if item[0] == choice), None)
        if not match:
            print(col("yellow", f"  Please enter a valid option between 0 and {len(UNIFIED_MENU)-1}, h, or q."))
            continue

        _key, _desc, cmd_str, cmd_args = match
        if not cmd_args:
            continue

        print(col("cyan", f"\n▶ Running: kancahub {cmd_str}\n"))
        try:
            dispatch(p, p.parse_args(cmd_args))
        except Exception as e:
            print(col("red", f"Error: {e}"))
        print()
        try:
            input(f" {C['bold']}Press Enter to return to menu...{C['reset']}")
        except (EOFError, KeyboardInterrupt):
            return 0
        print()


def main() -> int:
    p = build_parser()
    if len(sys.argv) <= 1:
        # Default to the friendly beginner guide; power users can pick 'p'
        # (or use `kancahub menu`) for the classic command list.
        return beginner_entry(p)
    args = p.parse_args()
    return dispatch(p, args)


def beginner_entry(p: argparse.ArgumentParser) -> int:
    """Show the beginner wizard; offer an escape to the advanced menu."""
    sys.path.insert(0, str(AUTO_FREECF / "scripts"))
    try:
        from beginner import beginner_menu
    except Exception:
        return interactive_mode(p)  # fall back to the classic menu

    # offer advanced escape on first screen
    print(get_ascii_banner())
    print(col("bold", "  New here? Just answer the questions.\n"))
    print(col("dim", "  (advanced users: run `kancahub menu` for the full command list)\n"))
    try:
        return beginner_menu()
    except KeyboardInterrupt:
        print(col("dim", "\n  Bye!"))
        return 0


def menu_entry(p: argparse.ArgumentParser) -> int:
    """The classic numbered command menu (for advanced users)."""
    return interactive_mode(p)


if __name__ == "__main__":
    sys.exit(main())
