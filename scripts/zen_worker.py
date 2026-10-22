#!/usr/bin/env python3
"""Zen worker: run OpenCode's free models for jobs that fit one prompt -> one answer.

WHY A SUBPROCESS AND NOT A MODEL IN THE CHAIN
---------------------------------------------
OpenCode's free tier cannot be reached as an OpenAI-compatible endpoint. The
gateway answers 403 FreeTierError to any request that did not come from the
OpenCode client, and that refusal is a *shape* check, not an IP check, so proxy
rotation cannot clear it (see decolua/9router#4997 and #4998: only 429/5xx are
retryable across pools). Measured on this box 2026-10-22: direct POST to
https://opencode.ai/zen/v1/chat/completions -> 403 in 0.65s with no key, and
`opencode run -m opencode/glm-5.3-flash-free "..."` -> 200 in 37s with a real
tool call. So the only supported path is the CLI.

WHAT THIS IS FOR
----------------
Work that is one prompt in, one answer out, where a wrong answer costs a retry
and never a wrong submission:
  * triage   - is this finding real, or a zero-result line dressed up as one?
  * scope    - turn a rendered brief into a host list
  * report   - prose for the "Model-written report" section

WHAT THIS IS NOT FOR
--------------------
The ReAct hunt loop. That needs a model that returns tool_calls over HTTP on
every step; a CLI subprocess per step would add process startup, lose the
conversation state the loop depends on, and fail differently under load.

SAFETY
------
Runs with the prompt on stdin, in an empty temp directory, and with `--auto`
deliberately NOT passed, so the worker cannot wander the filesystem or approve
its own actions. A non-zero exit, a timeout, or an empty answer returns None and
the caller keeps its existing behaviour: this module can only ever *improve* a
decision, never block one.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Ordered by measured latency first, then by context window (largest first).
#
# The list is derived from https://models.opencode.ai/api.json -- the catalog the
# CLI itself fetches at runtime -- filtered to models whose declared cost is 0
# for both input and output. `opencode models` only prints a subset (10 of the
# 46 free ones), so the catalog is the honest source: measured 2026-10-22, the
# zen provider declares 130 models and 46 of them are free.
#
# Only the first three have been measured end-to-end (tool call confirmed); the
# rest are ordered by context so a fallback still gets the biggest window
# available. A model that answers but cannot call a tool is caught by the
# caller's validation, not by this list.
ZEN_MODELS = (
    "opencode/mimo-v2.6-flash-free",
    "opencode/glm-5.3-flash-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/space-bunny-free",
    "opencode/beam-free",
    "opencode/cat-jam-free",
    "opencode/exo-free",
    "opencode/fledge-alpha-free",
    "opencode/muse-spark-1.2-contributor-free",
    "opencode/muse-spark-1.3-contributor-free",
    "opencode/nemotron-3.5-super-vl-free",
    "opencode/mimo-v2-pro-free",
    "opencode/minimax-m3.1-flash-free",
    "opencode/longcat-2.0-free",
    "opencode/longcat-2.5-preview-free",
    "opencode/x-preview-f-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/step-5-preview-free",
    "opencode/kimi-k2.5-free",
    "opencode/flint-40b-free",
    "opencode/ll-alpha-free",
    "opencode/mimo-v2-flash-free",
    "opencode/qwen3.6-plus-free",
    "opencode/mimo-v2-omni-free",
    "opencode/ling-3.0-flash-fin-free",
    "opencode/ling-3.0-flash-free",
    "opencode/ling-3.0-tiny-free",
    "opencode/ling-3.1-flash-free",
    "opencode/ling-2.6-flash-free",
    "opencode/ring-2.6-1t-free",
    "opencode/grok-code",
    "opencode/hy3-preview-free",
    "opencode/north-mini-code-free",
    "opencode/laguna-s-2.1-free",
    "opencode/glm-4.7-free",
    "opencode/glm-5-free",
    "opencode/minimax-m2.1-free",
    "opencode/minimax-m2.5-free",
    "opencode/nemotron-3-super-free",
    "opencode/deepseek-v4-flash-free",
    "opencode/big-pickle",
    "opencode/deepseek-v4.1-flash-free",
    "opencode/mimo-v2.5-free",
    "opencode/minimax-m3-free",
    "opencode/hy3-free",
    "opencode/trinity-large-preview-free",
)

DEFAULT_TIMEOUT = 120


def _opencode_bin() -> str | None:
    """Resolve the CLI. An explicit OPENCODE_BIN is FINAL, never a hint.

    Falling through to PATH when the override is wrong made the worker
    impossible to disable: setting OPENCODE_BIN=/nonexistent still ran the real
    binary and spent quota. An operator who sets an override means it -- if it
    does not exist, the worker is off.
    """
    env = os.environ.get("OPENCODE_BIN")
    if env:
        return env if Path(env).is_file() else None
    return shutil.which("opencode")


def available() -> bool:
    """True if the CLI exists. Cheap; does not spend a request."""
    return _opencode_bin() is not None


def run(prompt: str, *, model: str = "", timeout: int = DEFAULT_TIMEOUT,
        models: tuple[str, ...] = ()) -> str | None:
    """One prompt in, one answer out. Returns None on any failure.

    Failure is never raised: every caller treats None as "no help available"
    and falls back to the deterministic path it already had.
    """
    binary = _opencode_bin()
    if not binary:
        return None
    chain = (model,) if model else (models or ZEN_MODELS)
    for m in chain:
        out = _run_one(binary, m, prompt, timeout)
        if out:
            return out
    return None


def _run_one(binary: str, model: str, prompt: str, timeout: int) -> str | None:
    # Empty cwd: the worker has nothing to read, so a prompt-injected "read
    # /root/.ssh" has nothing to reach. Files it could see are files we would
    # have had to hand it, and we never do.
    with tempfile.TemporaryDirectory(prefix="zen-worker-") as tmp:
        t0 = time.time()
        try:
            p = subprocess.run(
                [binary, "run", "-m", model, prompt],
                cwd=tmp, capture_output=True, text=True, timeout=timeout,
                env={**os.environ, "NO_COLOR": "1"},
            )
        except subprocess.TimeoutExpired:
            print(f"[zen] {model} timed out after {timeout}s", file=sys.stderr, flush=True)
            return None
        except OSError as exc:
            print(f"[zen] {model} could not start: {exc}", file=sys.stderr, flush=True)
            return None
        dt = time.time() - t0
        if p.returncode != 0:
            err = (p.stderr or "").strip().splitlines()
            print(f"[zen] {model} rc={p.returncode} in {dt:.0f}s: "
                  f"{err[-1] if err else 'no stderr'}", file=sys.stderr, flush=True)
            return None
        text = (p.stdout or "").strip()
        if not text:
            print(f"[zen] {model} returned nothing in {dt:.0f}s", file=sys.stderr, flush=True)
            return None
        return text


# ── job: triage a candidate finding ──────────────────────────────────────────

TRIAGE_PROMPT = """You are triaging one automated security-scanner result before it is
submitted to a bug bounty program. Be strict: a false positive costs the operator
their reputation, and a missed real bug costs them money.

