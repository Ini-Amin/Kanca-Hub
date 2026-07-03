<p align="center">
  <img src="assets/logo.svg" width="128" height="128" alt="Auto-FreeCF logo">
</p>

<h1 align="center">Auto-FreeCF</h1>

<p align="center">
  <strong>Cloudflare Workers AI Account ID & Token Auto-Grabber</strong>
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-v3.0.0-181717?style=flat-square">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-2ea44f?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Mode" src="https://img.shields.io/badge/browser-automation-ff6b35?style=flat-square">
  <img alt="Cloudflare" src="https://img.shields.io/badge/Cloudflare-Workers%20AI-F38020?style=flat-square&logo=cloudflare&logoColor=white">
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

## ⚡ Quick Start

```bash
npm install -g auto-freecf
moycf
```

**That's it!** Auto-setup akan jalan, lalu muncul menu interaktif:

```
╔══════════════════════════════════════════════════════════╗
║   🚀 Auto-FreeCF                                         ║
║   Cloudflare Workers AI Account ID & Token Grabber       ║
╚══════════════════════════════════════════════════════════╝

Choose an option:

  [1] 🌐 Web UI (browser interface)
  [2] 💻 Terminal UI (interactive menu)
  [3] 📝 Process accounts file
  [4] 🚪 Exit

Select option (1-4):
```

Tinggal pilih mode yang mau dipakai. Done! ✅

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

### 2. Run & Choose Mode

Jalankan `moycf`, lalu pilih dari menu:

- **[1] Web UI** — Buka browser di `http://localhost:8080`, paste JSON, klik process
- **[2] Terminal UI** — Menu interaktif di terminal, bisa add account manual
- **[3] Process file** — Langsung process file JSON

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

Modern web interface — buka di browser, paste JSON, klik process.

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

---

## 💻 Terminal UI

Interactive terminal menu — navigate & process tanpa browser.

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

---

## ⚙️ Requirements

- Node.js 18+ — [Download](https://nodejs.org/)
- Python 3.10+ — [Download](https://www.python.org/downloads/)
- Internet connection
- Cloudflare account credentials

---

## 🔧 Troubleshooting

<details>
<summary><b>Python was not found</b></summary>

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
<summary><b>Permission error</b></summary>

```bash
sudo npm install -g auto-freecf
```
</details>

---

## 📄 License

MIT

---

<p align="center">
  <strong>Made with ❤️ for the community</strong>
</p>
