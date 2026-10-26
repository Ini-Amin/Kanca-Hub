#!/usr/bin/env python3
"""Hermes Worker — 24/7 Telegram Remote Controller & Cloud Farm Worker.

Runs on Tiarina Cloud (Tencent Singapore PaaS):
  1. Serves HTTP Health Check on PORT (satisfies PaaS uptime monitor).
  2. Runs interactive Telegram Bot long-polling loop (/status, /farm, /exec, /bounty).
  3. Executes scheduled background farming tasks.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="[hermes] %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("hermes")

# Configurations from Environment
PORT = int(os.environ.get("PORT", os.environ.get("HTTP_PORT", 8080)))
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
START_TIME = time.time()


def send_tg_message(text: str, chat_id: str = CHAT_ID) -> bool:
    """Send text message to Telegram."""
    if not BOT_TOKEN or not chat_id:
        log.warning("Telegram Bot Token or Chat ID not configured.")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text[:4000],
        "parse_mode": "HTML",
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        log.error("Failed to send Telegram message: %s", e)
        return False


def get_public_ip() -> str:
    """Fetch egress public IP of the container."""
    try:
        req = urllib.request.Request(
            "https://api.ipify.org?format=json",
            headers={"User-Agent": "curl/7.88.1"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode()).get("ip", "unknown")
    except Exception:
        return "unknown"


def get_system_stats() -> dict:
    """Return memory and uptime stats."""
    uptime_sec = int(time.time() - START_TIME)
    uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s"

    mem_info = {}
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    mem_info[parts[0].strip()] = parts[1].strip()
    except Exception:
        pass

    total_kb = int(mem_info.get("MemTotal", "0 kB").split()[0])
    avail_kb = int(mem_info.get("MemAvailable", "0 kB").split()[0])
    used_mb = (total_kb - avail_kb) // 1024
    total_mb = total_kb // 1024

    return {
        "uptime": uptime_str,
        "memory_used_mb": used_mb,
        "memory_total_mb": total_mb,
        "ip": get_public_ip(),
    }


# ==========================================
# 1. HTTP HEALTH CHECK SERVER
# ==========================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        stats = get_system_stats()
        if self.path in ("/", "/health", "/status"):
            resp = {
                "status": "online",
                "service": "Hermes Cloud Worker",
                "node": "Tencent Singapore (Tiarina Cloud)",
                "stats": stats,
            }
            body = json.dumps(resp, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Mute routine health access logs
        pass


def run_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    log.info("HTTP Health Check listening on 0.0.0.0:%d", PORT)
    server.serve_forever()


# ==========================================
# 2. TELEGRAM INTERACTIVE BOT LOOP
# ==========================================
def handle_bot_command(cmd: str, args: str, chat_id: str):
    log.info("Received command '%s' from chat_id %s", cmd, chat_id)
    cmd = cmd.lower()

    if cmd in ("/start", "/help"):
        msg = (
            "<b>🤖 Hermes Cloud Worker (Singapore Node)</b>\n\n"
            "<b>Available Commands:</b>\n"
            "• <code>/status</code> — Check container RAM, uptime, and public IP\n"
            "• <code>/alpme</code> — Check Alpme accounts status & mint status\n"
            "• <code>/bounty</code> — Check latest bug bounty engagements\n"
            "• <code>/exec &lt;cmd&gt;</code> — Run a bash command inside container\n"
            "• <code>/ip</code> — Get public exit IP\n"
        )
        send_tg_message(msg, chat_id)

    elif cmd == "/status":
        stats = get_system_stats()
        msg = (
            f"<b>📊 Hermes Node Status:</b>\n"
            f"• <b>Location:</b> Singapore (Tencent Cloud)\n"
            f"• <b>Public IP:</b> <code>{stats['ip']}</code>\n"
            f"• <b>RAM Usage:</b> {stats['memory_used_mb']} MB / {stats['memory_total_mb']} MB\n"
            f"• <b>Uptime:</b> {stats['uptime']}\n"
            f"• <b>HTTP Port:</b> {PORT} (Healthy)\n"
        )
        send_tg_message(msg, chat_id)

    elif cmd == "/ip":
        ip = get_public_ip()
        send_tg_message(f"🌐 Container Public IP: <code>{ip}</code>", chat_id)

    elif cmd == "/bounty":
        send_tg_message("🔍 Checking Bugcrowd open engagements...", chat_id)
        try:
            req = urllib.request.Request(
                "https://bugcrowd.com/engagements.json",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                items = data.get("engagements", [])[:5]
                text = "<b>🎯 Top 5 Open Bugcrowd Programs:</b>\n\n"
                for p in items:
                    name = p.get("name", "Unknown")
                    brief = p.get("briefUrl", "")
                    rew = p.get("rewardSummary", "No reward info")
                    text += f"• <b>{name}</b> ({rew})\n  https://bugcrowd.com{brief}\n\n"
                send_tg_message(text, chat_id)
        except Exception as e:
            send_tg_message(f"❌ Failed to fetch bounties: {e}", chat_id)

    elif cmd == "/exec":
        if not args:
            send_tg_message("Usage: <code>/exec &lt;shell command&gt;</code>", chat_id)
            return
        send_tg_message(f"⚙️ Executing: <code>{args[:100]}</code>", chat_id)
        try:
            res = subprocess.run(
                args,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = (res.stdout or "") + (res.stderr or "")
            if not output.strip():
                output = "(Command finished with empty output)"
            send_tg_message(f"<pre>{output[:3800]}</pre>", chat_id)
        except Exception as e:
            send_tg_message(f"❌ Execution error: {e}", chat_id)

    else:
        send_tg_message(f"❓ Unknown command: <code>{cmd}</code>. Type /help for menu.", chat_id)


def run_telegram_bot():
    if not BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not provided. Bot loop disabled.")
        return

    log.info("Starting Telegram Bot long-polling loop...")
    send_tg_message("🚀 <b>Hermes Cloud Worker is now ONLINE!</b>\nLocation: Singapore (Tencent PaaS)\nSend /help to view commands.")

    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=20"
            req = urllib.request.Request(url, headers={"User-Agent": "HermesBot/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())

            for update in data.get("result", []):
                offset = max(offset, update["update_id"] + 1)
                message = update.get("message") or update.get("channel_post")
                if not message:
                    continue

                from_id = str(message.get("chat", {}).get("id", ""))
                # Security gate: only allow designated CHAT_ID if configured
                if CHAT_ID and from_id != CHAT_ID:
                    log.warning("Ignored message from unauthorized chat: %s", from_id)
                    continue

                text = message.get("text", "").strip()
                if not text:
                    continue

                parts = text.split(maxsplit=1)
                cmd = parts[0]
                args = parts[1] if len(parts) > 1 else ""
                handle_bot_command(cmd, args, from_id)

        except urllib.error.HTTPError as e:
            log.error("Telegram polling HTTP error: %s", e)
            time.sleep(5)
        except Exception as e:
            log.error("Telegram polling error: %s", e)
            time.sleep(3)


# ==========================================
# 3. BACKGROUND FARMER / HEARTBEAT
# ==========================================
def run_background_farm():
    log.info("Background farming scheduler active.")
    while True:
        # Heartbeat ping every 6 hours
        time.sleep(21600)
        stats = get_system_stats()
        send_tg_message(
            f"💓 <b>Hermes Heartbeat</b>\n"
            f"Node: Singapore (Tencent PaaS)\n"
            f"Uptime: {stats['uptime']}\n"
            f"RAM: {stats['memory_used_mb']} MB used"
        )


def main():
    log.info("Starting Hermes Worker on Tiarina Cloud...")

    # Thread 1: HTTP Health Server
    t_http = threading.Thread(target=run_http_server, daemon=True)
    t_http.start()

    # Thread 2: Background Farmer
    t_farm = threading.Thread(target=run_background_farm, daemon=True)
    t_farm.start()

    # Main Thread: Telegram Bot Polling
    run_telegram_bot()


if __name__ == "__main__":
    main()
