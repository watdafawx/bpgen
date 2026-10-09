"""A plates-fed starter base fitted to a bus design's lane ends, no game: the made-up patches of
harness/busdesign_check.py planned as a bus (each direction), then the base: belts from the lanes it needs to its
inputs, starting right where those lanes end, nothing on top of the bus design. Writes run/fit-<direction>.json
(both plans' entities) for harness/fit_bus_run.py. python mod/test/fit_bus_test.py [direction ...]"""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import base, busdesign, extend, planner, tiers  # noqa: E402
from bpgen.service import Service  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PATCHES = {"iron-ore": (100, 139, -60, -11), "copper-ore": (100, 129, 20, 49), "coal": (160, 179, -20, -6)}
s = Service("vanilla")
planner.configure(s.data)
last = Path(tempfile.mkdtemp()) / "bus_design.json"
busdesign._last_file = lambda: last  # (not the player's own)
for direction in sys.argv[1:] or ["north", "east", "south", "west"]:
    runs = {n: {str(y): [[x1, x2]] for y in range(y1, y2 + 1)} for n, (x1, x2, y1, y2) in PATCHES.items()}
    snap = {"area": [-300, -300, 300, 300], "entities": [], "obstacles": [], "water": {}, "resources": runs}
    bus = s.plan({"mode": "busdesign", "snapshot": snap, "direction": direction, "origin": {"x": 0, "y": 0},
                  "patches": [[x1 - 5, y1 - 5, x2 + 6, y2 + 6] for x1, x2, y1, y2 in PATCHES.values()],
                  "belt": "transport-belt", "furnace": "electric-furnace", "length": 110})
    busdesign.save_last(bus, {"direction": direction})
    out = s.plan({"mode": "base", "layout": "bus", "plates": True, "fit_bus": True, "mall": False,
                  "belt": "transport-belt", "spm": 10})
    notes = out["summary"]["notes"]
    if direction == "east":  # (this bus flows into its own ore belts: the base must say so, not land on them)
        assert not out.get("absolute") and any("in the way" in n for n in notes), notes
        print(direction, "ok: refused,", notes[0])
        continue
    assert out.get("absolute"), f"{direction}: not fitted: {notes}"
    if direction == "north":  # (a pond just past the bus's end: refused, not pasted half into the water)
        ex, ey = bus["lanes"][0]["end"]
        pond = {"area": [-300, -300, 300, 300], "entities": [], "water": {str(ey - 8): [[ex - 30, ex + 30]]}}
        wet = s.plan({"mode": "base", "layout": "bus", "plates": True, "fit_bus": True, "mall": False,
                      "belt": "transport-belt", "spm": 10, "snapshot": pond})
        assert not wet.get("absolute") and any("in the way" in n for n in wet["summary"]["notes"]), \
            wet["summary"]["notes"]
    ents = out["sections"][0]["result"]["entities"]
    dv = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}[direction]
    starts = {(ln["end"][0] + dv[0], ln["end"][1] + dv[1]): ln["item"] for ln in bus["lanes"]}
    at = {(int(e["position"]["x"] // 1), int(e["position"]["y"] // 1)): e for e in ents}
    used = {t: it for t, it in starts.items() if t in at}
    assert used, f"{direction}: no adapter belt starts where a lane ends"
    for t in used:
        assert at[t]["name"] in ("transport-belt", "underground-belt"), (direction, t, at[t]["name"])
    clash = extend.plan_tiles(ents) & extend.plan_tiles(bus["entities"])
    assert not clash, f"{direction}: the base sits on the bus design at {sorted(clash)[:4]}"
    ents_bp, corner, _ = tiers.held_base(out["sections"][0]["result"]["blueprint"])
    assert corner == (0, 0), corner
    # the next tier, on the lanes tier 1 passes on: beside it, clear of it and the bus, its belts where those end
    passed = tiers.read_fitted(base._decode(tiers.held_base(out["blueprint"])[2])["blueprint"]["description"])
    assert passed and passed["lanes"], f"{direction}: tier 1 passes no lanes on"
    t2 = s.plan({"mode": "base", "plates": True, "spm": 30, "mall": False, "belt": "transport-belt",
                 "add_to": out["blueprint"]})
    assert t2.get("absolute"), f"{direction}: tier 2 not fitted: {t2['summary'].get('notes')}"
    ents2 = t2["entities"]
    clash2 = extend.plan_tiles(ents2) & (extend.plan_tiles(ents) | extend.plan_tiles(bus["entities"]))
    assert not clash2, f"{direction}: tier 2 sits on tier 1 or the bus at {sorted(clash2)[:4]}"
    at2 = {(int(e["position"]["x"] // 1), int(e["position"]["y"] // 1)) for e in ents2}
    fed = [ln for ln in passed["lanes"] if (ln["end"][0] + dv[0], ln["end"][1] + dv[1]) in at2]
    assert fed, f"{direction}: tier 2 takes none of the lanes tier 1 passes on"
    print(direction, "ok:", len(used), "lanes used of", len(starts), "| tier 2 on", len(fed), "of",
          len(passed["lanes"]), "passed-on lanes |", " / ".join(notes + t2["summary"]["notes"][1:2]))
    (ROOT / "run").mkdir(exist_ok=True)
    (ROOT / "run" / f"fit-{direction}.json").write_text(json.dumps(
        {"bus": bus["entities"], "base": ents, "lanes": bus["lanes"], "patches": PATCHES,
         "book": out["blueprint"], "box": out["absolute"]["box"], "base2": ents2}), encoding="utf-8")
print("ok")
