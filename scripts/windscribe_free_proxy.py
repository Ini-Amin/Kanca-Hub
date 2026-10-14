#!/usr/bin/env python3
"""Windscribe free HTTPS-proxy (no account) — decoded from extension 3.2.5.
Usage: windscribe_free_proxy.py [list|test] [host]"""
import hashlib, time, json, socket, ssl, sys

SECRET="952b4412f002315aa50751032fcaab03"
API="https://api.windscribe.com"; ASSETS="https://assets.windscribe.com"

def _qs():
    t=str(int(time.time()))
    return f"time={t}&client_auth_hash={hashlib.md5((SECRET+t).encode()).hexdigest()}"

def serverlist():
    import urllib.request
    d=json.loads(urllib.request.urlopen(f"{ASSETS}/serverlist/chrome/0/0?{_qs()}", timeout=15).read())
    free=[l for l in d["data"] if not l.get("premium_only")]
    return {l["name"]:[h["hostname"] for g in l.get("groups",[]) for h in g.get("hosts",[])] for l in free}

def test(host):
    s=socket.create_connection((host,443),timeout=12)
    ss=ssl.create_default_context().wrap_socket(s,server_hostname=host)
    ss.sendall(b"CONNECT example.com:80 HTTP/1.1\r\nHost: example.com:80\r\n\r\n")
    buf=b""
    while b"\r\n\r\n" not in buf: buf+=ss.recv(200)
    return buf.startswith(b"HTTP/1.1 200")

def fetch_sliders(n=12, outdir="reports/slider_review"):
    """Pull n AuthToken/login slider captchas into the review folder (ws_img/ +
    ws.json) for manual labeling. Returns list of saved ids."""
    import base64, urllib.request
    from pathlib import Path
    out = Path(outdir); (out / "ws_img").mkdir(parents=True, exist_ok=True)
    meta_p = out / "ws.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    saved = []
    while len(saved) < n:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"{ASSETS if False else API}/AuthToken/login?{_qs()}",
            headers={"Origin": "chrome-extension://hnmpcagpplmpfojmgmnngilcnanddlhb"},
        ), timeout=15).read()).get("data") or {}
        cap = d.get("captcha")
        if not cap:
            continue
        cid = hashlib.sha1((cap["background"][:512] + cap["slider"][:512]).encode()).hexdigest()[:16]
        (out / "ws_img" / f"{cid}_bg.png").write_bytes(base64.b64decode(cap["background"]))
        (out / "ws_img" / f"{cid}_piece.png").write_bytes(base64.b64decode(cap["slider"]))
        meta[cid] = {"top": cap.get("top"), "w": 700, "h": 400}
        saved.append(cid)
        time.sleep(0.4)
    meta_p.write_text(json.dumps(meta, indent=1))
    return saved

if __name__=="__main__":
    cmd=sys.argv[1] if len(sys.argv)>1 else "list"
    if cmd=="list":
        for name,hosts in serverlist().items(): print(f"{name}: {' '.join(hosts[:3])}{' …' if len(hosts)>3 else ''}")
    elif cmd=="test":
        print("tunnel:", "OK" if test(sys.argv[2]) else "FAIL")
    elif cmd=="sliders":
        n=int(sys.argv[2]) if len(sys.argv)>2 else 12
        ids=fetch_sliders(n)
        print("saved:", len(ids), "captchas -> reports/slider_review/ws_img/")
