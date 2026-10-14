"""The ground kept between placements (site.py), no game: a line planned beside a 4-lane bus, then placed again
from the same snapshot (as the window's arrows, Rotate and Redo send it) reuses the site read the first time and
lands exactly as a fresh read does; a placement's taps don't stay behind in the kept ground; players keep their own
last plan. python mod/test/site_test.py"""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import ingame, planner  # noqa: E402
from bpgen.service import Service  # noqa: E402

s = Service("vanilla")
planner.configure(s.data)
ingame._service = s
bus_items = ["copper-plate", "iron-plate", "stone", "coal"]


def snapshot(tick):
    ents = [{"name": "transport-belt", "type": "transport-belt", "position": {"x": 20 + 2 * i + 0.5, "y": y + 0.5},
             "direction": 8, "lanes": [it, it]} for i, it in enumerate(bus_items) for y in range(0, 100)]
    return {"surface": "nauvis", "tick": tick, "area": [0, 0, 120, 120], "entities": ents}


req = {"recipe": "electronic-circuit", "machine": "assembling-machine-2", "belts": ["transport-belt"],
       "inserters": ["inserter", "long-handed-inserter", "fast-inserter"], "rate_per_min": 120, "player": 1,
       "params": {"bus_feed": True, "snapshot": snapshot(100), "origin": {"x": 40, "y": 50}}}
out = json.loads(ingame.plan(json.dumps(req)))
assert out.get("absolute"), out
site = ingame._players[1]["site"]
assert site is not None and site.bus, "the plan's site (with its bus) is kept"
assert not site.ground.tapped and not site.bus["load"], "the kept site is left as read"


def place(tick, **kw):
    t = time.perf_counter()
    got = json.loads(ingame.place(json.dumps(dict({"player": 1, "snapshot": snapshot(tick), "seed": {"x": 40, "y": 50}},
                                                  **kw))))
    assert "error" not in got, got
    return got, time.perf_counter() - t


first, t_new = place(200)  # (a new measurement: read anew)
kept = ingame._players[1]["site"]
again, t_kept = place(200)  # (the same snapshot sent again: the kept site)
assert ingame._players[1]["site"] is kept, "the same snapshot reuses its site"
assert again["blueprint"] == first["blueprint"], "placed again from the kept site: exactly as the first time"
assert not kept.ground.tapped and not kept.bus["load"], "the kept site is still as read after placing"
fresh, _ = place(300)  # (a new tick: read anew, still the same answer)
assert ingame._players[1]["site"] is not kept
assert fresh["blueprint"] == first["blueprint"], "a fresh read places it the same"
turned, _ = place(300, turn=1)
assert turned["blueprint"] != first["blueprint"], "Rotate places it differently"
back, _ = place(300)
assert back["blueprint"] == first["blueprint"], "after Rotate, unturned again: as the first time (nothing left behind)"
print(f"placed: {t_new * 1000:.0f} ms read anew, {t_kept * 1000:.0f} ms from the kept site")

# an arrow onto the bus: said plainly, not a crash
hit = json.loads(ingame.place(json.dumps({"player": 1, "snapshot": snapshot(300), "seed": {"x": 40, "y": 50},
                                          "shift": [-40, 0]})))
assert "in the way" in hit.get("error", "") and "trace" not in hit, hit

# another player's plan doesn't take this one's place
other = dict(req, player=2, recipe="iron-gear-wheel", params=dict(req["params"], snapshot=snapshot(400)))
assert json.loads(ingame.plan(json.dumps(other))).get("absolute")
mine, _ = place(300)
assert mine["blueprint"] == first["blueprint"], "player 1 still places their own plan"
print("ok")
