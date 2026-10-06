#!/usr/bin/env python3
"""
Egress routing helper for Auto-FreeCF / KancaHub.

Provides smart proxy auto-wire with gentle probing and graceful fallback
(scripts/egress.py backed by scripts/proxy_lib.py) so any consumer feature gets
a working egress IP.

User strategy order:
  1. If a local gateway is already listening on 127.0.0.1:8888 or :8899,
     verify against target_url; if 200/ok, use it.
  2. Else start clean pool gateway (signup_from_scratch/proxies.txt) and verify.
  3. If blocked (403/429/503), escalate to:
     - Cloudflare WARP tunnel (if active / up)
     - PetaniProxy residential pool (~/petani-proxy/output/webshare_residential.txt)
  4. Graceful fallback to direct egress: (None, None, "direct").

Supports mode='auto' | 'none' | explicit proxy URL (or env KANCAHUB_EGRESS).
Importable without side effects (no background gateway spawned on import).
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

# Add scripts directory to path
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from typing import Iterable

from proxy_lib import (
    check_gateway_egress,
    country_allowed,
    ensure_clean_egress,
    find_free_port,
    get_my_ip,
    lookup_ip_country,
    stop_gateway,
)

__all__ = [
    "auto_egress",
    "is_blocked",
    "probe_status",
    "is_port_open",
    "ensure_clean_egress",
    "check_gateway_egress",
    "get_my_ip",
    "stop_gateway",
    "find_free_port",
    "country_allowed",
    "lookup_ip_country",
]


def _normalize_countries(val: str | Iterable[str] | None) -> set[str] | None:
    if val is None:
        return None
    if isinstance(val, str):
        parts = re.split(r"[\s,]+", val.strip())
        res = {p.upper() for p in parts if p}
        return res if res else None
    res = {str(p).strip().upper() for p in val if p}
    return res if res else None


def is_blocked(status: int) -> bool:
    """Return True if HTTP status code indicates blocked / challenge / rate-limited."""
    return status in (403, 429, 503)


def probe_status(target_url: str, proxy: str | None = None, timeout: float = 10.0) -> int:
    """
    Probe target_url through candidate proxy using a gentle one-shot curl request.
    Returns the HTTP status code (int), or 0 on error / timeout / unreachable.
    """
    cmd = [
        "curl",
        "-s",
        "-o", "/dev/null",
        "-w", "%{http_code}",
        "--max-time", str(int(timeout)),
        "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]
    if proxy:
        cmd.extend(["-x", proxy])
    cmd.append(target_url)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2.0)
        out = (res.stdout or "").strip()
        if out.isdigit():
            return int(out)
        return 0
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return 0


def is_port_open(host: str = "127.0.0.1", port: int = 8888, timeout: float = 0.5) -> bool:
    """Check if a TCP port is currently open and listening."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _check_warp_up() -> bool:
    """Check if Cloudflare WARP tunnel is active via scripts/warp_manager.py."""
    try:
        import warp_manager
        if hasattr(warp_manager, "status"):
            st = warp_manager.status()
            if isinstance(st, dict):
                return bool(st.get("up", False))
        if hasattr(warp_manager, "is_up"):
            return bool(warp_manager.is_up())
    except Exception:
        pass
    return False


