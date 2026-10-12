"""commands_farm — warp and yowes commands for kancahub."""
from __future__ import annotations

from pathlib import Path
import os
import sys



def cmd_warp(a) -> int:
    import kancahub
    py = kancahub.pick_python()
    wm = kancahub.AUTO_FREECF / "scripts" / "warp_manager.py"
    if not wm.exists():
        print(kancahub.col("red", f"✗ warp_manager.py not found at {wm}"))
        return 1
    sub = a.warp_cmd or "status"
    if sub == "up":
        return kancahub.run([py, str(wm), "up"])
    if sub == "down":
        return kancahub.run([py, str(wm), "down"])
    if sub == "gen":
        return kancahub.run([py, str(wm), "gen"])
    # status
    return kancahub.run([py, str(wm), "status"])


def cmd_yowes(a) -> int:
    import kancahub
    py = kancahub.pick_python()
    if not kancahub.YOWES.exists():
        print(kancahub.col("red", f"✗ Yowes not found at {kancahub.YOWES}"))
        return 1
    sub = a.yowes_cmd

    if sub == "list":
        script = (
            "import sys; sys.path.insert(0,'.');"
            "from countries import list_countries, get_country;"
            "print('Countries & document types:');"
            "[print(f'  {c:14s} {get_country(c)().get_country_name():14s} -> ' + ', '.join(get_country(c)().get_document_types())) for c in list_countries()]"
        )
        return kancahub.run([py, "-c", script], cwd=kancahub.YOWES)

    if sub == "schools":
        script = (
            "import sys; sys.path.insert(0,'.');"
            "from countries import get_country;"
            f"gen=get_country('{a.country}')();"
            "print(f'{len(gen.schools)} schools for', gen.get_country_name());"
            "[print('  ', s['name']) for s in gen.schools]"
        )
        return kancahub.run([py, "-c", script], cwd=kancahub.YOWES)

    if sub == "make":
        types = a.types.split(",") if a.types else None
        script = f"""
import sys; sys.path.insert(0,'.')
from mcp_server import generate_documents
res = generate_documents(
    country={a.country!r}, first_name={a.first!r}, last_name={a.last!r},
    school_name={a.school!r}, position={a.position!r}, date_of_birth={a.dob!r},
    gender={a.gender!r}, document_types={types!r}, output_dir={a.out!r},
)
print('✓ generated', res['count'], 'document(s) ->', res['output_dir'])
for f in res['files']: print('   ', f)
"""
        return kancahub.run([py, "-c", script], cwd=kancahub.YOWES)

    if sub == "gui":
        gui = kancahub.YOWES / "main_gui.py"
        return kancahub.run([py, str(gui)], cwd=kancahub.YOWES)

    if sub == "mcp":
        return kancahub.run([py, "mcp_server.py"], cwd=kancahub.YOWES)

    print(kancahub.col("red", "✗ unknown yowes command"))
    return 1
