# yowes → SheerID document bridge

How to replace the primitive `generate_teacher_badge()` in the K-12 verifier with
**real documents produced by the yowes engine**, so the SheerID `docUpload` step
receives a proper teacher ID / employment letter PNG instead of a hand-drawn badge.

> Status: **design / plan only.** `script.py` is **not** modified by this doc.
> Follow the TODO patch plan at the end to apply it.

---

## 1. Where the document is produced and uploaded

File: `~/petani-proxy/Farm-Acc-ChatGPT-K-12-Teachers/PyRuntime_64/script.py`

### 1.1 Current (primitive) generation — line ~481

```python
def generate_teacher_badge(first_name: str, last_name: str, school_name: str) -> bytes:
    """Generate fake K12 teacher badge PNG"""
    width, height = 500, 350
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    ...  # a 500x350 PIL badge
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()
```

Called once inside `K12Verifier.verify()` at **line ~765**:

```python
# Step 1: Generate teacher badge
print("\n   -> Langkah 1/4: Membuat kartu identitas guru...")
doc_data = generate_teacher_badge(first_name, last_name, school["name"])   # <-- replace
doc_size_kb = len(doc_data) / 1024
print(f"      Ukuran dokumen: {doc_size_kb:.2f} KB")
```

### 1.2 What SheerID expects at `docUpload`

The upload step (lines ~954–994) does this:

```python
# Step 4: Upload document
step4_body = {
    "files": [{
        "fileName": "teacher_badge.png",
        "mimeType": "image/png",          # <-- SheerID wants a PNG
        "fileSize": len(doc_data),         # <-- exact byte length of the PNG
    }]
}
data, status = self._request(
    "POST",
    f"{SHEERID_BASE_URL}/rest/v2/verification/{self.verification_id}/step/docUpload",
    step4_body,
)
upload_url = data["documents"][0].get("uploadUrl")   # <-- presigned S3 PUT URL
...
# PUT the raw bytes with Content-Type: image/png
if not self._upload_to_s3(upload_url, doc_data, "image/png"):
    return {"success": False, "error": "Upload ke S3 gagal"}
```

and `_upload_to_s3` (line ~707):

```python
def _upload_to_s3(self, upload_url: str, data: bytes, mime_type: str) -> bool:
    response = self.client.put(upload_url, content=data,
                               headers={"Content-Type": mime_type}, timeout=60.0)
    return 200 <= response.status_code < 300
```

**Contract summary:**

| Field | Requirement |
|---|---|
| `mimeType` / PUT `Content-Type` | `image/png` |
| `fileSize` | exact byte length of the PNG payload |
| payload to the presigned `uploadUrl` | **raw PNG bytes** via HTTP `PUT` |
| `doc_data` source | any function returning `bytes` that are a valid PNG |

So the bridge only needs to make `doc_data` come from yowes. Everything
downstream (init, PUT, `completeDocUpload`) already works with arbitrary PNG
bytes — no changes needed there.

---

## 2. What yowes provides

Engine: `~/petani-proxy/yowes` (also reachable via `generate_teacher_doc.py` in
the K-12 repo, which wraps it).

```python
from countries.us import USGenerator          # ~/petani-proxy/yowes/countries/us/__init__.py

gen = USGenerator()
gen.get_document_types()                       # ['employment_letter', 'teacher_id', 'teaching_license']

# signature (countries/us/__init__.py, line 101):
#   generate_document(doc_type, first, last, school, position, dob) -> bytes
png: bytes = gen.generate_document("teacher_id", first, last, school, position, dob)
```

`generate_document(doc_type, ...)` returns **raw PNG bytes**:
- `teacher_id` → US teacher ID card (`_generate_teacher_id`), A4-ish, `PNG`.
- `employment_letter` → employment verification letter, `PNG`.
- `teaching_license` → teaching license, `PNG`.

All three end with the same pattern:

```python
from io import BytesIO
buffer = BytesIO()
img.save(buffer, format='PNG')
return buffer.getvalue()   # -> bytes
```

Verified locally: `teacher_id` ≈ 186 KB, `employment_letter` ≈ 143 KB,
`teaching_license` ≈ 137 KB, each starting with the PNG magic
`\x89PNG\r\n\x1a\n`. These are well within SheerID's accepted upload sizes, but
you should still bound them (see §4.3).

---

## 3. ⚠️ The school-dict shape mismatch (must handle)

This is the single biggest gotcha. The K-12 verifier's `select_school()`
(line ~343) returns a **minimal** dict:

```python
{"id": 3521141, "idExtended": "3521141",
 "name": "Walter Payton College Preparatory High School", "type": "K12"}
```

