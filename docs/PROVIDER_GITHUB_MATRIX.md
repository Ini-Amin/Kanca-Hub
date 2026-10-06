# 9Router Provider GitHub Social Login & Farm Route Matrix

**Scope & Purpose**: Comprehensive analysis of identity providers supported by 9Router (`0.5.95`), determining which providers support "Continue with GitHub" (Social OAuth) versus requiring email signup, Google accounts, or phone verification. This matrix defines the routing strategy for account farming across the KancaHub toolkit.

---

## 1. Executive Summary & Strategic Decision

| Egress / Identity Farm Path | Feasibility & Automation | Primary Target Providers |
|---|---|---|
| **Path 1: Bulk GitHub Social OAuth** | **High Automation** (via `github_farm.py` + Camoufox/Playwright OAuth) | **Cline**, **Kiro AI**, **Zed**, **GitHub Copilot**, **Cursor IDE**, **GitLab Duo**, **Kimchi** |
| **Path 2: Domain Email Relay** | **Full Automation** (via Supabase relay / Tempik on `kancalabs.biz.id` / `kancalabs.my.id`) | **Cloudflare Workers AI**, **Token Harbor**, **Grok / xAI**, **Claude Code** |
| **Path 3: School Mailbox (.edu / Academic)** | **Semi-Automated** (via `binus.ac.id` plus-addressing & SheerID) | **OpenAI Codex / ChatGPT K-12**, **GitHub Student Pack** (unlocks free Copilot) |
| **Path 4: Google Identity (Gmail)** | **Hardware/ADB Automated** (via `gmail_adb.py` on Android) | **Antigravity**, **Gemini CLI**, **Google Cloud Code** |
| **Path 5: Phone / SMS (+86 Domestic)** | **Infeasible for Bulk Farming** (requires Chinese phone SMS) | **iFlow AI**, **CodeBuddy CN**, **Kimi**, **Zai GLM**, **Xiaomi MiMo** |

---

## 2. Master Provider Support Matrix

> **Legend**:
> - **[CODE]**: Verified directly from 9Router source code / routes / endpoints.
> - **[UI]**: Verified from provider web UI / authentication portal / user screenshot.
> - **[INFER]**: Inferred from platform parent company identity architecture.

