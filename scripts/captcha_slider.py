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
import re
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# Aliyun's pixel<->drag obfuscation (same coefficients zcode's claim.py uses).
# Fitted 2026-10-11 from 78 mid-drag probes in ~/.zcode-bridge/slider_dataset.jsonl
# (rms 0.17px): the old blind guess (b=0.077) was ~18% off, which is why one-shot
# drags never landed. Closed-loop probing corrects live, so this is only the
# first-move prior now.
COEF = (0.0036, 0.06365, 0.5728)  # refit from 1294 probes (rms 0.17px)
# Confidence gate: when gap_by_diff and flat_dark_guess disagree by more than
# this many px, skip the (benchmark ~50%) drag and go straight to vision.
DISAGREE_GATE = 4
NINE_ROUTER_HOME = Path(os.environ.get("NINE_ROUTER_HOME", Path.home() / ".9router"))
# HTTP vision was a trap: every free vision model on the pool (cbai/glm-5v-turbo,
# qwen3-vl, kimi-k2.5, glm-4.6v-flash) answers with a round constant (100/146/150)
# instead of reading the seam -- measured 12/12 wrong, 0 exact on the labels, and
# the old prompt's "Example: 146" made it worse (94 dataset rows all aimed at 150).
# Worse, the confidence gate let that constant OVERRIDE a good detector guess, so
# the solver dragged to 150 and missed ~always. Vision is therefore OFF by default;
# set CAPTCHA_VISION_MODEL to re-enable it behind a real (verified) model.
VISION_MODEL = os.environ.get("CAPTCHA_VISION_MODEL", "")
# Accepted landing range for a gap X. The 41 user-verified labels span 15..240, so
# clamping to [100,300] silently threw away the low-X puzzles (x=15/81/89/...).
GAP_MIN, GAP_MAX = 0, 300

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
        lo = max(145, 0)
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
    # Score texture variance + darkness + center prior inside the object's alpha mask in [130, 240].
    # ponytail: fixed linear weights (std + 0.15*mean + 3.0*prior); upgrade path: logistic column classifier if >1k labels.
    lo = max(130, 0)
    hi = min(240, W - pw)
    if hi > lo and np.any(mask):
        stds = {}
        for x in range(lo, hi):
            patch = bg_arr[y_min : y_max + 1, x : x + pw]
            stds[x] = patch[mask].std() + 0.15 * patch[mask].mean() + 3.0 * ((x - 175.0) / 50.0) ** 2
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

def flat_dark_guess(bg_b64: str, piece_b64: str) -> int | None:
    """Second-opinion scorer for the confidence gate in solve_aliyun().

    The gap is a dark textureless overlay, so rank full-width candidates by
    (std + 0.5*mean) inside the piece's alpha band. Deliberately diverse from
    gap_by_diff (no geometry windows; uses darkness). On the 35-label
    benchmark: 11/35 alone, union with gap_by_diff covers 25/35 (71%), and
    when the two AGREE (<=4px) precision is 8/10 (80%).
    """
    try:
        from PIL import Image
        import numpy as np
    except Exception:  # noqa: BLE001
        return None
    try:
        bg = Image.open(io.BytesIO(_png_bytes(bg_b64))).convert("L")
        pc = Image.open(io.BytesIO(_png_bytes(piece_b64)))
    except Exception:  # noqa: BLE001
        return None
    b = np.asarray(bg, dtype=np.float32)
    H, W = b.shape
    a = np.asarray(pc)
    pw = int(a.shape[1])
    if pw < 4 or W < pw + 12:
        return None
    if a.ndim == 3 and a.shape[2] == 4:
        alpha = a[:, :, 3]
        rows = np.where(alpha > 40)[0]
        y0, y1 = (int(rows.min()), int(rows.max())) if rows.size else (0, H - 1)
        mask = alpha[y0:y1 + 1] > 40
    else:
        y0, y1 = 0, H - 1
        mask = np.ones((H, pw), dtype=bool)
    if not mask.any():
        return None
    best_x, best_s = None, None
    for x in range(40, min(260, W - pw - 4)):
        patch = b[y0:y1 + 1, x:x + pw][mask]
        s = float(patch.std()) + 0.25 * float(patch.mean())
        if best_s is None or s < best_s:
            best_s, best_x = s, x
    return best_x

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


