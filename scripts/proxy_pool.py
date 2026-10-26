#!/usr/bin/env python3
"""Unified free-egress pool: WARP + Windscribe + VeePN + Browsec.

Usage:
  proxy_pool.py list [cc]            # all providers, optional country filter (browsec/ws)
  proxy_pool.py get [country]        # best available entry -> JSON {provider, proxy, egress_ip}
  proxy_pool.py test <provider_url>  # egress check for one entry
  proxy_pool.py rotate               # down/up WARP (only rotatable provider)

Entry JSON is consumed by farm scripts: proxy={"server": "https://host:port", ...}.
WARP is system-level (default route) — when active, 'get' returns {"provider":"warp","proxy":None}
meaning callers must run WITHOUT proxy setting.
"""
import json, socket, ssl, subprocess, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

def _egress(host, port, auth=None, proto="https", timeout=14):
    """Egress IP via HTTP/HTTPS-CONNECT proxy using curl_cffi (chrome TLS in-tunnel)."""
    try:
        from curl_cffi import requests as cr
        px=f"{proto}://{host}:{port}"
        if auth:
            import base64
            user,pw=base64.b64decode(auth).decode().split(":",1)
            px=f"{proto}://{user}:{pw}@{host}:{port}"
        r=cr.get("https://api.ipify.org?format=json", proxy=px, impersonate="chrome131", timeout=timeout)
        return r.json().get("ip")
    except Exception:
        return None

def warp_entry():
    try:
        import warp_manager
        st=warp_manager.status()
        if st.get("up"):
            return {"provider":"warp","proxy":None,"egress_ip":st.get("egress_ip"),
                    "note":"system tunnel active; run WITHOUT proxy"}
    except Exception:
        pass
    return None

def windscribe_entries(cc=None):
    try:
        import windscribe_free_proxy as ws
        creds = ws.get_credentials() if hasattr(ws, "get_credentials") else None
        hosts = ws.serverlist()
        if cc:
            hosts = {k: v for k, v in hosts.items() if cc.lower() in k.lower()}
        entries = []
        for k, hl in hosts.items():
            for h in hl[:2]:
                px = {"server": f"https://{h}:443"}
                if creds:
                    px["username"] = creds["username"]
                    px["password"] = creds["password"]
                entries.append({"provider": "windscribe", "proxy": px, "region": k})
        return entries
    except Exception:
        return []

def browsec_entries(cc=None):
    try:
        import browsec_free_proxy as br
        srv=servers=br.servers()
        if cc: srv={k:v for k,v in srv.items() if k.lower()==cc.lower()}
        return [{"provider":"browsec","proxy":{"server":f"https://{h}:{p}"},"region":cc_}
                for cc_,hl in srv.items() for h,p in hl[:2]]
    except Exception:
        return []

def veepn_entries(cc="nl"):
    try:
        if cc and cc.lower() not in ("nl", "netherlands", "amsterdam"):
            return []
        d=json.load(open(Path.home()/".cache/auto-freecf/veepn_pool.json")) if (Path.home()/".cache/auto-freecf/veepn_pool.json").exists() else None
        if not d:
            import veepn_free_proxy as vp
            d=vp.get_servers("nl")
            (Path.home()/".cache/auto-freecf").mkdir(parents=True,exist_ok=True)
            json.dump(d, open(Path.home()/".cache/auto-freecf/veepn_pool.json","w"))
        return [{"provider":"veepn",
                 "proxy":{"server":f"https://{s['addresses'][0]}:{s['port']}",
                          "username":s["username"],"password":s["password"]},
                 "region":s.get("regionName","")}
                for s in d]
    except Exception:
        return []
def fastly_entries(cc=None):
    try:
        import fastly_serverlist
        recs = fastly_serverlist.fetch_serverlist()
        candidates = fastly_serverlist.parse_servers(recs)
        if cc:
            candidates = [c for c in candidates if c["country_code"].lower() == cc.lower()]
        entries = []
        for c in candidates[:4]:
            entries.append({
                "provider": "fastly",
                "proxy": {"server": f"https://{c['host']}:{c['port']}"},
                "region": c["country_code"],
                "note": "Mozilla 50GB Anycast Node"
            })
        return entries
    except Exception:
        return []
def urban_vpn_entries(cc=None):
    try:
        import urban_vpn as uv
        px = uv.get_proxy(cc or "JP")
        return [{
            "provider": "urban_vpn",
            "proxy": {
                "server": f"http://{px['host']}:{px['port']}",
                "username": px["username"],
                "password": px["password"],
            },
            "region": px["country"],
            "note": "UrbanVPN 85-Country Residential Network"
        }]
    except Exception:
        return []


def all_entries(cc=None):
    warp = [warp_entry()] if (warp_entry() and not cc) else []
    return warp + urban_vpn_entries(cc) + fastly_entries(cc) + veepn_entries(cc) + windscribe_entries(cc) + browsec_entries(cc)
