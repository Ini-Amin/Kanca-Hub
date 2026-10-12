#!/usr/bin/env python3
"""scripts/zcode_bridge_server.py — local Anthropic/OpenAI-compatible bridge to the
ZCode Start Plan. Desktop app NOT needed: only per-account ZCode login tokens.

Forwarding recipe recovered by MITM-ing the official desktop app (verified live):
  POST https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages
  headers  = app source headers + `Authorization: Bearer <JWT>` + `x-api-key: <JWT>`
  WAF gate = body.system must LEAD with ZCode's own main-agent prompt blocks
             (scripts/zcode_system_blocks.json); client system is appended after.

State dir ~/.zcode-bridge/ (managed by scripts/zcode.py):
  accounts.txt  email:password per line
  keys.txt      <bridge-api-key>=<email>  (one key per account -> 9router sees
                one connection per account; its fallback = account failover)
  jwt/<email>.txt   ZCode login token, re-read hot on every request
  config.json   {"persona": {"enabled": bool, "models": [names...]}, "aliases": {name: {"model": ..., "persona": bool}}}
                persona.models empty/missing = ALL models; non-empty = whitelist
  persona.txt   persona template (abliteration by default)
  usage.json    local token counters (per account/model/day)
  balance.json  server-side quota cache written by `zcode.py watch`

Usage:  python3 scripts/zcode.py serve [--port 8787]
"""
import json, os, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.expanduser("~/.zcode-bridge")
UPSTREAM = "https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages"
BLOCKS_FILE = os.path.join(HERE, "zcode_system_blocks.json")
DEFAULT_MODEL = os.environ.get("ZCODE_MODEL", "GLM-5.3")
FALLBACK_JWT = "/tmp/opencode/zc_jwt.txt"

# Captured verbatim from the app's own model request (mitmproxy flow, 2026-10-10).
APP_HEADERS = {
    "user-agent": "ZCode/3.14.5 ai-sdk/provider-utils/4.0.27 runtime/node.js/24",
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "mid-conversation-system-2026-04-07",
    "http-referer": "https://zcode.z.ai",
    "x-title": "Z Code@electron",
    "x-zcode-app-version": "3.14.5",
    "x-platform": "linux-x64",
    "x-release-channel": "production",
    "x-client-language": "en-US",
    "x-client-timezone": "Asia/Jakarta",
    "x-os-category": "linux",
    "x-os-version": "7.2.8-300.fc45.x86_64",
    "x-zcode-agent": "glm",
    "x-zcode-session-type": "main",
    # NOTE: the concurrency slot (429 code 3009) is PER x-session-id — a
    # max_tokens-truncated request holds its session's slot. So a fresh
    # session id is minted per request (see upstream_headers).
}

SYSTEM_BLOCKS = json.load(open(BLOCKS_FILE))
USAGE_LOCK = threading.Lock()
API_KEYS = {k.strip() for k in
            (os.environ.get("BRIDGE_API_KEYS", "") or os.environ.get("BRIDGE_API_KEY", "")).split(",")
            if k.strip()}


# ---------- hot-reloaded state ----------
def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def load_config():
    try:
        return json.loads(_read(os.path.join(STATE, "config.json"), "{}"))
    except Exception:
        return {}


def load_key_map():
    """keys.txt: <bridge-key>=<email> per line."""
    m = {}
    for line in _read(os.path.join(STATE, "keys.txt")).splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            m[k.strip()] = v.strip()
    return m


def read_jwt(email=""):
    if email:
        tok = _read(os.path.join(STATE, "jwt", email + ".txt")).strip()
        if tok:
            return tok
    tok = os.environ.get("ZCODE_JWT", "").strip()
    return tok or _read(FALLBACK_JWT).strip()


def persona_text():
    return _read(os.path.join(STATE, "persona.txt")).strip()


