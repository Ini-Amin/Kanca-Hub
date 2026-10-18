const m = require("/home/amen/grokre/asarx/dist/electron-main/proto.cjs");
const M = m.GrokBotService.methods.watchGrokBotTranscripts;

const SCALAR = { 1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32", 6: "fixed64",
  7: "fixed32", 8: "bool", 9: "string", 12: "bytes", 13: "uint32", 15: "sfixed32",
  16: "sfixed64", 17: "sint32", 18: "sint64" };

function describe(f) {
  const T = f.T;
  if (f.kind === "scalar") return SCALAR[T] || "T" + T;
  if (f.kind === "message") return "msg:" + (T && T.typeName ? T.typeName : "?");
  if (f.kind === "enum") return "enum";
  return f.kind;
}

const rowsField = M.O.fields.list().find((f) => f.name === "rows");
const RT = rowsField.T;
console.log("ROWS " + RT.typeName);
for (const f of RT.fields.list()) console.log("  " + f.name + " #" + f.no + " " + describe(f) + (f.repeated ? "[]" : ""));

const entField = RT.fields.list().find((f) => f.name === "entries");
const ET = entField.T;
console.log("\nENTRY " + ET.typeName);
for (const f of ET.fields.list()) console.log("  " + f.name + " #" + f.no + " " + describe(f) + (f.repeated ? "[]" : ""));

console.log("\n--- SendGrokBotUserMessage OUT ---");
const UM = m.GrokBotService.methods.sendGrokBotUserMessage;
console.log(UM.O.typeName, UM.O.fields.list().map((f) => f.name + ":" + describe(f)).join(", "));