But yowes' US templates read extra keys — `town`, `state`, `postcode`, `lea`,
`address`, `phone`. A yowes default school looks like:

```python
{'name': 'Jefferson High School', 'address': '4141 Flowing Springs Road',
 'town': 'Shenandoah Junction', 'postcode': '25442', 'state': 'WV',
 'phone': '(304) 725-8491', 'lea': '...'}
```

Passing the K12 dict straight through **crashes**:

```
KeyError: 'town'
```

Verified. So the bridge must **augment** the K12 school dict with the location
keys yowes needs (parse `city, ST` from the K12 table, or supply defaults). The
SheerID submission still uses the original K12 `id`/`idExtended`/`name` — only
the *rendering* dict needs the extra fields.

**Two viable approaches:**

- **A (recommended).** Enrich the school dict in the bridge before rendering,
  using the `city` field already present in `K12_SCHOOLS`. Keeps the SheerID
  school identity intact.
- **B.** Match the K12 school name against yowes' `gen.search_school(name)` and
  use yowes' dict for rendering while keeping the K12 id for submission. Simpler
  but names rarely match (K12 list is magnet high schools; yowes list is
  generic), so A is more reliable.

---

## 4. Concrete integration

### 4.1 New bridge module

Create `~/Auto-FreeCF/scripts/yowes_bridge.py` (new file — does not touch the
K-12 repo):

```python
"""Generate SheerID-ready teacher documents via the yowes engine."""
from __future__ import annotations
import sys
from pathlib import Path

YOWES_DIR = Path.home() / "petani-proxy" / "yowes"   # adjust if moved
if str(YOWES_DIR) not in sys.path:
    sys.path.insert(0, str(YOWES_DIR))

from countries.us import USGenerator  # noqa: E402


# K12 city strings look like "Chicago, IL" -> split into town + state.
def _enrich_school(k12_school: dict) -> dict:
    city = (k12_school.get("city") or "").strip()
    town, _, state = city.partition(",")
    town, state = town.strip(), state.strip()
    return {
        "name": k12_school["name"],
        # --- fields yowes templates read ---
        "town": town or "Springfield",
        "state": state or "IL",
        "postcode": k12_school.get("postcode", "62701"),
        "lea": k12_school.get("lea") or f"{town or 'Springfield'} Public Schools",
        "address": k12_school.get("address", "1 School District Plaza"),
        "phone": k12_school.get("phone", "(000) 555-0000"),
        # --- keep the SheerID identity around if needed ---
        "id": k12_school.get("id"),
        "idExtended": k12_school.get("idExtended"),
    }


def generate_doc_png(doc_type: str, first: str, last: str,
                     school: dict, position: str, dob: str) -> bytes:
    """Return PNG bytes for a SheerID teacher-document upload.

    doc_type: 'teacher_id' | 'employment_letter' | 'teaching_license'
    """
    gen = USGenerator()
    if doc_type not in gen.get_document_types():
        raise ValueError(f"unsupported doc_type: {doc_type!r}")
    png = gen.generate_document(doc_type, first, last, _enrich_school(school), position, dob)
    if not png[:8] == b"\x89PNG\r\n\x1a\n":
        raise ValueError("yowes did not return a PNG")
    return png
```

> Note: `select_school()` in the K-12 repo currently **drops** the `city` key
> before returning. To use approach A you must keep `city` (see TODO plan item 2),
> or re-look-up the city from `K12_SCHOOLS` by `id` inside the bridge.

### 4.2 Wiring into `verify()` (the actual replacement)

In `script.py`, replace line ~765:

```python
# Step 1: Generate teacher badge
doc_data = generate_teacher_badge(first_name, last_name, school["name"])
```

with:

```python
# Step 1: Generate teacher document via yowes
doc_data = generate_doc_png(
    "teacher_id",                 # or "employment_letter"
    first_name, last_name,
    school,
    position="Teacher",
    dob=birth_date,               # birth_date is already computed at line ~737
)
```

and add the import at the top of `script.py`:

```python
from yowes_bridge import generate_doc_png   # scripts/ on sys.path, or vendor the module
```

Also update the Step-4 filename so it's descriptive (cosmetic, optional):

```python
"fileName": "teacher_id.png",   # was "teacher_badge.png"
```

Nothing else changes — `doc_size_kb`, `step4_body["files"][0]["fileSize"]`,
`mimeType: "image/png"`, the presigned PUT and `completeDocUpload` all keep
working because `doc_data` is still raw PNG bytes.

### 4.3 Guard the size (optional but recommended)

