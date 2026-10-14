#!/usr/bin/env python3
"""scripts/slider_review_html.py — build a self-contained review page for the
Aliyun slider puzzles so a human can confirm the exact gap X of every puzzle the
detector and the vision model disagree on.

Input  : reports/slider_review/merged.json   (see slider_review_merge.py)
         reports/slider_review/img/*.png     (bg + piece, 300x300 / ~300 tall)
Output : reports/slider_review/index.html + reports/slider_review/img/

Everything slider-related lives in ONE folder: reports/slider_review/
(override with SLIDER_DIR). The legacy ~/.zcode-bridge paths are symlinks into it.

Three sections:
  * "Needs verification" — puzzles with no trusted label where the diff detector
    and the vision model disagree. Each card shows the background, both candidate
    X's as guide lines, and a live preview of the piece snapped to the X you type.
  * "Failed" — puzzles the solver never landed (aim off > 5px). These are the raw
    material for the RLHF loop: labeling them is the human-feedback step that
    feeds the label store so the same puzzle is answered for free next time.
  * "Labeled" — puzzles that already have a trusted X (team labels / real passes)
    or where detector and vision agree. Read-only, for spot-checking.
  * "Windscribe" — SEPARATE section, own image set (ws_img/, 700px-wide bg +
    240px piece + a `top` offset hint) and its own export file (ws_verified.json).
    These guard the AuthToken/login slider on api.windscribe.com.

The page is a utility, not a landing page: one job (read the gap, fix the number),
so it is a light, flat, high-density tool. No gradients, no glass, no glow.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = Path(os.environ.get("SLIDER_DIR", REPO / "reports" / "slider_review"))
IMG_SRC = SRC / "img"
OUT = SRC

# palette: neutral base + two state colors. no third accent, no gradient.
CSS = """
:root{
  --bg:#fbfbfa; --panel:#fff; --line:#e3e3df; --line-strong:#c9c9c3;
  --ink:#1b1b19; --ink-2:#5c5c56; --ink-3:#8a8a83;
  --verify:#b45309; --verify-bg:#fdf6ec; --verify-line:#e8cfa8;
  --ok:#15803d; --ok-bg:#f2f8f3; --ok-line:#c2ddc8;
  --det:#1d4ed8; --vis:#9a3412;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--ink);
  font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:inherit}
