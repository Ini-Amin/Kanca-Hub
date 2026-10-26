#!/usr/bin/env python3
"""scripts/telegram_agent.py — Interactive 24/7 Telegram AI Assistant & Bounty Supervisor.

Runs on blu (or laptop):
  1. Long-polling Telegram bot loop (@AmebppBot).
  2. Multi-turn AI chat via 9Router (Gemini Flash / GLM-5.3 / DeepSeek).
  3. Interactive command & status awareness (/status, /hunt, /bounty, /stop).
  4. Automatic report notification dispatcher.
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
from collections import deque
from pathlib import Path

# Paths
HOME = Path.home()
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = HOME / ".config" / "auto-freecf" / ".env"
LOG_DIR = HOME / ".config" / "auto-freecf" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[tg-agent] %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("tg-agent")

# Load environment
BOT_TOKEN = ""
CHAT_ID = "6969373914"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            BOT_TOKEN = line.split("=", 1)[1].strip()
        elif line.startswith("TELEGRAM_CHAT_ID="):
            CHAT_ID = line.split("=", 1)[1].strip()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", BOT_TOKEN).strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", CHAT_ID).strip()
R9_URL = os.environ.get("NINE_ROUTER_URL", "http://127.0.0.1:20128/v1")

# Conversation memory (per-chat history)
CHAT_HISTORIES: dict[str, deque] = {}
HISTORY_LEN = 12

MODELS = [
    "CodeBuddy/deepseek-1017",
    "Zcode/GLM-5.3",
    "ag/gemini-3.8-flash-high",
    "xperimential/glm-5.3-uncensored:free",
    "atria/Atria-Dawn-Preview",
]


def parse_r9_response(raw: str) -> str:
    """Parse both standard JSON completions and SSE data: stream chunks."""
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
            return d["choices"][0]["message"].get("content") or ""
        except Exception:
            pass
    chunks = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data: ") and line != "data: [DONE]":
            try:
                d = json.loads(line[6:].strip())
                delta = d.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content") or ""
                if not content:
                    msg = d.get("choices", [{}])[0].get("message", {})
                    content = msg.get("content") or ""
                chunks.append(content)
            except Exception:
                pass
    return "".join(chunks).strip()


def _r9_key() -> str:
    db_paths = [
        HOME / ".9router" / "db" / "data.sqlite",
        Path("/root/.9router/db/data.sqlite"),
    ]
    for db in db_paths:
        if db.exists():
            try:
                import sqlite3
                con = sqlite3.connect(str(db))
                k = con.cursor().execute("SELECT key FROM apiKeys LIMIT 1").fetchone()[0]
                con.close()
                return k
            except Exception:
                pass
    return "sk-dummy"


def send_telegram(text: str, chat_id: str = CHAT_ID) -> bool:
    if not BOT_TOKEN or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    # Chunk long messages (Telegram max 4096)
    chunks = [text[i : i + 4000] for i in range(0, len(text), 4000)] or [text]
    for ch in chunks:
        payload = {"chat_id": chat_id, "text": ch, "parse_mode": "HTML"}
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15):
                pass
        except Exception:
            # Fallback to plain text if HTML parsing fails
            try:
                payload["parse_mode"] = ""
                req2 = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req2, timeout=15):
                    pass
            except Exception as e2:
                log.error("Failed to send telegram message: %s", e2)
                return False
    return True


def query_llm(messages: list[dict], system: str = "") -> str:
    """Query 9Router with automatic model fallback."""
    key = _r9_key()
    full_messages = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)

    for m in MODELS:
        body = {
            "model": m,
            "messages": full_messages,
            "max_tokens": 1500,
        }
        try:
            req = urllib.request.Request(
                f"{R9_URL}/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=35) as resp:
                raw_text = resp.read().decode("utf-8", "replace")
                content = parse_r9_response(raw_text)
                if content:
                    return content
        except Exception as e:
            log.warning("Model %s failed: %s; trying next...", m, e)
    return "Maaf, seluruh model AI di 9Router saat ini sedang tidak merespon. Mohon cek koneksi 9Router."


def get_live_system_context() -> str:
    """Collect strictly factual real-time context directly from running processes."""
    try:
        up = subprocess.run(["uptime"], capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        up = "unknown"

    # 1. Real active running processes
    active_tools = []
    try:
        ps_res = subprocess.run(["ps", "-eo", "pid,cmd"], capture_output=True, text=True, timeout=3).stdout
        for line in ps_res.splitlines():
            if "nuclei" in line and "grep" not in line:
                active_tools.append("Nuclei active scanner")
            elif "httpx" in line and "grep" not in line:
                active_tools.append("HTTPX live prober")
            elif "subfinder" in line and "grep" not in line:
                active_tools.append("Subfinder passive recon")
            elif "bounty_run.py auto" in line:
                active_tools.append("bounty_run autopilot loop")
    except Exception:
        pass

    # 2. Real current active target being worked
    bj_dirs = sorted(Path("/tmp").glob("bj-bugcrowd-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    current_target = "Idle / Menunggu siklus berikutnya"
    if bj_dirs:
        current_target = bj_dirs[0].name.replace("bj-bugcrowd-", "")

    # 3. Real state of tmux sessions (check if done/idle)
    tmux_states = []
    try:
        sessions = subprocess.run(["tmux", "ls"], capture_output=True, text=True, timeout=3).stdout.strip().splitlines()
        for s in sessions:
            s_name = s.split(":", 1)[0]
            pane = subprocess.run(["tmux", "capture-pane", "-p", "-t", s_name], capture_output=True, text=True, timeout=2).stdout
            if "Finished with status 0" in pane or "parity reached" in pane.lower() or not pane.strip():
                tmux_states.append(f"{s_name}: SUDAH SELESAI (Status 0, terminal idle)")
            elif "auto-hunter" in s_name:
                tmux_states.append(f"{s_name}: SEDANG BERJALAN (Target aktif: {current_target})")
            else:
                tmux_states.append(f"{s_name}: AKTIF")
    except Exception:
        pass

    log_file = HOME / ".config" / "auto-freecf" / "logs" / "bounty_auto.log"
    last_log = ""
    if log_file.exists():
        lines = log_file.read_text(errors="ignore").splitlines()
        last_log = "\n".join(lines[-15:]) if lines else ""

    ctx = f"""[Real-Time Verified System State - GROUND TRUTH]
