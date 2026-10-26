#!/usr/bin/env python3
"""Scope parsing + gate 1. Run after ANY change to parse_scope / host_in_scope.

Two real page layouts are covered, both captured from live Bugcrowd briefs:
  * bitso-mbb-og : 2 cards (1 in, 1 out)
  * okta         : 4 cards (3 in, 1 out) - the layout that broke the first parser
"""
import sys
from pathlib import Path
# Resolve scripts/ relative to this file so the test runs from any checkout
# (laptop, blu, a CI box) instead of only /home/amen/Auto-FreeCF.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from bounty_run import parse_scope, host_in_scope, filter_hosts, _scannable, scope_warnings
import bounty_run as B
from bounty_run import (INTIGRITI_MIRROR, YESWEHACK_MIRROR, _host_pattern,
                        _hosts_from_targets, discover, discover_intigriti,
                        discover_yeswehack, get_scope)

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

# ---------- rule specificity: narrow explicit allow beats broad deny ----------
# Real LaunchDarkly brief: the apex and a few named hosts are OUT, while four
# specific subdomains are IN. A blanket "deny always wins" rejected every
# explicitly in-scope asset and the program reported "no scannable in-scope
# seed (app-store only?)", retiring a live paying program as if it had nothing
# to test. These cases pin the intended reading: most specific rule wins, ties
# deny, unmatched stays out.
LD_IN = ["app.launchdarkly.com", "events.launchdarkly.com",
         "stream.launchdarkly.com", "docs.launchdarkly.com",
         "https://launchdarkly.com/docs"]
LD_OUT = ["blog.launchdarkly.com", "launchdarkly.com", "sandbox.launchdarkly.com",
          "slack.launchdarkly.com", "status.launchdarkly.com",
          "launchdarkly.atlassian.net"]
for host, want, why in (
        ("app.launchdarkly.com", True, "explicit allow beats apex deny"),
        ("stream.launchdarkly.com", True, "explicit allow beats apex deny"),
        ("blog.launchdarkly.com", False, "named deny beats apex allow"),
        ("sandbox.launchdarkly.com", False, "named deny beats apex allow"),
        ("random.launchdarkly.com", False, "no allow at all -> out"),
        ("launchdarkly.com", False, "apex is explicitly denied"),
        ("evil.com", False, "unmatched stays out"),
):
    got, reason = host_in_scope(host, LD_IN, LD_OUT)
    check(got == want, "specificity: %-26s -> %s (%s)" % (host, want, why))

check(_scannable(LD_IN, LD_IN, LD_OUT) ==
      ["app.launchdarkly.com", "docs.launchdarkly.com",
       "events.launchdarkly.com", "stream.launchdarkly.com"],
      "specificity: launchdarkly seeds are exactly the 4 explicit allows")

# The narrow-deny case must not regress: a specific exclusion inside a broad
# allow still has to be denied, and so must everything under it.
for host, want in (("bitso.com", True), ("api.bitso.com", True),
                   ("sandbox.bitso.com", False), ("x.sandbox.bitso.com", False)):
    got, _ = host_in_scope(host, ["bitso.com"], ["sandbox.bitso.com"])
    check(got == want, "specificity: narrow deny %-22s -> %s" % (host, want))


# ---------- Task 3: Intigriti / YesWeHack (mirror-backed) ----------
# Both feeds come from the same arkadiyt mirror HackerOne already reads, so they
# are PUBLIC and STRUCTURED -- no brief to render, no browser session. These
# tests seed _MIRROR_CACHE with canned documents and swap _http_json for a
# counter, so they prove the functions never go near the network *and* that a
# failed fetch cannot masquerade as "this platform has no programs".

# --- _host_pattern: what counts as a host, and what must be dropped ---
HP = [
    ("https://api.example.com/v1",      "api.example.com", "URL -> host, path dropped"),
    ("*.example.com",                   "*.example.com",   "wildcard kept, not flattened to apex"),
    ("example.com",                     "example.com",     "apex stays an apex"),
    ("cdn.sub.example.com",             "cdn.sub.example.com", "subdomain kept as-is"),
    ("all",                             "",                "Intigriti \"all\" is not a host"),
    ("Any resource created with the Outscale Cloud", "",
                                        "YesWeHack prose is not a host"),
    ("moment.js",                       "",                "filename is not a host"),
    ("report.pdf",                      "",                "downloadable asset is not a host"),
    ("*.doctolib.(fr|de|it|com|net)",   "",                "regex alternation can never suffix-match"),
    ("10.0.0.0/8",                      "",                "CIDR is not a hostname"),
    ("localhost",                       "",                "no dot -> not a routable host"),
    ("",                                "",                "empty is empty"),
]
print("Task 3a. _host_pattern only yields literal, resolvable hosts")
for value, want, why in HP:
    got = _host_pattern(value)
    check(got == want, "%-42s -> %-24r %s" % (value[:42], got, why))

