#!/usr/bin/env python3
"""Scope parsing + gate 1. Run after ANY change to parse_scope / host_in_scope.

Two real page layouts are covered, both captured from live Bugcrowd briefs:
  * bitso-mbb-og : 2 cards (1 in, 1 out)
  * okta         : 4 cards (3 in, 1 out) - the layout that broke the first parser
"""
import sys
from pathlib import Path
sys.path.insert(0, "/home/amen/Auto-FreeCF/scripts")
from bounty_run import parse_scope, host_in_scope, filter_hosts, _scannable, scope_warnings

fails = 0


def check(cond, label):
    global fails
    if not cond:
        fails += 1
    print("%-4s %s" % ("OK" if cond else "FAIL", label))


# ---------- layout A: per-target cards, one in + one out ----------
BITSO = """Targets
In Scope
In scope
Payment reward chart
Bitso.com - Bitso is a cryptocurrency platform that enables users to invest,
trade, and make borderless payments using digital assets.
Name / Location\tTags\tKnown issues
bitso.com
https://bitso.com
	6
https://nvio.mx/
	4
onchain.cc
https://onchain.cc
Out of Scope
Out of scope
Name / Location\tTags\tKnown issues
help.bitso.com
		0
status.bitso.com
		0
"""
ins, outs = parse_scope(BITSO)
check("bitso.com" in ins and "onchain.cc" in ins, "bitso: apexes are in scope")
check("help.bitso.com" in outs and "status.bitso.com" in outs, "bitso: carve-outs are out")
check("help.bitso.com" not in ins, "bitso: out-of-scope not leaked into in-scope")
check(not any(re_ in " ".join(ins) for re_ in ("cryptocurrency platform",)),
      "bitso: prose description excluded from targets")

# ---------- layout B: FOUR cards (this is what returned 0 in-scope) ----------
OKTA = """Targets
Okta Personal
Out of scope
Register for Okta Personal
Go to https://personal.trexcloud.com and sign up.
Name / Location\tTags\tKnown issues
personal.trexcloud.com
\t
API Testing
Okta Privileged Access
In scope
Payment reward chart
Name / Location\tTags\tKnown issues
bugcrowd-pam-###.oktapreview.com
\t
bugcrowd-pam-###.pam.oktapreview.com
\t
Okta Workflows
In scope
Focus Areas
Out of Scope
Testing for Denial of Service issues and testing with any sort of automation is STRICTLY out of scope.
Name / Location\tTags\tKnown issues
https://bugcrowd-pam-###.workflows.oktapreview.com
\t
Okta Device Access
In scope
Payment reward chart
Name / Location\tTags\tKnown issues
bugcrowd-pam-###.devices.oktapreview.com
\t
"""
i2, o2 = parse_scope(OKTA)
check(len(i2) >= 2, "okta: multiple in-scope cards collected (got %d)" % len(i2))
check("bugcrowd-pam-###.oktapreview.com" in i2, "okta: privileged-access card in scope")
check("bugcrowd-pam-###.devices.oktapreview.com" in i2, "okta: device-access card in scope")
check("personal.trexcloud.com" in o2, "okta: personal card out of scope")
check("personal.trexcloud.com" not in i2, "okta: out card not leaked into in")

# ---------- gate 1 ----------
IN = ["bitso.com", "onchain.cc"]
OUT = ["help.bitso.com", "status.bitso.com", "dev.bitso.com", "blog.bitso.com",
       "stage.bitso.com", "*.otc.bitso.com", "*.efx.bitso.com", "sandbox.bitso.com",
       "api-sandbox.bitso.com"]

CASES = [
    ("bitso.com", True, "apex in scope"),
    ("www.bitso.com", True, "subdomain of in-scope apex"),
    ("api.bitso.com", True, "subdomain"),
    ("www.efx.bitso.com", False, "LEAKED ONCE: *.efx.bitso.com"),
    ("efx.bitso.com", False, "*.X denies the apex too"),
    ("sandbox-rfq.bitso.com", True, "SIBLING of sandbox.bitso.com (warned)"),
    ("sandbox.bitso.com", False, "out-of-scope exact"),
    ("api-sandbox.bitso.com", False, "out-of-scope exact"),
    ("help.bitso.com", False, "out-of-scope exact"),
    ("x.otc.bitso.com", False, "out-of-scope wildcard"),
    ("evilbitso.com", False, "suffix trap: not a subdomain"),
    ("bitso.com.evil.net", False, "suffix trap reversed"),
    ("onchain.cc", True, "second in-scope root"),
    ("api.onchain.cc", True, "subdomain"),
    ("random.example.com", False, "unrelated"),
]
for host, want, why in CASES:
    got, reason = host_in_scope(host, IN, OUT)
    check(got == want, "gate1 %-26s %-5s %s" % (host, got, why))

