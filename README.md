<div align="center">

# 🌾 KancaHub

**One CLI for the whole account-farming toolkit** — Cloudflare Workers AI,
proxy harvesting, WARP tunnelling, ChatGPT, Grok/xAI, TokenHarbor,
and teacher document generation, unified behind `kancahub`.
ASK your agent to setup this ✌️.
👉 **[Full command reference → KANCAHUB.md](KANCAHUB.md)**

```bash
kancahub doctor                       # health check across all tools
kancahub stack signup -n 3 --warp     # create CF accounts on clean IPs → 9Router
kancahub thk inject                   # TokenHarbor keys → 9Router
kancahub grok run                     # xAI farm
kancahub yowes make --country us ...  # teacher documents
```

---

This project builds on **Auto-FreeCF** — the Python package in `auto_freecf/`
(login, signup and token export for Cloudflare Workers AI) — and integrates
[PetaniProxy](https://github.com/itzluthfi/petani-proxy),
[grok-register](https://github.com/AaronL725/grok-register),
[harbor](https://github.com/masanto/harbor),
and [yowes](https://github.com/masanto/yowes) — see [KANCAHUB.md](KANCAHUB.md).

## 📦 Install

```bash
git clone https://github.com/mocasus/Auto-FreeCF.git && cd Auto-FreeCF
bash scripts/install.sh        # venv + deps + ~/.local/bin/kancahub + doctor
```

Or just the Python package:

```bash
pip install -e ".[dev]"        # gives you `auto-freecf` and `moycf`
auto-freecf --help
```

Credentials are read from the environment — copy `.env.example` to
`~/.config/auto-freecf/.env` and fill in only what you need. Never commit a
filled-in copy; `.env`, `config.json` and `proxies.txt` are git-ignored.

## ✅ Development

```bash
python -m pytest -q            # 690+ tests; network/browser tests are opt-in
python scripts/gen_cli_surface.py   # after adding or renaming a kancahub command
```

## 🔒 Security

See [SECURITY.md](SECURITY.md) for details on reporting vulnerabilities and security best practices.

---

## 📜 Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for our community guidelines and standards.

---

## 📝 License

MIT