# A wildcard must NOT be widened to its apex (that claims subdomains the program
# never granted) and an apex must NOT be widened to its subdomains (same error).
check(_host_pattern("*.example.com") != "example.com",
      "wildcard is not silently flattened to the apex")

print()
print("Task 3b. structured targets -> in/out host lists, both key spellings")
INT_TARGETS = {
    "in_scope": [{"type": "url", "endpoint": "https://api.dropboxapi.com/"},
                 {"type": "url", "endpoint": "all"},
                 {"type": "url", "endpoint": "www.dropbox.com"},
                 "not-a-dict"],
    "out_of_scope": [{"type": "url", "endpoint": "community.dropbox.com"}],
}
ins, outs = _hosts_from_targets("intigriti", INT_TARGETS)
check(ins == ["api.dropboxapi.com", "www.dropbox.com"],
      "intigriti uses `endpoint`, drops \"all\" and junk (%s)" % ins)
check(outs == ["community.dropbox.com"], "intigriti out-of-scope extracted (%s)" % outs)

YWH_TARGETS = {
    "in_scope": [{"target": "https://app.example.com/", "type": "web-application"},
                 {"target": "*.example.com", "type": "web-application"}],
    "out_of_scope": [{"target": "store.example.com", "type": "other"}],
}
ins, outs = _hosts_from_targets("yeswehack", YWH_TARGETS)
check(ins == ["app.example.com", "*.example.com"],
      "yeswehack uses `target` (%s)" % ins)
check(outs == ["store.example.com"], "yeswehack out-of-scope extracted (%s)" % outs)
check(_hosts_from_targets("intigriti", None) == ([], []),
      "missing targets block is not an error")
check(_hosts_from_targets("intigriti", "garbage") == ([], []),
      "non-dict targets block is not an error")

print()
print("Task 3c. discovery filters and reward sort")
INT_DOC = [
    {"handle": "pay", "name": "Pay Co", "status": "open",
     "confidentiality_level": "public",
     "url": "https://www.intigriti.com/programs/pay/pay/detail",
     "min_bounty": {"value": 100}, "max_bounty": {"value": 5000}, "targets": {}},
    {"handle": "closed", "name": "Closed", "status": "closed",
     "confidentiality_level": "public", "url": "",
     "max_bounty": {"value": 99999}, "targets": {}},
    {"handle": "secret", "name": "Confidential", "status": "open",
     "confidentiality_level": "confidential", "url": "",
     "max_bounty": {"value": 99999}, "targets": {}},
    {"handle": "rich", "name": "Rich Co", "status": "open",
     "confidentiality_level": "public",
     "url": "https://www.intigriti.com/programs/rich/rich/detail",
     "min_bounty": {"value": 50}, "max_bounty": {"value": 50000}, "targets": {}},
    "junk-not-a-dict",
]
YWH_DOC = [
    {"id": "live", "name": "Live", "public": True, "disabled": False,
     "min_bounty": 10, "max_bounty": 1000, "targets": {}},
    {"id": "hidden", "name": "Hidden", "public": False, "disabled": False,
     "max_bounty": 99999, "targets": {}},
    {"id": "off", "name": "Off", "public": True, "disabled": True,
     "max_bounty": 99999, "targets": {}},
    {"name": "NoId", "public": True, "disabled": False,
     "max_bounty": 99999, "targets": {}},
]

B._MIRROR_CACHE[INTIGRITI_MIRROR] = INT_DOC
B._MIRROR_CACHE[YESWEHACK_MIRROR] = YWH_DOC

# A mirror fetch from here on is a BUG, not a fallback: both documents are
# already in the cache, so going back to the network would mean the cache path
# is broken. Any other feed (bugcrowd/hackerone) answers empty -- this test
# reads none of them, and an empty reply keeps those scrapers off the wire too.
_MIRROR_CALLS = {"n": 0}
_real_http_json = B._http_json


def _guard_mirror_fetch(*a, **k):
    url = a[0] if a else k.get("url")
    if url in (INTIGRITI_MIRROR, YESWEHACK_MIRROR):
        _MIRROR_CALLS["n"] += 1
        raise AssertionError("mirror fetch attempted: %r" % (url,))
    return {}


