// Answer one narrow question: would `Mu()` return our token on THIS host?
//
// This is the exact decision procedure from main-core.cjs, including the
// `Wr()` safeStorage gate, so it reproduces what the app does rather than what
// we wish it did. If this prints "READ OK" and the UI still says "Sign in",
// the break is downstream of the secret store and this script is not the bug.
"use strict";
const fs = require("fs");
const crypto = require("crypto");

const SECRETS = process.env.SECRETS || "/root/.config/Grok Bot/sand-secrets.json";
const Ka = "plaintext:v1:";
const Ae = "cursor-access-token";
const sr = "cursor-refresh-token";

// Wr(): true only when Electron safeStorage can actually encrypt/decrypt.
// blu runs with no keyring daemon, so this is false there.
function Wr() {
  try {
    const { safeStorage } = require("electron");
    return safeStorage.isEncryptionAvailable();
  } catch {
    return false;
  }
}

// Mu(e) — verbatim decision order from main-core.cjs.
function Mu(n, e) {
  const r = n[e];                                  // TOP-LEVEL lookup
  if (r != null && r.startsWith(Ka) === true) {    // plaintext short-circuits
    return Buffer.from(r.slice(Ka.length), "base64").toString("utf8");
  }
  if (!Wr()) return null;                          // safeStorage gate
  return r == null ? null : "(encrypted path)";
}

const raw = JSON.parse(fs.readFileSync(SECRETS, "utf8"));
const n = {};
for (const [k, v] of Object.entries(raw)) if (typeof v === "string") n[k] = v;

console.log("top-level keys :", Object.keys(raw).join(", "));
console.log("Wr() available :", Wr());
console.log("");

for (const key of [Ae, sr]) {
  const got = Mu(n, key);
  console.log(key);
  if (got == null) {
    console.log("   Mu() -> NULL   (app sees no session)");
  } else if (got.startsWith("(")) {
    console.log("   Mu() ->", got);
  } else {
    const parts = got.split(".");
    let sub = "PARSE FAIL";
    try { sub = JSON.parse(Buffer.from(parts[1], "base64url")).sub; } catch {}
    console.log("   Mu() -> READ OK  len:", got.length, " sub:", sub);
    console.log("   scope :", crypto.createHash("sha256").update(sub).digest("hex"));
  }
}
