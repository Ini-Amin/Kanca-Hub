#!/usr/bin/env python3
"""scripts/linuxdo_fetch.py — Harvest latest topics from LINUX DO via Camoufox.

LINUX DO (https://linux.do) is a premier developer & AI community (Discourse),
protected by Cloudflare Turnstile. This script solves Turnstile headlessly in ~6s,
fetches topics across Welfare (福利羊毛), Resource (资源荟萃), Develop, and Latest,
and caches the results into ~/.config/auto-freecf/linuxdo_cache.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
CACHE_FILE = Path.home() / ".config" / "auto-freecf" / "linuxdo_cache.json"

CAMOUFOX_PYTHON = Path.home() / ".local" / "share" / "auto-freecf" / "camoufox-venv" / "bin" / "python"
if not CAMOUFOX_PYTHON.is_file():
    CAMOUFOX_PYTHON = Path(sys.executable)

CATEGORIES = [
    ("latest", "https://linux.do/latest.json"),
    ("welfare", "https://linux.do/c/welfare/36.json"),
    ("resource", "https://linux.do/c/resource/14.json"),
    ("develop", "https://linux.do/c/develop/4.json"),
]


async def run_harvest(max_wait_secs: int = 40, log=print) -> list[dict]:
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        # Re-exec with camoufox venv if needed
        if CAMOUFOX_PYTHON.is_file() and str(CAMOUFOX_PYTHON) != sys.executable:
            import subprocess
            res = subprocess.run([str(CAMOUFOX_PYTHON), str(Path(__file__).resolve()), "--json"], capture_output=True, text=True)
            if res.returncode == 0:
                try:
                    return json.loads(res.stdout)
                except Exception:
                    pass
        raise RuntimeError("camoufox not found. Install or run inside camoufox-venv.")

    t0 = time.time()
    log("  [linuxdo] launching Camoufox stealth browser to bypass Turnstile...")
    async with AsyncCamoufox(headless=True, humanize=True, os="windows") as browser:
        page = await browser.new_page()
        await page.goto("https://linux.do/", wait_until="domcontentloaded", timeout=max_wait_secs * 1000)

        # Wait for Turnstile challenge to resolve
        for _ in range(max_wait_secs // 2):
            title = await page.title()
            if "LINUX DO" in title and "Just a moment" not in title:
                log(f"  [linuxdo] ✓ Turnstile passed in {time.time()-t0:.1f}s (Title: {title[:30]})")
                break
            await page.wait_for_timeout(2000)
        else:
            raise RuntimeError("Cloudflare Turnstile challenge did not resolve in time")

        log("  [linuxdo] querying category feeds in page context...")
        endpoints = [url for _, url in CATEGORIES]
        data = await page.evaluate("""async (urls) => {
            return await Promise.all(urls.map(async u => {
                try {
                    const r = await fetch(u);
                    return await r.json();
                } catch(e) {
                    return null;
                }
            }));
        }""", endpoints)

        all_topics: dict[int, dict] = {}
        for (cat_name, _), res in zip(CATEGORIES, data):
            if not res or not isinstance(res, dict):
                continue
            for t in res.get("topic_list", {}).get("topics", []):
                tid = t.get("id")
                if tid and tid not in all_topics:
                    all_topics[tid] = {
                        "id": tid,
                        "title": t.get("title", ""),
                        "slug": t.get("slug", ""),
                        "created_at": t.get("created_at", ""),
                        "bumped_at": t.get("bumped_at", ""),
                        "posts_count": t.get("posts_count", 1),
                        "views": t.get("views", 0),
                        "like_count": t.get("like_count", 0),
                        "category": cat_name,
                        "category_id": t.get("category_id"),
                        "author": t.get("last_poster_username", ""),
                        "url": f"https://linux.do/t/{t.get('slug', '')}/{tid}",
                    }

        result = list(all_topics.values())
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(result, indent=2))
        log(f"  [linuxdo] ✓ saved {len(result)} topics to {CACHE_FILE} (took {time.time()-t0:.1f}s)")
        return result


def load_cached_topics(max_age_secs: int = 1800) -> list[dict]:
    """Load topics from cache if fresher than max_age_secs."""
    if not CACHE_FILE.is_file():
        return []
    try:
        mtime = CACHE_FILE.stat().st_mtime
        if time.time() - mtime > max_age_secs:
            return []
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="LINUX DO topics harvester")
    ap.add_argument("--json", action="store_true", help="output json only")
    ap.add_argument("--filter", "-f", default="", help="filter keyword")
    ap.add_argument("--force", action="store_true", help="force live harvest")
    args = ap.parse_args()

    topics = []
    if not args.force:
        topics = load_cached_topics(max_age_secs=900)

    if not topics:
        try:
            topics = asyncio.run(run_harvest(log=(lambda *a: None) if args.json else print))
        except Exception as e:
            if not args.json:
                print(f"Error harvesting linux.do: {e}", file=sys.stderr)
            topics = load_cached_topics(max_age_secs=86400)

    if args.filter:
        kw = args.filter.lower()
        topics = [t for t in topics if kw in t.get("title", "").lower() or kw in t.get("author", "").lower()]

    if args.json:
        print(json.dumps(topics, indent=2))
        return 0

    print(f"\nLINUX DO Topics ({len(topics)} items):")
    print(f"{'CAT':<10} {'TITLE':<65} {'REPLIES':<8} {'AUTHOR'}")
    print("─" * 100)
    for t in topics[:25]:
        title = t.get("title", "")[:62]
        print(f"{t.get('category', ''):<10} {title:<65} {t.get('posts_count', 0):<8} @{t.get('author', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
