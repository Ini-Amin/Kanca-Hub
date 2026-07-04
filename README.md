# Auto-FreeCF

Cloudflare Workers AI Account ID & Token Auto-Grabber

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
