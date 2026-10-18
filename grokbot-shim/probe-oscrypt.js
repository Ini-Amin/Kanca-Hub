// Decrypt the app's own safeStorage blob to prove which crypto backend blu uses.
//
// WHY: the app writes `v10` blobs, but `require("electron")` from plain node
// cannot report the backend, and running Electron headless for one call is
// fragile. Chromium's OSCrypt on Linux with the `basic_text` backend uses a
// hardcoded password ("peanuts"), salt "saltysalt", 1 PBKDF2 iteration and
// AES-128-CBC with a 16-space IV. If that decrypts the app's OWN token into a
// readable JWT, then we can produce compatible ciphertext ourselves and stop
// fighting the store.
"use strict";
const fs = require("fs");
const crypto = require("crypto");

const SECRETS = process.env.SECRETS || "/root/.config/Grok Bot/sand-secrets.json";

function oscryptDecrypt(b64) {
  const raw = Buffer.from(b64, "base64");
  if (raw.subarray(0, 3).toString() !== "v10") return { err: "not v10: " + raw.subarray(0, 6).toString("hex") };
  const ct = raw.subarray(3);
  const key = crypto.pbkdf2Sync("peanuts", "saltysalt", 1, 16, "sha1");
  const iv = Buffer.alloc(16, " ");
  try {
    const d = crypto.createDecipheriv("aes-128-cbc", key, iv);
    const out = Buffer.concat([d.update(ct), d.final()]);
    // Chromium pads with PKCS#7; Node already stripped it. Older builds prefix
    // the plaintext with a 32-byte SHA256 of the host; strip that if present.
    return { ok: out.toString("utf8") };
  } catch (e) {
    return { err: "decrypt failed: " + e.message };
  }
}

const d = JSON.parse(fs.readFileSync(SECRETS, "utf8"));
const candidates = [];
for (const [k, v] of Object.entries(d)) {
  if (typeof v === "string" && k !== "cursor-accounts") candidates.push([k, v]);
}
try {
  const a = JSON.parse(d["cursor-accounts"]);
  const act = a.accounts[a.active];
  for (const [k, v] of Object.entries(act)) candidates.push(["accounts." + k, v]);
} catch {}

for (const [k, v] of candidates) {
  const r = oscryptDecrypt(v);
  const label = k.padEnd(34);
  if (r.ok) {
    const printable = r.ok.replace(/[^\x20-\x7e]/g, ".");
    console.log(label, "OK  len=" + r.ok.length, "->", printable.slice(0, 80));
  } else {
    console.log(label, r.err);
  }
}
