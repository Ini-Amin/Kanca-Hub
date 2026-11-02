#!/usr/bin/env python3
"""scripts/supplies_registry.py — Catalog and manager for 0xgetz AI supplies & harvesters.

Audited from https://codeberg.org/0xgetz?tab=repositories.
Provides listing, metadata inspection, and cloning into ~/Auto-FreeCF-vendor-refs.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

VENDOR_DIR = Path.home() / "Auto-FreeCF-vendor-refs"

SUPPLIES = {
    "virtuachat-9router": {
        "title": "VirtuaChat to 9Router",
        "lang": "Python",
        "url": "https://codeberg.org/0xgetz/virtuachat-9router.git",
        "models": "8 models (Claude 3.5 Sonnet, GPT-4o, Gemini 1.5 Pro, Llama 3.1 70B, etc.)",
        "auth": "Pure HTTP signup (Mail.tm 6-digit OTP) -> vc_session cookie -> SSE bridge",
        "best_feature": "Zero dependencies, pure HTTP registration without browser, direct 9Router SQLite wiring.",
    },
    "iweaver-cli": {
        "title": "iWeaver.ai Bulk Harvester",
        "lang": "JavaScript (Node.js)",
        "url": "https://codeberg.org/0xgetz/iweaver-cli.git",
        "models": "17 models with agentic search & mindmap generation (10 free credits/acct)",
        "auth": "REST signup with disposable mailbox -> JWT + session cookies",
        "best_feature": "Zero npm dependencies, automatic credit balance check, direct 9Router adapter.",
    },
    "socro-bridge": {
        "title": "Socro.io Bulk Bridge",
        "lang": "Go (single static binary)",
        "url": "https://codeberg.org/0xgetz/socro-bridge.git",
        "models": "Frontier models via Socro backend",
        "auth": "Mail.tm API -> Supabase auth token capture -> local bridge :18900",
        "best_feature": "Ultra-lightweight static Go binary (6 MB stripped), zero external dependencies.",
    },
    "yukirouter": {
        "title": "Yuki AI Router",
        "lang": "Go (single static binary)",
        "url": "https://codeberg.org/0xgetz/yukirouter.git",
        "models": "Yuki Coding & Chat models",
        "auth": "Headless OTP verification without browser -> token capture -> 9Router",
        "best_feature": "Zero-browser headless OTP flow, cross-platform static binary.",
    },
    "nottutpool": {
        "title": "Nottut AI Session Pool",
        "lang": "Go",
        "url": "https://codeberg.org/0xgetz/nottutpool.git",
        "models": "Nottut AI reasoning models",
        "auth": "Bulk account provisioning -> keepalive session loop -> 9Router pool",
        "best_feature": "Built-in session keepalive heartbeat preventing idle expiration.",
    },
    "elicit-bulk-9router": {
        "title": "Elicit Research Agent Pool",
        "lang": "JavaScript (Node.js)",
        "url": "https://codeberg.org/0xgetz/elicit-bulk-9router.git",
        "models": "Elicit scientific research & literature synthesis models",
        "auth": "Disposable inbox -> SuperTokens session cookie capture",
        "best_feature": "Exposes research agent outputs over standard OpenAI-compatible API.",
    },
    "cantutor-farm": {
        "title": "CANtutor AI Harvester",
        "lang": "Go",
        "url": "https://codeberg.org/0xgetz/cantutor-farm.git",
        "models": "Educational & coding models",
        "auth": "Two static Go binaries: harvester + bridge straight to 9Router",
        "best_feature": "Clean separation between background harvester and foreground bridge.",
    },
    "mmrouter": {
        "title": "MindMap AI Router",
        "lang": "Go",
        "url": "https://codeberg.org/0xgetz/mmrouter.git",
        "models": "MindMap structured reasoning models",
        "auth": "Bulk provisioning -> session pool -> 9Router",
        "best_feature": "Compact memory footprint, high concurrency goroutine handling.",
    },
    "voila-bulk-9router": {
        "title": "Voila AI to 9Router",
        "lang": "JavaScript (Node.js)",
        "url": "https://codeberg.org/0xgetz/voila-bulk-9router.git",
        "models": "Voila assistant models",
        "auth": "Account creator -> token pool -> 9Router OpenAI endpoint",
        "best_feature": "Pre-configured system prompts and tool schemas.",
    },
    "kaerpool": {
        "title": "Kaer API Pool",
        "lang": "JavaScript (Node.js)",
        "url": "https://codeberg.org/0xgetz/kaerpool.git",
        "models": "Kaer developer API models",
        "auth": "Bulk accounts -> API key generation -> pooled round-robin bridge",
        "best_feature": "Direct API key rotation with rate-limit backoff.",
    },
    "sparkian-bulk-9router": {
        "title": "Sparkian AI to 9Router",
        "lang": "JavaScript (Node.js)",
        "url": "https://codeberg.org/0xgetz/sparkian-bulk-9router.git",
        "models": "Sparkian conversation models",
        "auth": "Token harvester -> 9Router adapter",
        "best_feature": "Compact token store format.",
    },
    "solar-pool-9router": {
        "title": "Upstage Solar Chat to 9Router",
        "lang": "JavaScript (Node.js)",
        "url": "https://codeberg.org/0xgetz/solar-pool-9router.git",
        "models": "Upstage Solar Pro / Solar Mini models",
        "auth": "Web session harvester from solar-chat.upstage.ai -> OpenAI bridge",
        "best_feature": "Free frontier Solar models bridged with 100% fidelity.",
    },
    "arena-auto-chat": {
        "title": "LMSYS / Arena Auto-Chat",
        "lang": "JavaScript (Node.js)",
        "url": "https://codeberg.org/0xgetz/arena-auto-chat.git",
        "models": "Direct battle-tested frontier models",
        "auth": "Disposable inbox + magic-link verification -> direct chat session",
        "best_feature": "Automated magic-link parser without user interaction.",
    },
    "liquid-bulk-creator": {
        "title": "Liquid Console Creator",
        "lang": "JavaScript (Node.js)",
        "url": "https://codeberg.org/0xgetz/liquid-bulk-creator.git",
        "models": "Liquid AI LFM (Liquid Foundation Models)",
        "auth": "Automated console signup -> randomly-named API keys",
        "best_feature": "Generates persistent API keys that do not expire.",
    },
    "abliteration-bulk-creator": {
        "title": "Abliteration.ai Key Creator",
        "lang": "JavaScript / Python",
        "url": "https://codeberg.org/0xgetz/abliteration-bulk-creator.git",
        "models": "Unaligned / abliterated weights (Qwen, Llama uncensored)",
        "auth": "Turnstile solve -> API key generation",
        "best_feature": "Provides completely unaligned weights models without censorship.",
    },
    "orvispool": {
        "title": "Orvis AI Account Factory & Bridge (:8796)",
        "lang": "Go (single static binary)",
        "url": "https://codeberg.org/0xgetz/orvispool.git",
        "models": "16 models (Claude 4 Sonnet/Opus, GPT-4o, o3-mini, Gemini 2.5, Kimi K2.6)",
        "auth": "AWS Cognito headless verification -> token bundle -> local bridge on :8796",
        "best_feature": "100 free credits per account, instant OTP capture, direct OpenAI-compatible bridge.",
    },
    "komafree": {
        "title": "Koma-Free Keyless Onboarding",
        "lang": "Go (single static binary)",
        "url": "https://codeberg.org/0xgetz/komafree.git",
        "models": "koma/apple (vLLM inference)",
        "auth": "Keyless install-id UUID pair (no email, no password, no captcha, no token)",
        "best_feature": "Microsecond local account generation, zero external network dependency for signup.",
    },
    "stepfunrouter": {
        "title": "StepFun Studio Router",
        "lang": "Go",
        "url": "https://codeberg.org/0xgetz/stepfunrouter.git",
        "models": "StepFun flagship reasoning (Step-5 Preview, Step-3.7 Flash)",
        "auth": "Studio web streaming session capture -> 9Router OpenAI provider",
        "best_feature": "Access to free flagship Step-5 reasoning models without paid API keys.",
    },
    "minitoolai-bulk-9router": {
        "title": "MiniToolAI 62-Model Harvester",
        "lang": "JavaScript (Node.js)",
        "url": "https://codeberg.org/0xgetz/minitoolai-bulk-9router.git",
        "models": "62 working free-tier models (GPT, Claude, Gemini, Llama)",
        "auth": "Turnstile solve -> PHP session cookie & utoken capture",
        "best_feature": "Zero dependencies, massive catalogue of 62 models with auto failover.",
    },
    "opensatan-bulk-9router": {
        "title": "OpenSatan Anonymous Uncensored Pool",
        "lang": "JavaScript (Node.js)",
        "url": "https://codeberg.org/0xgetz/opensatan-bulk-9router.git",
        "models": "OpenSatan completely uncensored models (Satan / Genie personas)",
        "auth": "Zero signup, zero email: pure anonymous device-id & localStorage minting",
        "best_feature": "100% anonymous identities created in 4 seconds without browser.",
    },
    "oxalpha-bulk-9router": {
        "title": "Ox Alpha GLM-5.3 Reasoning Pool",
        "lang": "JavaScript (Node.js)",
        "url": "https://codeberg.org/0xgetz/oxalpha-bulk-9router.git",
        "models": "z-ai/glm-5.3-flash (1M context reasoning)",
        "auth": "Anonymous session harvest via CoreWeave/Parasail edge",
        "best_feature": "Free GLM-5.3 reasoning without WAF 3012 blocks.",
    },
    "ptero-bulk-9router": {
        "title": "Ptero.pro Anonymous AI Pool",
        "lang": "JavaScript (Node.js)",
        "url": "https://codeberg.org/0xgetz/ptero-bulk-9router.git",
        "models": "6 models (Ling 3.0 Flash, Claude, GPT-4o variants)",
        "auth": "Local 32-hex guest token generation (zero signup, not IP bound)",
        "best_feature": "Instant guest identity generation without any email or password.",
    },
    "agentscope-bulk-apikey": {
        "title": "AgentScope Platform Key Harvester",
        "lang": "JavaScript (Node.js)",
        "url": "https://codeberg.org/0xgetz/agentscope-bulk-apikey.git",
        "models": "AgentScope Model API keys",
        "auth": "Automated console signup -> Model API key minting",
        "best_feature": "Direct API keys injected into 9Router.",
    },
}


def list_supplies() -> None:
    print("\n=== AI Supplies & Harvester Fleet ===")
    print(f"{'NAME':<24} {'LANG':<12} {'TARGET MODELS & CAPABILITIES'}")
    print("-" * 80)
    for name, info in SUPPLIES.items():
        vendored = "✓ vendored" if (VENDOR_DIR / name).exists() else ""
        print(f"{name:<24} {info['lang']:<12} {info['models'][:42]}")
        print(f"  └─ {info['best_feature']}")
    print()


def show_info(name: str) -> None:
    info = SUPPLIES.get(name)
    if not info:
        sys.exit(f"Supply '{name}' not found. Run 'kancahub supplies list'.")
    print(f"\n=== Supply: {name} ===")
    print(f"  Title:        {info['title']}")
    print(f"  Language:     {info['lang']}")
    print(f"  Models:       {info['models']}")
    print(f"  Auth Flow:    {info['auth']}")
    print(f"  Best Feature: {info['best_feature']}")
    print(f"  Clone URL:    {info['url']}")
    vendored = (VENDOR_DIR / name).exists()
    print(f"  Status:       {'Already vendored in ~/Auto-FreeCF-vendor-refs' if vendored else 'Not yet cloned'}\n")


def clone_supply(name: str) -> None:
    info = SUPPLIES.get(name)
    if not info:
        sys.exit(f"Supply '{name}' not found. Run 'kancahub supplies list'.")
    target = VENDOR_DIR / name
    if target.exists():
        print(f"[!] {name} already exists at {target}")
        return
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[*] Cloning {name} into {target}...")
    subprocess.check_call(["git", "clone", "--depth", "1", info["url"], str(target)])
    print(f"[✓] Successfully cloned {name}")

CATEGORIZED_SUPPLIES = [
    ("Frontier & Multimodal", [
        ("orvispool", "Orvis AI Factory (:8796)", "16 models (Claude 4, GPT-4o, o3-mini, Gemini 2.5)"),
        ("minitoolai-bulk-9router", "MiniToolAI 62-Model Pool", "62 working free models with round-robin"),
        ("virtuachat-9router", "VirtuaChat to 9Router", "8 models (Claude 3.5 Sonnet, GPT-4o, Gemini 1.5 Pro)"),
        ("iweaver-cli", "iWeaver.ai Bulk Harvester", "17 models with agentic search & mindmaps"),
        ("socro-bridge", "Socro.io Bulk Bridge", "Frontier models via Supabase auth"),
        ("solar-pool-9router", "Upstage Solar Chat to 9Router", "Upstage Solar Pro / Solar Mini models"),
        ("arena-auto-chat", "LMSYS / Arena Auto-Chat", "Direct battle-tested frontier models"),
    ]),
    ("Coding & Deep Reasoning", [
        ("komafree", "Koma-Free Keyless Onboarding", "Keyless UUID pair -> koma/apple vLLM"),
        ("stepfunrouter", "StepFun Studio Router", "Step-5 Preview & Step-3.7 Flash free models"),
        ("oxalpha-bulk-9router", "Ox Alpha Reasoning Pool", "GLM-5.3 Flash 1M token reasoning"),
        ("yukirouter", "Yuki AI Router", "Yuki Coding & Chat models"),
        ("nottutpool", "Nottut AI Session Pool", "Nottut AI reasoning models (keepalive pool)"),
        ("mmrouter", "MindMap AI Router", "MindMap structured reasoning models"),
        ("cantutor-farm", "CANtutor AI Harvester", "Educational & coding models"),
        ("voila-bulk-9router", "Voila AI to 9Router", "Voila assistant models with tool schemas"),
    ]),
    ("Uncensored & Unaligned", [
        ("opensatan-bulk-9router", "OpenSatan Anonymous Pool", "Uncensored models with zero signup"),
        ("abliteration-bulk-creator", "Abliteration.ai Key Creator", "Unaligned / uncensored weights (Qwen, Llama)"),
        ("liquid-bulk-creator", "Liquid Console Creator", "Liquid AI LFM (Liquid Foundation Models)"),
        ("ptero-bulk-9router", "Ptero.pro Anonymous Pool", "Anonymous guest token pool"),
    ]),
    ("Developer API Key Harvesters", [
        ("agentscope-bulk-apikey", "AgentScope Key Harvester", "Direct Model API keys for 9Router"),
        ("kaerpool", "Kaer API Pool", "Kaer developer API models with rate-limit rotation"),
        ("sparkian-bulk-9router", "Sparkian AI to 9Router", "Sparkian conversation models"),
        ("elicit-bulk-9router", "Elicit Research Agent Pool", "Elicit scientific research & literature synthesis"),
    ]),
]


def farm_supply(name: str | None = None) -> int:
    """Run per-provider farm. If name is not provided, prompt interactively."""
    flat_map: list[tuple[str, str, str]] = []
    for _cat, items in CATEGORIZED_SUPPLIES:
        flat_map.extend(items)

    if not name:
        print("\n" + "═" * 70)
        print(" " * 17 + "AI Supplies: Per-Provider Farm Fleet")
        print("═" * 70)
        item_idx = 1
        for cat, items in CATEGORIZED_SUPPLIES:
            print(f"\n  [{cat}]")
            for key, title, desc in items:
                vendored = " [vendored]" if (VENDOR_DIR / key).exists() else ""
                print(f"    {item_idx:2d}. {title:<30} ({desc[:34]}...){vendored}")
                item_idx += 1
        print("\n  [ q] Cancel / Back")
        try:
            choice = input(f"\nPick a provider farm to run [1-{len(flat_map)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not choice or choice.lower() == "q":
            return 0
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(flat_map):
                name = flat_map[idx][0]
            else:
                print("Invalid selection.")
                return 1
        except ValueError:
            matched = [k for k, _t, _d in flat_map if choice.lower() in k.lower()]
            if matched:
                name = matched[0]
            else:
                print("Unknown provider.")
                return 1

    info = SUPPLIES.get(name)
    if not info:
        print(f"✗ Supply '{name}' not found. Run 'kancahub supplies list'.")
        return 1

    target = VENDOR_DIR / name
    if not target.exists():
        clone_supply(name)

    print("\n" + "─" * 70)
    print(f"  Target Provider : {info['title']} ({name})")
    print(f"  Models & Scope  : {info['models']}")
    print(f"  Authentication  : {info['auth']}")
    print("─" * 70)

    runner = None
    for cand in ["farm.py", "main.py", "signup.py", "index.py", "run.py"]:
        if (target / cand).is_file():
            runner = [sys.executable, str(target / cand)]
            break
    # Check precompiled binary in bin/ or dedicated CLI entry
    bin_name = f"{name}.exe" if sys.platform == "win32" else name
    bin_cand = target / "bin" / bin_name
    if not bin_cand.is_file() and sys.platform == "win32":
        bin_cand = target / "bin" / name
    node_bin = shutil.which("node") or "node"
    if bin_cand.is_file():
        runner = [str(bin_cand)]
    elif (target / "src" / "cli.js").is_file():
        runner = [node_bin, str(target / "src" / "cli.js")]
    elif (target / "cli.js").is_file():
        runner = [node_bin, str(target / "cli.js")]
    if not runner:
        for cand in ["index.js", "main.js", "app.js", "server.js"]:
            if (target / cand).is_file():
                runner = [node_bin, str(target / cand)]
                break
    if not runner and ((target / "main.go").is_file() or (target / "go.mod").is_file()):
        runner = ["go", "run", "."]

    if not runner:
        print(f"[✓] Cloned and ready in {target}.")
        print(f"    Inspect or run manual script inside {target}.")
        return 0

    print(f"[*] Executing: {' '.join(runner)} in {target}\n")
    try:
        p = subprocess.run(runner, cwd=str(target))
        return p.returncode
    except Exception as exc:
        print(f"✗ Failed to run provider farm: {exc}")
        return 1

def main() -> None:
    ap = argparse.ArgumentParser(description="AI supplies manager")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List all AI supplies & models")

    p_info = sub.add_parser("info", help="Show detailed architecture of a supply")
    p_info.add_argument("name", help="Supply name (e.g. virtuachat-9router)")

    p_clone = sub.add_parser("clone", help="Clone supply into ~/Auto-FreeCF-vendor-refs")
    p_clone.add_argument("name", help="Supply name")

    p_farm = sub.add_parser("farm", help="Run per-provider farm (interactive or specify name)")
    p_farm.add_argument("name", nargs="?", default=None, help="Supply name (optional)")

    args = ap.parse_args()
    if args.cmd == "list":
        list_supplies()
    elif args.cmd == "info":
        show_info(args.name)
    elif args.cmd == "clone":
        clone_supply(args.name)
    elif args.cmd == "farm":
        sys.exit(farm_supply(args.name))


if __name__ == "__main__":
    main()
