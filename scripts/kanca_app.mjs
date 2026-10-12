#!/usr/bin/env node
// Kanca app server: one port (20128) = 9Router (moved to 20129) + Agent page.
//   /agent            chat page; the model runs Auto-FreeCF tools while you watch
//   /agent/api/chat   NDJSON stream of steps (loopback only: it runs commands)
//   everything else   proxied to 9Router, retried while 9Router restarts
// Node stdlib only. Env: R9_KEY (9Router API key), NINEROUTER_PORT, KANCA_PORT.
import http from "node:http";
import net from "node:net";
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";

const PORT = +(process.env.KANCA_PORT || 20128);
const R9 = +(process.env.NINEROUTER_PORT || 20129);
const REPO = new URL("..", import.meta.url).pathname;
const VENV = `${homedir()}/.local/share/auto-freecf/venv/bin/python`;
const CAMOU = `${homedir()}/.local/share/auto-freecf/camoufox-venv/bin/python`;

// Each tool is an argv prefix; the model only supplies extra args (no shell).
export const TOOLS = {
  kancahub: [VENV, "scripts/kancahub.py"],          // autofarm, thk, mail, 9router, warp, zcode, doctor...
  // The ASUS owns the boxes and runs the rotate daemon; RAILBOX_HOST=local uses this machine's own.
  railbox: process.env.RAILBOX_HOST === "local" ? ["python3", "scripts/railbox.py"]
    : ["ssh", "-o", "ConnectTimeout=10", process.env.RAILBOX_HOST || "asus", "cd ~/Auto-FreeCF && python3 scripts/railbox.py"],
  tencent_claim: [CAMOU, "scripts/tencent_claim.py"],
  mailboxes: [VENV, "scripts/mailboxes.py"],
  warp_manager: [VENV, "scripts/warp_manager.py"],
  mobile_saving: [VENV, "scripts/mobile_saving.py"],
  r9_farm_loop: [VENV, "scripts/r9_farm_loop.py"],
};
const SPEC = [{ type: "function", function: {
  name: "run", description: "Run an Auto-FreeCF tool with CLI args. Use `--help` first if unsure of args. Output is truncated to the last 6000 chars.",
  parameters: { type: "object", required: ["tool", "args"], properties: {
    tool: { type: "string", enum: Object.keys(TOOLS) },
    args: { type: "array", items: { type: "string" } } } } } }];
const SYSTEM = `You are the Kanca agent on the user's laptop. You operate the Auto-FreeCF toolkit through the "run" tool.
Tools: ${Object.keys(TOOLS).join(", ")}. Free/self-serve only; never enter payment details. Keep replies short.`;

const loopback = (req) => /^(::1|127\.|::ffff:127\.)/.test(req.socket.remoteAddress || "");

function r9(path, body) {
  return new Promise((ok, bad) => {
    const q = http.request({ host: "127.0.0.1", port: R9, path, method: body ? "POST" : "GET",
      headers: { authorization: `Bearer ${process.env.R9_KEY || ""}`, "content-type": "application/json" } }, (r) => {
      let s = ""; r.on("data", (c) => (s += c)); r.on("end", () => { try { ok(JSON.parse(s)); } catch { bad(new Error(`${r.statusCode} ${s.slice(0, 300)}`)); } });
    });
    q.on("error", bad); q.end(body && JSON.stringify(body));
  });
}

