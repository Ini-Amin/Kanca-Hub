#!/usr/bin/env python3
"""Zen worker: parsing, fail-open behaviour, and the advisory-only contract."""
import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import abh_hunt as A      # noqa: E402
import zen_worker as Z    # noqa: E402


def test_parse_triage_accepts_strict_format():
    r = Z.parse_triage("VERDICT: FALSE\nCONFIDENCE: 95\nREASON: zero matches")
    assert r == {"verdict": "FALSE", "confidence": 95, "reason": "zero matches"}


def test_parse_triage_rejects_unknown_verdict():
    assert Z.parse_triage("VERDICT: MAYBE\nCONFIDENCE: 50") is None
    assert Z.parse_triage("no verdict at all") is None


def test_parse_scope_json_normalises_and_rejects_garbage():
    j = Z.parse_scope_json('x {"in_scope":["*.Okta.com"],"out_of_scope":[]} y')
    assert j["in_scope"] == ["*.okta.com"]
    assert Z.parse_scope_json("not json") is None
    assert Z.parse_scope_json('{"login_wall": true}')["login_wall"] is True


def test_run_returns_none_when_cli_missing():
    old = os.environ.get("OPENCODE_BIN")
    os.environ["OPENCODE_BIN"] = "/nonexistent/opencode"
    try:
        assert Z.available() is False
        assert Z.run("hi", timeout=5) is None
    finally:
        if old is None:
            os.environ.pop("OPENCODE_BIN", None)
        else:
            os.environ["OPENCODE_BIN"] = old


def _session_with(rows):
    td = Path(tempfile.mkdtemp())
    sid = "20260101T000000000000-cccc"
    sdir = td / "bughunter" / "recon" / "example.test" / "sessions" / sid
    sdir.mkdir(parents=True)
    (sdir / "agent_session.json").write_text(json.dumps({"findings_log": rows}))
    return td, sid


def test_gates_still_run_when_zen_unavailable():
    """A dead worker must never stop a real finding from being gated."""
    td, sid = _session_with([{"tool": "run_vuln_scan", "severity": "HIGH",
                              "text": "SQLi injectable at /login?id=1"}])
    old_bin, old_abh = os.environ.get("OPENCODE_BIN"), A.ABH
    os.environ["OPENCODE_BIN"] = "/nonexistent/opencode"
    A.ABH = td
    try:
        note, _n = A.gate_findings(sid, "example.test", alert=False)
        assert "gated" in note, note
    finally:
        A.ABH = old_abh
        if old_bin is None:
            os.environ.pop("OPENCODE_BIN", None)
        else:
            os.environ["OPENCODE_BIN"] = old_bin


def test_false_positive_dropped_before_gates():
    td, sid = _session_with([{"tool": "run_cve_intel", "severity": "HIGH",
                              "text": "range matches: 0"}])
    old_abh, old_avail, old_tri = A.ABH, Z.available, Z.triage_finding
    A.ABH = td
    Z.available = lambda: True
    Z.triage_finding = lambda f, **kw: {"verdict": "FALSE", "confidence": 95,
                                        "reason": "zero matches"}
    try:
        note, _n = A.gate_findings(sid, "example.test", alert=False)
        assert "rejected by triage" in note, note
    finally:
        A.ABH, Z.available, Z.triage_finding = old_abh, old_avail, old_tri


def main() -> int:
    import __main__ as mod
    fails = 0
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        fn = getattr(mod, name)
        if not callable(fn):
            continue
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as exc:
            fails += 1
            print(f"  FAIL {name}: {exc}")
    print(f"\nRESULT: {fails} FAILURES" if fails else "\nRESULT: all zen tests pass")
    return fails


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