B._http_json = _guard_mirror_fetch
try:
    int_rows = discover_intigriti(10)
    ywh_rows = discover_yeswehack(10)

    check([r["name"] for r in int_rows] == ["Rich Co", "Pay Co"],
          "intigriti: open+public only, reward-sorted desc (%s)"
          % [r["name"] for r in int_rows])
    check(all(r["platform"] == "intigriti" for r in int_rows),
          "intigriti rows carry the platform tag")
    check(int_rows[0]["brief"].endswith("/rich/rich/detail"),
          "intigriti brief is the program detail URL")
    check(int_rows[0]["max"] == 50000, "intigriti max bounty kept numeric")
    check([r["name"] for r in ywh_rows] == ["Live"],
          "yeswehack: public, enabled, id present (%s)"
          % [r["name"] for r in ywh_rows])
    check(ywh_rows[0]["brief"] == "https://yeswehack.com/programs/live",
          "yeswehack brief is built from the id")

    # Scope for known handles, straight from the seeded cache.
    i_ins, i_outs, i_page = get_scope("intigriti", "rich")
    check(len(i_page) > 0,
          "intigriti scope returns page_text (%d bytes) so callers can tell "
          "\"lists nothing\" from \"session expired\"" % len(i_page))
    y_ins, y_outs, y_page = get_scope("yeswehack", "live")
    check(len(y_page) > 0, "yeswehack scope returns page_text (%d bytes)" % len(y_page))

    # An unknown handle must be indistinguishable from "page came back empty" --
    # that is the signal cmd_scope/abh_hunt key off to say session-expired. It
    # must NOT look like a program that legitimately lists no targets, or a
    # typo'd slug would be read as a scoped-but-empty program.
    n_ins, n_outs, n_page = get_scope("intigriti", "does-not-exist")
    check((n_ins, n_outs, n_page) == ([], [], ""),
          "unknown handle -> empty page, not a fake empty scope")

    # A program whose only targets are regex/prose/CIDR yields NO seed rather
    # than one recon could not resolve -- and its page still renders, so the
    # caller can see the program exists but exposes no enumerable host.
    B._MIRROR_CACHE[YESWEHACK_MIRROR] = [
        {"id": "weird", "name": "Weird", "public": True, "disabled": False,
         "max_bounty": 100,
         "targets": {"in_scope": [{"target": "*.weird.(fr|de)"},
                                  {"target": "Any resource in our cloud"},
                                  {"target": "10.0.0.0/8"}]}}]
    w_ins, _, w_page = get_scope("yeswehack", "weird")
    check(w_ins == [] and len(w_page) > 0,
          "regex/prose/CIDR-only scope -> no seed, page still rendered (%s)" % w_ins)
    B._MIRROR_CACHE[YESWEHACK_MIRROR] = YWH_DOC

    check(_MIRROR_CALLS["n"] == 0,
          "discovery + scope served from cache, 0 mirror fetches")
finally:
    B._http_json = _real_http_json
    B._MIRROR_CACHE.pop(INTIGRITI_MIRROR, None)
    B._MIRROR_CACHE.pop(YESWEHACK_MIRROR, None)

print()
print("Task 3c-2. one Intigriti slug resolves to ONE program, never the first entry")
# Every Intigriti brief URL ends in "/detail". Deriving the slug from the URL
# last segment gave "detail" for all 140 programs, and _mirror_entry's
# endswith("/detail") then matched the first entry every time -- so hunting any
# Intigriti program would have scanned the FIRST program's hosts, and
# _mark_attempted("detail") would have retired all 140 at once.
INT_MANY = [
    {"handle": "aaa-first", "company_handle": "aaa", "name": "First",
     "status": "open", "confidentiality_level": "public", "url":
     "https://www.intigriti.com/programs/aaa/aaa-first/detail",
     "min_bounty": {"value": 0}, "max_bounty": {"value": 10}, "targets": {}},
    {"handle": "zzz-target", "company_handle": "zzz", "name": "Target",
     "status": "open", "confidentiality_level": "public", "url":
     "https://www.intigriti.com/programs/zzz/zzz-target/detail",
     "min_bounty": {"value": 0}, "max_bounty": {"value": 20}, "targets": {}},
]
B._MIRROR_CACHE[INTIGRITI_MIRROR] = INT_MANY
try:
    rows = discover_intigriti(10)
    # Compare as a set: discovery sorts by max bounty descending (zzz 20 >
    # aaa 10), so asserting a list order here would be asserting the sort, which
    # the section above already covers.
    check({r.get("slug") for r in rows} == {"aaa-first", "zzz-target"},
          "discovery publishes a real handle as slug (%s)"
          % sorted(r.get("slug") for r in rows))
    check(all(r.get("slug") and r["slug"] != "detail" for r in rows),
          "no published slug is the shared /detail tail")

    for handle, want_name in (("zzz-target", "Target"), ("aaa-first", "First")):
        entry = B._mirror_entry("intigriti", handle)
        check(entry is not None and entry["name"] == want_name,
              "handle %-11s -> %s (not the first feed entry)"
              % (handle, entry["name"] if entry else None))

    # The literal tail every program shares must never resolve to anything.
    check(B._mirror_entry("intigriti", "detail") is None,
          'slug "detail" resolves to NO program (it is every program\'s tail)')

    # And the URL shape still works for a manually passed program handle.
    check(B._mirror_entry("intigriti", "zzz-target")["name"] == "Target",
          "URL /<slug>/detail fallback resolves correctly")
    check(B._mirror_entry("intigriti", "nope") is None,
          "unknown slug -> None (caller sees an empty page)")

    # YesWeHack: id is the URL tail and is explicit, so it must agree too.
    B._MIRROR_CACHE[YESWEHACK_MIRROR] = [
        {"id": "y-first", "name": "YFirst", "public": True, "disabled": False,
         "max_bounty": 1, "targets": {}},
        {"id": "y-second", "name": "YSecond", "public": True, "disabled": False,
         "max_bounty": 2, "targets": {}}]
    yrows = discover_yeswehack(10)
    check({r.get("slug") for r in yrows} == {"y-first", "y-second"},
          "yeswehack publishes the id as slug (%s)"
          % sorted(r.get("slug") for r in yrows))
    check(B._mirror_entry("yeswehack", "y-second")["name"] == "YSecond",
          "yeswehack slug resolves to that program, not the first")
