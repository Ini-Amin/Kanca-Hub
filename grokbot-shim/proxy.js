// grok-bot (Cursor Sand) -> 9router gRPC-Web shim.
// Reuses the app's own proto.cjs so request/response codecs always match.
"use strict";
const http = require("http");
// proto.cjs ships inside the unpacked app (app.asar). The location differs per
// host (blu keeps it under /root/grokbot/asarx, the desktop under ~/grokre), so
// probe a candidate list instead of hardcoding one box's layout. A wrong path
// here is fatal: the shim dies at require() and the launcher reports only
// "SHIM FAILED", which looks like a port conflict but is actually this.
const PROTO_CANDIDATES = [
  process.env.GROKBOT_PROTO,
  "/root/grokbot/asarx/dist/electron-main/proto.cjs",
  "/home/amen/grokre/asarx/dist/electron-main/proto.cjs",
].filter(Boolean);
const PROTO = PROTO_CANDIDATES.find((p) => {
  try { return require("node:fs").existsSync(p); } catch { return false; }
});
if (!PROTO) {
  console.error("proto.cjs not found. Set GROKBOT_PROTO. Tried:\n  " + PROTO_CANDIDATES.join("\n  "));
  process.exit(1);
}
const { GrokBotService, AiService, AnalyticsService } = require(PROTO);
const m = require(PROTO);

const ROUTER = process.env.ROUTER_URL || "http://localhost:20128";
const ROUTER_KEY = process.env.ROUTER_API_KEY || "";
// Default model must be one that actually exists on the router. The old default
// `ag/gemini-3.8-flash-high` is gone upstream (9router answers "No active
// credentials for provider: gemini"), which made every chat fail with a 502 that
// looked like a shim bug.
const ROUTER_MODEL = process.env.ROUTER_MODEL || "cbai/deepseek-v4.1-flash";
const PORT = Number(process.env.SHIM_PORT || 8790);
// The transport is an HTTP/2 RPC edge, served by gateway.js on its own port.
// Keeping it separate from the HTTP/1.1 shim is deliberate: one process cannot
// answer both protocols on one port, and mixing them silently produces a
// connected-but-idle socket that the app reports as `transport: "down"`.
const GATEWAY_PORT = Number(process.env.GATEWAY_PORT || 8791);

// ---------- grpc-web framing ----------
function frame(flag, payload) {
  const h = Buffer.alloc(5);
  h[0] = flag;
  h.writeUInt32BE(payload.length, 1);
  return Buffer.concat([h, payload]);
}
function unframe(buf) {
  const msgs = [];
  let off = 0;
  while (off + 5 <= buf.length) {
    const flag = buf[off];
    const len = buf.readUInt32BE(off + 1);
    msgs.push({ flag, data: buf.subarray(off + 5, off + 5 + len) });
    off += 5 + len;
  }
  return msgs;
}
const TRAILERS_OK = frame(0x80, Buffer.from("grpc-status:0\r\ngrpc-message:\r\n"));

// ---------- chat state ----------
const sessions = new Map(); // agentId -> {generation, nextSeq, history:[{role,content}]}
function sess(agentId) {
  // generation is uint32 (not uint64) — BigInt here throws "invalid uint 32".
  if (!sessions.has(agentId)) sessions.set(agentId, { generation: 1, nextSeq: 1n, history: [] });
  return sessions.get(agentId);
}

async function callRouter(history, model) {
  const res = await fetch(`${ROUTER}/v1/chat/completions`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(ROUTER_KEY ? { authorization: `Bearer ${ROUTER_KEY}` } : {}) },
    body: JSON.stringify({ model: model || ROUTER_MODEL, messages: history }),
  });
  if (!res.ok) throw new Error(`router ${res.status}: ${await res.text()}`);
  // 9router returns plain JSON for some models and SSE (data: {...}) for
  // others (ag/*, ocz/*). Normalize both, same as scripts/bounty_run.py chat().
  const raw = await res.text();
  const head = raw.replace(/^\s+/, "");
  if (head.startsWith("data:") || head.startsWith(":")) {
    const parts = [];
    for (const line of raw.split(/\r?\n/)) {
      const t = line.trim();
      if (!t.startsWith("data:")) continue;
      const payload = t.slice(5).trim();
      if (!payload || payload === "[DONE]") continue;
      try {
        const d = JSON.parse(payload);
        const c = d.choices?.[0];
        const txt = c?.delta?.content ?? c?.message?.content ?? "";
        if (txt) parts.push(txt);
      } catch {}
    }
    if (parts.length) return parts.join("");
    throw new Error(`SSE reply had no content: ${raw.slice(0, 160)}`);
  }
  // Some upstreams (notably Zcode/GLM) stream several complete JSON objects
  // back-to-back with no `data:` prefix and no newline framing, so `JSON.parse`
  // on the whole body dies with "Unexpected non-whitespace character after JSON
  // at position N". Collect every top-level object and concatenate their deltas.
  const objs = splitJsonObjects(raw);
  if (objs.length === 0) throw new Error(`non-JSON router reply: ${raw.slice(0, 160)}`);
  const parts = [];
  for (const o of objs) {
    if (o.error) throw new Error(`router error: ${JSON.stringify(o.error).slice(0, 200)}`);
    const c = o.choices?.[0];
    const txt = c?.delta?.content ?? c?.message?.content ?? "";
    if (txt) parts.push(String(txt));
  }
  return parts.join("");
}

