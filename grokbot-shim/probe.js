// Diagnostic probe: drive shim like the app, log EVERY frame raw.
const http = require("http");
const PROTO = process.env.GROKBOT_PROTO
  || "/home/amen/grokre/asarx/dist/electron-main/proto.cjs";
const m = require(PROTO);

const HOST = "127.0.0.1", PORT = Number(process.env.SHIM_PORT || 8790);
const TEXT = process.argv[2] || "Reply with exactly: SHIM-ROUNDTRIP-OK";
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

function frame(flag, buf) {
  const h = Buffer.alloc(5); h[0] = flag; h.writeUInt32BE(buf.length, 1);
  return Buffer.concat([h, buf]);
}
function unframe(buf) {
  const out = []; let i = 0;
  while (i + 5 <= buf.length) {
    const len = buf.readUInt32BE(i + 1);
    if (i + 5 + len > buf.length) break;
    out.push({ flag: buf[i], data: buf.subarray(i + 5, i + 5 + len) });
    i += 5 + len;
  }
  return { frames: out, rest: buf.subarray(i) };
}

const SM = m.GrokBotService.methods.watchGrokBotTranscripts;
let rows = [];

const streamReq = http.request(
  { host: HOST, port: PORT, path: "/aiserver.v1.GrokBotService/WatchGrokBotTranscripts",
    method: "POST", headers: { "content-type": "application/grpc-web+proto", "x-grpc-web": "1" } },
  (res) => {
    log("[stream] http", res.statusCode);
    let buf = Buffer.alloc(0);
    res.on("data", (c) => {
      buf = Buffer.concat([buf, c]);
      const { frames, rest } = unframe(buf);
      buf = rest;
      for (const f of frames) {
        if (f.flag === 0x80) { log("[stream] trailers:", f.data.toString("utf8").trim()); continue; }
        if (f.flag !== 0) continue;
        let msg;
        try { msg = SM.O.fromBinary(f.data); }
        catch (e) { log("[stream] DECODE FAIL", e.message, "len", f.data.length, f.data.toString("hex").slice(0, 120)); continue; }
        const c = msg.frame?.case;
        if (c === "rows") {
          const v = msg.frame.value;
          log("[rows] entries:", (v.entries || []).length, "agent:", v.agentId, "gen:", String(v.generation));
          for (const e of v.entries || []) {
            const raw = Buffer.from(e.body).toString("utf8");
            log("  entry seq", String(e.seq), "kind:", e.entryKind, "id:", e.entryId, "body:", raw.slice(0, 300));
            try { rows.push(JSON.parse(raw)); } catch (err) { log("  body JSON fail:", err.message); }
          }
        } else if (c) log("[stream] frame:", c, JSON.stringify(msg.frame.value).slice(0, 120));
      }
    });
    res.on("end", () => log("[stream] closed"));
  }
);
streamReq.on("error", (e) => log("[stream] err", e.message));
streamReq.end(frame(0, new SM.I({ cursors: [{ agentId: "main" }] }).toBinary()));
log("[stream] opened");

setTimeout(async () => {
  const UM = m.GrokBotService.methods.sendGrokBotUserMessage;
  const body = frame(0, new UM.I({ agentId: "main", text: TEXT }).toBinary());
  const req = http.request(
    { host: HOST, port: PORT, path: "/aiserver.v1.GrokBotService/SendGrokBotUserMessage",
      method: "POST", headers: { "content-type": "application/grpc-web+proto", "x-grpc-web": "1" } },
    (res) => {
      let b = Buffer.alloc(0);
      res.on("data", (c) => (b = Buffer.concat([b, c])));
      res.on("end", () => {
        const { frames } = unframe(b);
        log("[send] http", res.statusCode, "frames", frames.length);
        for (const f of frames) {
          if (f.flag === 0x80) { log("[send] trailers:", f.data.toString("utf8").trim()); continue; }
          try { log("[send] out:", JSON.stringify(UM.O.fromBinary(f.data))); } catch (e) { log("[send] out decode fail", e.message); }
        }
      });
    }
  );
  req.on("error", (e) => log("[send] err", e.message));
  req.end(body);
  log("[send] posted:", TEXT);
}, 800);

setTimeout(() => {
  const reply = rows.find((r) => r.kind === "message" && r.role !== "user" && r.content);
  log("=== RESULT ===", reply ? "ROUNDTRIP OK: " + String(reply.content).slice(0, 200) : "NO REPLY ROW (rows=" + rows.length + ")");
  process.exit(reply ? 0 : 1);
}, 90000);