| Provider | 9Router ID | Category in 9Router | Login Methods Seen | GitHub Login Available? | Email Signup Needs Special Domain? | Notes |
|---|---|---|---|---|---|---|
| **Cline** | `cline`, `clinepass` | `oauth` | GitHub, Google, Microsoft, Email+SSO | **YES** `[UI]` | **No** (Standard / Any) | `authkit.cline.bot` (WorkOS) offers prominent "Continue with GitHub". 9Router handles OAuth callback exchange. |
| **OpenAI Codex** | `codex` | `oauth` | Email (password/OTP), Google, Microsoft, Apple | **NO** `[UI]` | **YES for K-12** (School .edu domain required) | `auth.openai.com` does NOT offer GitHub. Standard accounts take any email; K-12 teacher upgrade requires school domain + SheerID. |
| **Kiro AI** | `kiro` | `free` / `oauth` | GitHub, Google, AWS Builder ID, AWS IAM IdC | **YES** `[CODE]` | **No** (Standard / Any) | 9Router's `/api/oauth/kiro/social-authorize` and `/social-exchange` explicitly implement `provider: "github"`. |
| **GitHub Copilot** | `github` | `oauth` | GitHub Native (Device Code) | **NATIVE** `[CODE]` | **YES for Free Student Tier** | Uses `github.com/login/device/code`. Free unlimited access requires GitHub Student Developer Pack approval. |
| **Zed** | `zed` | `oauth` | GitHub Native | **NATIVE** `[CODE]` | **No** (Uses GitHub account) | Zed's identity architecture is 100% GitHub (`zed.dev/native_app_signin`). Key stored in system keyring as `zed-github-account`. |
| **Cursor IDE** | `cursor` | `oauth` | GitHub, Google, Email OTP | **YES** `[UI]` | **No** (Standard / Any) | `cursor.com` authenticator offers "Continue with GitHub". 9Router extracts session token from local `state.vscdb`. |
| **GitLab Duo** | `gitlab` | `oauth` | GitHub, Google, Bitbucket, Email, PAT | **YES** `[UI]` | **No** (Standard / Any) | `gitlab.com` offers "Sign in with GitHub". 9Router supports PKCE OAuth or Personal Access Token (PAT). |
| **Kimchi** | `kimchi` | `freeTier` / `oauth` | GitHub, Google, Email (CAST AI) | **YES** `[INFER]` | **No** (Standard / Any) | `app.kimchi.dev` uses CAST AI developer cloud authentication. |
| **Antigravity** | `antigravity` | `oauth` | Google Account Only | **NO** `[CODE]` | **Google Account Only** | `accounts.google.com/o/oauth2/v2/auth`. Connects to Google Cloud Code / Gemini 3.8. Must use Google identity. |
| **Gemini CLI** | `gemini-cli` | `free` / `oauth` | Google Account Only | **NO** `[CODE]` | **Google Account Only** | Google Cloud Code internal OAuth (`accounts.google.com`). Strictly Google identity. |
| **Claude Code** | `claude` | `oauth` | Google, Email (magic link / OTP) | **NO** `[UI]` | **No** (Can use mail relay) | `claude.ai/oauth/authorize`. Anthropic does not support GitHub social login. Accepts our domain relay. |
| **iFlow AI** | `iflow` | `oauth` | Chinese Phone (+86 SMS), WeChat | **NO** `[CODE]` | **N/A (Phone Only)** | Code sets `extraParams: { loginMethod: "phone", type: "phone" }`. Bypasses email entirely; requires SMS OTP. |
| **Qwen Code / Alibaba** | `qwen`, `alicode`, `alicode-intl` | `apikey` (OAuth deprecated) | Alibaba Cloud / Aliyun Account | **NO** `[CODE]` | **No** (Alibaba Cloud account) | Qwen OAuth free tier was discontinued 2026-04-15. 9Router now routes via DashScope / Bailian API keys. |
| **Zai GLM Coding** | `glm`, `glm-cn` | `oauth` / `apikey` | Phone, WeChat, Google, Email (`z.ai`) | **NO** `[UI]` | **No** (Standard / Any) | Zhipu AI (`zcode.z.ai` / `chat.z.ai`). Device login does not provide GitHub social auth. |
| **Xiaomi MiMo** | `xiaomi-mimo`, `mimo-free` | `oauth` / `apikey` | Xiaomi Account (Phone, Email, WeChat) | **NO** `[CODE]` | **No** (Mi Account) | Redirects to `account.xiaomi.com`. Proprietary ECDH+AES-GCM callback handshake. No GitHub login. |
| **Grok CLI / xAI** | `grok-cli`, `xai` | `oauth` / `apikey` | X (Twitter), Google, Email OTP | **NO** `[UI]` | **No** (Can use mail relay) | `auth.x.ai` device code flow. Supports X/Twitter, Google, Email OTP. Our `grok_driver.py` already automates this. |
| **CodeBuddy** | `codebuddy-cn`, `codebuddy-intl` | `oauth` | Tencent Cloud, WeChat, QQ (CN) / Email, Google (Intl) | **UNKNOWN / NO** `[INFER]` | **No** (Tencent / Standard) | Tencent Cloud developer ecosystem (`copilot.tencent.com`). No evidence of GitHub OAuth. |
| **Kimi** | `kimi` | `oauth` / `apikey` | Phone SMS, WeChat QR | **NO** `[UI]` | **N/A (Phone Only)** | Moonshot AI (`auth.kimi.com/api/oauth/device_authorization`). Geared towards Chinese mobile identity. |
| **Qoder** | `qoder`, `qoder-cn` | `oauth` / `apikey` | Email, Google, Qoder SSO | **UNKNOWN** `[INFER]` | **No** (Standard / Any) | PKCE device flow via `qoder.com/device/selectAccounts`. |
| **Muse** | `muse` | `oauth` / `apikey` | Meta Account (FB / IG / Meta) | **NO** `[CODE]` | **Meta Account Only** | `auth.meta.com/oidc/device/authorization/`. Strictly Meta ecosystem identity. |
| **Cloudflare Workers AI** | `cloudflare-ai` | `freeTier` / `apikey` | Email + Password + Turnstile | **NO** `[CODE]` | **No** (Can use mail relay) | `dash.cloudflare.com`. Handled by our native `Auto-FreeCF` signup pipeline. |
| **Token Harbor** | `tokenharbor` | `apikey` | Email + Password + Turnstile | **NO** `[CODE]` | **No** (Can use Tempik / relay) | Next.js Server Action signup. Free Turnstile solved via our in-browser `turnstile_camoufox.py`. |

