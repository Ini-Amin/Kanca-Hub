// Decisive check: can the app's own reader get a usable token right now?
//
// `readCredentialPair()` needs BOTH accessToken and refreshToken non-null, or
// `getValidAccessToken()` throws `Ho`, which makes `readAccountScope()` return
// undefined, which makes the account slot null, which routes to "signed-out",
// which means the transport factory is never invoked with a principal.
//
// `Mu(e)` for e in {access, refresh} ends with:
//     n = await SR(n)                       // migrate top-level -> nested
//     c = accounts[active]?.[e] ?? n[e]
//     return c == null || c.startsWith(Ka) ? null : fR(c)
// so the NESTED value must be decryptable ciphertext. This script replicates
// exactly that and reports what the app would get.
"use strict";
const fs = require("fs");
const crypto = require("crypto");

const SECRETS = process.env.SECRETS || "/root/.config/Grok Bot/sand-secrets.json";
const Ka = "plaintext:v1:";
const Ae = "cursor-access-token";
const sr = "cursor-refresh-token";
const ft = "cursor-accounts";

const KEY = crypto.pbkdf2Sync("peanuts", "saltysalt", 1, 16, "sha1");
const IV = Buffer.alloc(16, " ");

function fR(b64) {
  try {
    const raw = Buffer.from(b64, "base64");
    if (raw.subarray(0, 3).toString() !== "v10") return null;
    const d = crypto.createDecipheriv("aes-128-cbc", KEY, IV);
    return Buffer.concat([d.update(raw.subarray(3)), d.final()]).toString("utf8");
  } catch {
    return null;
  }
}

const Vi = (e) => {
  const [, t] = String(e).split(".");
  if (!t) return null;
  try { return JSON.parse(Buffer.from(t, "base64url")); } catch { return null; }
};
const scopeOfToken = (tok) => {
  const sub = Vi(tok)?.sub ?? tok;
  return crypto.createHash("sha256").update(sub).digest("hex");
};

const raw = JSON.parse(fs.readFileSync(SECRETS, "utf8"));
const n = {};
for (const [k, v] of Object.entries(raw)) if (typeof v === "string") n[k] = v;

const rec = JSON.parse(raw[ft]);
const active = rec.active;
const nested = rec.accounts[active] || {};

console.log("active scope      :", active);
console.log("nested keys       :", Object.keys(nested).join(", ") || "(none)");
console.log("top-level tokens  :", [Ae, sr].filter((k) => n[k] != null).join(", ") || "(none — migrated, expected)");
console.log("");

let ok = 0;
for (const key of [Ae, sr]) {
  // Replicate Mu(): nested first, then top level.
  const c = nested[key] ?? n[key];
  process.stdout.write(key.padEnd(22));
  if (c == null) { console.log("-> NULL   (app sees no credential)"); continue; }
  if (c.startsWith(Ka)) { console.log("-> REJECTED (plaintext when encryption is available)"); continue; }
  const plain = fR(c);
  if (plain == null) { console.log("-> NULL   (ciphertext will not decrypt)"); continue; }
  const p = Vi(plain);
  console.log("-> OK     len=" + plain.length + "  sub=" + (p ? p.sub : "PARSE FAIL"));
  if (key === Ae) {
    const sc = scopeOfToken(plain);
    console.log("".padEnd(22) + "   token scope = " + sc);
    console.log("".padEnd(22) + "   matches active? " + (sc === active ? "YES" : "NO  <-- lookup will miss"));
    if (sc === active) ok++;
  } else ok++;
}

console.log("");
console.log(ok === 2
  ? "RESULT: readCredentialPair() would SUCCEED -> account slot resolves -> factory can run"
  : "RESULT: readCredentialPair() would FAIL -> Ho thrown -> readAccountScope() undefined -> signed-out");
