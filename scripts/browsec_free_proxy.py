#!/usr/bin/env python3
"""Browsec free HTTPS-proxy (no account, no auth) — decoded from extension 4.0.0.
Usage: browsec_free_proxy.py [list|test] [cc]   e.g. browsec_free_proxy.py test us28.bd-assets.com 23479"""
import json, socket, ssl, sys, urllib.request

API="https://e4.bmtr.org/api/v1"
HDR={"X-Browsec-Version":"4.0.0"}

def servers():
    d=json.loads(urllib.request.urlopen(urllib.request.Request(
        f"{API}/servers?stdomains=1&cid=bfc", headers=HDR), timeout=15).read())
    return {cc:[(s["host"],s["port"]) for s in v.get("servers",[])] for cc,v in d["countries"].items()}

def test(host, port):
    s=socket.create_connection((host,int(port)),timeout=15)
    ss=ssl.create_default_context().wrap_socket(s,server_hostname=host)
    ss.sendall(b"CONNECT example.com:80 HTTP/1.1\r\nHost: example.com:80\r\n\r\n")
    buf=b""
    while b"\r\n\r\n" not in buf: buf+=ss.recv(200)
    return buf.startswith(b"HTTP/1.1 200")

if __name__=="__main__":
    cmd=sys.argv[1] if len(sys.argv)>1 else "list"
    if cmd=="list":
        for cc,hosts in sorted(servers().items()):
            if hosts: print(f"{cc}: {' '.join(f'{h}:{p}' for h,p in hosts[:3])}{' …' if len(hosts)>3 else ''}")
    elif cmd=="test":
        h,p=(sys.argv[2], sys.argv[3]) if len(sys.argv)>3 else ("us28.bd-assets.com","23479")
        print("tunnel:", "OK" if test(h,p) else "FAIL")
