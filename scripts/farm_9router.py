#!/usr/bin/env python3
"""
farm_9router — unified launcher for the tokenmix + zerotwo (zt-harvester) farms.

Wires both farms to:
  (a) kancahub proxy egress  — --proxy auto|none|URL (default auto) resolved via
      scripts/egress.py (auto_egress when present, else ensure_clean_egress);
  (b) 9Router at http://localhost:20128/v1 (management API :20128, OpenAI /v1).

Usage:
    python3 scripts/farm_9router.py tokenmix -n 3                 # farm via its venv
    python3 scripts/farm_9router.py zerotwo  -n 3                 # zt-harvester via its venv
    python3 scripts/farm_9router.py tokenmix -n 3 --dry-run       # print child argv only
    python3 scripts/farm_9router.py zerotwo  -n 1 --proxy none    # force direct egress

Honest by design: if a farm repo/venv is missing it prints the exact install
commands and exits non-zero — it never fakes a run. Child exit codes propagate.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
SCRIPTS = AUTO_FREECF / "scripts"

TOKENMIX_REPO = HOME / "tokenmix-bulk-creator"
ZT_REPO = HOME / "zt-harvester"

FARM_TOKENMIX = "tokenmix"
FARM_ZEROTWO = "zerotwo"
FARMS = (FARM_TOKENMIX, FARM_ZEROTWO)

DEFAULT_ROUTER_URL = "http://localhost:20128/v1"
DEFAULT_SHIM_PORT = 8787

# Signup URLs probed by egress auto-resolution.
TARGET_URL = {
    FARM_TOKENMIX: "https://tokenmix.ai",
    FARM_ZEROTWO: "https://app.zerotwo.ai",
}

INSTALL_HINT = {
    FARM_TOKENMIX: (
        f"cd {TOKENMIX_REPO} && python3 -m venv .venv && "
        ".venv/bin/pip install -e . && "
        ".venv/bin/python -m playwright install chromium"
    ),
    FARM_ZEROTWO: (
        f"cd {ZT_REPO} && python3.11 -m venv .venv && "
        ".venv/bin/pip install -e '.[shim]'"
    ),
}

# ─────────────────────────────────────────────────────────── pure helpers

def router_base(router_url: str) -> str:
    """9Router management API root from the OpenAI-style URL.

    zt-harvester's own client appends '/api/...' to --router-url, so the
    OpenAI '/v1' suffix (used by consumers of the endpoint) must be dropped
    when handing the URL to it. 'http://localhost:20128/v1' -> '...:20128'.
    """
    u = (router_url or DEFAULT_ROUTER_URL).rstrip("/")
    if u.endswith("/v1"):
        u = u[:-3]
    return u

def normalize_proxy_for(farm: str, proxy: str | None) -> str | None:
    """Adapt one resolved proxy URL to each child's expected --proxy format.

    tokenmix (Playwright): full URL, e.g. http://user:pass@host:port — as-is.
    zerotwo (zt-harvester): Proxy.parse only understands 'host:port' or
    'host:port:user:pass'; a URL with credentials is silently dropped, so
    convert it here.
    """
    if not proxy:
        return None
    if farm != FARM_ZEROTWO:
        return proxy
    u = urlparse(proxy if "://" in proxy else f"http://{proxy}")
    host, port = u.hostname or "", u.port or 0
    if not host or not port:
        return proxy  # unparsable — hand it over untouched
    if u.username:
        return f"{host}:{port}:{u.username}:{u.password or ''}"
    return f"{host}:{port}"

def build_child_cmd(
    farm: str,
    proxy: str | None,
    router_url: str = DEFAULT_ROUTER_URL,
    *,
    count: int = 1,
    shim_port: int = DEFAULT_SHIM_PORT,
    python: str | None = None,
) -> list[str]:
    """Pure argv builder for the child farm process (unit-testable)."""
    if farm == FARM_ZEROTWO:
        py = python or str(ZT_REPO / ".venv" / "bin" / "python")
        cmd = [
            py, "-m", "ztharvester.cli", "run",
            "-n", str(count),
            "--router-url", router_base(router_url),
            "--shim-base-url", f"http://localhost:{shim_port}/v1",
            "--concurrency", "1",
        ]
        p = normalize_proxy_for(farm, proxy)
        if p:
            cmd += ["--proxy", p]
        return cmd
    if farm == FARM_TOKENMIX:
        py = python or str(TOKENMIX_REPO / ".venv" / "bin" / "python")
        cmd = [
            py, "-m", "tokenmix_bulk",
            "-n", str(count),
            "-c", "1",
            "--delay-min", "20", "--delay-max", "45",
        ]
        p = normalize_proxy_for(farm, proxy)
        if p:
            cmd += ["--proxy", p]
        return cmd
    raise ValueError(f"unknown farm: {farm!r} (expected one of {FARMS})")

def check_farm_ready(farm: str, *, python: str | None = None,
                     repo: Path | None = None) -> list[str]:
    """Return a list of human-readable problems; empty list == ready to run."""
    repo = repo or (TOKENMIX_REPO if farm == FARM_TOKENMIX else ZT_REPO)
    py = python or str(repo / ".venv" / "bin" / "python")
    problems: list[str] = []
    if not repo.is_dir():
        problems.append(f"farm repo missing: {repo}")
    if not Path(py).exists():
        problems.append(f"venv python missing: {py}")
    return problems

def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

# ─────────────────────────────────────────────────────────── egress

def resolve_egress(mode: str, target_url: str) -> tuple[str | None, object | None, str]:
    """Resolve --proxy mode -> (proxy_url|None, proc|None, source).

    auto: prefer scripts/egress.py's smart ladder (auto_egress) when this
    checkout provides it; otherwise fall back to ensure_clean_egress with the
    standard pool. Never raises; worst case is direct.
    """
    raw = (mode or "auto").strip()
    low = raw.lower()
    if low in ("none", "direct", "off"):
        return None, None, "none"
    if low != "auto":
        url = raw if "://" in raw else f"http://{raw}"
        return url, None, "explicit"

    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    try:
        import egress as egress_mod
    except Exception as exc:  # noqa: BLE001
        print(f"  [proxy] egress helper unavailable ({exc}); using direct", flush=True)
        return None, None, "auto:direct"

    auto = getattr(egress_mod, "auto_egress", None)
    if callable(auto):
        try:
            proxy, proc, source = auto(target_url=target_url, mode="auto", verbose=True)
            return proxy, proc, f"auto:{source}"
        except Exception as exc:  # noqa: BLE001
            print(f"  [proxy] auto_egress failed ({exc}); falling back to pool gateway", flush=True)

    try:
        pool = AUTO_FREECF / "signup_from_scratch" / "proxies.txt"
        gw, proc = egress_mod.ensure_clean_egress(
            prefer_pool=str(pool) if pool.exists() else None, verbose=True)
        if gw:
            return gw, proc, "auto:pool_gateway"
    except Exception as exc:  # noqa: BLE001
        print(f"  [proxy] pool gateway failed ({exc})", flush=True)
    return None, None, "auto:direct"

# ─────────────────────────────────────────────────────────── run

def run_farm(farm: str, args: argparse.Namespace) -> int:
    repo = TOKENMIX_REPO if farm == FARM_TOKENMIX else ZT_REPO

    problems = check_farm_ready(farm)
    if problems:
        print(f"[!] {farm} is not ready to run:", file=sys.stderr)
        for p in problems:
            print(f"    - {p}", file=sys.stderr)
        print(f"    install with: {INSTALL_HINT[farm]}", file=sys.stderr)
        return 3

    if farm == FARM_ZEROTWO and not args.dry_run:
        if not port_open("127.0.0.1", args.shim_port):
            print(f"  [!] no shim on :{args.shim_port}; 9Router connections will fail.",
                  file=sys.stderr)
            print(f"      start it: cd {ZT_REPO} && .venv/bin/zt-harvester shim "
                  f"--port {args.shim_port}", file=sys.stderr)

    proxy, proc, source = resolve_egress(args.proxy, TARGET_URL[farm])
    if proxy:
        print(f"  [proxy] {source} -> {proxy}", flush=True)
    else:
        print(f"  [proxy] {source} (no proxy)", flush=True)

    cmd = build_child_cmd(
        farm, proxy, args.router_url,
        count=args.count, shim_port=args.shim_port,
    )
    print(f"  [farm] {' '.join(cmd)}", flush=True)
    print(f"  [farm] cwd={repo}", flush=True)

    if args.dry_run:
        print("  [farm] dry-run: child NOT executed.", flush=True)
        if proc is not None:
            if str(SCRIPTS) not in sys.path:
                sys.path.insert(0, str(SCRIPTS))
            try:
                from proxy_lib import stop_gateway
                stop_gateway(proc)
                print("  [proxy] gateway stopped (dry-run).", flush=True)
            except Exception:  # noqa: BLE001
                pass
        return 0

    try:
        rc = subprocess.run(cmd, cwd=str(repo)).returncode
    except KeyboardInterrupt:
        rc = 130
    finally:
        if proc is not None:
            if str(SCRIPTS) not in sys.path:
                sys.path.insert(0, str(SCRIPTS))
            try:
                from proxy_lib import stop_gateway
                stop_gateway(proc)
                print("  [proxy] gateway stopped.", flush=True)
            except Exception:  # noqa: BLE001
                pass

    if rc == 0:
        if farm == FARM_TOKENMIX:
            print("  [next] register harvested api_keys into 9Router "
                  f"({router_base(args.router_url)}) — see docs/FARM_9ROUTER_WIRING.md §5.",
                  flush=True)
        else:
            print(f"  [next] sessions ledger: {ZT_REPO / 'harvest' / 'sessions.jsonl'}; "
                  "check the '9router:' lines for routing status.", flush=True)
    return rc

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="farm_9router",
        description="Run the tokenmix / zerotwo farms wired to kancahub proxy "
                    "egress and 9Router (http://localhost:20128).")
    sub = ap.add_subparsers(dest="farm", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("-n", "--count", type=int, default=1,
                        help="number of accounts (default: 1)")
        sp.add_argument("--proxy", default="auto", metavar="auto|none|URL",
                        help="egress: auto (smart resolve), none (direct), or a "
                             "proxy URL (default: auto)")
        sp.add_argument("--router-url", default=DEFAULT_ROUTER_URL,
                        help=f"9Router URL (default: {DEFAULT_ROUTER_URL})")
        sp.add_argument("--shim-port", type=int, default=DEFAULT_SHIM_PORT,
                        help=f"zt-harvester shim port (default: {DEFAULT_SHIM_PORT})")
        sp.add_argument("--dry-run", action="store_true",
                        help="print the child argv + readiness, run nothing")

    common(sub.add_parser(FARM_TOKENMIX, help="tokenmix-bulk-creator farm"))
    common(sub.add_parser(FARM_ZEROTWO, help="zt-harvester (ZeroTwo) farm"))
    return ap

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_farm(args.farm, args)

if __name__ == "__main__":
    sys.exit(main())