def gap_by_label(bg_b64: str, piece_b64: str) -> int | None:
    """Answer from the accumulated label store: if this exact puzzle image was
    solved/labeled before, reuse the known X (content-hash keyed).

    This is the RLHF payoff -- every pass and every user-verified label makes
    future repeats FREE and exact. Store lives in the slider folder
    (slider_labels_store.json); see slider_attempts.mine_labels().
    """
    store = _label_store()
    if not store:
        return None
    h = hashlib.sha256(_png_bytes(bg_b64)).hexdigest()[:16]
    v = store.get(h)
    if v is None:
        return None
    try:
        x = int(v.get("x") if isinstance(v, dict) else v)
    except (TypeError, ValueError):
        return None
    return x if GAP_MIN <= x <= GAP_MAX else None


_LABEL_STORE_CACHE: dict | None = None


def _label_store() -> dict:
    global _LABEL_STORE_CACHE
    if _LABEL_STORE_CACHE is not None:
        return _LABEL_STORE_CACHE
    out: dict = {}
    for name in ("slider_verified.json", "slider_labels_store.json",
                 "slider_mined.json"):
        p = DATA_DIR.parent / name
        if not p.exists():
            continue
        try:
            raw = json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            continue
        for k, v in (raw or {}).items():
            if k in out:
                continue
            # verified/mined key on the image id (not a content hash); map both
            out.setdefault(k, v)
            fp = DATA_DIR / f"{k}_bg.png"
            if fp.exists():
                try:
                    out[hashlib.sha256(fp.read_bytes()).hexdigest()[:16]] = v
                except OSError:
                    pass
    _LABEL_STORE_CACHE = out
    return out


