# Auto-FreeCF 🚀

Cloudflare Workers AI Account ID & Token Auto-Grabber

## Quick Start

### 🌐 Web UI (Recommended)

```bash
# Linux/Mac
./run.sh --web

# Windows
run.bat --web
```

Buka browser: `http://localhost:8080`

### 💻 Terminal UI

```bash
# Linux/Mac
./run.sh --tui

# Windows
run.bat --tui
```

### 📝 Command Line

```bash
# Linux/Mac
./run.sh --accounts accounts.json

# Windows
run.bat --accounts accounts.json
```

## Installation

### Linux/Mac

```bash
git clone https://github.com/mocasus/Auto-FreeCF.git
cd Auto-FreeCF
./run.sh
```

### Windows

```powershell
git clone https://github.com/mocasus/Auto-FreeCF.git
cd Auto-FreeCF
run.bat
```

**First time setup:**
- Script akan otomatis install Python dependencies
- Download browser (Chromium) untuk automation
- Tunggu sampai selesai (±5 menit pertama kali)

## Usage

### 1. Buat File `accounts.json`

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

**Web UI (Browser-based):**
```bash
./run.sh --web
# Buka http://localhost:8080 di browser
```

**Terminal UI (Interactive):**
```bash
./run.sh --tui
```

**Command Line:**
```bash
./run.sh --accounts accounts.json
```

### 3. Hasil

Output otomatis tersimpan di: `exports/cf_accounts.json`

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

## Features

✅ Full auto browser automation  
✅ Bypass Cloudflare managed challenge  
✅ Extract Account ID & API Token  
✅ Test Workers AI access  
✅ Export to JSON  
✅ Web UI (port 8080)  
✅ Terminal UI  
✅ Batch processing  

## Requirements

- Python 3.10+
- Internet connection
- Cloudflare account credentials

## Troubleshooting

**Windows: "Python was not found"**
- Install Python dari https://www.python.org/downloads/
- Centang "Add Python to PATH" saat install

**Browser timeout**
- Cloudflare kadang lambat, coba lagi
- Pastikan internet stabil

**Permission error**
```bash
chmod +x run.sh
```

## License

MIT
