"""Direct insertion (direct.py), no game: electronic circuits with copper cable made in the blueprint come out as
cable assemblers inserting into circuit assemblers. A full yellow belt is CECEC units (3 cable : 2 circuit), a few a
minute CEC; every circuit assembler gets cable from each cable assembler beside it, nothing overlaps, every machine
and inserter has power, the inputs come in at the west edge and the output leaves at the east. The bus feed makes
cable this way too, and its bus load counts copper (what the cable is made from), not cable.
python mod/test/direct_test.py   (its rate in a game: python -m bpgen.verify --only direct)"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import extend, ingame, planner  # noqa: E402
from bpgen.service import Service  # noqa: E402

s = Service("vanilla")
planner.configure(s.data)
ingame._service = s
EARLY = ["inserter", "long-handed-inserter", "fast-inserter"]
MAKE = {"copper-cable": {"recipe": "copper-cable", "machine": "assembling-machine-2"}}


def plan(rate=None, **params):
    req = {"recipe": "electronic-circuit", "machine": "assembling-machine-2", "belts": ["transport-belt"],
           "inserters": EARLY, "player": 1, "params": dict({"make": MAKE}, **params)}
    if rate:
        req["rate_per_min"] = rate
    out = json.loads(ingame.plan(json.dumps(req)))
    assert "error" not in out, out
    return out


def check(out, unit, machines, at_least):
    full = s.plan(dict(s.params_from_request({"recipe": "electronic-circuit", "machine": "assembling-machine-2",
                                              "belts": ["transport-belt"], "inserters": EARLY}),
                       make=MAKE, rate_per_min=out.get("_rate")))
    sm = out["summary"]
    assert sm["machines"] == machines, (sm["machines"], machines)
    assert sm["expected"] * 60 >= at_least, sm["expected"]
    assert any(f"({unit} units)" in n for n in out["notes"]), out["notes"]
    ents = full["entities"]
    # nothing overlaps
    seen = {}
    for e in ents:
        for t in extend.footprint(e):
            assert t not in seen, f"{e['name']} on {seen[t]['name']} at {t}"
            seen[t] = e
    # every circuit assembler gets cable from each cable assembler beside it
    mach = {(round(e["position"]["x"] - 1.5), round(e["position"]["y"] - 1.5)): e for e in ents
            if e["name"] == "assembling-machine-2"}
    ins = {(int(e["position"]["x"]), int(e["position"]["y"])): e for e in ents if e["name"].endswith("inserter")}
    for (x, y), m in mach.items():
        if m["recipe"] != "electronic-circuit":
            continue
        for dx, pick in ((-4, planner.WEST), (4, planner.EAST)):
            n = mach.get((x + dx, y))
            if n and n["recipe"] == "copper-cable":
                gx = x - 1 if dx < 0 else x + 3
                fed = [i for (ix, iy), i in ins.items() if ix == gx and y <= iy < y + 3 and i.get("direction") == pick]
                assert fed, f"circuit assembler at {x},{y}: no inserter from the cable assembler at {x + dx},{y}"
    # power: every machine and inserter within 3 tiles of a pole (a medium pole's 7x7)
    poles = [(e["position"]["x"], e["position"]["y"]) for e in ents if e["name"] == planner.POLE]
    for e in ents:
        if e["name"].endswith("inserter") or e["name"].startswith("assembling"):
            ts = extend.footprint(e)
            assert any(any(abs(tx + 0.5 - px) <= 3.5 and abs(ty + 0.5 - py) <= 3.5 for tx, ty in ts)
                       for px, py in poles), f"no power: {e['name']} at {e['position']}"
    xs = [t[0] for e in ents for t in extend.footprint(e)]
    belts = {}  # row -> the belt tiles' x
    for e in ents:
        if e["name"] == "transport-belt":
            belts.setdefault(e["position"]["y"], []).append(e["position"]["x"])
    assert all(src["position"]["x"] == min(belts[src["position"]["y"]]) for src in full["sources"]), full["sources"]
    assert all(k["position"]["x"] >= max(xs) for k in full["sinks"]), full["sinks"]
    return full


out = plan()
full = check(out, "CECEC", 30, 899)
print("full belt:", out["summary"]["machines"], "machines,", round(out["summary"]["expected"] * 60), "/min")
few = plan(120)
few["_rate"] = 120
check(few, "CEC", 6, 120)
print("120/min:", few["summary"]["machines"], "machines,", round(few["summary"]["expected"] * 60), "/min")

# the bus feed makes the cable here too; what it takes off the bus is copper and iron, not cable
bus_items = ["copper-plate", "iron-plate", "stone", "coal"]
ents = [{"name": "transport-belt", "type": "transport-belt", "position": {"x": 20 + 2 * i + 0.5, "y": y + 0.5},
         "direction": 8, "lanes": [it, it]} for i, it in enumerate(bus_items) for y in range(0, 120)]
snap = {"surface": "nauvis", "tick": 5, "area": [0, 0, 160, 160], "entities": ents}
bus = plan(600, bus_feed=True, snapshot=snap, origin={"x": 60, "y": 60})
assert bus.get("absolute") and bus["absolute"]["taps"] >= 2, bus.get("absolute")
assert any("direct insertion" in n for n in bus["notes"]), bus["notes"]
needs = ingame._players[1]["last"]["needs"]
assert "copper-cable" not in needs and needs.get("copper-plate", 0) > 0, needs
print("bus fed:", bus["absolute"]["taps"], "taps; needs", {k: round(v, 2) for k, v in needs.items()})
print("ok")
