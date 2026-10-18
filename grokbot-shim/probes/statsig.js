const m = require("/home/amen/grokre/asarx/dist/electron-main/proto.cjs");
const SCALAR = { 1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32", 6: "fixed64",
  7: "fixed32", 8: "bool", 9: "string", 12: "bytes", 13: "uint32", 15: "sfixed32",
  16: "sfixed64", 17: "sint32", 18: "sint64" };
function describe(f) {
  const T = f.T;
  if (f.kind === "scalar") return SCALAR[T] || "T" + T;
  if (f.kind === "message") return "msg:" + (T && T.typeName ? T.typeName : "?");
  if (f.kind === "enum") return "enum:" + (T && T.typeName ? T.typeName : "?");
  if (f.kind === "map") return "map";
  return f.kind;
}
function dump(t, indent, depth) {
  console.log(indent + "MSG " + t.typeName);
  for (const f of t.fields.list()) {
    console.log(indent + "  " + f.name + " #" + f.no + " " + describe(f) + (f.repeated ? "[]" : ""));
    if (depth > 0 && f.kind === "message" && f.T && f.T.typeName) dump(f.T, indent + "    ", depth - 1);
  }
}
const a = m.AnalyticsService.methods;
for (const n of Object.keys(a)) {
  if (/statsig|bootstrap/i.test(n)) {
    console.log("=== " + a[n].name + " kind=" + a[n].kind);
    console.log("IN:"); dump(a[n].I, "  ", 1);
    console.log("OUT:"); dump(a[n].O, "  ", 2);
    console.log();
  }
}
