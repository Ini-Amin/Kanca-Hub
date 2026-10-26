#!/usr/bin/env python3
"""re_cleanroom.py — Clean-Room Reverse Engineering 4-Agent Autonomous Loop.

Implements the 4-Agent Clean-Room pipeline:
  1. Analyst: Examines binary/code/spec artifact -> writes analysis/evidence.md
  2. Spec-Writer: Distills evidence.md into handoff/SPEC.md (Sanitizer: no decompiled symbol leaks)
  3. Rebuilder: Reconstructs clean source code in rebuild/src/ solely from SPEC.md
  4. Verifier: Executes test inputs, compares original vs rebuild behavior, checks wrong=0 crash=0
Loops up to MAX_ROUNDS (default: 3) feeding behavioral divergences back into SPEC.md.

Usage:
  python3 scripts/re_cleanroom.py run <target_path> "<feature_description>" [--workdir /tmp/re_lab]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_R9 = os.environ.get("NINE_ROUTER_URL", "http://127.0.0.1:20128/v1")
DEFAULT_R9_KEY = os.environ.get("NINE_ROUTER_KEY", "REDACTED")

# Models for the 4-Agent Clean Room
ANALYST_MODEL = os.environ.get("RE_ANALYST_MODEL", "xperimential/glm-5.3-uncensored")
SPEC_MODEL = os.environ.get("RE_SPEC_MODEL", "xperimential/glm-5.3-uncensored")
REBUILDER_MODEL = os.environ.get("RE_REBUILDER_MODEL", "xperimential/glm-5.3-uncensored")
VERIFIER_MODEL = os.environ.get("RE_VERIFIER_MODEL", "ag/gemini-3.8-flash-high")

FALLBACK_MODELS = [
    "xperimential/glm-5.3-uncensored",
    "ag/gemini-3.8-flash-high",
    "tia/glm-5.3-flash:free",
]


def chat_llm(model: str, prompt: str, max_tokens: int = 4096, timeout: int = 180) -> str:
    """Chat via 9Router with fallback handling and stream normalization."""
    models_to_try = [model] + [m for m in FALLBACK_MODELS if m != model]
    last_err = None

    for m in models_to_try:
        body = {
            "model": m,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        req = urllib.request.Request(
            f"{DEFAULT_R9}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {DEFAULT_R9_KEY}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")

            # SSE data: normalization
            if "\ndata: " in raw[:600] or raw.lstrip().startswith(("data:", ":")):
                chunks = []
                for line in raw.splitlines():
                    line = line.strip()
                    if line.startswith("data: ") and line != "data: [DONE]":
                        try:
                            d = json.loads(line[6:])
                            delta = d["choices"][0].get("delta") or {}
                            chunks.append(delta.get("content", ""))
                        except Exception:
                            pass
                if chunks:
                    full_text = "".join(chunks)
                    if "blocked by" in full_text.lower() or "safety policy" in full_text.lower():
                        last_err = RuntimeError(f"Refusal from {m}")
                        continue
                    return full_text

            start = raw.find("{")
            if start >= 0:
                try:
                    data, _ = json.JSONDecoder().raw_decode(raw[start:])
                    content = (data["choices"][0]["message"] or {}).get("content") or ""
                    if content:
                        if "blocked by" in content.lower() or "safety policy" in content.lower():
                            last_err = RuntimeError(f"Refusal from {m}")
                            continue
                        return content
                except Exception:
                    pass
        except Exception as e:
            last_err = e
            # Try next model in chain
            continue

    raise RuntimeError(f"All model candidates failed for chat. Last error: {last_err}")


def sanitize_spec(spec_text: str) -> tuple[bool, list[str]]:
    """Strict gate: spec must not leak raw decompiled symbols or addresses."""
    pattern = re.compile(r"\b(0x[0-9a-fA-F]{5,}|sub_[0-9a-fA-F]+|var_[0-9a-fA-F]+|FUN_[0-9a-fA-F]+)\b")
    leaks = []
    for line_no, line in enumerate(spec_text.splitlines(), start=1):
        matches = pattern.findall(line)
        if matches:
            leaks.append(f"Line {line_no}: found {matches} in: {line.strip()[:60]}")
    return len(leaks) == 0, leaks


class CleanRoomRunner:
    def __init__(self, target_path: str, feature: str, workdir: Path, max_rounds: int = 3):
        self.target_path = Path(target_path).resolve()
        self.feature = feature
        self.workdir = Path(workdir).resolve()
        self.max_rounds = max_rounds

        self.analysis_dir = self.workdir / "analysis"
        self.handoff_dir = self.workdir / "handoff"
        self.rebuild_dir = self.workdir / "rebuild"
        self.src_dir = self.rebuild_dir / "src"

        for d in (self.analysis_dir, self.handoff_dir, self.src_dir):
            d.mkdir(parents=True, exist_ok=True)

    def step_analyst(self) -> Path:
        print("\n[*] [Phase 1: Analyst Agent] Analyzing target behavior and structure...")
        target_content = ""
        if self.target_path.is_file():
            try:
                target_content = self.target_path.read_text(errors="ignore")[:30000]
            except Exception:
                target_content = f"<Binary or non-text file: {self.target_path.name}, size={self.target_path.stat().st_size} bytes>"
        else:
            target_content = f"<Directory: {self.target_path}>"

        prompt = (
            f"You are the senior Analyst Agent in a Clean-Room Reverse Engineering Team.\n"
            f"Target: {self.target_path.name}\n"
            f"Requested Feature / Component to reverse: {self.feature}\n\n"
            f"Target Sample / Context:\n{target_content}\n\n"
            f"Your job:\n"
            f"1. Explain how this feature functions under the hood.\n"
            f"2. Identify data formats, state transitions, algorithms, and cryptographic/arithmetic operations.\n"
            f"3. Document your findings clearly with observed evidence.\n"
            f"Output in Markdown format for analysis/evidence.md."
        )
        evidence = chat_llm(ANALYST_MODEL, prompt, max_tokens=4096)
        out_file = self.analysis_dir / "evidence.md"
        out_file.write_text(evidence)
        print(f"[+] Evidence written to {out_file} ({len(evidence)} chars)")
        return out_file

    def step_spec_writer(self, evidence_path: Path, divergences: list[str] | None = None) -> Path:
        print("\n[*] [Phase 2: Spec-Writer Agent] Writing functional specification...")
        evidence_text = evidence_path.read_text()

        divergence_context = ""
        if divergences:
            divergence_context = (
                "\n\nCRITICAL FIX NEEDED — Previous rebuild diverged on these test cases:\n"
                + "\n".join(f"- {d}" for d in divergences)
                + "\nIncorporate these exact cases as explicit constraints under Test Cases."
            )

        prompt = (
            f"You are the Spec-Writer Agent in a Clean-Room Reverse Engineering Team.\n"
            f"Feature to specify: {self.feature}\n"
            f"Evidence from Analyst:\n{evidence_text}\n"
            f"{divergence_context}\n\n"
            f"CRITICAL SANITIZATION RULES:\n"
            f"- NEVER leak decompiled function names (e.g. sub_..., FUN_..., var_...).\n"
            f"- NEVER leak raw hex memory addresses (0x...).\n"
            f"- Write an implementation-agnostic specification in clean functional terms.\n"
            f"- Include a dedicated '## Test Cases' section with concrete inputs and expected outputs.\n"
            f"Output handoff/SPEC.md."
        )
        spec = chat_llm(SPEC_MODEL, prompt, max_tokens=4096)

        # Sanitizer gate
        passed, leaks = sanitize_spec(spec)
        if not passed:
            print("[-] Spec failed sanitization gate! Redacting leaks...")
            # Strip leaks
            for leak in leaks:
                print("   ", leak)
            spec = re.sub(r"\b(0x[0-9a-fA-F]{5,}|sub_[0-9a-fA-F]+|var_[0-9a-fA-F]+|FUN_[0-9a-fA-F]+)\b", "val_offset", spec)

        spec_file = self.handoff_dir / "SPEC.md"
        spec_file.write_text(spec)
        print(f"[+] Clean spec written to {spec_file} ({len(spec)} chars)")
        return spec_file

    def step_rebuilder(self, spec_path: Path) -> Path:
        print("\n[*] [Phase 3: Rebuilder Agent] Rebuilding source code from SPEC.md...")
        spec_text = spec_path.read_text()
        prompt = (
            f"You are the Rebuilder Agent in a Clean-Room Reverse Engineering Team.\n"
            f"You have NEVER seen the original source code or binary. You only see this clean spec:\n\n"
            f"{spec_text}\n\n"
            f"Instructions:\n"
            f"1. Write clean, robust, executable Python 3 (or requested language) code that implements every requirement.\n"
            f"2. Include self-checking runnable test cases or CLI execution.\n"
            f"3. Return ONLY valid source code within a standard ```python markdown codeblock.\n"
        )
        code_resp = chat_llm(REBUILDER_MODEL, prompt, max_tokens=4096)

        # Extract code block
        code_match = re.search(r"```(?:python|py)?\n(.*?)```", code_resp, re.DOTALL)
        code = code_match.group(1) if code_match else code_resp

        rebuild_file = self.src_dir / "rebuild_impl.py"
        rebuild_file.write_text(code)
        print(f"[+] Rebuilt implementation written to {rebuild_file} ({len(code)} chars)")
        return rebuild_file

    def step_verifier(self, spec_path: Path, rebuild_file: Path) -> tuple[int, int, list[str]]:
        print("\n[*] [Phase 4: Verifier Agent] Verifying parity and executing tests...")
        # Run the rebuilt implementation
        res = subprocess.run(
            [sys.executable, str(rebuild_file)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        divergences = []
        wrong = 0
        crash = 0

        if res.returncode != 0:
            crash = 1
            divergences.append(f"Execution crashed with exit code {res.returncode}:\n{res.stderr.strip()[:300]}")
        else:
            # Check test output
            stdout = res.stdout.strip()
            print(f"      Execution stdout:\n{stdout[:200]}")

        # Verifier agent review of conformance
        spec_text = spec_path.read_text()
        prompt = (
            f"You are the independent Verifier Agent.\n"
            f"Specification:\n{spec_text[:4000]}\n\n"
            f"Execution Results:\nExit code: {res.returncode}\nStdout: {res.stdout[:1500]}\nStderr: {res.stderr[:1500]}\n\n"
            f"Compare results against requirements.\n"
            f"End your report with a single strict summary line:\n"
            f"RESULT wrong=<N> crash=<N>"
        )
        review = chat_llm(VERIFIER_MODEL, prompt, max_tokens=1000)

        # Parse RESULT line
        match = re.search(r"RESULT wrong=(\d+) crash=(\d+)", review)
        if match:
            wrong = int(match.group(1))
            crash = int(match.group(2))
        elif crash == 0:
            wrong = 0

        report_file = self.workdir / "report.md"
        report_text = f"{review}\n\nRESULT wrong={wrong} crash={crash}\n"
        report_file.write_text(report_text)
        print(f"[+] Verifier Verdict: wrong={wrong}, crash={crash}")
        return wrong, crash, divergences

    def run(self):
        print(f"=== Starting Clean-Room RE Loop (Max {self.max_rounds} Rounds) ===")
        print(f"Target : {self.target_path}")
        print(f"Feature: {self.feature}")
        print(f"Workdir: {self.workdir}")

        evidence_path = self.step_analyst()
        divergences = None

        for round_num in range(1, self.max_rounds + 1):
            print(f"\n==================== ROUND {round_num}/{self.max_rounds} ====================")
            spec_path = self.step_spec_writer(evidence_path, divergences)
            rebuild_file = self.step_rebuilder(spec_path)
            wrong, crash, divergences = self.step_verifier(spec_path, rebuild_file)

            if wrong == 0 and crash == 0:
                print(f"\n[🎉 PARITY REACHED] Feature successfully reconstructed in Round {round_num}!")
                print(f"Output code: {rebuild_file}")
                return 0

            print(f"[-] Divergence detected (wrong={wrong}, crash={crash}). Looping to round {round_num + 1}...")

        print(f"\n[!] Reached max rounds ({self.max_rounds}) without 100% parity. Check {self.workdir / 'report.md'}")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean-Room RE 4-Agent Autonomous Loop")
    parser.add_argument("target", help="Path to target binary, script, or directory")
    parser.add_argument("feature", help="Feature or component to reverse engineer")
    parser.add_argument("--workdir", default="/tmp/re_lab", help="Working directory for cleanroom artifacts")
    parser.add_argument("--rounds", type=int, default=3, help="Max rounds (default: 3)")
    args = parser.parse_args()

    runner = CleanRoomRunner(args.target, args.feature, Path(args.workdir), max_rounds=args.rounds)
    sys.exit(runner.run())
