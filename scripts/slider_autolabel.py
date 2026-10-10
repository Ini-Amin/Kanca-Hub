#!/usr/bin/env python3
"""
scripts/slider_autolabel.py — grow the Aliyun slider dataset with NO human solves.

For each fresh puzzle we make ONE exact drag (measure-and-correct) at a guessed
gap, and the app tells us pass/fail. A PASS means the guess WAS the true gap, so
we append (background, piece, gap) to the offline dataset. Repeat for N puzzles.

Three things make this actually collect labels (the previous version collected 0):

1. PASS ORACLE. `captcha_slider._passed()` looks for "verification passed" or a
   hidden widget window. The live widget never says that: the SDK toast is
   "Slide successful!" / "滑动成功!", and z.ai's own success callback immediately
   calls the SDK's refresh() (`success:e=>{...,"captcha_verify_success",...}`),
   so the window stays VISIBLE with a new puzzle. `_passed()` therefore says
   False on a real pass. We instead install a page-side hook that wraps
   `window.initAliyunCaptcha`'s config and counts success/fail callbacks, plus a
   listener on the `*-verify.captcha-open-*.aliyuncs.com` response body.

2. DRAG THAT LANDS. The piece does not move 1:1 with the handle: piece.left is a
   quadratic in slider.left (Aliyun's obfuscation), and the curve is random per
   puzzle. We press once, read the piece/slider positions at two probe points
   mid-drag, fit piece = A*s^2 + B*s, then finish at the slider distance that
   puts the piece on the target gap (with a final mid-drag correction pass).

3. LIFECYCLE. After a PASS (or every K misses) we do a FULL reset (goto /auth
   fresh) instead of trusting the widget to reopen; every iteration logs what it
   saw so a stall is visible instead of silent.

No torch: this is pure browser + numpy/PIL. The heavy DL model is never shipped;
only the resulting dataset (and a tiny detector fit from it) is.

Usage (camoufox venv drives the browser):
  camoufox-venv/bin/python scripts/slider_autolabel.py --count 60 --out tests/fixtures/slider/auto
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import random
import re
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import captcha_slider as CS  # noqa: E402  (shared widget primitives)

AUTH = "https://chat.z.ai/auth"

# z.ai's captcha config (from its bundle): success/fail/onError callbacks.
# Wrap them -> a true pass/fail oracle, independent of DOM text/visibility.
_HOOK_PRE = r"""() => {
  if (window.__capOracle) return;
  const st = {npass: 0, nfail: 0, nerr: 0, last: null, at: 0};
  window.__capOracle = st;
  const wrapCfg = (cfg) => {
    try {
      if (cfg && typeof cfg === 'object') {
        const s = cfg.success, f = cfg.fail, e = cfg.onError;
        if (typeof s === 'function' && !s.__capW) {
          const w = function (res) { st.npass++; st.last = 'success'; st.at = Date.now();
            try { window.__capLastRes = typeof res === 'string' ? res.slice(0, 200)
                  : JSON.stringify(res).slice(0, 200); } catch (err) {}
            return s.apply(this, arguments); };
          w.__capW = true; cfg.success = w;
        }
        if (typeof f === 'function' && !f.__capW) {
          const w = function (err) { st.nfail++; st.last = 'fail'; st.at = Date.now();
            try { window.__capLastErr = typeof err === 'string' ? err.slice(0, 200)
                  : JSON.stringify(err).slice(0, 200); } catch (e2) {}
            return f.apply(this, arguments); };
          w.__capW = true; cfg.fail = w;
        }
        if (typeof e === 'function' && !e.__capW) {
          const w = function (err) { st.nerr++; st.last = 'error'; st.at = Date.now();
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
}"""

# Re-wrap if the SDK script landed without going through our setter (defensive).
_HOOK_WRAP = r"""() => {
  const fn = window.initAliyunCaptcha;
  const st = window.__capOracle;
  if (typeof fn !== 'function' || fn.__capW || !st) return typeof fn;
  const wrapCfg = (cfg) => {
    try {
      if (cfg && typeof cfg === 'object') {
        const s = cfg.success, f = cfg.fail, e = cfg.onError;
        if (typeof s === 'function' && !s.__capW) {
          const w = function (res) { st.npass++; st.last = 'success'; st.at = Date.now();
            return s.apply(this, arguments); };
          w.__capW = true; cfg.success = w;
        }
        if (typeof f === 'function' && !f.__capW) {
          const w = function (err) { st.nfail++; st.last = 'fail'; st.at = Date.now();
            return f.apply(this, arguments); };
          w.__capW = true; cfg.fail = w;
        }
        if (typeof e === 'function' && !e.__capW) {
          const w = function (err) { st.nerr++; st.last = 'error'; st.at = Date.now();
            return e.apply(this, arguments); };
          w.__capW = true; cfg.onError = w;
        }
      }
    } catch (err) {}
    return cfg;
  };
  const w = function (cfg) { return fn.call(this, wrapCfg(cfg)); };
  w.__capW = true;
  window.initAliyunCaptcha = w;
  return 'wrapped';
}"""

_ORACLE = "() => { const s = window.__capOracle || {}; return {npass: s.npass|0, nfail: s.nfail|0, nerr: s.nerr|0, last: s.last||null}; }"

_STATE = """(ids) => {
  const p = document.getElementById(ids.piece), s = document.getElementById(ids.slider),
        bg = document.getElementById(ids.bg), b = document.getElementById('aliyunCaptcha-captcha-body');
  if (!p || !s) return null;
  const pr = p.getBoundingClientRect(), br = bg ? bg.getBoundingClientRect() : null;
  const num = (v) => { const m = /(-?[\\d.]+)px?/.exec(String(v || '')); return m ? parseFloat(m[1]) : 0; };
  let travel = 260;
  if (b && b.getBoundingClientRect().width) {
    travel = Math.max(20, b.getBoundingClientRect().width - s.getBoundingClientRect().width - 4);
  }
  return {
    p: num(p.style.left), s: num(s.style.left),
    prel: br ? (pr.left - br.left) : pr.left,
    pw: pr.width, bw: br ? br.width : 0,
    natW: bg ? (bg.naturalWidth || 0) : 0,
    travel: travel,
  };
}"""

_SIGNUP_SWITCH = """() => {
  const e = [...document.querySelectorAll('button,a,div,span')]
    .filter(x => (x.innerText || '').trim() === 'Sign up' && x.children.length === 0);
  if (e.length) e[e.length - 1].click();
  return e.length;
}"""

# Real pass wording (SDK i18n) + zh, on top of the generic ones.
_PASS_TEXT = ("slide successful", "verification passed", "滑动成功", "验证通过", "验证成功")


class _Oracle:
    """Pass/fail from (a) the SDK success/fail callbacks and (b) the verify API."""

    def __init__(self, page):
        self.page = page
        self.events: list[dict] = []
        page.on("response", lambda r: asyncio.ensure_future(self._on_response(r)))

    async def _on_response(self, resp) -> None:
        try:
            url = resp.url
            if "captcha-open" not in url or "verify" not in url:
                return
            if resp.request.method != "POST":
                return
            post = resp.request.post_data or ""
            try:
                body = await resp.text()
            except Exception:  # noqa: BLE001
                return
            j = {}
            try:
                j = json.loads(body)
            except Exception:  # noqa: BLE001
                pass
            # The verdict is NESTED: top-level Success/Code just mean "the API call
            # worked". A wrong drag still returns Code=Success, Success=true and
            # only Result.VerifyResult=false (VerifyCode F015 = fail, T001 = pass).
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
            self.events.append({"t": time.time(), "ok": bool(vr), "code": vcode,
                                "msg": str(res.get("Message") or j.get("Message") or "")[:80],
                                "act": "VerifyCaptchaV3" if "VerifyCaptchaV3" in post else "?"})
        except Exception:  # noqa: BLE001
            pass

    async def snapshot(self) -> dict:
        try:
            o = await self.page.evaluate(_ORACLE)
        except Exception:  # noqa: BLE001
            o = {}
        return {"pass": int(o.get("npass") or 0), "fail": int(o.get("nfail") or 0),
                "err": int(o.get("nerr") or 0), "last": o.get("last"), "net": len(self.events)}


async def _install_hook(page) -> None:
    try:
        await page.add_init_script(_HOOK_PRE)
    except Exception:  # noqa: BLE001
        pass
    try:
        await page.evaluate(_HOOK_PRE)
    except Exception:  # noqa: BLE001
        pass


async def _ensure_hook(page) -> str:
    try:
        await page.evaluate(_HOOK_PRE)
        return str(await page.evaluate(_HOOK_WRAP))
    except Exception as e:  # noqa: BLE001
        return f"hook-error {e!r}"


async def _fill_form(page, box) -> None:
    """Get the page into Sign-up mode with the relay address filled (idempotent)."""
    # Already in signup mode? (name field present) -> skip the two mode clicks.
    try:
        ready = await page.evaluate(
            """() => !!(document.querySelector('input[placeholder*="Name" i]')
                 && document.querySelector('input[type="email"]')
                 && document.querySelector('input[type="password"]'))""")
    except Exception:  # noqa: BLE001
        ready = False
    if not ready:
        try:
            await page.get_by_text("Continue with Email").first.click(timeout=8000)
            await page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            pass
        try:
            await page.evaluate(_SIGNUP_SWITCH)
        except Exception:  # noqa: BLE001
            pass
        await page.wait_for_timeout(1800)
    nm = await page.query_selector('input[placeholder*="Name" i]')
    if nm:
        try:
            await nm.fill("Auto Label")
        except Exception:  # noqa: BLE001
            pass
    try:
        await page.fill('input[type="email"]', box.address, timeout=8000)
        await page.fill('input[type="password"]', "Zx9!mqR7pLs2Vw", timeout=8000)
    except Exception:  # noqa: BLE001
        pass


async def _full_reset(page, box, log, tag="reset") -> bool:
    """Fresh page -> Sign up form -> widget. Needed after a PASS and after K misses."""
    for attempt in range(1, 4):
        try:
            await page.goto(AUTH, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(3200)
            await _fill_form(page, box)
            if await CS._wait_open(page):
                log(f"    [{tag}] attempt {attempt}: widget open")
                return True
            log(f"    [{tag}] attempt {attempt}: widget not open yet")
        except Exception as e:  # noqa: BLE001
            log(f"    [{tag}] attempt {attempt}: {e!r}")
        await page.wait_for_timeout(1500)
    return False


async def _wait_new_puzzle(page, old_sig: str, timeout: float = 8.0) -> bool:
    """True once the piece image changed (refresh served a new puzzle)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        sig = await _puzzle_sig(page)
        if sig and sig != old_sig:
            return True
        try:
            await CS.refresh(page)
        except Exception:  # noqa: BLE001
            pass
        await page.wait_for_timeout(1200)
    return False


async def _puzzle_sig(page) -> str:
    try:
        return str(await page.evaluate(
            """(ids) => { const p = document.getElementById(ids.piece);
                 return p ? String(p.currentSrc || p.src || '').slice(-64) : ''; }""", CS.IDS))
    except Exception:  # noqa: BLE001
        return ""


async def _state(page) -> dict | None:
    try:
        return await page.evaluate(_STATE, CS.IDS)
    except Exception:  # noqa: BLE001
        return None


async def _drag_to(page, box: dict, target: float, log) -> dict:
    """Press the handle once, probe the piece:slider curve at two points, then
    land the piece on `target` (bg-pixel column == piece.left px at scale 1)."""
    sx, sy = box["x"], box["y"]
    probes: list[tuple[float, float]] = []
    out = {"probes": [], "s": None, "p": None, "ok": False, "note": ""}

    await page.mouse.move(sx, sy)
    await page.mouse.down()
    await page.wait_for_timeout(60)
    st0 = await _state(page)
    p0 = (st0 or {}).get("p", 0.0)
    travel = (st0 or {}).get("travel", 260.0) or 260.0
    scale = 1.0
    if st0 and st0.get("bw") and st0.get("natW"):
        scale = st0["bw"] / st0["natW"]
    goal = target * scale  # piece.left in CSS px

    try:
        for probe in (45.0, 125.0):
            await page.mouse.move(sx + probe, sy, steps=6)
            await page.wait_for_timeout(90)
            st = await _state(page)
            if not st or st["s"] <= 0:
                out["note"] = "slider did not move"
                return out
            probes.append((st["s"], st["p"]))
        out["probes"] = [(round(s, 2), round(p, 2)) for s, p in probes]

        # Fit piece = A s^2 + B s + p0 through the two probes (+ the known rest state).
        (s1, p1), (s2, p2) = probes
        if abs(s2 - s1) < 1e-6:
            A, B = 0.0, (p1 / s1 if s1 else 0.0)
        else:
            A = (p1 / s1 - p2 / s2) / (s1 - s2) if s1 and s2 else 0.0
            B = (p1 / s1 - A * s1) if s1 else 0.0
        # Solve A s^2 + B s + p0 = goal
        goal_rel = goal - p0
        if A > 1e-9:
            disc = B * B + 4 * A * goal_rel
            s_want = (-B + (disc ** 0.5)) / (2 * A) if disc >= 0 else travel
        else:
            s_want = goal_rel / B if B > 1e-9 else travel
        s_want = max(1.0, min(float(s_want), float(travel)))
        out["A"], out["B"], out["p0"] = round(A, 6), round(B, 6), round(p0, 2)

        def fit_p(s: float) -> float:
            return A * s * s + B * s + p0

        # The browser quantizes mouse coordinates to whole CSS px, so a fractional
        # target silently undershoots by up to a full pixel. Snap to the integer
        # handle step whose fitted piece position is closest to the goal.
        cand = {int(s_want // 1), int(-(-s_want // 1)), int(round(s_want))}
        s_step = min((c for c in cand if 0 < c <= travel),
                     key=lambda s: abs(fit_p(s) - goal), default=s_want)
        await page.mouse.move(sx + s_step, sy, steps=8)
        await page.wait_for_timeout(90)
        st = await _state(page) or {}
        # Mid-drag correction: re-aim with the local slope of the fitted curve
        # (allowed to move BACKWARDS too — the piece must land on the pixel, not
        # just "get close from below").
        for _ in range(3):
            p_now, s_now = st.get("p"), st.get("s")
            if p_now is None or s_now is None or s_now <= 0:
                break
            if abs(p_now - goal) <= 0.35:
                break
            slope = (2 * A * s_now + B) if A > 1e-9 else B
            if slope < 1e-3:
                break
            s_next = s_now + (goal - p_now) / slope
            # snap again to an integer handle step
            s_next = min((int(s_next // 1), int(-(-s_next // 1))), key=lambda s: abs(fit_p(s) - goal))
            s_next = max(1, min(int(s_next), int(travel)))
            if s_next == int(round(s_now)):
                break
            await page.mouse.move(sx + s_next, sy, steps=4)
            await page.wait_for_timeout(80)
            st = await _state(page) or {}
        out["s"], out["p"] = st.get("s"), st.get("p")
        out["ok"] = True
        out["note"] = (f"landed p={st.get('p')} at slider s={st.get('s')} "
                       f"(want {goal:.1f}, off {((st.get('p') or 0) - goal):+.2f})")
    finally:
        try:
            await page.mouse.up()
        except Exception:  # noqa: BLE001
            pass
    return out


async def run(*, count: int, out: Path, headless: bool, log=print) -> None:
    import mailboxes as M
    from camoufox.async_api import AsyncCamoufox

    out.mkdir(parents=True, exist_ok=True)
    labels_path = out / "labels.json"
    labels = json.loads(labels_path.read_text()) if labels_path.exists() else []
    have = {l.get("stamp") for l in labels}

    box = M.open_mailbox("auto", domain="kancalabs.biz.id", log=log)
    got = 0
    misses = 0
    K = 3  # full reset after this many misses in a row

    async with AsyncCamoufox(headless=headless, humanize=True, os="windows") as browser:
        page = await browser.new_page()
        await _install_hook(page)
        oracle = _Oracle(page)

        if not await _full_reset(page, box, log, tag="boot"):
            log("could not open the widget on a fresh page — aborting")
            return
        log(f"labels on disk: {len(labels)} (resuming)")

        for i in range(1, count + 1):
            try:
                hook = await _ensure_hook(page)
                sig = await _puzzle_sig(page)
                imgs = await CS._read_images(page)
                tries = 0
                while not imgs and tries < 4:
                    tries += 1
                    if not await CS._wait_open(page):
                        await _full_reset(page, box, log, tag=f"iter{i}-reopen")
                    await page.wait_for_timeout(1200)
                    sig = await _puzzle_sig(page)
                    imgs = await CS._read_images(page)
                if not imgs:
                    log(f"  [{i}/{count}] SKIP no images (hook={hook})")
                    misses += 1
                    continue

                # guess: detector, else a random offset in the common range
                g = CS.gap_by_diff(imgs[0], imgs[1])
                src = "detect"
                if g is None or not (40 <= g <= 295):
                    g = random.choice([188, 200, 212, 224, 236, 176, 164, 248])
                    src = "random"

                bx = await CS._slider_box(page)
                if not bx:
                    log(f"  [{i}/{count}] SKIP no slider handle")
                    misses += 1
                    if misses >= K:
                        await _full_reset(page, box, log, tag="no-handle")
                        misses = 0
                    continue

                before = await oracle.snapshot()
                res = await _drag_to(page, bx, float(g), log)
                await page.wait_for_timeout(400)
                if not res.get("ok"):
                    log(f"  [{i}/{count}] SKIP drag never engaged ({res.get('note')})")
                    misses += 1
                    if misses >= K:
                        await _full_reset(page, box, log, tag="no-drag")
                        misses = 0
                    else:
                        await _wait_new_puzzle(page, sig, timeout=6.0)
                    continue

                # Wait for the SDK / verify API to answer (pass or fail).
                passed = False
                why = ""
                t0 = time.time()
                while time.time() - t0 < 3.0:
                    await page.wait_for_timeout(200)
                    now = await oracle.snapshot()
                    if now["pass"] > before["pass"]:
                        passed, why = True, f"hook success (last={now['last']})"
                        break
                    if now["fail"] > before["fail"]:
                        why = f"hook fail (last={now['last']})"
                        break
                    if now["net"] > before["net"]:
                        ev = oracle.events[-1]
                        passed, why = bool(ev["ok"]), f"verify api Success={ev['ok']} {ev['code']}"
                        break
                    try:
                        txt = (await page.evaluate("()=>document.body.innerText") or "").lower()
                    except Exception:  # noqa: BLE001
                        txt = ""
                    if any(k in txt for k in _PASS_TEXT):
                        passed, why = True, "page text"
                        break
                if not why:
                    why = "no verdict in 3s"

                probe_note = res.get("note", "")
                log(f"  [{i}/{count}] guess={g} {src}  {'PASS' if passed else 'miss'}"
                    f"  probes={res.get('probes')}  {probe_note}  ({why}) hook={hook}")

                if passed:
                    stamp = str(int(time.time() * 1000))
                    if stamp not in have:
                        (out / f"{stamp}_bg.png").write_bytes(
                            base64.b64decode(imgs[0].split(",", 1)[-1]))
                        (out / f"{stamp}_pc.png").write_bytes(
                            base64.b64decode(imgs[1].split(",", 1)[-1]))
                        labels.append({"stamp": stamp, "bg": f"{stamp}_bg.png",
                                       "pc": f"{stamp}_pc.png", "gap_x": int(g),
                                       "landed_x": round(float(res.get("p") or g), 2),
                                       "source": "autolabel"})
                        have.add(stamp)
                        got += 1
                        labels_path.write_text(json.dumps(labels, indent=2))
                    log(f"      -> label saved (total {len(labels)})")
                    misses = 0
                    # captcha consumed by this pass: rebuild the page from scratch
                    if not await _full_reset(page, box, log, tag="after-pass"):
                        log("  widget would not come back after a pass — aborting")
                        break
                    continue

                misses += 1
                if misses >= K:
                    log(f"    {misses} misses in a row -> full reset")
                    if not await _full_reset(page, box, log, tag="after-misses"):
                        log("  widget would not come back — aborting")
                        break
                    misses = 0
                else:
                    # a miss: ask for a fresh puzzle, fall back to a full reset
                    if not await _wait_new_puzzle(page, sig, timeout=8.0):
                        log("    refresh did not serve a new puzzle -> full reset")
                        if not await _full_reset(page, box, log, tag="stale"):
                            break
                        misses = 0
            except Exception as e:  # noqa: BLE001
                log(f"  [{i}/{count}] ERROR {e!r} -> full reset")
                try:
                    if not await _full_reset(page, box, log, tag=f"iter{i}-err"):
                        break
                except Exception:  # noqa: BLE001
                    break

        log(f"done: +{got} new labels this run, {len(labels)} total -> {labels_path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Auto-label Aliyun slider puzzles (no human)")
    ap.add_argument("--count", type=int, default=60)
    ap.add_argument("--out", default="tests/fixtures/slider/auto")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args(argv)
    asyncio.run(run(count=a.count, out=Path(a.out), headless=a.headless))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
