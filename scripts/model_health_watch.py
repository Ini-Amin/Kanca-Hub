#!/usr/bin/env python3
"""Probe every model the bounty chain can use, and record which ones actually work.

WHY THIS EXISTS: the pipeline's model chain had no health signal at all. The
only watcher in the repo, thk_quota_watchdog.sh, counts zcode account buckets -
a different thing entirely, and it does not cover the 9router models the hunt
actually calls. The cost of the blind spot is measured: ocz/mimo-v2.6-flash-free
answers HTTP 200 in 12.7s but emits zero tool_calls, and Zcode/GLM-5.3 does not
answer at all. Both sat in the chain as if healthy. An agent handed a model that
200s without calling tools burns its entire step budget on
"(LLM produced no tool call)" and reports nothing.

"Healthy" here means exactly two things, because those are the two ways this
loop fails:
  1. the request completes inside --timeout
  2. the reply contains a tool_calls entry

A 200 with prose instead of a tool call is recorded as UNHEALTHY, not as a
success: to a ReAct loop it is indistinguishable from a dead endpoint, and it
costs the same wall clock.

Writes /root/.config/auto-freecf/model-health.json and exits 0 if at least one
model is healthy, 1 if none are (so systemd marks the unit failed and the
supervisor loop backs off instead of burning a hunt on a dead chain).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

STATE_DIR = Path(os.environ.get("AUTO_FREECF_STATE", "/root/.config/auto-freecf"))
OUT = STATE_DIR / "model-health.json"

# A tool-calling probe: the exact shape the ReAct loop depends on. Asking for a
# completion without tools would pass models that cannot call them at all.
TOOLS = [{
    "type": "function",
    "function": {
        "name": "run_recon",
        "description": "Run passive and active recon against an authorised target.",
        "parameters": {
            "type": "object",
            "properties": {"domain": {"type": "string", "description": "target host"}},
            "required": [],
        },
    },
}]

MESSAGES = [
    {"role": "system",
     "content": "You are a recon agent operating inside an authorised bug-bounty "
                "program. Use the provided tool."},
    {"role": "user",
     "content": "Target example.com is in scope. Call run_recon on it."},
]


def _decode(text: str) -> dict:
    """Parse a completion body that may be JSON, JSON with an SSE trailer, or SSE.

    9router answers a NON-streaming request with `content-type: text/event-stream`
    and a body that is a JSON object followed by `data: [DONE]`. requests'
    .json() raises "Extra data: line 1 column N" on that - a healthy reply that
    reads like a malformed one. `raw_decode` reads the leading object and
    ignores the trailer.

    The SSE branch must keep tool_calls from the last frame. Concatenating only
    delta.content (as the sibling decoder in agent.py does) silently turns a
    model that DID call a tool into one that appears not to have.
    """
    text = (text or "").strip()
    if not text:
        raise RuntimeError("empty response body")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    if text.startswith("data:"):
        parts: list[str] = []
        last: dict | None = None
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                obj = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            last = obj
            for ch in obj.get("choices") or []:
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    parts.append(delta["content"])
                msg = ch.get("message") or {}
                if msg.get("content"):
                    parts.append(msg["content"])
        if last is not None:
            merged = dict(last)
            merged["choices"] = [{"message": {
                "content": "".join(parts),
                # Preserved, not dropped: a tool call carried only in the last
                # SSE frame is still a tool call.
                "tool_calls": ((last.get("choices") or [{}])[-1].get("message")
                               or {}).get("tool_calls") or [],
            }}]
            return merged
        raise RuntimeError("SSE response carried no content chunks")

    return json.JSONDecoder().raw_decode(text)[0]


def probe(model: str, base: str, key: str, timeout: float) -> dict:
    t0 = time.time()
    rec = {"model": model, "ts": int(time.time())}
    try:
        r = requests.post(
            base + "/chat/completions",
            headers={"Authorization": "Bearer " + key},
            json={"model": model, "messages": MESSAGES, "tools": TOOLS,
                  "tool_choice": "auto", "max_tokens": 120},
            timeout=timeout,
        )
        rec["http"] = r.status_code
        d = _decode(r.text)
        if "choices" not in d and isinstance(d.get("data"), dict):
            d = d["data"]
        msg = (d.get("choices") or [{}])[0].get("message") or {}
        calls = msg.get("tool_calls") or []
        rec["tool_calls"] = len(calls)
        rec["content"] = (msg.get("content") or "")[:80]
        rec["secs"] = round(time.time() - t0, 1)
        # Both conditions, not just the status code.
        rec["healthy"] = (r.status_code == 200) and bool(calls)
        rec["reason"] = "ok" if rec["healthy"] else (
            "no tool_calls" if r.status_code == 200 else "http %s" % r.status_code)
    except requests.Timeout:
        rec.update(healthy=False, reason="timeout after %.0fs" % timeout,
                   secs=round(time.time() - t0, 1))
    except Exception as e:  # noqa: BLE001 - any failure is an unhealthy model
        rec.update(healthy=False, reason="%s: %s" % (type(e).__name__, str(e)[:60]),
                   secs=round(time.time() - t0, 1))
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--models", default="",
                    help="comma list; default = $BOUNTY_MODEL_FALLBACKS + tiers")
    a = ap.parse_args()

    base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    key = os.environ.get("OPENAI_API_KEY", "")
    if not base or not key:
        print("model-health: OPENAI_BASE_URL / OPENAI_API_KEY not set "
              "(source the env file, e.g. /root/abh.env)")
        return 2

    chain = [m.strip() for m in a.models.split(",") if m.strip()]
    if not chain:
        for var in ("BOUNTY_MODEL_FALLBACKS",):
            chain += [m.strip() for m in os.environ.get(var, "").split(",") if m.strip()]
        for var in ("ABH_MODEL_STRONG", "ABH_MODEL_STANDARD", "ABH_MODEL_CHEAP"):
            v = os.environ.get(var, "").strip()
            if v:
                chain.append(v)
    # de-dup, keep order
    seen, models = set(), []
    for m in chain:
        if m not in seen:
            seen.add(m)
            models.append(m)

    print("model-health: probing %d models against %s" % (len(models), base))
    results = [probe(m, base, key, a.timeout) for m in models]
    for r in results:
        flag = "HEALTHY " if r.get("healthy") else "UNHEALTHY"
        print("  %-30s %-10s %6ss  %s" % (
            r["model"], flag, r.get("secs", "-"), r.get("reason", "")))

    healthy = [r["model"] for r in results if r.get("healthy")]
    doc = {"ts": int(time.time()), "base": base, "models": results,
           "healthy": healthy, "count": len(results),
           "any_healthy": bool(healthy)}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2))
    tmp.replace(OUT)  # atomic: a reader never sees a half-written file
    print("model-health: %d/%d healthy -> %s" % (len(healthy), len(results), OUT))

    if not healthy:
        print("model-health: NO usable model - the hunt loop will back off")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
