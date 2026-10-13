"""Line's More lines (the window's Line tab), no game: the line and each of "lines" planned as its own line, as if
picked alone, in one blueprint book - a print each, labelled, picked under the preview; Place by my base puts the one
picked next to the base.
python mod/test/lines_test.py"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import base, ingame, planner  # noqa: E402
from bpgen.service import Service  # noqa: E402

s = Service("vanilla")
planner.configure(s.data)
ingame._service = s


def line(recipe):
    return {"recipe": recipe, "machine": "assembling-machine-2", "machine_quality": "normal", "modules": [],
            "recipe_productivity": 0}


req = dict(line("iron-gear-wheel"), belts=["transport-belt"], inserters=["inserter", "long-handed-inserter"],
           belt="transport-belt", rate_per_min=60, params={},
           lines=[line("copper-cable"), line("pipe"), line("copper-cable")])
out = json.loads(ingame.plan(json.dumps(req)))
assert "error" not in out, out
book = base._decode(out["blueprint"])["blueprint_book"]
labels = [p["blueprint"]["label"] for p in book["blueprints"]]
assert out["sections"] == labels and len(labels) == 4, (out["sections"], labels)
assert labels[0].startswith("iron-gear-wheel") and labels[1].startswith("copper-cable") \
    and labels[2].startswith("pipe") and labels[3].endswith("(2)"), labels  # (a recipe twice: told apart)
for p in book["blueprints"]:  # (each print its own line: one recipe in it)
    recipes = {e.get("recipe") for e in p["blueprint"]["entities"] if e.get("recipe")}
    assert len(recipes) == 1, (p["blueprint"]["label"], recipes)
assert out["test"] is None and out["mode"] == "line", out

# a single line still comes out as one print, and Place by my base takes the print picked
one = json.loads(ingame.plan(json.dumps(dict(req, lines=None))))
assert "blueprint" in base._decode(one["blueprint"]) and not one.get("sections"), one.get("sections")
json.loads(ingame.plan(json.dumps(req)))
snap = {"area": [-60, -60, 60, 60], "entities": []}
placed = json.loads(ingame.place(json.dumps({"section": labels[2], "bus": "off", "seed": {"x": 0, "y": 0},
                                             "snapshot": snap})))
assert "error" not in placed, placed
assert placed["label"].startswith("bpgen: pipe"), placed["label"]
recipes = {e.get("recipe") for e in base._decode(placed["blueprint"])["blueprint"]["entities"] if e.get("recipe")}
assert recipes == {"pipe"}, recipes
print(f"ok: {len(labels)} lines in one book ({', '.join(labels)}), the picked one placed by the base")
