// ============================================================
// Auto-FreeCF Email Ingest Worker (Cloudflare Email Worker)
//
// Runs on every incoming mail for kancalabs.biz.id (catch-all -> this Worker).
// Parses the message with postal-mime and POSTs it to the Supabase Edge
// Function `temp-mail-api?action=ingest`, authenticated with MAIL_INGEST_SECRET.
//
// Bindings / vars:
//   MAIL_INGEST_SECRET  (secret) — must match the Supabase function secret
//   SUPABASE_URL        (var)    — https://<ref>.supabase.co
//   FORWARD_TO          (var, optional) — human-readable safety copy mailbox
// ============================================================

import PostalMime from "postal-mime";

export default {
  async email(message, env, ctx) {
    const ingestUrl =
      `${env.SUPABASE_URL}/functions/v1/temp-mail-api?action=ingest`;

    const to = message.to;
    const from = message.from;
    const subject = message.headers.get("subject") || "";

    // --- parse MIME properly ---
    let parsed = { text: "", html: "", subject, from: { address: from, name: "" } };
    let rawBytes = null;
    try {
      // Buffer the raw stream so we can both parse and, if needed, reuse it.
      rawBytes = await new Response(message.raw).arrayBuffer();
      parsed = await PostalMime.parse(rawBytes);
    } catch (e) {
      console.error("MIME parse failed:", e && e.message);
    }

    const payload = {
      to,
      from_address: from,
      from_name: (parsed.from && parsed.from.name) || "",
      subject: parsed.subject || subject,
      text: parsed.text || "",
      html: parsed.html || "",
      message_id:
        message.headers.get("message-id") ||
        (parsed.messageId ?? "") ||
        crypto.randomUUID(),
      received_at: new Date().toISOString(),
      raw: { headers: Object.fromEntries(message.headers) },
    };

    // --- 1. store in Supabase (fail loudly so CF can retry) ---
    const resp = await fetch(ingestUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-ingest-secret": env.MAIL_INGEST_SECRET,
      },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      throw new Error(`ingest failed: ${resp.status} ${detail}`);
    }

    // --- 2. optional human-readable forward (never block delivery on it) ---
    if (env.FORWARD_TO) {
      try {
        await message.forward(env.FORWARD_TO);
      } catch (e) {
        console.error("forward failed:", e && e.message);
      }
    }
  },
};
