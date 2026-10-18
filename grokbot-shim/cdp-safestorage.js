// Ask the *running Electron app* what safeStorage reports, via the main
// process's own CDP target. The earlier probe used `require("electron")` from
// plain node, which throws MODULE_NOT_FOUND and made `Wr()` look false for the
// wrong reason — a false negative that sent us chasing the keyring.
//
// This runs inside the real main process, so `require("electron")` resolves and
// `isEncryptionAvailable()` is the genuine answer for this host.
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
        if (op === 1) { try { const m = JSON.parse(p.toString()); listeners.forEach((f) => f(m)); } catch {} }
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

async function evalIn(ws, id, expression) {
  const done = new Promise((res) => ws.onMessage((m) => { if (m.id === id) res(m); }));
  ws.send({ id, method: "Runtime.evaluate", params: { expression, returnByValue: true, awaitPromise: true, includeCommandLineAPI: true } });
  const r = await Promise.race([done, new Promise((x) => setTimeout(() => x({ timeout: true }), 12000))]);
  if (r.timeout) return "TIMEOUT";
  if (r.result?.exceptionDetails) return "EXC: " + (r.result.exceptionDetails.text || "") + " " + (r.result.exceptionDetails.exception?.description || "");
  return r.result?.result?.value;
}

(async () => {
  const list = await getJSON("/json/list");
  // The main process exposes itself as a "node" target when remote debugging is
  // on; that is where require("electron") actually works.
  const targets = list.map((t) => `${t.type} :: ${t.title} :: ${t.url}`);
  console.log("targets:");
  targets.forEach((t) => console.log("  " + t));

  const node = list.find((t) => t.type === "node" || /main/i.test(t.title || ""));
  if (!node) { console.log("\nno main-process target; only renderer is debuggable"); process.exit(0); }

  const ws = await wsConnect(node.webSocketDebuggerUrl);
  console.log("\nsafeStorage.isEncryptionAvailable():", await evalIn(ws, 1, `(()=>{try{const {safeStorage}=require("electron");return String(safeStorage.isEncryptionAvailable())}catch(e){return "ERR "+e.message}})()`));
  console.log("safeStorage backend               :", await evalIn(ws, 2, `(()=>{try{const {safeStorage}=require("electron");return safeStorage.getSelectedStorageBackend?String(safeStorage.getSelectedStorageBackend()):"(no api)"}catch(e){return "ERR "+e.message}})()`));
  console.log("app.isPackaged                    :", await evalIn(ws, 3, `(()=>{try{const {app}=require("electron");return String(app.isPackaged)}catch(e){return "ERR "+e.message}})()`));
  ws.close();
  process.exit(0);
})();
