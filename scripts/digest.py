#!/usr/bin/env python3
"""One place that turns pipeline state into a Telegram message.

WHY THIS MODULE EXISTS
----------------------
The old digest printed one aligned text row per program:

    okta    hunted    rc=0 steps=10 findings=0 seeds=24

That is a log line, not a report. A reader on a phone cannot tell from it
whether the run was worth money, which programs were skipped and why, or which
of the findings is actually worth reading first. Every field needed for that
decision was already in the run's data and was being thrown away by the
formatter.

Rules followed here:
  * Money first — a hunt is either chasing payout or it is not worth a run.
  * One line per program with the numbers that change a decision, not the
    numbers that only prove the process ran.
  * Skipped reasons are counted and named. "12 programs skipped" with no reason
    reads like a bug; "12 tanpa payout" is a decision.
  * HTML parse_mode with escaping: an unescaped '<' in a program name makes
    Telegram reject the whole message, which for an unattended digest means
    silence.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))

STATUS_ICON = {
    "hunted": "🟢",
    "hunt-empty": "🟡",
    "hunt-failed": "🔴",
    "dry-run": "⚪",
    "no-scope-pattern": "⚪",
    "no-seed": "⚪",
    "target-not-in-scope": "🟠",
    "retry": "🟡",
    "no-session": "🟠",
    "error": "🔴",
    "unknown": "⚪",
}


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""), quote=False)


def money(v) -> str:
    """'$15,000' / '$500–$15,000' / 'tanpa payout'."""
    lo = _int(v[0]) if isinstance(v, (tuple, list)) and v else 0
    hi = _int(v[1]) if isinstance(v, (tuple, list)) and len(v) > 1 else 0
    if not hi and not lo:
        return "tanpa payout"
    if lo and hi and lo != hi:
        return f"${lo:,}–${hi:,}"
    return f"${max(lo, hi):,}"


def _int(v) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    digits = "".join(ch for ch in str(v or "") if ch.isdigit())
    return int(digits) if digits else 0


MONTHS_ID = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
             "Jul", "Agu", "Sep", "Okt", "Nov", "Des")


def _now() -> str:
    n = datetime.now(WIB)
    return f"{n.day} {MONTHS_ID[n.month - 1]} {n:%H:%M} WIB"


def bounty_digest(rows: list[dict], platform: str = "all",
                  skipped: dict | None = None, url: str = "") -> str:
    """rows: one dict per attempted program with program/name/status/note/payout."""
    rows = rows or []
    done = [r for r in rows if r.get("status") == "hunted"]
    failed = [r for r in rows if r.get("status") in ("hunt-failed", "error")]
    findings = sum(_int(r.get("findings")) for r in rows)
    best = max((r.get("payout") or (0, 0))[1] for r in rows) if rows else 0

    head = [
        f"<b>🔎 ABH hunt</b> · {_now()}",
        f"{esc(platform)} · {len(rows)} program · {len(done)} selesai · "
        f"{len(failed)} gagal · <b>{findings} temuan</b>",
    ]
    if best:
        head.append(f"payout tertinggi diincar: <b>${_int(best):,}</b>")

    body = []
    for i, r in enumerate(rows, 1):
        icon = STATUS_ICON.get(r.get("status", ""), "⚪")
        name = esc(r.get("name") or r.get("program") or "?")
        target = esc(r.get("target") or "")
        line = [f"{i}. {icon} <b>{name}</b>"]
        if target:
            line[0] += f" <code>{target}</code>"
        facts = []
        if r.get("payout"):
            facts.append(money(r["payout"]))
        if r.get("inscope"):
            facts.append(f"scope {_int(r['inscope'])}")
        if r.get("steps"):
            facts.append(f"steps {_int(r['steps'])}")
        if r.get("findings"):
            facts.append(f"temuan {_int(r['findings'])}")
        if r.get("model"):
            facts.append(esc(r["model"]))
        if facts:
            line.append("   " + " · ".join(facts))
        note = (r.get("note") or "").strip()
        if note and r.get("status") not in ("hunted",):
            line.append(f"   <i>{esc(note[:160])}</i>")
        if r.get("session"):
            line.append(f"   sesi <code>{esc(r['session'])}</code>")
        body.append("\n".join(line))

    tail = []
    if skipped:
        parts = [f"{n} {why}" for why, n in sorted(skipped.items(), key=lambda kv: -kv[1]) if n]
        if parts:
            tail.append("⏭ dilewati: " + esc(", ".join(parts)))
    if url:
        tail.append(f'🔗 <a href="{esc(url)}">detail sesi</a>')

    return "\n\n".join(["\n".join(head)] + body + (["\n".join(tail)] if tail else []))


def gigs_digest(rows: list[dict], found: int = 0, url: str = "") -> str:
    """rows: gigs (title/usd/bids/src/url/agent) after filtering."""
    rows = rows or []
    agent_ok = [g for g in rows if g.get("agent")]
    top = sorted(rows, key=lambda g: (-(_int((g.get("usd") or (0, 0))[1])), g.get("bids", 0)))[:10]

    shown = min(len(rows), 10)
    head = [
        f"<b>💼 Gigs</b> · {_now()}",
        f"{found or len(rows)} baru · <b>{len(agent_ok)} bisa dieksekusi agent</b>"
        + (f" · tampil {shown} teratas" if len(rows) > shown else ""),
    ]
    body = []
    for i, g in enumerate(top, 1):
        amt = money(g.get("usd"))
        flag = " 🤖" if g.get("agent") else ""
        body.append(
            f"{i}. <a href=\"{esc(g.get('url',''))}\">{esc((g.get('title') or '')[:90])}</a>"
            f"{flag}\n"
            f"   {esc(amt)} · {_int(g.get('bids'))} bid · {esc(g.get('src',''))}"
        )
    tail = [f'🔗 <a href="{esc(url)}">feed lengkap</a>'] if url else []
    return "\n\n".join(["\n".join(head)] + body + (["\n".join(tail)] if tail else []))


if __name__ == "__main__":  # self-check: the two shapes that used to read badly
    d = bounty_digest(
        [{"program": "okta", "name": "Okta", "status": "hunted", "target": "okta.com",
          "payout": (500, 15000), "inscope": 24, "steps": 10, "findings": 2,
          "session": "s-123", "model": "Zcode/GLM-5.3", "note": "rc=0 steps=10 findings=2 seeds=24"},
         {"program": "vdp-x", "name": "VDP <X>", "status": "no-seed", "note": "no enumerable in-scope host"}],
        platform="bugcrowd", skipped={"tanpa payout": 12, "sudah dikerjakan": 8},
        url="http://100.120.116.62:8795/gigs/abh/sessions")
    assert "1. 🟢 <b>Okta</b>" in d
    assert "$500–$15,000" in d
    assert "12 tanpa payout" in d
    assert "VDP &lt;X&gt;" in d, "names must be escaped or Telegram rejects the message"
    assert "2. ⚪" in d
    g = gigs_digest([{"title": "Fix <bug>", "usd": (100, 500), "bids": 3,
                      "src": "algora", "url": "https://x/y", "agent": True}], found=1)
    assert "🤖" in g and "$100–$500" in g
    print("ok")
