#!/usr/bin/env python3
"""Direct Gemini 4 Argon bridge (lithium7b account only).

WHY this exists: 9Router 0.5.104 maps ag/gemini-4-argon-* to upstream
`...(tier)` (parens) which Google 404s; the IDE's bare `gemini-4-argon-{tier}`
works but only on the IDE channel. This bridge replicates the IDE's EXACT
request shape (captured via MITM 2026-10-16, see argon_cap notes in AGENTS.md)
against daily-cloudcode-pa, so opencode/omp can call argon.

  Endpoint: POST https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse
  Body: {"project":"aicode-consumers","requestId":"checkpoint/<uuid>",
         "request":{"contents":[...]}, "model":"gemini-4-argon-{low,medium,high}",
         "userAgent":"antigravity","requestType":"checkpoint"}
  Token: read per-call from 9Router DB (its refresher keeps it fresh).
         Single account: lithium7b@gmail.com. Sequential use only.

Run:  python3 scripts/argon_bridge.py  (listens 127.0.0.1:8788)
Test: curl -X POST 127.0.0.1:8788/v1/chat/completions -d '{"messages":[{"role":"user","content":"hi"}]}'
"""
import json
import os
import sqlite3
import threading
import urllib.request
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8788
DB = os.path.expanduser("~/.9router/db/data.sqlite")
EMAIL = "lithium7b@gmail.com"
ENDPOINT = "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
UA = "antigravity/hub/2.19.1 (aidev_client; os_type=linux; arch=amd64; cl=991101027)"
TIERS = {"low": "gemini-4-argon-low", "medium": "gemini-4-argon-medium",
         "high": "gemini-4-argon-high"}
_lock = threading.Lock()


def token():
    c = sqlite3.connect(DB)
    row = c.execute("SELECT data FROM providerConnections WHERE email=?", (EMAIL,)).fetchone()
    if not row:
        raise RuntimeError("no connection for " + EMAIL)
    return json.loads(row[0]).get("accessToken", "")


_SCHEMA_KEYS = {"type", "properties", "required", "description", "enum",
                "items", "format", "nullable"}


def _clean_schema(node):
    """Recursively force an OpenAI-ish schema into Gemini's proto dialect.

    Killer case (measured 2026-10-16): "type": ["string", "null"] (JSON-Schema
    nullable form). Gemini's proto field is not repeating -> whole request
    400s with 'Unknown name "type" ... cannot start list'. Collapse arrays
    to the first non-null type.
    """
    if isinstance(node, list):
        return [_clean_schema(x) for x in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k not in _SCHEMA_KEYS:
            continue
        out[k] = _clean_schema(v)
    t = out.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        out["type"] = non_null[0] if non_null else "string"
    props = out.get("properties")
    if isinstance(props, dict):
        out["properties"] = {k: (v if isinstance(v, dict) else {"type": "string"})
                             for k, v in props.items()}
    # required must name EXISTING properties: a single dangling entry makes
    # Google 400 the whole request (measured 2026-10-16, omp schemas).
    req = out.get("required")
    if isinstance(req, list):
        have = set(out.get("properties", {}).keys()) if isinstance(out.get("properties"), dict) else set()
        out["required"] = [r for r in req if r in have]
    return out


# ---------------------------------------------------------------------------
# thoughtSignature store
# ---------------------------------------------------------------------------
# Gemini 4/3 REQUIRE the `thoughtSignature` that came back with a `functionCall`
# to be replayed, verbatim, on the same part in the next request. Omit it and
# the WHOLE request 400s:
#
#   Function call is missing a thought_signature in functionCall parts.
#   ... function call `default_api:shell`, position 40.
#
# The old store was keyed ONLY by the `tc-<uuid>` id we mint, and lived only in
# process memory. Both halves were wrong:
#
#   * Callers rewrite ids. 9router's OpenAI-compat layer re-issues `call_*`
#     ids, so the replay arrives with an id we never minted -> lookup misses ->
#     signature silently dropped -> 400. (Measured 2026-10-18: same id = 200,
#     rewritten id = 400, identical conversation.)
#   * A restart emptied the map, so even an id-preserving caller 400'd on the
#     first replay after any restart.
#
# So we key by BOTH the id and a fingerprint of (tool name + arguments), which
# survives id rewriting, and persist to disk so restarts do not lose it.
_SIG_PATH = os.path.expanduser("~/.cache/argon-bridge/sig.json")
_SIG_MAX = 5000
_SIG = {}
_SIG_LOCK = threading.Lock()
# Holds a signature that arrived on its own chunk, for the functionCall that
# follows in the same upstream response. Reset per request.
_last_sig = [None]

# Google's documented dummy value for calls that genuinely have no signature
# (history imported from another model, synthetic seeds). Better than a 400:
# https://ai.google.dev/gemini-api/docs/thought-signatures
SKIP_SIG = "skip_thought_signature_validator"


def _sig_load():
    try:
        with open(_SIG_PATH) as f:
            d = json.load(f)
        if isinstance(d, dict):
            _SIG.update({k: v for k, v in d.items() if isinstance(v, str) and v})
    except Exception:
        pass


def _sig_save():
    try:
        os.makedirs(os.path.dirname(_SIG_PATH), exist_ok=True)
        tmp = _SIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_SIG, f)
        os.replace(tmp, _SIG_PATH)
    except Exception:
        pass


