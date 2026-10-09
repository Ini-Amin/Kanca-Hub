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
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
PETANI = HOME / "petani-proxy"
K12_ROOT = PETANI / "Farm-Acc-ChatGPT-K-12-Teachers"
K12_DIR = K12_ROOT / "PyRuntime_64"
YOWES = PETANI / "yowes"
HARBOR = AUTO_FREECF / "harbor"  # vendored in-repo (was ~/harbor, lost 2026-10-07)
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


# ── shared core re-exported from kancahub_base (docs/refactor-kancahub.md STEP 1)
#
# kancahub is imported BOTH ways by the suite — `import kancahub` (scripts/ on
# sys.path) and `from scripts import kancahub` (repo root on sys.path). Those are
# two module objects; collapse them onto ONE so a patch always hits the object
# base reads through _kc() (H1). Then copy every base name in, including the
# underscore ones `import *` would skip.
_existing_kancahub = sys.modules.get("kancahub")
if _existing_kancahub is not None and _existing_kancahub is not sys.modules[__name__]:
    sys.modules[__name__] = _existing_kancahub   # reuse first-loaded copy
else:
    sys.modules.setdefault("kancahub", sys.modules[__name__])
    sys.modules.setdefault("scripts.kancahub", sys.modules[__name__])
import importlib as _importlib
import importlib.util as _importlib_util


def _load(name: str):
    """Import a sibling module under ONE name for both import spellings
    (`import kancahub` vs `from scripts import kancahub`), so patches hit it."""
    mod = sys.modules.get(name)
    if mod is None:
        try:
            mod = _importlib.import_module(name)
        except ModuleNotFoundError:  # `from scripts import kancahub` spelling
            spec = _importlib_util.spec_from_file_location(
                name, Path(__file__).resolve().parent / f"{name}.py")
            mod = _importlib_util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
    sys.modules.setdefault(f"scripts.{name}", mod)
    return mod


kancahub_base = _load("kancahub_base")
globals().update({_n: getattr(kancahub_base, _n)
                  for _n in dir(kancahub_base) if not _n.startswith("__")})

# Extracted command modules (docs/refactor-kancahub.md). Each name is re-exported
# here so dispatch() and tests keep using `kancahub.<name>` (H1).
_REEXPORTS = {
    "commands_region": "cmd_region",
    "commands_misc": "cmd_ip_reuse cmd_egress_node cmd_report",
    "commands_vendor": "cmd_zcode cmd_abliteration",
    "commands_9router": "cmd_adb cmd_mobile _r9_cli_token _r9_api cmd_9router",
    "commands_proxy": "_probe_http _egress_verdict cmd_proxy _proxy_start _proxy_export "
                      "_proxy_test_pool _proxy_stats _proxy_api _native_proxy_lib "
                      "_proxy_native_harvest _proxy_native_health _proxy_native_gateway",
    "commands_stack": "cmd_stack _stack_signup _stack_login _stack_manage",
    "commands_grok": "cmd_grok",
    "commands_github": "map_github_farm_args build_github_parser build_edu_steps edu_needs_human "
                       "edu_doc_upload_attempted _github_accounts_count _sheerid_find_url "
                       "_sheerid_run _print_edu_summary cmd_github",
    "commands_thk": "THK_PER_IP_DEFAULT THK_PER_IP_MAX thk_classify _thk_account_count _current_ip "
                    "_thk_fresh_ip thk_chunks cmd_thk _thk_sync cmd_mail cmd_gmail",
    "commands_farm": "_k12_guided cmd_k12 cmd_warp cmd_yowes",
    "commands_otp": "cmd_otp cmd_scrape cmd_autofarm",
    "doctor": "cmd_doctor",
    "menu": "MENU_BACKGROUND MENU_BACKGROUND_CMD UNIFIED_MENU MENU_STAGE_HEADERS MENU_FARM_KEYS "
            "render_menu run_end_to_end_flow _farm_kind _menu_ask_farm_options _jobs_menu "
            "interactive_mode beginner_entry menu_entry",
}
for _m, _names in _REEXPORTS.items():
    _mod = _load(_m)
    globals()[_m] = _mod
    globals().update({_n: getattr(_mod, _n) for _n in _names.split()})


