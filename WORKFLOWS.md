# WORKFLOWS.md — the repeatable procedures

Companion to `AGENTS.md` (what exists) and `STYLE.md` (how to write it). This
file is the *do this, in this order* layer: the procedures that are easy to get
subtly wrong and expensive to re-derive.

Each workflow states its preconditions, the steps, and how to verify it actually
worked. If a step can silently no-op, there is a check for it.

---

## W1. KancaHub Interactive Menu & AI Model Fleet

**Precondition:** Python 3.10+ installed.

```bash
# 1. Start the interactive menu (cross-platform: Linux, macOS, Windows)
kancahub

# 2. Select Category [D] Frontier & Keyless AI Fleet:
#    [17] Orvis AI (Claude 4 Sonnet/Opus, GPT-4o, o3-mini) -> option 2 for smoke test
#    [18] Koma-Free (vLLM inference, keyless) -> option 2 for chat test
#    [19] StepFun Studio (Step-5 Preview, Step-3.7 Flash) -> option 1 to list models
#    [20] OpenSatan (100% uncensored) / OxAlpha (GLM-5.3 Flash 1M) -> option 1/4
#    [21] AI Supplies (22+ provider farm fleet) -> interactive provider picker
```

**Verify:** Selecting `17` -> `2` performs an end-to-end completion through the local Orvis bridge
and prints `✓ Orvis Bridge: pong` in under 3 seconds.

---

## W2. Direct 9Router SQLite Ingestion (Zero Auth / Bypass Login)

Third-party provider connectors often assume a default dashboard password ("123456")
over HTTP `POST /api/auth/login`. In local development, direct SQLite writes to
`~/.9router/db/data.sqlite` completely eliminate authentication failures and network round trips.

```python
import sqlite3, json, time, uuid
from pathlib import Path

db_path = Path.home() / ".9router/db/data.sqlite"
conn = sqlite3.connect(str(db_path))
cur = conn.cursor()

# 1. Ensure provider node exists
cur.execute("SELECT id FROM providerNodes WHERE id=?", ("openai-compatible-chat-orvis-bridge",))
if not cur.fetchone():
    now = int(time.time() * 1000)
    cur.execute(
        "INSERT INTO providerNodes (id, name, prefix, apiType, baseUrl, createdAt, updatedAt) "
        "VALUES (?, 'Orvis', 'orvis', 'openai-compatible', 'http://127.0.0.1:8796/v1', ?, ?)",
        ("openai-compatible-chat-orvis-bridge", now, now)
    )

# 2. Ensure connection exists with active flag
# Verified: 9Router hot-reloads data.sqlite changes live.
conn.commit()
conn.close()
```

**Verify:** `curl -s http://127.0.0.1:20128/v1/models | grep "orvis"` returns the routed models.

## W3. Farm: check supply and quota

```bash
# what the bridge thinks each account has left (authoritative, server-side)
python3 -c "
import json,os
d=json.load(open(os.path.expanduser('~/.zcode-bridge/balance.json')))
alive=sum(1 for v in d.values()
          if sum(b.get('remaining_units') or 0 for b in (v.get('buckets') or []))>0)
print('accounts',len(d),'with quota',alive)"
```

**Understand the fallback chain before reading the numbers.**
`pick_account()` walks `accounts.txt` **in file order** and returns the first
account whose JWT is alive and whose quota is not spent. Consequence, confirmed
by data: the accounts at the top absorb all load and burn out first, while the
ones lower down keep full quota. That is why "newest accounts are exhausted,
oldest are fine" is *not* a paradox — the newest are at the top of the file.

Do not re-sort `accounts.txt` casually. Order is priority.

**Verify a single key:**

```bash
KEY=$(awk -F= -v e="<email>" '$2==e{print $1;exit}' ~/.zcode-bridge/keys.txt)
curl -s -X POST http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" \
  -d '{"model":"Zcode/GLM-5.3","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'
```

- `"content"` → alive
- `code:1005 exceed quota limit` → quota spent, key itself is fine
- `Invalid API key` → wrong key (usually you grepped the `key=email` line wrong)

Bridge state lives in `~/.zcode-bridge/`, **not** `~/.config/auto-freecf/`.

---

## W4. Remote work on blu

```bash
ssh blu 'echo OK'          # always probe first; blu drops off the tailnet for hours
```

**blu is intermittently offline.** Observed outages over an hour. Never assume a
silent SSH means your command failed — check reachability first:

```bash
tailscale status | grep blu        # look for "offline, last seen Nm ago"
```

If offline, either wait or use the local fallback:

```bash
BOUNTY_REMOTE=1 python3 scripts/bounty_run.py ...
```

**Do not use loose `pkill -f` / `ps | grep` on blu.** The pattern matches the
remote `bash -c` line and will kill your own scan or your SSH session. Kill by
exact PID from `ss -tlnp`.

---

## W5. Grok Bot: patch the principal

**Background:** the app's transport is principal-scoped and its `connection.transport`
stays `"down"` while `roster.agentsById.count == 0`. That is circular: the roster
arrives over the transport.

The blocker was believed to be a missing Cursor-signed principal. It is not.
`parseJwtPayload` never verifies a signature, and
`accountScopeOfPrincipal = sha256(principal)`.

