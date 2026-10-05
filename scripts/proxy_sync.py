#!/usr/bin/env python3
"""
Proxy sync — wire freshly-harvested residential proxies into every tool.

The problem this fixes: PetaniProxy's Webshare hunter writes fresh residential
IPs to ~/petani-proxy/output/webshare_residential.txt, but our tools read
~/Auto-FreeCF/signup_from_scratch/proxies.txt (and harbor reads
~/harbor/tools/proxies.txt). Nothing copied them over, so a fresh harvest was
never actually used.

This module:
  * picks the freshest residential list (webshare_residential.txt, else
    live_elite.txt / live_all.txt)
  * validates them (optionally) and keeps the live ones
  * writes them to every consumer location
  * writes the gateway-usable form too

Usage:
  python3 proxy_sync.py                # sync freshest residential -> all tools
  python3 proxy_sync.py --validate     # also liveness-check (keeps only live)
  python3 proxy_sync.py --from FILE    # use a specific list
  python3 proxy_sync.py --show         # just print current wiring
"""

from __future__ import annotations

import argparse
import concurrent.futures
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
PETANI_OUT = HOME / "petani-proxy" / "output"
AUTO_FREECF = HOME / "Auto-FreeCF"
TARGETS = [
    AUTO_FREECF / "signup_from_scratch" / "proxies.txt",   # signup pipeline + github + grok
    HOME / "harbor" / "tools" / "proxies.txt",             # harbor (TokenHarbor)
]
SOURCES = [
    PETANI_OUT / "webshare_residential.txt",   # real residential (best)
    PETANI_OUT / "live_elite.txt",
    PETANI_OUT / "fast_elite.txt",
    PETANI_OUT / "live_all.txt",
]

C = {"reset": "\x1b[0m", "green": "\x1b[32m", "red": "\x1b[31m", "yellow": "\x1b[33m",
     "dim": "\x1b[2m", "bold": "\x1b[1m", "cyan": "\x1b[36m"}


def col(n, t):
    return f"{C.get(n,'')}{t}{C['reset']}"


def read_lines(p: Path) -> list[str]:
    if not p.exists():
        return []
    return [l.strip() for l in p.read_text().splitlines() if l.strip() and not l.startswith("#")]


def pick_source(explicit: str | None) -> tuple[Path | None, list[str]]:
    if explicit:
        p = Path(explicit)
        return p, read_lines(p)
    for p in SOURCES:
        lines = read_lines(p)
        if lines:
            return p, lines
    return None, []


def check(p: str, timeout: int = 12) -> bool:
    try:
        out = subprocess.run(["curl", "-s", "-m", str(timeout), "-x", p, "https://api.ipify.org"],
                             capture_output=True, text=True, timeout=timeout + 5).stdout.strip()
        return bool(out)
    except Exception:
        return False


def validate(lines: list[str], workers: int = 50) -> list[str]:
    alive = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for p, ok in zip(lines, ex.map(check, lines)):
            if ok:
                alive.append(p)
    return alive


def show() -> None:
    print(col("bold", "\n  Proxy wiring\n"))
    for s in SOURCES:
        n = len(read_lines(s))
        mark = "✅" if n else "➖"
        print(f"  {mark} source {s.name:34s} {n} lines")
    print()
    for t in TARGETS:
        n = len(read_lines(t))
        mark = "✅" if n else "❌"
        print(f"  {mark} target {str(t).replace(str(HOME),'~'):40s} {n} lines")
    print()


def prioritize_fresh(lines: list[str], limit: int | None = None) -> list[str]:
    """Put latest harvested proxies at the top and deduplicate."""
    seen = set()
    fresh = []
    for l in reversed(lines):
        if l not in seen:
            seen.add(l)
            fresh.append(l)
    return fresh[:limit] if limit else fresh


def sync_now(source: str | Path | None = None, validate_live: bool = False, limit: int | None = None, quiet: bool = False) -> int:
    """Sync freshest proxies into all tool pools."""
    src, lines = pick_source(str(source) if source else None)
    if not lines:
        if not quiet:
            print(col("red", "✗ no proxy source found."))
            print(col("dim", "  Harvest first:  kancahub proxy start   (option 3 = Residential)"))
        return 1

    lines = prioritize_fresh(lines, limit=limit)
    if not quiet:
        print(col("cyan", f"\n  source: {src}  ({len(lines)} proxies prioritized with freshest on top)"))

    if validate_live:
        if not quiet:
            print(col("dim", "  validating liveness…"))
        lines = validate(lines)
        if not quiet:
            print(col("cyan", f"  live: {len(lines)}"))
        if not lines:
            if not quiet:
                print(col("red", "  ✗ none survived validation."))
            return 1

    body = "\n".join(lines) + "\n"
    wrote = 0
    for t in TARGETS:
        try:
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(body)
            if not quiet:
                print(f"  {col('green','✓')} wrote {len(lines):4d} -> {str(t).replace(str(HOME),'~')}")
            wrote += 1
        except Exception as e:  # noqa: BLE001
            if not quiet:
                print(f"  {col('red','✗')} {t}: {e}")

    if not quiet:
        print(col("green", f"\n  ✅ {len(lines)} fresh proxies wired to {wrote} tool(s)."))
    return 0 if wrote else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync fresh proxies into all tools")
    ap.add_argument("--from", dest="src", default=None, help="explicit source file")
    ap.add_argument("--validate", action="store_true", help="liveness-check and keep only live")
    ap.add_argument("--limit", type=int, default=None, help="limit to N freshest proxies")
    ap.add_argument("--show", action="store_true", help="just print current wiring")
    a = ap.parse_args()

    if a.show:
        show()
        return 0

    return sync_now(source=a.src, validate_live=a.validate, limit=a.limit)


if __name__ == "__main__":
    sys.exit(main())
