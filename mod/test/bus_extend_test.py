"""extend.place beside a main bus: a 4-lane bus flowing south (iron, copper, stone, coal), a gear line placed by it
must tap the iron lane and put the gears on a new bus lane. python mod/test/bus_extend_test.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import extend, ingame, planner
from bpgen.service import Service

s = Service("vanilla"); planner.configure(s.data)
res = s.plan({"recipe": "iron-gear-wheel", "machine": "assembling-machine-2", "belt": "transport-belt",
              "rate_per_min": 60})
bus_items = ["copper-plate", "iron-plate", "stone", "coal"]
ents = [{"name": "transport-belt", "type": "transport-belt", "position": {"x": 20 + 2 * i + 0.5, "y": y + 0.5},
         "direction": 8, "lanes": [it, it]} for i, it in enumerate(bus_items) for y in range(0, 80)]
snap = {"area": [0, 0, 90, 100], "entities": ents}
sinks = [dict(k, item=k.get("item") or "iron-gear-wheel") for k in res.get("sinks") or []]
for mode in ("auto", "off"):
    r = extend.place(s.data, extend.Ground(snap), res["entities"], res.get("sources") or [], sinks=sinks, bus=mode)
    print(mode, r["offset"], r["bus"] and r["bus"]["lanes"], r["taps"], r["deliveries"], r["notes"], sep="\n  ")
    if mode == "auto":
        assert r["bus"] and r["bus"]["lanes"] == 4, "bus not found"
        assert r["taps"] and all(abs(t["from"]["x"] - 22.5) < 1 for t in r["taps"]), "should tap the iron lane"
        assert any(dl.get("new_lane") for dl in r["deliveries"]), "gears should get a new bus lane"
        plan = extend.plan_tiles(extend.shifted(res["entities"], *r["offset"]))
        assert min(t[0] for t in plan) > 26 + extend.BUS_ROOM, "build should sit beside the bus, clear of new lanes"
        ugs = [e for e in r["entities"] if e.get("ug_type")]
        assert ugs and all(e["position"]["x"] in (22.5, 24.5, 26.5) and e["direction"] == 8 for e in ugs),             "lanes between the iron lane and the build should dive under each branch"
        assert len(ugs) == 2 * 2 * len(r["taps"]), "a dive pair per crossed lane per branch"
# lane load: two iron lanes share the taps; one iron lane asked for twice what it carries says so
big = s.plan({"recipe": "iron-gear-wheel", "machine": "assembling-machine-2", "belt": "transport-belt",
              "rate_per_min": 900})
big_sinks = [dict(k, item="iron-gear-wheel") for k in big["sinks"]]
for items, check in ((["copper-plate", "iron-plate", "iron-plate", "coal"], "spread"),
                     (["copper-plate", "iron-plate", "stone", "coal"], "overdrawn")):
    ents = [{"name": "transport-belt", "type": "transport-belt", "position": {"x": 20 + 2 * i + 0.5, "y": y + 0.5},
             "direction": 8, "lanes": [it, it]} for i, it in enumerate(items) for y in range(0, 80)]
    r = extend.place(s.data, extend.Ground({"area": [0, 0, 120, 120], "entities": ents}), big["entities"],
                     big["sources"], sinks=big_sinks, needs=ingame._line_needs(s.data, big["summary"]))
    print(check, r["bus"]["load"], [n for n in r["notes"] if "takes" in n])
    if check == "spread":
        assert sorted(r["bus"]["load"]) == [22, 24], "each iron lane should feed one input"
        assert not any("takes" in n for n in r["notes"])
    else:
        assert any("takes 1800/min from one bus lane" in n for n in r["notes"]), "overdrawn lane should be noted"
        u = r["upgrades"]
        assert len(u) == 1 and u[0]["at"] == 22 and abs(u[0]["need"] - 30) < 0.5 and u[0]["belt"] == "transport-belt", u
# turned to face the bus: a bus flowing east (the build south of it: inputs turned north), and one flowing south
# with the player west of it (inputs turned east); every feed route stays short
for name, d, seed in (("east-flowing", 4, None), ("south-flowing, player west", 8, (5, 40))):
    ents = [{"name": "transport-belt", "type": "transport-belt", "direction": d, "lanes": [it, it],
             "position": {"x": (y if d == 4 else 30 + 2 * i) + 0.5, "y": (30 + 2 * i if d == 4 else y) + 0.5}}
            for i, it in enumerate(bus_items) for y in range(0, 80)]
    r = extend.place(s.data, extend.Ground({"area": [0, 0, 110, 110], "entities": ents}), res["entities"],
                     res.get("sources") or [], sinks=sinks, seed=seed)
    print(name, r["turn"], [t["belts"] for t in r["taps"]], r["notes"][0])
    assert r["turn"] and "turned to face it" in r["notes"][0], "the plan should be turned to face the bus"
    assert len(r["taps"]) == 2 and all(t["belts"] <= 30 for t in r["taps"]), "feeds should stay short once turned"
    if seed:
        assert "west side" in r["notes"][0], "built on the player's side"
# a short bus is continued past the build: its open lane ends only (one ends in a chest: left as it is)
ents = [{"name": "transport-belt", "type": "transport-belt", "position": {"x": 20 + 2 * i + 0.5, "y": y + 0.5},
         "direction": 8, "lanes": [it, it]} for i, it in enumerate(bus_items) for y in range(0, 40)]
ents.append({"name": "wooden-chest", "type": "container", "position": {"x": 24.5, "y": 40.5}})
r = extend.place(s.data, extend.Ground({"area": [0, 0, 110, 110], "entities": ents}), res["entities"],
                 res.get("sources") or [], sinks=sinks, seed=(40, 38))
cont = {e["position"]["x"] for e in r["entities"] if e["name"] == "transport-belt" and e["direction"] == 8
        and e["position"]["y"] > 40 and e["position"]["x"] in (20.5, 22.5, 24.5, 26.5)}
print("continued", sorted(cont), [n for n in r["notes"] if "extended" in n])
assert cont == {20.5, 22.5, 26.5}, "open lanes continued, the one into the chest not"
assert any("bus extended" in n for n in r["notes"])
# taps on the bus in the lane's own tier: a fast-belt plan on a yellow bus gets yellow splitters and undergrounds
fast = s.plan({"recipe": "iron-gear-wheel", "machine": "assembling-machine-2", "belt": "fast-transport-belt",
               "rate_per_min": 60})
ents = [{"name": "transport-belt", "type": "transport-belt", "position": {"x": 20 + 2 * i + 0.5, "y": y + 0.5},
         "direction": 8, "lanes": [it, it]} for i, it in enumerate(bus_items) for y in range(0, 80)]
r = extend.place(s.data, extend.Ground({"area": [0, 0, 110, 110], "entities": ents}), fast["entities"],
                 fast["sources"], sinks=[dict(k, item="iron-gear-wheel") for k in fast["sinks"]])
on_bus = {e["name"] for e in r["entities"] if 20 <= e["position"]["x"] <= 27.5 and e.get("direction") == 8}
print("tier on the bus", sorted(on_bus))
assert on_bus <= {"transport-belt", "underground-belt", "splitter"}, on_bus
# a water pipe among the belt lanes: still one bus, and a branch crossing it makes the pipe dive (pipe-to-ground pair)
ents = []
for i, it in enumerate(["copper-plate", "iron-plate", None, "coal"]):
    for y in range(0, 80):
        pos = {"x": 20 + 2 * i + 0.5, "y": y + 0.5}
        ents.append({"name": "transport-belt", "type": "transport-belt", "position": pos, "direction": 8,
                     "lanes": [it, it]} if it else {"name": "pipe", "type": "pipe", "position": pos, "fluid": "water"})
r = extend.place(s.data, extend.Ground({"area": [0, 0, 110, 110], "entities": ents}), res["entities"],
                 res.get("sources") or [], sinks=sinks)
ptg = sorted((e["position"]["y"], e["direction"]) for e in r["entities"] if e["name"] == "pipe-to-ground"
             and e["position"]["x"] == 24.5)
print("pipe lane", r["notes"][0], ptg)
assert "1 pipe" in r["notes"][0] and len(r["taps"]) == 2, r["notes"]
assert len(ptg) == 4 and [d for _, d in ptg] == [0, 8, 0, 8], "each branch: the pipe dives, ends facing the pipe"
# measured lanes (the mod's flows): an iron lane already carrying 14/s can't take this line's 2/s more; with a
# second, idle iron lane the line takes that one instead
need = ingame._line_needs(s.data, res["summary"])
for items in (["copper-plate", "iron-plate", "stone", "coal"], ["copper-plate", "iron-plate", "iron-plate", "coal"]):
    ents = []
    for i, it in enumerate(items):
        busy = it == "iron-plate" and i == 1
        for y in range(0, 80):
            ents.append({"name": "transport-belt", "type": "transport-belt", "direction": 8, "lanes": [it, it],
                         "position": {"x": 20 + 2 * i + 0.5, "y": y + 0.5},
                         "flow": 14.0 if busy else 0.0, "items": 7 if busy else 8})
    r = extend.place(s.data, extend.Ground({"area": [0, 0, 110, 110], "entities": ents}), res["entities"],
                     res.get("sources") or [], sinks=sinks, needs=need)
    print("measured", items[2], r["bus"]["load"], [n for n in r["notes"] if "already" in n])
    if items[2] == "stone":
        assert any("already carries about 840/min" in n for n in r["notes"]), r["notes"]
        assert r["upgrades"] and abs(r["upgrades"][0]["need"] - (14 + need["iron-plate"])) < 0.5, r["upgrades"]
    else:
        assert list(r["bus"]["load"]) == [24] and not any("already" in n for n in r["notes"]), "the idle lane"
# a new lane along the bus: branches leave it east at y 30 and west at y 50, the new lane dives under the one in its way
ents = [{"name": "transport-belt", "type": "transport-belt", "position": {"x": 20 + 2 * i + 0.5, "y": y + 0.5},
         "direction": 8, "lanes": [it, it]} for i, it in enumerate(bus_items) for y in range(0, 80)]
ents += [{"name": "transport-belt", "type": "transport-belt", "position": {"x": x + 0.5, "y": 30.5}, "direction": 4}
         for x in range(27, 40)]
ents += [{"name": "transport-belt", "type": "transport-belt", "position": {"x": x + 0.5, "y": 50.5}, "direction": 12}
         for x in range(5, 19)]
g = extend.Ground({"area": [0, 0, 110, 110], "entities": ents})
lane_ents, head, length = extend.new_bus_lane(s.data, g, extend.find_bus(g), "iron-plate")
ugs = sorted((e["position"]["y"], e["ug_type"]) for e in lane_ents if e.get("ug_type"))
print("new lane", head, length, ugs)
assert head == (28, 0) and length == 80, (head, length)
assert ugs == [(29.5, "input"), (31.5, "output")], "under the branch at y 30"
# fluid inputs off a bus pipe: a plastic line beside a bus with a petroleum pipe among its belts (between coal and
# iron) takes the gas straight off it, under the iron lane, and the coal off its lane
plastic = s.plan({"recipe": "plastic-bar", "machine": "chemical-plant", "belt": "transport-belt", "rate_per_min": 120})
ents = []
for i, it in enumerate(["copper-plate", "coal", None, "iron-plate"]):
    for y in range(0, 80):
        pos = {"x": 20 + 2 * i + 0.5, "y": y + 0.5}
        ents.append({"name": "transport-belt", "type": "transport-belt", "position": pos, "direction": 8,
                     "lanes": [it, it]} if it else {"name": "pipe", "type": "pipe", "position": pos,
                                                    "fluid": "petroleum-gas"})
r = extend.place(s.data, extend.Ground({"area": [0, 0, 120, 120], "entities": ents}), plastic["entities"],
                 plastic["sources"], sinks=[dict(k, item="plastic-bar") for k in plastic["sinks"]])
gas = [t for t in r["taps"] if t["items"] == "petroleum-gas"]
print("fluid taps", [(t["from"], t["belts"]) for t in gas])
assert len(gas) == 2 and all(t["from"]["x"] == 24.5 and t["belts"] <= 6 for t in gas), "gas off the bus pipe, short"
assert sum(1 for t in r["taps"] if t["items"] == "coal") == 2
print("ok")
