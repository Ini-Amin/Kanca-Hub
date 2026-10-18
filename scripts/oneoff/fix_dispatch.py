#!/usr/bin/env python3
"""One-shot: rewrite cmd_dispatch with host-level scope enforcement + artifact pull-back."""
from pathlib import Path

P = Path("/home/amen/Auto-FreeCF/scripts/bounty_run.py")
s = P.read_text()

START = "def cmd_dispatch(a):"
END = "def cmd_triage(a):"
i = s.index(START)
j = s.index(END)

NEW = '''# ---------- scope enforcement (gate 1, host level) ----------
def _host_of(entry):
    """Normalise a scope entry or URL to a bare lowercase host."""
    e = (entry or "").strip().lower()
    if not e:
        return ""
    if "://" in e:
        e = e.split("://", 1)[1]
    e = e.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    e = e.split("@")[-1].split(":")[0]
    return e.strip(".")


def _matches(host, pattern):
    """Glob-ish host match. '*.x.com' matches only subdomains; 'x.com' matches
    the apex AND its subdomains (a scope entry names the asset, not one label)."""
    host, pattern = host.lower().strip("."), pattern.lower().strip(".")
    if not host or not pattern:
        return False
    if pattern.startswith("*."):
        base = pattern[2:]
        return host.endswith("." + base)
    return host == pattern or host.endswith("." + pattern)


def host_in_scope(host, in_scope, out_of_scope):
    """Fail-closed: deny wins, and anything unmatched is OUT.

    Returns (allowed: bool, reason: str).
    """
    h = _host_of(host)
    if not h:
        return False, "empty host"
    for pat in out_of_scope:
        p = _host_of(pat)
        if p and _matches(h, p):
            return False, "out-of-scope %s" % pat
    for pat in in_scope:
        p = _host_of(pat)
        if p and _matches(h, p):
            return True, "in-scope %s" % pat
    return False, "not in scope"


def filter_hosts(hosts, in_scope, out_of_scope):
    """Split discovered hosts into (allowed, rejected). Never silently drops."""
    allowed, rejected = [], []
    seen = set()
    for h in hosts:
        host = _host_of(h)
        if not host or host in seen:
            continue
        seen.add(host)
        ok, why = host_in_scope(host, in_scope, out_of_scope)
        (allowed if ok else rejected).append((host, why))
    return allowed, rejected


def _scannable(targets, in_scope, out_of_scope):
    """Seed targets that are hosts we can actually scan (skip app-store URLs)."""
    out = []
    for t in targets:
        h = _host_of(t)
        if not h or h in ("apps.apple.com", "play.google.com", "itunes.apple.com"):
            continue
        ok, _ = host_in_scope(h, in_scope, out_of_scope)
        if ok:
            out.append(h)
    return sorted(set(out))


def cmd_dispatch(a):
    d = job_dir(a.id)
    job = json.loads((d / "job.json").read_text())
    ins, outs = job["in_scope"], job["out_of_scope"]

    if a.dry_run:
        toolcheck = ("export PATH=\\"$HOME/go/bin:$HOME/.local/bin:$PATH\\"; "
                     "for t in subfinder httpx nuclei opengrep katana; do "
                     "command -v $t >/dev/null && echo -n \\"$t \\"; done; echo")
        if os.environ.get("BOUNTY_REMOTE"):
            r = subprocess.run(["sh", "-c", toolcheck], capture_output=True, text=True, timeout=120)
            print("box tools:", (r.stdout or "").strip())
        else:
            rc, out, err = ssh(BOX, toolcheck)
            print("blu tools:", out.strip() or ("ERR " + err.strip()))
        seeds = _scannable(job["targets"], ins, outs)
        print("gate 1 seeds in scope:", ", ".join(seeds) or "-")
        for t in job["targets"]:
            h = _host_of(t)
            if h and h not in seeds:
                print("  REJECTED seed:", t, "->", host_in_scope(h, ins, outs)[1])
        return

    # ---- Gate 1a: only in-scope SEEDS are ever enumerated ----
    seeds = _scannable(job["targets"], ins, outs)
    if not seeds:
        sys.exit("gate 1: no in-scope scannable seed targets")

    # ---- Phase A: passive enumeration only (subfinder sends no traffic to targets) ----
    roots = " ".join(seeds)
    phase_a = ("export PATH=\\"$HOME/go/bin:$HOME/.local/bin:$PATH\\"; "
               "cd /tmp && rm -rf bj && mkdir bj && cd bj; "
               "for h in %s; do subfinder -silent -d \\"$h\\" 2>/dev/null; done | sort -u > subs_raw.txt; "
               "wc -l < subs_raw.txt" % roots)
    if os.environ.get("BOUNTY_REMOTE"):
        r = subprocess.run(["sh", "-c", phase_a], capture_output=True, text=True, timeout=1500)
        raw = r.stdout or ""
    else:
        rc, raw, err = ssh(BOX, phase_a, timeout=1500)

    # subfinder prints the count last; everything before it is the host list
    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    hosts = [x for x in lines if re.match(r"^[\\w*][\\w.*-]*\\.[a-z]{2,}$", x, re.I)]
    (d / "recon").mkdir(parents=True, exist_ok=True)
    (d / "recon" / "subs_raw.txt").write_text("\\n".join(hosts) + ("\\n" if hosts else ""))

    # ---- Gate 1b: filter discovered hosts BEFORE any active tool sees them ----
    allowed, rejected = filter_hosts(hosts, ins, outs)
    (d / "recon" / "scope_rejected.txt").write_text(
        "\\n".join("%s\\t%s" % (h, why) for h, why in rejected) + ("\\n" if rejected else ""))
    print("gate 1: %d discovered, %d in scope, %d rejected" % (len(hosts), len(allowed), len(rejected)))
    for h, why in rejected[:15]:
        print("  REJECTED", h, "->", why)
    if len(rejected) > 15:
        print("  ... %d more in recon/scope_rejected.txt" % (len(rejected) - 15))
    if not allowed:
        job["status"] = "recon-empty"
        (d / "job.json").write_text(json.dumps(job, indent=1))
        sys.exit("gate 1: nothing survived scope filtering")

    # ---- Phase B: active scanning, ONLY on the filtered allow-list ----
    allow_file = "\\n".join(h for h, _ in allowed) + "\\n"
    phase_b = ("export PATH=\\"$HOME/go/bin:$HOME/.local/bin:$PATH\\"; cd /tmp/bj; "
               "httpx -silent -mc 200,201,301,302,403 < allow.txt 2>/dev/null | head -80 > live.txt; "
               "nuclei -silent -l live.txt -severity high,critical 2>/dev/null | head -60 > nuclei.txt; "
               "echo ---LIVE---; cat live.txt; echo ---NUCLEI---; cat nuclei.txt; echo ---END---")
    if os.environ.get("BOUNTY_REMOTE"):
        Path("/tmp/bj/allow.txt").write_text(allow_file)
        r = subprocess.run(["sh", "-c", phase_b], capture_output=True, text=True, timeout=1500)
        out = r.stdout or ""
    else:
        # ship the allow-list, then scan; single ssh round-trip via stdin
        cmd = ("cd /tmp/bj && cat > allow.txt && " + phase_b)
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", BOX, cmd],
                           input=allow_file, capture_output=True, text=True, timeout=1500)
        out = p.stdout or ""

    live, nuclei = [], []
    mode = None
    for line in out.splitlines():
        t = line.strip()
        if t == "---LIVE---":
            mode = "live"
            continue
        if t == "---NUCLEI---":
            mode = "nuclei"
            continue
        if t == "---END---":
            break
        if not t:
            continue
        if mode == "live":
            live.append(t)
        elif mode == "nuclei":
            nuclei.append(t)

    (d / "recon" / "live.txt").write_text("\\n".join(live) + ("\\n" if live else ""))
    (d / "recon" / "nuclei.txt").write_text("\\n".join(nuclei) + ("\\n" if nuclei else ""))
    (d / "recon" / "subs.txt").write_text("\\n".join(h for h, _ in allowed) + "\\n")
    print("live: %d, nuclei: %d" % (len(live), len(nuclei)))
    for x in live[:15]:
        print("  live", x)
    for x in nuclei[:10]:
        print("  FINDING", x)

    job["status"] = "recon-done"
    job["recon_counts"] = {"discovered": len(hosts), "in_scope": len(allowed),
                           "rejected": len(rejected), "live": len(live), "nuclei": len(nuclei)}
    (d / "job.json").write_text(json.dumps(job, indent=1))


'''

s = s[:i] + NEW + s[j:]
P.write_text(s)
print("rewrote cmd_dispatch, len", len(s))
