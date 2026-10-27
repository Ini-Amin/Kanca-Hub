#!/usr/bin/env python3
"""scripts/glm_mass_engine.py — High-Throughput 120B GLM Token Workload Engine.

Drives massive parallel token consumption across ZCode Start Plan accounts before expiry:
  1. deobfuscate  : JS/AST bundle reverse-engineering & security sink mapping (GLM-5.3-Flash)
  2. distill-cve  : Deep-reasoning root cause & Opengrep rule synthesis from cve.db (GLM-5.3)
  3. hunt-sast    : Autonomous 2-stage SAST on in-scope bounty targets (Flash -> Thinking)
  4. status       : Quota, shards, and pipeline progress overview

Usage:
  python3 scripts/glm_mass_engine.py status
  python3 scripts/glm_mass_engine.py deobfuscate --input /tmp/ocjs/mod_055_660979.js [--limit-chunks 5]
  python3 scripts/glm_mass_engine.py distill-cve --limit 20 [--min-cvss 8.0]
  python3 scripts/glm_mass_engine.py hunt-sast --job-id bugcrowd-bitso-mbb-og-56930
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Paths & defaults
HOME = Path.home()
AUTO_CF_DIR = HOME / ".config" / "auto-freecf"
STATE_DIR = AUTO_CF_DIR / "glm_engine"
STATE_DIR.mkdir(parents=True, exist_ok=True)
CVE_DB = AUTO_CF_DIR / "cve.db"
BALANCE_FILE = HOME / ".zcode-bridge" / "balance.json"
BRIDGE_KEYS_FILE = HOME / ".zcode-bridge" / "keys.txt"
BRIDGE_URL = os.environ.get("GLM_BRIDGE_URL", "http://127.0.0.1:8787")
R9_URL = os.environ.get("GLM_R9_URL", "http://127.0.0.1:20128/v1")
MODEL_FLASH = "GLM-5.3-Flash"
MODEL_THINKING = "GLM-5.3"
FALLBACK_MODEL = "tia/glm-5.3-flash:free"


def _r9_key() -> str:
    try:
        con = sqlite3.connect(HOME / ".9router/db/data.sqlite")
        return con.cursor().execute("SELECT key FROM apiKeys LIMIT 1").fetchone()[0]
    except Exception:
        return ""

def log(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)


def load_bridge_keys() -> list[str]:
    if not BRIDGE_KEYS_FILE.exists():
        return ["sk-dummy"]
    bal_map = {}
    if BALANCE_FILE.exists():
        try:
            bdata = json.loads(BALANCE_FILE.read_text())
            for em, info in bdata.items():
                rem = sum(b.get("remaining_units", 0) for b in info.get("buckets", []))
                bal_map[em] = rem
        except Exception:
            pass
    scored_keys = []
    for line in BRIDGE_KEYS_FILE.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, em = line.split("=", 1)
            rem = bal_map.get(em.strip(), 0)
            scored_keys.append((rem, k.strip()))
    scored_keys.sort(key=lambda t: -t[0])
    keys = [k for rem, k in scored_keys if rem > 0]
    if not keys:
        keys = [k for _, k in scored_keys]
    return keys or ["sk-dummy"]


def chat_bridge(
    prompt: str,
    model: str = MODEL_FLASH,
    system: str = "You are an expert security engineer and software reverse-engineer.",
    session_type: str = "subagent",
    key: str = "",
    timeout: int = 180,
    max_tokens: int = 4096,
) -> str:
    """Execute a completion via ZCode bridge with automatic 9Router GLM fallback."""
    keys = load_bridge_keys()
    api_key = key or (keys[0] if keys else "sk-dummy")
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }
    try:
        req = urllib.request.Request(
            f"{BRIDGE_URL}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "x-zcode-session-type": session_type,
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read().decode("utf-8", "replace"))
            res = d["choices"][0]["message"].get("content") or ""
            if res:
                return res
    except Exception:
        pass

    # Fallback to 9Router GLM
    r9_k = _r9_key()
    r9_body = {
        "model": FALLBACK_MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    req2 = urllib.request.Request(
        f"{R9_URL}/chat/completions",
        data=json.dumps(r9_body).encode(),
        headers={
            "Authorization": f"Bearer {r9_k}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req2, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            start = raw.find("{")
            if start != -1:
                d, _ = json.JSONDecoder().raw_decode(raw[start:])
                return d["choices"][0]["message"].get("content") or ""
    except Exception:
        pass
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Subcommand: status
# ─────────────────────────────────────────────────────────────────────────────
def cmd_status() -> None:
    print("=== GLM-5.3 120B ENGINE STATUS ===")
    if BALANCE_FILE.exists():
        try:
            d = json.loads(BALANCE_FILE.read_text())
            now = time.time()
            models: dict[str, dict] = {}
            accts_with_plan = 0
            for email, info in d.items():
                buckets = info.get("buckets", [])
                if buckets:
                    accts_with_plan += 1
                for bk in buckets:
                    m = bk.get("show_name")
                    rem = bk.get("remaining_units", 0)
                    tot = bk.get("total_units", 0)
                    exp = bk.get("expires_at", 0)
                    if m not in models:
                        models[m] = {"rem": 0, "tot": 0, "exp_min": 999999, "exp_max": 0}
                    models[m]["rem"] += rem
                    models[m]["tot"] += tot
                    hrs = (exp - now) / 3600.0
                    models[m]["exp_min"] = min(models[m]["exp_min"], hrs)
                    models[m]["exp_max"] = max(models[m]["exp_max"], hrs)

            print(f"Accounts: {len(d)} total, {accts_with_plan} with active plan")
            for m, st in sorted(models.items()):
                print(f"  {m:16s}: {st['rem']:>14,} / {st['tot']:>14,} tokens left (exp in {st['exp_min']:.1f}h..{st['exp_max']:.1f}h)")
        except Exception as e:
            print(f"Error reading balance: {e}")
    else:
        print("balance.json not found.")

    # Check bridge health
    try:
        with urllib.request.urlopen(f"{BRIDGE_URL}/health", timeout=3) as r:
            h = json.loads(r.read().decode())
            print(f"Bridge Health     : OK (model={h.get('model')}, egress={h.get('egress')}, keys={h.get('keys')})")
    except Exception as e:
        print(f"Bridge Health     : DOWN ({e})")

    # Output stats
    deobf_files = list((STATE_DIR / "deobf").glob("*.jsonl")) if (STATE_DIR / "deobf").exists() else []
    cve_distilled = (STATE_DIR / "cve_distilled.jsonl").exists()
    cve_count = sum(1 for _ in open(STATE_DIR / "cve_distilled.jsonl")) if cve_distilled else 0
    print(f"Processed Bundles : {len(deobf_files)} files in {STATE_DIR / 'deobf'}")
    print(f"Distilled CVEs    : {cve_count} rules in {STATE_DIR / 'cve_distilled.jsonl'}")


# ─────────────────────────────────────────────────────────────────────────────
# Subcommand: deobfuscate
# ─────────────────────────────────────────────────────────────────────────────
def split_js_chunks(code: str, chunk_size: int = 40000) -> list[str]:
    """Split large JS file into cohesive function/module blocks."""
    if len(code) <= chunk_size:
        return [code]
    chunks = []
    lines = code.splitlines(keepends=True)
    cur, cur_len = [], 0
    for line in lines:
        cur.append(line)
        cur_len += len(line)
        if cur_len >= chunk_size and (line.startswith("}") or line.startswith("var ") or line.startswith("function ") or ";" in line):
            chunks.append("".join(cur))
            cur, cur_len = [], 0
    if cur:
        chunks.append("".join(cur))
    return chunks


def cmd_deobfuscate(input_path: Path, limit_chunks: int = 0, concurrency: int = 4) -> None:
    if not input_path.exists():
        sys.exit(f"Input file not found: {input_path}")
    code = input_path.read_text(errors="ignore")
    chunks = split_js_chunks(code)
    if limit_chunks > 0:
        chunks = chunks[:limit_chunks]
    log(f"Deobfuscating {input_path.name}: {len(code)} bytes -> {len(chunks)} chunks using {MODEL_FLASH}")

    out_dir = STATE_DIR / "deobf"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{input_path.stem}.jsonl"

    keys = load_bridge_keys()

    def _worker(idx: int, chunk: str) -> dict:
        prompt = f"""Analyze and deobfuscate this minified JavaScript chunk (Part {idx+1}/{len(chunks)}).
