const m = require("/home/amen/grokre/asarx/dist/electron-main/proto.cjs");
const M = m.GrokBotService.methods.watchGrokBotTranscripts;

const rowsField = M.O.fields.list().find((f) => f.name === "rows");
console.log("rows field keys:", Object.keys(rowsField).join(","));
console.log("rows fieldKind:", rowsField.fieldKind, "T is fn?", typeof rowsField.T);
const RT = typeof rowsField.T === "function" ? rowsField.T() : rowsField.T;
console.log("rows msg typeName:", RT && RT.typeName);
console.log("rows msg fields:", RT.fields.list().map((f) => f.name + "(" + f.no + "," + f.fieldKind + ")").join(" "));

const entField = RT.fields.list().find((f) => f.name === "entries");
const ET = typeof entField.T === "function" ? entField.T() : entField.T;
console.log("\nentry typeName:", ET.typeName);
for (const f of ET.fields.list()) {
  const T = typeof f.T === "function" ? f.T() : f.T;
  const tn = f.fieldKind === "message" && T && T.typeName ? T.typeName : (T === undefined ? "?" : String(T));
  console.log("  " + f.name + " #" + f.no + " " + f.fieldKind + " -> " + tn + (f.repeated ? "[]" : ""));
}
