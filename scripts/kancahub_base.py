"""kancahub_base — shared core for the kancahub CLI (STEP 1 of docs/refactor-kancahub.md).

Owns: colors/banner, interpreter + child-process helpers, the egress auto-wire,
session/background-job plumbing and the farm-ledger hook. kancahub.py re-exports
every name here, so `kancahub.<name>` keeps working — 12 test files patch names
on the kancahub module (H1/H2 in the refactor doc).

Mutable module globals (_AUTO_GATEWAYS, _egress_module, _egress_unavailable,
_LAST_RUN_OUT) live in EXACTLY ONE module: this one. Everything tests patch on
kancahub (run, _run_capture, _choose_egress, _load_egress, _stop_auto_gateways,
AUTO_FREECF, BACKGROUND_DIR, run_with_mobile_retry) is reached through `_kc()`
at call time, never captured at import — so a patch always lands.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import colorama
    colorama.init()  # the ONLY import-time side effect (H7)
except Exception:
    pass

# NOTE: no top-level `import kancahub` — kancahub imports this module, so that
# would be a circular import. Resolve it lazily at call time instead, which is
# also what makes mock patches on kancahub visible here (H1/H2/H4).
def _kc():
    import kancahub  # deferred: safe at call time, sees patches
    return kancahub

HOME = Path.home()
PETANI = HOME / "petani-proxy"
VENV_PY = HOME / ".local" / "share" / "auto-freecf" / "venv" / "bin" / "python"
GATEWAY_DEFAULT = "http://127.0.0.1:8888"

C = {
    "reset": "\x1b[0m", "bold": "\x1b[1m", "dim": "\x1b[2m",
    "cyan": "\x1b[36m", "green": "\x1b[32m", "yellow": "\x1b[33m",
    "red": "\x1b[31m", "magenta": "\x1b[35m",
}


def col(name: str, text: str) -> str:
    return f"{C.get(name, '')}{text}{C['reset']}"


def banner(title: str) -> None:
    print()
    print(col("cyan", "═" * 68))
    print(col("bold", f"  {title}"))
    print(col("cyan", "═" * 68))


def get_ascii_banner() -> str:
    cyan = C["cyan"]
    bold = C["bold"]
    reset = C["reset"]

    art = f"""{cyan}
 ██╗  ██╗ █████╗ ███╗   ██╗ ██████╗ █████╗ ██╗  ██╗██╗   ██╗██████╗ 
 ██║ ██╔╝██╔══██╗████╗  ██║██╔════╝██╔══██╗██║  ██║██║   ██║██╔══██╗
 █████╔╝ ███████║██╔██╗ ██║██║     ███████║███████║██║   ██║██████╔╝
 ██╔═██╗ ██╔══██║██║╚██╗██║██║     ██╔══██║██╔══██║██║   ██║██╔══██╗
 ██║  ██╗██║  ██║██║ ╚████║╚██████╗██║  ██║██║  ██║╚██████╔╝██████╔╝
 ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝{reset}"""
    box_top = f"{cyan} ╔══════════════════════════════════════════════════════════════════════╗{reset}"
    tagline = "one CLI for the whole account-farming toolkit"
    box_mid = f"{cyan} ║ {bold}{tagline.center(68)}{reset}{cyan} ║{reset}"
    box_bot = f"{cyan} ╚══════════════════════════════════════════════════════════════════════╝{reset}"
    return f"{art}\n{box_top}\n{box_mid}\n{box_bot}"


class KancaHubParser(argparse.ArgumentParser):
    """Custom parser displaying the big ASCII banner at the top of root --help."""

    def format_help(self) -> str:
        text = super().format_help()
        if self.prog == "kancahub":
            return get_ascii_banner() + "\n\n" + text
        return text


CAMOUFOX_VENV_PY = HOME / ".local" / "share" / "auto-freecf" / "camoufox-venv" / "bin" / "python"


def pick_python(camoufox: bool = False) -> str:
    """Interpreter for child scripts.

    camoufox=True returns the isolated python3.11 venv that has camoufox +
    playwright (scripts converted to Camoufox MUST run there; the main venv is
    python3.13 and has neither). Falls back to the main venv if absent.
    """
    if camoufox:
        cv = str(CAMOUFOX_VENV_PY)
        if CAMOUFOX_VENV_PY.exists() and os.access(cv, os.X_OK):
            return cv
        print(col("yellow", "⚠️ camoufox venv not found; falling back to the main venv "
                            "(camoufox/playwright may be missing)"))
    v = str(VENV_PY)
    if VENV_PY.exists() and os.access(v, os.X_OK):
        return v
    return shutil.which("python3") or sys.executable


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> int:
    print(col("dim", f"$ {' '.join(str(c) for c in cmd)}" + (f"   (cwd={cwd})" if cwd else "")))
    e = os.environ.copy()
    if env:
        e.update(env)
    try:
        return subprocess.call([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=e)
    except FileNotFoundError as ex:
        print(col("red", f"✗ {ex}"))
        return 127


def _gw_get(path: str, gateway: str = GATEWAY_DEFAULT) -> dict | None:
    try:
        with urllib.request.urlopen(gateway.rstrip("/") + path, timeout=6) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        print(col("red", f"✗ gateway {path}: {e}"))
        return None


def _gateway_alive(gateway: str = GATEWAY_DEFAULT) -> bool:
    try:
        with urllib.request.urlopen(gateway.rstrip("/") + "/api/status", timeout=4):
            return True
    except Exception:
        return False

def _http_status(url: str, timeout: float = 6.0) -> int | None:
    """GET a URL and return the HTTP status code, or None on any failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kancahub-doctor/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode() or 200
    except urllib.error.HTTPError as e:  # reachable, just not 2xx
        return e.code
    except Exception:
        return None

