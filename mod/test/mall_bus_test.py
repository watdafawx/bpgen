"""A grid mall fed from the main bus (the window's mall "Fed by: my main bus"), no game: a bus of iron and copper
plates, gears and circuits; the mall takes gears and circuits from it instead of making them, makes the rest (cable
is no longer needed, inserters are), and lands beside the bus with taps. python mod/test/mall_bus_test.py"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import extend, ingame, planner  # noqa: E402
from bpgen.service import Service  # noqa: E402

s = Service("vanilla")
planner.configure(s.data)
ingame._service = s
bus_items = ["iron-plate", "iron-plate", "copper-plate", "iron-gear-wheel", "electronic-circuit", "steel-plate"]
ents = [{"name": "transport-belt", "type": "transport-belt", "position": {"x": 20 + 2 * i + 0.5, "y": y + 0.5},
         "direction": 8, "lanes": [it, it]} for i, it in enumerate(bus_items) for y in range(0, 160)]
snap = {"area": [0, 0, 160, 170], "entities": ents}
products = ["inserter", "long-handed-inserter", "fast-inserter", "transport-belt", "underground-belt", "splitter",
            "electric-mining-drill", "assembling-machine-1", "pipe", "pipe-to-ground"]
req = {"belts": ["transport-belt"], "inserters": ["inserter", "long-handed-inserter", "fast-inserter"],
       "params": {"mode": "mall", "layout": "grid", "products": products, "machine": "assembling-machine-2",
                  "belt": "transport-belt", "bus_feed": True, "snapshot": snap, "origin": {"x": 60, "y": 60}}}
out = json.loads(ingame.plan(json.dumps(req)))
assert "error" not in out, out
notes = out.get("notes") or []
print("\n".join(notes))
assert out.get("absolute") and out["absolute"]["taps"] >= 1, f"not placed beside the bus with taps: {out.get('absolute')}"
made = notes[0] if notes else ""
assert any("taken from it" in n for n in notes), notes
assert not any("made here:" in n and ("iron-gear-wheel" in n.split("made here:")[1] or "electronic-circuit" in
                                       n.split("made here:")[1]) for n in notes), "gears/circuits made though on the bus"
print("ok")
