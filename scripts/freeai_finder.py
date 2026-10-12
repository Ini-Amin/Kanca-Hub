#!/usr/bin/env python3
"""Weekly free-AI finder: diff curated GitHub lists of free LLM APIs, report only new entries.
Usage: freeai_finder.py [--test]   (writes reports/free-ai-YYYY-MM-DD.md when something is new)"""
import json, re, sys, urllib.request
from datetime import date
from pathlib import Path

LISTS = ["mnfst/awesome-free-llm-apis", "nejib1/Free-LLM"]
SEEN = Path.home() / ".config/auto-freecf/freeai_seen.json"
OUT = Path(__file__).resolve().parent.parent / "reports"
LINK = re.compile(r"\[([^\]]{2,80})\]\((https?://[^)\s]+)\)")
SKIP = ("github.com/", "shields.io", "img.", "badge", "#")


def readme(repo):
    req = urllib.request.Request(f"https://raw.githubusercontent.com/{repo}/HEAD/README.md",
                                 headers={"User-Agent": "freeai-finder"})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")


def entries(md):
    out = {}
    for line in md.splitlines():
        for name, url in LINK.findall(line):
            if not any(s in url for s in SKIP):
                out.setdefault(url.split("?")[0].rstrip("/"), (name, line.strip()[:200]))
    return out


def main():
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    found = {}
    for repo in LISTS:
        try:
            for url, v in entries(readme(repo)).items():
                found.setdefault(url, (repo, *v))
        except Exception as e:
            print(f"{repo}: {e}", file=sys.stderr)
    if not found:
        sys.exit("all lists failed; nothing saved")
    new = {u: v for u, v in found.items() if u not in seen}
    first_run = not seen
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(seen | found.keys())))
    if first_run:
        return print(f"baseline saved: {len(found)} entries (next run reports only new ones)")
    if not new:
        return print("nothing new")
    OUT.mkdir(exist_ok=True)
    f = OUT / f"free-ai-{date.today()}.md"
    f.write_text(f"# New free-AI entries {date.today()}\n\n" + "".join(
        f"- **{n}** {u} (from {r})\n  > {l}\n" for u, (r, n, l) in sorted(new.items())))
    print(f"{len(new)} new -> {f}")


if __name__ == "__main__":
    if "--test" in sys.argv:
        e = entries("| [Groq](https://groq.com/?x=1) | free |\n![b](https://img.shields.io/x) [repo](https://github.com/a/b)")
        assert list(e) == ["https://groq.com"], e
        sys.exit(print("ok"))
    main()
