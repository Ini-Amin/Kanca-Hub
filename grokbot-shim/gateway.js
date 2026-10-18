// In-box Sand gateway stand-in: serves BOTH HTTP/1.1 and HTTP/2 (h2c).
//
// WHY BOTH PROTOCOLS
// -----------------
// The app's coordinator reaches the gateway two different ways:
//
//   1. `Lw()` health probe  -> `fetch(`${baseUrl}/health`)`. Node's global
//      `fetch` (undici) speaks **HTTP/1.1** for an `http://` URL. An h2c-only
//      server accepts the TCP connection and then fails the exchange, which
//      surfaces as `TypeError` / `fetch failed` with `baseUrlKind:"loopback"`.
//      That is the exact signature we saw after fixing the connection shape.
//   2. the event loop        -> a long-lived stream at `/events`, which the
//      bundle treats as server-sent events (`:ping`, `data:` framing).
//
// So this server runs an `http.Server` for the plain requests and, on the same
// port, upgrades the connection to an HTTP/2 session when the client sends the
// h2c preface (`PRI * HTTP/2.0`). That covers `fetch` and any h2 client.
//
// PROTOCOL NOTES (from the bundle constants)
//   paths  : /api, /events, /events/echo, /health
//   auth   : `Authorization: Bearer <token>`
//   headers: x-sand-slim-avatars, x-sand-mint-dedupe, traceparent
//   errors : `gateway/unknown-method`
//   roster : the UI filter is `harness === "temporal"`
"use strict";
const http = require("node:http");
const http2 = require("node:http2");

const PORT = Number(process.env.GW_PORT || 8791);
const log = (...a) => console.log(new Date().toISOString(), ...a);

// One agent is enough to make the sidebar render. The `agentId` must survive
// `parseAgentId()` and `harness` must be exactly "temporal" or the renderer
// filters it out and shows "Couldn't load Bots" with no error.
const AGENT = {
  agentId: "main",
  id: "main",
  name: "9router",
  description: "local gateway",
  title: "9router",
  harness: "temporal",
  visibility: 1,
  avatarShape: 0,
  avatarColor: "",
  createdAtMs: Date.now(),
  updatedAtMs: Date.now(),
};

// SSE frames the coordinator forwards to the renderer as transport events.
function rosterSeed() {
  return {
    type: "agents-roster-seed",
    agents: [AGENT],
    isNewestRoster: true,
  };
}

function openEventStream(res, write) {
  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    connection: "keep-alive",
  });
  // A leading comment proves liveness immediately; the client's frame sniffer
  // treats `:ping` as a valid first frame.
  res.write(":ping\n\n");
  write();
  const hb = setInterval(() => {
    try { res.write(":ping\n\n"); } catch { clearInterval(hb); }
  }, 15000);
  res.on("close", () => clearInterval(hb));
}

function sse(res, event) {
  res.write("data: " + JSON.stringify(event) + "\n\n");
}

// ---------- HTTP/1.1 handler ----------
const server = http.createServer((req, res) => {
  const path = (req.url || "").split("?")[0];
  log("H1", req.method, path);

  if (path === "/health") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: true }));
    return;
  }
  if (path === "/events") {
    openEventStream(res, () => {
      sse(res, rosterSeed());
      log("  sent roster seed (h1)");
    });
    return;
  }
  if (path === "/events/echo") {
    // Echo channel: the client posts and expects its own frames back.
    let buf = "";
    req.on("data", (c) => (buf += c));
    req.on("end", () => {
      openEventStream(res, () => {
        if (buf.trim()) res.write(buf.endsWith("\n\n") ? buf : buf + "\n\n");
      });
    });
    return;
  }
  if (path === "/api" || path.startsWith("/api/")) {
    // dune-rpc method call. The coordinator does:
    //   fetch(`${baseUrl}/api/${method}`, { method:"POST",
    //          headers: { "content-type": "application/json", ...auth } })
    // and then validates the reply's content type against the request kind:
    //   `yD(ct)` maps `application/json` -> {stream:false,binary:false}
    //   `application/connect+json`      -> {stream:true, binary:false}
    // A mismatch throws `unsupported content type <ct>` and surfaces in the
    // renderer as
    //   gateway-unreachable: ... (http_5xx): unsupported content type application/json
    // which is what the roster slice reported. So the reply content type must
    // match what the client expects for that method: unary -> application/json.
    let buf = "";
    req.on("data", (c) => (buf += c));
    req.on("end", () => {
      const method = path.slice("/api/".length) || "(none)";
      let body = {};
      try { body = JSON.parse(buf || "{}"); } catch {}
      log("  /api method:", method, "body:", JSON.stringify(body).slice(0, 160));
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(apiReply(method)));
    });
    return;
  }
  res.writeHead(404, { "content-type": "application/json" });
  res.end(JSON.stringify({ error: "gateway/unknown-method" }));
});

// Reply payloads for the dune-rpc methods the coordinator actually calls.
//
// `getServerAgentRoster` is the one that matters: the renderer's roster slice
// records `failure.command === "getServerAgentRoster"`, so this is the call that
// populates the sidebar. `getMainAgent` is its companion (mainAgent slice).
function apiReply(method) {
  switch (method) {
    case "getServerAgentRoster":
    case "listAgents":
      return { agents: [AGENT], isNewestRoster: true };
    case "getMainAgent":
      return { agent: AGENT };
    case "ensureDefaultMainAgent":
      return { agent: AGENT, created: false };
    case "getAgentAutomations":
    case "listAccountAutomations":
    case "listAgentAutomations":
      return { automations: [] };
    case "getAgentTasks":
    case "listAgentTasks":
      return { tasks: [] };
    case "getSubagents":
      return { subagents: [] };
    default:
      // Unknown method: empty object keeps the client's validator happy and
      // avoids a retry storm. dune-rpc's own error code for this is
      // `gateway/unknown-method`, but a hard error makes the client retry.
      return {};
  }
}

// ---------- h2c upgrade ----------
// Node's http.Server emits `upgrade` for an h2c preface. Handling it here lets
// one port serve both protocols, which is what the coordinator expects because
// it derives a single `baseUrl` and uses `fetch` (h1) plus a streaming client.
server.on("upgrade", (req, socket, head) => {
  log("h2c upgrade");
  const h2 = http2.createServer();
  h2.on("stream", (stream, headers) => {
    const path = headers[":path"] || "";
    log("H2", headers[":method"], path);
    if (path === "/health") {
      stream.respond({ ":status": 200, "content-type": "application/json" });
      stream.end(JSON.stringify({ ok: true }));
      return;
    }
    if (path === "/events") {
      stream.respond({ ":status": 200, "content-type": "text/event-stream" });
      stream.write(":ping\n\n");
      stream.write("data: " + JSON.stringify(rosterSeed()) + "\n\n");
      log("  sent roster seed (h2)");
      const hb = setInterval(() => { try { stream.write(":ping\n\n"); } catch { clearInterval(hb); } }, 15000);
      stream.on("close", () => clearInterval(hb));
      return;
    }
    stream.respond({ ":status": 200, "content-type": "application/json" });
    stream.end("{}");
  });
  h2.emit("connection", socket, head);
});

server.listen(PORT, "127.0.0.1", () => log("gateway on 127.0.0.1:" + PORT + " (http/1.1 + h2c)"));