FINDING
  tool:      {tool}
  severity:  {severity}
  url:       {url}
  claim:     {text}
  evidence:  {evidence}

TARGET PROGRAM: {program}
TARGET HOST:    {target}

Answer in EXACTLY this format, no preamble:

VERDICT: REAL | FALSE | UNCERTAIN
CONFIDENCE: 0-100
REASON: one sentence, naming the specific evidence that decided it
"""


def triage_finding(finding: dict, *, target: str = "", program: str = "",
                   timeout: int = DEFAULT_TIMEOUT) -> dict | None:
    """Ask a Zen model whether a finding is real.

    Returns {"verdict","confidence","reason"} or None if no model answered.
    The verdict is advisory: callers must still gate anything they submit.
    """
    prompt = TRIAGE_PROMPT.format(
        tool=finding.get("tool") or finding.get("vuln_class") or "?",
        severity=finding.get("severity", "?"),
        url=finding.get("url", "") or "(none)",
        text=(finding.get("text") or finding.get("title") or "")[:800],
        evidence=(finding.get("evidence") or "")[:800],
        program=program or finding.get("program", "") or "(unknown)",
        target=target or "(unknown)",
    )
    raw = run(prompt, timeout=timeout)
    if not raw:
        return None
    return parse_triage(raw)


def parse_triage(raw: str) -> dict | None:
    """Parse the strict answer format. Unknown verdicts are dropped, not guessed."""
    out = {"verdict": "", "confidence": 0, "reason": ""}
    for line in raw.splitlines():
        s = line.strip()
        up = s.upper()
        if up.startswith("VERDICT:"):
            v = s.split(":", 1)[1].strip().upper()
            for cand in ("REAL", "FALSE", "UNCERTAIN"):
                if cand in v:
                    out["verdict"] = cand
                    break
        elif up.startswith("CONFIDENCE:"):
            digits = "".join(ch for ch in s.split(":", 1)[1] if ch.isdigit())
            if digits:
                out["confidence"] = max(0, min(100, int(digits[:3])))
        elif up.startswith("REASON:"):
            out["reason"] = s.split(":", 1)[1].strip()[:300]
    return out if out["verdict"] else None


# ── job: extract hosts from a rendered scope brief ───────────────────────────

SCOPE_PROMPT = """Extract the security-program scope from the page text below.

