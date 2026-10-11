"""Move a section (the window's Extend tab, "Pick the section"), no game: two assembling machines making gears off a
belt that comes in from the main bus, their gears going out on a belt; moved to where the player stands, the old
one cleared, its belt in and its belt out run to the base again, its own settings (recipes, a chest limit) and a
circuit wire kept. python mod/test/move_test.py"""
import base64
import json
import sys
import zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import ingame, planner  # noqa: E402
from bpgen.service import Service  # noqa: E402

s = Service("vanilla")
planner.configure(s.data)
ingame._service = s
bus = ["iron-plate", "iron-plate", "copper-plate", "coal"]
ents = [{"name": "transport-belt", "type": "transport-belt", "position": {"x": 20 + 2 * i + 0.5, "y": y + 0.5},
         "direction": 8, "lanes": [it, it]} for i, it in enumerate(bus) for y in range(0, 120)]
# the section (x 40..50, y 30..40): iron comes in from the west along y = 33, gears leave east along y = 37
AREA = [40, 30, 50, 40]
section = [
    {"name": "assembling-machine-2", "position": {"x": 43.5, "y": 35.5}, "recipe": "iron-gear-wheel"},
    {"name": "assembling-machine-2", "position": {"x": 47.5, "y": 35.5}, "recipe": "iron-gear-wheel"},
    {"name": "inserter", "position": {"x": 43.5, "y": 33.5}, "direction": 0},
    {"name": "inserter", "position": {"x": 47.5, "y": 33.5}, "direction": 0},
    {"name": "inserter", "position": {"x": 43.5, "y": 37.5}, "direction": 0, "wire_id": "a",
     "control_behavior": {"circuit_enabled": True, "circuit_condition": {
         "first_signal": {"name": "iron-gear-wheel", "type": "item"}, "constant": 50, "comparator": "<"}}},
    {"name": "wooden-chest", "position": {"x": 47.5, "y": 38.5}, "bar": 4, "wire_id": "b"},
    {"name": "medium-electric-pole", "position": {"x": 45.5, "y": 33.5}},
]
section[4]["circuit_to"] = ["b"]
for x in range(40, 50):
    section.append({"name": "transport-belt", "position": {"x": x + 0.5, "y": 32.5}, "direction": 4})
    section.append({"name": "transport-belt", "position": {"x": x + 0.5, "y": 37.5 + 0 * x}, "direction": 4})
section = [e for e in section if not (e["name"] == "transport-belt" and e["position"]["y"] == 37.5
                                       and 42 <= e["position"]["x"] <= 44)]  # (the wired inserter's spot)
for e in section:
    snap_e = dict(e, type=s.decorate(e)["type"])
    if e["name"] == "transport-belt":
        snap_e["lanes"] = ["iron-plate", "iron-plate"] if e["position"]["y"] == 32.5 else ["iron-gear-wheel", False]
    ents.append(snap_e)
for x in range(28, 40):  # (iron in from the bus's side, gears out to the east)
    ents.append({"name": "transport-belt", "type": "transport-belt", "position": {"x": x + 0.5, "y": 32.5},
                 "direction": 4, "lanes": ["iron-plate", "iron-plate"]})
for x in range(50, 56):
    ents.append({"name": "transport-belt", "type": "transport-belt", "position": {"x": x + 0.5, "y": 37.5},
                 "direction": 4, "lanes": ["iron-gear-wheel", False]})
snap = {"area": [0, 0, 160, 140], "entities": ents}
cx, cy = 45, 35  # (the blueprint's positions are round its middle, as the game writes them)
bp = planner.blueprint_string([dict(e, position={"x": e["position"]["x"] - cx, "y": e["position"]["y"] - cy})
                               for e in section], "section")
req = {"params": {"mode": "move", "move": {"area": AREA, "blueprint": bp, "offset": [cx, cy]},
                  "snapshot": snap, "seed": {"x": 60, "y": 90}}}
out = json.loads(ingame.move(json.dumps(req)))
assert "error" not in out, out
print("\n".join(out["notes"]))
a = out["absolute"]
moved = json.loads(zlib.decompress(base64.b64decode(out["blueprint"][1:])))["blueprint"]
names = [e["name"] for e in moved["entities"]]
assert names.count("assembling-machine-2") == 2 and sum(1 for e in moved["entities"] if e.get("recipe")) == 2
assert any(e.get("bar") == 4 for e in moved["entities"]), "the chest limit kept"
assert any(w[1] == 1 and w[3] == 1 for w in moved["wires"]), "the circuit wire kept"
assert len(a["clear"]) >= len(section), "the old section marked for removal"
assert a["taps"] >= 1 and not a["inputs"], f"its iron belt run to the bus: {a}"
bx, by, bw, bh = a["box"][:4]
assert abs(bx + bw / 2 - 60) < 25 and abs(by + bh / 2 - 90) < 25, f"not where the player stands: {a['box']}"
print("ok")
