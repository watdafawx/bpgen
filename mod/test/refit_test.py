"""Refit an area (the window's Mall tab, "Pick the area"), no game: a messy mall beside a main bus - machines all
over the place, belts wandering, poles - is rebuilt as a compact grid mall. Its entities are cleared but for the
bus's belts and a furnace the mall doesn't make; the mall lands in the cleared ground, with taps on the bus.
python mod/test/refit_test.py"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import ingame, planner  # noqa: E402
from bpgen.service import Service  # noqa: E402

s = Service("vanilla")
planner.configure(s.data)
ingame._service = s
bus_items = ["iron-plate", "iron-plate", "copper-plate", "iron-gear-wheel"]
ents = [{"name": "transport-belt", "type": "transport-belt", "position": {"x": 20 + 2 * i + 0.5, "y": y + 0.5},
         "direction": 8, "lanes": [it, it]} for i, it in enumerate(bus_items) for y in range(0, 120)]
# the mess: east of the bus, machines scattered with gaps, belts snaking between them, a furnace among them
AREA = [30, 20, 90, 80]
mess = [("inserter", 34, 24), ("transport-belt", 50, 30), ("inserter", 40, 50), ("electronic-circuit", 60, 40),
        ("copper-cable", 70, 60), ("electronic-circuit", 80, 26), ("pipe", 46, 70)]
for i, (r, x, y) in enumerate(mess):
    ents.append({"name": "assembling-machine-2", "type": "assembling-machine", "position": {"x": x + 0.5, "y": y + 0.5},
                 "direction": 0, "recipe": r})
ents.append({"name": "stone-furnace", "type": "furnace", "position": {"x": 86, "y": 76}, "direction": 0,
             "recipe": "iron-plate"})
for x in range(30, 88):  # (a belt wandering through)
    ents.append({"name": "transport-belt", "type": "transport-belt", "position": {"x": x + 0.5, "y": 45.5},
                 "direction": 4, "lanes": ["iron-plate", False]})
for x in range(32, 90, 7):
    ents.append({"name": "small-electric-pole", "type": "electric-pole", "position": {"x": x + 0.5, "y": 35.5},
                 "direction": 0})
outside = {"name": "assembling-machine-2", "type": "assembling-machine", "position": {"x": 100.5, "y": 50.5},
           "direction": 0, "recipe": "pipe"}
ents.append(outside)
snap = {"area": [0, 0, 140, 130], "entities": ents}
products = list(dict.fromkeys(r for r, _, _ in mess))


def plan(add):
    req = {"belts": ["transport-belt"], "inserters": ["inserter", "long-handed-inserter", "fast-inserter"],
           "params": {"mode": "mall", "products": products, "machine": "assembling-machine-2",
                      "belt": "transport-belt", "snapshot": json.loads(json.dumps(snap)),
                      "refit": {"area": AREA, "add": add}}}
    out = json.loads(ingame.plan(json.dumps(req)))
    assert "error" not in out, out
    return out


out = plan(False)
notes = out.get("notes") or []
print("\n".join(notes))
a = out["absolute"]
clear = {(c["name"], c["position"]["x"], c["position"]["y"]) for c in a["clear"]}
assert sum(1 for c in clear if c[0] == "assembling-machine-2") == len(mess), clear
assert not any(c[0] == "stone-furnace" for c in clear), "the furnace isn't refit: kept"
assert ("assembling-machine-2", 100.5, 50.5) not in clear, "outside the area: kept"
assert not any(c[0] == "transport-belt" and 20 <= c[1] <= 28 for c in clear), "bus belts kept"
assert sum(1 for c in clear if c[0] == "transport-belt") == 58, "the wandering belt goes"
assert a["taps"] >= 1, "fed from the bus"
bx, by, bw, bh = a["box"][:4]
cx, cy = bx + bw / 2, by + bh / 2
assert 20 <= cx <= 100 and 10 <= cy <= 90, f"not in the area's place: {a['box']}"
assert any("the area had 7" in n for n in notes), notes
more = plan(True)
print("with Add machines:", [n for n in more["notes"] if "the area had" in n])
print("ok")