def _env_get(key: str, env_file: Path) -> str:
    """Read a single KEY=value out of a dotenv file (no interpolation)."""
    if not env_file.exists():
        return ""
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(key + "=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""

def _count_lines(p: Path) -> int:
    try:
        return sum(1 for ln in p.read_text().splitlines() if ln.strip())
    except OSError:
        return 0

# ══════════════════════════════════════════════════ egress auto-wire

SCRIPTS_DIR = Path(__file__).resolve().parent
PROXY_AUTO = "auto"
PROXY_NONE = "none"

# Probe target per farm group; only used by --proxy auto, resolved lazily at run
# time (never at import/parse time).
EGRESS_TARGETS = {
    "github": "https://github.com/signup",
    "grok": "https://accounts.x.ai/sign-up",
    "thk": "https://tokenharbor.ai/",
    "k12": "https://chatgpt.com/",
}

# Country routing policy per farm group (allow/exclude sets).
# thk excludes ID because TokenHarbor rejects Indonesian egress IPs.
EGRESS_COUNTRY = {
    "thk": {"exclude": {"ID"}},
}

_AUTO_GATEWAYS: list = []          # gateways spawned by auto_egress (kept alive)
_egress_module = None              # scripts/egress.py once imported
_egress_unavailable = False        # import failed -> keep legacy behavior

PROXY_HELP = (
    "egress mode (default: auto). auto = smart auto-wire (local gateway -> pool "
    "gateway -> WARP -> residential -> direct, verified per target); none = force "
    "direct; WARP = force the Cloudflare WARP tunnel; or an explicit proxy URL "
    "such as http://127.0.0.1:8888 (an explicit URL always wins over auto)"
)

def _looks_like_proxy(mode: str) -> bool:
    """True for an explicit proxy URL or host:port (never 'auto'/'none')."""
    m = (mode or "").strip()
    if "://" in m:
        return True
    host, _, port = m.rpartition(":")
    return bool(host) and port.isdigit()

def _load_egress():
    """Lazily import scripts/egress.py; returns the module or None on failure.

    Import is deliberately deferred: `--help` and parsing must never touch the
    network or import optional proxy machinery.
    """
    global _egress_module, _egress_unavailable
    if _egress_module is not None or _egress_unavailable:
        return _egress_module
    try:
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        import egress as _egress  # noqa: PLC0415 - deferred on purpose
        _egress_module = _egress
    except Exception as exc:  # noqa: BLE001 - defensive: never break farming
        _egress_unavailable = True
        print(col("yellow", f"⚠ egress auto-wire unavailable ({exc}); "
                            "farm commands keep their built-in proxy behavior"))
    return _egress_module

def _stop_auto_gateways() -> None:
    """Stop auto-wire gateways we spawned, so the CLI leaves nothing running."""
    mod = _kc()._load_egress()
    stop = getattr(mod, "stop_gateway", None) if mod is not None else None
    while _AUTO_GATEWAYS:
        proc = _AUTO_GATEWAYS.pop()
        if stop is not None:
            try:
                stop(proc)
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass

# Shown whenever the free proxy ladder fails / falls back to direct, so the user
# always sees the strongest (free) alternative: their own mobile/hotspot egress.
EGRESS_HINT = (
    "  Hint: GitHub/TokenHarbor block datacenter + WARP IPs. If this keeps failing,\n"
    "        tether your PHONE (mobile hotspot) and re-run with --proxy none — a real\n"
    "        carrier IP is the free path that GitHub accepts. See: kancahub doctor"
)

def print_egress_hint() -> None:
    print(col("cyan", EGRESS_HINT))


class EgressChoice:
    """Outcome of resolving a --proxy mode for one farm command."""

    __slots__ = ("proxy", "source", "direct", "warp", "unavailable")

    def __init__(self, proxy, source, *, direct=False, warp=False, unavailable=False):
        self.proxy = proxy
        self.source = source
        self.direct = direct
        self.warp = warp
        self.unavailable = unavailable

def _choose_egress(
    mode: str,
    target_url: str,
    *,
    country: str | set[str] | None = None,
    exclude_countries: str | set[str] | None = None,
) -> EgressChoice:
    """Resolve a --proxy mode into a concrete egress decision.

    auto        -> smart ladder (local gateway -> pool gateway -> WARP ->
                   residential -> direct) via scripts/egress.py
    none/direct -> force a direct connection
    URL/WARP    -> use it explicitly (user choice always wins over auto)

    Import failure is NOT fatal: it yields `unavailable`, and the caller leaves
    the child's built-in behavior untouched (with the warning already printed).
    """
    raw = (mode or PROXY_AUTO).strip()
    low = raw.lower()

    if low in (PROXY_NONE, "direct", "off", "no"):
        return EgressChoice(None, "none", direct=True)

    # Scheme-less proxies (127.0.0.1:8888, user:pass@host:port) are explicit
    # hops: give them a scheme so auto_egress's matcher sees them as such.
    if low not in (PROXY_AUTO, "warp") and "://" not in raw and _looks_like_proxy(raw):
        raw = f"http://{raw}"
        low = raw.lower()

    mod = _kc()._load_egress()
    if mod is None:
        if _looks_like_proxy(raw):        # explicit URL still honoured
            return EgressChoice(raw, "explicit")
        return EgressChoice(None, "unavailable", unavailable=True)

    kwargs: dict = {"target_url": target_url, "mode": raw, "verbose": True}
    if country is not None:
        kwargs["country"] = country
    if exclude_countries is not None:
        kwargs["exclude_countries"] = exclude_countries

    try:
        try:
            proxy, proc, source = mod.auto_egress(**kwargs)
        except TypeError:
            if "country" in kwargs or "exclude_countries" in kwargs:
                kwargs.pop("country", None)
                kwargs.pop("exclude_countries", None)
                proxy, proc, source = mod.auto_egress(**kwargs)
            else:
                raise
    except Exception as exc:  # noqa: BLE001 - defensive: fall back to legacy
        print(col("yellow", f"⚠ egress auto-wire failed ({exc}); "
                            "farm commands keep their built-in proxy behavior"))
        return EgressChoice(None, "unavailable", unavailable=True)

    if proc is not None:
        _AUTO_GATEWAYS.append(proc)

    if proxy:
        label = f"auto:{source}" if low == PROXY_AUTO else source
        print(col("green", f"  [proxy] {label} -> {proxy}"))
        return EgressChoice(proxy, label)

    if source == "warp":
        print(col("green", "  [proxy] WARP tunnel active (system-wide, no --proxy needed)"))
        return EgressChoice(None, "warp", warp=True)

    print(col("yellow", "  [proxy] auto: no usable proxy — using a direct connection"))
    print_egress_hint()
    return EgressChoice(None, "direct", direct=True)

def _resolve_proxy(
    mode: str,
    target_url: str,
    *,
    country: str | set[str] | None = None,
    exclude_countries: str | set[str] | None = None,
) -> str | None:
    """Return the proxy URL for `mode`, or None when the connection is direct."""
    return _kc()._choose_egress(
        mode, target_url, country=country, exclude_countries=exclude_countries
    ).proxy

def _proxy_env(choice: EgressChoice) -> dict | None:
    """Environment overrides for children that take no --proxy flag."""
    if not choice.proxy:
        return None
    return {
        "HTTP_PROXY": choice.proxy, "HTTPS_PROXY": choice.proxy,
        "http_proxy": choice.proxy, "https_proxy": choice.proxy,
    }

def _proxy_flags(choice: EgressChoice, *, supports_no_proxy: bool) -> list[str]:
    """CLI flags for children that accept --proxy / --no-proxy."""
    if choice.proxy:
        return ["--proxy", choice.proxy]
    if supports_no_proxy and not choice.unavailable:
        # none, auto->direct, or an active WARP tunnel: do not acquire a gateway.
        return ["--no-proxy"]
    return []


def _phone_not_on_mobile() -> bool:
    """True only when the phone is reachable AND confirmed not on mobile data (no device => False)."""
    try:
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        import mobile_rotate
        serial = mobile_rotate.first_device()
        return bool(serial) and mobile_rotate.phone_state(serial)["verdict"] != "ok"
    except Exception:  # noqa: BLE001
        return False


def _mobile_rotate_once(wait: float = 25.0) -> str | None:
    """Rotate the tethered phone's carrier IP once via mobile_rotate.py --rotate.
    Returns the new IP (str), or None if no device is connected / rotation fails."""
    py = pick_python()
    tool = _kc().AUTO_FREECF / "scripts" / "mobile_rotate.py"
    if not tool.exists():
        print(col("yellow", "  [mobile] Note: mobile_rotate.py not found"))
        return None
    try:
        proc = subprocess.run(
            [py, str(tool), "--rotate", "--wait", str(wait)],
            capture_output=True,
            text=True,
            timeout=wait + 30.0,
        )
    except Exception as exc:
        print(col("yellow", f"  [mobile] Note: mobile rotate invocation failed ({exc})"))
        return None

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        print(col("yellow", f"  [mobile] Note: no tethered phone carrier rotation available ({err or 'no device'})"))
        return None

    out = proc.stdout or ""
    new_ip = None
    for line in out.splitlines():
        if "IP after" in line:
            parts = line.split(":", 1)
            if len(parts) > 1:
                cand = parts[1].split()[0].strip()
                if cand and cand != "?":
                    new_ip = cand
                    break
    if not new_ip:
        m = re.search(r"IP after\s*:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", out)
        if m:
            new_ip = m.group(1)

    if new_ip:
        print(col("green", f"  [mobile] ✓ Rotated carrier IP: {new_ip}"))
    return new_ip


def make_plus_address(base_email: str | None, prefix: str = "farm", n: int = 1) -> str:
    """Generate a plus-addressed email from a base address. Pure — no I/O.

    Examples:
      make_plus_address('you@gmail.com', 'farm', 3) -> 'you+farm3@gmail.com'
      make_plus_address('gmail.com', 'farm', 3)     -> 'farm3@gmail.com' (domain-only)
      make_plus_address('@gmail.com', 'farm', 3)    -> 'farm3@gmail.com' (domain-only)
      make_plus_address('invalid', 'farm', 3)       -> '' (invalid input)
    """
    if not base_email or not isinstance(base_email, str):
        return ""
    base = base_email.strip()
    if not base:
        return ""
    pfx = str(prefix if prefix is not None else "farm").strip()

    if "@" in base:
        parts = base.split("@", 1)
        local = parts[0].strip()
        domain = parts[1].strip()
        # strip any existing plus suffix from the local part
        local = local.split("+", 1)[0].strip()
        if not domain or "." not in domain:
            return ""
        tag = f"{pfx}{n}" if pfx else str(n)
        if not local:
            # domain-only like "@gmail.com"
            return f"{tag}@{domain}"
        return f"{local}+{tag}@{domain}"
    else:
        # Domain-only like "gmail.com"
        if "." in base and not base.startswith("."):
            tag = f"{pfx}{n}" if pfx else str(n)
            return f"{tag}@{base}"
        return ""


def _is_block_signal(text: str) -> bool:
    t = (text or "").lower()
    signals = (
        "403",
        "access_restricted",
        "not supported",
        "take a breath",
        "forbidden",
        "blocked",
        "country your connection exits from",
    )
    return any(s in t for s in signals)


BACKGROUND_DIR = HOME / ".config" / "auto-freecf" / "background"
SESSION_GUARD_STATE = HOME / ".config" / "auto-freecf" / "session_guard.json"

# Per-account pacing presets (seconds between account creations):
#   fast   - 20-45s  (accept more throttle risk)
#   normal - 60-90s  (~1-1.5 min/account: dodges TokenHarbor's soft throttle)
#   safe   - 120-180s (slow-roll; best for account longevity / warming)
PACE_PRESETS: dict[str, tuple[float, float]] = {
    "fast": (20.0, 45.0),
    "normal": (60.0, 90.0),
    "safe": (120.0, 180.0),
}


class SessionGateway:
    """A proxy gateway OWNED and tracked by the current kancahub process.

    Unlike the detached -b mode, this runs a child process that the CLI keeps a
    handle on: it reports status, is reused by farm commands in the SAME session,
    and is stopped when the session exits. This is the 'one long-running CLI that
    does proxy AND farms' model.
    """

    def __init__(self, port: int = 8888, target: int = 30):
        self.port = int(port)
        self.target = int(target)
        self.proc: subprocess.Popen | None = None
        self._log_path: Path | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self, petani: Path, py: str) -> bool:
        if self.alive:
            print(col("yellow", f"  gateway already running on :{self.port}"))
            return True
        _kc().BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
        self._log_path = _kc().BACKGROUND_DIR / f"session-gateway-{self.port}.log"
        logf = open(self._log_path, "a")
        print(col("cyan", f"  starting gateway on :{self.port} (owned by this session)…"))
        try:
            self.proc = subprocess.Popen(
                [py, "-u", str(petani), "--serve", str(self.port), "--target", str(self.target)],
                cwd=str(PETANI), stdout=logf, stderr=subprocess.STDOUT,
            )
        except FileNotFoundError as ex:
            print(col("red", f"✗ {ex}"))
            return False
        # wait for it to accept connections
        import socket as _socket
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                print(col("red", f"✗ gateway exited early — see {self._log_path}"))
                return False
            try:
                with _socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    print(col("green", f"  ✓ gateway ready on 127.0.0.1:{self.port}"))
                    return True
            except OSError:
                time.sleep(0.4)
        print(col("yellow", f"  ⚠ gateway not ready yet on :{self.port}"))
        return True

    @property
    def alive(self) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        if self.proc is not None:
            return False
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/status", timeout=3):
                return True
        except Exception:  # noqa: BLE001
            return False

    @property
    def pool_size(self) -> int | None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/status", timeout=3) as r:
                data = json.loads(r.read())
            return int(data.get("stats", {}).get("pool_size"))
        except Exception:  # noqa: BLE001
            return None

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            print(col("dim", f"  gateway on :{self.port} stopped"))
        self.proc = None


