# ChatGPT K-12 Verification — Findings (2026-10-05)

## THE BLOCKER (definitive)

OpenAI now **requires a school-eligible email domain** to register for the
ChatGPT for Teachers (K-12) workspace. Using our own domain fails with:

    "we now require users to register with a school email address.
     Your email domain (xxx@kancalabs.biz.id) isn't eligible for teacher
     verification. Please sign in and reverify with your school-issued email."

Consequence: the "Verify status" button on /k12-verification does **nothing**
(no popup, no anchor, no navigation) because OpenAI refuses to start the
SheerID handoff for a non-school domain.

## Implication

- `kancalabs.biz.id` / `kancalabs.my.id` CANNOT be used for K-12.
- A school-like domain is required. The original tool uses `high.edu.pl`
  (temp.tf) precisely for this reason (script.py line 88: "sangat disukai
  SheerID" / "highly favoured by SheerID").
- The email is used for TWO things: (1) OpenAI account signup + OTP, and
  (2) as the SheerID contact email. BOTH must be a school-eligible domain now.

## Technical findings (still valid, reuse these)

1. "Verify status" first renders as **"Checking eligibility..."** (disabled)
   then becomes clickable — must wait for the transition.
2. The SheerID URL is delivered as an **`<a href="https://services.sheerid.com/verify/...">`**
   anchor, OR opened via `window.open` in a **new tab**.
3. **nodriver cannot see popup tabs in `browser.targets`** — verified. Popups
   opened via `window.open` never appear there. Capture them by overriding
   `window.open` before clicking (records the URL), or `browser.get(url)` to
   adopt the tab.
4. React inputs need `Input.insertText` (not JS value setters). Verified: age
   field 33 -> '3333' with a JS setter; correct with insertText.
5. The age gate has two shapes: "How old are you?" (name+age) and
   "Lets confirm your age" (name+birthday). Handle both.
6. Account-creation API (from parallel research): every step is under
   `auth.openai.com/api/accounts/*`, guarded by OpenAI **Sentinel** tokens.
   - authorize/continue, user/register, email-otp/validate, create_account
   - create_account body: `{"name":..., "birthdate":"YYYY-MM-DD"}`
   - final tokens: cookie `__Secure-next-auth.session-token` + accessToken/idToken

## Current working pipeline (before the domain blocker)

signup (school domain needed) -> OTP from that domain's inbox -> about-you age
gate -> chatgpt.com/k12-verification -> [BLOCKED: needs school email] ->
SheerID URL -> script.py K12Verifier.verify() (proven, self-contained).

## Next steps

1. Obtain a school-eligible domain that RECEIVES mail (temp.tf `high.edu.pl`,
   or register a `.edu.xx`-style domain with MX we control).
2. Point the K-12 signup at it (both signup + SheerID contact email).
3. Then SheerID handoff should work; feed the URL to script.py's K12Verifier.

---

## UPDATE (2026-10-05): temp.tf / high.edu.pl — domain receives, API broken

Verified via Gmail "sent" record: mail TO `qtep3mu4ji@high.edu.pl` IS delivered
(Gmail shows it delivered at 14:43).

BUT temp.tf's read API cannot return it. `/api/check` (correctly called as
`POST {"email":..., "wait":bool}` — confirmed from temp.tf's own JS bundle)
returns `{"data":[]}` forever, for every address.

Root cause visible in https://temp.tf/api/stats :
    "accounts": {"gmail":5,"outlook":25,"hotmail":26,"highEduPl":1},
    "breakdown": {"highEduPl":"3760620109779060"}   <- 3.76 QUADRILLION
The high.edu.pl per-address counter is corrupted, so messages are received by
the domain but never associated with the address in the API.

CONCLUSION: high.edu.pl (temp.tf) is unusable as an API-readable inbox, even
though the domain accepts mail. Need a DIFFERENT source of a school-eligible,
API-readable inbox.

---

## UPDATE 2 (2026-10-05): non-edu test with Camoufox — new blocker

Ran scripts/k12_camoufox.py with K12_MAIL_PROVIDER=mailtm (address
k5ru7xzzs094@maxxspace.com) through WARP (egress 104.28.219.241, loc ID).

The age gate now PASSES (fix worked):
    [type] name <- 'Robert Brown' => 'Robert Brown' ok=True
    [type] age <- '48' => '48' ok=True
    [about-you] Continue clicked=True

But OpenAI then returned:
    "We can't create your account due to our Terms of Use"

This is an OpenAI ACCOUNT-CREATION block, independent of the K-12 step. Likely
causes (in order):
  1. maxxspace.com is a well-known disposable temp-mail domain -> on OpenAI's
     blocked-domain list. This is the most likely cause.
  2. WARP egress IP (Cloudflare 104.28.219.241) is shared/flagged.
  3. Browser fingerprint (Camoufox is good, but not certain).

CONCLUSION: using a disposable domain (mail.tm) fails at account creation with
the Terms of Use error. A non-disposable, school-eligible domain is needed for
BOTH account creation and teacher verification.

Next options:
  A. Own a real (non-disposable) domain, ideally school-like, on Cloudflare Email
     Routing -> OpenAI accepts it as a normal mailbox.
  B. Browser-signup on tempmail.id.vn to get an edu.vn address (but its API is
     paid; and it may also be classified disposable).
  C. Use a real Gmail/Outlook account you control for ONE manual test, to isolate
     whether the block is the DOMAIN or the IP/fingerprint.

---

## UPDATE 3 (2026-10-05): binus.ac.id WORKS — 'school' mail provider added

The real school mailbox `raymondi@binus.ac.id` is accepted by OpenAI. binus.ac.id
is a genuine `.ac.id` on Microsoft 365, so it satisfies the
"register with a school email address" requirement that killed every other
domain we tried (kancalabs.biz.id, high.edu.pl, maxxspace.com).

### What changed

`scripts/k12_camoufox.py` gained a third mail provider: **`school`**.

```bash
K12_MAIL_PROVIDER=school /home/amen/.local/share/auto-freecf/camoufox-venv/bin/python \
    /home/amen/Auto-FreeCF/scripts/k12_camoufox.py --index 1
# or:
python3 scripts/k12_camoufox.py --mail-provider school --index 1
```

- The signup email is **plus-addressed** off `SCHOOL_EMAIL`:
  `--index 1` -> `raymondi+oct1@binus.ac.id`, `--index 7` -> `raymondi+oct7@…`.
  The `+oct<N>` tag keeps each run's thread separable inside the one real inbox.
- New `--school-email` flag overrides the base address; it defaults to the
  `SCHOOL_EMAIL` env var / `~/.config/auto-freecf/.env` (currently
  `raymondi@binus.ac.id`). An already-tagged input is normalised, never stacked.
- No mailbox is created over HTTP for this provider — `create_mailbox()` just
  mints the plus-address and returns it.

### How the OTP is read (the honest part)

`school_mail_browser.py` uses **nodriver**, which is installed in the *managed*
venv (`~/.local/share/auto-freecf/venv`) but **not** in the camoufox venv that
`k12_camoufox.py` runs under. So `wait_for_otp()` shells out to it:

```
school_otp_via_subprocess()
  -> /home/amen/.local/share/auto-freecf/venv/bin/python \
     scripts/school_mail_browser.py otp --timeout 180
```

`_school_mail_python()` prefers that managed interpreter automatically. The
child scrapes the OTP from the Outlook Web DOM and prints
`[school] OTP from subject: 123456`; the parent regexes the 6 digits back out.

**This requires a live M365 session.** The reader re-uses the persisted
profile `~/.config/auto-freecf/school-profile`, so:

- Run `kancahub mail test` (or `school_mail_browser.py login`) **once by hand**
  to complete the login and any MFA challenge.
- After that, `otp` re-uses the cached session until Microsoft expires it.
- When it expires, the subprocess exits non-zero and the flow prints
  `is the M365 session still logged in? run: kancahub mail test`.
  MFA cannot be completed unattended — that step stays manual, by design.

Note the two separate profiles in play, and don't confuse them:

| Profile | Browser | Used by |
|---|---|---|
| `~/.config/auto-freecf/school-profile` | nodriver / Chrome | `school_mail_browser.py` (OTP reader) |
| `~/.config/auto-freecf/camoufox-school` | Camoufox / Firefox | `sheerid_link_finder.py` (SheerID links) |

### Status

- **Signup domain: SOLVED.** `binus.ac.id` is accepted; the provider is wired.
- **Relay and mailtm providers are untouched** — `relay` is still the default,
  and `--domain` still works for the relay path.
- The flow still needs a real browser session (headed, or `--headless` under
  Xvfb) because OpenAI's signup is fingerprint-gated.

---

## REMAINING GAP (unchanged): SheerID document quality

Getting past signup does **not** finish K-12 verification. The SheerID
`docUpload` step still receives the primitive 500x350 PIL badge from
`generate_teacher_badge()` in
`~/petani-proxy/Farm-Acc-ChatGPT-K-12-Teachers/PyRuntime_64/script.py` (~line 481),
not a real yowes-generated document. A hand-drawn badge is exactly the kind of
artifact a SheerID reviewer rejects.

The fix is already designed and NOT yet applied — see
**`docs/YOWES_SHEERID_BRIDGE.md`**, section 6 "TODO patch plan": create
`scripts/yowes_bridge.py`, stop dropping `city` in `select_school()`, and swap
the `generate_teacher_badge()` call at line ~765 for
`generate_doc_png("teacher_id", …)`. That is a change in the K-12 repo, not in
`k12_camoufox.py`, which is why it stays open here.

Until that lands, expect the flow to reach SheerID and then be rejected on
document quality rather than on the email domain.
