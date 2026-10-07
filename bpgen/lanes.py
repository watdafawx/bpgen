"""Two products on one output belt, one per lane.

A block's output belt carries its product on both lanes (its two machine rows drop on opposite lanes). Its whole
belt side-loaded onto one lane of a merge belt puts it all on that lane: product A comes in from the north and fills
the north lane, product B from the south and fills the south lane, and the merge belt goes on east as one output.
A lane holds half a belt, so a product only pairs up when it fits in half.
"""
import math

from bpgen import chain, router
from bpgen.extend import footprint
from bpgen.planner import EAST, NORTH, SOUTH

MARGIN = 12  # tiles around the blueprint the merge belts may use


def share_output_lanes(data, ents, sinks, belt, rates, lane_capacity):
    """ents: the blueprint's entities (with w/h), sinks: its open belt outputs ({position, item}), rates: item ->
    items/s. -> (entities added, new sinks, notes)"""
    ug, ug_max = chain.related_belts(data, belt)
    blocked = set()
    for e in ents:
        blocked.update(footprint(e))
    xs = [t[0] for t in blocked]
    ys = [t[1] for t in blocked]
    bounds = (min(xs) - MARGIN, min(ys) - MARGIN, max(xs) + MARGIN, max(ys) + MARGIN)
    spans = set()
    belts = [s for s in sinks if s.get("kind", "belt") == "belt" and s.get("item")]
    others = [s for s in sinks if s not in belts]
    notes, fits = [], []
    for s in belts:
        if rates.get(s["item"], 0) <= lane_capacity:
            fits.append(s)
        else:
            others.append(s)
            notes.append(f"{s['item']} needs more than one lane: keeps its own output belt")
    fits.sort(key=lambda s: s["position"]["y"])  # neighbours pair up: the shortest belts
    added, out = [], list(others)
    x_merge = max(xs) + 3  # merge tiles in a column east of everything
    while len(fits) >= 2:
        a, b = fits.pop(0), fits.pop(0)
        y = round((a["position"]["y"] + b["position"]["y"]) / 2 - 0.5)
        t = (x_merge, y)
        for dy in range(0, 30):  # the nearest free merge tile with room for the way out
            for cand in ((x_merge, y + dy), (x_merge, y - dy)):
                if cand not in blocked and (cand[0] + 1, cand[1]) not in blocked:
                    t = cand
                    break
            else:
                continue
            break
        blocked.update({t, (t[0] + 1, t[1])})
        legs = []
        try:
            for s, into in ((a, SOUTH), (b, NORTH)):  # a from the north: its north lane; b from the south
                start = (math.floor(s["position"]["x"]), math.floor(s["position"]["y"]))
                if start in blocked:  # (a sink is the output belt's last tile: start just past it)
                    start = (start[0] + 1, start[1])
                path = router.route(blocked, [(start, [EAST])], t, into, bounds, ug_max, underground_spans=spans)
                router.reserve(path, blocked, spans)
                legs.append(path)
        except router.RouteError:
            out += [a, b]
            notes.append(f"{a['item']} and {b['item']}: no room to merge their belts, each keeps its own")
            continue
        for path in legs:
            for tile, kind, d in path:
                e = {"name": belt if kind == "belt" else ug, "position": {"x": tile[0] + 0.5, "y": tile[1] + 0.5},
                     "direction": d}
                if kind != "belt":
                    e["ug_type"] = "input" if kind == "ug-in" else "output"
                added.append(e)
        added.append({"name": belt, "position": {"x": t[0] + 0.5, "y": t[1] + 0.5}, "direction": EAST})
        added.append({"name": belt, "position": {"x": t[0] + 1.5, "y": t[1] + 0.5}, "direction": EAST})  # the way out
        out.append({"kind": "belt", "position": {"x": t[0] + 1.5, "y": t[1] + 0.5}, "item": a["item"],
                    "lanes": [a["item"], b["item"]]})
        notes.append(f"{a['item']} (north lane) and {b['item']} (south lane) share one output belt")
    out += fits  # an odd one out keeps its belt
    return added, out, notes
