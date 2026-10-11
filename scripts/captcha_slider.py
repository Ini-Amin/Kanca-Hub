#!/usr/bin/env python3
"""
scripts/captcha_slider.py — Aliyun Captcha 2.0 slider solver (shared).

Used by both the z.ai account signup and the ZCode plan claim. The puzzle is the
"drag the slider to restore the complete image" widget: a background image with a
missing strip and a puzzle piece to place into it.

Gap detection: image diff first (cheap, deterministic), then a 9Router vision
model (`ag/gemini-3.8-flash-high`) if the diff is not confident. The drag is
humanized: eased motion with a small overshoot and back, plus a sine wobble,
mirroring zcode's claim.py.

Aliyun DOM ids (confirmed live on chat.z.ai):
    aliyunCaptcha-img            background  (base64 png)
    aliyunCaptcha-puzzle         puzzle piece (base64 png)
    aliyunCaptcha-sliding-slider the handle to drag
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
import os
import random
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

# Aliyun's pixel<->drag obfuscation (same coefficients zcode's claim.py uses).
# Fitted 2026-10-11 from 78 mid-drag probes in ~/.zcode-bridge/slider_dataset.jsonl
# (rms 0.17px): the old blind guess (b=0.077) was ~18% off, which is why one-shot
# drags never landed. Closed-loop probing corrects live, so this is only the
# first-move prior now.
COEF = (0.0036, 0.0632, 0.59)
NINE_ROUTER_HOME = Path(os.environ.get("NINE_ROUTER_HOME", Path.home() / ".9router"))
VISION_MODEL = os.environ.get("CAPTCHA_VISION_MODEL", "ag/gemini-3.8-flash-high")

IDS = {
    "bg": "aliyunCaptcha-img",
    "piece": "aliyunCaptcha-puzzle",
    "slider": "aliyunCaptcha-sliding-slider",
    "left": "aliyunCaptcha-sliding-left",
    "window": "aliyunCaptcha-window-float",
}

# ──────────────────────────────────────────────────────────── dataset capture
# Every solve attempt (signup, mint, claim, harvest) appends one JSONL record and
# stores the puzzle images once. This is the raw material for (a) fitting the
# real piece-vs-slider curve per site, (b) labeling the gap detector, and
# (c) tuning the sweep prior. Inspect with:  python captcha_slider.py analyze
DATA_DIR = Path(os.environ.get("SLIDER_DATA_DIR", Path.home() / ".zcode-bridge" / "slider_data"))
DATA_LOG = DATA_DIR.parent / "slider_dataset.jsonl"


def _save_puzzle_images(puzzle_id: str, bg_b64: str, piece_b64: str) -> dict:
    """Store both puzzle images (once per puzzle) -> relative paths."""
    key = hashlib.sha1(puzzle_id.encode()).hexdigest()[:16]
    out = {}
    for name, b64 in (("bg", bg_b64), ("piece", piece_b64)):
        fp = DATA_DIR / ("%s_%s.png" % (key, name))
        if not fp.exists():
            try:
                fp.write_bytes(_png_bytes(b64))
            except Exception:  # noqa: BLE001
                continue
        out[name] = fp.name
    return out


def log_attempt(site: str, puzzle_id: str, *, target: int | None, guess: int | None,
                probes: list, passed: bool, released: bool, images: dict | None) -> None:
    """Append one attempt record. `probes` = [(slider_left, piece_left), ...]
    sampled mid-drag: this is the calibration data for the movement curve."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": round(time.time(), 1),
            "site": site,
            "puzzle": puzzle_id,
            "target": target,
            "detector_guess": guess,
            "probes": [[round(s, 1), round(p, 1)] for s, p in probes],
            "released": released,
            "passed": passed,
            "images": images or {},
        }
        with open(DATA_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001
        pass


def invert(left_px: float) -> float:
    """Slider drag distance that moves the piece to pixel column `left_px`."""
    a, b, c = COEF
    d = b * b - 4 * a * (c - left_px)
    return (-b + math.sqrt(max(d, 0))) / (2 * a)


# ─────────────────────────────────────────────────────────── gap detection
def _png_bytes(b64: str) -> bytes:
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    b64 = b64.strip().replace("\n", "").replace(" ", "")
    b64 = b64.replace("-", "+").replace("_", "/")
    b64 += "=" * (-len(b64) % 4)
    return base64.b64decode(b64)


def gap_by_diff(bg_b64: str, piece_b64: str) -> int | None:
    """Locate where the piece belongs by detecting what is missing/inpainted in the scene.

    The piece has an alpha mask indicating its shape and vertical span.
    Depending on object geometry and visual context:
    1. Tall vertical hanging objects (aspect ratio > 3.0, e.g. hanging lanterns)
       align with vertical context structures outside the slice band.
    2. Small symbols / numerals (pw <= 16, e.g. clock digits) occupy a sharp
       detail valley in the right-hand subject dial.
    3. Standard objects (wheels, hubcaps, bow-ties, etc.) correspond to the
       inpainting anomaly / minimum texture variance inside the object's alpha mask.
    """
    try:
        from PIL import Image
        import numpy as np
    except Exception:  # noqa: BLE001
        return None
    try:
        bg = Image.open(io.BytesIO(_png_bytes(bg_b64))).convert("RGB")
        pc = Image.open(io.BytesIO(_png_bytes(piece_b64)))  # preserve RGBA
    except Exception:  # noqa: BLE001
        return None

    bg_arr = np.asarray(bg, dtype=np.float32)
    pc_arr = np.asarray(pc)
    H, W = bg_arr.shape[:2]
    pw = int(pc_arr.shape[1])
    if pw < 4 or W < pw + 10:
        return None

    # Determine vertical bounding box of the non-transparent piece
    if pc_arr.ndim == 3 and pc_arr.shape[2] == 4:
        alpha = pc_arr[:, :, 3]
        y_nz = np.where(alpha > 40)[0]
        if y_nz.size > 0:
            y_min, y_max = int(y_nz.min()), int(y_nz.max())
            mask = alpha[y_min : y_max + 1, :] > 40
        else:
            y_min, y_max = 0, H - 1
            mask = np.ones((H, pw), dtype=bool)
    else:
        y_min, y_max = 0, H - 1
        mask = np.ones((H, pw), dtype=bool)

    h = y_max - y_min + 1
    aspect_ratio = h / max(1, pw)

    # 1. Tall vertical hanging objects (aspect ratio > 3.0, e.g. hanging lanterns)
    if aspect_ratio > 3.0:
        outside_mask = np.ones(H, dtype=bool)
        outside_mask[y_min : y_max + 1] = False
        g_out = bg_arr[outside_mask, :, :].mean(axis=2)
        dx_out = np.abs(np.diff(g_out, axis=1)).sum(axis=0)
        out_sums = np.array([dx_out[x : x + pw].sum() for x in range(len(dx_out) - pw)])
        lo = max(180, 0)
        hi = min(265, len(out_sums))
        if hi > lo:
            return lo + int(np.argmax(out_sums[lo:hi]))

    # 2. Small symbols / numerals (pw <= 16, e.g. clock digits)
    # Target gap in the right half of the subject dial
    if pw <= 16:
        g_band = bg_arr[y_min : y_max + 1, :, :].mean(axis=2)
        dx_band = np.abs(np.diff(g_band, axis=1)).sum(axis=0)
        band_sums = np.array([dx_band[x : x + pw].sum() for x in range(len(dx_band) - pw)])
        lo = 170
        hi = min(220, len(band_sums))
        if hi > lo:
            return lo + int(np.argmin(band_sums[lo:hi]))

    # 3. Standard objects (wheels, hubcaps, bow-ties, etc.):
    # Find minimum texture variance inside the object's alpha mask in typical puzzle range [130, 240]
    lo = max(130, 0)
    hi = min(240, W - pw)
    if hi > lo and np.any(mask):
        stds = {}
        for x in range(lo, hi):
            patch = bg_arr[y_min : y_max + 1, x : x + pw]
            stds[x] = patch[mask].std()
        if stds:
            return min(stds, key=stds.get)

    # Fallback: minimum column detail in subject
    g_full = bg_arr.mean(axis=2)
    dx_full = np.abs(np.diff(g_full, axis=1)).sum(axis=0)
    full_sums = np.array([dx_full[x : x + pw].sum() for x in range(len(dx_full) - pw)])
    lo = max(100, 0)
    hi = min(250, len(full_sums))
    if hi > lo:
        return lo + int(np.argmin(full_sums[lo:hi]))

    return lo

def _r9_key(base: str = "http://localhost:20128") -> str:
    env = os.environ.get("NINE_ROUTER_KEY")
    if env:
        return env.strip()
    try:
        mid = (NINE_ROUTER_HOME / "machine-id").read_text().strip()
        sec = (NINE_ROUTER_HOME / "auth" / "cli-secret").read_text().strip()
        tok = hashlib.sha256((mid + "9r-cli-auth" + sec).encode()).hexdigest()[:16]
        req = urllib.request.Request(f"{base}/api/keys", headers={"x-9r-cli-token": tok})
        keys = json.loads(urllib.request.urlopen(req, timeout=8).read() or "{}").get("keys", [])
        for k in keys:
            if k.get("key") and k.get("isActive", True):
                return k["key"]
    except Exception:  # noqa: BLE001
        pass
    try:
        con = sqlite3.connect(NINE_ROUTER_HOME / "db" / "data.sqlite")
        row = con.execute("SELECT key FROM apiKeys WHERE isActive=1 LIMIT 1").fetchone()
        if row:
            return row[0]
        row = con.execute(
            "SELECT data FROM providerConnections WHERE isActive=1 LIMIT 1").fetchone()
        if row:
            return json.loads(row[0]).get("apiKey", "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def gap_by_vision(bg_b64: str, piece_b64: str, *, model: str = VISION_MODEL,
                  base: str = "http://localhost:20128", timeout: float = 60) -> int | None:
    """Ask a vision model where the piece belongs. Returns the x pixel, or None."""
    key = _r9_key(base)
    if not key:
        return None
    if "," not in bg_b64:
        bg_b64 = "data:image/png;base64," + bg_b64
    if "," not in piece_b64:
        piece_b64 = "data:image/png;base64," + piece_b64
    ask = (
        "Task: Solve an Aliyun inpaint restore slider puzzle.\n"
        "Image 1 is the 300x150 background canvas with a displaced vertical slice.\n"
        "Image 2 is the puzzle slice.\n"
        "Find the horizontal X pixel column (integer between 10 and 290) in Image 1 "
        "where Image 2 restores the background.\n"
        "Return ONLY the single integer X coordinate. Example: 146"
    )
    body = {
        "model": model, "stream": False, "max_tokens": 20,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": ask},
            {"type": "image_url", "image_url": {"url": bg_b64}},
            {"type": "image_url", "image_url": {"url": piece_b64}},
        ]}],
    }
    req = urllib.request.Request(f"{base.rstrip('/')}/v1/chat/completions",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode()
    except Exception:  # noqa: BLE001
        return None
    # 9Router may return plain JSON OR an SSE stream; handle both.
    s = ""
    if txt.lstrip().startswith("data:"):
        for line in txt.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                j = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            for ch in j.get("choices") or []:
                s += ((ch.get("delta") or {}).get("content")) or ""
    else:
        try:
            j = json.loads(txt)
            s = (j.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        except Exception:  # noqa: BLE001
            return None
    import re
    m = re.search(r"\d+", s or "")
    return int(m.group(0)) if m else None


# ─────────────────────────────────────────────────────────── browser driving
async def _read_images(page) -> tuple[str, str] | None:
    """Read both widget images as base64 data URLs.

    The first puzzle may arrive inline (data: URL), but refreshed / post-fail
    puzzles are served from the cross-origin CDN (static-captcha-sgp.aliyuncs.com),
    which TAINTS a canvas (SecurityError). That CDN does send permissive CORS
    headers, so fetch() (or a crossOrigin <img>) recovers the bytes. Returns None
    while the widget is closed or the images are still loading.
    """
    return await page.evaluate(
        """async (ids) => {
            const g = id => document.getElementById(id);
            const bg = g(ids.bg), pc = g(ids.piece);
            if (!bg || !pc) return null;
            const r = bg.getBoundingClientRect();
            if (!r.width) return null;
            if (!bg.complete || !pc.complete || !bg.naturalWidth || !pc.naturalWidth) return null;
            const b64 = (buf) => { let s=''; const b=new Uint8Array(buf);
                for (let i=0;i<b.length;i+=8192)
                    s+=String.fromCharCode.apply(null, b.subarray(i,i+8192));
                return 'data:image/png;base64,'+btoa(s); };
            const viaCanvas = (el) => { try {
                const w=el.naturalWidth, h=el.naturalHeight;
                const c=document.createElement('canvas'); c.width=w; c.height=h;
                c.getContext('2d').drawImage(el,0,0,w,h); return c.toDataURL('image/png');
            } catch(e) { return null; } };
            const viaFetch = async (url) => { const r = await fetch(url);
                return b64(await r.arrayBuffer()); };
            const viaCors = (url) => new Promise((resolve) => {
                const im = new Image(); im.crossOrigin = 'anonymous';
                im.onload = () => { try {
                    const c=document.createElement('canvas');
                    c.width=im.naturalWidth; c.height=im.naturalHeight;
                    c.getContext('2d').drawImage(im,0,0); resolve(c.toDataURL('image/png'));
                } catch(e){ resolve(null); } };
                im.onerror = () => resolve(null); im.src = url; });
            const one = async (el) => {
                const u = el.currentSrc || el.src || '';
                if (u.startsWith('data:')) return viaCanvas(el);
                try { return await viaFetch(u); } catch (e) { /* fall through */ }
                return await viaCors(u);
            };
            const b = await one(bg); const p = await one(pc);
            if (!b || !p) return null;
            return [b, p];
        }""", IDS)


async def _puzzle_id(page):
    """A per-puzzle fingerprint: the widget's certifyId text plus the piece
    image URL/size (both change on every refresh / post-fail swap)."""
    return await page.evaluate(
        """(ids) => {
            const pc = document.getElementById(ids.piece);
            const c = document.getElementById('aliyunCaptcha-certifyId');
            return JSON.stringify({
                cid: c ? (c.textContent||c.innerText||'').trim() : null,
                src: pc ? String(pc.currentSrc||pc.src||'').slice(-48) : null,
                w: pc ? pc.naturalWidth : 0,
            });
        }""", IDS)


async def _piece_geom(page):
    """Displayed width/height/natural size/current left of the puzzle element."""
    return await page.evaluate(
        """(ids) => {
            const pc = document.getElementById(ids.piece);
            if (!pc) return null;
            const r = pc.getBoundingClientRect();
            const bg = document.getElementById(ids.bg);
            const b = bg ? bg.getBoundingClientRect() : null;
            return {
                natW: pc.naturalWidth, natH: pc.naturalHeight,
                left: r.left, top: r.top, w: r.width, h: r.height,
                bgLeft: b ? b.left : null,
                relLeft: b != null ? r.left - b.left : null,
                relTop: b != null ? r.top - b.top : null,
            };
        }""", IDS)


async def _drag(page, sx: float, sy: float, dist: float) -> None:
    """Smooth LINEAR native drag.

    Playwright dispatches real Input events; a finer straight-line walk (many
    steps + tiny inter-step delay) gives a smooth, steady motion with no easing
    curve, no overshoot and no jitter.
    """
    await page.mouse.move(sx, sy)
    await page.mouse.down()
    steps = 30
    for i in range(1, steps + 1):
        await page.mouse.move(sx + dist * (i / steps), sy)
        await asyncio.sleep(0.006)
    await page.mouse.up()
    await asyncio.sleep(0.2)

async def _slider_box(page):
    return await page.evaluate(
        """(id) => { const s=document.getElementById(id);
             if(!s) return null; const r=s.getBoundingClientRect();
             return (r.width>0 && r.y>0 && r.y<950)
               ? {x:r.x+r.width/2, y:r.y+r.height/2} : null; }""", IDS["slider"])


async def _reopen(page):
    """Dismiss and re-open the puzzle: close any open window, then click the bar."""
    await page.evaluate(
        """() => { const c=document.getElementById('aliyunCaptcha-btn-close');
             if (c && c.offsetParent) c.click(); }""")
    await _sleep(0.3)
    for sel in ("#aliyunCaptcha-captcha-body", "#aliyunCaptcha-captcha-wrapper"):
        try:
            await page.click(sel, timeout=2000)
            break
        except Exception:  # noqa: BLE001
            continue


async def _sleep(sec: float) -> None:
    import asyncio as _a
    await _a.sleep(sec)


async def _passed(page) -> bool:
    """Success = the page says so (English or Chinese), or the puzzle window is gone."""
    t = (await page.evaluate("()=>document.body.innerText") or "").lower()
    if any(k in t for k in ("verification passed", "验证通过", "验证成功")):
        return True
    return await page.evaluate(
        """(id) => { const w=document.getElementById(id);
             return !w || /hidden/.test(w.className) || getComputedStyle(w).display === 'none' || w.getBoundingClientRect().width === 0; }""",
        IDS["window"])

async def _ensure_open(page) -> None:
    """Open the puzzle window if it is closed (click the 'start verification' bar)."""
    for sel in ("#aliyunCaptcha-captcha-body", "#aliyunCaptcha-captcha-wrapper", "#captcha-element"):
        try:
            await page.click(sel, timeout=2000)
            await page.wait_for_timeout(1200)
            return
        except Exception:  # noqa: BLE001
            continue


async def _wait_open(page, tries: int = 6) -> bool:
    """Click the 'start verification' bar until the puzzle window is actually up."""
    for _ in range(tries):
        if await _read_images(page):
            return True
        for sel in ("#aliyunCaptcha-captcha-body", "#aliyunCaptcha-captcha-wrapper", "#captcha-element"):
            try:
                await page.click(sel, timeout=2000)
                break
            except Exception:  # noqa: BLE001
                continue
        await page.wait_for_timeout(1500)
    return bool(await _read_images(page))


async def refresh(page) -> None:
    """Ask the widget for a fresh puzzle (top-right refresh button)."""
    await page.evaluate(
        """() => { const r=document.getElementById('aliyunCaptcha-btn-refresh');
             if (r) r.click(); }""")
    await _sleep(1.5)


_LEFTS = """()=>{const p=document.getElementById('aliyunCaptcha-puzzle');const s=document.getElementById('aliyunCaptcha-sliding-slider');return {p:parseFloat((p&&p.style.left)||'0')||0, s:parseFloat((s&&s.style.left)||'0')||0};}"""


async def _drag_exact(page, box: dict, target_piece_left: float, log=print) -> tuple[bool, list]:
    """One gesture: press, then ITERATIVELY probe the piece's own style.left
    mid-drag and correct until within ~1.5px, then release.

    The piece does not move 1:1 with the slider: the map is a per-puzzle
    quadratic (Aliyun's obfuscation), so a single ratio probe only linearizes
    one point and systematically misses -- which is why the blind invert() drag
    never landed. Re-fitting the local slope on every probe converges in 3-4
    probes regardless of the coefficients.

    Returns (released_ok, probes) where probes = [(slider_left, piece_left), ...]
    -- the calibration samples we log to the dataset.
    """
    sx, sy = box["x"], box["y"]
    await page.mouse.move(sx, sy)
    await page.mouse.down()
    await page.mouse.move(sx + 40, sy, steps=4)   # probe (do NOT release)
    await page.wait_for_timeout(120)
    probes = []
    s_prev = None
    for it in range(10):
        st = await page.evaluate(_LEFTS)
        p, s = st["p"], st["s"]
        if s <= 0:
            break                                   # widget not draggable
        probes.append((s, p))
        err = target_piece_left - p
        if abs(err) <= 1.5:
            await page.mouse.up()
            await asyncio.sleep(0.25)
            return True, probes
        # local slope from the last two probes (fallback: assume ~1:1 once)
        slope = None
        if len(probes) >= 2:
            (s0, p0), (s1, p1) = probes[-2], probes[-1]
            ds, dp = s1 - s0, p1 - p0
            if abs(ds) > 0.5 and abs(dp) > 0.3:
                slope = dp / ds
        step = err / slope if slope and slope > 0.05 else err
        step = max(-80, min(220, step))
        if s_prev is not None and abs(s - s_prev) < 0.5 and abs(step) > 1:
            break                                   # slider clamped: unreachable
        s_prev = s
        await page.mouse.move(sx + s + step, sy, steps=5)
        await page.wait_for_timeout(80)
    # never release on a bad aim: a miss swaps in a NEW puzzle and burns the
    # attempt anyway, but an unreachable target should just be abandoned.
    await page.mouse.up()
    await asyncio.sleep(0.25)
    return False, probes


async def solve_aliyun(page, *, model: str = VISION_MODEL, log=print,
                       puzzles: int = 20, jitter: int = 16) -> bool:
    """Solve via MEASURE-AND-CORRECT: the piece moves at a small random fraction
    of the slider, so we learn the ratio mid-drag and land the piece on the target.
    Targets = invert() of the gaps our labels show (165-245). One accurate drag per
    fresh puzzle; refresh between attempts (a mouse-up submits).
    """
    # target = where the piece's LEFT should end up. Order: detector guess first
    # (blur/smear metric is right ~25% and exact when right), then a data-driven
    # sweep of the observed range.
    SWEEP = [205, 195, 185, 215, 225, 235, 175, 165, 245, 155, 240, 170, 250, 160]

    if not await _wait_open(page):
        log("  [slider] widget would not open")
        return False

    seen_widget = True   # only trust _passed() once we know the widget existed
    missed = False       # after one miss, spend a vision call on later puzzles
    for i in range(1, puzzles + 1):
        if seen_widget and await _passed(page):
            log("  [slider] already passed!")
            return True
        # NOTE: on a MISS the Aliyun widget REFRESHES ITSELF to a new puzzle.
        # Calling refresh() again would refresh TWICE -- so we only re-open.
        if not await _wait_open(page):
            await refresh(page)
            await page.wait_for_timeout(900)
            if not await _wait_open(page):
                seen_widget = False
                continue
        seen_widget = True
        # detector first; after one miss, spend a vision call -- the diff
        # detector alone lands ~30% (dataset-measured), vision is the real
        # lever now that the drag itself is closed-loop.
        imgs = await _read_images(page)
        pid = await _puzzle_id(page)
        guess = None
        if imgs:
            guess = gap_by_diff(imgs[0], imgs[1])
            if (missed or guess is None) and model:
                try:
                    v = gap_by_vision(imgs[0], imgs[1], model=model)
                except Exception:  # noqa: BLE001
                    v = None
                if v and 100 <= v <= 300:
                    guess = v
        targets = ([guess] if guess and 100 <= guess <= 300 else []) + \
                  [t for t in SWEEP if t != guess]
        t = targets[0]
        box = await _slider_box(page)
        if not box:
            continue
        # CLOSED-LOOP landing: press, probe the piece's real position mid-drag,
        # correct, release. invert()/COEF only matter as a rough prior now --
        # the probes measure the true per-puzzle movement curve live.
        released, probes = await _drag_exact(page, box, float(t), log)
        imgs = imgs or await _read_images(page)
        images = _save_puzzle_images(pid, imgs[0], imgs[1]) if imgs else {}
        await page.wait_for_timeout(1200)
        ok = seen_widget and await _passed(page)
        log_attempt(page.url.split("?")[0], pid, target=t, guess=guess,
                    probes=probes, passed=ok, released=released, images=images)
        if ok:
            log(f"  [slider] solved ✓ (target={t}{' detect' if t == guess else ''}, puzzle {i})")
            return True
        log(f"  [slider] puzzle {i}: target={t}{' detect' if t == guess else ''} miss")
        missed = True
    log("  [slider] not solved")
    return False


# ─────────────────────────────────────────────────────────────── dataset CLI
async def _open_signup_widget(page) -> bool:
    """(Re)mount the chat.z.ai signup form so the inline widget appears."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import zai_signup as ZS
    try:
        await page.goto("https://chat.z.ai/auth", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        # same DOM-click sequence as signup: reveal the email form, then Create
        # Account mode -- the inline widget only mounts after both.
        await page.get_by_text("Continue with Email").first.click(timeout=5000)
        await page.wait_for_timeout(2000)
        await page.evaluate(ZS._JS_CLICK, "Sign up")
        await page.wait_for_timeout(2500)
        return True
    except Exception:  # noqa: BLE001
        return False


async def harvest(count: int = 15, *, headless: bool = False) -> int:
    """Farm labeled puzzles: open the chat.z.ai SIGNUP form (its inline widget
    needs no account), then solve -> refresh -> solve ... Every attempt lands
    closed-loop and is logged with its images and probe curve."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from playwright.async_api import async_playwright

    p = await async_playwright().start()
    try:
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=4000)
    except Exception:  # noqa: BLE001
        import zcode as Z            # _ensure_cdp_chrome() knows the executable
        await Z._ensure_cdp_chrome()
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=8000)
    ctx = b.contexts[0] if b.contexts else await b.new_context()
    page = await ctx.new_page()
    await _open_signup_widget(page)

    solved = attempts = remounts = 0
    while attempts < count:
        if not await _wait_open(page, tries=3):
            await refresh(page)
            await page.wait_for_timeout(1200)
        if not await _wait_open(page, tries=2):
            # a PASS locks the inline widget until the form remounts
            remounts += 1
            if remounts > count + 3:
                print("[harvest] widget unavailable after repeated remounts, stopping")
                break
            await _open_signup_widget(page)
            continue
        ok = await solve_aliyun(page, puzzles=3)
        attempts += 1
        solved += int(ok)
        if attempts < count:
            await refresh(page)
            await page.wait_for_timeout(1500)
    print("[harvest] done: %d/%d solved, dataset=%s" % (solved, attempts, DATA_LOG))
    return solved


