#!/usr/bin/env python3
"""hCaptcha puzzle dataset capture + review page (OpenCode Console signup).

WHAT THIS CAPTURES, AND WHY IT IS NOT DONE VIA THE API
-----------------------------------------------------
hCaptcha serves its puzzle payload through `getcaptcha`, but on opencode.ai that
response is ENCRYPTED (`enc_get_req: true`; measured 2026-10-22 -- the body
starts with 0xa5, not `{`). So the payload cannot be parsed. What CAN be read is
the challenge iframe itself: Playwright can evaluate inside a cross-origin frame,
and hCaptcha's DOM there is stable and semantic:

    div.challenge-prompt      -> the question text
    div.challenge-view        -> the tile area
    div[class*=task] / canvas -> the individual tiles

So we capture from the FRAME (DOM + a cropped screenshot), plus the verdict from
the `checkcaptcha` response, which is plain JSON (`{"pass": true, ...}`).

Every accepted puzzle is a free label: the tiles the operator clicked ARE the
ground truth. That mirrors the Aliyun slider loop in reports/slider_review/ --
passes become labels, fails stay visible for review.

OUTPUT  reports/hcaptcha_review/
    img/<id>_challenge.png    cropped challenge screenshot (prompt + tiles)
    img/<id>_t<N>.png         one crop per tile
    dataset.jsonl             {id, question, tiles[], clicks[], verdict, ts}
    labels.jsonl              accepted puzzles only (= truth)
    index.html                review page (All / Failed / Passed / Unknown)
    state.json                last run stats

USAGE
    python3 scripts/hcaptcha_dataset.py capture --keep-open 1200
    python3 scripts/hcaptcha_dataset.py review
    python3 scripts/hcaptcha_dataset.py stats
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import os
import random
import string
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

DIR = Path(os.environ.get("HCAPTCHA_DIR", REPO / "reports" / "hcaptcha_review"))
IMG = DIR / "img"
DATASET = DIR / "dataset.jsonl"
LABELS = DIR / "labels.jsonl"
STATE = DIR / "state.json"
KEYLEDGER = DIR / "api_keys.jsonl"
SESSION_FILE = Path.home() / ".config" / "auto-freecf" / "opencode_session.txt"
CONSOLE = "https://opencode.ai/console"
MODEL = os.environ.get("HCAPTCHA_VISION_MODEL", "ag/gemini-3.8-flash-high")


# ── storage helpers ────────────────────────────────────────────────────────

def _ensure() -> None:
    IMG.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _pid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


# ── the frame reader (evaluated inside hCaptcha's challenge iframe) ────────

READ_FRAME = r"""
() => {
  const q = document.querySelector('.challenge-prompt');
  let question = (q ? q.innerText : '').trim().split('\n')[0] || '';
  if (!question) {
    const skip = /^(skip|verify|next|en|i am human|please try again)/i;
    for (const line of (document.body.innerText || '').split('\n')) {
      const t = line.trim();
      if (t.length > 8 && !skip.test(t)) { question = t; break; }
    }
  }

  // hCaptcha renders the whole puzzle on ONE <canvas>. Measured 2026-10-23:
  // there are no per-tile DOM nodes at all -- a 3x3 grid, an animal-selection
  // scene and a drag-drop board are all drawn into the same canvas. Any
  // "find the tiles by geometry" approach therefore finds wrappers, not cells,
  // which is why earlier captures recorded 1-2 bogus "tiles" for a 9-cell grid.
  // The canvas IS the puzzle image; clicks are recorded as points inside it.
  const cv = document.querySelector('canvas');
  let canvas = null;
  if (cv) {
    const r = cv.getBoundingClientRect();
    canvas = {x: Math.round(r.left), y: Math.round(r.top),
              w: Math.round(r.width), h: Math.round(r.height)};
  }
  const view = (() => {
    const v = document.querySelector('.challenge-view');
    if (!v) return null;
    const r = v.getBoundingClientRect();
    return {x: Math.round(r.left), y: Math.round(r.top),
            w: Math.round(r.width), h: Math.round(r.height)};
  })();

  return {
    question,
    canvas,
    view,
    has_canvas: !!cv,
    clicks: window.__hc_clicks || [],
  };
}
"""

ARM_CLICKS = r"""
() => {
  if (window.__hc_armed) return 'already';
  window.__hc_armed = true;
  window.__hc_clicks = [];
  const rec = (kind, ev) => window.__hc_clicks.push({
    kind, x: Math.round(ev.clientX), y: Math.round(ev.clientY), t: Date.now()
  });
  // Grid challenges are solved by clicking; drag-drop ones ("Move the correct
  // vial", "Drag ONE animal to the silhouette") only fire pointer events.
  // Recording clicks alone left every drag puzzle with clicks=[] even when the
  // operator solved it, so both gestures are captured, labelled by `kind`.
  document.addEventListener('click', (ev) => rec('click', ev), true);
  document.addEventListener('pointerdown', (ev) => rec('down', ev), true);
  document.addEventListener('pointerup', (ev) => rec('up', ev), true);
  return 'armed';
}
"""

# Page-level fallback recorder. hCaptcha destroys and recreates the challenge
# iframe between rounds, which wipes window.__hc_clicks inside it -- measured:
# 4 puzzles captured with clicks=[] even though the operator clicked tiles. The
# top document survives those swaps, so record coordinates there too.
ARM_PAGE_CLICKS = r"""
() => {
  if (window.__hc_page_armed) return 'already';
  window.__hc_page_armed = true;
  window.__hc_page_clicks = [];
  const rec = (kind, ev) => window.__hc_page_clicks.push({
    kind, x: Math.round(ev.clientX), y: Math.round(ev.clientY), t: Date.now()
  });
  document.addEventListener('click', (ev) => rec('click', ev), true);
  document.addEventListener('pointerdown', (ev) => rec('down', ev), true);
  document.addEventListener('pointerup', (ev) => rec('up', ev), true);
  return 'armed';
}
"""


def _tile_hit(tiles: list[dict], x: int, y: int) -> int:
    for t in tiles:
        if t["x"] <= x <= t["x"] + t["w"] and t["y"] <= y <= t["y"] + t["h"]:
            return t["i"]
    return -1


# ── capture ────────────────────────────────────────────────────────────────

async def _capture(keep_open: int, email: str, password: str, auto: bool = False) -> dict:
    from camoufox.async_api import AsyncCamoufox
    import mailboxes

    _ensure()
    mb = None
    if not email:
        mb = mailboxes.open_mailbox("tempik")
        email = mb.address
    else:
        # Reuse the inbox that owns this address, or we would poll a different
        # mailbox and never see the verification mail.
        local = email.split("@")[0]
        try:
            mb = mailboxes.open_mailbox("tempik", local_part=local)
        except Exception:
            mb = None
    if not password:
        password = "".join(random.choices(string.ascii_letters + string.digits, k=12)) + "!7aA"

    stats = {"puzzles": 0, "pass": 0, "fail": 0, "unknown": 0, "email": email}
    print(f"[capture] registering: {email}")
    print("[capture] solve puzzles in the window. Each one is saved as it resolves.")

    seen: dict[str, dict] = {}      # pid -> record
    order: list[str] = []           # recency
    verdicts: list[dict] = []

    async with AsyncCamoufox(headless=False, humanize=True) as browser:
        page = await browser.new_page(viewport={"width": 1280, "height": 900})

        async def on_response(resp):
            u = resp.url or ""
            # hCaptcha's verdict endpoint is `checkcaptcha`; older/alternate
            # flows use `checkcaptcha/<key>` or a bare `/check` under the
            # challenge host. Match loosely and log what we saw, because a
            # silent miss here means every solved round is recorded "unknown".
            # ONLY `checkcaptcha` carries the round verdict. `checksiteconfig`
            # and `getcaptcha` also return 200 with a `pass` field, and matching
            # them recorded a FALSE PASS on the very first poll -- the dataset
            # would then contain unlabelled puzzles marked as accepted truth.
            if "checkcaptcha" not in u:
                return
            try:
                body = await resp.json()
            except Exception:
                body = {}
            ok = bool(body.get("pass"))
            if not ok and isinstance(body, dict):
                ok = bool(body.get("generated_pass_UUID")) or body.get("success") is True
            verdicts.append({"pass": ok, "ts": time.time(), "url": u.split("?")[0]})
            print(f"[capture] verdict seen: pass={ok} url={u.split('?')[0][-60:]}", flush=True)

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        await page.goto(f"{CONSOLE}/signup", wait_until="domcontentloaded", timeout=90000)
        # The form is client-rendered; the inputs appear a moment after load.
        try:
            await page.wait_for_selector("input[type=email]", timeout=30000)
        except Exception:
            pass
        await page.wait_for_timeout(1200)

        # Fill via the native setter: React ignores a plain .value assignment,
        # and Playwright's fill() can time out when hCaptcha's overlay sits over
        # the field (measured: 30s TimeoutError on a visible input).
        filled = await page.evaluate("""(creds) => {
            const setVal = (el, v) => {
                const proto = el instanceof HTMLTextAreaElement
                    ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            };
            const em = document.querySelector('input[type=email]');
            const pw = document.querySelector('input[type=password]');
            if (!em || !pw) return {ok: false, why: 'inputs missing'};
            setVal(em, creds.email);
            setVal(pw, creds.password);
            return {ok: true};
        }""", {"email": email, "password": password})
        if not filled.get("ok"):
            print(f"[capture] form fill failed: {filled}", flush=True)
        await page.wait_for_timeout(900)

        # Click via JS: a real click is intercepted by the invisible hCaptcha
        # iframe that covers the button (measured: 30s timeout, locator resolved).
        clicked = await page.evaluate("""() => {
            const b = Array.from(document.querySelectorAll('button'))
                .find(x => /create account/i.test(x.innerText));
            if (!b) return false;
            b.click();
            return true;
        }""")
        if not clicked:
            print("[capture] Create Account button not found", flush=True)
        try:
            await page.evaluate(ARM_PAGE_CLICKS)
        except Exception:
            pass

        deadline = time.time() + keep_open
        last_sig = ""
        handled_verdict = 0
        ticks = 0

        while time.time() < deadline:
            await page.wait_for_timeout(900)

            # find the challenge frame. The URL only carries `frame=challenge`
            # while a challenge is actually shown; a checkbox-only widget uses
            # `frame=checkbox-invisible`, which has no tiles.
            # hCaptcha keeps the previous round's iframe alive but hidden, and
            # a cross-origin frame's own bounding box is unreliable. So: read
            # EVERY challenge frame and keep the one that actually reports a
            # canvas with a real size. Measured: trusting the frame's bbox made
            # the loop sit on a hidden frame for 300s while the visible round
            # was a different iframe.
            frame = None
            info = None
            best_area = 0
            for f in page.frames:
                u = f.url or ""
                if "hcaptcha" not in u or "frame=challenge" not in u:
                    continue
                try:
                    await f.evaluate(ARM_CLICKS)
                    cand = await f.evaluate(READ_FRAME)
                except Exception:
                    continue
                cv = cand.get("canvas") or {}
                area = int(cv.get("w") or 0) * int(cv.get("h") or 0)
                if area > best_area:
                    best_area = area
                    frame = f
                    info = cand

            if frame is None:
                ticks += 1
                if ticks % 20 == 0:
                    print(f"[capture] waiting... frames={len(page.frames)} "
                          f"verdicts={len(verdicts)} url={page.url[:60]}", flush=True)
                # no challenge on screen; if a verdict arrived, drain it
                while handled_verdict < len(verdicts):
                    v = verdicts[handled_verdict]
                    handled_verdict += 1
                    _resolve_newest(seen, order, v["pass"], stats)
                continue


            question = info.get("question") or ""
            canvas = info.get("canvas")
            if not canvas:
                # The challenge frame appears before its canvas is mounted
                # (measured: ~2-4s gap). Skipping here used to record a single
                # bogus "no-canvas" puzzle and lose the real one, so wait for the
                # canvas inside this frame instead of giving up.
                ticks += 1
                if ticks % 6 == 0:
                    print(f"[capture] waiting for canvas (q={question[:40]!r})", flush=True)
                continue

            # A canvas with zero size is not a puzzle yet.
            if not canvas.get("w") or not canvas.get("h"):
                continue

            # Identity must be per-challenge, not per-prompt. Measured: the
            # prompt "Click on the letter that is part of this group: XYZ" is
            # served many times with different artwork, so hashing the question
            # collapsed distinct puzzles into one id and the later ones were
            # skipped as "same puzzle". hCaptcha carries a per-round id in the
            # frame URL; fall back to a timestamp if it is ever absent.
            m_id = re.search(r"[?&]id=([A-Za-z0-9_-]+)", frame.url or "")
            round_id = m_id.group(1) if m_id else ""
            sig = _pid(question, round_id or str(time.time()),
                       str(canvas["w"]), str(canvas["h"]))
            if sig == last_sig:
                if sig in seen:
                    seen[sig]["clicks"] = [
                        {"kind": c.get("kind", "click"),
                         "x": c["x"] - canvas["x"], "y": c["y"] - canvas["y"],
                         "t": c["t"]}
                        for c in (info.get("clicks") or [])
                    ]
                    # The canvas mounts before hCaptcha paints the puzzle into
                    # it. Measured: the first crop was a blank white area under
                    # the prompt. Keep re-cropping while the round is on screen
                    # so the file ends up holding the fully drawn puzzle.
                    try:
                        fe2 = await frame.frame_element()
                        b2 = await fe2.bounding_box()
                        if b2:
                            await page.screenshot(
                                path=str(IMG / seen[sig]["challenge_png"]),
                                clip={"x": b2["x"] + canvas["x"],
                                      "y": b2["y"] + canvas["y"],
                                      "width": canvas["w"], "height": canvas["h"]})
                    except Exception:
                        pass
                continue
            last_sig = sig

            pid = sig
            # The canvas is the whole puzzle: crop it out of the page.
            fbox = None
            try:
                fe = await frame.frame_element()
                fbox = await fe.bounding_box()
            except Exception:
                fbox = None
            ox = fbox["x"] if fbox else 0
            oy = fbox["y"] if fbox else 0

            shot = IMG / f"{pid}_challenge.png"
            try:
                await page.screenshot(path=str(shot), clip={
                    "x": ox + canvas["x"], "y": oy + canvas["y"],
                    "width": canvas["w"], "height": canvas["h"],
                })
            except Exception:
                shot = None

            # Points are recorded in frame coordinates; store them canvas-relative
            # so a solver can consume them without knowing the frame offset.
            pts = [{"kind": c.get("kind", "click"),
                    "x": c["x"] - canvas["x"], "y": c["y"] - canvas["y"],
                    "t": c["t"]}
                   for c in (info.get("clicks") or [])]

            # ── wait for the canvas to actually paint ────────────────────
            # The canvas element mounts before hCaptcha draws the puzzle into
            # it. Solving the first crop fed the vision model a blank white
            # rectangle (measured: solver answered confidently about an empty
            # image). Poll until two consecutive crops are byte-identical.
            if shot:
                prev = b""
                for _ in range(12):
                    await page.wait_for_timeout(350)
                    try:
                        fe3 = await frame.frame_element()
                        b3 = await fe3.bounding_box()
                        if not b3:
                            continue
                        await page.screenshot(path=str(shot), clip={
                            "x": b3["x"] + canvas["x"], "y": b3["y"] + canvas["y"],
                            "width": canvas["w"], "height": canvas["h"]})
                        cur = shot.read_bytes()
                    except Exception:
                        continue
                    if cur and cur == prev:
                        break
                    prev = cur

            seen[pid] = {
                "id": pid,
                "ts": time.time(),
                "site": CONSOLE,
                "question": question,
                "canvas": canvas,
                "clicks": pts,
                "auto_points": [],       # filled by the solver when --auto runs
                "verdict": None,
                "challenge_png": shot.name if shot else None,
            }
            # ── auto-solve ────────────────────────────────────────────────
            # When --auto is set, ask the vision solver what to do and perform
            # it with real pointer events, so hCaptcha sees a genuine gesture.
            if auto and shot:
                try:
                    sys.path.insert(0, str(REPO / "scripts"))
                    from hcaptcha_solver import solve_one as _solve
                    res = _solve({"id": pid, "question": question,
                                  "canvas": canvas, "challenge_png": shot.name}, MODEL)
                    ans = (res or {}).get("answer")
                    if ans:
                        # Persist the solver's own decision: the frame-level
                        # click log is wiped when hCaptcha swaps rounds, so this
                        # is often the ONLY record of what was attempted.
                        if ans.get("points"):
                            seen[pid]["auto_points"] = ans["points"]
                        elif ans.get("from") and ans.get("to"):
                            seen[pid]["auto_points"] = [ans["from"], ans["to"]]
                        act = ans.get("action")
                        if act == "click" and ans.get("points"):
                            for pt in ans["points"][:12]:
                                await page.mouse.click(ox + canvas["x"] + pt["x"],
                                                       oy + canvas["y"] + pt["y"])
                                await page.wait_for_timeout(260)
                            print(f"[capture] {pid} auto-clicked {len(ans['points'])} point(s)",
                                  flush=True)
                        elif act == "drag" and ans.get("from") and ans.get("to"):
                            f0, t0 = ans["from"], ans["to"]
                            await page.mouse.move(ox + canvas["x"] + f0["x"],
                                                  oy + canvas["y"] + f0["y"])
                            await page.mouse.down()
                            await page.wait_for_timeout(180)
                            await page.mouse.move(ox + canvas["x"] + t0["x"],
                                                  oy + canvas["y"] + t0["y"],
                                                  steps=22)
                            await page.wait_for_timeout(180)
                            await page.mouse.up()
                            print(f"[capture] {pid} auto-dragged {f0} -> {t0}", flush=True)
                        # Submit. The button lives BELOW the canvas (measured:
                        # canvas y=10..480, submit at y=521), so it is outside
                        # the crop and easy to miss. A failed submit is silent --
                        # no checkcaptcha response ever arrives and the round
                        # sits until timeout -- so try three ways and report.
                        await page.wait_for_timeout(600)
                        submitted = False
                        for attempt in range(3):
                            try:
                                clicked = await frame.evaluate("""() => {
                                    const b = document.querySelector('.button-submit, [class*=submit]');
                                    if (!b) return false;
                                    b.click();
                                    return true;
                                }""")
                                if clicked:
                                    submitted = True
                                    break
                            except Exception:
                                pass
                            try:
                                sub = frame.locator(".button-submit")
                                if await sub.count():
                                    await sub.first.click(timeout=4000)
                                    submitted = True
                                    break
                            except Exception:
                                pass
                            await page.wait_for_timeout(700)
                        print(f"[capture] {pid} submit={'sent' if submitted else 'FAILED'}",
                              flush=True)
                        # hCaptcha mints the token only after the CHALLENGE's
                        # own submit button is pressed and the answer is right.
                        # Measured (token diagnostic, 48s): h-captcha-response
                        # stayed at length 0 the whole time, so pressing the form
                        # button again was useless -- the challenge had never been
                        # accepted. Loop: submit the challenge, check the token,
                        # and if the challenge is still up, re-answer and retry.
                        token_ok = False
                        for round_try in range(6):
                            try:
                                f2 = None
                                for cand2 in page.frames:
                                    u2 = cand2.url or ""
                                    if "hcaptcha" in u2 and "frame=challenge" in u2:
                                        f2 = cand2
                                        break
                                if f2 is not None:
                                    await f2.evaluate(
                                        """() => { const b = document.querySelector('.button-submit, [class*=submit]'); if (b) b.click(); }""")
                            except Exception:
                                pass

                            for _ in range(12):
                                await page.wait_for_timeout(350)
                                try:
                                    n = await page.evaluate(
                                        """() => {
                                            const a = document.querySelector('[name="h-captcha-response"]');
                                            const b = document.querySelector('textarea[name="g-recaptcha-response"]');
                                            const v = (a && a.value) || (b && b.value) || '';
                                            return v.length;
                                        }""")
                                except Exception:
                                    n = 0
                                if n and int(n) > 20:
                                    token_ok = True
                                    break
                            if token_ok:
                                break

                            # Wrong answer -> hCaptcha serves a NEW round. Solve it
                            # and try again instead of sitting on the failure.
                            try:
                                f3 = None
                                info3 = None
                                best3 = 0
                                for cand3 in page.frames:
                                    u3 = cand3.url or ""
                                    if "hcaptcha" not in u3 or "frame=challenge" not in u3:
                                        continue
                                    await cand3.evaluate(ARM_CLICKS)
                                    c3 = await cand3.evaluate(READ_FRAME)
                                    cv3 = c3.get("canvas") or {}
                                    a3 = int(cv3.get("w") or 0) * int(cv3.get("h") or 0)
                                    if a3 > best3:
                                        best3, f3, info3 = a3, cand3, c3
                                if f3 is None or not info3:
                                    continue
                                cv3 = info3["canvas"]
                                q3 = info3.get("question") or ""
                                fbox3 = await f3.frame_element()
                                b3 = await fbox3.bounding_box()
                                if not b3:
                                    continue
                                shot3 = IMG / f"{pid}_retry{round_try}.png"
                                await page.wait_for_timeout(700)
                                await page.screenshot(path=str(shot3), clip={
                                    "x": b3["x"] + cv3["x"], "y": b3["y"] + cv3["y"],
                                    "width": cv3["w"], "height": cv3["h"]})
                                sys.path.insert(0, str(REPO / "scripts"))
                                from hcaptcha_solver import solve_one as _solve3
                                res3 = _solve3({"id": f"{pid}_r{round_try}", "question": q3,
                                                "canvas": cv3, "challenge_png": shot3.name}, MODEL)
                                ans3 = (res3 or {}).get("answer")
                                if not ans3:
                                    continue
                                if ans3.get("action") == "click":
                                    for pt in (ans3.get("points") or [])[:12]:
                                        await page.mouse.click(b3["x"] + cv3["x"] + pt["x"],
                                                               b3["y"] + cv3["y"] + pt["y"])
                                        await page.wait_for_timeout(250)
                                elif ans3.get("action") == "drag" and ans3.get("from"):
                                    f0, t0 = ans3["from"], ans3["to"]
                                    await page.mouse.move(b3["x"] + cv3["x"] + f0["x"],
                                                          b3["y"] + cv3["y"] + f0["y"])
                                    await page.mouse.down()
                                    await page.wait_for_timeout(180)
                                    await page.mouse.move(b3["x"] + cv3["x"] + t0["x"],
                                                          b3["y"] + cv3["y"] + t0["y"], steps=22)
                                    await page.wait_for_timeout(180)
                                    await page.mouse.up()
                                print(f"[capture] {pid} retry {round_try + 1}: {q3[:40]!r}", flush=True)
                            except Exception as exc:
                                print(f"[capture] retry failed: {str(exc)[:90]}", flush=True)
                        print(f"[capture] {pid} token={'ready' if token_ok else 'MISSING'}",
                              flush=True)
                        if token_ok:
                            try:
                                await page.evaluate(
                                    """() => {
                                        const b = Array.from(document.querySelectorAll('button'))
                                            .find(x => /create account/i.test(x.innerText));
                                        if (b) b.click();
                                    }""")
                                print(f"[capture] {pid} form re-submitted with token", flush=True)
                            except Exception:
                                pass
                    else:
                        print(f"[capture] {pid} solver gave no answer", flush=True)
                except Exception as exc:
                    print(f"[capture] auto-solve failed: {type(exc).__name__}: {str(exc)[:110]}",
                          flush=True)

            order.append(pid)
            stats["puzzles"] += 1
            print(f"[capture] {pid}  {question[:58]!r}  canvas={canvas['w']}x{canvas['h']}")

            # drain any verdict that already landed
            while handled_verdict < len(verdicts):
                v = verdicts[handled_verdict]
                handled_verdict += 1
                _resolve_newest(seen, order, v["pass"], stats)

        # ── verify the email inside this browser, then mint the API key ────
        # The console session is created by the browser's own navigation; the
        # same /auth/verify-email call over urllib returns a stub cookie and
        # /auth/login is captcha-gated, so verification MUST happen here.
        if email and "/check-email" in (page.url or ""):
            print("[capture] registered. waiting for the verification mail...", flush=True)
            try:
                link = mb.link(r"https://opencode\.ai/console/verify-email\?[^\s\"'<>]+",
                               timeout=180) if mb else None
            except Exception as exc:
                link = None
                print(f"[capture] mailbox poll failed: {exc}", flush=True)
            if link:
                print(f"[capture] opening verify link in the same browser", flush=True)
                try:
                    await page.goto(link, wait_until="networkidle", timeout=90000)
                    await page.wait_for_timeout(4000)
                except Exception as exc:
                    print(f"[capture] verify nav: {str(exc)[:100]}", flush=True)
                print(f"[capture] verify -> {page.url[:80]}", flush=True)

                cookies = await page.context.cookies()
                hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies
                                if "opencode.ai" in c.get("domain", "") and c.get("value"))
                if hdr:
                    print(f"[capture] session cookies captured ({len(hdr)} chars)", flush=True)
                    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                    SESSION_FILE.write_text(hdr)
                    try:
                        sys.path.insert(0, str(REPO / "scripts"))
                        from opencode_farm import whoami as _who, mint_key as _mint
                        me = _who(hdr)
                        print(f"[capture] session check: {me['status']}", flush=True)
                        if me["status"] == 200:
                            # opencode.ai resets connections when several POSTs
                            # arrive back-to-back from one IP (measured: mint
                            # died with ECONNRESET right after register+verify,
                            # then succeeded unchanged 8s later). Retry with
                            # backoff instead of losing a freshly made account.
                            r = {}
                            tok = None
                            for attempt in range(4):
                                try:
                                    r = _mint(hdr, "farm-1", permissions="all")
                                    body = r.get("body")
                                    tok = body.get("token") if isinstance(body, dict) else None
                                    if tok:
                                        break
                                    print(f"[capture] mint attempt {attempt + 1}: "
                                          f"{r.get('status')} {str(body)[:90]}", flush=True)
                                except Exception as exc:
                                    print(f"[capture] mint attempt {attempt + 1}: "
                                          f"{type(exc).__name__}", flush=True)
                                await page.wait_for_timeout(6000)
                            print(f"[capture] key mint: {r.get('status', '?')} "
                                  f"{'GOT ' + tok[:14] + '...' if tok else 'FAILED'}",
                                  flush=True)
                            if tok:
                                _append_jsonl(KEYLEDGER, {"email": email, "api_key": tok,
                                                         "created": time.time()})
                                print(f"[capture] key saved to {KEYLEDGER}", flush=True)
                                try:
                                    import sqlite3
                                    import opencode_inject as _inj
                                    _con = sqlite3.connect(str(_inj.db_path()))
                                    _inj.ensure_node(_con, False)
                                    _inj.upsert(_con, email or "opencode-farm", tok, False)
                                    _con.commit()
                                    _con.close()
                                    print(f"[capture] key auto-injected to 9router: {tok[:14]}...", flush=True)
                                except Exception as _ie:
                                    print(f"[capture] 9router injection notice: {_ie}", flush=True)
                    except Exception as exc:
                        print(f"[capture] session/key step failed: {str(exc)[:120]}", flush=True)

        # close out anything unresolved
        for pid in order:
            rec = seen.get(pid)
            if rec and rec["verdict"] is None:
                rec["verdict"] = "unknown"
                stats["unknown"] += 1
                _append_jsonl(DATASET, rec)

    STATE.write_text(json.dumps({"last_run": time.time(), "stats": stats}, indent=1))
    return stats


def _resolve_newest(seen: dict, order: list, ok: bool, stats: dict) -> None:
    """Attach the verdict to EVERY puzzle still unresolved in this session.

    hCaptcha often serves several rounds before answering checkcaptcha (e.g. a
    drag-drop round followed by a 3x3 grid round). The verdict covers the whole
    session: it only passes if every round passed. So marking only the newest
    puzzle would record the earlier rounds as "unknown" even though the operator
    demonstrably got them right -- losing exactly the labels we want. Mark all.
    """
    pending = [pid for pid in order if seen.get(pid) and seen[pid]["verdict"] is None]
    for pid in pending:
        rec = seen[pid]
        rec["verdict"] = "pass" if ok else "fail"
        rec["round"] = pending.index(pid) + 1
        rec["session_size"] = len(pending)
        _append_jsonl(DATASET, rec)
        if ok:
            _append_jsonl(LABELS, {
                "id": rec["id"],
                "question": rec["question"],
                "canvas": rec.get("canvas"),
                "challenge_png": rec.get("challenge_png"),
                "clicks": rec["clicks"],
                "auto_points": rec.get("auto_points") or [],
                "source": "hcaptcha-pass",
                "ts": rec["ts"],
            })
            stats["pass"] += 1
            print(f"[capture] {pid} PASS  clicks={rec['clicks']}")
        else:
            stats["fail"] += 1
            print(f"[capture] {pid} FAIL  clicks={rec['clicks']}")


# ── review page ────────────────────────────────────────────────────────────

def _build_review() -> Path:
    _ensure()
    rows = _read_jsonl(DATASET)
    n_pass = sum(1 for r in rows if r.get("verdict") == "pass")
    n_fail = sum(1 for r in rows if r.get("verdict") == "fail")

    def card(r: dict) -> str:
        verdict = r.get("verdict") or "?"
        cls = {"pass": "ok", "fail": "bad"}.get(verdict, "unk")
        png = r.get("challenge_png")
        pts = r.get("clicks") or []
        auto = r.get("auto_points") or []
        canvas = r.get("canvas") or {}
        cw, chh = canvas.get("w") or 500, canvas.get("h") or 470

        # Existing points: solver answer (grey) and human/recorded clicks (green).
        marks = ""
        for i, c in enumerate(auto):
            if not cw or not chh:
                continue
            marks += (f'<span class="pt auto" style="left:{100.0*c["x"]/cw:.1f}%;'
                      f'top:{100.0*c["y"]/chh:.1f}%">A{i+1}</span>')
        for i, c in enumerate(pts):
            if not cw or not chh:
                continue
            marks += (f'<span class="pt" style="left:{100.0*c["x"]/cw:.1f}%;'
                      f'top:{100.0*c["y"]/chh:.1f}%">{i+1}</span>')

        body = (f'<div class="canvas-wrap" data-id="{r["id"]}" data-w="{cw}" data-h="{chh}">'
                f'<img src="img/{png}" loading="lazy">{marks}</div>' if png
                else '<div class="miss big">image not captured</div>')

        # Every card is labelable -- including PASSES, because a pass only proves
        # the CLICK was accepted, and a human correction is the strongest truth
        # we can store (same idea as the Aliyun slider review page).
        return (f'<article class="card {cls}" data-verdict="{verdict}" data-id="{r["id"]}">'
                f'<header><b>{r.get("question") or "(no prompt)"}</b>'
                f'<span class="meta">{verdict} · {cw}x{chh} · '
                f'{len(pts)} recorded · {len(auto)} solver</span></header>'
                f'{body}'
                f'<div class="tools"><span class="hint">click the image to add a point</span>'
                f'<button class="clear">clear</button></div></article>')

    order = {"fail": 0, "unknown": 1, "pass": 2}
    rows.sort(key=lambda r: (order.get(r.get("verdict"), 3), -r.get("ts", 0)))
    cards = "\n".join(card(r) for r in rows)

    html = f"""<!doctype html>
