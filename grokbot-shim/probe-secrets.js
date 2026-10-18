// Replicate the app's secret-store decode path against the real on-disk file.
//
// WHY a standalone probe: every previous attempt at patching sand-secrets.json
// was judged by "does transport come up", which conflates three independent
// failures (bad encoding, wrong principal, app refusing the session). This
// script answers only the narrow question "would the app's own readers accept
// what is on disk right now", by re-implementing _R/Vi/Ag/Bn verbatim from
// dist/electron-main/main-core.cjs.
"use strict";
const fs = require("fs");
const crypto = require("crypto");

const SECRETS = process.env.SECRETS || "/root/.config/Grok Bot/sand-secrets.json";
const Ka = "plaintext:v1:";

// _R(e): the secret-store reader for a single value.
function _R(e) {
  if (e == null) return { kind: "absent" };
  if (e.startsWith(Ka)) {
    return { kind: "present", value: Buffer.from(e.slice(Ka.length), "base64").toString("utf8") };
  }
  if (!Wr()) return { kind: "unreadable" };
  return { kind: "corrupt" };
}

// Wr(): whether Electron safeStorage can actually decrypt on this host. blu has
// no keyring daemon, so this is false and every v10| blob is unreadable — which
// is exactly why the original 28-byte values never produced a principal.
function Wr() {
  try {
    const { safeStorage } = require("electron");
    return safeStorage.isEncryptionAvailable();
  } catch {
    return false;
  }
}

// Vi(e): parseJwtPayload. Base64url-decodes the middle segment, never verifies.
function Vi(e) {
  const [, t] = e.split(".");
  if (!t) return null;
  try { return JSON.parse(Buffer.from(t, "base64url")); } catch { return null; }
}

function Ag({ principal }) {
  return crypto.createHash("sha256").update(principal).digest("hex");
}
function Bn({ accessToken }) {
  return Ag({ principal: Vi(accessToken)?.sub ?? accessToken });
}

const d = JSON.parse(fs.readFileSync(SECRETS, "utf8"));
const a = JSON.parse(d["cursor-accounts"]);
const act = a.accounts[a.active];

console.log("file keys      :", Object.keys(d).join(", "));
console.log("active account :", a.active);
console.log("accounts       :", Object.keys(a.accounts).length);
console.log("");

for (const k of ["cursor-access-token", "cursor-refresh-token", "cursor-account-profile"]) {
  const raw = act[k];
  console.log(k);
  if (raw == null) { console.log("   ABSENT"); continue; }
  const r = _R(raw);
  console.log("   len      :", raw.length, "prefix:", raw.slice(0, 14));
  console.log("   _R kind  :", r.kind);
  if (r.kind === "present") {
    const p = Vi(r.value);
    console.log("   Vi sub   :", p ? p.sub : "PARSE FAIL");
    console.log("   scope    :", p ? Bn({ accessToken: r.value }) : "-");
  }
}

console.log("");
console.log("expected scope :", Ag({ principal: "grokbot-local-principal" }));
