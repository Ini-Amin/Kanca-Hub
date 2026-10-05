#!/usr/bin/env python3
"""
harbor_config.py — configure the local `harbor` (TokenHarbor) install for us.

What it does
------------
1. Sets ``[tempik].base_url`` in ``~/harbor/tools/tokenharbor/config.toml`` to the
   deployed Tempik worker (https://tempik.kancalabs.workers.dev).
2. Copies the Capsolver key: reads ``CAPSOLVER_API_KEY`` from
   ``~/.config/auto-freecf/.env`` (if present) and writes it to
   ``~/harbor/tools/.capsolver_key``. If the key is absent, it prints clear
   instructions and leaves harbor's existing key file untouched.
3. Optionally refreshes harbor's proxy list ``~/harbor/tools/proxies.txt`` from
   ``~/Auto-FreeCF/signup_from_scratch/proxies.txt`` (if the source exists).
   Harbor expects ``user:pass@host:port`` (it prepends ``http://`` itself), so
   any ``http://``/``https://`` scheme is stripped on the way in.
4. Appends a ``CAPSOLVER_API_KEY=`` placeholder to ``~/.config/auto-freecf/.env``
   if the key is missing there (never overwrites existing lines).

All paths are overridable with CLI flags. Nothing here talks to the network.

Usage
-----
    python3 scripts/harbor_config.py                 # do everything
    python3 scripts/harbor_config.py --no-proxies    # skip the proxy list step
    python3 scripts/harbor_config.py --dry-run       # show, don't write
    python3 scripts/harbor_config.py --status        # report config only
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

HOME = Path.home()

HARBOR_DIR = HOME / "harbor"
HARBOR_CONFIG = HARBOR_DIR / "tools" / "tokenharbor" / "config.toml"
HARBOR_CAPSOLVER = HARBOR_DIR / "tools" / ".capsolver_key"
HARBOR_PROXIES = HARBOR_DIR / "tools" / "proxies.txt"

ENV_FILE = HOME / ".config" / "auto-freecf" / ".env"
AUTO_FREECF_PROXIES = HOME / "Auto-FreeCF" / "signup_from_scratch" / "proxies.txt"

TEMPIK_BASE_URL = "https://tempik.kancalabs.workers.dev"

# ANSI colors (best-effort; degrade to plain text when not a TTY)
_TTY = sys.stdout.isatty()
def _c(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if _TTY else text

OK = lambda s: _c("32", s)     # noqa: E731  green
WARN = lambda s: _c("33", s)   # noqa: E731  yellow
ERR = lambda s: _c("31", s)    # noqa: E731  red
DIM = lambda s: _c("2", s)     # noqa: E731


# ─────────────────────────────────────────────────────────── env helpers

def read_env_value(path: Path, key: str) -> str | None:
    """Return the value of `key` in a dotenv-style file, or None."""
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return None


def ensure_env_key(path: Path, key: str, placeholder: str, dry_run: bool) -> str:
    """
    Append `key=placeholder` to `path` if `key` is not already present.

    Returns one of: "present", "appended", "would-append", "created".
    Never overwrites an existing value.
    """
    if path.exists() and read_env_value(path, key) is not None:
        return "present"

    existing = path.read_text() if path.exists() else ""
    created = not path.exists()

    if existing and not existing.endswith("\n"):
        existing += "\n"

    block = ""
    if existing and not existing.endswith("\n\n"):
        block += "\n"
    block += f"# --- Capsolver (Turnstile solving for TokenHarbor / harbor) ---\n{key}={placeholder}\n"

    if dry_run:
        return "created" if created else "would-append"

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(block)
    return "created" if created else "appended"


# ─────────────────────────────────────────────────────────── config.toml

def set_toml_value(path: Path, section: str, key: str, value: str, dry_run: bool) -> str:
    """
    Set `key = "value"` inside `[section]` of a TOML file, editing in place.

    Returns one of: "updated", "unchanged", "created", "would-update".
    Preserves comments/formatting; only the one value line is rewritten.
    """
    if not path.exists():
        if dry_run:
            return "would-create"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'[{section}]\n{key} = "{value}"\n')
        return "created"

    lines = path.read_text().splitlines()
    in_section = False
    section_seen = False
    out: list[str] = []
    changed = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not changed:
                # end of our section without finding the key -> insert before next header
                out.append(f'{key} = "{value}"')
                changed = True
            in_section = stripped == f"[{section}]"
            section_seen = section_seen or in_section
            out.append(line)
            continue

        if in_section and re.match(rf'^\s*{re.escape(key)}\s*=', line):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f'{indent}{key} = "{value}"')
            changed = True
            in_section = False  # only replace the first match
            continue

        out.append(line)

    if in_section and not changed:  # section header was the last line
        out.append(f'{key} = "{value}"')
        changed = True

    if not section_seen:
        if out and out[-1].strip():
            out.append("")
        out.append(f"[{section}]")
        out.append(f'{key} = "{value}"')
        changed = True

    new = "\n".join(out).rstrip("\n") + "\n"
    old = "\n".join(lines).rstrip("\n") + "\n"
    if new == old:
        return "unchanged"
    if dry_run:
        return "would-update"
    path.write_text(new)
    return "updated"


# ─────────────────────────────────────────────────────────── proxies

def _normalize_proxy_line(line: str) -> str:
    """
    Harbor expects `user:pass@host:port` (optionally `name\\tuser:pass@host:port`).
    Strip a leading scheme so multi-format pools work.
    """
    line = line.strip()
    if not line:
        return ""
    if "\t" in line:
        # keep a name column if present; normalize the credentials column
        name, _, rest = line.rpartition("\t")
        rest = re.sub(r"^https?://", "", rest.strip())
        return f"{name}\t{rest}"
    return re.sub(r"^https?://", "", line)


def write_proxies(src: Path, dst: Path, dry_run: bool) -> str:
    """Copy + normalize proxies from `src` to `dst`. Returns status string."""
    if not src.exists():
        return f"skipped (no source: {src})"
    lines = [l for l in (_normalize_proxy_line(x) for x in src.read_text().splitlines()) if l]
    if not lines:
        return f"skipped (source empty: {src})"
    content = "\n".join(lines) + "\n"
    if dst.exists() and dst.read_text() == content:
        return f"unchanged ({len(lines)} proxies)"
    if dry_run:
        return f"would-write {len(lines)} proxies -> {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content)
    return f"wrote {len(lines)} proxies -> {dst}"


# ─────────────────────────────────────────────────────────── main

def main() -> int:
    ap = argparse.ArgumentParser(description="Configure harbor (TokenHarbor) for the Tempik mail worker")
    ap.add_argument("--harbor-dir", default=str(HARBOR_DIR), help=f"harbor repo dir (default {HARBOR_DIR})")
    ap.add_argument("--env-file", default=str(ENV_FILE), help=f"auto-freecf .env (default {ENV_FILE})")
    ap.add_argument("--proxies-src", default=str(AUTO_FREECF_PROXIES),
                    help=f"source proxies.txt (default {AUTO_FREECF_PROXIES})")
    ap.add_argument("--tempik-url", default=TEMPIK_BASE_URL, help=f"Tempik base URL (default {TEMPIK_BASE_URL})")
    ap.add_argument("--turnstile-provider", default="camoufox", choices=["camoufox", "capsolver"],
                    help="Turnstile solver provider (default: camoufox [free in-browser])")
    ap.add_argument("--no-proxies", action="store_true", help="skip refreshing harbor's proxy list")
    ap.add_argument("--dry-run", action="store_true", help="show actions without writing")
    ap.add_argument("--status", action="store_true", help="only report current harbor config")
    args = ap.parse_args()

    harbor_dir = Path(args.harbor_dir).expanduser()
    cfg_path = harbor_dir / "tools" / "tokenharbor" / "config.toml"
    caps_path = harbor_dir / "tools" / ".capsolver_key"
    proxies_dst = harbor_dir / "tools" / "proxies.txt"
    env_path = Path(args.env_file).expanduser()
    proxies_src = Path(args.proxies_src).expanduser()
    dry = args.dry_run

    print(f"\n{'=' * 66}\n  harbor_config — Tempik + Turnstile (Camoufox) + proxies\n{'=' * 66}")

    if args.status:
        print(f"  harbor dir    : {harbor_dir} ({'ok' if harbor_dir.exists() else ERR('MISSING')})")
        print(f"  config.toml   : {cfg_path} ({'ok' if cfg_path.exists() else ERR('MISSING')})")
        cur = None
        cur_solver = None
        if cfg_path.exists():
            text = cfg_path.read_text()
            m = re.search(r'\[tempik\][^\[]*?base_url\s*=\s*"([^"]*)"', text, re.S)
            cur = m.group(1) if m else None
            m2 = re.search(r'\[turnstile\][^\[]*?provider\s*=\s*"([^"]*)"', text, re.S)
            cur_solver = m2.group(1) if m2 else "camoufox (default)"
        print(f"  tempik url    : {cur or ERR('(unset)')}")
        print(f"  turnstile     : {cur_solver}")
        have_key = caps_path.exists() and caps_path.read_text().strip() != ""
        print(f"  capsolver key : {caps_path} ({'ok' if have_key else DIM('none [optional - using camoufox]')})")
        env_key = read_env_value(env_path, "CAPSOLVER_API_KEY")
        print(f"  env key       : {'set' if env_key else DIM('absent')} ({env_path})")
        print(f"  proxies       : {proxies_dst} ({'ok' if proxies_dst.exists() else WARN('none')})")
        print()
        return 0

    rc = 0
    if dry:
        print(DIM("  (dry run — nothing will be written)"))

    # ── 1. [tempik].base_url ────────────────────────────────────────────
    print(f"\n  [{OK('1')}] Tempik base_url -> {args.tempik_url}")
    if not cfg_path.exists():
        print(f"      {ERR('✗')} config not found: {cfg_path}")
        print(f"      create it from example: cp {cfg_path.with_name('example.config.toml')} {cfg_path}")
        rc = 1
    else:
        st = set_toml_value(cfg_path, "tempik", "base_url", args.tempik_url, dry)
        mark = {"updated": OK("✓"), "unchanged": DIM("="), "would-update": WARN("~"),
                "created": OK("✓")}.get(st, WARN("?"))
        print(f"      {mark} {st}  ({cfg_path})")

    # ── 2. [turnstile].provider ─────────────────────────────────────────
    print(f"\n  [{OK('2')}] Turnstile solver provider -> {args.turnstile_provider}")
    if cfg_path.exists():
        st = set_toml_value(cfg_path, "turnstile", "provider", args.turnstile_provider, dry)
        mark = {"updated": OK("✓"), "unchanged": DIM("="), "would-update": WARN("~"),
                "created": OK("✓")}.get(st, WARN("?"))
        print(f"      {mark} {st}  ({cfg_path})")

    # ── 3. Capsolver key (optional) ─────────────────────────────────────
    print(f"\n  [{OK('3')}] Capsolver key -> {caps_path}")
    key = read_env_value(env_path, "CAPSOLVER_API_KEY")
    if key and "REPLACE-ME" not in key:
        if dry:
            print(f"      {WARN('~')} would write key ({len(key)} chars) to {caps_path}")
        else:
            caps_path.parent.mkdir(parents=True, exist_ok=True)
            caps_path.write_text(key + "\n")
            os.chmod(caps_path, 0o600)
            print(f"      {OK('✓')} wrote key from {env_path} ({len(key)} chars, mode 600)")
    else:
        print(f"      {DIM('•')} Capsolver key optional (Camoufox solver provides free Turnstile solving)")

    # ── 4. proxy list (optional) ────────────────────────────────────────
    print(f"\n  [{OK('4')}] harbor proxy list"), 
    if args.no_proxies:
        print(f"      {DIM('skipped (--no-proxies)')}")
    else:
        st = write_proxies(proxies_src, proxies_dst, dry)
        mark = OK("✓") if st.startswith(("wrote", "unchanged")) else (WARN("~") if st.startswith("would") else DIM("="))
        print(f"      {mark} {st}")

    print(f"\n{'=' * 66}")
    print("  Run TokenHarbor CLI with free Camoufox solver:")
    print("    /home/amen/.local/share/auto-freecf/camoufox-venv/bin/python -m tools.tokenharbor.cli batch 1")
    print("  Or via KancaHub:")
    print("    kancahub thk batch 1")
    print(f"{'=' * 66}\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
