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

import json
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


def _try_freepool(target_url: str, verbose: bool, *, limit: int = 8,
                  timeout: float = 12.0) -> str | None:
    """Pick the best browser-grade proxy from the harvested free pool.

    scripts/freepool.py scores proxies at two levels: reachable (an IP echo
    answers) and browser-grade (the real target page loads, non-trivially). Only
    level-2 survivors are trusted here, because a reachable-but-tiny response is
    what makes a signup look "failed" for reasons that are really the proxy.

    Free lists are ~1% alive and rarely browser-grade, so this takes the first
    few candidates only -- a slow search costs more than it is worth.
    """
    from pathlib import Path as _P

    pool = _P.home() / ".config" / "auto-freecf" / "freepool_browser.txt"
    if not pool.exists() or pool.stat().st_size == 0:
        if verbose:
            print("  [egress] • No free pool scored yet "
                  "(run: scripts/freepool.py fetch && scripts/freepool.py filter)",
                  file=sys.stderr)
        return None
    try:
        cands = [ln.strip() for ln in pool.read_text().splitlines() if ln.strip()][:max(1, limit)]
    except Exception:  # noqa: BLE001
        return None
    for cand in cands:
        st = probe_status(target_url, proxy=cand, timeout=timeout)
        if 200 <= st < 400 and not is_blocked(st):
            if verbose:
                print(f"  [egress] ✓ Free-pool proxy {cand} verified for {target_url} (HTTP {st})",
                      file=sys.stderr)
            return cand
        if verbose:
            print(f"  [egress] • Free-pool {cand} returned HTTP {st} for {target_url}", file=sys.stderr)
    if verbose:
        print("  [egress] ✗ No free-pool proxy passed for this target", file=sys.stderr)
    return None


def _try_proxyma(target_url: str, verbose: bool, *, timeout: float = 12.0) -> str | None:
    """Anonymous, keyless HTTPS proxies from proxyma.space (datacenter exits).

    ponytail: hosting-flagged IPs clear Cloudflare gates but NOT Google reCAPTCHA
    audio. Upgrade path: none free; needs a residential/mobile exit.
    """
    import json as _json
    import urllib.request as _ur

    try:
        def _get(path, secret=None):
            req = _ur.Request("https://proxyma.space" + path,
                              headers={"X-Proxy-Secret": secret or "", "User-Agent": "Mozilla/5.0"})
            return _json.load(_ur.urlopen(req, timeout=timeout))
        secret = _get("/api/secret")["secret"]
        cred = _get("/api/proxy-credentials", secret)["data"]
        nodes = [n for n in _get("/api/proxies?nodes=1", secret)["data"] if n.get("status") == "online"]
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"  [egress] • Proxyma unavailable ({exc})", file=sys.stderr)
        return None
    for n in sorted(nodes, key=lambda x: x.get("latency_ms", 9999))[:4]:
        cand = f"https://{cred['username']}:{cred['password']}@{n['host']}:{n['port']}"
        st = probe_status(target_url, proxy=cand, timeout=timeout)
        if 200 <= st < 400 and not is_blocked(st):
            if verbose:
                print(f"  [egress] ✓ Proxyma {n['region']} verified for {target_url} (HTTP {st})", file=sys.stderr)
            return cand
    return None


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
    allow_mobile: bool = True,
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
            - MOBILE phone egress: rotate the tethered phone's carrier IP and use the
              laptop's (now mobile) connection (allow_mobile=True, default)
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

    # 0. Registered DEVICE egress nodes (another device's own connection — e.g. a
    #    phone on mobile data, or a host on a different network). These are the
    #    highest-value free rung because they are real devices, not public proxies.
    if allow_mobile:
        for name, node_url in _load_egress_nodes().items():
            st = probe_status(target_url, proxy=node_url, timeout=8.0)
            if 200 <= st < 400 and not is_blocked(st):
                if verbose:
                    eg = check_gateway_egress(node_url, retries=1, timeout=4.0)
                    print(f"  [egress] ✓ Device node '{name}' verified for {target_url} (HTTP {st}, exit {eg or '?'})", file=sys.stderr)
                return node_url, None, f"node:{name}"
            elif verbose:
                print(f"  [egress] • Device node '{name}' returned HTTP {st} for {target_url}", file=sys.stderr)

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

    # 2b. Harvested FREE pool (scripts/freepool.py). Free lists are ~1% alive and
    #     rarely browser-grade, so this is a low rung: try a handful of the best
    #     candidates, verified against THIS target, and give up quickly. It exists
    #     because a new user has no residential key and free is all they have.
    #     Skipped when a caller pins prefer_pool: an explicit pool is a promise.
    if not prefer_pool:
        fp = _try_freepool(target_url, verbose)
        if fp:
            return fp, None, "freepool"
        px = _try_proxyma(target_url, verbose)
        if px:
            return px, None, "proxyma"

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

    # 3c. Mobile escalation: rotate the tethered phone's carrier IP, then re-probe
    # the target over the laptop's (now mobile) connection. A real mobile IP is
    # often accepted where datacenter/WARP are blocked (proved with TokenHarbor),
    # so this is worth a try before giving up to a plain direct connection.
    if allow_mobile and not is_blocked(probe_status(target_url, proxy=None, timeout=8.0)):
        if verbose:
            print("  [egress] ✓ Direct/mobile connection already passes; using it", file=sys.stderr)
        return None, None, "mobile" if _phone_connected() else "direct"
    if allow_mobile and _phone_connected():
        if verbose:
            print("  [egress] • Escalating to MOBILE phone egress (rotating carrier IP)…", file=sys.stderr)
        new_ip = _rotate_phone_ip(verbose=verbose)
        if new_ip:
            st = probe_status(target_url, proxy=None, timeout=12.0)
            if not is_blocked(st):
                if verbose:
                    print(f"  [egress] ✓ Mobile egress verified: exit {new_ip} (HTTP {st})", file=sys.stderr)
                return None, None, "mobile"
            if verbose:
                print(f"  [egress] • Mobile egress {new_ip} still blocked (HTTP {st})", file=sys.stderr)

    # 4. Fallback to direct
    if verbose:
        print("  [egress] ✗ All proxy candidates failed or blocked; falling back to direct", file=sys.stderr)
    return None, None, "direct"