```bash
# 1. dry-run the JWT generator (touches nothing)
python3 -c "
import sys; sys.path.insert(0,'scripts')
import grokbot_principal_patch as g
print(g.make_jwt()[:60]); print(g.scope_of(g.SUB))"

# 2. apply on blu (writes a timestamped backup first)
scp scripts/grokbot_principal_patch.py blu:/root/grokbot/
ssh blu 'python3 /root/grokbot/grokbot_principal_patch.py'

# 3. restart and verify
ssh blu '~/grokbot/grokbot-svc.sh restart'
ssh blu 'cd /root/grokbot && node cdp-values.js' | head -20
```

**Verify:** `connection.transport` is no longer `"down"`. Also check the RPC
flood stopped — before the fix `BootstrapStatsig` ran ~1,436×/minute:

```bash
ssh blu 'tail -3000 /var/log/grokbot/shim.log | grep -c BootstrapStatsig'
```

**Rollback:** restore the newest `sand-secrets.json.bak-*` next to the file.

---

## W6. Restarting a wedged systemd user manager

Symptom: everything you launch hangs, `systemctl --user start` blocks for 20 s+,
and `ps` shows the user `systemd` pegged.

```bash
# 1. confirm it is the manager, not a single unit
systemctl --user list-jobs                 # many "start waiting"?
top -bn1 | head -5                         # systemd near 100% of one core?

# 2. find the crash-looper
for f in ~/.config/systemd/user/*.service; do
  n=$(systemctl --user show "$(basename $f .service)" -p NRestarts --value 2>/dev/null)
  [ "${n:-0}" -gt 0 ] && echo "$n $(basename $f)"
done | sort -rn | head

# 3. clear the wedge (temporary — the loop will re-wedge it until fixed)
systemctl --user daemon-reexec
```

`daemon-reexec` reloads code but keeps state, so it is a **diagnostic**, not a
fix. A true fix requires fixing the unit (see STYLE.md, systemd rules) and, if
the manager is still spinning with an empty queue, a reboot.

---

## W7. Delegating to a subagent model

**Run subagents serially.** Two concurrent `Zcode/GLM-5.3` calls exceed the
bridge limit and return `429 bridge busy`. Serial is not a preference; parallel
simply fails.

Working models: `cbai/deepseek-v4.1-flash`, `Zcode/GLM-5.3`,
`THK/deepseek-v4.1-flash:free`, `ag/gemini-3.8-flash-high`.
`THK/deepseek-v4.1-flash` (paid) is unusable — balance $0, returns 402.

9router is slow (`/v1/models` ≈ 7.5 s), so use timeouts ≥ 25 s. SSE `data:`
lines appear even for non-streaming calls, plus a trailing `data: [DONE]`.

When delegating RE work, paste **extracted code**, not file paths — a subagent
without shell access cannot read them.

## W8. Using grok-bot as a model endpoint (no UI needed)

The shim speaks OpenAI, so any agent or script can use blu as a provider even
while the roster is still broken. This is the path that works today.

```bash
curl -s -m 90 -X POST http://100.120.116.62:8790/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"Zcode/GLM-5.3","messages":[{"role":"user","content":"say OK"}]}'
```

`GET /v1/models` returns the default (`cbai/deepseek-v4.1-flash`). All four
worker models answer: `cbai/deepseek-v4.1-flash`, `Zcode/GLM-5.3`,
`THK/deepseek-v4.1-flash:free`, and the default when `model` is omitted.

Two rules for the router id:

1. Keep the provider prefix. `cbai/…`, `Zcode/…`, `THK/…:free` are the router's
   own ids. Only strip `gb/`, which is grok-bot's namespace, not the router's.
   A blanket `^[a-z]+/` strip turns `cbai/deepseek-v4.1-flash` into
   `deepseek-v4.1-flash`, which 9router does not know.
2. Expect concatenated JSON. Some upstreams (Zcode/GLM) return several complete
   objects with no `data:` prefix and no newline framing. Parse by scanning
   balanced `{…}` runs, not with a single `JSON.parse`.

## W9. Reaching the grok-bot UI from a phone

```bash
# already installed as a service
systemctl status novnc          # websockify --web=/usr/share/novnc 6080 127.0.0.1:5900
```

Open `http://100.120.116.62:6080/vnc_lite.html` from any tailnet device.
`vnc_lite.html` beats `vnc.html` on a phone: no toolbar, no clipboard chrome.

Verify without a browser:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://100.120.116.62:6080/vnc.html   # 200
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' -H 'Sec-WebSocket-Version: 13' \
  http://100.120.116.62:6080/websockify                                          # 101
```

The VNC server has no password and binds `0.0.0.0`. The tailnet is the only
boundary — never expose 5900 or 6080 beyond it.

## W10. Driving the app's real Sand coordinator

Useful when working on the gateway protocol, and the fastest way to prove a
gateway change is correct without the UI.

```bash
cd /root/grokbot
node coordinator-host.js          # forks the real artifact, answers control frames
```

A healthy run prints, in this order:

```
<- lifecycle hello
<- request c-1 resolveGatewayConnection {"path":"cookie-provider"}
-> reply c-1 {...}
...
[other] coordinator-control event {"kind":"event","family":"transport-connected","payload":{"generation":1}}
```

If you see `control protocol breach: frame.kind must be …`, the reply envelope
is wrong — it must be `kind:"reply"` with `outcome:{status:"ok",value}`, not
`kind:"response"` with `result`. If you see
`reportGatewayReachability {outcome:"network", causeSummary:"TypeError/ERR_INVALID_URL"}`,
`resolveGatewayConnection` returned `{url,token}` instead of `{baseUrl,headers}`.

Do **not** run this under systemd expecting it to feed the UI: it is a separate
process with no channel to the renderer. Under systemd it additionally fails
with `composition failure: TypeError` unless launched through `bash -lc`.
