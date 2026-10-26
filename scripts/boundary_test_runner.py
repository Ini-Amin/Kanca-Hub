#!/usr/bin/env python3
"""scripts/boundary_test_runner.py — Differential Boundary & Parser Testing.

Tests edge-case boundary inputs directly against modern installed runtimes
(PHP 8.5.12, Java 25, Python 3.13) to detect parser differentials and loose validation.
Safe, benign, non-destructive assertions only.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def test_php_path_normalization() -> dict:
    """Test path normalization and wrapper handling in local PHP 8.5."""
    code = r"""
    $inputs = [
        'dotdot' => 'foo/../bar',
        'nullbyte' => "foo\\0bar",
        'unicode' => "foo/\u{202E}bar",
        'wrapper' => 'data://text/plain;base64,c3VjY2Vzcz0x',
    ];
    $results = [];
    foreach ($inputs as $k => $v) {
        $results[$k] = [
            'basename' => basename($v),
            'filter_var' => filter_var($v, FILTER_DEFAULT),
            'is_wrapper' => (bool)preg_match('#^[a-zA-Z0-9+-.]+://#', $v),
        ];
    }
    echo json_encode($results);
    """
    try:
        res = subprocess.run(["php", "-r", code], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return {"status": "ok", "runtime": "PHP 8.5", "output": json.loads(res.stdout)}
        return {"status": "error", "error": res.stderr.strip()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def test_php_type_juggling() -> dict:
    """Test loose vs strict comparison behavior in PHP 8.5."""
    code = r"""
    $checks = [
        'loose_zero_string' => ('0' == 0),
        'loose_string_hash' => ('0e12345' == '0e67890'),
        'array_in_array' => in_array([], ['test'], false),
        'strict_in_array' => in_array([], ['test'], true),
    ];
    echo json_encode($checks);
    """
    try:
        res = subprocess.run(["php", "-r", code], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return {"status": "ok", "runtime": "PHP 8.5", "output": json.loads(res.stdout)}
        return {"status": "error", "error": res.stderr.strip()}
    except Exception as e:
        return {"status": "error", "error": str(e)}
def test_java_parser_differentials() -> dict:
    """Test Java 25 Path normalization and URL differentials."""
    java_code = """
    import java.net.URI;
    import java.nio.file.Paths;
    public class JavaProbe {
        public static void main(String[] args) throws Exception {
            String p = Paths.get("/app/uploads/../../../etc/passwd").normalize().toString();
            String url = new URI("http://target.com#@attacker.com").getHost();
            System.out.println(String.format("{\\\"normalized_path\\\":\\\"%s\\\",\\\"host\\\":\\\"%s\\\"}", p, url));
        }
    }
    """
    try:
        tmp = Path("/tmp/JavaProbe.java")
        tmp.write_text(java_code)
        res = subprocess.run(["java", str(tmp)], capture_output=True, text=True, timeout=8)
        tmp.unlink(missing_ok=True)
        if res.returncode == 0:
            return {"status": "ok", "runtime": "Java 25", "output": json.loads(res.stdout.strip())}
        return {"status": "error", "error": res.stderr.strip()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def test_node_url_differentials() -> dict:
    """Test Node.js 24 WHATWG URL parser differentials."""
    cmd = [
        "node", "-e",
        'const u = new URL("http://target.com\\\\@attacker.com");'
        'const u2 = new URL("http://target.com:80@attacker.com");'
        'console.log(JSON.stringify({backslash_host: u.hostname, userinfo_host: u2.hostname}));'
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return {"status": "ok", "runtime": "Node 24", "output": json.loads(res.stdout.strip())}
        return {"status": "error", "error": res.stderr.strip()}
    except Exception as e:
        return {"status": "error", "error": str(e)}



def main():
    print("=== Differential Boundary Test Suite (PHP 8.5 / Java 25) ===")
    r1 = test_php_path_normalization()
    print("\n[1] PHP 8.5 Path & Wrapper Normalization:")
    print(json.dumps(r1, indent=2))

    r2 = test_php_type_juggling()
    print("\n[2] PHP 8.5 Type Juggling & Array Handling:")
    print(json.dumps(r2, indent=2))

    r3 = test_java_parser_differentials()
    print("\n[3] Java 25 Path & URI Normalization:")
    print(json.dumps(r3, indent=2))

    r4 = test_node_url_differentials()
    print("\n[4] Node.js 24 WHATWG URL Parser Differentials:")
    print(json.dumps(r4, indent=2))

    report = {
        "php_path": r1,
        "php_type_juggling": r2,
        "java_differentials": r3,
        "node_url_differentials": r4,
    }
    Path("reports/boundary_test_results.json").write_text(json.dumps(report, indent=2))
    print("\n[✓] Results saved to reports/boundary_test_results.json")


if __name__ == "__main__":
    main()
