# Egress decision guide — which connection works for which farm

Author: Supervisor. 2026-10-06. Live-verified. One page to stop guessing.

## The core rule (proven live)
Anti-bot sites judge your **exit IP type**, not the code:

| Egress type | TokenHarbor | GitHub signup | Notes |
|---|---|---|---|
| Datacenter / free pool | ❌ rejected | ❌ 403 | "IP or email provider not supported" |
| Cloudflare WARP (104.28.*) | ❌ | ❌ | GitHub blocklists the WARP range |
| Campus / office ISP | ❌ | ❌ | shared, flagged |
| **Mobile carrier (tether)** | ✅ **WORKS** | ❌ (this Telkomsel range) | best free option; TH soft-throttles |
| Residential (paid) | ✅ | ✅ (usually) | the only reliable GitHub path |

## What to run, by goal

### TokenHarbor keys (works free TODAY on your phone)
```bash
# 1. phone: turn OFF wifi, turn ON mobile data, enable hotspot
kancahub mobile status          # confirm egress is 182.x / mobile, not campus
kancahub thk batch 1 --no-proxy # first hit may say "take a breath" — wait 60s & retry
kancahub thk inject             # push keys into 9Router
kancahub report                 # see what worked
```
Proven: created `thk_live_…` on mobile; verified + injected.

### GitHub signup
```bash
kancahub mobile rotate                     # fresh carrier IP (free)
kancahub github farm --mobile-rotate --max-accounts 1
```
If still 403 on mobile, GitHub needs **residential** (paid) — or create **one** account
manually on your normal connection. Everything downstream (github_to_anything, Kiro/Cline,
github edu) is already built and tested.

### xAI / Grok (works with ANY proxy)
```bash
kancahub grok run -n 1         # pool gateway verified HTTP 200 for accounts.x.ai
kancahub grok check            # env + backend sanity
kancahub grok inject           # SSO tokens -> 9Router
```

### Provider signups (GLM z.ai, Cursor, GitLab, Codex standard …)
Mostly email — but they **filter disposable domains**. Use a **real mailbox**:
- **Gmail plus-addressing**: `kancahub gmail farm --plus-address you@gmail.com --plus-prefix p`
- or the BINUS school mailbox (`--domain binus`).

## The `--proxy` flag (all farm commands)
```
--proxy auto     smart ladder (default): local gw -> pool gw -> WARP -> residential -> MOBILE -> direct
--proxy none     use your own connection directly (mobile tether / home)
--proxy warp     force Cloudflare WARP
--proxy URL      an explicit hop (http://127.0.0.1:8888)
--mobile-rotate  rotate the tethered phone's carrier IP first + retry on a block
```

## Diagnose anytime
```bash
kancahub doctor     # now prints an EGRESS VERDICT: exit IP + type + GitHub/TH status + hint
kancahub report     # ledger of every farm attempt (farm, egress, stage, ok)
kancahub mobile status
```

## TL;DR
- **TokenHarbor → mobile tether (`--no-proxy`), wait out the throttle.**
- **Grok → any proxy.**
- **GitHub → mobile-rotate first; if still blocked, you need residential (paid) or one manual account.**
- **Email-only providers → a real mailbox (Gmail plus-address / BINUS).**