def best(country=None, include_warp=True):
    """First entry whose tunnel actually reaches ipify (validated, not just listed).
    With a country filter, WARP is skipped (system tunnel has no per-country egress)."""
    entries=all_entries(country)
    if country and include_warp:
        include_warp=False
    if not include_warp:
        entries=[e for e in entries if e["provider"]!="warp"]
    for e in entries:
        p=e.get("proxy")
        if p is None: return e  # warp system-level, trust status
        proto = p.get("server", "https://").split("://")[0]
        hostport=p["server"].split("//")[1]
        host,port=hostport.rsplit(":",1)
        auth=None
        if p.get("username"):
            import base64
            auth=base64.b64encode(f"{p['username']}:{p['password']}".encode()).decode()
        ip=_egress(host,port,auth,proto=proto)
        if ip:
            e["egress_ip"]=ip
            return e

if __name__=="__main__":
    cmd=sys.argv[1] if len(sys.argv)>1 else "get"
    if cmd=="list":
        for e in all_entries(sys.argv[2] if len(sys.argv)>2 else None):
            print(f"{e['provider']:11} {e.get('region',''):12} {e['proxy']['server'] if e['proxy'] else '(system)'}")
    elif cmd=="get":
        e=best(sys.argv[2] if len(sys.argv)>2 else None)
        print(json.dumps(e, indent=1) if e else "{}")
    elif cmd=="test":
        url=sys.argv[2]
        host,port=url.split("//")[1].rsplit(":",1)
        print(json.dumps({"egress_ip":_egress(host,port)}))
    elif cmd=="rotate":
        import warp_manager
        print("rotated:", warp_manager.rotate())



# ---- legacy rotating-pool API (kept: unit-tested, used by account farm templates) ----

import argparse
import secrets
from pathlib import Path


def parse_proxy_line(line: str, default_scheme: str = "http") -> str | None:
    """Normalise host:port | host:port:user:pass | user:pass@host:port | URL."""
    if not line:
        return None
    s = str(line).strip()
    if not s or s.startswith("#") or s.startswith("//"):
        return None
    s = s.split(" #", 1)[0].strip()
    if not s:
        return None

    scheme = default_scheme.replace("://", "")
    m = __import__("re").match(r"^(https?|socks4|socks5)://(.*)$", s, __import__("re").I)
    if m:
        scheme, s = m.group(1).lower(), m.group(2)

    if "@" in s:
        creds, _, hostport = s.rpartition("@")
        if not hostport:
            return None
        u, _, p = creds.partition(":")
        from urllib.parse import quote
        auth = f"{quote(u)}:{quote(p)}@" if creds else ""
        return f"{scheme}://{auth}{hostport}"

    parts = s.split(":")
    if len(parts) == 2:
        return f"{scheme}://{parts[0]}:{parts[1]}"
    if len(parts) >= 3:
        host, port, user = parts[0], parts[1], parts[2]
        pw = ":".join(parts[3:])
        if not port.isdigit():
            return None
        from urllib.parse import quote
        if not user:
            return f"{scheme}://{host}:{port}"
        return f"{scheme}://{quote(user)}:{quote(pw)}@{host}:{port}"
    return None


def load_proxy_file(path: str | Path, default_scheme: str = "http") -> list[str]:
    try:
        raw = Path(path).read_text()
    except OSError:
        return []
    return [p for p in (parse_proxy_line(l, default_scheme) for l in raw.splitlines()) if p]


def expand_template(template: str, index: int = 0, *, rotate: bool = True, country: str = "") -> str:
    """Expand {session}/{index}/{country} placeholders in a gateway URL."""
    if not template:
        return ""
    sid = secrets.token_hex(4) if rotate else "fixed"
    out = template.replace("{session}", sid).replace("{index}", str(index))
    if country:
        out = out.replace("{country}", country)
    return out


class ProxyPool:
    def __init__(self, *, proxy_list=None, proxy_file: str | Path = "",
                 proxy_template: str = "", rotate_per_account: bool = True,
                 country: str = "", default_scheme: str = "http"):
        self.static: list[str] = [p for p in (parse_proxy_line(x, default_scheme) for x in (proxy_list or [])) if p]
        if proxy_file and Path(proxy_file).exists():
            self.static += load_proxy_file(proxy_file, default_scheme)
        # de-dup, keep order
        seen = set()
        self.static = [p for p in self.static if not (p in seen or seen.add(p))]
        self.template = proxy_template or ""
        self.rotate_per_account = rotate_per_account
        self.country = country
        self._cursor = 0
        self.usage: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.static) or bool(self.template)

    def assign(self, index: int) -> str:
        """The egress for account `index` ("" = direct)."""
        if self.template:
            p = expand_template(self.template, index, rotate=self.rotate_per_account, country=self.country)
            self.usage[p] = self.usage.get(p, 0) + 1
            return p
        if self.static:
            p = self.static[self._cursor % len(self.static)]
            self._cursor += 1
            self.usage[p] = self.usage.get(p, 0) + 1
            return p
        return ""

    def rotate(self, current: str) -> str:
        """A different egress than `current` (use after a failure)."""
        if self.template:
            return self.assign(self._cursor)
        if len(self.static) <= 1:
            return self.static[0] if self.static else ""
        self._cursor += 1
        nxt = self.static[self._cursor % len(self.static)]
        if nxt == current:
            self._cursor += 1
            nxt = self.static[self._cursor % len(self.static)]
        self.usage[nxt] = self.usage.get(nxt, 0) + 1
        return nxt
