"""menu — unified interactive menu and guided flows for kancahub."""
from __future__ import annotations

import argparse
import sys

# ── Menu item definition ─────────────────────────────────────────────────
# Each item: (key, description, cmd_str, cmd_args, status)
# status: None = normal, "blocked" = known dead, "note" = has caveat

MenuEntry = tuple[str, str, str, list[str] | None, str | None]

CATEGORIES: list[tuple[str, str, list[MenuEntry]]] = [
    ("A", "Egress & Network Guard", [
        ("1", "Check everything is healthy", "doctor", ["doctor"], None),
        ("2", "Start rotating proxy gateway (background)", "proxy gateway", ["proxy", "gateway"], None),
        ("3", "Manage WARP tunnel (up/down/status)", "warp", ["warp"], None),
        ("4", "Harvest proxies (background)", "proxy harvest", ["proxy", "harvest"], None),
        ("5", "VPN pool: status & residential fleet", "vpn status", ["vpn", "status"], None),
    ]),
    ("B", "AI Farms & Harvesters (Live Providers)", [
        ("6",  "ZCode: auto-claim daily plans & pool", "zcode auto-claim", ["zcode", "auto-claim"], None),
        ("7",  "OpenCode Hub: free models, fleet & bridge", "opencode menu", ["opencode", "menu"], None),
        ("8",  "Freebuff: sticky-IP agent slots", "freebuff status", ["freebuff", "status"], None),
        ("9",  "Dahl Inference: 100M-token keys", "dahlfarm mint", ["dahlfarm", "mint"], None),
        ("10", "Abliteration.ai accounts + API keys", "abliteration mint", ["abliteration", "mint"], None),
        ("11", "GitHub Education account farm", "github farm", ["github", "farm"], None),
        ("12", "TokenHarbor AI keys", "thk batch", ["thk", "batch"], None),
    ]),
    ("C", "System, Bridges & Tools", [
        ("13", "Autofarm any website (paste URL → pipeline)", "autofarm", ["autofarm"], None),
        ("14", "9Router MITM proxy status & model routing", "9router mitm status", ["9router", "mitm", "status"], None),
        ("15", "Prune dead 9Router connections", "stack sync --prune", ["stack", "sync", "--prune"], None),
        ("16", "Free-AI farm status", "freeai status", ["freeai", "status"], None),
    ]),
    ("D", "Frontier & Keyless AI Fleet", [
        ("17", "Orvis AI: 9 frontier models (Claude 4, GPT-4o, Gemini 2.5)", "orvis menu", ["orvis"], None),
        ("18", "Koma-Free: keyless vLLM inference (koma/apple)", "komafree menu", ["komafree"], None),
        ("19", "OpenSatan: anonymous uncensored chat (satan/genie)", "opensatan menu", ["opensatan"], None),
        ("20", "OpenCode Anonymous: GLM-5.3 & DeepSeek (WARP gateway)", "opencode anon", ["opencode", "anon"], None),
        ("21", "AI Supplies: 22+ provider farm fleet", "supplies farm", ["supplies", "farm"], None),
    ]),
]
# Flat view for backward compat (render, dispatch, tests)
UNIFIED_MENU: list[tuple[str, str, str, list[str] | None]] = []
for _cat_key, _cat_name, _items in CATEGORIES:
    for _k, _d, _c, _a, _s in _items:
        UNIFIED_MENU.append((_k, _d, _c, _a))

# Status tags per key
_STATUS: dict[str, str | None] = {}
for _cat_key, _cat_name, _items in CATEGORIES:
    for _k, _d, _c, _a, _s in _items:
        if _s:
            _STATUS[_k] = _s

# Stage headers keyed by the FIRST item key in each category
MENU_STAGE_HEADERS: dict[str, str] = {}
for _cat_key, _cat_name, _items in CATEGORIES:
    if _items:
        MENU_STAGE_HEADERS[_items[0][0]] = f"[{_cat_key}] {_cat_name}"

