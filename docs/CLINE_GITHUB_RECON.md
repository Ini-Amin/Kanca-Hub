# Cline — "Continue with GitHub" → 9Router bulk sign-in (recon)

Status: RECON ONLY. No code, no live network. Every claim tagged:

- `[CODE]` — read directly from the 9Router package at
  `/home/amen/.npm-global/lib/node_modules/9router/` (compiled `.next-cli-build`
  server chunks + `src/`). Not modified.
- `[UI]` — rendered dashboard artifact (server-rendered HTML).
- `[INFER]` — reasoned from the above, not directly observed. Verify before relying on it.

## 0. Verdict: provider **and** CLI tool

Cline is **both** a first-class OAuth provider in 9Router **and** a connectable CLI tool.
It is *not* CLI-only. `[CODE]`

- Provider catalog (chunk `458.js`): `{id:"cline",priority:80,alias:"cl",uiAlias:"cl",
  category:"oauth",authModes:["oauth"],hasOAuth:!0}` and
  `{id:"clinepass",priority:85,alias:"clinepass",authModes:["apikey","oauth"],hasOAuth:!0}` `[CODE]`
- Dashboard provider picker lists both:
  `<option value="cline">Cline</option>` / `<option value="clinepass">ClinePass</option>` `[UI]`
  (`app/.next-cli-build/server/app/dashboard/providers/new.html`)
- CLI-tool integration: `src/cli/commands/connectTools.js` → `id:"cline", name:"Cline CLI"`,
  writes `~/.cline/data/globalState.json` + `~/.cline/data/secrets.json` `[CODE]`;
  route `app/api/cli-tools/cline-settings/` exists `[CODE]`.

So 9Router can hold a real Cline connection (provider node `cline`) **and** re-point a local
Cline CLI at the 9Router endpoint. Two independent surfaces.

## 1. Cline auth endpoints (exact)

From the provider `oauth` blocks in chunk `458.js` `[CODE]`:

| Field | cline | clinepass |
|---|---|---|
| appBaseUrl | `https://app.cline.bot` | `https://app.cline.bot` |
| apiBaseUrl | `https://api.cline.bot` | `https://api.cline.bot` |
| authorizeUrl | `https://api.cline.bot/api/v1/auth/authorize` | `https://api.cline.bot/api/v1/auth/authorize` |
| token URL | `https://api.cline.bot/api/v1/auth/token` (key `tokenExchangeUrl`) | `https://api.cline.bot/api/v1/auth/token` (key `tokenUrl`) |
| refreshUrl | `https://api.cline.bot/api/v1/auth/refresh` | `https://api.cline.bot/api/v1/auth/refresh` |
| chat baseUrl | `https://api.cline.bot/api/v1/chat/completions` | same |

**Flow type and PKCE** — from the OAuth modules (chunk `4049.js`, modules `18941` cline /
`35152` clinepass) `[CODE]`:

- `flowType:"authorization_code"` — **not** PKCE. The generic route generates a
  `codeVerifier/codeChallenge/state` for all providers, but the Cline module's
  `buildAuthUrl` ignores them.
- `buildAuthUrl` emits: `https://api.cline.bot/api/v1/auth/authorize?client_type=extension&callback_url=<redirectUri>&redirect_uri=<redirectUri>` — **no `client_id`, no `scope`, no `code_challenge`.**
- `exchangeToken` POSTs JSON to the token URL:
  `{grant_type:"authorization_code", code, client_type:"extension", redirect_uri}`
  (`Content-Type: application/json`). No client_secret.
- Response parsed as `data.accessToken | accessToken`, `data.refreshToken`, `data.userInfo.email`,
  `data.expiresAt` (accepts both `{data:{…}}` and flat shapes). On failure:
  `"Cline token exchange failed: …"` `[CODE]`
- `mapTokens` → `{accessToken, refreshToken, expiresIn (from expires_at), email,
  providerSpecificData:{firstName,lastName}}` `[CODE]`

**No client_id / PKCE is exposed for Cline** — Cline uses `client_type=extension` handoff, not a
registered public client. This is different from the `github` (Copilot) provider, which *does*
carry `clientId:"Iv1.b507a08c87ecfe98"` `[CODE]`.

**Token shape / headers** — chunk `5215.js` (`w$`) + chunk `6249.js` `clineHeaders` `[CODE]`:

- The Cline auth token is **WorkOS-issued**: a raw JWT (`eyJ…`) is rewritten to
  `workos:<jwt>`; an already-`workos:`-prefixed value passes through. Non-JWT values pass raw.
- Sent as `Authorization: Bearer <token|workos:token>` plus
  `HTTP-Referer: https://cline.bot`, `X-Title: Cline`, `X-CLIENT-TYPE: 9router`, `X-CLIENT-VERSION`.
- → The identity provider behind `api.cline.bot/api/v1/auth/authorize` is **WorkOS AuthKit**. `[INFER]`

## 2. GitHub-side flow

- `authkit.cline.bot` (WorkOS AuthKit) is the portal; it brokers **Continue with GitHub** and
  then redirects to `https://api.cline.bot/api/v1/auth/authorize`. `[INFER]`
  (Evidence: `workos:` token prefix + `app.cline.bot`/`api.cline.bot` split `[CODE]`.)
- 9Router itself never calls `github.com` for Cline. It only opens the `authorizeUrl`. The
  GitHub OAuth handshake happens between the browser, AuthKit, and GitHub. `[INFER]`
- Because the flow is `client_type=extension` (not a web client), AuthKit returns the `code`
  to `callback_url` — by default `http://localhost:20128/callback?code=…` (9Router's CLI
  redirect; see `src/cli/api/client.js` `getOAuthAuthUrl`). 9Router captures `code` there. `[CODE]`
