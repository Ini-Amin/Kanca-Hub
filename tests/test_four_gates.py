#!/usr/bin/env python3
"""Stage 4/5: the four pre-submit gates, each driven with a synthetic finding.

Every gate is exercised BOTH ways — the case it must reject and the case it
must let through. A gate that only ever passes is not a gate, and the two
failure modes this suite exists to catch are the quiet ones:

  * a gate whose condition can never be false (the host_in_scope tuple trap),
  * a gate that short-circuits the wrong way, sending traffic before scope
    has been decided.

Run after ANY change to run_four_gates / gate_* / stage5_report:
    python3 tests/test_four_gates.py        (or pytest tests/test_four_gates.py)
"""
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

# ABH lives beside this repo on a workstation and at /root on blu. The gates
# locate poc_bundler.py through ABH, so point ABH_DIR at the real checkout
# before abh_hunt is imported (it reads the var at import time).
os.environ.setdefault(
    "ABH_DIR",
    str(REPO.parent / "Agentic-Bug-Hunter"
        if (REPO.parent / "Agentic-Bug-Hunter" / "bughunter" / "agent.py").is_file()
        else "/root/Agentic-Bug-Hunter"),
)

import abh_hunt as H  # noqa: E402

fails = 0


def check(cond, label):
    global fails
    if not cond:
        fails += 1
    print("%-4s %s" % ("OK" if cond else "FAIL", label))


ENV = {
    "BRAIN_MODEL": "Zcode/GLM-5.3",
    "BOUNTY_MODEL_FALLBACKS": ("Zcode/GLM-5.3,THK/deepseek-v4.1-flash:free,"
                               "nr/step-5-preview,ocz/mimo-v2.6-flash-free"),
}
INS = ["example.com"]
OUTS = ["admin.example.com"]
# Tests must not make network calls: dup checks are exercised through this
# stand-in unless a test is specifically about _h1_dup_check's own plumbing.
CLEAN_H1 = lambda p, k: ([], True, "")  # noqa: E731


def F(**kw):
    base = dict(
        title="OAuth redirect_uri open redirect accepts an arbitrary host",
        url="https://example.com/oauth/callback?next=https://evil.test",
        vuln_class="oauth",
        severity="high",
        method="GET",
        indicator="state=",
        indicator_status=200,
        impact="Attacker can steal the authorization code by hosting the "
               "callback on their own origin.",
    )
    base.update(kw)
    return base


