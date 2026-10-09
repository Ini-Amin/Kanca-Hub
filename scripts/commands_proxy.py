"""commands_proxy — proxy management, harvesting, and diagnostics for kancahub."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request


def _probe_http(url: str, timeout: float = 10.0) -> int:
    """Return the HTTP status for `url` (0 on network error)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return 0


def _egress_verdict() -> str:
    """Probe GitHub signup + TokenHarbor through the current egress and print a
    plain-English verdict with an actionable next step. Read-only, best-effort."""
    import kancahub
    try:
        my_ip = urllib.request.urlopen("https://api.ipify.org", timeout=8).read().decode().strip()
    except Exception:  # noqa: BLE001
        my_ip = "?"

    ip_info = {}
    try:
        import json as _json
        raw = urllib.request.urlopen(f"http://ip-api.com/json/{my_ip}?fields=isp,org,as,mobile,proxy,hosting,country", timeout=8).read().decode()
        ip_info = _json.loads(raw)
    except Exception:  # noqa: BLE001
        ip_info = {}

    kind = "mobile" if ip_info.get("mobile") else ("datacenter/hosting" if ip_info.get("hosting") else "residential/ISP")
    gh = kancahub._probe_http("https://github.com/signup")
    thk = kancahub._probe_http("https://tokenharbor.ai/")

    lines = []
    lines.append(f"  exit IP   : {my_ip}  [{ip_info.get('isp', '?')}]  ({kind})")
    lines.append(f"  github    : signup -> HTTP {gh}   {'✅ OK' if gh and gh < 400 else '❌ blocked'}")
    lines.append(f"  tokenharbor:        -> HTTP {thk}  {'✅ reachable' if thk and thk < 400 else '❌ blocked'}")

    if gh == 0 or thk == 0:
        lines.append(kancahub.col("yellow", "  ⚠ Network flaky/unreachable — check your connection."))
    elif gh >= 400 and kind != "mobile":
        lines.append(kancahub.col("cyan", "  → GitHub blocked. Best free fix: `kancahub mobile rotate` on a TETHERED phone,"))
        lines.append(kancahub.col("cyan", "    then `kancahub github farm --no-proxy`. (Datacenter/WARP IPs are blocked too.)"))
    elif kind == "mobile":
        lines.append(kancahub.col("cyan", "  → Mobile IP detected — the strongest free egress. `--proxy none` uses it directly."))
    else:
        lines.append(kancahub.col("green", "  → Egress looks usable."))
    return "\n".join(lines)


