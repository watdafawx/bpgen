"""A plan put down by hand, no game: hand() gives the plan as a tagged blueprint (turned to face the bus, with the
spot bpgen would pick), the "player" puts it down somewhere (moved, turned, flipped: the game's ghosts simulated
here), connect() ties it in from there. Put down on bpgen's own spot it connects exactly as Place by my base does.
python mod/test/hand_test.py"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import base, extend, ingame, planner  # noqa: E402
from bpgen.service import Service  # noqa: E402

s = Service("vanilla")
planner.configure(s.data)
ingame._service = s
bus_items = ["copper-plate", "iron-plate", "stone", "coal"]
BUS = [{"name": "transport-belt", "type": "transport-belt", "position": {"x": 20 + 2 * i + 0.5, "y": y + 0.5},
        "direction": 8, "lanes": [it, it]} for i, it in enumerate(bus_items) for y in range(0, 100)]


def snapshot(tick, more=()):
    return {"surface": "nauvis", "tick": tick, "area": [0, 0, 120, 120], "entities": BUS + list(more)}


req = {"recipe": "electronic-circuit", "machine": "assembling-machine-2", "belts": ["transport-belt"],
       "inserters": ["inserter", "long-handed-inserter", "fast-inserter"], "rate_per_min": 120, "player": 1,
       "params": {"bus_feed": True, "snapshot": snapshot(50), "origin": {"x": 40, "y": 50}}}  # (cable made inside)
assert json.loads(ingame.plan(json.dumps(req))).get("blueprint")
seed = {"x": 40, "y": 50}
h = json.loads(ingame.hand(json.dumps({"player": 1, "snapshot": snapshot(100), "seed": seed})))
assert "error" not in h, h
x0, y0, w, hh = h["spot"]
assert x0 > 27, f"the spot is beside the bus, not on it: {h['spot']}"
handed = base._decode(h["blueprint"])["blueprint"]["entities"]
assert all(e["tags"]["bpgen_place"]["id"] == h["id"] for e in handed)
held = ingame._players[1]["hands"][h["id"]]["entities"]  # (where hand() had them: world coordinates at the spot)


def put_down(mirror, k, off, tick):
    """the game's ghosts of the handed plan, put down that way: (built for connect(), ghost entities)"""
    # (the game turns and flips a blueprint about its middle, then the player moves it: off from where it was)
    cx = round(sum(e["position"]["x"] for e in held) / len(held))
    cy = round(sum(e["position"]["y"] for e in held) / len(held))
    tx, ty = extend.turned(-cx if mirror else cx, cy, k)
    how = (mirror, k, (cx - tx + off[0], cy - ty + off[1]))
    moved = extend.repose({"entities": held, "sources": [], "sinks": [], "turn": 0, "offset": [0, 0]}, how)
    built = [{"i": i, "x": e["position"]["x"], "y": e["position"]["y"], "direction": e.get("direction")}
             for i, e in enumerate(moved["entities"])]
    ghosts = [dict(s.decorate(dict(e)), ghost=True) for e in moved["entities"]]
    return built, ghosts


def connect(mirror, k, off, tick):
    built, ghosts = put_down(mirror, k, off, tick)
    out = json.loads(ingame.connect(json.dumps({"player": 1, "id": h["id"], "built": built,
                                                "snapshot": snapshot(tick, ghosts)})))
    assert "error" not in out, out
    assert (out["mirror"], out["turns"]) == (mirror, k), out
    return out


# on bpgen's own spot: as Place by my base
same = connect(False, 0, (0, 0), 200)
ref = json.loads(ingame.place(json.dumps({"player": 1, "snapshot": snapshot(300), "seed": seed})))
ref_ents = base._decode(ref["blueprint"])["blueprint"]["entities"]
got_ents = base._decode(same["blueprint"])["blueprint"]["entities"]
assert same["absolute"]["taps"] == ref["absolute"]["taps"] >= 1, (same["absolute"], ref["absolute"])
assert len(got_ents) == len(ref_ents) - len(held), (len(got_ents), len(ref_ents), len(held))
print("on its spot:", same["absolute"]["taps"], "taps,", len(got_ents), "entities to connect it")

# moved down the bus, further out
moved = connect(False, 0, (6, 20), 400)
assert moved["absolute"]["taps"] >= 1, moved
print("moved:", moved["absolute"]["taps"], "taps")
# turned half round and flipped: still tied in (longer routes), the right way recognised
for mirror, k in ((False, 2), (True, 0), (True, 1)):
    out = connect(mirror, k, (10, 0), 500 + k)
    print(f"mirror={mirror} turns={k}:", out["absolute"] and out["absolute"]["taps"], "taps;",
          "; ".join(n for n in out["notes"] if "by hand" in n) or "all inputs tapped")
# a blueprint handed out a little earlier (picked up again by Move) still connects; a much older one is refused
old = h["id"]
json.loads(ingame.hand(json.dumps({"player": 1, "snapshot": snapshot(100), "seed": seed})))
assert connect(False, 0, (0, 0), 550)["absolute"]["taps"] >= 1
for _ in range(5):
    json.loads(ingame.hand(json.dumps({"player": 1, "snapshot": snapshot(100), "seed": seed})))
stale = json.loads(ingame.connect(json.dumps({"player": 1, "id": old, "built": [], "snapshot": snapshot(600)})))
assert "older plan" in stale.get("error", ""), stale
# the paste told as a delta since hand()'s snapshot (no new snapshot): tied in as from a full one; a delta from
# another snapshot than the one kept: rescan
h3 = json.loads(ingame.hand(json.dumps({"player": 1, "snapshot": snapshot(700), "seed": seed})))
held = ingame._players[1]["hands"][h3["id"]]["entities"]
built, ghosts = put_down(False, 0, (0, 0), 0)
delta = json.loads(ingame.connect(json.dumps({"player": 1, "id": h3["id"], "built": built, "delta": {
    "since": 700, "now": 705, "surface": "nauvis", "built": ghosts, "removed": []}})))
assert "error" not in delta and not delta.get("rescan"), delta
assert ingame._players[1]["site"].snap["tick"] == 705, "the site kept moves on to the delta's tick"
full = json.loads(ingame.connect(json.dumps({"player": 1, "id": h3["id"], "built": built,
                                             "snapshot": snapshot(710, ghosts)})))
assert delta["blueprint"] == full["blueprint"], "a delta ties it in as the full snapshot does"
stale = json.loads(ingame.connect(json.dumps({"player": 1, "id": h3["id"], "built": built, "delta": {
    "since": 700, "now": 720, "surface": "nauvis", "built": [], "removed": []}})))
assert stale.get("rescan"), stale
print("delta: as the full snapshot; stale delta: rescan")
print("ok")
