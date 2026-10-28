#!/usr/bin/env python3
"""persona — ONE home for the fake human identities every farm fills into forms.

Why this exists: farms used to invent their own names, and several hardcoded a
`kanca`/`Kanca` prefix. That prefix is a fingerprint — every account from every
farm obviously shares one owner, and some sites DISABLE accounts that
look bulk-registered. So names are drawn from region-tagged pools instead, and
the prefix never appears.

Rules baked in here:
  * a given name is only ever paired with a surname from the SAME region, so the
    name stays plausible instead of mixing continents;
  * `display_name()` returns `[A-Za-z0-9_]`-safe text (sites reject spaces in a handle);
  * `full_name()` returns "First Last" (for web forms that want a space);
  * `username()` returns a lowercase handle, optionally constrained to a length.

Import it anywhere:
    from persona import full_name, display_name, username, password
"""
from __future__ import annotations

import random
import string

# Region-tagged pools. Add regions, not prefixes.
_NAMES: tuple[dict[str, tuple[str, ...]], ...] = (
    {"first": ("rizky", "dimas", "bayu", "arif", "fajar", "putra", "reza", "yusuf",
               "siti", "dewi", "ayu", "putri", "sari", "indah", "wulan", "intan",
               "budi", "eko", "wahyu", "ilham", "lukman", "nanda"),
     "last": ("pratama", "saputra", "wijaya", "hidayat", "nugroho", "setiawan",
              "kusuma", "permana", "gunawan", "santoso", "wibowo", "lestari")},
    {"first": ("miguel", "carlos", "javier", "diego", "andres", "mateo", "lucas",
               "sofia", "valentina", "camila", "isabella", "daniela", "gabriel",
               "rafael", "thiago", "bruno", "pedro", "ana", "lara", "marta"),
     "last": ("garcia", "rodriguez", "martinez", "lopez", "gonzalez", "perez",
              "sanchez", "ramirez", "torres", "flores", "rivera", "castillo")},
    {"first": ("ahmed", "mohamed", "youssef", "omar", "khaled", "hassan", "tariq",
               "layla", "amina", "noor", "fatima", "sara", "zainab", "yasmine",
               "mustafa", "ibrahim", "karim", "rania", "hana", "salma"),
     "last": ("khan", "ahmed", "hassan", "ali", "hussain", "mahmoud", "ibrahim",
              "abbas", "mansour", "nasser", "khalil", "saleh")},
    {"first": ("james", "michael", "david", "daniel", "ryan", "kevin", "brian",
               "jessica", "emily", "ashley", "sarah", "lauren", "megan", "hannah",
               "matthew", "andrew", "joshua", "rachel", "olivia", "chloe"),
     "last": ("smith", "johnson", "williams", "brown", "jones", "miller", "davis",
              "wilson", "moore", "taylor", "anderson", "thomas", "clark")},
    {"first": ("lukas", "maximilian", "felix", "jonas", "leon", "niklas", "elena",
               "anna", "laura", "julia", "sophie", "marie", "thomas", "stefan",
               "andreas", "klaus", "ines", "katharina", "franziska", "sebastian"),
     "last": ("muller", "schmidt", "schneider", "fischer", "weber", "meyer",
              "wagner", "becker", "hoffmann", "schulz", "koch", "richter")},
    {"first": ("arjun", "rohan", "vikram", "rahul", "aditya", "karan", "sanjay",
               "priya", "ananya", "neha", "pooja", "divya", "kavya", "ria",
               "varun", "nikhil", "sameer", "meera", "isha", "tara"),
     "last": ("sharma", "verma", "patel", "gupta", "singh", "kumar", "reddy",
              "nair", "mehta", "joshi", "chopra", "desai")},
    {"first": ("minjun", "jihoon", "seoyeon", "haruto", "yuki", "sora", "ren",
               "akira", "kenji", "sakura", "yuna", "hana", "wei", "jun", "mei",
               "li", "chen", "xiaoyu", "thi", "linh"),
     "last": ("kim", "lee", "park", "choi", "tanaka", "suzuki", "sato", "yamamoto",
              "wang", "zhang", "liu", "chen", "nguyen", "tran", "pham")},
    {"first": ("ivan", "dmitri", "nikolai", "alexei", "sergei", "andrei", "pavel",
               "olga", "natasha", "irina", "svetlana", "ekaterina", "yuri", "boris",
               "viktor", "mikhail", "tatiana", "elena", "ksenia", "artem"),
     "last": ("petrov", "ivanov", "sokolov", "popov", "volkov", "smirnov", "kuznetsov",
              "novak", "kowalski", "horvath", "dvorak", "popescu")},
)


def _region(region: str = "") -> dict:
    if region.upper() in ("US", "EN", "ANGLO"):
        return _NAMES[3]
    return random.choice(_NAMES)


def _parts(second: bool = True, region: str = "") -> list[str]:
    r = _region(region)
    out = [random.choice(r["first"])]
    if second and random.random() < 0.30:
        out.append(random.choice(r["first"]))
    out.append(random.choice(r["last"]))
    return out


def full_name(*, number: bool = True, region: str = "") -> str:
    """"First Last" (space-separated) for web forms that expect a person name."""
    name = " ".join(p.capitalize() for p in _parts(region=region))
    if number and random.random() < 0.25:
        name += str(random.randint(10, 99))
    return name


def display_name(*, number: bool = True) -> str:
    """`[A-Za-z0-9_]`-only name (most sites reject spaces in a handle)."""
    name = "_".join(_parts())
    if number and random.random() < 0.25:
        name += str(random.randint(10, 99))
    return name


def username(prefix: str = "", *, min_len: int = 0, max_len: int = 0,
             digits: int = 4) -> str:
    """A lowercase handle. `prefix` is allowed but should be a NON-identifying
    word (or empty) — never a shared brand token. Pads with digits to reach
    `min_len`, truncates to `max_len`, and keeps only [a-z0-9_]."""
    base = "_".join(_parts(second=False))
    if digits:
        base += "".join(random.choices(string.digits, k=digits))
    handle = (prefix + base).lower()
    handle = "".join(c for c in handle if c.isalnum() or c == "_")
    if min_len and len(handle) < min_len:
        handle += "".join(random.choices(string.ascii_lowercase + string.digits,
                                         k=min_len - len(handle)))
    if max_len:
        handle = handle[:max_len]
    return handle


def password(n: int = 14, *, require_special: bool = True) -> str:
    """A strong password that always satisfies 'letter + digit (+ symbol)' rules."""
    core = "".join(random.choices(string.ascii_letters + string.digits, k=max(6, n - 2)))
    out = list(core)
    random.shuffle(out)
    out.append(random.choice(string.digits))
    if require_special:
        out.append(random.choice("!@#$%^&*-_"))
    return "".join(out)[:max(n, 8)]


if __name__ == "__main__":
    import re
    import sys
    if "--test" in sys.argv:
        safe = re.compile(r"^[A-Za-z0-9_]+$")
        for _ in range(400):
            assert safe.match(display_name()), display_name()
            assert "kanca" not in full_name().lower()
            assert "kanca" not in username().lower()
            assert len(password(14)) >= 8
        sys.exit(print("ok"))
    for _ in range(12):
        print(f"  {display_name():26s} {full_name():24s} {username():22s}")