def cmd_proxy(a) -> int:
    import kancahub
    py = kancahub.pick_python()
    sub = a.proxy_cmd

    # ── stop a background gateway/daemon we started ──
    if sub == "stop":
        rc = kancahub._stop_background(getattr(a, "name", None) or "gateway")
        kancahub._stop_background("daemon")
        return rc

    # ── proof of masking (real IP vs gateway) ──
    if sub == "verify":
        v = kancahub.AUTO_FREECF / "scripts" / "proxy_verify.py"
        cmd = [py, str(v)]
        if getattr(a, "proxy", None):
            cmd += ["--proxy", a.proxy]
        if getattr(a, "pool", None):
            cmd += ["--pool", a.pool]
        if getattr(a, "gateway_out", None):
            cmd += ["--gateway", a.gateway_out]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    # ── one-command guided proxy mode (PetaniProxy power, one question) ──
    if sub == "start":
        return kancahub._proxy_start(a, py)

    # ── NATIVE backend (scripts/proxy_lib.py): self-contained, no PetaniProxy TUI ──
    if sub == "nharvest":
        return kancahub._proxy_native_harvest(a, py)
    if sub == "nhealth":
        return kancahub._proxy_native_health(a, py)
    if sub == "ngateway":
        return kancahub._proxy_native_gateway(a, py)

    petani = kancahub.PETANI / "main.py"
    if not petani.exists():
        print(kancahub.col("red", f"✗ PetaniProxy not found at {kancahub.PETANI}"))
        return 1

    def petani_cmd(extra: list[str]) -> int:
        return kancahub.run([py, str(petani)] + [str(x) for x in extra], cwd=kancahub.PETANI)

    if sub == "harvest":
        cmd = ["--target", a.target, "--max", a.max, "--workers", a.workers,
               "--timeout", a.timeout]
        if a.protocol:
            cmd += ["--protocol", a.protocol]
        if a.country:
            cmd += ["--country", a.country]
        if a.anonymity:
            cmd += ["--anonymity", a.anonymity]
        if a.target_url:
            cmd += ["--target-url", a.target_url]
        if a.loop:
            cmd += ["--loop", a.loop]
        if a.sync_9router:
            cmd += ["--sync-9router", a.sync_9router]
        if a.serve:
            cmd += ["--serve", a.serve]
        return petani_cmd(cmd)

    if sub == "fast":
        cmd = ["--fast-harvest", a.target, "--max-latency", a.max_latency]
        return petani_cmd(cmd)

    if sub == "serve" or sub == "gateway":
        if getattr(a, "background", False):
            print(kancahub.col("yellow", f"Starting rotating gateway on :{a.port} in the BACKGROUND…"))
            return kancahub._spawn_background(
                [py, str(petani), "--serve", str(a.port), "--target", str(a.target)],
                cwd=kancahub.PETANI, name=f"gateway{a.port}", ready_port=int(a.port),
            )
        print(kancahub.col("yellow", f"Rotating gateway + REST API + dashboard on :{a.port}"))
        print(kancahub.col("dim", f"  dashboard: http://127.0.0.1:{a.port}/dashboard"))
        print(kancahub.col("dim", f"  PAC:       http://127.0.0.1:{a.port}/proxy.pac"))
        print(kancahub.col("dim", "  (tip: add -b/--background to detach and keep using the CLI)"))
        return petani_cmd(["--serve", a.port, "--target", a.target])

    if sub == "daemon":
        if getattr(a, "background", False):
            print(kancahub.col("yellow", "Starting 24/7 auto-healing gateway on :8888 in the BACKGROUND…"))
            return kancahub._spawn_background([py, str(petani), "--daemon-gateway"],
                                             cwd=kancahub.PETANI, name="daemon", ready_port=8888)
        print(kancahub.col("yellow", "24/7 auto-healing gateway on :8888 (Ctrl-C to stop)"))
        print(kancahub.col("dim", "  (tip: add -b/--background to detach and keep using the CLI)"))
        return petani_cmd(["--daemon-gateway"])

    if sub == "residential":
        cmd = ["--webshare", str(a.accounts)]
        if a.headless:
            cmd.append("--headless")
        rc = petani_cmd(cmd)
        try:
            from proxy_sync import sync_now
            sync_now(quiet=False)
        except Exception:
            pass
        return rc

    if sub == "ripool":
        # RapidProxy/SwiftProxy free-trial signup + residential harvest. Drives a
        # browser (camoufox), so it must run under the camoufox venv.
        py_camo = kancahub.pick_python(camoufox=True)
        tool = kancahub.AUTO_FREECF / "scripts" / "residential_proxy_signup.py"
        if not tool.exists():
            print(kancahub.col("red", f"✗ residential_proxy_signup.py not found at {tool}"))
            return 1
        cmd = [py_camo, str(tool), a.vendor, "-n", str(a.accounts)]
        if getattr(a, "headless", False):
            cmd.append("--headless")
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    if sub == "sync":
        from proxy_sync import sync_now
        return sync_now(source=getattr(a, "src", None), validate_live=getattr(a, "validate", False))

    if sub == "warp":
        return petani_cmd(["--warp"])

    if sub == "grok":
        cmd = ["--grok-farm", a.accounts]
        if a.headless:
            cmd += ["--headless"]
        if a.mail_provider:
            cmd += ["--mail-provider", a.mail_provider]
        return petani_cmd(cmd)

    if sub == "pipeline":
        cmd = ["--pipeline", a.accounts]
        if a.headless:
            cmd += ["--headless"]
        return petani_cmd(cmd)

    if sub == "sync9r":
        path = a.db or "auto"
        print(kancahub.col("cyan", f"Syncing harvested proxies into 9Router DB ({path})"))
        return petani_cmd(["--target", a.target, "--sync-9router", path])

    if sub == "export":
        return kancahub._proxy_export(a)

    if sub == "test":
        return kancahub._proxy_test_pool(a)

    if sub == "stats":
        return kancahub._proxy_stats(a)

    if sub == "api":
        return kancahub._proxy_api(a)

    if sub == "res-gateway":
        gw = kancahub.AUTO_FREECF / "scripts" / "proxy_gateway.py"
        cmd = [py, str(gw), "--pool", a.pool, "--port", str(a.port)]
        if getattr(a, "scheme", None):
            cmd += ["--scheme", a.scheme]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    print(kancahub.col("red", "✗ unknown proxy command"))
    return 1