Return ONLY JSON, no prose:
{{"in_scope": ["host or pattern", ...], "out_of_scope": ["host or pattern", ...]}}

Rules:
- Hosts and wildcard patterns only (example.com, *.example.com). No URLs, no
  paths, no app-store links, no prose.
- Keep templated entries verbatim (e.g. tenant-###.example.com); do not invent
  a replacement.
- An entry belongs to out_of_scope ONLY if it sits under an out-of-scope
  heading. Hosts merely mentioned in prose are not exclusions.
- If the page is a sign-in screen, return {{"in_scope": [], "out_of_scope": [],
  "login_wall": true}}.

PAGE TEXT:
{page}
"""


def extract_scope(page_text: str, *, timeout: int = DEFAULT_TIMEOUT) -> dict | None:
    """Fallback extractor for briefs the regex parser cannot read."""
    raw = run(SCOPE_PROMPT.format(page=(page_text or "")[:12000]), timeout=timeout)
    if not raw:
        return None
    return parse_scope_json(raw)


def parse_scope_json(raw: str) -> dict | None:
    """Pull the first JSON object out of a model answer; None if it is not one."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        d = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    def _clean(v):
        if not isinstance(v, list):
            return []
        return [str(x).strip().lower() for x in v if isinstance(x, str) and x.strip()]
    out = {"in_scope": _clean(d.get("in_scope")),
           "out_of_scope": _clean(d.get("out_of_scope"))}
    if d.get("login_wall"):
        out["login_wall"] = True
    return out if (out["in_scope"] or out["out_of_scope"] or out.get("login_wall")) else None


if __name__ == "__main__":
    if not available():
        sys.exit("opencode CLI not found (set OPENCODE_BIN)")
    job = sys.argv[1] if len(sys.argv) > 1 else "ping"
    if job == "ping":
        print(run("Reply with exactly: OK", timeout=90))
    elif job == "triage":
        demo = {"tool": "run_cve_intel", "severity": "HIGH", "url": "",
                "text": "sources: research_db=no legacy_db=yes | range matches: 0"}
        print(json.dumps(triage_finding(demo), indent=1))
    else:
        sys.exit(f"unknown job {job!r} (ping|triage)")