---

## 3. Deep-Dive on Target Providers (Evidence vs. Inference)

### 3.1. Cline (`cline`, `clinepass`)
* **9Router Architecture**: Next.js app routes `https://api.cline.bot/api/v1/auth/authorize` with PKCE code exchange to `https://api.cline.bot/api/v1/auth/token`.
* **Login Evidence**:
  - `[UI Evidence]`: Verified via live AuthKit portal (`authkit.cline.bot`). Screen presents four explicit options:
    1. *Email + Enterprise SSO*
    2. *Continue with Google*
    3. *Continue with Microsoft*
    4. *Continue with GitHub*
* **Farming Applicability**: **Tier 1 (Optimal)**. Bulk GitHub accounts generated by `github_farm.py` can authorize Cline in a headless browser, receive Cline tokens, and register into 9Router.

### 3.2. OpenAI Codex (`codex`)
* **9Router Architecture**: OAuth Authorization Code with PKCE on `https://auth.openai.com/oauth/authorize` (`clientId: app_EMoamEEZ73f0CkXaXp7hrann`, fixed loopback callback port `1455`).
* **Login Evidence**:
  - `[CODE Evidence]`: Upstream is `auth.openai.com`.
  - `[UI Evidence]`: `auth.openai.com` only renders buttons for Google, Microsoft, Apple, and native Email. **GitHub social login does not exist.**
* **Domain Restrictions**:
  - Standard ChatGPT accounts accept any email.
  - **ChatGPT for Teachers / K-12 Workspace** strictly validates the email domain. As proven in `K12_FINDINGS.md`, non-educational domains fail with:
    > *"Your email domain isn't eligible for teacher verification. Please sign in and reverify with your school-issued email."*
* **Farming Applicability**: **Must use Path 3 (Academic Mailbox: `binus.ac.id`)**. GitHub accounts cannot be used directly for Codex.

### 3.3. Kiro AI (`kiro`)
* **9Router Architecture**: Dual flow support in 9Router:
  1. AWS IAM Identity Center / Builder ID (`oidc.us-east-1.amazonaws.com`).
  2. Social authentication backend (`prod.us-east-1.auth.desktop.kiro.dev`).
* **Login Evidence**:
  - `[CODE Evidence]`: 9Router source `/api/oauth/kiro/social-authorize/route.js` explicitly validates:
    ```javascript
    if (!["google", "github"].includes(provider))
        return NextResponse.json({ error: "Invalid provider. Use 'google' or 'github'" }, { status: 400 });
    ```
    And `/api/oauth/kiro/social-exchange/route.js` exchanges the code and sets `authMethod: "github"`.
* **Farming Applicability**: **Tier 1 (Direct Match)**. 9Router has first-class code support for GitHub social login to mint Kiro connection tokens.

### 3.4. GitHub Copilot (`github`)
* **9Router Architecture**: Standard GitHub Device Code OAuth flow (`https://github.com/login/device/code`, client ID `Iv1.b507a08c87ecfe98`), requesting `copilotTokenUrl` at `https://api.github.com/copilot_internal/v2/token`.
* **Login Evidence**:
  - `[CODE Evidence]`: The identity provider is GitHub itself.
