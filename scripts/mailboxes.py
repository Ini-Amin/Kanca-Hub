#!/usr/bin/env python3
"""
scripts/mailboxes.py — one small interface over the disposable-mail providers.

  tempik  : self-hosted Worker (kancalabs.my.id) — default, readable, free
  relay   : KancaHub Supabase relay (kancalabs.biz.id / .my.id)
  litensi : paid email-activation (needs LITENSI_API_ID/KEY + balance)
  static  : no inbox — just the requested address (nothing to read)

A Mailbox exposes: .address, .read() -> list[dict], .code(vendor, timeout).

Pure HTTP + stdlib; the Litensi path requires the requests-based client only when used.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

TEMPIK_BASE = "https://tempik.kancalabs.workers.dev"


def _code_from(text: str) -> Optional[str]:
    plain = re.sub(r"<[^>]+>", " ", text or "")
    m = re.search(r"\b(\d{4})-(\d{4})\b", plain)  # GitHub XXXX-XXXX
    if m:
        return m.group(1) + m.group(2)
    m = re.search(r"\b(\d{4,8})\b", plain)
    return m.group(1) if m else None


class Mailbox:
    def __init__(self, address: str, read_fn: Callable[[], list[dict]], confirm: Callable[[], None] | None = None):
        self.address = address
        self._read = read_fn
        self._confirm = confirm

    def read(self) -> list[dict]:
        try:
            return self._read() or []
        except Exception:
            return []

    def code(self, vendor: str = "", timeout: float = 180.0, interval: float = 4.0,
             exclude: set[str] | None = None) -> Optional[str]:
        """Poll until a code appears. Prefers mail mentioning `vendor`; skips `exclude`."""
        skip = {str(c) for c in (exclude or set())}
        end = time.time() + timeout
        while time.time() < end:
            mails = self.read()
            branded, other = [], []
            for m in mails:
                text = " ".join(str(m.get(k) or "") for k in ("subject", "text", "body", "html", "source"))
                (branded if vendor and vendor.lower() in text.lower() else other).append(text)
            for text in branded + other:
                c = _code_from(text)
                if c and c not in skip:
                    return c
            time.sleep(interval)
        return None

    def confirm(self) -> None:
        if self._confirm:
            try:
                self._confirm()
            except Exception:
                pass


# ── provider: tempik ─────────────────────────────────────────────
def _tempik(domain: str = "kancalabs.my.id", local_part: str = "", log=print) -> Mailbox:
    import json
    import urllib.parse
    import urllib.request

    def _req(path: str, method: str = "GET", body: dict | None = None, sid: str = "") -> dict | list:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{TEMPIK_BASE}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "Mozilla/5.0 (compatible; KancaHub/1.0)",
                                              "Origin": TEMPIK_BASE, "Referer": TEMPIK_BASE + "/"})
        if sid:
            req.add_header("x-session-id", sid)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode() or "{}")

    sid = (_req("/api/session") or {}).get("sessionId", "")
    created = _req("/api/inboxes", "POST", {"localPart": local_part, "domain": domain}, sid)
    address = created.get("address") or f"{local_part}@{domain}"

    def read() -> list[dict]:
        try:
            return _req(f"/api/inboxes/{urllib.parse.quote(address, safe='')}/messages", sid=sid) or []
        except Exception:
            return []

    return Mailbox(address, read)


# ── provider: relay (KancaHub Supabase) ─────────────────────────
def _relay(domain: str = "kancalabs.biz.id", log=print) -> Mailbox:
    import github_farm as g
    mb = g.create_relay_mailbox(domain=domain)
    jwt = mb.get("jwt", "")
    return Mailbox(mb["email"], lambda: g.poll_relay_inbox(jwt))


# ── provider: litensi (paid email activation) ───────────────────
def _litensi(site: str, log=print) -> Mailbox:
    import otp_litensi
    cli = otp_litensi.LitensiClient()
    email, order_id = cli.create_mailbox()
    return Mailbox(email,
                   lambda: [cli.get_status(cli.last_order_id or order_id)],
                   confirm=lambda: cli.mark_success(cli.last_order_id or order_id))


# ── provider: static ────────────────────────────────────────────
def _static(domain: str, local_part: str = "", log=print) -> Mailbox:
    return Mailbox(f"{local_part or 'usr'}@{domain}", lambda: [])


_PROVIDERS = {"tempik": _tempik, "relay": _relay, "litensi": _litensi, "static": _static}


def open_mailbox(provider: str = "tempik", *, domain: str = "", local_part: str = "",
                 site: str = "", log=print) -> Mailbox:
    """Create/attach a mailbox. Falls back to a static address on failure."""
    provider = (provider or "tempik").strip().lower()
    fn = _PROVIDERS.get(provider)
    if fn is None:
        log(f"  [mail] unknown provider {provider!r}; using a static address")
        return _static(domain or "kancalabs.my.id", local_part, log)
    try:
        if provider == "tempik":
            return _tempik(domain or "kancalabs.my.id", local_part, log)
        if provider == "relay":
            return _relay(domain or "kancalabs.biz.id", log)
        if provider == "litensi":
            return _litensi(site, log)
        return _static(domain or "kancalabs.my.id", local_part, log)
    except Exception as exc:  # network/keys/stock — never fatal to a farm run
        log(f"  [mail] {provider} unavailable ({exc}); using a static address")
        return _static(domain or "kancalabs.my.id", local_part, log)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Probe a disposable-mail provider")
    ap.add_argument("provider", nargs="?", default="tempik", choices=sorted(_PROVIDERS))
    ap.add_argument("--domain", default="")
    ap.add_argument("--site", default="github.com")
    a = ap.parse_args()
    mb = open_mailbox(a.provider, domain=a.domain, site=a.site)
    print("address:", mb.address)
    print("mail:", mb.read())