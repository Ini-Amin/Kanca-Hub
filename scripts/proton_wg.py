#!/usr/bin/env python3
"""proton_wg.py — turn a Proton free account into WireGuard configs, one per exit.

HOW IT WORKS (all derived from the open-source client, not from a binary)
-------------------------------------------------------------------------
The account keypair is ed25519; WireGuard wants x25519, so the client converts
the seed with `crypto_sign_ed25519_sk_to_curve25519` before registering the
public half. That is the quirk documented in
~/.Auto-FreeCF-vendor-refs/reports/research_proton.md (key_mgr.py).

Flow per server:
    generate ed25519 keypair
    -> POST /vpn/v1/certificate {ClientPublicKey: <x25519 pub, base64>}
    -> response carries the WireGuard config material
    -> write a .conf with Table=off so it never touches the default route

`Table = off` is deliberate: this box already runs `egress.py --relay 25346`
and a WARP tunnel. A VPN that rewrites the default route would break every
other worker on the machine. With Table=off the interface only carries traffic
bound to it explicitly (`curl --interface proton-JP`), which is exactly what a
rotation harness wants.

Usage:
    proton_wg.py make --cc JP --count 3      # write configs for 3 JP exits
    proton_wg.py make --all-free             # every Tier-0 logical (121)
    proton_wg.py ls
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

OUT = Path.home() / ".config/auto-freecf/proton_wg"
API_CERT = "/vpn/v1/certificate"
API_LOGICALS = "/vpn/v1/logicals?SecureCoreFilter=all&WithState=true"
API_CLIENTCONFIG = "/vpn/v1/clientconfig"

# Same reason as proton_session.py: proton-core and cryptography live in the
# Auto-FreeCF venv, not in the distro python.
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
    sys.exit("proton-core is not installed in the venv; see proton_session.py")


_ensure_venv()


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def gen_keypair() -> tuple[str, str]:
    """Generate the keypair the Proton API expects.

    Measured against python-proton-vpn-api-core (key_mgr.py:117, fetcher.py:91):
    `ClientPublicKey` must be the **ed25519 public key in PEM
    (SubjectPublicKeyInfo) form** — NOT base64 raw bytes and NOT x25519. Sending
    raw bytes returns `[HTTP/400, 2001] Kunci tidak dapat dibaca`, which is what
    the first implementation hit.

    The x25519 form is only needed later, for the WireGuard config itself:
    the client converts the ed25519 seed with
    `crypto_sign_ed25519_sk_to_curve25519` (key_mgr.py:44). We reproduce that
    with the documented SHA-512 clamping so no PyNaCl dependency is required.
    """
    import hashlib

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    seed = secrets.token_bytes(32)
    ed_priv = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    raw_ed_priv = ed_priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    ed_pub_pem = ed_priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")

    # ed25519 seed -> x25519 private, exactly as key_mgr.py documents it.
    h = bytearray(hashlib.sha512(raw_ed_priv).digest()[:32])
    h[0] &= 248
    h[31] &= 127
    h[31] |= 64
    wg_priv = base64.b64encode(bytes(h)).decode()

    return wg_priv, ed_pub_pem


def fetch_logicals(session) -> list[dict]:
    data = session.api_request(API_LOGICALS)
    return [x for x in (data or {}).get("LogicalServers", []) if x.get("Tier") == 0]


def fetch_clientconfig(session) -> dict:
    try:
        return session.api_request(API_CLIENTCONFIG) or {}
    except Exception:
        return {}


def wg_port(clientconfig: dict) -> int:
    """WireGuard port for Proton servers.

    Measured 2026-10-23: /vpn/v1/clientconfig has NO WireGuardPorts key -- it
    only lists OpenVPN UDP/TCP ports. Proton's WireGuard endpoints listen on
    51820, so that is the value used unless the response grows a WG-specific
    field later.
    """
    for key in ("WireGuardPorts", "WireGuardPort"):
        ports = clientconfig.get(key)
        if isinstance(ports, list):
            for p in ports:
                if isinstance(p, int):
                    return p
        elif isinstance(ports, int):
            return ports
    return 51820


def make_config(session, logical: dict, port: int, dns: str = "10.2.0.1") -> dict:
    """Request a certificate for one logical and build the .conf text."""
    servers = logical.get("Servers") or []
    if not servers:
        raise RuntimeError("logical has no servers")
    srv = servers[0]
    wg_priv, wg_pub = gen_keypair()

    resp = session.api_request(API_CERT, jsondata={
        "ClientPublicKey": wg_pub,
        "Duration": "1440 min",
    })
    # The response is a flat dict: {Code, SerialNumber, ClientKeyFingerprint,
    # ClientKey, Certificate} where Certificate is a PEM *string*. The first
    # version treated it as a nested object and did cert.get(...), which raised
    # "'str' object has no attribute 'get'".
    resp = resp or {}
    cert_pem = resp.get("Certificate") or ""
    if not cert_pem:
        raise RuntimeError(f"no certificate in response: {str(resp)[:200]}")

    endpoint_ip = srv.get("EntryIP") or srv.get("IP")
    # The server's WireGuard public key is published per-server as
    # X25519PublicKey. The certificate response contains only the CLIENT
    # certificate (measured 2026-10-23: Code/SerialNumber/ClientKeyFingerprint/
    # ClientKey/Certificate) -- there is no ServerPublicKey field, which is what
    # made the first builder fail with "'str' object has no attribute 'get'".
    server_pub = srv.get("X25519PublicKey") or ""
    if not server_pub:
        raise RuntimeError("server has no X25519PublicKey")
    name = logical.get("Name") or "proton"
    conf = "\n".join([
        "[Interface]",
        f"PrivateKey = {wg_priv}",
        f"Address = 10.2.0.2/32",
        f"DNS = {dns}",
        "Table = off",
        "MTU = 1420",
        "",
        "[Peer]",
        f"PublicKey = {server_pub}",
        f"AllowedIPs = 0.0.0.0/0, ::/0",
        f"Endpoint = {endpoint_ip}:{port}",
        "PersistentKeepalive = 25",
        "",
    ])
    return {
        "name": name,
        "conf": conf,
        "exit_ip": srv.get("ExitIP"),
        "entry_ip": endpoint_ip,
        "country": logical.get("ExitCountry"),
        "city": logical.get("City"),
        "cert_serial": resp.get("SerialNumber"),
        "cert_pem": cert_pem,
    }


def cmd_make(a) -> int:
    from proton_session import session_from_cache
    s = session_from_cache()
    if not s:
        print("no Proton session — run: proton_session.py login", file=sys.stderr)
        return 1

    logicals = fetch_logicals(s)
    if a.cc:
        logicals = [x for x in logicals if (x.get("ExitCountry") or "").upper() == a.cc.upper()]
    if not a.all_free:
        logicals = logicals[:a.count]

    cc = fetch_clientconfig(s)
    port = wg_port(cc)
    OUT.mkdir(parents=True, exist_ok=True)

    written = 0
    for lg in logicals:
        try:
            info = make_config(s, lg, port)
        except Exception as exc:
            print(f"  skip {lg.get('Name')}: {type(exc).__name__}: {str(exc)[:80]}", file=sys.stderr)
            continue
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in info["name"])
        path = OUT / f"{safe}.conf"
        path.write_text(info["conf"])
        path.chmod(0o600)
        meta = OUT / f"{safe}.json"
        meta.write_text(json.dumps({k: v for k, v in info.items() if k != "conf"}, indent=1))
        print(f"  {safe:<22} exit={info['exit_ip']:<16} {info['country']} {info['city'] or ''}")
        written += 1

    print(f"\nwrote {written} config(s) to {OUT}")
    return 0 if written else 2


def cmd_ls(a) -> int:
    if not OUT.is_dir():
        print("no configs yet — run: proton_wg.py make --cc JP --count 3")
        return 1
    rows = sorted(OUT.glob("*.json"))
    print(f"{len(rows)} config(s) in {OUT}")
    for p in rows[:20]:
        try:
            d = json.loads(p.read_text())
            print(f"  {d['name']:<22} exit={d.get('exit_ip','?'):<16} {d.get('country','')}")
        except Exception:
            pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Proton free-tier WireGuard configs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--cc", help="country code filter (JP, NL, US, SG, ...)")
    m.add_argument("--count", type=int, default=3)
    m.add_argument("--all-free", action="store_true", help="every Tier-0 logical")
    sub.add_parser("ls")
    a = ap.parse_args()
    return cmd_make(a) if a.cmd == "make" else cmd_ls(a)


if __name__ == "__main__":
    sys.exit(main())