// splitJsonObjects scans for balanced top-level `{...}` runs, ignoring braces
// inside strings. Returns parsed objects, skipping anything unparseable.
function splitJsonObjects(raw) {
  const out = [];
  let depth = 0, start = -1, inStr = false, esc = false;
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    if (inStr) {
      if (esc) esc = false;
      else if (ch === "\\") esc = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') { inStr = true; continue; }
    if (ch === "{") { if (depth === 0) start = i; depth++; continue; }
    if (ch === "}") {
      depth--;
      if (depth === 0 && start >= 0) {
        try { out.push(JSON.parse(raw.slice(start, i + 1))); } catch {}
        start = -1;
      }
    }
  }
  return out;
}

// entry body JSON -> bytes; shape validated by client's row parser ($1): needs id + kind + content
function messageEntry(s, content, who) {
  const body = {
    id: `e${s.nextSeq}`,
    kind: "message",
    content,
    timestampMs: Date.now(),
    sender: who, // guess; extra fields are tolerated by the validator
  };
  const e = {
    seq: s.nextSeq++,
    entryKind: "message",
    body: new TextEncoder().encode(JSON.stringify(body)),
    updatedSeq: 0n,
    entryId: body.id,
  };
  return e;
}

// ---------- principal identity ----------
// The app derives its whole session identity from the access token, not from
// any RPC response:
//
//   cn(e)  = e.authId ?? e.email            (main-app.cjs)
//   wg(e)  = cn(e) == null ? "logged-out" : sha256(cn(e))
//   lf     = (wg(state) ?? "logged-out") !== "logged-out"    // == signedIn
//
// and the token itself is decoded by:
//
//   kind:"logged-in", ...t?.sub!=null ? {authId: t.sub} : {}, ...
//
// So `authId` IS the JWT `sub`, and `signedIn` is simply "do we have a sub".
// A hardcoded GetMe.authId that disagrees with `sub` makes every downstream
// lookup miss: `seedSavedProfile` looks for
// `accountScopeOfPrincipal({principal: authId})` in the saved-accounts list and
// silently finds nothing, so the profile never seeds and the transport factory
// never gets a principal.
//
// Therefore GetMe must echo back the principal from the *request*, which is the
// only place the shim can see the token the app is actually holding.
const FALLBACK_SUB = "grokbot-local-principal";

function principalFromRequest(res) {
  try {
    const h = (res && res.req && res.req.headers) || {};
    const auth = h.authorization || h.Authorization || "";
    const tok = auth.replace(/^Bearer\s+/i, "").trim();
    const parts = tok.split(".");
    if (parts.length >= 2) {
      const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
      if (payload && typeof payload.sub === "string" && payload.sub) {
        return { sub: payload.sub, email: payload.email || `${payload.sub}@grokbot.invalid` };
      }
    }
  } catch {
    /* fall through: an unparseable token is not fatal, we still need a reply */
  }
  return { sub: FALLBACK_SUB, email: "local@grokbot.invalid" };
}

function scopeOf(principal) {
  return require("node:crypto").createHash("sha256").update(principal).digest("hex");
}

// Mint the same unsigned JWT the on-disk patch writes. `Vi()` only base64url
// decodes the payload and never touches the signature, so "alg: none" with a
// trailing empty segment is accepted verbatim. Keeping one generator for both
// the file patch and the login endpoints is what keeps `sub` identical across
// the two paths — a mismatch there is invisible and breaks the account scope.
const JWT_SUB = process.env.SHIM_PRINCIPAL_SUB || FALLBACK_SUB;
function mintToken() {
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const header = b64({ alg: "none", typ: "JWT" });
  const payload = b64({
    sub: JWT_SUB,
    email: `${JWT_SUB}@grokbot.invalid`,
    exp: Math.floor(Date.now() / 1000) + 3650 * 86400,
  });
  return `${header}.${payload}.`;
}

// ---------- method handlers ----------
// ---------- /gigs control commands ----------
// The bounty pipeline runs unattended on this box and is driven over HTTP by
// scripts/gigs_api.py (:8795). Intercepting `/gigs ...` here answers it from the
// API instead of the model, so steering the pipeline from the grok-bot window
// needs no SSH, costs zero tokens, and cannot be talked out of a guardrail by a
// prompt. Anything not starting with `/gigs` still goes to the router.
const GIGS = process.env.GIGS_URL || "http://127.0.0.1:8795";
// Must match ABH_PLATFORMS in scripts/gigs_api.py. Used only to decide which
// token of `/gigs hunt <program> <platform>` is the platform.
const GIGS_PLATFORMS = ["bugcrowd", "hackerone", "intigriti", "yeswehack", "both", "all"];

async function gigsJson(pathname, opts) {
  const res = await fetch(`${GIGS}${pathname}`, opts);
  let body = {};
  try { body = await res.json(); } catch { /* empty or non-JSON body */ }
  return { status: res.status, body };
}

// Job names come from third-party feeds and can contain `|` or newlines, either
// of which would break the Markdown table they land in.
function cell(v, max = 40) {
  return String(v == null ? "" : v).replace(/\|/g, "/").replace(/\s+/g, " ").slice(0, max);
}

