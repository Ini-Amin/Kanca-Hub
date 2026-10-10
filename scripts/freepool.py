#!/usr/bin/env python3
"""scripts/freepool.py -- harvest free proxy lists, filter for *browser* viability.

Why not just use the petani-proxy harvester?
--------------------------------------------
It exists and works (30 sources across http/socks4/socks5), but it is a large
TUI-coupled module and it validates proxies with a plain HTTP GET. That is the
wrong test for us: a proxy that answers a 2 KB GET in 3 s can still be unusable
to drive a browser, because a captcha-gated signup needs a full page load
(hundreds of KB, subresources, a TLS handshake to the real target).

Measured 2026-10-10: 9/40 monosans proxies "alive" by GET, but 0/3 could load
proxy.webshare.io inside 60 s. So validate at *two* levels:

  level 1  reachability  -- can it fetch an IP-echo at all?  (cheap, parallel)
  level 2  browser-grade -- can it carry a real page load?    (slow, opt-in)

Only level-2 survivors are worth handing to a signup flow.

Sources
-------
From ~/petani-proxy/config/sources.json (already includes monosans), plus any
extra URLs passed with --source. The four lists named when this was written:

  monosans/proxy-list          http.txt socks4.txt socks5.txt   (live)
  HProxy Free Proxy            (404 as of 2026-10-10)
  VPSLab Free Proxy List       (404 as of 2026-10-10)
  IP2Location (LITE)           (404 as of 2026-10-10)

Usage
-----
  python scripts/freepool.py fetch                     # download all sources
  python scripts/freepool.py filter --limit 200        # level-1 + level-2
  python scripts/freepool.py filter --level 1 --limit 500
  python scripts/freepool.py show                       # what is on disk

Output: ~/.config/auto-freecf/freepool.json  (with per-proxy verdicts)

Transport note
--------------
urllib cannot speak socks at all ("unknown url type: socks5"), so socks/socks4
candidates are validated through curl -x, exactly as proxy_lib.py does. A proxy
we cannot reach is not a usable proxy, however good its exit IP looks.

Flakiness is expected and is not a bug: the same proxy can load the 52 KB
Webshare page (level 2 pass) while timing out on a 12-byte IP echo (level 1
fail) moments earlier. Free proxies are that unstable -- treat the pool as a
source of occasional luck, not a dependable rung.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
OUT_DIR = HOME / ".config" / "auto-freecf"
POOL_JSON = OUT_DIR / "freepool.json"
PETANI_SOURCES = HOME / "petani-proxy" / "config" / "sources.json"

# the lists the user named, kept here so they are tried even if the petani
# config forgets them. (Three of the four 404'd when this was written; the
# fetch step reports that per-URL rather than silently dropping them.)
EXTRA_SOURCES = {
    "http": [
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_geolocation/http.txt",
        "https://raw.githubusercontent.com/HProxy/Free-Proxy/main/http.txt",
        "https://raw.githubusercontent.com/VPSLabCloud/free-proxy-list/main/http.txt",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    ],
    "socks4": [
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
    ],
    "socks5": [
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    ],
}

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

PROXY_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}$")


def _load_sources() -> dict[str, list[str]]:
    src = {k: list(v) for k, v in EXTRA_SOURCES.items()}
    if PETANI_SOURCES.exists():
        try:
            pet = json.loads(PETANI_SOURCES.read_text())
            for proto, urls in pet.items():
                p = proto.lower()
                if p not in src:
                    src[p] = []
                for u in urls:
                    if u not in src[p]:
                        src[p].append(u)
        except Exception as e:  # noqa: BLE001
            print(f"  ! could not read {PETANI_SOURCES}: {e}")
    return src


def fetch_source(url: str, timeout: float = 25.0) -> tuple[str, list[str]]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        body = urllib.request.urlopen(req, timeout=timeout, context=_CTX).read()
        txt = body.decode(errors="replace")
    except Exception as e:  # noqa: BLE001
        return url, [f"!ERR {str(e)[:50]}"]
    found = []
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # accept "host:port" and "scheme://host:port" and "host:port:user:pass"
        cand = line.split("://")[-1].split(":")[0:2]
        if len(cand) == 2:
            hp = f"{cand[0]}:{cand[1]}"
            if PROXY_RE.match(hp):
                found.append(hp)
    return url, found


def cmd_fetch(a) -> int:
    src = _load_sources()
    print(f"sources: {sum(len(v) for v in src.values())} urls "
          f"({', '.join(f'{k}={len(v)}' for k, v in src.items())})")
    allp: dict[str, dict] = {}
    t0 = time.time()
    for proto, urls in src.items():
        with cf.ThreadPoolExecutor(min(16, len(urls) or 1)) as ex:
            for url, found in ex.map(fetch_source, urls):
                if found and found[0].startswith("!ERR"):
                    print(f"  [{proto}] {found[0]:52} {url[:60]}")
                    continue
                for hp in found:
                    allp.setdefault(hp, {"proxy": hp, "scheme": proto,
                                         "sources": []})
                    allp[hp]["sources"].append(url.split("/")[4] if
                                               "githubusercontent" in url else url[:32])
                print(f"  [{proto}] {len(found):6} proxies  {url[:64]}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    POOL_JSON.write_text(json.dumps({
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": len(allp), "proxies": list(allp.values()),
    }, indent=1))
    print(f"\nunique proxies: {len(allp)}   ({time.time()-t0:.1f}s) -> {POOL_JSON}")
    return 0


def _proxy_opener(scheme: str, proxy: str):
    """An opener for HTTP proxies, or None when the scheme is socks.

    urllib cannot speak socks at all ("unknown url type: socks5"), so socks
    candidates go through curl instead -- which proxy_lib.py already does for the
    same reason. A beautiful browser-grade proxy is useless to us if we cannot
    reach it, so the transport has to match the scheme.
    """
    if scheme == "http":
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": f"http://{proxy}",
                                         "https": f"http://{proxy}"}),
            urllib.request.HTTPSHandler(context=_CTX),
        )
    return None


def _curl(url: str, scheme: str, proxy: str, timeout: float) -> bytes | None:
    """Fetch `url` via curl so socks/socks5 work. Returns body or None."""
    import subprocess

    full = f"{scheme}h://{proxy}" if scheme.startswith("socks") else f"http://{proxy}"
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", str(int(timeout)), "-x", full,
             "-A", _UA, "-L", url],
            capture_output=True, timeout=timeout + 5,
        )
        return r.stdout if r.returncode == 0 and r.stdout else None
    except Exception:  # noqa: BLE001
        return None


def _level1(entry: dict, timeout: float) -> dict:
    """Reachability: can it fetch an IP echo?"""
    scheme, proxy = entry["scheme"], entry["proxy"]
    t0 = time.time()
    if scheme == "http":
        try:
            opener = _proxy_opener(scheme, proxy)
            ip = opener.open("https://api.ipify.org", timeout=timeout).read().decode().strip()[:45]
            return {**entry, "exit_ip": ip,
                    "latency_ms": int((time.time() - t0) * 1000), "level1": True}
        except Exception as e:  # noqa: BLE001
            return {**entry, "level1": False, "err": str(e)[:40]}
    body = _curl("https://api.ipify.org", scheme, proxy, timeout)
    if body and re.match(r"^\d{1,3}(?:\.\d{1,3}){3}", body.decode(errors="replace").strip()):
        return {**entry, "exit_ip": body.decode().strip()[:45],
                "latency_ms": int((time.time() - t0) * 1000), "level1": True}
    return {**entry, "level1": False, "err": "curl failed"}


# A page load that must succeed for the proxy to be usable for signups.
# webshare register is the harshest of the targets we care about.
BROWSER_TARGET = "https://proxy.webshare.io/register"
PAGE_MIN_BYTES = 20000


def _level2(entry: dict, timeout: float) -> dict:
    """Browser-grade: fetch the real target page and require real content."""
    scheme, proxy = entry["scheme"], entry["proxy"]
    t0 = time.time()
    try:
        if scheme == "http":
            opener = _proxy_opener(scheme, proxy)
            req = urllib.request.Request(BROWSER_TARGET,
                                         headers={"User-Agent": _UA, "Accept": "text/html"})
            r = opener.open(req, timeout=timeout)
            body = r.read(400_000)
        else:
            body = _curl(BROWSER_TARGET, scheme, proxy, timeout) or b""
        ms = int((time.time() - t0) * 1000)
        low = body.decode(errors="replace").lower()
        ok = len(body) >= PAGE_MIN_BYTES and "just a moment" not in low
        return {**entry, "level2": ok, "page_bytes": len(body), "page_ms": ms,
                "challenge": "just a moment" in low,
                "err": None if ok else f"small_or_challenged ({len(body)}b)"}
    except Exception as e:  # noqa: BLE001
        return {**entry, "level2": False, "err": str(e)[:40]}


def cmd_filter(a) -> int:
    if not POOL_JSON.exists():
        print(f"no pool at {POOL_JSON} -- run `freepool.py fetch` first")
        return 1
    pool = json.loads(POOL_JSON.read_text())["proxies"]
    if a.limit and len(pool) > a.limit:
        # spread the sample across schemes so http does not crowd out socks
        import random
        random.seed(1)
        pool = random.sample(pool, a.limit)
    print(f"level {a.level}: testing {len(pool)} proxies "
          f"(timeout {a.timeout}s, {a.workers} workers)…")
    t0 = time.time()
    with cf.ThreadPoolExecutor(a.workers) as ex:
        l1 = list(ex.map(lambda e: _level1(e, a.timeout), pool))
    alive = [e for e in l1 if e.get("level1")]
    print(f"  level 1: {len(alive)}/{len(pool)} reachable   ({time.time()-t0:.0f}s)")
    for e in sorted(alive, key=lambda x: x.get("latency_ms", 9999))[:10]:
        print(f"     {e['scheme']:6} {e['proxy']:22} {e.get('latency_ms')}ms -> {e['exit_ip']}")

    results = alive
    if a.level >= 2 and alive:
        t1 = time.time()
        with cf.ThreadPoolExecutor(max(4, a.workers // 2)) as ex:
            l2 = list(ex.map(lambda e: _level2(e, a.timeout), alive))
        results = [e for e in l2 if e.get("level2")]
        print(f"  level 2: {len(results)}/{len(alive)} can load {BROWSER_TARGET.split('//')[1][:30]}"
              f"   ({time.time()-t1:.0f}s)")
        for e in results[:10]:
            print(f"     {e['scheme']:6} {e['proxy']:22} {e.get('page_ms')}ms "
                  f"{e.get('page_bytes')}b -> {e['exit_ip']}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    good = OUT_DIR / "freepool_browser.txt"
    with good.open("w") as f:
        for e in results:
            f.write(f"{e['scheme']}://{e['proxy']}\n")
    (OUT_DIR / "freepool_scored.json").write_text(json.dumps({
        "filtered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": a.level, "tested": len(pool),
        "reachable": len(alive), "browser_grade": len(results),
        "proxies": results,
    }, indent=1))
    print(f"\nbrowser-grade pool: {len(results)} -> {good}")
    if not results:
        print("  (none. free lists are mostly dead; that is expected. "
              "widen --limit or accept that only residential clears the gates.)")
    return 0


def cmd_proxyma(a) -> int:
    """Write a verified Proxyma pool file (one https://user:pass@host:port per line).

    Drops nodes that fail, are slow, or re-sign TLS (curl verify error), so a
    round-robin gateway over this file never lands on a laggy/broken node.
    ponytail: datacenter exits; Cloudflare gates only, not Google reCAPTCHA.
    """
    import subprocess
    def g(path, secret=""):
        req = urllib.request.Request("https://proxyma.space" + path,
                                     headers={"X-Proxy-Secret": secret, "User-Agent": _UA})
        return json.load(urllib.request.urlopen(req, timeout=15, context=_CTX))
    s = g("/api/secret")["secret"]
    c = g("/api/proxy-credentials", s)["data"]
    nodes = [n for n in g("/api/proxies?nodes=1", s)["data"] if n.get("status") == "online"]
    urls = [f"https://{c['username']}:{c['password']}@{n['host']}:{n['port']}" for n in nodes]
    def probe(u):
        r = subprocess.run(["curl", "-s", "-m", str(int(a.max_time)), "-x", u, "-o", "/dev/null",
                            "-w", "%{http_code} %{ssl_verify_result}", "https://tokenharbor.ai/login"],
                           capture_output=True, text=True)
        return u, r.stdout.strip() == "200 0"
    with cf.ThreadPoolExecutor(8) as ex:
        good = [u for u, ok in ex.map(probe, urls) if ok]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(good) + ("\n" if good else ""))
    print(f"proxyma: {len(good)}/{len(urls)} nodes verified -> {a.out}")
    return 0 if good else 1


def cmd_show(_a) -> int:
    if not POOL_JSON.exists():
        print(f"no pool at {POOL_JSON}")
        return 1
    d = json.loads(POOL_JSON.read_text())
    print(f"pool: {d['total']} proxies, fetched {d.get('fetched_at')}")
    by: dict[str, int] = {}
    for p in d["proxies"]:
        by[p["scheme"]] = by.get(p["scheme"], 0) + 1
    for k, v in sorted(by.items()):
        print(f"  {k:8} {v}")
    sc = OUT_DIR / "freepool_scored.json"
    if sc.exists():
        s = json.loads(sc.read_text())
        print(f"scored {s.get('filtered_at')}: level {s.get('level')}, "
              f"{s.get('reachable')} reachable, {s.get('browser_grade')} browser-grade")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="download all sources into the pool").set_defaults(fn=cmd_fetch)
    f = sub.add_parser("filter", help="test pool for reachability and browser viability")
    f.add_argument("--level", type=int, default=2, choices=(1, 2))
    f.add_argument("--limit", type=int, default=150, help="max proxies to test")
    f.add_argument("--timeout", type=float, default=15.0)
    f.add_argument("--workers", type=int, default=30)
    f.set_defaults(fn=cmd_filter)
    px = sub.add_parser("proxyma", help="write a verified keyless Proxyma pool")
    px.add_argument("--out", default="/tmp/opencode/proxyma_ok.txt")
    px.add_argument("--max-time", type=float, default=10.0, help="drop nodes slower than this")
    px.set_defaults(fn=cmd_proxyma)
    sub.add_parser("show", help="what is on disk").set_defaults(fn=cmd_show)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