def analyze() -> None:
    """Dataset report: pass rate, detector accuracy, and a least-squares refit
    of the piece-vs-slider quadratic from every mid-drag probe."""
    if not DATA_LOG.exists():
        print("no dataset yet at", DATA_LOG)
        return
    recs = [json.loads(l) for l in open(DATA_LOG, encoding="utf-8") if l.strip()]
    n = len(recs)
    hits = [r for r in recs if r.get("passed")]
    det = [r for r in recs if r.get("detector_guess") is not None]
    det_hits = [r for r in det if r.get("passed") and r.get("target") == r.get("detector_guess")]
    print("attempts: %d   passed: %d (%.0f%%)" % (n, len(hits), 100.0 * len(hits) / max(1, n)))
    print("detector used: %d   detector-correct: %d (%.0f%%)"
          % (len(det), len(det_hits), 100.0 * len(det_hits) / max(1, len(det))))
    # fit p = a*s^2 + b*s + c over all probes (this is what invert() inverts)
    pts = [(s, p) for r in recs for s, p in r.get("probes", []) if s > 0]
    if len(pts) >= 6:
        import statistics
        best = None
        for a10 in range(0, 60):   # grid-search the quadratic term a
            a = a10 / 10000.0
            xs = [s for s, _ in pts]
            ys = [p - a * s * s for s, p in pts]
            mx, my = statistics.mean(xs), statistics.mean(ys)
            sxx = sum((x - mx) ** 2 for x in xs)
            if sxx == 0:
                continue
            b1 = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
            c1 = my - b1 * mx
            err = sum((p - (a * s * s + b1 * s + c1)) ** 2 for s, p in pts) / len(pts)
            if best is None or err < best[0]:
                best = (err, a, b1, c1)
        if best:
            err, a, b1, c1 = best
            print("probe samples: %d   fitted COEF = (%.8f, %.8f, %.8f)  rms=%.2fpx"
                  % (len(pts), a, b1, c1, err ** 0.5))
            print("current  COEF = (%.8f, %.8f, %.8f)" % COEF)
            print("-> paste the fitted tuple into COEF if rms is low and it differs")
    # target distribution of the passing aims = better sweep prior
    if hits:
        tg = sorted(r["target"] for r in hits if r.get("target"))
        print("passing targets (sweep prior):", tg)


def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Aliyun slider solver / dataset tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("harvest", help="farm labeled puzzles into the dataset")
    s.add_argument("--count", type=int, default=15)
    s.add_argument("--headless", action="store_true")
    sub.add_parser("analyze", help="dataset report + COEF refit")
    a = ap.parse_args(argv)
    if a.cmd == "harvest":
        return asyncio.run(harvest(a.count, headless=a.headless))
    analyze()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())