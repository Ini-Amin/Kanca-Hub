"""commands_region — region profiles command for kancahub."""
from __future__ import annotations


def cmd_region(a) -> int:
    import kancahub
    py = kancahub.pick_python()
    reg = kancahub.AUTO_FREECF / "scripts" / "regions.py"
    if not reg.exists():
        print(kancahub.col("red", "✗ regions.py not found"))
        return 1
    if a.region_cmd in (None, "current"):
        return kancahub.run([py, str(reg), "current"])
    if a.region_cmd == "list":
        return kancahub.run([py, str(reg), "list"])
    if a.region_cmd == "set":
        return kancahub.run([py, str(reg), "set", a.name])
    if a.region_cmd == "clear":
        return kancahub.run([py, str(reg), "clear"])
    if a.region_cmd == "show":
        return kancahub.run([py, str(reg), "show", a.name])
    print(kancahub.col("red", "✗ unknown region command"))
    return 1
