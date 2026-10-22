#!/usr/bin/env python3
"""Scope plumbing: wildcards must survive into -d flags, and the seed the
pipeline picks must be one its own scope guard will accept.

Live bug 2026-10-22 (Okta, Bugcrowd): scope was `*.okta.com` plus explicit
hosts. `_scannable()` correctly turned the wildcard into the enumeration root
`okta.com`, but `scope_flags()` dropped `*.okta.com` (the placeholder filter
treats any `*` as template syntax), so the agent received only the explicit
hosts. The first tool call was then blocked by its own guard --
"Out of scope: okta.com matches no in-scope rule" -- and the hunt reported
steps=0. A program that pays up to $75,000 produced a clean-looking empty run.
"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
BUGBUNTER = Path("/home/amen/Agentic-Bug-Hunter/bughunter")

import abh_hunt as A          # noqa: E402
import bounty_run as B        # noqa: E402


OKTA_IN = ["*.okta.com", "*.okta-emea.com", "support.okta.com", "www.okta.com"]
OKTA_OUT = ["personal.trexcloud.com"]


def _flags(flags):
    return ([flags[i + 1] for i, v in enumerate(flags) if v == "-d"],
            [flags[i + 1] for i, v in enumerate(flags) if v == "-x"])


def _rank(seeds, ins, outs):
    def key(h):
        ok, _ = B.host_in_scope(h, ins, outs)
        return (0 if ok else 1,
                0 if h.count(".") == 1 else (1 if h.startswith("www.") else 2), h)
    return sorted(seeds, key=key)


def test_wildcard_survives_into_scope_flags():
    allow, deny = _flags(A.scope_flags(OKTA_IN, OKTA_OUT))
    assert "*.okta.com" in allow, f"wildcard dropped from -d: {allow}"
    assert "okta.com" in allow, "apex must accompany the wildcard"
    assert "*.okta-emea.com" in allow and "okta-emea.com" in allow
    assert "support.okta.com" in allow and "www.okta.com" in allow
    assert "personal.trexcloud.com" in deny


def test_templated_assets_still_dropped():
    allow, _ = _flags(A.scope_flags(
        ["bugcrowd-pam-###.oktapreview.com", "*.okta.com"], []))
    assert not any("###" in h or h.endswith("-") for h in allow), allow


def test_chosen_seed_is_in_scope():
    seeds = B._scannable(OKTA_IN, OKTA_IN, OKTA_OUT)
    chosen = _rank(seeds, OKTA_IN, OKTA_OUT)[0]
    ok, why = B.host_in_scope(chosen, OKTA_IN, OKTA_OUT)
    assert ok, f"pipeline would hunt {chosen!r}, which its own guard rejects: {why}"


def test_wildcard_only_program_still_yields_a_seed():
    """A wildcard-only program must stay huntable (base is an enumeration root)."""
    ins = ["*.example.com"]
    seeds = B._scannable(ins, ins, [])
    assert seeds == ["example.com"], seeds


def test_wildcard_with_apex_denied_yields_no_seed():
    """If the program denies the apex, do not enumerate from it."""
    ins, outs = ["*.example.com"], ["example.com"]
    assert B._scannable(ins, ins, outs) == []


def test_guard_accepts_target_and_rejects_strangers():
    if not (BUGBUNTER / "tools" / "scope_checker.py").is_file():
        return  # engine not checked out next to the orchestrator
    sys.path.insert(0, str(BUGBUNTER))
    from tools.scope_checker import ScopeChecker

    allow, deny = _flags(A.scope_flags(OKTA_IN, OKTA_OUT))
    sc = ScopeChecker(domains=allow, excluded_domains=deny)
    seeds = _rank(B._scannable(OKTA_IN, OKTA_IN, OKTA_OUT), OKTA_IN, OKTA_OUT)
    assert sc.is_in_scope("https://" + seeds[0] + "/"), seeds[0]
    assert not sc.is_in_scope("https://evil.example/")


def main() -> int:
    fails = 0
    # Collect at CALL time from the module globals, so tests defined after this
    # function (appended sections) are still discovered. An earlier version
    # iterated globals() inline and silently skipped every test below it.
    import __main__ as mod
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
    print(f"\nRESULT: {fails} FAILURES" if fails else "\nRESULT: all scope tests pass")
    return fails


if __name__ == "__main__":
    sys.exit(1 if main() else 0)


# ── login-wall detection ──────────────────────────────────────────────────────
LOGIN_PAGE = "Verify with your password\nPassword\xa0\nForgot password?\nBack to sign in\n\n"
REAL_BRIEF = """Okta
In Scope
bugcrowd-pam-###.oktapreview.com
www.okta.com
Out of Scope
personal.trexcloud.com
Vulnerabilities rewarded
Sign in flows are in scope.
"""


def test_login_wall_is_detected():
    assert B.is_login_wall(LOGIN_PAGE) is True


def test_real_brief_is_not_a_login_wall():
    """False positives here would retire every program."""
    assert B.is_login_wall(REAL_BRIEF) is False


def test_single_login_marker_is_not_enough():
    assert B.is_login_wall("Please sign in to continue reading") is False
    assert B.is_login_wall("") is False