# ---------- seed derivation ----------
SEEDS = [
    (["bitso.com"], ["bitso.com"], [], ["bitso.com"], "apex seed"),
    (["*.bitso.com"], ["*.bitso.com"], [], ["bitso.com"], "wildcard -> base"),
    (["*.bitso.com"], ["*.bitso.com"], ["*.bitso.com"], [], "wildcard fully denied"),
    (["https://bitso.com/"], ["https://bitso.com"], [], ["bitso.com"], "url normalised"),
    (["https://apps.apple.com/x"], ["bitso.com"], [], [], "app store skipped"),
    (["help.bitso.com"], ["bitso.com"], ["help.bitso.com"], [], "denied seed dropped"),
]
for tgt, i_, o_, want, why in SEEDS:
    check(_scannable(tgt, i_, o_) == want, "seed %-26s -> %s (%s)" % (str(tgt)[:26], _scannable(tgt, i_, o_), why))

# ---------- warning surface ----------
check(bool(scope_warnings(["sandbox-rfq.bitso.com"], ["sandbox.bitso.com"])),
      "warn: sandbox-rfq flagged for human review")
check(not scope_warnings(["www.bitso.com"], ["sandbox.bitso.com"]),
      "warn: www.bitso.com not flagged")

# ---------- phase B marker parsing ----------
from bounty_run import parse_markers
r = parse_markers("---LIVE---\nhttps://a.x\nhttps://b.x\n---NUCLEI---\n[cve] a.x\n---END---\n")
check(r == {"LIVE": ["https://a.x", "https://b.x"], "NUCLEI": ["[cve] a.x"]},
      "markers: live+nuclei split")
check(parse_markers("ssh: connection refused\n") is None,
      "markers: no markers -> None (lost payload)")
check(parse_markers("---LIVE---\n") == {"LIVE": []},
      "markers: empty live section is a real empty, not lost")
check(parse_markers("noise\n---LIVE---\nhttps://a.x\n") == {"LIVE": ["https://a.x"]},
      "markers: pre-marker shell noise ignored")

# ---------- gate 0: stale-scope detection ----------
# A job stores its scope at creation. If the brief later lists exclusions the job
# is missing, those hosts get scanned as in-scope. This is not hypothetical: the
# bitso job was created with 7 exclusions while the brief listed 19, so
# sandbox.bitso.com and quetzal.bitso.com were scanned despite being explicitly
# out of scope. Reproduce that comparison here.
from bounty_run import _host_of

STALE = ["help.bitso.com", "status.bitso.com", "dev.bitso.com", "blog.bitso.com",
         "stage.bitso.com", "*.otc.bitso.com", "*.efx.bitso.com"]
FULL = STALE + ["landing.bitso.com", "devmalta.bitso.com", "edu.bitso.com",
                "sandbox.bitso.com", "api-dev.bitso.com", "api-stage.bitso.com",
                "api-sandbox.bitso.com", "stagemalta.bitso.com", "quetzal.bitso.com",
                "login.bitso.io", "bitsohelp.zendesk.com", "support.bitso.com"]


def stale_missing(stored, current):
    s = {_host_of(x) for x in stored if _host_of(x)}
    c = {_host_of(x) for x in current if _host_of(x)}
    return c - s


miss = stale_missing(STALE, FULL)
check(len(miss) == 12, "gate0: detects all 12 missing exclusions (got %d)" % len(miss))
check("sandbox.bitso.com" in miss, "gate0: flags sandbox.bitso.com (was really scanned)")
check("quetzal.bitso.com" in miss, "gate0: flags quetzal.bitso.com (was really scanned)")
check(stale_missing(FULL, FULL) == set(), "gate0: no false positive when scope is current")
check(stale_missing(FULL, STALE) == set(), "gate0: shrinking scope is not flagged as stale")

# ---------- real captured pages (regression: prose caveat flipped a card) ----------
# The inline fixtures above are hand-written and were MASKING a bug: in the real
# Okta brief, the "Okta Workflows" card declares "In scope", but its target table
# is printed after a prose subsection headed "Out of Scope" ("Testing for Denial
# of Service ... is STRICTLY out of scope"). The parser took the LAST label seen,
# so it filed the in-scope workflows target as OUT. Only a real page shows this.
# These fixtures are the actual captured briefs, so they pin the true layout.
FIX = Path(__file__).resolve().parent / "fixtures"


def _parse_fixture(name):
    return parse_scope((FIX / name).read_text())


if (FIX / "bugcrowd-okta-brief.txt").exists():
    oi, oo = _parse_fixture("bugcrowd-okta-brief.txt")
    check(any("workflows" in x for x in oi),
          "real okta: workflows target is IN scope (prose 'Out of Scope' must not flip the card)")
    check(not any("workflows" in x for x in oo),
          "real okta: workflows target not leaked into OUT")
    check("personal.trexcloud.com" in oo, "real okta: personal card stays OUT")
    check(any("pam.oktapreview.com" in x for x in oi), "real okta: pam target stays IN")

if (FIX / "bugcrowd-bitso-brief.txt").exists():
    bi, bo = _parse_fixture("bugcrowd-bitso-brief.txt")
    check(len(bi) == 10, "real bitso: 10 in-scope entries (got %d)" % len(bi))
    check(len(bo) == 19, "real bitso: 19 out-of-scope entries (got %d)" % len(bo))
    for trap in ("sandbox.bitso.com", "quetzal.bitso.com", "*.efx.bitso.com"):
        check(trap in bo, "real bitso: %s excluded" % trap)
    check("bitso.com" in bi, "real bitso: apex in scope")

print()
print("RESULT:", "ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
