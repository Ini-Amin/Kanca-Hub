# Refactoring scripts/kancahub.py (4,677 lines) — safe plan

Goal: split the god-file into modules with **zero CLI change**. Safe because
`tests/test_cli_surface.py` pins all 134 commands + their arg destinations.

## The one rule that makes it safe

**12 test files patch names on the `kancahub` module itself**
(`patch.object(kancahub, "run")`, `kancahub.AUTO_FREECF`, `kancahub._choose_egress`,
`kancahub._load_egress`, `kancahub._stop_auto_gateways`, `kancahub.run_with_mobile_retry`,
`kancahub._run_capture`, `kancahub._mobile_rotate_once`, `kancahub._ledger_record`, …).

So extracted command modules MUST NOT do `from kancahub_base import run` (binds at
import → patches stop working). Instead: **`import kancahub` inside the function, then
`kancahub.run(...)`**, and keep `kancahub` re-exporting every symbol.

## Module seams (order = safe extraction order)

1. `kancahub_base.py` — `col/banner/pick_python/run/_gw_get/_http_status/_env_get`,
   egress (`EgressChoice/_choose_egress/_load_egress/_proxy_env/_AUTO_GATEWAYS`),
   `_spawn_background/_stop_background/JobRegistry/SessionGateway`,
   `run_with_mobile_retry/_phone_not_on_mobile/_mobile_rotate_once`,
   `_ledger_record/make_plus_address/BACKGROUND_DIR/SESSION_GUARD_STATE`. (~700 l)
   - `_AUTO_GATEWAYS/_egress_module/_egress_unavailable/_LAST_RUN_OUT` must live in
     exactly ONE module (globals rebind in their defining module).
2. `commands_misc.py` — adb/mobile/warp/region/mail/gmail/yowes/otp/scrape/report/ip-reuse/9router/egress-node
3. `commands_proxy.py`  4. `commands_stack.py`  5. `commands_grok.py`
6. `commands_github.py` (careful: `AUTO_FREECF` patched)  7. `commands_thk.py`
8. `commands_farm.py` (k12 + autofarm)  9. `doctor.py`  10. `menu.py` **last** (has the cycle)

`kancahub.py` keeps: `build_parser`, `build_github_parser` re-export, `dispatch`,
`main`, `beginner_entry`, `menu_entry`, `if __name__`.

## Per-step canary
```
python -c "import sys;sys.path.insert(0,'scripts');import kancahub;kancahub.build_parser()"
python -m unittest tests.test_cli_surface            # 134-command surface
```

## Hazards
- H1 module-namespace patching (above)
- H2 `AUTO_FREECF`/`BACKGROUND_DIR` patched on `kancahub` → read via lazy `kancahub.X`
- H3 parser coupling (`cmd_session`, `run_end_to_end_flow` call `build_parser`/`dispatch`) → keep in `kancahub.py`, lazy-import
- H4 module globals (`_AUTO_GATEWAYS` …) live in one module only
- H6 `SCRIPTS_DIR = Path(__file__).parent` per module
- H7 no import-time work except `colorama.init()` → put in base
- H8 `CAMOUFOX_PY` vs `CAMOUFOX_VENV_PY` both exist — keep both

## Status
- [x] Regression surface test (`tests/test_cli_surface.py` + fixture)
- [ ] kancahub_base.py
- [ ] commands_* extraction (steps 2–9)
- [ ] menu.py