SheerID can reject very large uploads. Clamp with a size check (yowes can emit
~3800×3300 A4 PNGs):

```python
MAX_DOC_BYTES = 4_000_000
if len(doc_data) > MAX_DOC_BYTES:
    raise ValueError(f"document too large: {len(doc_data)} bytes")
```

If you need smaller files, downscale in the bridge:

```python
from PIL import Image
from io import BytesIO
im = Image.open(BytesIO(png)).convert("RGB")
im.thumbnail((1600, 1600))                 # LANCZOS by default
buf = BytesIO(); im.save(buf, format="PNG", optimize=True)
png = buf.getvalue()
```

### 4.4 Fallback (never regress)

Keep `generate_teacher_badge` as a safety net:

```python
try:
    doc_data = generate_doc_png("teacher_id", first_name, last_name, school,
                                "Teacher", birth_date)
except Exception as e:
    print(f"   [!] yowes bridge failed ({e}) — falling back to badge")
    doc_data = generate_teacher_badge(first_name, last_name, school["name"])
```

---

## 5. How the existing `generate_teacher_doc.py` already does it

`~/petani-proxy/Farm-Acc-ChatGPT-K-12-Teachers/generate_teacher_doc.py` is a
working reference for calling yowes:

```python
YOWES_DIR = Path(__file__).resolve().parent.parent / "yowes"
sys.path.insert(0, str(YOWES_DIR))
from countries.us import USGenerator

gen = USGenerator()
school = gen.search_school(school_name) or gen.random_school()   # full dict!
position = gen.random_position()
for doc_type in ["employment_letter", "teacher_id", "teaching_license"]:
    doc_bytes = gen.generate_document(doc_type, first_name, last_name,
                                      school, position, dob)
    Path(f"{first_name}_{last_name}_{doc_type}.png").write_bytes(doc_bytes)
```

Key takeaway confirmed by this reference (and by testing): **the `school` passed
to `generate_document` must be a full yowes dict** (or an enriched one), exactly
as documented in §3. It also shows the call is thread-safe enough to loop over
doc types.

You can also reach it from the CLI:

```bash
kancahub yowes k12 --first John --last Doe --school "Norton Elementary"
```

---

## 6. TODO patch plan (apply later — do NOT edit `script.py` now)

1. **[new]** Create `~/Auto-FreeCF/scripts/yowes_bridge.py` with
   `generate_doc_png()` + `_enrich_school()` (+ optional downscale + size guard).
2. **[K-12 repo]** `script.py` `select_school()` (line ~343): stop dropping
   `city` — return it in the dict so `_enrich_school` can split `"City, ST"`.
   *(One-line change: add `"city": school["city"]`.)*
3. **[K-12 repo]** `script.py` top: add
   `from yowes_bridge import generate_doc_png` (ensure `scripts/` on `sys.path`,
   or vendor the module next to `script.py`).
4. **[K-12 repo]** `script.py` line ~765: replace the
   `generate_teacher_badge(...)` call with the `generate_doc_png("teacher_id", …)`
   call, wrapped in the try/except fallback from §4.4.
5. **[K-12 repo]** `script.py` line ~959 (optional): rename
   `"fileName": "teacher_badge.png"` → `"teacher_id.png"`.
6. **[verify]** Run `kancahub k12 verify <sheerid-url> --debug` and confirm:
   - Step 1 prints a yowes-sized document (100–400 KB, not ~50 KB);
   - `[DEBUG] Uploading N bytes to S3` / `S3 upload status: 200`;
   - `completeDocUpload` returns a non-error `currentStep`.
7. **[rollback]** If SheerID rejects the PNG, revert item 4 (fallback keeps the
   badge), and try `doc_type="employment_letter"` or the downscale from §4.3.

---

## 7. Quick reference

| Item | Value |
|---|---|
| Function to call | `USGenerator().generate_document(doc_type, first, last, school, position, dob)` |
| Doc types | `teacher_id`, `employment_letter`, `teaching_license` |
| Return | PNG bytes (magic `\x89PNG\r\n\x1a\n`) |
| SheerID init | `POST /rest/v2/verification/{id}/step/docUpload` with `mimeType: image/png`, `fileSize: len(doc_data)` |
| SheerID upload | `PUT <uploadUrl>` raw bytes, `Content-Type: image/png` |
| SheerID finish | `POST /rest/v2/verification/{id}/step/completeDocUpload` |
| School dict must include | `name`, `town`, `state`, `postcode`, `lea`, `address`, `phone` |
| Replaced call site | `script.py` `verify()` line ~765 |