def cmd_session(a) -> int:
    """One long-running CLI session: run a proxy gateway INSIDE it and run farms
    from the same prompt, reusing that gateway. The gateway is owned by this
    process and stops when you leave the session."""
    py = pick_python()
    petani = PETANI / "main.py"
    gw = SessionGateway(port=int(getattr(a, "port", 8888) or 8888),
                        target=int(getattr(a, "target", 30) or 30))

    def status_line() -> str:
        if gw.alive:
            pool = gw.pool_size
            pool_s = f"{pool} proxies" if pool is not None else "?"
            return col("green", f"gateway :{gw.port}  ● UP  ({pool_s})")
        return col("dim", f"gateway :{gw.port}  ○ down")

    p = build_parser()

    # optionally auto-start the gateway at session start
    if getattr(a, "start_gateway", False) and petani.exists():
        gw.start(petani, py)

    print(col("bold", "\n  kancahub session — one CLI for proxy + farms.\n"))
    print(col("dim", "  the gateway runs INSIDE this session (tracked here), farms reuse it.\n"))

    while True:
        print("  " + status_line())
        print()
        print("   [g] start/refresh gateway      [s] stop gateway        [i] gateway info")
        print("   [1] github farm      [2] thk batch    [3] grok run    [4] gmail farm")
        print("   [5] k12 auto         [6] stack signup [7] autofarm    [d] doctor")
        print("   [q] quit (stops the gateway)")
        try:
            ch = input(f"\n  {col('bold', 'session')}> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if ch in ("q", "", "quit", "exit"):
            break
        if ch == "g":
            if petani.exists():
                gw.start(petani, py)
            else:
                print(col("red", f"✗ PetaniProxy not found at {PETANI}"))
            continue
        if ch == "s":
            gw.stop()
            continue
        if ch == "i":
            if gw.alive:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{gw.port}/api/status", timeout=4) as r:
                        print(col("dim", r.read().decode()[:600]))
                except Exception as e:  # noqa: BLE001
                    print(col("red", f"  could not read gateway info: {e}"))
            else:
                print(col("dim", "  gateway is down — press [g] to start"))
            continue

        # farms run in this same session; they inherit the gateway via --proxy.
        proxy_arg = f"http://127.0.0.1:{gw.port}" if gw.alive else "auto"
        farms = {
            "1": ["github", "farm", "--proxy", proxy_arg],
            "2": ["thk", "batch", "1", "--proxy", proxy_arg],
            "3": ["grok", "run", "-n", "1", "--proxy", proxy_arg],
            "4": ["gmail", "farm", "--proxy", proxy_arg],
            "5": ["k12", "auto", "--proxy", proxy_arg],
            "6": ["stack", "signup", "-n", "1"],
            "7": [],  # autofarm prompts for a URL
            "d": ["doctor"],
        }
        if ch == "7":
            try:
                url = input("  target URL: ").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if not url:
                continue
            argv = ["autofarm", url, "--proxy", proxy_arg]
        elif ch in farms and farms[ch]:
            argv = farms[ch]
        else:
            print(col("yellow", "  unknown option"))
            continue

        print(col("cyan", f"\n  ▶ kancahub {' '.join(argv)}"))
        try:
            dispatch(p, p.parse_args(argv))
        except SystemExit:
            pass
        except Exception as e:  # noqa: BLE001
            print(col("red", f"  error: {e}"))
        print()

    gw.stop()
    print(col("dim", "\n  session closed.\n"))
    return 0


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
    r9 = sub.add_parser("9router", help="9Router helpers incl. the MITM proxy (route Antigravity/Copilot/Kiro IDE traffic through 9Router)")
    r9s = r9.add_subparsers(dest="r9_cmd")
    r9m = r9s.add_parser("mitm", help="9Router MITM proxy: status/enable/disable/trust-cert")
    r9m.add_argument("action", nargs="?", default="status", choices=["status", "enable", "disable", "trust-cert"])
    r9m.add_argument("--tool", default="antigravity", help="IDE tool: antigravity|copilot|kiro|cursor (default antigravity)")
    r9m.add_argument("--sudo-password", default=None, help="sudo password (mitm binds :443 + installs a CA)")
    r9m.add_argument("--api-key", default=None, help="9Router API key for the mitm enable step (auto-fetched if omitted)")
    r9p = r9s.add_parser("proxy", help="9Router outbound proxy pool (add/list)")
    r9p.add_argument("action", nargs="?", default="list", choices=["list", "add", "add-file"])
    r9p.add_argument("proxy_url", nargs="?", default=None, help="proxy URL, or a file path for add-file")
    r9p.add_argument("--name", default="", help="pool name")
    r9p.add_argument("--type", default="http", choices=["http", "vercel", "cloudflare", "deno"])
    r9c = r9s.add_parser("combo", help="9Router combos (fallback model groups)")
    r9c.add_argument("action", nargs="?", default="list", choices=["list", "make-thk-fallback"])
    r9c.add_argument("--name", default="thk-fallback", help="combo name (default thk-fallback)")
    r9c.add_argument("--models", default=None, help="comma list of models (default: free THK models)")
    r9c.add_argument("--base", default="http://localhost:20128")
    r9m.add_argument("--base", default="http://localhost:20128", help="9Router base URL (default :20128)")
    ipr = sub.add_parser("ip-reuse", help="CGNAT session guard: exit-IP reuse per IP + mid-session IP changes")
    ipr.add_argument("--clear", action="store_true", help="clear the recorded session history")
    en = sub.add_parser("egress-node", help="use a device's OWN connection as an egress node (phone/PC on the same network)")
    ens = en.add_subparsers(dest="en_cmd")
    ens.add_parser("list", help="list registered device nodes + probe them (default)")
    ensrv = ens.add_parser("serve", help="run THIS device as an egress node (proxy on its own IP)")
    ensrv.add_argument("--host", default="0.0.0.0", help="bind interface (default 0.0.0.0; use a Tailscale IP for safety)")
    ensrv.add_argument("--port", type=int, default=8899)
    ensrv.add_argument("-b", "--background", action="store_true", help="run the node in the background")
    ena = ens.add_parser("add", help="register a remote device node")
    ena.add_argument("name")
    ena.add_argument("url", help="e.g. http://100.77.106.64:8899")
    enr = ens.add_parser("remove", help="forget a device node")
    enr.add_argument("name")
    ses = sub.add_parser("session", help="one long-running CLI: run the proxy gateway inside it and run farms that reuse it")
    ses.add_argument("--port", type=int, default=8888, help="gateway port (default 8888)")
    ses.add_argument("--target", type=int, default=30, help="proxy pool target size (default 30)")
    ses.add_argument("--start-gateway", action="store_true", help="start the gateway immediately at session start")
    rep = sub.add_parser("report", help="show the farm run ledger (what worked/failed across runs)")
    rep.add_argument("-n", "--tail", type=int, default=0, help="also show the last N entries")
    rep.add_argument("--clear", action="store_true", help="clear the ledger")
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
    # ---- mobile (rotate the tethered phone's carrier IP = a "free residential" hop) ----
    mop = sub.add_parser("mobile", help="rotate the tethered phone's MOBILE carrier IP (real mobile IP bypasses datacenter blocks)")
    mos = mop.add_subparsers(dest="mobile_cmd")
    mos.add_parser("status", help="show the phone, its network (Wi-Fi vs mobile data), egress IP and a verdict (default)")
    mor = mos.add_parser("rotate", help="toggle airplane mode to get a fresh carrier IP")
    mor.add_argument("--until", default=None, help="keep rotating until the new IP starts with this prefix")
    mor.add_argument("--wait", type=float, default=20.0, help="max seconds to wait for the new IP (default 20)")
    mor.add_argument("--force", action="store_true", help="rotate even if the phone is not on mobile data")
    msv = mos.add_parser("saving", help="IP-only saving mode: phone IP for the signup, Wi-Fi for the heavy traffic + data cap")
    msv.add_argument("action", nargs="?", default="status", choices=["status", "start", "used", "stop"])
    msv.add_argument("--cap-mb", type=float, default=50, help="mobile-data budget per session (default 50 MB)")
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
    tb.add_argument("--per-ip", type=int, default=THK_PER_IP_DEFAULT, metavar="N",
                    help=f"accounts per egress IP before rotating (default {THK_PER_IP_DEFAULT}, max {THK_PER_IP_MAX})")
    tb.add_argument("--concurrency", type=int, default=None, metavar="N",
                    help="parallel workers within a chunk (default: harbor's; each uses its own proxy/browser)")
    tb.add_argument("--inject", action="store_true", help="inject new keys into 9Router when the batch ends")
    tb.add_argument("--store", choices=["9router", "ledger", "both"], default=None,
                    help="where to record results: 9router (SQLite via inject), ledger (jsonl), both")
    tsetup = ts.add_parser("setup", help="full setup on TokenHarbor (interactive)")
    tck = ts.add_parser("create-key", help="create an API key for an existing account")
    # Farm commands: wire kancahub's smart egress in by default (fallback: harbor's
    # own proxy_list.txt, untouched).
    for _farm_parser in (tb, tsetup, tck):
        _farm_parser.add_argument(
            "--proxy", default="auto", metavar="PROXY",
            help=PROXY_HELP + "\n  (inherited by harbor via the environment)")
        _farm_parser.add_argument("--no-proxy", action="store_true", help="alias for --proxy none")
        _farm_parser.add_argument(
            "--country", default=None, metavar="CC",
            help="egress country filter (e.g. US, SG; default: auto other-country, excludes ID for thk)",
        )
        _farm_parser.add_argument(
            "--mobile-rotate", action="store_true",
            help="rotate tethered phone carrier IP before run & retry on block",
        )
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
    tse.add_argument("--harbor-dir", default=None, help="harbor repo dir (default <repo>/harbor)")
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
    gs.add_parser("check", help="check the grok-register backend + mail env, then exit")
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

    # ---- otp (Litensi email activation — cheap pay-per-code mailboxes) ----
    op = sub.add_parser("otp", help="Litensi email activation (pay-per-code OTP mailboxes)")
    os_ = op.add_subparsers(dest="otp_cmd")
    os_.add_parser("profile", help="show Litensi balance / account")
    op_prices = os_.add_parser("prices", help="list zones + stock for a site")
    op_prices.add_argument("--site", default=None)
    op_order = os_.add_parser("order", help="order a mailbox")
    op_order.add_argument("--site", default=None)
    op_wait = os_.add_parser("wait", help="poll for the code")
    op_wait.add_argument("--order-id", required=True)
    op_wait.add_argument("--email", default="")
    op_wait.add_argument("--timeout", type=int, default=240)
    op_done = os_.add_parser("done", help="mark an order SUCCESS (code used)")
    op_done.add_argument("--order-id", required=True)
    opw = os_.add_parser("webhook", help="Litensi SMS webhook + public tunnel (up/down/url/status)")
    opw.add_argument("action", nargs="?", default="up", choices=["up", "down", "url", "status"])
    opw.add_argument("--latest", action="store_true", help="print the newest received SMS code")

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
    gmf.add_argument("--no-proxy", action="store_true", help="force a direct connection (use your own egress)")
    gmf.add_argument("--out", default=None, help="JSON results file (appended to)")
    gmf.add_argument("--dry-run", action="store_true", help="walk the flow, do not submit")
    gmf.add_argument("--random-password", action="store_true", help="generate a password per account")
    gmf.add_argument("--mobile-rotate", action="store_true", help="rotate tethered phone carrier IP before run & retry on block")
    gmf.add_argument("--plus-address", default=None, metavar="EMAIL",
                     help="base Gmail you OWN for plus-addressing (redirects mail; does NOT create "
                          "Gmail accounts and is not used by THIS farm — intended for service signups)")
    gmf.add_argument("--plus-prefix", default="farm", metavar="STR", help="tag prefix for plus-addressing (default: farm)")
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

    gmslow = gms.add_parser("slow", help="drip-feed: create 1 Gmail every few days (safest)")
    gmslow.add_argument("action", nargs="?", choices=["check", "run", "reset"], default="check",
                        help="check (default) | run (create if due) | reset")
    gmslow.add_argument("--interval-days", type=float, default=3.5,
                        help="min days between accounts (default 3.5)")
    gmslow.add_argument("--backend", choices=["auto", "device", "desktop"], default="auto")
    gmslow.add_argument("--count", type=int, default=1)

    gmw = gms.add_parser("waydroid",
                          help="create Gmail on Waydroid (fresh Android 13 + GMS) via Kiwi CDP")
    gmw.add_argument("--cdp", default="http://127.0.0.1:9222", help="Kiwi CDP endpoint (default :9222)")
    gmw.add_argument("--first", default="Victor")
    gmw.add_argument("--last", default="Hayes")
    gmw.add_argument("--password", default=None)
    gmw.add_argument("--month", default="May")
    gmw.add_argument("--day", type=int, default=15)
    gmw.add_argument("--year", type=int, default=1991)
    gmw.add_argument("--md", default=None,
                     help="Markdown store for created accounts (default ~/gmail_accounts.md)")
    gmw.add_argument("--wait", type=int, default=180, help="seconds to wait for you to scan the QR")
    gmw.add_argument("--no-launch", action="store_true", help="skip Waydroid UI/Kiwi launch/adb forward")

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
        g.add_argument("-b", "--background", action="store_true",
                       help="detach the gateway into the background (CLI keeps working)")

    d = ps.add_parser("daemon", help="24/7 auto-healing gateway on :8888")
    d.add_argument("-b", "--background", action="store_true",
                   help="detach the daemon into the background (CLI keeps working)")

    st = ps.add_parser("stop", help="stop a background gateway/daemon started with -b")
    st.add_argument("--name", default="gateway", help="background job name (default: gateway)")

    r = ps.add_parser("residential", help="Webshare residential hunter")
    r.add_argument("-n", "--accounts", type=int, default=1)
    r.add_argument("--headless", action="store_true")

    ri = ps.add_parser("ripool", help="RapidProxy/SwiftProxy free-trial signup -> residential pool")
    ri.add_argument("vendor", choices=["rapidproxy", "swiftproxy"])
    ri.add_argument("-n", "--accounts", type=int, default=1)
    ri.add_argument("--headless", action="store_true")

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

    # ---- diagnose (where each tool breaks) ----
    dg = sub.add_parser("diagnose", help="one-line verdicts: where each tool breaks (egress/DataDome/phone/OTP/webhook)")
    dg.add_argument("--live", action="store_true", help="also probe egress IP + github.com/signup")

    # ---- hunt (keyword -> free-AI-key sites; prove ONE, then scale) ----
    hu = sub.add_parser("hunt", help="hunt free-AI-key sites by keyword (discover -> rank -> prove one -> scale)")
    hus = hu.add_subparsers(dest="hunt_cmd")
    hd = hus.add_parser("discover", help="Firecrawl-search keywords, rank + dedupe free-key sites")
    hd.add_argument("--query", default=None)
    hd.add_argument("--queries", default=None, help="comma-separated queries (default: free ai, bansos ai, ...)")
    hd.add_argument("--limit", type=int, default=8)
    hus.add_parser("report", help="show the last discovery, ranked")

    # ---- scrape (Firecrawl, using the keys already in 9Router) ----
    sc = sub.add_parser("scrape", help="Firecrawl: clean-markdown scrape / web search (uses 9Router's firecrawl keys)")
    scs = sc.add_subparsers(dest="scrape_cmd")
    sc_scr = scs.add_parser("url", help="scrape a URL to clean markdown")
    sc_scr.add_argument("url")
    sc_ser = scs.add_parser("search", help="web search")
    sc_ser.add_argument("query")
    sc_ser.add_argument("--limit", type=int, default=5)
    scs.add_parser("key", help="show which firecrawl key is used (masked)")

    # ---- vendor passthroughs (tools vendored in ~/Auto-FreeCF-vendor-refs) ----
    zc = sub.add_parser("zcode",
                        help="zcode-claim: z.ai account -> ZCode JWT -> claim free Start Plan -> 9Router glm pool")
    zc.add_argument("mode", nargs="?", choices=["claim", "console", "connect"], default="claim",
                    help="claim (CLI orchestrator), console (web UI bridge), connect (register keys)")
    zc.add_argument("extra", nargs=argparse.REMAINDER,
                    help="args passed straight to the vendored script (e.g. -- --headless)")

    ab = sub.add_parser("abliteration",
                        help="abliteration-bulk-creator: bulk-create abliteration.ai accounts with API keys")
    ab.add_argument("mode", nargs="?", choices=["run", "test", "check-proxies"], default="run",
                    help="run (create accounts), test (offline self-test), check-proxies")
    ab.add_argument("extra", nargs=argparse.REMAINDER,
                    help="args passed straight to node (e.g. -- -n 5 -c 2)")

    # ---- autofarm ----
    af = sub.add_parser("autofarm", help="paste any website URL to adapt and autofarm with clean proxies")
    af.add_argument("url", nargs="?", default=None, help="website signup/login URL")
    af.add_argument("--domain", choices=["kancalabs.biz.id", "kancalabs.my.id", "biz.id", "my.id"], default="kancalabs.my.id",
                    help="disposable email domain (default: kancalabs.my.id — Tempik, readable)")
    af.add_argument("--mail", choices=["auto", "tempik", "relay", "litensi", "static", "gmail", "emailmux", "emailnator", "mailtm"], default="auto",
                    help="mailbox provider (default: auto by domain)")
    af.add_argument("--inject-9router", action="store_true", help="inject credentials into 9Router SQLite DB")
    af.add_argument("--out", default=None, help="output JSON path (default: results/autofarm_accounts.json)")
    af.add_argument("--headless", action="store_true", help="run without showing browser UI")
    af.add_argument("--proxy", default=PROXY_AUTO,
                    help="egress mode (default: auto). auto = smart auto-wire (local gateway -> "
                         "pool gateway -> WARP -> residential -> direct, verified per target); "
                         "none = force direct; WARP = force Cloudflare WARP; or an explicit "
                         "proxy URL such as http://127.0.0.1:8888")
    af.add_argument("--no-proxy", action="store_true", help="alias for --proxy none: force direct")
    af.add_argument("--inspect-only", action="store_true", help="detect auth methods (GitHub/Google/email) and exit — no filling, nothing written")
    af.add_argument("--mobile-rotate", action="store_true", help="rotate tethered phone carrier IP before run & retry on block")
    af.add_argument("--plus-address", default=None, metavar="EMAIL", help="use plus-addressing (you+farm1@gmail.com) instead of disposable domain")
    af.add_argument("--plus-prefix", default="farm", metavar="STR", help="tag prefix for plus-addressing (default: farm)")

    return p


def dispatch(p: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    g = args.group

    if g == "doctor":
        return cmd_doctor(args)
    if g == "report":
        return cmd_report(args)
    if g == "9router":
        return cmd_9router(args)
    if g == "ip-reuse":
        return cmd_ip_reuse(args)
    if g == "egress-node":
        return cmd_egress_node(args)
    if g == "session":
        return cmd_session(args)
    if g == "beginner":
        return beginner_entry(p)
    if g == "adb":
        return cmd_adb(args)
    if g == "mobile":
        return cmd_mobile(args)
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
    if g == "otp":
        if not getattr(args, "otp_cmd", None):
            p.parse_args(["otp", "--help"])
            return 1
        return cmd_otp(args)
    if g == "scrape":
        return cmd_scrape(args)
    if g == "zcode":
        return cmd_zcode(args)
    if g == "abliteration":
        return cmd_abliteration(args)
    if g == "hunt":
        tool = AUTO_FREECF / "scripts" / "ai_key_hunt.py"
        if not tool.exists():
            print(col("red", "✗ ai_key_hunt.py not found"))
            return 1
        sub = getattr(args, "hunt_cmd", None) or "report"
        cmd = [pick_python(), str(tool), sub]
        if sub == "discover":
            if getattr(args, "query", None):
                cmd += ["--query", args.query]
            if getattr(args, "queries", None):
                cmd += ["--queries", args.queries]
            cmd += ["--limit", str(getattr(args, "limit", 8))]
        return run(cmd, cwd=AUTO_FREECF)
    if g == "diagnose":
        tool = AUTO_FREECF / "scripts" / "diagnose.py"
        cmd = [pick_python(), str(tool)]
        if getattr(args, "live", False):
            cmd.append("--live")
        return run(cmd, cwd=AUTO_FREECF)

    p.print_help()
    return 0


def main() -> int:
    p = build_parser()
    if len(sys.argv) <= 1:
        # Default to the friendly beginner guide; power users can pick 'p'
        # (or use `kancahub menu`) for the classic command list.
        return beginner_entry(p)
    args = p.parse_args()
    return dispatch(p, args)


if __name__ == "__main__":
    sys.exit(main())
