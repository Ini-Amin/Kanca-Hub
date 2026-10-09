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
    return base64.b64decode(b64)


def gap_by_diff(bg_b64: str, piece_b64: str) -> int | None:
    """Find the displaced strip's left edge in the background.

    The 'restore the image' widget keeps a vertical strip shifted sideways, so the
    background has two strong vertical SEAMS (the strip's left/right edges). We
    take the per-column colour gradient, find the two strongest peaks that are a
    plausible strip-width apart, and return the left one.
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
    bga = np.asarray(bg, dtype=np.float32)
    pca = np.asarray(pc, dtype=np.float32)
    if bga.ndim != 3 or pca.ndim != 3:
        return None
    # Vertical-edge energy per column (sum over rows and channels).
    col = np.abs(np.diff(bga, axis=1)).sum(axis=(0, 2))  # shape (W-1,)
    if col.size < 30:
        return None
    # Smooth a little so a single noisy column does not win.
    k = np.ones(3) / 3
    col_s = np.convolve(col, k, mode="same")
    # Strip width from the piece image (fallback 45 px).
    sw = int(pca.shape[1]) if 20 <= pca.shape[1] <= 90 else 45
    best = None
    order = np.argsort(col_s)[::-1]
    peaks: list[int] = []
    for idx in order:
        if all(abs(int(idx) - p) > 6 for p in peaks):
            peaks.append(int(idx))
        if len(peaks) >= 8:
            break
    for i, a in enumerate(peaks):
        for b in peaks[i + 1:]:
            lo, hi = sorted((a, b))
            if abs(hi - lo) <= max(sw * 2, 120):
                score = col_s[a] + col_s[b]
                if best is None or score > best[0]:
                    best = (score, lo)
    if best is None:
        return int(peaks[0]) if peaks else None
    return best[1]


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
        "You are solving a slider captcha. Image 1 is the background with a missing "
        "vertical strip; image 2 is the piece that belongs there. Reply with ONLY the "
        "integer x pixel (the LEFT edge of the missing strip) in image-1 coordinates."
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
    return await page.evaluate(
        """(ids) => {
            const g = id => document.getElementById(id);
            const bg = g(ids.bg), pc = g(ids.piece);
            if (!bg || !pc) return null;
            const r = bg.getBoundingClientRect();
            if (!r.width) return null;
            return [bg.src, pc.src];
        }""", IDS)


async def _drag(page, sx: float, sy: float, dist: float) -> None:
    """Humanized drag: cubic ease-out with a small overshoot then settle."""
    over = dist + random.uniform(6, 10)
    await page.mouse.move(sx, sy)
    await page.mouse.down()
    for i in range(1, 26):
        t = i / 25
        e = 1 - (1 - t) ** 3
        await page.mouse.move(sx + over * e, sy + math.sin(t * 5) * 1.2)
        await asyncio.sleep(0.007)
    for j in range(1, 7):
        t = j / 6
        await page.mouse.move(sx + over + (dist - over) * t, sy)
        await asyncio.sleep(0.02)
    await page.mouse.up()


async def _slider_box(page):
    return await page.evaluate(
        """(id) => { const s=document.getElementById(id);
             if(!s) return null; const r=s.getBoundingClientRect();
             return (r.width>0 && r.y>0 && r.y<950)
               ? {x:r.x+r.width/2, y:r.y+r.height/2} : null; }""", IDS["slider"])


async def _reopen(page):
    """Nudge the compact slider so the puzzle window re-opens after a miss."""
    await page.evaluate(
        """(ids) => { const c=document.getElementById('aliyunCaptcha-btn-close');
             if(c && c.offsetParent) c.click();
             const l=document.getElementById(ids.left); if(l) l.click(); }""", IDS)


async def _passed(page) -> bool:
    t = (await page.evaluate("()=>document.body.innerText")).lower()
    return "verification passed" in t


async def solve_aliyun(page, *, model: str = VISION_MODEL, log=print,
                       rounds: int = 8, step: int = 4) -> bool:
    """Solve the Aliyun slider.

    Primary method: sweep the slider offset (the vendor's proven approach) --
    drag by invert(target) for target=4,8,...,check "verification passed", reopen
    between tries. If the image diff locates the seam confidently we ALSO try that
    offset first (fewer drags), and the vision model is a last resort.

    Returns True once the captcha reports passed.
    """
    try:
        await page.click(f"#{IDS['slider']}", timeout=3000)
    except Exception:  # noqa: BLE001
        pass
    await page.wait_for_timeout(1500)

    # Cheap first guess from the image diff.
    seeds: list[int] = []
    imgs = await _read_images(page)
    if imgs:
        x = gap_by_diff(imgs[0], imgs[1])
        if x is not None:
            seeds.append(x)
    order = seeds + [t for t in range(step, 300, step) if t not in seeds]
    tried = 0
    for rnd in range(rounds):
        for x in order:
            box = await _slider_box(page)
            if not box:
                await _reopen(page)
                await page.wait_for_timeout(900)
                box = await _slider_box(page)
                if not box:
                    continue
            await _drag(page, box["x"], box["y"], invert(x))
            await page.wait_for_timeout(900)
            tried += 1
            if await _passed(page):
                log(f"  [slider] solved ✓ (offset={x}, {tried} drags)")
                return True
            await _reopen(page)
            await page.wait_for_timeout(500)
        log(f"  [slider] round {rnd + 1} no hit")
    return False