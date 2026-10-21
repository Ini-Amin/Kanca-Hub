#!/usr/bin/env python3
"""gig_exec — turn a discovered gig into an executed task, not just a link.

WHAT WAS WRONG
--------------
gig_finder.py produced a markdown list and stopped. The next step was always a
human reading a link, deciding, and starting from an empty directory. That is
the entire cost of the pipeline: discovery was already automated, delivery was
not.

WHAT THIS DOES
--------------
1. materialises a workspace per gig:   ~/.config/auto-freecf/gigs/work/<id>/
   with TASK.md (brief, acceptance, repo/branch, payout, source) — so the work
   is reproducible and reviewable instead of living in a chat log;
2. picks a runner that can actually act: a local coding CLI (opencode/claude/
   codex) if one is on PATH, otherwise the local 9router model for a written
   deliverable (proposal + plan), which is what a bidding marketplace needs
   before code anyway;
3. records the outcome in status.json — ran / why not / what it produced.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not submit anything on your behalf. Every platform needs a credential
we do not hold (Freelancer paid account, Algora GitHub token, Superteam wallet),
and a "submitted" flag we cannot verify is worse than no flag: it tells the
next step the work is done when nobody delivered it. `--submit` is refused
with the exact missing prerequisite instead of pretending success.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
CFG = HOME / ".config" / "auto-freecf"
GIGS = CFG / "gigs"
WORK = GIGS / "work"
SEEN_JSON = GIGS / "latest.json"
ROUTER = os.environ.get("BOUNTY_R9_BASE", "http://127.0.0.1:20128/v1")
MODEL = os.environ.get("GIG_MODEL", "THK/deepseek-v4.1-flash:free")
MAX_TASK_CHARS = 12000


def _load_env() -> None:
    """Read the same env file the hunt uses, so the key is not required in the
    caller's shell. Without this the model call 401s when gig_exec is started by
    systemd or a bare ssh command (measured: HTTP 401 Unauthorized)."""
    for path in (os.environ.get("ABH_ENV", ""), str(HOME / "abh.env"),
                 str(HOME / ".config" / "auto-freecf" / ".env")):
        if not path:
            continue
        p = Path(path)
        if not p.is_file():
            continue
        for line in p.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.removeprefix("export ").split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def load_gig(gig_id: str = "", url: str = "") -> dict:
    if not SEEN_JSON.exists():
        raise SystemExit(f"no gig store at {SEEN_JSON} — run gig_finder.py first")
    data = json.loads(SEEN_JSON.read_text() or "{}")
    items = data.values() if isinstance(data, dict) else data
    for g in items:
        if not isinstance(g, dict):
            continue
        if gig_id and g.get("id") != gig_id:
            continue
        if url and g.get("url") != url:
            continue
        if gig_id or url:
            return g
    raise SystemExit(f"gig not found: {gig_id or url}")


def which_runner() -> tuple[str, str]:
    """Return (kind, command). 'cli' can edit code; 'model' can only write docs."""
    for name in ("opencode", "claude", "codex"):
        p = shutil.which(name)
        if p:
            return "cli", p
    return "model", ""


def task_md(g: dict, runner: str) -> str:
    # gigs come from JSON, where a tuple is a list: coerce instead of assuming
    # `payout + (0, 0)` works (it raises on a list, which killed the first run).
    raw = g.get("usd") or []
    vals = list(raw) + [0, 0]
    lo, hi = int(vals[0] or 0), int(vals[1] or 0)
    lines = [
        f"# {g.get('title') or 'Gig'}",
        "",
        f"- **Sumber**: {g.get('src','?')}",
        f"- **URL**: {g.get('url','')}",
        f"- **Payout**: ${lo:,}–${hi:,}" if hi else "- **Payout**: tidak disebutkan",
        f"- **Bid**: {g.get('bids', 0)}",
        f"- **Runner**: {runner}",
        "",
        "## Brief dari sumber",
        "",
        (g.get("text") or "(deskripsi tidak tersedia dari feed; buka URL di atas)")[:4000],
        "",
        "## Definisi selesai",
        "",
        "1. Artefak ada di direktori ini dan bisa dijalankan/dibuka tanpa langkah manual yang tidak tertulis.",
        "2. Setiap klaim di dokumen hasil merujuk ke berkas atau perintah yang benar-benar dijalankan.",
        "3. Kalau pekerjaan menyentuh repo: satu branch, diff bisa dibaca, perintah verifikasi tertulis.",
        "4. Tulis `RESULT.md`: apa yang dikerjakan, apa yang diverifikasi, apa yang belum, dan langkah kirim ke platform.",
        "",
        "## Yang TIDAK boleh dilakukan",
        "",
        "- Mengubah kredensial, membuka PR ke repo pihak ketiga, atau menandai gig sebagai terkirim tanpa kredensial yang valid.",
        "- Menyalin teks dari sumber lain tanpa atribusi.",
    ]
    return "\n".join(lines)


def _decode_json(text: str) -> dict:
    """Decode a router body that may be SSE-framed or have a trailing `data: [DONE]`.

    9router answers a NON-streaming request with content-type text/event-stream
    and appends the SSE terminator to the JSON document, so `json.loads` raises
    "Extra data" (measured here as JSONDecodeError: Expecting value: line 1
    column 1 — after a `data:` prefix). Take the last `data:` frame that parses.
    """
    text = (text or "").strip()
    if not text:
        raise RuntimeError("empty response body")
    try:
        return json.loads(text)
    except ValueError:
        pass
    candidates = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
    for c in reversed(candidates):
        if not c or c == "[DONE]":
            continue
        try:
            return json.loads(c)
        except ValueError:
            continue
    start = text.find("{")
    if start >= 0:
        try:
            return json.JSONDecoder().raw_decode(text[start:])[0]
        except ValueError:
            pass
    raise RuntimeError(f"unparseable response: {text[:160]!r}")


def model_text(prompt: str, timeout=240) -> str:
    """One completion from the local router, with the same failover the hunt uses.

    Measured on blu: a single Zcode/GLM-5.3-Flash completion can exceed 120 s, and
    a hard 120 s timeout turned that into `state: failed` with no artifact at all.
    Failover is per model and per call, so one slow provider does not lose the
    workspace — the caller still gets whichever model answers first.
    """
    key = os.environ.get("OPENAI_API_KEY", "")
    chain = [MODEL] + [m.strip() for m in os.environ.get(
        "GIG_MODEL_FALLBACKS",
        "ag/gemini-3.8-flash-high,ocz/mimo-v2.6-flash-free"
    ).split(",") if m.strip() and m.strip() != MODEL]
    last_err = None
    for m in chain:
        body = json.dumps({
            "model": m,
            "messages": [
                {"role": "system", "content":
                 "You are a senior engineer writing a client-ready proposal. Be concrete, "
                 "no filler, no invented experience. Indonesian output, technical terms in English."},
                {"role": "user", "content": prompt[:MAX_TASK_CHARS]},
            ],
            "temperature": 0.3,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            ROUTER.rstrip("/") + "/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}" if key else ""})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
            payload = _decode_json(raw)
            text = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            if text.strip():
                if m != chain[0]:
                    say(f"  model fell back to {m}")
                return text
            last_err = RuntimeError("empty completion")
        except Exception as e:  # noqa: BLE001
            last_err = e
            say(f"  model {m} failed: {type(e).__name__}: {e}")
    raise last_err or RuntimeError("all models failed")


def run_cli(cmd_path: str, workspace: Path, prompt: str, timeout: int) -> tuple[int, str]:
    """Run a coding CLI headless inside the workspace."""
    name = Path(cmd_path).name
    if name == "opencode":
        argv = [cmd_path, "run", prompt]
    elif name == "claude":
        argv = [cmd_path, "-p", prompt, "--permission-mode", "acceptEdits"]
    else:  # codex
        argv = [cmd_path, "exec", prompt]
    say("  $", " ".join(argv[:3]), "...")
    p = subprocess.run(argv, cwd=str(workspace), capture_output=True, text=True,
                       timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


BOUNTY_PLATFORMS = {
    "bugcrowd": "bugcrowd", "hackerone": "hackerone",
    "intigriti": "intigriti", "yeswehack": "yeswehack",
}


def _program_slug(g: dict) -> str:
    """Bug-bounty gigs are identified by a program slug, not an id we invent.

    The feed URLs carry it as the last path segment (bugcrowd/<slug>,
    hackerone/<handle>, yeswehack.com/programs/<slug>), which is exactly what
    abh_hunt.py --program matches on.
    """
    url = (g.get("url") or "").rstrip("/")
    return url.rsplit("/", 1)[-1].lower() if "/" in url else ""


def execute(g: dict, dry: bool = False, timeout: int = 1800) -> dict:
    gid = str(g.get("id") or "unknown")
    wd = WORK / gid
    wd.mkdir(parents=True, exist_ok=True)
    kind, cmd = which_runner()
    (wd / "TASK.md").write_text(task_md(g, kind), encoding="utf-8")

    status = {"id": gid, "title": g.get("title"), "url": g.get("url"),
              "runner": kind, "started": int(time.time()), "state": "prepared"}

    platform = BOUNTY_PLATFORMS.get((g.get("src") or "").lower())
    slug = _program_slug(g) if platform else ""

    if dry:
        status["note"] = f"dry run: workspace prepared, runner={kind}"
        if platform and slug:
            status["note"] += f"; akan meneruskan ke hunt program={slug}"
        (wd / "status.json").write_text(json.dumps(status, indent=2))
        return status

    # A bounty gig's "deliverable" is a validated finding, not a document. The
    # hunt engine already does exactly that, so hand off instead of drafting a
    # proposal nobody asked for.
    if platform and slug and kind != "cli":
        status.update(runner="abh_hunt", program=slug, platform=platform)
        argv = [sys.executable, str(HERE / "abh_hunt.py"),
                "--platform", platform, "--program", slug,
                "--max-programs", "1", "--hours", "0.5", "--steps", "10"]
        say("  hand-off ->", " ".join(argv[1:]))
        try:
            p = subprocess.run(argv, cwd=str(HERE.parent), capture_output=True,
                               text=True, timeout=timeout)
            (wd / "agent.log").write_text((p.stdout or "") + (p.stderr or ""), encoding="utf-8")
            status.update(state="ran" if p.returncode == 0 else "failed", rc=p.returncode,
                          note="hunt engine dijalankan untuk program %s" % slug)
        except subprocess.TimeoutExpired:
            status.update(state="failed", note=f"hunt timed out after {timeout}s")
        except Exception as e:  # noqa: BLE001
            status.update(state="failed", note=f"{type(e).__name__}: {e}")
        status["ended"] = int(time.time())
        status["artifacts"] = sorted(p.name for p in wd.iterdir() if p.is_file())
        (wd / "status.json").write_text(json.dumps(status, indent=2))
        return status

    prompt = (wd / "TASK.md").read_text(encoding="utf-8") + (
        "\n\nKerjakan sekarang. Tulis RESULT.md di direktori ini setelah selesai.")

    if kind == "cli":
        try:
            rc, out = run_cli(cmd, wd, prompt, timeout)
            (wd / "agent.log").write_text(out[-200000:], encoding="utf-8")
            status.update(state="ran" if rc == 0 else "failed", rc=rc)
            status["note"] = f"coding CLI exited {rc}"
        except subprocess.TimeoutExpired:
            status.update(state="failed", note=f"runner timed out after {timeout}s")
        except Exception as e:  # noqa: BLE001
            status.update(state="failed", note=f"{type(e).__name__}: {e}")
    else:
        # No coding CLI here. Produce the two documents a bid actually needs,
        # and say plainly that code has to be written where a runner exists.
        try:
            plan = model_text(
                "Gig berikut perlu dikerjakan. Tulis rencana kerja teknis: langkah, "
                "risiko, estimasi waktu, dan pertanyaan yang harus dijawab klien.\n\n"
                + (wd / "TASK.md").read_text(encoding="utf-8"))
            (wd / "plan.md").write_text(plan, encoding="utf-8")
            proposal = model_text(
                "Tulis proposal singkat (maks 180 kata) untuk gig di bawah. "
                "Sebut hanya kemampuan yang bisa dibuktikan dari pekerjaan ini. "
                "Tanpa basa-basi, tanpa klaim palsu.\n\n"
                + (wd / "TASK.md").read_text(encoding="utf-8")[:6000])
            (wd / "proposal.md").write_text(proposal, encoding="utf-8")
            status.update(state="drafted", note="plan.md + proposal.md; kode perlu runner CLI")
        except Exception as e:  # noqa: BLE001
            status.update(state="failed", note=f"model call failed: {type(e).__name__}: {e}")

    status["ended"] = int(time.time())
    status["artifacts"] = sorted(p.name for p in wd.iterdir() if p.is_file())
    (wd / "status.json").write_text(json.dumps(status, indent=2))
    return status


def submit(g: dict) -> dict:
    """Refuse with the real reason instead of pretending a delivery happened."""
    platform = (g.get("src") or "").lower()
    need = {
        "freelancer": "akun Freelancer berbayar + bid manual (API tidak menerima bid tanpa membership)",
        "algora": "GitHub token di gh CLI (`gh auth login`) + repo akses write untuk membuka PR",
        "superteam": "wallet Solana yang terhubung + submit lewat halaman listing",
        "hackerone": "HackerOne API token (api.hackerone.com butuh kredensial untuk submit)",
        "bugcrowd": "sesi login Bugcrowd; submission tidak tersedia lewat API publik",
        "intigriti": "sesi login Intigriti",
        "yeswehack": "sesi login YesWeHack",
    }.get(platform, "kredensial platform tidak diketahui")
    return {"submitted": False, "platform": platform or "?", "prerequisite": need}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gig_exec.py",
                                description="Execute a discovered gig into a workspace")
    ap.add_argument("--id", default="", help="gig id from gig_finder")
    ap.add_argument("--url", default="", help="gig url, as an alternative to --id")
    ap.add_argument("--dry-run", action="store_true", help="prepare workspace only")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--submit", action="store_true", help="report what submission needs")
    a = ap.parse_args(argv)
    try:
        g = load_gig(a.id, a.url)
    except SystemExit as e:
        print(e)
        return 2
    if a.submit:
        print(json.dumps(submit(g), indent=2, ensure_ascii=False))
        return 1
    st = execute(g, dry=a.dry_run, timeout=a.timeout)
    print(json.dumps(st, indent=2, ensure_ascii=False))
    return 0 if st.get("state") in ("ran", "drafted", "prepared") else 1


if __name__ == "__main__":
    raise SystemExit(main())
