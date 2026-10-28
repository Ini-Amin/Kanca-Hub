#!/usr/bin/env python3
"""patch_9router_codebuddy.py — make the built-in `codebuddy-intl` provider work directly.

Why this exists
---------------
`codebuddy-intl` (alias `cbai`) in 9Router ships with IDE-flavoured headers and no
correlation IDs, so www.codebuddy.ai answers `{"code":11128,"msg":"request illegal"}`
("This call failed channel verification"). Measured 2026-10-28 by capturing the
official CLI (`NODE_OPTIONS=--require spy.js`, see docs), the upstream gate is a set
of static headers plus one body rule:

  required headers (static values are accepted):
    X-Conversation-ID, X-Request-ID, X-Conversation-Message-ID,
    X-Conversation-Request-ID, X-Root-Request-ID,
    X-Agent-Purpose/Intent/Runtime/Type, X-IDE-Type/Name/Version,
    X-Domain, X-Product, X-Private-Data, X-User-Id,
    x-codebuddy-request, x-requested-with, User-Agent: CLI/<ver> CodeBuddy/<ver>

  required body rule: messages[0].role must be "system"
    (otherwise 11128 "first message is not system prompt")

Everything is static — replaying the same four IDs on every call still returns 200 —
so the header map can live in the compiled provider config.

What it changes
---------------
For every chunk that contains the `codebuddy-intl` transport:
  * baseUrl  -> https://www.codebuddy.ai/v2/chat/completions   (undoes any bridge redirect)
  * headers  -> the official static set above

Idempotent: re-running is a no-op once the headers already match.
9Router caches these chunks at boot, so a restart is required afterwards — this
script never restarts anything.

Usage:
    python3 scripts/patch_9router_codebuddy.py            # patch
    python3 scripts/patch_9router_codebuddy.py --check    # report only
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.home() / ".npm-global/lib/node_modules/9router/app/.next-cli-build/server"
UPSTREAM = "https://www.codebuddy.ai/v2/chat/completions"

# 32 hex chars. Static by measurement: upstream does not correlate these across calls.
CID = "0123456789abcdef0123456789abcdef"

HEADERS = {
    "User-Agent": "CLI/2.177.0 CodeBuddy/2.177.0",
    "X-Product": "SaaS",
    "X-IDE-Type": "CLI",
    "X-IDE-Name": "CLI",
    "X-IDE-Version": "2.177.0",
    "X-Domain": "www.codebuddy.ai",
    "X-Private-Data": "false",
    "X-Agent-Purpose": "conversation",
    "X-Agent-Intent": "craft",
    "X-Agent-Runtime": "other",
    "X-Agent-Type": "main",
    "x-requested-with": "XMLHttpRequest",
    "x-codebuddy-request": "1",
    "X-User-Id": CID,
    "X-Conversation-ID": CID,
    "X-Request-ID": CID,
    "X-Conversation-Message-ID": CID,
    "X-Conversation-Request-ID": CID,
    "X-Root-Request-ID": CID,
}


def headers_js() -> str:
    return json.dumps(HEADERS, separators=(",", ":"))


def patch_file(path: Path, check: bool) -> str:
    src = path.read_text()
    idx = src.find('id:"codebuddy-intl"')
    if idx < 0:
        return "skip"
    end = src.find("usage:{url:", idx)
    if end < 0:
        return "skip"
    seg = src[idx:end]

    m_base = re.search(r'baseUrl:"([^"]*)"', seg)
    m_hdr = re.search(r"headers:\{(.*?)\},auth:\{", seg)
    if not m_base or not m_hdr:
        return "skip"

    want_base = UPSTREAM
    # m_hdr.group(1) is the text BETWEEN the braces, so compare/inject inner text only
    want_hdr = headers_js()[1:-1]
    if m_base.group(1) == want_base and m_hdr.group(1) == want_hdr:
        return "already"

    if check:
        return f"needs patch (base={m_base.group(1)})"

    new_seg = (
        seg[: m_base.start(1)] + want_base + seg[m_base.end(1) :]
    )
    # recompute header span on the rewritten segment
    m_hdr = re.search(r"headers:\{(.*?)\},auth:\{", new_seg)
    new_seg = new_seg[: m_hdr.start(1)] + want_hdr + new_seg[m_hdr.end(1) :]

    backup = path.with_suffix(path.suffix + ".bak-cbintl")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(src[:idx] + new_seg + src[end:])
    return "patched"


def syntax_ok(path: Path) -> bool:
    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  SYNTAX ERROR in {path.name}: {r.stderr.strip()[:200]}")
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report state, change nothing")
    args = ap.parse_args()

    if not ROOT.exists():
        sys.exit(f"9Router build not found at {ROOT}")

    touched = 0
    for path in sorted(ROOT.rglob("*.js")):
        if path.name.endswith(".bak-cbintl"):
            continue
        try:
            head = path.read_text()[:2_000_000]
        except OSError:
            continue
        if 'id:"codebuddy-intl"' not in head:
            continue
        status = patch_file(path, args.check)
        print(f"{path.relative_to(ROOT)}: {status}")
        if status == "patched":
            touched += 1
            if not syntax_ok(path):
                return 1

    if touched:
        print(f"\npatched {touched} file(s). 9Router caches chunks at boot — restart to load it.")
    elif not args.check:
        print("\nnothing to do.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
