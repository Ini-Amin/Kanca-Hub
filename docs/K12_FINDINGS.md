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
