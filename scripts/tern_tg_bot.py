#!/usr/bin/env python3
"""tern_tg_bot.py — Interactive Telegram Bot Bridge for Tern Terminal & System Monitor.

Provides a lightweight, zero-lag remote control for Tern and server workloads
directly from Telegram on your phone.

Commands:
  /status            - Live status of Oracle A1 hunter, Fastly proxy, blu, and 9router
  /screen, /capture  - Snapshot of the active Tern terminal screen (@focused)
  /ls                - List all open windows, tabs, and panes in Tern
  /send <text>       - Send keystrokes / command into the active Tern terminal pane
  /cmd <shell cmd>   - Execute a shell command on the laptop and return output
  /blu <shell cmd>   - Execute a shell command on `blu` server over SSH
  /help              - Show help and available commands
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Add repo scripts to sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import notify

logging.basicConfig(
    level=logging.INFO,
    format="[tg-tern] %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("tg-tern")

TERN_BIN = Path.home() / "Downloads/tern/tern"
CHUNK_SIZE = 3800


def escape_markdown(text: str) -> str:
    """Escape text for MarkdownV2 or keep preformatted in backticks."""
    return text.replace("`", "'")


def send_reply(chat_id: str | int, text: str, code_block: bool = False):
    """Send chunked reply back to user on Telegram."""
    if code_block:
        clean = escape_markdown(text)
        formatted = f"```\n{clean}\n```"
    else:
        formatted = text

    for i in range(0, max(1, len(formatted)), CHUNK_SIZE):
        chunk = formatted[i : i + CHUNK_SIZE]
        try:
            notify._api(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "Markdown" if code_block else None,
                    "disable_web_page_preview": True,
                },
            )
        except Exception as e:
            # Fallback to plain text if Markdown parsing fails
            try:
                notify._api(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": text[i : i + CHUNK_SIZE],
                        "disable_web_page_preview": True,
                    },
                )
            except Exception as e2:
                log.error("Failed sending message: %s", e2)


def get_tern_ls() -> str:
    if not TERN_BIN.exists():
        return f"Tern binary not found at {TERN_BIN}"
    try:
        res = subprocess.run([str(TERN_BIN), "ls"], capture_output=True, text=True, timeout=5)
        return res.stdout.strip() or res.stderr.strip() or "No open Tern sessions."
    except Exception as e:
        return f"Error running tern ls: {e}"


def get_tern_capture(block: str = "@focused") -> str:
    if not TERN_BIN.exists():
        return f"Tern binary not found at {TERN_BIN}"
    try:
        res = subprocess.run([str(TERN_BIN), "capture", block], capture_output=True, text=True, timeout=8)
        out = res.stdout.strip()
        if not out and res.stderr:
            out = res.stderr.strip()
        # Return last 30 lines if too large
        lines = out.splitlines()
        if len(lines) > 40:
            lines = lines[-40:]
        return "\n".join(lines) or "(Terminal screen is currently blank)"
    except Exception as e:
        return f"Error capturing Tern screen: {e}"


def send_tern_text(text: str, block: str = "@focused") -> str:
    if not TERN_BIN.exists():
        return f"Tern binary not found at {TERN_BIN}"
    try:
        # Append newline if not present
        to_send = text if text.endswith("\n") else text + "\n"
        res = subprocess.run([str(TERN_BIN), "send", block, "text", to_send], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return f"✓ Sent to Tern [{block}]: {text}"
        return f"Error: {res.stderr.strip()}"
    except Exception as e:
        return f"Error sending to Tern: {e}"


def get_system_status() -> str:
    lines = ["🖥️ *SYSTEM & WORKLOAD STATUS*"]

    # 1. Oracle A1 Status (from LightVela)
    try:
        res_oci = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=4", "lightvela", "systemctl --user is-active oracle-hunter.service; journalctl --user -u oracle-hunter.service -n 1 --no-pager"],
            capture_output=True, text=True, timeout=6
        )
        oci_out = res_oci.stdout.strip().splitlines()
        state = oci_out[0] if oci_out else "unknown"
        last_log = oci_out[-1] if len(oci_out) > 1 else ""
        lines.append(f"\n🛰️ *Oracle A1 Hunter (LightVela)*: `{state}`\n   Last: {last_log[:70]}")
    except Exception as e:
        lines.append(f"\n🛰️ *Oracle A1 Hunter*: `Error checking ({e})`")

    # 2. blu server status
    try:
        res_blu = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=4", "blu", "uptime; tmux ls 2>/dev/null || echo 'No tmux'"],
            capture_output=True, text=True, timeout=6
        )
        blu_out = res_blu.stdout.strip().splitlines()
        up = blu_out[0] if blu_out else "unknown"
        sessions = [l for l in blu_out[1:] if ":" in l]
        lines.append(f"\n☁️ *Blu Server*: Up\n   Load: {up.split('load average:')[-1].strip() if 'load average:' in up else up}\n   Tmux: {', '.join(sessions) if sessions else 'none'}")
    except Exception as e:
        lines.append(f"\n☁️ *Blu Server*: `Unreachable ({e})`")

    # 3. 9Router status
    try:
        res_9r = subprocess.run(
            ["curl", "-s", "http://127.0.0.1:20128/api/health", "-m", "2"],
            capture_output=True, text=True, timeout=3
        )
        r9_ok = "active" if "ok" in res_9r.stdout else "down"
        lines.append(f"\n⚡ *9Router*: `{r9_ok}` (:20128)")
    except Exception:
        lines.append(f"\n⚡ *9Router*: `down`")

    # 4. Fastly VPN Proxy status
    try:
        res_px = subprocess.run(
            ["curl", "-s", "http://127.0.0.1:25347/", "-m", "2"],
            capture_output=True, text=True, timeout=3
        )
        px_ok = "active" if "Fastly" in res_px.stdout else "inactive"
        lines.append(f"🛡️ *Fastly 50GB Proxy*: `{px_ok}` (:25347)")
    except Exception:
        lines.append(f"🛡️ *Fastly 50GB Proxy*: `inactive`")

    return "\n".join(lines)


def run_shell(cmd_str: str, target_blu: bool = False) -> str:
    if target_blu:
        full_cmd = ["ssh", "-o", "ConnectTimeout=10", "blu", cmd_str]
    else:
        full_cmd = ["bash", "-c", cmd_str]

    try:
        res = subprocess.run(full_cmd, capture_output=True, text=True, timeout=45)
        out = (res.stdout or "") + (res.stderr or "")
        return out.strip() or f"(Command exited with code {res.returncode}, no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out after 45 seconds."
    except Exception as e:
        return f"Execution error: {e}"


def handle_message(msg: dict):
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", ""))
    authorized_chat = str(notify.CHAT)

    # Security check: only respond to owner's chat_id
    if authorized_chat and chat_id != authorized_chat:
        log.warning("Unauthorized access attempt from chat_id: %s", chat_id)
        return

    text = (msg.get("text") or "").strip()
    if not text:
        return

    log.info("Received command: %s", text)

    # Command Router
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/start", "/help"):
        help_text = (
            "🤖 *Tern Terminal & Workload Bridge*\n\n"
            "• `/status` - Cek Oracle Hunter, Blu, 9Router, Fastly Proxy\n"
            "• `/screen` atau `/capture` - Ambil snapshot layar Tern aktif\n"
            "• `/ls` - Daftar window dan tab Tern\n"
            "• `/send <teks>` - Kirim perintah/ketikan ke terminal Tern\n"
            "• `/cmd <perintah>` - Jalankan command bash di laptop\n"
            "• `/blu <perintah>` - Jalankan command di server blu (ssh)\n"
        )
        send_reply(chat_id, help_text)

    elif cmd == "/status":
        st = get_system_status()
        send_reply(chat_id, st)

    elif cmd in ("/screen", "/capture"):
        target_block = arg or "@focused"
        screen_text = get_tern_capture(target_block)
        send_reply(chat_id, screen_text, code_block=True)

    elif cmd == "/ls":
        ls_text = get_tern_ls()
        send_reply(chat_id, ls_text, code_block=True)

    elif cmd == "/send":
        if not arg:
            send_reply(chat_id, "Gunakan: `/send <perintah atau teks>`")
        else:
            res = send_tern_text(arg)
            send_reply(chat_id, res)

    elif cmd == "/cmd":
        if not arg:
            send_reply(chat_id, "Gunakan: `/cmd <command bash di laptop>`")
        else:
            send_reply(chat_id, f"Running on laptop: `{arg}`...")
            out = run_shell(arg, target_blu=False)
            send_reply(chat_id, out, code_block=True)

    elif cmd == "/blu":
        if not arg:
            send_reply(chat_id, "Gunakan: `/blu <command di server blu>`")
        else:
            send_reply(chat_id, f"Running on blu: `{arg}`...")
            out = run_shell(arg, target_blu=True)
            send_reply(chat_id, out, code_block=True)

    else:
        # Default: if user types plain text, give quick tip
        send_reply(
            chat_id,
            f"Perintah tidak dikenal. Ketik `/help` untuk menu perintah, atau `/send {text}` untuk mengetik ke Tern."
        )


def main():
    if not notify.TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN not set in ~/.config/auto-freecf/.env")

    log.info("Starting Tern Telegram Bot Daemon (Authorized Chat ID: %s)...", notify.CHAT)
    offset = 0

    # Notify startup
    try:
        notify.send("🤖 *Tern Telegram Bot Bridge Aktif!*\nKetik `/help` untuk daftar perintah kontrol terminal.", title="Tern Bot Online")
    except Exception as e:
        log.warning("Could not send startup message: %s", e)

    while True:
        try:
            updates = notify._api("getUpdates", {"offset": offset, "timeout": 25})
            if updates and updates.get("ok"):
                for u in updates.get("result", []):
                    offset = u.get("update_id", 0) + 1
                    msg = u.get("message")
                    if msg:
                        handle_message(msg)
        except urllib.error.URLError as e:
            time.sleep(5)
        except Exception as e:
            log.error("Poll loop error: %s", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