def _load_egress_nodes() -> dict[str, str]:
    """Registered device egress nodes (name -> url) from egress_nodes.json."""
    try:
        p = Path(os.environ.get("EGRESS_NODES", Path.home() / ".config" / "auto-freecf" / "egress_nodes.json"))
        if not p.exists():
            return {}
        data = json.loads(p.read_text())
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _phone_connected() -> bool:
    """True if an Android phone is connected via adb (mobile-egress capable)."""
    try:
        import subprocess
        r = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=8)
        for line in r.stdout.splitlines()[1:]:
            if line.strip().endswith("device"):
                return True
    except Exception:
        pass
    return False


def _rotate_phone_ip(verbose: bool = True) -> str | None:
    """Rotate the tethered phone's carrier IP by shelling scripts/mobile_rotate.py."""
    try:
        import subprocess
        here = Path(__file__).resolve().parent
        tool = here / "mobile_rotate.py"
        if not tool.exists():
            return None
        r = subprocess.run(
            [sys.executable, str(tool), "--rotate", "--wait", "25"],
            capture_output=True, text=True, timeout=90,
        )
        out = (r.stdout or "") + (r.stderr or "")
        for line in out.splitlines():
            if "IP after" in line:
                return line.split(":", 1)[1].strip()
        return None
    except Exception:
        return None


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Ensure clean non-blocked egress gateway with smart fallback")
    ap.add_argument("--target", default="https://github.com/signup", help="target URL to probe (default: https://github.com/signup)")
    ap.add_argument("--mode", default="auto", help="egress mode: auto | none | <proxy_url> (default: auto)")
    ap.add_argument("--prefer-pool", default=None, help="preferred pool file (legacy option)")
    ap.add_argument("--target-ip", default=None, help="blocked/forbidden IP to avoid (legacy option)")
    ap.add_argument("--country", default=None, help="allowed egress country code(s) (e.g. US, SG)")
    ap.add_argument("--exclude-country", default=None, help="excluded egress country code(s) (e.g. ID)")
    ap.add_argument("--no-mobile", action="store_true", help="do not escalate to the tethered phone's mobile IP")
    args = ap.parse_args()

    gw, proc, source = auto_egress(
        target_url=args.target,
        mode=args.mode,
        verbose=True,
        prefer_pool=args.prefer_pool,
        country=args.country,
        exclude_countries=args.exclude_country,
        allow_mobile=not args.no_mobile,
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