def _spawn_background(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None,
                      name: str = "gateway", ready_port: int | None = None,
                      timeout: float = 15.0) -> int:
    """Start a long-running server (proxy gateway/daemon) DETACHED so the CLI
    returns immediately and the flow can continue. Writes a pid/log file and,
    when ready_port is given, waits until the port accepts connections.

    This fixes the "kancahub proxy gets stuck and can't continue" UX problem:
    PetaniProxy's --serve/--daemon-gateway normally run in the FOREGROUND.
    """
    _kc().BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _kc().BACKGROUND_DIR / f"{name}.log"
    pid_path = _kc().BACKGROUND_DIR / f"{name}.pid"
    e = os.environ.copy()
    if env:
        e.update(env)
    logf = open(log_path, "a")
    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(cwd) if cwd else None,
            env=e,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # detach from this terminal (survives the CLI exit)
        )
    except FileNotFoundError as ex:
        print(col("red", f"✗ {ex}"))
        return 127
    pid_path.write_text(str(proc.pid))
    print(col("green", f"  ✓ {name} started in the background (pid {proc.pid})"))
    print(col("dim", f"    log: {log_path}"))
    if ready_port:
        import socket as _socket
        deadline = time.time() + timeout
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                print(col("red", f"  ✗ {name} exited early — see {log_path}"))
                return 1
            try:
                with _socket.create_connection(("127.0.0.1", ready_port), timeout=0.5):
                    ready = True
                    break
            except OSError:
                time.sleep(0.4)
        if ready:
            print(col("green", f"  ✓ {name} ready on 127.0.0.1:{ready_port}"))
        else:
            print(col("yellow", f"  ⚠ {name} not listening on :{ready_port} yet (still starting?)"))
    print(col("dim", f"    stop with: kancahub proxy stop   (or kill {pid_path})"))
    return 0


