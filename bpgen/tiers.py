"""Starter bases in tiers, all lined up on one center: a C of stone brick above the bus head.

Every bus-layout base gets the C (4 wide, 5 tall, its top-left a tile left of the leftmost bus input and 7 rows above
it) and absolute snapping on a GRID-tile grid with the C's top-left on a grid point: pasted near the last tier,
the next lands with its C on the last one's.

  - a bigger base (re-planned) pastes over the old one, the C on the C (Ctrl+Shift: what's in the way goes)
  - add_tier: what the held base doesn't make yet (more science, new packs) as a base of its own beside it, east,
    its bus inputs on the same row; its blueprint carries the old base's C so it lands right next to it, and the old
    base stays as it is
  - fit_to_bus: a plates-fed base turned to a bus design's flow, its inputs belted from where the bus's lanes end
    (busdesign.save_last), placed right there; its snapping keeps the C where it landed for the tiers after it
"""
import math

from bpgen import base, extend, planner

TILE = "stone-path"  # (stone brick: from the start of the game)
GRID = 64
C_SHAPE = [(x, 0) for x in range(4)] + [(0, y) for y in range(1, 4)] + [(x, 4) for x in range(4)]
GAP = 6  # tiles between the old base and the added one
FITTED = "Fitted to the end of your bus design by bpgen."  # (the description of a base fitted to a bus: a book renames its prints)


def head_corner(sources):
    """the C's top-left for a base whose bus inputs are these sources (the top row of belt sources)"""
    belts = [s for s in sources if s.get("kind") == "belt"]
    if not belts:
        return None
    top = min(s["position"]["y"] for s in belts)
    hx = min(math.floor(s["position"]["x"]) for s in belts if s["position"]["y"] == top)
    return hx - 1, math.floor(top) - 7


def c_tiles(corner):
    return [(corner[0] + x, corner[1] + y) for x, y in C_SHAPE]


def marked(bp_string, corner, at=(0, 0)):
    """the blueprint with the C at `corner` and the absolute snapping that puts the C on world tiles congruent to
    `at` (mod GRID): a grid point, or where a bus design's lanes end"""
    bp = base._decode(bp_string)
    b = bp["blueprint"]
    b["tiles"] = [{"name": TILE, "position": {"x": x, "y": y}} for x, y in c_tiles(corner)]
    # (the game puts the blueprint's own origin this far into a grid cell, measured in game)
    b["snap-to-grid"] = {"x": GRID, "y": GRID}
    b["absolute-snapping"] = True
    b["position-relative-to-grid"] = {"x": (at[0] - corner[0]) % GRID, "y": (at[1] - corner[1]) % GRID}
    return base._encode(bp)


def _snap_of(bp_string, corner):
    """where a marked blueprint's C lands (mod GRID): the `at` it was marked with"""
    b = base._decode(bp_string).get("blueprint") or {}
    r = b.get("position-relative-to-grid") or {}
    return (r.get("x", 0) + corner[0]) % GRID, (r.get("y", 0) + corner[1]) % GRID


FLOW = {"north": 0, "east": 4, "south": 8, "west": 12}


