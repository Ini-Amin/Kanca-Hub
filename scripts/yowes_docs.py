#!/usr/bin/env python3
"""SheerID-ready teacher documents via the yowes engine.

`script.py` (K-12 verifier) uploads a document to SheerID at the `docUpload`
step. Its built-in `generate_teacher_badge()` draws a crude 500x350 Pillow
badge, which SheerID rejects as "The document is insufficient". This module
renders a real A4 employment letter / teacher ID card / teaching license with
the yowes engine instead, and returns raw PNG bytes ready for the upload.

Contract with SheerID (see docs/YOWES_SHEERID_BRIDGE.md §1.2):
    mimeType / PUT Content-Type : image/png
    fileSize                    : exact byte length of the PNG
    payload                     : the raw PNG bytes, PUT to a presigned S3 URL

Everything downstream of `doc_data` therefore keeps working, because this
module returns the same thing `generate_teacher_badge()` did: `bytes`.

Usage:
    from yowes_docs import generate_doc_png
    png = generate_doc_png("employment_letter", "John", "Doe", school, "Teacher", "1985-03-15")

CLI (managed venv):
    /home/amen/.local/share/auto-freecf/venv/bin/python scripts/yowes_docs.py \
        --first John --last Doe --school "Norton Elementary" --out /tmp/doc.png
"""

from __future__ import annotations

import argparse
import sys
from io import BytesIO
from pathlib import Path

# yowes lives outside this repo; put it on sys.path before importing.
YOWES_DIR = Path.home() / "petani-proxy" / "yowes"
if str(YOWES_DIR) not in sys.path:
    sys.path.insert(0, str(YOWES_DIR))

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DOC_TYPES = ("employment_letter", "teacher_id", "teaching_license")

# SheerID accepts the ~140-190 KB yowes PNGs, but bound them anyway so a huge
# A4 render can never turn into an upload failure.
MAX_DOC_BYTES = 4_000_000
MAX_DIMENSION = 2400          # downscale target if we ever exceed MAX_DOC_BYTES


class YowesUnavailable(RuntimeError):
    """yowes (or its Pillow dependency) could not be imported."""


def _load_generator():
    """Import USGenerator lazily so a missing yowes never breaks script.py."""
    try:
        from countries.us import USGenerator  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        raise YowesUnavailable(f"cannot import yowes USGenerator from {YOWES_DIR}: {e}") from e
    return USGenerator


def _fmt_dob(dob: str) -> str:
    """YYYY-MM-DD -> MM/DD/YYYY (US documents print the US order)."""
    parts = (dob or "").split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        y, m, d = parts
        return f"{int(m):02d}/{int(d):02d}/{y}"
    return dob or ""


def enrich_school(school: dict) -> dict:
    """Add the location keys yowes' US templates read.

    `script.py`'s `select_school()` returns only id/idExtended/name/type, and
    yowes indexes `school['town']` DIRECTLY (countries/us/__init__.py:193,296),
    so passing it through unchanged raises KeyError. We derive town/state from
    the K12 `city` field ("Chicago, IL") when present, else fall back to
    harmless defaults. The SheerID identity (id/idExtended/name) is preserved.
    """
    city = str(school.get("city") or "").strip()
    town, _, state = city.partition(",")
    town, state = town.strip(), state.strip()

    return {
        # --- identity used for the SheerID submission ---
        "name": school.get("name", "Jefferson High School"),
        "id": school.get("id"),
        "idExtended": school.get("idExtended"),
        # --- fields the yowes templates read ---
        "town": town or "Louisville",
        "state": state or "KY",
        "postcode": school.get("postcode") or "40241",
        "lea": school.get("lea") or f"{town or 'Jefferson County'} Public Schools",
        "address": school.get("address") or "8101 Brownsboro Road",
        "phone": school.get("phone") or "(502) 485-8308",
    }


