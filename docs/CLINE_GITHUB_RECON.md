# Cline — "Continue with GitHub" recon (implementation-ready)

Status: RECON ONLY (no code, no live calls). Evidence tagged [CODE] / [UI] / [INFER].
Companion docs: `docs/PROVIDER_GITHUB_MATRIX.md`, `scripts/github_to_kiro.py` (pattern).

## 1. What Cline is inside 9Router

- **Provider entry exists**: `app/.next-cli-build/server/app/dashboard/providers/new.html`
  contains both `Cline` and `ClinePass` (ids `cline`, `clinepass`). `[CODE]`
- **Also a CLI tool**: `app/api/cli-tools/cline-settings/route.js` exists — Cline can be
  driven as a CLI tool that consumes a 9Router endpoint (not necessarily a full provider). `[CODE]`
- **OAuth is generic**: there is **no dedicated `api/oauth/cline/*` route**. Cline auth runs
  through the **generic** `api/oauth/[provider]/[action]/route.js`, which references
  `cline`, `clinepass`, `authorizeUrl`, `oauth`, `tokens`, `baseUrl`. `[CODE]`
  → So 9Router *can* hold a Cline connection, dispatched by the generic OAuth handler.

## 2. Auth endpoints

- **AuthKit (WorkOS) portal**: `https://authkit.cline.bot` — from the user screenshot it offers:
  Email + Enterprise SSO, **Continue with Google**, Continue with Microsoft, **Continue with GitHub**. `[UI]`
- **Authorize/token**: 9Router's generic route carries an `authorizeUrl` + token exchange
  for provider `cline`/`clinepass`; the concrete Cline API base is under `api.cline.bot`
  (AuthKit issues the code, 9Router exchanges it). Exact client_id / PKCE values were **not
  pinned down from the compiled bundle** — treat as TODO. `[CODE]`(route exists) / `[INFER]`(values)

## 3. GitHub-side flow (expected)

1. Start the Cline authorize URL (PKCE code_challenge) `[CODE]`
2. AuthKit shows social buttons → click **Continue with GitHub** `[UI]`
3. Browser lands on `https://github.com/login/oauth/authorize?...` (scopes likely
   `read:user user:email`) `[INFER]`
4. If the GitHub account is already signed in (Camoufox profile), GitHub returns the
   `code` on the loopback/redirect; consume it. `[INFER]`
5. Exchange code → Cline access token → register a 9Router connection (provider node
   prefix e.g. `cline`/`clinepass`, `authType` apikey/oauth, data {baseUrl, token, email}).
   Shape to be confirmed against the generic route handler. `[CODE]`(route) / `[INFER]`(fields)

## 4. Concrete plan

- Build `scripts/github_to_cline.py` mirroring `github_to_kiro.py`:
  load `github_accounts.json` → Camoufox → click "Continue with GitHub" on authkit.cline.bot
  → capture the Cline token → POST to 9Router (port 20128, `x-9r-cli-token`) as a `cline`
  provider connection.
- Reuse the rate/backoff helper from `github_to_kiro.py`.

## 5. Top risks / unknowns

1. **WorkOS org approval** — an Enterprise-SSO org may require admin approval; a plain
   GitHub social login should not, but confirm on a live (single) attempt.
2. **2FA / device verification** on the GitHub side — HALT, do not bypass (account-lock risk).
3. **Token shape** — whether 9Router stores a Cline *OAuth token* or an *API key*; the
   generic route must be read precisely before writing the connection.

## 6. Rate limit

Every GitHub OAuth call: **20–45s random between accounts, 1.5–4s between requests,
stop immediately on any Cloudflare/Turnstile/Arkose challenge**, max 3 accounts/run.
No parallel workers.

## 7. Bottom line

Cline **does** support "Continue with GitHub" `[UI]` and 9Router has a **generic OAuth
route that names cline/clinepass** `[CODE]`. It is a viable Path-1 target, but the exact
PKCE client values and the 9Router connection field shape are **unconfirmed** and must be
read from a live single attempt before bulk automation.
