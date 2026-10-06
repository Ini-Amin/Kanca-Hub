#!/usr/bin/env python3
"""
Non-interactive driver for Grok xAI account registration (grok-register).

Configures /home/amen/grok-register/config.json programmatically with:
  - Mail relay (Cloudflare Email Routing via Supabase temp-mail-api)
  - Proxy settings (rotating pool or single proxy)
  - Register count & multi-threading
  - Local token pool export (token.json)

Then runs the registration batch non-interactively without terminal prompts.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
GROK_REG = HOME / "grok-register"
CONFIG_FILE = GROK_REG / "config.json"
EXAMPLE_FILE = GROK_REG / "config.example.json"


def _load_env() -> dict:
    env = {}
    p = HOME / ".config" / "auto-freecf" / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def prepare_config(
    accounts: int = 1,
    proxy_pool: str | None = None,
    proxy: str | None = None,
    workers: int = 1,
    headless: bool = False,
    mail_provider: str | None = None,
    domain: str | None = None,
) -> dict:
    """Read existing config or example template and merge automation settings."""
    cfg: dict = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    if not cfg and EXAMPLE_FILE.exists():
        try:
            cfg = json.loads(EXAMPLE_FILE.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

    # 1. Accounts & concurrency
    count = max(1, accounts)
    cfg["register_count"] = count
    cfg["multi_thread_enabled"] = bool(workers > 1 and count > 1)
    cfg["multi_thread_workers"] = max(1, workers)

    # 2. Proxy settings
    if proxy_pool:
        pool_path = Path(proxy_pool).resolve()
        cfg["proxy_mode"] = "pool"
        cfg["proxy_pool_file"] = str(pool_path)
    elif proxy:
        # grok-register only accepts proxy_mode in {auto,direct,single,pool};
        # a single explicit --proxy maps to "single" (was wrongly "fixed" → ConfigError).
        cfg["proxy_mode"] = "single"
        cfg["proxy"] = proxy
    else:
        # Check standard pool location
        default_pool = AUTO_FREECF / "signup_from_scratch" / "proxies.txt"
        if default_pool.exists() and default_pool.stat().st_size > 0:
            cfg["proxy_mode"] = "pool"
            cfg["proxy_pool_file"] = str(default_pool.resolve())
        elif not cfg.get("proxy_pool_file") and not cfg.get("proxy"):
            cfg["proxy_mode"] = "auto"

    # 3. Mail relay configuration
    env = _load_env()
    cf_cfg = {}
    cf_json = AUTO_FREECF / "signup_from_scratch" / "config.json"
    if cf_json.exists():
        try:
            cf_cfg = json.loads(cf_json.read_text(encoding="utf-8"))
        except Exception:
            pass

    supabase_url = (env.get("SUPABASE_URL") or "").rstrip("/")
    mail_key = env.get("TMK_KEY") or cf_cfg.get("mail_api_key") or ""
    if not supabase_url and cf_cfg.get("mail_api"):
        m = re.match(r"(https://[^/]+)", cf_cfg["mail_api"])
        supabase_url = m.group(1) if m else ""
    domains_list = (env.get("K12_DOMAINS") or ",".join(cf_cfg.get("mail_domains") or ["kancalabs.biz.id"])).split(",")
    target_domains = [d.strip() for d in domains_list if d.strip()]

    provider = (mail_provider or cfg.get("email_provider") or "cloudflare").strip().lower()
    if provider == "cloudflare" and supabase_url and mail_key:
        cfg["email_provider"] = "cloudflare"
        cfg["cloudflare_api_base"] = f"{supabase_url}/functions/v1/temp-mail-api"
        cfg["cloudflare_api_key"] = mail_key
        cfg["cloudflare_auth_mode"] = "x-api-key"
        cfg["cloudflare_path_accounts"] = "/new_address"
        cfg["cloudflare_path_messages"] = "/parsed_mails"
        selected_domain = domain or (target_domains[0] if target_domains else "kancalabs.biz.id")
        cfg["defaultDomains"] = selected_domain
    elif mail_provider:
        cfg["email_provider"] = mail_provider

    # 4. Token & CPA outputs
    cfg["grok2api_auto_add_local"] = True
    token_json_path = GROK_REG / "token.json"
    cfg["grok2api_local_token_file"] = str(token_json_path)
    cfg["cpa_export_enabled"] = True
    cfg["cpa_auth_dir"] = str(GROK_REG / "cpa_auths")
    if headless:
        cfg["cpa_headless"] = True

    # Persist config to disk
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg


def run_grok(
    accounts: int = 1,
    headless: bool = False,
    proxy_pool: str | None = None,
    proxy: str | None = None,
    workers: int = 1,
    mail_provider: str | None = None,
    domain: str | None = None,
) -> int:
    """Run grok registration non-interactively."""
    if not GROK_REG.exists():
        print(f"✗ grok-register not found at {GROK_REG}", file=sys.stderr)
        return 1

    cfg = prepare_config(
        accounts=accounts,
        proxy_pool=proxy_pool,
        proxy=proxy,
        workers=workers,
        headless=headless,
        mail_provider=mail_provider,
        domain=domain,
    )
    print(f"[*] Prepared grok config: provider={cfg.get('email_provider')}, "
          f"proxy_mode={cfg.get('proxy_mode')}, count={accounts}, workers={workers}", flush=True)

    # Change working directory and update sys.path for grok-register modules
    old_cwd = os.getcwd()
    os.chdir(str(GROK_REG))
    if str(GROK_REG) not in sys.path:
        sys.path.insert(0, str(GROK_REG))

    # Apply headless patch if requested
    if headless:
        try:
            import browser_runtime
            orig_create_browser_options = browser_runtime.create_browser_options

            def _headless_browser_options(*args, **kwargs):
                opts = orig_create_browser_options(*args, **kwargs)
                try:
                    opts.headless(True)
                    opts.set_argument("--headless=new")
                except Exception:
                    pass
                return opts

            browser_runtime.create_browser_options = _headless_browser_options
            print("[*] Headless browser mode enabled", flush=True)
        except Exception as e:
            print(f"[!] Warning: could not patch headless options: {e}", flush=True)

    try:
        import app_config
        app_config.load_config()
        import grok_register_ttk

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        accounts_output_file = os.path.join(str(GROK_REG), f"accounts_{timestamp}.txt")
        token_file = os.path.join(str(GROK_REG), "token.json")

        controller = grok_register_ttk.CliStopController()
        grok_register_ttk.cli_log(f"[*] 启动非交互注册任务，目标数量: {accounts}")
        grok_register_ttk.cli_log(f"[*] 账号输出文件: {accounts_output_file}")

        last_stats = {"success": 0, "fail": 0, "pending": 0, "warnings": 0}

        def observer(batch, account, output):
            last_stats["success"] = getattr(batch, "success_count", 0)
            last_stats["fail"] = getattr(batch, "fail_count", 0)
            last_stats["pending"] = getattr(batch, "registered_unsaved_count", 0)
            last_stats["warnings"] = getattr(batch, "postprocess_warning_count", 0)
            grok_register_ttk.cli_log(
                f"[*] 进度: 成功 {last_stats['success']} | 失败 {last_stats['fail']} | "
                f"待恢复 {last_stats['pending']} | 警告 {last_stats['warnings']}"
            )

        try:
            batch = grok_register_ttk.run_registration_common(
                count=accounts,
                log_callback=grok_register_ttk.cli_log,
                cancel_callback=controller.should_stop,
                accounts_output_file=accounts_output_file,
                observer=observer,
            )
            if batch:
                last_stats["success"] = getattr(batch, "success_count", last_stats["success"])
                last_stats["fail"] = getattr(batch, "fail_count", last_stats["fail"])
        except KeyboardInterrupt:
            controller.stop()
            grok_register_ttk.cli_log("[!] 收到中断信号，已停止")
        except Exception as exc:
            grok_register_ttk.log_exception("注册执行失败", exc, grok_register_ttk.cli_log)

        print("\n" + "=" * 60)
        print(f"📊 Grok Registration Summary:")
        print(f"   Success : {last_stats['success']}")
        print(f"   Failed  : {last_stats['fail']}")
        print(f"   Pending : {last_stats['pending']}")
        print(f"   Accounts file : {accounts_output_file}")
        print(f"   Token pool    : {token_file}")
        print("=" * 60 + "\n", flush=True)

        return 0 if last_stats["success"] > 0 or last_stats["fail"] == 0 else 1

    finally:
        os.chdir(old_cwd)


def main():
    ap = argparse.ArgumentParser(description="Non-interactive driver for grok-register")
    ap.add_argument("-n", "--accounts", type=int, default=1, help="number of accounts to register")
    ap.add_argument("--headless", action="store_true", help="run browser in headless mode")
    ap.add_argument("--proxy-pool", default=None, help="path to proxy pool file")
    ap.add_argument("--proxy", default=None, help="single proxy URL (e.g. http://127.0.0.1:8888)")
    ap.add_argument("--workers", type=int, default=1, help="concurrent worker threads")
    ap.add_argument("--mail-provider", choices=["cloudflare", "duckmail", "yyds", "outlook"], default=None)
    ap.add_argument("--domain", default=None, help="custom domain for cloudflare mail provider")

    args = ap.parse_args()
    rc = run_grok(
        accounts=args.accounts,
        headless=args.headless,
        proxy_pool=args.proxy_pool,
        proxy=args.proxy,
        workers=args.workers,
        mail_provider=args.mail_provider,
        domain=args.domain,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
