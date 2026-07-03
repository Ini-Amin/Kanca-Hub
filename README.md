# 🚀 Auto-FreeCF

> Cloudflare Workers AI Account ID & Token Auto-Grabber

![Version](https://img.shields.io/badge/version-3.0.5-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.10+-yellow)

## ✨ Features

- 🌐 **Web UI** - Modern browser interface with real-time processing
- 💻 **Terminal UI** - Interactive command-line interface
- 📝 **Multi-format Support** - JSON or TXT (email:password) input files
- 🤖 **Full Automation** - Login, extract Account ID, create API token
- 🛡️ **Bypass Challenge** - Handles Cloudflare managed challenges
- 📦 **Auto Setup** - One-command installation with verbose progress
- 🎨 **Beautiful UI** - Clean, modern design with "By mmoaa" watermark

## 🚀 Quick Start

### Install & Run

```bash
npm install -g auto-freecf
moycf
```

That's it! The installer will:
- ✅ Check Python installation
- ✅ Create virtual environment
- ✅ Install dependencies (httpx, curl_cffi, playwright, flask)
- ✅ Download Chromium browser
- ✅ Show verbose progress with time estimates

### First Run

When you run `moycf`, you'll see:

```
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║   🚀 Auto-FreeCF                                         ║
║   Cloudflare Workers AI Account ID & Token Grabber       ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
   By mmoaa

📋 System Check
──────────────────────────────────────────────────────────
✓ Python found: python3
✓ Virtual environment exists
✓ Dependencies already installed

Choose an option:

  [1] 🌐 Web UI (browser interface)
  [2] 💻 Terminal UI (interactive menu)
  [3] 📝 Process accounts file
  [4] 🚪 Exit

Select option (1-4):
```

## 📝 Input Formats

### TXT Format (Recommended)

Create `accounts.txt`:

```
user1@example.com:password1
user2@example.com:password2
user3@example.com:password3
```

### JSON Format

Create `accounts.json`:

```json
[
  {"email": "user1@example.com", "password": "password1"},
  {"email": "user2@example.com", "password": "password2"}
]
```

## 🎨 Usage Modes

### Web UI

Modern browser interface with:
- Real-time processing status
- Auto-detect JSON/TXT format
- Beautiful gradient design
- "By mmoaa" watermark

```bash
moycf
# Select option [1]
# Open http://localhost:8080
```

### Terminal UI

Interactive command-line interface with:
- Colorful output
- Progress tracking
- Manual account entry
- View saved accounts

```bash
moycf
# Select option [2]
```

### CLI Mode

Process files directly:

```bash
# TXT format
moycf --accounts accounts.txt

# JSON format
moycf --accounts accounts.json
```

## 📦 Output

Results are saved to `exports/cf_accounts.json`:

```json
[
  {
    "email": "user@example.com",
    "account_id": "abc123...",
    "api_token": "xyz789...",
    "workers_ai_ok": true
  }
]
```

## 🔧 Troubleshooting

### Python Not Found

```bash
# Install Python 3.10+
# Windows: Download from python.org
# Linux: sudo apt install python3 python3-pip
# macOS: brew install python3
```

### Permission Errors

```bash
# Windows: Run PowerShell as Administrator
# Linux/macOS: sudo npm install -g auto-freecf
```

### Browser Issues

```bash
# Reinstall Chromium
cd ~/.auto-freecf/venv
python -m playwright install chromium
```

## 📂 Project Structure

```
Auto-FreeCF/
├── assets/
│   └── logo.svg          # Project logo
├── cli.js                # Node.js CLI wrapper
├── browser_bot.py        # Core automation engine
├── web_ui.py             # Web interface (Flask)
├── terminal_ui.py        # Terminal interface
├── requirements.txt      # Python dependencies
├── package.json          # npm package config
└── exports/              # Output directory
    └── cf_accounts.json  # Results
```

## 🔄 Update

```bash
npm update -g auto-freecf
```

## 📄 License

MIT

---

<p align="center">
  <strong>Made with ❤️ by mmoaa</strong>
</p>
