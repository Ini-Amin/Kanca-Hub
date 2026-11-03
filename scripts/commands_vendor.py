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
    mode = getattr(a, "mode", "claim") or "claim"
    py = kancahub.pick_python(camoufox=True)
    if mode == "auto-claim":
        zscript = kancahub.AUTO_FREECF / "scripts" / "zcode.py"
        return kancahub.run([py, str(zscript), "promo", "claim"] + _extra(a), cwd=kancahub.AUTO_FREECF)
    if mode == "quota":
        zscript = kancahub.AUTO_FREECF / "scripts" / "zcode.py"
        return kancahub.run([py, str(zscript), "quota"] + _extra(a), cwd=kancahub.AUTO_FREECF)

    root = VENDOR / "zcode-claim-9router"
    script = root / _ZCODE_SCRIPTS.get(mode, "")
    if not script or not script.exists():
        print(kancahub.col("red", f"✗ zcode {mode}: {script} not found"))
        print(kancahub.col("dim", f"  vendor: {root}"))
        return 1
    return kancahub.run([py, str(script)] + _extra(a), cwd=root)


def cmd_abliteration(a) -> int:
    """abliteration-bulk-creator: bulk abliteration.ai accounts, each with an API key."""
    import kancahub
    mode = getattr(a, "mode", "run") or "run"
    if mode == "connect":
        from abliteration_mint import inject_9router, KEYS_FILE
        key = getattr(a, "key", None)
        if not key and KEYS_FILE.is_file():
            lines = [l.strip() for l in KEYS_FILE.read_text().splitlines() if l.strip()]
            if lines:
                key = lines[-1].split(":")[-1]
        if key:
            ok = inject_9router(key)
            if ok:
                print(kancahub.col("green", "✓ Injected Abliteration node & key into 9Router SQLite DB!"))
                print(kancahub.col("dim", "  Prefix: ablit/* (e.g. ablit/abliterated-model-large-flash)"))
                return 0
            else:
                print(kancahub.col("red", "✗ Failed to inject Abliteration into 9Router DB"))
                return 1
        else:
            print(kancahub.col("yellow", "• No saved Abliteration key found. Run 'kancahub abliteration mint' first."))
            return 1
    if mode == "mint":
        # Native Camoufox mint (free Turnstile solve + our mailbox); vendor's
        # Chromium fails the Turnstile on every egress we have.
        tool = kancahub.AUTO_FREECF / "scripts" / "abliteration_mint.py"
        if not tool.exists():
            print(kancahub.col("red", f"✗ abliteration_mint.py not found at {tool}"))
            return 1
        py = kancahub.pick_python(camoufox=True)
        extra = _extra(a)
        if not any(arg.startswith("--proxy") for arg in extra):
            try:
                mode_p = getattr(a, "proxy", None) or kancahub.PROXY_AUTO
                policy = kancahub.EGRESS_COUNTRY.get("abliteration", {})
                country = getattr(a, "country", None)
                allow = {country.strip().upper()} if country else (set(policy.get("allow") or []) or None)
                exclude = None if country else (set(policy.get("exclude") or []) or None)
                choice = kancahub._choose_egress(
                    mode_p,
                    kancahub.EGRESS_TARGETS.get("abliteration", "https://abliteration.ai"),
                    country=allow,
                    exclude_countries=exclude,
                )
                if choice and choice.proxy:
                    extra = ["--proxy", choice.proxy] + extra
            except Exception:
                pass
        ret = kancahub.run([py, str(tool)] + extra)
        if ret == 0:
            from abliteration_mint import inject_9router, KEYS_FILE
            if KEYS_FILE.is_file():
                lines = [l.strip() for l in KEYS_FILE.read_text().splitlines() if l.strip()]
                if lines:
                    key = lines[-1].split(":")[-1]
                    inject_9router(key)
                    print(kancahub.col("green", "✓ Automatically injected Abliteration key into 9Router SQLite DB!"))
        return ret
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
    ret = kancahub.run([str(node), str(entry)] + _extra(a), cwd=root, env=env)
    # Automatically inject any minted keys into 9Router right after run
    if mode in ("run", "mint"):
        from abliteration_mint import inject_9router, KEYS_FILE
        vendor_keys = root / "accounts" / "keys.txt"
        if vendor_keys.is_file():
            lines = [l.strip() for l in vendor_keys.read_text().splitlines() if l.strip()]
            if lines:
                KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
                for line in lines:
                    email, key = line.split(":", 1) if ":" in line else ("", line)
                    inject_9router(key, email=email)
                print(kancahub.col("green", f"✓ Automatically injected {len(lines)} Abliteration key(s) into 9Router SQLite DB!"))
    return ret

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

    sub = getattr(a, "oc_cmd", None)
    if not sub and sys.stdin.isatty():
        sub = "menu"
    sub = sub or "status"

    if sub == "menu":
        print("\n" + "═" * 70)
        print(" " * 20 + "OpenCode AI Controller Hub")
        print("═" * 70)
        print("  [1] ⚡ Instant Free AI        (Direct inference — no login required)")
        print("  [2] 🚀 Fleet Parallel Run     (Dispatch prompt across local + blu VMs)")
        print("  [3] 👤 User Device Login      (Interactive disposable user authorization)")
        print("  [4] 🔐 Personal Operator Link (Connect operator's personal Brave session)")
        print("  [5] 🌾 Bulk Account Farm      (Temp Mail / Guerrilla Mail signup)")
        print("  [6] 📋 View Free Models       (46 free Zen models)")
        print("  [7] 🔍 CLI Status & Config    (Version, providers, 9Router keys)")
        print("  [ q] Cancel / Back")
        try:
            choice = input("\nPick an option [1-7]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice == "1":
            try:
                prompt = input("Enter prompt (default: 'Explain CSRF vulnerability in 2 sentences'): ").strip()
            except (EOFError, KeyboardInterrupt):
                return 0
            prompt = prompt or "Explain CSRF vulnerability in 2 sentences"
            setattr(a, "prompt", prompt)
            a.oc_cmd = "free"
            return cmd_opencode(a)
        elif choice == "2":
            try:
                prompt = input("Enter prompt for fleet parallel run: ").strip()
            except (EOFError, KeyboardInterrupt):
                return 0
            prompt = prompt or "Ping from orchestrator"
            setattr(a, "prompt", prompt)
            setattr(a, "parallel", True)
            a.oc_cmd = "free"
            return cmd_opencode(a)
        elif choice == "3":
            setattr(a, "personal", False)
            a.oc_cmd = "login"
            return cmd_opencode(a)
        elif choice == "4":
            setattr(a, "personal", True)
            a.oc_cmd = "login"
            return cmd_opencode(a)
        elif choice == "5":
            a.oc_cmd = "farm"
            return cmd_opencode(a)
        elif choice == "6":
            a.oc_cmd = "models"
            setattr(a, "free_only", True)
            return cmd_opencode(a)
        elif choice == "7":
            a.oc_cmd = "status"
            return cmd_opencode(a)
        return 0
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

    if sub in ("free", "anon"):
        model = getattr(a, "model", None) or "opencode/glm-5.3-flash-free"
        prompt = getattr(a, "prompt", None) or "Hello! What model are you?"
        remote = getattr(a, "remote", None)
        parallel = getattr(a, "parallel", False)

        if parallel:
            print(kancahub.col("cyan", f"  running parallel inference across fleet: local + blu [{model}]"))
            import concurrent.futures

            def _run_local():
                binary = shutil.which("opencode") or os.environ.get("OPENCODE_BIN", "") or str(Path.home() / ".opencode/bin/opencode")
                import tempfile
                with tempfile.TemporaryDirectory(prefix="oc-anon-") as tmp:
                    p = subprocess.run([binary, "run", "--standalone", "-m", model, prompt],
                                       cwd=tmp, capture_output=True, text=True, timeout=60)
                    return ("local", p.returncode, p.stdout.strip(), p.stderr.strip())

            def _run_remote(host):
                cmd = f"mkdir -p /tmp/oc_test && cd /tmp/oc_test && /root/.opencode/bin/opencode run --standalone -m {model} {subprocess.list2cmdline([prompt])}"
                p = subprocess.run(["ssh", host, cmd], capture_output=True, text=True, timeout=60)
                return (host, p.returncode, p.stdout.strip(), p.stderr.strip())

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                fut_loc = ex.submit(_run_local)
                fut_rem = ex.submit(_run_remote, "blu")
                for fut in [fut_loc, fut_rem]:
                    host, rc, out, err = fut.result()
                    if rc == 0:
                        print(kancahub.col("green", f"\n[{host}] ✓ (rc=0):"))
                        print(out)
                    else:
                        print(kancahub.col("yellow", f"\n[{host}] ✗ (rc={rc}): {err or out}"))
            return 0

        if remote:
            print(kancahub.col("cyan", f"  running remote inference on {remote} [{model}]"))
            rem_cmd = f"mkdir -p /tmp/oc_test && cd /tmp/oc_test && /root/.opencode/bin/opencode run --standalone -m {model} {subprocess.list2cmdline([prompt])}"
            return kancahub.run(["ssh", remote, rem_cmd])

        binary = shutil.which("opencode") or os.environ.get("OPENCODE_BIN", "") or str(Path.home() / ".opencode/bin/opencode")
        if not binary or not Path(binary).is_file():
            print(kancahub.col("red", "✗ opencode CLI not found"))
            return 1
        import tempfile
        with tempfile.TemporaryDirectory(prefix="oc-free-") as tmp:
            print(kancahub.col("dim", f"  standalone free run (no login required): model={model}"))
            return kancahub.run([binary, "run", "--standalone", "-m", model, prompt], cwd=tmp)

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
        if getattr(a, "mail", None):
            argv += ["--mail", a.mail]
        return kancahub.run(argv)

    if sub == "login":
        tool = kancahub.AUTO_FREECF / "scripts" / "opencode_auth.py"
        if not tool.exists():
            print(kancahub.col("red", f"✗ opencode_auth.py not found at {tool}"))
            return 1
        py = kancahub.pick_python()
        cmd = [py, str(tool), "device"]
        if getattr(a, "personal", False):
            cmd.append("--personal")
        return kancahub.run(cmd)
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

def cmd_dahlfarm(a: Any) -> int:
    """dahlfarm: bulk-mint Dahl Inference API keys with Turnstile solve."""
    import kancahub
    root = VENDOR / "dahlfarm"
    if not root.exists():
        print(kancahub.col("red", f"✗ dahlfarm not found at {root}"))
        return 1
    mode = getattr(a, "dahl_cmd", "mint") or "mint"
    script_map = {
        "mint": "src/cli.js",
        "start": "src/launcher.js",
        "verify": "src/verify.js",
        "connect": "src/connect-9router.js",
        "check": "check-proxies.js",
        "test": "test-models.js",
    }
    entry = root / script_map.get(mode, "src/cli.js")
    node = _find_node()
    return kancahub.run([str(node), str(entry)] + _extra(a), cwd=root)


def cmd_proxyvault(a: Any) -> int:
    """proxyvault: self-hosted proxy harvesting console with FastAPI & React UI."""
    import kancahub
    root = VENDOR / "proxyvault"
    if not root.exists():
        print(kancahub.col("red", f"✗ proxyvault not found at {root}"))
        return 1
    mode = getattr(a, "pv_cmd", "serve") or "serve"
    py = kancahub.pick_python()
    if mode == "serve":
        port = str(getattr(a, "port", 8888))
        print(kancahub.col("green", f"[*] Starting ProxyVault on http://127.0.0.1:{port}"))
        return kancahub.run([py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", port], cwd=root)
    elif mode == "demo":
        return kancahub.run([py, "scripts/record_demo.py"], cwd=root)
    return 0
def _find_node() -> str:
    found = shutil.which("node")
    if found:
        return found
    if sys.platform != "win32" and Path("/usr/bin/node").is_file():
        return "/usr/bin/node"
    return "node"


def _find_binary(root: Path, name: str) -> Path:
    candidates = [name]
    if sys.platform == "win32":
        candidates = [f"{name}.exe", name]
    for c in candidates:
        bp = root / "bin" / c
        if bp.is_file():
            return bp
    return root / "bin" / (f"{name}.exe" if sys.platform == "win32" else name)
def inject_orvis_9router(base_url: str = "http://127.0.0.1:8796/v1") -> bool:
    """Inject Orvis OpenAI-compatible bridge into 9Router SQLite DB without passwords."""
    import sqlite3
    import uuid
    db_path = Path.home() / ".9router/db/data.sqlite"
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        node_id = "openai-compatible-chat-orvis-bridge"
        node_name = "Orvis"
        now = int(time.time() * 1000)

        cur.execute("SELECT id FROM providerNodes WHERE id=?", (node_id,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO providerNodes (id, name, prefix, apiType, baseUrl, createdAt, updatedAt) "
                "VALUES (?, ?, 'orvis', 'openai-compatible', ?, ?, ?)",
                (node_id, node_name, base_url, now, now),
            )

        cdata = json.dumps({
            "name": "Orvis-Bridge-Local",
            "apiKey": "sk-orvis-local-bridge",
            "providerSpecificData": {"baseUrl": base_url},
        })
        cur.execute("SELECT id FROM providerConnections WHERE provider=?", (node_id,))
        existing = cur.fetchone()
        if existing:
            cur.execute("UPDATE providerConnections SET data=?, isActive=1 WHERE provider=?", (cdata, node_id))
        else:
            cid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO providerConnections (id, provider, authType, name, priority, isActive, data, createdAt, updatedAt) "
                "VALUES (?, ?, 'apikey', 'Orvis-Bridge-Local', 1, 1, ?, ?, ?)",
                (cid, node_id, cdata, now, now),
            )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def cmd_orvis(a: Any) -> int:
    """orvispool: bulk Orvis AI accounts (100 free credits each) -> 16 frontier models bridge."""
    import kancahub
    root = VENDOR / "orvispool"
    if not root.exists():
        print(kancahub.col("red", f"✗ orvispool not found at {root}"))
        return 1
    binary = _find_binary(root, "orvispool")
    if not binary.is_file():
        print(kancahub.col("cyan", "[*] Compiling orvispool binary..."))
        out_bin = f"bin/orvispool.exe" if sys.platform == "win32" else "bin/orvispool"
        ret = subprocess.call(["go", "build", "-o", out_bin, "./cmd/orvispool"], cwd=root)
        binary = _find_binary(root, "orvispool")
        if ret != 0 or not binary.is_file():
            print(kancahub.col("red", "✗ Failed to compile orvispool"))
            return 1

    mode = getattr(a, "orvis_cmd", "accounts") or "accounts"
    extra = _extra(a)

    if mode == "connect":
        port = getattr(a, "port", 8796) or 8796
        base_url = f"http://127.0.0.1:{port}/v1"
        ok = inject_orvis_9router(base_url)
        if ok:
            print(kancahub.col("green", f"✓ Injected Orvis node ({base_url}) into 9Router SQLite DB!"))
            print(kancahub.col("dim", "  Prefix: orvis/* (e.g. orvis/anthropic/claude-sonnet-4-20250514, orvis/openai/o3-mini)"))
            return 0
        else:
            print(kancahub.col("red", "✗ Failed to inject Orvis into 9Router DB"))
            return 1

    if mode == "test":
        port = getattr(a, "port", 8796) or 8796
        import urllib.request
        model = getattr(a, "model", "anthropic/claude-sonnet-4-20250514") or "anthropic/claude-sonnet-4-20250514"
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "respond with pong"}],
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer sk-orvis-local-bridge"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read().decode())
                ans = res["choices"][0]["message"]["content"]
                print(kancahub.col("green", f"✓ Orvis Bridge ({model}): {ans}"))
                return 0
        except Exception as exc:
            print(kancahub.col("red", f"✗ Orvis test failed: {exc}"))
            return 1

    cmd = [str(binary), mode] + extra
    return kancahub.run(cmd, cwd=root)


def cmd_komafree(a: Any) -> int:
    """komafree: keyless instant vLLM onboarding & chat (native Python + Go fallback)."""
    import kancahub
    mode = getattr(a, "koma_cmd", "doctor") or "doctor"

    # Native Python fast path
    if mode == "chat":
        from native_providers import KomaFree
        prompt = getattr(a, "prompt", "hello") or "hello"
        model = getattr(a, "model", "koma/apple") or "koma/apple"
        try:
            res = KomaFree.chat(prompt, model=model)
            print(kancahub.col("green", f"✓ {res['model']} ({res['latency_s']}s):"))
            print(f"  {res['text']}")
            return 0
        except Exception as exc:
            print(kancahub.col("red", f"✗ KomaFree chat failed: {exc}"))
            return 1

    if mode == "doctor":
        from native_providers import KomaFree
        try:
            ok = KomaFree.ping()
            if ok:
                print(kancahub.col("green", "✓ KomaFree upstream (https://koma.run) is LIVE and responding!"))
                print(kancahub.col("dim", "  Model: koma/apple (vLLM inference, keyless, zero-auth)"))
                return 0
            else:
                print(kancahub.col("yellow", "⚠ KomaFree ping returned empty or unexpected response."))
                return 1
        except Exception as exc:
            print(kancahub.col("red", f"✗ KomaFree connectivity check failed: {exc}"))
            return 1

    # Fallback to compiled binary if requested
    root = VENDOR / "komafree"
    if not root.exists():
        print(kancahub.col("yellow", "• Native Python handled komafree (no Go vendor needed)."))
        return 0
    binary = _find_binary(root, "komafree")
    if not binary.is_file():
        print(kancahub.col("cyan", "[*] Compiling komafree binary..."))
        out_bin = f"bin/komafree.exe" if sys.platform == "win32" else "bin/komafree"
        ret = subprocess.call(["go", "build", "-o", out_bin, "./cmd/komafree"], cwd=root)
        binary = _find_binary(root, "komafree")
        if ret != 0 or not binary.is_file():
            print(kancahub.col("red", "✗ Failed to compile komafree"))
            return 1

    extra = _extra(a)
    cmd = [str(binary), mode] + extra
    return kancahub.run(cmd, cwd=root)

def cmd_stepfun(a: Any) -> int:
    """stepfunrouter: bulk StepFun accounts & Studio session capture (Step-5 reasoning)."""
    import kancahub
    root = VENDOR / "stepfunrouter"
    if not root.exists():
        print(kancahub.col("red", f"✗ stepfunrouter not found at {root}"))
        return 1
    binary = _find_binary(root, "stepfunrouter")
    if not binary.is_file():
        print(kancahub.col("cyan", "[*] Compiling stepfunrouter binary..."))
        out_bin = f"bin/stepfunrouter.exe" if sys.platform == "win32" else "bin/stepfunrouter"
        ret = subprocess.call(["go", "build", "-o", out_bin, "./cmd/stepfunrouter"], cwd=root)
        binary = _find_binary(root, "stepfunrouter")
        if ret != 0 or not binary.is_file():
            print(kancahub.col("red", "✗ Failed to compile stepfunrouter"))
            return 1

    mode = getattr(a, "step_cmd", "models") or "models"
    extra = _extra(a)
    cmd = [str(binary), mode] + extra
    return kancahub.run(cmd, cwd=root)


def cmd_opensatan(a: Any) -> int:
    """opensatan: anonymous uncensored models (native Python + Node.js fallback)."""
    import kancahub
    mode = getattr(a, "satan_cmd", "chat") or "chat"

    # Native Python fast path
    if mode == "chat":
        from native_providers import OpenSatan
        prompt = getattr(a, "prompt", "hello") or "hello"
        try:
            res = OpenSatan.chat(prompt, persona="satan")
            print(kancahub.col("magenta", f"[OpenSatan - {res['persona']}] ({res['latency_s']}s):"))
            print(f"  {res['text']}")
            return 0
        except Exception as exc:
            print(kancahub.col("red", f"✗ OpenSatan chat failed: {exc}"))
            return 1

    root = VENDOR / "opensatan-bulk-9router"
    if not root.exists():
        print(kancahub.col("yellow", "• Native Python handled OpenSatan (no Node.js vendor needed)."))
        return 0
    script = root / "src" / "cli.js"
    node = _find_node()
    extra = _extra(a)
    cmd = [str(node), str(script), mode] + extra
    return kancahub.run(cmd, cwd=root, env={"POOL_PORT": "8784"})

def cmd_oxalpha(a: Any) -> int:
    """oxalpha-bulk-9router: GLM-5.3 Flash 1M token stealth reasoning pool."""
    import kancahub
    root = VENDOR / "oxalpha-bulk-9router"
    if not root.exists():
        print(kancahub.col("red", f"✗ oxalpha not found at {root}"))
        return 1
    mode = getattr(a, "ox_cmd", "list") or "list"
    script = root / "src" / "cli.js"
    node = _find_node()
    extra = _extra(a)
    cmd = [str(node), str(script), mode] + extra
    return kancahub.run(cmd, cwd=root, env={"PORT": "8781"})
