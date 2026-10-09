"""commands_stack — Cloudflare stack signup, login, and management commands for kancahub."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import urllib.request


def cmd_stack(a) -> int:
    import kancahub
    py = kancahub.pick_python()
    pip = kancahub.AUTO_FREECF / "scripts" / "pipeline.py"
    inj = kancahub.AUTO_FREECF / "scripts" / "inject_9router.py"
    signup_main = kancahub.AUTO_FREECF / "signup_from_scratch" / "main.py"
    sub = a.stack_cmd

    if sub == "signup":
        return kancahub._stack_signup(a, py, pip)

    if sub == "login":
        return kancahub._stack_login(a, py)

    if sub == "inject":
        cmd = [py, str(inj)]
        if a.input:
            cmd += ["-i", a.input]
        if a.model:
            cmd += ["--model", a.model]
        if a.db:
            cmd += ["--db", a.db]
        if a.dry_run:
            cmd += ["--dry-run"]
        if not a.no_verify:
            cmd += ["--verify"]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    if sub == "validate":
        if not a.token or not a.account_id:
            print(kancahub.col("red", "✗ validate needs --token and --account-id"))
            return 1
        cmd = [py, str(signup_main), "--validate-only",
               "--token", a.token, "--account-id", a.account_id]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF / "signup_from_scratch")

    if sub == "sync":
        sync = kancahub.AUTO_FREECF / "scripts" / "sync_9router.py"
        cmd = [py, str(sync)]
        if a.db:
            cmd += ["--db", a.db]
        if a.prune:
            cmd += ["--prune"]
        if a.deactivate:
            cmd += ["--deactivate"]
        if a.export_clean:
            cmd += ["--export-clean", a.export_clean]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    if sub == "manage":
        return kancahub._stack_manage(a, py)

    if sub == "web":
        print(kancahub.col("cyan", f"Starting Auto-FreeCF Web UI on :{a.port}"))
        cmd = [py, "web_ui.py", "--port", str(a.port)]
        if a.open:
            cmd += ["--open"]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    if sub == "cookie-import":
        # process_cookies.py usage: process_cookies.py <cookies.json> <account_label>
        # (sys.argv[1]=cookie file, sys.argv[2]=label; writes accounts.json beside itself).
        pc = kancahub.AUTO_FREECF / "process_cookies.py"
        if not pc.exists():
            print(kancahub.col("red", f"✗ process_cookies.py not found at {pc}"))
            return 1
        cookies = Path(a.cookies).expanduser().resolve()  # absolute: subprocess runs in AUTO_FREECF
        if not cookies.exists():
            print(kancahub.col("red", f"✗ cookies file not found: {cookies}"))
            return 1
        return kancahub.run([py, str(pc), str(cookies), a.label], cwd=kancahub.AUTO_FREECF)

    print(kancahub.col("red", "✗ unknown stack command"))
    return 1


def _stack_signup(a, py, pip) -> int:
    import kancahub
    # Optionally bring WARP up for a clean Cloudflare egress, tear it down after.
    warp_was_down = False
    if getattr(a, "warp", False):
        wm = kancahub.AUTO_FREECF / "scripts" / "warp_manager.py"
        st = subprocess.run([py, str(wm), "status"], capture_output=True, text=True)
        if "up" not in st.stdout:
            warp_was_down = True
        print(kancahub.col("cyan", "Bringing up WARP tunnel for clean egress…"))
        if kancahub.run([py, str(wm), "up"]) != 0:
            print(kancahub.col("yellow", "⚠️ WARP up failed — continuing without it"))
        else:
            # Let routing/DNS settle, then warm up the exact hosts the run will
            # use (WARP's first connection on a fresh tunnel is often reset).
            import time as _t
            print(kancahub.col("dim", "Waiting for WARP route to settle…"))
            _t.sleep(8)
            hosts = [
                "https://api.ipify.org",
                "https://dash.cloudflare.com/sign-up",
            ]
            # include the configured mail backend host if available
            try:
                cfg = json.loads((kancahub.AUTO_FREECF / "signup_from_scratch" / "config.json").read_text())
                api = cfg.get("mail_api") or ""
                if api:
                    from urllib.parse import urlparse
                    hosts.append(f"{urlparse(api).scheme}://{urlparse(api).netloc}")
            except Exception:
                pass
            for h in hosts:
                for attempt in range(3):
                    try:
                        req = urllib.request.Request(h, headers={"User-Agent": "Mozilla/5.0"})
                        urllib.request.urlopen(req, timeout=15).read(1)
                        break
                    except Exception:
                        _t.sleep(2)
            print(kancahub.col("green", "✓ egress warmed up"))

    try:
        # Prefer the one-shot pipeline (signup -> verify -> inject) when injecting.
        if not a.no_inject:
            cmd = [py, str(pip), "-n", str(a.accounts), "--output", a.output]
            if a.gateway:
                cmd += ["--gateway", a.gateway]
            if a.proxy:
                cmd += ["--proxy", a.proxy]
            if a.proxy_pool:
                cmd += ["--proxy-pool", a.proxy_pool]
            if a.headless:
                cmd += ["--headless"]
            if a.fast:
                cmd += ["--fast"]
            if a.workers:
                cmd += ["--workers", str(a.workers)]
            if getattr(a, "delay", None) is not None:
                cmd += ["--delay", str(a.delay)]
            if getattr(a, "retry", None) is not None:
                cmd += ["--retry", str(a.retry)]
            return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

        # Signup-only path: call signup main directly (supports --export-txt).
        cmd = [py, "main.py", "--accounts", str(a.accounts), "--output", a.output]
        if a.proxy:
            cmd += ["--proxy", a.proxy]
        if a.proxy_pool:
            cmd += ["--proxy-pool", a.proxy_pool]
        if a.headless:
            cmd += ["--headless"]
        if a.fast:
            cmd += ["--fast"]
        if a.workers:
            cmd += ["--workers", str(a.workers)]
        if a.export_txt:
            cmd += ["--export-txt", a.export_txt]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF / "signup_from_scratch")
    finally:
        if getattr(a, "warp", False) and warp_was_down:
            print(kancahub.col("dim", "Tearing down WARP tunnel…"))
            kancahub.run([py, str(kancahub.AUTO_FREECF / "scripts" / "warp_manager.py"), "down"])


def _stack_login(a, py) -> int:
    """Login to EXISTING Cloudflare accounts (email/password or Google)."""
    import kancahub
    browser_bot = kancahub.AUTO_FREECF / "browser_bot.py"
    if a.bulk:
        cmd = [py, str(browser_bot), "--accounts", a.bulk]
    elif a.account:
        cmd = [py, str(browser_bot), "--single", a.account]
    else:
        print(kancahub.col("red", "✗ provide an account (email:password) or --bulk <file>"))
        return 1
    if a.google:
        cmd += ["--login-method", "google"]
    if a.proxy:
        cmd += ["--proxy", a.proxy]
    if a.visible:
        cmd += ["--visible"]
    return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)


def _stack_manage(a, py) -> int:
    import kancahub
    mgr = kancahub.AUTO_FREECF / "cf_workerai_manager.py"
    cmd = [py, str(mgr)]
    if a.token:
        cmd += ["--token", a.token]
    if a.token_file:
        cmd += ["--token-file", a.token_file]
    if a.model:
        cmd += ["--model", a.model]
    if a.out_json:
        cmd += ["--out-json", a.out_json]
    if a.out_csv:
        cmd += ["--out-csv", a.out_csv]
    if a.no_test:
        cmd += ["--no-test"]
    return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)