def resolve_model(model):
    """alias (combo) -> (model chain, force_persona). The chain is walked in
    order with account failover inside each step: e.g. GLM-5.3 on Acc1..N,
    then the fallback GLM-5.3-Flash on Acc1..N. 'zcode/GLM-5.3' and
    claude/gpt names -> single GLM-5.3 chain."""
    cfg = load_config()
    m = (model or "").strip()
    alias = (cfg.get("aliases") or {}).get(m)
    if isinstance(alias, dict):
        chain = alias.get("models")
        if not isinstance(chain, list) or not chain:      # legacy single-model alias
            chain = [alias.get("model", DEFAULT_MODEL)]
        chain = [x.strip() for x in chain if isinstance(x, str) and x.strip()]
        return (chain or [DEFAULT_MODEL]), bool(alias.get("persona"))
    if "/" in m:
        m = m.rsplit("/", 1)[-1]
    return ([(m if m.upper().startswith("GLM") else DEFAULT_MODEL)]), False


def persona_active(force, requested="", resolved=""):
    """force (alias --persona) always wins; otherwise persona applies when
    enabled AND (models list empty = ALL, or requested/resolved name listed)."""
    if force:
        return True
    p = load_config().get("persona") or {}
    if not p.get("enabled"):
        return False
    sel = p.get("models") or []
    if not sel:
        return True
    for name in (requested, resolved):
        n = (name or "").strip()
        if n in sel:
            return True
        if "/" in n and n.rsplit("/", 1)[-1] in sel:
            return True
    return False


def persona_scope():
    p = load_config().get("persona") or {}
    if not p.get("enabled"):
        return "off"
    return ",".join(p.get("models") or []) or "ALL"


def merge_system(client_system, force_persona=False, requested="", resolved=""):
    """WAF gate: ZCode's own prompt must lead. Persona (if on) sits after it,
    then the client's own system."""
    blocks = list(SYSTEM_BLOCKS)
    if persona_active(force_persona, requested, resolved):
        txt = persona_text()
        if txt:
            blocks.append({"type": "text", "text": txt})
    if not client_system:
        return blocks
    if isinstance(client_system, str):
        client_system = [{"type": "text", "text": client_system}]
    return blocks + list(client_system)


# ---------- usage + quota ----------
def record_usage(email, model, prompt, completion):
    if not email or (not prompt and not completion):
        return
    day = time.strftime("%Y-%m-%d")
    path = os.path.join(STATE, "usage.json")
    with USAGE_LOCK:
        try:
            data = json.loads(_read(path, "{}"))
        except Exception:
            data = {}
        acct = data.setdefault(email, {})
        if acct.get("day") != day:      # daily buckets reset with the plan
            acct.clear()
            acct["day"] = day
        per = acct.setdefault(model, [0, 0])
        per[0] += int(prompt or 0)
        per[1] += int(completion or 0)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)


def quota_cache():
    try:
        return json.loads(_read(os.path.join(STATE, "balance.json"), "{}"))
    except Exception:
        return {}


def quota_exhausted(email, model):
    """429 (so 9router fails over to the next connection) when the watcher's
    server-side quota cache says this account's bucket for `model` is spent."""
    if not email:
        return False
    cache = quota_cache().get(email)
    if not cache or time.time() - cache.get("ts", 0) > 900:
        return False
    cap = "model:" + model.lower()
    seen = False
    for b in cache.get("buckets", []):
        if cap in (b.get("capabilities") or []):
            seen = True
            if b.get("remaining_units", 1) > 0:
                return False  # one live bucket (e.g. weekend 300M) = account usable
    return seen  # exhausted only if every bucket for this model is empty


