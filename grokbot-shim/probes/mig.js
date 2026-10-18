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
const meth = m.SandBoxService.methods.watchSandBoxMigration;
console.log("watchSandBoxMigration kind=" + meth.kind + " in=" + meth.I.typeName + " out=" + meth.O.typeName);
for (const f of meth.O.fields.list()) console.log("  " + f.name + " #" + f.no + " " + describe(f) + (f.repeated ? "[]" : ""));
console.log("\nenum SandBoxMigrationPhase:");
const e = m.SandBoxMigrationPhase;
for (const [k, v] of Object.entries(e || {})) console.log("   " + k + " = " + v);
console.log("\nroster-related RPCs present:");
for (const [k, v] of Object.entries(m.GrokBotService.methods)) {
  if (/agent|roster|migration|box/i.test(k)) console.log("  " + v.name);
}