Extract:
1. module_purpose: One sentence summary of what this code does.
2. recovered_symbols: A JSON object mapping minified variables/functions to clear semantic names (e.g. {{"e": "authToken", "r": "signRequest"}}).
3. endpoints_and_rpc: Any URLs, API routes, WebSocket endpoints, IPC event names, or HTTP headers referenced.
4. security_sinks: Any potentially dangerous patterns (eval, innerHTML, postMessage without origin check, auth tokens, hardcoded secrets, prototype pollution).

Output ONLY valid JSON matching this schema:
{{
  "chunk_idx": {idx},
  "module_purpose": "...",
  "recovered_symbols": {{}},
  "endpoints_and_rpc": [],
  "security_sinks": []
}}

Source Code:
```javascript
{chunk}
```
"""
        k = keys[idx % len(keys)]
        try:
            raw = chat_bridge(prompt, model=MODEL_FLASH, session_type="subagent", key=k, max_tokens=8192)
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                return json.loads(raw[start : end + 1])
        except Exception as e:
            return {"chunk_idx": idx, "error": str(e)}
        return {"chunk_idx": idx, "error": "invalid model response"}

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = {pool.submit(_worker, i, c): i for i, c in enumerate(chunks)}
        with open(out_file, "a") as f:
            for fut in as_completed(futs):
                idx = futs[fut]
                res = fut.result()
                f.write(json.dumps(res) + "\n")
                f.flush()
                log(f"  Chunk {idx+1}/{len(chunks)}: {res.get('module_purpose') or res.get('error')}")

    log(f"Deobfuscation complete: saved to {out_file}")


# ─────────────────────────────────────────────────────────────────────────────
# Subcommand: distill-cve
# ─────────────────────────────────────────────────────────────────────────────
def cmd_distill_cve(limit: int = 50, min_cvss: float = 7.5, concurrency: int = 4) -> None:
    if not CVE_DB.exists():
        sys.exit(f"CVE database not found at {CVE_DB}")
    con = sqlite3.connect(CVE_DB)
    cur = con.cursor()
    query = """
    SELECT id, description, cvss31, cvss40, cwes, vendors
    FROM cve
    WHERE (cvss31 >= ? OR cvss40 >= ?)
      AND description IS NOT NULL
      AND description != ''
    ORDER BY COALESCE(cvss31, cvss40) DESC
    LIMIT ?
    """
    rows = cur.execute(query, (min_cvss, min_cvss, limit)).fetchall()
    log(f"Distilling {len(rows)} high-severity CVEs (CVSS >= {min_cvss}) using {MODEL_THINKING}")

    out_file = STATE_DIR / "cve_distilled.jsonl"
    keys = load_bridge_keys()

    def _worker(idx: int, row: tuple) -> dict:
        cve_id, desc, cvss31, cvss40, cwes, vendors = row
        score = cvss31 or cvss40 or 0.0
        prompt = f"""You are a top-tier vulnerability researcher. Reason about this CVE and distill it into an actionable detection rule.