def gap_by_vision(bg_b64: str, piece_b64: str, *, model: str = VISION_MODEL,
                  base: str = "http://localhost:20128", timeout: float = 15) -> int | None:
    """Ask a vision model where the piece belongs. Returns the x pixel, or None."""
    key = _r9_key(base)
    if not key:
        return None
    if "," not in bg_b64:
        bg_b64 = "data:image/png;base64," + bg_b64
    if "," not in piece_b64:
        piece_b64 = "data:image/png;base64," + piece_b64
    ask = (
        "Look at the two images.\n"
        "Image 1 is a photo with a vertical strip cut out and inpainted (the strip "
        "leaves a visible seam / texture discontinuity).\n"
        "Image 2 is the strip that was removed.\n"
        "Give the horizontal pixel X (0-299) of the LEFT edge of the cut in Image 1.\n"
        "Answer with ONE integer only, no words.\n"
        "IMPORTANT: the answer varies per image. Do NOT default to a round number; "
        "measure the seam you actually see."
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


def gap_by_cmd(bg_path, piece_path, *, timeout: float = 45.0) -> int | None:
    """Generic vision seam: run $SLIDER_VISION_CMD -- a command template with
    {bg} / {piece} placeholders -- and parse the integer it prints on stdout.
    Anything can back it (local model, team helper, HTTP shim); the repo stays
    agnostic. Unset or failing -> None, callers fall back to the model route."""
    tpl = os.environ.get("SLIDER_VISION_CMD", "").strip()
    if not tpl:
        return None
    try:
        argv = [a.format(bg=str(bg_path), piece=str(piece_path))
                for a in shlex.split(tpl)]
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        m = re.search(r"\d+", out.stdout or "")
        v = int(m.group(0)) if m else None
    except Exception:  # noqa: BLE001
        return None
    return v if v is not None and 10 <= v <= 290 else None


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


_PASS_TEXT = ("slide successful", "滑动成功", "verification passed",
              "验证通过", "验证成功")


async def _passed(page) -> bool:
    """Success = the page says so (English or Chinese), or the puzzle window is gone."""
    t = (await page.evaluate("()=>document.body.innerText") or "").lower()
    if any(k in t for k in _PASS_TEXT):
        return True
    return await page.evaluate(
        """(id) => { const w=document.getElementById(id);
             return !w || /hidden/.test(w.className) || getComputedStyle(w).display === 'none' || w.getBoundingClientRect().width === 0; }""",
        IDS["window"])


# ─────────────────────────────────────────── real pass/fail oracle (z.ai)
# _passed() alone is NOT trustworthy on z.ai: the SDK's success toast is
# "Slide successful!" and z.ai's success callback immediately refreshes the
# widget, so the window stays VISIBLE and _passed() returns False on a REAL
# pass -- which makes solve_aliyun abandon a passed captcha and abort signups.
# Two signals ARE trustworthy and are used here:
#   1. the Aliyun verify API response body: Result.VerifyResult (bool) /
#      Result.VerifyCode ("T001" = pass, "F015" = fail). Works retroactively --
#      no init-script timing needed.
#   2. the SDK's own success/fail callbacks, wrapped page-side (best effort;
#      only catches callbacks registered after the wrap is installed).
_ORACLE_HOOK = r"""() => {
  if (window.__capOracle) return 'already';
  const st = {npass: 0, nfail: 0, nerr: 0, last: null, at: 0};
  window.__capOracle = st;
  const wrapCfg = (cfg) => {
    try {
      if (cfg && typeof cfg === 'object') {
        const s = cfg.success, f = cfg.fail, e = cfg.onError;
        if (typeof s === 'function' && !s.__capW) {
          const w = function () { st.npass++; st.last = 'success'; st.at = Date.now();
            return s.apply(this, arguments); };
          w.__capW = true; cfg.success = w;
        }
        if (typeof f === 'function' && !f.__capW) {
          const w = function () { st.nfail++; st.last = 'fail'; st.at = Date.now();
            return f.apply(this, arguments); };
          w.__capW = true; cfg.fail = w;
        }
        if (typeof e === 'function' && !e.__capW) {
          const w = function () { st.nerr++; st.last = 'error'; st.at = Date.now();
            return e.apply(this, arguments); };
          w.__capW = true; cfg.onError = w;
        }
      }
    } catch (err) {}
    return cfg;
  };
  let _raw = null;
  try {
    Object.defineProperty(window, 'initAliyunCaptcha', {
      configurable: true,
      get() { return _raw; },
      set(fn) {
        if (typeof fn === 'function' && !fn.__capW) {
          const w = function (cfg) { return fn.call(this, wrapCfg(cfg)); };
          w.__capW = true; _raw = w;
        } else { _raw = fn; }
      },
    });
  } catch (err) {}
  return 'hooked';
}"""

_ORACLE_READ = ("() => { const s = window.__capOracle || {}; "
                "return {npass: s.npass|0, nfail: s.nfail|0, nerr: s.nerr|0, last: s.last||null}; }")


class _SliderOracle:
    """Pass/fail from (a) the SDK success/fail callbacks and (b) the verify API.

    Construct once per page, await install(), then snapshot() before a drag and
    poll after it. The verify-API listener works even when the page-side hook
    missed the SDK's one-time initAliyunCaptcha call.
    """

    def __init__(self, page):
        self.page = page
        self.events: list[dict] = []
        try:
            page.on("response", lambda r: asyncio.ensure_future(self._on_response(r)))
        except Exception:  # noqa: BLE001
            pass

    async def install(self) -> None:
        try:
            await self.page.add_init_script(_ORACLE_HOOK)
        except Exception:  # noqa: BLE001
            pass
        try:
            await self.page.evaluate(_ORACLE_HOOK)
        except Exception:  # noqa: BLE001
            pass

    async def _on_response(self, resp) -> None:
        try:
            url = resp.url
            if "captcha-open" not in url or "verify" not in url:
                return
            if resp.request.method != "POST":
                return
            try:
                body = await resp.text()
            except Exception:  # noqa: BLE001
                return
            j = {}
            try:
                j = json.loads(body)
            except Exception:  # noqa: BLE001
                pass
            # The verdict is NESTED: top-level Success/Code only mean "the API
            # call worked"; a wrong drag still returns Success=true and only
            # Result.VerifyResult=false (VerifyCode F015 = fail, T001 = pass).
            res = j.get("Result") if isinstance(j.get("Result"), dict) else {}
            vr = res.get("VerifyResult")
            if vr is None:
                vr = j.get("VerifyResult")
            vcode = str(res.get("VerifyCode") or j.get("VerifyCode") or "")
            if not isinstance(vr, bool):
                m = re.search(r'"VerifyResult"\s*:\s*(true|false)', body, re.I)
                if m:
                    vr = m.group(1).lower() == "true"
                elif vcode:
                    vr = vcode[:1].upper() == "T"
                else:
                    return  # not a verdict-bearing response (e.g. InitCaptchaV3)
            self.events.append({"t": time.time(), "ok": bool(vr), "code": vcode})
        except Exception:  # noqa: BLE001
            pass

    async def snapshot(self) -> dict:
        try:
            o = await self.page.evaluate(_ORACLE_READ)
        except Exception:  # noqa: BLE001
            o = {}
        return {"pass": int(o.get("npass") or 0), "fail": int(o.get("nfail") or 0),
                "err": int(o.get("nerr") or 0), "last": o.get("last"),
                "net": len(self.events)}


async def _verdict(page, oracle, before: dict, timeout: float = 3.0) -> bool:
    """True once ANY trustworthy signal says the drag PASSED.

    Order: SDK success callback > verify API > success toast > legacy _passed()
    (window gone). Returns False on an explicit fail, or after `timeout`.
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        now = await oracle.snapshot()
        if now["pass"] > before["pass"]:
            return True
        if now["fail"] > before["fail"]:
            return False
        if now["net"] > before["net"]:
            return bool(oracle.events[-1]["ok"])
        try:
            txt = (await page.evaluate("()=>document.body.innerText") or "").lower()
        except Exception:  # noqa: BLE001
            txt = ""
        if any(k in txt for k in _PASS_TEXT):
            return True
        if await _passed(page):
            return True
        await page.wait_for_timeout(250)
    return False

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


async def _get_oracle(page):
    """One _SliderOracle per page (installs the hook + response listener once)."""
    orc = getattr(page, "_cap_oracle", None)
    if orc is None:
        orc = _SliderOracle(page)
        await orc.install()
        try:
            page._cap_oracle = orc
        except Exception:  # noqa: BLE001
            pass
    return orc


async def _wait_variant(page, tries: int = 20) -> bool:
    """Wait until the SDK has rendered either surface, so the variant is known.

    `solve_aliyun` is called the instant the popup opens, when the DOM is still
    empty; probing then says "not a checkbox" and the slider path waits forever
    for images that will never exist.
    """
    for _ in range(tries):
        try:
            st = await page.evaluate(
                """() => {
                    const cb = document.getElementById('aliyunCaptcha-checkbox-icon');
                    const img = document.getElementById('aliyunCaptcha-img');
                    return {cb: !!cb, img: !!img};
                }""")
        except Exception:  # noqa: BLE001
            return False
        if st.get("cb") or st.get("img"):
            return True
        try:
            await page.wait_for_timeout(1000)
        except Exception:  # noqa: BLE001
            return False
    return False


async def _checkbox_present(page) -> bool:
    """True when the widget is the checkbox variant (no puzzle image at all).

    Presence only — the icon is 0x0 for the first second or two while the SDK
    lays the popup out, so a size test here would fall through to the slider
    path and wait forever for images that never exist.
    """
    try:
        return bool(await page.evaluate(
            """() => {
                const i = document.getElementById('aliyunCaptcha-checkbox-icon');
                const img = document.getElementById('aliyunCaptcha-img');
                return !!i && !img;
            }"""))
    except Exception:  # noqa: BLE001
        return False


async def solve_checkbox(page, *, log=print, tries: int = 20) -> bool:
    """Tick the 'Confirm you are not a robot' checkbox.

    Two measured traps:
      * the icon has zero size for the first second or two while the SDK lays
        the popup out — a click then lands on nothing, and AutoClaw's page reads
        the disappearance as a *dismissal* and cancels the attempt;
      * the click must be a real pointer sequence (move → down → up), not a DOM
        `.click()`, or the SDK's behavioural check ignores it.
    """
    for i in range(tries):
        try:
            await page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001 — window closed = the tick landed
            log("  [checkbox] window closed")
            return True
        try:
            box = await page.evaluate(
                """() => {
                    const el = document.getElementById('aliyunCaptcha-checkbox-icon');
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    if (r.width < 2 || r.height < 2) return null;
                    return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                }""")
        except Exception:  # noqa: BLE001 — window closed = the tick landed
            log("  [checkbox] window closed")
            return True
        if not box:
            continue
        await page.wait_for_timeout(700)
        try:
            await page.mouse.move(box["x"], box["y"], steps=12)
            await page.wait_for_timeout(300)
            await page.mouse.down()
            await page.wait_for_timeout(90)
            await page.mouse.up()
        except Exception:  # noqa: BLE001
            log("  [checkbox] click failed (window closed)")
            return True
        log(f"  [checkbox] ticked at {box} (attempt {i})")
        # success = the popup goes away; a miss leaves it up for a retry
        for _ in range(6):
            await page.wait_for_timeout(700)
            try:
                gone = await page.evaluate(
                    """() => {
                        const w = document.getElementById('aliyunCaptcha-window-popup');
                        const i = document.getElementById('aliyunCaptcha-checkbox-icon');
                        return !w || !i || w.getBoundingClientRect().width === 0;
                    }""")
            except Exception:  # noqa: BLE001
                return True
            if gone:
                log("  [checkbox] solved ✓")
                return True
    log("  [checkbox] not solved")
    return False


async def solve_aliyun(page, *, model: str = VISION_MODEL, log=print,
                       puzzles: int = 20, jitter: int = 16) -> bool:
    """Solve whichever Aliyun widget the page shows.

    Aliyun Captcha 2.0 renders one of two surfaces for the same SceneId:

    * **slider** — `aliyunCaptcha-img` + `aliyunCaptcha-puzzle` + a drag handle.
      Solved by measure-and-correct below.
    * **checkbox** — just `aliyunCaptcha-checkbox-icon` ("Confirm you are not a
      robot"), no image at all. AutoClaw's app uses this one, which is why
      `solve_aliyun` used to report "widget would not open" against it: there
      are no images to diff, so the slider path can never start.

    The checkbox path needs one *trusted* click on the icon, and only after the
    widget has laid out — clicking early lands on nothing and the app treats the
    attempt as dismissed.
    """
    try:
        if await _wait_variant(page) and await _checkbox_present(page):
            return await solve_checkbox(page, log=log)
    except Exception:  # noqa: BLE001 — the popup closed under us
        log("  [captcha] window closed")
        return True

    # target = where the piece's LEFT should end up. Order: detector guess first
    # (blur/smear metric is right ~25% and exact when right), then a data-driven
    # sweep of the observed range. The 41 user labels span 15..240 (7 below
    # 130), so the old 155-min sweep could never reach the low-X puzzles; the
    # band below widens downward while keeping the dense 150-240 core first.
    SWEEP = [205, 195, 185, 215, 175, 165, 225, 235, 155, 145, 135, 245, 125,
             240, 170, 250, 160, 115, 105, 95, 85, 75, 65, 55, 45, 35, 25, 15]

    if not await _wait_open(page):
        log("  [slider] widget would not open")
        return False

    oracle = await _get_oracle(page)
    seen_widget = True   # only trust _passed() once we know the widget existed
    missed = False       # after one miss, spend a vision call on later puzzles
    for i in range(1, puzzles + 1):
        if seen_widget and await _verdict(page, oracle, await oracle.snapshot(),
                                          timeout=0.4):
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
        # detector first; after one miss, ask for help: the generic
        # $SLIDER_VISION_CMD seam (anything can back it -- e.g. a local team
        # helper), then the built-in model route. The diff detector alone lands
        # ~30% (dataset-measured) and the drag is already closed-loop, so
        # detection is the only error left.
        imgs = await _read_images(page)
        pid = await _puzzle_id(page)
        images = _save_puzzle_images(pid, imgs[0], imgs[1]) if imgs else {}
        guess = None
        from_label = False
        if imgs:
            # 0. Label store first: a puzzle we've already solved/labeled is a
            #    free, exact answer (the RLHF reuse payoff). Content-hash keyed.
            guess = gap_by_label(imgs[0], imgs[1])
            from_label = guess is not None
            if guess is None:
                guess = gap_by_diff(imgs[0], imgs[1])
            # Confidence gate: two diverse scorers agreeing => ~80% precision
            # (benchmark); disagreeing => ~50%, so skip the doomed drag and
            # spend the vision call before touching the slider. NOTE: the HTTP
            # model route is OFF by default (free pool models answer a round
            # constant); the $SLIDER_VISION_CMD seam still works if set, and
            # neither ever overrides a label-store hit (already exact).
            g2 = flat_dark_guess(imgs[0], imgs[1]) if guess is not None else None
            if not from_label and (missed or guess is None
                    or (g2 is not None and abs(guess - g2) > DISAGREE_GATE)):
                v = None
                if images:
                    v = gap_by_cmd(DATA_DIR / images["bg"], DATA_DIR / images["piece"])
                if not v and model:
                    try:
                        v = gap_by_vision(imgs[0], imgs[1], model=model)
                    except Exception:  # noqa: BLE001
                        v = None
                if v is not None and GAP_MIN <= v <= GAP_MAX:
                    guess = v
        targets = ([guess] if guess is not None and GAP_MIN <= guess <= GAP_MAX
                   else []) + [t for t in SWEEP if t != guess]
        t = targets[0]
        box = await _slider_box(page)
        if not box:
            continue
        before = await oracle.snapshot()
        if os.environ.get("HUMANIZED_DRAG", "1") == "1":
            try:
                from human_slider import drag_humanized_playwright
                drag_dx = invert(float(t))
                released = await drag_humanized_playwright(page, box, drag_dx, log)
                probes = [(drag_dx, float(t))]
            except Exception as e:
                log(f"  [slider] humanized drag error: {e}, falling back to _drag_exact")
                released, probes = await _drag_exact(page, box, float(t), log)
        else:
            released, probes = await _drag_exact(page, box, float(t), log)
        if not images:                     # late-arriving widget images
            imgs = imgs or await _read_images(page)
            images = _save_puzzle_images(pid, imgs[0], imgs[1]) if imgs else {}
        # Trustworthy verdict: SDK callback / verify API / toast / window gone.
        # _passed() alone returns False on a real z.ai pass, so never rely on it.
        ok = seen_widget and await _verdict(page, oracle, before)
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
    # ground truth check: re-run the diff detector over every labeled pair
    # (batch labels from the vision team + the passed-record targets)
    labels = {}
    lab_path = DATA_DIR.parent / "slider_labels.jsonl"
    if lab_path.exists():
        for line in open(lab_path, encoding="utf-8"):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("label") and rec.get("bg"):
                labels[rec["bg"]] = rec["label"]
    for r in recs:
        if r.get("passed") and r.get("images", {}).get("bg"):
            labels[r["images"]["bg"]] = r["target"]
    if labels:
        ok = tot = 0
        misses = []
        for bgname, truth in sorted(labels.items()):
            fp = DATA_DIR / bgname
            pc = DATA_DIR / bgname.replace("_bg.png", "_piece.png")
            if not (fp.exists() and pc.exists()):
                continue
            bg_b64 = base64.b64encode(fp.read_bytes()).decode()
            pc_b64 = base64.b64encode(pc.read_bytes()).decode()
            g = gap_by_diff(bg_b64, pc_b64)
            tot += 1
            hit = g is not None and abs(g - truth) <= 3
            ok += int(hit)
            if not hit:
                misses.append("%s truth=%s guess=%s" % (bgname.split("_")[0], truth, g))
        print("diff detector vs %d ground-truth labels: %d correct (%.0f%%)"
              % (tot, ok, 100.0 * ok / max(1, tot)))
        if misses:
            print("  detector misses:", "; ".join(misses[:10]))


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