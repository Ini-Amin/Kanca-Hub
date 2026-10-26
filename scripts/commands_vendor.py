"""commands_vendor — passthroughs to vendored third-party tools in ~/Auto-FreeCF-vendor-refs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
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

    # Auto-wire an egress the way TokenHarbor does, for the same reason.
    #
    # MEASURED FAILURE (blu, 2026-10-22): `kancahub abliteration run` printed
    #   [abc] proxy: disabled (direct connection) — signup will likely be blocked
    #   [abc] inbox: ...@gmail.com (emailmux)
    # and then stopped. The vendor CLI was telling us the truth: abliteration.ai
    # is behind Cloudflare Turnstile, and a direct egress is rejected, so the run
    # dies right after the mailbox step. Nothing was misconfigured in the tool --
    # it was simply never given a usable exit.
    #
    # THK solved this by choosing an egress and exporting it; do the same here so
    # the same flags (--proxy/--no-proxy/--country) mean the same thing in both
    # farm commands. An explicit --cdp still wins: a remote browser carries its
    # own egress, and proxying the local node process would be pointless.
    choice = None
    if mode in ("run", "mint") and not cdp:
        try:
            mode_p = getattr(a, "proxy", None) or kancahub.PROXY_AUTO
            if getattr(a, "no_proxy", False):
                mode_p = kancahub.PROXY_NONE
            policy = kancahub.EGRESS_COUNTRY.get("abliteration", {})
            country = getattr(a, "country", None)
            allow = {country.strip().upper()} if country else (set(policy.get("allow") or []) or None)
            exclude = None if country else (set(policy.get("exclude") or []) or None)
            choice = kancahub._choose_egress(
                mode_p,
                kancahub.EGRESS_TARGETS["abliteration"],
                country=allow,
                exclude_countries=exclude,
            )
            env.update(kancahub._proxy_env(choice) or {})
            if getattr(choice, "direct", False):
                print(kancahub.col("yellow",
                      "  [proxy] direct connection — Cloudflare will likely block the "
                      "signup; pass --proxy auto or --cdp <browser> to give it an exit"))
            else:
                print(kancahub.col("dim", "  [proxy] %s" % (getattr(choice, "label", "acquired"))))
        except Exception as exc:  # noqa: BLE001 - egress is best-effort, never fatal
            print(kancahub.col("yellow", "  [proxy] auto-wire failed: %s" % str(exc)[:100]))

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


# notrack.ai pooling adapter. 8784 is the vendor default; nothing else on blu
# uses it (8787 zcode, 8789 gemini, 8790 grokbot-shim, 8791 grokbot-gateway).
NOTRACK_PORT = 8784


def cmd_notrack(a) -> int:
    """notrack-bulk-9router: create notrack.ai sessions and serve them as one endpoint.

    Cheapest farm measured 2026-10-19: 1 account in 5s, no email, no CAPTCHA,
    works from a datacenter IP (unlike THK/hark/agentscope). The adapter pools the
    sessions and round-robins them, so 9Router needs ONE connection pointing at it.
    Measured caveat: completions are flaky at speed (200/502 alternating) --
    callers must retry.
    """
    import kancahub
    root = VENDOR / "notrack-bulk-9router"
    mode = getattr(a, "mode", "list") or "list"
    if not root.exists():
        print(kancahub.col("red", f"✗ notrack vendor missing at {root}"))
        return 1
    if mode == "adapter":
        script = root / "src" / "adapter.js"
    else:
        script = root / "src" / "cli.js"
    if not script.exists():
        print(kancahub.col("red", f"✗ {script} not found"))
        return 1
    extra = _extra(a)
    if mode == "adapter":
        cmd = ["node", str(script)]
    else:
        cmd = ["node", str(script), mode] + extra
    env = {"PORT": str(NOTRACK_PORT)} if mode in ("adapter", "run") else None
    if mode in ("adapter", "run"):
        print(kancahub.col("dim", f"  adapter base: http://127.0.0.1:{NOTRACK_PORT}/v1"))
    return kancahub.run(cmd, cwd=root, env=env)

def cmd_opencode(a) -> int:
    """OpenCode CLI: free Zen models as workers, plus local provider wiring.

    WHY THIS EXISTS
    ---------------
    OpenCode's free tier cannot be used as an OpenAI-compatible endpoint: the
    gateway answers 403 FreeTierError to any request the OpenCode client did not
    send, and that is a request-SHAPE check, so proxy rotation cannot clear it
    (decolua/9router#4997; #4998 only makes 429/5xx retryable across pools).
    Measured on this box 2026-10-22: a direct POST returns 403 in 0.65s, while
    `opencode run -m <free model>` returns 200 with a real tool call. The CLI is
    therefore the only supported path, which is what `zen` wraps.

    QUOTA, ALSO MEASURED 2026-10-22 (do not repeat the old wrong claim)
    -------------------------------------------------------------------
    Once the anonymous quota is spent the CLI answers 429 FreeUsageLimitError
    with `retry-after` around 1000-22000s. That 429 is NOT per-IP: a fresh
    Windscribe exit (135.136.3.111) hit it on its very first request, and so did
    a second exit, and so did two different working directories (distinct
    x-opencode-project hashes). It also ignores the model. What the key actually
    is remains unknown; the remaining candidates are the project hash combined
    with a per-install counter, or an anonymous identity derived from the
    install. Do not build an IP-rotation farm on this until that is settled.
    """
    import kancahub

    sub = getattr(a, "oc_cmd", None) or "status"

    if sub == "status":
        binary = shutil.which("opencode") or os.environ.get("OPENCODE_BIN", "")
        if not binary or not Path(binary).is_file():
            print(kancahub.col("red", "✗ opencode CLI not found"))
            print(kancahub.col("dim", "  install: curl -fsSL https://opencode.ai/install | bash"))
            return 1
        print(kancahub.col("cyan", f"opencode: {binary}"))
        try:
            out = subprocess.run([binary, "--version"], capture_output=True,
                                 text=True, timeout=30)
            print("  version:", (out.stdout or "").strip() or "?")
        except Exception as exc:  # noqa: BLE001
            print("  version: unknown (%s)" % exc)
        cfg = Path.home() / ".config" / "opencode" / "opencode.json"
        if cfg.is_file():
            import json as _json
            d = _json.loads(cfg.read_text())
            provs = list((d.get("provider") or {}).keys())
            print("  providers:", ", ".join(provs[:8]) or "-")
            print("  model:", d.get("model", "-"))
            print("  small_model:", d.get("small_model", "-"))
            ag = d.get("agent") or {}
            for name, spec in ag.items():
                if isinstance(spec, dict):
                    print(f"  agent {name}: {spec.get('model', '-')}")
        return 0

    if sub == "models":
        binary = shutil.which("opencode") or os.environ.get("OPENCODE_BIN", "")
        if not binary or not Path(binary).is_file():
            print(kancahub.col("red", "✗ opencode CLI not found"))
            return 1
        out = subprocess.run([binary, "models"], capture_output=True, text=True, timeout=120)
        lines = [l for l in (out.stdout or "").splitlines() if l.strip()]
        if getattr(a, "free_only", False):
            lines = [l for l in lines if "free" in l.lower()]
        for l in lines:
            print(l)
        print(kancahub.col("dim", f"\n{len(lines)} model(s)"))
        return 0

    if sub == "run":
        binary = shutil.which("opencode") or os.environ.get("OPENCODE_BIN", "")
        if not binary or not Path(binary).is_file():
            print(kancahub.col("red", "✗ opencode CLI not found"))
            return 1
        model = getattr(a, "model", None) or "opencode/glm-5.3-flash-free"
        prompt = getattr(a, "prompt", None)
        if not prompt:
            print(kancahub.col("red", "✗ --prompt is required for `opencode run`"))
            return 1
        # Same egress auto-wire as `thk` and `abliteration`, but measured
        # 2026-10-22: rotating the exit does NOT clear the free-tier 429.
        # Windscribe US-East (135.136.3.111) and US-West, both fresh exits,
        # returned FreeUsageLimitError on the first request, and two different
        # working directories (different x-opencode-project hashes) behaved the
        # same. The 403 FreeTierError is a request-shape check; the 429 is keyed
        # to something machine-scoped that survives IP and project changes.
        # Keeping the egress wiring because a 405/timeout from a bad relay is
        # still worth avoiding, not because rotation buys quota.
        env = None
        try:
            mode_p = getattr(a, "proxy", None) or kancahub.PROXY_AUTO
            if getattr(a, "no_proxy", False):
                mode_p = kancahub.PROXY_NONE
            policy = kancahub.EGRESS_COUNTRY.get("opencode", {})
            country = getattr(a, "country", None)
            allow = {country.strip().upper()} if country else (set(policy.get("allow") or []) or None)
            exclude = None if country else (set(policy.get("exclude") or []) or None)
            choice = kancahub._choose_egress(mode_p, kancahub.EGRESS_TARGETS["opencode"],
                                             country=allow, exclude_countries=exclude)
            env = dict(kancahub._proxy_env(choice) or {})
            if getattr(choice, "direct", False):
                print(kancahub.col("dim", "  [proxy] direct connection"))
            else:
                print(kancahub.col("dim", "  [proxy] %s" % (getattr(choice, "source", "acquired"))))
        except Exception as exc:  # noqa: BLE001 - best effort
            print(kancahub.col("yellow", "  [proxy] auto-wire failed: %s" % str(exc)[:90]))
            env = None
        # Run in an empty temp dir without --auto: a worker gets one prompt and
        # no filesystem to wander, so a prompt-injected "read /root/.ssh" has
        # nothing to reach.
        import tempfile
        with tempfile.TemporaryDirectory(prefix="oc-worker-") as tmp:
            print(kancahub.col("dim", f"  model: {model}  cwd: {tmp}"))
            return kancahub.run([binary, "run", "-m", model, prompt], cwd=tmp, env=env)

    if sub == "zen":
        # Delegate to the shared worker used by the bounty pipeline, so there is
        # one implementation of "one prompt in, one answer out" instead of two.
        tool = kancahub.AUTO_FREECF / "scripts" / "zen_worker.py"
        if not tool.exists():
            print(kancahub.col("red", f"✗ zen_worker.py not found at {tool}"))
            return 1
        py = kancahub.pick_python()
        job = getattr(a, "job", None) or "ping"
        return kancahub.run([py, str(tool), job])

    if sub == "captcha":
        tool = kancahub.AUTO_FREECF / "scripts" / "hcaptcha_dataset.py"
        if not tool.exists():
            print(kancahub.col("red", f"✗ hcaptcha_dataset.py not found at {tool}"))
            return 1
        py = kancahub.pick_python(camoufox=True)
        argv = [py, str(tool), "capture"]
        if getattr(a, "continuous", False):
            argv.append("--continuous")
        if getattr(a, "keep_open", None):
            argv += ["--keep-open", str(a.keep_open)]
        return kancahub.run(argv)

    if sub == "farm":
        tool = kancahub.AUTO_FREECF / "scripts" / "opencode_farm.py"
        if not tool.exists():
            print(kancahub.col("red", f"✗ opencode_farm.py not found at {tool}"))
            return 1
        py = kancahub.pick_python(camoufox=True)
        argv = [py, str(tool), "run"]
        if getattr(a, "headed", False):
            argv.append("--headed")
        if getattr(a, "count", None):
            argv += ["--count", str(a.count)]
        return kancahub.run(argv)

    if sub == "login":
        tool = kancahub.AUTO_FREECF / "scripts" / "opencode_auth.py"
        if not tool.exists():
            print(kancahub.col("red", f"✗ opencode_auth.py not found at {tool}"))
            return 1
        py = kancahub.pick_python()
        return kancahub.run([py, str(tool), "device"])

    if sub == "bridge":
        tool = kancahub.AUTO_FREECF / "scripts" / "opencode_bridge.py"
        if not tool.exists():
            print(kancahub.col("red", f"✗ opencode_bridge.py not found at {tool}"))
            return 1
        py = kancahub.pick_python()
        port = getattr(a, "port", None) or 8791
        return kancahub.run([py, str(tool), "serve", "--port", str(port)])

    if sub in ("signup", "key", "inject"):
        # The account flow the operator actually uses:
        #   https://opencode.ai/console/signup  -> create account
        #   https://opencode.ai/console/account -> name an API key, copy it
        #   then paste it into 9router as an opencode provider connection.
        if sub == "signup":
            url = "https://opencode.ai/console/signup"
        elif sub == "key":
            url = "https://opencode.ai/console/account"
        else:
            # Batch injection: delegate to scripts/opencode_inject.py so the
            # farm path and the one-key path share a single implementation.
            tool = kancahub.AUTO_FREECF / "scripts" / "opencode_inject.py"
            if not tool.exists():
                print(kancahub.col("red", f"✗ opencode_inject.py not found at {tool}"))
                return 1
            py = kancahub.pick_python()
            argv = [py, str(tool)]
            key = getattr(a, "api_key", None)
            if key:
                argv += ["--key", key]
            for flag, val in (("--from-file", getattr(a, "from_file", None)),
                              ("--from-json", getattr(a, "from_json", None)),
                              ("--from-auth-json", getattr(a, "from_auth_json", None))):
                if val:
                    argv += [flag, val]
            if getattr(a, "dry_run", False):
                argv.append("--dry-run")
            return kancahub.run(argv)

        prof = Path.home() / ".config" / "auto-freecf" / "sessions" / "opencode"
        prof.mkdir(parents=True, exist_ok=True)
        py = kancahub.pick_python(camoufox=True)
        print(kancahub.col("cyan", f"  opening {url}"))
        print(kancahub.col("dim", "  log in, then create an API key under Account; "
                                  "copy it and run: kancahub opencode inject --api-key <KEY>"))
        return kancahub.run([py, "-c", _OPENCODE_BROWSE % (str(prof), url)])

    print(kancahub.col("red", f"✗ unknown opencode subcommand: {sub}"))
    return 1


# Opens a headed browser on a persistent profile and waits for the user to close
# it. Kept as a string so it can be handed to `python -c` without a temp file.
_OPENCODE_BROWSE = """
import asyncio, sys
from camoufox.async_api import AsyncCamoufox
prof, url = sys.argv[1], sys.argv[2]
async def main():
    async with AsyncCamoufox(headless=False, humanize=True,
                             user_data_dir=prof) as browser:
        page = await browser.new_page()
        await page.goto(url, timeout=120000)
        print("READY", await page.title(), flush=True)
        while True:
            await page.wait_for_timeout(5000)
asyncio.run(main())
"""


def _opencode_inject_9router(key: str, kancahub) -> int:
    """Add an OpenCode API key as a 9router provider connection.

    Writes to 9router's SQLite the same way scripts/inject_thk_9router.py does:
    one connection row per key, so 9router's own failover rotates accounts.
    """
    import sqlite3
    import uuid

    db = Path.home() / ".9router" / "db" / "data.sqlite"
    if not db.is_file():
        print(kancahub.col("red", f"✗ 9router db not found at {db}"))
        return 1
    node = "openai-compatible-chat-opencode-zen"
    conn_id = str(uuid.uuid4())
    data = json.dumps({
        "apiKey": key,
        "baseUrl": "https://opencode.ai/zen/v1",
        "models": ["glm-5.3-flash-free", "mimo-v2.6-flash-free",
                   "nemotron-3.5-lightning-free", "minimax-m3.1-flash-free"],
    })
    try:
        c = sqlite3.connect(str(db))
        c.execute(
            "insert into providerConnections (id, provider, authType, name, email,"
            " priority, isActive, data) values (?,?,?,?,?,?,?,?)",
            (conn_id, node, "apiKey", "OpenCodeZen", None, 100, 1, data))
        c.commit()
    except Exception as exc:  # noqa: BLE001
        print(kancahub.col("red", f"✗ 9router inject failed: {exc}"))
        return 1
    print(kancahub.col("green", f"✓ injected OpenCode Zen key as {conn_id}"))
    print(kancahub.col("dim", "  NOTE: free-tier keys still refuse requests that do not"
                              " come from the OpenCode client (403 FreeTierError). This"
                              " row only works for a PAID Zen key."))
    return 0

def cmd_omnirush(a: Any) -> int:
    """OmniRush device login & gateway bridge."""
    import subprocess
    script = Path(__file__).resolve().parent / "omnirush_bridge.py"
    cmd = [sys.executable, str(script)]
    sub = getattr(a, "omnirush_cmd", None)
    if sub:
        cmd.append(sub)
    return subprocess.call(cmd)
