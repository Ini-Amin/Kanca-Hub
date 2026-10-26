#!/usr/bin/env python3
"""scripts/zcode.py — ZCode Start Plan bridge CLI.

  account add <email> <pw>   store account (accounts.txt) + per-account bridge key
                             (keys.txt) + mint JWT (unless --no-mint)
  account list / rm <email>
  mint <email>|--all         refresh ZCode login token (OAuth flow, needs CDP Chrome)
  persona on [models...]|off|show  abliteration persona (whitelist: only listed models get it)
  models add <alias> <model> [--persona]   combo: virtual model -> real model (+persona)
  models list / rm <alias>
  quota [--live]             per-account quota (billing/balance) + local usage counters
  promo [check|claim]        list / claim promo "coupons" (billing/preview -> claim,
                             slider captcha when direct claim is rejected)
  farm [--dry-run]           create one fresh z.ai account + add to bridge + mint JWT
  autofarm on|off|show       watcher auto-runs farm when ALL accounts hit 0% GLM-5.3
                             (guards: max_accounts, cooldown_hours)
  watch [--once] [--interval N]
                             event watcher: plan changes, quota thresholds, quota
                             reset, inactive plan -> auto-claim (direct + slider
                             solver), invalid JWT -> auto re-mint, promo auto-claim,
                             balance.json cache the bridge uses to 429-failover
  serve [--port N]           run the bridge (scripts/zcode_bridge_server.py)

State: ~/.zcode-bridge/  (accounts.txt, keys.txt, jwt/<email>.txt, config.json,
persona.txt, usage.json, balance.json, events.log, promos.json, farm_state.json)
"""
import argparse, json, os, subprocess, sys, time, urllib.request, urllib.error

STATE = os.path.expanduser("~/.zcode-bridge")
def _find_browser_py():
    for cand in (
        os.environ.get("PY_BROWSER"),
        os.path.expanduser("~/.local/share/auto-freecf/camoufox-venv/bin/python"),
        os.path.expanduser("~/venv/bin/python"),
        sys.executable,
    ):
        if cand and os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand
    return sys.executable

PY_BROWSER = _find_browser_py()
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
MINT = os.path.join(SCRIPTS, "zcode_bridge.py")
CLAIM_HELPER = os.path.join(SCRIPTS, "zcode_claim.py")
SERVER = os.path.join(SCRIPTS, "zcode_bridge_server.py")


def p(name):
    return os.path.join(STATE, name)


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def load_json(path, default):
    try:
        return json.loads(read(path, json.dumps(default)))
    except Exception:
        return default


def event(kind, msg):
    line = "%s [%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), kind, msg)
    with open(p("events.log"), "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def inject_9router():
    """Give every bridge account a 9Router connection row (name Zcode<N>).

    Minting only makes the bridge serve an account; 9Router lists
    providerConnections rows, so an account without one is invisible in the
    dashboard and never used. Idempotent -- safe to call after every mint.
    """
    inj = os.path.join(SCRIPTS, "inject_thk_9router.py")
    if not os.path.exists(inj):
        return
    try:
        subprocess.call([sys.executable, inj, "--kind", "zcode"])
    except Exception as e:  # noqa: BLE001
        print("9router inject skipped:", str(e)[:80])


def accounts():
    out = []
    for line in read(p("accounts.txt")).splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ":" in line:
            e, w = line.split(":", 1)
            out.append((e.strip(), w.strip()))
    return out


def keys_map():
    m = {}
    for line in read(p("keys.txt")).splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            m[k.strip()] = v.strip()
    return m


def jwt_for(email):
    return read(p(os.path.join("jwt", email + ".txt"))).strip()


def device_mid(email=""):
    """Stable per-ACCOUNT device id.

    Measured 2026-10-24: sending the desktop app's single device-mid for every
    farmed account made z.ai treat the whole farm as ONE device, and
    /billing/preview answered `plans: []` — the 1024 Dev Day coupon was hidden.
    Repeating the same request with a fresh uuid for the same account returned
    `plans: [zcode-v3-start-plan-1024]`. So the id must be derived per account
    and stay stable for that account (rotating it every call would look like a
    device factory instead).

    Derived from the account name with UUIDv5 so it is deterministic, unique,
    and needs no storage.
    """
    import uuid
    if not email:
        try:
            return json.load(open(os.path.expanduser("~/.zcode/v2/telemetry-state.json")))["deviceMid"]
        except Exception:
            return "56e5b07f-d86e-4adc-b4f4-2384a5243378"
    ns = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    return str(uuid.uuid5(ns, "zcode-device:" + email.strip().lower()))


def billing(email, ep="balance", extra=""):
    jwt = jwt_for(email)
    if not jwt:
        raise RuntimeError("no JWT for %s (run: zcode.py mint %s)" % (email, email))
    H = {"Authorization": "Bearer " + jwt, "User-Agent": "ZCode/3.16.3",
         "http-referer": "https://zcode.z.ai", "x-platform": "linux-x64",
         "x-zcode-app-version": "3.16.3", "x-release-channel": "production",
         "x-client-language": "en-US", "x-client-timezone": "Asia/Jakarta",
         "x-os-category": "linux", "x-os-version": "7.2.8", "x-title": "Z Code@electron",
         "x-device-mid": device_mid(email),
         "x-request-id": "00000000-0000-4000-8000-%012d" % (time.time() % 1e12)}
    url = "https://zcode.z.ai/api/v1/zcode-plan/billing/%s?app_version=3.16.3%s" % (ep, extra)
    with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=25) as r:
        d = json.loads(r.read())
    if d.get("code") != 0:
        raise RuntimeError("billing/%s code=%s %s" % (ep, d.get("code"), d.get("msg")))
    return d["data"]


