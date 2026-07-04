<div align="center">

# 🚀 Auto-FreeCF

<img src="assets/logo.svg" alt="Auto-FreeCF Logo" width="200"/>

### Cloudflare Workers AI Account ID & Token Auto-Grabber

<img alt="Version" src="https://img.shields.io/badge/version-v3.3.6-5865F2?style=flat-square">
<img alt="License" src="https://img.shields.io/badge/license-MIT-green?style=flat-square">
<img alt="Node" src="https://img.shields.io/badge/node-%3E=18.0.0-339933?style=flat-square">
<img alt="Python" src="https://img.shields.io/badge/python-3.10+-3776AB?style=flat-square">
<img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue?style=flat-square">

**Fully automated Cloudflare account grabber with advanced stealth scripts**

[Installation](#-installation) • [Usage](#-usage) • [Features](#-features) • [Documentation](#-documentation)

</div>

---

## ✨ Features

- 🤖 **Full Automation** — Login, grab Account ID, create API Token, all automatic
- 🛡️ **Stealth Mode** — Bypass Cloudflare bot detection with advanced stealth scripts
- 👻 **Headless by Default** — Runs completely in background, no browser window opens
- 🌐 **Residential Proxy** — Optional proxy configuration for better success rate
- 📝 **Single & Bulk** — Input single email:pass atau bulk dari file
- 📦 **Auto Setup** — Automatic dependency installation with live timer
- 💾 **Export Results** — Save to TXT format with account_id:worker_token
- 🔐 **Google OAuth** — Support login via Google Sign-In (fully automated)

---

## 📁 Project Structure

```
Auto-FreeCF/
├── src/                    # Core source code
│   ├── __init__.py
│   ├── browser_bot.py      # Main browser automation logic
│   ├── turnstile_solver.py # Turnstile challenge solver
│   └── utils.py            # Utility functions
├── tests/                  # Test files
├── config/                 # Configuration files (proxy configs)
├── docs/                   # Documentation
├── assets/                 # Static assets
├── cli.js                  # CLI entry point
├── terminal_ui.py          # Terminal UI
├── web_ui.py               # Web UI
├── browser_bot.py          # Backward compatibility wrapper
└── package.json            # NPM package config
```

## 🚀 Installation

```bash
npm install -g auto-freecf
```

## 💻 Usage

### CLI Mode

```bash
# Single account (email:password)
moycf email@example.com:password123

# Bulk accounts from file
moycf accounts.txt

# With proxy
moycf accounts.txt --proxy config/proxy.json

# Google OAuth login
moycf google_email:password --login-method google
```

### Interactive Mode

```bash
moycf
```

Then choose:
1. Single account (email:password)
2. Single account (Google OAuth)
3. Bulk accounts (from file)

### Web UI

```bash
python web_ui.py
```

Open http://localhost:8080 in your browser.

## 🔧 Development

### Project Structure

- **src/browser_bot.py**: Main CFAutoGrabber class with login, token creation logic
- **src/turnstile_solver.py**: Turnstile challenge solving (isolated page approach)
- **src/utils.py**: Helper functions (load_accounts, load_proxy_config, save_results)
- **browser_bot.py**: Backward compatibility wrapper for existing scripts

### Running Tests

```bash
cd tests
python test_login.py
```

## 📝 License

MIT
