// Patch Grok Bot's secret store with REAL safeStorage ciphertext.
//
// ── Why every earlier attempt failed ────────────────────────────────────────
// The store's reader, `Mu(e)` in main-core.cjs, ends with:
//
//     if (!Wr()) return null;                       // no keyring -> give up
//     if (hB(e)) {                                  // hB = access/refresh/team
//       n = await SR(n);                            // migrate nested -> top
//       let c = (nested?.accounts[nested.active]?.[e]) ?? n[e];
//       return c == null || c.startsWith(Ka) ? null : fR(c);
//     }                                             //            ^^^^^^^^^^
//                                                   //  plaintext is REJECTED
//
// `Wr()` is `safeStorage.isEncryptionAvailable()`. On blu that is TRUE — the app
// itself writes `v10` blobs — so the plaintext branch never wins, and a
// `plaintext:v1:` value is explicitly turned into null. Writing plaintext is
// therefore not merely ignored, it is the one encoding the app refuses.
//
// ── What blu's safeStorage actually is ──────────────────────────────────────
// Linux Chromium OSCrypt with the `basic_text` backend (no keyring daemon):
//
//     prefix : "v10"
//     key    : PBKDF2-HMAC-SHA1("peanuts", "saltysalt", 1 iteration, 16 bytes)
//     iv     : 16 bytes of 0x20 (spaces)
//     cipher : AES-128-CBC, PKCS#7 padding
//
// Verified by decrypting the app's own token with exactly that recipe — see
// probe-oscrypt.js, which recovered a readable JWT. So we can encrypt
// compatible values from plain node, no Electron needed.
//
// ── The two other things that must be right ─────────────────────────────────
//   * `accounts` is keyed by `sha256(principal)` and `active` points into it;
//     the session lookup is `accounts[accountScopeOfToken(token)]`, so the
//     record must be REKEYED or the lookup misses.
//   * the same ciphertext must also sit at top level, because `Mu` reads
//     `n[e]` before consulting the nested record.
//
// Run order matters: the app rewrites this file on shutdown AND startup, so
// stop -> patch -> start. `restart` stops first, and that stop overwrites you.
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const SUB = process.env.GROKBOT_SUB || "grokbot-local-principal";
const EMAIL = process.env.GROKBOT_EMAIL || "local@grokbot.invalid";
const DEV_CLIENT_ID = "OzaBXLClY5CAGxNzUhQ2vlknpi07tGuE";

// Chromium OSCrypt constants for the basic_text backend. These are fixed by
// Chromium, not by the app: "peanuts" is the well-known fallback password used
// when no real keyring is present.
const OSC_KEY = crypto.pbkdf2Sync("peanuts", "saltysalt", 1, 16, "sha1");
const OSC_IV = Buffer.alloc(16, " ");

function oscEncrypt(plain) {
  const c = crypto.createCipheriv("aes-128-cbc", OSC_KEY, OSC_IV);
  const ct = Buffer.concat([c.update(plain, "utf8"), c.final()]);
  return Buffer.concat([Buffer.from("v10"), ct]).toString("base64");
}

function oscDecrypt(b64) {
  const raw = Buffer.from(b64, "base64");
  if (raw.subarray(0, 3).toString() !== "v10") throw new Error("not a v10 blob");
  const d = crypto.createDecipheriv("aes-128-cbc", OSC_KEY, OSC_IV);
  return Buffer.concat([d.update(raw.subarray(3)), d.final()]).toString("utf8");
}

const b64url = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
function makeJwt() {
  return [
    b64url({ alg: "none", typ: "JWT" }),
    b64url({
      sub: SUB,
      email: EMAIL,
      // Far future so the client never decides to refresh on expiry.
      exp: Math.floor(Date.now() / 1000) + 3650 * 86400,
    }),
    "",
  ].join(".");
}

const scopeOf = (p) => crypto.createHash("sha256").update(p).digest("hex");

function main() {
  const file = process.argv[2]
    || path.join(process.env.HOME || "/root", ".config", "Grok Bot", "sand-secrets.json");
  if (!fs.existsSync(file)) { console.error("not found:", file); return 1; }

  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  const accounts = JSON.parse(data["cursor-accounts"]);
  const oldActive = accounts.active;
  const acct = accounts.accounts[oldActive];

  const jwt = makeJwt();
  const enc = oscEncrypt(jwt);

  // Prove the value is readable before committing it: a ciphertext the app
  // cannot decrypt is indistinguishable from a plaintext one (both -> null).
  if (oscDecrypt(enc) !== jwt) { console.error("round-trip failed"); return 1; }

  acct["cursor-access-token"] = enc;
  acct["cursor-refresh-token"] = enc;
  acct["cursor-account-profile"] = enc;

  const newActive = scopeOf(SUB);
  accounts.accounts[newActive] = acct;
  if (oldActive !== newActive) delete accounts.accounts[oldActive];
  accounts.active = newActive;

  data["cursor-accounts"] = JSON.stringify(accounts);
  // Top level too: `Mu` checks n[e] before the nested record.
  data["cursor-access-token"] = enc;
  data["cursor-refresh-token"] = enc;
  data["auth-client-id"] = DEV_CLIENT_ID;

  const backup = `${file}.bak-${Date.now()}`;
  fs.copyFileSync(file, backup);
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2));
  fs.renameSync(tmp, file);

  console.log("file        :", file);
  console.log("backup      :", backup);
  console.log("sub         :", SUB);
  console.log("scope       :", newActive);
  console.log("rekeyed     :", oldActive.slice(0, 16) + "… ->", newActive.slice(0, 16) + "…");
  console.log("encoding    : v10 + AES-128-CBC(peanuts)  len=" + enc.length);
  console.log("round-trip  : OK");
  console.log("top-level   : cursor-access-token, cursor-refresh-token, auth-client-id");
  return 0;
}

process.exit(main());
