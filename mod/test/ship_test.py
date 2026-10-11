"""The ship generator (bpgen/ship.py), no game: an inner-planet shuttle for 2 and 4 thrusters; nothing on top of
anything, every powered thing reached by a pole, nothing (no foundation either) in front of the collectors or
behind the thrusters, the hub's inserters tagged for wiring; Krastorio 2's
condensers too when bpgen has your pack's data. harness\\ship_run.py runs one on a real platform.
python mod/test/ship_test.py"""
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import extend, planner, ship  # noqa: E402
from bpgen.service import Service  # noqa: E402

def runs():
    s = Service("vanilla")
    planner.configure(s.data)
    for n in (2, 4):
        yield s, n, 12
    try:  # (your own pack, when bpgen has its data: Krastorio 2's condensers in place of the melters)
        s = Service("pack")
    except Exception as e:  # noqa: BLE001
        print("no pack data:", e)
        return
    if "kr-atmospheric-condenser" in s.data.raw.get("assembling-machine", {}):
        planner.configure(s.data)
        yield s, 2, 9


for s, n, gated in runs():
    r = ship.plan_ship(s.data, thrusters=n)
    print("\n".join(r["notes"]))
    ents = [s.decorate(e) for e in r["entities"]]
    c = Counter(t for e in ents for t in extend.footprint(e))
    assert not [t for t, k in c.items() if k > 1], "overlaps"
    assert not any("not reached" in note for note in r["notes"]), r["notes"]
    floor = {(t["position"]["x"], t["position"]["y"]) for t in r["tiles"]}
    used = set(c)
    for e in ents:
        x, y = e["position"]["x"], e["position"]["y"]
        if e["name"] == "asteroid-collector":  # (its front row and four ahead: open space)
            ahead = {(int(x - 1.5) + i, int(y - 1.5) - k) for i in range(3) for k in range(0, 4)}
            assert not ahead & (floor | (used - set(extend.footprint(e)))), f"something in front of a collector at {x},{y}"
        if e["name"] == "thruster":
            behind = {(int(x) + i, int(y + 2.5) + k) for i in range(-2, 2) for k in range(0, 20)}
            assert not behind & (floor | used), f"something behind a thruster at {x},{y}"
    assert sum(1 for e in ents if (e.get("tags") or {}).get("bpgen_hub")) == gated, "the hub's gated inserters (and the chunk decider)"
    assert sum(1 for e in ents if e["name"] == "thruster") == n
print("ok")
