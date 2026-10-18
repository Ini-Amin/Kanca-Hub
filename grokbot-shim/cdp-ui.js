// Dump what the renderer is actually showing, plus any console errors.
//
// WHY: `transport: "down"` is the last link in a chain, so it cannot tell us
// where the chain broke. The visible text and the console are the cheapest
// signal for "did the app even get past boot / sign-in / sand-access".
"use strict";
const http = require("http");
const net = require("net");
const crypto = require("crypto");

function getJSON(path) {
  return new Promise((res, rej) => {
    http.get({ host: "127.0.0.1", port: 9222, path }, (r) => {
      let b = "";
      r.on("data", (c) => (b += c));
      r.on("end", () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
    }).on("error", rej);
  });
}

function wsConnect(url) {
  const u = new URL(url);
  return new Promise((resolve, reject) => {
    const key = crypto.randomBytes(16).toString("base64");
    const sock = net.connect(Number(u.port), u.hostname, () => {
      sock.write(
        `GET ${u.pathname} HTTP/1.1\r\nHost: ${u.host}\r\nUpgrade: websocket\r\n` +
        `Connection: Upgrade\r\nSec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`
      );
    });
    let buf = Buffer.alloc(0), hs = false;
    const listeners = [];
    sock.on("data", (d) => {
      buf = Buffer.concat([buf, d]);
      if (!hs) {
        const i = buf.indexOf("\r\n\r\n");
        if (i < 0) return;
        hs = true;
        buf = buf.subarray(i + 4);
        resolve(api);
      }
      while (buf.length >= 2) {
        const op = buf[0] & 0x0f;
        let len = buf[1] & 0x7f, off = 2;
        if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
        else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
        if (buf.length < off + len) return;
        const p = buf.subarray(off, off + len);
        buf = buf.subarray(off + len);
        if (op === 1) {
          try {
            const msg = JSON.parse(p.toString());
            listeners.forEach((f) => f(msg));
          } catch {}
        }
      }
    });
    sock.on("error", reject);
    const api = {
      onMessage: (f) => listeners.push(f),
      send: (o) => {
        const p = Buffer.from(JSON.stringify(o));
        const mask = crypto.randomBytes(4);
        const masked = Buffer.alloc(p.length);
        for (let i = 0; i < p.length; i++) masked[i] = p[i] ^ mask[i % 4];
        let hdr;
        if (p.length < 126) { hdr = Buffer.alloc(6); hdr[1] = 0x80 | p.length; mask.copy(hdr, 2); }
        else { hdr = Buffer.alloc(8); hdr[1] = 0x80 | 126; hdr.writeUInt16BE(p.length, 2); mask.copy(hdr, 4); }
        hdr[0] = 0x81;
        sock.write(Buffer.concat([hdr, masked]));
      },
      close: () => sock.destroy(),
    };
  });
}

async function evaluate(ws, id, expression) {
  const done = new Promise((res) => ws.onMessage((m) => { if (m.id === id) res(m); }));
  ws.send({ id, method: "Runtime.evaluate", params: { expression, returnByValue: true, awaitPromise: true } });
  const r = await Promise.race([done, new Promise((x) => setTimeout(() => x({ timeout: true }), 10000))]);
  if (r.timeout) return { error: "timeout" };
  if (r.result?.exceptionDetails) return { error: r.result.exceptionDetails.text };
  return r.result?.result?.value;
}

(async () => {
  const targets = await getJSON("/json/list");
  const page = targets.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
  if (!page) { console.error("no page target"); process.exit(1); }
  const ws = await wsConnect(page.webSocketDebuggerUrl);

  // Turn on console capture first so we see messages emitted from here on.
  ws.send({ id: 900, method: "Runtime.enable" });
  ws.send({ id: 901, method: "Log.enable" });
  const errors = [];
  ws.onMessage((m) => {
    if (m.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(m.params?.type)) {
      errors.push((m.params.args || []).map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 200));
    }
    if (m.method === "Log.entryAdded" && m.params?.entry?.level === "error") {
      errors.push(String(m.params.entry.text).slice(0, 200));
    }
  });

  console.log("url   :", await evaluate(ws, 1, "location.href"));
  console.log("title :", await evaluate(ws, 2, "document.title"));
  const text = await evaluate(ws, 3, "document.body ? document.body.innerText.slice(0,600) : '(no body)'");
  console.log("text  :", JSON.stringify(text));
  const globals = await evaluate(ws, 4, "Object.keys(window).filter(k=>/store|auth|sand|ipc|api/i.test(k)).slice(0,25)");
  console.log("globals:", JSON.stringify(globals));
  const bridge = await evaluate(ws, 5, "typeof window.electron !== 'undefined' ? Object.keys(window.electron||{}).slice(0,20) : 'no electron'");
  console.log("bridge:", JSON.stringify(bridge));

  await new Promise((r) => setTimeout(r, 2500));
  console.log("errors:", JSON.stringify(errors.slice(0, 10), null, 1));
  ws.close();
  process.exit(0);
})();