def fit_to_bus(svc, ents, sources, bus, belt, beside=False):
    """a bus-layout base (flowing south, its inputs on row 0) fitted to the end of a bus's lanes: turned to the bus's
    direction, belts from each lane to the base's input of that item (in order, crossing underground). The lanes it
    doesn't need run on past its east side (its frame) and end there, side by side, for the next tier (`exits`).
    beside: the base all east of its lanes (a later tier, fitted to the lanes the last one passed on, next to it).
    -> dict(entities (world), blueprint, box, corner (world), exits [{item, end}], notes) or None (nothing to fit)"""
    from bpgen import busdesign, chain, router
    direction = bus.get("direction") or "north"
    d = FLOW.get(direction, 0)
    k = (d - 8) // 4 % 4  # (quarter turns taking the base's south to the bus's flow)
    dv = router.DIRS[d]
    lanes = [dict(ln, start=(ln["end"][0] + dv[0], ln["end"][1] + dv[1])) for ln in bus.get("lanes") or []]
    if not lanes:
        return None
    # the lanes' starts in the base's frame (row 0 of the adapter is the bus end): turned back
    for ln in lanes:
        ln["local"] = busdesign._turn_tile(ln["start"], (4 - k) % 4)
    u0 = min(ln["local"][0] for ln in lanes)
    for ln in lanes:
        ln["u"] = ln["local"][0] - u0
    cols = sorted((s for s in sources if s.get("kind") == "belt"), key=lambda s: s["position"]["x"])
    notes, pairs, spare, missing = [], [], [], []
    for item in sorted({s["lanes"][0] for s in cols if s.get("lanes")} | {ln["item"] for ln in lanes}):
        cs = [s for s in cols if (s.get("lanes") or [None])[0] == item]
        ls = sorted((ln for ln in lanes if ln["item"] == item), key=lambda ln: ln["u"])
        pairs += [(ln, (math.floor(s["position"]["x"]), 0)) for ln, s in zip(ls, cs)]
        spare += ls[len(cs):]
        missing += [item for _ in cs[len(ls):]]
    if not pairs:
        return None
    ug, max_ug = chain.related_belts(svc.data, belt)
    base_tiles = extend.plan_tiles([svc.decorate(e) for e in ents])
    span = max(ln["u"] for ln in lanes)
    if beside:  # (all of it east of its lanes: the last tier is west of them)
        bx = min(t[0] for t in base_tiles) - span - 3
    else:  # (the lanes centred over the inputs)
        cx = [g[0] for _, g in pairs]
        bx = round((min(cx) + max(cx)) / 2 - span / 2)
    # the spare lanes' ends: past the base's east side, side by side, flowing on (south) from row 0
    ex0 = max(max(t[0] for t in base_tiles), bx + span) + 2
    spare.sort(key=lambda ln: ln["u"])
    exits = [(ln, (ex0 + j, 1)) for j, ln in enumerate(spare)]
    xs = [t[0] for t in base_tiles] + [bx + ln["u"] for ln in lanes] + [g[0] for _, g in exits]
    routed = None
    n = len(pairs) + len(exits)
    for height in (n + 3, n * 2 + 6, n * 3 + 12):
        top = -height
        bounds = (min(xs) - height, top, max(xs) + height, max(t[1] for t in base_tiles))
        for order in (lambda p: abs(bx + p[0]["u"] - p[1][0]), lambda p: p[0]["u"], lambda p: -p[0]["u"],
                      lambda p: -p[1][0], lambda p: p[1][0]):
            approach = {(g[0], g[1] - 1) for _, g in pairs + exits}  # (each goal's last tile: its own belt's only)
            blocked = (set(base_tiles) | {(bx + ln["u"], top) for ln in lanes} | {g for _, g in exits}
                       | {(g[0], g[1] + 1) for _, g in exits} | approach)
            spans, out = set(), []
            try:
                for ln, goal in sorted(pairs + exits, key=order):
                    start = (bx + ln["u"], top)
                    blocked.discard(start)
                    blocked.discard((goal[0], goal[1] - 1))
                    path = router.route(blocked, [(start, [8, 4, 12])], goal, 8, bounds, max_ug,
                                        underground_spans=spans)
                    router.reserve(path, blocked, spans)
                    out += extend.path_entities(path, belt, ug)
            except router.RouteError:
                continue
            routed = (top, out)
            break
        if routed:
            break
    if not routed:
        raise planner.PlanError("couldn't run belts from the bus's lanes to the base's inputs")
    top, adapter = routed
    local = extend.shifted(ents, -bx, 0) + extend.shifted(adapter, -bx, 0)  # (lane 0's start at x 0, row `top`)
    corner_local = (-5, top - 6)  # (the C: beside the first lane, above the bus end)
    # turned to the bus's flow and moved so lane 0's start is where the bus's first lane ends
    first = min(lanes, key=lambda ln: ln["u"])
    ts = busdesign._turn_tile((0, top), k)
    dx, dy = first["start"][0] - ts[0], first["start"][1] - ts[1]

    def to_world(t):
        w = busdesign._turn_tile((t[0] - bx, t[1]), k)
        return w[0] + dx, w[1] + dy
    world = extend.shifted([busdesign._turn(e, k) for e in local], dx, dy)
    cw = busdesign._turn_tile(corner_local, k)
    cw = (cw[0] + dx, cw[1] + dy)  # (the C itself stays upright: only its place turns)
    clash = extend.plan_tiles([svc.decorate(e) for e in world]) & {tuple(t) for t in bus.get("tiles") or []}
    if clash:
        x, y = min(clash)
        raise planner.PlanError(f"at the end of the bus something's in the way (at {x}, {y}: water, a cliff, the bus's "
                                "own belts or a building): plan the bus longer or another way, or clear it")
    out_lanes = [{"item": ln["item"], "end": list(to_world((g[0], g[1] - 1)))} for ln, g in exits]
    if bus.get("end_pole"):  # (power: a pole line from the bus's (or the last tier's) pole to this base's)
        mine = extend.pole_tiles(world)
        taken = extend.plan_tiles([svc.decorate(e) for e in world]) | {tuple(t) for t in bus.get("tiles") or []}
        to = extend.nearest(mine, tuple(bus["end_pole"]))
        run = extend.pole_chain(tuple(bus["end_pole"]), to, taken) if to else None
        if run is None:
            notes.append("power: no pole line found to the bus's poles: wire this base yourself")
        else:
            world += run
    clean = [{key: v for key, v in e.items() if key not in ("type", "w", "h", "fluid", "new")} for e in world]
    rel = extend.shifted(clean, -cw[0], -cw[1])  # (the blueprint's coordinates: the C's corner at 0, 0)
    desc = fitted_description(cw, direction, out_lanes)
    bp = marked(planner.blueprint_string(rel, "starter base (on your bus)", description=desc), (0, 0),
                (cw[0] % GRID, cw[1] % GRID))
    occ = extend.plan_tiles([svc.decorate(e) for e in world])
    x0, y0 = min(t[0] for t in occ), min(t[1] for t in occ)
    box = (x0, y0, max(t[0] for t in occ) - x0 + 1, max(t[1] for t in occ) - y0 + 1, GRID)
    notes.append(f"fitted to the bus: {len(pairs)} lane{'s' if len(pairs) > 1 else ''} run to the base's inputs; "
                 "Place it puts it at the bus's end")
    if spare:
        notes.append("lanes it doesn't need run on past its side, for the next tier: "
                     + " ".join(f"[item={ln['item']}]" for ln in spare))
    if missing:
        notes.append("the bus has too few lanes of " + " ".join(f"[item={i}]" for i in missing)
                     + ": feed those inputs yourself")
    return {"entities": [svc.decorate(e) for e in world], "blueprint": bp, "box": box, "corner": cw,
            "exits": out_lanes, "notes": notes}


