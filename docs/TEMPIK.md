# Tempik — self-hosted disposable mail (deployed)

Source: /home/amen/tgbot-verify/tempik (Cloudflare Worker, D1, Email Worker + Hono API)

## Deployment (live)
- Worker:        tempik
- URL:           https://tempik.kancalabs.workers.dev
- D1 database:   tempik-db  (id 6c07fa13-6d73-442e-a07e-25dfa9c13f3d)
- Mail domain:   kancalabs.my.id
- Email routing: kancalabs.my.id catch-all -> Email Worker "tempik"  (CF zone 79be4613...)

## API (from tgbot-verify/tempik/API.md)
- GET  /api/session                 -> {sessionId}          (anonymous, no login)
- GET  /api/config                  -> {appName, mailDomain, mailDomains, webHost}
- POST /api/inboxes                 {localPart?, domain?}   -> {address, created_at}
- GET  /api/inboxes/<address>/messages                        -> [{id, from, subject, body, ...}]
- GET  /api/inboxes/<address>/messages/<id>                   -> full message
- header: x-session-id on all except /api/session and /api/config

## Verified
- /api/config returns 200 with mailDomains:["kancalabs.my.id"]
- session + inbox creation work (e.g. sirsakasri78@kancalabs.my.id)

## Note
First request after deploy can return "error code: 1042" (cold start) — retry.
