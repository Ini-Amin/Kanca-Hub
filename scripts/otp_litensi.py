#!/usr/bin/env python3
"""
scripts/otp_litensi.py — Litensi email-activation (OTP) client.

Litensi sells per-activation mailboxes (pay-as-you-go, no monthly): order a
mailbox for a site, poll for the code, confirm when used, reorder to extend an
expiring window. Cheap option for signups that need an email OTP.

Ported from github-regkit-mibp/github_register/litensi.py (API contract
unchanged). Credentials come from the environment / ~/.config/auto-freecf/.env:
  LITENSI_API_ID, LITENSI_API_KEY, LITENSI_SITE (default github.com), LITENSI_ZONE

Usage (any venv — requests only):
  python scripts/otp_litensi.py profile
  python scripts/otp_litensi.py prices --site github.com
  python scripts/otp_litensi.py order  --site github.com
  python scripts/otp_litensi.py wait   --order-id 12345 --email a@b.com
  python scripts/otp_litensi.py done   --order-id 12345
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

import requests

API_BASE = "https://litensi.id/api/mail"
PROFILE_BASE = "https://litensi.id/api/profile"
ENV_FILE = Path.home() / ".config" / "auto-freecf" / ".env"


class LitensiError(RuntimeError):
    pass


def _env() -> dict:
    data: dict[str, str] = {}
    try:
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                data[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return data


def _cfg(key: str, default: str = "") -> str:
    return (os.environ.get(key) or _env().get(key) or default).strip()


HINTS = {
    "BAD SITE": " — LITENSI_SITE must be a domain (e.g. github.com)",
    "BAD API": " — check LITENSI_API_ID / LITENSI_API_KEY",
    "BAD API ID": " — LITENSI_API_ID is invalid or inactive",
    "BAD API KEY": " — LITENSI_API_KEY is invalid",
    "BAD ZONE": " — zone unavailable (leave blank for auto-pick)",
    "OUT OF STOCK": " — mailbox stock is empty for this zone/site",
    "NOT ENOUGH BALANCE": " — Litensi balance is insufficient",
    "IP NOT ALLOWED": " — this server IP is not whitelisted in the Litensi dashboard",
}


class LitensiClient:
    def __init__(self, api_id: str = "", api_key: str = "", site: str = "", zone: str = ""):
        self.api_id = api_id or _cfg("LITENSI_API_ID")
        self.api_key = api_key or _cfg("LITENSI_API_KEY")
        self.site = site or _cfg("LITENSI_SITE", "github.com")
        self.zone = zone or _cfg("LITENSI_ZONE")
        if not self.api_id or not self.api_key:
            raise LitensiError("LITENSI_API_ID / LITENSI_API_KEY not set")
        if not self.site:
            raise LitensiError("LITENSI_SITE not set")
        self.session = requests.Session()
        self._last_order_id = ""

    def _post(self, path: str, data: dict, base: Optional[str] = None) -> dict:
        url = f"{base or API_BASE}/{path}" if path else (base or API_BASE)
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = self.session.post(url, data=data, timeout=30)
                break
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(2 * (attempt + 1))
        else:
            raise LitensiError(f"litensi {url} unreachable: {last_exc}")
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if not resp.ok or not (payload and payload.get("success")):
            reason = payload.get("data") if isinstance(payload, dict) else None
            hint = HINTS.get(str(reason).strip().upper(), "")
            raise LitensiError(
                f"litensi {path or 'profile'} failed (HTTP {resp.status_code}): "
                f"{reason or payload or resp.text[:200]}{hint}")
        return payload.get("data") if payload.get("data") is not None else {}

    def profile(self) -> dict:
        return self._post("", {"api_id": self.api_id, "api_key": self.api_key}, base=PROFILE_BASE)

    def prices(self) -> list[dict]:
        data = self._post("prices", {"api_id": self.api_id, "api_key": self.api_key, "site": self.site})
        return data if isinstance(data, list) else []

    def pick_zone(self) -> str:
        stock = [z for z in self.prices() if float(z.get("stock") or 0) > 0]
        if not stock:
            raise LitensiError(f"no zones in stock for site {self.site!r}")
        return min(stock, key=lambda z: float(z.get("price") or 0))["zone"]

    def create_mailbox(self) -> tuple[str, str]:
        zone = self.zone or self.pick_zone()
        data = self._post("order", {"api_id": self.api_id, "api_key": self.api_key,
                                    "zone": zone, "site": self.site})
        email, order_id = data.get("email"), data.get("order_id")
        if not email or order_id is None:
            raise LitensiError(f"litensi order bad response: {data}")
        self._last_order_id = str(order_id)
        return email, str(order_id)

    def get_status(self, order_id: str) -> dict:
        return self._post("getstatus", {"api_id": self.api_id, "api_key": self.api_key, "order_id": order_id})

    def set_status(self, order_id: str, status: str) -> dict:
        if status not in ("SUCCESS", "CANCELED"):
            raise LitensiError(f"invalid setstatus value: {status}")
        return self._post("setstatus", {"api_id": self.api_id, "api_key": self.api_key,
                                        "order_id": order_id, "status": status})

    def mark_success(self, order_id: str) -> dict:
        return self.set_status(order_id, "SUCCESS")

    def cancel(self, order_id: str) -> dict:
        return self.set_status(order_id, "CANCELED")

    def reorder(self, email: str) -> dict:
        return self._post("reorder", {"api_id": self.api_id, "api_key": self.api_key,
                                      "site": self.site, "email": email})

    def wait_for_code(self, order_id: str, email: str = "", timeout: int = 240,
                      poll_interval: int = 5, reorder_after: int = 150,
                      log: Optional[Callable[[str], None]] = None,
                      exclude_codes: Optional[Iterable[str]] = None) -> str:
        poll_interval = max(5, poll_interval)  # litensi: >= 5s between getstatus
        started = time.time()
        current = str(order_id)
        reordered_at: Optional[float] = None
        reorder_disabled = False
        skip = {str(c).strip() for c in (exclude_codes or ()) if str(c).strip()}
        while time.time() - started < timeout:
            try:
                data = self.get_status(current)
            except Exception as exc:
                msg = str(exc).lower()
                if email and not reorder_disabled and (
                        "activation does not exist" in msg or "email activation expired" in msg):
                    try:
                        data = self.reorder(email)
                        current = str(data.get("order_id") or current)
                        reordered_at = time.time()
                        continue
                    except Exception as re:
                        reorder_disabled = True
                        if log:
                            log(f"[!] reorder failed permanently: {re}")
                if log:
                    log(f"[!] getstatus failed: {exc}")
                time.sleep(poll_interval)
                continue
            if str(data.get("status") or "") == "CANCELED":
                raise LitensiError("litensi order canceled")
            text = "\n".join(x for x in (data.get("message", ""), data.get("full_message", "")) if x)
            code = extract_code(text)
            if code and code not in skip:
                self._last_order_id = current
                return code
            elapsed = time.time() - (reordered_at or started)
            if email and not reorder_disabled and elapsed >= reorder_after:
                try:
                    data = self.reorder(email)
                    current = str(data.get("order_id") or current)
                    reordered_at = time.time()
                except Exception as exc:
                    reorder_disabled = True
                    if log:
                        log(f"[!] reorder failed: {exc}")
            time.sleep(poll_interval)
        raise LitensiError(f"no code after {timeout}s")

    @property
    def last_order_id(self) -> str:
        return self._last_order_id


def extract_code(text: str, patterns: Optional[list[str]] = None) -> Optional[str]:
    """First code in the message. Default: GitHub's XXXX-XXXX, else 4-8 digits."""
    if not text:
        return None
    plain = re.sub(r"<[^>]+>", " ", text)
    for pat in (patterns or [r"(\d{4})-(\d{4})", r"\b(\d{6,8})\b", r"\b(\d{4})\b"]):
        m = re.search(pat, plain)
        if m:
            return "".join(m.groups()) if m.groups() else m.group(0)
    return None


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Litensi email-activation (OTP) client")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("profile", help="show balance / account")
    p = sub.add_parser("prices", help="list zones + stock for a site")
    p.add_argument("--site", default=None)
    o = sub.add_parser("order", help="order a mailbox")
    o.add_argument("--site", default=None)
    o.add_argument("--zone", default=None)
    w = sub.add_parser("wait", help="poll for the code")
    w.add_argument("--order-id", required=True)
    w.add_argument("--email", default="")
    w.add_argument("--timeout", type=int, default=240)
    d = sub.add_parser("done", help="mark an order SUCCESS (code used)")
    d.add_argument("--order-id", required=True)
    c = sub.add_parser("cancel", help="cancel an order")
    c.add_argument("--order-id", required=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        cli = LitensiClient(site=getattr(a, "site", "") or "")
        if a.cmd == "profile":
            print(cli.profile())
        elif a.cmd == "prices":
            for z in cli.prices():
                print(f"  {z.get('zone'):<20} stock={z.get('stock')} price={z.get('price')}")
        elif a.cmd == "order":
            email, oid = cli.create_mailbox()
            print(f"  email={email}  order_id={oid}")
        elif a.cmd == "wait":
            code = cli.wait_for_code(a.order_id, email=a.email, timeout=a.timeout, log=print)
            print(f"  CODE={code}  (confirm with: done --order-id {cli.last_order_id})")
        elif a.cmd == "done":
            print(cli.mark_success(a.order_id))
        elif a.cmd == "cancel":
            print(cli.cancel(a.order_id))
    except LitensiError as e:
        print(f"  ✗ {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())