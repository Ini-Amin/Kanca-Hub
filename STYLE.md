# STYLE.md — how to write code and prose in this repo

Companion to `AGENTS.md`. `AGENTS.md` says *what* the system does and *why* it is
built that way; this file says *how to write* the things you add to it.

## Language

- **English only, in every file.** Code, comments, commit messages, docs, log
  lines, Telegram output. No mixed-language comments, no transliterated words.
- Conversation with the user may be Indonesian or English, but nothing that
  lands in the repo or on disk should be Indonesian. A future reader greps this
  tree; mixed-language comments make that harder, not friendlier.

## Comments explain WHY, never WHAT

The single most valuable rule here. A comment that restates the code is noise;
a comment that records a decision, a failed assumption, or a trap is worth more
than the code it sits on.

Bad — restates the code:

```python
# loop over accounts
for e in ordered_accounts():
```

Good — records the decision and the trap:

```python
# accounts.txt order IS the priority order: pick_account() walks it and returns
# the first account whose quota is not spent. Reordering this file silently
# changes which account absorbs load, which is why we never sort it.
for e in ordered_accounts():
```

When you fix a bug, write the comment for the *next* person who will be tempted
to undo the fix. This repo is full of those: the `ExitOnForwardFailure` note, the
"20128 on both sides" note, the gate-0 note. Follow that pattern.

### Record what you disproved, not just what you built

Several of the most useful comments in this tree exist because a confident
conclusion turned out to be wrong:

- "grok-bot's coordinator is missing" → it was inside `app.asar` all along.
- "grok-bot needs a Cursor-signed principal" → `parseJwtPayload` never checks a
  signature; `accountScope = sha256(sub)`.
- "HackerOne is dead, token is 401" → the REST API is 401, but
  `POST https://hackerone.com/graphql` is unauthenticated for public programs.

A comment saying *"we thought X, it was actually Y, here is the evidence"* stops
the next person from re-running the same dead end. Prefer it over a clean,
confident, wrong story.

## Python

- Target the system `python3` (3.13 on this box). No `pip install --break-system-packages`
  — use a venv.
- Stdlib first. `urllib.request` over `requests` in scripts that run unattended,
  so a missing package cannot take down a timer.
- Every network call gets an explicit timeout. A script on a timer must never
  hang forever; that is what wedged the systemd queue once already.
- Functions that shell out return `(ok, output)` or raise — never print and
  return `None` on failure, because callers cannot distinguish that from "empty".
- Keep the CLI shape `python3 scripts/<name>.py <subcommand> [flags]`. Every
  script in `scripts/` follows it.

## Shell

- `set -u` at minimum; `set -euo pipefail` when the script is short.
- Quote every expansion: `"$VAR"`, `"${arr[@]}"`.
- Never `pkill -f` a pattern that could match your own SSH command line on a
  remote box. It has killed live scans here. Kill by exact PID from
  `ss -tlnp`.
- Prefer `timeout N cmd` around anything that talks to the network.

## systemd units

Three rules learned the hard way on 2026-10-18, when a crash-looping unit wedged
the user manager at 94% CPU and made every app launch hang:

1. **`Restart=always` without a rate limit is a bug.** Pair it with
   `StartLimitIntervalSec=` and `StartLimitBurst=`, or use `Restart=on-failure`.
   `StartLimitIntervalSec=0` *disables* the limiter — it does not mean "no limit
   needed".
2. **Never order a user unit `After=` a system target.** `network-online.target`
   and `tailscaled.service` do not exist in the user manager; they resolve to
   `not-found`.
3. **A `Type=oneshot` service holds a start job for its whole runtime.** Give it
   a `TimeoutStopSec=` and `SendSIGKILL=yes`, or a stop that cannot finish keeps
   the job queue occupied.

## Docs

- Tables for anything enumerable (sources, timers, endpoints, models).
- Put the *reason* in the same paragraph as the fact. "24 programs, keyless" is
  weaker than "24 programs, keyless — this is why Bugcrowd replaced Freelancer".
- Mark dead ends explicitly with ~~strikethrough~~ and the date, plus what
  replaced them. Do not silently delete history; the next person needs to know
  it was tried.
- Every claim about a remote host should be reproducible from the command
  shown next to it.

## Commits

- Imperative subject under ~70 chars: `fix: rate-limit zcode-egress-tunnel`.
- Body explains the symptom the user saw, then the cause, then the fix.
- Never commit `artifacts/`, `grokbot-shim/artifacts/`, or logs — they are
  gitignored for size, not by accident.
