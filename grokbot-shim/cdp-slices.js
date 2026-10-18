// Inspect the renderer store slices that decide whether the sidebar renders.
//
// WHY: `connection.transport` is "down", which is what the UI reports as
// "Couldn't load Bots". The store is reachable (cdp-store.js works), so instead
// of guessing which slice is empty we dump the ones the sidebar reads:
//
//   agentLoad      - per-agent load/status; empty object means no agent known
//   selection      - which agent is selected
//   team / teamPolicy - team gating
//   sandAccess     - GRANTED vs unknown
//   firstRun       - onboarding phase ("shell" means it never advanced)
//
// The goal is to name the single slice that is blocking, so the fix targets the
// right RPC instead of patching the UI.
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

async function ev(ws, id, expression) {
  const done = new Promise((res) => ws.onMessage((m) => { if (m.id === id) res(m); }));
  ws.send({ id, method: "Runtime.evaluate", params: { expression, returnByValue: true, awaitPromise: true } });
  const r = await Promise.race([done, new Promise((x) => setTimeout(() => x({ timeout: 1 }), 20000))]);
  if (r.timeout) return "TIMEOUT";
  if (r.result?.exceptionDetails) {
    return "EXC " + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text || "");
  }
  return r.result?.result?.value;
}

// Grab the store once (same fiber walk cdp-values.js proved), then dump slices.
const INSTALL = `
(() => {
  const el = document.querySelector('#root') || document.body;
  const ck = Object.keys(el).find(k => k.startsWith('__reactContainer$'));
  if (!ck) return 'no react root';
  const seen = new Set();
  let found = null;
  function walk(f, depth) {
    if (!f || seen.has(f) || found || depth > 60) return;
    seen.add(f);
    const cands = [];
    if (f.memoizedProps) cands.push(f.memoizedProps.client, f.memoizedProps.value);
    let d = f.dependencies && f.dependencies.firstContext, g = 0;
    while (d && g++ < 60) { cands.push(d.memoizedValue); d = d.next; }
    for (const c of cands) {
      if (c && typeof c === 'object' && c.connection && c.agentLoad && c.team) { found = c; return; }
    }
    walk(f.child, depth + 1); walk(f.sibling, depth);
  }
  walk(el[ck], 0);
  if (!found) return 'store not found';
  window.__gbStore = found;
  return 'ok';
})()`;

(async () => {
  const list = await getJSON("/json/list");
  const page = list.find((t) => t.type === "page");
  const ws = await wsConnect(page.webSocketDebuggerUrl);

  console.log("install:", await ev(ws, 1, INSTALL));

  const SLICES = ["roster", "mainAgent", "accountSlot", "boot", "experiments", "desktop", "start", "noteReconnect"];
  for (let i = 0; i < SLICES.length; i++) {
    const k = SLICES[i];
    const v = await ev(ws, 100 + i, `
      (() => {
        const s = window.__gbStore; let v = s[${JSON.stringify(k)}];
        if (v && v.snapshots && typeof v.snapshots.get === 'function') v = v.snapshots.get();
        else if (v && typeof v.get === 'function' && v.constructor && v.constructor.name !== 'Object') v = v.get();
        try { return JSON.stringify(v, (a,b) => typeof b === 'bigint' ? String(b) : b); } catch (e) { return String(v); }
      })()`);
    console.log("\n" + k + ":", String(v).slice(0, 600));
  }

  // Enumerate every key so nothing is missed.
  const keys = await ev(ws, 999, `Object.keys(window.__gbStore)`);
  console.log("\nall store keys:", typeof keys === "string" ? keys : JSON.stringify(keys));

  ws.close();
  process.exit(0);
})();
