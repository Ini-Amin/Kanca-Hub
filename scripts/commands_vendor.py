"""commands_vendor — passthroughs to vendored third-party tools in ~/Auto-FreeCF-vendor-refs."""
from __future__ import annotations

import json
import os
import sys
import time
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
    mode = getattr(a, "mode", "run") or "run"
    if mode == "mint":
        # Native Camoufox mint (free Turnstile solve + our mailbox); vendor's
        # Chromium fails the Turnstile on every egress we have.
        tool = kancahub.AUTO_FREECF / "scripts" / "abliteration_mint.py"
        if not tool.exists():
            print(kancahub.col("red", f"✗ abliteration_mint.py not found at {tool}"))
            return 1
        py = kancahub.pick_python(camoufox=True)
        return kancahub.run([py, str(tool)] + _extra(a))
    root = VENDOR / "abliteration-bulk-creator"
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


def cmd_autoclaw(a) -> int:
    """AutoClaw 2.0.3 (Zhipu zwork): referral fission, phone signup, and OAuth."""
    import kancahub
    import autoclaw_client as ac

    sub = getattr(a, "autoclaw_cmd", None)
    if not sub:
        if sys.stdin.isatty():
            print(kancahub.col("cyan", "\n╔══════════════════════════════════════════════════════════════╗"))
            print(kancahub.col("cyan", "║  AutoClaw 2.0.3 — Zhipu zwork (+800 Fission Referral Bonus)  ║"))
            print(kancahub.col("cyan", "╚══════════════════════════════════════════════════════════════╝"))
            default_inv = ac.get_default_invite_code()
            inv_note = f" [cached: {default_inv}]" if default_inv else ""
            print(f"  [1] 1-Click z.ai OAuth Claim (+800 tokens){inv_note}")
            print("  [2] Check Fission Campaign Status & Limits")
            print("  [3] View Wallet Balances & Credit Grants")
            print("  [4] List Saved AutoClaw Accounts")
            print("  [5] Bind Referral Code Manually")
            print("  [6] View Aliyun Captcha Configuration")
            print("  [q] Exit")
            try:
                choice = input(f"\n {kancahub.C['bold']}Select an option [1]: {kancahub.C['reset']}").strip() or "1"
            except (EOFError, KeyboardInterrupt):
                return 0
            if choice.lower() in ("q", "quit", "exit"):
                return 0
            menu_map = {
                "1": "claim",
                "2": "status",
                "3": "wallets",
                "4": "accounts",
                "5": "bind",
                "6": "captcha",
            }
            sub = menu_map.get(choice, "claim")
        else:
            sub = "status"
    client = ac.AutoClawClient()


    if sub == "status":
        print(f"\n{kancahub.col('cyan', 'AutoClaw 2.0.3 Status')} (Zhipu zwork):")
        try:
            res = client.activity_status()
            if res.get("code") == 0:
                acts = res.get("data", {}).get("activities", [])
                if acts:
                    act = acts[0]
                    print(f"  • Fission Activity : {act.get('activity_id')} ({kancahub.col('green', act.get('status', 'active'))})")
                    print(f"  • Inviter Reward   : {act.get('bind_inviter_reward_amount')} tokens/invite (max {act.get('bind_inviter_reward_limit')} invites)")
                    print(f"  • Inviter Limits   : 5 invites = 4,000 tokens max")
                    ends = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(act.get('ends_at', 0))) if act.get('ends_at') else 'N/A'
                    print(f"  • Campaign Ends    : {ends} ({act.get('metadata', {}).get('timezone', 'Asia/Shanghai')})")
                else:
                    print("  • Fission Activity : None ongoing")
            else:
                print(kancahub.col("yellow", f"  ⚠ Activity status returned code {res.get('code')}: {res.get('msg')}"))
        except Exception as exc:
            print(kancahub.col("red", f"  ✗ Could not fetch activity status: {exc}"))

        try:
            cap = client.captcha_config()
            if cap.get("code") == 0:
                d = cap.get("data", {})
                print(f"  • Captcha Gate     : {d.get('captcha_supplier')} (region: {d.get('region')}, prefix: {d.get('prefix')}, scene: {d.get('scene_id')})")
        except Exception as exc:
            print(kancahub.col("red", f"  ✗ Could not fetch captcha config: {exc}"))

        accs = ac.load_accounts()
        print(f"  • Saved Accounts   : {len(accs)} account(s) in {ac.ACCOUNTS_PATH.name}")
        return 0

    if sub == "captcha":
        try:
            cap = client.captcha_config()
            print(json.dumps(cap, indent=2))
            return 0 if cap.get("code") == 0 else 1
        except Exception as exc:
            print(kancahub.col("red", f"✗ Captcha config error: {exc}"))
            return 1

    if sub == "claim":
        bind_code = getattr(a, "bind", None) or ac.get_default_invite_code()
        if not bind_code:
            try:
                prompt_msg = f"{kancahub.C['bold']}Enter inviter referral code (+800 tokens, or press Enter to skip): {kancahub.C['reset']}"
                raw = input(prompt_msg).strip()
                if raw.lower() in ("q", "quit", "exit", "cancel"):
                    return 0
                bind_code = raw
            except (EOFError, KeyboardInterrupt):
                return 0
        elif isinstance(bind_code, str) and bind_code.lower() in ("q", "quit", "exit"):
            bind_code = ""
        if bind_code:
            ac.set_default_invite_code(bind_code)

        port = getattr(a, "port", 18432) or 18432
        nav = getattr(a, "navigate_uri", None) or f"http://localhost:{port}/auth/callback-zai"
        verify_param = getattr(a, "verify_param", None)

        print(kancahub.col("cyan", "\n── AutoClaw z.ai One-Click OAuth Claim ──"))
        if bind_code:
            print(f"  • Inviter Code : {kancahub.col('green', bind_code)} (+800 tokens)")
        else:
            print(f"  • Inviter Code : {kancahub.col('dim', '(none — login only)')}")
        print(f"  • Callback URL : {nav}")
        try:
            if verify_param:
                try:
                    url_res = client.get_oauth_url(verify_param, navigate_uri=nav, provider="zai")
                    if url_res.get("code") == 0 and url_res.get("data"):
                        oauth_url = url_res["data"]["oauth_url"]
                        print(kancahub.col("cyan", "\n▶ Opening z.ai OAuth URL in browser:"))
                        print(f"  {oauth_url}\n")
                        import webbrowser
                        webbrowser.open(oauth_url)
                except Exception as exc:
                    print(kancahub.col("yellow", f"  ⚠ Note: {exc}"))

                print(kancahub.col("bold", "\n▶ Waiting for z.ai OAuth login in browser (port 18432)..."))
                cb = client.listen_oauth_callback(port=port, path="/auth/callback-zai")
                code = cb.get("code")
                state = cb.get("state")
                if not (code and state):
                    print(kancahub.col("red", f"✗ OAuth callback failed: {cb.get('error', 'timed out or cancelled')}"))
                    return 1
                res = client.oauth_login(code, state, navigate_uri=nav, provider="zai", bind_code=bind_code)
            else:
                print(kancahub.col("cyan", f"\n▶ Launching interactive OAuth claim helper on http://localhost:{port}/"))
                print(kancahub.col("dim", "  (Complete the slider in your browser; it will auto-redirect to z.ai authorization)"))
                res = client.interactive_claim(bind_code=bind_code, port=port, navigate_uri=nav, provider="zai")

            if res.get("code") == 0 and res.get("data"):
                d = res["data"]
                name = d.get("user_name") or "User"
                uid = d.get("user_id")
                print(kancahub.col("green", f"\n✓ Logged in via z.ai! User: {name} (ID: {uid})"))
                print(f"  Account Saved: {ac.ACCOUNTS_PATH}")
                br = res.get("bind_result", {})
                if br.get("code") == 0:
                    print(kancahub.col("green", f"  ✓ Referral code '{bind_code}' bound successfully! (+800 inviter tokens)"))
                else:
                    print(kancahub.col("yellow", f"  ⚠ Referral bind result: {br.get('msg') or br}"))

                tok = d.get("access_token")
                if tok:
                    try:
                        w = client.get_wallets(token=tok)
                        if w.get("code") == 0 and w.get("data"):
                            balances = w["data"].get("balances", {})
                            print(kancahub.col("cyan", f"  • Active Wallet Balance: {json.dumps(balances)}"))
                    except Exception:
                        pass
                return 0
            else:
                print(kancahub.col("red", f"✗ Claim failed ({res.get('code')}): {res.get('msg')}"))
                return 1
        except Exception as exc:
            print(kancahub.col("red", f"✗ Error during OAuth claim: {exc}"))
            return 1
    if sub == "login":
        code = getattr(a, "code", None)
        state = getattr(a, "state", None)
        provider = getattr(a, "provider", "zai") or "zai"
        bind_code = getattr(a, "bind", None)
        port = getattr(a, "port", 18432) or 18432
        nav = getattr(a, "navigate_uri", None) or f"http://localhost:{port}/auth/callback-{provider}"
        listen = getattr(a, "listen", False)
        verify_param = getattr(a, "verify_param", None)

        # Flow 1: Full interactive with Aliyun captcha verify proof
        if verify_param:
            try:
                url_res = client.get_oauth_url(verify_param, navigate_uri=nav, provider=provider)
                if url_res.get("code") != 0 or not url_res.get("data"):
                    print(kancahub.col("red", f"✗ Failed to get OAuth URL ({url_res.get('code')}): {url_res.get('msg')}"))
                    return 1
                oauth_url = url_res["data"]["oauth_url"]
                print(kancahub.col("cyan", f"\n▶ Opening {provider.upper()} OAuth URL in browser:"))
                print(f"  {oauth_url}\n")
                import webbrowser
                webbrowser.open(oauth_url)
                print(kancahub.col("dim", f"Waiting for OAuth redirect on {nav} (timeout: 120s)..."))
                cb = client.listen_oauth_callback(port=port, path=f"/auth/callback-{provider}")
                code = cb.get("code")
                state = cb.get("state")
                if not (code and state):
                    print(kancahub.col("red", f"✗ OAuth callback failed: {cb.get('error', 'no code/state')}"))
                    return 1
            except Exception as exc:
                print(kancahub.col("red", f"✗ OAuth flow error: {exc}"))
                return 1

        # Flow 2: Listen only (for user completing OAuth in browser or desktop app)
        elif listen and not (code and state):
            print(kancahub.col("cyan", f"\n▶ Listening for {provider.upper()} OAuth callback on {nav}..."))
            print(kancahub.col("dim", "Complete the login in your browser; this will capture the redirect automatically."))
            try:
                cb = client.listen_oauth_callback(port=port, path=f"/auth/callback-{provider}")
                code = cb.get("code")
                state = cb.get("state")
                if not (code and state):
                    print(kancahub.col("red", f"✗ Callback error: {cb.get('error', 'timed out or cancelled')}"))
                    return 1
                print(kancahub.col("green", "✓ Callback captured! Exchanging authorization code..."))
            except Exception as exc:
                print(kancahub.col("red", f"✗ Error listening for callback: {exc}"))
                return 1

        # Flow 3: Code and state provided
        if code and state:
            try:
                res = client.oauth_login(code, state, navigate_uri=nav, provider=provider, bind_code=bind_code)
                if res.get("code") == 0 and res.get("data"):
                    d = res["data"]
                    name = d.get("user_name") or "User"
                    uid = d.get("user_id")
                    print(kancahub.col("green", f"\n✓ Successfully logged in via {provider.upper()}! User: {name} (ID: {uid})"))
                    print(f"  Access Token : {d.get('access_token', '')[:32]}...")
                    print(f"  Account Saved: {ac.ACCOUNTS_PATH}")
                    if bind_code:
                        br = res.get("bind_result", {})
                        if br.get("code") == 0:
                            print(kancahub.col("green", f"  ✓ Referral code '{bind_code}' bound successfully! (+800 inviter reward)"))
                        else:
                            print(kancahub.col("yellow", f"  ⚠ Bind result: {br.get('msg') or br}"))
                    return 0
                else:
                    print(kancahub.col("red", f"✗ OAuth login failed ({res.get('code')}): {res.get('msg')}"))
                    return 1
            except Exception as exc:
                print(kancahub.col("red", f"✗ OAuth login error: {exc}"))
                return 1

        # No flags: same 1-click interactive flow as claim (captcha page + redirect + bind).
        print(kancahub.col("cyan", "\n── AutoClaw z.ai OAuth Login ──"))
        if bind_code:
            print(f"  • Inviter Code : {kancahub.col('green', bind_code)} (+800 tokens)")
        print(kancahub.col("dim", "  (No flags given — starting the 1-click interactive flow)"))
        try:
            res = client.interactive_claim(bind_code=bind_code, port=port, navigate_uri=nav, provider=provider)
            if res.get("code") == 0 and res.get("data"):
                d = res["data"]
                name = d.get("user_name") or "User"
                print(kancahub.col("green", f"\n✓ Logged in via {provider.upper()}! User: {name} (ID: {d.get('user_id')})"))
                print(f"  Account Saved: {ac.ACCOUNTS_PATH}")
                br = res.get("bind_result", {})
                if bind_code:
                    if br.get("code") == 0:
                        print(kancahub.col("green", f"  ✓ Referral code '{bind_code}' bound successfully! (+800 inviter reward)"))
                    else:
                        print(kancahub.col("yellow", f"  ⚠ Bind result: {br.get('msg') or br}"))
                return 0
            print(kancahub.col("red", f"✗ Login failed ({res.get('code')}): {res.get('msg')}"))
            print(kancahub.col("dim", "  Advanced: kancahub autoclaw login --listen | --verify-param <proof> | --code <c> --state <s>"))
            return 1
        except Exception as exc:
            print(kancahub.col("red", f"✗ OAuth login error: {exc}"))
            return 1

    if sub == "oauth-url":
        provider = getattr(a, "provider", "zai") or "zai"
        verify_param = getattr(a, "verify_param", None)
        port = getattr(a, "port", 18432) or 18432
        nav = getattr(a, "navigate_uri", None) or f"http://localhost:{port}/auth/callback-{provider}"
        if not verify_param:
            print(kancahub.col("red", "✗ oauth-url requires --verify-param (from Aliyun captcha)"))
            return 1
        try:
            res = client.get_oauth_url(verify_param, navigate_uri=nav, provider=provider)
            if res.get("code") == 0 and res.get("data"):
                url = res["data"].get("oauth_url")
                print(f"OAuth URL ({provider}):\n{url}")
                return 0
            else:
                print(kancahub.col("red", f"✗ oauth-url failed ({res.get('code')}): {res.get('msg')}"))
                return 1
        except Exception as exc:
            print(kancahub.col("red", f"✗ Error getting oauth url: {exc}"))
            return 1
    if sub == "bind":
        invite_code = getattr(a, "invite_code", None)
        if not invite_code:
            try:
                raw = input(f" {kancahub.C['bold']}Enter inviter referral code to bind (or q to cancel): {kancahub.C['reset']}").strip()
            except (EOFError, KeyboardInterrupt):
                return 0
            if not raw or raw.lower() in ("q", "quit", "exit", "cancel"):
                return 0
            invite_code = raw
        token = getattr(a, "token", None)
        try:
            res = client.bind_invite_code(invite_code, token=token)
            if res.get("code") == 0:
                print(kancahub.col("green", f"✓ Successfully bound invite code '{invite_code}'! (+800 inviter reward)"))
                return 0
            else:
                print(kancahub.col("yellow", f"⚠ Bind response ({res.get('code')}): {res.get('msg')}"))
                return 0 if res.get("code") in (410000, 400000) else 1
        except Exception as exc:
            print(kancahub.col("red", f"✗ Error binding invite code: {exc}"))
            return 1

    if sub == "wallets":
        token = getattr(a, "token", None)
        try:
            res = client.get_wallets(token=token)
            if res.get("code") == 0 and res.get("data"):
                d = res["data"]
                print(f"\n{kancahub.col('cyan', 'AutoClaw Wallet Balances')}:")
                balances = d.get("balances", {})
                if balances:
                    for scope, amt in balances.items():
                        print(f"  • {scope:12} : {kancahub.col('green', str(amt))} tokens")
                else:
                    print(json.dumps(d, indent=2))
                return 0
            else:
                print(json.dumps(res, indent=2))
                return 0 if res.get("code") == 0 else 1
        except ValueError as exc:
            print(kancahub.col("yellow", f"⚠ {exc}"))
            print(kancahub.col("dim", "  Run 'kancahub autoclaw claim' first to log in with z.ai."))
            return 0
        except Exception as exc:
            print(kancahub.col("red", f"✗ Error fetching wallets: {exc}"))
            return 1

    if sub == "accounts":
        accs = ac.load_accounts()
        if not accs:
            print(kancahub.col("yellow", f"\nNo saved accounts in {ac.ACCOUNTS_PATH.name}"))
            print(kancahub.col("dim", "  Tip: run 'kancahub autoclaw claim' to log in via z.ai and get +800 tokens."))
            return 0
        print(f"\n{kancahub.col('cyan', 'Saved AutoClaw Accounts')} ({len(accs)}):")
        print("  " + "─" * 60)
        for i, acc in enumerate(accs, 1):
            name = acc.get("user_name") or acc.get("phone") or "unknown"
            uid = acc.get("user_id", "")
            auth = acc.get("auth_type", "oauth")
            created = acc.get("created_at", "")
            print(f"  [{i}] {name:16} (ID: {uid:12}) [{auth}] {created}")
        print("  " + "─" * 60)
        return 0

    print(kancahub.col("red", f"✗ unknown autoclaw subcommand {sub!r}"))
    return 1