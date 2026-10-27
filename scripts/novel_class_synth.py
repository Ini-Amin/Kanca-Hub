#!/usr/bin/env python3
"""scripts/novel_class_synth.py — Synthesize novel vulnerability classes and weak-fix bypasses.

Generates candidate weakness patterns from first principles and modern architecture gaps
(e.g. Next.js server actions, OAuth state confusion, SSRF in AI agent tool calls,
prototype pollution via object spread, GraphQL depth bypasses).
Produces:
  1. Candidate Weakness Class Name & Description
  2. Nearest CWE Mapping & Why Standard CWE Classification Fails
  3. Opengrep AST Detection Rule
  4. 3 Raw Ground-Truth Code Files:
     - vulnerable.<ext> (unpatched / naive flaw)
     - bypass.<ext>     (weak fix / incomplete patch)
     - safe.<ext>       (architecturally secure remediation)
  5. Rigorous Sandbox TPR & FPR Gate:
     - TPR: must match vulnerable AND bypass
     - FPR: must NOT match safe
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
sys.path.insert(0, str(ROOT / "scripts"))
from bounty_run import chat_any

OUTPUT_DIR = Path.home() / ".config" / "auto-freecf" / "novel_classes"
OPENGREP_BIN = shutil.which("opengrep") or str(Path.home() / ".local" / "bin" / "opengrep")

FALLBACK_MODELS = [
    "CodeBuddy/deepseek-v4.1-flash",
    "Zcode/GLM-5.3",
    "ag/gemini-3.8-flash-high"
]

THEMES = [
    "Next.js / React Server Actions unauthorized direct invocation and state desync (CWE-287/CWE-639)",
    "AI Agent Tool-Use SSRF & Local IPC abuse via unvalidated LLM function arguments (CWE-918/CWE-862)",
    "OAuth 2.1 State/PKCE downgrade and session fixation via mixed callback routing (CWE-384/CWE-601)",
    "Prototype pollution to dynamic property injection via object spread and Object.assign (CWE-1321)",
    "GraphQL batching & circular query complexity authorization bypass (CWE-770/CWE-863)",
    "WebSocket origin spoofing and cross-site hijacking without token binding (CWE-346/CWE-352)",
    "JWT algorithm confusion and public key header (jku/jwk) injection (CWE-347/CWE-287)",
    "Path normalization mismatch between reverse proxy and upstream framework router (CWE-22/CWE-444)",
]


def synthesize_novel_class(theme: str, log=print) -> dict | None:
    prompt = f"""You are a principal security researcher and vulnerability theorist.
Design a NOVEL candidate weakness class based on this modern architectural theme:
"{theme}"

Reason deeply about:
1. Candidate Class Name (e.g. "ServerAction-DirectMethod-Bypass", "ToolAgent-IncompleteOrigin-SSRF").
2. Core Architectural Flaw: The subtle mismatch in trust boundaries between layers.
3. Common Naive Fix: What developers try first (and why it fails).
4. Bypass Technique: How an attacker circumvents the naive fix.
5. Nearest Standard CWE(s) and why existing CWE descriptions fail to capture the full nuance.
6. Opengrep AST Rule: Complete, valid Opengrep YAML rule that accurately detects this weakness.
7. Three Raw Code Files:
   - `vulnerable_code`: Realistic unpatched code.
   - `bypass_code`: The code after a naive patch, showing the bypass.
   - `safe_code`: The robustly remediated version.

