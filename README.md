<p align="center">
  <img src="assets/logo.svg" width="128" height="128" alt="Auto-FreeCF logo">
</p>

<h1 align="center">Auto-FreeCF</h1>
<p align="center">Cloudflare Workers AI Account ID and token auto-grabber with full browser automation.</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-v3.0.0-181717?style=flat-square">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-2ea44f?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Mode" src="https://img.shields.io/badge/browser-automation-ff6b35?style=flat-square">
  <img alt="Cloudflare" src="https://img.shields.io/badge/Cloudflare-Workers%20AI-F38020?style=flat-square&logo=cloudflare&logoColor=white">
</p>

<p align="center">
  <a href="#features"><img alt="Features" src="https://img.shields.io/badge/%E2%9C%A8-features-181717?style=flat-square"></a>
  <a href="#quick-start"><img alt="Quick Start" src="https://img.shields.io/badge/%E2%9A%A1-quick%20start-2ea44f?style=flat-square"></a>
  <a href="#web-ui"><img alt="Web UI" src="https://img.shields.io/badge/%F0%9F%8C%90-web%20ui-4285F4?style=flat-square"></a>
  <a href="#terminal-ui"><img alt="Terminal UI" src="https://img.shields.io/badge/%F0%9F%92%BB-terminal%20ui-3776AB?style=flat-square"></a>
</p>

---

## ✨ Features

- 🤖 **Full Auto Browser Automation** — Login, grab Account ID, create API Token, all automatic
- 🛡️ **Bypass Cloudflare Challenge** — Handle managed challenge without hassle
- 🌐 **Web UI** — Modern browser interface, paste JSON and process
- 💻 **Terminal UI** — Interactive terminal with colors and step-by-step progress
- 📝 **CLI Mode** — Batch processing via command line
- 📦 **Auto Setup** — Dependencies install automatically, just run
- 🧪 **Workers AI Test** — Verify token can access Workers AI
- 💾 **Export JSON** — Results saved in clean JSON format

---

## 🚀 Quick Start

### Clone & Run

```bash
git clone https://github.com/mocasus/Auto-FreeCF.git
cd Auto-FreeCF
```

### Choose Mode

| Mode | Command | Description |
|------|---------|-------------|
| 🌐 **Web UI** | `./run.sh --web` / `run.bat --web` | Open browser, paste accounts, done! |
| 💻 **Terminal UI** | `./run.sh --tui` / `run.bat --tui` | Interactive menu in terminal |
| 📝 **CLI** | `./run.sh --accounts file.json` | Batch process from file |
| 📋 **Menu** | `./run.sh` / `run.bat` | Interactive menu to choose mode |

> **First time?** Script auto-installs all dependencies. Wait ~5 min on first run.

---

## 📖 Usage

### 1. Prepare `accounts.json`

```json
[
  {
    "email": "user1@example.com",
    "password": "password1"
  },
  {
    "email": "user2@example.com",
    "password": "password2"
  }
]
```

### 2. Run

```bash
# Web UI — open http://localhost:8080
./run.sh --web

# Terminal UI — interactive menu
./run.sh --tui

# CLI — process file directly
./run.sh --accounts accounts.json

# Menu — choose mode interactively
./run.sh
```

### 3. Results

Output saved to: `exports/cf_accounts.json`

```json
[
  {
    "email": "user1@example.com",
    "account_id": "abc123...",
    "api_token": "xyz789...",
    "workers_ai_ok": true
  }
]
```

---

## 🌐 Web UI

Modern web interface — open in browser, paste JSON, click process.

```
┌──────────────────────────────────────────────┐
│  🚀 Auto-FreeCF                              │
│  ─────────────────────────────────────────── │
│                                              │
│  Enter your Cloudflare accounts:             │
│  ┌────────────────────────────────────────┐  │
│  │ [                                      │  │
│  │   {"email": "user@example.com",        │  │
│  │    "password": "mypassword"}           │  │
│  │ ]                                      │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  [  🚀 Process Accounts  ]                   │
│                                              │
│  ✅ Success! Processed 5 accounts.           │
│  Results saved to: exports/cf_accounts.json  │
└──────────────────────────────────────────────┘
```

**Features:**
- 📋 Paste JSON directly in browser
- 🔄 Real-time progress tracking
- 📊 Complete results with per-account status
- 🎨 Clean & modern UI

---

## 💻 Terminal UI

Interactive terminal menu — navigate & process without browser.

```
╔══════════════════════════════════════════════╗
║          🚀 Auto-FreeCF — TUI               ║
╠══════════════════════════════════════════════╣
║                                              ║
║   [1] 📂 Process from JSON file              ║
║   [2] ✏️  Add account manually                ║
║   [3] 📋 View saved accounts                 ║
║   [4] 🚪 Exit                                ║
║                                              ║
╚══════════════════════════════════════════════╝
```

**Features:**
- 🎨 Colorful output with emoji
- 📊 Step-by-step progress per account
- ✏️ Add account manually without creating file
- 📋 View & manage saved accounts

---

## ⚙️ Requirements

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.10+ | [Download](https://www.python.org/downloads/) |
| Internet | — | To connect to Cloudflare |
| Cloudflare Account | — | Email + password |

---

## 🔧 Troubleshooting

<details>
<summary><b>Windows: "Python was not found"</b></summary>

1. Install Python from https://www.python.org/downloads/
2. **Check "Add Python to PATH"** during install
3. Restart terminal
</details>

<details>
<summary><b>Browser timeout / stuck</b></summary>

- Cloudflare can be slow, try again
- Make sure internet is stable
- Delete `browser_data/` folder and retry
</details>

<details>
<summary><b>Permission error (Linux/Mac)</b></summary>

```bash
chmod +x run.sh
```
</details>

<details>
<summary><b>ModuleNotFoundError</b></summary>

```bash
pip install -r requirements.txt
playwright install chromium
```
</details>

---

## 📁 Project Structure

```
Auto-FreeCF/
├── assets/
│   └── logo.svg          # 🎨 Project logo
├── run.sh                # 🐧 Linux/Mac launcher
├── run.bat               # 🪟 Windows launcher
├── browser_bot.py        # 🤖 Core automation engine
├── web_ui.py             # 🌐 Web interface (Flask)
├── terminal_ui.py        # 💻 Terminal interface
├── requirements.txt      # 📦 Python dependencies
├── accounts.json         # 📝 Input accounts
└── exports/
    └── cf_accounts.json  # 💾 Output results
```

---

## 📄 License

MIT — Free to use, modify, and distribute.

---

<p align="center">
  <strong>Made with ❤️ for the community</strong>
</p>