function runTool({ tool, args = [] }, emit, live) {
  const argv = TOOLS[tool];
  if (!argv) return Promise.resolve(`unknown tool ${tool}`);
  return new Promise((ok) => {
    // ssh joins args into a remote shell line, so quote each one there.
    const q = (s) => argv[0] === "ssh" ? `'${String(s).replace(/'/g, `'\\''`)}'` : String(s);
    const p = spawn(argv[0], [...argv.slice(1), ...args.map(q)], { cwd: REPO,
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
    live.add(p);
    let out = "";
    const take = (c) => { out = (out + c).slice(-6000); emit({ type: "out", text: String(c) }); };
    p.stdout.on("data", take); p.stderr.on("data", take);
    const t = setTimeout(() => p.kill(), 20 * 60e3);
    p.on("close", (code) => { clearTimeout(t); live.delete(p); ok(`${out}\n[exit ${code}]`); });
    p.on("error", (e) => ok(`spawn failed: ${e.message}`));
  });
}

async function chat(req, res) {
  let body = ""; for await (const c of req) body += c;
  const { model, messages } = JSON.parse(body);
  res.writeHead(200, { "content-type": "application/x-ndjson" });
  const emit = (e) => res.write(JSON.stringify(e) + "\n");
  const live = new Set(); res.on("close", () => live.forEach((p) => p.kill()));
  const msgs = [{ role: "system", content: SYSTEM }, ...messages];
  try {
    for (let step = 0; step < 15 && !res.destroyed; step++) {
      const r = await r9("/v1/chat/completions", { model, messages: msgs, tools: SPEC, stream: false, max_tokens: 4000 });
      const m = r.choices?.[0]?.message;
      if (!m) throw new Error(JSON.stringify(r).slice(0, 400));
      msgs.push(m);
      if (m.content) emit({ type: "text", text: m.content });
      if (!m.tool_calls?.length) break;
      for (const tc of m.tool_calls) {
        let a; try { a = JSON.parse(tc.function.arguments || "{}"); } catch { a = {}; }
        emit({ type: "tool", tool: a.tool, args: a.args || [] });
        msgs.push({ role: "tool", tool_call_id: tc.id, content: await runTool(a, emit, live) });
      }
    }
  } catch (e) { emit({ type: "error", text: e.message }); }
  emit({ type: "done", messages: msgs.slice(1) }); res.end();
}

function proxy(req, res, body, deadline) {
  const up = http.request({ host: "127.0.0.1", port: R9, path: req.url, method: req.method, headers: req.headers },
    (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
  up.on("error", (e) => {
    if (e.code === "ECONNREFUSED" && Date.now() < deadline) return setTimeout(proxy, 500, req, res, body, deadline);
    if (!res.headersSent) res.writeHead(502);
    res.end(`kanca: 9Router down (${e.code})`);
  });
  up.end(body);
}

const page = () => readFileSync(new URL("agent.html", import.meta.url));
const server = http.createServer(async (req, res) => {
  if (req.url === "/agent" || req.url === "/agent/") return res.writeHead(200, { "content-type": "text/html" }).end(page());
  if (req.url.startsWith("/agent/api/")) {
    if (!loopback(req)) return res.writeHead(403).end("agent API is loopback-only");
    if (req.url === "/agent/api/models") {
      try { const d = await r9("/v1/models"); return res.end(JSON.stringify(d.data.map((m) => m.id))); }
      catch (e) { return res.writeHead(502).end(e.message); }
    }
    if (req.url === "/agent/api/chat" && req.method === "POST") return chat(req, res);
    return res.writeHead(404).end();
  }
  const chunks = []; req.on("data", (c) => chunks.push(c));
  req.on("end", () => proxy(req, res, Buffer.concat(chunks), Date.now() + 20000));
});
server.on("upgrade", (req, sock, head) => {
  const up = net.connect(R9, "127.0.0.1", () => {
    up.write(`${req.method} ${req.url} HTTP/1.1\r\n` + Object.entries(req.headers).map(([k, v]) => `${k}: ${v}`).join("\r\n") + "\r\n\r\n");
    up.write(head); sock.pipe(up).pipe(sock);
  });
  up.on("error", () => sock.destroy()); sock.on("error", () => up.destroy());
});

if (process.argv[2] === "--test") {
  const live = new Set(), seen = [];
  const out = await runTool({ tool: "railbox", args: ["--help"] }, (e) => seen.push(e), live);
  console.assert(out.includes("swarm") && out.includes("[exit 0]") && seen.length, out);
  console.assert((await runTool({ tool: "rm", args: ["-rf", "/"] }, () => {}, live)).startsWith("unknown tool"));
  console.log("ok");
} else server.listen(PORT, process.env.KANCA_HOST || "0.0.0.0", () => console.log(`kanca app on :${PORT} -> 9Router :${R9}`));
