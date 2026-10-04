// ============================================================
// temp-mail-api — Supabase Edge Function
//
// Backend for Auto-FreeCF's signup pipeline. Implements the
// protocol expected by mail-adapter/adapter.py (bluk-cf format):
//
//   POST ?action=create   {domain}                     + x-api-key
//        -> {address, owner_token, domain}
//   GET  ?action=messages ?owner_token=&address=        + x-api-key
//        -> {messages: [...]}
//   GET  ?action=message  ?owner_token=&id=             + x-api-key
//        -> {message: {...}}
//   GET  ?action=domains                                + x-api-key
//        -> {domains: [...]}
//   POST ?action=ingest   <incoming mail>              + x-ingest-secret
//        -> {ok: true, message_id}                     (called by CF Worker)
//
// Secrets (set with `supabase secrets set`):
//   TMK_KEY            — shared API key required on temp-mail actions
//   MAIL_INGEST_SECRET — shared secret required on ?action=ingest
//   MAIL_DOMAINS       — comma-separated list of accepted domains
//
// SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are injected automatically.
// ============================================================

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_ROLE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const TMK_KEY = Deno.env.get("TMK_KEY") ?? "";
const MAIL_INGEST_SECRET = Deno.env.get("MAIL_INGEST_SECRET") ?? "";
const MAIL_DOMAINS = (Deno.env.get("MAIL_DOMAINS") ?? "")
  .split(",")
  .map((d) => d.trim().toLowerCase())
  .filter(Boolean);

const db = createClient(SUPABASE_URL, SERVICE_ROLE, {
  auth: { persistSession: false },
});

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type,Authorization,x-api-key,x-ingest-secret",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...CORS },
  });
}

function err(message: string, status = 400): Response {
  return json({ error: message }, status);
}

function randomToken(bytes = 24): string {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
}

function randomLocalPart(): string {
  const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
  const buf = new Uint8Array(10);
  crypto.getRandomValues(buf);
  return Array.from(buf, (b) => alphabet[b % alphabet.length]).join("");
}

// Constant-time-ish compare to avoid trivial timing leaks.
function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// ---------- action handlers ----------

async function createAddress(body: Record<string, unknown>): Promise<Response> {
  let domain = String(body.domain ?? "").trim().toLowerCase();
  if (!domain) domain = MAIL_DOMAINS[0] ?? "";
  if (!domain) return err("No domain configured (MAIL_DOMAINS empty)", 500);
  if (MAIL_DOMAINS.length && !MAIL_DOMAINS.includes(domain)) {
    return err(`Unsupported domain: ${domain}`, 422);
  }

  // Allow a caller-supplied local part (adapter also accepts `name`).
  const requested = String(body.name ?? body.local ?? "").trim().toLowerCase();
  const local = (requested || randomLocalPart()).replace(/[^a-z0-9._-]/g, "");
  if (!local) return err("Invalid local part", 422);

  const address = `${local}@${domain}`;
  const owner_token = randomToken();

  const { error } = await db.from("temp_addresses").insert({
    address,
    owner_token,
    domain,
  });

  if (error) {
    // 23505 = unique violation (address already exists) → retry once with random local
    if (error.code === "23505" && requested) {
      const retryLocal = randomLocalPart();
      const retryAddr = `${retryLocal}@${domain}`;
      const retryToken = randomToken();
      const { error: e2 } = await db.from("temp_addresses").insert({
        address: retryAddr,
        owner_token: retryToken,
        domain,
      });
      if (e2) return err(`insert failed: ${e2.message}`, 500);
      return json({ address: retryAddr, owner_token: retryToken, domain });
    }
    return err(`insert failed: ${error.message}`, 500);
  }

  return json({ address, owner_token, domain });
}

async function listMessages(params: URLSearchParams): Promise<Response> {
  const owner_token = params.get("owner_token") ?? "";
  const address = params.get("address") ?? "";
  if (!owner_token) return err("owner_token required", 422);

  // Resolve address from token if not provided.
  let addr = address;
  if (!addr) {
    const { data, error } = await db
      .from("temp_addresses")
      .select("address")
      .eq("owner_token", owner_token)
      .maybeSingle();
    if (error) return err(error.message, 500);
    if (!data) return err("unknown owner_token", 404);
    addr = data.address;
  }

  const { data, error } = await db
    .from("temp_messages")
    .select("id, message_id, address, from_address, from_name, subject, text_body, html_body, received_at")
    .eq("address", addr)
    .order("received_at", { ascending: false })
    .limit(50);

  if (error) return err(error.message, 500);
  return json({ messages: data ?? [] });
}

