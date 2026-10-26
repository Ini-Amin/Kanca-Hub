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
def check_credentials_valid(creds, host="us-central-152.totallyacdn.com", timeout=4):
    """Fast handshake test: verifies credentials aren't rejected with 407 or exhausted."""
    try:
        import base64
        auth = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
        s = socket.create_connection((host, 443), timeout=timeout)
        ss = ssl.create_default_context().wrap_socket(s, server_hostname=host)
        ss.sendall(f"CONNECT example.com:80 HTTP/1.1\r\nHost: example.com:80\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode())
        buf = ss.recv(200)
        ss.close()
        return buf.startswith(b"HTTP/1.1 200")
    except Exception:
        return False

def get_credentials(force_refresh=False):
    """Get valid Windscribe proxy credentials {username, password}.
    Checks cached credentials with auto-validation; automatically auto-registers
    a fresh account on the fly whenever the cache is missing or expired."""
    import base64, urllib.request
    from pathlib import Path
    cache_path = Path.home() / ".cache/auto-freecf/windscribe_creds.json"
    if not force_refresh and cache_path.exists():
        try:
            d = json.loads(cache_path.read_text())
            if d.get("username") and d.get("password"):
                if check_credentials_valid(d):
                    return d
        except Exception:
            pass
    farm_file = Path("/home/amen/Auto-FreeCF-vendor-refs/artifacts/vpnre/ws_farm_accounts.jsonl")
    if farm_file.exists():
        for line in reversed(farm_file.read_text().splitlines()):
            if not line.strip():
                continue
            try:
                acc = json.loads(line)
                sh = acc.get("session_auth_hash")
                if not sh:
                    continue
                req = urllib.request.Request(
                    f"{API}/ServerCredentials?platform=chrome&{_qs()}",
                    headers={
                        "Authorization": f"Bearer {sh}",
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                        "Accept": "application/json",
                    },
                )
                res = json.loads(urllib.request.urlopen(req, timeout=10).read())
                creds = res.get("data")
                if creds and "username" in creds:
                    u = base64.b64decode(creds["username"]).decode()
                    p = base64.b64decode(creds["password"]).decode()
                    out = {"username": u, "password": p}
                    if check_credentials_valid(out):
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_text(json.dumps(out))
                        return out
            except Exception:
                continue
    try:
        import windscribe_account_farm as waf
        acc = waf.create_account()
        sh = acc["session_auth_hash"]
        req = urllib.request.Request(
            f"{API}/ServerCredentials?platform=chrome&{_qs()}",
            headers={
                "Authorization": f"Bearer {sh}",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        )
        res = json.loads(urllib.request.urlopen(req, timeout=10).read())
        creds = res.get("data")
        if creds and "username" in creds:
            u = base64.b64decode(creds["username"]).decode()
            p = base64.b64decode(creds["password"]).decode()
            out = {"username": u, "password": p}
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(out))
            return out
    except Exception:
        pass
    return None

def gap_by_hole_ring(bg_b64, piece_b64, top, *, lo=10):
    """Windscribe slider solver: scores candidate x by darkness inside the hole's
    black outline ring (dist 3-5px inside silhouette) minus half bright surround."""
    import base64, io
    try:
        from PIL import Image
        import numpy as np
    except Exception:  # noqa: BLE001
        return None
    try:
        bg = np.asarray(Image.open(io.BytesIO(base64.b64decode(bg_b64))).convert("L"), dtype=np.float32)
        pc = np.asarray(Image.open(io.BytesIO(base64.b64decode(piece_b64))).convert("RGBA"), dtype=np.float32)
    except Exception:  # noqa: BLE001
        return None
    alpha = pc[:, :, 3]
    ph, pw = alpha.shape
    H, W = bg.shape
    band = bg[top:top + ph]
    if band.shape[0] != ph:
        return None
    inside = alpha >= 180
    cur = ~inside
    dist = __import__("numpy").full(inside.shape, 99, dtype=np.int32)
    dist[cur] = 0
    for d in range(1, 7):
        nxt = cur.copy()
        nxt[1:, :] |= cur[:-1, :]; nxt[:-1, :] |= cur[1:, :]
        nxt[:, 1:] |= cur[:, :-1]; nxt[:, :-1] |= cur[:, 1:]
        dist[nxt & ~cur] = d
        cur = nxt
    hole_ring = (dist >= 3) & (dist <= 5)
    outer_ring = (dist >= 1) & (dist <= 2)
    best_x, best_s = lo, float("inf")
    for x in range(lo, W - pw - 5):
        patch = band[:, x:x + pw]
        s = float(patch[hole_ring].mean() - 0.5 * patch[outer_ring].mean())
        if s < best_s:
            best_s, best_x = s, x
    return best_x

def login(user, pw):
    """Full login: POST /AuthToken/login -> solve slider -> POST /Session."""
    import base64, hashlib, math, urllib.request
    UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    HDR = {"Origin": "chrome-extension://hnmpcagpplmpfojmgmnngilcnanddlhb",
           "User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"}
    r1 = urllib.request.Request(f"{API}/AuthToken/login?platform=chrome&{_qs()}",
        data=json.dumps({"username": user, "password": pw}).encode(), headers=HDR, method="POST")
    d1 = json.loads(urllib.request.urlopen(r1, timeout=15).read())["data"]
    cap = d1.get("captcha")
    if not cap:
        return d1
    sol = gap_by_hole_ring(cap["background"], cap["slider"], int(cap["top"]))
    target = sol * (221.0 / 700.0)
    steps = 25
    xs = [round(target * (1 - (1 - i / steps) ** 2)) for i in range(1, steps + 1)]
    ys = [round(120 + 3 * math.sin(i * 0.4)) for i in range(1, steps + 1)]
    tok = d1["token"]
    sig = hashlib.sha256((tok + "if_you_copy_this_you_might_die_a_painful_death").encode()).hexdigest()
    body = {"username": user, "password": pw, "session_type_id": 2,
            "secure_token": tok, "secure_token_sig": sig,
            "captcha_solution": sol, "captcha_trail": {"x": xs, "y": ys}}
    r2 = urllib.request.Request(f"{API}/Session?platform=chrome&{_qs()}",
        data=json.dumps(body).encode(), headers=HDR, method="POST")
    return json.loads(urllib.request.urlopen(r2, timeout=15).read())["data"]

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
