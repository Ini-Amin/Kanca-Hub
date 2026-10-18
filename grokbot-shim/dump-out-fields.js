// Print the response descriptor for the Sand RPCs the shim answers, so handler
// field names can be matched to the message instead of guessed.
// Usage: node dump-out-fields.js [ServiceName]
"use strict";
const fs = require("node:fs");
const PROTO = [process.env.GROKBOT_PROTO,
  "/root/grokbot/asarx/dist/electron-main/proto.cjs",
  "/home/amen/grokre/asarx/dist/electron-main/proto.cjs"].filter(Boolean)
  .find((p) => { try { return fs.existsSync(p); } catch { return false; } });
if (!PROTO) { console.error("proto.cjs not found"); process.exit(1); }
const m = require(PROTO);

const WANT = ["RuntimeCapabilities", "ListGrokBotAgents", "EnsureSandBox",
  "GetSandBoxRunState", "GetSandAccessStatus"];

function walk(fields, prefix) {
  for (const f of fields.list()) {
    const t = f.T && f.T.values ? JSON.stringify(f.T.values)
      : (f.T && f.T.typeName ? f.T.typeName : String(f.T));
    console.log(`   ${prefix}${f.localName}  (js: ${f.localName.replace(/_([a-z])/g, (_, c) => c.toUpperCase())})  kind=${f.kind} T=${t}`);
    if (f.message && f.message.fields) walk(f.message.fields, prefix + f.localName + ".");
  }
}

for (const [name, svc] of Object.entries(m)) {
  if (!svc || !svc.methods) continue;
  for (const key of Object.keys(svc.methods)) {
    const M = svc.methods[key];
    const nm = String(M.name || key);
    if (!WANT.some((w) => nm.includes(w))) continue;
    console.log(`=== ${name}.${nm} ===`);
    walk(M.O.fields, "   ");
  }
}
