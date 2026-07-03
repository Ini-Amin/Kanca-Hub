<div align="center">

<img src="assets/logo.svg" width="128" height="128" alt="Auto-FreeCF">

# 🚀 Auto-FreeCF

**Cloudflare Workers AI Account ID & Token Auto-Grabber**

<img alt="Version" src="https://img.shields.io/badge/version-v3.2.0-5865F2?style=flat-square">
<img alt="License" src="https://img.shields.io/badge/license-MIT-57F287?style=flat-square">
<img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white">
<img alt="Node" src="https://img.shields.io/badge/node-18%2B-339933?style=flat-square&logo=node.js&logoColor=white">
<img alt="npm" src="https://img.shields.io/badge/npm-auto--freecf-CB3837?style=flat-square&logo=npm">

*By mmoaa*

</div>

---

> [!WARNING]
> ## ⚠️ BETA TESTING
> **This tool is currently in BETA / masa testing.**
> 
> - Fitur dan behavior masih bisa berubah sewaktu-waktu
> - Mungkin masih ada bug atau issue yang belum terhandle
> - Cloudflare bot detection bisa berubah dan membuat tool tidak bekerja
> - **Gunakan dengan risiko sendiri (use at your own risk)**
> - Jangan gunakan untuk production sebelum tool dinyatakan stable
> 
> Feedback dan bug report sangat diharapkan! 🙏

---

## 🚀 Overview

Auto-FreeCF automatically grabs **Cloudflare Account IDs** and creates **Workers AI API Tokens** using browser automation with **stealth mode** and **residential proxy support**.

Supports **single account** (email:pass) dan **bulk accounts** dari file (email:pass per line).

---

## ⚡ Quick Start

```bash
npm install -g auto-freecf
moycf
```

That's it! First run will auto-setup everything (Python venv, pip packages, Chromium).

---

## ✨ Features

- 🤖 **Full Automation** — Login, grab Account ID, create API Token, all automatic
- 🛡️ **Stealth Mode** — Bypass Cloudflare bot detection with advanced stealth scripts
- 🌐 **Residential Proxy** — Optional proxy configuration for better success rate
- 📝 **Single & Bulk** — Input single email:pass atau bulk dari file
- 📦 **Auto Setup** — Automatic dependency installation with live timer
- 💾 **Export Results** — Save to JSON with account ID, API token, and validation

---

## 📖 Usage

### CLI Mode (Recommended)

**Single account** — langsung masukkan email:password:

```bash
moycf user@example.com:mypassword123
```

**Bulk accounts** — dari file (format email:pass per line):

```bash
moycf accounts.txt
```

**With proxy:**

```bash
moycf accounts.txt --proxy=proxy.json
moycf user@example.com:pass123 --proxy=proxy.json
```

### Interactive Mode

Jalankan tanpa argument, lalu pilih mode:

```bash
moycf
```

```
Choose mode:
  [1] Single account (enter email:password)
  [2] Bulk accounts (from file)
  [3] Exit
```

Pilih mode → masukkan input → selesai. Tidak ada menu berlapis.

---

## 📝 Input Formats

### Single Account (CLI)

Langsung di command line:

```bash
moycf user@example.com:password123
```

### Bulk File — TXT Format (Recommended)

Simple `email:password` format, one per line:

```txt
user1@example.com:password1
user2@example.com:password2
user3@example.com:password3
```

### Bulk File — JSON Format

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

## 🌐 Residential Proxy Support (Optional)

For better success rate, especially with bot detection, you can use residential proxies.

### 1. Create Proxy Config

Create a `proxy.json` file:

```json
{
  "server": "http://proxy.example.com:8080",
  "username": "your_username",
  "password": "your_password"
}
```

### 2. Run with Proxy

```bash
moycf accounts.txt --proxy=proxy.json
moycf user@example.com:pass --proxy=proxy.json
```

### 3. Supported Proxy Formats

- HTTP/HTTPS proxies: `http://host:port` or `https://host:port`
- SOCKS5 proxies: `socks5://host:port`
- Authentication via `username` and `password` fields

---

## 📦 Auto Setup

First run automatically sets up:
- Python virtual environment
- Required packages (playwright, requests, etc.)
- Chromium browser for automation

Setup includes live timer and progress indicators.

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
- Use residential proxy for better reliability
</details>

<details>
<summary><b>Permission error on Linux/macOS</b></summary>

```bash
sudo npm install -g auto-freecf
```
</details>

<details>
<summary><b>Path with spaces error on Windows</b></summary>

- Fixed in v3.1.2+ — update with `npm update -g auto-freecf`
- If still having issues, reinstall: `npm uninstall -g auto-freecf && npm install -g auto-freecf`
</details>

<details>
<summary><b>Bot detection / Challenge page stuck</b></summary>

- Use residential proxy (`--proxy=proxy.json`)
- Enable headless mode (default)
- Try with different proxy provider
</details>

---

## 📄 License

MIT

---

<div align="center">

**Made with ❤️ by mmoaa**

</div>
