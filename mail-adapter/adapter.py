#!/usr/bin/env python3
"""
Mail Adapter — Supabase temp-mail API → bluk-cf compatible format.

bluk-cf expects:
  POST /api/new_address    {domain}        → {address, jwt}
  GET  /parsed_mails       Bearer {jwt}    → [mail, ...]
  GET  /parsed_mail/{id}   Bearer {jwt}    → {mail}

Supabase API (what we have):
  POST ?action=create      {domain} + x-api-key          → {address, owner_token}
  GET  ?action=messages    {owner_token, address} + x-api-key → {messages: [...]}
  GET  ?action=message     {owner_token, message_id} + x-api-key → {mail}

This adapter translates between the two, storing owner_token↔address mappings.
"""

import json
import os
import time
from urllib.parse import urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests

API_BASE = os.environ.get("API_BASE", "https://ijrccpgiulrmfpavazsl.supabase.co/functions/v1/temp-mail-api")

# No baked-in fallback: a key committed to the repo is a key that must be rotated.
# Set TMK_KEY in the environment (or in a local, git-ignored .env) before starting.
API_KEY = os.environ.get("TMK_KEY", "").strip()
if not API_KEY:
    raise SystemExit(
        "TMK_KEY is not set.\n"
        "  export TMK_KEY=tmk_...   (get it from the temp-mail provider dashboard)\n"
        "Never commit the key — mail-adapter/config.json and .env are git-ignored."
    )

HEADERS  = {"x-api-key": API_KEY, "Content-Type": "application/json"}

# Mapping: jwt (owner_token) → {"owner_token": ..., "address": ...}
MAP_FILE = os.path.join(os.path.dirname(__file__), "token_map.json")

def _load_map():
    try:
        with open(MAP_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_map(data):
    with open(MAP_FILE, "w") as f:
        json.dump(data, f)

TOKEN_MAP = _load_map()

# Fallback list of domains if /domains fails
DEFAULT_DOMAINS = [
    "mmoaa.org", "gmilio.web.id", "kintole.com",
    "moymoy.me", "moyqris.me", "membleh.me",
    "openfile.my.id", "neorastorepl.my.id", "hapusmbg.biz.id",
    "turunkanprabowo.my.id"
]


def supabase_create(domain: str) -> dict:
    """Create email via Supabase API."""
    r = requests.post(
        f"{API_BASE}?action=create",
        headers=HEADERS,
        json={"domain": domain},
        timeout=30
    )
    r.raise_for_status()
    data = r.json()
    # Store mapping for later lookups
    TOKEN_MAP[data["owner_token"]] = {
        "owner_token": data["owner_token"],
        "address": data["address"],
    }
    _save_map(TOKEN_MAP)
    return data


def supabase_messages(owner_token: str, address: str) -> list:
    """Get messages for an address."""
    r = requests.get(
        f"{API_BASE}?action=messages",
        headers=HEADERS,
        params={"owner_token": owner_token, "address": address},
        timeout=30
    )
    r.raise_for_status()
    data = r.json()
    return data.get("messages", []) if isinstance(data, dict) else []


def supabase_message(owner_token: str, message_id) -> dict:
    """Get single message content."""
    r = requests.get(
        f"{API_BASE}?action=message",
        headers=HEADERS,
        params={"owner_token": owner_token, "id": message_id},
        timeout=30
    )
    r.raise_for_status()
    data = r.json()
    # Supabase returns {"message": {...}}
    msg = data.get("message", data)
    return msg


def _normalize_mail(raw: dict, mail_id: str = "") -> dict:
    """Normalize Supabase mail fields to bluk-cf format."""
    from_addr = raw.get("from_address", raw.get("from", ""))
    from_name = raw.get("from_name", "")
    if from_name and from_addr:
        from_field = f"{from_name} <{from_addr}>"
    elif from_addr:
        from_field = from_addr
    else:
        from_field = from_name or ""
    text = raw.get("text_body", raw.get("text", raw.get("plain", raw.get("body", ""))))
    html_content = raw.get("html_body", raw.get("html", ""))
    return {
        "id": mail_id or raw.get("id", raw.get("message_id", "")),
        "from": from_field,
        "sender": from_field,
        "subject": raw.get("subject", ""),
        "text": text,
        "html": html_content,
        "body": text,
        "snippet": text[:200] if text else "",
        "date": raw.get("received_at", raw.get("date", "")),
    }


def supabase_domains() -> list:
    """Get available domains."""
    try:
        r = requests.get(
            f"{API_BASE}?action=domains",
            headers=HEADERS,
            timeout=15
        )
        r.raise_for_status()
        data = r.json()
        return data.get("domains", [])
    except Exception:
        return DEFAULT_DOMAINS


class AdapterHandler(BaseHTTPRequestHandler):
    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, msg, status=400):
        self._json({"error": msg}, status)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        body = self.rfile.read(length)
        return json.loads(body) if body else {}

    def _get_jwt(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return None

    def _get_token_info(self):
        """Resolve JWT → {owner_token, address}."""
        jwt = self._get_jwt()
        if not jwt:
            return None
        
        # Try embedded format: owner_token::address
        if "::" in jwt:
            parts = jwt.split("::", 1)
            return {"owner_token": parts[0], "address": parts[1]}
        
        # Try in-memory/file cache
        if jwt in TOKEN_MAP:
            return TOKEN_MAP[jwt]
        
        # Fallback: use JWT as owner_token, address unknown
        return {"owner_token": jwt, "address": None}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/api/new_address":
            domain = body.get("domain", "gmilio.web.id")
            try:
                data = supabase_create(domain)
                self._json({
                    "address": data["address"],
                    "jwt": f"{data['owner_token']}::{data['address']}",
                    "domain": data.get("domain", domain),
                })
            except Exception as e:
                self._error(str(e), 500)
        else:
            self._error("Not found", 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        # Strip /api prefix (bluk-cf uses /api/parsed_mails)
        if path.startswith("/api/"):
            path = path[4:]
        params = parse_qs(parsed.query)

        if path == "/domains":
            try:
                self._json({"domains": supabase_domains()})
            except Exception as e:
                self._error(str(e), 500)
            return

        if path == "/parsed_mails":
            ti = self._get_token_info()
            if not ti:
                self._error("Missing or invalid Authorization", 401)
                return
            try:
                msgs = supabase_messages(ti["owner_token"], ti["address"])
                # Transform to parsed_mails format
                results = []
                for m in msgs:
                    mid = m.get("message_id") or m.get("id")
                    full = supabase_message(ti["owner_token"], mid)
                    results.append(_normalize_mail(full, mid))
                self._json(results)
            except Exception as e:
                self._error(str(e), 500)
            return

        # /parsed_mail/<id>
        if path.startswith("/parsed_mail/"):
            ti = self._get_token_info()
            if not ti:
                self._error("Missing or invalid Authorization", 401)
                return
            mail_id = path.split("/")[-1]
            try:
                full = supabase_message(ti["owner_token"], mail_id)
                self._json(_normalize_mail(full, mail_id))
            except Exception as e:
                self._error(str(e), 500)
            return

        self._error("Not found", 404)

    def log_message(self, format, *args):
        """Quiet logging."""
        pass


def main():
    port = int(os.environ.get("PORT", "9877"))
    server = HTTPServer(("0.0.0.0", port), AdapterHandler)
    print(f"Mail adapter running on port {port}")
    print(f"Backend: {API_BASE}")
    server.serve_forever()


if __name__ == "__main__":
    main()
