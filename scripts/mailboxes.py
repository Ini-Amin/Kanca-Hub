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

    def link(self, pattern: str, timeout: float = 180.0, interval: float = 4.0) -> Optional[str]:
        """Poll for a URL matching `pattern` (click-through verify flows)."""
        rx = re.compile(pattern)
        end = time.time() + timeout
        while time.time() < end:
            for m in self.read():
                blob = " ".join(str(m.get(k) or "") for k in ("subject", "text", "body", "html", "source", "message"))
                hit = rx.search(blob)
                if hit:
                    return hit.group(0)
            time.sleep(interval)
        return None


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


# ── provider: emailmux (real @gmail.com) ────────────────────────
EMAILMUX_BASE = "https://emailmux.com"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36")


def _json_req(url: str, method: str = "GET", body: dict | None = None,
              headers: dict | None = None, timeout: int = 30):
    import json as _json
    import urllib.request
    data = _json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _json.loads(r.read().decode() or "{}")


def _emailmux(local_part: str = "", log=print, api_key: str = "", domain: str = "gmail") -> Mailbox:
    """Real @gmail.com inboxes on demand (public endpoints, or Bearer API with a key)."""
    import urllib.parse
    hdr = {"Content-Type": "application/json", "Accept": "application/json, text/plain, */*",
           "Accept-Language": "en-US,en;q=0.9", "User-Agent": _UA,
           "Origin": EMAILMUX_BASE, "Referer": EMAILMUX_BASE + "/"}
    if api_key:
        hdr["Authorization"] = f"Bearer {api_key}"
    if api_key:
        data = _json_req(f"{EMAILMUX_BASE}/api/random", "POST", {"domian": [domain]}, hdr)
        email = data.get("address") or data.get("email") or (data.get("data") or {}).get("address")
    else:
        data = _json_req(f"{EMAILMUX_BASE}/generate-email", "POST", {"domains": [domain]}, hdr)
        email = data.get("email") or data.get("address") or (data.get("data") or {}).get("email")
    if not email:
        raise RuntimeError(f"emailmux: no address in response ({str(data)[:120]})")
    if "@gmail.com" not in email.lower() and domain == "gmail":
        raise RuntimeError(f"emailmux: expected @gmail.com, got {email}")

    def read() -> list[dict]:
        if api_key:
            d = _json_req(f"{EMAILMUX_BASE}/api/emails", "POST", {"address": email}, hdr)
            m = d.get("email") or d.get("message") or d.get("data")
            return [m] if isinstance(m, dict) else []
        d = _json_req(f"{EMAILMUX_BASE}/emails?email={urllib.parse.quote(email)}", headers=hdr)
        return d.get("emails") or d.get("messages") or d.get("data") or []

    return Mailbox(email, read)


# ── provider: emailnator (real @gmail.com) ──────────────────────
EMAILNATOR_BASE = "https://www.emailnator.com"


def _emailnator(local_part: str = "", log=print) -> Mailbox:
    hdr = {"Content-Type": "application/json", "Accept": "application/json, text/plain, */*",
           "Accept-Language": "en-US,en;q=0.9", "User-Agent": _UA,
           "Origin": "https://www.emailnator.com", "Referer": "https://www.emailnator.com/"}
    data = _json_req(f"{EMAILNATOR_BASE}/api/generate-email", "POST",
                     {"ids": ["domain", "plusGmail"]}, hdr)
    email = (data.get("email") or data.get("address") or (data.get("data") or {}).get("email")
             or (data[0] if isinstance(data, list) else None))
    if not email:
        raise RuntimeError(f"emailnator: no address ({str(data)[:120]})")

    def read() -> list[dict]:
        d = _json_req(f"{EMAILNATOR_BASE}/api/message-list", "POST",
                      {"email": email, "limit": 20}, hdr)
        mails = d.get("messages") or d.get("data") or []
        out = []
        for m in mails:
            if not (m.get("body") or m.get("html") or m.get("message")):
                mid = m.get("messageID") or m.get("id") or m.get("_id")
                if mid:
                    try:
                        m = _json_req(f"{EMAILNATOR_BASE}/api/message/{mid}", "POST", {}, hdr).get("message") or m
                    except Exception:
                        pass
            out.append(m)
        return out

    return Mailbox(email, read)


