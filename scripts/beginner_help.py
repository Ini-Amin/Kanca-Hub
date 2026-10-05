"""Plain-English help for KancaHub beginners."""


def c(name, text):
    from beginner import c as _c
    return _c(name, text)


def explain_what_is_this() -> None:
    print()
    print(c("bold", "  What is KancaHub?"))
    print(c("dim", "  ─────────────────"))
    print("  KancaHub is a toolbox that automates a few online signups and")
    print("  document tasks for you, using YOUR computers/accounts. In plain terms:")
    print()
    print(f"  {c('green','•')} {c('bold','Free AI access')} — Cloudflare gives a free AI tier. KancaHub creates")
    print("    a Cloudflare account and grabs an API key so you can use that AI.")
    print()
    print(f"  {c('green','•')} {c('bold','Proxy')} — websites block you if too many signups come from one")
    print("    internet address. A 'proxy' makes your internet look like it comes")
    print("    from somewhere else, so you don't get blocked.")
    print()
    print(f"  {c('green','•')} {c('bold','WARP')} — a free Cloudflare tool that gives you a cleaner internet")
    print("    address (helps avoid blocks).")
    print()
    print(f"  {c('green','•')} {c('bold','Token / API key')} — a long password-like string that lets a program")
    print("    use an AI service on your behalf.")
    print()
    print(f"  {c('green','•')} {c('bold','9Router')} — a program on your computer that collects all your AI")
    print("    keys in one place so your AI apps can use them.")
    print()
    print(c("dim", "  New here? Do option 2 ('Check if everything is ready') first."))
    print()
    try:
        input("  Press Enter to go back")
    except (EOFError, KeyboardInterrupt):
        print()


def cheatsheet() -> str:
    return """\
BEGINNER CHEAT-SHEET
────────────────────
Run:            kancahub                 (this friendly menu)
                kancahub beginner        (same thing)
Check setup:    kancahub doctor
Free AI keys:   kancahub stack signup -n 1 --warp
Proxy on :8888: kancahub proxy nharvest --out-txt pool.txt
                kancahub proxy ngateway --pool pool.txt --port 8888
Stuck?          option 2 in the menu shows ❌ marks to fix

Everything you make is saved under /home/amen/Auto-FreeCF  (results, keys, docs).
Secrets live in /home/amen/.config/auto-freecf/.env  (never shared).
"""
