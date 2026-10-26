#!/usr/bin/env python3
"""Empirical bounty targeting: rank a program/host/finding by measured exploitation odds.

WHY THIS EXISTS
---------------
`bounty_run.py` discovers programs and scans hosts, but it ranks work by nothing
empirical -- a Zimbra instance and a Linux kernel daemon look equally interesting
to the pipeline. The CVE research in `~/workspace/Research Bug` measured what
actually gets exploited in the wild, and three of its numbers are directly
actionable for bounty targeting:

  1. Vendor hazard. KEV conversion rate per vendor, vs the 0.4474% baseline.
     Zimbra 42.8x, SonicWall 20.2x, Citrix 16.5x, Ivanti 16.0x, Palo Alto 10.1x,
     VMware 7.7x, Fortinet 6.2x. Linux kernel: 0.3x.
  2. CWE hazard. Type confusion 11.3x, auth-bypass-alt-channel 5.7x,
     deserialization 4.9x, OS command injection 4.0x. XSS 0.4x.
  3. CVSS-3.1 blind spots. 4,196 Critical/High CVEs have a CVSS 4.0 score and
     no CVSS 3.1 score at all -- invisible to any `cvss >= 7.0` filter.

So this module answers one question for the pipeline: **where is a finding worth
the most?** It does not replace the 7-Question Gate; it decides order and whether
a candidate deserves escalation.

DATA
----
`data/empirical_risk.json`, exported from cve_research.db (408,714 CVEs,
1,746 KEV, 202,409 SSVC). Regenerate with `scripts/empirical_risk_export.py`.
Vendor/CWE keys are lowercase. Unknown vendor -> no multiplier (1.0), never a
guess.

USAGE
    from empirical_targeting import score_host, score_finding, rank_hosts

    score_host("mail.example.com")                  -> vendor guess + hazard
    score_finding(vendor="synacor", cwe="CWE-502")  -> combined multiplier
    rank_hosts(["a.example.com", ...])              -> sorted list
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("EMPIRICAL_RISK_JSON", REPO / "data" / "empirical_risk.json"))

BASE_RATE = 0.004474

# Vendor name -> the substrings that identify it in a hostname or banner.
# Kept narrow on purpose: a wrong vendor guess silently inflates a target.
VENDOR_HINTS = {
    "synacor": ("zimbra",),
    "sonicwall": ("sonicwall", "mysonicwall"),
    "citrix": ("citrix", "netscaler", "adc"),
    "ivanti": ("ivanti", "pulse", "connectsecure", "pulsesecure"),
    "paloaltonetworks": ("paloalto", "globalprotect"),
    "vmware": ("vmware", "vcenter", "esxi", "vsphere"),
    "fortinet": ("fortinet", "fortigate", "forticlient"),
    "microsoft": ("exchange", "sharepoint", "owa", "microsoft", "outlook"),
    "apache": ("apache", "httpd", "tomcat"),
    "cisco": ("cisco", "asa", "firepower"),
    "papercut": ("papercut",),
    "connectwise": ("connectwise", "screenconnect"),
    "roundcube": ("roundcube", "webmail"),
    "sitecore": ("sitecore",),
    "rarlab": ("winrar", "rarlab"),
    "beyondtrust": ("beyondtrust", "bomgar", "privileged remote access"),
}

_cache: dict | None = None


def load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(DATA.read_text())
        except Exception:
            _cache = {"vendors": {}, "cwes": {}, "blind_spots": []}
    return _cache


def vendor_hazard(vendor: str) -> float:
    """Empirical KEV-conversion multiplier vs the 0.4474% baseline. 1.0 if unknown."""
    if not vendor:
        return 1.0
    v = vendor.strip().lower()
    rec = (load().get("vendors") or {}).get(v)
    if rec:
        return float(rec.get("hazard") or 1.0)
    for known, rec in (load().get("vendors") or {}).items():
        if known and (known in v or v in known):
            return float(rec.get("hazard") or 1.0)
    return 1.0


def cwe_hazard(cwe: str) -> float:
    """Empirical KEV-conversion multiplier for a CWE. 1.0 if unknown."""
    if not cwe:
        return 1.0
    c = cwe.strip().upper()
    if not c.startswith("CWE-"):
        c = "CWE-" + c
    rec = (load().get("cwes") or {}).get(c)
    return float(rec.get("hazard") or 1.0) if rec else 1.0


def guess_vendor(text: str) -> str:
    """Best-effort vendor from a hostname or banner. '' when nothing matches."""
    if not text:
        return ""
    low = text.lower()
    for vendor, hints in VENDOR_HINTS.items():
        for h in hints:
            if h in low:
                return vendor
    return ""


def score_host(host: str) -> dict:
    """Rank a target host by the vendor it appears to be running."""
    vendor = guess_vendor(host)
    hz = vendor_hazard(vendor)
    return {
        "host": host,
        "vendor": vendor,
        "vendor_hazard": hz,
        "priority": round(hz, 2),
        "why": (f"{vendor} is exploited at {hz:.1f}x the baseline rate"
                if vendor else "no vendor signal; baseline priority"),
    }


def score_finding(*, vendor: str = "", cwe: str = "", cvss31: float | None = None,
                  cvss40: float | None = None, ssvc_exploitation: str = "",
                  has_exploit_ref: bool = False) -> dict:
    """Combine the measured multipliers for one candidate finding.

    Deliberately multiplicative and capped: the point is ordering, not a
    probability claim. A 42.8x vendor times a 4.9x CWE is not "210x likely",
    so the result is reported as a rank score with the factors listed.
    """
    v = vendor_hazard(vendor)
    c = cwe_hazard(cwe)
    factors = []
    if v != 1.0:
        factors.append(f"vendor {vendor} {v:.1f}x")
    if c != 1.0:
        factors.append(f"{cwe} {c:.1f}x")

    score = v * c

    if has_exploit_ref:
        score *= 1.85
        factors.append("public exploit reference 1.85x")
    if (ssvc_exploitation or "").lower() == "active":
        score *= 25.0
        factors.append("SSVC exploitation=active 25x")
    elif (ssvc_exploitation or "").lower() == "poc":
        score *= 2.40
        factors.append("SSVC exploitation=poc 2.4x")

    # Blind spot: no CVSS 3.1 but a real CVSS 4.0 score. Any cvss>=7 filter
    # misses these entirely, so they are worth surfacing rather than skipping.
    blind = cvss31 is None and cvss40 is not None and cvss40 >= 7.0
    if blind:
        factors.append(f"CVSS-3.1 blind spot (4.0={cvss40})")

    return {
        "score": round(score, 2),
        "factors": factors,
        "blind_spot": blind,
        "verdict": ("escalate" if score >= 10 else
                    "worth-a-look" if score >= 3 else "baseline"),
    }


def rank_hosts(hosts: list[str]) -> list[dict]:
    """Sort hosts by vendor hazard, highest first. Stable for equal scores."""
    scored = [score_host(h) for h in hosts]
    scored.sort(key=lambda r: (-r["priority"], r["host"]))
    return scored


def blind_spot_ids(limit: int = 50) -> list[str]:
    return [b.get("id", "") for b in (load().get("blind_spots") or [])[:limit]]


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if not args:
        d = load()
        print(f"vendors: {len(d.get('vendors') or {})}  "
              f"cwes: {len(d.get('cwes') or {})}  "
              f"blind spots: {len(d.get('blind_spots') or [])}")
        print("top vendors:", ", ".join(list(d.get("vendors") or {})[:10]))
        print("top cwes   :", ", ".join(list(d.get("cwes") or {})[:10]))
    elif args[0] == "host":
        for h in args[1:]:
            r = score_host(h)
            print(f"{r['priority']:6.2f}  {r['host']:40} {r['why']}")
    elif args[0] == "finding":
        kw = dict(a.split("=", 1) for a in args[1:] if "=" in a)
        r = score_finding(
            vendor=kw.get("vendor", ""),
            cwe=kw.get("cwe", ""),
            cvss31=float(kw["cvss31"]) if "cvss31" in kw else None,
            cvss40=float(kw["cvss40"]) if "cvss40" in kw else None,
            ssvc_exploitation=kw.get("ssvc", ""),
            has_exploit_ref=kw.get("exploit", "").lower() in ("1", "true", "yes"),
        )
        print(json.dumps(r, indent=1))
