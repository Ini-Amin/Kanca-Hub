#!/usr/bin/env python3
"""proton_session.py — log in to a Proton account and cache the API session.

WHY THIS EXISTS
---------------
A ProtonVPN free account can reach 121 Tier-0 logicals (646 distinct exit IPs,
measured 2026-10-23 from the client's own cache). Getting there needs an
authenticated session: the server list and the WireGuard certificate endpoint
both reject anonymous calls (401). Login itself is SRP-6a over plain HTTPS and
has no captcha and no 2FA requirement for a normal account — see
~/.Auto-FreeCF-vendor-refs/reports/research_proton.md, which derived this from
the open-source `python-proton-core` rather than from the binary.

So: one interactive login here, then everything else (server list, certificate
issuance, tunnel setup) is pure HTTP with the cached token.

SECRETS
-------
Credentials are read from ~/.config/auto-freecf/.env as
    PROTON_USER=...
    PROTON_PASSWORD=...
or prompted for once. The session token is cached at
~/.cache/auto-freecf/proton_session.json with 0600.

Usage:
    proton_session.py login          # authenticate, cache the session
    proton_session.py status         # show cached account + scopes
    proton_session.py logout         # forget the cached session
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

CACHE = Path.home() / ".cache/auto-freecf/proton_session.json"
ENV = Path.home() / ".config/auto-freecf/.env"
APPVERSION = "linux-vpn@4.18.2"

# proton-core lives in the Auto-FreeCF venv (it is not a distro package and is
# not installable with `pip install proton-core` -- the PyPI name does not
# exist; it must come from the ProtonVPN git repo). Running this file with the
# system python therefore fails at `import proton`. Re-exec into the venv so
# the documented command works whichever python the caller happens to use.
VENV_PY = Path.home() / ".local/share/auto-freecf/venv/bin/python"


def _ensure_venv() -> None:
    if os.environ.get("_PROTON_REEXEC"):
        return
    try:
        import proton.session  # noqa: F401
        return
    except ImportError:
        pass
    if VENV_PY.is_file() and Path(sys.executable) != VENV_PY:
        os.environ["_PROTON_REEXEC"] = "1"
        os.execv(str(VENV_PY), [str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.exit("proton-core is not installed. Run:\n"
             "  ~/.local/share/auto-freecf/venv/bin/pip install "
             "\"git+https://github.com/ProtonVPN/python-proton-core.git\"")


_ensure_venv()


def _env(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    if ENV.is_file():
        for line in ENV.read_text().splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _make_session(appversion: str = APPVERSION):
    """Build a Session.

    `appversion` is a read-only property (api.py:660) — it can only be set
    through the constructor, not assigned afterwards. That is why
    `s.appversion = ...` raised AttributeError.
    """
    from proton.session import Session
    return Session(appversion=appversion)


def login(username: str = "", password: str = "") -> dict:
    username = username or _env("PROTON_USER")
    password = password or _env("PROTON_PASSWORD")
    if not username:
        username = input("Proton username: ").strip()
    if not password:
        password = getpass.getpass("Proton password: ")

    s = _make_session()
    ok = s.authenticate(username, password)
    if not ok:
        raise SystemExit("authentication failed (check username/password; "
                         "2FA accounts must disable it or use an app password)")

    # RefreshToken has NO public property (api.py defines UID, Scopes,
    # AccountName, AccessToken only), so `s.RefreshToken` raises
    # AttributeError. __getstate__ is the supported way to read the full
    # session payload -- it is what the client persists.
    state = s.__getstate__()
    rec = {
        "username": s.AccountName or username,
        "uid": state.get("UID") or s.UID,
        "access_token": state.get("AccessToken") or s.AccessToken,
        "refresh_token": state.get("RefreshToken"),
        "scopes": list(state.get("Scopes") or s.Scopes or []),
        "appversion": APPVERSION,
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(rec, indent=1))
    CACHE.chmod(0o600)
    return rec


def load() -> dict | None:
    if not CACHE.is_file():
        return None
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return None


def session_from_cache() -> object | None:
    """Rebuild a Session from the cached token. None when there is no cache.

    UID / AccessToken / RefreshToken / Scopes are all read-only properties
    (api.py:675-700), so they cannot be assigned. proton-core restores a
    persisted session through `__setstate__`, which is the documented path and
    also re-creates the transport. Assigning the properties directly silently
    left `authenticated` False.
    """
    rec = load()
    if not rec:
        return None
    s = _make_session(rec.get("appversion") or APPVERSION)
    s.__setstate__({
        "UID": rec.get("uid"),
        "AccessToken": rec.get("access_token"),
        "RefreshToken": rec.get("refresh_token"),
        "Scopes": rec.get("scopes") or [],
        "AccountName": rec.get("username"),
        "LastUseData": {"appversion": rec.get("appversion") or APPVERSION},
    })
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description="Proton account session")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login")
    sub.add_parser("status")
    sub.add_parser("logout")
    a = ap.parse_args()

    if a.cmd == "login":
        rec = login()
        print(f"logged in: {rec['username']} (uid {rec['uid'][:8]}…)")
        print(f"scopes: {', '.join(rec['scopes'][:8]) or '(none)'}")
        print(f"cached: {CACHE}")
        return 0

    if a.cmd == "logout":
        if CACHE.exists():
            CACHE.unlink()
            print("session cache removed")
        else:
            print("no cached session")
        return 0

    rec = load()
    if not rec:
        print("no cached session — run: proton_session.py login")
        return 1
    print(f"username : {rec['username']}")
    print(f"uid      : {rec['uid']}")
    print(f"scopes   : {', '.join(rec['scopes'][:10])}")
    print(f"cache    : {CACHE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
