# Webshare residential hunter → Camoufox port

Author: Supervisor. 2026-10-06. Root cause + plan.

## Why (verified live)
- Webshare auto-hunter (`petani-proxy/core/webshare_hunter.py`) uses **DrissionPage/Chromium**.
- Live run FAILED: reCAPTCHA not solved → "Akun 1 waktu tunggu habis" → 0 residential proxies.
- CapSolver path is DEAD: `CAPSOLVER_API_KEY` in .env = `CAP-xx...` → `ERROR_KEY_DOES_NOT_EXIST`.
- The register page uses **Google reCAPTCHA v2 (invisible)**, sitekey `6LeHZ6UUAAAAAKat_YS--O2tj_by3gv3r_l03j9d`,
  frames: `/recaptcha/api2/anchor` + `/bframe`. It is NOT Cloudflare Turnstile.
- The free path already in code = **reCAPTCHA AUDIO challenge** → transcribed with
  `speech_recognition.recognize_google` (works; deps + ffmpeg present). It just runs on
  DrissionPage and doesn't engage.

## User directive
Use **Camoufox** (already installed, stealthy) instead of DrissionPage/Chromium for the
Webshare hunter.

## Plan: `scripts/webshare_camoufox.py`
Port `hunt_single_auto` (register + harvest) to Camoufox async:
1. Camoufox (headless option, os=windows) → goto `https://proxy.webshare.io/register`.
2. Fill email (our domain via relay OR duckmail/mail.tm), password, ToS checkbox; click
   "Sign Up With Email".
3. reCAPTCHA handling: the v2 is INVISIBLE → submit triggers challenge; when the bframe
   appears, click the AUDIO button, download `#audio-source`, transcribe via
   `speech_recognition` (recognize_google), type into `#audio-response`, click verify.
   (Optionally accept a live CAPSOLVER key if the env one is valid — it is not.)
4. On dashboard, poll the proxy list (same JS as the hunter) and collect
   `<user>:<pass>@<ip>:<port>` → write to `petani-proxy/output/webshare_residential.txt`
   (REAL residential) + optionally sync to 9Router.
5. Rate-limit + honest reporting (no fake proxies).
6. CLI: `--count N --headless --domain kancalabs.biz.id|myid --out FILE`.

### Venv split
Camoufox lives in `camoufox-venv`; `speech_recognition`+`pydub` live in the main venv.
Options: (a) install the two light deps into camoufox-venv, or (b) run Camoufox async from
camoufox-venv and shell the audio-transcription to the main venv. Prefer (a) if pip works.

## Outcome we want
Real residential IPs in `webshare_residential.txt` → then `auto_egress` uses them for
GitHub/TokenHarbor; GitHub signup stops 403ing.

## Tasks
- W1: `scripts/webshare_camoufox.py` (port) + tests (pure helpers only).
- W2: wire its output file into `auto_egress` residential slot + `kancahub proxy residential`
  so `kancahub proxy residential -n 1` runs the Camoufox hunter.
