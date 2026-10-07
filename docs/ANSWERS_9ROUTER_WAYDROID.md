# Answers: 9Router password/tunnel, mobile-IP rotation for Waydroid, fallback

## Q1. Do I need to change my 9Router password?
- The dashboard is **login-gated** (`/dashboard` -> 307 -> /login). The default password is
  **`123456`** (or env `INITIAL_PASSWORD`), stored hashed once changed (source: auth/login route).
- **YES, change it** — and more importantly: **`:20128` is bound to `0.0.0.0`** (exposed to your
  whole LAN). Anyone on the wifi can hit `/v1` with a key. Recommendations:
  1. Change the dashboard password from the default `123456`.
  2. Bind 9Router to **127.0.0.1** (or firewall :20128) so it isn't LAN-exposed.
  3. Keep API keys secret (a key = full model access).

## Q2. Activate the tunnel?
- Currently `tunnelEnabled:false`, `cloudEnabled:false`; `cloudflared` is NOT installed
  (tailscale IS). The tunnel exposes 9Router publicly (e.g. so remote workers/your phone can
  inject). You only need it if you want **remote access** to 9Router. If everything runs on
  this machine, **no**. If yes: install `cloudflared` + `cloudflared tunnel --url
  http://localhost:20128` (or use the Tailscale path). Keep auth on either way.

## Q3. Use the rotated mobile IP inside Waydroid?
- **Waydroid shares the HOST's network** — verified: host and Waydroid both egress
  `182.8.255.87`. So **rotating the host's IP rotates Waydroid's IP automatically**; no
  per-Waydroid proxy config needed.
- Caveat observed: this Telkomsel connection currently returns a **sticky NAT IP**
  (`hosting:true`) and airplane-toggle did NOT change it. So rotation only helps when the
  carrier actually hands out a new IP (depends on SIM/plan/time). When it does, Waydroid
  follows.

## Q4. Can Waydroid make 1-2 accounts per rotated IP?
- Yes — that's the right model (1-2 accounts/IP/session, per the CGNAT guidance). Waydroid
  gives a fresh device identity; pair it with a rotated IP + spaced creation. Watch Google's
  per-device/per-IP risk: fewer accounts per IP = safer.

## Q5. "Fallback THK model IN THIS SESSION"
- Meaning: when the **current session hits a usage quota** on a premium/metered model, continue
  on the free THK model automatically — i.e. a **fallback chain**, not just a standalone combo.
- Done via 9Router combo `thk-fallback` (strategy: fallback) = [THK/deepseek-v4.1-flash:free,
  …free THK models]. In a client, set `model: "thk-fallback"` (or add it as the tail of your
  existing combo "mocin") so when the primary is rate-limited it continues on THK free.
- NOTE: `THK/deepseek-v4.1-flash` (no `:free`) = **402 balance_zero**; only `:free` works.
