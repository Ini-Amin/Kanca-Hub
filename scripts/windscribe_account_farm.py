#!/usr/bin/env python3
"""Windscribe free-account farm — POST /Users (username+password only, no email/captcha).
Verified live 2026-10-14: 2GB/mo account, returns session_auth_hash for API calls.
Server limits: errorCode 707/429 rate limit (~1/30s burst), fresh time+hash per attempt.
Usage: windscribe_account_farm.py [count] [outfile]"""
import hashlib, json, os, random, string, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SECRET="952b4412f002315aa50751032fcaab03"
API="https://api.windscribe.com"

def _auth():
    t=str(int(time.time()))
    return {"time":t,"client_auth_hash":hashlib.md5((SECRET+t).encode()).hexdigest()}

def create_account():
    for attempt in range(5):
        try:
            from persona import username  # noqa: PLC0415
            user=username(max_len=18)
        except Exception:  # noqa: BLE001
            user="user_"+''.join(random.choices(string.ascii_lowercase+string.digits,k=8))
        pw=''.join(random.choices(string.ascii_letters+string.digits,k=12))+"A1"
        body={"username":user,"password":pw,**_auth(),"session_type_id":2}
        req=urllib.request.Request(f"{API}/Users", data=json.dumps(body).encode(),
            headers={"Content-Type":"application/json","Accept":"application/json"})
        try:
            resp=json.loads(urllib.request.urlopen(req, timeout=15).read())
        except urllib.error.HTTPError as e:
            if e.code in (429,400):
                time.sleep(15*(attempt+1)); continue
            raise
        if "errorCode" in resp:
            time.sleep(15*(attempt+1)); continue
        d=resp["data"]
        return {"user":user,"pw":pw,"uid":d["user_id"],
                "session_auth_hash":d["session_auth_hash"],
                "traffic_max":d["traffic_max"],"reg_date":d["reg_date"]}
    raise RuntimeError("rate limited after retries")

if __name__=="__main__":
    n=int(sys.argv[1]) if len(sys.argv)>1 else 1
    out=sys.argv[2] if len(sys.argv)>2 else "/home/amen/Auto-FreeCF-vendor-refs/artifacts/vpnre/ws_farm_accounts.jsonl"
    for i in range(n):
        try:
            a=create_account()
            with open(out,"a") as f: f.write(json.dumps(a)+"\n")
            print(f"[{i+1}/{n}] OK {a['user']} quota={a['traffic_max']//2**30}GB")
        except Exception as e:
            print(f"[{i+1}/{n}] FAIL {e}")
        if i<n-1: time.sleep(20)
