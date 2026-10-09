"""commands_otp — otp, scrape, and autofarm commands for kancahub."""
from __future__ import annotations


def cmd_otp(a) -> int:
    """Litensi email-activation client (scripts/otp_litensi.py)."""
    import kancahub
    py = kancahub.pick_python()  # requests only — no browser
    sub = a.otp_cmd

    if sub == "webhook":
        tool = kancahub.AUTO_FREECF / "scripts" / "otp_webhook_up.py"
        if not tool.exists():
            print(kancahub.col("red", "✗ otp_webhook_up.py not found"))
            return 1
        if getattr(a, "latest", False):
            return kancahub.run([py, str(kancahub.AUTO_FREECF / "scripts" / "sms_webhook.py"), "--latest"], cwd=kancahub.AUTO_FREECF)
        return kancahub.run([py, str(tool), getattr(a, "action", "up")], cwd=kancahub.AUTO_FREECF)

    tool = kancahub.AUTO_FREECF / "scripts" / "otp_litensi.py"
    if not tool.exists():
        print(kancahub.col("red", f"✗ otp_litensi.py not found at {tool}"))
        return 1
    cmd = [py, str(tool), sub]
    if sub == "prices" and getattr(a, "site", None):
        cmd += ["--site", a.site]
    elif sub == "order" and getattr(a, "site", None):
        cmd += ["--site", a.site]
    elif sub == "wait":
        cmd += ["--order-id", a.order_id, "--email", getattr(a, "email", "") or "",
                "--timeout", str(getattr(a, "timeout", 240) or 240)]
    elif sub == "done":
        cmd += ["--order-id", a.order_id]
    return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)


def cmd_scrape(a) -> int:
    """Firecrawl scrape/search using the keys already configured in 9Router."""
    import kancahub
    py = kancahub.pick_python()
    tool = kancahub.AUTO_FREECF / "scripts" / "firecrawl.py"
    if not tool.exists():
        print(kancahub.col("red", f"✗ firecrawl.py not found at {tool}"))
        return 1
    sub = getattr(a, "scrape_cmd", None) or "key"
    if sub == "url":
        return kancahub.run([py, str(tool), "scrape", a.url], cwd=kancahub.AUTO_FREECF)
    if sub == "search":
        return kancahub.run([py, str(tool), "search", a.query, "--limit", str(getattr(a, "limit", 5))], cwd=kancahub.AUTO_FREECF)
    return kancahub.run([py, str(tool), "key"], cwd=kancahub.AUTO_FREECF)


def cmd_autofarm(a) -> int:
    import kancahub
    # autofarm drives Camoufox (falls back to Playwright) — both live ONLY in the
    # camoufox venv. The main venv has neither, so it crashed with ModuleNotFoundError.
    py = kancahub.pick_python(camoufox=True)
    tool = kancahub.AUTO_FREECF / "scripts" / "autofarm.py"
    cmd = [py, str(tool)]
    if getattr(a, "url", None):
        cmd.append(a.url)
    if getattr(a, "domain", None):
        cmd += ["--domain", a.domain]
    if getattr(a, "mail", None):
        cmd += ["--mail", a.mail]
    if getattr(a, "inject_9router", False):
        cmd.append("--inject-9router")
    if getattr(a, "out", None):
        cmd += ["--out", a.out]
    if getattr(a, "headless", False):
        cmd.append("--headless")
    if getattr(a, "inspect_only", False):
        cmd.append("--inspect-only")
    if getattr(a, "plus_address", None):
        plus_pfx = getattr(a, "plus_prefix", "farm") or "farm"
        sample_email = kancahub.make_plus_address(a.plus_address, plus_pfx, 1)
        print(kancahub.col("cyan", f"  • Plus-addressing configured: {sample_email}"))

    # Smart egress auto-wire: resolve --proxy auto|none|URL against the target.
    force_none = bool(getattr(a, "no_proxy", False)) or str(getattr(a, "proxy", "") or "").lower() in ("none", "direct", "off", "no")
    mode = kancahub.PROXY_NONE if force_none else (getattr(a, "proxy", None) or kancahub.PROXY_AUTO)
    target = getattr(a, "url", None) or "https://example.com"
    choice = kancahub._choose_egress(mode, target)
    _acct = f"autofarm:{target[:40]}"
    try:
        if force_none or not choice.proxy:
            # Tell autofarm explicitly to stay direct (it otherwise grabs pool[0]).
            cmd.append("--no-proxy")
            return kancahub.run_with_mobile_retry(cmd, cwd=kancahub.AUTO_FREECF,
                                                  mobile_rotate=getattr(a, "mobile_rotate", False), account=_acct)
        cmd += ["--proxy", choice.proxy]
        return kancahub.run_with_mobile_retry(cmd, cwd=kancahub.AUTO_FREECF, env=kancahub._proxy_env(choice),
                                              mobile_rotate=getattr(a, "mobile_rotate", False), account=_acct)
    finally:
        kancahub._stop_auto_gateways()
