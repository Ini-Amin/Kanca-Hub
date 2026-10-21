#!/usr/bin/env python3
"""notify — ONE home for Telegram notifications (gig approvals, farm alerts).

Setup (once, needs the human):
  1. Create the bot via @BotFather -> token into ~/.config/auto-freecf/.env
     as TELEGRAM_BOT_TOKEN (600 perms, never in the repo, never in bashrc).
  2. Send any message to the bot from your Telegram account.
  3. Run: notify.py setup   -> captures your chat id into TELEGRAM_CHAT_ID.

Usage:
  notify.py "text..."            send (chunked at 4000 chars)
  notify.py --title T "text..."  send with a bold title line
  notify.py setup                list chats from getUpdates (pick yours)
  notify.py test                 send a hello to verify the wiring

Approval flow (> $X gigs) is NOT here yet: that needs an inbound-update loop
(poll getUpdates, wait for YES/NO). Build it with the gig worker, not here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ENV_FILE = Path.home() / ".config" / "auto-freecf" / ".env"
TG_LOG = Path.home() / ".config" / "auto-freecf" / "logs" / "telegram.log"
CHUNK = 4000


def _tg_log(direction: str, chat: str, text: str) -> None:
    """Append-only conversation log (outbound now; worker appends inbound).
    The user ordered every customer conversation into the telegram logs."""
    try:
        TG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(TG_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": __import__("time").time(), "dir": direction,
                                "chat": str(chat), "text": text[:2000]}) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _load_env() -> None:
    if ENV_FILE.exists():
        for ln in ENV_FILE.read_text(errors="replace").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


def _api(method: str, payload: dict) -> dict:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in ~/.config/auto-freecf/.env")
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}",
                                 data=json.dumps(payload).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def send(text: str, chat: str = "", parse_mode: str = "") -> list[int]:
    """Send, chunked. Returns message ids. Raises on missing config/API error.

    parse_mode="HTML" lets a digest carry bold labels and links. Callers that
    use it MUST escape user-controlled text (program names, gig titles): an
    unescaped '<' makes Telegram reject the whole message, which for an
    unattended digest means silence instead of an error anyone sees.
    """
    target = chat or CHAT
    if not target:
        raise RuntimeError("TELEGRAM_CHAT_ID not set (run: notify.py setup)")
    ids = []
    for i in range(0, max(1, len(text)), CHUNK):
        payload = {"chat_id": target, "text": text[i:i + CHUNK] or ".",
                   "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        j = _api("sendMessage", payload)
        if not j.get("ok"):
            raise RuntimeError(f"sendMessage failed: {j}")
        ids.append((j.get("result") or {}).get("message_id", 0))
    _tg_log("out", target, text)
    return ids


def cmd_setup() -> int:
    j = _api("getUpdates", {"limit": 20, "timeout": 0})
    if not j.get("ok"):
        print(f"getUpdates failed: {j}")
        return 1
    seen = {}
    for u in j.get("result", []):
        m = (u.get("message") or u.get("edited_message") or {})
        c = m.get("chat") or {}
        if c.get("id"):
            seen[c["id"]] = (c.get("type"), c.get("title") or
                             ((m.get("from") or {}).get("username") or (m.get("from") or {}).get("first_name")))
    if not seen:
        print("no chats yet: send any message to the bot first, then re-run setup")
        return 1
    print("chats that messaged the bot:")
    for cid, (typ, name) in seen.items():
        mark = "  <-- current TELEGRAM_CHAT_ID" if str(cid) == CHAT else ""
        print(f"  {cid}  ({typ}) {name}{mark}")
    print("\nput yours in ~/.config/auto-freecf/.env as TELEGRAM_CHAT_ID=<id>")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="notify.py", description="Telegram notifier")
    ap.add_argument("text", nargs="*", help="message (omit for setup/test)")
    ap.add_argument("--title", default="", help="bold title line")
    ap.add_argument("--chat", default="", help="override TELEGRAM_CHAT_ID")
    ap.add_argument("--setup", action="store_true", help="list chats, capture id")
    ap.add_argument("--test", action="store_true", help="send a hello")
    a = ap.parse_args(argv)
    try:
        if a.setup:
            return cmd_setup()
        if a.test:
            ids = send("notify.py hello — wiring OK")
            print(f"sent {ids}")
            return 0
        if not a.text:
            ap.print_help()
            return 2
        body = (f"*{a.title}*\n" if a.title else "") + " ".join(a.text)
        ids = send(body, chat=a.chat)
        print(f"sent {ids}")
        return 0
    except RuntimeError as e:
        print(f"error: {e}")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
