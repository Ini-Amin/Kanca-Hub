# Gmail via phone hardware (ADB) — LIVE finding

Author: Supervisor. Date verified live (device c8ec3d70, Chrome 154).

## Result: the phone-hardware path CAN create a Gmail
Live run of `kancahub gmail adb --count 1` drove the phone's Chrome over CDP and reached
`https://accounts.google.com/.../crossflowverification/samedevice` (the device check we
thought was a hard wall). **Clicking the on-page "Continue" advanced PAST it** into
"Add recovery email" -> … -> **`https://myaccount.google.com/u/1/`** = signed in.

Confirmed on the signed-in Google Account page:
    Account | Oliver Anderson | oliveranderson6290@gmail.com

So the device-check gate IS passable on this phone (no Xiaomi toggle needed) — the earlier
"hard wall" was because the continued click wasn't followed through the post-verify screens.

## THE BUG (real, must fix)
`gmail_adb.py` reported `status: failed:unknown` and did NOT save the account, even though
the account was created and logged in. The script's flow stops after submit and its
`state()` does not recognize the `crossflowverification/samedevice` -> (Continue) ->
recovery-email -> `myaccount.google.com` success path. Result: **the account is created but
lost (email known, password not saved).**

## Fix needed (scripts/gmail_adb.py)
1. Add the `crossflowverification/samedevice` step: click "Continue" (real mouse via CDP),
   then handle the post screens: "Add recovery email" -> click "Skip", any "I agree"/"Next"
   -> click, until `myaccount.google.com` (success) is reached.
2. Recognize success = URL matches `myaccount.google.com` (already in state() as 'success'),
   BUT the flow must actually DRIVE those screens and then return `created` + `save()`.
3. On success, save {email, password, status:"created"} to gmail_accounts.json. Today it
   saves only on `created|phone_required`; the run never got `created` because it didn't walk
   the post screens.
4. Keep the password visible in the run header so a human can recover it.

## Takeaway
Phone-hardware Gmail creation WORKS here; the script just doesn't finish/save the last
screens. Fixing (1)-(3) would make `kancahub gmail adb` produce saved accounts.
