"""Oil for the bus design: pumpjacks on the picked oil fields, their crude piped to an oil block at the bus head
(refineries and chemical plants, laid out by the starter base's composer: plastic, and sulfur when there's water)
whose products go onto bus lanes like the smelters' plates.

    crude field -> pumpjacks -> pipes -> [oil block: basic oil processing -> petroleum -> plastic (+ coal), sulfur
    (+ water)] -> belts -> bus lanes

The block's water inlet is left open (an offshore pump goes there: placing one needs the shore, left to the player),
and its coal comes from a chest at its coal belt, filled by hand (plastic takes little).
"""
import math

from bpgen import base, compose_base, extend, planner

PUMPJACK = "pumpjack"
PUMPJACK_OUT = (1, -2)  # (facing north: the tile its crude comes out on, from its middle)
FLUID_CATEGORY = "basic-fluid"
PIPE, PIPE_UG = planner.PIPE, planner.UNDERGROUND
CHEST, CHEST_INSERTER = "wooden-chest", "burner-inserter"


def fields(snap, areas):
    """the oil (fluid resource) entities in the picked areas: [(name, tile, amount)], from the snapshot's
    fluid_resources (the mod sends their amounts; an older one doesn't: 100% then)"""
    out = []
    for f in snap.get("fluid_resources") or []:
        x, y = f["x"], f["y"]
        if any(a[0] <= x <= a[2] and a[1] <= y <= a[3] for a in areas):
            out.append((f["name"], (math.floor(x), math.floor(y)), f.get("amount")))
    return out


def crude_rate(data, name, amount):
    """crude a second from one pumpjack on this field"""
    r = data.raw.get("resource", {}).get(name) or {}
    m = r.get("minable") or {}
    res = (m.get("results") or [{}])[0]
    per = (res.get("amount_min", 10) + res.get("amount_max", 10)) / 2 if "amount_min" in res else res.get("amount", 10)
    normal = r.get("normal") or 300000
    amount = max(amount or normal, r.get("minimum") or 0)
    pj = data.raw.get("mining-drill", {}).get(PUMPJACK) or {}
    return pj.get("mining_speed", 1) * amount / normal * per / (m.get("mining_time") or 1)


def targets(crude, water):
    """per minute: plastic and sulfur from this much crude a second (basic oil processing: 45 petroleum a 100 crude;
    with water a third of the petroleum makes sulfur)"""
    petroleum = crude * 0.45
    if water:
        return {"plastic-bar": round(petroleum * 2 / 3 / 20 * 2 * 60, 1), "sulfur": round(petroleum / 3 / 30 * 2 * 60, 1)}
    return {"plastic-bar": round(petroleum / 20 * 2 * 60, 1)}


def plan_block(svc, crude, belt, water=True):
    """the oil block (local coordinates, top-left at 0): -> dict(entities, sources, sinks, products {item: per s},
    notes) or raises PlanError"""
    data = svc.data
    raw = (base.raw_items(data) - base.MADE_HERE) | {"crude-oil", "water", "coal"}
    want = targets(crude, water)
    if not water:
        raw.discard("water")
    out = svc.plan_base({"targets": want, "raw": sorted(raw), "labs": False, "mall": False, "layout": "compact",
                         "belt": belt, "machines": ["oil-refinery", "chemical-plant"]})
    first = (out.get("sections") or [{}])[0]
    if first.get("kind") != "routed":
        raise planner.PlanError("the oil block didn't lay out: " + (out["summary"].get("route_note") or ""))
    r = first["result"]
    return {"entities": r["entities"], "sources": r.get("sources") or [], "sinks": r.get("sinks") or [],
            "products": {k: v / 60 for k, v in want.items()}}


def pumpjacks(svc, oil, blocked):
    """a pumpjack on each oil entity, facing north, and a medium pole beside it (on a free tile). -> (entities,
    their output tiles, crude a second)"""
    ents, outs, rate = [], [], 0.0
    for name, (x, y), amount in oil:
        pj = {"name": PUMPJACK, "position": {"x": x + 0.5, "y": y + 0.5}, "direction": planner.NORTH}
        tiles = extend.plan_tiles([svc.decorate(pj)])
        if tiles & blocked:
            continue
        blocked |= tiles
        ents.append(pj)
        outs.append((x + PUMPJACK_OUT[0], y + PUMPJACK_OUT[1]))
        rate += crude_rate(svc.data, name, amount)
    for o in outs:
        blocked.add(o)  # (their pipes' tiles: kept from the poles)
    for e in list(ents):
        x, y = math.floor(e["position"]["x"]), math.floor(e["position"]["y"])
        spot = next(((x + dx, y + dy) for dx, dy in ((-2, -2), (2, 2), (-2, 2), (2, -2), (0, 2), (-2, 0), (0, -2), (2, 0))
                     if (x + dx, y + dy) not in blocked), None)
        if spot:
            blocked.add(spot)
            ents.append({"name": planner.POLE, "position": {"x": spot[0] + 0.5, "y": spot[1] + 0.5}})
    for o in outs:
        blocked.discard(o)
    return ents, outs, rate


def foreign_fluids(block, crude_inlet):
    """tile -> fluid of what in the placed oil block carries fluids: its pipes joined to the crude inlet "crude-oil",
    every other pipe and fluid machine "other" (a crude pipe mustn't touch them: it would join them)"""
    pipes = {}
    for e in block:
        if e["name"] in (PIPE, PIPE_UG):
            pipes[(math.floor(e["position"]["x"]), math.floor(e["position"]["y"]))] = e
    crude, todo = set(), [crude_inlet]
    while todo:  # (the block's crude pipes: joined to the tile beside the inlet)
        t = todo.pop()
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            q = (t[0] + d[0], t[1] + d[1])
            if q in pipes and q not in crude:
                crude.add(q)
                todo.append(q)
    out = {}
    for e in block:
        if e.get("fluid") or e["name"] in (PIPE, PIPE_UG):
            for t in extend.footprint(e):
                out[t] = "crude-oil" if t in crude else "other"
    return out


def pipe_crude(goal, outs, blocked, foreign, bounds):
    """pipes from the oil block's crude inlet (`goal`, an empty tile beside its pipe) to every pumpjack's output tile,
    each joining the pipes so far; another fluid's pipes never touched. -> entities, or None (one not reached)"""
    network, ents, pspans = {goal}, [], set()
    ents.append({"name": PIPE, "position": {"x": goal[0] + 0.5, "y": goal[1] + 0.5}})
    for o in sorted(outs, key=lambda t: abs(t[0] - goal[0]) + abs(t[1] - goal[1])):
        if o in network:
            continue
        try:
            path = compose_base._route_pipe(network, o, "crude-oil", blocked, foreign, bounds, pspans,
                                            into=planner.SOUTH)
        except compose_base.ComposeError:
            return None
        compose_base._claim_spans(path, pspans)
        for tile, kind, d in path:
            foreign[tile] = "crude-oil"
            if tile in network and kind == "pipe":
                continue
            e = {"name": PIPE if kind == "pipe" else PIPE_UG, "position": {"x": tile[0] + 0.5, "y": tile[1] + 0.5}}
            if kind != "pipe":
                e["direction"] = d
            else:
                network.add(tile)
            ents.append(e)
            blocked.add(tile)
    return ents
