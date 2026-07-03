<p align="center">
  <img src="assets/logo.svg" width="128" height="128" alt="Auto-FreeCF logo">
</p>

<h1 align="center">Auto-FreeCF</h1>
<p align="center">Automated Cloudflare Workers AI credential extractor - Login and extract Account ID + API Token from existing CF accounts.</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-v3.0.0-181717?style=flat-square">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-2ea44f?style=flat-square">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Mode" src="https://img.shields.io/badge/browser-none-ff6b35?style=flat-square">
  <img alt="Cloudflare" src="https://img.shields.io/badge/Cloudflare-Workers%20AI-F38020?style=flat-square&logo=cloudflare&logoColor=white">
</p>

<p align="center">
  <a href="#features"><img alt="Features" src="https://img.shields.io/badge/%E2%9C%A8-features-181717?style=flat-square"></a>
  <a href="#quick-start"><img alt="Quick Start" src="https://img.shields.io/badge/%E2%9A%A1-quick%20start-2ea44f?style=flat-square"></a>
  <a href="#how-it-works"><img alt="How It Works" src="https://img.shields.io/badge/%F0%9F%94%A7-how%20it%20works-ff6b35?style=flat-square"></a>
  <a href="#exports"><img alt="Exports" src="https://img.shields.io/badge/%F0%9F%93%A6-exports-3776AB?style=flat-square"></a>
</p>

---

> 🔒 **Private Repository**
>
> This repository is currently private. It contains automated credential extraction
> tools for Cloudflare Workers AI accounts.

---

## Features

- **Auto-login** to Cloudflare accounts using email + password
- **Auto-extract** Account ID from authenticated session
- **Auto-create** Workers AI API tokens with proper permissions
- **Export** all credentials (email, password, account_id, api_token) to JSON/CSV
- **No browser automation** - Pure HTTP with session management
- **Batch processing** - Process multiple accounts from file
- **Clean output** - Ready for downstream injection or manual use

## Quick Start

```bash
git clone https://github.com/mocasus/Auto-FreeCF.git
cd Auto-FreeCF
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### Web UI (Open in Browser)

```bash
./run.sh --web --port 8080
```

Open browser ke `http://localhost:8080`

### Terminal UI

```bash
./run.sh --tui
```

### Single Account (CLI)

```bash
./run.sh --email your@email.com --password yourpassword
```

### Batch Processing

Create `accounts.txt`:
```
email1@example.com:password1
email2@example.com:password2
email3@example.com:password3
```

Run:
```bash
./run.sh --batch accounts.txt
```

### Output

Results saved to `/root/cf-account-bot/exports/cf_accounts.json`:

```json
[
  {
    "email": "your@email.com",
    "password": "yourpassword",
    "account_id": "023e105f4ecef8ad9ca31a8372d0c353",
    "api_token": "YOUR_WORKERS_AI_TOKEN",
    "created_at": "2026-07-03T12:34:56Z"
  }
]
```

## How It Works

1. **Login** - Authenticates to Cloudflare using email/password
2. **Session** - Maintains authenticated session with cookies
3. **Extract Account ID** - Calls `/client/v4/accounts` to get account ID
4. **Create Token** - Creates Workers AI API token with proper permissions
5. **Export** - Saves all credentials to JSON/CSV

### Token Permissions

The tool creates tokens with:
- **Workers AI Read** permission
- Scoped to the specific account
- No expiration (manual revocation required)

## CLI

```bash
./run.sh --help
```

Options:

```text
# Single account
--email EMAIL           Cloudflare account email
--password PASSWORD     Cloudflare account password

# Batch processing
--batch FILE            File with email:password per line

# Output options
--out-json PATH         JSON output path (default: exports/cf_accounts.json)
--out-csv PATH          CSV output path (default: exports/cf_accounts.csv)

# Advanced
--no-create-token       Skip token creation, only extract account ID
--verify                Verify token works with Workers AI test call
```

## Exports

### JSON Format

```json
{
  "email": "your@email.com",
  "password": "yourpassword",
  "account_id": "023e105f4ecef8ad9ca31a8372d0c353",
  "api_token": "YOUR_WORKERS_AI_TOKEN",
  "created_at": "2026-07-03T12:34:56Z"
}
```

### CSV Format

```csv
email,password,account_id,api_token,created_at
your@email.com,yourpassword,023e105f4ecef8ad9ca31a8372d0c353,YOUR_WORKERS_AI_TOKEN,2026-07-03T12:34:56Z
```

## Testing

Verify extracted credentials work:

```bash
./run.sh --email your@email.com --password yourpassword --verify
```

This will:
1. Extract credentials
2. Test Workers AI endpoint with the token
3. Report success/failure

## Security Notes

- Credentials are stored locally in plain text - secure your exports
- Tokens have no expiration - revoke manually when done
- Use dedicated accounts for testing
- Do not commit credentials to version control

## Root Cause Notes

Cloudflare does not expose a public signup API. The dashboard is protected by:
- Managed Challenge (403 from VPS IPs)
- Session-based authentication
- No programmatic account creation endpoint

This tool works with **existing accounts** by automating the login and credential extraction flow.

## License

MIT

<p align="center"><sub>v3.0.0 · 2026 · Built by <a href="https://github.com/mocasus">@mocasus</a></sub></p>
