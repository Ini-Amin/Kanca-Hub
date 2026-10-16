# AGENT_MEMORY.md — shared agent memory (RAG). AGENTS ONLY, not for humans.

`kancahub menu` intentionally does NOT list this. Humans get nothing from
raw pgvector rows (scores like `0.851`, truncated snippets); agents get
"does a tool already do X?" answered in one call. Keep it that way: do NOT
re-add memory items to `UNIFIED_MENU`, `beginner.py`, or human docs.

## What it is

Supabase pgvector RAG over the repo's own knowledge: `AGENTS.md`,
`README.md`, every `docs/` + `reports/` file, and every `scripts/*.py`
module docstring. Backed by Edge Fn `memory-embed` (gte-small, 384-dim)
+ table `memory_docs` in the SAME Supabase project as `temp-mail-api`.

Single home: `scripts/memory.py`. CLI passthrough: `kancahub memory ...`
(parser + dispatch in `scripts/kancahub.py`, group `memory`).

## Commands (agents only)

```bash
kancahub memory search "query" -k 8   # top chunks, source + score
kancahub memory ask "query" -k 8      # search, then answer via Zcode/GLM-5.3-Flash
kancahub memory stats                 # rows, sources, last update (JSON)
kancahub memory index                 # embed + upsert CHANGED sources only
kancahub memory index --full          # re-embed everything
kancahub memory reindex               # drop + rebuild everything
```

## Rules

- **Before creating a file, `memory search` first** (same rule as AGENTS.md's
  grep rule, but semantic). Nearly every "new" tool already exists.
- `index` (incremental) is the default; `reindex` only when the schema or
  chunking changes. Free-tier Edge Fn is sequential — batches of 8, 0.4 s gaps.
- `memory-index.timer` refreshes nightly 03:40. If `stats` shows a stale
  `last_update`, the timer is down — check it before reindexing by hand.
- `ask` needs a 9Router key (`zmine._r9_key()` → `~/.9router`, `~/.config/9router`,
  or `NINE_ROUTER_KEY`). Empty key → empty answer, not an error.
- Env (from `~/.config/auto-freecf/.env`): `SUPABASE_URL`,
  `SUPABASE_PROJECT_REF`, `SUPABASE_SERVICE_ROLE_KEY`, `MEM_KEY` (or `TMK_KEY`).

## Verified live (2026-10-16)

- `stats`: 712 rows, 172 sources (doc 365 / report 202 / script 145).
- `search "avoid duplicate scripts"`: 0.851 AGENTS.md — correct top hit.
- `ask "where is the zmine farm script"`: correct (`scripts/zmine.py`), ~8 s.
