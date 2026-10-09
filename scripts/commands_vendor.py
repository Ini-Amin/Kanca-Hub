"""commands_vendor — passthroughs to vendored third-party tools in ~/Auto-FreeCF-vendor-refs."""
from __future__ import annotations

import os
from pathlib import Path

VENDOR = Path.home() / "Auto-FreeCF-vendor-refs"

_ZCODE_SCRIPTS = {"claim": "src/zcode_claim.py", "console": "src/console_bridge.py",
                  "connect": "src/connect_9router.py"}
_ABC_SCRIPTS = {"run": "src/index.js", "test": "tests/selftest.js",
                "check-proxies": "tests/check-proxies.js"}


def _extra(a) -> list[str]:
    ex = [x for x in (getattr(a, "extra", None) or [])]
    return ex[1:] if ex and ex[0] == "--" else ex


def cmd_zcode(a) -> int:
    """zcode-claim-9router: z.ai token -> ZCode JWT -> claim -> 9Router glm pool."""
    import kancahub
    root = VENDOR / "zcode-claim-9router"
    mode = getattr(a, "mode", "claim") or "claim"
    script = root / _ZCODE_SCRIPTS.get(mode, "")
    if not script or not script.exists():
        print(kancahub.col("red", f"✗ zcode {mode}: {script} not found"))
        print(kancahub.col("dim", f"  vendor: {root}"))
        return 1
    # playwright + websockets live only in the camoufox venv.
    py = kancahub.pick_python(camoufox=True)
    return kancahub.run([py, str(script)] + _extra(a), cwd=root)


def cmd_abliteration(a) -> int:
    """abliteration-bulk-creator: bulk abliteration.ai accounts, each with an API key."""
    import kancahub
    root = VENDOR / "abliteration-bulk-creator"
    mode = getattr(a, "mode", "run") or "run"
    entry = root / _ABC_SCRIPTS.get(mode, "")
    if not entry or not entry.exists():
        print(kancahub.col("red", f"✗ abliteration {mode}: {entry} not found"))
        print(kancahub.col("dim", f"  vendor: {root}"))
        return 1
    node = Path.home() / ".local" / "share" / "node24" / "bin" / "node"
    if not node.exists():
        import shutil
        found = shutil.which("node")
        node = Path(found) if found else None
    if not node:
        print(kancahub.col("red", "✗ node not found (need Node 18+)"))
        return 1
    if mode == "run" and not (root / "node_modules").exists():
        print(kancahub.col("yellow", f"⚠ deps missing — run once: cd {root} && npm install"))
    # --cdp: drive the signup through a cloud/remote browser (TinyFish, Bright Data)
    # instead of a local Chromium, so a clean residential egress does the turnstile.
    env = os.environ.copy()
    cdp = getattr(a, "cdp", None) or (getattr(a, "extra_env", None) or {}).get("ABC_CDP")
    if cdp:
        env["ABC_CDP"] = cdp
    return kancahub.run([str(node), str(entry)] + _extra(a), cwd=root, env=env)