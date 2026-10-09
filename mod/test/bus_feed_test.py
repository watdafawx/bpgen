"""A production line fed from the main bus (the window's "Feed from my bus"), no game: a 4-lane bus (iron, copper,
stone, coal); electronic circuits take iron plates from it, make their copper cable in the blueprint, and land
beside the bus with taps on its lanes. python mod/test/bus_feed_test.py"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import ingame, planner  # noqa: E402
from bpgen.service import Service  # noqa: E402

s = Service("vanilla")
planner.configure(s.data)
ingame._service = s
bus_items = ["copper-plate", "iron-plate", "stone", "coal"]
ents = [{"name": "transport-belt", "type": "transport-belt", "position": {"x": 20 + 2 * i + 0.5, "y": y + 0.5},
         "direction": 8, "lanes": [it, it]} for i, it in enumerate(bus_items) for y in range(0, 100)]
snap = {"area": [0, 0, 120, 120], "entities": ents}
req = {"recipe": "electronic-circuit", "machine": "assembling-machine-2", "belts": ["transport-belt"],
       "inserters": ["inserter", "long-handed-inserter", "fast-inserter"], "rate_per_min": 120,
       "params": {"bus_feed": True, "snapshot": snap, "origin": {"x": 40, "y": 50}}}
out = json.loads(ingame.plan(json.dumps(req)))
assert "error" not in out, out
notes = out.get("notes") or (out.get("summary") or {}).get("notes") or []
print("\n".join(notes))
print("absolute:", out.get("absolute"))
assert out.get("absolute"), "not placed beside the bus"
assert any("from your bus" in n and "iron-plate" in n for n in notes), "iron should come from the bus"
assert any("made in the blueprint" in n and "copper-cable" in n for n in notes), "copper cable should be made here"
assert out["absolute"]["taps"] >= 1, "no taps on the bus"
# no bus around: a plain line, saying so
req["params"]["snapshot"] = {"area": [0, 0, 120, 120], "entities": []}
out = json.loads(ingame.plan(json.dumps(req)))
assert "error" not in out and not out.get("absolute"), out
print("ok")
