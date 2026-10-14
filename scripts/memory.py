#!/usr/bin/env python3
"""memory — shared agent memory (RAG) backed by Supabase pgvector.

Single home for "what did we already learn?". Indexes the repo's own memory
sources (AGENTS.md, docs/, reports/, and every script's module docstring) into
a pgvector table, then answers questions by nearest-neighbour search. This is
the Q5 answer: agents stop forgetting and stop re-creating tools that exist.

It does NOT replace AGENTS.md — it makes AGENTS.md (and docs/reports) queryable.
Embeddings come from the `memory-embed` Edge Function (Supabase's free built-in
gte-small, 384-dim); storage is the `memory_docs` table in the SAME Supabase
project that already backs temp-mail-api.

  memory.py index [--full]      embed + upsert changed sources
  memory.py search "query"      top chunks, with source + score
  memory.py ask "query"         search, then answer with the LLM (Zcode flash)
  memory.py stats               row count + sources + last update
  memory.py reindex             drop + rebuild everything

Env (loaded from ~/.config/auto-freecf/.env if present):
  SUPABASE_URL, SUPABASE_PROJECT_REF, SUPABASE_SERVICE_ROLE_KEY, MEM_KEY
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
ENV_FILE = Path.home() / ".config" / "auto-freecf" / ".env"

CHUNK_CHARS = 1400          # ~350 tokens: big enough for context, small for hits
CHUNK_OVERLAP = 200
EMBED_BATCH = 8             # Edge Function is sequential; keep batches modest


# ─────────────────────────────────────────────────────────────── env + http
def _load_env() -> None:
    if not ENV_FILE.exists():
        return
    for ln in ENV_FILE.read_text(errors="replace").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
PROJECT_REF = os.environ.get("SUPABASE_PROJECT_REF", "")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
MEM_KEY = os.environ.get("MEM_KEY") or os.environ.get("TMK_KEY", "")
FUNCTIONS_URL = f"{SUPABASE_URL}/functions/v1" if SUPABASE_URL else ""
REST_URL = f"{SUPABASE_URL}/rest/v1" if SUPABASE_URL else ""


def log(msg: str) -> None:
    print(msg, flush=True)


def _post(url: str, body: dict, headers: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _get(url: str, headers: dict, timeout: int = 60):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"[]")


# ─────────────────────────────────────────────────────────────── embeddings
def embed_texts(texts: list[str]) -> list[list[float]]:
    if not FUNCTIONS_URL:
        raise RuntimeError("SUPABASE_URL not set (check ~/.config/auto-freecf/.env)")
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i:i + EMBED_BATCH]
        emb = _embed_batch(batch)
        out.extend(emb)
        if i + EMBED_BATCH < len(texts):
            time.sleep(0.4)                       # be gentle on the free fn
    return out


def _embed_batch(batch: list[str], tries: int = 4) -> list[list[float]]:
    """One Edge Function call, retrying on the free-tier WORKER_RESOURCE_LIMIT
    (546) and transient 5xx. A batch that keeps failing is split and retried
    element-by-element, so a single heavy chunk can't sink the whole index."""
    last = ""
    for t in range(tries):
        try:
            j = _post(f"{FUNCTIONS_URL}/memory-embed", {"texts": batch},
                      {"x-api-key": MEM_KEY})
            emb = j.get("embeddings") or []
            if len(emb) == len(batch):
                return emb
            last = f"mismatch {len(batch)}->{len(emb)}"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code not in (429, 500, 502, 503, 504, 546):
                raise
        except Exception as e:  # noqa: BLE001
            last = type(e).__name__
        time.sleep(1.5 * (t + 1))
    if len(batch) > 1:                            # split and retry the halves
        mid = len(batch) // 2
        return _embed_batch(batch[:mid]) + _embed_batch(batch[mid:])
    raise RuntimeError(f"embed failed for 1 chunk after retries: {last}")


def embed_query(text: str) -> list[float]:
    j = _post(f"{FUNCTIONS_URL}/memory-embed", {"query": text}, {"x-api-key": MEM_KEY})
    e = j.get("embedding")
    if not e:
        raise RuntimeError("no embedding returned")
    return e


