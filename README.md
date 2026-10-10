<div align="center">

# 🌾 KancaHub

**One CLI for the whole account-farming toolkit** — Cloudflare Workers AI,
proxy harvesting, WARP tunnelling, ChatGPT K-12, Grok/xAI, TokenHarbor,
and teacher document generation, unified behind `kancahub`.

👉 **[Full command reference → KANCAHUB.md](KANCAHUB.md)**

```bash
kancahub doctor                       # health check across all tools
kancahub stack signup -n 3 --warp     # create CF accounts on clean IPs → 9Router
kancahub k12 auto && kancahub k12 inject   # ChatGPT K-12 → 9Router (codex)
kancahub thk inject                   # TokenHarbor keys → 9Router
kancahub grok run                     # xAI farm
kancahub yowes make --country us ...  # teacher documents
```

---

This project builds on **Auto-FreeCF** (below) and integrates
[PetaniProxy](https://github.com/itzluthfi/petani-proxy),
[grok-register](https://github.com/AaronL725/grok-register),
[harbor](https://github.com/masanto/harbor),
[ChatGPT-K-12](https://github.com/itzluthfi/Farm-Acc-ChatGPT-K-12-Teachers-Verification-Too),
and [yowes](https://github.com/masanto/yowes) — see [KANCAHUB.md](KANCAHUB.md).

## 🔒 Security

See [SECURITY.md](SECURITY.md) for details on reporting vulnerabilities and security best practices.

---

## 📜 Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for our community guidelines and standards.

---

## 📝 License

MIT
