#!/usr/bin/env python3
"""scripts/exa_search.py — Exa.ai neural search engine client & CLI.

Uses the active Exa API key ($50 grant) for semantic and neural web search.
Returns AI-ready neural search results, extracted text contents, and highlights.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

EXA_API_URL = "https://api.exa.ai/search"

# Look for EXA_API_KEY in environment or .search_keys.env
def get_api_key() -> str:
    key = os.environ.get("EXA_API_KEY", "").strip()
    if key:
        return key
    search_env = Path(__file__).resolve().parent.parent / ".search_keys.env"
    if search_env.is_file():
        for line in search_env.read_text().splitlines():
            if "EXA_API_KEY" in line and "=" in line:
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    return key
    raise RuntimeError("EXA_API_KEY not found in environment or .search_keys.env")


def search(
    query: str,
    num_results: int = 5,
    max_chars: int = 800,
    use_autoprompt: bool = True,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> dict:
    """Execute an Exa neural search and return parsed results."""
    api_key = get_api_key()
    payload: dict = {
        "query": query,
        "numResults": num_results,
        "useAutoprompt": use_autoprompt,
        "contents": {
            "text": {"maxCharacters": max_chars}
        }
    }
    if include_domains:
        payload["includeDomains"] = include_domains
    if exclude_domains:
        payload["excludeDomains"] = exclude_domains

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        EXA_API_URL,
        data=data,
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; KancaHub/1.0)",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def main(cli_args: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Exa.ai neural search CLI")
    ap.add_argument("query", help="search query")
    ap.add_argument("-n", "--num", type=int, default=5, help="number of results")
    ap.add_argument("--json", action="store_true", help="output raw JSON")
    ap.add_argument("--max-chars", type=int, default=600, help="max content chars per result")
    args = ap.parse_args(cli_args)

    try:
        res = search(args.query, num_results=args.num, max_chars=args.max_chars)
    except Exception as e:
        print(f"Error executing Exa search: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(res, indent=2))
        return 0

    results = res.get("results", [])
    print(f"\nExa Neural Search ({len(results)} results for: '{args.query}'):")
    print("═" * 80)
    for i, it in enumerate(results, 1):
        title = it.get("title") or "No title"
        url = it.get("url") or ""
        snippet = it.get("text", "").strip().replace("\n", " ")
        if len(snippet) > 280:
            snippet = snippet[:280] + "..."
        print(f"[{i}] {title}")
        print(f"    URL: {url}")
        if snippet:
            print(f"    Snippet: {snippet}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