# ---------------- account ----------------
def cmd_account_add(a):
    line = "%s:%s" % (a.email, a.pw)
    cur = read(p("accounts.txt"))
    if a.email in [e for e, _ in accounts()]:
        print("already present:", a.email)
    else:
        write(p("accounts.txt"), (cur + ("\n" if cur and not cur.endswith("\n") else "") + line + "\n"))
        print("account stored:", a.email)
    if a.email not in keys_map().values():
        n = len(keys_map()) + 1
        key = "sk-zc-%02d-%s" % (n, os.urandom(12).hex())
        with open(p("keys.txt"), "a") as f:
            f.write("%s=%s\n" % (key, a.email))
        print("bridge key for 9router:", key)
    if not a.no_mint:
        # cmd_mint reads a.all and a.password; a bare Namespace(email=...) crashes.
        cmd_mint(argparse.Namespace(email=a.email, all=False, password=a.pw))
    os.makedirs(p("jwt"), exist_ok=True)
    inject_9router()


def cmd_account_list(a):
    km = keys_map()
    rev = {v: k for k, v in km.items()}
    cache = load_json(p("balance.json"), {})
    for email, _ in accounts():
        buckets = (cache.get(email) or {}).get("buckets", [])
        q = ", ".join("%s %s/%s" % (b.get("show_name"), b.get("used_units"), b.get("total_units"))
                      for b in buckets) or "no cache (run: zcode.py watch --once)"
        print("%-40s key=%s jwt=%s | %s" % (email, rev.get(email, "-")[:16] + "…" if rev.get(email) else "-",
                                            "yes" if jwt_for(email) else "NO", q))


def cmd_account_rm(a):
    write(p("accounts.txt"), "\n".join("%s:%s" % (e, w) for e, w in accounts() if e != a.email) + "\n")
    write(p("keys.txt"), "\n".join("%s=%s" % (k, v) for k, v in keys_map().items() if v != a.email) + "\n")
    print("removed:", a.email)


