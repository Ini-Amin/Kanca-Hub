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


def bugcrowd():
    """Bugcrowd public engagements: free to join, NO bidding, NO paid account.

    This is the answer to the Freelancer blocker. Freelancer is a bidding
    marketplace — you pay for a membership and then compete on price against
    dozens of other bidders, which is the worst possible shape for an agent.
    Bugcrowd is the opposite: the program list is public and keyless, joining is
    free, and a submission is judged on the finding itself. Every entry here is
    something an autonomous worker can actually pursue end-to-end (recon ->
    triage -> draft) with no human bidding step.

    The engagements.json feed is the same one `bounty_run.py discover` uses.
    """
    d = json.loads(get("https://bugcrowd.com/engagements.json"))
    for x in (d.get("engagements") or []):
        name = x.get("name") or ""
        url = "https://bugcrowd.com" + (x.get("briefUrl") or "")
        if not x.get("briefUrl"):
            continue
        # Reward is not in the feed; a managed bounty pays and a VDP does not.
        # Keep both but mark VDPs so they do not read as paid work.
        vdp = "vdp" in (x.get("briefUrl") or "").lower()
        yield {"id": "bc" + str(x.get("id") or url), "title": name,
               "usd": (0, 0), "bids": 0,
               "text": ("bug bounty program, free to join, no bidding"
                        + (" · VDP (no reward)" if vdp else " · paid")),
               "url": url, "src": "bugcrowd", "agent": True}


BOUNTY_MIRROR = ("https://raw.githubusercontent.com/arkadiyt/"
                 "bounty-targets-data/main/data/")


def _mirror(name):
    """Pull one platform's public program list from the bounty-targets mirror.

    WHY A MIRROR AND NOT EACH PLATFORM'S OWN API:
    only Bugcrowd exposes a keyless JSON feed of its own (engagements.json).
    HackerOne, Intigriti and YesWeHack all require a session for their native
    endpoints — H1's api.hackerone.com/v1 returns 401 without a token, which is
    exactly what blocked us for days. The mirror republishes the *public scope*
    of every listed program, refreshed daily, with no auth at all. Reading scope
    never needed a token; only submitting a report does.

    HackerOne additionally has a working public GraphQL endpoint
    (POST https://hackerone.com/graphql, e.g. team(handle:"security")
    { structured_scopes { ... } }) which returns live scope per program. That is
    used by bounty_run.py when we need the current, authoritative scope for one
    specific program; this feed is for *discovery* across all programs.
    """
    d = json.loads(get(BOUNTY_MIRROR + name + "_data.json"))
    if isinstance(d, dict):
        d = list(d.values())
    return d


def _money(v):
    """Coerce a bounty bound to an int.

    The mirror is inconsistent: HackerOne/YesWeHack give numbers, Intigriti
    gives strings (sometimes "" or "0"). A raise here used to kill the whole
    provider, so every value goes through this.
    """
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "").replace("$", "")
    if not s:
        return 0
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def _targets_summary(targets):
    """Count in-scope web assets so a program with real surface ranks higher.

    A program whose entire scope is three API hosts is not worth an autonomous
    recon pass — that is the fireblocks case, where subfinder legitimately found
    nothing because there was nothing to find. Counting web-ish targets lets
    `good()` drop those instead of spending a scan on them.

    The mirror nests everything under {"in_scope": [...], "out_of_scope": [...]},
    and each platform names its fields differently:
      hackerone  asset_identifier / asset_type
      intigriti  endpoint / type           (often a prose description, type=url)
      yeswehack  target / type             (type like "web-application")
    """
    if isinstance(targets, dict):
        rows = targets.get("in_scope") or []
    else:
        rows = targets or []
    web = 0
    for t in rows:
        if not isinstance(t, dict):
            continue
        kind = str(t.get("asset_type") or t.get("type") or "").lower()
        # A wildcard/domain/url/web-application entry is real recon surface.
        if any(k in kind for k in ("url", "wildcard", "domain", "website",
                                   "web-application", "api")):
            web += 1
    return web


def _bounty_platform(name, label, url_key="url"):
    """Shared shape for hackerone / intigriti / yeswehack from the mirror."""
    for x in _mirror(name):
        if x.get("disabled"):
            continue
        title = x.get("name") or x.get("handle") or ""
        if not title:
            continue
        if name == "hackerone":
            h = x.get("handle") or ""
            url = f"https://hackerone.com/{h}"
        elif name == "intigriti":
            url = x.get("url") or ""
        else:  # yeswehack has no url field; build from handle when present
            h = x.get("handle") or x.get("id")
            url = f"https://yeswehack.com/programs/{h}" if h else ""
        if not url:
            continue
        lo = _money(x.get("min_bounty"))
        hi = _money(x.get("max_bounty"))
        web = _targets_summary(x.get("targets"))
        # A program with zero web targets has nothing for an autonomous recon
        # pass to touch; keep it visible but do not mark it agent-enterable.
        agent = web > 0
        yield {"id": f"{name[:2]}{x.get('id') or url}", "title": title,
               "usd": (int(lo), int(hi)), "bids": 0,
               "text": (f"bug bounty, keyless scope, {web} web targets, "
                        f"no bidding"
                        + ("" if agent else " · NO WEB SURFACE")),
               "url": url, "src": label, "agent": agent}


def hackerone():
    yield from _bounty_platform("hackerone", "hackerone")


def intigriti():
    yield from _bounty_platform("intigriti", "intigriti")


def yeswehack():
    yield from _bounty_platform("yeswehack", "yeswehack")


def good(g):
    blob = f"{g['title']} {g['text']}"
    hi = g["usd"][1]
    if g["src"] in ("bugcrowd", "hackerone", "intigriti", "yeswehack"):
        # A bug-bounty program is security work by definition; the generic
        # keyword filter would drop "Vinted" or "Chime" for containing no
        # tech word at all. VDPs are informational, keep them visible.
        return True
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
               + [("superteam", superteam), ("algora", algora),
                  ("bugcrowd", bugcrowd), ("hackerone", hackerone),
                  ("intigriti", intigriti), ("yeswehack", yeswehack)])
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
    notify(f, rows)


def notify(f, rows):
    """Push the digest to Telegram.

    The daily timer previously wrote the report and stopped there, so a new gig
    only surfaced if someone happened to open reports/. Delivery is the whole
    point of running unattended — a report nobody reads is the same as no report.
    Failure to deliver must not fail the run: the file is still on disk.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from notify import send
    except Exception as e:
        print(f"telegram: import failed ({e}) - report left at {f}", file=sys.stderr)
        return
    lines = [f"GIGS {len(rows)} new"]
    for g in rows[:12]:
        money = f"${g['usd'][0]}-{g['usd'][1]}" if g["usd"][1] else "budget ?"
        flag = " [agent]" if g.get("agent") else ""
        lines.append(f"- {g['title'][:70]} | {money} | {g['bids']} bids | {g['src']}{flag}")
        lines.append(f"  {g['url']}")
    if len(rows) > 12:
        lines.append(f"... +{len(rows) - 12} more in {f.name}")
    try:
        send("\n".join(lines)[:3800])
        print("telegram: sent")
    except Exception as e:
        print(f"telegram: send failed ({e}) - report left at {f}", file=sys.stderr)


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
