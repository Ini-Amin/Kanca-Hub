<p align="center">
  <img src="assets/logo.svg" width="128" height="128" alt="Auto-FreeCF logo">
</p>

<h1 align="center">Auto-FreeCF</h1>

<p align="center">
  <strong>Cloudflare Workers AI Account ID & Token Auto-Grabber</strong>
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-v3.0.6-181717?style=flat-square">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-2ea44f?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Node" src="https://img.shields.io/badge/node-18%2B-339933?style=flat-square&logo=node.js&logoColor=white">
  <img alt="Cloudflare" src="https://img.shields.io/badge/Cloudflare-Workers%20AI-F38020?style=flat-square&logo=cloudflare&logoColor=white">
  <img alt="npm" src="https://img.shields.io/badge/npm-auto--freecf-CB3837?style=flat-square&logo=npm">
</p>

<p align="center">
  <em>By mmoaa</em>
</p>

---

## 🚀 Overview

Auto-FreeCF is a fully automated tool that grabs **Cloudflare Account IDs** and creates **Workers AI API Tokens** using browser automation. Just provide your credentials, sit back, and let the bot do the work.

Supports **JSON** and **TXT** (email:password) input formats, with three different UI modes to choose from.

---

## ✨ Features

- 🤖 **Full Auto Browser Automation** — Login, grab Account ID, create API Token, all automatic
- 🛡️ **Bypass Cloudflare Challenge** — Handle managed challenge without hassle
- 🌐 **Web UI** — Modern browser interface with beautiful gradient design
- 💻 **Terminal UI** — Interactive terminal with colors and step-by-step progress
- 📝 **CLI Mode** — Batch processing via command line
- 📦 **Auto Setup** — Dependencies install automatically with verbose progress & time estimates
- 📂 **Multi-Format Input** — Supports both JSON and TXT (email:password) files
- 🧪 **Workers AI Test** — Verify token can access Workers AI
- 💾 **Export JSON** — Results saved in clean JSON format
- 🎨 **Beautiful Branding** — Logo, watermarks, and clean UI throughout

---

## ⚡ Quick Start

### Install

```bash
npm install -g auto-freecf
```

### Run

```bash
moycf
```

**That's it!** First run will auto-setup everything with verbose progress:

```
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║   🚀 Auto-FreeCF                                         ║
║   Cloudflare Workers AI Account ID & Token Grabber       ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
   By mmoaa

📋 System Check
──────────────────────────────────────────────────
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

---

## 📂 Input Formats

### TXT Format (Recommended)

Simple `email:password` format, one per line:

```txt
user1@example.com:password1
user2@example.com:password2
user3@example.com:password3
```

### JSON Format

Standard JSON array:

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

---

## 📖 Usage

### 1. Prepare Your Accounts File

Create `accounts.txt` (recommended) or `accounts.json`:

```bash
# TXT format
echo "user@example.com:mypassword" > accounts.txt

# JSON format
echo '[{"email":"user@example.com","password":"mypassword"}]' > accounts.json
```

### 2. Run & Choose Mode

Run `moycf`, then choose from the menu:

- **[1] Web UI** — Opens browser at `http://localhost:8080`, supports both JSON & TXT paste
- **[2] Terminal UI** — Interactive menu with colors, can add accounts manually
- **[3] Process file** — Directly process a JSON or TXT file

### 3. Results

Output saved to: `exports/cf_accounts.json`

```json
[
  {
    "email": "user1@example.com",
    "account_id": "abc123def456...",
    "api_token": "xyz789abc012...",
    "workers_ai_ok": true
  }
]
```

---

## 🌐 Web UI

Modern web interface with auto-detect format support:

```
┌──────────────────────────────────────────────────┐
│  🚀 Auto-FreeCF                                  │
│  Cloudflare Workers AI Account ID & Token Grabber│
│  ─────────────────────────────────────────────── │
│                                                  │
│  📝 Supported Formats                            │
│  JSON: [{"email": "...", "password": "..."}]     │
│  TXT:  email:password                            │
│                                                  │
│  Enter your Cloudflare accounts:                 │
│  ┌────────────────────────────────────────────┐  │
│  │ user1@example.com:pass1                    │  │
│  │ user2@example.com:pass2                    │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  [  🚀 Process Accounts  ]                       │
│                                                  │
│  ✅ Success! Processed 2 accounts.               │
│  Results saved to: exports/cf_accounts.json      │
│                                                  │
│                                    By mmoaa      │
└──────────────────────────────────────────────────┘
```

---

## 💻 Terminal UI

Interactive terminal menu with colorful output:

```
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║   🚀 Auto-FreeCF                                         ║
║   Cloudflare Workers AI Account ID & Token Grabber       ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
   By mmoaa

Choose an option:

  [1] 📂 Process accounts from file (JSON/TXT)
  [2] ✏️  Add account manually
  [3] 📋 View saved accounts
  [4] 🚪 Exit

Select option (1-4):
```

---

## 📦 Auto Setup

First run automatically sets up everything:

```
📋 System Check
──────────────────────────────────────────────────
✓ Python found: python3
➤ Creating virtual environment...
  This isolates Python dependencies (~10s)
✓ Virtual environment created (8s)

📦 Installing Dependencies
──────────────────────────────────────────────────
  First time setup — this may take a few minutes

➤ [1/2] Installing Python packages...
  Packages: httpx, curl_cffi, playwright, flask
  Estimated time: ~30-60s
✓ Python packages installed (45s)

➤ [2/2] Installing Chromium browser...
  Downloading Chromium (~150MB)
  Estimated time: ~1-3 min (depends on connection)
✓ Chromium installed (2m 15s)

══════════════════════════════════════════════════════
✓ All dependencies installed! Total: 2m 28s
══════════════════════════════════════════════════════
```

---

## ⚙️ Requirements

- **Node.js 18+** — [Download](https://nodejs.org/)
- **Python 3.10+** — [Download](https://www.python.org/downloads/)
- **Internet connection**
- **Cloudflare account credentials**

---

## 🔄 Update

```bash
npm update -g auto-freecf
```

---

## 🔧 Troubleshooting

<details>
<summary><b>Python was not found</b></summary>

1. Install Python from https://www.python.org/downloads/
2. **Check "Add Python to PATH"** during install
3. Restart terminal
</details>

<details>
<summary><b>Browser timeout / stuck</b></summary>

- Cloudflare can be slow sometimes, try again
- Make sure internet connection is stable
- Delete `browser_data/` folder and try again
</details>

<details>
<summary><b>Permission error on Linux/macOS</b></summary>

```bash
sudo npm install -g auto-freecf
```
</details>

<details>
<summary><b>Path with spaces error on Windows</b></summary>

- Fixed in v3.0.6+ — update with `npm update -g auto-freecf`
- If still having issues, reinstall: `npm uninstall -g auto-freecf && npm install -g auto-freecf`
</details>

---

## 📄 License

MIT

---

<p align="center">
  <strong>Made with ❤️ by mmoaa</strong>
</p>
