"""The belt-loop mall (bpgen/mall_sushi.py), no game: the starter mall plus products needing lubricant and water
planned as one; every product placed, nothing on top of anything else, every circuit wire within reach.
harness\\grid_mall_run.py --sushi runs it in a game (--pack: with your own mod pack).
python mod/test/mall_loop_test.py"""
import base64
import json
import math
import sys
import zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import base, extend, planner  # noqa: E402
from bpgen.service import Service  # noqa: E402

s = Service("vanilla")
planner.configure(s.data)
prods = base.mall_products(s.data, "assembling-machine-2") + ["express-transport-belt", "concrete"]
o = s.plan({"mode": "mall", "layout": "sushi", "products": prods, "machine": "assembling-machine-2",
            "belt": "transport-belt"})
print("\n".join(o["summary"]["notes"]))
assert sorted(o["summary"]["products"]) == sorted(set(prods)), set(prods) - set(o["summary"]["products"])
taken = {}
for e in o["entities"]:
    for t in extend.footprint(e):
        assert t not in taken, f"{e['name']} on {taken[t]} at {t}"
        taken[t] = e["name"]
bp = json.loads(zlib.decompress(base64.b64decode(o["blueprint"][1:])))["blueprint"]
at = {e["entity_number"]: e["position"] for e in bp["entities"]}
for a, _, b, _ in bp["wires"]:
    assert math.dist((at[a]["x"], at[a]["y"]), (at[b]["x"], at[b]["y"])) <= 9, "a wire out of reach"
assert any(src["kind"] == "fluid" for src in o["sources"]), "the fluid products' pipes"
assert all(len(set(src["lanes"])) == 1 for src in o["sources"] if src["kind"] == "belt"), "an item an input"
chests = sum(1 for e in o["entities"] if e.get("bar"))
assert chests == len(o["summary"]["products"]), f"{chests} chests for {len(o['summary']['products'])} products"
print("ok")