Output ONLY valid JSON:
{{
  "candidate_class": "NameOfVulnerabilityClass",
  "theme": "{theme}",
  "cwe_mapping": ["CWE-XXX", "CWE-YYY"],
  "architectural_flaw": "...",
  "naive_fix_flaw": "...",
  "bypass_technique": "...",
  "why_existing_cwe_fails": "...",
  "opengrep_yaml_rule": "rules:\\n  - id: ...\\n    languages: [javascript]\\n    ...",
  "language": "javascript",
  "vulnerable_code": "...",
  "bypass_code": "...",
  "safe_code": "..."
}}
"""
    raw, used_model = chat_any(FALLBACK_MODELS, prompt, max_tokens=131072, timeout=600)
    if not raw.strip():
        log("  Empty response from model")
        return None

    cleaned = raw
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        log("  Non-JSON output from model")
        return None

    try:
        data = json.loads(cleaned[start : end + 1])
        data["used_model"] = used_model
        return data
    except Exception as e:
        log(f"  JSON parse error: {e}")
        return None


def verify_class_in_sandbox(class_dir: Path, rule_file: Path, ext: str) -> dict:
    if not os.path.exists(OPENGREP_BIN):
        return {"gate": "NO_OPENGREP", "weak": 0, "bypass": 0, "safe": 0}

    def _count(fpath: Path) -> int:
        r = subprocess.run([OPENGREP_BIN, "--config", str(rule_file), "--json", str(fpath)],
                           capture_output=True, text=True, timeout=60)
        try:
            return len(json.loads(r.stdout).get("results", []))
        except Exception:
            return 0

    w = _count(class_dir / f"vulnerable.{ext}")
    b = _count(class_dir / f"bypass.{ext}")
    s = _count(class_dir / f"safe.{ext}")

    is_proven = (w > 0 and b > 0 and s == 0)
    gate = "PROVEN_VALID" if is_proven else ("LEAKY_RULE" if s > 0 else "PARTIAL_MATCH")
    return {"gate": gate, "weak_matches": w, "bypass_matches": b, "safe_matches": s}


def run_pipeline(count: int = 3, log=print) -> list[dict]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log(f"Synthesizing {count} novel weakness classes with sandbox verification...")

    verified_classes = []
    for idx in range(min(count, len(THEMES))):
        theme = THEMES[idx]
        log(f"\n[Synthesizing Class #{idx+1}] Theme: {theme[:60]}...")
        result = synthesize_novel_class(theme, log=log)
        if not result:
            continue

        c_name = re.sub(r'[^a-zA-Z0-9_-]', '_', result.get("candidate_class", f"NovelClass_{idx+1}"))
        c_dir = OUTPUT_DIR / c_name
        c_dir.mkdir(parents=True, exist_ok=True)

        lang = result.get("language", "javascript")
        ext = "js" if lang == "javascript" else "py"

        rule_path = c_dir / "rule.yaml"
        rule_path.write_text(result.get("opengrep_yaml_rule", ""), encoding="utf-8")
        (c_dir / f"vulnerable.{ext}").write_text(result.get("vulnerable_code", ""), encoding="utf-8")
        (c_dir / f"bypass.{ext}").write_text(result.get("bypass_code", ""), encoding="utf-8")
        (c_dir / f"safe.{ext}").write_text(result.get("safe_code", ""), encoding="utf-8")

        # Rigorous Sandbox Gate
        gate_info = verify_class_in_sandbox(c_dir, rule_path, ext)
        result["sandbox_gate"] = gate_info
        log(f"  [SANDBOX GATE] {c_name} -> {gate_info['gate']} (weak={gate_info['weak_matches']}, bypass={gate_info['bypass_matches']}, safe={gate_info['safe_matches']})")

        (c_dir / "meta.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        verified_classes.append(result)

    summary_file = OUTPUT_DIR / "summary.json"
    summary_file.write_text(json.dumps(verified_classes, indent=2), encoding="utf-8")
    log(f"\nPipeline finished: {len(verified_classes)} novel classes created in {OUTPUT_DIR}")
    return verified_classes


def main():
    parser = argparse.ArgumentParser(description="Synthesize novel weakness classes")
    parser.add_argument("--count", type=int, default=3, help="Number of classes to synthesize")
    args = parser.parse_args()
    run_pipeline(count=args.count)


if __name__ == "__main__":
    main()