def ground_taken(svc, snapshot, buildings=True):
    """the tiles of a snapshot (in game: around the player) a fitted base can't go on: water, cliffs and (buildings)
    buildings and ghosts. Trees and rocks don't count (a paste marks them for removal); without buildings, the last
    tier's base doesn't either (a bigger one is pasted over it)"""
    snap = svc.snapshot_view(snapshot) if snapshot else None
    if not snap:
        return set()
    g = extend.Ground(snap)
    return g.blocked(avoid_ore=False) if buildings else g.obstacles | g.water


def fitted_description(corner, direction, exits):
    """what a later tier needs of a fitted base, in its blueprint's description (it survives the game's export)"""
    lines = [FITTED, f"C at {corner[0]},{corner[1]}; bus flowing {direction}"]
    if exits:
        lines.append("lanes passed on: " + "; ".join(f"{e['item']} at {e['end'][0]},{e['end'][1]}" for e in exits))
    return "\n".join(lines)


def read_fitted(description):
    """fitted_description back -> {corner, direction, lanes: [{item, end}]} or None"""
    import re
    if not (description or "").startswith(FITTED):
        return None
    m = re.search(r"C at (-?\d+),(-?\d+); bus flowing (\w+)", description)
    if not m:
        return None
    lanes = [{"item": i, "end": [int(x), int(y)]}
             for i, x, y in re.findall(r"([\w-]+) at (-?\d+),(-?\d+)", description.split("lanes passed on:")[-1])
             ] if "lanes passed on:" in description else []
    return {"corner": (int(m[1]), int(m[2])), "direction": m[3], "lanes": lanes}


def _corner(b):
    tiles = {(t["position"]["x"], t["position"]["y"]) for t in b.get("tiles") or [] if t.get("name") == TILE}
    return next(((x, y) for x, y in sorted(tiles) if all((x + dx, y + dy) in tiles for dx, dy in C_SHAPE)), None)


def held_base(bp_string):
    """a held blueprint, or the print of a book with the C (else its first: a starter base's book has the connected
    base first, its sections after) -> (entities, the C's top-left in its coordinates or None, that print's string)"""
    bp = base._decode(bp_string)
    if "blueprint_book" in bp:
        prints = [p for p in bp["blueprint_book"].get("blueprints") or [] if "blueprint" in p]
        if not prints:
            raise planner.PlanError("that book has no blueprint in it")
        bp = next((p for p in prints if _corner(p["blueprint"])), prints[0])
        bp = {"blueprint": bp["blueprint"]}
    b = bp.get("blueprint") or {}
    return b.get("entities") or [], _corner(b), base._encode(bp)