async function getMessage(params: URLSearchParams): Promise<Response> {
  const owner_token = params.get("owner_token") ?? "";
  const id = params.get("id") ?? params.get("message_id") ?? "";
  if (!owner_token) return err("owner_token required", 422);
  if (!id) return err("id required", 422);

  const { data: addrRow, error: aErr } = await db
    .from("temp_addresses")
    .select("address")
    .eq("owner_token", owner_token)
    .maybeSingle();
  if (aErr) return err(aErr.message, 500);
  if (!addrRow) return err("unknown owner_token", 404);

  // `id` may be the numeric PK or the provider message_id.
  const numeric = /^\d+$/.test(id);
  const query = db
    .from("temp_messages")
    .select("id, message_id, address, from_address, from_name, subject, text_body, html_body, received_at")
    .eq("address", addrRow.address);
  const { data, error } = numeric
    ? await query.eq("id", Number(id)).maybeSingle()
    : await query.eq("message_id", id).maybeSingle();

  if (error) return err(error.message, 500);
  if (!data) return err("message not found", 404);
  return json({ message: data });
}

async function listDomains(): Promise<Response> {
  return json({ domains: MAIL_DOMAINS });
}

async function ingest(body: Record<string, unknown>): Promise<Response> {
  const address = String(body.to ?? body.address ?? "").trim().toLowerCase();
  if (!address) return err("to/address required", 422);

  const domain = address.split("@")[1] ?? "";
  if (MAIL_DOMAINS.length && !MAIL_DOMAINS.includes(domain)) {
    return err(`domain not accepted: ${domain}`, 422);
  }

  const message_id =
    String(body.message_id ?? body.id ?? "") || `ing-${randomToken(12)}`;

  const { error } = await db.from("temp_messages").upsert(
    {
      message_id,
      address,
      from_address: body.from_address ?? body.from ?? null,
      from_name: body.from_name ?? null,
      subject: body.subject ?? null,
      text_body: body.text ?? body.text_body ?? null,
      html_body: body.html ?? body.html_body ?? null,
      raw: body.raw ?? body,
      received_at: body.received_at ?? new Date().toISOString(),
    },
    { onConflict: "message_id" },
  );

  if (error) return err(error.message, 500);
  return json({ ok: true, message_id });
}

// ---------- bluk-cf compatible path handlers ----------
// These let the signup pipeline call this function directly (no local
// mail-adapter needed). The "jwt" is `owner_token::address`, matching
// mail-adapter.py's embedded format.

function parseJwt(header: string): { owner_token: string; address: string } | null {
  const auth = header.startsWith("Bearer ") ? header.slice(7) : header;
  if (!auth) return null;
  if (auth.includes("::")) {
    const [owner_token, address] = auth.split("::", 1 + 1);
    return { owner_token, address };
  }
  return { owner_token: auth, address: "" };
}

async function pathNewAddress(req: Request): Promise<Response> {
  const body = await req.json().catch(() => ({}));
  const res = await createAddress(body as Record<string, unknown>);
  if (!res.ok) return res;
  const data = await res.clone().json() as { address: string; owner_token: string; domain: string };
  // bluk-cf expects {address, jwt, domain}
  return json({
    address: data.address,
    jwt: `${data.owner_token}::${data.address}`,
    domain: data.domain,
  });
}