CVE: {cve_id} (Score: {score}, CWE: {cwes}, Vendor: {vendors})
Description:
{desc}

Perform deep root-cause reasoning:
1. Identify the exact flaw mechanism (source -> missing validation -> dangerous sink).
2. Write a minimal, idiomatic Opengrep/Semgrep YAML rule pattern to detect this bug class in source code.
3. Provide a safe verification probe (curl or payload pattern) to test if a target is vulnerable without causing denial of service.

Return ONLY a JSON object:
{{
  "cve_id": "{cve_id}",
  "cvss": {score},
  "cwe": "{cwes}",
  "root_cause_pattern": "...",
  "opengrep_yaml_rule": "...",
  "verification_probe": "..."
}}
"""
        k = keys[idx % len(keys)]
        try:
            raw = chat_bridge(prompt, model=MODEL_THINKING, session_type="other", key=k, max_tokens=16000)
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                return json.loads(raw[start : end + 1])
        except Exception as e:
            return {"cve_id": cve_id, "error": str(e), "raw": raw if 'raw' in locals() else ""}
        return {"cve_id": cve_id, "error": "non-JSON reply", "raw": raw if 'raw' in locals() else ""}

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = {pool.submit(_worker, i, r): i for i, r in enumerate(rows)}
        with open(out_file, "a") as f:
            for fut in as_completed(futs):
                idx = futs[fut]
                res = fut.result()
                f.write(json.dumps(res) + "\n")
                f.flush()
                log(f"  Distilled [{idx+1}/{len(rows)}] {res.get('cve_id')}: {res.get('root_cause_pattern', '')[:60]}...")

    log(f"Distillation complete: saved to {out_file}")


# ─────────────────────────────────────────────────────────────────────────────
# Subcommand: hunt-sast (Mass Autonomous SAST)
# ─────────────────────────────────────────────────────────────────────────────
def cmd_hunt_sast(job_id: str) -> None:
    job_path = AUTO_CF_DIR / "bounty" / job_id
    if not job_path.is_dir():
        sys.exit(f"Bounty job directory not found: {job_path}")

    # Enforce Gate 1: Scope Check
    job_spec_file = job_path / "job.json"
    if not job_spec_file.exists():
        sys.exit(f"job.json missing in {job_path}")
    job_spec = json.loads(job_spec_file.read_text())
    in_scope = job_spec.get("in_scope", [])
    out_of_scope = job_spec.get("out_of_scope", [])

    # Import scope checker directly from bounty_run
    sys.path.insert(0, str(Path(__file__).parent))
    from bounty_run import host_in_scope

    live_file = job_path / "recon" / "live.txt"
    if not live_file.exists() or live_file.stat().st_size == 0:
        log(f"No live targets in {live_file}")
        return

    targets = []
    for line in live_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        from urllib.parse import urlparse
        u = urlparse(line if "://" in line else f"https://{line}")
        host = (u.hostname or "").lower()
        if host_in_scope(host, in_scope, out_of_scope):
            targets.append(line)
        else:
            log(f"  [GATE 1 DENIED] Host {host} is outside program scope — SKIPPING.")

    log(f"Gate 1 verified: {len(targets)} in-scope targets for {job_id}")
    findings_file = job_path / "glm_sast_findings.json"
    bundles_dir = job_path / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)

    stage1_candidates = []
    discovered_endpoints = set()

    # Stage 0: Harvest JS Bundles & HTML Assets from Target
    for t in targets[:10]:
        log(f"Stage 0: Harvesting HTML and client bundles from {t}")
        try:
            req = urllib.request.Request(t, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=12) as r:
                html_content = r.read().decode("utf-8", "replace")
                # Save HTML
                (bundles_dir / "index.html").write_text(html_content, encoding="utf-8")
                
                # Find script tags
                script_srcs = re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', html_content)
                for s in script_srcs:
                    bundle_url = s if s.startswith("http") else urllib.parse.urljoin(t, s)
                    fname = Path(urllib.parse.urlparse(bundle_url).path).name
                    if fname and not (bundles_dir / fname).exists():
                        log(f"  Downloading bundle {fname} from {bundle_url}")
                        try:
                            b_req = urllib.request.Request(bundle_url, headers={"User-Agent": "Mozilla/5.0"})
                            with urllib.request.urlopen(b_req, timeout=15) as br:
                                (bundles_dir / fname).write_bytes(br.read())
                        except Exception as be:
                            log(f"  Failed to fetch bundle {fname}: {be}")
        except Exception as e:
            log(f"  Error fetching {t}: {e}")

    # Stage 1A: AST Scan on Harvested Bundles using Validated Opengrep Rules
    rules_dir = AUTO_CF_DIR / "validated_ast_rules" / "javascript"
    opengrep_bin = shutil.which("opengrep") or str(Path.home() / ".local" / "bin" / "opengrep")
    if rules_dir.exists() and os.path.exists(opengrep_bin) and any(bundles_dir.glob("*.js")):
        log(f"Stage 1A: Running Opengrep AST scan on {bundles_dir} with validated rules...")
        cmd = [opengrep_bin, "--config", str(rules_dir), "--max-target-bytes", "25000000", "--json", str(bundles_dir)]
        try:
            s_res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if s_res.stdout.strip():
                ast_data = json.loads(s_res.stdout.strip())
                for r in ast_data.get("results", []):
                    check_id = r.get("check_id", "").split(".")[-1]
                    line = r.get("start", {}).get("line", 1)
                    msg = r.get("extra", {}).get("message", "").strip()
                    snippet = r.get("extra", {}).get("lines", "").strip()[:180]
                    stage1_candidates.append({
                        "type": "AST_VULNERABILITY",
                        "rule_id": check_id,
                        "target": str(Path(r.get("path", "")).name),
                        "evidence": f"Line {line}: {msg}\nSnippet: {snippet}",
                        "parameter": check_id,
                        "confidence": "medium"
                    })
                log(f"  Opengrep AST matched {len(ast_data.get('results', []))} candidate sinks")
        except Exception as se:
            log(f"  AST Scan error: {se}")

    # Stage 1B: Extract API endpoints and probe for unauthenticated exposure
    for js_file in bundles_dir.glob("*.js"):
        try:
            txt = js_file.read_text(encoding="utf-8", errors="ignore")
            eps = set(re.findall(r'/(?:api|v[0-9]|auth|oauth|user|admin|service)/[a-zA-Z0-9_/-]+', txt))
            discovered_endpoints.update(eps)
        except Exception:
            pass

    log(f"Stage 1B: Discovered {len(discovered_endpoints)} API endpoints in client code. Testing active responses...")
    for ep in sorted(discovered_endpoints)[:25]:
        for t in targets[:1]:
            test_url = urllib.parse.urljoin(t, ep)
            try:
                req = urllib.request.Request(test_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    b_len = int(resp.headers.get("Content-Length") or 0)
                    resp_bytes = resp.read()
                    text_body = resp_bytes.decode("utf-8", "replace")[:500]
                    log(f"  [HTTP {resp.status}] {ep} ({len(resp_bytes)} bytes)")
                    if resp.status == 200 and len(resp_bytes) > 20:
                        stage1_candidates.append({
                            "type": "UNAUTHENTICATED_ENDPOINT_EXPOSURE",
                            "target": test_url,
                            "evidence": f"HTTP 200 OK without Authorization header. Response: {text_body[:250]}",
                            "parameter": ep,
                            "confidence": "high"
                        })
            except urllib.error.HTTPError as he:
                if he.code in (401, 405):
                    body_text = he.read().decode("utf-8", "replace")[:250]
                    if "version" in ep or "info" in ep:
                        stage1_candidates.append({
                            "type": "INFORMATION_DISCLOSURE",
                            "target": test_url,
                            "evidence": f"HTTP {he.code} with exposed body data: {body_text}",
                            "parameter": ep,
                            "confidence": "medium"
                        })
            except Exception:
                pass

    # Deduplicate candidate findings by signature
    unique_candidates = []
    seen_signatures = set()
    for c in stage1_candidates:
        sig = f"{c.get('type')}:{c.get('rule_id') or c.get('parameter')}:{c.get('target')}"
        if sig not in seen_signatures:
            seen_signatures.add(sig)
            unique_candidates.append(c)

    log(f"Stage 1 Complete: {len(unique_candidates)} unique candidate signatures surfaced for Stage 2 deep reasoning")
    # Stage 2: Deep Exploit Verification (GLM-5.3 Thinking)
    verified_findings = []
    for c in unique_candidates:
        log(f"Stage 2 (GLM-5.3 Thinking): Adversarially verifying {c.get('type')} ({c.get('parameter')}) on {c.get('target')}")
        prompt = f"""You are an adversarial security auditor. Your job is to PROVE or DISPROVE this candidate vulnerability.