# ─────────────────────────────────────────────────────────────── chunking
def _split(text: str) -> list[tuple[str, str]]:
    """Split into (heading, chunk) pairs, preferring markdown/heading boundaries."""
    parts: list[tuple[str, str]] = []
    cur_head = ""
    buf = ""
    for ln in text.splitlines():
        h = re.match(r"^\s{0,3}(#{1,6})\s+(.*)$", ln)
        if h and buf.strip():
            parts.append((cur_head, buf.strip()))
            buf = ""
        if h:
            cur_head = h.group(2).strip()[:120]
        buf += ln + "\n"
        while len(buf) > CHUNK_CHARS:
            cut = buf.rfind("\n", CHUNK_CHARS // 2, CHUNK_CHARS)
            cut = cut if cut > 0 else CHUNK_CHARS
            parts.append((cur_head, buf[:cut].strip()))
            buf = buf[max(0, cut - CHUNK_OVERLAP):]
    if buf.strip():
        parts.append((cur_head, buf.strip()))
    return [(h, c) for h, c in parts if len(c) >= 40]


def _docstring(path: Path) -> str:
    """First module/function docstring of a script, which is where its purpose is."""
    try:
        txt = path.read_text(errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    m = re.search(r'^\s*(?:"""|\'\'\')(.+?)(?:"""|\'\'\')', txt, re.S | re.M)
    return m.group(1).strip() if m else ""


def sources(full: bool = False) -> list[tuple[str, str, str]]:
    """Yield (source, kind, text) for every memory source in the repo."""
    out: list[tuple[str, str, str]] = []

    for name in ("AGENTS.md", "README.md"):
        p = ROOT / name
        if p.exists():
            out.append((name, "doc", p.read_text(errors="replace")))

    for d, kind in (("docs", "doc"), ("reports", "report")):
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix in (".md", ".txt", ".json") and p.stat().st_size < 400_000:
                out.append((str(p.relative_to(ROOT)), kind, p.read_text(errors="replace")))

    # script docstrings: the fastest way to answer "does a tool already do X?"
    for p in sorted(SCRIPTS.glob("*.py")):
        ds = _docstring(p)
        if ds:
            out.append((f"scripts/{p.name}", "script", f"# {p.name}\n\n{ds}"))
    return out


# ─────────────────────────────────────────────────────────────── storage
def _rest_headers() -> dict:
    if not SERVICE_KEY:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY not set")
    return {"apikey": SERVICE_KEY, "authorization": f"Bearer {SERVICE_KEY}"}


def _existing() -> dict[str, str]:
    """source -> updated_at, so we can skip unchanged files on a normal index."""
    rows = _get(f"{REST_URL}/memory_docs?select=source,updated_at", _rest_headers())
    out: dict[str, str] = {}
    for r in rows:
        s = r["source"]
        out[s] = max(out.get(s, ""), r.get("updated_at") or "")
    return out


def index(full: bool = False) -> dict:
    have = {} if full else _existing()
    stats = {"sources": 0, "chunks": 0, "skipped": 0, "rows": 0}
    for src, kind, text in sources():
        stats["sources"] += 1
        if src in have:
            stats["skipped"] += 1
            continue
        rows = _split(text)
        if not rows:
            continue
        stats["chunks"] += len(rows)
        emb = embed_texts([c for _, c in rows])
        payload = [{"source": src, "chunk": i, "kind": kind, "heading": h,
                    "content": c, "tokens": max(1, len(c) // 4), "embedding": e}
                   for i, ((h, c), e) in enumerate(zip(rows, emb))]
        _post(f"{REST_URL}/memory_docs?on_conflict=source,chunk", payload,
              {**_rest_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"})
        stats["rows"] += len(payload)
        log(f"  {src:48s} {len(payload):3d} chunk(s)")
    return stats

def search(query: str, k: int = 8, min_sim: float = 0.0) -> list[dict]:
    q = embed_query(query)
    return _post(f"{REST_URL}/rpc/match_memory",
                 {"query_embedding": q, "match_count": k, "min_similarity": min_sim},
                 _rest_headers())


def stats() -> dict:
    rows = _get(f"{REST_URL}/memory_docs?select=source,kind,tokens,updated_at",
                _rest_headers())
    by_kind: dict[str, int] = {}
    last = ""
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        last = max(last, r.get("updated_at") or "")
    return {"rows": len(rows), "sources": len({r["source"] for r in rows}),
            "by_kind": by_kind, "last_update": last}


# ─────────────────────────────────────────────────────────────── ask (LLM)
def _llm(prompt: str, model: str = "Zcode/GLM-5.3-Flash") -> str:
    try:
        sys.path.insert(0, str(SCRIPTS))
        import zmine  # noqa: PLC0415
        key = zmine._r9_key()
    except Exception:  # noqa: BLE001
        key = os.environ.get("NINE_ROUTER_KEY", "")
    if not key:
        return ""
    base = os.environ.get("NINE_ROUTER_BASE", "http://localhost:20128")
    body = {"model": model, "stream": False, "max_tokens": 700, "temperature": 0,
            "reasoning_effort": "medium",
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(f"{base.rstrip('/')}/v1/chat/completions",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"authorization": f"Bearer {key}",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        j = json.loads(r.read())
    return ((j.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""


def ask(query: str, k: int = 8) -> str:
    hits = search(query, k=k, min_sim=0.1)
    if not hits:
        return "(no memory hits)"
    ctx = "\n\n".join(f"[{h['source']} #{h['chunk']}] {h.get('heading') or ''}\n{h['content']}"
                      for h in hits)
    prompt = ("You are the memory index for a code repo. Answer the question ONLY from "
              "the context. Cite the sources you used as [path]. If the answer is not "
              "in the context, say so plainly.\n\n"
              f"Context:\n{ctx}\n\nQuestion: {query}\n\nAnswer:")
    return _llm(prompt)


# ─────────────────────────────────────────────────────────────── CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="memory.py", description="pgvector memory for agents")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("index"); s.add_argument("--full", action="store_true")
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("-k", type=int, default=8)
    s.add_argument("--min-sim", type=float, default=0.0)
    s = sub.add_parser("ask"); s.add_argument("query"); s.add_argument("-k", type=int, default=8)
    sub.add_parser("stats")
    sub.add_parser("reindex")

    a = ap.parse_args(argv)
    try:
        if a.cmd in ("index", "reindex"):
            st = index(full=(a.cmd == "reindex"))
            log(f"indexed {st['rows']} chunk(s) from {st['sources']} source(s)")
        elif a.cmd == "search":
            for h in search(a.query, k=a.k, min_sim=a.min_sim):
                head = f" — {h['heading']}" if h.get("heading") else ""
                log(f"{h['similarity']:.3f}  {h['source']}#{h['chunk']}{head}")
                log("    " + h["content"][:200].replace("\n", " ") + "...")
        elif a.cmd == "ask":
            log(ask(a.query, k=a.k))
        elif a.cmd == "stats":
            log(json.dumps(stats(), indent=2))
    except urllib.error.HTTPError as e:
        log(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
        return 1
    except Exception as e:  # noqa: BLE001
        log(f"error: {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
