"""commands_freeai — free-tier LLM provider farms (radox, beatapi) for kancahub."""
from __future__ import annotations

import json
from pathlib import Path

CFG = Path.home() / ".config" / "auto-freecf"
LEDGERS = {
    "radox": CFG / "radox_accounts.jsonl",
    "beatapi": CFG / "beatapi_accounts.jsonl",
}


def _mask(key: str) -> str:
    if len(key) <= 10:
        return key[:4] + "…"
    return f"{key[:6]}…{key[-4:]}"


def _read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _report(name: str) -> None:
    import kancahub
    recs = _read_ledger(LEDGERS[name])
    if not recs:
        print(f"{name}: no accounts yet")
        return
    keys = [r.get("api_key") for r in recs if r.get("api_key")]
    last = recs[-1]
    status = last.get("status", "?")
    print(f"{name}: {len(recs)} accounts · last status: {status} · api_keys: {len(keys)}")


def cmd_freeai(a) -> int:
    import kancahub
    sub = a.freeai_cmd

    if sub == "status":
        for name in LEDGERS:
            _report(name)
        return 0

    if sub == "keys":
        found = False
        for name, path in LEDGERS.items():
            for rec in _read_ledger(path):
                if rec.get("api_key"):
                    found = True
                    print(f"{name}: {_mask(rec['api_key'])}")
        if not found:
            print("no api_keys captured yet")
        return 0

    # radox / beatapi: run the signup farm in the camoufox venv (browser deps)
    script = kancahub.AUTO_FREECF / "scripts" / f"{sub}_signup.py"
    if not script.exists():
        print(kancahub.col("red", f"✗ script not found: {script}"))
        return 1
    py = kancahub.pick_python(camoufox=True)
    cmd = [py, str(script),
           "--attempts", str(getattr(a, "attempts", 1) or 1),
           "--out-json", str(CFG / f"{sub}_results.json")]
    if getattr(a, "headless", False):
        cmd.append("--headless")
    return kancahub.run(cmd)
