// Verify each shim handler's reply actually survives proto serialization.
//
// WHY: a handler that returns a correctly-shaped object still yields `{}` on
// the wire if a field name does not exist in the response message. The proto
// encoder drops unknown fields silently, so the app sees an empty message and
// reports ConnectError.Internal while the shim log shows the "correct" object.
// That exact mismatch cost hours: GetSandBoxRunState's field is `state`, while
// EnsureSandBox's is `run_state`.
//
// Run: node verify-rest-shapes.js            (add GROKBOT_PROTO if needed)
// Fails loudly on any method whose reply serializes to an empty object.
"use strict";
const fs = require("node:fs");
const PROTO_CANDIDATES = [
  process.env.GROKBOT_PROTO,
  "/root/grokbot/asarx/dist/electron-main/proto.cjs",
  "/home/amen/grokre/asarx/dist/electron-main/proto.cjs",
].filter(Boolean);
const PROTO = PROTO_CANDIDATES.find((p) => { try { return fs.existsSync(p); } catch { return false; } });
if (!PROTO) { console.error("proto.cjs not found; set GROKBOT_PROTO"); process.exit(1); }
const m = require(PROTO);

// method -> the reply our shim handler returns, keyed the way proxy.js does.
const CASES = [
  ["GrokBotService", "GetSandBoxRunState", { state: 3 }],
  ["GrokBotService", "EnsureSandBox", {
    runState: 3, gatewayUrl: "http://127.0.0.1:8791", gatewayToken: "t",
    networkToken: "n", vncUrl: "", forkVncBaseUrl: "",
    cluster: "shim", podId: "shim",
  }],
  ["GrokBotService", "GetGrokBotRuntimeCapabilities", {
    capabilities: {
      temporalCreationEnabled: true, durableIdentityEnabled: true,
      durableIdentityWritesEnabled: true, agentMessagingEnabled: false,
      serverRoomsEnabled: false,
    },
  }],
  ["DashboardService", "GetUserPrivacyMode", {
    privacyMode: 2, accountPrivacyMode: 2, isEnforcedByTeam: false,
    isNotMigratedToServerSourceOfTruth: false, partnerDataShare: false,
    hasAcknowledgedGracePeriodDisclaimer: true, hoursRemainingInGracePeriod: 0,
  }],
  ["DashboardService", "GetSandAccessStatus", { state: 1, blockReason: 1 }],
  ["GrokBotService", "ListGrokBotAgents", {
    agents: [{
      agentId: "main", id: "main", name: "9router", harness: "temporal",
      description: "via shim", visibility: 1,
      createdAtMs: BigInt(Date.now()), updatedAtMs: BigInt(Date.now()),
    }],
    slackConnections: [],
  }],
];

// camelCase -> the descriptor's localName, so a JS-side name that the message
// does not declare shows up as a mismatch instead of being silently dropped.
function jsNameOf(field) {
  return field.localName.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}

let bad = 0;
for (const [svcName, methName, reply] of CASES) {
  const svc = m[svcName];
  const key = svc && Object.keys(svc.methods).find(
    (k) => String(svc.methods[k].name) === methName);
  if (!key) { console.log(`SKIP  ${svcName}.${methName} (no descriptor)`); continue; }
  const M = svc.methods[key];

  // Which of our reply keys does the message not declare?
  //
  // Nested message types are NOT reachable as `f.message.fields` on a descriptor
  // built this way (that property is empty), so resolve the type by name through
  // the registry instead. Getting this wrong reports every nested key as
  // "undeclared" even when it is perfectly valid.
  const TYPE_CACHE = new Map();
  function fieldsOf(typename) {
    if (!typename) return null;
    if (TYPE_CACHE.has(typename)) return TYPE_CACHE.get(typename);
    const msg = m[typename] || m.aiserver?.v1?.[typename.split(".").pop()];
    const out = msg && msg.fields ? msg.fields : null;
    TYPE_CACHE.set(typename, out);
    return out;
  }
  const declared = new Map();
  const UNRESOLVED = new Set();
  const collect = (fields, prefix) => {
    for (const f of fields.list()) {
      const tn = f.T && f.T.typeName;
      declared.set(prefix + jsNameOf(f), f);
      const nested = tn ? fieldsOf(tn) : null;
      if (nested) collect(nested, prefix + jsNameOf(f) + ".");
      // A message type that is NOT in the registry cannot be introspected here
      // (proto.cjs exports only the top-level services). Mark it so we do not
      // report its children as "undeclared" — that was a false positive that
      // made a correct handler look broken.
      //
      // ENUMS also carry a typeName but are leaves, so exclude them: an enum has
      // a `.values` array on its type object, a message does not. Without this,
      // every enum field was marked unresolved and the real nested problem was
      // masked.
      else if (tn && !(f.T && Array.isArray(f.T.values))) {
        UNRESOLVED.add(prefix + jsNameOf(f) + ".*");
      }
    }
  };
  collect(M.O.fields, "");

  const unknown = [];
  for (const k of Object.keys(reply)) {
    if (k.includes(".")) continue;               // nested handled below
    if (!declared.has(k)) unknown.push(k);
  }
  for (const k of Object.keys(reply.capabilities || {})) {
    // "capabilities.*" in UNRESOLVED means the nested message type is not
    // introspectable from this registry, so its children CANNOT be checked.
    // Report that as a note (already printed) rather than as a failure.
    if (UNRESOLVED.has("capabilities.*")) continue;
    if (!declared.has("capabilities." + k)) unknown.push("capabilities." + k);
  }

  let json, binBytes = 0;
  try {
    const inst = new M.O(reply);
    binBytes = inst.toBinary().byteLength;
    json = M.O.fromBinary(inst.toBinary()).toJson();
  }
  catch (e) { json = { __error: String(e.message).slice(0, 80) }; }
  const empty = json && typeof json === "object" && Object.keys(json).length === 0 || binBytes === 0;

  const flag = (unknown.length || empty) ? "FAIL" : "ok  ";
  if (unknown.length || empty) bad++;
  console.log(`${flag} ${svcName}.${methName}`);
  if (empty) console.log(`       serialized EMPTY: ${JSON.stringify(reply).slice(0, 90)}`);
  if (unknown.length) console.log(`       undeclared keys: ${unknown.join(", ")}`);
  if (UNRESOLVED.size) console.log(`       note: not introspectable here: ${[...UNRESOLVED].join(", ")}`);
  console.log(`       -> ${JSON.stringify(json).slice(0, 130)}`);
}
console.log(bad ? `\n${bad} method(s) would serialize empty/mismatched` : "\nall methods serialize with declared fields");
process.exit(bad ? 1 : 0);
