"""commands_thk — TokenHarbor, mail, and gmail commands for kancahub."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

# TokenHarbor caps signups per network. Measured live on a fresh mobile IP: 5 accounts (even
# at 12s gaps) then "Too many sign-ups from this network. Please try again in an hour."
# Pace did not matter, only the per-IP count. 4 leaves a safety margin; 5 is the hard max.
THK_PER_IP_DEFAULT = 4
THK_PER_IP_MAX = 5


def thk_classify(out: str) -> str:
    """Why a harbor batch failed: netcap | throttled | blocked | other. Pure.

    Success is NOT decided here: it comes from harbor/account.json (_thk_account_count).
    """
    o = (out or "").lower()
    if "too many sign-ups from this network" in o:
        return "netcap"      # per-network cap (~1h): only a NEW IP helps; waiting a minute does not
    if "a bit fast" in o or "take a breath" in o:
        return "throttled"   # burst throttle: waiting a minute or two helps
    if "not supported" in o:
        return "blocked"     # IP / email provider rejected outright
    return "other"


def _thk_account_count() -> int:
    """Real number of accounts saved by harbor (account.json), not what we asked for."""
    import kancahub
    try:
        d = json.loads((kancahub.HARBOR / "account.json").read_text())
        return len(d) if isinstance(d, list) else (1 if d else 0)
    except Exception:  # noqa: BLE001
        return 0


def _current_ip() -> str | None:
    import kancahub
    try:
        if str(kancahub.SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(kancahub.SCRIPTS_DIR))
        import session_guard
        return session_guard.get_ip()
    except Exception:  # noqa: BLE001
        return None


def _thk_fresh_ip(used: set[str], tries: int = 3) -> bool:
    """Rotate the carrier IP until it is one we have not used this run. Records it in `used`."""
    import kancahub
    for _ in range(tries):
        ip = kancahub._mobile_rotate_once()
        if ip and ip not in used:
            used.add(ip)
            return True
        print(kancahub.col("yellow", f"  [thk] carrier returned {'the same' if ip else 'no'} IP; rotating again…"))
    return False


def thk_chunks(count: int, per_ip: int = THK_PER_IP_DEFAULT) -> list[int]:
    """Split `count` accounts into per-IP chunks, e.g. (7, 3) -> [3, 3, 1]. Pure."""
    per_ip = max(1, min(int(per_ip), THK_PER_IP_MAX))
    count = max(0, int(count))
    return [per_ip] * (count // per_ip) + ([count % per_ip] if count % per_ip else [])


def cmd_thk(a) -> int:
    import kancahub
    py = kancahub.pick_python()
    sub = a.thk_cmd
    harbor = kancahub.HARBOR / "tools" / "tokenharbor"

    if sub in ("setup", "batch", "create-key", "test-key", "enable-free",
               "check-proxies", "status"):
        if not harbor.exists():
            print(kancahub.col("red", f"✗ harbor not found at {kancahub.HARBOR}"))
            return 1
        # Harbor's free Turnstile solver needs camoufox + playwright, which live
        # only in the isolated camoufox-venv. Run the CLI there.
        py = kancahub.pick_python(camoufox=True)
        cfg = harbor / "config.toml"
        if not cfg.exists():
            (harbor / "config.toml").write_text((harbor / "example.config.toml").read_text())
            print(kancahub.col("yellow", f"• created {cfg} from example — set [tempik].base_url + capsolver key"))
        cmd = [py, "-m", "tools.tokenharbor.cli", sub]
        # pass through common optionals
        for flag, val in (("--email", getattr(a, "email", None)),
                          ("--password", getattr(a, "password", None)),
                          ("--label", getattr(a, "label", None))):
            if val:
                cmd += [flag, val]
        if sub == "batch" and getattr(a, "count", None):
            cmd = [py, "-m", "tools.tokenharbor.cli", "batch", str(a.count)]
            if getattr(a, "concurrency", None):
                cmd += ["--concurrency", str(a.concurrency)]
        if sub == "test-key" and getattr(a, "key", None):
            cmd = [py, "-m", "tools.tokenharbor.cli", "test-key", a.key]
        # Farm commands (setup/batch/create-key): auto-wire a verified egress.
        # Harbor picks its own proxy from tools/tokenharbor/proxy_list.txt, so an
        # explicit/auto hop is only inherited through the environment.
        env = None
        if sub in ("setup", "batch", "create-key"):
            mode = getattr(a, "proxy", None) or kancahub.PROXY_AUTO
            if getattr(a, "no_proxy", False):
                mode = kancahub.PROXY_NONE
            if getattr(a, "mobile_rotate", False):
                # The tethered phone's carrier IP IS the egress: go direct (harbor
                # NO_PROXY) and rotate it; a pool gateway would mix IPs mid-session.
                mode = kancahub.PROXY_NONE
            user_country = getattr(a, "country", None)
            thk_policy = kancahub.EGRESS_COUNTRY.get("thk", {})
            thk_exclude = set(thk_policy.get("exclude", set()))
            thk_allow = set(thk_policy.get("allow", set()))
            if user_country:
                thk_allow = {user_country.strip().upper()}
                thk_exclude = set()

            choice = kancahub._choose_egress(
                mode,
                kancahub.EGRESS_TARGETS["thk"],
                country=thk_allow if thk_allow else None,
                exclude_countries=thk_exclude if thk_exclude else None,
            )
            env = dict(kancahub._proxy_env(choice) or {})
            if choice.direct:
                print(kancahub.col("dim", "  [proxy] direct connection (no egress gateway acquired)"))
                # Harbor otherwise picks a random proxy from its own pool — tell it
                # to stay direct so the user's egress (e.g. mobile tether) is used.
                env["TOKENHARBOR_NO_PROXY"] = "1"
        try:
            mobile = getattr(a, "mobile_rotate", False)
            env = dict(env or {})
            before = kancahub._thk_account_count()
            used_ips: set[str] = {kancahub._current_ip() or ""}
            # Batches run in per-IP chunks; the carrier IP rotates between chunks
            # (--mobile-rotate does its own rotation before each chunk's run).
            chunks = kancahub.thk_chunks(a.count, getattr(a, "per_ip", kancahub.THK_PER_IP_DEFAULT)) if sub == "batch" else [None]
            rc, made = 0, 0
            for i, n in enumerate(chunks, 1):
                if n is not None:
                    cmd = [py, "-m", "tools.tokenharbor.cli", "batch", str(n)]
                    if getattr(a, "concurrency", None):
                        cmd += ["--concurrency", str(a.concurrency)]
                    if len(chunks) > 1:
                        print(kancahub.col("cyan", f"\n  [thk] chunk {i}/{len(chunks)}: {n} account(s) on this IP"))
                # chunk 1 rotates inside run_with_mobile_retry; later chunks were already rotated
                # (and verified fresh) by _thk_fresh_ip, so don't toggle airplane mode twice.
                crc = kancahub.run_with_mobile_retry(cmd, cwd=kancahub.HARBOR, env=env,
                                                     mobile_rotate=mobile and i == 1,
                                                     account=f"thk:{n or 1}x")
                made = kancahub._thk_account_count() - before  # real accounts, not the chunk size
                why = kancahub.thk_classify(kancahub._LAST_RUN_OUT.get("text", ""))
                short = n is not None and made < sum(chunks[:i])
                if why == "netcap" or why == "blocked":
                    print(kancahub.col("red", f"  [thk] {why}: this IP is capped"
                                             + (" for ~1h — rotate to a NEW IP" if why == "netcap" else "")))
                    rc = rc or 1
                    if n is not None:
                        break  # retrying a capped IP only extends the lockout
                elif crc != 0:
                    rc = crc
                    if n is not None:
                        break  # a failed chunk means this IP/egress is burnt: stop, don't hammer
                elif short:
                    rc = rc or 1  # chunk "succeeded" but short: some signups were throttled
                if n is not None and i < len(chunks):
                    if not mobile:
                        print(kancahub.col("yellow", "  [thk] per-IP cap reached; re-run on a fresh IP "
                                                    "(or use --mobile-rotate to rotate automatically)."))
                        break
                    # --mobile-rotate rotates again before the next run; here we only make sure the
                    # carrier handed out a DIFFERENT IP (it often keeps the same one), else the next
                    # chunk would run on a capped IP.
                    if not kancahub._thk_fresh_ip(used_ips):
                        print(kancahub.col("red", "  [thk] could not get a new IP after 3 rotations — "
                                                 "stopping before the next chunk (wait ~1h or switch network)."))
                        rc = rc or 1
                        break
            # Record every thk batch attempt so 'kancahub report' can show what worked.
            kancahub._ledger_record(
                "thk",
                target=kancahub.EGRESS_TARGETS["thk"],
                egress=("mobile" if mobile else (choice.source if 'choice' in dir() else "")),
                stage=("created" if made else "failed"),
                ok=bool(made) if sub == "batch" else (rc == 0),
                count=made,
            )
            if sub == "batch" and (getattr(a, "inject", False) or getattr(a, "store", None) in ("9router", "both")) and made:
                print(kancahub.col("cyan", "\n  [thk] injecting new keys into 9Router…"))
                inj_rc = kancahub.run([py, str(kancahub.AUTO_FREECF / "scripts" / "inject_thk_9router.py"), "--verify"], cwd=kancahub.AUTO_FREECF)
                if inj_rc != 0:
                    print(kancahub.col("yellow", "  ⚠ inject failed; keys stay in harbor/account.json — retry: kancahub thk inject"))
                    rc = rc or inj_rc
            if sub == "batch":
                print(kancahub.col("green" if made else "red", f"\n  [thk] {made}/{a.count} account(s) created"))
                return 0 if made else (rc or 1)
            return rc
        finally:
            kancahub._stop_auto_gateways()

    if sub == "inject":
        inj = kancahub.AUTO_FREECF / "scripts" / "inject_thk_9router.py"
        cmd = [py, str(inj)]
        if a.input:
            cmd += ["-i", a.input]
        if a.model:
            cmd += ["--model", a.model]
        if a.verify:
            cmd.append("--verify")
        if a.dry_run:
            cmd.append("--dry-run")
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    if sub == "sync":
        return kancahub._thk_sync(a, py)

    if sub == "setup-env":
        helper = kancahub.AUTO_FREECF / "scripts" / "harbor_config.py"
        if not helper.exists():
            print(kancahub.col("red", f"✗ harbor_config.py not found at {helper}"))
            return 1
        cmd = [py, str(helper)]
        for flag, val in (("--harbor-dir", getattr(a, "harbor_dir", None)),
                          ("--env-file", getattr(a, "env_file", None)),
                          ("--proxies-src", getattr(a, "proxies_src", None)),
                          ("--tempik-url", getattr(a, "tempik_url", None))):
            if val:
                cmd += [flag, val]
        if getattr(a, "no_proxies", False):
            cmd.append("--no-proxies")
        if getattr(a, "status", False):
            cmd.append("--status")
        if getattr(a, "dry_run", False):
            cmd.append("--dry-run")
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    print(kancahub.col("red", "✗ unknown thk command"))
    return 1


def _thk_sync(a, py) -> int:
    """Verify + prune TokenHarbor connections in 9Router."""
    import kancahub
    import sqlite3
    db = Path(a.db or kancahub.NINE_ROUTER_DB)
    if not db.exists():
        print(kancahub.col("red", f"✗ 9Router DB not found: {db}"))
        return 1
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    node = None
    for nid, data in con.execute("SELECT id, data FROM providerNodes"):
        try:
            if json.loads(data or "{}").get("prefix") == "THK":
                node = nid
        except Exception:
            pass
    if not node:
        print(kancahub.col("yellow", "No TokenHarbor node in 9Router — run `kancahub thk inject` first."))
        con.close()
        return 0
    rows = list(con.execute("SELECT id, name, data FROM providerConnections WHERE provider=?", (node,)))
    print(f"Testing {len(rows)} TokenHarbor connection(s)…")
    dead = []
    for r in rows:
        dd = json.loads(r["data"]); key = dd["apiKey"]; model = dd.get("defaultModel") or "deepseek-v4.1-flash:free"
        url = f"https://tokenharbor.ai/v1/chat/completions"
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}).encode()
        # tokenharbor.ai is intermittently SLOW (30s+). A single 20s shot with
        # no retry falsely marks valid keys dead — and --prune then DELETES them,
        # which is how good keys "go invalid". Retry, and only treat a definitive
        # auth rejection (401/403) as dead; timeouts/429/5xx are "unknown" → keep.
        verdict = "unknown"
        for attempt in range(3):
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=45) as resp:
                    verdict = "alive" if resp.status == 200 else "unknown"
                    break
            except urllib.error.HTTPError as he:
                if he.code in (401, 403):
                    verdict = "dead"
                    break
                verdict = "unknown"  # 429 / 5xx → keep, don't prune
            except Exception:
                verdict = "unknown"
            time.sleep(3)
        alive = verdict == "alive"
        print(f"  {'✅' if alive else ('⛔' if verdict == 'dead' else '⚠ keep')} {r['name']:34s}")
        if verdict == "dead":
            dead.append(r["id"])
    if a.prune and dead:
        for cid in dead:
            con.execute("DELETE FROM providerConnections WHERE id=?", (cid,))
        con.commit()
        print(f"  ✓ removed {len(dead)} dead")
    con.close()
    return 0


def cmd_mail(a) -> int:
    """School mailbox (BINUS M365) via browser — wraps scripts/school_mail_browser.py."""
    import kancahub
    # school_mail_browser uses nodriver; run it under camoufox-venv.
    py = kancahub.pick_python(camoufox=True)
    smb = kancahub.AUTO_FREECF / "scripts" / "school_mail_browser.py"
    if not smb.exists():
        print(kancahub.col("red", f"✗ school_mail_browser.py not found at {smb}"))
        return 1

    sub = getattr(a, "mail_cmd", None)
    if not sub:
        print(kancahub.col("yellow", "• no mail subcommand — showing help"))
        return kancahub.run([py, str(smb), "--help"], cwd=kancahub.AUTO_FREECF)

    if sub in ("test", "login", "otp"):
        cmd = [py, str(smb), sub]
        if sub == "otp":
            cmd += ["--timeout", str(getattr(a, "timeout", 180) or 180)]
        if sub == "login":
            print(kancahub.col("dim", "  (login leaves the browser open — Ctrl-C when done inspecting)"))
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    print(kancahub.col("red", f"✗ unknown mail command: {sub}"))
    return 1


def cmd_gmail(a) -> int:
    """Gmail account farm — wraps scripts/gmail_creator.py (and gmail_adb.py)."""
    import kancahub
    # gmail_creator drives the browser via nodriver; run it under camoufox-venv
    # so ALL browser subcommands share one reliable interpreter.
    py = kancahub.pick_python(camoufox=True)
    gc = kancahub.AUTO_FREECF / "scripts" / "gmail_creator.py"
    sub = getattr(a, "gmail_cmd", None)

    # Android/ADB path (real device skips Google's phone gate).
    if sub == "adb":
        adbg = kancahub.AUTO_FREECF / "scripts" / "gmail_adb.py"
        if not adbg.exists():
            print(kancahub.col("red", "✗ gmail_adb.py not found"))
            return 1
        cmd = [py, str(adbg), "--count", str(getattr(a, "count", 1) or 1)]
        if getattr(a, "package", None):
            cmd += ["--package", a.package]
        if getattr(a, "password", None):
            cmd += ["--password", a.password]
        if getattr(a, "dry_run", False):
            cmd.append("--dry-run")
        print(kancahub.col("cyan", "Using the Android phone (ADB) — trusted-device path"))
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    if sub == "slow":
        tool = kancahub.AUTO_FREECF / "scripts" / "gmail_slow.py"
        if not tool.exists():
            print(kancahub.col("red", "✗ gmail_slow.py not found"))
            return 1
        cmd = [py, str(tool), a.action]
        if a.action == "run":
            cmd += ["--interval-days", str(a.interval_days), "--backend", a.backend,
                    "--count", str(a.count)]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    if sub == "waydroid":
        wd = kancahub.AUTO_FREECF / "scripts" / "gmail_waydroid.py"
        if not wd.exists():
            print(kancahub.col("red", "✗ gmail_waydroid.py not found"))
            return 1
        cmd = [py, str(wd), "--cdp", getattr(a, "cdp", "http://127.0.0.1:9222")]
        for flag in ("first", "last", "password", "month", "md"):
            if getattr(a, flag, None):
                cmd += [f"--{flag}", str(getattr(a, flag))]
        if getattr(a, "day", None):
            cmd += ["--day", str(a.day)]
        if getattr(a, "year", None):
            cmd += ["--year", str(a.year)]
        cmd += ["--wait", str(getattr(a, "wait", 180))]
        if getattr(a, "no_launch", False):
            cmd.append("--no-launch")
        print(kancahub.col("cyan", "Driving Gmail signup on Waydroid via Kiwi CDP (real mouse events)"))
        print(kancahub.col("dim", "  prereqs: waydroid running + Kiwi CDP on :9222 (adb forward)"))
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    if not gc.exists():
        print(kancahub.col("red", f"✗ gmail_creator.py not found at {gc}"))
        return 1

    if sub == "check":
        return kancahub.run([py, str(gc), "--check"], cwd=kancahub.AUTO_FREECF)

    if sub == "farm":
        # Base Gmail for plus-addressing: --plus-address wins, else read it from
        # ~/.config/auto-freecf/.env (GMAIL_FARM_ADDRESS). No password needed here —
        # the farm only needs the base ADDRESS to make you+tag@gmail.com variants.
        plus_addr = getattr(a, "plus_address", None) or kancahub._env_get("GMAIL_FARM_ADDRESS", kancahub.ENV_FILE) or None
        count = getattr(a, "count", 1) or 1
        if plus_addr:
            # HONEST: Google usernames cannot contain '+', so you CANNOT create
            # 'base+farmN@gmail.com'. Plus-addressing only REDIRECTS mail to an
            # address you already own — it is for signing up to OTHER services,
            # not for creating new Gmails. This farm makes NEW accounts with
            # random names, so the base address is not used here.
            print(kancahub.col("yellow", f"  ⚠ Plus-addressing ({plus_addr}) does NOT create Gmail accounts — "
                                        f"Google usernames cannot contain '+'. It only redirects mail to a Gmail you "
                                        f"already own, for signups to OTHER services. This farm still makes NEW random Gmails."))
            if getattr(a, "dry_run", False):
                print(kancahub.col("dim", "    For provider/service signups, use: autofarm --plus-address " + plus_addr))

        cmd = [py, str(gc), "--count", str(count)]
        if getattr(a, "headless", False):
            cmd.append("--headless")
        if getattr(a, "proxy", None) and not getattr(a, "no_proxy", False):
            cmd += ["--proxy", a.proxy]
        if getattr(a, "out", None):
            cmd += ["--out", a.out]
        if getattr(a, "dry_run", False):
            cmd.append("--dry-run")
        if getattr(a, "random_password", False):
            cmd.append("--random-password")
        return kancahub.run_with_mobile_retry(cmd, cwd=kancahub.AUTO_FREECF,
                                             mobile_rotate=getattr(a, "mobile_rotate", False),
                                             account=f"gmail:{getattr(a, 'count', 1)}x")

    if sub == "dry-run":
        cmd = [py, str(gc), "--count", str(getattr(a, "count", 1) or 1), "--dry-run"]
        if getattr(a, "proxy", None):
            cmd += ["--proxy", a.proxy]
        if getattr(a, "out", None):
            cmd += ["--out", a.out]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    print(kancahub.col("red", f"✗ unknown gmail command: {sub}"))
    return 1
