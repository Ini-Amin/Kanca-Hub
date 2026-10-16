"""commands_zmine — referee-focused zmine.si status for kancahub (agents only).

Reads the LOCAL store (zmine_accounts.jsonl) + live-checks usable accounts
(login -> overview). Never registers, never writes. Registration tuning lives
in scripts/zmine.py + systemd units (owned by another agent — do NOT touch).

Money rule: ref count is vanity, active miners pay (5% commission + session
bonus). This command surfaces WHO mines, not how many exist.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _load_store() -> tuple[Path, list[dict]]:
    p = Path.home() / ".config" / "auto-freecf" / "zmine_accounts.jsonl"
    if not p.exists():
        return p, []
    out = []
    for ln in p.read_text(errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            pass
    return p, out


def cmd_zmine(a) -> int:
    """Referee-focused zmine status: usable vs disabled, session, timer."""
    import kancahub
    sys.path.insert(0, str(kancahub.SCRIPTS_DIR))
    try:
        import zmine as Z  # noqa: PLC0415 (read-only use: login/overview)
    except Exception as e:  # noqa: BLE001
        print(kancahub.col("red", f"✗ zmine.py unavailable: {e}"))
        return 1

    sub = getattr(a, "zmine_cmd", None) or "status"
    store_path, accts = _load_store()
    usable = [x for x in accts if not x.get("disabled")]
    dead = [x for x in accts if x.get("disabled")]

    if sub == "status":
        print(kancahub.col("cyan", "\nZmine farm — referee status (active miners pay, count is vanity):"))
        print(f"  • Store        : {store_path} ({len(accts)} stored)")
        print(f"  • Usable       : {kancahub.col('green', str(len(usable)))} login-ok")
        print(f"  • Disabled     : {kancahub.col('yellow', str(len(dead)))} (401, keep stamped — never re-register a mining name)")
        for x in usable:
            name = x.get("displayName") or "?"
            ref = x.get("ref") or "(main)"
            print(f"    - {name:24s} ref={ref} proxy={'pool' if x.get('proxy') else 'direct'}")
        # timer state (read-only)
        import subprocess
        try:
            r = subprocess.run(["systemctl", "--user", "show", "zmine-tick.timer",
                                "--property=ActiveState,NextElapseUSec"],
                               capture_output=True, text=True, timeout=5)
            print(f"  • Timer        : {r.stdout.strip().replace(chr(10), ' ')}")
        except Exception:  # noqa: BLE001
            print("  • Timer        : (unknown — systemctl unavailable)")
        print(kancahub.col("dim", "  Detail: docs/AGENT_ZMINE.md (agents only). Dashboard is the app screen."))
        return 0

    if sub == "refs":
        print(kancahub.col("cyan", "\nZmine referees — who mines (commission source):"))
        print(f"  {'MINER':24s} {'SESSION':12s} {'TASKS':10s} {'MINED*':>10s}")
        for x in usable:
            name = (x.get("displayName") or "?")[:24]
            try:
                code, j, cookie = Z.login(x["loginKey"])
            except Exception as e:  # noqa: BLE001
                print(f"  {name:24s} login-err {type(e).__name__}")
                continue
            if code != 200 or not cookie:
                print(f"  {name:24s} login {code} (died since last tick?)")
                continue
            try:
                c2, m = Z.mining_overview(cookie)
                hp = (m.get("hashpower") or {}) if isinstance(m, dict) else {}
                sess = ((m.get("miner") or {}).get("sessionExpiresAt", "") or "")[:10] if isinstance(m, dict) else ""
                c3, t = Z.tasks_next(cookie)
                st = (t.get("stats") or {}) if isinstance(t, dict) else {}
                tasks = f"{st.get('correctToday', '?')}/{st.get('cap', '?')}" if st else "?"
            except Exception:  # noqa: BLE001
                print(f"  {name:24s} overview-err")
                continue
            print(f"  {name:24s} {sess:12s} {tasks:10s} {str(hp.get('totalMh', '?')):>10s}")
        print(kancahub.col("dim", "  *THEY MINED per miner lives on the dashboard (app screen), not the API."))
        print(kancahub.col("dim", "  Keep every usable session alive: kancahub zmine keepalive."))
        return 0

    if sub == "keepalive":
        print(kancahub.col("cyan", f"\nZmine keepalive — tick {len(usable)} usable account(s):"))
        try:
            Z.tick(model=getattr(a, "model", None) or Z.TASK_MODEL,
                   want=int(getattr(a, "want", 12) or 12),
                   pace=float(getattr(a, "pace", 20.0) or 20.0))
        except Exception as e:  # noqa: BLE001
            print(kancahub.col("red", f"✗ keepalive failed: {e}"))
            return 1
        print(kancahub.col("green", "✓ keepalive done (sessions renewed, tasks topped up)."))
        return 0

    print(kancahub.col("red", f"✗ unknown zmine subcommand {sub!r}"))
    return 1
