<div align="center">

<img src="assets/logo.svg" width="128" height="128" alt="Auto-FreeCF">

# 🚀 Auto-FreeCF

**Cloudflare Workers AI Account ID & Token Auto-Grabber**

<img alt="Version" src="https://img.shields.io/badge/version-v3.1.2-5865F2?style=flat-square">
<img alt="License" src="https://img.shields.io/badge/license-MIT-57F287?style=flat-square">
<img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white">
<img alt="Node" src="https://img.shields.io/badge/node-18%2B-339933?style=flat-square&logo=node.js&logoColor=white">
<img alt="npm" src="https://img.shields.io/badge/npm-auto--freecf-CB3837?style=flat-square&logo=npm">

*By mmoaa*

</div>

---

> [!NOTE]
> ## 🚀 Overview
> Auto-FreeCF automatically grabs **Cloudflare Account IDs** and creates **Workers AI API Tokens** using browser automation. Just provide your credentials and let the bot handle everything.
> 
> Supports **JSON** and **TXT** (email:password) input formats, with Web UI, Terminal UI, and CLI modes.

---

> [!TIP]
> ## ⚡ Quick Start
> ```bash
> npm install -g auto-freecf
> moycf
> ```
> That's it! First run will auto-setup everything (Python venv, pip packages, Chromium).

---

> [!IMPORTANT]
> ## ✨ Features
> - 🤖 **Full Automation** — Login, grab Account ID, create API Token, all automatic
> - 🛡️ **Bypass Cloudflare Challenge** — Handle managed challenge automatically
> - 🌐 **Web UI** — Modern browser interface with gradient design
> - 💻 **Terminal UI** — Interactive terminal with colors and progress
> - 📝 **CLI Mode** — Batch processing via command line
> - 📦 **Auto Setup** — Dependencies install automatically with **live timer**
> - 📂 **Multi-Format** — Supports both JSON and TXT (email:password) files
> - 💾 **Export JSON** — Results saved in clean JSON format

---

> [!NOTE]
> ## 📂 Input Formats
> 
> **TXT Format (Recommended):**
> ```txt
> user1@example.com:password1
> user2@example.com:password2
> ```
> 
> **JSON Format:**
> ```json
> [
>   {"email": "user1@example.com", "password": "password1"},
>   {"email": "user2@example.com", "password": "password2"}
> ]
> ```

---

> [!TIP]
> ## 📖 Usage
> 1. **Prepare accounts file** — Create `accounts.txt` or `accounts.json`
> 2. **Run `moycf`** — Choose from menu:
>    - **[1] Web UI** — Opens browser at `http://localhost:8080`
>    - **[2] Terminal UI** — Interactive menu with colors
>    - **[3] Process file** — Directly process a JSON or TXT file
> 3. **Get results** — Output saved to `exports/cf_accounts.json`
> 
> **Output format:**
> ```json
> [
>   {
>     "email": "user1@example.com",
>     "account_id": "abc123def456...",
>     "api_token": "xyz789abc012...",
>     "workers_ai_ok": true
>   }
> ]
> ```

---

> [!IMPORTANT]
> ## ⚙️ Requirements
> - **Node.js 18+** — [Download](https://nodejs.org/)
> - **Python 3.10+** — [Download](https://www.python.org/downloads/)
> - **Internet connection**
> - **Cloudflare account credentials**

---

> [!NOTE]
> ## 🔄 Update
> ```bash
> npm update -g auto-freecf
> ```

---

<details>
<summary><b>🔧 Troubleshooting</b></summary>

### Python was not found
1. Install Python from https://www.python.org/downloads/
2. **Check "Add Python to PATH"** during install
3. Restart terminal

### Browser timeout / stuck
- Cloudflare can be slow sometimes, try again
- Make sure internet connection is stable
- Delete `browser_data/` folder and try again

### Permission error on Linux/macOS
```bash
sudo npm install -g auto-freecf
```

### Path with spaces error on Windows
- Fixed in v3.1.2+ — update with `npm update -g auto-freecf`
- If still having issues, reinstall: `npm uninstall -g auto-freecf && npm install -g auto-freecf`

</details>

---

## 📄 License

MIT

---

<div align="center">

**Made with ❤️ by mmoaa**

</div>
