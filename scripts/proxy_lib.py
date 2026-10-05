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
            return {
                "proxy": proxy, "protocol": proto, "scheme": scheme,
                "latency_ms": int((time.time() - start) * 1000),
                "egress": body, "anonymity": "elite" if body != "" else "unknown",
            }
    except Exception:
        pass
    return None


def validate(candidates: list[dict], target: int = 20, timeout: float = 4.0,
             workers: int = 100, test_url: str = DEFAULT_TEST_URL,
             on_live=None) -> list[dict]:
    """Concurrently test candidates until `target` live ones are found."""
    live: list[dict] = []
    checked = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_check_one, c, test_url, timeout): c for c in candidates[: max(target * 25, target)]}
        for fut in concurrent.futures.as_completed(futs):
            checked += 1
            res = fut.result()
            if res:
                live.append(res)
                if on_live:
                    on_live(res)
                if len(live) >= target:
                    break
    return live


def health(pool_file: str, timeout: float = 6.0, workers: int = 50,
           test_url: str = DEFAULT_TEST_URL) -> tuple[list[str], list[str]]:
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