def _stop_background(name: str = "gateway") -> int:
    pid_path = _kc().BACKGROUND_DIR / f"{name}.pid"
    if not pid_path.exists():
        print(col("dim", f"  no {name} pid file ({pid_path})"))
        return 0
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 15)
        pid_path.unlink()
        print(col("green", f"  ✓ stopped {name} (pid {pid})"))
        return 0
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        print(col("dim", f"  {name} already stopped"))
        return 0
    except Exception as e:  # noqa: BLE001
        print(col("red", f"  ✗ could not stop {name}: {e}"))
        return 1


class JobRegistry:
    """Long-running jobs started from the menu, tracked by THIS process.

    The whole point: when the user picks 'Start the proxy gateway' (or another
    long-lived action) from `kancahub`/`kancahub menu`, it must NOT block the
    menu — it starts as a background job here, the menu returns immediately, and
    the job's status is always visible. No second terminal needed.
    """

    def __init__(self):
        self.jobs: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, Path] = {}

    def start(self, name: str, cmd: list[str], *, cwd: Path | None = None,
              env: dict | None = None) -> int:
        # If it is already running, just report.
        if self.alive(name):
            print(col("yellow", f"  '{name}' is already running (pid {self.jobs[name].pid})"))
            return 0
        _kc().BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
        log_path = _kc().BACKGROUND_DIR / f"job-{name}.log"
        self.logs[name] = log_path
        logf = open(log_path, "a")
        try:
            proc = subprocess.Popen(
                [str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                env={**os.environ, **(env or {})},
                stdout=logf, stderr=subprocess.STDOUT, start_new_session=True,
            )
        except FileNotFoundError as ex:
            print(col("red", f"✗ {ex}"))
            return 127
        self.jobs[name] = proc
        print(col("green", f"  ✓ started '{name}' in the background (pid {proc.pid})"))
        print(col("dim", f"    log: {log_path}   ·   stop: choose [j] then the job, or kancahub proxy stop"))
        # brief grace so an instant crash is visible
        time.sleep(1.0)
        if proc.poll() is not None:
            print(col("red", f"  ✗ '{name}' exited immediately — see {log_path}"))
            return 1
        return 0

    def alive(self, name: str) -> bool:
        p = self.jobs.get(name)
        return p is not None and p.poll() is None

    def running(self) -> list[str]:
        return [n for n in list(self.jobs) if self.alive(n)]

    def stop(self, name: str) -> int:
        p = self.jobs.get(name)
        if p is None:
            print(col("dim", f"  no job named '{name}'"))
            return 0
        if p.poll() is None:
            # Kill the whole process GROUP (the job may fork grandchildren like
            # PetaniProxy's server/worker) so nothing is left listening.
            try:
                os.killpg(os.getpgid(p.pid), 15)
            except Exception:  # noqa: BLE001
                try:
                    p.terminate()
                except Exception:  # noqa: BLE001
                    pass
            try:
                p.wait(timeout=6)
            except Exception:  # noqa: BLE001
                try:
                    os.killpg(os.getpgid(p.pid), 9)
                except Exception:  # noqa: BLE001
                    pass
            print(col("green", f"  ✓ stopped '{name}'"))
        self.jobs.pop(name, None)
        return 0

    def stop_all(self) -> None:
        for n in list(self.jobs):
            self.stop(n)


_LAST_RUN_OUT: dict[str, str] = {}  # text of the last captured farm run, for failure classification


def _run_capture(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> tuple[int, str]:
    print(col("dim", f"$ {' '.join(str(c) for c in cmd)}" + (f"   (cwd={cwd})" if cwd else "")))
    e = os.environ.copy()
    if env:
        e.update(env)
    output_lines: list[str] = []
    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(cwd) if cwd else None,
            env=e,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if proc.stdout:
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                output_lines.append(line)
        ret = proc.wait()
        _LAST_RUN_OUT["text"] = "".join(output_lines)
        return ret, _LAST_RUN_OUT["text"]
    except FileNotFoundError as ex:
        print(col("red", f"✗ {ex}"))
        return 127, str(ex)


def _session_guard_start(account: str) -> None:
    """Record the egress IP at the start of a farm account session (best-effort)."""
    try:
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        import session_guard
        ip = session_guard.get_ip()
        rec = session_guard.guard_start(account, ip=ip)
        print(col("dim", f"  [session] {account} start ip={ip} (reuse so far: {rec.get('reuse', 0)})"))
    except Exception:  # noqa: BLE001
        pass


def _session_guard_end(account: str) -> None:
    """Check the egress IP stayed stable for the account session (best-effort)."""
    try:
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        import session_guard
        r = session_guard.guard_end(account)
        if r.get("verdict") == "changed":
            print(col("yellow", f"  [session] ⚠ {account} exit IP changed {r.get('start_ip')} -> "
                                f"{r.get('end_ip')} — sites may flag inconsistency"))
        elif r.get("verdict") == "stable":
            print(col("dim", f"  [session] {account} ip stable ({r.get('end_ip')})"))
    except Exception:  # noqa: BLE001
        pass


def run_with_mobile_retry(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict | None = None,
    *,
    mobile_rotate: bool = False,
    wait: float = 25.0,
    account: str | None = None,
) -> int:
    """Run a farm command.

    - If mobile_rotate: rotate the carrier IP before the run, and retry once with a
      fresh IP on a block signal.
    - If account is given: record the egress IP at start/end via session_guard so a
      mid-session IP change (a CGNAT anti-pattern) is surfaced.
    """
    if account:
        _kc()._session_guard_start(account)

    try:
        if not mobile_rotate:
            if account and account.startswith("thk"):
                # thk needs the output to tell a network cap from a burst throttle;
                # other farms keep the plain streaming run() (tests patch it).
                return _kc()._run_capture(cmd, cwd=cwd, env=env)[0]
            return _kc().run(cmd, cwd=cwd, env=env)

        # Rotation 1: before the run
        print(col("cyan", "  [mobile] Rotating carrier IP before run (--mobile-rotate enabled)…"))
        if _kc()._mobile_rotate_once(wait=wait) is None and _kc()._phone_not_on_mobile():
            # No new IP AND the phone is on Wi-Fi / out of data: running would burn signups on a
            # flagged Wi-Fi IP. Stop here instead of farming.
            print(col("red", "  [mobile] ✗ phone is not on mobile data — refusing to farm. "
                             "Check: kancahub mobile status"))
            return 3

        code, out = _kc()._run_capture(cmd, cwd=cwd, env=env)
        if code == 0:
            return 0

        if _kc()._is_block_signal(out):
            print(col("yellow", "\n  ⚠ Block signal detected during farm run."))
            delay = random.uniform(20.0, 30.0)
            print(col("cyan", f"  [mobile] Cooling down ({delay:.1f}s) and rotating carrier IP for retry…"))
            time.sleep(delay)
            # Rotation 2: retry rotation (cap: 2 per account/run)
            _kc()._mobile_rotate_once(wait=wait)
            print(col("cyan", "  [mobile] Retrying farm command with fresh mobile egress…"))
            code, _ = _kc()._run_capture(cmd, cwd=cwd, env=env)  # also refreshes _LAST_RUN_OUT

        return code
    finally:
        if account:
            _kc()._session_guard_end(account)
def _ledger_record(farm: str, *, target: str = "", egress: str = "", exit_ip: str = "",
                   stage: str = "", ok: bool = False, count: int = 0, note: str = "") -> None:
    """Best-effort append to the farm run ledger (never raises)."""
    try:
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        import farm_ledger
        farm_ledger.record(farm, target=target, egress=egress, exit_ip=exit_ip,
                           stage=stage, ok=ok, count=count, note=note)
    except Exception:  # noqa: BLE001
        pass