def _sig_key(name, args):
    """Stable fingerprint of a call, independent of the caller's tool_call id."""
    try:
        norm = json.dumps(args, sort_keys=True, separators=(",", ":"))
    except Exception:
        norm = str(args)
    return "fp:" + __import__("hashlib").sha256(
        ((name or "") + "\x00" + norm).encode()).hexdigest()[:32]


def _sig_put(tid, sig, name=None, args=None):
    if not (isinstance(sig, str) and sig):
        return
    with _SIG_LOCK:
        if len(_SIG) >= _SIG_MAX:
            for k in list(_SIG)[: _SIG_MAX // 10]:
                _SIG.pop(k, None)
        if tid:
            _SIG[tid] = sig
        if name is not None:
            _SIG[_sig_key(name, args)] = sig
        _sig_save()


def _sig_get(tid, name=None, args=None):
    with _SIG_LOCK:
        if tid and tid in _SIG:
            return _SIG[tid]
        if name is not None:
            return _SIG.get(_sig_key(name, args))
    return None


_sig_load()


def _to_gemini(messages, tools):
    """OpenAI messages+tools -> Gemini contents+declarations+systemInstruction."""
    system = None
    contents = []
    names = {}  # tool_call_id -> function name (same-request mapping)
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "system":
            c = m.get("content")
            system = {"role": "user", "parts": [{"text": c if isinstance(c, str) else ""}]}
            continue
        if role == "assistant" and m.get("tool_calls"):
            parts = []
            txt = m.get("content")
            if isinstance(txt, str) and txt:
                parts.append({"text": txt})
            for tc in m["tool_calls"]:
                fn = (tc.get("function") or {})
                tid = tc.get("id") or ("tc-" + uuid.uuid4().hex[:8])
                names[tid] = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                # thoughtSignature goes OUTSIDE functionCall: upstream rejects
                # unknown fields inside it.
                name = fn.get("name", "")
                part = {"functionCall": {"name": name, "args": args}}
                sig = _sig_get(tid, name, args)
                # Never emit a bare functionCall: that is the 400. If we have no
                # real signature (id rewritten AND fingerprint miss, or history
                # imported from another model) fall back to Google's documented
                # sentinel, which skips validation instead of failing the turn.
                part["thoughtSignature"] = sig or SKIP_SIG
                parts.append(part)
            contents.append({"role": "model", "parts": parts})
            continue
        if role == "tool":
            nm = names.get(m.get("tool_call_id", ""), "")
            out = m.get("content")
            txt = out if isinstance(out, str) else json.dumps(out)
            if nm:
                contents.append({"role": "user", "parts": [
                    {"functionResponse": {"name": nm, "response": {"output": txt}}}]})
            else:
                # unknown id (history from another model/bridge): a nameless
                # functionResponse makes Google 400 the WHOLE request, so
                # degrade to plain text instead of failing everything.
                contents.append({"role": "user", "parts": [
                    {"text": "[tool result (unlinked)]: " + txt}]})
            continue
        c = m.get("content")
        if isinstance(c, list):  # content blocks: keep text, note images
            txts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") != "image_url"]
            imgs = sum(1 for b in c if isinstance(b, dict) and b.get("type") == "image_url")
            c = "".join(txts) + (" [image omitted]" if imgs else "")
        contents.append({"role": ("model" if role == "assistant" else "user"),
                         "parts": [{"text": str(c or "")}]})
    decls = []
    for t in tools or []:
        fn = (t.get("function") or {}) if isinstance(t, dict) else {}
        if not fn.get("name"):
            continue
        params = fn.get("parameters") or {}
        if not isinstance(params, dict):
            params = {}
        # strip OpenAI-isms + collapse nullable type-arrays (see _clean_schema)
        params = _clean_schema(params)
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}}
        params.setdefault("type", "object")
        params.setdefault("properties", {})
        decls.append({"name": fn["name"], "description": fn.get("description", "")[:500],
                      "parameters": params})
    return system, contents, decls


