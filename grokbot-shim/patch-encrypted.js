// Patch the secret store using Electron safeStorage — the *native* encoding.
//
// WHY the previous approaches could never work:
//   * A bare JWT is treated as a safeStorage ciphertext, fails to decrypt, and
//     the reader silently keeps the old value.
//   * `plaintext:v1:` is accepted by the reader, BUT `wse()` then migrates it:
//     it re-encrypts with safeStorage, stores the result under `cursor-accounts`,
//     and DELETES the top-level plaintext key. So a plaintext patch is consumed
//     and discarded within one startup — the file looks patched, then reverts.
//
// safeStorage IS available on this host (the app itself writes `v10` blobs of
// 163 bytes for the refresh token), so the correct move is to produce a real
// `v10` ciphertext ourselves and let the app decrypt it normally.
//
// This script must run INSIDE Electron (it needs safeStorage), so it is loaded
// with ELECTRON_RUN_AS_NODE unset and a display. Invoke via:
//     DISPLAY=:100 electron patch-encrypted.js
// It patches, verifies by decrypting its own output, and exits.
"use strict";
const { app, safeStorage } = require("electron");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const SUB = "grokbot-local-principal";
const EMAIL = "local@grokbot.invalid";
const DEV_CLIENT_ID = "OzaBXLClY5CAGxNzUhQ2vlknpi07tGuE";

function secretsPath() {
  return path.join(app.getPath("userData"), "sand-secrets.json");
}

function b64url(raw) {
  return Buffer.from(raw).toString("base64url");
}

function makeJwt() {
  const header = b64url(JSON.stringify({ alg: "none", typ: "JWT" }));
  const payload = b64url(JSON.stringify({
    sub: SUB,
    email: EMAIL,
    exp: Math.floor(Date.now() / 1000) + 3650 * 86400,
  }));
  return `${header}.${payload}.`;
}

const scopeOf = (p) => crypto.createHash("sha256").update(p).digest("hex");

app.whenReady().then(() => {
  try {
    const file = secretsPath();
    console.log("secrets file      :", file);
    console.log("isEncryptionAvail :", safeStorage.isEncryptionAvailable());
    try {
      console.log("backend           :", safeStorage.getSelectedStorageBackend
        ? safeStorage.getSelectedStorageBackend() : "(no api)");
    } catch (e) { console.log("backend           : ERR", e.message); }

    const data = JSON.parse(fs.readFileSync(file, "utf8"));
    const accounts = JSON.parse(data["cursor-accounts"]);
    const oldActive = accounts.active;
    const acct = accounts.accounts[oldActive];

    const jwt = makeJwt();
    const enc = safeStorage.encryptString(jwt).toString("base64");

    // Round-trip check before writing: if our own ciphertext does not decrypt,
    // writing it would just be another silent no-op.
    const back = safeStorage.decryptString(Buffer.from(enc, "base64"));
    if (back !== jwt) throw new Error("safeStorage round-trip failed");
    console.log("round-trip        : OK (len " + enc.length + ")");

    acct["cursor-access-token"] = enc;
    acct["cursor-refresh-token"] = enc;
    acct["cursor-account-profile"] = enc;

    // Rekey: the app looks the session up as accounts[scopeOf(token)], so the
    // record must live under the scope our token implies.
    const newActive = scopeOf(SUB);
    accounts.accounts[newActive] = acct;
    if (oldActive !== newActive) delete accounts.accounts[oldActive];
    accounts.active = newActive;

    data["cursor-accounts"] = JSON.stringify(accounts);
    data["cursor-access-token"] = enc;
    data["cursor-refresh-token"] = enc;
    data["auth-client-id"] = DEV_CLIENT_ID;

    const backup = file + ".bak-" + Date.now();
    fs.copyFileSync(file, backup);

    const tmp = file + ".tmp";
    fs.writeFileSync(tmp, JSON.stringify(data, null, 2));
    fs.renameSync(tmp, file);

    console.log("backup            :", backup);
    console.log("sub               :", SUB);
    console.log("scope             :", newActive);
    console.log("rekeyed           :", oldActive.slice(0, 16), "->", newActive.slice(0, 16));
    console.log("written           :", enc.slice(0, 24) + "...");
    console.log("DONE");
  } catch (e) {
    console.error("FAILED:", e && e.stack || e);
    app.exit(1);
  }
  app.exit(0);
});
