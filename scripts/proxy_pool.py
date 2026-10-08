#!/usr/bin/env python3
"""
scripts/proxy_pool.py — per-account rotating proxy pool with template expansion.

Ported from abliteration-bulk-creator's proxy.js. Whereas proxy_lib.harvest()
collects proxies, this hands ONE egress to each account and can expand a
sticky-rotating gateway template so every account gets a fresh exit IP:

    http://user:pass@gate.superproxy.io:22225
    -> http://user-session-{session}:pass@...:22225      (a new session per account)
    supports {session}, {index}, {country}

Sources, in priority order: an explicit list, a proxy file (proxy.txt), or a
template. Everything is plain data — no I/O beyond reading the file.

Usage:
  python scripts/proxy_pool.py --list "http://u:p@h:1,http://u:p@h:2"
  python scripts/proxy_pool.py --template "http://u-session-{session}:p@gw:22225" --count 5
"""
from __future__ import annotations

import argparse
import secrets
from pathlib import Path


def parse_proxy_line(line: str, default_scheme: str = "http") -> str | None:
    """Normalise host:port | host:port:user:pass | user:pass@host:port | URL."""
    if not line:
        return None
    s = str(line).strip()
    if not s or s.startswith("#") or s.startswith("//"):
        return None
    s = s.split(" #", 1)[0].strip()
    if not s:
        return None

    scheme = default_scheme.replace("://", "")
    m = __import__("re").match(r"^(https?|socks4|socks5)://(.*)$", s, __import__("re").I)
    if m:
        scheme, s = m.group(1).lower(), m.group(2)

    if "@" in s:
        creds, _, hostport = s.rpartition("@")
        if not hostport:
            return None
        u, _, p = creds.partition(":")
        from urllib.parse import quote
        auth = f"{quote(u)}:{quote(p)}@" if creds else ""
        return f"{scheme}://{auth}{hostport}"

    parts = s.split(":")
    if len(parts) == 2:
        return f"{scheme}://{parts[0]}:{parts[1]}"
    if len(parts) >= 3:
        host, port, user = parts[0], parts[1], parts[2]
        pw = ":".join(parts[3:])
        if not port.isdigit():
            return None
        from urllib.parse import quote
        if not user:
            return f"{scheme}://{host}:{port}"
        return f"{scheme}://{quote(user)}:{quote(pw)}@{host}:{port}"
    return None


def load_proxy_file(path: str | Path, default_scheme: str = "http") -> list[str]:
    try:
        raw = Path(path).read_text()
    except OSError:
        return []
    return [p for p in (parse_proxy_line(l, default_scheme) for l in raw.splitlines()) if p]


def expand_template(template: str, index: int = 0, *, rotate: bool = True, country: str = "") -> str:
    """Expand {session}/{index}/{country} placeholders in a gateway URL."""
    if not template:
        return ""
    sid = secrets.token_hex(4) if rotate else "fixed"
    out = template.replace("{session}", sid).replace("{index}", str(index))
    if country:
        out = out.replace("{country}", country)
    return out


class ProxyPool:
    def __init__(self, *, proxy_list=None, proxy_file: str | Path = "",
                 proxy_template: str = "", rotate_per_account: bool = True,
                 country: str = "", default_scheme: str = "http"):
        self.static: list[str] = [p for p in (parse_proxy_line(x, default_scheme) for x in (proxy_list or [])) if p]
        if proxy_file and Path(proxy_file).exists():
            self.static += load_proxy_file(proxy_file, default_scheme)
        # de-dup, keep order
        seen = set()
        self.static = [p for p in self.static if not (p in seen or seen.add(p))]
        self.template = proxy_template or ""
        self.rotate_per_account = rotate_per_account
        self.country = country
        self._cursor = 0
        self.usage: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.static) or bool(self.template)

    def assign(self, index: int) -> str:
        """The egress for account `index` ("" = direct)."""
        if self.template:
            p = expand_template(self.template, index, rotate=self.rotate_per_account, country=self.country)
            self.usage[p] = self.usage.get(p, 0) + 1
            return p
        if self.static:
            p = self.static[self._cursor % len(self.static)]
            self._cursor += 1
            self.usage[p] = self.usage.get(p, 0) + 1
            return p
        return ""

    def rotate(self, current: str) -> str:
        """A different egress than `current` (use after a failure)."""
        if self.template:
            return self.assign(self._cursor)
        if len(self.static) <= 1:
            return self.static[0] if self.static else ""
        self._cursor += 1
        nxt = self.static[self._cursor % len(self.static)]
        if nxt == current:
            self._cursor += 1
            nxt = self.static[self._cursor % len(self.static)]
        self.usage[nxt] = self.usage.get(nxt, 0) + 1
        return nxt


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-account rotating proxy pool")
    ap.add_argument("--list", default="", help="comma/newline separated proxy URLs")
    ap.add_argument("--file", default="", help="proxy file (one per line)")
    ap.add_argument("--template", default="", help="gateway template with {session}/{index}/{country}")
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--country", default="")
    a = ap.parse_args(argv)
    lst = [x for x in a.list.replace("\n", ",").split(",") if x.strip()] if a.list else []
    pool = ProxyPool(proxy_list=lst, proxy_file=a.file, proxy_template=a.template, country=a.country)
    print(f"  pool enabled={pool.enabled} static={len(pool.static)} template={bool(pool.template)}")
    for i in range(a.count):
        print(f"  account {i}: {pool.assign(i) or '(direct)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())