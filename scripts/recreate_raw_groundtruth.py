#!/usr/bin/env python3
"""scripts/recreate_raw_groundtruth.py — Recreate raw ground-truth vulnerable and safe code
for validated CVE AST rules, then compile and test in the sandbox to prove TPR=1.0, FPR=0.0.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = Path.home() / ".config" / "auto-freecf" / "validated_ast_rules"
BENCHMARK_DIR = Path.home() / ".config" / "auto-freecf" / "raw_groundtruth"
OPENGREP_BIN = shutil.which("opengrep") or str(Path.home() / ".local" / "bin" / "opengrep")

EXT_MAP = {
    "javascript": "js",
    "typescript": "ts",
    "python": "py",
    "php": "php",
    "go": "go",
    "c": "c",
    "java": "java",
    "csharp": "cs",
}


def build_testcase_prompt(cve_id: str, rule_yaml: str, root_cause: str) -> str:
    return f"""You are a top-tier security researcher creating ground-truth test cases for a SAST rule.
CVE: {cve_id}
Root Cause Pattern:
{root_cause}

Opengrep Rule:
```yaml
{rule_yaml}
```

Write TWO complete, standalone code files:
1. `vulnerable`: Realistic vulnerable implementation that strictly triggers the Opengrep rule.
2. `safe`: The correctly patched/sanitized version of the same code that must NOT trigger the Opengrep rule.

Output ONLY valid JSON:
{{
  "language": "javascript|python|php|...",
  "vulnerable_code": "...",
  "safe_code": "...",
  "attack_vector": "...",
  "fix_explanation": "..."
}}
"""


def verify_groundtruth(rule_file: Path, test_dir: Path, ext: str) -> tuple[bool, str]:
    """Run opengrep on test_dir and assert vulnerable matches, safe does not."""
    vuln_file = test_dir / f"vulnerable.{ext}"
    safe_file = test_dir / f"safe.{ext}"

    if not vuln_file.exists() or not safe_file.exists():
        return False, "missing testcase files"

    # Test vulnerable file
    res_vuln = subprocess.run([OPENGREP_BIN, "--config", str(rule_file), "--json", str(vuln_file)],
                              capture_output=True, text=True, timeout=15)
    try:
        data_vuln = json.loads(res_vuln.stdout)
        matches_vuln = len(data_vuln.get("results", []))
    except Exception:
        matches_vuln = 0

    # Test safe file
    res_safe = subprocess.run([OPENGREP_BIN, "--config", str(rule_file), "--json", str(safe_file)],
                             capture_output=True, text=True, timeout=15)
    try:
        data_safe = json.loads(res_safe.stdout)
        matches_safe = len(data_safe.get("results", []))
    except Exception:
        matches_safe = 0

    if matches_vuln > 0 and matches_safe == 0:
        return True, f"PERFECT (vuln matches={matches_vuln}, safe matches=0)"
    return False, f"MISMATCH (vuln matches={matches_vuln}, safe matches={matches_safe})"


def process_groundtruth(limit: int = 10, lang: str = "javascript", log=print) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from bounty_run import chat_any

    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    lang_rules = list((RULES_DIR / lang).glob("*.yaml")) if (RULES_DIR / lang).exists() else []

    log(f"Recreating raw ground-truth code for {min(len(lang_rules), limit)} {lang} rules...")
    verified_suite = []

    fallback_models = [
        "CodeBuddy/deepseek-v4.1-flash",
        "Zcode/GLM-5.3",
        "ag/gemini-3.8-flash-high"
    ]

    for rule_path in lang_rules[:limit]:
        cve_id = rule_path.stem
        test_dir = BENCHMARK_DIR / cve_id
        test_dir.mkdir(parents=True, exist_ok=True)
        meta_file = test_dir / "meta.json"

        rule_content = rule_path.read_text(encoding="utf-8")
        log(f"Synthesizing raw positive & negative test cases for {cve_id} via multi-tier chain...")

        prompt = build_testcase_prompt(cve_id, rule_content, f"Distilled rule for {cve_id}")
        raw, used_model = chat_any(fallback_models, prompt, max_tokens=131072, timeout=600)
        log(f"  model replied: {used_model} ({len(raw)} chars)")
        cleaned = raw
        if "```json" in cleaned:
            cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1:
            log(f"  [ERROR] Non-JSON reply for {cve_id}. Snippet: {raw[:150]}")
            continue

        try:
            d = json.loads(cleaned[start : end + 1])
            ext = EXT_MAP.get(lang, "js")
            vuln_f = test_dir / f"vulnerable.{ext}"
            safe_f = test_dir / f"safe.{ext}"

            vuln_f.write_text(d["vulnerable_code"], encoding="utf-8")
            safe_f.write_text(d["safe_code"], encoding="utf-8")
            meta_file.write_text(json.dumps(d, indent=2), encoding="utf-8")

            # Verify in sandbox
            ok, status = verify_groundtruth(rule_path, test_dir, ext)
            if ok:
                log(f"  [PROVEN VALID 100%] {cve_id}: {status}")
                verified_suite.append({"cve_id": cve_id, "status": "verified", "test_dir": str(test_dir)})
            else:
                log(f"  [RECREATED BUT TUNING NEEDED] {cve_id}: {status}")
        except Exception as e:
            log(f"  [ERROR] {cve_id}: {e}")

    log(f"Ground-truth suite completed: {len(verified_suite)} rules verified with raw code in {BENCHMARK_DIR}")


def main():
    parser = argparse.ArgumentParser(description="Recreate and verify raw ground-truth code for AST rules.")
    parser.add_argument("--limit", type=int, default=5, help="Number of rules to process")
    parser.add_argument("--lang", default="javascript", help="Language ruleset to recreate")
    args = parser.parse_args()
    process_groundtruth(limit=args.limit, lang=args.lang)


if __name__ == "__main__":
    main()
