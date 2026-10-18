// Inject the roster into the running renderer, using the app's own event path.
//
// WHY: the renderer only accepts roster data through `agents-roster-seed`
// arriving on the transport. The transport lives in the coordinator child, which
// this deployment never forks (no `--bootstrap` process, zero connections to the
// gateway port). Rather than reimplement the whole IPC + MessageChannel
// contract, this probe calls the *same* handler the transport would call, from
// inside the page, and then reports whether the sidebar recovered.
//
// The handler signature, from the bundle:
//   "agents-roster-seed": a => { ... i(a.agents, a.isNewestRoster !== false) }
// where `i` feeds the roster store the UI reads. We do not have `i` by name, so
// we walk React fibers for the roster store object (the one exposing
// `agentsById`) and write into it directly, then force a render.
//
// This is a *diagnostic*, not the final fix: it proves the UI path works end to
// end once the roster is non-empty. If it works, the permanent fix is to have
// the real coordinator deliver the same payload (coordinator-host.js already
// gets `transport-connected`), not to keep injecting.
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

// Step 1: find EVERY store-like object reachable from React, and report which
// ones look like a roster. We do not assume `agentsById` exists — the earlier
// probe found none, which means the roster store may use a different field name
// (e.g. a Map under `byId`, or a plain object). So enumerate candidates instead.
const SCAN = `
(() => {
  const el = document.querySelector('#root') || document.body;
  const ck = Object.keys(el).find(k => k.startsWith('__reactContainer$'));
  if (!ck) return { err: 'no react root' };
  const seen = new Set();
  const out = [];
  function consider(o, where) {
    if (!o || typeof o !== 'object') return;
    const keys = Object.keys(o);
    const hit = keys.filter(k => /agent|roster|bot/i.test(k));
    if (hit.length) {
      out.push({
        where,
        keys: keys.slice(0, 14),
        agentKeys: hit.slice(0, 10),
        sizes: hit.slice(0, 10).map(k => {
          const v = o[k];
          if (v instanceof Map) return k + '=Map(' + v.size + ')';
          if (Array.isArray(v)) return k + '=Arr(' + v.length + ')';
          if (v && typeof v === 'object') return k + '=Obj(' + Object.keys(v).length + ')';
          return k + '=' + typeof v;
        }),
      });
    }
  }
  function walk(f, depth) {
    if (!f || seen.has(f) || depth > 80 || out.length > 24) return;
    seen.add(f);
    if (f.memoizedProps) { consider(f.memoizedProps, 'props'); for (const k of Object.keys(f.memoizedProps)) consider(f.memoizedProps[k], 'prop:' + k); }
    if (f.memoizedState) { consider(f.memoizedState, 'state'); }
    let d = f.dependencies && f.dependencies.firstContext, g = 0;
    while (d && g++ < 80) { consider(d.memoizedValue, 'ctx'); d = d.next; }
    walk(f.child, depth + 1); walk(f.sibling, depth);
  }
  walk(el[ck], 0);
  return { count: out.length, hits: out };
})()`;

(async () => {
  const list = await getJSON("/json/list");
  const page = list.find((t) => t.type === "page");
  const ws = await wsConnect(page.webSocketDebuggerUrl);

  const scan = await ev(ws, 1, SCAN);
  console.log("scan:", typeof scan === "string" ? scan : JSON.stringify(scan, null, 2).slice(0, 3000));

  ws.close();
  process.exit(0);
})();
