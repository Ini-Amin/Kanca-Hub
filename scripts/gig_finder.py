#!/usr/bin/env python3
"""Daily gig finder: small paid jobs our agents can do (scraping, bots, automation, data).

Sources:
  * Freelancer.com public API          — bidding marketplace (needs a paid account to bid)
  * Reddit r/forhire, r/slavelabour    — best effort (RSS often 429s)
  * Superteam Earn public API          — SOLANA bounties; keyless, and each listing
                                         carries `agentAccess` (AGENT_ALLOWED vs
                                         HUMAN_ONLY). This is the one that answers
                                         "besides Freelancer, where can an agent go
                                         all the way to done": the deliverable is a
                                         PR / repo / written artifact, submitted via
                                         the listing's own link — no bidding war.
  * Algora bounties                    — GitHub issue bounties (claim by PR)

Usage: gig_finder.py [--test]   -> reports/gigs-YYYY-MM-DD.md with only gigs not seen before."""
import json, re, sys, time, urllib.parse, urllib.request
from datetime import date
from html import unescape
from pathlib import Path

QUERIES = ["web scraping", "python automation", "telegram bot", "n8n", "data entry python", "selenium", "chrome extension"]
FIT = re.compile(r"scrap|crawl|automat|bot|n8n|zapier|make\.com|python|selenium|playwright|extension|excel|sheets|data|api", re.I)
NOPE = re.compile(r"\b(wordpress theme|logo|video edit|voice|translation|writer|seo backlinks|onsite|full[- ]time)\b", re.I)
# Work an autonomous agent can actually finish on its own: code, audits, testing, docs.
FIT_ST = re.compile(r"audit|securit|develop|build|code|contract|program|test|bug|feedback|review|doc|integration|sdk|api|smart contract|repo", re.I)
MIN_USD, MAX_USD, MAX_BIDS = 10, 500, 40
SEEN = Path.home() / ".config/auto-freecf/gigs_seen.json"
OUT = Path(__file__).resolve().parent.parent / "reports"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) gigfinder/0.1"}


def get(url, accept="application/json"):
    req = urllib.request.Request(url, headers={**UA, "accept": accept})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")


def freelancer(q):
    url = "https://www.freelancer.com/api/projects/0.1/projects/active/?" + urllib.parse.urlencode(
        {"query": q, "limit": 30, "compact": "true", "full_description": "true", "sort_field": "time_updated"})
    for p in json.loads(get(url))["result"]["projects"]:
        rate = p["currency"].get("exchange_rate") or 1
        lo, hi = (p["budget"].get("minimum") or 0) * rate, (p["budget"].get("maximum") or 0) * rate
        yield {"id": f"fl{p['id']}", "title": p["title"], "usd": (round(lo), round(hi or lo)),
               "bids": p["bid_stats"]["bid_count"], "text": p.get("preview_description", ""),
               "url": f"https://www.freelancer.com/projects/{p['seo_url']}", "src": "freelancer"}


def reddit(sub):
    for e in re.findall(r"<entry>(.*?)</entry>", get(f"https://www.reddit.com/r/{sub}/new/.rss"), re.S):
        title = unescape(re.search(r"<title>(.*?)</title>", e, re.S).group(1))
        if not re.match(r"\[(task|hiring)\]", title, re.I):
            continue
        usd = re.search(r"\$\s?(\d{1,4})", title)
        yield {"id": "rd" + re.search(r"<id>(.*?)</id>", e).group(1), "title": title,
               "usd": (int(usd.group(1)),) * 2 if usd else (0, 0), "bids": 0,
               "text": re.sub(r"<[^>]+>", " ", unescape(re.search(r"<content[^>]*>(.*?)</content>", e, re.S).group(1)))[:300],
               "url": re.search(r'<link href="([^"]+)"', e).group(1), "src": f"r/{sub}"}


