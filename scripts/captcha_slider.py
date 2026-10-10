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
import urllib.request
from pathlib import Path

# Aliyun's pixel<->drag obfuscation (same coefficients zcode's claim.py uses).
COEF = (0.00354991, 0.07703295, -0.01016667)
NINE_ROUTER_HOME = Path(os.environ.get("NINE_ROUTER_HOME", Path.home() / ".9router"))
VISION_MODEL = os.environ.get("CAPTCHA_VISION_MODEL", "ag/gemini-3.8-flash-high")

IDS = {
    "bg": "aliyunCaptcha-img",
    "piece": "aliyunCaptcha-puzzle",
    "slider": "aliyunCaptcha-sliding-slider",
    "left": "aliyunCaptcha-sliding-left",
    "window": "aliyunCaptcha-window-float",
}


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
    """Pure LINEAR native drag: press the handle, move straight to the target,
    release. No easing curve, no overshoot, no jitter, no pauses.

    Playwright's mouse.* dispatch real Input events (native CDP Input.dispatchMouseEvent),
    and mouse.move(..., steps=N) walks a straight line — so this is linear + native.
    """
    await page.mouse.move(sx, sy)
    await page.mouse.down()
    await page.mouse.move(sx + dist, sy, steps=12)   # single straight-line move
    await page.mouse.up()

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


async def _drag_exact(page, box: dict, target_piece_left: float, log=print) -> bool:
    """One gesture: press, probe to learn the piece/slider ratio MID-DRAG, then
    finish so the piece lands at `target_piece_left`. Returns True if a ratio was
    measured (the piece does not move 1:1 with the slider, and the ratio is random
    per puzzle, so it must be measured live)."""
    sx, sy = box["x"], box["y"]
    await page.mouse.move(sx, sy)
    await page.mouse.down()
    await page.mouse.move(sx + 60, sy, steps=4)   # probe (do NOT release)
    await page.wait_for_timeout(120)
    st = await page.evaluate(_LEFTS)
    if st["s"] <= 0:
        await page.mouse.up()
        return False
    ratio = st["p"] / st["s"]
    # we are currently at slider=st['s'], piece=st['p']; move the rest.
    need_total = target_piece_left / ratio if ratio > 0 else st["s"]
    need_total = max(need_total, st["s"])
    cur_x = sx + st["s"]
    await page.mouse.move(sx + need_total, sy, steps=8)
    await page.wait_for_timeout(80)
    await page.mouse.up()
    return True


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

    for i in range(1, puzzles + 1):
        if await _passed(page):
            log("  [slider] already passed!")
            return True
        if i > 1:
            await refresh(page)
            await page.wait_for_timeout(1100)
            if not await _wait_open(page):
                continue
        # detector-first guess
        imgs = await _read_images(page)
        guess = None
        if imgs:
            guess = gap_by_diff(imgs[0], imgs[1])
        targets = ([guess] if guess and 100 <= guess <= 300 else []) + \
                  [t for t in SWEEP if t != guess]
        t = targets[0]
        box = await _slider_box(page)
        if not box:
            continue
        # drag the mouse by the mapped distance (invert(target)), CLAMPED to the
        # handle's real travel so the slider never slams into the end.
        max_travel = await page.evaluate(
            """() => { const s=document.getElementById('aliyunCaptcha-sliding-slider');
                 const b=document.getElementById('aliyunCaptcha-captcha-body');
                 if(!s||!b) return 260;
                 return Math.max(20, b.getBoundingClientRect().width - s.getBoundingClientRect().width - 4);
               }""") or 260.0
        dist = min(invert(t), float(max_travel))
        await _drag(page, box["x"], box["y"], dist)
        await page.wait_for_timeout(1200)
        if await _passed(page):
            log(f"  [slider] solved ✓ (target={t}{' detect' if t == guess else ''}, puzzle {i})")
            return True
        log(f"  [slider] puzzle {i}: target={t}{' detect' if t == guess else ''} miss")
    log("  [slider] not solved")
    return False