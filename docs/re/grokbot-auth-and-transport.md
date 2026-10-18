# Grok Bot on blu — what runs, what does not, and why

Status 2026-10-18, after the session that got the real Sand coordinator to
report `transport-connected`. Read this before touching anything under
`/root/grokbot`.

## What works now

| Piece | State | How to check |
|---|---|---|
| Sign-in | **working** | `python3 -c "import json;print(json.load(open('/root/.config/Grok Bot/desktop-status.json'))['signedIn'])"` → `True` |
| Saved account | **working** | `savedAccounts.value.accounts[0].id == sha256(sub)`; store shows `97033c83…` |
| OpenAI-compatible endpoint | **working** | `POST http://<blu>:8790/v1/chat/completions` |
| 4 worker models | **working** | `cbai/deepseek-v4.1-flash`, `Zcode/GLM-5.3`, `THK/deepseek-v4.1-flash:free`, all answer |
| noVNC (phone/desktop access) | **working** | `http://100.120.116.62:6080/vnc.html` → 200, ws upgrade → 101 |
| Sand gateway (`gateway.js`) | **working** | `grokbot-gateway.service` active, serves `/health`, `/events`, `/api/*` |
| Real coordinator over control channel | **working, standalone** | `coordinator-host.js` reaches `transport-connected` generation 1 |
| 9router tunnel | **working** | blu:20128 → desktop, `/v1/models` 200 |
| keepalive | **working** | `blu-keepalive.service` active, 0 restarts |
| **Sidebar roster in the UI** | **NOT working** | `roster.loadState == "failed"`, `agentsById == {}` |

## The one thing that does not work, precisely

The renderer's roster slice says:

```json
{"loadState":"failed",
 "failure":{"code":"source/transport-failure","boundary":"host",
            "retry":{"owner":"client","command":"getServerAgentRoster"},
            "transportKind":"http_5xx"},
 "failureDetail":"gateway-unreachable: server getServerAgentRoster unreachable (http_5xx): unsupported content type application/json"}
```

This is the app's **own** coordinator talking to **its own** view of the
gateway. It is not our gateway being wrong — we proved our gateway answers
correctly by driving the real coordinator ourselves:

```
coordinator-host.js  ->  transport-connected {generation: 1}
gateway.js           ->  H1 GET /events, sent roster seed
                     ->  H1 POST /api/listAgents -> {"agents":[...],"isNewestRoster":true}
```

So the protocol works. What is missing is the **IPC wiring**: the app forks its
coordinator with `utilityProcess.fork` and hands it three `MessageChannel`s
(`post`, `executors`, `events`) plus a renderer data port, via
`t.postMessage({bootstrap:{processConfig}}, [n.port1, r.port1, o.port1])`.
Our standalone host can answer the coordinator's control requests, but it has
no path into the renderer, so the roster it obtains never reaches the UI.

**The app never forks the coordinator in this deployment.** Verified: no child
with `--bootstrap`, no process holding `node-agent-coordinator/main.cjs`, and
zero connections from the app to the gateway port. `I8e(...)` builds the
factory `ee => {...}` and `CJe({fork: …})` is only reached from that factory,
which is only invoked with a principal once the account slot settles as
`active` — and `accountSlot` is `grokbot-local-principal` (a plain string), not
the `sha256` scope the state machine expects.

## The auth chain, in the order it bit us

Each layer looked like "the patch did nothing". All five had to be right.

### 1. `GetMe.authId` must equal the JWT `sub`

```
cn(e)    = e.authId ?? e.email
wg(e)    = cn(e) == null ? "logged-out" : sha256(cn(e))
signedIn = wg(state) != "logged-out"
kind:"logged-in" comes from t.sub
```

A hardcoded `authId` that disagrees with `sub` makes every principal-scoped
lookup miss silently. The shim parses the bearer token and echoes `sub`.

### 2. `cursor-accounts` must be rekeyed to `sha256(sub)`

```
accounts = { "<scope>": { cursor-access-token, … } }
active   = "<scope>"
```

Lookup is `accounts[accountScopeOfToken(token)]`. Leaving the old key misses
even though the token decodes fine.

### 3. A bare JWT is treated as safeStorage ciphertext

`Mu(e)` checks `startsWith(Ka)` (`Ka = "plaintext:v1:"`). Anything else goes to
`safeStorage.decryptString`, fails, and silently keeps the old value.

### 4. `plaintext:v1:` is *rejected*, not accepted

```js
if (!Wr()) return null;
return c == null || c.startsWith(Ka) ? null : fR(c);
```

`Wr()` is `safeStorage.isEncryptionAvailable()` and it is **TRUE** on blu — the
app itself writes `v10` blobs. So the plaintext branch is the one encoding the
app refuses.

