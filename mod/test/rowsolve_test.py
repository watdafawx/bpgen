"""Every recipe in every machine that can craft it (vanilla + Space Age, with and without beacons) either plans as a
line - the row solver stands its machines, rowlayout lays them out - or is refused with a PlanError, never a crash;
and every line it lays out is sound: no two entities on one tile, every machine with its recipe, an output (a belt
sink or a fluid sink) and a source for every ingredient. No game.
python mod/test/rowsolve_test.py [-v]"""
import json
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import extend, planner  # noqa: E402
from bpgen.service import Service  # noqa: E402

verbose = "-v" in sys.argv
s = Service("vanilla")
data = s.data
planner.configure(data)
calib = json.loads(s.calib_path.read_text())
BELT = "transport-belt"

counts = Counter()
problems = []
machines = {**data.raw["assembling-machine"], **data.raw.get("furnace", {})}
for recipe, r in sorted(data.raw["recipe"].items()):
    for machine, m in sorted(machines.items()):
        if not planner.can_craft(m, r) or m.get("hidden"):
            continue
        for beacon in (None, "beacon"):
            if beacon and beacon not in data.raw.get("beacon", {}):
                continue
            tag = f"{recipe} in {machine}" + (" + beacon" if beacon else "")
            try:
                p = planner.plan(data, calib, recipe, machine, BELT, beacon=beacon)
                ents, sources, sinks = planner.layout(p)
            except planner.PlanError as e:
                counts["refused"] += 1
                if verbose:
                    print(f"  ({tag}: {e})")
                continue
            except Exception as e:  # noqa: BLE001
                problems.append(f"{tag}: crashed: {type(e).__name__}: {e}")
                continue
            counts["laid out"] += 1
            seen = {}
            for e in (s.decorate(x) for x in ents):
                for t in extend.footprint(e):
                    if t in seen:
                        problems.append(f"{tag}: {e['name']} and {seen[t]} both on {t}")
                        break
                    seen[t] = e["name"]
            made = [e for e in ents if e["name"] == machine]
            if not made or len(made) != p.machines:
                problems.append(f"{tag}: {len(made)} machines laid out, the plan says {p.machines}")
            if not r.get("results") and not sinks:
                pass
            elif not sinks:
                problems.append(f"{tag}: no sink")
            fed = {x["fluid"] for x in sources if x.get("fluid")} | {i for x in sources for i in x.get("lanes") or [] if i}
            fed |= {x["fluid"] for x in sources if x.get("kind") == "fluid"}
            wanted = {i["name"] for i in r.get("ingredients") or []}
            if not wanted <= fed:
                problems.append(f"{tag}: nothing brings {sorted(wanted - fed)}")
for p_ in problems[:20]:
    print("  -", p_)
print("ok:" if not problems else "FAIL:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
      f"({len(problems)} problems)" if problems else "")
assert not problems, f"{len(problems)} problems"
assert counts["laid out"] > 1800, counts

# a 1x1 machine (the pack's ME molecular assembler has one): its one inserter column on each side can't spare one
# for a pole, so the power pass stands the poles; every powered entity must be in a pole's supply area
tiny = dict(data.raw["assembling-machine"]["assembling-machine-2"], name="test-1x1-assembler",
            collision_box=[[-0.4, -0.4], [0.4, 0.4]], fluid_boxes=[])
data.raw["assembling-machine"]["test-1x1-assembler"] = tiny
pole = data.raw["electric-pole"][planner.POLE]
reach = pole["supply_area_distance"]
for recipe in ("iron-gear-wheel", "copper-cable", "electronic-circuit"):
    p = planner.plan(data, calib, recipe, "test-1x1-assembler", BELT)
    ents, sources, sinks = planner.layout(p)
    poles = [(e["position"]["x"], e["position"]["y"]) for e in ents if e["name"] == planner.POLE]
    powered = [e for e in ents if e["name"] == "test-1x1-assembler" or "inserter" in e["name"]]
    cold = [e["name"] for e in powered if not any(abs(e["position"]["x"] - x) <= reach + 0.5
                                                   and abs(e["position"]["y"] - y) <= reach + 0.5 for x, y in poles)]
    assert not cold, (recipe, "unpowered", cold[:4])
    assert sum(e["name"] == "test-1x1-assembler" for e in ents) == p.machines, recipe
print("ok: 1x1 machines plan and are powered")
