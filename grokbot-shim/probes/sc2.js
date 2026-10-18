const m = require("/home/amen/grokre/asarx/dist/electron-main/proto.cjs");
const M = m.GrokBotService.methods.watchGrokBotTranscripts;

const SCALAR = { 1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32", 6: "fixed64",
  7: "fixed32", 8: "bool", 9: "string", 12: "bytes", 13: "uint32", 15: "sfixed32",
  16: "sfixed64", 17: "sint32", 18: "sint64" };

function ty(f) {
  if (f.fieldKind === "scalar") return SCALAR[f.T] || ("T" + f.T);
  if (f.fieldKind === "message") return "msg:" + f.message.typeName;
  if (f.fieldKind === "enum") return "enum";
  return f.fieldKind;
}
function show(t, indent) {
  console.log(indent + "MSG " + t.typeName);
  for (const f of t.fields.list()) {
    console.log(indent + "  " + f.name + " : " + ty(f) + (f.repeated ? "[]" : "") + " #" + f.no);
  }
}

const rowsField = M.O.fields.list().find((f) => f.name === "rows");
console.log("rows field found:", !!rowsField);
show(rowsField.message, "");
const entField = rowsField.message.fields.list().find((f) => f.name === "entries");
show(entField.message, "  ");