def _proxy_start(a, py) -> int:
    """One-command guided proxy: pick a mode, it starts and prints one line.

    Mirrors PetaniProxy's modes with a beginner-proof prompt and a single
    result line ('Proxy ready: http://127.0.0.1:8888').
    """
    import kancahub
    petani = kancahub.PETANI / "main.py"
    if not petani.exists():
        print(kancahub.col("red", f"✗ PetaniProxy not found at {kancahub.PETANI}"))
        return 1

    mode = getattr(a, "mode", None)

    if not mode:
        print()
        print(kancahub.col("bold", "  Choose proxy mode:"))
        print(f"   [{kancahub.C['green']}1{kancahub.C['reset']}] {kancahub.col('bold', 'WARP')}          — clean Cloudflare egress, zero captcha, unlimited (best for signups that get blocked)")
        print(f"   [{kancahub.C['green']}2{kancahub.C['reset']}] {kancahub.col('bold', 'Gateway')}       — rotate thousands of free public proxies on 127.0.0.1:8888 (auto-refill, dashboard)")
        print(f"   [{kancahub.C['green']}3{kancahub.C['reset']}] {kancahub.col('bold', 'Residential')}   — hunt real residential IPs via Webshare (best vs Cloudflare/Turnstile; may need Setup)")
        print(f"   [{kancahub.C['green']}4{kancahub.C['reset']}] {kancahub.col('bold', 'Daemon')}        — 24/7 auto-healing gateway on :8888 (leave running in background)")
        print()
        try:
            mode = input(f"  {kancahub.col('bold','Select mode [1-4]')} {kancahub.col('dim','(default: 2)')}: ").strip() or "2"
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

    mode = str(mode).strip().lower()
    if mode in ("1", "warp", "c"):
        wm = kancahub.AUTO_FREECF / "scripts" / "warp_manager.py"
        print(kancahub.col("cyan", "\n  Starting Cloudflare WARP (clean egress)…\n"))
        # ponytail: shells out to petani-proxy main.py -C, then reports status with warp_manager.py
        rc = kancahub.run([py, str(petani), "-C"], cwd=kancahub.PETANI)
        if wm.exists():
            kancahub.run([py, str(wm), "up"])
            kancahub.run([py, str(wm), "status"])
        print(kancahub.col("green", "\n  Proxy ready: WARP tunnel active"))
        print(kancahub.col("dim", "  (turn off later with: kancahub warp down)\n"))
        return rc

    if mode in ("2", "gateway"):
        port = int(getattr(a, "port", 8888) or 8888)
        target = str(getattr(a, "target", 30) or 30)
        # Background by default here: the guided flow must NOT block the terminal.
        print(kancahub.col("cyan", f"\n  Starting rotating gateway on :{port} in the background…\n"))
        rc = kancahub._spawn_background([py, str(petani), "--serve", str(port), "--target", target],
                                        cwd=kancahub.PETANI, name=f"gateway{port}", ready_port=port)
        if rc == 0:
            print(kancahub.col("green", f"  Proxy ready: http://127.0.0.1:{port}"))
            print(kancahub.col("cyan",  f"  Dashboard:   http://127.0.0.1:{port}/dashboard"))
            print(kancahub.col("dim",   f"  PAC URL:     http://127.0.0.1:{port}/proxy.pac"))
            print(kancahub.col("dim",   "  stop: kancahub proxy stop\n"))
        return rc

    if mode in ("3", "residential", "w"):
        accs = str(getattr(a, "accounts", 1) or 1)
        print(kancahub.col("cyan", f"\n  Hunting real residential IPs via Webshare (target {accs} accounts)…\n"))
        # ponytail: shells out to petani-proxy main.py -W 1
        return kancahub.run([py, str(petani), "-W", accs], cwd=kancahub.PETANI)

    if mode in ("4", "daemon", "g"):
        # Background the daemon so the CLI is never left stuck.
        print(kancahub.col("cyan", "\n  Starting 24/7 auto-healing gateway on :8888 in the background…\n"))
        rc = kancahub._spawn_background([py, str(petani), "--daemon-gateway"],
                                        cwd=kancahub.PETANI, name="daemon", ready_port=8888)
        if rc == 0:
            print(kancahub.col("green", "  Proxy ready: http://127.0.0.1:8888"))
            print(kancahub.col("cyan",  "  Dashboard:   http://127.0.0.1:8888/dashboard"))
            print(kancahub.col("dim",   "  stop: kancahub proxy stop\n"))
        return rc

    print(kancahub.col("red", f"✗ unknown mode '{mode}'. Choose 1 (WARP), 2 (Gateway), 3 (Residential), or 4 (Daemon)."))
    return 1