Target: {c.get('target')}
Finding Type: {c.get('type')}
Evidence: {c.get('evidence')}
Parameter: {c.get('parameter')}

Reason through:
1. Is this reachable by an unauthenticated attacker?
2. Is the sink sanitized or protected by modern browser defaults (e.g. textContent vs innerHTML, CSRF tokens, origin checks)?
3. Draft a concrete reproduction proof (curl command).

Output ONLY JSON:
{{
  "verdict": "CONFIRMED" or "FALSE_POSITIVE",
  "reasoning": "...",
  "cvss_estimate": 0.0,
  "poc_curl": "..."
}}
"""
        try:
            raw = chat_bridge(prompt, model=MODEL_THINKING, session_type="subagent", max_tokens=16000)
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                v = json.loads(raw[start : end + 1])
                if v.get("verdict") == "CONFIRMED":
                    c["verification"] = v
                    verified_findings.append(c)
                    findings_file.write_text(json.dumps(verified_findings, indent=2))
                    log(f"  [CONFIRMED] {c.get('type')} (CVSS {v.get('cvss_estimate')}) on {c.get('target')}")
                else:
                    log(f"  [FALSE POSITIVE KILLED] {c.get('type')}: {v.get('reasoning')[:80]}")
        except Exception as e:
            log(f"  Stage 2 error: {e}")

    findings_file.write_text(json.dumps(verified_findings, indent=2))
    log(f"Mass SAST Hunt completed: {len(verified_findings)} confirmed findings written to {findings_file}")


def main() -> None:
    ap = argparse.ArgumentParser(description="GLM-5.3 120B High-Throughput Workload Engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Print quota and pipeline status")

    deobf_p = sub.add_parser("deobfuscate", help="Deobfuscate JS bundle & map security sinks")
    deobf_p.add_argument("--input", required=True, type=Path, help="Path to minified JS file")
    deobf_p.add_argument("--limit-chunks", type=int, default=0, help="Max chunks to process")
    deobf_p.add_argument("--concurrency", type=int, default=4, help="Parallel worker threads")

    cve_p = sub.add_parser("distill-cve", help="Distill high-severity CVEs into Opengrep rules")
    cve_p.add_argument("--limit", type=int, default=20, help="Number of CVEs to process")
    cve_p.add_argument("--min-cvss", type=float, default=7.5, help="Minimum CVSS threshold")
    cve_p.add_argument("--concurrency", type=int, default=4, help="Parallel worker threads")

    sast_p = sub.add_parser("hunt-sast", help="Run 2-stage autonomous SAST hunt on bounty job")
    sast_p.add_argument("--job-id", required=True, help="Bounty job directory ID")

    args = ap.parse_args()
    if args.cmd == "status":
        cmd_status()
    elif args.cmd == "deobfuscate":
        cmd_deobfuscate(args.input, args.limit_chunks, args.concurrency)
    elif args.cmd == "distill-cve":
        cmd_distill_cve(args.limit, args.min_cvss, args.concurrency)
    elif args.cmd == "hunt-sast":
        cmd_hunt_sast(args.job_id)


if __name__ == "__main__":
    main()
