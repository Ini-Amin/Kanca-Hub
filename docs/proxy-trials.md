# Residential-proxy free trials — reachable from this stack?

Reverse-engineered with Firecrawl (`kancahub scrape`), 2026-10-08. The goal is
**self-serve** trials (no KYC / no credit card / no "contact sales"), because
those are the only ones our farm can actually reach.

## Self-serve, no card (usable)

| Vendor | Trial | Signup | Gate | Our stack can? |
|---|---|---|---|---|
| **ProxyScrape** | 7 days, **10 GB** | `dashboard.proxyscrape.com/v2/sign-up` | email+password+confirm + **Cloudflare Turnstile** | ✅ Camoufox Turnstile solver + relay mailbox |
| **LimeProxies** | free trial | `app.limeproxies.com/#/login/signup` | first/last/email → verify (SPA; needs browser to confirm link vs code) | ⚠️ likely (needs live probe) |
| **Dexodata** | free trial | `dexodata.com/auth/register` | no credit card | ⚠️ not yet probed |
| **Webshare** | 10 proxies, **1 GB/mo** | `proxy.webshare.io/register` | email+password + reCAPTCHA v2 audio | ✅ already automated (`webshare_camoufox.py`) |
| **PingProxy** | free tier | site signup | unknown | ⚠️ not probed |

## Contact-sales / KYC / card (NOT usable automatically)

- **RapidProxy, SwiftProxy** — "500MB free — Contact us" (Telegram). We signed up
  but traffic stays 0 GB. (`residential_proxy_signup.py` reaches the dashboard.)
- **Bright Data, Oxylabs, NetNut** — business/KYC or card-gated trials.
- **Byteful** — 1 GB residential but **KYC (ID verification)** required.
- **ZooProxy** — image-captcha signup + geetest slider on login (see `zooproxy_trial.py`).

## Ranked recommendation

1. **ProxyScrape** — 10 GB/7 days, self-serve, Turnstile (we solve it). **Best.**
2. **LimeProxies / Dexodata** — probe their signup with a browser (link vs code).
3. **Webshare** — already works; reuse for small needs.

All are ethically/legally the vendor's own trial; spend it on **one** account,
don't mass-create (that's what gets accounts flagged).

_Note: `kancahub scrape search/url` (Firecrawl) is how this list was built — no
proxies needed for the recon itself._
