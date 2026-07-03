# <p align="center">🚀 Auto-FreeCF</p>

<p align="center">
  <strong>Cloudflare Workers AI Account ID & Token Auto-Grabber</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Playwright-Automation-green?logo=googlechrome&logoColor=white" />
  <img src="https://img.shields.io/badge/License-MIT-orange" />
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20Mac-lightgrey" />
</p>

---

## ✨ Fitur

- 🤖 **Full Auto Browser Automation** — Login, ambil Account ID, buat API Token, semua otomatis
- 🛡️ **Bypass Cloudflare Challenge** — Handle managed challenge tanpa ribet
- 🌐 **Web UI** — Interface browser yang modern dan mudah dipakai
- 💻 **Terminal UI** — Interface terminal interaktif dengan warna dan progress bar
- 📝 **CLI Mode** — Batch processing via command line
- 📦 **Auto Setup** — Install dependencies otomatis, tinggal jalan
- 🧪 **Workers AI Test** — Cek apakah token bisa akses Workers AI
- 💾 **Export JSON** — Hasil tersimpan rapi dalam format JSON

---

## 🚀 Quick Start

### Clone & Run

```bash
git clone https://github.com/mocasus/Auto-FreeCF.git
cd Auto-FreeCF
```

### Pilih Mode

| Mode | Command | Deskripsi |
|------|---------|-----------|
| 🌐 **Web UI** | `./run.sh --web` / `run.bat --web` | Buka browser, paste accounts, done! |
| 💻 **Terminal UI** | `./run.sh --tui` / `run.bat --tui` | Menu interaktif di terminal |
| 📝 **CLI** | `./run.sh --accounts file.json` | Batch processing dari file |

> **First time?** Script akan otomatis install semua dependencies. Tunggu ±5 menit pertama kali.

---

## 📖 Cara Pakai

### 1. Siapkan File `accounts.json`

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

### 2. Jalankan

```bash
# Web UI — buka http://localhost:8080
./run.sh --web

# Terminal UI — menu interaktif
./run.sh --tui

# CLI — langsung process file
./run.sh --accounts accounts.json
```

### 3. Hasil

Output tersimpan di: `exports/cf_accounts.json`

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

## 🖥️ Web UI

<p align="center">
  <em>Modern web interface — buka di browser, paste JSON, klik process</em>
</p>

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
- 📋 Paste JSON langsung di browser
- 🔄 Real-time progress tracking
- 📊 Hasil lengkap dengan status per account
- 🎨 Clean & modern UI

---

## 💻 Terminal UI

<p align="center">
  <em>Interactive terminal menu — navigate & process tanpa browser</em>
</p>

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
- 🎨 Colorful output dengan emoji
- 📊 Progress bar saat processing
- ✏️  Add account manual tanpa bikin file
- 📋 View & manage saved accounts

---

## ⚙️ Requirements

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.10+ | [Download](https://www.python.org/downloads/) |
| Internet | — | Untuk connect Cloudflare |
| Cloudflare Account | — | Email + password |

---

## 🔧 Troubleshooting

<details>
<summary><b>Windows: "Python was not found"</b></summary>

1. Install Python dari https://www.python.org/downloads/
2. **Centang "Add Python to PATH"** saat install
3. Restart terminal
</details>

<details>
<summary><b>Browser timeout / stuck</b></summary>

- Cloudflare kadang lambat, coba lagi
- Pastikan internet stabil
- Hapus folder `browser_data/` lalu coba lagi
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
├── run.sh              # 🐧 Linux/Mac launcher
├── run.bat             # 🪟 Windows launcher
├── browser_bot.py      # 🤖 Core automation engine
├── web_ui.py           # 🌐 Web interface (Flask)
├── terminal_ui.py      # 💻 Terminal interface
├── requirements.txt    # 📦 Python dependencies
├── accounts.json       # 📝 Input accounts
└── exports/
    └── cf_accounts.json # 💾 Output results
```

---

## 📄 License

MIT — Free to use, modify, and distribute.

---

<p align="center">
  <strong>Made with ❤️ for the community</strong>
</p>