def upstream(messages, tier, tools=None):
    model = TIERS.get((tier or "medium").lower(), TIERS["medium"])
    _last_sig[0] = None
    system, contents, decls = _to_gemini(messages, tools)
    req_body = {"contents": contents}
    if system:
        req_body["systemInstruction"] = system
    if decls:
        req_body["tools"] = [{"functionDeclarations": decls}]
    body = {"project": "aicode-consumers", "requestId": "checkpoint/" + str(uuid.uuid4()),
            "request": req_body, "model": model,
            "userAgent": "antigravity", "requestType": "checkpoint"}
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + token(),
                                          "Content-Type": "application/json",
                                          "User-Agent": UA})
    with _lock:
        raw = urllib.request.urlopen(req, timeout=300).read().decode("utf-8", "replace")
    out, calls, saw_finish = [], [], []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data: ") and line != "data: [DONE]":
            try:
                d = json.loads(line[6:])
            except Exception:
                continue
            for cand in ((d.get("response") or {}).get("candidates") or []):
                if cand.get("finishReason"):
                    saw_finish.append(cand["finishReason"])
                for p in (((cand.get("content") or {}).get("parts")) or []):
                    if not isinstance(p, dict):
                        continue
                    if "text" in p and "thought" not in p:
                        out.append(p["text"])
                    elif "functionCall" in p:
                        fc = p["functionCall"] or {}
                        tcid = "tc-" + uuid.uuid4().hex[:8]
                        name = fc.get("name", "")
                        args = fc.get("args") or {}
                        # The signature may ride on the functionCall part OR on a
                        # sibling `thought` part (both shapes are legal, and the
                        # thought-part shape is what broke gemini-cli). Grab
                        # whatever this chunk carries, and remember it under both
                        # the id and the call fingerprint.
                        _sig_put(tcid,
                                 p.get("thoughtSignature") or p.get("thought_signature")
                                 or _last_sig[0],
                                 name, args)
                        calls.append({"id": tcid, "type": "function",
                                      "function": {"name": name,
                                                   "arguments": json.dumps(args)}})
                    elif p.get("thoughtSignature") or p.get("thought_signature"):
                        # Signature-only chunk: hold it for the functionCall that
                        # follows in this same response.
                        _last_sig[0] = p.get("thoughtSignature") or p.get("thought_signature")
    if not "".join(out) and not calls:
        try:
            with open("/tmp/opencode/argon_empty.log", "a") as f:
                f.write("=== %s model=%s finish=%s bytes=%d\n%s\n" % (
                    __import__("time").strftime("%H:%M:%S"), model,
                    saw_finish, len(raw), raw[:1500]))
        except Exception:
            pass
    return "".join(out), calls, model


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            return self._send({"ok": True, "model": "argon", "account": EMAIL})
        if self.path == "/v1/models" or self.path.endswith("/models"):
            return self._send({"object": "list", "data": [
                {"id": "argon-low", "object": "model", "owned_by": "argon"},
                {"id": "argon-medium", "object": "model", "owned_by": "argon"},
                {"id": "argon-high", "object": "model", "owned_by": "argon"}]})
        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._send({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return self._send({"error": "bad json"}, 400)
        tier = body.get("tier", "medium")
        if isinstance(body.get("model"), str) and body["model"].startswith("argon-"):
            tier = body["model"].split("-", 1)[1]
        try:
            text, calls, model = upstream(body.get("messages") or [], tier,
                                          body.get("tools"))
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            return self._send({"error": "upstream HTTP %s %s" % (e.code, detail)}, 502)
        except Exception as e:
            return self._send({"error": str(e)[:120]}, 500)
        msg = {"role": "assistant", "content": text or None}
        if calls:
            msg["tool_calls"] = calls
        finish = "tool_calls" if calls else "stop"
        if body.get("stream"):
            # real SSE: omp's streaming parser drops non-chunked bodies,
            # which turns pure tool-call answers into "empty stop".
            cid = "argon-" + uuid.uuid4().hex[:8]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            def emit(obj):
                self.wfile.write(("data: %s\n\n" % json.dumps(obj)).encode())
            # A client that hangs up mid-stream (omp does this the moment it has
            # what it needs) raises BrokenPipeError from deep in socketserver and
            # dumps a full traceback into the journal. That is normal client
            # behaviour, not a bridge fault, and the noise buries real errors.
            try:
                emit({"id": cid, "object": "chat.completion.chunk", "model": model,
                      "choices": [{"index": 0, "delta": {"role": "assistant",
                                                         "content": text or None},
                                   "finish_reason": None}]})
                for i, tc in enumerate(calls):
                    emit({"id": cid, "object": "chat.completion.chunk", "model": model,
                          "choices": [{"index": 0, "delta": {"tool_calls": [{
                              "index": i, "id": tc["id"], "type": "function",
                              "function": tc["function"]}]}, "finish_reason": None}]})
                emit({"id": cid, "object": "chat.completion.chunk", "model": model,
                      "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
                self.wfile.write(b"data: [DONE]\n\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self._send({"id": "argon-" + uuid.uuid4().hex[:8], "object": "chat.completion",
                    "model": model, "choices": [
                        {"index": 0, "message": msg, "finish_reason": finish}]})


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
