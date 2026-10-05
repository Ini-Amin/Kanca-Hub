# GitHub Farm — signup + Education application (Camoufox)

Standalone helper: `scripts/github_farm.py`. Drives GitHub signup and the
GitHub Education (Student Pack) application with **Camoufox** (anti-detect
Firefox via the Playwright API), reading the verification code from the BINUS
school mailbox.

> This is a **helper, not a turnkey farmer.** Read the "Automated vs manual"
> section — Arkose/CAPTCHA and the Education identity attestation are **not**
> automatable and must be finished by a human.

Must run under the isolated Camoufox venv (python3.11 + playwright):

```bash
/home/amen/.local/share/auto-freecf/camoufox-venv/bin/python \
    scripts/github_farm.py --index 1 --dry-run
```

Legacy nodriver is **not** used anymore.

---

## What it does

1. **Signup** a NEW account at `https://github.com/signup`:
   - email = `raymondi+gh<N>@binus.ac.id` (plus-addressing; `--index N`)
   - random strong password + `word-word<digits>` username (availability retry)
   - the **8-digit launch code** step, read live from the school mailbox
     (Outlook Web) via Camoufox — reusing the shared meter reader if present
   - account-created detection
2. **Education application** at
   `https://education.github.com/discount_requests/application`:
   - school = BINUS University / Universitas Bina Nusantara
   - school email = same plus-address, name + best-effort academic fields
   - **stops before** any attestation / photo upload
3. Saves each account to `github_accounts.json` (gitignored, mode 600).

It does **not** touch the existing account `Ini-Amin`. Only the
`raymondi@binus.ac.id` plus-addresses are targeted.

---

## Automated vs manual (honest)

**Automatable** ✅

- Email, password, username entry (React/Playwright-safe typing, verified)
- Username-availability retry
- Reading the 8-digit email launch code from the school mailbox
- Education application field fill (school, email, name)

**Not automatable** ❌ *(the script detects + reports these, never fakes success)*

- **Arkose / CAPTCHA** and device-fingerprint gates on signup. Camoufox lowers
  bot signals but there is **no solver**. Detected → reported.
- **Network/IP blocks**: GitHub may serve *"Access is temporarily restricted"*
  (DataDome-style anti-bot) for a flagged egress IP — e.g. the WARP Cloudflare
  range. Detected → reported; retry from another IP (`--proxy` / `--pool`).
- **Education identity attestation**: legal-name attestation, "I am a student"
  checkbox, and the **photo/scan of a student ID or enrollment proof**. This is a
  legal/identity step a human must do; GitHub reviews it manually.
- Email-domain proof, billing, and any MFA/device verification.

---

## Proxy / pool

| Flag | What it does |
|---|---|
| `--proxy URL` | Single egress. Supports the KancaHub rotating gateway `http://127.0.0.1:8888` (or `:8899`): when detected, a sticky `X-Session-ID` header is applied (`scripts/gateway_session.py`) so one upstream proxy is pinned for the whole flow. |
| `--pool FILE` | Newline-separated proxy list; one chosen per `--index` for rotation. Auto-detects the gateway and applies sticky sessions too. |

`--proxy` wins over `--pool`. Exit IP / geo follow the proxy (`geoip=True`).

---

## CLI

```bash
# deps + mailbox self-report
…/camoufox-venv/bin/python scripts/github_farm.py --check

# walk the flow, screenshot, do NOT submit
…/camoufox-venv/bin/python scripts/github_farm.py --index 1 --dry-run

# real signup (stops before captcha/attestation)
…/camoufox-venv/bin/python scripts/github_farm.py --index 1

# headless (Xvfb 'virtual')
…/camoufox-venv/bin/python scripts/github_farm.py --index 1 --headless

# via the local KancaHub gateway (sticky session)
…/camoufox-venv/bin/python scripts/github_farm.py --index 1 --proxy http://127.0.0.1:8888

# rotate across a pool by index
…/camoufox-venv/bin/python scripts/github_farm.py --index 2 --pool signup_from_scratch/proxies.txt
```

Outputs: `github_accounts.json` (accounts) and `debug_github/*.png`
(screenshots). Both are gitignored.

---

## Config (`~/.config/auto-freecf/.env`)

```
SCHOOL_EMAIL=raymondi@binus.ac.id
SCHOOL_MAIL_PASSWORD=...
SCHOOL_MAIL_URL=https://outlook.office.com/mail/   # optional
```

The mailbox reader uses a persistent Camoufox profile at
`~/.config/auto-freecf/school-profile`. If the M365 tenant enforces MFA, log in
once manually so the persisted session is reused.