def quota_payload(only=None):
    """GET /quota: per-account buckets + aggregated {name:{used,total,resetAt}}
    (the shape 9router's quota page default parser expects). only=email = that
    key's account (used by 9router per-connection attribution)."""
    cache = quota_cache()
    accounts, quotas = [], {}
    for email, rec in cache.items():
        if only and email != only:
            continue
        buckets = []
        for b in rec.get("buckets", []):
            item = {"name": b.get("show_name"), "plan": b.get("plan_id"),
                    "bucket_id": b.get("bucket_id"),
                    "used": b.get("used_units", 0), "total": b.get("total_units", 0),
                    "remaining": b.get("remaining_units", 0),
                    "resetAt": b.get("period_end")}
            buckets.append(item)
            q = quotas.setdefault(item["name"], {"used": 0, "total": 0,
                                                 "remaining": 0, "resetAt": None})
            q["used"] += item["used"]
            q["total"] += item["total"]
            q["remaining"] += item["remaining"]
            if item["resetAt"] and (q["resetAt"] is None or item["resetAt"] < q["resetAt"]):
                q["resetAt"] = item["resetAt"]
        accounts.append({"email": email, "ts": rec.get("ts"), "buckets": buckets})
    return {"ok": True, "ts": time.time(), "accounts": accounts, "quotas": quotas}


def accounts_txt():
    out = []
    for line in _read(os.path.join(STATE, "accounts.txt")).splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ":" in line:
            out.append(line.split(":", 1)[0].strip())
    return out


def ordered_accounts():
    """Unique accounts in priority order = accounts.txt order (first account wins)."""
    seen, out = set(), []
    for e in accounts_txt() + list(load_key_map().values()):
        if e and e not in seen:
            seen.add(e)
            out.append(e)
    return out


def pick_account(preferred, model, exclude=()):
    """preferred account first if its JWT is alive AND quota remains; otherwise
    the next account in priority order. None = nothing usable."""
    order = ([preferred] if preferred is not None else []) + ordered_accounts()
    for e in order:
        if not e or e in exclude:
            continue
        if read_jwt(e) and not quota_exhausted(e, model):
            return e
    return None


def bridge_event(kind, msg):
    line = "%s [%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), kind, msg)
    try:
        with open(os.path.join(STATE, "events.log"), "a") as f:
            f.write(line + "\n")
    except OSError:
        pass
    sys.stderr.write("[bridge] %s\n" % line)


def upstream_headers(email=""):
    h = dict(APP_HEADERS)
    jwt = read_jwt(email)
    h["authorization"] = "Bearer " + jwt
    h["x-api-key"] = jwt
    for k in ("x-request-id", "x-query-id", "x-zcode-trace-id", "x-session-id"):
        h[k] = str(uuid.uuid4())
    return h