def auto_egress(
    target_url: str = "https://github.com/signup",
    *,
    mode: str = "auto",
    verbose: bool = True,
    prefer_pool: str | None = None,
    country: str | Iterable[str] | None = None,
    exclude_countries: str | Iterable[str] | None = None,
) -> tuple[str | None, object | None, str]:
    """
    Smart proxy auto-wire with gentle verification probes and graceful fallback.

    Returns:
        (proxy_url_or_None, gateway_proc_or_None, source_string)

    Strategy order for mode='auto':
        1. Local gateway (:8888, :8899)
        2. Pool gateway (signup_from_scratch/proxies.txt or prefer_pool)
        3. Escalation on block (403/429/503):
           - Cloudflare WARP tunnel (if up)
           - PetaniProxy residential pool (~/petani-proxy/output/webshare_residential.txt)
        4. Fallback to direct: (None, None, 'direct')
    """
    norm_country = _normalize_countries(country)
    norm_exclude = _normalize_countries(exclude_countries)

    env_mode = os.environ.get("KANCAHUB_EGRESS", "").strip()
    from_env = False
    if mode == "auto" and env_mode:
        eff_mode = env_mode
        from_env = True
    else:
        eff_mode = mode

    eff_mode_clean = eff_mode.strip()
    eff_lower = eff_mode_clean.lower()

    if eff_lower in ("none", "direct"):
        if verbose:
            print("  [egress] Direct egress selected (mode=none)", file=sys.stderr)
        return None, None, "direct"

    if eff_lower == "warp":
        if verbose:
            print("  [egress] WARP egress selected explicitly", file=sys.stderr)
        return None, None, "warp"

    # Explicit proxy URL or host:port
    if "://" in eff_mode_clean or re.match(r"^\d{1,3}(?:\.\d{1,3}){3}:\d+$", eff_mode_clean):
        proxy_url = eff_mode_clean if "://" in eff_mode_clean else f"http://{eff_mode_clean}"
        source = "env" if from_env else "explicit"
        if verbose:
            print(f"  [egress] Using {source} proxy: {proxy_url}", file=sys.stderr)
        return proxy_url, None, source

    # ── AUTO STRATEGY ──

    # 1. Existing local gateway on 127.0.0.1:8888 or :8899
    for port in (8888, 8899):
        if is_port_open("127.0.0.1", port):
            cand = f"http://127.0.0.1:{port}"
            # If country filter is active, check candidate egress country
            if norm_country or norm_exclude:
                eg_ip = check_gateway_egress(cand, retries=1, timeout=3.0)
                if eg_ip:
                    cc, _, resolved = lookup_ip_country(eg_ip)
                    if not country_allowed(cc, allow=norm_country, exclude=norm_exclude, resolved=resolved):
                        if verbose:
                            print(f"  [egress] • Local gateway on :{port} exits via {eg_ip} ({cc}) which violates country filter; skipping", file=sys.stderr)
                        continue
            st = probe_status(target_url, proxy=cand, timeout=10.0)
            if 200 <= st < 400 and not is_blocked(st):
                if verbose:
                    print(f"  [egress] ✓ Active local gateway on :{port} verified for {target_url} (HTTP {st})", file=sys.stderr)
                return cand, None, f"local_gateway:{port}"
            elif verbose:
                print(f"  [egress] • Local gateway on :{port} returned HTTP {st} for {target_url}", file=sys.stderr)

    # 2. Try ensure_clean_egress with signup_from_scratch/proxies.txt (or custom prefer_pool)
    if prefer_pool:
        pool_arg = prefer_pool
    else:
        repo_root = Path(__file__).resolve().parent.parent
        default_pool = repo_root / "signup_from_scratch" / "proxies.txt"
        pool_arg = str(default_pool) if default_pool.exists() else None

    gw_url, gw_proc = ensure_clean_egress(
        prefer_pool=pool_arg,
        verbose=verbose,
        countries=norm_country,
        exclude_countries=norm_exclude,
    )
    if gw_url:
        st = probe_status(target_url, proxy=gw_url, timeout=10.0)
        if 200 <= st < 400 and not is_blocked(st):
            if verbose:
                print(f"  [egress] ✓ Pool gateway {gw_url} verified for {target_url} (HTTP {st})", file=sys.stderr)
            return gw_url, gw_proc, "pool_gateway"
        else:
            if verbose:
                print(f"  [egress] • Pool gateway returned HTTP {st} for {target_url}; stopping and escalating…", file=sys.stderr)
            if gw_proc is not None:
                stop_gateway(gw_proc)
                gw_proc = None

    # 3. Escalate to WARP / residential
    # 3a. Check if WARP is up
    if _check_warp_up():
        warp_ok = True
        if norm_country or norm_exclude:
            my_ip = get_my_ip(timeout=4.0)
            if my_ip:
                cc, _, resolved = lookup_ip_country(my_ip)
                if not country_allowed(cc, allow=norm_country, exclude=norm_exclude, resolved=resolved):
                    warp_ok = False
                    if verbose:
                        print(f"  [egress] • WARP exits via {my_ip} ({cc}) which violates country filter; skipping", file=sys.stderr)
        if warp_ok:
            st = probe_status(target_url, proxy=None, timeout=10.0)
            if 200 <= st < 400 and not is_blocked(st):
                if verbose:
                    print(f"  [egress] ✓ WARP tunnel active and verified for {target_url} (HTTP {st})", file=sys.stderr)
                return None, None, "warp"
            elif verbose:
                print(f"  [egress] • WARP is up but probe returned HTTP {st} for {target_url}", file=sys.stderr)

    # 3b. PetaniProxy residential pool
    petani_res = Path.home() / "petani-proxy" / "output" / "webshare_residential.txt"
    if not petani_res.exists() or petani_res.stat().st_size == 0:
        if verbose:
            print(f"  [egress] ✗ PetaniProxy residential pool not available at {petani_res}", file=sys.stderr)
    else:
        res_gw, res_proc = ensure_clean_egress(
            prefer_pool=str(petani_res),
            verbose=verbose,
            countries=norm_country,
            exclude_countries=norm_exclude,
        )
        if res_gw:
            st = probe_status(target_url, proxy=res_gw, timeout=10.0)
            if 200 <= st < 400 and not is_blocked(st):
                if verbose:
                    print(f"  [egress] ✓ Residential gateway verified for {target_url} (HTTP {st})", file=sys.stderr)
                return res_gw, res_proc, "residential"
            else:
                if verbose:
                    print(f"  [egress] ✗ Residential gateway returned HTTP {st} on {target_url}", file=sys.stderr)
                if res_proc is not None:
                    stop_gateway(res_proc)
                    res_proc = None

    # 4. Fallback to direct
    if verbose:
        print("  [egress] ✗ All proxy candidates failed or blocked; falling back to direct", file=sys.stderr)
    return None, None, "direct"


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Ensure clean non-blocked egress gateway with smart fallback")
    ap.add_argument("--target", default="https://github.com/signup", help="target URL to probe (default: https://github.com/signup)")
    ap.add_argument("--mode", default="auto", help="egress mode: auto | none | <proxy_url> (default: auto)")
    ap.add_argument("--prefer-pool", default=None, help="preferred pool file (legacy option)")
    ap.add_argument("--target-ip", default=None, help="blocked/forbidden IP to avoid (legacy option)")
    ap.add_argument("--country", default=None, help="allowed egress country code(s) (e.g. US, SG)")
    ap.add_argument("--exclude-country", default=None, help="excluded egress country code(s) (e.g. ID)")
    args = ap.parse_args()

    gw, proc, source = auto_egress(
        target_url=args.target,
        mode=args.mode,
        verbose=True,
        prefer_pool=args.prefer_pool,
        country=args.country,
        exclude_countries=args.exclude_country,
    )

    if gw:
        exit_ip = check_gateway_egress(gw, retries=2, timeout=5.0) or "unknown"
    else:
        exit_ip = get_my_ip() or "unknown"

    print(f"\n[egress] Chosen source : {source}")
    print(f"[egress] Proxy URL     : {gw or 'None (direct/WARP)'}")
    print(f"[egress] Exit IP       : {exit_ip}")

    if proc is not None:
        print("\nGateway active. Press Ctrl+C to terminate gateway...")
        try:
            proc.wait()
        except KeyboardInterrupt:
            print("\nStopping gateway...")
        finally:
            stop_gateway(proc)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
