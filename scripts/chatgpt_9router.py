#!/usr/bin/env python3
"""
ChatGPT session capture + 9Router (codex provider) injection for KancaHub.

ChatGPT "K-12 teacher" accounts are authenticated *web* accounts. 9Router can
use them through its built-in `codex` provider (category "oauth"), which talks
to https://chatgpt.com/backend-api/codex/responses using the ChatGPT OAuth
session — NOT an OpenAI API key.

So the bridge is:
    ChatGPT login session  ->  {accessToken, refreshToken, idToken,
                                chatgptAccountId, chatgptPlanType}
                          ->  9Router providerConnections (provider='codex')

This module:
  * capture_session(page)      — pull the session from a live browser tab
  * inject_session(db, session)— upsert a codex connection
  * sync(db)                   — verify + prune codex connections
  * main()                     — CLI

The session comes from GET https://chatgpt.com/api/auth/session, which returns
{accessToken, idToken, expires, user, account:{id, planType}}, plus the
refresh token which lives in the `__Secure-next-auth.session-token` cookie.

Usage (standalone, after you have a session JSON):
    python3 chatgpt_9router.py inject --session session.json
    python3 chatgpt_9router.py sync --prune
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

NINE_ROUTER_HOME = Path(os.environ.get("NINE_ROUTER_HOME", Path.home() / ".9router"))
DB_PATH = NINE_ROUTER_HOME / "db" / "data.sqlite"
CODEX_PROVIDER = "codex"
SESSION_URL = "https://chatgpt.com/api/auth/session"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ─────────────────────────────────────────────────────────── capture (in-browser)

SESSION_JS = r"""
(async () => {
  try {
    const r = await fetch('/api/auth/session', { credentials: 'include' });
    const j = await r.json();
    // refresh token (httpOnly cookie, not visible to JS) is captured separately
    return JSON.stringify({
      accessToken: j.accessToken || '',
      idToken: j.idToken || '',
      expires: j.expires || '',
      userEmail: (j.user && j.user.email) || '',
      accountId: (j.account && j.account.id) || '',
      planType: (j.account && (j.account.planType || j.account.plan_type)) || ''
    });
  } catch (e) {
    return JSON.stringify({ error: String(e) });
  }
})()
"""


def capture_session(page) -> dict:
    """Extract the ChatGPT session from a nodriver/DrissionPage-like tab.

    Works with objects exposing either `evaluate(js, await_promise=True)` (nodriver)
    or `run_js(js)` (DrissionPage). Cookies are read from the browser for the
    refresh token.
    """
    raw = None
    try:
        if hasattr(page, "evaluate"):
            raw = page.evaluate(SESSION_JS, await_promise=True, return_by_value=True)
        elif hasattr(page, "run_js"):
            raw = page.run_js(SESSION_JS)
    except Exception as e:  # noqa: BLE001
        return {"error": f"evaluate failed: {e}"}

    if isinstance(raw, dict) and "value" in raw:
        raw = raw["value"]
    if isinstance(raw, str):
        try:
            sess = json.loads(raw)
        except Exception:
            sess = {"error": f"unparsable session: {raw[:120]}"}
    else:
        sess = raw or {}

    # refresh token from cookies
    try:
        cookies = None
        if hasattr(page, "cookies"):
            cookies = page.cookies
        elif hasattr(page, "get_cookies"):
            cookies = page.get_cookies()
        if cookies:
            for c in cookies:
                name = c.get("name") if isinstance(c, dict) else getattr(c, "name", "")
                val = c.get("value") if isinstance(c, dict) else getattr(c, "value", "")
                if name in ("__Secure-next-auth.session-token",
                            "next-auth.session-token",
                            "__Secure-authjs.session-token"):
                    sess["refreshToken"] = val
    except Exception:
        pass

    return sess


# ─────────────────────────────────────────────────────────── session JSON helpers

def normalize(sess: dict) -> dict | None:
    """Map a raw session dict to the 9Router codex connection fields."""
    at = sess.get("accessToken") or sess.get("access_token")
    if not at:
        return None
    rt = sess.get("refreshToken") or sess.get("refresh_token") or ""
    idt = sess.get("idToken") or sess.get("id_token") or ""
    exp_iso = sess.get("expires") or ""
    expires_at = exp_iso
    expires_in = 864000
    try:
        if exp_iso:
            dt = datetime.fromisoformat(exp_iso.replace("Z", "+00:00"))
            expires_at = dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            expires_in = int((dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        pass
    return {
        "email": sess.get("userEmail") or sess.get("email") or "",
        "accessToken": at,
        "refreshToken": rt,
        "idToken": idt,
        "expiresAt": expires_at,
        "expiresIn": max(expires_in, 0),
        "chatgptAccountId": sess.get("accountId") or sess.get("chatgptAccountId") or "",
        "chatgptPlanType": sess.get("planType") or sess.get("chatgptPlanType") or "free",
    }


# ─────────────────────────────────────────────────────────── 9Router DB

def backup_db(db: Path) -> Path | None:
    if not db.exists():
        return None
    dest = db.with_suffix(f".sqlite.chatgpt-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(db, dest)
    return dest


def upsert(con: sqlite3.Connection, s: dict, dry_run: bool) -> str:
    cur = con.cursor()
    ts = now_iso()
    name = s["email"] or f"k12-{s['chatgptAccountId'][:8] or uuid.uuid4().hex[:8]}"
    data = {
        "accessToken": s["accessToken"],
        "refreshToken": s["refreshToken"],
        "expiresAt": s["expiresAt"],
        "testStatus": "active",
        "expiresIn": s["expiresIn"],
        "idToken": s["idToken"],
        "lastRefreshAt": ts,
        "providerSpecificData": {
            "chatgptAccountId": s["chatgptAccountId"],
            "chatgptPlanType": s["chatgptPlanType"],
        },
    }
    existing = None
    for cid, cdata in cur.execute("SELECT id, data FROM providerConnections WHERE provider=?", (CODEX_PROVIDER,)):
        if s["accessToken"] and s["accessToken"] in (cdata or ""):
            existing = cid
            break
        if name and f'"{name}"' in (cdata or "") and "email" not in (cdata or ""):
            pass
    if existing:
        if not dry_run:
            cur.execute("UPDATE providerConnections SET data=?, isActive=1, updatedAt=? WHERE id=?",
                        (json.dumps(data), ts, existing))
        print(f"  ~ updated  {name}")
        return existing
    new_id = f"chatgpt-{uuid.uuid4()}"
    if not dry_run:
        cur.execute(
            "INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (new_id, CODEX_PROVIDER, "oauth", name, s["email"] or None, 1, 1,
             json.dumps(data), ts, ts),
        )
    print(f"  + inserted {name}  (plan={s['chatgptPlanType']})")
    return new_id


def cmd_inject(a) -> int:
    db = Path(a.db)
    if not db.exists():
        print(f"✗ 9Router DB not found: {db}", file=sys.stderr)
        return 1
    p = Path(a.session)
    if not p.exists():
        print(f"✗ session file not found: {p}", file=sys.stderr)
        return 1
    raw = json.loads(p.read_text())
    sessions = raw if isinstance(raw, list) else [raw]
    norm = [n for n in (normalize(s) for s in sessions) if n]
    print(f"Loaded {len(sessions)} session(s), {len(norm)} usable.")
    if not norm:
        print("Nothing to inject (no accessToken).")
        return 1
    if not a.dry_run:
        b = backup_db(db)
        if b:
            print(f"Backup: {b}")
    con = sqlite3.connect(db)
    try:
        for s in norm:
            upsert(con, s, a.dry_run)
        if not a.dry_run:
            con.commit()
    finally:
        con.close()
    print(f"\n{'would inject' if a.dry_run else 'Injected'} {len(norm)} ChatGPT (codex) connection(s).")
    return 0


def _probe(s: dict) -> bool:
    """Best-effort liveness: hit the codex usage endpoint with the access token."""
    if not s.get("accessToken"):
        return False
    req = urllib.request.Request(CODEX_USAGE_URL, headers={
        "Authorization": f"Bearer {s['accessToken']}",
        "accept": "application/json",
        "originator": "codex_cli_rs",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        return e.code == 200
    except Exception:
        return False


def cmd_sync(a) -> int:
    db = Path(a.db)
    if not db.exists():
        print(f"✗ 9Router DB not found: {db}", file=sys.stderr)
        return 1
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = list(con.execute("SELECT id, name, data FROM providerConnections WHERE provider=?", (CODEX_PROVIDER,)))
    print(f"Testing {len(rows)} codex connection(s)…")
    dead = []
    for r in rows:
        try:
            dd = json.loads(r["data"])
        except Exception:
            dead.append(r["id"]); continue
        ok = _probe(dd)
        print(f"  {'✅' if ok else '❌'} {r['name'] or r['id']:34s} plan={dd.get('providerSpecificData',{}).get('chatgptPlanType','?')}")
        if not ok:
            dead.append(r["id"])
    if a.prune and dead:
        for cid in dead:
            con.execute("DELETE FROM providerConnections WHERE id=?", (cid,))
        con.commit()
        print(f"  ✓ removed {len(dead)} dead")
    con.close()
    return 0


def cmd_from_page(a) -> int:
    """Capture a session from a live ChatGPT tab (advanced; needs a driver)."""
    print("This command requires a browser driver (nodriver/DrissionPage).")
    print("Use it from a running auto_k12_flow session instead — see capture_session().")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="ChatGPT session -> 9Router codex provider")
    ap.add_argument("--db", default=str(DB_PATH))
    sub = ap.add_subparsers(dest="cmd")
    pi = sub.add_parser("inject", help="inject a session.json into 9Router")
    pi.add_argument("--session", required=True)
    pi.add_argument("--dry-run", action="store_true")
    ps = sub.add_parser("sync", help="verify + prune codex connections")
    ps.add_argument("--prune", action="store_true")
    sub.add_parser("from-page", help="(info) capture from a live tab")
    a = ap.parse_args()

    if a.cmd == "inject":
        return cmd_inject(a)
    if a.cmd == "sync":
        return cmd_sync(a)
    if a.cmd == "from-page":
        return cmd_from_page(a)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
