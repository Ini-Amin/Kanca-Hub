#!/usr/bin/env python3
"""Daily gig finder: small paid jobs our agents can do (scraping, bots, automation, data).
Sources: Freelancer.com public API + Reddit r/forhire, r/slavelabour (best effort, RSS often 429s).
Usage: gig_finder.py [--test]   -> reports/gigs-YYYY-MM-DD.md with only gigs not seen before."""
import json, re, sys, time, urllib.parse, urllib.request
from datetime import date
from html import unescape
from pathlib import Path

QUERIES = ["web scraping", "python automation", "telegram bot", "n8n", "data entry python", "selenium", "chrome extension"]
FIT = re.compile(r"scrap|crawl|automat|bot|n8n|zapier|make\.com|python|selenium|playwright|extension|excel|sheets|data|api", re.I)
NOPE = re.compile(r"\b(wordpress theme|logo|video edit|voice|translation|writer|seo backlinks|onsite|full[- ]time)\b", re.I)
MIN_USD, MAX_USD, MAX_BIDS = 10, 500, 40
SEEN = Path.home() / ".config/auto-freecf/gigs_seen.json"
OUT = Path(__file__).resolve().parent.parent / "reports"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) gigfinder/0.1"}


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8", "replace")


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


def good(g):
    blob = f"{g['title']} {g['text']}"
    hi = g["usd"][1]
    return FIT.search(blob) and not NOPE.search(blob) and g["bids"] <= MAX_BIDS and (hi == 0 or MIN_USD <= hi <= MAX_USD)


def main():
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    gigs = {}
    for name, it in [(q, lambda q=q: freelancer(q)) for q in QUERIES] + [(s, lambda s=s: reddit(s)) for s in ("forhire", "slavelabour")]:
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
            w.write(f"- **{g['title']}** · {money} · {g['bids']} bids · {g['src']}\n  {g['url']}\n  > {' '.join(g['text'].split())[:240]}\n")
    print(f"{len(rows)} new gigs -> {f}")


if __name__ == "__main__":
    if "--test" in sys.argv:
        base = {"text": "", "bids": 3, "usd": (20, 50)}
        assert good({**base, "title": "Scrape product prices to Excel"})
        assert not good({**base, "title": "Design a logo"})
        assert not good({**base, "title": "Python bot", "bids": 90})
        assert not good({**base, "title": "Scraper", "usd": (2000, 5000)})
        sys.exit(print("ok"))
    main()