# ── provider: mail.tm (classic temp-mail REST) ──────────────────
MAILTM_BASE = "https://api.mail.tm"


def _mailtm(local_part: str = "", log=print, domain: str = "") -> Mailbox:
    import json as _json
    import secrets
    import urllib.request

    def api(path: str, method: str = "GET", body: dict | None = None, token: str = ""):
        hdr = {"Accept": "application/json"}
        if body is not None:
            hdr["Content-Type"] = "application/json"
        if token:
            hdr["Authorization"] = f"Bearer {token}"
        data = _json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{MAILTM_BASE}{path}", data=data, method=method, headers=hdr)
        with urllib.request.urlopen(req, timeout=30) as r:
            return _json.loads(r.read().decode() or "{}")

    doms = api("/domains")
    lst = doms if isinstance(doms, list) else doms.get("hydra:member", [])
    dom = domain or next((x["domain"] for x in lst if x.get("isActive", True) and not x.get("isPrivate")), None) \
        or (lst[0]["domain"] if lst else None)
    if not dom:
        raise RuntimeError("mail.tm: no active domain")
    addr = f"{local_part or 'm' + secrets.token_hex(5)}@{dom}"
    pw = secrets.token_hex(8) + "Aa1!"
    api("/accounts", "POST", {"address": addr, "password": pw})
    tok = api("/token", "POST", {"address": addr, "password": pw}).get("token", "")

    def read() -> list[dict]:
        d = api("/messages", token=tok)
        lst2 = d if isinstance(d, list) else d.get("hydra:member", [])
        out = []
        for m in lst2:
            if not (m.get("text") or m.get("html")) and m.get("id"):
                try:
                    m = api(f"/messages/{m['id']}", token=tok)
                except Exception:
                    pass
            out.append(m)
        return out

    return Mailbox(addr, read)


# ── provider: gmail chain (real @gmail via emailmux -> emailnator -> mail.tm) ──
def _gmail_chain(local_part: str = "", log=print) -> Mailbox:
    for name, fn in (("emailmux", _emailmux), ("emailnator", _emailnator), ("mailtm", _mailtm)):
        try:
            mb = fn(local_part=local_part, log=log)
            log(f"  [mail] {name} -> {mb.address}")
            return mb
        except Exception as exc:
            log(f"  [mail] {name} unavailable ({str(exc)[:80]})")
    raise RuntimeError("gmail chain exhausted (emailmux, emailnator, mail.tm)")


_PROVIDERS = {"tempik": _tempik, "relay": _relay, "litensi": _litensi, "static": _static,
              "emailmux": _emailmux, "emailnator": _emailnator, "mailtm": _mailtm,
              "gmail": _gmail_chain}


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
        if provider == "emailmux":
            return _emailmux(local_part, log)
        if provider == "emailnator":
            return _emailnator(local_part, log)
        if provider == "mailtm":
            return _mailtm(local_part, log, domain)
        if provider == "gmail":
            return _gmail_chain(local_part, log)
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
        print("  tempik     kancalabs.my.id   — free, readable; UI shows only this browser's inboxes")
        print("  relay      kancalabs.biz.id  — the relay (Litensi account codes land here)")
        print("  litensi    paid email activation (needs keys/balance)")
        print("  gmail      chain        — real @gmail.com: emailmux -> emailnator -> mail.tm (fallback)")
        print("  emailmux   real @gmail.com   — public endpoints (per-IP rate-limited) or Bearer key")
        print("  emailnator real @gmail.com   — generate-email + message-list")
        print("  mailtm     temp-mail REST    — domains/accounts/token/messages")
        print("  static     no inbox")
        print("  relay-account  read the saved Litensi account inbox directly")
        print("  list-inboxes   list every CLI inbox in the persisted Tempik session")
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