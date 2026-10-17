"""A line's layouts (layouts.py, the window's layout picker), no game: lines with ingredients made in the blueprint are
planned as the window asks (Service.request) and every layout offered is checked:
  - its print decodes, no two entities share a tile, and every machine has an inserter or pipe beside it;
  - each input the player brings (sources) and each output (sinks) sits on a belt or pipe of the print;
  - no two layouts are the same, the plan's own comes first, the place state has one entry per layout and picking
    one (ingame._pick_layout) makes it the plan to place;
  - the plan's own layout is never larger than before layouts existed (AREA_BEFORE: bpgen 0.28.3's numbers).
python mod/test/layouts_test.py [NAME ...]"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from bpgen import base, ingame, planner  # noqa: E402
from bpgen.chain import _sizer, _tiles  # noqa: E402
from bpgen.service import Service  # noqa: E402

A2 = "assembling-machine-2"
BASE = {"machine": A2, "machine_quality": "normal", "modules": [], "recipe_productivity": 0,
        "belts": ["transport-belt"], "inserters": ["inserter", "long-handed-inserter", "fast-inserter"]}


def mk(**items):
    return {"make": {k.replace("_", "-"): {"recipe": r, "machine": m} for k, (r, m) in items.items()}}


REQUESTS = {
    "circuit-cable": dict(BASE, recipe="electronic-circuit", rate_per_min=120,
                          params=mk(copper_cable=("copper-cable", A2))),
    "adv-circuit-cable-plastic": dict(BASE, recipe="advanced-circuit", rate_per_min=30, params=mk(
        copper_cable=("copper-cable", A2), plastic_bar=("plastic-bar", "chemical-plant"))),
    "engine-gear-pipe": dict(BASE, recipe="engine-unit", rate_per_min=30, params=mk(
        iron_gear_wheel=("iron-gear-wheel", A2), pipe=("pipe", A2))),
    "plastic-petroleum": dict(BASE, recipe="plastic-bar", machine="chemical-plant", rate_per_min=60, params=mk(
        petroleum_gas=("basic-oil-processing", "oil-refinery"))),
    "inserter-gear-circuit": dict(BASE, recipe="inserter", rate_per_min=30, params=mk(
        iron_gear_wheel=("iron-gear-wheel", A2), electronic_circuit=("electronic-circuit", A2))),
    "logistic-inserter-belt": dict(BASE, recipe="logistic-science-pack", rate_per_min=30, params=mk(
        inserter=("inserter", A2), transport_belt=("transport-belt", A2))),
    "adv-circuit-circuit-cable": dict(BASE, recipe="advanced-circuit", rate_per_min=30, params=mk(
        copper_cable=("copper-cable", A2), electronic_circuit=("electronic-circuit", A2))),
    "red-science-gear": dict(BASE, recipe="automation-science-pack", rate_per_min=60, params=mk(
        iron_gear_wheel=("iron-gear-wheel", A2))),
}
# the print's area at bpgen 0.28.3, before layouts (direct / chain / composed as it laid them out then)
AREA_BEFORE = {"circuit-cable": 238, "adv-circuit-cable-plastic": 1184, "engine-gear-pipe": 1225,
               "plastic-petroleum": 665, "inserter-gear-circuit": 957, "logistic-inserter-belt": 1088,
               "adv-circuit-circuit-cable": 1121, "red-science-gear": 578}


def first_print(bp_string):
    bp = base._decode(bp_string)
    if "blueprint_book" in bp:
        return bp["blueprint_book"]["blueprints"][0]["blueprint"]
    return bp["blueprint"]


def check_print(data, size_of, ents):
    """-> problems with a print's entities"""
    bad = []
    at = {}
    for e in ents:
        for t in _tiles(e, size_of):
            if t in at:
                bad.append(f"{e['name']} and {at[t]['name']} on {t}")
            at[t] = e
    for e in ents:
        if not e.get("recipe"):
            continue
        near = set()
        for (x, y) in _tiles(e, size_of):
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    o = at.get((x + dx, y + dy))
                    if o is not None and o is not e:
                        near.add(o["name"])
        if not any("inserter" in n or n in (planner.PIPE, planner.UNDERGROUND) for n in near):
            bad.append(f"{e['name']} ({e['recipe']}) at {e['position']} has nothing feeding it")
    return bad[:5]


def check_ends(data, size_of, ents, ends, what):
    at = {}
    for e in ents:
        for t in _tiles(e, size_of):
            at[t] = e["name"]
    bad = []
    for s in ends or []:
        t = (math.floor(s["position"]["x"]), math.floor(s["position"]["y"]))
        name = at.get(t, "")
        if not any(k in name for k in ("belt", "splitter", "pipe")) and "fluid" not in (s.get("kind") or ""):
            bad.append(f"{what} at {t} is on {name or 'nothing'}")
    return bad


def main():
    names = sys.argv[1:] or list(REQUESTS)
    s = Service("vanilla")
    planner.configure(s.data)
    ingame._service = s
    size_of = _sizer(s.data)
    failed = []
    for name in names:
        st = {}
        out = s.request(s.params(json.loads(json.dumps(REQUESTS[name]))), measure=False, keep=st)
        lays = out.get("layouts") or []
        problems = []
        if len(lays) < 2:
            problems.append(f"{len(lays)} layouts offered")
        if lays and lays[0]["blueprint"] != out["blueprint"]:
            problems.append("the first layout isn't the plan's own")
        if lays and lays[0]["area"] > AREA_BEFORE[name]:
            problems.append(f"the plan's layout grew: {lays[0]['area']} tiles, {AREA_BEFORE[name]} before")
        if len(st.get("layouts") or []) != len(lays):
            problems.append("the place state doesn't have one entry per layout")
        sigs = set()
        for i, l in enumerate(lays):
            pr = first_print(l["blueprint"])
            ents = pr.get("entities") or []
            sig = tuple(sorted((e["name"], e["position"]["x"], e["position"]["y"]) for e in ents))
            if sig in sigs:
                problems.append(f"layout {i + 1} is a duplicate")
            sigs.add(sig)
            problems += [f"layout {i + 1}: {p}" for p in check_print(s.data, size_of, ents)]
            last = st["layouts"][i]["last"]
            problems += [f"layout {i + 1}: {p}" for p in check_ends(s.data, size_of, last["entities"],
                                                                    last["sources"], "input")]
            problems += [f"layout {i + 1}: {p}" for p in check_ends(s.data, size_of, last["entities"],
                                                                    last["sinks"], "output")]
            ingame._pick_layout(st, i + 1)
            if st["last"] is not last:
                problems.append(f"layout {i + 1}: picking it doesn't make it the plan to place")
        row = "  ".join(f"{l['kind']} {l['w']}x{l['h']}={l['area']} b{l['belts']} m{l['machines']}" for l in lays)
        print(f"{'FAIL' if problems else 'ok  '} {name}: {row}")
        for p in problems:
            print("    " + p)
        if problems:
            failed.append(name)
    if failed:
        print(f"{len(failed)} of {len(names)} failed")
        return 1
    print(f"ok: {len(names)} lines, their layouts sound")
    return 0


if __name__ == "__main__":
    sys.exit(main())
