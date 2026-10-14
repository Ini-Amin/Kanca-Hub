#!/usr/bin/env python3
"""VeePN free HTTPS-proxy flow (decoded from extension 5.0.3, see vendor-refs reports).
Usage: veepn_free_proxy.py [region]  -> prints proxy list; --test tunnels example.com."""
import json, urllib.request, urllib.parse, uuid, sys, time, socket, ssl, base64, hashlib

API_DOMAINS=["https://bitphox.com","https://tronyza.com","https://freloop.com","https://hisball.com","https://tronlit.com","https://hibchr.com"]
DEV={"udid":str(uuid.uuid4()),"appVersion":"5.0.3","platform":"chrome",
     "platformVersion":"Chrome/120.0.0.0","timeZone":"Asia/Jakarta","deviceName":"Chrome 120.0"}

def _req(base, path, body=None, token=None, method=None):
    h={"Content-Type":"application/json","User-Agent":"Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0"}
    if token: h["Authorization"]=f"Bearer {token}"
    data=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(base+path, data=data, headers=h, method=method or ("POST" if data else "GET"))
    return json.loads(urllib.request.urlopen(r, timeout=15).read())

def api(path, body=None, token=None, method=None):
    last=None
    for base in API_DOMAINS:
        try: return _req(base, path, body, token, method)
        except urllib.error.HTTPError as e:
            last=e
            if e.code in (403,429): time.sleep(1); continue  # CF-blocked/rate-limited: rotate
            raise
    raise last

def get_servers(region="nl"):
    tok=api("/v3/launch/", DEV)["access"]
    return api("/v3/server/list/", {**DEV,"protocol":"https","region":region,"type":0}, token=tok)

def test_tunnel(srv):
    host, port = srv["addresses"][0], srv["port"]
    s=socket.create_connection((host,port),timeout=12)
    ss=ssl.create_default_context().wrap_socket(s,server_hostname=host)
    auth=base64.b64encode(f"{srv['username']}:{srv['password']}".encode()).decode()
    ss.sendall(f"CONNECT example.com:80 HTTP/1.1\r\nHost: example.com:80\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode())
    buf=b""
    while b"\r\n\r\n" not in buf: buf+=ss.recv(200)
    return buf.startswith(b"HTTP/1.1 200")

if __name__=="__main__":
    region=sys.argv[1] if len(sys.argv)>1 else "nl"
    servers=get_servers(region)
    json.dump(servers, open("veepn_servers.json","w"), indent=1)
    print(f"{len(servers)} servers for {region}:")
    for s in servers: print(f"  https {s['addresses'][0]}:{s['port']} ({s['regionName']})")
    if "--test" in sys.argv:
        print("tunnel:", "OK" if test_tunnel(servers[0]) else "FAIL")
