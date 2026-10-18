import subprocess

def split_hunks(path):
    out = subprocess.run(["git", "diff", "-U3", "--", path], capture_output=True, text=True).stdout
    lines = out.splitlines(keepends=True)
    preamble, hunks, cur_head, cur_body, in_hunk = [], [], [], [], False
    for line in lines:
        if line.startswith("@@"):
            if cur_head: hunks.append((cur_head, cur_body))
            cur_head, cur_body, in_hunk = [line], [], True
        elif in_hunk: cur_body.append(line)
        else: preamble.append(line)
    if cur_head: hunks.append((cur_head, cur_body))
    return preamble, hunks

# --- egress.py: keep only relay rewrite hunk ---
pre, hunks = split_hunks("scripts/egress.py")
keep = [i for i,(h,b) in enumerate(hunks) if any("def pipe(src, dst)" in l for l in b)]
print("egress keep hunks:", keep)
patch = "".join(pre)
for i in keep:
    patch += "".join(hunks[i][0]) + "".join(hunks[i][1])
open("/tmp/egress_relay.patch","w").write(patch)

# --- zcode_bridge_server.py: inspect each hunk for ownership ---
pre2, hunks2 = split_hunks("scripts/zcode_bridge_server.py")
for i,(h,b) in enumerate(hunks2):
    first = h[0].strip()
    body_txt = "".join(b)
    print(f"--- zcode hunk {i}: {first}")
    print("    has-3012-doc:", "3012" in body_txt, "| has-app_code-parse:", "app_code = bj" in body_txt,
          "| has-Connection-reset:", "Connection reset" in body_txt,
          "| has-MAX_BODY:", "MAX_BODY_BYTES" in body_txt,
          "| has-UpstreamBusy:", "UpstreamBusy" in body_txt,
          "| has-_check_body_size:", "_check_body_size" in body_txt,
          "| has-gated:", "gated_call_upstream" in body_txt)
