// Read the app's live auth state over CDP.
//
// WHY: `cdp-values.js` only reports `connection.transport`, which is the *end*
// of a long chain (token -> sub -> accountScope -> account record -> profile ->
// principal -> transport). When transport is "down" it says nothing about where
// the chain broke. This probe walks the earlier links: what auth kind the
// renderer settled on, whether it has an authId, and which account scope it
// computed.
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
        if (op === 1) { try { listeners.forEach((f) => f(JSON.parse(p.toString()))); } catch {} }
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

const EXPR = `(() => {
  const out = {};
  try {
    const w = window;
    // The renderer keeps the store on a few known globals depending on build;
    // probe all of them rather than assuming one.
    const cands = [w.__store, w.store, w.__APP_STORE__, w.__sandStore];
    const st = cands.find(Boolean);
    out.hasStore = !!st;
    if (st && typeof st.getState === "function") {
      const s = st.getState();
      out.topKeys = Object.keys(s).slice(0, 40);
      const auth = s.auth || s.authState || s.session;
      if (auth) {
        out.authKind = auth.kind ?? null;
        out.authId = auth.authId ?? null;
        out.authEmail = auth.email ?? null;
        out.authKeys = Object.keys(auth);
      }
      out.accountSlot = s.accountSlot ?? s.currentAccountSlot ?? null;
    }
  } catch (e) { out.error = String(e && e.message || e); }
  return out;
})()`;

(async () => {
  const targets = await getJSON("/json/list");
  const page = targets.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
  if (!page) { console.error("no page target"); process.exit(1); }
  const ws = await wsConnect(page.webSocketDebuggerUrl);
  const id = 1;
  const done = new Promise((res) => {
    ws.onMessage((m) => { if (m.id === id) res(m); });
  });
  ws.send({ id, method: "Runtime.evaluate", params: { expression: EXPR, returnByValue: true, awaitPromise: true } });
  const r = await Promise.race([done, new Promise((x) => setTimeout(() => x({ timeout: true }), 12000))]);
  if (r.timeout) console.log(JSON.stringify({ error: "CDP timeout" }, null, 1));
  else console.log(JSON.stringify(r.result?.result?.value ?? r, null, 1));
  ws.close();
  process.exit(0);
})();