function gigsErr(status, body, what) {
  if (status === 429) return `**${what}: cooldown** — retry in ${body.retry_in_s}s`;
  return `**${what} failed (${status})** — ${cell(body.error, 120)}`;
}

function fmtStatus(sessions, jobs) {
  const rows = (sessions && sessions.sessions) || [];
  const hunting = Object.keys((sessions && sessions.agent_running) || {});
  const loop = (sessions && sessions.hunt_loop_pids) || [];
  const out = [
    "**Bounty pipeline**",
    loop.length ? `- hunt loop: running (pid ${loop.join(", ")})` : "- hunt loop: **not running**",
    hunting.length ? `- agent: hunting ${hunting.join(", ")}` : "- agent: idle",
  ];
  if (!rows.length) {
    out.push("- sessions: none yet");
  } else {
    out.push("", "| target | steps | findings | last tool | state |",
             "|---|---|---|---|---|");
    for (const r of rows.slice(0, 12)) {
      // `findings` already excludes our own [SURFACE RANKING] note; showing the
      // raw row count here would report findings before any scanner has run.
      const state = r.running ? "**RUNNING**" : (r.bump_pending ? "bump waiting" : "idle");
      out.push(`| ${cell(r.target)} | ${r.steps} | ${r.findings} | ${cell((r.last_tool || {}).tool, 24)} | ${state} |`);
    }
    if (rows.length > 12) out.push(`_…and ${rows.length - 12} more_`);
  }
  const list = (jobs && jobs.jobs) || [];
  if (list.length) {
    out.push("", `**Jobs:** ${list.length}`);
    for (const j of list.slice(0, 6)) {
      out.push(`- \`${cell(j.id, 28)}\` ${cell(j.platform)} ${cell(j.program, 26)} — ${cell(j.status, 16)}`);
    }
    if (list.length > 6) out.push(`_…and ${list.length - 6} more_`);
  } else if (jobs && jobs.note) {
    out.push("", `**Jobs:** _${cell(jobs.note, 60)}_`);
  }
  return out.join("\n");
}

