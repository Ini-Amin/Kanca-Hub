#!/usr/bin/env python3
"""
Native proxy toolkit for KancaHub — improvised from PetaniProxy's ideas, but
self-contained (no shelling out to petani's TUI for the core loop).

What this gives the CLI:
  * harvest()        — pull public proxies from a curated list of feeds
  * validate()       — concurrent aiohttp/threaded liveness + latency + country
  * health()         — one-shot check of a pool file
  * rotate_gateway() — a rotating local gateway with sticky sessions
                       (delegates to scripts/proxy_gateway.py, which itself uses
                        petani's LocalProxyBridge when present)

Everything returns plain data so kancahub can print tables / export files.
"""

from __future__ import annotations

import concurrent.futures
import json
import random
import re
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Iterable

# ── public proxy feeds (same idea as petani's sources.json, trimmed to live ones)
HTTP_FEEDS = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all",
]
SOCKS5_FEEDS = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt",
]
SOCKS4_FEEDS = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
]

_IPPORT = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})\s*$")

DEFAULT_TEST_URL = "https://api.ipify.org"

# Cache per IP: ip -> (country_code, country_name, resolved)
_IP_COUNTRY_CACHE: dict[str, tuple[str, str, bool]] = {}
_IP_CACHE_LOCK = threading.Lock()


def lookup_ip_country(ip: str, timeout: float = 2.5) -> tuple[str, str, bool]:
    """Look up countryCode and country name for an egress IP via ip-api.com.

    Returns (country_code, country_name, resolved: bool).
    Thread-safe and cached per IP. Degrades gracefully on timeout/network failure.
    """
    if not ip or not isinstance(ip, str):
        return "", "", False

    clean_ip = ip.strip()
    with _IP_CACHE_LOCK:
        if clean_ip in _IP_COUNTRY_CACHE:
            return _IP_COUNTRY_CACHE[clean_ip]

    cc, name, resolved = "", "", False
    # Rate limit guard: ip-api free tier is 45 req/min. Short timeout, degrade gracefully.
    try:
        url = f"https://ip-api.com/json/{clean_ip}?fields=countryCode,country,proxy,hosting"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.88.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
            if isinstance(data, dict):
                cc = str(data.get("countryCode") or "").strip().upper()
                name = str(data.get("country") or "").strip()
                resolved = bool(cc)
    except Exception:
        # Fall back to http if https fails
        try:
            url_http = f"http://ip-api.com/json/{clean_ip}?fields=countryCode,country,proxy,hosting"
            req_http = urllib.request.Request(url_http, headers={"User-Agent": "curl/7.88.1"})
            with urllib.request.urlopen(req_http, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
                if isinstance(data, dict):
                    cc = str(data.get("countryCode") or "").strip().upper()
                    name = str(data.get("country") or "").strip()
                    resolved = bool(cc)
        except Exception:
            resolved = False

    res = (cc, name, resolved)
    with _IP_CACHE_LOCK:
        _IP_COUNTRY_CACHE[clean_ip] = res
    return res


def country_allowed(
    cc: str | None,
    allow: set[str] | Iterable[str] | None = None,
    exclude: set[str] | Iterable[str] | None = None,
    resolved: bool = True,
) -> bool:
    """Determine if country code `cc` is permitted under allow/exclude rules.

    Semantics:
      - If resolved is False: keep (True) UNLESS allow is set and non-empty.
      - If resolved is True:
        * reject if cc is in exclude
        * if allow is set, accept only if cc is in allow
        * otherwise accept (True)
    """
    allow_set = {str(c).strip().upper() for c in allow if str(c).strip()} if allow else set()
    exclude_set = {str(c).strip().upper() for c in exclude if str(c).strip()} if exclude else set()

    if not resolved:
        return not bool(allow_set)

    code = (cc or "").strip().upper()
    if code and code in exclude_set:
        return False
    if allow_set:
        return bool(code and code in allow_set)
    return True


# ───────────────────────────────────────────── harvest

def _fetch(url: str, timeout: int = 12) -> list[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return []


def harvest(protocols: Iterable[str] = ("http",), verbose: bool = True) -> list[dict]:
    """Fetch candidate proxies from public feeds. Returns [{proxy, protocol}]."""
    feeds: list[str] = []
    for p in protocols:
        if p == "http":
            feeds += HTTP_FEEDS
        elif p == "socks5":
            feeds += SOCKS5_FEEDS
        elif p == "socks4":
            feeds += SOCKS4_FEEDS

    seen: set[str] = set()
    out: list[dict] = []
    for url in feeds:
        lines = _fetch(url)
        host = re.sub(r"^https?://", "", url).split("/")[0]
        proto = "socks5" if "socks5" in url else "socks4" if "socks4" in url else "http"
        added = 0
        for ln in lines:
            m = _IPPORT.match(ln)
            if not m:
                continue
            key = f"{m.group(1)}:{m.group(2)}"
            if key in seen:
                continue
            seen.add(key)
            out.append({"proxy": key, "protocol": proto, "source": host})
            added += 1
        if verbose:
            print(f"  + {added:5d} from {host}", file=sys.stderr)
    return out


# ───────────────────────────────────────────── validate

def _check_one(cand: dict, test_url: str, timeout: float) -> dict | None:
    proxy = cand["proxy"]
    proto = cand.get("protocol", "http")
    scheme = "socks5h" if proto == "socks5" else "socks4a" if proto == "socks4" else "http"
    full = f"{scheme}://{proxy}"
    start = time.time()
    # Prefer curl (handles socks + auth cleanly). Fall back to urllib for http.
    try:
        import subprocess
        r = subprocess.run(
            ["curl", "-s", "-m", str(int(timeout)), "-x", full, test_url],
            capture_output=True, text=True,
        )
        body = (r.stdout or "").strip()
        if body and re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", body):
            cc, country_name, resolved = lookup_ip_country(body)
            return {
                "proxy": proxy, "protocol": proto, "scheme": scheme,
                "latency_ms": int((time.time() - start) * 1000),
                "egress": body, "anonymity": "elite" if body != "" else "unknown",
                "country_code": cc, "country": country_name,
                "country_resolved": resolved,
            }
    except Exception:
        pass
    return None


def validate(candidates: list[dict], target: int = 20, timeout: float = 4.0,
             workers: int = 100, test_url: str = DEFAULT_TEST_URL,
             on_live=None,
             countries: set[str] | Iterable[str] | None = None,
             exclude_countries: set[str] | Iterable[str] | None = None) -> list[dict]:
    """Concurrently test candidates until `target` live ones are found."""
    live: list[dict] = []
    checked = 0
    limit = max(target * 50, len(candidates)) if (countries or exclude_countries) else max(target * 25, target)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_check_one, c, test_url, timeout): c for c in candidates[:limit]}
        for fut in concurrent.futures.as_completed(futs):
            checked += 1
            res = fut.result()
            if res:
                cc = res.get("country_code", "")
                resolved = res.get("country_resolved", bool(cc))
                if not country_allowed(cc, allow=countries, exclude=exclude_countries, resolved=resolved):
                    continue
                live.append(res)
                if on_live:
                    on_live(res)
                if len(live) >= target:
                    break
    return live


