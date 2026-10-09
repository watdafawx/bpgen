"""Starter base tiers, no game: tier 1 as a main bus fed plates at its head, with the stone-brick C; tier 2 added from
tier 1's blueprint (what it doesn't make yet), beside it, on the same C. Writes both blueprints to
run/tier1.txt and run/tier2.txt. python mod/test/tiers_test.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import base, extend, planner, tiers  # noqa: E402
from bpgen.service import Service  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
s = Service("vanilla")
planner.configure(s.data)
common = {"mode": "base", "layout": "bus", "plates": True, "mall": False, "belt": "transport-belt"}
t1 = s.plan(dict(common, spm=10))
routed = t1["sections"][0]
assert routed["kind"] == "routed" and routed["result"]["summary"]["layout"] == "bus", t1["summary"].get("route_note")
bp1 = routed["result"]["blueprint"]
ents1, c1, _ = tiers.held_base(bp1)
assert c1, "tier 1 has no C"
b1 = base._decode(bp1)["blueprint"]
assert b1.get("absolute-snapping") and b1["snap-to-grid"]["x"] == tiers.GRID
smelted = [e for e in ents1 if e.get("recipe") in ("iron-plate", "copper-plate") or "furnace" in e["name"]]
assert not smelted, f"tier 1 smelts: {smelted[:2]}"
print("tier 1:", len(ents1), "entities, C at", c1, "bring in", t1["summary"]["bring_in"])

t2 = s.plan(dict(common, spm=30, plates=False, add_to=t1["blueprint"]))  # (the whole book in hand, as in game)
ents2, c2, _ = tiers.held_base(t2["blueprint"])
assert c2 == c1, f"tier 2's C {c2} is not tier 1's {c1}"
old = extend.plan_tiles([s.decorate(e) for e in ents1])
new = extend.plan_tiles([s.decorate(e) for e in ents2])
assert not old & new, f"tier 2 overlaps tier 1 on {len(old & new)} tiles"
assert set(t2["summary"]["bring_in"]) <= {"iron-plate", "copper-plate", "stone-brick"}, t2["summary"]["bring_in"]
print("tier 2:", len(ents2), "entities;", t2["summary"]["notes"][0])
(ROOT / "run").mkdir(exist_ok=True)
(ROOT / "run" / "tier1.txt").write_text(bp1, encoding="utf-8")
(ROOT / "run" / "tier2.txt").write_text(t2["blueprint"], encoding="utf-8")
print("ok")