* **Access Requirements**: An ordinary free GitHub account does not have Copilot enabled by default. It requires either:
  1. Active GitHub Copilot subscription/trial.
  2. **GitHub Student Developer Pack** (unlocked for free via school verification in `github_farm.py`).
* **Farming Applicability**: **Direct**. Bulk accounts that complete the Student Developer Pack application immediately unlock Copilot models (`gpt-5`, `claude-4.5-sonnet`, `gemini-2.5-pro`) in 9Router.

### 3.5. Zed (`zed`)
* **9Router Architecture**: Secret import from system keyring (`secret-tool` / macOS keychain) or manual import.
* **Login Evidence**:
  - `[CODE Evidence]`: `app/.next-cli-build/server/app/api/oauth/zed/auto-import/route.js` specifically queries the label `zed-github-account`.
  - `[UI Evidence]`: Zed editor has no native password authentication; all user accounts are GitHub accounts authenticated via `https://zed.dev/native_app_signin`.
* **Farming Applicability**: **Tier 1**. Any valid GitHub account is an instant Zed account.

### 3.6. Antigravity (`antigravity`) & Gemini CLI (`gemini-cli`)
* **9Router Architecture**: Google OAuth 2.0 Authorization Code (`https://accounts.google.com/o/oauth2/v2/auth`), targeting `cloudcode-pa.googleapis.com`.
* **Login Evidence**:
  - `[CODE Evidence]`: Endpoints strictly point to `accounts.google.com` and `googleapis.com`.
  - `[INFER]`: Google Cloud Code services require a Google account. GitHub accounts cannot be used.
* **Farming Applicability**: **Requires Path 4 (Google / Gmail accounts)** via `gmail_adb.py`.

### 3.7. iFlow AI (`iflow`)
* **9Router Architecture**: Authorize endpoint `https://iflow.cn/oauth` with fixed client ID `10009311001`.
* **Login Evidence**:
  - `[CODE Evidence]`: 9Router explicitly specifies `extraParams: { loginMethod: "phone", type: "phone" }`.
  - `[UI Evidence]`: iFlow China requires an active SMS code sent to a mainland China mobile carrier (+86).
* **Farming Applicability**: **Not Farmable via GitHub or Email**. Excluded from automated pipelines.

### 3.8. Qwen Code (`qwen`, `alicode`, `alicode-intl`)
* **9Router Architecture**: Static model mapping to DashScope / Bailian API endpoints (`coding-intl.dashscope.aliyuncs.com`).
* **Login Evidence**:
  - `[CODE Evidence]`: `chunks/3966.js` confirms:
    > *"Qwen OAuth free tier was discontinued on 2026-04-15. Use 9Router with alicode/openrouter/anthropic/gemini providers instead."*
  - Requires standard Alibaba Cloud API keys, not social OAuth.
* **Farming Applicability**: **API Key only**.

### 3.9. Zai GLM Coding (`glm`)
* **9Router Architecture**: Device initialization at `https://zcode.z.ai/api/v1/oauth/cli/init`.
* **Login Evidence**:
  - `[CODE Evidence]`: Connects to Zhipu AI (`z.ai`).
  - `[UI Evidence]`: Supports Chinese phone number, WeChat login, or email on international portal. No GitHub button.
* **Farming Applicability**: **Must use Email/API key path**.

### 3.10. Xiaomi MiMo (`xiaomi-mimo`)
* **9Router Architecture**: Custom ECDH key-exchange with encrypted `u` parameter callback, redirecting through `account.xiaomi.com`.
* **Login Evidence**:
  - `[CODE Evidence]`: Handled via Xiaomi Mi Account (`account.xiaomi.com`).
  - `[UI Evidence]`: Xiaomi does not federate with GitHub.
* **Farming Applicability**: **Must use Xiaomi account path**.

---

## 4. Recommended Farm Path Strategy

