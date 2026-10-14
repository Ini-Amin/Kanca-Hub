-- ============================================================
-- memory — shared agent memory (RAG) on pgvector
--
-- Answers "why not use the vector DB we already have?". Supabase
-- already backs temp-mail-api, so the memory index lives here too
-- instead of a new service. Embeddings are 384-dim (gte-small),
-- computed by the `memory-embed` Edge Function (free, built in).
--
--   memory_docs   one row per chunk of a memory source (AGENTS.md,
--                 docs/, reports/, script module docstrings).
--
-- Search is cosine distance on `embedding`; `source`+`chunk` is the
-- natural key so re-ingesting is idempotent (upsert).
-- ============================================================

create extension if not exists vector;

create table if not exists public.memory_docs (
    id          bigint generated always as identity primary key,
    source      text not null,              -- repo-relative path
    chunk       int  not null,              -- 0-based chunk index in that source
    kind        text not null default 'doc',-- doc | script | report
    heading     text,                        -- nearest markdown heading
    content     text not null,
    tokens      int,                         -- rough size, for budgeting
    embedding   vector(384),
    updated_at  timestamptz not null default now(),
    unique (source, chunk)
);

create index if not exists memory_docs_embedding_idx
    on public.memory_docs using hnsw (embedding vector_cosine_ops);

create index if not exists memory_docs_source_idx on public.memory_docs (source);

-- Cosine-distance search, callable from the REST API as an RPC.
create or replace function public.match_memory(
    query_embedding vector(384),
    match_count     int default 8,
    min_similarity  float default 0.0
)
returns table (
    source     text,
    chunk      int,
    kind       text,
    heading    text,
    content    text,
    similarity float
)
language sql stable
as $$
    select m.source, m.chunk, m.kind, m.heading, m.content,
           1 - (m.embedding <=> query_embedding) as similarity
    from public.memory_docs m
    where m.embedding is not null
      and 1 - (m.embedding <=> query_embedding) >= min_similarity
    order by m.embedding <=> query_embedding
    limit match_count;
$$;
