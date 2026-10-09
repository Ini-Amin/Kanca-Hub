"""commands_misc — ip-reuse, egress-node, and report commands for kancahub."""
from __future__ import annotations

from pathlib import Path
import sys


def cmd_ip_reuse(a) -> int:
    """Show the CGNAT-aware session guard: exit-IP reuse per IP + mid-session changes."""
    import kancahub
    py = kancahub.pick_python()
    tool = kancahub.AUTO_FREECF / "scripts" / "session_guard.py"
    if not tool.exists():
        print(kancahub.col("red", "✗ session_guard.py not found"))
        return 1
    if getattr(a, "clear", False):
        try:
            Path(kancahub.SESSION_GUARD_STATE).unlink()
            print(kancahub.col("green", f"✓ cleared {kancahub.SESSION_GUARD_STATE}"))
        except FileNotFoundError:
            print(kancahub.col("dim", "nothing to clear"))
        return 0
    return kancahub.run([py, str(tool), "report"], cwd=kancahub.AUTO_FREECF)


def cmd_egress_node(a) -> int:
    """Use a device's own connection as an egress node (serve here / register remote)."""
    import kancahub
    py = kancahub.pick_python()
    tool = kancahub.AUTO_FREECF / "scripts" / "egress_node.py"
    if not tool.exists():
        print(kancahub.col("red", "✗ egress_node.py not found"))
        return 1
    sub = getattr(a, "en_cmd", None) or "list"
    if sub == "serve":
        cmd = [py, str(tool), "serve", "--host", getattr(a, "host", "0.0.0.0"),
               "--port", str(getattr(a, "port", 8899))]
        if getattr(a, "background", False):
            return kancahub._spawn_background(cmd, cwd=kancahub.AUTO_FREECF, name="egress-node",
                                              ready_port=int(getattr(a, "port", 8899)))
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)
    if sub == "add":
        return kancahub.run([py, str(tool), "add", a.name, a.url], cwd=kancahub.AUTO_FREECF)
    if sub == "remove":
        return kancahub.run([py, str(tool), "remove", a.name], cwd=kancahub.AUTO_FREECF)
    return kancahub.run([py, str(tool), "list"], cwd=kancahub.AUTO_FREECF)


def cmd_report(a) -> int:
    """Show the farm run ledger (what each farm attempt produced / where it stopped)."""
    import kancahub
    sys.path.insert(0, str(kancahub.SCRIPTS_DIR))
    try:
        import farm_ledger
    except Exception as e:  # noqa: BLE001
        print(kancahub.col("red", f"✗ farm_ledger unavailable: {e}"))
        return 1
    if getattr(a, "clear", False):
        try:
            Path(farm_ledger.LEDGER_PATH).unlink()
            print(kancahub.col("green", f"✓ cleared {farm_ledger.LEDGER_PATH}"))
        except FileNotFoundError:
            print(kancahub.col("dim", "nothing to clear"))
        except Exception as e:  # noqa: BLE001
            print(kancahub.col("red", f"✗ {e}"))
            return 1
        return 0
    print(kancahub.col("bold", f"\n  Farm run ledger  ({farm_ledger.LEDGER_PATH})\n"))
    print(farm_ledger.summarize(farm_ledger.read()))
    n = int(getattr(a, "tail", 0) or 0)
    if n:
        print(kancahub.col("bold", f"\n  Last {n} runs:\n"))
        for r in farm_ledger.read()[-n:]:
            flag = "✅" if r.get("ok") else "❌"
            print(f"  {flag} {r.get('ts','')} {r.get('farm',''):<9} "
                  f"egress={r.get('egress','') or '-':<12} stage={r.get('stage','') or '-':<16} "
                  f"n={r.get('count',0)}")
    print()
    return 0


def cmd_rephrase(a) -> int:
    """Refusal-aware 9Router middleware — rephrase a declined prompt and retry."""
    import kancahub
    tool = kancahub.AUTO_FREECF / "scripts" / "rephraser.py"
    if not tool.exists():
        print(kancahub.col("red", f"✗ rephraser.py not found at {tool}"))
        return 1
    if getattr(a, "selftest", False):
        return kancahub.run([kancahub.pick_python(), str(tool), "--selftest"])
    sub = getattr(a, "rephrase_cmd", None) or "key"
    if sub == "key":
        return kancahub.run([kancahub.pick_python(), str(tool), "key"])
    if sub == "chat":
        cmd = [kancahub.pick_python(), str(tool), "chat", a.prompt]
        if getattr(a, "model", None):
            cmd += ["--model", a.model]
        if getattr(a, "max_rephrases", None) is not None:
            cmd += ["-n", str(a.max_rephrases)]
        if getattr(a, "json", False):
            cmd.append("--json")
        return kancahub.run(cmd)
    print(kancahub.col("red", "✗ usage: kancahub rephrase [key | chat <prompt> [--model M] [-n N] [--json]] [--selftest]"))
    return 1
