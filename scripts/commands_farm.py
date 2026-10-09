"""commands_farm — k12, warp, and yowes commands for kancahub."""
from __future__ import annotations

from pathlib import Path
import os
import sys


def _k12_guided(py) -> int:
    """Guided K-12 verification, mirroring run_cmd.bat's [1]-[13] modes."""
    import kancahub
    script = kancahub.K12_DIR / "script.py"
    if not script.exists():
        print(kancahub.col("red", f"✗ {script} not found"))
        return 1

    def _ask(prompt, default=""):
        d = f" [Enter = {default}]" if default else ""
        try:
            return input(f"  {kancahub.col('bold', prompt)}{d}: ").strip() or default
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)

    print(kancahub.col("bold", "\n  K-12 / SheerID verification (guided)\n"))
    url = _ask("Paste the SheerID verification URL")
    if not url:
        print(kancahub.col("red", "  ✗ need a URL"))
        return 1

    print(kancahub.col("bold", "\n  Connection mode:"))
    print("   [1] direct + temp email        (default, simplest)")
    print("   [2] proxy ip:port")
    print("   [3] proxy user:pass@ip:port")
    print("   [4] debug, no proxy")
    print("   [7] no temp email")
    print("   [10] manual email (send to your inbox)")
    print("   [g] use local gateway 127.0.0.1:8888 (run 'kancahub proxy start' first)")
    mode = _ask("Pick", "1").lower()

    cmd = [py, str(script), url]
    if mode == "2":
        cmd += ["--proxy", _ask("proxy IP:PORT")]
    elif mode == "3":
        cmd += ["--proxy", _ask("user:pass@IP:PORT")]
    elif mode == "4":
        cmd += ["--debug"]
    elif mode == "7":
        cmd += ["--no-temp-email"]
    elif mode == "10":
        cmd += ["--email", _ask("your email")]
    elif mode == "g":
        cmd += ["--proxy", "127.0.0.1:8888"]

    return kancahub.run(cmd, cwd=kancahub.K12_DIR)


