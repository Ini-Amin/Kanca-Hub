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
    """Locate where the piece belongs: the LOW-DETAIL (smeared/blank) band.

    The 'restore the image' widget blanks a vertical band of the picture and hands
    you that band as the piece. So the target is the smooth band INSIDE the busy
    subject. We take per-column horizontal detail, restrict to the subject's
    x-span (columns with real detail), and return the left edge of the piece-width
    window with the least detail.
    """
    try:
        from PIL import Image
        import numpy as np
    except Exception:  # noqa: BLE001
        return None
    try:
        bg = Image.open(io.BytesIO(_png_bytes(bg_b64))).convert("RGB")
        pc = Image.open(io.BytesIO(_png_bytes(piece_b64))).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    g = np.asarray(bg, dtype=np.float32).mean(axis=2)
    pw = int(np.asarray(pc).shape[1])
    H, W = g.shape
    if g.ndim != 2 or W < pw + 4 or pw < 16:
        return None
    col = np.abs(np.diff(g, axis=1)).sum(axis=0)  # detail per column
    thr = max(1.0, float(col.mean()))
    active = np.where(col > thr)[0]
    if active.size < 4:
        return None
    lo, hi = int(active.min()), int(active.max())
    pw = max(8, min(pw, W - 4))
    x_hi = min(hi, W - pw - 1)
    if x_hi <= lo:
        lo = max(0, W - pw - 1)
        x_hi = lo
    best, bx = None, lo
    for x in range(lo, min(hi, W - pw - 1) + 1):
        s = float(col[x:x + pw].sum())
        if best is None or s < best:
            best, bx = s, x
    return bx


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
        row = sqlite3.connect(NINE_ROUTER_HOME / "db" / "data.sqlite").execute(
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
    # 9Router streams SSE even with stream:false; stitch the content deltas.
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
            s = (json.loads(txt).get("choices") or [{}])[0].get("message", {}).get("content", "")
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
    """Direct, robotic drag -- human-likeness is NOT required.

    Hovering/lingering makes the Aliyun handle jitter, so we move on and pull in a
    single quick motion with no easing or pauses. Accuracy is all that matters.
    """
    await page.mouse.move(sx, sy)
    await page.mouse.down()
    await page.mouse.move(sx + dist, sy, steps=8)
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
    """Success = the page says so, or the puzzle window is gone."""
    t = (await page.evaluate("()=>document.body.innerText") or "").lower()
    if "verification passed" in t:
        return True
    return await page.evaluate(
        """(id) => { const w=document.getElementById(id);
             return !w || /hidden/.test(w.className) || w.getBoundingClientRect().width === 0; }""",
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


async def solve_aliyun(page, *, model: str = VISION_MODEL, log=print,
                       puzzles: int = 8, jitter: int = 14) -> bool:
    """Solve the Aliyun 'restore the image' slider, gently.

    Per puzzle: re-detect the blank band (diff, else vision) and make ONE careful
    humanized drag (plus tiny +/- jitters around it, not a fast spam sweep). If the
    puzzle is not passed, REFRESH to a fresh image and try again.
    """
    if not await _wait_open(page):
        log("  [slider] widget would not open")
        return False

    for i in range(1, puzzles + 1):
        imgs = await _read_images(page)
        if not imgs:
            await _wait_open(page)
            imgs = await _read_images(page)
        if not imgs:
            await refresh(page)
            await _wait_open(page)
            continue
        x = gap_by_diff(imgs[0], imgs[1])
        how = "diff"
        if x is None:
            x = gap_by_vision(imgs[0], imgs[1], model=model)
            how = "vision"
        if x is None:
            log(f"  [slider] puzzle {i}: not detected -> refresh")
            await refresh(page)
            continue
        log(f"  [slider] puzzle {i}: target x={x} ({how})")
        # A handful of PACED tries around the guess (detection is approximate);
        # not a fast sweep. Then move on to a fresh puzzle.
        for x2 in (x, x - 16, x + 16, x - 32, x + 32):
            box = await _slider_box(page)
            if not box:
                await _ensure_open(page)
                box = await _slider_box(page)
                if not box:
                    break
            await _drag(page, box["x"], box["y"], invert(x2))
            await page.wait_for_timeout(1600)  # human pace, no spam
            if await _passed(page):
                log("  [slider] solved ✓")
                return True
            # a miss usually auto-closes the puzzle: reopen + fetch a fresh image
            await _wait_open(page)
            await refresh(page)
            await _wait_open(page)
            imgs = await _read_images(page)
            if not imgs:
                break
        await page.wait_for_timeout(600)
    log("  [slider] not solved")
    return False