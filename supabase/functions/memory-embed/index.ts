// ============================================================
// memory-embed — Supabase Edge Function
//
// Turns text into 384-dim embeddings with Supabase's built-in
// `gte-small` model (free, no API key, no external provider). The
// repo's memory indexer (scripts/memory.py) calls this to embed
// chunks for pgvector, and to embed search queries.
//
//   POST  {texts: ["...", ...]}   -> {embeddings: [[...], ...]}
//   POST  {query: "..."}          -> {embedding: [...]}
//
// Auth: x-api-key header must equal MEM_KEY (set with
// `supabase secrets set MEM_KEY=...`). Falls back to TMK_KEY.
// ============================================================

// deno-lint-ignore no-explicit-any
declare const Supabase: any;

const MEM_KEY = Deno.env.get("MEM_KEY") ?? Deno.env.get("TMK_KEY") ?? "";

const session = new Supabase.ai.Session("gte-small");

async function embed(text: string): Promise<number[]> {
  // mean_pool + normalize: exactly what gte-small wants for cosine search.
  const out = await session.run(text, { mean_pool: true, normalize: true });
  return Array.from(out as ArrayLike<number>);
}

Deno.serve(async (req: Request): Promise<Response> => {
  const key = req.headers.get("x-api-key") ?? "";
  if (MEM_KEY && key !== MEM_KEY) {
    return new Response(JSON.stringify({ error: "unauthorized" }), {
      status: 401,
      headers: { "content-type": "application/json" },
    });
  }

  let body: { texts?: string[]; query?: string };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "invalid json" }), {
      status: 400,
      headers: { "content-type": "application/json" },
    });
  }

  try {
    if (Array.isArray(body.texts)) {
      const embeddings: number[][] = [];
      for (const t of body.texts) embeddings.push(await embed(String(t ?? "")));
      return new Response(JSON.stringify({ embeddings }), {
        headers: { "content-type": "application/json" },
      });
    }
    if (typeof body.query === "string") {
      const embedding = await embed(body.query);
      return new Response(JSON.stringify({ embedding }), {
        headers: { "content-type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ error: "send {texts:[]} or {query:''}" }), {
      status: 400,
      headers: { "content-type": "application/json" },
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 500,
      headers: { "content-type": "application/json" },
    });
  }
});
