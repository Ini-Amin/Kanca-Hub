"""doctor — health and diagnostics command for kancahub."""
from __future__ import annotations


def cmd_doctor(_a) -> int:
    import kancahub
    kancahub.banner("KancaHub doctor")
    py = kancahub.pick_python()
    print(f"  python : {py}\n")

    files = [
        ("Auto-FreeCF dir", kancahub.AUTO_FREECF.exists()),
        ("  signup main.py", (kancahub.AUTO_FREECF / "signup_from_scratch" / "main.py").exists()),
        ("  cli.js (moycf)", (kancahub.AUTO_FREECF / "cli.js").exists()),
        ("  web_ui.py", (kancahub.AUTO_FREECF / "web_ui.py").exists()),
        ("  pipeline.py", (kancahub.AUTO_FREECF / "scripts" / "pipeline.py").exists()),
        ("  inject_9router.py", (kancahub.AUTO_FREECF / "scripts" / "inject_9router.py").exists()),
        ("  residential_gateway.py", (kancahub.AUTO_FREECF / "scripts" / "residential_gateway.py").exists()),
        ("  proxy_gateway.py", (kancahub.AUTO_FREECF / "scripts" / "proxy_gateway.py").exists()),
        ("  cf_workerai_manager.py", (kancahub.AUTO_FREECF / "cf_workerai_manager.py").exists()),
        ("PetaniProxy dir", kancahub.PETANI.exists()),
        ("  main.py", (kancahub.PETANI / "main.py").exists()),
        ("  core/server.py", (kancahub.PETANI / "core" / "server.py").exists()),
        ("K-12 script.py", (kancahub.K12_DIR / "script.py").exists()),
        ("K-12 auto_k12_flow.py", (kancahub.K12_DIR / "auto_k12_flow.py").exists()),
        ("K-12 gen doc bridge", (kancahub.K12_ROOT / "generate_teacher_doc.py").exists()),
        ("Yowes dir", kancahub.YOWES.exists()),
        ("  countries/", (kancahub.YOWES / "countries").exists()),
        ("  main_gui.py", (kancahub.YOWES / "main_gui.py").exists()),
        ("harbor (TokenHarbor)", (kancahub.HARBOR / "tools" / "tokenharbor").exists()),
        ("grok-register", (kancahub.GROK_REG / "grok_register_ttk.py").exists()),
        ("  registration_flow", (kancahub.GROK_REG / "registration_flow.py").exists()),
        ("turnstilePatch", (kancahub.PETANI / "core" / "turnstilePatch").exists()),
        ("9Router DB", kancahub.NINE_ROUTER_DB.exists()),
        ("Secrets .env", (kancahub.HOME / ".config" / "auto-freecf" / ".env").exists()),
        ("Proxies pool", (kancahub.AUTO_FREECF / "signup_from_scratch" / "proxies.txt").exists()),
        ("WARP config", (kancahub.PETANI / "output" / "warp" / "warp.conf").exists()),
        ("Region profile", (kancahub.HOME / ".config" / "auto-freecf" / "region.json").exists()),
    ]
    for label, ok in files:
        print(f"  {'✅' if ok else '❌'} {label}")

    print(kancahub.col("bold", "\n  Python modules:"))
    for mod in ("nodriver", "patchright", "httpx", "requests", "curl_cffi",
                "cloudscraper", "DrissionPage", "speech_recognition", "pydub",
                "PIL", "mcp", "customtkinter", "rich", "tomllib", "fastapi"):
        r = kancahub.subprocess.run([py, "-c", f"import {mod}"], capture_output=True)
        print(f"  {'✅' if r.returncode == 0 else '❌'} {mod}")

    print(kancahub.col("bold", "\n  Binaries:"))
    for b in ("google-chrome", "ffmpeg", "git", "adb", "wg", "sing-box"):
        print(f"  {'✅' if kancahub.shutil.which(b) else '➖'} {b}")

    petani_gw = kancahub._gateway_alive(kancahub.GATEWAY_DEFAULT)
    print(f"\n  {'✅' if petani_gw else '➖'} PetaniProxy gateway :8888 {'(running)' if petani_gw else '(not running)'}")

    # ── proxy backend ─────────────────────────────────────────────
    native_lib = kancahub.AUTO_FREECF / "scripts" / "proxy_lib.py"
    native_gateway = kancahub.AUTO_FREECF / "scripts" / "proxy_gateway.py"
    native_ok = native_lib.exists() and native_gateway.exists()
    petani_ok = (kancahub.PETANI / "main.py").exists()
    backend = ("native (scripts/proxy_lib.py)" if native_ok else
               "petani (petani-proxy/main.py)" if petani_ok else "MISSING")
    print(kancahub.col("bold", f"\n  Proxy backend: {backend}"))
    print(f"  {'✅' if native_ok else '❌'} native proxy_lib.py ({native_lib})")
    print(f"  {'✅' if native_gateway.exists() else '❌'} native proxy_gateway.py ({native_gateway})")
    print(f"  {'✅' if petani_ok else '➖'} PetaniProxy legacy fallback ({kancahub.PETANI})")
    print(kancahub.col("dim", "    native commands: kancahub proxy nharvest | nhealth | ngateway"))

    # WARP
    try:
        import subprocess as _sp
        wm = kancahub.AUTO_FREECF / "scripts" / "warp_manager.py"
        if wm.exists():
            r = _sp.run([py, str(wm), "status"], capture_output=True, text=True)
            up = "🟢 up" in r.stdout
            print(f"  {'✅' if up else '➖'} WARP tunnel {'(up)' if up else '(down)'}")
    except Exception:
        pass

    # ── Camoufox (isolated venv + fetched browser binary) ──────────
    print(kancahub.col("bold", "\n  Camoufox:"))
    cf_py = kancahub.CAMOUFOX_PY
    cf_py_ok = cf_py.exists() and kancahub.os.access(str(cf_py), kancahub.os.X_OK)
    print(f"  {'✅' if cf_py_ok else '❌'} camoufox venv python ({cf_py})")
    if cf_py_ok:
        r = kancahub.subprocess.run(
            [str(cf_py), "-c", "import camoufox, playwright"],
            capture_output=True, text=True,
        )
        imp_ok = r.returncode == 0
        print(f"  {'✅' if imp_ok else '❌'} import camoufox + playwright")
        if not imp_ok:
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            if tail:
                print(kancahub.col("dim", f"      {tail[-1][:100]}"))
    else:
        print("  ❌ import camoufox + playwright (venv python missing)")
    cf_cache = kancahub.CAMOUFOX_CACHE
    try:
        cf_fetched = cf_cache.exists() and any(cf_cache.iterdir())
    except OSError:
        cf_fetched = False
    if cf_fetched:
        n_entries = len(list(cf_cache.iterdir()))
        print(f"  ✅ browser binary fetched (~/.cache/camoufox, {n_entries} entries)")
    else:
        print("  ❌ browser binary fetched (~/.cache/camoufox empty — run: camoufox fetch)")

    # ── Tempik mail worker (HTTP reachability) ─────────────────────
    print(kancahub.col("bold", "\n  Tempik:"))
    tempik_code = kancahub._http_status(kancahub.TEMPIK_URL, timeout=6.0)
    if tempik_code == 200:
        print(f"  ✅ GET {kancahub.TEMPIK_URL} -> 200")
    elif tempik_code is None:
        print(f"  ❌ GET {kancahub.TEMPIK_URL} -> unreachable")
    else:
        print(f"  ❌ GET {kancahub.TEMPIK_URL} -> HTTP {tempik_code} (expected 200)")

    # ── School mailbox (M365 / BINUS) ──────────────────────────────
    print(kancahub.col("bold", "\n  School mailbox:"))
    env_file = kancahub.ENV_FILE
    school_email = kancahub._env_get("SCHOOL_EMAIL", env_file)
    print(f"  {'✅' if school_email else '❌'} SCHOOL_EMAIL "
          f"{school_email if school_email else '(not set in ' + str(env_file) + ')'}")
    school_pw = bool(kancahub._env_get("SCHOOL_MAIL_PASSWORD", env_file))
    print(f"  {'✅' if school_pw else '❌'} SCHOOL_MAIL_PASSWORD set")
    prof = kancahub.SCHOOL_PROFILE
    prof_ok = prof.is_dir() and any(prof.iterdir())
    if prof_ok:
        print(f"  ✅ school profile dir ({prof.name}, logged-in session cached)")
    elif prof.is_dir():
        print(f"  ➖ school profile dir ({prof.name} exists but empty — run: kancahub mail test)")
    else:
        print(f"  ❌ school profile dir ({prof} missing)")

    # ── Outputs / state files ──────────────────────────────────────
    print(kancahub.col("bold", "\n  Outputs & state:"))
    state = [
        ("results.json (CF signup)", kancahub.AUTO_FREECF / "results.json"),
        ("results.json (signup_from_scratch)", kancahub.AUTO_FREECF / "signup_from_scratch" / "results.json"),
        ("github_accounts.json", kancahub.AUTO_FREECF / "github_accounts.json"),
        ("k12_sessions.json", kancahub.K12_DIR / "k12_sessions.json"),
        ("region.json", kancahub.HOME / ".config" / "auto-freecf" / "region.json"),
    ]
    for label, path in state:
        if path.exists():
            size = path.stat().st_size
            extra = ""
            if path.name == "github_accounts.json" or path.name == "results.json":
                try:
                    data = kancahub.json.loads(path.read_text())
                    n = len(data) if isinstance(data, list) else len(data.get("accounts", [])) if isinstance(data, dict) else 0
                    extra = f", {n} entries"
                except Exception:
                    extra = ""
            print(f"  ✅ {label} ({size} bytes{extra})")
        else:
            print(f"  ➖ {label} (absent)")

    proxies = kancahub.AUTO_FREECF / "signup_from_scratch" / "proxies.txt"
    if proxies.exists():
        n_prox = kancahub._count_lines(proxies)
        print(f"  {'✅' if n_prox else '➖'} proxies.txt ({n_prox} proxies)")
    else:
        print("  ❌ proxies.txt (missing)")

    # ── 9Router connection inventory ───────────────────────────────
    print(kancahub.col("bold", "\n  9Router connections:"))
    if kancahub.NINE_ROUTER_DB.exists():
        import sqlite3
        try:
            con = sqlite3.connect(f"file:{kancahub.NINE_ROUTER_DB}?mode=ro", uri=True)
            def _cnt(where: str, params: tuple = ()) -> tuple[int, int]:
                row = con.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(isActive), 0) FROM providerConnections {where}",
                    params,
                ).fetchone()
                return int(row[0]), int(row[1])
            cf_t, cf_a = _cnt("WHERE provider = ?", ("cloudflare-ai",))
            cx_t, cx_a = _cnt("WHERE provider = ?", ("codex",))
            thk_t, thk_a = _cnt("WHERE provider = ?", (kancahub.THK_NODE_ID,))
            xai_t, xai_a = _cnt("WHERE provider = ?", ("xai",))
            print(f"  {'✅' if cf_t else '➖'} cloudflare-ai : {cf_t} total, {cf_a} active  (farm: Auto-FreeCF)")
            print(f"  {'✅' if cx_t else '➖'} codex (ChatGPT): {cx_t} total, {cx_a} active  (YOUR account)")
            print(f"  {'✅' if thk_t else '➖'} TokenHarbor (thk): {thk_t} total, {thk_a} active  (farm: harbor)")
            print(f"  {'✅' if xai_t else '➖'} xai (Grok OAuth): {xai_t} total, {xai_a} active  (YOUR account)")
            print(kancahub.col("dim", "  (codex/xai are the user's own accounts — not farm output)"))
            con.close()
        except Exception as e:  # noqa: BLE001
            print(kancahub.col("red", f"  ❌ could not read DB: {e}"))
    else:
        print(kancahub.col("red", f"  ❌ 9Router DB not found: {kancahub.NINE_ROUTER_DB}"))

    # ── Scripts built since the last doctor pass ───────────────────
    print(kancahub.col("bold", "\n  Scripts:"))
    wanted = [
        "proxy_gateway.py", "grok_driver.py", "grok_9router.py", "github_farm.py",
        "sheerid_link_finder.py", "school_mail_browser.py", "gmail_creator.py",
        "harbor_config.py", "camoufox_helpers.py",
    ]
    for name in wanted:
        p = kancahub.AUTO_FREECF / "scripts" / name
        print(f"  {'✅' if p.exists() else '❌'} {name}")

    # ── Egress verdict: what is the CURRENT internet exit good for? ──
    print(kancahub.col("bold", "\n  Egress verdict (what this connection can do):"))
    verdict = kancahub._egress_verdict()
    print(verdict)

    print()
    return 0
