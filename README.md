<div align="center">

# 🚀 Auto-FreeCF

<img src="assets/logo.svg" alt="Auto-FreeCF Logo" width="200"/>

### Cloudflare Workers AI Account ID & Token Auto-Grabber

<img alt="Version" src="https://img.shields.io/badge/version-v3.3.11-5865F2?style=flat-square">
<img alt="License" src="https://img.shields.io/badge/license-MIT-green?style=flat-square">
<img alt="Node" src="https://img.shields.io/badge/node-%3E=18.0.0-339933?style=flat-square">
<img alt="Python" src="https://img.shields.io/badge/python-3.10+-3776AB?style=flat-square">
<img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue?style=flat-square">

**Fully automated Cloudflare account grabber with advanced stealth scripts**

[Installation](#-installation) • [Usage](#-usage) • [Features](#-features) • [Documentation](#-documentation)

</div>

---

## ⚡ Quick Start

```bash
npm install -g auto-freecf
moycf
```

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

## 🔒 Security Policy

### Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 3.3.x   | :white_check_mark: |
| < 3.3   | :x:                |

### Reporting a Vulnerability

We take security seriously. If you discover a vulnerability, please help us by following these steps:

**DO:**
- Email us at [security@auto-freecf.com](mailto:security@auto-freecf.com)
- Include detailed information about the vulnerability
- Provide steps to reproduce if possible
- Allow us time to address the issue before public disclosure

**DON'T:**
- Open a public GitHub issue about the vulnerability
- Exploit the vulnerability maliciously
- Share the vulnerability details with others before we've had a chance to fix it

**What to expect:**
- We will acknowledge receipt of your report within 48 hours
- We will provide a timeline for a fix within 7 days
- We will keep you informed of our progress
- We will credit you in the security advisory (unless you prefer to remain anonymous)

### Security Best Practices

- Always use the latest version
- Keep your dependencies updated
- Use strong, unique passwords
- Enable 2FA on your Cloudflare account
- Review the code before running it

---

## 📜 Code of Conduct

### Our Pledge

We as members, contributors, and leaders pledge to make participation in our community a harassment-free experience for everyone, regardless of age, body size, visible or invisible disability, ethnicity, sex characteristics, gender identity and expression, level of experience, education, socio-economic status, nationality, personal appearance, race, religion, or sexual identity and orientation.

We pledge to act and interact in ways that contribute to an open, welcoming, diverse, inclusive, and healthy community.

### Our Standards

Examples of behavior that contributes to a positive environment:

* Using welcoming and inclusive language
* Being respectful of differing viewpoints and experiences
* Gracefully accepting constructive criticism
* Focusing on what is best for the community
* Showing empathy towards other community members

Examples of unacceptable behavior:

* The use of sexualized language or imagery, and sexual attention or advances of any kind
* Trolling, insulting or derogatory comments, and personal or political attacks
* Public or private harassment
* Publishing others' private information without explicit permission
* Other conduct which could reasonably be considered inappropriate in a professional setting

### Enforcement Responsibilities

Community leaders are responsible for clarifying and enforcing our standards of acceptable behavior and will take appropriate and fair corrective action in response to any behavior that they deem inappropriate, threatening, offensive, or harmful.

### Scope

This Code of Conduct applies within all community spaces, and also applies when an individual is officially representing the community in public spaces.

### Enforcement

Instances of abusive, harassing, or otherwise unacceptable behavior may be reported to the community leaders responsible for enforcement at [conduct@auto-freecf.com](mailto:conduct@auto-freecf.com).

All complaints will be reviewed and investigated promptly and fairly.

### Attribution

This Code of Conduct is adapted from the [Contributor Covenant](https://www.contributor-covenant.org), version 2.0.

---

## 📝 License

MIT