def cmd_k12(a) -> int:
    import kancahub
    py = kancahub.pick_python()
    py_camo = kancahub.pick_python(camoufox=True)
    sub = a.k12_cmd

    if sub == "verify":
        script = kancahub.K12_DIR / "script.py"
        if not script.exists():
            print(kancahub.col("red", f"✗ {script} not found"))
            return 1
        if not a.url:
            print(kancahub.col("red", "✗ url required: kancahub k12 verify <sheerid-url>"))
            return 1
        mode = getattr(a, "proxy", None) or kancahub.PROXY_AUTO
        choice = kancahub._choose_egress("127.0.0.1:8888" if getattr(a, "gateway", False) else mode,
                                kancahub.EGRESS_TARGETS["k12"])
        cmd = [py, str(script), a.url]
        if choice.proxy:
            cmd += ["--proxy", choice.proxy]
        elif choice.unavailable and mode not in (kancahub.PROXY_AUTO, kancahub.PROXY_NONE):
            cmd += ["--proxy", mode]  # explicit URL, egress helper unavailable
        if a.debug:
            cmd += ["--debug"]
        if a.email:
            cmd += ["--email", a.email]
        if a.no_temp_email:
            cmd += ["--no-temp-email"]
        if a.ask_email:
            cmd += ["--ask-email"]
        try:
            return kancahub.run(cmd, cwd=kancahub.K12_DIR)
        finally:
            kancahub._stop_auto_gateways()

    if sub == "auto":
        # Preferred: OUR k12_camoufox flow, which signs up with a REAL school
        # mailbox (binus.ac.id via K12_MAIL_PROVIDER=school), reads the OTP from
        # the logged-in M365 session, then walks signup -> age gate -> SheerID.
        # OpenAI accepts binus.ac.id, so this clears the "school email" gate that
        # the old temp.tf flow (auto_k12_flow.py) stalls on.
        camoufox_flow = kancahub.AUTO_FREECF / "scripts" / "k12_camoufox.py"
        original = kancahub.K12_DIR / "auto_k12_flow.py"
        mode = getattr(a, "proxy", None) or kancahub.PROXY_AUTO
        if getattr(a, "no_proxy", False):
            mode = kancahub.PROXY_NONE
        choice = kancahub._choose_egress(mode, kancahub.EGRESS_TARGETS["k12"])
        env = kancahub._proxy_env(choice)  # None when direct (so --no-proxy stays clean)
        # The school mailbox provider is a process-wide default, not a proxy var:
        # set it in os.environ so it reaches the child even on the direct path.
        kancahub.os.environ.setdefault("K12_MAIL_PROVIDER", "school")
        if camoufox_flow.exists():
            print(kancahub.col("cyan", "Full auto flow: ChatGPT signup -> OTP -> SheerID -> K12Verifier"))
            print(kancahub.col("dim", "  uses YOUR school mailbox (binus.ac.id via K12_MAIL_PROVIDER=school) + Camoufox"))
            py_camo = kancahub.pick_python(camoufox=True)
            try:
                return kancahub.run([py_camo, str(camoufox_flow)], cwd=kancahub.AUTO_FREECF, env=env)
            finally:
                kancahub._stop_auto_gateways()
        # Fallback: the old tool flow (DrissionPage + temp.tf) if ours is missing.
        if original.exists():
            print(kancahub.col("yellow", "k12_camoufox.py missing; using the legacy temp.tf flow"))
            try:
                return kancahub.run([py, str(original)], cwd=kancahub.K12_DIR, env=env)
            finally:
                kancahub._stop_auto_gateways()
        print(kancahub.col("red", "✗ no k12 auto flow found"))
        return 1

    if sub == "inject":
        inj = kancahub.AUTO_FREECF / "scripts" / "chatgpt_9router.py"
        if not inj.exists():
            print(kancahub.col("red", "✗ chatgpt_9router.py not found"))
            return 1
        sess = a.session
        if not sess:
            for cand in (kancahub.K12_DIR / "k12_sessions.json", kancahub.Path.cwd() / "k12_sessions.json"):
                if cand.exists():
                    sess = str(cand); break
        if not sess:
            print(kancahub.col("red", "✗ no session file. Run `kancahub k12 auto` first, or pass --session <file>"))
            return 1
        cmd = [py, str(inj), "inject", "--session", sess]
        if a.dry_run:
            cmd.append("--dry-run")
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    if sub == "sync":
        inj = kancahub.AUTO_FREECF / "scripts" / "chatgpt_9router.py"
        cmd = [py, str(inj), "sync"]
        if a.prune:
            cmd.append("--prune")
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    if sub == "run":
        # Guided menu that mirrors the original run_cmd.bat [1]-[13].
        return kancahub._k12_guided(py)

    if sub == "modes":
        print(kancahub.col("bold", "\nK-12 connection modes (maps to run_cmd.bat [1]-[12]):"))
        rows = [
            ("1", "direct + temp email", "kancahub k12 verify <url>"),
            ("2", "proxy ip:port", "kancahub k12 verify <url> --proxy IP:PORT"),
            ("3", "proxy auth", "kancahub k12 verify <url> --proxy user:pass@IP:PORT"),
            ("4", "debug no proxy", "kancahub k12 verify <url> --debug"),
            ("7", "no temp email", "kancahub k12 verify <url> --no-temp-email"),
            ("10", "manual email", "kancahub k12 verify <url> --email you@x.com"),
            ("--", "use local gateway", "kancahub k12 verify <url> --gateway"),
            ("--", "full auto account", "kancahub k12 auto"),
        ]
        for n, label, ex in rows:
            print(f"  [{n:>2}] {label:22s} {kancahub.col('dim', ex)}")
        return 0

    if sub == "link-finder":
        finder = kancahub.AUTO_FREECF / "scripts" / "sheerid_link_finder.py"
        if not finder.exists():
            print(kancahub.col("red", f"✗ sheerid_link_finder.py not found at {finder}"))
            print(kancahub.col("dim", "  It has not been created yet — add scripts/sheerid_link_finder.py, then re-run."))
            return 1
        extra = [x for x in (a.extra or [])]
        if extra and extra[0] == "--":
            extra = extra[1:]
        return kancahub.run([py_camo, str(finder)] + extra, cwd=kancahub.AUTO_FREECF)

    print(kancahub.col("red", "✗ unknown k12 command"))
    return 1


