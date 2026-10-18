You are continuing a reverse-engineering project on a Linux box. Work carefully and report concisely.

# CONTEXT (all verified, do not re-derive)

We reverse-engineered `grok-bot` 0.71.2, which is Cursor's "Sand" Electron desktop app. Files:

- App tree:        /root/grokbot/           (extracted .deb: binary, .so, .pak, icudtl.dat)
- Our shim:        /root/grokbot/proxy.js   (gRPC-Web + REST translator, serves 127.0.0.1:8790)
- Supervisor:      /root/grokbot/grokbot-svc.sh  (start|stop|restart|status|logs)
- Diagnostic probe: /root/grokbot/verify.js
- Protobuf registry: /root/grokbot/asarx/asarx/dist/electron-main/proto.cjs

MAIN-PROCESS CODE (this is where the answer is — read these first):
- /root/grokbot/re/zcode/main-app.cjs    (2.6 MB — the main process; auth flow,
                                          coordinator/transport wiring, Statsig call)
- /root/grokbot/re/zcode/main-core.cjs   (865 KB — shared core)
- /root/grokbot/re/zcode/main.cjs        (9 KB — entry point)

Note: the on-box asar extraction is INCOMPLETE (only proto.cjs survived). I have placed
the three main-process files you need in /root/grokbot/re/zcode/. Do not go looking for a
fuller extraction elsewhere on the box; it is not there.

## What ALREADY WORKS (proven, do not break it)

The chat protocol round-trip works end to end. `probe2.js` sends a message through the
shim and receives a real `rows` frame containing the model's reply ("SHIM-ROUNDTRIP-OK").

Key facts learned the hard way:
- The backend base URL is overridden with `SAND_BACKEND_URL` or `CURSOR_API_BASE_URL`.
- `proto.cjs` is protobuf-es v2: `toBinary()` is an instance method, `fromBinary` is static.
  Field descriptors expose `.kind`, `.T` (already the class, NOT a thunk), `.no`, `.name`.
- In `GrokBotTranscriptWatchRows`, `generation` is **uint32** (use Number). `seq` and
  `updated_seq` are uint64 (BigInt). Passing a BigInt for generation throws
  `invalid uint 32: bigint`.
- gRPC-Web framing: 5-byte header, flag 0 = message, 0x80 = trailers. Unary writes the
  message before `grpc-status:0`.
- The app retries 404s forever. NEVER 404 a known `aiserver.v1.*` service — register all
  7 services so unknown RPCs return empty responses.
- The main process calls `AnalyticsService/BootstrapStatsig` with `fetch().json()`, NOT
  gRPC-Web. The shim must serve BOTH gRPC-Web and plain REST/JSON on the same RPC paths.
  Answering a REST call with gRPC frames makes `.json()` throw and retry thousands of times.
- Handler dispatch matches `Service.Method` first, then the bare method name.
- Faked auth: `/auth/poll` returns camelCase `{accessToken,refreshToken}`; `/oauth/token`
  returns snake_case `{access_token,refresh_token,expires_in}`.
- `GetSandAccessStatus` must return `state: 1` (GRANTED); `GetSandTrialClaimStatus` 3
  (GRANTED); `GetUserPrivacyMode` 2 (NO_TRAINING). `GetMe` must return a real user or the
  UI dies at "having trouble connecting".

## THE BLOCKER TO SOLVE (this is your job)

The UI chat input is disabled behind "Connecting to your computer…".

Confirmed via Chrome DevTools Protocol (CDP, app runs with `--remote-debugging-port=9222`):

    connection.transport: "down"
    teamPolicy.status:    "failed"     (retries getTeamMemberPolicy, reason
                                        "desktop-source/transport-failure")

The gate is the `sand-node-agent-coordinator`: an Electron `utilityProcess.fork` plus a
MessagePort stream to a REAL sandbox box. It is not HTTP, which is why the shim cannot
satisfy it. The real SandBox/box protocol (ensureSandBox, exec streaming, tool-call
transcript frames) is unimplemented.

# YOUR TASK

Investigate how to make that transport report healthy. Concretely:

1. Find where `sand-node-agent-coordinator` is forked and what it expects. Grep
   main-app.cjs and the rest of asarx for: `utilityProcess`, `sand-node-agent`,
   `agent-coordinator`, `MessagePort`, `postMessage`, `transport`, `ensureSandBox`,
   `teamMemberPolicy`, `getTeamMemberPolicy`.
2. Determine whether the transport can be satisfied locally — i.e. whether we can
   implement the coordinator's expected message protocol ourselves and serve it from the
   shim — or whether it hard-requires a remote Cursor sandbox we cannot emulate.
3. Look for any env var, config flag, or dev/offline mode that bypasses the sandbox
   requirement entirely (grep for `process.env`, `SAND_`, `OFFLINE`, `DEV_`, `LOCAL_`,
   `bypass`, `mock`, `stub`). This would be the cheapest possible win.
4. If a local implementation is plausible, write a minimal prototype and report exactly
   which messages the coordinator sends and expects back.

# RULES

- READ-ONLY on existing files. Do not modify proxy.js, grokbot-svc.sh, or anything under
  asarx/. If you write a prototype, put it in /root/grokbot/re/zcode/.
- Do not restart or stop the app or shim. They are currently not running anyway.
- Do not touch anything related to z.ai, 9router config, or the bounty pipeline.
- Do not make network requests to third-party hosts.
- If you conclude it requires a real Cursor sandbox, say so plainly and explain the
  evidence. A clear "this is not emulatable, here is why" is a SUCCESSFUL result — do not
  invent a workaround that merely hides the failure.

# OUTPUT

Report back, under 60 lines:

1. What you inspected (file:line references for the important findings).
2. Whether the transport can be satisfied locally: YES / NO / PARTIAL, with evidence.
3. If YES/PARTIAL: the exact message protocol (message names, shapes, direction), and
   what you prototyped.
4. If NO: the specific hard dependency that blocks it.
5. Any env var / flag / dev mode that bypasses the sandbox requirement.
6. The single highest-value next step.