### 5. blu's safeStorage is Chromium OSCrypt `basic_text`

```
prefix : "v10"
key    : PBKDF2-HMAC-SHA1("peanuts", "saltysalt", 1 iteration, 16 bytes)
iv     : 16 bytes of 0x20
cipher : AES-128-CBC, PKCS#7
```

Proven by decrypting the app's own token (`probe-oscrypt.js`), so we can mint
compatible ciphertext from plain node.

**Order is non-negotiable: `stop -> patch -> start`.** The app rewrites
`sand-secrets.json` on shutdown AND startup; `restart` stops first and flushes
the old values over your write.

## The roster gate that looks like a transport problem

`ListGrokBotAgents` was called **zero** times for a long time. The cause was not
the transport:

```
Eon()  -> (await getGrokBotRuntimeCapabilities()).capabilities
bon(c) -> c.temporalCreationEnabled && c.durableIdentityEnabled &&
          c.durableIdentityWritesEnabled
PV().isServerRosterEnabled -> capabilities.durableIdentityEnabled ?? …
```

`capabilities` is a **message**, not a repeated field. Returning
`capabilities: []` leaves all three flags undefined, `isServerRosterEnabled`
becomes false, and the roster fetch is skipped entirely — silently, with the UI
showing "Couldn't load Bots" rather than an error.

Correct shape:

```json
{"capabilities":{"temporalCreationEnabled":true,
                 "durableIdentityEnabled":true,
                 "durableIdentityWritesEnabled":true},
 "maxConcurrentTurns":4,"supportsStreaming":true}
```

## The gateway protocol, as actually implemented

Constants from the bundle:

```
paths   : /api, /events, /events/echo, /health
auth    : Authorization: Bearer <token>
headers : x-sand-slim-avatars, x-sand-mint-dedupe, traceparent
errors  : gateway/unknown-method
```

Method call shape:

```
POST ${baseUrl}/api/${method}
content-type: application/json
```

Content-type matters. `yD()` maps:

```
text/event-stream          -> {stream:true,  binary:true}
application/connect+json   -> {stream:true,  binary:false}
application/json           -> {stream:false, binary:false}
application/proto          -> {stream:false, binary:true}
```

A mismatch throws `unsupported content type <ct>` — that string is what the
renderer surfaced. Unary calls need `application/json`; streams need
`text/event-stream`.

Health probe (`Lw`) is `fetch(`${baseUrl}/health`)` and requires `{ok:true}`.

### The connection object

`resolveGatewayConnection` must return a **connection**, not `{url, token}`:

```json
{"baseUrl":"http://127.0.0.1:8791",
 "headers":{"authorization":"Bearer <token>","x-sand-network-token":"<token>"}}
```

Evidence: `ensureConnection()` reads `o.baseUrl` / `o.headers`; `Lw()` does
`fetch(`${baseUrl}/health`, {headers})`; `In(baseUrl)` classifies the hostname
as `loopback | pod_proxy`. Returning `{url,…}` leaves `baseUrl` undefined, so
`fetch(undefined + "/health")` throws `TypeError/ERR_INVALID_URL` and
`reportGatewayReachability` loops with `baseUrlKind:"unknown"`.

### The control channel frame shapes

Replies must be `kind:"reply"` with an `outcome`, not `kind:"response"` with a
`result`:

```json
{"channel":"coordinator-control",
 "frame":{"kind":"reply","requestId":"c-1",
          "outcome":{"status":"ok","value":{…}}}}
```

Getting this wrong produces, in order:

```
control protocol breach: frame.kind must be lifecycle, request, cancel, reply, or event
reply.outcome is not a valid outcome
```

and the coordinator exits 1 within milliseconds. Validator is `XK()`; consumer
is `R.resolve(h.value)`.

### Both protocols on one port

The coordinator uses `fetch` (HTTP/1.1, undici) for `/health` and the streaming
client for `/events`. An h2c-only server accepts the socket and then fails the
exchange. `gateway.js` therefore runs an `http.Server` and upgrades to HTTP/2 on
the h2c preface, so one `baseUrl` serves both.

## systemd cannot run the coordinator — use a login shell

The exact same environment values succeed from an interactive shell and fail
under systemd with:

```
node-agent-coordinator: composition failure: TypeError
```

Tried and did **not** help: `ProtectSystem=no`, `ProtectHome=no`,
`PrivateTmp=no`, `NoNewPrivileges=no`, `MemoryDenyWriteExecute=no`, explicit
`HOME`/`USER`/`LOGNAME`/`PATH`, `WorkingDirectory`, and `env -i` with the same
values (which *works* from a shell).