def call_upstream(payload, email="", timeout=600):
    import urllib.request, urllib.error
    for attempt in range(2):
        req = urllib.request.Request(UPSTREAM, data=json.dumps(payload).encode(),
                                     headers=upstream_headers(email), method="POST")
        try:
            return urllib.request.urlopen(req, timeout=timeout), None
        except urllib.error.HTTPError as e:
            body = e.read()
            # 429 code 3009 = transient per-account concurrency blip: one retry
            if e.code == 429 and attempt == 0 and b"3009" in body:
                time.sleep(5)
                continue
            e.read = lambda _b=body: _b   # keep body re-readable for the relay
            return None, e
        except Exception as e:
            return None, e
    return None, Exception("upstream retries exhausted")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    account = ""

    def log_message(self, fmt, *args):
        sys.stderr.write("[bridge] %s\n" % (fmt % args))

    # ---------- auth ----------
    def _client_key(self):
        got = (self.headers.get("authorization") or "").strip()
        if got.lower().startswith("bearer "):
            got = got[7:].strip()
        if not got:
            got = (self.headers.get("x-api-key") or "").strip()
        if not got and "?" in self.path:
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            got = (q.get("api_key") or q.get("key") or [""])[0]
        return got

    def _auth_account(self):
        """Returns email for this request's key, or None if unauthorized.
        Empty string = open mode (no keys configured anywhere)."""
        keys = load_key_map()
        key = self._client_key()
        if keys:
            return keys.get(key)
        if API_KEYS:
            return "" if key in API_KEYS else None
        return ""

    def _send(self, status, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self):
        self._send(401, {"error": {"message": "Invalid API key", "type": "invalid_request_error",
                                   "code": "invalid_api_key"}})

    def _relay_error(self, err, raw=None):
        if raw is not None:
            data = raw.read()
            self.send_response(raw.code)
            self.send_header("content-type", raw.headers.get("content-type", "application/json"))
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._send(502, {"error": {"type": "bridge_error", "message": str(err)}})

    # ---------- routes ----------
    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/", "/health"):
            return self._send(200, {"ok": True, "upstream": "zcode-start-plan",
                                    "model": DEFAULT_MODEL, "keys": len(load_key_map()) or len(API_KEYS)})
        if path == "/quota":
            # open on localhost; a valid bridge key narrows to that key's account
            return self._send(200, quota_payload(self._auth_account()))
        if self._auth_account() is None:
            return self._unauthorized()
        if path.endswith("/models"):
            ids = sorted({DEFAULT_MODEL, "GLM-5.3-Flash"} |
                         set((load_config().get("aliases") or {}).keys()))
            self._send(200, {"object": "list", "data": [
                {"id": i, "object": "model", "owned_by": "zcode-start-plan"} for i in ids]})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        try:
            path = self.path.split("?")[0].rstrip("/")
            self.account = self._auth_account()
            if self.account is None:
                return self._unauthorized()
            if path.endswith("/messages"):
                return self._anthropic_messages()
            if path.endswith("/chat/completions"):
                return self._openai_chat()
            self._send(404, {"error": {"message": "not found"}})
        except Exception as e:
            try:
                self._send(500, {"error": {"type": "bridge_error", "message": str(e)}})
            except Exception:
                pass

    def _select_account(self, models):
        """walk the model chain: GLM-5.3 Acc1 -> Acc2 -> ... -> then the
        fallback model (e.g. GLM-5.3-Flash) across accounts. First model with
        a usable account wins and becomes self.model. 429 code 3010 only when
        the WHOLE chain is spent (then 9router's connection fallback applies).
        """
        for m in models:
            chosen = pick_account(self.account, m)
            if chosen:
                if m != models[0]:
                    bridge_event("failover", "chain: %s exhausted everywhere -> using %s"
                                 % (models[0], m))
                if self.account and chosen != self.account:
                    bridge_event("failover", "quota: %s spent for %s -> using %s"
                                 % (self.account, m, chosen))
                self.account = chosen
                self.model = m
                return True
        self._send(429, {"code": 3010,
                         "msg": "bridge: no account with quota for %s" % " -> ".join(models)})
        return False

    def _call_failover(self, payload, model):
        """send upstream; if the JWT is dead (401) rotate to the next usable
        account instead of failing the client."""
        tried, err = [], None
        while len(tried) < 4:
            email = self.account if not tried else pick_account(None, model, exclude=tried)
            if email is None or email in tried:
                break
            tried.append(email)
            resp, err = call_upstream(payload, email)
            if err is None:
                if email != self.account:
                    bridge_event("failover", "dead JWT on %s -> using %s" % (self.account, email))
                self.account = email
                return resp, None
            if getattr(err, "code", None) != 401:
                break
        return None, err

    def _call_chain(self, payload, chain, model):
        """upstream shed the model with 405/3012 (anti-abuse; e.g. a brand-new
        account hitting the flagship GLM-5.3 while its Flash bucket is fine)
        -> continue down the chain instead of relaying the error."""
        resp, err = self._call_failover(payload, model)
        while (err is not None and getattr(err, "code", None) == 405
               and chain.index(model) + 1 < len(chain)):
            model = chain[chain.index(model) + 1]
            if not self._select_account([model]):
                return resp, err
            bridge_event("failover", "upstream 3012 shed -> chain to %s (%s)"
                         % (model, self.account))
            payload["model"] = model
            resp, err = self._call_failover(payload, model)
        return resp, err

    # ---------- Anthropic ----------
    def _anthropic_messages(self):
        body = self._read_body()
        req_model = body.get("model")
        chain, force_p = resolve_model(req_model)
        if not self._select_account(chain):
            return
        model = self.model
        body["model"] = model
        body["system"] = merge_system(body.get("system"), force_p, req_model, model)
        resp, err = self._call_chain(body, chain, model)
        if err is not None:
            return self._relay_error(err, raw=err if hasattr(err, "read") else None)
        if body.get("stream"):
            return self._stream_upstream(resp, model)
        data = resp.read()
        try:
            j = json.loads(data)
            u = j.get("usage") or {}
            record_usage(self.account, model, u.get("input_tokens"), u.get("output_tokens"))
        except Exception:
            pass
        self.send_response(getattr(resp, "status", 200))
        self.send_header("content-type", resp.headers.get("content-type", "application/json"))
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream_upstream(self, resp, model):
        """SSE passthrough with a usage-tee (frames are forwarded untouched)."""
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        usage, buf = [0, 0], b""
        try:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
                if not self.account:
                    continue
                buf += chunk
                while b"\n\n" in buf:
                    frame, buf = buf.split(b"\n\n", 1)
                    for line in frame.split(b"\n"):
                        if not line.startswith(b"data:"):
                            continue
                        try:
                            ev = json.loads(line[5:])
                        except Exception:
                            continue
                        if ev.get("type") == "message_start":
                            usage[0] = (ev.get("message") or {}).get("usage", {}).get("input_tokens", 0)
                        elif ev.get("type") == "message_delta":
                            o = (ev.get("usage") or {}).get("output_tokens")
                            if o is not None:
                                usage[1] = o
        finally:
            self.close_connection = True
            resp.close()
            record_usage(self.account, model, usage[0], usage[1])

    # ---------- OpenAI ----------
    def _read_body(self):
        n = int(self.headers.get("content-length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _openai_chat(self):
        req = self._read_body()
        payload = self._oa_to_anthropic(req)
        chain, force_p = resolve_model(req.get("model"))
        if not self._select_account(chain):
            return
        model = self.model
        payload["model"] = model
        payload["system"] = merge_system(payload.get("system"), force_p,
                                         req.get("model"), model)
        resp, err = self._call_chain(payload, chain, model)
        if err is not None:
            return self._relay_error(err, raw=err if hasattr(err, "read") else None)
        if payload.get("stream"):
            return self._oa_stream(resp, req, model)
        data = json.loads(resp.read())
        u = data.get("usage") or {}
        record_usage(self.account, model, u.get("input_tokens"), u.get("output_tokens"))
        self._send(200, self._anthropic_to_oa(data, req))

    @staticmethod
    def _oa_to_anthropic(req):
        sys_text, msgs, tools = None, [], None
        for m in req.get("messages", []):
            role, content = m.get("role"), m.get("content")
            if role == "system":
                sys_text = content if sys_text is None else sys_text + "\n\n" + str(content)
                continue
            if role == "tool":
                block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id"),
                         "content": content if isinstance(content, str) else json.dumps(content)}
                if msgs and msgs[-1]["role"] == "user" and isinstance(msgs[-1]["content"], list):
                    msgs[-1]["content"].append(block)
                else:
                    msgs.append({"role": "user", "content": [block]})
                continue
            blocks = []
            if isinstance(content, str) and content:
                blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                for c in content:
                    if c.get("type") == "text":
                        blocks.append({"type": "text", "text": c.get("text", "")})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc.get("id") or "toolu_" + uuid.uuid4().hex[:16],
                               "name": fn.get("name", ""), "input": args})
            if blocks:
                msgs.append({"role": role, "content": blocks})
        if req.get("tools"):
            tools = [{"name": t.get("function", {}).get("name"),
                      "description": t.get("function", {}).get("description", ""),
                      "input_schema": t.get("function", {}).get("parameters") or {"type": "object"}}
                     for t in req["tools"] if t.get("type") == "function"]
        payload = {"model": req.get("model"), "max_tokens": req.get("max_tokens") or 4096,
                   "messages": msgs or [{"role": "user", "content": "hi"}],
                   "system": sys_text or "You are a helpful assistant."}
        if tools:
            payload["tools"] = tools
        tc = req.get("tool_choice")
        if tc == "auto":
            payload["tool_choice"] = {"type": "auto"}
        elif tc == "none":
            payload["tool_choice"] = {"type": "none"}
        elif isinstance(tc, dict) and tc.get("function", {}).get("name"):
            payload["tool_choice"] = {"type": "tool", "name": tc["function"]["name"]}
        if req.get("stream"):
            payload["stream"] = True
        return payload

    @staticmethod
    def _finish_reason(stop):
        return {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
                "tool_use": "tool_calls", "refusal": "content_filter"}.get(stop, "stop")

    def _anthropic_to_oa(self, data, req):
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        tool_calls = [{"id": b["id"], "type": "function",
                       "function": {"name": b.get("name", ""), "arguments": json.dumps(b.get("input") or {})}}
                      for b in data.get("content", []) if b.get("type") == "tool_use"]
        msg = {"role": "assistant", "content": text or None}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        u = data.get("usage", {})
        return {"id": "chatcmpl-" + (data.get("id") or uuid.uuid4().hex),
                "object": "chat.completion", "created": int(time.time()),
                "model": req.get("model") or DEFAULT_MODEL,
                "choices": [{"index": 0, "message": msg,
                             "finish_reason": self._finish_reason(data.get("stop_reason"))}],
                "usage": {"prompt_tokens": u.get("input_tokens", 0),
                          "completion_tokens": u.get("output_tokens", 0),
                          "total_tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0)}}

    def _oa_stream(self, resp, req, model):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("connection", "close")
        self.end_headers()
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())
        tool_idx, finish = -1, "stop"
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        def chunk(delta, fr=None, include_usage=False):
            c = {"id": cid, "object": "chat.completion.chunk", "created": created,
                 "model": req.get("model") or model,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": fr}]}
            if include_usage:
                c["usage"] = usage
            self.wfile.write(b"data: " + json.dumps(c).encode() + b"\n\n")
            self.wfile.flush()

        chunk({"role": "assistant", "content": ""})
        buf = b""
        try:
            while True:
                data = resp.read(4096)
                if not data:
                    break
                buf += data
                while b"\n\n" in buf:
                    frame, buf = buf.split(b"\n\n", 1)
                    for line in frame.split(b"\n"):
                        line = line.strip()
                        if not line.startswith(b"data:"):
                            continue
                        try:
                            ev = json.loads(line[5:])
                        except Exception:
                            continue
                        t = ev.get("type")
                        if t == "content_block_start" and ev.get("content_block", {}).get("type") == "tool_use":
                            tool_idx += 1
                            b0 = ev["content_block"]
                            chunk({"tool_calls": [{"index": tool_idx, "id": b0.get("id"),
                                                   "type": "function", "function": {
                                                       "name": b0.get("name", ""), "arguments": ""}}]})
                        elif t == "content_block_delta":
                            d = ev.get("delta", {})
                            if d.get("type") == "text_delta":
                                chunk({"content": d.get("text", "")})
                            elif d.get("type") == "input_json_delta" and tool_idx >= 0:
                                chunk({"tool_calls": [{"index": tool_idx,
                                                       "function": {"arguments": d.get("partial_json", "")}}]})
                        elif t == "message_start":
                            usage["prompt_tokens"] = (ev.get("message") or {}).get("usage", {}).get("input_tokens", 0)
                        elif t == "message_delta":
                            finish = self._finish_reason((ev.get("delta") or {}).get("stop_reason"))
                            o = (ev.get("usage") or {}).get("output_tokens")
                            if o is not None:
                                usage["completion_tokens"] = o
                        elif t == "error":
                            chunk({"content": "\n[upstream error: %s]" % json.dumps(ev.get("error"))})
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
            record_usage(self.account, model, usage["prompt_tokens"], usage["completion_tokens"])
            chunk({}, fr=finish, include_usage=True)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        finally:
            self.close_connection = True
            resp.close()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="ZCode Start Plan -> OpenAI/Anthropic bridge")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    if not read_jwt():
        sys.exit("no JWT: run `python3 scripts/zcode.py mint --all` first")
    keys = load_key_map()
    print("bridge on http://%s:%d  model=%s  keys=%d  persona=%s"
          % (args.host, args.port, DEFAULT_MODEL, len(keys) or len(API_KEYS),
             persona_scope()))
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
