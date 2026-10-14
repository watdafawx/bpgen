"""Power to a build placed next to the base (extend.link_power), no game: from the base's nearest powered pole to the
build's nearest pole, every new pole within wire reach of the one before, round a wall of chests in the way (poles
laid along a straight line and nudged aside could end up out of reach); a pole of a network with no power source is
passed over; no way round at all: None (the build says to connect it by hand). python mod/test/power_test.py"""
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import extend, planner  # noqa: E402

REACH = planner.POLE_REACH


def ent(name, typ, x, y, **kw):
    return dict({"name": name, "type": typ, "position": {"x": x + 0.5, "y": y + 0.5}, "box": [x, y, x + 1, y + 1]}, **kw)


def chain_ok(old, poles, new):
    pts = [old] + [(p["position"]["x"], p["position"]["y"]) for p in poles]
    assert all(math.dist(a, b) <= REACH for a, b in zip(pts, pts[1:])), pts
    assert math.dist(pts[-1], new) <= REACH, (pts[-1], new)


# a wall 7 tiles thick across the straight line, with a gap at its south end
wall = [ent("wooden-chest", "container", x, y) for x in range(14, 21) for y in range(-12, 9)]
base = [ent("medium-electric-pole", "electric-pole", 0, 0, network=1, powered=True)] + wall
dead = ent("medium-electric-pole", "electric-pole", 30, -6, network=2, powered=False)  # (a mall's own, unpowered)
snap = {"area": [-20, -30, 60, 30], "entities": base + [dead]}
ground = extend.Ground(snap)
build = [{"name": planner.POLE, "position": {"x": 36.5, "y": 0.5}}]
blocked = ground.blocked(avoid_ore=False)
poles = extend.link_power(ground, blocked, build, planner.POLE, REACH)
assert poles, "linked"
chain_ok((0.5, 0.5), poles, (36.5, 0.5))
wall_tiles = {t for e in wall for t in extend.footprint(e)}
assert not any((math.floor(p["position"]["x"]), math.floor(p["position"]["y"])) in wall_tiles for p in poles)
print(f"round the wall: {len(poles)} poles")

# walled in on every side: no way, said so
box = [ent("wooden-chest", "container", x, y) for x in range(-16, 17) for y in range(-16, 17)
       if max(abs(x), abs(y)) >= 3]  # (13 thick: wider than a wire reaches)
snap = {"area": [-20, -30, 60, 30], "entities": [base[0]] + box}  # (the pole inside, the build outside)
ground = extend.Ground(snap)
assert extend.link_power(ground, ground.blocked(avoid_ore=False), build, planner.POLE, REACH) is None
print("walled in: None")

# the bus design's pole runs (pole_chain) the same way
run = extend.pole_chain((0, 0), (36, 0), set(wall_tiles))
assert run is not None
chain_ok((0.5, 0.5), run, (36.5, 0.5))
print("ok")
