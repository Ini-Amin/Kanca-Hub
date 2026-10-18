# SKILLS.md — the reusable techniques

Companion to `AGENTS.md`, `STYLE.md`, `WORKFLOWS.md`. Workflows are procedures
for *this* repo; skills are the transferable techniques underneath them — the
things worth knowing before you start the next reverse-engineering job, the next
provider integration, or the next "why is this host broken" session.

Each skill lists what it is for, the technique, and the failure mode it prevents.

---

## S1. Reading a minified Electron bundle

**Use when** you need to know what a packaged app actually does.

The trap: minified identifiers (`Ag`, `Bn`, `cU`) are meaningless, and
`grep "Ag ="` finds nothing because they are function declarations or
chunk-local bindings.

**Technique — find the export map first.** Bundles expose a map like:

```js
{ get accountScopeOfPrincipal(){ return Ag },
  get parseJwtPayload(){ return Vi } }
```

That single line tells you which short name implements which public name. Grep
for the *public* name, read the `return` target, then grep for that short name
in declaration form: `function Ag(`, `Ag=(`, `[,;{} ]Ag=`.

**Watch out:** `Object.keys()` misses getter-only exports — use
`Object.getOwnPropertyNames()`.

**Technique — when names fail, search behaviour.** If the binding cannot be
found, search for what the function must contain: `split(".")`, `base64url`,
`createHash("sha256")`, `"iat"`. The implementation is often more findable by
its primitives than by its name.

**Failure mode prevented:** concluding "the code is not in this file" and giving
up. It was there; the grep was wrong.

---

## S2. Testing a security assumption before believing it

**Use when** a system seems to require a credential you do not have.

The pattern that cost days here: HackerOne "required a token" because
`api.hackerone.com/v1/me` returned 401 in four auth shapes. Every shape failed,
so the conclusion felt solid. It was wrong — the *REST API* needs a token; the
*GraphQL endpoint* does not.

**Technique:**

1. Enumerate the *interfaces*, not just the one you found. Same product often
   exposes REST, GraphQL, and a JSON feed with different auth.
2. Probe for the cheapest possible response — `{"query":"{__typename}"}` — before
   building anything.
3. Distinguish **read** from **write** auth. Reading public data is frequently
   unauthenticated; submitting is not.

```bash
curl -s -X POST https://hackerone.com/graphql \
  -H "Content-Type: application/json" -d '{"query":"{__typename}"}'
# {"data":{"__typename":"Query"}}   <- alive, no token
```

**Failure mode prevented:** a whole platform written off as dead because one
endpoint said 401.

---

## S3. Distinguishing "quota spent" from "credential invalid"

**Use when** an API call fails and you need to know whether to rotate a key or
wait.

Three different failures that all look like "it does not work":

| Response | Meaning | Action |
|---|---|---|
| `Invalid API key` | key not recognised | fix the key lookup |
| `code:1005 exceed quota limit` | key fine, bucket empty | wait for reset / use another account |
| `<urlopen error>` | upstream unreachable | check egress/tunnel |

**Technique:** read the error *code*, not the status. Then cross-check against a
server-side cache rather than probing each key — probing N keys through a rate
limited bridge produces mostly noise (14 of 16 probes returned `OTHER` here,
purely from self-inflicted load).

**Failure mode prevented:** rotating 128 healthy keys because one bucket was
empty, or waiting hours on a key that was simply mistyped.

---

## S4. Reading a fallback chain to explain "impossible" data

**Use when** the numbers contradict intuition — e.g. "the newest accounts are
exhausted but the oldest are full".

**Technique:** find the selection function and read its *order*.

```python
def pick_account(preferred, model, exclude=()):
    order = ([preferred] if preferred is not None else []) + ordered_accounts()
    for e in order: ...
```

`ordered_accounts()` returns `accounts.txt` **in file order**. So the first
entries absorb every request until their quota is gone, then it falls through.
Newest accounts were appended to the top of the file, so they burned out first.
No paradox — just an unread ordering.

**Generalisation:** when behaviour looks backwards, look for an implicit sort.
Priority order in a file, list iteration order, dict insertion order — one of
them is the explanation.

---

## S5. Gate before you scan

**Use when** an automated system takes an irreversible action (scanning a third
party, sending a report, deleting state).

**Technique:** re-derive the permission from the source of truth at the moment of
action, and refuse when it disagrees with the stored plan.

`dispatch` re-parses the brief and refuses if `job.json` is missing exclusions
the page now lists. That gate caught a real violation: a job created with 7
exclusions while the brief listed 19, where `sandbox.bitso.com` and
`quetzal.bitso.com` had actually been scanned.

**Design rules:**

- **Fail closed.** No answer means no action.
- **Gate on discovered values, not seeds.** Filtering only the initial host list
  lets subfinder find a new host and walk straight past the check.
- Provide an explicit override (`--allow-stale-scope`) so a legitimate stale case
  is not a dead end — but make it loud.

**Failure mode prevented:** scanning out-of-scope infrastructure, which is the
one mistake in this domain that is not recoverable by retrying.

---

## S6. Bounded execution for anything unattended

**Use when** writing a script that runs on a timer or a remote host.

Every layer needs its own deadline, because the outer one cannot reach through:

```bash
timeout $BOUNTY_NUCLEI_SECS nuclei ...    # remote bound (works through ssh)
subprocess.run(..., timeout=1800)          # local bound (cannot)
```

`subprocess.run(timeout=)` blocks on `ssh`, so it never fires while `ssh` waits
on `nuclei`. The deadline has to live on the far side of the ssh hop.

**Also:** give each job its own working directory. A hardcoded `/tmp/bj` means
two concurrent jobs silently overwrite each other's output — which looks like
corrupted results, not like a collision.

---

## S7. Verify the fix, not the theory

**Use when** you think you have found the cause.

The theory that `blu-tunnel` was wedging systemd was plausible and wrong. The
actual looper was `zcode-egress-tunnel` with **1,524** restarts. What settled it
was counting restarts per unit rather than reasoning about the one unit that
looked suspicious:

```bash
for f in ~/.config/systemd/user/*.service; do
  n=$(systemctl --user show "$(basename $f .service)" -p NRestarts --value)
  [ "${n:-0}" -gt 0 ] && echo "$n $(basename $f)"
done | sort -rn | head
```

**Technique:** when several candidates could explain a symptom, measure all of
them cheaply instead of investigating one deeply. A counter beats a hypothesis.

**And:** after the fix, re-measure the *user-visible* symptom (start latency,
transport state), not just the internal counter. A cleared queue with systemd
still at 94% means the diagnosis was incomplete — which is exactly what happened,
and why the reboot was needed.

---

## S8. Recognising a circular dependency

**Use when** a system is stuck "waiting for itself".

grok-bot: the UI gate reads `harness` per agent → the roster is empty → the
roster arrives over the transport → the transport is gated by that same check.

**Technique:** draw the dependency as arrows and look for a cycle. Then find the
one edge that can be broken from outside — here, the principal, which the
transport factory needs *before* the roster can ever arrive.

**Failure mode prevented:** repeatedly restarting the app and re-reading logs,
hoping the cycle resolves itself. A cycle never resolves itself; you have to cut
an edge.