finally:
    B._MIRROR_CACHE.pop(INTIGRITI_MIRROR, None)
    B._MIRROR_CACHE.pop(YESWEHACK_MIRROR, None)

print()
print("Task 3d. --platform routing reaches exactly the right scrapers")
# Each scraper is swapped for a stub returning one tagged row, so this tests the
# ROUTING in discover() and nothing else. Testing the real scrapers here would
# mean network calls and a dependency on three third-party feeds staying up.
_real_scrapers = (B.discover_bugcrowd, B.discover_hackerone,
                  B.discover_intigriti, B.discover_yeswehack)


def _stub(plat):
    return lambda limit=25: [{"platform": plat, "name": plat,
                              "min": "", "max": 0, "brief": ""}]


B.discover_bugcrowd = _stub("bugcrowd")
B.discover_hackerone = _stub("hackerone")
B.discover_intigriti = _stub("intigriti")
B.discover_yeswehack = _stub("yeswehack")
try:
    def plats(value):
        return [r["platform"] for r in discover(value, 5)]

    check(plats("both") == ["bugcrowd", "hackerone"],
          '"both" is still exactly bugcrowd+hackerone, not two new feeds (%s)'
          % plats("both"))
    check(plats("all") == ["bugcrowd", "hackerone", "intigriti", "yeswehack"],
          '"all" reaches every platform (%s)' % plats("all"))
    check(plats("intigriti") == ["intigriti"],
          '"intigriti" reaches only intigriti')
    check(plats("yeswehack") == ["yeswehack"],
          '"yeswehack" reaches only yeswehack')
    check(plats("bugcrowd") == ["bugcrowd"],
          '"bugcrowd" unchanged')
    check(discover("nothing-else", 5) == [], "unknown platform returns nothing")
finally:
    (B.discover_bugcrowd, B.discover_hackerone,
     B.discover_intigriti, B.discover_yeswehack) = _real_scrapers

print()
print("Task 3e. a mirror fetch failure must not read as \"no programs\"")
B._MIRROR_CACHE.pop(INTIGRITI_MIRROR, None)
_real_http_json = B._http_json
B._http_json = _guard_mirror_fetch  # raises on the mirror URL -> except branch
try:
    check(discover_intigriti(5) == [],
          "fetch failure -> empty list, not an exception")
    check(get_scope("intigriti", "anything") == ([], [], ""),
          "fetch failure -> empty page, not a fabricated scope")
    check(discover_yeswehack(5) == [],
          "second mirror failing the same way is equally non-fatal")
finally:
    B._http_json = _real_http_json
    B._MIRROR_CACHE.pop(INTIGRITI_MIRROR, None)
    B._MIRROR_CACHE.pop(YESWEHACK_MIRROR, None)

print()
print("RESULT:", "ALL PASS" if fails == 0 else "%d FAILURES" % fails)
def test_bounty_scope():
    """Pytest entrypoint for bounty scope assertions."""
    assert fails == 0, f"{fails} bounty-scope check(s) failed"


if __name__ == "__main__":
    sys.exit(1 if fails else 0)
