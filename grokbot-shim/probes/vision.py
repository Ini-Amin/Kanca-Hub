import base64, json, urllib.request
KEY="REDACTED"
def img(p): return base64.b64encode(open(p,'rb').read()).decode()
prompt = open('/tmp/vprompt.txt').read()
body={"model":"ag/gemini-3.8-flash-high","stream":True,"messages":[{"role":"user","content":[
 {"type":"text","text":prompt},
 {"type":"image_url","image_url":{"url":"data:image/png;base64,"+img("/tmp/full-desktop.png")}},
 {"type":"image_url","image_url":{"url":"data:image/png;base64,"+img("/tmp/full-mobile.png")}}]}],"temperature":0.2}
req=urllib.request.Request("http://localhost:20128/v1/chat/completions",data=json.dumps(body).encode(),
 headers={"Content-Type":"application/json","Authorization":"Bearer "+KEY})
out=[]
for line in urllib.request.urlopen(req,timeout=600):
    line=line.decode().strip()
    if not line.startswith("data: ") or line=="data: [DONE]": continue
    try: c=json.loads(line[6:])
    except: continue
    d=c.get("choices",[{}])[0].get("delta",{}).get("content")
    if d: out.append(d)
print("".join(out))
