#!/usr/bin/env python3
"""codebase_migrator.py — Autonomous Test Generator & Codebase Hardening.

Inspects target scripts, identifies untested behaviors, contracts, and boundaries,
synthesizes deterministic, full-suite-safe unit tests using LLMs (nr/step-5-preview
or xperimential/glm-5.3-uncensored), and verifies them in a self-healing loop
until 100% pass rate is achieved.

Usage:
  python3 scripts/codebase_migrator.py generate scripts/fastly_serverlist.py
  python3 scripts/codebase_migrator.py batch scripts/fastly_*.py scripts/mozilla_guardian_client.py
"""

from __future__ import annotations

import argparse
import ast
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

MIGRATOR_MODEL = os.environ.get("MIGRATOR_MODEL", "xperimential/glm-5.3-uncensored")
FALLBACK_MODELS = [
    "ag/gemini-3.8-flash-high",
    "tia/glm-5.3-flash:free",
]


def chat_llm(model: str, prompt: str, max_tokens: int = 4096, timeout: int = 180) -> str:
    """Chat via 9Router with fallback handling."""
    models_to_try = [model] + [m for m in FALLBACK_MODELS if m != model]
    last_err = None

    for m in models_to_try:
        body = {
            "model": m,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
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

            chunks = []
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith(":") or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
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
                        return content
                except Exception:
                    pass
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"All model candidates failed for migrator. Last error: {last_err}")


def extract_code_elements(script_path: Path) -> dict:
    """Extract AST functions and classes from the target script."""
    text = script_path.read_text(errors="ignore")
    tree = ast.parse(text, filename=str(script_path))
    functions = []
    classes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
    return {"functions": functions, "classes": classes, "source": text}


def generate_tests_for_script(script_path: Path, max_attempts: int = 3) -> bool:
    """Generate and iterate unit tests for a single script until 100% passing."""
    script_path = script_path.resolve()
    stem = script_path.stem
    test_path = script_path.parent.parent / "tests" / f"test_{stem}.py"
    print(f"\n[*] Generating unit tests for: {script_path.name}")
    print(f"    Target test file: {test_path.relative_to(script_path.parent.parent)}")

    elements = extract_code_elements(script_path)
    functions_str = ", ".join(elements["functions"]) or "None"
    classes_str = ", ".join(elements["classes"]) or "None"

    failure_feedback = ""

    for attempt in range(1, max_attempts + 1):
        print(f"    [Attempt {attempt}/{max_attempts}] Prompting model for unit test suite...")
        prompt = (
            f"You are a Senior Test Engineer implementing comprehensive, robust unit tests.\n"
            f"Target file: {script_path.name}\n"
            f"Exported Functions: {functions_str}\n"
            f"Exported Classes: {classes_str}\n\n"
            f"Target Source Code:\n```python\n{elements['source']}\n```\n\n"
            f"Requirements:\n"
            f"1. Use `unittest` module with `TestCase` classes.\n"
            f"2. Tests MUST be completely isolated and deterministic: MOCK ALL network sockets, HTTP requests (`urllib.request`), and sensitive file operations.\n"
            f"3. Cover core behaviors, boundary values, error branches, and schema validations.\n"
            f"4. Import target symbols using relative or sys.path adjustments so `python3 -m unittest {test_path}` works seamlessly.\n"
            f"5. Return ONLY executable Python test code in a ```python ... ``` markdown block.\n"
        )
        if failure_feedback:
            prompt += f"\n\nPREVIOUS ATTEMPT FAILED WITH:\n{failure_feedback}\nFix the failing assertions or mock setups."

        resp = chat_llm(MIGRATOR_MODEL, prompt, max_tokens=4096)
        def _extract(text: str) -> str:
            m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
            if m:
                return m.group(1).strip()
            lines = text.strip().splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            return "\n".join(lines).strip()

        test_code = _extract(resp)

        test_path.write_text(test_code)

        # Run unittest
        cmd = [sys.executable, "-m", "unittest", str(test_path)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if res.returncode == 0:
            print(f"    [+] All tests passed! ({test_path.name})")
            print(f"    {res.stderr.strip() or res.stdout.strip()}")
            return True
        else:
            print(f"    [-] Test run failed on attempt {attempt}:")
            err_output = res.stderr.strip() or res.stdout.strip()
            print("        " + err_output.splitlines()[-1] if err_output.splitlines() else "Unknown error")
            failure_feedback = err_output[:2000]

    print(f"    [!] Failed achieving 100% pass rate after {max_attempts} attempts.")
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Codebase Migrator & Test Generator")
    parser.add_argument("command", choices=["generate", "batch"], help="Action to run")
    parser.add_argument("paths", nargs="+", help="Target script path(s)")
    args = parser.parse_args()

    for p_str in args.paths:
        p = Path(p_str)
        if p.is_file() and p.suffix == ".py":
            generate_tests_for_script(p)
        else:
            for matched in Path(".").glob(p_str):
                if matched.is_file() and matched.suffix == ".py":
                    generate_tests_for_script(matched)