async function pathParsedMails(req: Request): Promise<Response> {
  const info = parseJwt(req.headers.get("Authorization") ?? req.headers.get("x-api-key") ?? "");
  if (!info || !info.owner_token) return err("Missing or invalid Authorization", 401);

  // Resolve address if not embedded in the token.
  let addr = info.address;
  if (!addr) {
    const { data } = await db
      .from("temp_addresses").select("address").eq("owner_token", info.owner_token).maybeSingle();
    if (!data) return err("unknown owner_token", 404);
    addr = data.address;
  }

  const { data, error } = await db
    .from("temp_messages")
    .select("id, message_id, address, from_address, from_name, subject, text_body, html_body, received_at")
    .eq("address", addr)
    .order("received_at", { ascending: false })
    .limit(50);
  if (error) return err(error.message, 500);

  // Shape into the parsed_mails format the pipeline expects.
  const results = (data ?? []).map((m) => {
    const fromField = m.from_name ? `${m.from_name} <${m.from_address}>` : (m.from_address ?? "");
    return {
      id: m.id,
      message_id: m.message_id,
      from: fromField,
      sender: fromField,
      to: m.address,
      subject: m.subject ?? "",
      text: m.text_body ?? "",
      html: m.html_body ?? "",
      body: m.text_body ?? "",
      snippet: (m.text_body ?? "").slice(0, 200),
      date: m.received_at,
    };
  });
  return json(results);
}

async function pathParsedMail(req: Request, id: string): Promise<Response> {
  const info = parseJwt(req.headers.get("Authorization") ?? req.headers.get("x-api-key") ?? "");
  if (!info || !info.owner_token) return err("Missing or invalid Authorization", 401);

  const numeric = /^\d+$/.test(id);
  const q = db
    .from("temp_messages")
    .select("id, message_id, address, from_address, from_name, subject, text_body, html_body, received_at");
  const { data, error } = numeric
    ? await q.eq("id", Number(id)).maybeSingle()
    : await q.eq("message_id", id).maybeSingle();
  if (error) return err(error.message, 500);
  if (!data) return err("message not found", 404);

  const fromField = data.from_name ? `${data.from_name} <${data.from_address}>` : (data.from_address ?? "");
  return json({
    id: data.id,
    message_id: data.message_id,
    from: fromField,
    sender: fromField,
    to: data.address,
    subject: data.subject ?? "",
    text: data.text_body ?? "",
    html: data.html_body ?? "",
    body: data.text_body ?? "",
    snippet: (data.text_body ?? "").slice(0, 200),
    date: data.received_at,
  });
}

// ---------- router ----------

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 200, headers: CORS });

  const url = new URL(req.url);
  let path = url.pathname;
  // Supabase may prefix the function name; normalize to the tail route.
  path = path.replace(/^.*\/temp-mail-api/, "") || "/";

  const action = (url.searchParams.get("action") ?? "").toLowerCase();
  const apiKey = req.headers.get("x-api-key") ?? "";
  const ingestHeader = req.headers.get("x-ingest-secret") ?? "";

  // ---- bluk-cf compatible path routes (auth via x-api-key OR Authorization) ----
  if (path === "/new_address" && req.method === "POST") {
    if (!TMK_KEY || !safeEqual(apiKey, TMK_KEY)) return err("Unauthorized", 401);
    return await pathNewAddress(req);
  }
  if (path === "/parsed_mails" && req.method === "GET") {
    return await pathParsedMails(req);
  }
  if (path.startsWith("/parsed_mail/") && req.method === "GET") {
    return await pathParsedMail(req, path.split("/").pop() ?? "");
  }

  try {
    // ---- ingest (Cloudflare Worker) ----
    if (action === "ingest") {
      if (req.method !== "POST") return err("POST required", 405);
      if (!MAIL_INGEST_SECRET || !safeEqual(ingestHeader, MAIL_INGEST_SECRET)) {
        return err("Unauthorized", 401);
      }
      const body = await req.json().catch(() => ({}));
      return await ingest(body as Record<string, unknown>);
    }

    // ---- all other actions require the shared API key ----
    if (!TMK_KEY || !safeEqual(apiKey, TMK_KEY)) {
      return err("Unauthorized", 401);
    }

    switch (action) {
      case "create": {
        if (req.method !== "POST") return err("POST required", 405);
        const body = await req.json().catch(() => ({}));
        return await createAddress(body as Record<string, unknown>);
      }
      case "messages":
        return await listMessages(url.searchParams);
      case "message":
        return await getMessage(url.searchParams);
      case "domains":
        return await listDomains();
      default:
        return err("Not found", 404);
    }
  } catch (e) {
    return err(e instanceof Error ? e.message : String(e), 500);
  }
});