def cmd_mint(a):
    targets = accounts() if a.all else [x for x in accounts() if x[0] == a.email]
    if not targets and a.email:
        targets = [(a.email, a.password or "")]
    if not targets:
        sys.exit("no accounts in accounts.txt; add one first")
    if not _ensure_cdp_chrome():
        sys.exit("no CDP Chrome on :9222 and no chrome binary found")
    for email, pw in targets:
        if not pw:
            sys.exit("password missing for %s" % email)
        out = p(os.path.join("jwt", email + ".txt"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        env = dict(os.environ, ZB_EMAIL=email, ZB_PW=pw, ZB_OUT=out)
        print("minting", email, "->", out)
        rc = subprocess.call([PY_BROWSER, MINT], env=env)
        print("mint rc=%s for %s (%s)" % (rc, email, "ok" if os.path.exists(out) else "FAILED"))
        if os.path.exists(out) and email == os.environ.get("ZB_EMAIL"):
            # keep the shared fallback in sync (bridge server reads it)
            write(os.path.join(STATE, "zc_jwt.txt"), read(out).strip() + "\n")

def cmd_remint(a):
    """Smart batch re-minting of accounts with plan quota, prioritizing
    queued 3012 accounts and accounts with positive remaining headroom."""
    accts = dict(accounts())
    q = load_json(p("remint_queue.json"), {})
    bal = load_json(p("balance.json"), {})

    scored = []
    now = time.time()
    for email, pw in accts.items():
        if a.email and email != a.email:
            continue
        jf = p(os.path.join("jwt", email + ".txt"))
        age_hours = (now - os.path.getmtime(jf)) / 3600.0 if os.path.exists(jf) else 9999.0

        if not a.force and email not in q and age_hours < a.max_age_hours:
            continue

        b_info = bal.get(email, {})
        rem_units = 0
        if isinstance(b_info, dict):
            for b in b_info.get("buckets", []):
                if isinstance(b, dict):
                    rem_units += b.get("remaining_units") or 0

        priority = (1e12 if email in q else 0) + rem_units + (age_hours * 1000)
        scored.append((priority, email, pw, age_hours, rem_units))

    scored.sort(key=lambda x: -x[0])
    limit = a.limit if a.limit and a.limit > 0 else len(scored)
    selected = scored[:limit]

    print(f"[*] Auto re-mint queue: {len(selected)} accounts to process (out of {len(scored)} eligible)")
    if not selected:
        print("  All accounts have fresh JWTs (< %.1fh) and no pending queue." % a.max_age_hours)
        return

    if not _ensure_cdp_chrome():
        sys.exit("no CDP Chrome on :9222 and no chrome binary found")

    success = 0
    for idx, (_, email, pw, age, rem) in enumerate(selected, 1):
        if not _ensure_cdp_chrome():
            time.sleep(1)
            _ensure_cdp_chrome()
        print(f"\n[{idx}/{len(selected)}] Re-minting {email} (age: {age:.1f}h, quota: {rem:,})...")
        if not pw:
            print(f"  [!] Skipped {email}: missing password")
            continue
        out = p(os.path.join("jwt", email + ".txt"))
        env = dict(os.environ, ZB_EMAIL=email, ZB_PW=pw, ZB_OUT=out)
        rc = subprocess.call([PY_BROWSER, MINT], env=env)
        if rc == 0 and os.path.exists(out):
            success += 1
            print(f"  [✓] Successfully re-minted {email}")
            if email in q:
                del q[email]
                write(p("remint_queue.json"), json.dumps(q, indent=2))
        else:
            print(f"  [✗] Failed to re-mint {email} (rc={rc})")

        if idx < len(selected) and a.delay > 0:
            time.sleep(a.delay)

    print(f"\n[DONE] Auto re-mint completed: {success}/{len(selected)} succeeded.")
    if success > 0:
        inject_9router()


# ---------------- persona / combos ----------------
def _cfg_toggle(fn):
    cfg = load_json(p("config.json"), {})
    write(p("config.json"), json.dumps(fn(cfg), indent=1))


def cmd_persona(a):
    if a.action == "show":
        per = load_json(p("config.json"), {}).get("persona") or {}
        sel = per.get("models") or []
        print("enabled:", bool(per.get("enabled")))
        print("models:", ", ".join(sel) if sel else "ALL")
        print(read(p("persona.txt"))[:400])
        return
    on = a.action == "on"
    sel = list(a.models) if on else (load_json(p("config.json"), {}).get("persona") or {}).get("models") or []
    _cfg_toggle(lambda c: {**c, "persona": {"enabled": on, "models": sel}})
    event("persona", "persona %s models=%s" % ("ON" if on else "OFF", ",".join(sel) or "ALL"))


def cmd_models(a):
    cfg = load_json(p("config.json"), {})
    aliases = cfg.get("aliases") or {}
    if a.action == "add":
        chain = [x.strip() for x in a.model.split(",") if x.strip()]
        aliases[a.alias] = {"models": chain, "persona": bool(a.persona)}
        event("models", "alias %s -> %s persona=%s" % (a.alias, "->".join(chain), bool(a.persona)))
    elif a.action == "rm":
        aliases.pop(a.alias, None)
    else:
        for k, v in aliases.items():
            chain = v.get("models") or ([v.get("model")] if v.get("model") else [])
            print("%-24s -> %-30s persona=%s" % (k, "->".join(chain), v.get("persona")))
        return
    _cfg_toggle(lambda c: {**c, "aliases": aliases})


# ---------------- quota ----------------
def cmd_quota(a):
    data = {}
    if a.live:
        for email, _ in accounts():
            try:
                d = billing(email)
                data[email] = {"ts": time.time(),
                               "buckets": [dict(b, capabilities=b.get("capabilities")) for b in d.get("balances", [])],
                               "plans": [{"plan_id": x["plan_id"], "status": x["status"]} for x in d.get("plans", [])]}
            except Exception as e:
                print(email, "live ERR:", str(e)[:90])
        write(p("balance.json"), json.dumps(data))
    else:
        data = load_json(p("balance.json"), {})
    usage = load_json(p("usage.json"), {})
    for email, _ in accounts():
        print("== %s ==" % email)
        for b in (data.get(email) or {}).get("buckets", []):
            tot, used = b.get("total_units", 0), b.get("used_units", 0)
            pct = (100.0 * used / tot) if tot else 0
            print("  %-14s %8d/%-9d (%.1f%%) reset %s" % (b.get("show_name"), used, tot, pct,
                  time.strftime("%m-%d %H:%M", time.localtime(b.get("period_end", 0)))))
        local = usage.get(email) or {}
        for model, v in local.items():
            if isinstance(v, list) and len(v) == 2:
                print("  [bridge counted today] %-14s in=%d out=%d" % (model, v[0], v[1]))


# ---------------- watcher ----------------
def _claim_direct(email, plan_id):
    """POST billing/claim with the SAME per-account identity preview uses.

    The claim endpoint must see the same x-device-mid as the preview request
    that offered the coupon; sending the shared desktop id here is what made
    every claim answer 3012 "unusual activity" while preview looked fine.
    """
    jwt = jwt_for(email)
    body = json.dumps({"plan_id": plan_id}).encode()
    H = {"Authorization": "Bearer " + jwt, "Content-Type": "application/json",
         "User-Agent": "ZCode/3.16.3", "http-referer": "https://zcode.z.ai",
         "x-zcode-app-version": "3.16.3", "x-release-channel": "production",
         "x-client-language": "en-US", "x-client-timezone": "Asia/Jakarta",
         "x-os-category": "linux", "x-os-version": "7.2.8", "x-title": "Z Code@electron",
         "x-platform": "linux-x64", "x-device-mid": device_mid(email),
         "x-request-id": "00000000-0000-4000-8000-%012d" % (time.time() % 1e12)}
    req = urllib.request.Request("https://zcode.z.ai/api/v1/zcode-plan/billing/claim?app_version=3.16.3",
                                 data=body, headers=H, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"code": e.code}


# ---------------- promo ("coupon") auto-claim ----------------
# The desktop app's "manual claim" feature: GET billing/preview lists claimable
# plan coupons; POST billing/claim takes one (with Aliyun captcha in the app ->
# browser slider fallback here). Same claim endpoint the plan-inactive path uses.
PROMO_COOLDOWN = 24 * 3600


def promo_preview(email):
    """Claimable plan coupons (the app's getManualClaimPlanPreviews)."""
    d = billing(email, "preview", "&platform=linux-x64")
    return d.get("plans") or []


def _promo_try_claim(email, pw, plan_id):
    st = load_json(p("promos.json"), {})
    ses = st.get(email) or {}
    ses[plan_id] = time.time()   # record BEFORE claiming: dedupes watcher retries
    st[email] = ses
    write(p("promos.json"), json.dumps(st))
    r = _claim_direct(email, plan_id)
    event("promo_direct", "%s %s -> %s" % (email, plan_id, str(r)[:120]))
    if (r or {}).get("code") != 0:
        # the claim scene (11xygtvd) F001s in headless chromium; only the
        # headed CDP Chrome passes Aliyun's risk check -> ensure it is up.
        if not _ensure_cdp_chrome():
            event("promo_err", "%s %s: no CDP Chrome, skipping browser claim" % (email, plan_id))
            return
        rc = subprocess.call([PY_BROWSER, CLAIM_HELPER, email, pw, plan_id])
        event("promo_browser", "%s %s helper rc=%s" % (email, plan_id, rc))


def _promo_tick(email, pw):
    try:
        plans = promo_preview(email)
    except Exception as e:
        event("promo_err", "%s: %s" % (email, str(e)[:80]))
        return
    if not plans:
        return
    st = load_json(p("promos.json"), {}).get(email) or {}
    now = time.time()
    for pl in plans:
        pid = (pl.get("plan_id") or "").strip()
        if not pid:
            continue
        if now - float(st.get(pid) or 0) < PROMO_COOLDOWN:
            continue
        event("promo", "%s claimable: %s (%s)" % (email, pid, pl.get("name")))
        _promo_try_claim(email, pw, pid)


def cmd_promo(a):
    for email, pw in accounts():
        try:
            plans = promo_preview(email)
        except Exception as e:
            print(email, "preview ERR:", str(e)[:90])
            continue
        print("== %s == %d claimable" % (email, len(plans)))
        for pl in plans:
            pid = (pl.get("plan_id") or "").strip()
            print("  %-36s %s  %s" % (pid, pl.get("name"), (pl.get("description") or "")[:50]))
            if a.action == "claim" and pid:
                _promo_try_claim(email, pw, pid)


def _mint_guard(email, cooldown=1800):
    """True at most once per `cooldown` seconds per account (mint_state.json).

    Minting spawns a browser OAuth flow (~minutes); without this the 60s watcher
    would relaunch it on every tick for an account that can't be minted.
    """
    st = load_json(p("mint_state.json"), {})
    last = float((st.get(email) or {}).get("ts") or 0)
    if time.time() - last < cooldown:
        return False
    st[email] = {"ts": time.time()}
    write(p("mint_state.json"), json.dumps(st))
    return True


def _plan_claim_tick(email, pw, plan_id):
    """Try to claim an inactive plan -- but ONLY when the server actually offers
    a claimable coupon/plan for this account, and at most once per cooldown.

    Why the gate: the Aliyun claim captcha (scene 11xygtvd) is signed AND
    behavioural -- the vendor README documents that scripted SDK drags are
    silently rejected ("3007") no matter how accurate, so a claim can only
    succeed via a real interactive solve. Retrying every 60s tick produced an
    endless 3007/rc=1 loop and warmed up the WAF. `billing/preview` tells us
    whether a coupon exists at all; when it is empty there is nothing to claim,
    so we skip the browser helper entirely.
    """
    st = load_json(p("promos.json"), {}).get(email) or {}
    if time.time() - float(st.get(plan_id) or 0) < PROMO_COOLDOWN:
        return
    try:
        claimable = promo_preview(email)
    except Exception as e:  # noqa: BLE001
        event("promo_err", "%s preview: %s" % (email, str(e)[:80]))
        return
    if not claimable:
        # no coupon/voucher right now -> nothing the claim endpoint can grant
        return
    st[plan_id] = time.time()      # record BEFORE claiming: dedupes watcher retries
    write(p("promos.json"), json.dumps({**load_json(p("promos.json"), {}), email: st}))
    event("plan_inactive", "%s: coupon present -> claiming %s" % (email, plan_id))
    r = _claim_direct(email, plan_id)
    event("claim_direct", "%s -> %s" % (email, str(r)[:120]))
    if (r or {}).get("code") in (0, None):
        return
    # direct claim needs a signed interactive param; only the headed CDP Chrome
    # can even attempt it (headless SDK init F001s). One attempt per cooldown.
    if not _ensure_cdp_chrome():
        event("claim_err", "%s: no CDP Chrome, skipping browser claim" % email)
        return
    rc = subprocess.call([PY_BROWSER, CLAIM_HELPER, email, pw, plan_id])
    event("claim_browser", "%s helper rc=%s" % (email, rc))


def _watch_tick():
    cache = load_json(p("balance.json"), {})

    # 0) Process queued accounts that encountered 3012 or dead JWT
    remint_q = load_json(p("remint_queue.json"), {})
    if remint_q:
        accts_map = dict(accounts())
        for q_email, meta in list(remint_q.items()):
            pw = accts_map.get(q_email)
            if pw and _mint_guard(q_email):
                event("auto_remint", "%s (reason: %s)" % (q_email, meta.get("reason", "queue")))
                cmd_mint(argparse.Namespace(email=q_email, all=False, password=pw))
                remint_q.pop(q_email, None)
                write(p("remint_queue.json"), json.dumps(remint_q, indent=2))
                break

    for email, pw in accounts():
        # An account with no JWT can't be billed at all -- and never self-heals
        # (the generic handler below would just log billing_err every tick).
        # Mint it here, rate-limited, so a freshly farmed account becomes usable
        # without a human running `zcode.py mint` by hand.
        if not jwt_for(email):
            if _mint_guard(email):
                event("jwt_missing", "%s -> minting" % email)
                cmd_mint(argparse.Namespace(email=email, all=False, password=pw))
                ok = bool(jwt_for(email))
                event("jwt_mint", "%s -> %s" % (email, "ok" if ok else "failed"))
                if ok:
                    inject_9router()
            continue
        try:
            d = billing(email)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                event("jwt_invalid", "%s -> re-minting" % email)
                cmd_mint(argparse.Namespace(email=email, all=False, password=pw))
            else:
                event("billing_err", "%s: %s" % (email, str(e)[:80]))
            continue
        except Exception as e:
            event("billing_err", "%s: %s" % (email, str(e)[:80]))
            continue

        plans = [x for x in d.get("plans", []) if x.get("status") == "active"]
        prev = cache.get(email) or {}
        prev_plans = {x["plan_id"]: x["status"] for x in prev.get("plans", [])}
        for x in d.get("plans", []):
            if prev_plans.get(x["plan_id"]) != x["status"]:
                event("plan", "%s %s -> %s" % (email, x["plan_id"], x["status"]))
        if not plans:
            pid = (prev.get("plans") or [{}])[0].get("plan_id") or "zcode-v3-start-plan"
            _plan_claim_tick(email, pw, pid)

        for b in d.get("balances", []):
            tot, used = b.get("total_units", 0), b.get("used_units", 0)
            pct = 100.0 * used / tot if tot else 100
            # key by bucket_id: two plans can each have a "GLM-5.3-Flash" bucket
            bid = b.get("bucket_id") or b.get("show_name")
            old = {x.get("bucket_id") or x.get("show_name"): x
                   for x in prev.get("buckets", [])}.get(bid, {})
            old_pct = 100.0 * old.get("used_units", 0) / old.get("total_units", 1) if old.get("total_units") else 0
            for th in (0, 10, 50):
                if old_pct > th >= pct:
                    event("quota", "%s %s crossed %d%% used (%d/%d)" % (email, bid, th, used, tot))
            if old and old.get("period_start") != b.get("period_start"):
                event("quota_reset", "%s %s new period (used %d)" % (email, bid, used))
        cache[email] = {"ts": time.time(),
                        "buckets": d.get("balances", []),
                        "plans": [{"plan_id": x["plan_id"], "status": x["status"]} for x in d.get("plans", [])]}
    write(p("balance.json"), json.dumps(cache))
    cfg = load_json(p("config.json"), {})
    if (cfg.get("promo") or {}).get("auto_claim"):
        for email, pw in accounts():
            _promo_tick(email, pw)
    _autofarm_tick(cache)
    event("watch", "tick ok (%d accounts)" % len(accounts()))


# ---------------- autofarm ----------------
def _farm_cfg():
    return {"enabled": False, "max_accounts": 4, "cooldown_hours": 20,
            "min_active": 3,
            **(load_json(p("config.json"), {}).get("autofarm") or {})}


def _ensure_cdp_chrome():
    """mint (zcode_bridge.py) drives a CDP Chrome on :9222; start one if absent."""
    import socket
    s = socket.socket()
    s.settimeout(1.5)
    try:
        s.connect(("127.0.0.1", 9222))
        s.close()
        return True
    except OSError:
        pass
    for binary in ("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
                   "/usr/bin/chromium", "/usr/bin/chromium-browser"):
        if os.path.exists(binary):
            prof = os.path.expanduser("~/.config/auto-freecf/cdp-profile")
            subprocess.Popen([binary, "--remote-debugging-port=9222",
                              "--user-data-dir=" + prof,
                              "--no-first-run", "--no-default-browser-check"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            for _ in range(20):
                time.sleep(1)
                s = socket.socket()
                s.settimeout(1.5)
                try:
                    s.connect(("127.0.0.1", 9222))
                    s.close()
                    return True
                except OSError:
                    continue
    return False


def cmd_farm(a=None, cfg=None, dry=False):
    """Create one fresh z.ai account (relay mailbox + captcha + verify link),
    add it to the bridge (accounts.txt + key) and mint its ZCode JWT."""
    cfg = cfg or _farm_cfg()
    accts = accounts()
    # max_accounts <= 0 means UNLIMITED: keep farming as long as we are not
    # blocked. The real limiter is the per-account quota, not a hard cap.
    cap = int(cfg.get("max_accounts", 4))
    if cap > 0 and len(accts) >= cap:
        event("farm", "skipped: max_accounts=%d reached" % cap)
        return None
    st = load_json(p("farm_state.json"), {})
    # cooldown applies in full after a SUCCESSFUL farm; a failed run only
    # pauses briefly (30 min) so a flaky signup can be retried the same day.
    cool = float(cfg.get("cooldown_hours", 20)) * 3600
    if st.get("status") != "done":
        cool = min(cool, 1800)
    if not dry and time.time() - float(st.get("ts") or 0) < cool:
        left = (cool - (time.time() - float(st.get("ts") or 0))) / 60
        event("farm", "skipped: cooldown (%.0fmin left, last=%s)" % (left, st.get("status")))
        return None
    if dry:
        event("farm", "dry-run ok (accounts=%d, would signup+add+mint)" % len(accts))
        return None
    if not _ensure_cdp_chrome():
        event("farm_err", "no CDP Chrome on :9222 and no chrome binary found")
        return None
    write(p("farm_state.json"), json.dumps({"ts": time.time(), "status": "running"}))
    out = os.path.join(os.path.expanduser("~/.config/auto-freecf"), "farm_signup.json")
    if os.path.exists(out):
        os.remove(out)
    # headed: the chat.z.ai signup captcha fails too often under headless
    # camoufox; the desktop session has DISPLAY.
    rc = subprocess.call([PY_BROWSER, os.path.join(SCRIPTS, "zai_signup.py"),
                          "--attempts", "3", "--out-json", out])
    rec = load_json(out, {})
    email, pw = rec.get("email"), rec.get("password")
    # Trust the RECORD over the exit code: zai_signup writes its JSON before
    # returning, and a headed browser teardown can still kill the process with
    # rc=120 ("failed to flush stdout") AFTER a successful signup. Gating on
    # rc!=0 discarded valid accounts (e.g. rw5gutz7gn@ at 01:06, status=ok).
    if rec.get("status") == "ok" and email:
        if rc != 0:
            event("farm", "signup ok despite rc=%s (%s)" % (rc, email))
    else:
        event("farm_err", "signup rc=%s status=%s error=%s" % (rc, rec.get("status"), str(rec.get("error"))[:60]))
        write(p("farm_state.json"), json.dumps({"ts": time.time(), "status": "failed"}))
        return None
    event("farm", "signup ok: %s" % email)
    cmd_account_add(argparse.Namespace(email=email, pw=pw, no_mint=True))
    # from here an account exists -> full cooldown applies even if mint/plan flake
    write(p("farm_state.json"), json.dumps({"ts": time.time(), "status": "done"}))
    cmd_mint(argparse.Namespace(email=email, all=False, password=pw))
    inject_9router()
    # Start Plan auto-grants at sign-in; poll until it shows as active
    for _ in range(6):
        try:
            active = [x["plan_id"] for x in billing(email).get("plans", [])
                      if x.get("status") == "active"]
            if active:
                event("farm", "plan active for %s (%s)" % (email, ",".join(active)))
                return email
        except Exception as e:
            event("farm_err", "billing poll: %s" % str(e)[:80])
        time.sleep(20)
    event("farm", "%s added but plan not active yet (run: zcode.py quota --live)" % email)
    return email


def _autofarm_tick(cache):
    cfg = _farm_cfg()
    if not cfg.get("enabled"):
        return
    accts = [e for e, _ in accounts()]
    if not accts:
        return

    def spent(email):
        """True if this account has an active plan but no GLM-5.3 quota left."""
        bs = (cache.get(email) or {}).get("buckets", [])
        glm = [b for b in bs if "model:glm-5.3" in (b.get("capabilities") or [])]
        return bool(glm) and all(b.get("remaining_units", 0) <= 0 for b in glm)

    def usable(email):
        """True if the account has a live plan with quota left (cache-based)."""
        bs = (cache.get(email) or {}).get("buckets", [])
        if not bs:
            return False
        glm = [b for b in bs if "model:glm-5.3" in (b.get("capabilities") or [])]
        return bool(glm) and any(b.get("remaining_units", 0) > 0 for b in glm)

    # min_active: keep at least this many usable accounts in the pool. Farm when
    # the usable count drops to/below it. Default 3 (see _farm_cfg); set 0 to
    # only ever farm when EVERYTHING is exhausted (the old behaviour).
    min_active = int(cfg.get("min_active", 3))
    usable_n = sum(1 for e in accts if usable(e))
    if usable_n > min_active:
        return
    if usable_n == 0 and not any(spent(e) for e in accts):
        # nothing usable but nothing is spent either -> billing data is stale
        # (no tick has filled the cache yet); don't farm blind.
        return
    event("autofarm", "usable=%d <= min_active=%d -> farming" % (usable_n, min_active))
    cmd_farm(cfg=cfg)


def cmd_autofarm(a):
    if a.action == "show":
        print(json.dumps(_farm_cfg(), indent=1))
        return
    _cfg_toggle(lambda c: {**c, "autofarm": {**_farm_cfg(), "enabled": a.action == "on"}})
    event("autofarm", "autofarm %s" % ("ON" if a.action == "on" else "OFF"))
    print(json.dumps(_farm_cfg(), indent=1))


def cmd_watch(a):
    event("watch", "started (interval=%ss)" % a.interval)
    while True:
        try:
            _watch_tick()
        except Exception as e:
            event("watch_err", str(e)[:120])
        if a.once:
            return
        time.sleep(a.interval)


def cmd_serve(a):
    os.execv(sys.executable, [sys.executable, SERVER, "--host", a.host, "--port", str(a.port)])


def main():
    ap = argparse.ArgumentParser(prog="zcode.py", description="ZCode Start Plan bridge CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("account"); sa = s.add_subparsers(dest="action", required=True)
    x = sa.add_parser("add"); x.add_argument("email"); x.add_argument("pw"); x.add_argument("--no-mint", action="store_true")
    sa.add_parser("list")
    x = sa.add_parser("rm"); x.add_argument("email")

    s = sub.add_parser("mint"); s.add_argument("email", nargs="?"); s.add_argument("--all", action="store_true"); s.add_argument("--password")
    s = sub.add_parser("remint")
    s.add_argument("email", nargs="?", help="Specific email to re-mint (optional)")
    s.add_argument("--limit", type=int, default=5, help="Max accounts to re-mint (default: 5)")
    s.add_argument("--max-age-hours", type=float, default=24.0, help="Skip accounts with JWTs newer than this (default: 24h)")
    s.add_argument("--delay", type=float, default=3.0, help="Delay between accounts in seconds (default: 3s)")
    s.add_argument("--force", action="store_true", help="Force re-mint regardless of JWT age")


    s = sub.add_parser("persona")
    s.add_argument("action", choices=["on", "off", "show"])
    s.add_argument("models", nargs="*",
                   help="whitelist: only these models/aliases get the persona (empty = ALL)")

    s = sub.add_parser("models"); s.add_argument("action", choices=["add", "list", "rm"])
    s.add_argument("alias", nargs="?"); s.add_argument("model", nargs="?"); s.add_argument("--persona", action="store_true")

    s = sub.add_parser("quota"); s.add_argument("--live", action="store_true")

    s = sub.add_parser("watch"); s.add_argument("--once", action="store_true"); s.add_argument("--interval", type=int, default=60)

    s = sub.add_parser("promo")
    s.add_argument("action", choices=["check", "claim"], nargs="?", default="check",
                   help="check: list claimable plan coupons; claim: claim them now")

    s = sub.add_parser("farm")
    s.add_argument("--dry-run", action="store_true",
                   help="verify guards (cooldown/max) without creating an account")

    s = sub.add_parser("autofarm")
    s.add_argument("action", choices=["on", "off", "show"],
                   help="watcher auto-creates an account when ALL accounts hit 0%% GLM-5.3")

    s = sub.add_parser("serve"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8787)

    a = ap.parse_args()
    os.makedirs(p("jwt"), exist_ok=True)
    if a.cmd == "account":
        {"add": cmd_account_add, "list": cmd_account_list, "rm": cmd_account_rm}.get(a.action, cmd_account_list)(a)
    elif a.cmd == "mint":
        cmd_mint(a)
    elif a.cmd == "remint":
        cmd_remint(a)
    elif a.cmd == "persona":
        cmd_persona(a)
    elif a.cmd == "models":
        cmd_models(a)
    elif a.cmd == "quota":
        cmd_quota(a)
    elif a.cmd == "watch":
        cmd_watch(a)
    elif a.cmd == "promo":
        cmd_promo(a)
    elif a.cmd == "farm":
        cmd_farm(dry=a.dry_run)
    elif a.cmd == "autofarm":
        cmd_autofarm(a)
    elif a.cmd == "serve":
        cmd_serve(a)


if __name__ == "__main__":
    main()
