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
TEMPIK_SESSION_FILE = Path.home() / ".config" / "auto-freecf" / "tempik_session.json"


def _tempik_req(path: str, method: str = "GET", body: dict | None = None, sid: str = ""):
    import json
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{TEMPIK_BASE}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "Mozilla/5.0 (compatible; KancaHub/1.0)",
                                          "Origin": TEMPIK_BASE, "Referer": TEMPIK_BASE + "/"})
    if sid:
        req.add_header("x-session-id", sid)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode() or "{}")


def tempik_session_id(*, fresh: bool = False) -> str:
    """A PERSISTED Tempik session so every CLI inbox lands in one list.

    The website keeps its session in localStorage and makes a new one per
    browser; the CLI used to do the same per run, so inboxes never accumulated.
    Reusing one saved id here means all CLI-created inboxes show together — and
    can be viewed on the website by pasting this id into localStorage.
    """
    import json
    if not fresh and TEMPIK_SESSION_FILE.exists():
        try:
            sid = json.loads(TEMPIK_SESSION_FILE.read_text()).get("sessionId", "")
            if sid:
                return sid
        except Exception:
            pass
    sid = (_tempik_req("/api/session") or {}).get("sessionId", "")
    if sid:
        TEMPIK_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        TEMPIK_SESSION_FILE.write_text(json.dumps({"sessionId": sid}))
    return sid


def _tempik(domain: str = "kancalabs.my.id", local_part: str = "", log=print) -> Mailbox:
    import urllib.parse
    sid = tempik_session_id()
    created = _tempik_req("/api/inboxes", "POST", {"localPart": local_part, "domain": domain}, sid)
    address = created.get("address") or f"{local_part}@{domain}"

    def read() -> list[dict]:
        try:
            return _tempik_req(f"/api/inboxes/{urllib.parse.quote(address, safe='')}/messages", sid=sid) or []
        except Exception:
            return []

    return Mailbox(address, read)


def tempik_list() -> list[dict]:
    """All inboxes in the persisted CLI session (so nothing is 'lost')."""
    sid = tempik_session_id()
    try:
        return _tempik_req("/api/inboxes", sid=sid) or []
    except Exception:
        return []


# ── provider: relay (KancaHub Supabase) ─────────────────────────
def _relay(domain: str = "kancalabs.biz.id", log=print) -> Mailbox:
    import github_farm as g
    mb = g.create_relay_mailbox(domain=domain)
    jwt = mb.get("jwt", "")
    return Mailbox(mb["email"], lambda: g.poll_relay_inbox(jwt))


def relay_inbox(jwt: str, address: str = "") -> Mailbox:
    """Read an EXISTING relay inbox (biz.id) by its JWT — e.g. the Litensi account."""
    import github_farm as g
    return Mailbox(address or "(relay inbox)", lambda: g.poll_relay_inbox(jwt))


def _litensi_account_jwt() -> tuple[str, str]:
    """(jwt, email) of the saved Litensi account's relay inbox, or ('','')."""
    import json
    try:
        d = json.loads((Path.home() / ".config" / "auto-freecf" / "litensi_account.json").read_text())
        return d.get("relay_jwt", ""), d.get("email", "")
    except Exception:
        return "", ""


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


def route_for(domain: str) -> str:
    """Which backend serves a domain. Tempik (the Worker) is my.id ONLY; the
    relay serves my.id AND biz.id. So biz.id must go through the relay."""
    d = (domain or "").lower()
    if d.endswith("kancalabs.biz.id"):
        return "relay"
    if d.endswith("kancalabs.my.id"):
        return "tempik"
    return "relay"  # any other domain → the relay (supports arbitrary domains here)