# Menu keys that start LONG-RUNNING servers/loops -> run as background jobs.
MENU_BACKGROUND: dict[str, str] = {
    "2": "gateway",
    "4": "harvest",
}

MENU_BACKGROUND_CMD: dict[str, list[str]] = {
    "2": ["proxy", "gateway", "--port", "8888", "--target", "30"],
    "4": ["proxy", "harvest", "--target", "20"],
}

# Menu keys that are account farms -> the menu asks for count/pace/egress.
MENU_FARM_KEYS = {"11", "12"}

# Items needing custom interactive prompts before dispatch.
_INTERACTIVE_HANDLERS: dict[str, str] = {
    "13": "_handle_autofarm",
    "17": "_handle_orvis",
    "18": "_handle_komafree",
    "19": "_handle_opensatan",
    "20": "_handle_opencode_anon",
    "21": "_handle_supplies",
}
_STATUS_LABELS: dict[str, str] = {
    "blocked": " \x1b[31m[blocked]\x1b[0m",
    "note":    " \x1b[33m[!]\x1b[0m",
}


def render_menu() -> str:
    """Render the unified menu as text (non-interactive, testable)."""
    import kancahub
    lines = []
    lines.append(f" {kancahub.C['bold']}KancaHub{kancahub.C['reset']}")
    lines.append("")
    for cat_key, cat_name, items in CATEGORIES:
        lines.append(f" {kancahub.C['bold']}{kancahub.C['cyan']}── [{cat_key}] {cat_name} ──{kancahub.C['reset']}")
        for key, desc, cmd_str, _, status in items:
            cmd_part = f" {kancahub.C['cyan']}({cmd_str}){kancahub.C['reset']}"
            tag = _STATUS_LABELS.get(status or "", "")
            lines.append(f"  [{kancahub.col('bold', key):>4}] {desc:<44}{cmd_part}{tag}")
        lines.append("")
    lines.append(f"  [{kancahub.col('bold', '0')}]  Run end-to-end setup (guided pipeline)")
    lines.append(f"  [{kancahub.col('bold', 'b')}]  Beginner guide (plain English)")
    lines.append(f"  [{kancahub.col('bold', 'j')}]  Manage background jobs")
    lines.append(f"  [{kancahub.col('bold', 'h')}]  Help & command reference")
    lines.append(f"  [{kancahub.col('bold', 'q')}]  Exit")
    lines.append("")
    return "\n".join(lines)


def _all_keys() -> list[str]:
    return [item[0] for item in UNIFIED_MENU]