def cmd_warp(a) -> int:
    import kancahub
    py = kancahub.pick_python()
    wm = kancahub.AUTO_FREECF / "scripts" / "warp_manager.py"
    if not wm.exists():
        print(kancahub.col("red", f"✗ warp_manager.py not found at {wm}"))
        return 1
    sub = a.warp_cmd or "status"
    if sub == "up":
        return kancahub.run([py, str(wm), "up"])
    if sub == "down":
        return kancahub.run([py, str(wm), "down"])
    if sub == "gen":
        return kancahub.run([py, str(wm), "gen"])
    # status
    return kancahub.run([py, str(wm), "status"])


def cmd_yowes(a) -> int:
    import kancahub
    py = kancahub.pick_python()
    if not kancahub.YOWES.exists():
        print(kancahub.col("red", f"✗ Yowes not found at {kancahub.YOWES}"))
        return 1
    sub = a.yowes_cmd

    if sub == "list":
        script = (
            "import sys; sys.path.insert(0,'.');"
            "from countries import list_countries, get_country;"
            "print('Countries & document types:');"
            "[print(f'  {c:14s} {get_country(c)().get_country_name():14s} -> ' + ', '.join(get_country(c)().get_document_types())) for c in list_countries()]"
        )
        return kancahub.run([py, "-c", script], cwd=kancahub.YOWES)

    if sub == "schools":
        script = (
            "import sys; sys.path.insert(0,'.');"
            "from countries import get_country;"
            f"gen=get_country('{a.country}')();"
            "print(f'{len(gen.schools)} schools for', gen.get_country_name());"
            "[print('  ', s['name']) for s in gen.schools]"
        )
        return kancahub.run([py, "-c", script], cwd=kancahub.YOWES)

    if sub == "make":
        types = a.types.split(",") if a.types else None
        script = f"""
import sys; sys.path.insert(0,'.')
from mcp_server import generate_documents
res = generate_documents(
    country={a.country!r}, first_name={a.first!r}, last_name={a.last!r},
    school_name={a.school!r}, position={a.position!r}, date_of_birth={a.dob!r},
    gender={a.gender!r}, document_types={types!r}, output_dir={a.out!r},
)
print('✓ generated', res['count'], 'document(s) ->', res['output_dir'])
for f in res['files']: print('   ', f)
"""
        return kancahub.run([py, "-c", script], cwd=kancahub.YOWES)

    if sub == "k12":
        # Bridge: use Tool A's generate_teacher_doc.py (wraps yowes USGenerator)
        bridge = kancahub.K12_ROOT / "generate_teacher_doc.py"
        if not bridge.exists():
            print(kancahub.col("red", f"✗ {bridge} not found"))
            return 1
        cmd = [py, str(bridge)]
        if a.first:
            cmd += ["--first", a.first]
        if a.last:
            cmd += ["--last", a.last]
        if a.school:
            cmd += ["--school", a.school]
        if a.out:
            cmd += ["--out", a.out]
        return kancahub.run(cmd, cwd=kancahub.K12_ROOT)

    if sub == "gui":
        gui = kancahub.YOWES / "main_gui.py"
        return kancahub.run([py, str(gui)], cwd=kancahub.YOWES)

    if sub == "mcp":
        return kancahub.run([py, "mcp_server.py"], cwd=kancahub.YOWES)

    print(kancahub.col("red", "✗ unknown yowes command"))
    return 1