def open_mailbox(provider: str = "auto", *, domain: str = "", local_part: str = "",
                 site: str = "", log=print) -> Mailbox:
    """Create/attach a mailbox. Falls back to a static address on failure.

    provider="auto" routes by domain: my.id -> tempik, biz.id -> relay, so the
    caller never has to know which backend owns which domain.
    """
    provider = (provider or "auto").strip().lower()
    domain = domain or "kancalabs.my.id"
    if provider == "auto":
        provider = route_for(domain)
        log(f"  [mail] domain {domain} -> {provider} backend")
    fn = _PROVIDERS.get(provider)
    if fn is None:
        log(f"  [mail] unknown provider {provider!r}; using a static address")
        return _static(domain, local_part, log)
    try:
        if provider == "tempik":
            if domain.lower().endswith("biz.id"):
                log("  [mail] Tempik only serves kancalabs.my.id — routing biz.id via the relay")
                provider = "relay"
            else:
                return _tempik(domain, local_part, log)
        if provider == "relay":
            return _relay(domain, log)
        if provider == "litensi":
            return _litensi(site, log)
        return _static(domain, local_part, log)
    except Exception as exc:  # network/keys/stock — never fatal to a farm run
        log(f"  [mail] {provider} unavailable ({exc}); using a static address")
        return _static(domain, local_part, log)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Create/read a disposable mailbox")
    ap.add_argument("provider", nargs="?", default="tempik",
                    choices=sorted(_PROVIDERS) + ["relay-account", "list-providers", "list-inboxes"],
                    help="tempik|relay|litensi|static, or relay-account (the saved Litensi inbox)")
    ap.add_argument("--domain", default="")
    ap.add_argument("--site", default="github.com")
    ap.add_argument("--local-part", default="", help="pick the name (tempik/static)")
    ap.add_argument("--jwt", default="", help="read an existing relay inbox by JWT")
    ap.add_argument("--watch", action="store_true", help="keep printing new mail until Ctrl-C")
    ap.add_argument("--timeout", type=float, default=180.0)
    a = ap.parse_args()

    if a.provider == "list-providers":
        print("  tempik  kancalabs.my.id  — free, readable; UI shows only this browser's inboxes")
        print("  relay   kancalabs.biz.id — the relay (Litensi account codes land here)")
        print("  litensi paid email activation (needs keys/balance)")
        print("  static  no inbox")
        print("  relay-account  read the saved Litensi account inbox directly")
        raise SystemExit(0)

    if a.provider == "list-inboxes":
        sid = tempik_session_id()
        inboxes = tempik_list()
        print(f"  Tempik session : {sid}")
        print(f"  inboxes        : {len(inboxes)}")
        for ib in inboxes:
            print(f"    - {ib.get('address')}   (created {ib.get('created_at','?')})")
        print("\n  To see these on the Tempik website, open DevTools (F12) → Console:")
        print(f"    localStorage.setItem('tempik_session_id','{sid}'); location.reload()")
        print("  (or: localStorage.removeItem('tempik_session_id') to get a fresh list)")
        raise SystemExit(0)

    if a.provider == "relay-account":
        jwt, email = _litensi_account_jwt()
        if not jwt:
            print("  ✗ no saved Litensi account JWT (~/.config/auto-freecf/litensi_account.json)")
            raise SystemExit(1)
        mb = relay_inbox(jwt, email)
        print(f"  reading relay inbox : {email}")
        print(f"  relay domain        : kancalabs.biz.id  (this is the Litensi code inbox)")
        print(f"  note                : the Tempik site (kancalabs.my.id) is a DIFFERENT domain\n")
    elif a.jwt:
        mb = relay_inbox(a.jwt)
    else:
        mb = open_mailbox(a.provider, domain=a.domain, local_part=a.local_part, site=a.site)
        print(f"  address : {mb.address}")
        print(f"  inbox   : https://tempik.kancalabs.workers.dev/  (this session only)\n")
    if not a.watch:
        for m in mb.read():
            print(f"  - {m.get('subject','')} :: {m.get('body','')[:120]}")
        raise SystemExit(0)
    print("  watching for mail (Ctrl-C to stop)...")
    seen: set[str] = set()
    import time as _t
    end = _t.time() + a.timeout
    try:
        while _t.time() < end:
            for m in mb.read():
                key = f"{m.get('subject','')}|{m.get('received_at','')}"
                if key in seen:
                    continue
                seen.add(key)
                print(f"\n  ✉ {m.get('from_address','?')} — {m.get('subject','')}")
                print(f"    {str(m.get('body',''))[:400]}")
            _t.sleep(3)
    except KeyboardInterrupt:
        pass