def health(pool_file: str, timeout: float = 6.0, workers: int = 50,
           test_url: str = DEFAULT_TEST_URL,
           countries: set[str] | Iterable[str] | None = None,
           exclude_countries: set[str] | Iterable[str] | None = None) -> tuple[list[str], list[str]]:
    """Check a pool file. Returns (alive_urls, dead_urls)."""
    lines = [l.strip() for l in Path(pool_file).read_text().splitlines() if l.strip() and not l.startswith("#")]
    cands = []
    for l in lines:
        if "://" in l:
            scheme = l.split("://", 1)[0]
            proto = "socks5" if "socks5" in scheme else "socks4" if "socks4" in scheme else "http"
            hostpart = l.split("://", 1)[1]
            cands.append({"proxy": hostpart, "protocol": proto, "raw": l})
        else:
            cands.append({"proxy": l, "protocol": "http", "raw": l})

    alive, dead = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_check_one, c, test_url, timeout): c for c in cands}
        for fut in concurrent.futures.as_completed(futs):
            c = futs[fut]
            res = fut.result()
            if res:
                cc = res.get("country_code", "")
                resolved = res.get("country_resolved", bool(cc))
                if not country_allowed(cc, allow=countries, exclude=exclude_countries, resolved=resolved):
                    dead.append(c.get("raw") or f"http://{c['proxy']}")
                    continue
                alive.append(c.get("raw") or f"http://{c['proxy']}")
            else:
                dead.append(c.get("raw") or f"http://{c['proxy']}")
    return alive, dead


# ───────────────────────────────────────────── export / gateway