def _proxy_export(a) -> int:
    import kancahub
    src = kancahub.PETANI / "output" / "live_elite.txt"
    if not src.exists():
        src = kancahub.PETANI / "output" / "live_all.txt"
    if not src.exists():
        print(kancahub.col("red", "✗ no harvested proxies — run `kancahub proxy harvest`"))
        return 1
    dst = kancahub.AUTO_FREECF / "signup_from_scratch" / "proxies.txt"
    lines = [l.strip() for l in src.read_text().splitlines() if l.strip()]
    if a.to_pool:
        dst.write_text("\n".join(lines) + "\n")
        print(kancahub.col("green", f"✓ {len(lines)} proxies -> {dst}"))
    else:
        for l in lines[:a.limit]:
            print(" ", l)
        print(kancahub.col("dim", f"({len(lines)} total; use --to-pool to write the Auto-FreeCF pool)"))
    return 0


def _proxy_test_pool(a) -> int:
    import kancahub
    dst = Path(a.pool) if a.pool else (kancahub.AUTO_FREECF / "signup_from_scratch" / "proxies.txt")
    if not dst.exists():
        print(kancahub.col("red", f"✗ pool not found: {dst}"))
        return 1
    lines = [l.strip() for l in dst.read_text().splitlines() if l.strip()]
    ok = 0
    for p in lines:
        ip = subprocess.run(["curl", "-s", "-m", "10", "-x", p, "https://api.ipify.org"],
                            capture_output=True, text=True).stdout.strip()
        print(f"  {'✅' if ip else '❌'} {ip or 'dead'}")
        ok += bool(ip)
    print(kancahub.col("green", f"\n  {ok}/{len(lines)} live"))
    return 0


def _proxy_stats(a) -> int:
    import kancahub
    gw = a.gateway
    kancahub.banner(f"Gateway stats ({gw})")
    st = kancahub._gw_get("/api/status", gw)
    if st:
        print(json.dumps(st, indent=2)[:2000])
    else:
        print(kancahub.col("yellow", "Gateway not reachable. Start it: kancahub proxy daemon"))
    return 0


def _proxy_api(a) -> int:
    import kancahub
    gw = a.gateway
    path = a.path
    if not path.startswith("/"):
        path = "/" + path
    data = kancahub._gw_get(path, gw)
    if data is not None:
        print(json.dumps(data, indent=2)[:4000])
        return 0
    return 1


# ── native proxy backend (scripts/proxy_lib.py) ──────────────────

def _native_proxy_lib() -> Path:
    import kancahub
    return kancahub.AUTO_FREECF / "scripts" / "proxy_lib.py"


def _proxy_native_harvest(a, py: str) -> int:
    import kancahub
    lib = kancahub._native_proxy_lib()
    if not lib.exists():
        print(kancahub.col("red", f"✗ native proxy lib not found at {lib}"))
        return 1
    cmd = [py, str(lib), "harvest", "--target", str(a.target),
           "--protocol", a.protocol, "--timeout", str(a.timeout), "--workers", str(a.workers)]
    if a.out_txt:
        cmd += ["--out-txt", a.out_txt]
    if a.out_json:
        cmd += ["--out-json", a.out_json]
    print(kancahub.col("cyan", f"Native harvest ({a.protocol}) target={a.target}"))
    return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)


def _proxy_native_health(a, py: str) -> int:
    import kancahub
    lib = kancahub._native_proxy_lib()
    if not lib.exists():
        print(kancahub.col("red", f"✗ native proxy lib not found at {lib}"))
        return 1
    print(kancahub.col("cyan", f"Native pool health: {a.pool}"))
    return kancahub.run([py, str(lib), "health", str(a.pool)], cwd=kancahub.AUTO_FREECF)


def _proxy_native_gateway(a, py: str) -> int:
    import kancahub
    lib = kancahub._native_proxy_lib()
    if not lib.exists():
        print(kancahub.col("red", f"✗ native proxy lib not found at {lib}"))
        return 1
    cmd = [py, str(lib), "gateway", "--pool", str(a.pool), "--port", str(a.port),
           "--scheme", a.scheme]
    print(kancahub.col("cyan", f"Native rotating gateway on 127.0.0.1:{a.port} (Ctrl-C to stop)"))
    return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)