async function gigsCommand(raw) {
  const parts = String(raw || "").trim().split(/\s+/);
  const sub = (parts[1] || "status").toLowerCase();
  const rest = parts.slice(2);
  const post = (pathname, payload) => gigsJson(pathname, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (sub === "help" || sub === "?") {
    return [
      "`/gigs status` — sessions + jobs",
      "`/gigs discover [platform]` — pull a platform's program feed",
      "`/gigs hunt <program-slug> [platform]` — start a hunt (900s cooldown)",
      "`/gigs bump <guidance>` — steer the agent that is hunting right now",
      "`/gigs findings <id>` — findings recorded for one job",
    ].join("\n");
  }

  if (sub === "status" || sub === "jobs") {
    // Two independent reads: a dead /gigs/jobs must not hide the sessions.
    const [s, j] = await Promise.all([gigsJson("/gigs/abh/sessions"), gigsJson("/gigs/jobs")]);
    return fmtStatus(s.body, j.body);
  }

  if (sub === "discover") {
    const platform = (rest[0] || "both").toLowerCase();
    const { status, body } = await post("/gigs/discover", { platform });
    if (status >= 400) return gigsErr(status, body, "Discover");
    const lines = String(body.stdout || "").trim().split("\n").filter(Boolean);
    const head = lines.slice(0, 12).join("\n");
    return `**Discovery: \`${platform}\`** rc=${body.rc}\n\`\`\`\n${head || "(nothing found)"}\n\`\`\``
      + (lines.length > 12 ? `\n_…and ${lines.length - 12} more_` : "");
  }

  if (sub === "hunt") {
    // `/gigs hunt dropbox` and `/gigs hunt dropbox intigriti` both work: a
    // trailing token that names a platform is taken as the platform, the rest
    // is the program slug. Slugs are hyphenated, so spaced input is joined.
    let platform = "bugcrowd";
    let program = rest;
    if (rest.length > 1 && GIGS_PLATFORMS.includes(rest[rest.length - 1].toLowerCase())) {
      platform = rest[rest.length - 1].toLowerCase();
      program = rest.slice(0, -1);
    }
    const payload = { platform };
    if (program.length) payload.program = program.join("-");
    const { status, body } = await post("/gigs/abh/hunt", payload);
    if (status >= 400) return gigsErr(status, body, "Hunt");
    // 202: nothing has been hunted yet. A run is minutes of recon plus model
    // calls, so the answer says how to check rather than claiming success.
    return `**Hunt accepted** — \`${cell(payload.program || "next new program", 40)}\` on ${platform}`
      + `\ncooldown ${body.cooldown_s}s · check \`/gigs status\` or \`GET ${body.status_url}\``;
  }

  if (sub === "bump") {
    const text = rest.join(" ").trim();
    if (!text) return "**bump** needs guidance — `/gigs bump focus on the upload endpoint`";
    const { status, body } = await post("/gigs/abh/bump", { text });
    // 409 is the honest answer: writing into a finished session would be a
    // silent no-op, because agent.py reads the bump on its NEXT step.
    if (status === 409) return `**Nothing to steer** — ${cell(body.error, 80)}. ${cell(body.hint, 90)}`;
    if (status >= 400) return gigsErr(status, body, "Bump");
    return `**Guidance queued** (${body.chars} chars) for ${cell(body.session, 70)}\n`
      + `${cell(body.note, 90)}`;
  }

  if (sub === "findings") {
    const id = rest[0];
    if (!id) return "**findings** needs a job id — `/gigs findings <id>`";
    // /gigs/findings/<id> answers 200 with an empty list for an id that does
    // not exist at all, so existence is checked separately — otherwise a typo'd
    // id reads as "this job has no findings yet" and the operator waits for
    // something that can never arrive.
    const job = await gigsJson(`/gigs/jobs/${encodeURIComponent(id)}`);
    if (job.status === 404) return `**No such job** — \`${cell(id, 40)}\``;
    if (job.status >= 400) return gigsErr(job.status, job.body, "Findings");
    const { status, body } = await gigsJson(`/gigs/findings/${encodeURIComponent(id)}`);
    if (status >= 400) return gigsErr(status, body, "Findings");
    const rows = body.findings || [];
    if (!rows.length) return `**No findings** for \`${cell(id, 40)}\``;
    const out = [`**${rows.length} findings** for \`${cell(id, 40)}\``];
    for (const f of rows.slice(0, 15)) {
      out.push(`- **${cell(f.severity, 10)}** ${cell(f.tool, 20)} — ${cell(f.text, 110)}`);
    }
    if (rows.length > 15) out.push(`_…and ${rows.length - 15} more_`);
    return out.join("\n");
  }

  return `**Unknown /gigs subcommand** \`${cell(sub, 20)}\` — try \`/gigs help\``;
}

const handlers = {
  SendGrokBotUserMessage: async (req, res, send) => {
    const s = sess(req.agentId || "main");
    s.history.push({ role: "user", content: req.text });
    send({ dispatched: true }); // grpc-web unary: headers + message + trailers
    // `/gigs` is a control command, not a prompt: answer it straight from the
    // gigs API. Sending it to the model would spend tokens on it and, worse,
    // leave it to a model to decide whether a guardrail applies.
    if ((req.text || "").trim().startsWith("/gigs")) {
      let reply;
      try {
        reply = await gigsCommand(req.text);
      } catch (err) {
        // An unreachable API must read as a control-plane outage, not as the
        // chat being broken.
        reply = `[gigs] cannot reach ${GIGS} — ${err.message}`;
      }
      s.history.push({ role: "assistant", content: reply });
      broadcast(req.agentId || "main", messageEntry(s, reply, "agent"), s);
      return;
    }
    // answer asynchronously; watchers get it via the rows fanout below
    try {
      const reply = await callRouter(s.history);
      s.history.push({ role: "assistant", content: reply });
      const entry = messageEntry(s, reply, "agent");
      broadcast(req.agentId || "main", entry, s);
    } catch (err) {
      console.error("[router]", err.message);
      const entry = messageEntry(s, `[shim error] ${err.message}`, "agent");
      broadcast(req.agentId || "main", entry, s);
    }
  },
  WatchGrokBotTranscripts: async (req, res, send, keepOpen) => {
    const agentId = req.cursors?.[0]?.agentId || "main";
    const s = sess(agentId);
    send({ frame: { case: "connected", value: { streamId: `shim-${Date.now()}`, serverTimeMs: BigInt(Date.now()), absoluteLifetimeMs: 0n } } });
    const w = { agentId, send };
    watchers.add(w);
    keepOpen(); // server-streaming: leave the response open
    const hb = setInterval(() => { try { send({ frame: { case: "heartbeat", value: {} } }) } catch {} }, 15000);
    res.on("close", () => { clearInterval(hb); watchers.delete(w); });
  },
  GetGrokBotSendStatus: async () => ({ status: 2 }), // try SENT; iterate if spinner sticks
  // The roster UI filters on TWO things and drops the entry silently if either
  // is wrong: `agents.filter(o => o.harness === "temporal")` and then
  // `parseAgentId(o.agentId) != null`. Omitting `harness` yields an empty list
  // that arrives successfully — which the UI renders as "Couldn't load Bots",
  // not as an error, so the cause is invisible from the outside.
  ListGrokBotAgents: async () => ({
    agents: [{
      agentId: "main",
      id: "main",
      name: "9router",
      harness: "temporal",        // required by the roster filter
      description: "via shim",
      visibility: 1,              // PERSONAL
      createdAtMs: BigInt(Date.now()),
      updatedAtMs: BigInt(Date.now()),
    }],
    slackConnections: [],
    // `isNewestRoster` does NOT exist on ListGrokBotAgentsResponse (verified:
    // agents, slackConnections, truncated, teamsConnections). It is silently
    // dropped. Harmless, but removed so the reply matches the message.
  }),
  GetGrokBotTermsSeen: async () => ({ seen: true }),
  // DashboardService privacy/access lookups. Service prefix matters: these are
  // dispatched as aiserver.v1.DashboardService/*, not AiService/*.
  // 0 = UNSPECIFIED reads as "not migrated" and can gate the roster; 2 = NO_TRAINING
  // is a normal, non-blocking value.
  "DashboardService.GetUserPrivacyMode": async () => ({
    privacyMode: 2, accountPrivacyMode: 2, isEnforcedByTeam: false,
    isNotMigratedToServerSourceOfTruth: false, partnerDataShare: false,
    hasAcknowledgedGracePeriodDisclaimer: true, hoursRemainingInGracePeriod: 0,
  }),
  // Sand access must be GRANTED (1); UNSPECIFIED (0) drops the UI into
  // "Grok Bot is having trouble connecting" and it never reaches the chat.
  "DashboardService.GetSandAccessStatus": async () => ({ state: 1, blockReason: 1 }), // GRANTED, NONE
  "DashboardService.GetSandTrialClaimStatus": async () => ({ status: 3 }), // GRANTED
  "DashboardService.GetSandUsageStatus": async () => ({}),
  "DashboardService.GetHardLimit": async () => ({}),
  "DashboardService.GetCurrentPeriodUsage": async () => ({}),
  "DashboardService.GetEffectiveUserPlugins": async () => ({}),
  "DashboardService.GetAvailableMcpServers": async () => ({}),
  "DashboardService.GetTeams": async () => ({ teams: [] }),
  "DashboardService.GetTeamAdminSettingsOrEmptyIfNotInTeam": async () => ({}),
  // GetMe must echo the principal carried in the request token. See the
  // `principalFromRequest` note above: a mismatch between GetMe.authId and the
  // JWT `sub` makes every principal-scoped lookup miss silently.
  "DashboardService.GetMe": async (req, res) => {
    const p = principalFromRequest(res);
    return {
      authId: p.sub, userId: 1, email: p.email,
      firstName: "Grok", lastName: "Bot", workosId: p.sub,
      teamId: 0, createdAt: new Date().toISOString(), isEnterpriseUser: false,
      teamName: "", emailDomainType: "personal", country: "US",
      organizationId: 0n, organizationPublicId: "grokbot-org",
      isTeamAdmin: false, hasActiveMobileSession: false,
      publicUserId: p.sub, scmOnlyRestricted: false,
    };
  },
  // seedSavedProfile() looks up `accounts.find(a => a.id === sha256(authId))`
  // and bails out when the list is empty, so the profile never seeds and the
  // transport factory stays without a principal. Returning exactly the account
  // that matches our scope keeps that lookup a hit.
  "DashboardService.ListSavedAccounts": async (req, res) => {
    const p = principalFromRequest(res);
    return {
      accounts: [{
        id: scopeOf(p.sub),
        email: p.email,
        name: "Grok Bot (local)",
        profile: JSON.stringify({ email: p.email, name: "Grok Bot (local)" }),
      }],
    };
  },
  // Statsig bootstrap: an empty config string makes the client retry in a
  // tight loop (thousands of calls), starving the roster load. A minimal but
  // well-formed payload satisfies it.
  "AnalyticsService.BootstrapStatsig": async () => ({
    config: JSON.stringify({
      has_updates: true, time: Date.now(), feature_gates: {}, dynamic_configs: {},
      layer_configs: {}, param_stores: {}, sdkParams: {}, sdkInfo: {},
    }),
    generatedAtMs: BigInt(Date.now()),
  }),
  "AnalyticsService.GetFirstWindowStatsigDecision": async () => ({ variant: "control", reason: "shim" }),
  // Streaming RPCs called over REST: must stay open and emit JSON lines.
  // WatchSandBoxMigration drives firstBoxPhase; closing it immediately keeps
  // the UI on "Connecting to your computer…".
  "GrokBotService.WatchSandBoxMigration": async (req, res) => {
    if (!res) return {};
    res.writeHead(200, { "content-type": "application/json" });
    // one terminal DONE event (phase 6) so the client settles
    res.write(JSON.stringify({ phase: 6, detail: "shim", atMs: Date.now(), offsetKey: "shim-1", operationId: "shim-op" }) + "\n");
    return undefined; // keep open; caller closes on client disconnect
  },
  "SandBoxService.WatchSandBoxMigration": async (req, res) => {
    if (!res) return {};
    res.writeHead(200, { "content-type": "application/json" });
    res.write(JSON.stringify({ phase: 6, detail: "shim", atMs: Date.now(), offsetKey: "shim-1", operationId: "shim-op" }) + "\n");
    return undefined;
  },
  "AnalyticsService.SubmitLogs": async () => ({}),
  "DashboardService.ListMarketplacePlugins": async () => ({ plugins: [], nextPageToken: "", hasMore: false }),
  "DashboardService.GetUnifyCta": async () => ({ show: false }),
  // The sidebar shows "Connecting to your computer…" until this says RUNNING.
  //
  // NOTE THE FIELD NAME: this response's field is `state` (enum
  // SandBoxRunState), NOT `run_state`. EnsureSandBox below uses `run_state`.
  // They are different messages with different field names; using the wrong one
  // makes the proto encoder emit `{}` and the app logs
  // `leg=run-state ConnectError.Internal` forever while the shim log shows a
  // cheerful empty reply.
  GetSandBoxRunState: async () => ({ state: 3 }), // 3 = SAND_BOX_RUN_STATE_RUNNING
  // WITHOUT THIS THE SIDEBAR NEVER LOADS. The response message is FLAT, not
  // nested: `EnsureSandBoxResponse` has top-level `gateway_url`, `run_state`,
  // `gateway_token`, `vnc_url`, ... and NO `box` field at all. Returning the
  // old `{box:{...}}` shape therefore serialized to a literal `{}` — every field
  // silently dropped by the proto encoder — so the app saw a box with an empty
  // gatewayUrl and the roster had nowhere to go. Verified on the wire:
  // `[rest] ...EnsureSandBox -> {}` while the handler "returned" a full object.
  //
  // The sidebar also needs `run_state` RUNNING (3) and a `gateway_url` that
  // actually answers /health, otherwise `q7()` throws "Broker did not expose
  // the in-box Sand gateway".
  //
  // gateway_url points at the dedicated dual-protocol server (gateway.js), NOT
  // at this shim: the transport is an HTTP/2 RPC edge and this process is
  // HTTP/1.1. Pointing it here yields an ESTABLISHED socket with zero traffic
  // and `transport: "down"` — a protocol mismatch that reads like a logic bug.
  //
  // Field names are snake_case here because that is what the proto descriptor
  // declares; proto-es does not camelCase on the wire for Connect/JSON.
  EnsureSandBox: async () => ({
    runState: 3,                                     // enum SandBoxRunState RUNNING
    gatewayUrl: `http://127.0.0.1:${GATEWAY_PORT}`,
    gatewayToken: "shim-gateway-token",
    networkToken: "shim-network-token",
    vncUrl: "",
    forkVncBaseUrl: "",
    cluster: "shim",
    podId: "shim",
    // `egressTunnelOwnership` is deliberately OMITTED. The descriptor reports
    // kind=8 (a wrapper scalar), and every value tried — 0, 1, 2, 3, "shim" —
    // throws "cannot encode field egress_tunnel_ownership", which aborts the
    // ENTIRE reply and turns EnsureSandBox into `{}`. The field is optional and
    // the app copes with it absent, so dropping it is strictly better than
    // sending a value that makes the whole message unencodable.
  }),
  RecreateSandBox: async () => ({
    runState: 3, gatewayUrl: `http://127.0.0.1:${GATEWAY_PORT}`, gatewayToken: "shim-gateway-token", networkToken: "shim-network-token",
  }),
  ForceRecreateSandBox: async () => ({
    runState: 3, gatewayUrl: `http://127.0.0.1:${GATEWAY_PORT}`, gatewayToken: "shim-gateway-token", networkToken: "shim-network-token",
  }),
  // `capabilities` is a MESSAGE, not a repeated field. The roster gate is:
  //   Eon()  -> (await getGrokBotRuntimeCapabilities()).capabilities
  //   bon(c) -> c.temporalCreationEnabled && c.durableIdentityEnabled &&
  //             c.durableIdentityWritesEnabled
  //   PV().isServerRosterEnabled -> capabilities.durableIdentityEnabled ?? ...
  // Sending `capabilities: []` leaves all three undefined, so
  // `isServerRosterEnabled` is false and `listGrokBotAgents` is NEVER CALLED —
  // which is exactly the "ListGrokBotAgents 0 calls" symptom.
  //
  // KEY STYLE: camelCase here, NOT the descriptor's snake_case. proto-es field
  // descriptors report `localName` in snake_case but the JS constructor takes
  // camelCase and `toJson()` emits camelCase back. Verified directly:
  //   new C({temporal_creation_enabled:true}).toJson()  -> {}
  //   new C({temporalCreationEnabled:true}).toJson()     -> {"temporalCreationEnabled":true}
  // So a snake_case key is silently dropped, which looks identical to a false
  // capability and reproduces the same "0 calls" symptom. Do not "fix" this to
  // match the descriptor's printed names.
  GetGrokBotRuntimeCapabilities: async () => ({
    // ONLY `capabilities` exists on this response. There is no maxConcurrentTurns
    // and no supportsStreaming — verified against the descriptor:
    //   GetGrokBotRuntimeCapabilitiesResponse { capabilities: GrokBotRuntimeCapabilities }
    // Those two extras were silently dropped, which is harmless, but they made
    // the reply look richer than the message actually is.
    capabilities: {
      temporalCreationEnabled: true,
      durableIdentityEnabled: true,
      durableIdentityWritesEnabled: true,
      agentMessagingEnabled: false,
      serverRoomsEnabled: false,
    },
  }),
  GetGrokBotUserRuntimeSettings: async () => ({}),
  ListSandBoxes: async () => ({ sandBoxes: [] }),
  CommitGrokBotTranscriptEntries: async (req) => ({ committedCount: req.entries?.length ?? 0, deletedCount: 0, rejections: [] }),
  ListGrokBotTranscriptEntries: async () => ({ rows: [] }),
};
const watchers = new Set();
function broadcast(agentId, entry, s) {
  console.log("[broadcast]", agentId, "watchers:", watchers.size, "entrySeq:", String(entry.seq));
  for (const w of watchers) {
    if (w.agentId !== agentId && w.agentId !== "main") continue;
    try {
      w.send({ frame: { case: "rows", value: { agentId, generation: s.generation, entries: [entry], deletes: [], sessionId: "" } } });
      console.log("[broadcast] sent to", w.agentId);
    } catch (e) {
      console.error("[broadcast] send failed:", e.message);
    }
  }
}

// ---------- server ----------
// register every service proto.cjs exports so unknown RPCs get an empty
// response instead of a 404 the app retries forever
const svc = {};
for (const k of ["AgentStoreService", "AiService", "AnalyticsService", "BackgroundComposerService", "DashboardService", "GrokBotService", "SandBoxService"]) {
  svc[`aiserver.v1.${k}`] = m[k];
}
const server = http.createServer((req, res) => {
  // Login flow: /auth/poll 404 = pending, 200 {accessToken,refreshToken} = done.
  //
  // The token returned here is the app's whole identity: `authId = sub`, and
  // `signedIn = (sha256(authId) != "logged-out")`. Returning an opaque
  // "shim-token" makes `Vi()` (parseJwtPayload) fail, so `kind` stays
  // "logged-out" and the app treats the session as signed out no matter what
  // GetMe says. So we mint a real (unsigned) JWT with the same `sub` the
  // on-disk patch uses.
  // dune-rpc method calls. The roster/transport path is NOT a gRPC-Web call on
  // GrokBotService — it is a POST to `/api/<method>` on the origin backend URL,
  // which defaults to `https://api.origin.cursor.com` unless
  // CURSOR_ORIGIN_BACKEND_URL overrides it. With that override pointed here,
  // these arrive at the shim instead of Cloudflare.
  //
  // The renderer reported the failure verbatim:
  //   gateway-unreachable: server getServerAgentRoster unreachable (http_5xx):
  //   unsupported content type application/json
  // so the reply content type has to match the request kind (unary -> JSON).
  if (req.method === "POST" && (req.url === "/api" || req.url.startsWith("/api/"))) {
    const method = req.url.slice("/api/".length) || "(none)";
    const ch = [];
    req.on("data", (c) => ch.push(c));
    req.on("end", () => {
      let body = {};
      try { body = JSON.parse(Buffer.concat(ch).toString("utf8") || "{}"); } catch {}
      console.log("[api]", method, JSON.stringify(body).slice(0, 160));
      const agent = {
        agentId: "main", id: "main", name: "9router", description: "via shim",
        title: "9router", harness: "temporal", visibility: 1, avatarShape: 0,
        avatarColor: "", createdAtMs: Date.now(), updatedAtMs: Date.now(),
      };
      let out = {};
      if (method === "getServerAgentRoster") out = { agents: [agent], isNewestRoster: true };
      else if (method === "getMainAgent") out = { agent };
      else if (method === "ensureDefaultMainAgent") out = { agent, created: false };
      else if (/Automations?$/.test(method)) out = { automations: [] };
      else if (/Tasks?$/.test(method)) out = { tasks: [] };
      else if (method === "getSubagents") out = { subagents: [] };
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(out));
    });
    return;
  }
  if (req.url.startsWith("/auth/poll")) {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ accessToken: mintToken(), refreshToken: mintToken() }));
    return;
  }
  if (req.method === "POST" && req.url === "/oauth/token") {
    // OAuth refresh: snake_case tokens, same shape /auth/poll uses camelCase.
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ access_token: mintToken(), refresh_token: mintToken(), expires_in: 36000, token_type: "Bearer" }));
    return;
  }
  if (req.url.startsWith("/auth/login")) { // info endpoint if the client hits it
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ loginUrl: "https://cursor.com/login" }));
    return;
  }
  // ---- OpenAI-compatible surface: lets 9router use this shim as a provider node ----
  if (req.method === "GET" && (req.url === "/v1/models" || req.url === "/models")) {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ object: "list", data: [{ id: ROUTER_MODEL, object: "model", owned_by: "grokbot-shim" }] }));
    return;
  }
  if (req.method === "POST" && (req.url === "/v1/chat/completions" || req.url === "/chat/completions")) {
    const ch = [];
    req.on("data", (c) => ch.push(c));
    req.on("end", async () => {
      let body = {};
      try { body = JSON.parse(Buffer.concat(ch).toString("utf8") || "{}"); } catch {}
      // Router model ids carry their own provider prefix (`cbai/…`, `Zcode/…`,
      // `THK/…:free`), so the prefix must be preserved. Only the grok-bot-side
      // `gb/` namespace is stripped, because that one is ours, not the router's.
      // The previous blanket `^[a-z]+/` strip turned `cbai/deepseek-v4.1-flash`
      // into `deepseek-v4.1-flash`, which the router does not know.
      const model = String(body.model || ROUTER_MODEL).replace(/^gb\//i, "") || ROUTER_MODEL;
      const messages = Array.isArray(body.messages) && body.messages.length
        ? body.messages : [{ role: "user", content: "" }];
      try {
        const reply = await callRouter(messages, model);
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({
          id: `chatcmpl-shim-${Date.now()}`, object: "chat.completion", created: Math.floor(Date.now() / 1000),
          model, choices: [{ index: 0, message: { role: "assistant", content: reply }, finish_reason: "stop" }],
          usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
        }));
      } catch (e) {
        res.writeHead(502, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: { message: e.message, type: "upstream_error" } }));
      }
    });
    return;
  }
  const m = /^\/([a-zA-Z0-9.v]+)\/([A-Za-z0-9]+)$/.exec(req.url);
  // Connect-style REST: some RPCs are called with fetch(JSON) rather than
  // gRPC-Web (e.g. AnalyticsService/BootstrapStatsig from the main process).
  // Answering those with gRPC frames makes the client's .json() throw and it
  // retries forever, starving the roster load.
  const isGrpcWeb = /grpc-web/i.test(req.headers["content-type"] || "") || req.headers["x-grpc-web"] === "1";
  if (m && !isGrpcWeb) {
    const svcName = `aiserver.v1.${m[1].split(".").pop()}`;
    const svcObj = svc[m[1]] || svc[svcName];
    const meth = svcObj && Object.values(svcObj.methods).find((k) => k.name.toLowerCase() === m[2].toLowerCase());
    const ch = [];
    req.on("data", (c) => ch.push(c));
    req.on("end", async () => {
      const isProto = /\/proto/i.test(req.headers["content-type"] || "");
      let body = {};
      try { body = isProto && meth?.I ? meth.I.fromBinary(Buffer.concat(ch)) : JSON.parse(Buffer.concat(ch).toString("utf8") || "{}"); } catch {}
      const svcShort = m[1].split(".").pop();
      const qualified = `${svcShort}.${m[2]}`.toLowerCase();
      const hk = Object.keys(handlers).find((k) => k.toLowerCase() === qualified)
        || Object.keys(handlers).find((k) => k.toLowerCase() === m[2].toLowerCase());
      if (!handlers[hk]) {
        console.log("[rest] no handler", m[1] + "/" + m[2]);
        res.writeHead(200, { "content-type": isProto ? "application/proto" : "application/json" });
        res.end(isProto ? Buffer.alloc(0) : "{}");
        return;
      }
      try {
        const out = await handlers[hk](body, res, () => {}, () => {});
        if (res.writableEnded || res.headersSent) return; // streaming handler owns the response
        if (isProto && meth?.O) { res.writeHead(200, { "content-type": "application/proto" }); return res.end(Buffer.from(new meth.O(out ?? {}).toBinary())); } // ponytail: unary proto only, add streaming frame header when Connect stream needed
        // Serialize through the real proto message when we can.
        //
        // WHY: Connect/JSON encodes ENUMS BY NAME, but a bare JSON.stringify emits
        // the raw number. GetSandBoxRunState returning {"state":3} decodes as a
        // failure, not RUNNING, so the app raised ConnectError.Internal and the
        // UI sat on "Grok Bot is having trouble connecting" while the shim log
        // showed a cheerful {"state":3} reply. Measured: the app calls this over
        // REST 3970 times, so it is the single loudest edge failure
        // (leg=run-state) alongside team-policy-read.
        //
        // proto-es toJson() gives us the exact wire shape Connect expects, with
        // enums as names and int64 as strings. Fall back to plain stringify only
        // for methods with no proto descriptor (rare).
        let json;
        try {
          if (meth && meth.O) json = JSON.stringify(new meth.O(out ?? {}).toJson());
          else throw new Error("no proto output type");
        } catch (_) {
          json = JSON.stringify(out ?? {}, (k, v) => (typeof v === "bigint" ? Number(v) : v));
        }
        console.log("[rest]", m[1] + "/" + m[2], "->", json.slice(0, 120));
        res.writeHead(200, { "content-type": "application/json" });
        res.end(json);
      } catch (e) {
        console.error("[rest]", m[2], e.message);
        res.writeHead(200, { "content-type": "application/json" });
        res.end("{}");
      }
    });
    return;
  }
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", async () => {
    const body = Buffer.concat(chunks);
    if (!m || !svc[m[1]]) {
      console.log(req.method, req.url, "-> 404");
      res.writeHead(404).end();
      return;
    }
    const svcObj = svc[m[1]];
    const method = svcObj.methods[Object.keys(svcObj.methods).find((k) => k.toLowerCase() === m[2].toLowerCase())];
    if (!method) { res.writeHead(404).end(); return; }

    let reqMsg = {};
    try {
      const frames = unframe(body);
      const data = frames.find((f) => f.flag === 0)?.data;
      if (data && data.length) reqMsg = method.I.fromBinary(data);
    } catch (e) {
      console.log("[decode-fail]", m[2], e.message);
    }
    console.log("->", m[1] + "/" + m[2], JSON.stringify(reqMsg, (k, v) => typeof v === "bigint" ? v.toString() : v instanceof Uint8Array ? `<${v.length}b>` : v).slice(0, 300));

    const isStream = method.kind === 1; // MethodKind.ServerStreaming
    res.writeHead(200, {
      "content-type": "application/grpc-web+proto",
      "grpc-encoding": "identity",
    });
    let open = isStream;
    // protobuf-es v2: toBinary is an instance method, not static
    const send = (msg) => {
      const inst = msg instanceof method.O ? msg : new method.O(msg);
      res.write(frame(0, inst.toBinary()));
    };
    const keepOpen = () => { open = true; };

    try {
      // Match "Service.Method" first (service-qualified), then bare method name.
      const svcShort = m[1].split(".").pop();
      const qualified = `${svcShort}.${m[2]}`.toLowerCase();
      const hk = Object.keys(handlers).find((k) => k.toLowerCase() === qualified)
        || Object.keys(handlers).find((k) => k.toLowerCase() === m[2].toLowerCase());
      const h = handlers[hk];
      const out = h ? await h(reqMsg, res, send, keepOpen) : undefined;
      if (!open && out !== undefined) send(out); // handlers that only return a value get auto-sent
    } catch (e) {
      console.error("[handler]", m[2], e);
    }
    if (!open) res.end(TRAILERS_OK);
  });
});
server.on("error", (e) => { console.error("server error:", e.message); process.exit(1); });
server.listen(PORT, () => console.log(`shim on http://127.0.0.1:${PORT} -> ${ROUTER} (model ${ROUTER_MODEL})`));