def main() -> int:
    global fails
    fails = 0

    # ---------- model family / challenger selection ----------
    print("1. model_family ignores the routing prefix (or the gate pairs a "
          "model with itself)")
    check(H.model_family("Zcode/GLM-5.3") == "glm", "Zcode/GLM-5.3 -> glm")
    check(H.model_family("THK/deepseek-v4.1-flash:free") == "deepseek",
          "deepseek tag stripped")
    check(H.model_family("ocz/space-bunny-free") == "space", "space-bunny-free -> space")
    check(H.model_family("ag/gemini-3.8-flash-high") == "gemini", "gemini -> gemini")
    check(H.model_family("") == "", "empty -> empty")

    print("2. pick_challenger returns a different family, or nothing")
    check(H.pick_challenger("Zcode/GLM-5.3", ENV) == "THK/deepseek-v4.1-flash:free",
          "GLM hunter challenges with deepseek")
    check(H.model_family(H.pick_challenger("Zcode/GLM-5.3", ENV)) != "glm",
          "chosen challenger is not the hunter's family")
    same = {"BRAIN_MODEL": "Zcode/GLM-5.3",
            "BOUNTY_MODEL_FALLBACKS": "Zcode/GLM-5.3,OTHK/GLM-9"}
    check(H.pick_challenger("Zcode/GLM-5.3", same) is None,
          "GLM-only fallbacks -> None (no fake second opinion)")
    check(H.model_chain(ENV)[0] == "Zcode/GLM-5.3", "chain starts at the primary")

    # ---------- gate 1: SCOPE ----------
    print("3. SCOPE fails closed on out-of-scope, empty allow list, no host")
    ok, why = H.gate_scope(F(), INS, OUTS)
    check(ok, f"in-scope host passes ({why})")
    ok, why = H.gate_scope(F(url="https://evil.test/cb"), INS, OUTS)
    check(not ok, f"out-of-scope host rejected ({why})")
    ok, why = H.gate_scope(F(url="https://admin.example.com/cb"), INS, OUTS)
    check(not ok, f"explicitly denied host rejected ({why})")
    ok, why = H.gate_scope(F(url=""), INS, OUTS)
    check(not ok, f"no URL rejected ({why})")
    ok, why = H.gate_scope(F(), [], OUTS)
    check(not ok, f"empty in-scope list rejected ({why})")
    ok, why = H.gate_scope(F(host="sub.example.com"), INS, OUTS)
    check(ok, f"in-scope subdomain passes ({why})")

    # ---------- gate 2: DUPCHECK ----------
    print("4. DUPCHECK rejects duplicates, accepts novel findings")
    with tempfile.TemporaryDirectory() as td:
        jobs = Path(td)
        ok, why = H.gate_dupcheck(F(), program="acme", h1=CLEAN_H1, jobs_dir=jobs)
        check(ok, f"novel finding passes ({why})")

        j = jobs / "job-old"
        j.mkdir()
        (j / "report.md").write_text(
            "# Prior work\n\n## OAuth redirect_uri open redirect accepts an "
            "arbitrary host\n\nbody\n", encoding="utf-8")
        ok, why = H.gate_dupcheck(F(), program="acme", h1=CLEAN_H1, jobs_dir=jobs)
        check(not ok, f"identical title in report.md rejected ({why})")
        ok, why = H.gate_dupcheck(
            F(title="OAuth redirect_uri open redirect accepts arbitrary host!"),
            program="acme", h1=CLEAN_H1, jobs_dir=jobs)
        check(not ok, f"punctuation-only difference still rejected ({why})")

        ok, why = H.gate_dupcheck(
            F(title="Something else entirely",
              url="https://example.com/x", vuln_class="sqli"),
            program="acme", h1=CLEAN_H1, jobs_dir=jobs)
        check(ok, f"unrelated title passes ({why})")

    print("5. DUPCHECK rejects a published CVE (public knowledge, not a finding)")
    ok, why = H.gate_dupcheck(
        F(title="RCE via CVE-2021-44228 in log4j 2.14.1", cve="cve-2021-44228"),
        program="acme", h1=CLEAN_H1)
    check(not ok, f"claimed CVE rejected ({why})")
    check("CVE-2021-44228" in why, "reason names the CVE")
    # A CVE mentioned only as background reading must NOT kill the finding.
    ok, why = H.gate_dupcheck(
        F(title="Novel bypass in the same library",
          description="Similar in spirit to CVE-2021-44228 but a different sink."),
        program="acme", h1=CLEAN_H1)
    check(ok, f"CVE cited in prose only is not a claim ({why})")
    # cve.db supplies the advisory text for the reason line when present.
    with tempfile.TemporaryDirectory() as td:
        import sqlite3
        db = Path(td) / "cve.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE cve(id TEXT PRIMARY KEY, description TEXT)")
        con.execute("INSERT INTO cve VALUES (?, ?)",
                    ("CVE-2021-44228", "Apache Log4j2 JNDI features do not "
                                      "protect against attacker controlled LDAP"))
        con.commit()
        con.close()
        _, why = H.gate_dupcheck(F(title="x CVE-2021-44228", cve="CVE-2021-44228"),
                                 program="acme", h1=CLEAN_H1, cve_db=db)
        check("Log4j2" in why, f"local cve.db description pulled in ({why[:70]})")

    print("6. DUPCHECK consults H1 and does not launder an outage as a pass")
    ok, why = H.gate_dupcheck(F(), program="acme",
                              h1=lambda p, k: ([{"title": "Same bug, disclosed"}], True, ""))
    check(not ok, f"H1 disclosed report rejected ({why})")
    ok, why = H.gate_dupcheck(F(), program="acme",
                              h1=lambda p, k: ([], True, ""))
    check(ok, f"H1 reachable and clean passes ({why})")
    ok, why = H.gate_dupcheck(F(), program="acme",
                              h1=lambda p, k: ([], False, "HTTPSConnectionPool: timeout"))
    check(ok, f"H1 outage does not freeze reporting ({why})")
    check("UNREACHABLE" in why, "the blind check is flagged on the gate line")

    print("6b. _h1_dup_check tells 'no duplicates' apart from 'request failed'")
    # check_h1_dups() collapses both into []; its only difference is a line on
    # stderr. Read that correctly or the gate waves findings through outages.
    validate_path = Path(os.environ["ABH_DIR"]) / "bughunter" / "tools" / "validate.py"
    if not validate_path.is_file():
        # ABH_DIR might have been overwritten by an earlier test (e.g. test_gigs_api)
        real_abh = Path("/home/amen/Agentic-Bug-Hunter")
        if (real_abh / "bughunter" / "tools" / "validate.py").is_file():
            validate_path = real_abh / "bughunter" / "tools" / "validate.py"
            bh_dir = str(real_abh / "bughunter")
            if bh_dir not in sys.path:
                sys.path.insert(0, bh_dir)
        else:
            print("  SKIP 6b: validate.py not found at", validate_path)
            validate_path = None
    else:
        bh_dir = str(Path(os.environ["ABH_DIR"]) / "bughunter")
        if bh_dir not in sys.path:
            sys.path.insert(0, bh_dir)
    if validate_path:
        # Clear any stale 'tools' namespace package from sys.modules so the real
        # bughunter.tools package (with __init__.py) gets loaded from bh_dir
        for k in list(sys.modules.keys()):
            if k == "tools" or k.startswith("tools."):
                del sys.modules[k]
        spec = importlib.util.spec_from_file_location("tools.validate", validate_path)
        vmod = importlib.util.module_from_spec(spec)
        sys.modules["tools.validate"] = vmod
        spec.loader.exec_module(vmod)
        real_h1 = vmod.check_h1_dups
        try:
            def _failed(p, k):
                print("  HackerOne duplicate check failed: HTTP Error 429: Too Many",
                      file=sys.stderr)
                return []
            vmod.check_h1_dups = _failed
            hits, reachable, detail = H._h1_dup_check("acme", "oauth")
            check(hits == [] and not reachable, f"failure detected, not read as clean ({detail[:60]})")

            vmod.check_h1_dups = lambda p, k: []
            hits, reachable, detail = H._h1_dup_check("acme", "oauth")
            check(hits == [] and reachable, "genuine empty result reads as reachable")

            vmod.check_h1_dups = lambda p, k: [{"title": "dup"}]
            hits, reachable, detail = H._h1_dup_check("acme", "oauth")
            check(len(hits) == 1 and reachable, "hits come back through")

            hits, reachable, detail = H._h1_dup_check("", "")
            check(not reachable, f"missing program/keyword reported, not guessed ({detail})")
        finally:
            vmod.check_h1_dups = real_h1

    # ---------- gate 3: VM-PoC ----------
    print("7. VM-PoC verifies by replaying, and fails when it cannot")
    ok, why = H.gate_vm_poc(F(), poc=lambda f: (200, "ok?state=abc", ""))
    check(ok, f"indicator present in body passes ({why})")
    ok, why = H.gate_vm_poc(F(), poc=lambda f: (200, "nothing to see here", ""))
    check(not ok, f"indicator missing rejected ({why})")
    ok, why = H.gate_vm_poc(F(indicator="", indicator_status=403),
                            poc=lambda f: (403, "denied", ""))
    check(ok, f"status-only indicator matches ({why})")
    ok, why = H.gate_vm_poc(F(indicator="", indicator_status=403),
                            poc=lambda f: (200, "ok", ""))
    check(not ok, f"status mismatch rejected ({why})")
    ok, why = H.gate_vm_poc(F(indicator="", indicator_status=None))
    check(not ok, f"no indicator at all rejected, fail closed ({why})")
    ok, why = H.gate_vm_poc(F(), poc=lambda f: (None, None, "replay exited 7: boom"))
    check(not ok, f"failed replay surfaces its reason ({why})")
    check("boom" in why, "the reason is propagated, not swallowed")

    print("8. real poc_bundler refuses a mutating method before any traffic")
    bundler = Path(os.environ["ABH_DIR"]) / "bughunter" / "tools" / "poc_bundler.py"
    if not bundler.is_file():
        real_bundler = Path("/home/amen/Agentic-Bug-Hunter/bughunter/tools/poc_bundler.py")
        if real_bundler.is_file():
            bundler = real_bundler
    if bundler.is_file():
        with tempfile.TemporaryDirectory() as td:
            p = subprocess.run(
                [sys.executable, str(bundler), "capture", "http://127.0.0.1:9/",
                 "-X", "PUT", "--json", "-o", td],
                capture_output=True, text=True, timeout=60, env=dict(os.environ))
        check(p.returncode == 2, f"PUT exits 2 (got {p.returncode})")
        check("confirm-unsafe" in p.stderr, "explains --confirm-unsafe")
        status, body, note = H._poc_replay(
            F(url="https://example.com/api/v1/thing", method="PUT"), timeout=5)
        check(body is None, "unattended replay of PUT is refused, not attempted")
        check("confirm-unsafe" in note, f"refusal reason reaches the gate ({note[:60]})")
    else:
        check(False, f"poc_bundler.py missing at {bundler}")

    # ---------- gate 4: CHALLENGER ----------
    print("9. CHALLENGER needs an independent model and a SUBMIT verdict")
    ok, why = H.gate_challenger(F(), env=ENV, challenge=lambda m, t: ("SUBMIT", "yes"))
    check(ok, f"SUBMIT passes ({why})")
    check("deepseek" in why, "reason names the challenger model")
    ok, why = H.gate_challenger(F(), env=ENV, challenge=lambda m, t: ("DROP", "Q3 no impact"))
    check(not ok, f"DROP rejected ({why})")
    ok, why = H.gate_challenger(F(), env=ENV, challenge=lambda m, t: ("UNKNOWN", ""))
    check(not ok, f"UNKNOWN (no answer) rejected ({why})")
    ok, why = H.gate_challenger(F(), env=ENV, challenge=lambda m, t: (_boom(), ""))
    check(not ok, f"challenger exception rejected, fail closed ({why})")
    only_glm = {"BRAIN_MODEL": "Zcode/GLM-5.3",
                "BOUNTY_MODEL_FALLBACKS": "Zcode/GLM-5.3,OTHK/GLM-9"}
    ok, why = H.gate_challenger(F(), env=only_glm, challenge=lambda m, t: ("SUBMIT", ""))
    check(not ok, f"no different family available blocks submit ({why})")
    seen = []

    def record(m, t):
        seen.append((m, t))
        return "SUBMIT", ""

    H.gate_challenger(F(), env=ENV, challenge=record)
    check(len(seen) == 1 and seen[0][0] == "THK/deepseek-v4.1-flash:free",
          "challenger is called with the chosen model")
    check("TITLE:" in seen[0][1] and "URL:" in seen[0][1],
          "challenger is shown the finding's facts")

    # ---------- run_four_gates: ordering + short circuit ----------
    print("10. run_four_gates runs all four, in order, and stops at a failure")
    calls = {"poc": 0, "chal": 0}

    def fake_poc(f):
        calls["poc"] += 1
        return 200, "state=abc", ""

    def fake_chal(m, t):
        calls["chal"] += 1
        return "SUBMIT", ""

    with tempfile.TemporaryDirectory() as td:
        all_pass, log = H.run_four_gates(
            F(), INS, OUTS, program="acme", env=ENV, jobs_dir=Path(td),
            h1=lambda p, k: ([], True, ""), poc=fake_poc, challenge=fake_chal)
        check(all_pass, "clean finding clears all four")
        check(H.GATE_PASSED in log, "log carries the 4/4 marker")
        for g in H.GATE_NAMES:
            check(f"GATE {g}: PASS" in log, f"  {g} present and PASS")
        check(calls["poc"] == 1 and calls["chal"] == 1, "gates 3 and 4 each ran once")

        calls["poc"] = calls["chal"] = 0
        blocked, log = H.run_four_gates(
            F(url="https://evil.test/cb"), INS, OUTS, program="acme", env=ENV,
            jobs_dir=Path(td), h1=lambda p, k: ([], True, ""),
            poc=fake_poc, challenge=fake_chal)
        check(not blocked, "out-of-scope finding is blocked")
        check("BLOCKED at SCOPE" in log, "says where it stopped")
        check(H.GATE_PASSED not in log, "no 4/4 marker on a blocked run")
        check(calls["poc"] == 0, "PoC replay never ran against an unapproved host")
        check(calls["chal"] == 0, "challenger never ran after a SCOPE failure")

        calls["poc"] = calls["chal"] = 0
        dup_j = Path(td) / "prev"
        dup_j.mkdir()
        (dup_j / "report.md").write_text(
            "## " + F()["title"] + "\n", encoding="utf-8")
        blocked, log = H.run_four_gates(
            F(), INS, OUTS, program="acme", env=ENV, jobs_dir=Path(td),
            h1=lambda p, k: ([], True, ""), poc=fake_poc, challenge=fake_chal)
        check(not blocked and "BLOCKED at DUPCHECK" in log, f"dup stops at gate 2 ({log.splitlines()[-1]})")
        check(calls["poc"] == 0, "no replay once a duplicate is known")

    # ---------- stage 5: report + escalation ----------
    print("11. stage5 writes the report only for a 4/4 finding")
    with tempfile.TemporaryDirectory() as td:
        jobs = Path(td)
        ref = H.stage5_report(F(), "GATE RESULT: BLOCKED at SCOPE",
                              job_id="j-blocked", jobs_dir=jobs, alert=False)
        check(ref is None, "stage5 refuses a non-passing gate log")
        check(not (jobs / "j-blocked" / "report.md").exists(), "no report written for it")
        check(not (jobs / "_ledger.json").exists(), "nothing added to the ledger")

        good_log = "GATE SCOPE: PASS — ok\nGATE DUPCHECK: PASS — ok\n" \
                   "GATE VM-PoC: PASS — ok\nGATE CHALLENGER: PASS — ok\n" + H.GATE_PASSED + " — cleared for report"
        path = H.stage5_report(F(), good_log, job_id="j-good", program="acme",
                               jobs_dir=jobs, alert=False)
        check(path is not None and path.is_file(), f"report written to {path}")
        text = path.read_text(encoding="utf-8")
        check(text.startswith("# OAuth redirect_uri"), "report has the title")
        check("state=" in text, "reproduction states the indicator")
        check("GATE CHALLENGER: PASS" in text, "gate log is embedded in the report")
        rows = json.loads((jobs / "_ledger.json").read_text(encoding="utf-8"))
        check(len(rows) == 1, "ledger has exactly one row")
        check(rows[0]["url"] == F()["url"], "ledger row records the URL")
        check(rows[0]["program"] == "acme", "ledger row records the program")
        # The ledger feeds gate 2: a re-submission of the same title is a dup.
        ok, why = H.gate_dupcheck(F(), program="acme", h1=CLEAN_H1, jobs_dir=jobs)
        check(not ok, f"ledger closes the loop — second submit rejected ({why})")

    # ---------- wiring: ABH findings_log -> gate findings ----------
    print("12. findings_log rows are shaped into gate findings")
    row = {"tool": "run_cors_check", "severity": "HIGH", "ts": "t",
           "text": 'https://app.example.com/api/cors reflects origin with '
                   'credentials for "allow-origin: https://evil.test"'}
    f = H.finding_from_log(row, "example.com")
    check(f["url"].startswith("https://app.example.com/api/cors"),
          f"URL extracted ({f['url']})")
    check(f["vuln_class"] == "cors", f"vuln_class from tool name ({f['vuln_class']})")
    check(f["severity"] == "HIGH", "severity preserved")
    check(f["indicator"] == "allow-origin: https://evil.test",
          f"quoted response fragment becomes the indicator ({f['indicator']})")

    f = H.finding_from_log(
        {"tool": "run_vuln_scan", "severity": "CRITICAL",
         "text": "leaked mailbox admin.team@example.org on https://x.example.com/a"})
    check(f["indicator"] == "admin.team@example.org",
          f"a leaked address is the proof itself ({f['indicator']})")
    check(H.vuln_class_for("run_graphql_audit") == "graphql", "known tool mapped")
    check(H.vuln_class_for("run_something_new") == "something_new",
          "unknown tool degrades instead of raising")

    # No URL -> gate 1 must block; no indicator -> gate 3 must block.
    f = H.finding_from_log({"tool": "run_recon", "severity": "MEDIUM",
                            "text": "nginx 1.25.3 detected on the apex"})
    check(f["url"] == "", "no host in the line stays empty (not invented)")
    check(f["indicator"] == "", "no provable literal stays empty (not invented)")
    ok, why = H.gate_scope(f, INS, OUTS)
    check(not ok, f"scope blocks an unpointable finding ({why})")
    ok, why = H.gate_vm_poc(f, poc=lambda g: (200, "x", ""))
    check(not ok, f"vm-poc blocks an unverifiable finding ({why})")

    # A scheme-less host/path is reconstructed, but scope is still decided on it.
    f = H.finding_from_log({"tool": "run_vuln_scan", "severity": "HIGH",
                            "text": "app.example.com/admin -> 403 with bypass"})
    check(f["url"] == "https://app.example.com/admin",
          f"scheme-less host/path reconstructed ({f['url']})")
    ok, _ = H.gate_scope(f, INS, OUTS)
    check(ok, "reconstructed host still passes the real scope check")
    f = H.finding_from_log({"tool": "run_vuln_scan", "severity": "HIGH",
                            "text": "evil.test/x -> 403"})
    ok, why = H.gate_scope(f, INS, OUTS)
    check(not ok, f"reconstructed host is still scope-checked ({why})")

    print("13. session file is read, and unreadable/absent input is not fatal")
    with tempfile.TemporaryDirectory() as td:
        check(H.load_abh_findings("", "example.com") == [], "empty session -> []")
        check(H.load_abh_findings(str(Path(td) / "nope.json")) == [], "missing file -> []")
        bad = Path(td) / "s.json"
        bad.write_text("{not json", encoding="utf-8")
        check(H.load_abh_findings(str(bad)) == [], "corrupt file -> []")
        good = Path(td) / "ok.json"
        good.write_text(json.dumps({"findings_log": [
            {"tool": "run_cors_check", "severity": "HIGH", "text": "https://a.example.com/c1 origin reflected"},
            {"tool": "run_cors_check", "severity": "HIGH", "text": "https://a.example.com/c1 origin reflected"},
            {"tool": "run_cors_check", "severity": "HIGH", "text": "https://b.example.com/c2 origin reflected"},
            {"tool": "run_recon", "severity": "LOW", "text": "tech nginx"},
        ]}), encoding="utf-8")
        rows = H.load_abh_findings(str(good), "example.com")
        check(len(rows) == 3, f"duplicate rows collapsed ({len(rows)})")
        check(rows[0]["severity"] == "HIGH", "severity carried through")

        print("14. gate_findings reports what happened and only reports winners")
        import zen_worker as _zw
        _zw_avail = _zw.available
        _zw.available = lambda: False
        try:
            note, _n = H.gate_findings(str(good), "example.com", program="acme",
                                   ins=INS, outs=OUTS, jobs_dir=Path(td) / "jobs",
                                   alert=False, h1=CLEAN_H1,
                                   challenge=lambda m, t: ("SUBMIT", ""))
            check(note.startswith("2 gated"), f"counts the gated rows ({note})")
            check("cleared" in note, "says how many cleared")
        finally:
            _zw.available = _zw_avail
        check("cleared" in note, "says how many cleared")

        low = Path(td) / "low.json"
        low.write_text(json.dumps({"findings_log": [
            {"tool": "run_recon", "severity": "LOW", "text": "tech nginx"}]}),
            encoding="utf-8")
        note, _n = H.gate_findings(str(low), "example.com", jobs_dir=Path(td) / "jobs",
                               alert=False, h1=CLEAN_H1)
        check("none above LOW" in note, f"LOW rows are not gated ({note})")
        empty = Path(td) / "empty.json"
        empty.write_text(json.dumps({"findings_log": []}), encoding="utf-8")
        note, _n = H.gate_findings(str(empty), "example.com", jobs_dir=Path(td) / "jobs",
                               alert=False, h1=CLEAN_H1)
        check(note == "no findings recorded", f"no rows is explicit ({note})")

    print("RESULT: %s" % ("ALL PASS" if fails == 0 else "%d FAILURES" % fails))
    return fails


def _boom():
    raise RuntimeError("model gateway down")


def test_four_gates():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
