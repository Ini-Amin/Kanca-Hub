// Drive the app's real Sand coordinator, answering its control-channel requests.
//
// WHY THIS EXISTS
// ---------------
// The transport (and therefore the roster) belongs to `node-agent-coordinator`.
// It ships with the app and it DOES adopt when forked with an IPC channel plus
// `--bootstrap`. On adoption it emits `coordinator-control` frames and then
// blocks waiting for answers. In the last run it asked, in order:
//
//   lifecycle/hello (protocolVersion 3)
//   resolveGatewayConnection {path:"cookie-provider"}
//   resolveGatewayConnection {path:"gateway"}
//   resolveServerTranscriptTail
//   isOnePasswordAppInstalled
//   resolveGatewayConnection {path:"local-exec-refresh"}
//   reportGatewayReachability {outcome:"timeout", method:"events"}
//   mintLocalExecDaemonCredential
//
// So the missing piece is not the gateway protocol itself — it is these answers.
// `resolveGatewayConnection{path:"gateway"}` is the one that tells the
// coordinator where the in-box Sand gateway lives. Point it at the h2c probe
// (`gateway.js`, port 8791) and the transport can connect.
//
// Frame shapes are echoed back with the same `requestId`, on the same channel.
"use strict";
const { fork } = require("node:child_process");
const path = require("node:path");

const ARTIFACT = process.env.COORD_ARTIFACT
  || "/root/grokbot/asarx/dist/node-agent-coordinator/main.cjs";
const DATA_DIR = process.env.SAND_USER_DATA_DIR || "/root/.config/Grok Bot";
const GATEWAY_URL = process.env.GATEWAY_URL || "http://127.0.0.1:8791";
const GATEWAY_TOKEN = process.env.GATEWAY_TOKEN || "shim-gateway-token";
const NETWORK_TOKEN = process.env.NETWORK_TOKEN || "shim-network-token";

const bootstrap = {
  processConfig: {
    appVersion: process.env.SAND_CLIENT_APP_VERSION || "0.71.2",
    isPackaged: true,
    dataDir: DATA_DIR,
  },
};

const log = (...a) => console.log(new Date().toISOString(), ...a);

const child = fork(ARTIFACT, [`--bootstrap=${JSON.stringify(bootstrap)}`], {
  stdio: ["ignore", "pipe", "pipe", "ipc"],
  env: {
    ...process.env,
    SAND_PACKAGED: "1",
    NODE_PATH: path.join(path.dirname(ARTIFACT), "..", "deps"),
  },
});

child.stdout.on("data", (d) => process.stdout.write("[out] " + d));
child.stderr.on("data", (d) => process.stderr.write("[err] " + d));

// Reply on the coordinator-control channel with the same requestId.
//
// The reply envelope is validated by `XK()` and MUST be one of:
//   { status: "ok",     value: <any> }
//   { status: "failed", failure: { code: string, message: string, ... } }
// Sending the payload bare (e.g. `{url, token}`) is a protocol breach: the
// coordinator logs `lifecycle shutdown reason:"protocol-error"` and exits 1
// immediately after the first bad answer. That is exactly the failure mode we
// hit before adding this envelope.
function reply(requestId, value) {
  const frame = {
    channel: "coordinator-control",
    frame: { kind: "reply", requestId, outcome: { status: "ok", value } },
  };
  log("-> reply", requestId, JSON.stringify(value).slice(0, 200));
  child.send(frame);
}

function replyError(requestId, code, message) {
  const frame = {
    channel: "coordinator-control",
    frame: {
      kind: "reply",
      requestId,
      outcome: { status: "failed", failure: { code, message } },
    },
  };
  log("-> reply-error", requestId, code, message);
  child.send(frame);
}

child.on("message", (msg) => {
  const ch = msg && msg.channel;
  const f = msg && msg.frame;
  if (!f) { log("[msg]", JSON.stringify(msg).slice(0, 200)); return; }

  if (ch === "coordinator-control" && f.kind === "request") {
    log("<- request", f.requestId, f.method, JSON.stringify(f.args || {}).slice(0, 160));
    switch (f.method) {
      case "resolveGatewayConnection": {
        // The coordinator consumes this value as a *connection object*, not a
        // `{url, token}` pair. Evidence from the bundle:
        //   ensureConnection():  o.baseUrl, o.headers
        //   Lw(health probe):    fetch(`${baseUrl}/health`, { headers })
        //   In(baseUrl):         classifies hostname as loopback | pod_proxy
        //   transport:           `L2(conn, {token})` builds
        //                        `authorization: Bearer <token>` from the token
        // So the bearer has to be baked into `headers` here; there is no
        // separate token field the transport would read. Returning `{url,…}`
        // leaves `baseUrl` undefined, and `fetch(undefined + "/health")` throws
        // `TypeError/ERR_INVALID_URL`, which surfaces as
        // `reportGatewayReachability{outcome:"network",baseUrlKind:"unknown"}`
        // on a loop and never connects.
        reply(f.requestId, {
          baseUrl: GATEWAY_URL,
          headers: {
            authorization: `Bearer ${GATEWAY_TOKEN}`,
            "x-sand-network-token": NETWORK_TOKEN,
          },
        });
        return;
      }
      case "resolveServerTranscriptTail":
        reply(f.requestId, { entries: [], nextBeforeSeq: undefined });
        return;
      case "isOnePasswordAppInstalled":
        reply(f.requestId, { installed: false });
        return;
      case "mintLocalExecDaemonCredential":
        reply(f.requestId, { credential: null });
        return;
      case "reportGatewayReachability":
        // Fire-and-forget telemetry; acknowledge so the coordinator is not stuck.
        reply(f.requestId, { ok: true });
        return;
      default:
        // Unknown control method: answer with an empty object rather than
        // hanging, so one missing handler cannot stall the whole boot.
        reply(f.requestId, {});
        return;
    }
  }

  if (ch === "coordinator-control" && f.kind === "lifecycle") {
    log("<- lifecycle", f.phase);
    return;
  }

  log("[other]", ch, f.kind || "", JSON.stringify(f).slice(0, 220));
});

child.on("exit", (code, sig) => {
  log("[coord] exited", code, sig);
  process.exit(code ?? 0);
});

process.on("SIGTERM", () => child.kill("SIGTERM"));
process.on("SIGINT", () => child.kill("SIGINT"));

log("coordinator forked, pid", child.pid, "gateway ->", GATEWAY_URL);
