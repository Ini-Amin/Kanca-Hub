// Find the roster slice of the renderer store and dump its exact shape.
//
// WHY: the UI shows "Couldn't load Bots" because `roster.agentsById` is empty.
// The roster is normally pushed by the coordinator over the transport, but the
// coordinator never spawns in this deployment (no `--bootstrap` child, no
// gateway port). Before deciding whether to patch the bundle, we need to know
// the exact object the UI reads, so a seed can be written in the right shape.
//
// `cdp-store.js` already proved the fiber walk works, so this reuses it and
// additionally hunts for any object exposing `agentsById`.
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
  const r = await Promise.race([done, new Promise((x) => setTimeout(() => x({ timeout: 1 }), 15000))]);
  if (r.timeout) return "TIMEOUT";
  if (r.result?.exceptionDetails) return "EXC " + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text || "");
  return r.result?.result?.value;
}

// Hunt any React-reachable object that exposes `agentsById`.
const FIND_ROSTER = `
(() => {
  const el = document.querySelector('#root') || document.body;
  const ck = Object.keys(el).find(k => k.startsWith('__reactContainer$'));
  if (!ck) return { err: 'no react root' };
  const seen = new Set();
  const hits = [];
  function walk(f, depth) {
    if (!f || seen.has(f) || depth > 80) return;
    seen.add(f);
    const cands = [];
    if (f.memoizedProps) { for (const k of Object.keys(f.memoizedProps)) cands.push(f.memoizedProps[k]); }
    if (f.memoizedState) cands.push(f.memoizedState);
    let d = f.dependencies && f.dependencies.firstContext, g = 0;
    while (d && g++ < 80) { cands.push(d.memoizedValue); d = d.next; }
    for (const c of cands) {
      if (c && typeof c === 'object' && c.agentsById && typeof c.agentsById.get === 'function') {
        if (!hits.includes(c)) hits.push(c);
      }
    }
    walk(f.child, depth + 1); walk(f.sibling, depth);
  }
  walk(el[ck], 0);
  if (!hits.length) return { err: 'no agentsById found' };
  const h = hits[0];
  return {
    keys: Object.keys(h).slice(0, 40),
    agentCount: h.agentsById.size,
    sampleKeys: [...h.agentsById.keys()].slice(0, 5),
    // Probe the neighbouring fields the UI filters on.
    hasRoster: 'roster' in h,
    loaded: h.loaded, status: h.status, error: h.error ? String(h.error) : null,
  };
})()`;

(async () => {
  const list = await getJSON("/json/list");
  const page = list.find((t) => t.type === "page");
  const ws = await wsConnect(page.webSocketDebuggerUrl);
  console.log("roster probe:", JSON.stringify(await ev(ws, 1, FIND_ROSTER), null, 2));
  ws.close();
  process.exit(0);
})();
