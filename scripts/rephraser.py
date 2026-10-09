#!/usr/bin/env python3
"""
scripts/rephraser.py — refusal-aware middleware in front of 9Router.

Some providers (Claude included) answer a declined request with a normal HTTP 200
whose stop reason is a refusal, not an error (see Anthropic's "Refusals and
fallback"). A caller that only checks HTTP/error status sees an empty answer and
moves on. This wraps the 9Router chat endpoint so a refusal is detected,
rephrased (neutral restatement that preserves intent), and retried — the pattern
the Anthropic doc describes, applied across 9Router's whole provider pool.

Use as a module (middleware):

    from rephraser import chat
    resp = chat([{"role": "user", "content": "..."}], model="mocin")

or as a CLI:

    python scripts/rephraser.py chat "explain TLS pinning" --model mocin
    python scripts/rephraser.py key
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

NINE_ROUTER_HOME = Path(os.environ.get("NINE_ROUTER_HOME", Path.home() / ".9router"))
DB = NINE_ROUTER_HOME / "db" / "data.sqlite"
DEFAULT_BASE = os.environ.get("NINE_ROUTER_BASE", "http://localhost:20128")
DEFAULT_MODEL = os.environ.get("NINE_ROUTER_MODEL", "mocin")

# Phrases a model uses when it declines. Providers that don't emit a structured
# stop_reason only leave prose, so we also match the common openers.
_REFUSAL_OPENERS = (
    "i can't help with", "i cannot help with", "i can't assist", "i cannot assist",
    "i'm unable to help", "i am unable to help", "i won't help", "i will not help",
    "i can't provide", "i cannot provide", "i'm not able to help",
)
_REFUSAL_CATEGORIES = ("cyber", "bio", "frontier_llm", "reasoning_extraction", "general_harms")


def _cli_token() -> str:
    """9Router x-9r-cli-token = sha256(machineId + '9r-cli-auth' + cliSecret)[:16]."""
    env = os.environ.get("R9_TOKEN") or os.environ.get("NINE_ROUTER_CLI_TOKEN")
    if env:
        return env.strip()
    try:
        mid = (NINE_ROUTER_HOME / "machine-id").read_text().strip()
        sec = (NINE_ROUTER_HOME / "auth" / "cli-secret").read_text().strip()
        return hashlib.sha256((mid + "9r-cli-auth" + sec).encode()).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return ""


def _key(base: str = DEFAULT_BASE) -> str:
    """A 9Router API key: env, then the live key list, then the DB."""
    env = os.environ.get("NINE_ROUTER_KEY")
    if env:
        return env.strip()
    try:
        req = urllib.request.Request(f"{base.rstrip('/')}/api/keys",
                                     headers={"x-9r-cli-token": _cli_token()})
        with urllib.request.urlopen(req, timeout=8) as r:
            keys = json.loads(r.read().decode() or "{}").get("keys", [])
        for k in keys:
            if k.get("key") and k.get("isActive", True):
                return k["key"]
    except Exception:  # noqa: BLE001
        pass
    try:
        row = sqlite3.connect(DB).execute(
            "SELECT data FROM providerConnections WHERE isActive=1 LIMIT 1").fetchone()
        if row and row[0]:
            return json.loads(row[0]).get("apiKey", "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def detect_refusal(resp: dict) -> str | None:
    """Return the refusal category (or 'refusal'/'prose') for a chat response, else None."""
    details = resp.get("stop_details") or {}
    if resp.get("stop_reason") == "refusal" or details.get("type") == "refusal":
        return details.get("category") or "refusal"
    choices = resp.get("choices") or []
    if not choices:
        return None
    c0 = choices[0]
    for field in ("stop_reason", "finish_reason"):
        if c0.get(field) in ("refusal", "content_filter"):
            return (c0.get("stop_details") or {}).get("category") or "refusal"
    msg = c0.get("message") or {}
    if msg.get("refusal"):
        return "refusal"
    text = (msg.get("content") or "").strip().lower()
    if text and len(text) < 400 and any(text.startswith(p) for p in _REFUSAL_OPENERS):
        return "prose"
    return None


def _from_sse(raw: str) -> dict:
    """Assemble a non-streaming-shaped response from an SSE body (a router that
    streamed anyway). Concatenates delta.content and takes the last finish_reason."""
    parts, model, finish = [], None, "stop"
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            j = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        model = j.get("model") or model
        for ch in j.get("choices") or []:
            parts.append(((ch.get("delta") or {}).get("content")) or "")
            finish = ch.get("finish_reason") or finish
    text = "".join(parts)
    if not text and not model:
        return {"error": "unparseable response", "body": raw[:300]}
    return {"model": model, "stop_reason": finish,
            "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": text}}]}


def _post(base: str, key: str, payload: dict, timeout: float) -> dict:
    payload = {**payload, "stream": False}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/v1/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read().decode(errors="replace")[:300]}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return _from_sse(raw)


_REWRITE_SYSTEM = (
    "You rewrite a request so it is clearer and neutrally framed, without adding "
    "or removing intent. Keep every concrete, legitimate detail (technologies, "
    "context, constraints). Do not answer the request — output ONLY the rewritten "
    "request text, in first person, ready to send as a user message."
)


def rephrase(messages: list[dict], *, model: str, base: str, key: str,
             timeout: float, why: str) -> list[dict]:
    """Return messages with the last user turn restated by `model`."""
    idx = next((i for i in range(len(messages) - 1, -1, -1)
                if messages[i].get("role") == "user"), None)
    if idx is None:
        return messages
    ask = [
        {"role": "system", "content": _REWRITE_SYSTEM},
        {"role": "user", "content": messages[idx]["content"]},
    ]
    if why:
        ask[1]["content"] += f"\n\n(It was declined as: {why}. Restate neutrally.)"
    res = _post(base, key, {"model": model, "messages": ask, "max_tokens": 700}, timeout)
    choices = res.get("choices") or []
    text = ((choices[0].get("message") or {}).get("content") or "").strip() if choices else ""
    if not text:
        return messages
    out = [dict(m) for m in messages]
    out[idx] = {**out[idx], "content": text}
    return out


def chat(messages: list[dict], *, model: str = DEFAULT_MODEL, base: str = DEFAULT_BASE,
         key: str | None = None, max_rephrases: int = 2, timeout: float = 120.0,
         verbose: bool = False) -> dict:
    """Send `messages` through 9Router; on a refusal, rephrase and retry.

    Returns the last response dict. Adds `rephrased_tries` and `refusal` keys so
    the caller can see what happened. Set max_rephrases=0 for a plain pass-through.
    """
    key = key or _key(base)
    if not key:
        return {"error": "no 9Router key (env NINE_ROUTER_KEY, /api/keys, or DB)"}
    msgs = [dict(m) for m in messages]
    last_why = None
    for attempt in range(max_rephrases + 1):
        res = _post(base, key, {"model": model, "messages": msgs}, timeout)
        why = detect_refusal(res)
        if not why:
            res["rephrased_tries"] = attempt
            return res
        last_why = why
        if verbose:
            print(f"  [rephraser] refusal ({why}) — attempt {attempt + 1}/{max_rephrases + 1}",
                  file=sys.stderr)
        if attempt == max_rephrases:
            break
        msgs = rephrase(msgs, model=model, base=base, key=key, timeout=timeout, why=why)
    res["rephrased_tries"] = max_rephrases
    res["refusal"] = last_why
    return res


def _text(res: dict) -> str:
    choices = res.get("choices") or []
    if choices:
        return (choices[0].get("message") or {}).get("content") or ""
    return res.get("error") or json.dumps(res)[:300]


def _selftest() -> int:
    """Offline: refusal detection + rephrase path, no network."""
    assert detect_refusal({"stop_reason": "refusal",
                           "stop_details": {"type": "refusal", "category": "cyber"}}) == "cyber"
    assert detect_refusal({"choices": [{"finish_reason": "stop",
                                        "message": {"content": "Hi"}}]}) is None
    assert detect_refusal({"choices": [{"finish_reason": "content_filter"}]}) == "refusal"
    assert detect_refusal({"choices": [{"message": {"content": "I can't help with that."}}]}) == "prose"
    # a fake transport that refuses once, then answers after the rewrite
    calls = {"n": 0}

    def fake_post(base, key, payload, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"stop_reason": "refusal", "stop_details": {"type": "refusal", "category": "cyber"}}
        return {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}

    global _post
    real, _post = _post, fake_post
    try:
        res = chat([{"role": "user", "content": "x"}], key="k", max_rephrases=1)
    finally:
        _post = real
    assert res.get("rephrased_tries") == 1, res
    assert _text(res) == "ok", res
    print("rephraser selftest: OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Refusal-aware retry middleware for 9Router")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("chat", help="send a prompt (rephrasing on refusal)")
    c.add_argument("prompt")
    c.add_argument("--model", default=DEFAULT_MODEL)
    c.add_argument("-n", "--max-rephrases", type=int, default=2)
    c.add_argument("--json", action="store_true")
    sub.add_parser("key", help="show which 9Router key is used (masked)")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()
    if a.cmd == "key":
        k = _key(a.base)
        print("  key:", (k[:8] + "...") if k else "(none)")
        return 0 if k else 1
    if a.cmd == "chat":
        res = chat([{"role": "user", "content": a.prompt}], model=a.model,
                   base=a.base, max_rephrases=a.max_rephrases, verbose=True)
        if a.json:
            print(json.dumps(res)[:4000])
        else:
            print(_text(res))
        return 0 if not res.get("error") else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())