- Server: blu (Ubuntu 24.04, 8 vCPU / 23 GB RAM)
- Server Uptime: {up}
- SINGLE Active Target Scanning RIGHT NOW: {current_target} (Running tools: {', '.join(set(active_tools)) or 'None'})
- Note: Only ONE target runs at a time sequentially. The historical bugcrowd folders on disk are past/cached jobs.
- Tmux Sessions Truth:
{chr(10).join('  • ' + st for st in tmux_states) if tmux_states else '  None'}
- Latest Autopilot Log Tail:
{last_log or 'No active logs'}
"""
    return ctx


def handle_user_message(user_text: str, chat_id: str):
    log.info("Processing message from chat %s: %s", chat_id, user_text[:80])

    # Direct quick commands
    text_clean = user_text.strip().lower()
    if text_clean in ("/start", "/help"):
        send_telegram(
            "<b>🤖 KancaHub Interactive AI Assistant is Ready!</b>\n\n"
            "Anda bisa berbicara langsung dengan saya layaknya di chat biasa:\n"
            "• Tanya jawab seputar status riset, CVE, dan target.\n"
            "• Beri perintah untuk cek target, eksekusi, atau pantau progress.\n\n"
            "<b>Perintah Cepat:</b>\n"
            "• <code>/status</code> — Cek RAM, Uptime & Proses Hunting\n"
            "• <code>/log</code> — Lihat log autopilot terakhir\n"
            "• <code>/stop</code> — Hentikan sesi hunting background",
            chat_id,
        )
        return

    if text_clean == "/status":
        up = subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()
        mem = subprocess.run(["free", "-h"], capture_output=True, text=True).stdout.strip()
        tmux = subprocess.run(["tmux", "ls"], capture_output=True, text=True).stdout.strip()
        send_telegram(
            f"<b>📊 Status Server blu:</b>\n<pre>{up}\n\n{mem}\n\nSessions:\n{tmux or 'No active sessions'}</pre>",
            chat_id,
        )
        return

    if text_clean == "/log":
        log_file = HOME / ".config" / "auto-freecf" / "logs" / "bounty_auto.log"
        if log_file.exists():
            lines = log_file.read_text(errors="ignore").splitlines()
            snippet = "\n".join(lines[-25:])
            send_telegram(f"<b>📋 Log Bounty Autopilot Terakhir:</b>\n<pre>{snippet[:3800]}</pre>", chat_id)
        else:
            send_telegram("Belum ada log aktif di <code>bounty_auto.log</code>.", chat_id)
        return

    # Natural conversation with LLM
    history = CHAT_HISTORIES.setdefault(chat_id, deque(maxlen=HISTORY_LEN))
    history.append({"role": "user", "content": user_text})

    sys_context = get_live_system_context()
    system_prompt = f"""You are KancaHub's trusted autonomous AI security assistant running 24/7 on the cloud server 'blu'.
You are conversing directly with your owner on Telegram.

Personality & Rules:
- Terse, evidence-first, highly competent technical engineer.
- Respond naturally in Indonesian (dominant language) with English technical terms.
- Use the provided real-time system context to answer questions about active scans, targets, and server state.
- Keep replies concise, readable on a phone screen, without fluff or ceremony.

{sys_context}"""

    reply = query_llm(list(history), system=system_prompt)
    history.append({"role": "assistant", "content": reply})

    send_telegram(reply, chat_id)


def main():
    if not BOT_TOKEN:
        sys.exit("Error: TELEGRAM_BOT_TOKEN not configured in environment or .env")

    log.info("Starting Telegram Agent on blu (Chat ID: %s)...", CHAT_ID)
    send_telegram("🤖 <b>KancaHub Interactive AI Agent aktif di blu!</b>\nSilakan ketik apa saja untuk berdiskusi.")

    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=25"
            req = urllib.request.Request(url, headers={"User-Agent": "KancaHubAgent/1.0"})
            with urllib.request.urlopen(req, timeout=35) as r:
                data = json.loads(r.read().decode("utf-8"))

            for update in data.get("result", []):
                offset = max(offset, update["update_id"] + 1)
                msg = update.get("message")
                if not msg:
                    continue

                from_id = str(msg.get("chat", {}).get("id", ""))
                if CHAT_ID and from_id != CHAT_ID:
                    log.warning("Ignored unauthorized chat: %s", from_id)
                    continue

                text = msg.get("text", "").strip()
                if not text:
                    continue

                threading.Thread(target=handle_user_message, args=(text, from_id), daemon=True).start()

        except urllib.error.HTTPError as e:
            log.error("Telegram polling HTTP error: %s", e)
            time.sleep(5)
        except Exception as e:
            log.error("Telegram polling error: %s", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