def add_tier(svc, params, progress=None, cancel=None):
    """params as a starter base's, with "add_to": the held base's blueprint. -> a base result of what it doesn't
    make yet, beside it, carrying its C"""
    held = params["add_to"]
    old, corner, print_ = held_base(held)
    if not old:
        raise planner.PlanError("hold the blueprint of the base to add to")
    if corner is None:
        raise planner.PlanError("the held blueprint has no stone-brick C: plan its base with bpgen first (main bus)")
    fitted = read_fitted((base._decode(print_).get("blueprint") or {}).get("description"))
    if fitted and not fitted["lanes"]:
        raise planner.PlanError("the held base passes no spare bus lanes on: for the next tier plan a bigger base "
                                "instead (it fits to the same bus end; paste it over the old one, C on C)")
    have = svc.analyze_blueprint(print_).get("science") or {}  # (that print only: a book's sections repeat it)
    data = svc.data
    lab = params.get("lab") or "lab"
    spm = float(params.get("spm") or 30)
    want = base.packs_for(data, lab, params.get("addons"))
    targets = {p: round(spm - have.get(p, 0), 1) for p in want if spm - have.get(p, 0) > 0.5}
    if not targets:
        raise planner.PlanError(f"the held base already makes {spm:g}/min of every pack: ask for more")
    p = {k: v for k, v in params.items() if k not in ("add_to", "spm", "import")}
    p.update(targets=targets, layout="bus", fit_bus=False)
    recipes = data.raw["recipe"]
    if not any((recipes.get(e.get("recipe") or "") or {}).get("category") == "smelting" or svc.decorate(e)["type"] == "furnace"
               for e in old):
        p["plates"] = True  # (the held base smelts nothing: its plates come in at the bus head, and this part's too)
    out = svc.plan_base(p, progress, cancel)
    first = (out.get("sections") or [{}])[0]
    if first.get("kind") != "routed" or (first["result"]["summary"].get("layout") != "bus"):
        raise planner.PlanError("the added part didn't lay out as a main bus: "
                                + (out["summary"].get("route_note") or "try a smaller step"))
    ents, srcs = first["result"]["entities"], first["result"].get("sources") or []
    if fitted:  # (on the lanes the held base passes on, beside it: clear of it and of the bus design)
        from bpgen import busdesign
        cw = fitted["corner"]
        old_world = extend.shifted([svc.decorate({k: v for k, v in e.items() if k in ("name", "position", "direction")})
                                    for e in old], cw[0], cw[1])
        taken = ({tuple(t) for t in (busdesign.load_last(params) or {}).get("tiles") or []} | extend.plan_tiles(old_world)
                 | ground_taken(svc, params.get("snapshot")))
        old_poles = extend.pole_tiles(old_world)
        fit = fit_to_bus(svc, ents, srcs, {"direction": fitted["direction"], "lanes": fitted["lanes"],
                                           "tiles": sorted(taken),
                                           "end_pole": extend.nearest(old_poles, tuple(fitted["lanes"][0]["end"]))},
                         p.get("belt") or "transport-belt", beside=True)
        if not fit:
            raise planner.PlanError("none of the lanes the held base passes on carry what this tier needs")
        sm = dict(out["summary"])
        sm["notes"] = [f"the held base makes {', '.join(f'{k} {v:g}' for k, v in have.items()) or 'no science'}/min: "
                       f"this adds {', '.join(f'{k} {v:g}' for k, v in targets.items())}/min beside it, on the bus "
                       "lanes it passes on"] + fit["notes"]
        return {"params": params, "mode": "base", "blueprint": fit["blueprint"], "text": sm["notes"][0],
                "entities": fit["entities"], "sources": [], "sinks": [], "summary": sm,
                "absolute": {"box": list(fit["box"]), "taps": 0, "fitted": True}}
    new_corner = head_corner(srcs)
    # beside the old base, east, the bus inputs on the old base's row (its C's row)
    old_tiles = extend.plan_tiles([svc.decorate({k: v for k, v in e.items() if k in ("name", "position", "direction")})
                                   for e in old])
    new_tiles = extend.plan_tiles(ents)
    dx = max(t[0] for t in old_tiles) + GAP - min(min(t[0] for t in new_tiles), new_corner[0])
    dy = corner[1] - new_corner[1]
    moved = [{k: v for k, v in e.items() if k not in ("type", "w", "h", "fluid")}
             for e in extend.shifted(ents, dx, dy)]
    label = f"bpgen: add {', '.join(f'{k} {v:g}/min' for k, v in targets.items())}"
    bp = marked(planner.blueprint_string(moved, label, description=first["result"].get("description")), corner,
                _snap_of(print_, corner))  # (snapped as the held base: its C on the old C, wherever that is)
    sm = dict(out["summary"])
    sm["notes"] = [f"the held base makes {', '.join(f'{k} {v:g}' for k, v in have.items()) or 'no science'}/min: "
                   f"this adds {', '.join(f'{k} {v:g}' for k, v in targets.items())}/min beside it (east), "
                   "its bus inputs on the same row; paste it with its C on the old base's C"]
    return {"params": params, "mode": "base", "blueprint": bp, "text": label,
            "entities": [svc.decorate(e) for e in moved],
            "sources": extend.shifted(srcs, dx, dy), "sinks": [], "summary": sm}
