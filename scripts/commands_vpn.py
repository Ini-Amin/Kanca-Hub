"""commands_vpn — `kancahub vpn` subcommand: free VPN-fleet egress pool (proxy_pool.py).

Providers wired: Windscribe (61 hosts, account-less serverlist + farmed accounts),
VeePN (per-device creds), Browsec (44 countries, no auth). WARP has its own
command (`kancahub warp`) because it is a system tunnel, not a CONNECT proxy.
"""
from __future__ import annotations

import json

def cmd_vpn(a) -> int:
    import kancahub
    sub = getattr(a, "vpn_cmd", None) or "status"
    if sub == "status":
        return kancahub.run([kancahub.pick_python(), "-c", _py_status()], cwd=kancahub.AUTO_FREECF)
    if sub == "list":
        return kancahub.run([kancahub.pick_python(), "-c", _py_list(getattr(a, "country", None))], cwd=kancahub.AUTO_FREECF)
    if sub == "get":
        cc = getattr(a, "country", None)
        script = _py_get(cc, validate=not getattr(a, "no_verify", False))
        return kancahub.run([kancahub.pick_python(), "-c", script], cwd=kancahub.AUTO_FREECF)
    if sub == "farm":
        return kancahub.run([kancahub.pick_python(), str(kancahub.AUTO_FREECF / "scripts" / "windscribe_account_farm.py"),
                             str(getattr(a, "count", 1) or 1)], cwd=kancahub.AUTO_FREECF)
    if sub == "flag":
        tool = kancahub.AUTO_FREECF / "scripts" / "ip_flag_guard.py"
        ip, host = getattr(a, "ip", None), getattr(a, "host", None)
        cmd = [kancahub.pick_python(), str(tool)]
        if getattr(a, "clear_flag", False):
            cmd += ["clear", "--ip", ip or "", "--host", host or ""]
        else:
            if not (ip and host):
                print(kancahub.col("red", "✗ flag needs --ip and --host (or --clear)"))
                return 1
            cmd += ["flag", "--ip", ip, "--host", host]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)
    print(kancahub.col("red", f"✗ unknown vpn subcommand {sub!r}"))
    return 1

def _py_status() -> str:
    return (
        "import sys; sys.path.insert(0,'scripts');"
        "import proxy_pool, ip_flag_guard;"
        "ents=[e for e in proxy_pool.all_entries() if e['provider']!='warp'];"
        "from collections import Counter;"
        "c=Counter(e['provider'] for e in ents);"
        "regions=len({e.get('region','') for e in ents});"
        "print(f'VPN pool: {len(ents)} entries | ' + ', '.join(f'{k}={v}' for k,v in sorted(c.items())) + f' | {regions} regions');"
        "print('flags:'); print(ip_flag_guard.table() or '(none)')"
    )

def _py_list(cc) -> str:
    filt = repr(cc) if cc else "None"
    return (
        "import sys; sys.path.insert(0,'scripts');"
        "import proxy_pool;"
        f"ents=[e for e in proxy_pool.all_entries({filt}) if e['provider']!='warp'];"
        "[print(f\"{e['provider']:11} {(e.get('region') or ''):14} \" + (e['proxy']['server'] if e.get('proxy') else '(system)')) for e in ents]"
    )

def _py_get(cc, validate=True) -> str:
    filt = repr(cc) if cc else "None"
    core = ("e = proxy_pool.best(" + filt + ", include_warp=False)" if filt != "None"
            else "e = proxy_pool.best(None, include_warp=True)")
    return (
        "import sys, json; sys.path.insert(0,'scripts');"
        "import proxy_pool;"
        + core + ";"
        "print(json.dumps(e, indent=1) if e else '{}')"
    )