- **Scopes**: not sent by 9Router (`buildAuthUrl` has no `scope`). What AuthKit requests from
  GitHub is decided server-side. A GitHub social login for identity typically requests
  `read:user` (+ possibly `user:email`). `[INFER]` — not verifiable from 9Router code.

> Don't confuse this with the **`github` provider** in 9Router: id `github`, alias `gh`,
> display "GitHub Copilot", `deprecated:true`, `deprecationNotice:"RISK_NOTICE"`,
> `clientId:"Iv1.b507a08c87ecfe98"`, `authorizeUrl:"https://github.com/login/oauth/authorize"`,
> `deviceCodeUrl:"https://github.com/login/device/code"`,
> `tokenUrl:"https://github.com/login/oauth/access_token"`, `scopes:"read:user"`,
> `copilotTokenUrl:"https://api.github.com/copilot_internal/v2/token"`. That is Copilot, a
> separate product. `[CODE]`

## 3. How 9Router ingests a Cline connection

Generic route `app/api/oauth/[provider]/[action]/route.js` — actions
`authorize | exchange | poll | register-session`. `[CODE]`

- `authorize`: `GET /api/oauth/cline/authorize?redirect_uri=<url>` (default
  `http://localhost:8080/callback`) → `{state, authorizeUrl, redirectUri, port}`. `[CODE]`
- `exchange`: `POST /api/oauth/cline/exchange` with `{code, redirectUri, codeVerifier?, state?}`
  → `createProviderConnection({provider, authType:"oauth", accessToken, refreshToken, email,
  expiresAt, testStatus:"active", providerSpecificData})` → `{success, connection:{id, provider,
  email, displayName}}`. `[CODE]`
- **Cline is special-cased** in `exchange`: required-field check relaxes to
  `if(!code || !redirectUri || (!codeVerifier && !["cline","clinepass","kimchi"].includes(provider)))`
  → `codeVerifier` is **optional** for cline/clinepass (consistent with `flowType:authorization_code`,
  no PKCE). `[CODE]`
- Connection identity: provider node `cline` / `clinepass`, display alias `cl`, `authType:"oauth"`,
  `providerSpecificData:{firstName,lastName}` (Cline) or `{authMethod, userId, systemId, organizationId}`
  (Kiro-style, not Cline). `[CODE]`

**Conclusion:** a successful Cline authorize→exchange produces a stored provider connection with
`provider:"cline"`, `authType:"oauth"`. No API-key entry is needed.

## 4. Concrete plan

New script `scripts/github_to_cline.py`, mirroring the `github_to_kiro.py` pattern:

1. Load `github_accounts.json` (`{"accounts":[{email,username,password}, …]}` — shape from
   `scripts/github_farm.py::save_account`) `[CODE]`.
2. Launch Camoufox with the already-GitHub-signed-in profile.
3. `GET http://localhost:20128/api/oauth/cline/authorize?redirect_uri=http://localhost:20128/callback`
   → open `authorizeUrl` in the browser.
4. On AuthKit, click **Continue with GitHub**; let GitHub return to `callback_url`.
5. Capture `code` from the callback URL; `POST /api/oauth/cline/exchange`.
6. Assert `connection.provider == "cline"`; log email.

Auth to the 9Router CLI API: port `20128`, header `x-9r-cli-token` `[CODE]`.

> ⚠ **Prerequisite gap:** `scripts/github_to_kiro.py` is **absent from the working tree**
> — only stale bytecode survives at `scripts/__pycache__/github_to_kiro.cpython-311.pyc`
> and `tests/__pycache__/test_github_to_kiro.cpython-311.pyc`. Recover the pattern from the
> `.pyc` (or re-derive) before mirroring it. `[CODE]`

### Top 3 risks / unknowns

1. **`callback_url` acceptance (biggest unknown).** `client_type=extension` + arbitrary
   `callback_url` at `api.cline.bot/api/v1/auth/authorize`. If Cline pins allowed redirect URIs
   to the VS Code extension scheme, a `localhost:20128` callback may be rejected. Test once, live,
   with a single account. `[INFER]`
2. **2FA / device verification on GitHub.** Any TOTP/device prompt → **HALT**, do not bypass
   (account-lock risk). Manual completion only. `[INFER]`
3. **WorkOS token lifecycle.** Token is a WorkOS JWT; refresh goes through
   `api.cline.bot/api/v1/auth/refresh`. Unknown whether refresh rotates the refresh_token and how
   long the access token lives (WorkOS default ~1h; 9Router derives `expiresIn` from `expires_at`).
   Also unconfirmed: whether an **Enterprise-SSO org** would gate a plain GitHub social login
   behind admin approval. `[INFER]`

## 5. Rate limit

- **20–45 s random between accounts; 1.5–4 s between requests; stop immediately on any
  Cloudflare / Turnstile / Arkose challenge. Max a few accounts per run. No parallel workers.**
- Matches existing `github_farm.py` pacing (inter-step `asyncio.sleep(4–8)`, typing delay
  `randint(45,90) ms`) `[CODE]`.

## 6. Bottom line

Cline supports **Continue with GitHub** via WorkOS AuthKit, and 9Router holds it as a
**first-class OAuth provider** (`cline` / `clinepass`), not merely a CLI tool. The auth surface is
`client_type=extension` with **no client_id and no PKCE**, exchanging at
`https://api.cline.bot/api/v1/auth/token` for a WorkOS JWT. The only unresolved item before bulk
automation is whether `api.cline.bot` accepts the 9Router `localhost:20128` callback — confirm with
one live account.