What works: run it through `bash -lc`.

```ini
ExecStart=/bin/bash -lc "/root/grokbot/run-coord-svc.sh"
```

The unit is installed but **disabled** — it proves the protocol, it cannot feed
the app.

## Phone / remote access

`novnc.service` runs
`websockify --web=/usr/share/novnc 6080 127.0.0.1:5900`, proxying the `x11vnc`
display the app renders into.

```
http://100.120.116.62:6080/vnc.html
http://100.120.116.62:6080/vnc_lite.html
```

`vnc_lite.html` is better on a phone — no toolbar or clipboard chrome.

The VNC server has **no password** (`-nopw`) and binds `0.0.0.0`. That is only
acceptable because the tailnet is the boundary. Do not expose 5900 or 6080
beyond it.

## Using grok-bot as a model endpoint ("agent blu")

The shim exposes an OpenAI-compatible surface, so any agent can use blu as a
provider without the Electron UI:

```
POST http://100.120.116.62:8790/v1/chat/completions
{"model":"Zcode/GLM-5.3","messages":[{"role":"user","content":"…"}]}
```

`GET /v1/models` lists the default. Router model ids carry their own provider
prefix and must be preserved — the old blanket `^[a-z]+/` strip turned
`cbai/deepseek-v4.1-flash` into `deepseek-v4.1-flash`, which the router does not
know. Only the `gb/` namespace is stripped, because that one is ours.

`callRouter` also has to tolerate several complete JSON objects concatenated
with no `data:` prefix and no newline framing — Zcode/GLM does this, and a plain
`JSON.parse` dies with "Unexpected non-whitespace character after JSON".

## Traps worth remembering

- `pkill -f "Grok Bot/grok-bot"` over SSH matches the remote `bash -c` line and
  kills your own session. Kill by exact PID.
- `require("electron")` from plain node throws `MODULE_NOT_FOUND`, so a probe
  that calls `safeStorage.isEncryptionAvailable()` that way reports `false` for
  the wrong reason. That false negative sent us chasing the keyring.
- The shim needs `GROKBOT_PROTO` or a correct candidate path. A wrong path makes
  it die at `require()` and the launcher prints only `SHIM FAILED`, which reads
  like a port conflict.
- `StartLimitIntervalSec` belongs in `[Unit]`, not `[Service]`. In `[Service]`
  systemd logs `Unknown key name` and ignores it, so a failing unit respawns
  forever.
- System services have no `HOME`; scripts that read it need
  `Environment=HOME=/root`.
- `Environment=SAND_USER_DATA_DIR=/root/.config/Grok Bot` is invalid because of
  the space. Quote the whole assignment:
  `Environment="SAND_USER_DATA_DIR=/root/.config/Grok Bot"`.
- The renderer store is reachable only by walking React fibers. `window.__store`
  does not exist; `cdp-slices.js` installs it as `window.__gbStore`.
- A CDP websocket helper must pass the *parsed* message to listeners. The first
  version did `listeners.forEach(f => JSON.parse(...))` and never fired.
- `SAND_BACKEND_URL`, `CURSOR_API_BASE_URL` and `CURSOR_ORIGIN_BACKEND_URL` are
  three separate knobs. Missing the third leaves the origin backend pointing at
  `api.origin.cursor.com` (Cloudflare), which is why the app kept opening
  sockets to `104.18.x.x:443` even with the other two overridden.

## Files

| Path | Role |
|---|---|
| `/root/grokbot/9router-shim/proxy.js` | gRPC-Web + REST + OpenAI-compatible shim |
| `/root/grokbot/gateway.js` | Sand gateway stand-in, HTTP/1.1 + h2c, `/api` + `/events` |
| `/root/grokbot/coordinator-host.js` | forks the real coordinator, answers `coordinator-control` |
| `/root/grokbot/run-coord-svc.sh` | login-shell wrapper for the coordinator unit |
| `/root/grokbot/patch-oscrypt.js` | mints OSCrypt `v10` ciphertext + rekeys the account |
| `/root/grokbot/probe-oscrypt.js` | proves the OSCrypt backend by decrypting the app's own token |
| `/root/grokbot/probe-credentials.js` | replicates `Mu`/`SR` and reports what the app would read |
| `/root/grokbot/cdp-slices.js` | dumps renderer store slices (`roster`, `mainAgent`, …) |
| `/root/grokbot/cdp-store.js` | dumps the whole store via the fiber walk |
| `/root/grokbot/cdp-ui.js` | dumps visible text + console errors |
| `/root/grokbot/grokbot-svc.sh` | launcher: shim + Xvfb + app + x11vnc |
