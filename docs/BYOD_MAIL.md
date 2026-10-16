# BYOD Mail — use your OWN domain with KancaHub

KancaHub farms need a mailbox that can **receive** verification mail.
Out of the box it uses the maintainer's domains (`kancalabs.my.id` via
Tempik, `kancalabs.biz.id` via the relay). That works for trying things,
but for real use bring your own domain — otherwise you share rate limits,
abuse reputation, and inbox visibility with strangers.

Any farm flag that takes `--domain` already accepts your domain:

```bash
kancahub autofarm https://example.com/signup --domain mail.example.com
kancahub github farm --domain mail.example.com --max-accounts 2
```

Custom domains always route through the **relay** backend (Tempik only
serves `kancalabs.my.id`). The relay is just this stack, which you can
self-host in ~15 minutes — all of it is already in this repo:

## What you need (all free)

| Piece | Lives in | What it does |
|---|---|---|
| `temp-mail-api` | `supabase/functions/temp-mail-api/` | mailbox API: `?action=create/messages/message/domains`, plus `?action=ingest` for incoming mail |
| Email Worker | `cloudflare/email-worker/` (`worker.js` + `wrangler.toml.example`) | catch-all on your domain → POSTs every mail to `temp-mail-api?action=ingest` |
| DB | `supabase/migrations/0001_temp_mail.sql` | `temp_addresses` + messages tables |

## Setup (15 min)

1. **Supabase** — new free project → `supabase db push` (applies the
   migration) → deploy the edge function → set secrets:
   `TMK_KEY` (your API key), `MAIL_INGEST_SECRET` (shared with the
   Worker), `MAIL_DOMAINS` (comma-separated, e.g. `mail.example.com`).
2. **Cloudflare** — add your domain to Cloudflare → Email Routing →
   catch-all → send to the Worker → `npx wrangler deploy` with
   `SUPABASE_URL` + `MAIL_INGEST_SECRET` set.
3. **KancaHub** — point the CLI at your relay:
   ```bash
   export K12_MAIL_API="https://<ref>.supabase.co/functions/v1/temp-mail-api"
   export K12_MAIL_KEY="<your TMK_KEY>"
   # or put both in ~/.config/auto-freecf/.env (perms 600)
   kancahub github check --domain mail.example.com
   ```

## How it behaves

- `--domain mail.example.com` → relay backend automatically; Tempik is
  never tried for non-`kancalabs.my.id` domains (explicit `--mail tempik`
  with a foreign domain falls back with a warning, never silently).
- The relay only serves domains in its `MAIL_DOMAINS` — a typo returns
  HTTP 422 `Unsupported domain`, not an empty inbox.
- Your domain, your rate limits, your inboxes. Nothing shared.

## Zero-setup alternatives (no domain needed)

- `kancalabs.my.id` (Tempik, default) — fine for a first try.
- Gmail chain (`--mail gmail`) — real `@gmail.com` via public
  emailmux/emailnator/mail.tm endpoints, per-IP rate-limited.
- Litensi (`--mail litensi`) — paid per-code activation, needs balance.
