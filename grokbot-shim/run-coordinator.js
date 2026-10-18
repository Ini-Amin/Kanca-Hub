// Boot the app's OWN Sand gateway (node-agent-coordinator) the way the app does.
//
// WHY: the gateway that owns the transport is not something we should imitate.
// It ships with the app at `dist/node-agent-coordinator/main.cjs`. Running it
// with plain `node main.cjs` fails twice:
//
//   node-agent-coordinator: missing --bootstrap argument
//   node-agent-coordinator: no renderer data channel was transferred
//
// The second error is `process.send == null`, i.e. it must be forked as a child
// with an IPC channel, not spawned. This script does exactly that: `fork` with
// `--bootstrap <json>` in argv, which is what `QK()` parses.
//
// Bootstrap schema, from the validator `Tw()`:
//   { processConfig: { appVersion: string, isPackaged: boolean, dataDir: string } }
//
// If the coordinator adopts, it will open its gateway listener (paths /api,
// /events, /events/echo, /health) and we can point `box.gatewayUrl` at it
// instead of at the fake h2c probe.
"use strict";
const { fork } = require("node:child_process");
const path = require("node:path");

const ARTIFACT = process.env.COORD_ARTIFACT
  || "/root/grokbot/asarx/dist/node-agent-coordinator/main.cjs";
const DATA_DIR = process.env.SAND_USER_DATA_DIR || "/root/.config/Grok Bot";

const bootstrap = {
  processConfig: {
    appVersion: process.env.SAND_CLIENT_APP_VERSION || "0.71.2",
    isPackaged: true,
    dataDir: DATA_DIR,
  },
};

console.log("artifact :", ARTIFACT);
console.log("bootstrap:", JSON.stringify(bootstrap));

const child = fork(ARTIFACT, [`--bootstrap=${JSON.stringify(bootstrap)}`], {
  // `ipc` is what makes `process.send` exist; without it the child rejects with
  // "no renderer data channel was transferred".
  stdio: ["ignore", "pipe", "pipe", "ipc"],
  env: {
    ...process.env,
    SAND_PACKAGED: "1",
    NODE_PATH: path.join(path.dirname(ARTIFACT), "..", "deps"),
  },
});

child.stdout.on("data", (d) => process.stdout.write("[coord:out] " + d));
child.stderr.on("data", (d) => process.stderr.write("[coord:err] " + d));

child.on("message", (m) => {
  let s;
  try { s = JSON.stringify(m); } catch { s = String(m); }
  console.log("[coord:msg]", s.slice(0, 400));
});

child.on("exit", (code, sig) => {
  console.log("[coord] exited", code, sig);
  process.exit(code ?? 0);
});

// Give it a moment, then report which ports it opened.
setTimeout(() => {
  console.log("[parent] child pid:", child.pid, "connected:", child.connected);
}, 3000);

setTimeout(() => {
  console.log("[parent] still alive after 20s — coordinator adopted");
}, 20000);