def _downscale(png: bytes) -> bytes:
    """Shrink an oversized render so it stays uploadable."""
    from PIL import Image  # noqa: PLC0415

    im = Image.open(BytesIO(png)).convert("RGB")
    im.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
    buf = BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def generate_doc_png(doc_type: str, first: str, last: str, school: dict,
                     position: str = "Teacher", dob: str = "") -> bytes:
    """Render one document and return PNG bytes.

    doc_type: 'employment_letter' | 'teacher_id' | 'teaching_license'
    school:   a K12 dict (enriched internally) or an already-full yowes dict
    """
    USGenerator = _load_generator()
    gen = USGenerator()

    if doc_type not in gen.get_document_types():
        raise ValueError(f"unsupported doc_type {doc_type!r}; expected one of {DOC_TYPES}")

    rendered = gen.generate_document(
        doc_type, first, last, enrich_school(school), position, _fmt_dob(dob),
    )

    if not rendered or rendered[:8] != PNG_MAGIC:
        raise ValueError(f"yowes did not return a PNG for {doc_type!r}")
    if len(rendered) > MAX_DOC_BYTES:
        rendered = _downscale(rendered)
        if len(rendered) > MAX_DOC_BYTES:
            raise ValueError(f"document too large even after downscale: {len(rendered)} bytes")
    return rendered


def generate_docs(doc_types, first: str, last: str, school: dict,
                  position: str = "Teacher", dob: str = "") -> dict:
    """Render several documents at once -> {doc_type: png_bytes}.

    A single doc type that fails does not sink the others; its key is simply
    omitted, and the caller decides whether to fall back to the built-in badge.
    """
    out: dict[str, bytes] = {}
    for dt in doc_types:
        try:
            out[dt] = generate_doc_png(dt, first, last, school, position, dob)
        except Exception as e:  # noqa: BLE001
            print(f"      [!] yowes {dt} failed: {e}", flush=True)
    return out


def pick_best_document(first: str, last: str, school: dict,
                       position: str = "Teacher", dob: str = "") -> tuple[str, bytes]:
    """Preferred upload: an employment letter reads as the strongest proof.

    Falls back through teacher_id, then teaching_license. Raises if all fail,
    which lets `script.py` fall back to the legacy badge.
    """
    last_err: Exception | None = None
    for dt in ("employment_letter", "teacher_id", "teaching_license"):
        try:
            return dt, generate_doc_png(dt, first, last, school, position, dob)
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"      [!] yowes {dt} failed: {e}", flush=True)
    raise YowesUnavailable(f"no yowes document could be rendered: {last_err}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate teacher documents (PNG) via yowes")
    ap.add_argument("--first", required=True)
    ap.add_argument("--last", required=True)
    ap.add_argument("--school", default="Jefferson High School",
                    help="school name (matched against the yowes list, else used as-is)")
    ap.add_argument("--city", default=None, help="'Chicago, IL' — sets town/state")
    ap.add_argument("--position", default="Teacher")
    ap.add_argument("--dob", default="1985-03-15")
    ap.add_argument("--type", dest="doc_type", default="employment_letter",
                    choices=list(DOC_TYPES))
    ap.add_argument("--all", action="store_true", help="render all three doc types")
    ap.add_argument("--out", default=None, help="output PNG path (single type)")
    ap.add_argument("--outdir", default=".", help="output dir when --all is used")
    a = ap.parse_args()

    school = {"name": a.school, "city": a.city} if a.city else {"name": a.school}
    # If the name matches a real yowes school, borrow its full location data.
    try:
        USGenerator = _load_generator()
        hit = USGenerator().search_school(a.school)
        if hit:
            school = {**hit, **{k: v for k, v in school.items() if v}}
            print(f"      matched yowes school: {hit['name']} ({hit.get('town')}, {hit.get('state')})")
    except YowesUnavailable as e:
        print(f"      [!] {e}")
        return 1

    types = list(DOC_TYPES) if a.all else [a.doc_type]
    rc = 0
    for dt in types:
        try:
            png = generate_doc_png(dt, a.first, a.last, school, a.position, a.dob)
        except Exception as e:  # noqa: BLE001
            print(f"      ✗ {dt}: {e}")
            rc = 1
            continue
        dest = Path(a.out) if (a.out and not a.all) else Path(a.outdir) / f"{dt}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(png)
        print(f"      ✓ {dt}: {len(png):,} bytes ({len(png)/1024:.1f} KB) -> {dest}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
