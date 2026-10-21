#!/usr/bin/env python3
"""Role-split phases for the ABH hunt loop.

One ReAct loop doing recon, judgement and report lets the same context drift
and grades its own candidates. This module runs the SAME agent.py engine once
per role (recon -> analyze -> report) with a per-role model and a per-role
brief injected through env vars the agent already inherits, so the engine
itself stays unaware of the split.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _default_abh() -> Path:
    env_p = os.environ.get("ABH_DIR")
    if env_p:
        return Path(env_p)
    local_p = HERE.parent.parent / "Agentic-Bug-Hunter"
    if (local_p / "bughunter" / "agent.py").is_file():
        return local_p
    return Path("/root/Agentic-Bug-Hunter")


ABH = _default_abh()
AGENT = ABH / "bughunter" / "agent.py"
PHASE_LOG = "phase-log.jsonl"

ROLES: dict[str, dict] = {
    "recon": {
        "name": "recon",
        "model_env_key": "ABH_MODEL_RECON",
        "step_cap": 12,
        "system_addendum": (
            "ROLE: recon. Enumerate the attack surface only: subdomains, live "
            "hosts, exposed endpoints, technologies. Do NOT judge severity, do "
            "NOT draft findings, do NOT write reports. Output a structured "
            "inventory of what exists."
        ),
    },
    "analyze": {
        "name": "analyze",
        "model_env_key": "ABH_MODEL_ANALYZE",
        "step_cap": 15,
        "system_addendum": (
            "ROLE: analyze. You are given recon output in ABH_INPUT. Pick the "
            "most promising candidates and actively test them. Judge what is a "
            "real finding and what is noise. Do NOT write the final report "
            "body; output validated findings with evidence."
        ),
    },
    "report": {
        "name": "report",
        "model_env_key": "ABH_MODEL_REPORT",
        "step_cap": 6,
        "system_addendum": (
            "ROLE: report. You are given findings in ABH_INPUT. Write the "
            "submission-ready report body for each finding: impact, steps to "
            "reproduce, evidence. Do NOT invent findings that are not present "
            "in the input."
        ),
    },
}

_suffix_support: bool | None = None


def agent_has_session_suffix() -> bool:
    """True if agent.py accepts --session-suffix (probed once per process)."""
    global _suffix_support
    if _suffix_support is None:
        try:
            _suffix_support = "--session-suffix" in AGENT.read_text(encoding="utf-8", errors="replace")
        except OSError:
            _suffix_support = False
    return _suffix_support


def resolve(role: str, default: str = "") -> str:
    """Model for a role: env override, else caller default, else tier default."""
    r = ROLES[role]
    m = os.environ.get(r["model_env_key"], "").strip()
    if m:
        return m
    if role == "recon":
        return os.environ.get("ABH_MODEL_CHEAP", "THK/deepseek-v4.1-flash:free")
    if role == "report":
        return os.environ.get("ABH_MODEL_STRONG", "ag/gemini-3.8-flash-high")
    if role == "analyze":
        strong = os.environ.get("ABH_MODEL_STRONG", "ag/gemini-3.8-flash-high")
        standard = os.environ.get("ABH_MODEL_STANDARD", "ocz/mimo-v2.6-flash-free")
        cand = default or standard
        # If analyze would duplicate report's model, step down to standard tier
        # so recon -> analyze -> report spans three distinct models.
        if cand == strong:
            return standard
        return cand
    return default


def phase_cmd(role: str, target: str, flags: list[str], hours: float,
              steps: int, model: str) -> list[str]:
    cmd = [sys.executable, str(AGENT), "--target", target, *flags,
           "--time", str(hours), "--max-steps", str(steps)]
    if agent_has_session_suffix():
        cmd += ["--session-suffix", role]
    return cmd


def run_phase(role: str, target: str, flags: list[str], hours: float,
              steps: int, env: dict, default_model: str = "",
              prior_output: str = "") -> tuple[int, str, dict]:
    """Run one role phase. Returns (rc, stdout+stderr, {role, model, rc, seconds}).

    `prior_output` is the accumulated stdout of the previous phase, handed to
    the agent through ABH_INPUT. When the engine lacks --session-suffix, all
    phases share one session and the role is recorded in phase-log.jsonl
    beside the target's recon dir instead.
    """
    r = ROLES[role]
    model = resolve(role, default_model)
    steps = min(steps, r["step_cap"]) if steps else r["step_cap"]
    cmd = phase_cmd(role, target, flags, hours, steps, model)
    child = dict(env)
    child["BRAIN_MODEL"] = model
    child["ABH_ROLE_BRIEF"] = r["system_addendum"]
    if prior_output:
        child["ABH_INPUT"] = prior_output
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=str(ABH), env=child, capture_output=True,
                       text=True, timeout=int(hours * 3600) + 600)
    seconds = round(time.monotonic() - t0, 1)
    text = (p.stdout or "") + (p.stderr or "")
    meta = {
        "role": role,
        "model": model,
        "rc": p.returncode,
        "seconds": seconds,
    }
    if not agent_has_session_suffix() and not os.environ.get("ABH_ROLE_DRY"):
        try:
            ldir = ABH / "bughunter" / "recon" / target
            ldir.mkdir(parents=True, exist_ok=True)
            with (ldir / PHASE_LOG).open("a", encoding="utf-8") as fh:
                entry = dict(meta, ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            pass
    return p.returncode, text, meta