def superteam():
    """Superteam Earn (Solana bounties). Keyless public API; `agentAccess` tells us
    whether autonomous agents may enter. Submissions are the artifact itself, so a
    dev/audit bounty is genuinely end-to-end automatable."""
    d = json.loads(get("https://earn.superteam.fun/api/listings/?status=open&take=60"))
    for x in d:
        if x.get("status") != "OPEN":
            continue
        agent = x.get("agentAccess") == "AGENT_ALLOWED"
        yield {"id": "st" + str(x.get("id")), "title": x.get("title", ""),
               "usd": (int(x.get("rewardAmount") or 0),) * 2,
               "bids": (x.get("_count") or {}).get("Submission") or 0,
               "text": f"{x.get('type')} · {x.get('token')} · {x.get('agentAccess')}"
                       + (" · AGENT-CAN-ENTER" if agent else ""),
               "url": f"https://earn.superteam.fun/listing/{x.get('slug')}",
               "src": "superteam", "agent": agent}


def algora():
    """Algora bounties (GitHub issue bounties, claimed by opening a PR). tRPC API."""
    url = ("https://console.algora.io/api/trpc/bounty.list?input="
           + urllib.parse.quote(json.dumps({"json": {"limit": 25, "status": "OPEN"}})))
    j = json.loads(get(url))
    items = (((j[0].get("result") or {}).get("data") or {}).get("json") or {}).get("items") or []
    for b in items:
        yield {"id": "ag" + str(b.get("id")), "title": b.get("title", ""),
               "usd": (int(b.get("reward") or 0),) * 2, "bids": 0,
               "text": (b.get("description") or "")[:200],
               "url": b.get("url") or f"https://algora.io/bounties/{b.get('id')}",
               "src": "algora", "agent": True}


def good(g):
    blob = f"{g['title']} {g['text']}"
    hi = g["usd"][1]
    if g["src"] in ("superteam", "algora"):
        # Agent-doable work ONLY: the AGENT_ALLOWED flag alone is not enough —
        # sponsors flag content/social bounties too (e.g. a Spanish promo bounty
        # that bans fully-AI content yet carries the agent flag). Require the
        # work itself to read like code/audit/dev.
        if not FIT_ST.search(blob):
            return False
        return True
    return FIT.search(blob) and not NOPE.search(blob) and g["bids"] <= MAX_BIDS and (hi == 0 or MIN_USD <= hi <= MAX_USD)


def main():
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    gigs = {}
    sources = ([(q, lambda q=q: freelancer(q)) for q in QUERIES]
               + [(s, lambda s=s: reddit(s)) for s in ("forhire", "slavelabour")]
               + [("superteam", superteam), ("algora", algora)])
    for name, it in sources:
        try:
            for g in it():
                if g["id"] not in seen and good(g):
                    gigs[g["id"]] = g
        except Exception as e:
            print(f"{name}: {e}", file=sys.stderr)
        time.sleep(2)
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(seen | gigs.keys())[-5000:]))
    if not gigs:
        return print("no new gigs")
    OUT.mkdir(exist_ok=True)
    f = OUT / f"gigs-{date.today()}.md"
    rows = sorted(gigs.values(), key=lambda g: (g["bids"], -g["usd"][1]))
    with f.open("a") as w:
        w.write(f"\n# Gigs {time.strftime('%Y-%m-%d %H:%M')} (fewest bids first)\n\n")
        for g in rows:
            money = f"${g['usd'][0]}-{g['usd'][1]}" if g["usd"][1] else "budget ?"
            flag = " · 🤖 agent-can-enter" if g.get("agent") else ""
            w.write(f"- **{g['title']}** · {money} · {g['bids']} bids · {g['src']}{flag}\n  {g['url']}\n  > {' '.join(g['text'].split())[:240]}\n")
    print(f"{len(rows)} new gigs -> {f}")


if __name__ == "__main__":
    if "--test" in sys.argv:
        base = {"text": "", "bids": 3, "usd": (20, 50), "src": "freelancer"}
        assert good({**base, "title": "Scrape product prices to Excel"})
        assert not good({**base, "title": "Design a logo"})
        assert not good({**base, "title": "Python bot", "bids": 90})
        assert not good({**base, "title": "Scraper", "usd": (2000, 5000)})
        assert good({**base, "title": "Smart contract audit", "src": "algora"})
        assert good({**base, "title": "Write a thread about X", "src": "superteam",
                     "text": "bounty agent submission for code review", "agent": True})
        assert not good({**base, "title": "Write a thread about X", "src": "superteam",
                         "text": "social content promo", "agent": True})
        assert not good({**base, "title": "Make a meme video", "src": "superteam"})
        sys.exit(print("ok"))
    main()