def run_end_to_end_flow(p: argparse.ArgumentParser, farm_choice: str | None = None) -> int:
    """
    Execute the safe end-to-end pipeline in order:
      (a) Proxy verify / start (egress)
      (b) Chosen account farm
      (c) Inject to 9Router
      (d) Sync / health check

    Stops and reports honest error if any step fails.
    """
    import kancahub
    print(kancahub.col("bold", "\n════════════════════════════════════════════════════════════════════════"))
    print(kancahub.col("bold", "                  KancaHub End-to-End Setup Pipeline                    "))
    print(kancahub.col("bold", "════════════════════════════════════════════════════════════════════════"))
    print(kancahub.col("dim",  "  Safe sequential flow: Egress -> Farm -> 9Router Inject -> Sync\n"))

    farms = [
        ("1", "GitHub Education", ["github", "farm"], None, ["stack", "sync"]),
        ("2", "TokenHarbor AI", ["thk", "batch"], ["thk", "inject"], ["thk", "sync"]),
        ("3", "Abliteration.ai accounts + API keys", ["abliteration", "mint"], ["abliteration", "connect"], ["stack", "sync"]),
        ("4", "ZCode: z.ai Start Plan -> glm pool", ["zcode", "claim", "--", "--connect"], None, ["stack", "sync"]),
        ("5", "Dahl Inference 100M keys", ["dahlfarm", "mint"], ["dahlfarm", "connect"], ["stack", "sync"]),
    ]

    if farm_choice is None:
        print(kancahub.col("bold", "  Choose target farm for this pipeline run:"))
        for f_key, f_name, f_cmd, _, _ in farms:
            print(f"    [{kancahub.col('bold', f_key)}] {f_name:<28} {kancahub.col('cyan', f'({chr(32).join(f_cmd)})')}")
        print(f"    [{kancahub.col('bold', 'c')}] Cancel / Back to menu\n")

        try:
            f_choice = input(f"  {kancahub.col('bold', f'Select farm [1-{len(farms)}, c]')}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nPipeline cancelled.")
            return 0
    else:
        f_choice = str(farm_choice).strip().lower()

    if f_choice in ("c", "cancel", "q", "quit", ""):
        print("\nPipeline cancelled.")
        return 0

    selected_farm = next((f for f in farms if f[0] == f_choice), None)
    if not selected_farm:
        print(kancahub.col("yellow", f"✗ Invalid selection '{f_choice}'. Pipeline aborted."))
        return 1

    _, farm_label, farm_args, inject_args, sync_args = selected_farm
    print(kancahub.col("cyan", f"\n▶ Selected pipeline target: {farm_label}\n"))

    farm_args = kancahub._menu_ask_farm_options(f_choice, list(farm_args))
    print(kancahub.col("dim", f"  → will run: kancahub {' '.join(farm_args)}\n"))

    # ── Step (a): Proxy Egress Verification / Start ──
    print(kancahub.col("bold", "[Step 1/4] Egress check / proxy verification…"))
    rc_egress = kancahub.dispatch(p, p.parse_args(["proxy", "verify"]))
    if rc_egress != 0:
        print(kancahub.col("yellow", "\n• No active proxy masking detected."))
        try:
            start_ans = input(f" {kancahub.col('bold', 'Start rotating proxy gateway on 127.0.0.1:8888 now? [Y/n]')}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            start_ans = "n"
        if start_ans in ("", "y", "yes"):
            rc_start = kancahub.dispatch(p, p.parse_args(["proxy", "start"]))
            if rc_start != 0:
                print(kancahub.col("red", "\n✗ Step 1 failed: Proxy gateway did not start. Stopping pipeline to avoid IP block."))
                return rc_start
        else:
            print(kancahub.col("yellow", "\n• Gateway declined — trying WARP as egress fallback…"))
            rc_warp = kancahub.dispatch(p, p.parse_args(["warp", "up"]))
            if rc_warp == 0 and kancahub.dispatch(p, p.parse_args(["proxy", "verify"])) == 0:
                farm_args += ["--proxy", "warp"]
                print(kancahub.col("green", "✓ Step 1 fallback: WARP egress verified.\n"))
            else:
                print(kancahub.col("red", "\n✗ Step 1 stopped: Clean proxy egress is required for farm operations."))
                return 1
    print(kancahub.col("green", "✓ Step 1 complete: Egress proxy verified.\n"))

    # ── Step (b): Account Farm ──
    print(kancahub.col("bold", f"[Step 2/4] Running {farm_label} ({' '.join(farm_args)})…"))
    rc_farm = kancahub.dispatch(p, p.parse_args(farm_args))
    if rc_farm != 0:
        print(kancahub.col("red", f"\n✗ Step 2 failed: {farm_label} returned exit code {rc_farm}."))
        print(kancahub.col("yellow", "  Stopping pipeline. Resolve issues before injecting to 9Router."))
        return rc_farm
    print(kancahub.col("green", f"✓ Step 2 complete: {farm_label} completed successfully.\n"))

    # ── Step (c): Inject to 9Router ──
    if inject_args and "--inject" in farm_args:
        print(kancahub.col("dim", "[Step 3/4] Already injected by the batch (--inject). Skipping.\n"))
    elif inject_args:
        print(kancahub.col("bold", f"[Step 3/4] Injecting credentials into 9Router ({' '.join(inject_args)})…"))
        rc_inject = kancahub.dispatch(p, p.parse_args(inject_args))
        if rc_inject != 0:
            print(kancahub.col("red", f"\n✗ Step 3 failed: 9Router injection returned exit code {rc_inject}."))
            print(kancahub.col("yellow", "  Stopping pipeline. Credentials were not registered."))
            return rc_inject
        print(kancahub.col("green", "✓ Step 3 complete: Injected credentials into 9Router.\n"))
    else:
        print(kancahub.col("dim", f"[Step 3/4] 9Router injection not required for {farm_label}. Skipping.\n"))

    # ── Step (d): Sync ──
    if sync_args:
        print(kancahub.col("bold", f"[Step 4/4] Syncing 9Router status ({' '.join(sync_args)})…"))
        rc_sync = kancahub.dispatch(p, p.parse_args(sync_args))
        if rc_sync != 0:
            print(kancahub.col("red", f"\n✗ Step 4 failed: Sync returned exit code {rc_sync}."))
            return rc_sync
        print(kancahub.col("green", "✓ Step 4 complete: Synced with 9Router.\n"))

    print(kancahub.col("green", "════════════════════════════════════════════════════════════════════════"))
    print(kancahub.col("green", f"✓ End-to-end setup for {farm_label} completed successfully!"))
    print(kancahub.col("green", "════════════════════════════════════════════════════════════════════════\n"))
    return 0


def _farm_kind(key: str, argv: list[str]) -> str:
    """Normalize a menu key / argv into a farm kind: github|thk|grok|gmail|other."""
    flat = " ".join(argv).lower()
    if "github" in flat:
        return "github"
    if "thk" in flat:
        return "thk"
    if "grok" in flat:
        return "grok"
    if "gmail" in flat:
        return "gmail"
    return "other"


def _menu_ask_farm_options(key: str, argv: list[str]) -> list[str]:
    """Prompt IN THE CLI for common farm options and append them to argv.

    Keeps everything inside `kancahub` (no need to remember flags). Every prompt
    has a default, so just pressing Enter keeps sensible behavior.
    """
    import kancahub
    def _ask(prompt: str, default: str) -> str:
        try:
            v = input(f"   {kancahub.col('bold', prompt)} [{default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return default
        return v or default

    kind = kancahub._farm_kind(key, argv)
    if kind == "other":
        return argv

    print(kancahub.col("dim", "   (Enter = keep default; these run inside this CLI)"))

    n = _ask("how many accounts", "1")
    if kind == "thk":
        argv = ["thk", "batch", n]
    elif kind == "grok":
        argv = ["grok", "run", "-n", n]
    elif kind == "gmail":
        argv = ["gmail", "farm", "--count", n]
    elif kind == "github":
        argv = [a for a in argv if a not in ("--max-accounts",)]
        if n.isdigit() and n != "1":
            argv += ["--max-accounts", n]

    if kind == "thk":
        print(kancahub.col("dim", f"   (TokenHarbor allows ~{kancahub.THK_PER_IP_MAX} signups per IP, then locks it ~1h; "
                                  f"{kancahub.THK_PER_IP_DEFAULT} is safe — the IP rotates automatically after that)"))
        per = _ask(f"accounts per IP before rotating (1-{kancahub.THK_PER_IP_MAX})", str(kancahub.THK_PER_IP_DEFAULT))
        if per.isdigit():
            argv += ["--per-ip", str(max(1, min(int(per), kancahub.THK_PER_IP_MAX)))]
        inj = _ask("Inject into 9Router (bulk) when done? (y/n)", "y").lower()
        if inj in ("y", "yes"):
            argv += ["--inject"]
        store = _ask("Store into (9router/ledger/both)", "both").lower()
        if store in ("9router", "ledger", "both"):
            argv += ["--store", store]

    if kind != "thk":
        pace = _ask("pace (fast/normal/safe)", "normal")
        if kind == "github" and pace in ("fast", "normal", "safe"):
            argv += ["--pace", pace]

    egress = _ask("egress — auto=best-available(incl. phone) / none=direct / mobile / warp", "auto")
    if egress == "mobile":
        argv += ["--mobile-rotate"]
    elif egress in ("auto", "none", "warp"):
        argv += ["--proxy", egress]
    return argv


# ── Interactive handlers for items needing custom prompts ─────────────

def _handle_autofarm(p: argparse.ArgumentParser) -> None:
    """Autofarm interactive prompt: URL, mailbox, options."""
    import kancahub
    try:
        url = input(f" {kancahub.C['bold']}Target signup/login URL: {kancahub.C['reset']}").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not url:
        return
    dom = input(f" {kancahub.C['bold']}Mailbox [1=my.id, 2=biz.id, 3=litensi, 4=static, 5=real gmail, or type your own domain]: {kancahub.C['reset']}").strip()
    if dom == "2":
        domain, mail = "kancalabs.biz.id", "auto"
    elif dom == "3":
        domain, mail = "kancalabs.my.id", "litensi"
    elif dom == "4":
        domain, mail = "kancalabs.my.id", "static"
    elif dom == "5":
        domain, mail = "kancalabs.my.id", "gmail"
    elif "@" in dom or "." in dom:
        domain = dom.lstrip("@").strip().lower()
        mail = "relay"
    else:
        domain, mail = "kancalabs.my.id", "auto"
    cmd_args = ["autofarm", url, "--domain", domain, "--mail", mail]
    inj = input(f" {kancahub.C['bold']}Inject into 9Router? (y/N): {kancahub.C['reset']}").strip().lower()
    if inj in ("y", "yes"):
        cmd_args.append("--inject-9router")
    hl = input(f" {kancahub.C['bold']}Run headless? (y/N): {kancahub.C['reset']}").strip().lower()
    if hl in ("y", "yes"):
        cmd_args.append("--headless")
    print(kancahub.col("cyan", f"\n▶ Running: kancahub {' '.join(cmd_args)}\n"))
    try:
        kancahub.dispatch(p, p.parse_args(cmd_args))
    except Exception as e:  # noqa: BLE001
        print(kancahub.col("red", f"Error: {e}"))
def _handle_orvis(p: argparse.ArgumentParser) -> None:
    """Interactive handler for Orvis AI (Claude 4, GPT-4o, Gemini 2.5)."""
    import kancahub
    print(kancahub.col("bold", "\n── [17] Orvis AI: 9 Frontier Models (100 free credits/acct) ──"))
    print("  [1] List harvested accounts & credit balance")
    print("  [2] Quick smoke test - Claude 4 Sonnet ('pong')")
    print("  [3] Quick smoke test - Gemini 2.5 Flash ('pong')")
    print("  [4] Quick smoke test - OpenAI GPT-4o ('pong')")
    print("  [5] Mint new account headlessly (Cognito + Proxy + Tempik)")
    print("  [6] Connect / inject into 9Router SQLite database")
    print("  [q] Cancel / Back to menu\n")
    try:
        c = input(f" {kancahub.C['bold']}Select [1-6, q]: {kancahub.C['reset']}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if c == "1":
        kancahub.dispatch(p, p.parse_args(["orvis", "accounts"]))
    elif c == "2":
        kancahub.dispatch(p, p.parse_args(["orvis", "test", "--model", "anthropic/claude-sonnet-4-20250514"]))
    elif c == "3":
        kancahub.dispatch(p, p.parse_args(["orvis", "test", "--model", "google/gemini-2.5-flash"]))
    elif c == "4":
        kancahub.dispatch(p, p.parse_args(["orvis", "test", "--model", "openai/gpt-4o"]))
    elif c == "5":
        kancahub.dispatch(p, p.parse_args(["orvis", "create"]))
    elif c == "6":
        kancahub.dispatch(p, p.parse_args(["orvis", "connect"]))


def _handle_komafree(p: argparse.ArgumentParser) -> None:
    """Interactive handler for Koma-Free (vLLM inference)."""
    import kancahub
    print(kancahub.col("bold", "\n── [17] Koma-Free: Keyless vLLM Inference (koma/apple) ──"))
    print("  [1] Live doctor & connectivity probe")
    print("  [2] Quick smoke test ('pong' in ~0.5s)")
    print("  [3] Custom prompt")
    print("  [q] Cancel / Back to menu\n")
    try:
        c = input(f" {kancahub.C['bold']}Select [1-3, q]: {kancahub.C['reset']}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if c == "1":
        kancahub.dispatch(p, p.parse_args(["komafree", "doctor"]))
    elif c == "2":
        kancahub.dispatch(p, p.parse_args(["komafree", "chat", "-m", "koma/apple", "-p", "pong"]))
    elif c == "3":
        try:
            txt = input(f" {kancahub.C['bold']}Prompt: {kancahub.C['reset']}").strip() or "hello"
        except (EOFError, KeyboardInterrupt):
            return
        kancahub.dispatch(p, p.parse_args(["komafree", "chat", "-m", "koma/apple", "-p", txt]))


def _handle_opensatan(p: argparse.ArgumentParser) -> None:
    """Interactive handler for OpenSatan (uncensored)."""
    import kancahub
    print(kancahub.col("bold", "\n── [18] OpenSatan: Anonymous Uncensored Chat ──"))
    print("  [1] Quick smoke test (Satan persona)")
    print("  [2] Custom prompt")
    print("  [q] Cancel / Back to menu\n")
    try:
        c = input(f" {kancahub.C['bold']}Select [1-2, q]: {kancahub.C['reset']}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if c == "1":
        kancahub.dispatch(p, p.parse_args(["opensatan", "chat", "--prompt", "respond with single word: pong"]))
    elif c == "2":
        try:
            txt = input(f" {kancahub.C['bold']}Prompt: {kancahub.C['reset']}").strip() or "hello"
        except (EOFError, KeyboardInterrupt):
            return
        kancahub.dispatch(p, p.parse_args(["opensatan", "chat", "--prompt", txt]))


def _handle_opencode_anon(p: argparse.ArgumentParser) -> None:
    """Interactive handler for OpenCode Anonymous Gateway."""
    import kancahub, json, urllib.request, time
    print(kancahub.col("bold", "\n── [19] OpenCode Anonymous Gateway (GLM-5.3 & Beam) ──"))
    print("  [1] Smoke test GLM-5.3 Flash (oc-anon/glm-5.3-flash-free)")
    print("  [2] Smoke test Beam fast model (oc-anon/beam-free)")
    print("  [3] Custom prompt")
    print("  [q] Cancel / Back to menu\n")
    try:
        c = input(f" {kancahub.C['bold']}Select [1-3, q]: {kancahub.C['reset']}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    model = "beam-free" if c == "2" else "glm-5.3-flash-free"
    prompt = "respond with single word: pong"
    if c == "3":
        try:
            prompt = input(f" {kancahub.C['bold']}Prompt: {kancahub.C['reset']}").strip() or "hello"
        except (EOFError, KeyboardInterrupt):
            return
    elif c not in ("1", "2"):
        return

    print(kancahub.col("cyan", f"[*] Calling {model} via Anonymous Gateway (:8799)..."))
    t0 = time.time()
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8799/v1/chat/completions",
            data=json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}).encode(),
            headers={"Authorization": "Bearer sk-opencode-anon-local", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            body = json.loads(r.read().decode())
            ans = body["choices"][0]["message"]["content"]
            print(kancahub.col("green", f"✓ Response ({time.time()-t0:.2f}s):"))
            print(f"  {ans}\n")
    except Exception as exc:
        print(kancahub.col("red", f"✗ Request failed: {exc}\n"))


def _handle_opencode_pool(p: argparse.ArgumentParser) -> None:
    """Interactive handler for OpenCode Zen Key Pool."""
    import kancahub, json, urllib.request
    print(kancahub.col("bold", "\n── [20] OpenCode Zen 26-Key Pool (:8791) ──"))
    print("  [1] View active pool keys & cooldown status")
    print("  [2] Quick smoke test via pool")
    print("  [q] Cancel / Back to menu\n")
    try:
        c = input(f" {kancahub.C['bold']}Select [1-2, q]: {kancahub.C['reset']}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if c == "1":
        try:
            with urllib.request.urlopen("http://127.0.0.1:8791/pool", timeout=5) as r:
                d = json.loads(r.read().decode())
                print(f"  Total Keys : {d.get('total_keys')}")
                print(f"  Active Keys: {d.get('active_keys')}")
                for item in d.get("keys", [])[:10]:
                    cd = item.get("cooldown", 0)
                    cd_str = f"cooling down ({cd}s left)" if cd > 0 else "ready"
                    print(f"    • {item.get('key'):<22} : {cd_str}")
        except Exception as exc:
            print(kancahub.col("red", f"✗ Could not query pool: {exc}"))
    elif c == "2":
        kancahub.dispatch(p, p.parse_args(["opencode", "status"]))


def _handle_supplies(p: argparse.ArgumentParser) -> None:
    """Interactive handler for AI Supplies fleet."""
    import supplies_registry
    supplies_registry.farm_supply()


# ── Background job management ─────────────────────────────────────────

def _jobs_menu(jobs: "JobRegistry") -> None:
    """List/inspect/stop the background jobs started from the menu."""
    import kancahub
    while True:
        names = list(jobs.jobs)
        print(kancahub.col("bold", "\n  Background jobs:\n"))
        if not names:
            print(kancahub.col("dim", "  (none) — start one with [2] gateway or [4] harvest\n"))
            return
        for i, n in enumerate(names, 1):
            state = kancahub.col("green", "● running") if jobs.alive(n) else kancahub.col("dim", "○ stopped")
            log = jobs.logs.get(n)
            print(f"   [{i}] {n:<10} {state}   log: {log}")
        print("\n   [l N] tail job N's log     [s N] stop job N     [a] stop all     [Enter] back")
        try:
            c = input("  jobs> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if not c:
            return
        if c == "a":
            jobs.stop_all()
            continue
        if c.startswith(("l", "s")) and len(c) > 1:
            try:
                idx = int(c[1:].strip()) - 1
                name = names[idx]
            except (ValueError, IndexError):
                print(kancahub.col("yellow", "  pick a valid number"))
                continue
            if c[0] == "s":
                jobs.stop(name)
            else:
                log = jobs.logs.get(name)
                if log and log.exists():
                    print(kancahub.col("dim", f"  — tail of {log} —"))
                    print("\n".join(log.read_text(errors="replace").splitlines()[-25:]))
                else:
                    print(kancahub.col("dim", "  no log"))


def interactive_mode(p: argparse.ArgumentParser) -> int:
    """Interactive menu loop."""
    import kancahub
    print(kancahub.get_ascii_banner())
    jobs = kancahub.JobRegistry()
    valid_keys = set(_all_keys())

    while True:
        print(kancahub.render_menu())
        running = jobs.running()
        if running:
            print(kancahub.col("green", f"  background jobs: {', '.join(running)}   (choose [j] to manage)"))
        print()

        try:
            choice = input(f" {kancahub.C['bold']}Select [1-{UNIFIED_MENU[-1][0]}, 0, b, j, h, q]: {kancahub.C['reset']}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            jobs.stop_all()
            print("\nExiting.")
            return 0

        if not choice or choice in ("q", "quit", "exit"):
            jobs.stop_all()
            return 0
        if choice == "h":
            p.print_help()
            try:
                input(f" {kancahub.C['bold']}Press Enter to return to menu...{kancahub.C['reset']}")
            except (EOFError, KeyboardInterrupt):
                jobs.stop_all()
                return 0
            print()
            continue
        if choice == "b":
            kancahub.beginner_entry(p)
            continue
        if choice == "j":
            kancahub._jobs_menu(jobs)
            continue
        if choice == "0":
            try:
                kancahub.run_end_to_end_flow(p)
            except Exception as e:
                print(kancahub.col("red", f"Error in end-to-end setup: {e}"))
            print()
            try:
                input(f" {kancahub.C['bold']}Press Enter to return to menu...{kancahub.C['reset']}")
            except (EOFError, KeyboardInterrupt):
                return 0
            print()
            continue

        if choice not in valid_keys:
            print(kancahub.col("yellow", f"  Invalid option. Choose 1-{UNIFIED_MENU[-1][0]}, 0, h, j, or q."))
            continue

        # Dispatch: custom handler, background, farm, or generic
        handler_name = _INTERACTIVE_HANDLERS.get(choice)
        if handler_name:
            handler_fn = globals()[handler_name]
            try:
                handler_fn(p)
            except Exception as e:  # noqa: BLE001
                print(kancahub.col("red", f"Error: {e}"))
            print()
            try:
                input(f" {kancahub.C['bold']}Press Enter to return to menu...{kancahub.C['reset']}")
            except (EOFError, KeyboardInterrupt):
                jobs.stop_all()
                return 0
            print()
            continue

        match = next((item for item in kancahub.UNIFIED_MENU if item[0] == choice), None)
        if not match:
            continue

        _key, _desc, cmd_str, cmd_args = match
        if not cmd_args:
            continue

        # Background items
        if _key in kancahub.MENU_BACKGROUND:
            job = kancahub.MENU_BACKGROUND[_key]
            print(kancahub.col("cyan", f"\n▶ Starting in background: kancahub {cmd_str}\n"))
            jobs.start(job, [sys.executable, str(kancahub.SCRIPTS_DIR / "kancahub.py")] + kancahub.MENU_BACKGROUND_CMD.get(_key, cmd_args))
            print(kancahub.col("dim", "  the menu stays usable — choose [j] to see/stop jobs.\n"))
            continue

        # Farm items: ask for count/pace/egress
        if _key in kancahub.MENU_FARM_KEYS:
            print()
            cmd_args = kancahub._menu_ask_farm_options(_key, list(cmd_args))
            cmd_str = " ".join(cmd_args)

        print(kancahub.col("cyan", f"\n▶ Running: kancahub {cmd_str}\n"))
        try:
            kancahub.dispatch(p, p.parse_args(cmd_args))
        except Exception as e:
            print(kancahub.col("red", f"Error: {e}"))
        print()
        try:
            input(f" {kancahub.C['bold']}Press Enter to return to menu...{kancahub.C['reset']}")
        except (EOFError, KeyboardInterrupt):
            jobs.stop_all()
            return 0
        print()


def beginner_entry(p: argparse.ArgumentParser) -> int:
    """Show the beginner wizard; offer an escape to the advanced menu."""
    import kancahub
    kancahub.sys.path.insert(0, str(kancahub.AUTO_FREECF / "scripts"))
    try:
        from beginner import beginner_menu
    except Exception:
        return kancahub.interactive_mode(p)

    print(kancahub.get_ascii_banner())
    print(kancahub.col("bold", "  New here? Just answer the questions.\n"))
    print(kancahub.col("dim", "  (advanced users: run `kancahub menu` — or just `kancahub` — for the full list)\n"))
    try:
        return beginner_menu()
    except KeyboardInterrupt:
        print(kancahub.col("dim", "\n  Bye!"))
        return 0


def menu_entry(p: argparse.ArgumentParser) -> int:
    """The classic numbered command menu (for advanced users)."""
    import kancahub
    return kancahub.interactive_mode(p)
