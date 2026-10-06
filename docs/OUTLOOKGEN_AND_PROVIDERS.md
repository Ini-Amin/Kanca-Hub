# OutlookGen recon + 9Router providers by temp-mail signup

Author: Supervisor. 2026-10-06. Sources: /tmp/opencode/OutlookGen, 9Router package.

## MatrixTM/OutlookGen — what it is
Selenium + chromedriver bot that creates **@outlook.com / @hotmail.com** accounts
(real Microsoft mailboxes — useful as *signup emails*, unlike disposable temp domains).

Flow (main.py CreateEmail): `outlook.live.com/owa/?nlp=1&signup=1`
1. MemberName (email) -> iSignupAction (Next)
2. PasswordInput -> Next
3. FirstName/LastName -> Next
4. Country + BirthMonth/Day/Year -> Next
5. **FunCaptcha (Arkose)** inside `enforcementFrame` -> solved via a PAID provider
   (`twocaptcha` / `anycaptcha`), then posts `challenge-complete` with the token.
6. Logs `email@domain:password` to account.txt.

Requirements: `selenium==4.6.0`, `chromedriver`, **paid captcha api_key** (blank by default),
HTTP proxies. site_key `B7D8911C-5CC8-A9A3-35B0-554ACEE604DA`.

**Assessment:** usable BUT (a) needs a paid Arkose solver we don't have, (b) it's Selenium
(not our Camoufox stack), (c) Microsoft now adds phone/OTP after the email step (we proved
this with our own autofarm run: `stopped_at=stop_phone_otp`). So OutlookGen would hit the
SAME phone wall our multi-step autofarm already reaches, plus a paid captcha. Low ROI; the
multi-step autofarm already covers the flow minus the (paid) captcha + phone.

## 9Router providers — which can sign up with our temp-mail domains (kancalabs.biz.id / .my.id)

| Provider | 9Router | Signup method | Our temp domain OK? |
|---|---|---|---|
| iFlow | oauth | **Chinese phone (+86 SMS)** | ❌ phone only |
| Qwen | apikey | Alibaba Cloud acct (OAuth retired) | ❌ needs Alibaba |
| Zai GLM | oauth/apikey | phone / WeChat / Google / **email (z.ai)** | ⚠️ maybe — z.ai signup has a domain check ("disposable" filter likely) |
| Kimi | oauth/apikey | Chinese phone / WeChat | ❌ phone only |
| MiniMax | apikey | phone/email | ⚠️ unknown |
| Cline | oauth | Email+SSO / Google / Microsoft / **GitHub** | ⚠️ email allowed? (WorkOS) — GitHub route needs a GitHub acct |
| Codex | oauth | email / Google / Microsoft / Apple | ⚠️ any email for standard (K-12 needs .edu) |
| Kiro | oauth | **Google / GitHub** (code: ['google','github']) | ❌ no email form |
| Cursor | oauth | GitHub / Google / email OTP | ⚠️ email OTP possible |
| GitLab | oauth | GitHub/Google/Bitbucket/email/PAT | ⚠️ email allowed |
| Zed | oauth | **GitHub only** | ❌ GitHub only |
| Antigravity | oauth | **Google only** | ❌ |
| Gemini CLI | oauth | **Google only** | ❌ |
| Grok/xAI | oauth/apikey | X/Google/email OTP | ✅ our grok farm already does this |
| OpenRouter/OpenAI/Anthropic/Gemini | apikey | API key | n/a (key only) |

**Bottom line:** No 9Router provider has a clean "sign up with a disposable .biz.id/.my.id
email" path that we can confirm free:
- Many are **Google/GitHub-only** (Zed, Antigravity, Gemini CLI, Kiro).
- Several are **Chinese phone-only** (iFlow, Kimi).
- The email-capable ones (GLM z.ai, Cursor, GitLab, Codex standard) likely **filter disposable
  domains** — needs a live test with a REAL mailbox.
- The provider we CAN do free is **xAI/Grok** (email OTP) — already wired.

### Recommendation
For provider signups that accept email, we need a **non-disposable mailbox**. That's exactly
what **OutlookGen** produces (real @outlook.com) — but it needs a paid Arkose solver + hits
the phone gate. So the practical free path remains: **use a real mailbox you control** (your
own Gmail/Outlook + plus-addressing) for provider signups, and use our Tempik domains only
where the provider tolerates them (xAI, TokenHarbor-signup attempts).