```
                          ┌──────────────────────────┐
                          │   KancaHub Farm Engine   │
                          └─────────────┬────────────┘
                                        │
         ┌───────────────────┬──────────┴─────────┬───────────────────┐
         ▼                   ▼                    ▼                   ▼
   [PATH 1: GITHUB]   [PATH 2: RELAY]      [PATH 3: SCHOOL]     [PATH 4: GMAIL]
  github_farm.py     auto-freecf / tempik   binus.ac.id (.edu)   gmail_adb.py
         │                   │                    │                   │
         ├► Cline            ├► Cloudflare AI     ├► OpenAI Codex     ├► Antigravity
         ├► Kiro AI          ├► Token Harbor      │  (ChatGPT K-12)   └► Gemini CLI
         ├► Zed              ├► Grok / xAI        └► GitHub Student
         ├► Cursor           └► Claude Code          (Free Copilot)
         ├► GitLab Duo
         └► Kimchi
```

### Path 1: Bulk GitHub Social OAuth (Priority #1 for Expansion)
* **Mechanism**: Use `scripts/github_farm.py` (Camoufox + clean egress gateway) to create GitHub accounts.
* **Downstream Integration**:
  1. **Cline**: Direct OAuth authorization on `authkit.cline.bot` via GitHub button.
  2. **Kiro AI**: Direct call to 9Router `/api/oauth/kiro/social-authorize?provider=github`.
  3. **Zed**: Automated token extraction from `zed.dev/native_app_signin`.
  4. **Cursor**: Social sign-in to extract `state.vscdb` session tokens.
  5. **GitLab**: 1-click social sign-in.
* **Advantage**: Bypasses email verification, Turnstile on third-party sites, and anti-disposable domain filters.

### Path 2: Custom Domain Email Relay (`kancalabs.biz.id` / `my.id`)
* **Mechanism**: Supabase edge function relay (`/functions/v1/temp-mail-api`) and Cloudflare Worker Tempik (`tempik.kancalabs.workers.dev`).
* **Downstream Integration**:
  1. **Cloudflare Workers AI**: `pipeline.py` creates accounts and API tokens.
  2. **Token Harbor**: `cli.py` creates accounts with free Turnstile bypass (`turnstile_camoufox.py`).
  3. **Grok / xAI**: `grok_driver.py` registers accounts with automatic OTP extraction.
  4. **Claude Code**: Direct email signup.

### Path 3: Academic School Mailbox (`binus.ac.id`)
* **Mechanism**: Outlook Web session automation (`school_mail_browser.py`, `sheerid_link_finder.py`) with plus-addressing (`raymondi+gh<N>@binus.ac.id`).
* **Downstream Integration**:
  1. **OpenAI Codex**: ChatGPT for Teachers K-12 workspace (strictly requires school domain).
  2. **GitHub Student Developer Pack**: Unlocks 100% free GitHub Copilot access for Path 1 accounts.

### Path 4: Google Accounts (Android ADB)
* **Mechanism**: Real mobile hardware automation (`gmail_adb.py`) via physical Xiaomi phone and Chrome CDP.
* **Downstream Integration**:
  1. **Antigravity**: Google Cloud Code Gemini 3.8 models.
  2. **Gemini CLI**: Free Google AI quota.

---

## 5. Summary & Action Plan

1. **Focus GitHub Account Farm on**:
   - **Cline** (`authkit.cline.bot` supports GitHub 1-click).
   - **Kiro AI** (9Router has native code support for Kiro GitHub social exchange).
   - **Zed** (native GitHub authentication).
   - **GitHub Student Developer Pack** (converts GitHub account into active Copilot subscription).
2. **Do NOT attempt GitHub Social Login for**:
   - **Codex** (requires email / Google / Apple / Microsoft, and school domain for K-12).
   - **Antigravity** (strictly Google Account).
   - **iFlow** (strictly Chinese phone SMS).
   - **Xiaomi MiMo** (strictly Xiaomi Mi Account).
   - **GLM / Zai** (strictly Z.ai account).
