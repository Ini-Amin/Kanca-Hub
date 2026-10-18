const m = require("/home/amen/grokre/asarx/dist/electron-main/proto.cjs");
const SCALAR = { 1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32", 6: "fixed64",
  7: "fixed32", 8: "bool", 9: "string", 12: "bytes", 13: "uint32", 15: "sfixed32",
  16: "sfixed64", 17: "sint32", 18: "sint64" };
function describe(f) {
  const T = f.T;
  if (f.kind === "scalar") return SCALAR[T] || "T" + T;
  if (f.kind === "message") return "msg:" + (T && T.typeName ? T.typeName : "?");
  if (f.kind === "enum") return "enum:" + (T && T.typeName ? T.typeName : "?");
  return f.kind;
}
function dump(t, indent, depth) {
  console.log(indent + "MSG " + t.typeName);
  for (const f of t.fields.list()) {
    console.log(indent + "  " + f.name + " #" + f.no + " " + describe(f) + (f.repeated ? "[]" : ""));
    if (depth > 0 && f.kind === "message" && f.T && f.T.typeName) dump(f.T, indent + "    ", depth - 1);
  }
}
for (const n of ["getMe", "getTeams", "getAvailableMcpServers", "listMarketplacePlugins"]) {
  const meth = m.DashboardService.methods[n];
  if (!meth) { console.log(n + " MISSING"); continue; }
  console.log("=== " + meth.name + " ===");
  dump(meth.O, "", 1);
  console.log();
}
console.log("=== ListGrokBotAgents OUT ===");
dump(m.GrokBotService.methods.listGrokBotAgents.O, "", 2);
