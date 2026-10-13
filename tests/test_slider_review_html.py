"""Regression: the slider review page must let a human DRAG the piece to the gap.

The first version only had a number box, so the page could not be "slid" — the
whole point was to move the piece into the slot with the mouse. Guard the drag
handlers (pointerdown/move + capture) and the no-tab-spawn property.
"""
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GEN = REPO / "scripts" / "slider_review_html.py"


def load_gen():
    spec = importlib.util.spec_from_file_location("slider_review_html", GEN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["slider_review_html"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestSliderReviewDrag(unittest.TestCase):
    def _generate(self):
        """Run the generator against a tiny fixture and return the HTML."""
        import shutil
        m = load_gen()
        tmp = Path(tempfile.mkdtemp())
        (tmp / "img").mkdir()
        # 1x1 png, enough for the copy step
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
            "0049454e44ae426082")
        (tmp / "img" / "bg.png").write_bytes(png)
        (tmp / "img" / "pc.png").write_bytes(png)
        (tmp / "merged.json").write_text(json.dumps([{
            "id": "p1", "bg": "bg.png", "piece": "pc.png",
            "detector": 120, "vision": 150, "status": "verify"}]))
        m.SRC = tmp
        m.IMG_SRC = tmp / "img"
        m.OUT = tmp / "out"
        m.main()
        html = (tmp / "out" / "index.html").read_text()
        shutil.rmtree(tmp, ignore_errors=True)
        return html

    def test_page_has_pointer_drag_handlers(self):
        html = self._generate()
        self.assertIn("pointerdown", html)
        self.assertIn("pointermove", html)
        self.assertIn("setPointerCapture", html)
        self.assertIn("attachDrag(el)", html)

    def test_canvas_is_draggable_not_just_a_crosshair(self):
        html = self._generate()
        self.assertIn("cursor:grab", html)
        self.assertIn("touch-action:none", html)

    def test_page_never_spawns_a_tab(self):
        html = self._generate()
        self.assertNotIn("window.open", html)
        self.assertNotIn('target="_blank"', html)

    def test_export_still_present(self):
        html = self._generate()
        self.assertIn("slider_verified.json", html)

    def test_failed_section_present(self):
        """The user asked for the FAILED puzzles to be visible on the page."""
        import json as _j
        import shutil
        import tempfile
        m = load_gen()
        tmp = Path(tempfile.mkdtemp())
        (tmp / "img").mkdir()
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
            "0049454e44ae426082")
        for f in ("bg.png", "pc.png"):
            (tmp / "img" / f).write_bytes(png)
        (tmp / "merged.json").write_text(_j.dumps([{
            "id": "f1", "bg": "bg.png", "piece": "pc.png",
            "detector": 150, "vision": None, "status": "failed",
            "attempts": 3, "fails": 3}]))
        m.SRC = tmp
        m.IMG_SRC = tmp / "img"
        m.OUT = tmp / "out"
        m.main()
        html = (tmp / "out" / "index.html").read_text()
        shutil.rmtree(tmp, ignore_errors=True)
        self.assertIn('id="failed"', html)
        self.assertIn("Failed", html)
        self.assertIn('"status": "failed"', html)

    def test_embedded_js_parses(self):
        """Strip the browser globals and hand the script to node if available."""
        import os, shutil, subprocess, tempfile
        if not shutil.which("node"):
            self.skipTest("node not installed")
        html = self._generate()
        js = re.search(r"<script>(.*)</script>", html, re.S).group(1)
        stub = ("globalThis.document={getElementById:()=>({addEventListener(){}}),"
                "querySelectorAll:()=>[],createElement:()=>({classList:{add(){},remove(){}},"
                "querySelector:()=>({addEventListener(){}}),set innerHTML(v){},get innerHTML(){return''}})};"
                "globalThis.Image=class{};globalThis.Blob=class{};"
                "globalThis.URL={createObjectURL:()=>'',revokeObjectURL(){}};globalThis.Event=class{};\n")
        fd, path = tempfile.mkstemp(suffix=".js")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(stub + js)
            p = subprocess.run(["node", "--check", path], capture_output=True)
        finally:
            os.unlink(path)
        self.assertEqual(p.returncode, 0, p.stderr.decode())


if __name__ == "__main__":
    unittest.main()
