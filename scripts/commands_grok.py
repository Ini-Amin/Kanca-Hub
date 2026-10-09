"""commands_grok — Grok farm commands for kancahub."""
from __future__ import annotations

import json
from pathlib import Path


def cmd_grok(a) -> int:
    """Grok farm — grok-register backend (preferred) or PetaniProxy fallback."""
    import kancahub
    py = kancahub.pick_python()
    sub = a.grok_cmd or "run"

    if sub == "run":
        driver = kancahub.AUTO_FREECF / "scripts" / "grok_driver.py"
        mode = getattr(a, "proxy", None) or kancahub.PROXY_AUTO
        if getattr(a, "no_proxy", False):
            mode = kancahub.PROXY_NONE
        choice = kancahub._choose_egress(mode, kancahub.EGRESS_TARGETS["grok"])
        if driver.exists() and not a.petani:
            cmd = [py, str(driver), "-n", str(getattr(a, "accounts", 1))]
            if getattr(a, "headless", False):
                cmd += ["--headless"]
            if getattr(a, "proxy_pool", None):
                cmd += ["--proxy-pool", a.proxy_pool]
            # An explicit hop must win over the child's auto/pool mode.
            if choice.proxy:
                cmd += ["--proxy", choice.proxy]
            if getattr(a, "workers", None):
                cmd += ["--workers", str(a.workers)]
            print(kancahub.col("cyan", "Using grok non-interactive driver (SSO risk gate, relay mail, pool export)"))
            try:
                return kancahub.run_with_mobile_retry(cmd, cwd=kancahub.AUTO_FREECF,
                                                     mobile_rotate=getattr(a, "mobile_rotate", False),
                                                     account=f"grok:{getattr(a, 'accounts', 1)}x")
            finally:
                kancahub._stop_auto_gateways()
        if (kancahub.GROK_REG / "grok_register_ttk.py").exists() and not a.petani:
            cmd = [py, "grok_register_ttk.py", "cli"]
            print(kancahub.col("cyan", "Using grok-register backend (SSO risk gate, 5 mail providers, pool export)"))
            print(kancahub.col("dim", "  (backend takes no --proxy; egress is inherited from the environment)"))
            try:
                return kancahub.run(cmd, cwd=kancahub.GROK_REG, env=kancahub._proxy_env(choice))
            finally:
                kancahub._stop_auto_gateways()
        # fallback to PetaniProxy
        petani = kancahub.PETANI / "main.py"
        cmd = [py, str(petani), "--grok-farm", str(getattr(a, "accounts", 1))]
        if getattr(a, "headless", False):
            cmd += ["--headless"]
        print(kancahub.col("yellow", "Using PetaniProxy grok farm fallback"))
        try:
            return kancahub.run(cmd, cwd=kancahub.PETANI, env=kancahub._proxy_env(choice))
        finally:
            kancahub._stop_auto_gateways()

    if sub == "web":
        if not (kancahub.GROK_REG / "web").exists():
            print(kancahub.col("red", "✗ grok-register web/ not found"))
            return 1
        print(kancahub.col("cyan", "Grok-register WebUI -> http://127.0.0.1:8092"))
        return kancahub.run([py, "-m", "web.server"], cwd=kancahub.GROK_REG)

    if sub == "gui":
        return kancahub.run([py, "grok_register_ttk.py"], cwd=kancahub.GROK_REG)

    if sub == "retry":
        if not a.pending:
            print(kancahub.col("red", "✗ retry needs --pending <file.jsonl>"))
            return 1
        cmd = [py, "grok_register_ttk.py", "retry-pending", a.pending]
        if a.out:
            cmd.append(a.out)
        return kancahub.run(cmd, cwd=kancahub.GROK_REG)

    if sub == "pool":
        # show grok2api local token pool if present
        tk = kancahub.GROK_REG / "token.json"
        if tk.exists():
            data = json.loads(tk.read_text())
            n = len(data.get("ssoBasic", []))
            print(kancahub.col("green", f"grok2api local pool: {n} token(s) -> {tk}"))
        else:
            print(kancahub.col("dim", f"no grok2api pool yet ({tk})"))
        return 0

    if sub == "check":
        # Verify the grok-register backend + mail config without creating anything.
        print(kancahub.col("bold", "\n  Grok / xAI farm — environment check\n"))
        ok = True
        checks = [
            ("grok-register dir", kancahub.GROK_REG.exists()),
            ("grok_register_ttk.py", (kancahub.GROK_REG / "grok_register_ttk.py").exists()),
            ("grok_driver.py", (kancahub.AUTO_FREECF / "scripts" / "grok_driver.py").exists()),
            ("grok_9router.py", (kancahub.AUTO_FREECF / "scripts" / "grok_9router.py").exists()),
            ("token.json pool", (kancahub.GROK_REG / "token.json").exists()),
        ]
        for label, present in checks:
            print(f"  {'✅' if present else '➖'} {label}")
        env = {}
        env_file = Path.home() / ".config" / "auto-freecf" / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()
        for key in ("XAI_API_KEY", "K12_MAIL_KEY", "SUPABASE_URL"):
            print(f"  {'✅' if env.get(key) else '➖'} env {key}")
        print(kancahub.col("dim", "\n  run:  kancahub grok run -n 1   (uses --proxy auto egress)"))
        return 0 if ok else 1

    if sub == "inject":
        inj = kancahub.AUTO_FREECF / "scripts" / "grok_9router.py"
        if not inj.exists():
            print(kancahub.col("red", f"✗ grok_9router.py not found at {inj}"))
            return 1
        choice = kancahub._choose_egress(getattr(a, "proxy", None) or kancahub.PROXY_AUTO,
                                        kancahub.EGRESS_TARGETS["grok"])
        cmd = [py, str(inj)]
        if a.input:
            cmd += ["-i", str(Path(a.input).expanduser())]
        if a.base_url:
            cmd += ["--base-url", a.base_url]
        if a.verify:
            cmd.append("--verify")
        if a.dry_run:
            cmd.append("--dry-run")
        # grok_9router.py has no --proxy flag; inherit the hop via the environment.
        try:
            return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF, env=kancahub._proxy_env(choice))
        finally:
            kancahub._stop_auto_gateways()

    print(kancahub.col("red", "✗ unknown grok command"))
    return 1
