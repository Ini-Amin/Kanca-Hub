#!/usr/bin/env python3
"""scripts/ast_rule_curator.py — Validate, fix, and curate distilled CVE Opengrep AST rules.

Addresses the exact gap: LLM-distilled CVE rules must be validated against the real
opengrep binary to eliminate schema hallucinations, unbound metavariables, and AST syntax errors
before scanning target code.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISTILLED_FILE = Path.home() / ".config" / "auto-freecf" / "glm_engine" / "cve_distilled_enriched.jsonl"
VALIDATED_DIR = Path.home() / ".config" / "auto-freecf" / "validated_ast_rules"
VALIDATED_INDEX = VALIDATED_DIR / "validated_index.jsonl"

OPENGREP_BIN = shutil.which("opengrep") or str(Path.home() / ".local" / "bin" / "opengrep")

SUPPORTED_LANGUAGES = {
    "javascript", "typescript", "python", "php", "go", "java", "c", "csharp", "ruby", "json", "yaml"
}


def clean_yaml_text(raw: str) -> str:
    """Strip markdown codefences and clean trailing garbage from LLM output."""
    t = raw.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def validate_rule_file(rule_path: Path) -> tuple[bool, str]:
    """Run opengrep --validate on a single YAML rule file."""
    if not os.path.exists(OPENGREP_BIN):
        return False, f"opengrep binary not found at {OPENGREP_BIN}"
    try:
        res = subprocess.run(
            [OPENGREP_BIN, "--validate", "--config", str(rule_path)],
            capture_output=True, text=True, timeout=10
        )
        if res.returncode == 0:
            return True, "valid"
        err = res.stderr.strip() or res.stdout.strip()
        return False, err[:200]
    except Exception as e:
        return False, str(e)


def curate_rules(limit: int = 0, languages: set[str] | None = None, log=print) -> dict:
    if not DISTILLED_FILE.exists():
        log(f"Distilled CVE file not found: {DISTILLED_FILE}")
        return {"total": 0, "valid": 0, "invalid": 0}

    VALIDATED_DIR.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="ast_validation_"))

    total = 0
def curate_rules(limit: int = 0, languages: set[str] | None = None, log=print) -> dict:
    if not DISTILLED_FILE.exists():
        log(f"Distilled CVE file not found: {DISTILLED_FILE}")
        return {"total": 0, "valid": 0, "invalid": 0}

    VALIDATED_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(VALIDATED_DIR, ignore_errors=True)
    VALIDATED_DIR.mkdir(parents=True, exist_ok=True)

    lines = DISTILLED_FILE.read_text(encoding="utf-8").splitlines()
    if limit > 0:
        lines = lines[:limit]

    log(f"Parsing and batch-validating {len(lines)} distilled CVE rules against Opengrep AST engine...")

    staged_by_lang: dict[str, list[tuple[str, dict, str]]] = {}
    total = 0
    invalid_syntax = 0

    for idx, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue

        raw_rule = d.get("opengrep_yaml_rule")
        cve_id = d.get("cve_id") or f"CVE-UNKNOWN-{idx}"
        if not raw_rule or not isinstance(raw_rule, str):
            continue

        total += 1
        cleaned = clean_yaml_text(raw_rule)
        try:
            parsed_yaml = yaml.safe_load(cleaned)
        except Exception:
            invalid_syntax += 1
            continue

        if not isinstance(parsed_yaml, dict) or "rules" not in parsed_yaml:
            invalid_syntax += 1
            continue

        rules_list = parsed_yaml.get("rules")
        if not isinstance(rules_list, list) or not rules_list:
            invalid_syntax += 1
            continue

        rule_langs = set()
        for r in rules_list:
            if isinstance(r, dict) and "languages" in r:
                langs = r["languages"]
                if isinstance(langs, list):
                    for l in langs:
                        rule_langs.add(str(l).lower())

        unsupported = rule_langs - SUPPORTED_LANGUAGES
        if unsupported:
            invalid_syntax += 1
            continue

        if languages and not (rule_langs & languages):
            continue

        primary_lang = sorted(rule_langs)[0] if rule_langs else "generic"
        staged_by_lang.setdefault(primary_lang, []).append((cve_id, d, cleaned))

    # Write rules by language directory and batch-validate
    valid_count = 0
    rejected_count = invalid_syntax
    by_lang: dict[str, int] = {}
    verified_records = []

    for lang, items in staged_by_lang.items():
        lang_dir = VALIDATED_DIR / lang
        lang_dir.mkdir(parents=True, exist_ok=True)
        temp_files: list[tuple[str, dict, Path]] = []

        for cve_id, raw_dict, yaml_content in items:
            f_path = lang_dir / f"{cve_id}.yaml"
            f_path.write_text(yaml_content, encoding="utf-8")
            temp_files.append((cve_id, raw_dict, f_path))

        # Test each file in this language with fast individual check
        for cve_id, raw_dict, f_path in temp_files:
            ok, err = validate_rule_file(f_path)
            if ok:
                valid_count += 1
                by_lang[lang] = by_lang.get(lang, 0) + 1
                record = {
                    "cve_id": cve_id,
                    "cvss": raw_dict.get("cvss"),
                    "cwe": raw_dict.get("cwe"),
                    "language": lang,
                    "path": str(f_path.relative_to(VALIDATED_DIR)),
                    "verification_probe": raw_dict.get("verification_probe"),
                    "root_cause": raw_dict.get("root_cause_pattern")
                }
                verified_records.append(record)
            else:
                f_path.unlink(missing_ok=True)
                rejected_count += 1

    # Save index of verified rules
    with open(VALIDATED_INDEX, "w", encoding="utf-8") as f:
        for r in verified_records:
            f.write(json.dumps(r) + "\n")

    log(f"AST Validation Completed:")
    log(f"  Processed : {total} rules")
    log(f"  Passed    : {valid_count} (100% verified Opengrep AST syntax)")
    log(f"  Rejected  : {rejected_count} (schema violations / invalid AST patterns dropped)")
    log(f"  By Language: {by_lang}")
    log(f"  Saved to  : {VALIDATED_DIR}")
    return {"total": total, "valid": valid_count, "invalid": rejected_count, "by_lang": by_lang}


def scan_target(target_path: Path, language: str = "javascript", log=print) -> list[dict]:
    """Scan a target codebase or harvested bundle using validated AST rules."""
    lang_dir = VALIDATED_DIR / language
    if not lang_dir.exists():
        log(f"No validated rules for language {language} in {lang_dir}")
        return []

    if not target_path.exists():
        log(f"Target path does not exist: {target_path}")
        return []

    log(f"Scanning {target_path} using validated AST rules in {lang_dir}...")
    cmd = [
        OPENGREP_BIN,
        "--config", str(lang_dir),
        "--json",
        str(target_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        out = res.stdout.strip()
        if not out:
            return []
        data = json.loads(out)
        results = data.get("results", [])
        log(f"  Opengrep match count: {len(results)}")
        return results
    except Exception as e:
        log(f"  Scan error: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description="Curate and validate distilled CVE AST rules.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    val_p = sub.add_parser("validate", help="Validate all distilled rules against opengrep")
    val_p.add_argument("--limit", type=int, default=0, help="Limit number of rules to process (0 = all)")
    val_p.add_argument("--lang", help="Comma-separated language filter (e.g. javascript,python)")

    scan_p = sub.add_parser("scan", help="Scan a target directory using validated rules")
    scan_p.add_argument("target", help="Directory or file to scan")
    scan_p.add_argument("--lang", default="javascript", help="Language ruleset to use")

    args = parser.parse_args()
    if args.cmd == "validate":
        langs = set(args.lang.split(",")) if args.lang else None
        curate_rules(limit=args.limit, languages=langs)
    elif args.cmd == "scan":
        scan_target(Path(args.target), language=args.lang)


if __name__ == "__main__":
    main()
