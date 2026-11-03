# Panduan Lengkap OmniSearch (Multi-Platform Keyword Sentinel)

**OmniSearch** adalah engine pemantau dan pencari kata kunci (*keyword sentinel*) lintas **43 platform publik** secara paralel dan real-time tanpa membutuhkan API key.

Binary statis Go (~7 MB) telah terkompilasi dan terpasang global di:
```bash
~/.local/bin/omnisearch
```

---

## 1. Daftar Platform yang Didukung (43 Platform)

| Kategori | Platform / Sumber | Keunggulan & Jenis Informasi |
|---|---|---|
| **Forum AI & Komunitas Asia** | `linuxdo`, `nodeseek`, `v2ex`, `lowendtalk` | **Bansos AI, saldo API gratis ($2-$10), referral pass, trik reverse proxy, diskon server/VPS.** |
| **Developer & Repos** | `github`, `githubrepos`, `gitlab`, `codeberg`, `huggingface`, `dockerhub`, `pypi`, `npm` | Rilis library baru, issue keamanan, token leaks, model weights. |
| **Tech Forum & Diskusi** | `hackernews`, `discourse` (OpenAI, Cursor), `stackexchange`, `devto`, `qiita`, `habr` | Diskusi teknis frontier LLM, rilis tool internal dev. |
| **Media Sosial & Microblog** | `bluesky`, `mastodon`, `lemmy`, `reddit`, `threads`, `telegram`, `hive`, `4chan` | Sinyal tren cepat, link drop referral first-come first-served. |
| **Berita & RSS** | `googlenews`, `bingnews`, `duckduckgo`, `yahoo`, `producthunt`, `substack`, `rss` | Pengumuman resmi, artikel berita global. |
| **Media & Arsip** | `youtube`, `peertube`, `podcasts`, `archiveorg`, `openalex`, `openlibrary`, `wikipedia` | Transkrip video, snapshot Wayback Machine. |

---

## 2. Panduan Perintah (Command Cheat Sheet)

### A. Pencarian Sekali Jalan (`once`)
Gunakan mode `once` untuk melakukan pencarian instan dan langsung keluar:
```bash
# 1. Cari keyword "claude" di forum-forum bansos & developer Asia (sangat cepat ~0.5s):
omnisearch once -k "claude" -s linuxdo,v2ex,nodeseek,lowendtalk

# 2. Cari di platform berita & developer global (HackerNews, GitHub, Dev.to):
omnisearch once -k "anthropic" -s hackernews,github,devto

# 3. Cari postingan baru maksimal 2 jam terakhir:
omnisearch once -k "reverse proxy" --max-age 2h

# 4. Filter dengan kata wajib / exclude kata tertentu:
omnisearch once -k "api" --require "gratis|free|giveaway" --exclude "paid"
```

### B. Pemantauan Real-Time Otomatis (`watch`)
Gunakan mode `watch` untuk membiarkan OmniSearch memantau internet terus-menerus dan membunyikan alert saat ada postingan baru:
```bash
# Pantau referral pass Claude secara terus-menerus setiap 30 detik:
omnisearch watch -k "claude guest pass" -s bluesky,mastodon,reddit,hackernews --interval 30s
```

### C. Notifikasi Otomatis (Telegram & Webhook)
OmniSearch dapat langsung mengirimkan pesan ke bot Telegram atau webhook Discord/Slack:
```bash
# Kirim alert postingan baru langsung ke Telegram:
omnisearch watch -k "gemini pro" --telegram "TOKEN_BOT:CHAT_ID"

# Kirim alert ke webhook Discord / Slack:
omnisearch watch -k "zero-day" --webhook "https://discord.com/api/webhooks/..."
```

### D. Verifikasi Link Otomatis (`--verify-claude-pass`)
Untuk referral pass seperti `claude.ai/referral/...`, OmniSearch bisa langsung memvalidasi link ke endpoint API sebelum memunculkan alert, sehingga hanya link yang **masih aktif (belum diklaim orang)** yang ditampilkan:
```bash
omnisearch watch -k "claude.ai/referral" --verify-claude-pass
```

---

## 3. Integrasi Khusus: LINUX DO Harvester (`scripts/linuxdo_fetch.py`)

Karena `linux.do` menggunakan Cloudflare Turnstile, OmniSearch dilengkapi modul otomatis berbasis **Camoufox Stealth Browser**:
* **Bypass Turnstile:** Lolos verifikasi otomatis dalam ~6 detik.
* **Kategori yang Dipanen:**
  * `welfare` (福利羊毛): Pembagian kupon, saldo API, akun gratis.
  * `resource` (资源荟萃): Reverse proxy, script otomatis, tool scraping.
  * `develop`: Diskusi teknis dev & AI.
  * `latest`: Topik terbaru komunitas.

### Cara Penggunaan Mandiri:
```bash
# Panen topik terbaru dan filter langsung dari terminal:
python3 scripts/linuxdo_fetch.py --filter "claude"
python3 scripts/linuxdo_fetch.py --filter "api"
python3 scripts/linuxdo_fetch.py --filter "free"

# Paksa pembaruan cache langsung dari browser:
python3 scripts/linuxdo_fetch.py --force
```

---

## 4. Lokasi File & Output
* **Binary:** `~/.local/bin/omnisearch`
* **Source code:** `/home/amen/Auto-FreeCF-vendor-refs/omnisearch`
* **Linux.do Cache:** `~/.config/auto-freecf/linuxdo_cache.json`
* **Alerts Output:** Direktori `alerts/` (format `.jsonl` atau `.md`)