<meta charset="utf-8">
<title>hCaptcha review — OpenCode Console</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ font: 14px/1.45 system-ui, sans-serif; margin: 0; padding: 18px 22px; }}
h1 {{ font-size: 17px; margin: 0 0 4px; }}
.sub {{ opacity: .65; margin-bottom: 14px; }}
.bar {{ position: sticky; top: 0; background: Canvas; padding: 8px 0 12px;
        display: flex; gap: 8px; flex-wrap: wrap;
        border-bottom: 1px solid color-mix(in srgb, CanvasText 15%, transparent); }}
button {{ font: inherit; padding: 5px 12px; border-radius: 6px; cursor: pointer;
          border: 1px solid color-mix(in srgb, CanvasText 25%, transparent);
          background: transparent; color: inherit; }}
button.on {{ background: color-mix(in srgb, CanvasText 12%, transparent); }}
.card {{ border: 1px solid color-mix(in srgb, CanvasText 15%, transparent);
         border-radius: 8px; padding: 10px 12px; margin: 10px 0; }}
.card.bad {{ border-left: 4px solid #d33; }}
.card.ok  {{ border-left: 4px solid #2a2; }}
.card.unk {{ border-left: 4px solid #d9a300; }}
header {{ display: flex; justify-content: space-between; gap: 12px;
          flex-wrap: wrap; margin-bottom: 8px; }}
.meta {{ opacity: .6; font-size: 12px; font-variant-numeric: tabular-nums; }}
.canvas-wrap {{ position: relative; display: inline-block; max-width: 100%; }}
.canvas-wrap img {{ display: block; max-width: 100%; height: auto; border-radius: 4px; }}
.pt {{ position: absolute; transform: translate(-50%, -50%);
       width: 20px; height: 20px; border-radius: 50%;
       background: #2a2; color: #fff; font-size: 10px; font-weight: 700;
       display: grid; place-items: center; box-shadow: 0 0 0 2px #fff;
       pointer-events: none; }}
.pt.auto {{ background: #888; }}
.canvas-wrap {{ cursor: crosshair; }}
.tools {{ display: flex; gap: 10px; align-items: center; margin-top: 6px; }}
.hint {{ opacity: .55; font-size: 12px; }}
.tools button {{ padding: 2px 10px; font-size: 12px; }}
.miss {{ display: grid; place-items: center; height: 80px; font-size: 12px; opacity: .5; }}
.miss.big {{ height: 140px; }}
</style>
<h1>hCaptcha review — OpenCode Console</h1>
<div class="sub">{len(rows)} puzzles · {n_pass} pass · {n_fail} fail ·
  green outline = tile the operator clicked</div>
<div class="bar">
  <button class="on" data-f="all">All</button>
  <button data-f="fail">Failed</button>
  <button data-f="pass">Passed</button>
  <button data-f="unknown">Unknown</button>
  <span class="sep"></span>
  <button id="export">Export labels</button>
  <span id="count" class="hint"></span>
</div>
{cards}
<script>
// Filter buttons (only the verdict ones; Export is handled separately).
document.querySelectorAll('.bar button[data-f]').forEach(b => b.onclick = () => {{
  document.querySelectorAll('.bar button[data-f]').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  const f = b.dataset.f;
  document.querySelectorAll('.card').forEach(c => {{
    c.style.display = (f === 'all' || c.dataset.verdict === f) ? '' : 'none';
  }});
}});

// Click-to-label: same interaction as the Aliyun slider review page. Every
// click adds a point in CANVAS coordinates, which is exactly what the dataset
// stores, so an exported label can be written straight back.
const LABELS = {{}};
function recount() {{
  const n = Object.keys(LABELS).length;
  document.getElementById('count').textContent = n ? n + ' edited' : '';
}}
document.querySelectorAll('.canvas-wrap').forEach(wrap => {{
  const id = wrap.dataset.id;
  const w = +wrap.dataset.w, h = +wrap.dataset.h;
  wrap.addEventListener('click', ev => {{
    const r = wrap.getBoundingClientRect();
    const x = Math.round((ev.clientX - r.left) * w / r.width);
    const y = Math.round((ev.clientY - r.top) * h / r.height);
    (LABELS[id] = LABELS[id] || []).push({{x, y}});
    const s = document.createElement('span');
    s.className = 'pt';
    s.textContent = LABELS[id].length;
    s.style.left = (100 * x / w) + '%';
    s.style.top = (100 * y / h) + '%';
    wrap.appendChild(s);
    recount();
  }});
  const card = wrap.closest('.card');
  const clr = card.querySelector('.clear');
  if (clr) clr.onclick = () => {{
    LABELS[id] = [];
    wrap.querySelectorAll('.pt:not(.auto)').forEach(n => n.remove());
    recount();
  }};
}});

document.getElementById('export').onclick = () => {{
  // Shape matches labels.jsonl so the file can be appended directly.
  const out = Object.entries(LABELS)
    .filter(([, pts]) => pts.length)
    .map(([id, pts]) => ({{id, clicks: pts, source: 'human-review', ts: Date.now() / 1000}}));
  if (!out.length) {{ alert('No corrections yet. Click the puzzle images first.'); return; }}
  const blob = new Blob([out.map(o => JSON.stringify(o)).join('\n') + '\n'],
                        {{type: 'application/x-ndjson'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'hcaptcha_human_labels.jsonl';
  a.click();
  URL.revokeObjectURL(a.href);
}};
</script>
"""
    out = DIR / "index.html"
    out.write_text(html)
    return out


# ── commands ───────────────────────────────────────────────────────────────

def cmd_capture(a) -> int:
    """One session, or --continuous: a new registration each time one finishes.

    Continuous mode exists because the dataset only grows if capture keeps
    happening. Every accepted puzzle is a labelled row; every account that gets
    registered is one more chance to capture the next puzzle variant.
    """
    total = {"puzzles": 0, "pass": 0, "fail": 0, "unknown": 0, "accounts": 0}
    rounds = 0
    while True:
        rounds += 1
        print(f"\n[capture] === round {rounds} ===", flush=True)
        st = asyncio.run(_capture(a.keep_open, a.email, a.password, auto=getattr(a, "auto", False)))
        for k in ("puzzles", "pass", "fail", "unknown"):
            total[k] += st.get(k, 0)
        total["accounts"] += 1
        print(json.dumps(st, indent=2))
        print(f"[capture] review page: {_build_review()}", flush=True)
        if not a.continuous:
            break
        if a.max_accounts and total["accounts"] >= a.max_accounts:
            print(f"[capture] reached --max-accounts={a.max_accounts}")
            break
        # A fresh account per round, otherwise the mailbox is already used and
        # the signup form rejects the duplicate address.
        if a.email:
            print("[capture] continuous mode with a fixed --email cannot register again; stopping")
            break
        time.sleep(3)

    print("\n[capture] TOTALS:", json.dumps(total))
    return 0


def cmd_review(a) -> int:
    print(f"review page: {_build_review()}")
    return 0


def cmd_merge(a) -> int:
    """Fold a review-page export into labels.jsonl, overriding weaker sources.

    Precedence, strongest last: hcaptcha-pass < human-review. A human looking at
    the image is better evidence than the solver's accepted guess, and both are
    better than nothing -- so a human label REPLACES the auto label for that id
    instead of being appended next to it (that is what produced duplicate,
    contradictory rows in the Aliyun set).
    """
    from pathlib import Path as _P
    src = _P(a.path).expanduser()
    if not src.is_file():
        print(f"no such file: {src}")
        return 1
    incoming = []
    for line in src.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                incoming.append(json.loads(line))
            except Exception:
                pass
    if not incoming:
        print("file has no label rows")
        return 1

    existing = {r.get("id"): r for r in _read_jsonl(LABELS)}
    added = replaced = 0
    for row in incoming:
        pid = row.get("id")
        if not pid or not row.get("clicks"):
            continue
        ds = {d.get("id"): d for d in _read_jsonl(DATASET)}
        base = ds.get(pid) or {}
        rec = {
            "id": pid,
            "question": base.get("question", ""),
            "canvas": base.get("canvas"),
            "challenge_png": base.get("challenge_png"),
            "clicks": row["clicks"],
            "source": "human-review",
            "ts": row.get("ts") or time.time(),
        }
        if pid in existing:
            replaced += 1
        else:
            added += 1
        existing[pid] = rec

    LABELS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                              for r in existing.values()))
    print(f"labels: {len(existing)} total ({added} new, {replaced} replaced)")
    print(f"written: {LABELS}")
    return 0


def cmd_stats(a) -> int:
    rows = _read_jsonl(DATASET)
    labels = _read_jsonl(LABELS)
    print(f"dataset : {DATASET}  ({len(rows)} puzzles)")
    print(f"labels  : {LABELS}  ({len(labels)} accepted)")
    print(f"images  : {IMG}  ({len(list(IMG.glob('*')))} files)")
    ok = sum(1 for r in rows if r.get("verdict") == "pass")
    if rows:
        print(f"pass rate: {ok}/{len(rows)} = {ok / len(rows):.0%}")
    qs: dict = {}
    for r in rows:
        q = (r.get("question") or "?").lower()
        qs[q] = qs.get(q, 0) + 1
    for q, n in sorted(qs.items(), key=lambda t: -t[1])[:12]:
        print(f"  {n:4}  {q[:70]}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("capture", help="visible browser; every puzzle you solve is recorded")
    s.add_argument("--keep-open", type=int, default=1200)
    s.add_argument("--email", default="")
    s.add_argument("--password", default="")
    s.add_argument("--continuous", action="store_true",
                   help="keep registering new accounts after each success (unattended growth)")
    s.add_argument("--max-accounts", type=int, default=0,
                   help="with --continuous: stop after N accounts (0 = unlimited)")
    s.add_argument("--auto", action="store_true",
                   help="let the vision solver click/drag instead of a human")
    s.set_defaults(fn=cmd_capture)

    s = sub.add_parser("review", help="rebuild reports/hcaptcha_review/index.html")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("merge", help="fold a human-label export into labels.jsonl")
    s.add_argument("path", nargs="?", default="~/Downloads/hcaptcha_human_labels.jsonl")
    s.set_defaults(fn=cmd_merge)

    s = sub.add_parser("stats", help="dataset counters + question histogram")
    s.set_defaults(fn=cmd_stats)

    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
