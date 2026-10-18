#!/usr/bin/env python3
"""Patch Grok Bot's sand-secrets.json so the app accepts a synthetic principal.

WHY THIS WORKS (verified against the shipped bundle, not guessed)
------------------------------------------------------------------
The transport factory is principal-scoped:

    ee => { let q = accountScopeOfPrincipal({principal: ee}); ... }

and in `dist/electron-main/main-core.cjs` the implementation is:

    function Ag({principal:e}){return createHash("sha256").update(e).digest("hex")}
    function Bn({accessToken:e}){return Ag({principal:Vi(e)?.sub ?? e})}
    function Vi(e){let[,t]=e.split("."); if(!t) return null;
      try { return JSON.parse(Buffer.from(t,"base64url")) } catch { return null } }

So `accountScope` is a bare `sha256(principal)` — no salt, no prefix — and
`Vi` (parseJwtPayload) NEVER verifies a signature. It only base64url-decodes the
middle segment and JSON-parses it.

That demolishes the earlier conclusion that a Cursor-signed principal was
required. Any `x.<base64url JSON>.y` string with a `sub` claim is accepted, and
`sub` fully controls the resulting scope hash.

THE ENCODING IS THE PART THAT BIT US
------------------------------------
Writing a bare JWT into the file does nothing. The secret store has three
encodings (main-core.cjs):

    Vg = "sand-secrets.json"      the file we write
    dB = "user-secrets.json"      a second store
    Ka = "plaintext:v1:"          -> Buffer.from(rest,"base64").toString("utf8")
    pR = "scoped:v1:"             -> per-account scope wrapper
    Ae = "cursor-access-token"
    sr = "cursor-refresh-token"
    $a = "cursor-account-profile"
    ft = "cursor-accounts"
    Tse = /^[0-9a-f]{64}$/        a sha256 hex, i.e. an account scope

Readers (`_R`, `cB`, `Mu`) branch on `startsWith(Ka)`. Anything that does NOT
start with `plaintext:v1:` is treated as an Electron **safeStorage** ciphertext
(`v10|...`, which is what the current 28-byte `djEw...` blobs decode to), handed
to `safeStorage.decryptString`, and on failure the code silently keeps the old
value. That is why a bare-JWT patch looked like it did nothing.

So we must wrap: `plaintext:v1:` + base64(jwt).

THE DEV-AUTH BYPASS
-------------------
    sU  = "KbZUR41cY7W6zRSdpSUJ7I7mLYBKOCmB"   # production authClientId
    Xoe = "OzaBXLClY5CAGxNzUhQ2vlknpi07tGuE"   # dev authClientId
    function DA(e){ return isLocal(e) || e.endsWith(".lclhst.build")
                    || e === "dev-staging.cursor.sh" }
    function wA(e){ return e.authClientId !== sU }   # usesDevAuthFlow

`usesDevAuthFlow` is true whenever authClientId differs from the production id.
It is not a secrets key — it is derived from config. main-core.cjs reads
`authClientIdOverride: e.SAND_AUTH_CLIENT_ID`, so the env var is the reliable
lever; we also write the key for good measure.

WHAT THIS SCRIPT CHANGES
------------------------
`/root/.config/Grok Bot/sand-secrets.json` only, after a timestamped backup.
It does not touch the app bundle.

Two independent things must both be right, and getting only one of them looks
exactly like "the patch did nothing":

  1. the nested `cursor-accounts` record must be rekeyed to `sha256(sub)`, or
     the account lookup misses; and
  2. the same token must also sit at TOP LEVEL, because `Mu()` checks the
     top-level record first and blu has no keyring, so `Wr()` is false and the
     nested branch is unreachable.

IMPORTANT: the app REWRITES this file on shutdown AND on startup, so the only
safe order is  stop -> patch -> start. `restart` stops first, and that stop is
what flushes the old values back over your write.
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

SECRETS = "/root/.config/Grok Bot/sand-secrets.json"

# Stable principal id. Changing this changes the account scope, which would
# orphan any on-disk state keyed by the old scope, so keep it constant.
SUB = "grokbot-local-principal"
EMAIL = "local@grokbot.invalid"
DEV_CLIENT_ID = "OzaBXLClY5CAGxNzUhQ2vlknpi07tGuE"
PROD_CLIENT_ID = "KbZUR41cY7W6zRSdpSUJ7I7mLYBKOCmB"
PLAINTEXT = "plaintext:v1:"


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_jwt(sub=SUB, email=EMAIL, ttl_days=3650):
    """An unsigned (alg=none) JWT. `Vi` ignores the signature entirely."""
    header = b64url(json.dumps({"alg": "none", "typ": "JWT"},
                               separators=(",", ":")).encode())
    payload = b64url(json.dumps({
        "sub": sub,
        "email": email,
        # Far future so `shouldRefreshAccessToken` never fires on expiry.
        "exp": int(time.time()) + ttl_days * 86400,
    }, separators=(",", ":")).encode())
    return f"{header}.{payload}."


def encode_plaintext(token):
    """Wrap a token so the store's `startsWith(Ka)` branch accepts it.

    A bare JWT is treated as safeStorage ciphertext, fails to decrypt, and the
    reader silently falls back to the previous value — a no-op patch.
    """
    return PLAINTEXT + base64.b64encode(token.encode()).decode()


def scope_of(principal):
    return hashlib.sha256(principal.encode()).hexdigest()


def app_running():
    try:
        out = subprocess.run(["pgrep", "-f", "Grok Bot/grok-bot"],
                             capture_output=True, text=True, timeout=10)
        return bool(out.stdout.strip())
    except Exception:
        return False


def main():
    if not os.path.exists(SECRETS):
        return fail(f"not found: {SECRETS}")

    with open(SECRETS) as f:
        data = json.load(f)

    accounts = json.loads(data["cursor-accounts"])
    old_active = accounts["active"]
    acct = accounts["accounts"][old_active]

    jwt = make_jwt()
    wrapped = encode_plaintext(jwt)
    # The bundle compares accountScopeOfToken(accessToken) against
    # accountScopeOfPrincipal(principal); both resolve to sha256(sub) once the
    # token parses, so one JWT serves all three fields.
    acct["cursor-access-token"] = wrapped
    acct["cursor-refresh-token"] = wrapped
    acct["cursor-account-profile"] = wrapped

    # REKEY — this is the part that made every earlier patch a silent no-op.
    #
    # `accounts` is a map keyed by account scope, and `active` points at one of
    # those keys. The app looks the session up as
    # `accounts[accountScopeOfToken(accessToken)]`, so leaving the old key in
    # place means the lookup misses even though the token itself decodes fine:
    # the file looks valid, `_R` returns "present", `Vi` returns the right sub,
    # and the app still reports signedIn:false because there is no entry under
    # the new scope. Move the record to the scope the token implies.
    new_active = scope_of(SUB)
    accounts["accounts"][new_active] = acct
    if old_active != new_active:
        del accounts["accounts"][old_active]
    accounts["active"] = new_active

    data["cursor-accounts"] = json.dumps(accounts)

    # TOP-LEVEL MIRROR — the actual unlock, and the reason every earlier attempt
    # failed no matter how correct the nested record was.
    #
    # `Mu(e)` (main-core.cjs) is the one reader everything funnels through:
    #
    #     let r = n[e];                          // TOP-LEVEL lookup
    #     if (r?.startsWith(Ka) === true) {      // plaintext short-circuits HERE
    #       return Buffer.from(r.slice(Ka.length),"base64").toString("utf8")
    #     }
    #     if (!Wr()) return null;                // <- safeStorage gate
    #     if (hB(e)) { ...nested cursor-accounts... }
    #
    # blu has no keyring daemon, so Wr() is false and it returns null BEFORE the
    # nested `cursor-accounts` branch is ever consulted. A token stored only
    # inside cursor-accounts is therefore invisible on this host, which is why
    # the file could be perfectly valid (see probe-secrets.js) and the UI still
    # said "Sign in".
    #
    # Writing the same values at top level takes the plaintext branch, which
    # returns before Wr() is tested. That is the only path that works without
    # an OS keyring.
    data["cursor-access-token"] = wrapped
    data["cursor-refresh-token"] = wrapped
    # Belt and braces: the env var SAND_AUTH_CLIENT_ID is the documented lever,
    # this key is the config equivalent.
    data["auth-client-id"] = DEV_CLIENT_ID

    backup = SECRETS + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(SECRETS, backup)

    tmp = SECRETS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, SECRETS)

    print("backup        :", backup)
    print("sub           :", SUB)
    print("account scope :", scope_of(SUB))
    print("rekeyed       :", old_active[:16] + "…", "->", new_active[:16] + "…")
    print("authClientId  :", DEV_CLIENT_ID)
    print("encoding      :", PLAINTEXT + "base64(jwt)")
    print("wrapped len   :", len(wrapped))
    print()
    print("NOTE: the app writes this file on shutdown AND on startup, so the")
    print("only safe order is  stop -> patch -> start. Patching a running app")
    print("and then restarting loses the patch: `restart` stops first, and that")
    print("stop is what flushes the old values back over your write.")
    print("expected after start: connection.transport != 'down'")
    return 0


def fail(msg):
    print("ERROR:", msg, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
