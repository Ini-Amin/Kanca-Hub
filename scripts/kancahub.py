#!/usr/bin/env python3
"""
KancaHub — one CLI for the whole account-farming toolkit.

Wraps four tools into a single, friendly command surface:

  stack     Auto-FreeCF   — Cloudflare Workers AI account + token pipeline
  proxy     PetaniProxy   — rotating proxy gateway, harvest, WARP, Webshare hunter
  k12       ChatGPT K-12  — SheerID teacher verification flow
  yowes     Yowes         — teacher document generator (13 countries)

Examples:
    kancahub doctor                 # check every tool's health/deps
    kancahub proxy harvest           # harvest fresh public proxies
    kancahub proxy gateway           # start rotating gateway on :8888
    kancahub proxy residential       # Webshare hunter -> fresh residential IPs
    kancahub stack run -n 3          # signup -> verify -> inject (9Router)
    kancahub stack run -n 3 --gateway
    kancahub k12 run <verify-url> --proxy 127.0.0.1:8888
    kancahub yowes list              # countries + document types
    kancahub yowes make --country us --first John --last Doe \
        --school "Thomas Jefferson" --position Teacher --dob 1985-03-15

Run `kancahub <group> --help` for per-group options.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
PETANI = HOME / "petani-proxy"
K12_DIR = PETANI / "Farm-Acc-ChatGPT-K-12-Teachers" / "PyRuntime_64"
YOWES = PETANI / "yowes"
VENV_PY = HOME / ".local" / "share" / "auto-freecf" / "venv" / "bin" / "python"

C = {
    "reset": "\x1b[0m", "bold": "\x1b[1m", "dim": "\x1b[2m",
    "cyan": "\x1b[36m", "green": "\x1b[32m", "yellow": "\x1b[33m",
    "red": "\x1b[31m", "magenta": "\x1b[35m",
}


def col(name: str, text: str) -> str:
    return f"{C.get(name, '')}{text}{C['reset']}"


def banner(title: str) -> None:
    print()
    print(col("cyan", "═" * 64))
    print(col("bold", f"  {title}"))
    print(col("cyan", "═" * 64))


def pick_python() -> str:
    v = str(VENV_PY)
    if VENV_PY.exists() and os.access(v, os.X_OK):
        return v
    return shutil.which("python3") or sys.executable


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> int:
    print(col("dim", f"$ {' '.join(cmd)}" + (f"   (cwd={cwd})" if cwd else "")))
    e = os.environ.copy()
    if env:
        e.update(env)
    try:
        return subprocess.call(cmd, cwd=str(cwd) if cwd else None, env=e)
    except FileNotFoundError as ex:
        print(col("red", f"✗ {ex}"))
        return 127


# ---------------------------------------------------------------- doctor

def cmd_doctor(_args) -> int:
    banner("KancaHub doctor — checking the toolkit")
    py = pick_python()
    print(f"  python : {py}")

    checks = [
        ("Auto-FreeCF dir", AUTO_FREECF.exists()),
        ("  pipeline.py", (AUTO_FREECF / "scripts" / "pipeline.py").exists()),
        ("  inject_9router.py", (AUTO_FREECF / "scripts" / "inject_9router.py").exists()),
        ("PetaniProxy dir", PETANI.exists()),
        ("  main.py", (PETANI / "main.py").exists()),
        ("K-12 tool", (K12_DIR / "script.py").exists()),
        ("Yowes dir", YOWES.exists()),
        ("  countries/", (YOWES / "countries").exists()),
        ("9Router DB", (HOME / ".9router" / "db" / "data.sqlite").exists()),
        ("Secrets .env", (HOME / ".config" / "auto-freecf" / ".env").exists()),
        ("Proxies pool", (AUTO_FREECF / "signup_from_scratch" / "proxies.txt").exists()),
    ]
    for label, ok in checks:
        print(f"  {'✅' if ok else '❌'} {label}")

    print(col("bold", "\n  Python modules (venv):"))
    for mod in ("nodriver", "patchright", "httpx", "requests", "curl_cffi",
                "cloudscraper", "DrissionPage", "speech_recognition", "pydub",
                "PIL"):
        r = subprocess.run([py, "-c", f"import {mod}"], capture_output=True)
        print(f"  {'✅' if r.returncode == 0 else '❌'} {mod}")

    print(col("bold", "\n  External binaries:"))
    for b in ("google-chrome", "ffmpeg", "git"):
        print(f"  {'✅' if shutil.which(b) else '❌'} {b}")

    gw = False
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8888", timeout=2):
            gw = True
    except Exception:
        pass
    print(f"  {'✅' if gw else '➖'} rotating gateway on :8888 {'(running)' if gw else '(not running)'}")
    return 0


# ---------------------------------------------------------------- proxy

def cmd_proxy(args) -> int:
    py = pick_python()
    main = PETANI / "main.py"
    if not main.exists():
        print(col("red", f"✗ PetaniProxy not found at {PETANI}"))
        return 1

    sub = args.proxy_cmd
    if sub == "harvest":
        cmd = [py, "main.py", "--target", str(args.target),
               "--max", str(args.max), "--workers", str(args.workers)]
        if args.country:
            cmd += ["--country", args.country]
        if args.protocol:
            cmd += ["--protocol", args.protocol]
        return run(cmd, cwd=PETANI)

    if sub == "gateway":
        print(col("yellow", f"Starting rotating gateway on :{args.port} (Ctrl-C to stop)…"))
        return run([py, "main.py", "--serve", str(args.port),
                    "--target", str(args.target)], cwd=PETANI)

    if sub == "daemon":
        print(col("yellow", "Starting 24/7 auto-healing gateway on :8888…"))
        return run([py, "main.py", "--daemon-gateway"], cwd=PETANI)

    if sub == "residential":
        return run([py, "main.py", "-W", str(args.accounts)], cwd=PETANI)

    if sub == "warp":
        return run([py, "main.py", "-C"], cwd=PETANI)

    if sub == "export":
        src = PETANI / "output" / "live_elite.txt"
        if not src.exists():
            src = PETANI / "output" / "live_all.txt"
        if not src.exists():
            print(col("red", "✗ No harvested proxies found. Run `kancahub proxy harvest` first."))
            return 1
        dst = AUTO_FREECF / "signup_from_scratch" / "proxies.txt"
        lines = [l.strip() for l in src.read_text().splitlines() if l.strip()]
        dst.write_text("\n".join(lines) + "\n")
        print(col("green", f"✓ Exported {len(lines)} proxies -> {dst}"))
        return 0

    if sub == "test":
        dst = AUTO_FREECF / "signup_from_scratch" / "proxies.txt"
        if not dst.exists():
            print(col("red", f"✗ {dst} not found"))
            return 1
        lines = [l.strip() for l in dst.read_text().splitlines() if l.strip()]
        ok = 0
        for p in lines:
            ip = subprocess.run(
                ["curl", "-s", "-m", "10", "-x", p, "https://api.ipify.org"],
                capture_output=True, text=True).stdout.strip()
            print(f"  {'✅' if ip else '❌'} {ip or 'dead'}")
            ok += bool(ip)
        print(col("green", f"\n  {ok}/{len(lines)} live"))
        return 0

    print(col("red", "✗ unknown proxy command"))
    return 1


# ---------------------------------------------------------------- stack

def cmd_stack(args) -> int:
    py = pick_python()
    pipeline = AUTO_FREECF / "scripts" / "pipeline.py"
    if not pipeline.exists():
        print(col("red", f"✗ pipeline.py not found at {pipeline}"))
        return 1

    sub = args.stack_cmd
    if sub == "run":
        cmd = [py, str(pipeline), "-n", str(args.accounts)]
        if args.gateway:
            cmd += ["--gateway", args.gateway]
        if args.proxy:
            cmd += ["--proxy", args.proxy]
        if args.proxy_pool:
            cmd += ["--proxy-pool", args.proxy_pool]
        if args.headless:
            cmd += ["--headless"]
        if args.fast:
            cmd += ["--fast"]
        if args.workers:
            cmd += ["--workers", str(args.workers)]
        if args.no_inject:
            cmd += ["--no-inject"]
        if args.output:
            cmd += ["--output", args.output]
        return run(cmd, cwd=AUTO_FREECF)

    if sub == "inject":
        inject = AUTO_FREECF / "scripts" / "inject_9router.py"
        cmd = [py, str(inject)]
        if args.input:
            cmd += ["-i", args.input]
        if not args.no_verify:
            cmd.append("--verify")
        return run(cmd, cwd=AUTO_FREECF)

    print(col("red", "✗ unknown stack command"))
    return 1


# ---------------------------------------------------------------- k12

def cmd_k12(args) -> int:
    py = pick_python()
    script = K12_DIR / "script.py"
    if not script.exists():
        print(col("red", f"✗ K-12 script not found at {script}"))
        return 1

    sub = args.k12_cmd
    if sub == "run":
        if not args.url:
            print(col("red", "✗ verification URL required: kancahub k12 run <url>"))
            return 1
        cmd = [py, str(script), args.url]
        if args.proxy:
            cmd += ["--proxy", args.proxy]
        elif args.gateway:
            cmd += ["--proxy", "127.0.0.1:8888"]
        if args.debug:
            cmd += ["--debug"]
        if args.email:
            cmd += ["--email", args.email]
        if args.no_temp_email:
            cmd += ["--no-temp-email"]
        return run(cmd, cwd=K12_DIR)

    print(col("red", "✗ unknown k12 command"))
    return 1


# ---------------------------------------------------------------- yowes

def cmd_yowes(args) -> int:
    py = pick_python()
    if not YOWES.exists():
        print(col("red", f"✗ Yowes not found at {YOWES}"))
        return 1

    sub = args.yowes_cmd

    if sub == "list":
        script = (
            "import sys; sys.path.insert(0,'.');"
            "from countries import list_countries, get_country;"
            "print('Available countries:');"
            "[print('  ', c, '->', ', '.join(get_country(c)().get_document_types())) for c in list_countries()]"
        )
        return run([py, "-c", script], cwd=YOWES)

    if sub == "schools":
        script = (
            "import sys; sys.path.insert(0,'.');"
            "from countries import get_country;"
            f"gen=get_country('{args.country}')();"
            "[print('  ', s['name']) for s in gen.schools]"
        )
        return run([py, "-c", script], cwd=YOWES)

    if sub == "make":
        types = args.types.split(",") if args.types else None
        script = f"""