header{position:sticky;top:0;z-index:5;background:var(--panel);
  border-bottom:1px solid var(--line);padding:10px 16px;
  display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
header h1{font-size:15px;margin:0;font-weight:650;letter-spacing:-.01em}
header .meta{color:var(--ink-2);font-size:12.5px}
header .meta b{font-family:var(--mono);font-weight:600;color:var(--ink)}
header .spacer{flex:1}
button{font:inherit;font-size:13px;padding:6px 11px;border:1px solid var(--line-strong);
  background:var(--panel);color:var(--ink);border-radius:5px;cursor:pointer}
button:hover{background:#f4f4f1}
button.primary{background:var(--ink);color:#fff;border-color:var(--ink)}
button.primary:hover{background:#000}
main{padding:16px;max-width:1240px;margin:0 auto}
section{margin-bottom:26px}
h2{font-size:13px;font-weight:650;margin:0 0 3px}
h2 .count{font-family:var(--mono);color:var(--ink-2);font-weight:500}
.hint{color:var(--ink-2);font-size:12.5px;margin:0 0 12px;max-width:70ch}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(238px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:7px;overflow:hidden}
.card.verify{border-color:var(--verify-line);background:var(--verify-bg)}
.card.fail{border-color:#e0b4b4;background:#fdf3f3}
.card.lab{border-color:var(--ok-line);background:var(--ok-bg)}
.card .cap{display:flex;justify-content:space-between;align-items:center;gap:8px;
  padding:6px 9px;border-bottom:1px solid var(--line);font-size:11.5px}
.card.verify .cap{border-color:var(--verify-line)}
.card.fail .cap{border-color:#e0b4b4}
.card.lab .cap{border-color:var(--ok-line)}
.id{font-family:var(--mono);color:var(--ink-2);font-size:11px}
.state{font-weight:650;font-size:10.5px;letter-spacing:.02em;text-transform:uppercase}
.verify .state{color:var(--verify)}
.fail .state{color:#b91c1c}
.lab .state{color:var(--ok)}
.pair{display:flex;gap:8px;padding:9px;align-items:flex-start}
canvas{display:block;border:1px solid var(--line);border-radius:4px;background:#222;
  image-rendering:pixelated;cursor:grab;touch-action:none}
.card.dragging canvas{cursor:grabbing}
.card.dragging{border-color:var(--ink)}
.card.dragging canvas{box-shadow:0 0 0 2px var(--ink)}
.fields{flex:1;min-width:0;display:flex;flex-direction:column;gap:6px}
label.row{display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--ink-2)}
label.row span.k{width:52px;flex:none;font-family:var(--mono)}
input[type=number]{width:72px;font:inherit;font-family:var(--mono);font-size:13px;
  padding:4px 6px;border:1px solid var(--line-strong);border-radius:4px;background:#fff}
input[type=number]:focus{outline:2px solid var(--ink);outline-offset:1px}
.val{font-family:var(--mono);font-size:12px}
.swatch{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:3px;
  vertical-align:middle}
.det .swatch{background:var(--det)} .vis .swatch{background:var(--vis)}
.err{font-family:var(--mono);font-size:11.5px;color:var(--ink-2)}
.err.bad{color:#b91c1c}
.mini{font-size:11px;color:var(--ink-3)}
footer{color:var(--ink-3);font-size:11.5px;padding:10px 16px 40px;max-width:1240px;margin:0 auto}
kbd{font-family:var(--mono);font-size:11px;border:1px solid var(--line-strong);
  border-bottom-width:2px;border-radius:3px;padding:1px 4px;background:#fff}
"""

JS = r"""
const DATA = __DATA__;
const IMG = "img/";

// ---- canvas: draw bg, then the piece at x; guides for detector/vision ----
function draw(card){
  const p = card._p, cv = card.querySelector("canvas"), g = cv.getContext("2d");
  const x = clamp(parseInt(card.querySelector("input").value,10));
  g.clearRect(0,0,cv.width,cv.height);
  if(!p._bg || !p._pc) return;
  const s = cv.width / p._bg.naturalWidth;
  g.drawImage(p._bg, 0,0, cv.width, cv.height);
  if(x!=null){
    g.globalAlpha=.95;
    const sc = cv.width / p._bg.naturalWidth;   // uniform: display px per bg px
    g.drawImage(p._pc, x*sc, (p.top||0)*sc, p._pc.naturalWidth*sc, p._pc.naturalHeight*sc);
    g.globalAlpha=1;
  }
  // guides
  const line=(gx,col,dash)=>{ if(gx==null)return; g.save();
    g.setLineDash(dash); g.strokeStyle=col; g.lineWidth=1.5;
    g.beginPath(); g.moveTo(gx*s+.5,0); g.lineTo(gx*s+.5,cv.height); g.stroke(); g.restore(); };
  line(p.detector, "#1d4ed8", [4,3]);
  line(p.vision,   "#9a3412", [2,3]);
  // readout
  const out = card.querySelector(".err");
  if(p.truth!=null && x!=null){
    const d=Math.abs(x-p.truth);
    out.textContent = d===0 ? "exact vs truth" : "off by "+d+"px vs truth "+p.truth;
    out.className = "err"+(d===0?"":" bad");
  } else if(p.detector!=null && x!=null){
    const d=Math.abs(x-p.detector);
    out.textContent = d===0 ? "= detector" : "detector "+p.detector;
    out.className="err";
  } else out.textContent="";
}
function clamp(v,max){ max=max||300; return (v==null||isNaN(v))?null:Math.max(0,Math.min(max,v)); }

function load(img,src){ return new Promise(r=>{ img.onload=()=>r(img);
  img.onerror=()=>r(null); img.src=src; }); }

// ---- drag the piece straight to the gap (the whole point of the page) ----
function attachDrag(el){
  const cv = el.querySelector("canvas");
  const inp = el.querySelector("input");
  let dragging = false;
  // canvas is 300 CSS px wide (native 300) -> X == offsetX, but scale anyway
  const xFromEvent = e => {
    const r = cv.getBoundingClientRect();
    const bgw = el._p.status==="windscribe" ? 700 : 300;   // input is in BG pixels
    return Math.round((e.clientX - r.left) * (bgw / r.width));
  };
  const xmax = el._p.status==="windscribe" ? 460 : 300;
  const setX = v => { inp.value = clamp(v, xmax); inp.dispatchEvent(new Event("input")); };
  let grabBg = 0, grabClientX = 0;   // delta-drag: piece moves WITH the cursor
  cv.addEventListener("pointerdown", e => {
    if(e.button!=null && e.button!==0) return;
    dragging = true; el.classList.add("dragging");
    cv.setPointerCapture(e.pointerId);
    grabBg = parseInt(inp.value,10)||0;
    grabClientX = e.clientX;
    e.preventDefault();
  });
  cv.addEventListener("pointermove", e => {
    if(!dragging) return;
    const r = cv.getBoundingClientRect();
    const bgw = el._p.status==="windscribe" ? 700 : 300;
    setX(Math.round(grabBg + (e.clientX - grabClientX) * (bgw / r.width)));
    e.preventDefault();
  });
  const stop = e => {
    if(!dragging) return;
    dragging = false; el.classList.remove("dragging");
    try{ cv.releasePointerCapture(e.pointerId); }catch(_){}
  };
  cv.addEventListener("pointerup", stop);
  cv.addEventListener("pointercancel", stop);
  // left/right arrows also nudge, matching up/down in the number field
  cv.tabIndex = 0;
  cv.addEventListener("keydown", e => {
    if(e.key==="ArrowLeft"||e.key==="ArrowRight"){
      e.preventDefault();
      setX((parseInt(inp.value,10)||0)+(e.key==="ArrowRight"?1:-1));
    }
  });
}

function card(p){
  const el = document.createElement("div");
  const kind = p.status==="verify" ? "verify" : p.status==="failed" ? "fail"
             : p.status==="windscribe" ? "lab" : "lab";
  el.className = "card " + kind;
  const start = p.truth ?? p.detector ?? p.vision ?? 0;
  const label = p.status==="windscribe" ? "windscribe" : p.status==="verify" ? "verify" : p.status==="failed" ? "failed" : "labeled";
  el.innerHTML =
    '<div class="cap"><span class="id">'+p.id.slice(0,10)+'</span>'+
    '<span class="state">'+label+'</span></div>'+
    '<div class="pair"><canvas width="'+(p.status==="windscribe"?700:300)+'" height="'+(p.status==="windscribe"?400:300)+'" style="width:100%;height:auto" title="drag the piece to the slot"></canvas>'+
      '<div class="fields">'+
        '<label class="row"><span class="k">gap&nbsp;X</span>'+
          '<input type="number" min="0" max="'+(p.status==="windscribe"?460:300)+'" step="1" value="'+start+'"></label>'+
        '<div class="row det"><span class="val"><span class="swatch"></span>'+
          (p.detector==null?"—":p.detector)+'</span> <span class="mini">detector</span></div>'+
        '<div class="row vis"><span class="val"><span class="swatch"></span>'+
          (p.vision==null?"—":p.vision)+'</span> <span class="mini">vision'+
          (p.conf?" ("+p.conf+")":"")+'</span></div>'+
        (p.truth!=null?'<div class="row"><span class="val">'+
          '<span class="swatch" style="background:#15803d"></span>'+p.truth+
          '</span> <span class="mini">truth'+(p.source?" · "+p.source:"")+'</span></div>':'')+
        (p.attempts?'<div class="mini">'+p.attempts+' attempt'+(p.attempts>1?'s':'')+
          (p.fails?' · '+p.fails+' miss':'')+'</div>':'')+
        '<div class="err"></div>'+
      '</div></div>';
  el._p = p;
  attachDrag(el);
  const inp = el.querySelector("input");
  inp.addEventListener("input", ()=>{ p.chosen = clamp(parseInt(inp.value,10), p.status==="windscribe"?460:300); draw(el); });
  inp.addEventListener("keydown", e=>{
    if(e.key==="ArrowUp"||e.key==="ArrowDown"){
      e.preventDefault(); inp.value = clamp((parseInt(inp.value,10)||0)+(e.key==="ArrowUp"?1:-1), p.status==="windscribe"?460:300);
      inp.dispatchEvent(new Event("input"));
    }
  });
  inp.addEventListener("wheel", e=>{ if(document.activeElement===inp){e.preventDefault();
    inp.value=clamp((parseInt(inp.value,10)||0)+(e.deltaY<0?1:-1), p.status==="windscribe"?460:300);
    inp.dispatchEvent(new Event("input")); }}, {passive:false});
  return el;
}

function render(list){
  const host = document.getElementById(list.host);
  host.innerHTML="";
  list.items.forEach(p=>host.appendChild(card(p)));
}

function exportLabels(){
  const out = {}, ws = {};
  document.querySelectorAll(".card").forEach(c=>{
    const p=c._p, v=c.querySelector("input").value;
    if(p.status==="windscribe"){
      ws[p.id] = {x: clamp(parseInt(v,10),460), top:p.top??null};
    } else if(p.status==="verify" || p.status==="failed"){
      out[p.id] = {x: clamp(parseInt(v,10)), detector:p.detector,
        vision:p.vision, truth:p.truth??null, conf:p.conf??null};
    }
  });
  const dl=(obj,name)=>{ const blob=new Blob([JSON.stringify(obj,null,1)],{type:"application/json"});
    const a=document.createElement("a"); a.href=URL.createObjectURL(blob);
    a.download=name; a.click(); URL.revokeObjectURL(a.href); };
  if(Object.keys(out).length) dl(out,"slider_verified.json");
  if(Object.keys(ws).length) dl(ws,"ws_verified.json");
}

async function main(){
  render({host:"verify", items:DATA.filter(p=>p.status==="verify")});
  render({host:"failed", items:DATA.filter(p=>p.status==="failed")});
  render({host:"labeled", items:DATA.filter(p=>p.status==="labeled")});
  render({host:"windscribe", items:DATA.filter(p=>p.status==="windscribe")});
  // load images then paint
  for(const c of document.querySelectorAll(".card")){
    const p=c._p;
    const dir = p.status==="windscribe" ? "ws_img/" : IMG;
    p._bg = await load(new Image(), dir+p.bg);
    p._pc = await load(new Image(), dir+p.piece);
    draw(c);
  }
  document.getElementById("export").addEventListener("click", exportLabels);
  document.getElementById("export2").addEventListener("click", exportLabels);
}
main();
"""


def _ws_puzzles() -> list[dict]:
    meta = json.loads((SRC / "ws.json").read_text()) if (SRC / "ws.json").exists() else {}
    out = []
    for i in sorted(meta):
        if (SRC / "ws_img" / f"{i}_bg.png").exists():
            out.append(dict(id=i, bg=f"{i}_bg.png", piece=f"{i}_piece.png",
                            detector=None, flat_dark=None, vision=None, conf=None,
                            truth=None, source="", status="windscribe",
                            attempts=0, fails=0, top=meta[i].get("top")))
    return out


def main() -> int:
    merged = json.loads((SRC / "merged.json").read_text())
    ws = _ws_puzzles()
    # order: verify first (the ones that need a human), then failed, then labeled
    verify = [p for p in merged if p["status"] == "verify"]
    failed = [p for p in merged if p["status"] == "failed"]
    labeled = [p for p in merged if p["status"] == "labeled"]
    verify.sort(key=lambda p: abs((p.get("detector") or 0) - (p.get("vision") or 0)), reverse=True)
    failed.sort(key=lambda p: p.get("attempts") or 0, reverse=True)
    labeled.sort(key=lambda p: p["id"])

    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / "img"
    dst.mkdir(exist_ok=True)
    n = 0
    if dst.resolve() != IMG_SRC.resolve():
        for p in merged:
            for f in (p["bg"], p["piece"]):
                s = IMG_SRC / f
                if s.exists():
                    shutil.copyfile(s, dst / f)
                    n += 1
    else:
        n = len(list(dst.glob("*.png")))

    payload = [{k: p.get(k) for k in ("id", "bg", "piece", "detector", "flat_dark",
                                      "vision", "conf", "truth", "source", "status",
                                      "attempts", "fails", "top")}
               for p in verify + failed + labeled + ws]
    wsdir = OUT / "ws_img"
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Aliyun slider review — confirm the gap X</title>
<style>{CSS}</style></head>
<body>
<header>
  <h1>Aliyun slider — gap X review</h1>
  <span class="meta">{len(verify)} to verify · {len(failed)} failed · {len(labeled)} labeled · {len(merged)} puzzles · {len(ws)} windscribe</span>
  <span class="spacer"></span>
  <button class="primary" id="export">Export my labels</button>
</header>
<main>
  <section>
    <h2>Needs verification <span class="count">{len(verify)}</span></h2>
    <p class="hint">The diff detector and the vision model disagree on these. <b>Drag the
    piece</b> until it drops into the dark slot — click anywhere to snap, drag to fine-tune.
    The number updates as you move. <kbd>←</kbd><kbd>→</kbd> (on the image) or <kbd>↑</kbd><kbd>↓</kbd>
    (in the box) nudge by 1px; the wheel nudges the focused box. Dashed
    <span class="det"><span class="swatch"></span>blue</span> = detector,
    <span class="vis"><span class="swatch"></span>red</span> = vision.</p>
    <div class="grid" id="verify"></div>
  </section>
  <section>
    <h2>Failed <span class="count">{len(failed)}</span></h2>
    <p class="hint">The solver never landed these (aim off &gt;5px, so the drag missed).
    Labeling them is the human-feedback step: the X you set is stored and the same
    puzzle is then answered for free next time (see <code>slider_attempts.py</code>).</p>
    <div class="grid" id="failed"></div>
  </section>
  <section>
    <h2>Labeled <span class="count">{len(labeled)}</span></h2>
    <p class="hint">Already trusted: real slider passes, team-confirmed labels, or the
    detector and vision model agree. Read-only — spot-check if you like.</p>
    <div class="grid" id="labeled"></div>
  </section>
  <section>
    <h2>Windscribe <span class="count">{len(ws)}</span></h2>
    <p class="hint">Auth-token slider from <code>api.windscribe.com/AuthToken/login</code>
    (guard of the account-quota API). Bg is 700px wide and the piece sits at the
    <b>top</b> offset printed on the card — set the gap X the same way: drag, click,
    or type. Export lands in <code>ws_verified.json</code>.</p>
    <div class="grid" id="windscribe"></div>
  </section>
  <p><button id="export2">Export my labels</button>
     <span class="mini">— downloads <code>slider_verified.json</code> with your X for every
     card in "Needs verification" and "Failed".</span></p>
</main>
<footer>Generated by <code>scripts/slider_review_html.py</code> from
<code>reports/slider_review/merged.json</code> — one folder holds the images, the
attempt dataset, every label source, and this page. Images: Aliyun slider puzzles
captured by the z.ai signup flow; Windscribe sliders in ws_img/ from the
AuthToken/login challenge.</footer>
<script>{JS.replace("__DATA__", json.dumps(payload))}</script>
</body></html>
"""
    (OUT / "index.html").write_text(html)
    print("wrote", OUT / "index.html", f"({len(html)//1024} KB, {n} images)")
    print("  verify:", len(verify), " failed:", len(failed), " labeled:", len(labeled))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