def export(proxies: list[dict], out_txt: str | None = None, out_json: str | None = None) -> dict:
    written = {}
    if out_txt:
        lines = [f"{p['scheme']}://{p['proxy']}" for p in proxies]
        Path(out_txt).write_text("\n".join(lines) + "\n")
        written["txt"] = out_txt
    if out_json:
        Path(out_json).write_text(json.dumps(proxies, indent=2) + "\n")
        written["json"] = out_json
    return written


def rotate_gateway(pool_file: str, port: int = 8899, scheme: str = "auto",
                   host: str = "127.0.0.1", ttl: float = 600.0) -> int:
    """Start the rotating gateway (uses scripts/proxy_gateway.py)."""
    import subprocess
    here = Path(__file__).resolve().parent
    py = sys.executable
    return subprocess.call([
        py, str(here / "proxy_gateway.py"),
        "--pool", pool_file, "--port", str(port), "--host", host,
        "--scheme", scheme, "--ttl", str(ttl),
    ])


def find_free_port(host: str = "127.0.0.1") -> int:
    """Find a random unallocated local port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def get_my_ip(timeout: float = 5.0) -> str | None:
    """Fetch external public IP directly."""
    for url in ("https://api.ipify.org", "https://icanhazip.com", "https://checkip.amazonaws.com"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.88.1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", "ignore").strip()
                if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", text):
                    return text
        except Exception:
            continue
    return None


def check_gateway_egress(gateway_url: str, retries: int = 4, timeout: float = 6.0) -> str | None:
    """Fetch external IP routed through gateway_url, trying across pool rotation."""
    import subprocess
    for attempt in range(retries):
        try:
            r = subprocess.run(
                ["curl", "-s", "-m", str(int(timeout)), "-x", gateway_url, "https://api.ipify.org"],
                capture_output=True, text=True,
            )
            body = (r.stdout or "").strip()
            if body and re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", body):
                return body
        except Exception:
            pass
        time.sleep(0.5)
    return None


def stop_gateway(proc: subprocess.Popen | None) -> None:
    """Safely terminate a background gateway process."""
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=1)
            except Exception:
                pass


def ensure_clean_egress(
    prefer_pool: str | Path | None = None,
    target_ip: str | None = None,
    verbose: bool = True,
    countries: set[str] | Iterable[str] | None = None,
    exclude_countries: set[str] | Iterable[str] | None = None,
) -> tuple[str | None, subprocess.Popen | None]:
    """
    Ensure a local rotating gateway running on verified clean proxies whose exit
    IP differs from the host/blocked IP.

    1. Checks prefer_pool if provided and has alive proxies matching country rules.
    2. Else harvests and validates fresh public proxies (SOCKS5, HTTP).
    3. Spawns scripts/proxy_gateway.py in the background on a free port.
    4. Verifies gateway egress through https://api.ipify.org and country constraints.
    5. Returns (gateway_url, proc) or (None, None).
    """
    import subprocess
    import tempfile

    real_ip = get_my_ip()
    forbidden_ips = set()
    if real_ip:
        forbidden_ips.add(real_ip)
    if target_ip:
        forbidden_ips.add(target_ip)

    if verbose:
        print(f"  [egress] Real / blocked IP: {real_ip or target_ip or 'unknown'}", file=sys.stderr)

    temp_pool_path: str | None = None

    # 1. Check prefer_pool if given
    if prefer_pool and Path(prefer_pool).exists():
        if verbose:
            print(f"  [egress] Checking health of preferred pool {prefer_pool}…", file=sys.stderr)
        alive, _ = health(str(prefer_pool), timeout=3.0, workers=20,
                          countries=countries, exclude_countries=exclude_countries)
        if alive:
            tfile = tempfile.NamedTemporaryFile("w+", delete=False, prefix="clean_egress_pool_", suffix=".txt")
            tfile.write("\n".join(alive) + "\n")
            tfile.close()
            temp_pool_path = tfile.name
            if verbose:
                print(f"  [egress] ✓ Using {len(alive)} verified proxies from preferred pool", file=sys.stderr)

    # 2. Harvest + validate if needed
    if not temp_pool_path:
        if verbose:
            print("  [egress] Preferred pool has no live proxies; harvesting from public feeds…", file=sys.stderr)
        # Prioritize SOCKS5 for reliable HTTPS TCP tunneling
        cands = harvest(protocols=("socks5", "http"), verbose=False)
        live = validate(cands, target=5, timeout=3.5, workers=80,
                        countries=countries, exclude_countries=exclude_countries)
        if len(live) < 2:
            cands = harvest(protocols=("socks5", "http", "socks4"), verbose=False)
            live = validate(cands, target=6, timeout=4.0, workers=100,
                            countries=countries, exclude_countries=exclude_countries)
        # Avoid proxies that exit on Cloudflare WARP (104.28.*) since GitHub blocks them
        non_warp_live = [p for p in live if not str(p.get("egress", "")).startswith("104.28.")]
        if non_warp_live:
            live = non_warp_live
        if not live:
            if verbose:
                print("  [egress] ✗ All candidate proxies failed validation; no live proxy found.", file=sys.stderr)
            return None, None

        tfile = tempfile.NamedTemporaryFile("w+", delete=False, prefix="clean_egress_pool_", suffix=".txt")
        tfile.write("\n".join(f"{p['scheme']}://{p['proxy']}" for p in live) + "\n")
        tfile.close()
        temp_pool_path = tfile.name
        if verbose:
            print(f"  [egress] ✓ Harvested {len(live)} live proxies -> {temp_pool_path}", file=sys.stderr)

    # 3. Find free port & start gateway
    port = find_free_port()
    here = Path(__file__).resolve().parent
    gateway_script = here / "proxy_gateway.py"
    py = sys.executable

    proc = subprocess.Popen(
        [py, "-u", str(gateway_script), "--pool", temp_pool_path, "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 4. Wait for gateway ready
    deadline = time.time() + 8.0
    ready = False
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                ready = True
                break
        except (OSError, ConnectionRefusedError):
            time.sleep(0.2)

    if not ready:
        stop_gateway(proc)
        if verbose:
            print(f"  [egress] ✗ Gateway on port {port} failed to start.", file=sys.stderr)
        return None, None

    # 5. Verify egress differs from real/blocked IP and matches country rules
    gateway_url = f"http://127.0.0.1:{port}"
    egress_ip = check_gateway_egress(gateway_url, retries=5, timeout=7.0)
    if not egress_ip:
        stop_gateway(proc)
        if verbose:
            print("  [egress] ✗ Gateway could not connect to external network.", file=sys.stderr)
        return None, None

    if egress_ip in forbidden_ips:
        stop_gateway(proc)
        if verbose:
            print(f"  [egress] ✗ Gateway exit IP {egress_ip} matches blocked IP.", file=sys.stderr)
        return None, None

    if countries or exclude_countries:
        gw_cc, gw_name, gw_res = lookup_ip_country(egress_ip)
        if not country_allowed(gw_cc, allow=countries, exclude=exclude_countries, resolved=gw_res):
            stop_gateway(proc)
            if verbose:
                print(f"  [egress] ✗ Gateway exit IP {egress_ip} ({gw_cc or 'unknown'}) rejected by country filter.", file=sys.stderr)
            return None, None
        elif verbose and gw_cc:
            print(f"  [egress] ✓ Country verified: {gw_cc} ({gw_name})", file=sys.stderr)

    if verbose:
        print(f"  [egress] ✓ Clean egress gateway ready: {gateway_url} (exit IP: {egress_ip})", file=sys.stderr)

    return gateway_url, proc


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="KancaHub native proxy toolkit")
    sub = ap.add_subparsers(dest="cmd")

    h = sub.add_parser("harvest", help="harvest + validate proxies")
    h.add_argument("--target", type=int, default=20)
    h.add_argument("--protocol", default="http", choices=["http", "socks4", "socks5"])
    h.add_argument("--timeout", type=float, default=4.0)
    h.add_argument("--workers", type=int, default=100)
    h.add_argument("--out-txt")
    h.add_argument("--out-json")

    v = sub.add_parser("health", help="check a pool file")
    v.add_argument("pool")

    g = sub.add_parser("gateway", help="start rotating gateway")
    g.add_argument("--pool", required=True)
    g.add_argument("--port", type=int, default=8899)
    g.add_argument("--scheme", default="auto")

    a = ap.parse_args()
    if a.cmd == "harvest":
        cands = harvest(protocols=[a.protocol])
        print(f"  candidates: {len(cands)}; validating…", file=sys.stderr)
        live = validate(cands, target=a.target, timeout=a.timeout, workers=a.workers,
                        on_live=lambda r: print(f"  live {r['egress']:15s} {r['latency_ms']}ms", file=sys.stderr))
        print(f"✓ {len(live)} live")
        if a.out_txt or a.out_json:
            print(export(live, a.out_txt, a.out_json))
        return 0
    if a.cmd == "health":
        alive, dead = health(a.pool)
        print(f"  {len(alive)} alive / {len(dead)} dead")
        return 0
    if a.cmd == "gateway":
        return rotate_gateway(a.pool, port=a.port, scheme=a.scheme)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