import sys; sys.path.insert(0,'.')
from mcp_server import generate_documents
res = generate_documents(
    country={args.country!r},
    first_name={args.first!r},
    last_name={args.last!r},
    school_name={args.school!r},
    position={args.position!r},
    date_of_birth={args.dob!r},
    gender={args.gender!r},
    document_types={types!r},
    output_dir={args.out!r},
)
print('✓ generated', res['count'], 'document(s) ->', res['output_dir'])
for f in res['files']:
    print('   ', f)
"""
        return run([py, "-c", script], cwd=YOWES)

    if sub == "mcp":
        return run([py, "mcp_server.py"], cwd=YOWES)

    print(col("red", "✗ unknown yowes command"))
    return 1


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kancahub",
        description="KancaHub — one CLI for Cloudflare farming, proxies, K-12 & docs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="group")

    sub.add_parser("doctor", help="check every tool's health and dependencies")

    pp = sub.add_parser("proxy", help="PetaniProxy: harvest / gateway / residential / warp")
    ps = pp.add_subparsers(dest="proxy_cmd")
    h = ps.add_parser("harvest", help="harvest + validate public proxies")
    h.add_argument("--target", type=int, default=20)
    h.add_argument("--max", type=int, default=400)
    h.add_argument("--workers", type=int, default=100)
    h.add_argument("--country", default=None, help="ISO code, e.g. US, SG, ID")
    h.add_argument("--protocol", choices=["all", "http", "socks4", "socks5"], default=None)
    g = ps.add_parser("gateway", help="start rotating forward proxy")
    g.add_argument("--port", type=int, default=8888)
    g.add_argument("--target", type=int, default=30)
    ps.add_parser("daemon", help="24/7 auto-healing gateway")
    r = ps.add_parser("residential", help="Webshare hunter -> fresh residential IPs")
    r.add_argument("-n", "--accounts", type=int, default=1)
    ps.add_parser("warp", help="generate Cloudflare WARP profile")
    ps.add_parser("export", help="export harvested proxies -> Auto-FreeCF pool")
    ps.add_parser("test", help="validate the Auto-FreeCF proxy pool")

    sp = sub.add_parser("stack", help="Auto-FreeCF: signup -> verify -> inject")
    ss = sp.add_subparsers(dest="stack_cmd")
    sr = ss.add_parser("run", help="run the full pipeline")
    sr.add_argument("-n", "--accounts", type=int, default=1)
    sr.add_argument("--gateway", nargs="?", const="http://127.0.0.1:8888", default=None)
    sr.add_argument("--proxy", default=None)
    sr.add_argument("--proxy-pool", default=None)
    sr.add_argument("--workers", type=int, default=None)
    sr.add_argument("--headless", action="store_true")
    sr.add_argument("--fast", action="store_true")
    sr.add_argument("--no-inject", action="store_true")
    sr.add_argument("--output", default=None)
    si = ss.add_parser("inject", help="inject existing results into 9Router")
    si.add_argument("-i", "--input", default=None)
    si.add_argument("--no-verify", action="store_true")

    kp = sub.add_parser("k12", help="ChatGPT K-12 teacher verification")
    ks = kp.add_subparsers(dest="k12_cmd")
    kr = ks.add_parser("run", help="run verification for a SheerID URL")
    kr.add_argument("url", nargs="?", help="SheerID verification URL")
    kr.add_argument("--proxy", default=None, help="IP:port or user:pass@ip:port")
    kr.add_argument("--gateway", action="store_true", help="use 127.0.0.1:8888")
    kr.add_argument("--debug", action="store_true")
    kr.add_argument("--email", default=None)
    kr.add_argument("--no-temp-email", action="store_true")

    yp = sub.add_parser("yowes", help="teacher document generator")
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
    ym.add_argument("--types", default=None, help="comma list, e.g. teacher_id,employment_letter")
    ym.add_argument("--out", default="")
    ys.add_parser("mcp", help="run the yowes MCP server (stdio)")

    return p


def main() -> int:
    p = build_parser()
    args = p.parse_args()

    if args.group == "doctor":
        return cmd_doctor(args)
    if args.group == "proxy":
        if not getattr(args, "proxy_cmd", None):
            p.parse_args(["proxy", "--help"])
            return 1
        return cmd_proxy(args)
    if args.group == "stack":
        if not getattr(args, "stack_cmd", None):
            p.parse_args(["stack", "--help"])
            return 1
        return cmd_stack(args)
    if args.group == "k12":
        if not getattr(args, "k12_cmd", None):
            p.parse_args(["k12", "--help"])
            return 1
        return cmd_k12(args)
    if args.group == "yowes":
        if not getattr(args, "yowes_cmd", None):
            p.parse_args(["yowes", "--help"])
            return 1
        return cmd_yowes(args)

    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
