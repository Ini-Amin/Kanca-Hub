// Dump the exact agent shape the roster expects.
//
// WHY: the UI settles on "Couldn't load Bots" and `connection` shows
// `resyncingReplicaKeys: ["roster"]`. The seeding code filters
// `agents.filter(o => o.harness === "temporal")` and then drops anything where
// `parseAgentId(o.agentId) == null`, so a roster entry with the wrong `harness`
// or a malformed `agentId` is silently discarded — the list arrives, and the UI
// still shows nothing. This prints the parser and the expected id shape.
"use strict";
const fs = require("fs");
const B = "/root/grokbot/asarx/dist/electron-main/main-app.cjs";
const s = fs.readFileSync(B, "utf8");

// 1. the id parser
for (const re of [/parseAgentId\s*[=:]\s*[^,;]{0,200}/g, /function\s+parseAgentId\s*\([^)]*\)\s*\{[^}]{0,300}\}/g]) {
  const hits = s.match(re) || [];
  if (hits.length) { console.log("parseAgentId:"); hits.slice(0, 3).forEach((h) => console.log("  " + h.slice(0, 220))); }
}

// 2. any literal agentId values shipped in the bundle (seed templates)
const ids = [...s.matchAll(/agentId\s*:\s*"([^"]{3,60})"/g)].map((m) => m[1]);
console.log("\nliteral agentIds in bundle:", [...new Set(ids)].slice(0, 10));

// 3. the harness values referenced
const harness = [...s.matchAll(/harness\s*===?\s*"([^"]{2,30})"/g)].map((m) => m[1]);
console.log("harness literals          :", [...new Set(harness)].slice(0, 10));

// 4. what ListGrokBotAgents response fields are read
const idx = s.indexOf("listGrokBotAgents({includeTeamAgents");
if (idx > 0) console.log("\nresponse consumer:\n  " + s.slice(idx, idx + 320).replace(/\s+/g, " "));
