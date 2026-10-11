"""Space platform (Space Age): an inner-planet shuttle around the platform's hub, self-sufficient.

The hub (at 0,0, tiles -4..3 both ways, already there: a blueprint can't hold it) is the store. Machines sit around it,
an inserter between each and the hub (the ring of tiles touching it):

    north:  2 asteroid collectors (an inserter per chunk kind, each only while the hub holds few of it: chunks don't
            stack) and the metallic chunk crusher
    east:   electric furnace (iron plate) and an assembler (firearm magazines); magazines go out on a belt that runs
            round to the turrets along the front
    south:  fuel chemical plant, an ice melter feeding it water directly, the oxide chunk crusher (ice)
    west:   oxidizer chemical plant, a second melter feeding it, the carbonic chunk crusher (carbon)

Where the fuel and oxidizer take a gas made from nothing (Krastorio 2: hydrogen and oxygen out of an atmospheric
condenser, which works in space), the condensers stand in for the melters: two for hydrogen under the fuel plant (it
lies with its outputs to the hub, its inputs down), one for oxygen above the oxidizer plant. No ice is needed then, so
no oxide crusher, and the collectors only take metallic and carbonic chunks.

What goes into the hub runs only while the hub holds less than its share (wired to the hub when built: the mod reads
the "bpgen_hub" tag), so no item crowds out the others; the collectors take only the chunks the hub is short of (a
decider reading the hub sets their filters). Fuel and oxidizer run in pipes to the thrusters at the back (fuel into
each one's left side, oxidizer into its right; their headers cross with pipes-to-ground). Solar panels
behind, enough for the machines at the orbit's sunlight; foundation under all of it.
"""
import math

from bpgen.planner import EAST, NORTH, SOUTH, WEST, PIPE, POLE, UNDERGROUND, PlanError

HUB_CAPS = {"chunk": 4, "ore": 400, "carbon": 200, "ice": 200, "plate": 200, "ammo": 200}
PITCH = 7  # thrusters: 4 wide, their pipes either side and a tile between


def _recipe_for(data, product, category=None, ingredient=None):
    """the recipe making `product` (in `category`, from `ingredient` when given): the one named like it first"""
    best = None
    for rn, r in data.raw["recipe"].items():
        if r.get("hidden") or (category and r.get("category", "crafting") != category):
            continue
        if not any(x.get("name") == product for x in r.get("results") or []):
            continue
        ings = [i.get("name") for i in r.get("ingredients") or []]
        if ingredient and ingredient not in ings:
            continue
        key = (rn != product, len(ings), len(r.get("results") or []), len(rn), rn)  # (the plain one, not "advanced-")
        if best is None or key < best[0]:
            best = (key, rn)
    return best[1] if best else None


def _condenser(data, fluids):
    """(machine, recipe) making the one fluid in `fluids` from nothing, in a 5x5 machine with pipes either side of
    each edge's middle (Krastorio 2's atmospheric condenser), or None"""
    if len(fluids) != 1:
        return None
    raw = data.raw
    found = []
    for rn, r in sorted(raw["recipe"].items()):
        if r.get("hidden") or r.get("ingredients") or not any(
                x.get("name") == fluids[0] for x in r.get("results") or []):
            continue
        for mn, m in sorted(raw.get("assembling-machine", {}).items()):
            box = m.get("collision_box") or [[0, 0], [0, 0]]
            if r.get("category", "crafting") not in (m.get("crafting_categories") or []) or round(box[1][0] * 2) != 5:
                continue
            at = {tuple(c.get("position") or ()) for fb in m.get("fluid_boxes") or []
                  for c in fb.get("pipe_connections") or []}
            if {(-1, -2), (1, -2), (-1, 2), (1, 2), (2, -1), (2, 1)} <= at:  # (a powered one, the plain name, first)
                found.append(((m.get("energy_source") or {}).get("type") != "electric", len(mn), mn, rn))
    return min(found)[2:] if found else None

def _usage_w(proto):
    """an entity's power draw in watts (its energy_usage, e.g. "540kW")"""
    v = str(proto.get("energy_usage") or "0W")
    num = float("".join(c for c in v if c.isdigit() or c == ".") or 0)
    return num * {"k": 1e3, "M": 1e6, "G": 1e9}.get(v.rstrip("WJ")[-1:], 1)


def plan_ship(data, thrusters=2, turrets=6, location="nauvis", inserter="fast-inserter", belt="transport-belt",
              foundation=None):
    """-> dict(entities (world positions round the hub at 0,0), tiles (foundation), notes, power)"""
    raw = data.raw
    need = {"asteroid-collector": "asteroid-collector", "thruster": "thruster", "electric-furnace": "furnace",
            "chemical-plant": "assembling-machine", "crusher": "assembling-machine",
            "assembling-machine-2": "assembling-machine", "gun-turret": "ammo-turret", "solar-panel": "solar-panel"}
    missing = [n for n, t in need.items() if n not in raw.get(t, {}) and n not in raw.get("assembling-machine", {})]
    if missing:
        raise PlanError(f"this game has no {', '.join(missing)}: the ship needs Space Age")
    crush = {k: _recipe_for(data, item, "crushing", chunk) for k, item, chunk in (
        ("metallic", "iron-ore", "metallic-asteroid-chunk"), ("carbonic", "carbon", "carbonic-asteroid-chunk"),
        ("oxide", "ice", "oxide-asteroid-chunk"))}
    melt = _recipe_for(data, "water", ingredient="ice")
    fuel = _recipe_for(data, "thruster-fuel")
    ox = _recipe_for(data, "thruster-oxidizer")
    ammo = _recipe_for(data, "firearm-magazine")
    for k, r in {**crush, "fuel": fuel, "oxidizer": ox, "ammo": ammo}.items():
        if not r and k != "oxide":
            raise PlanError(f"no recipe for the ship's {k}")
    fluids = {rn: [i["name"] for i in raw["recipe"][rn].get("ingredients") or [] if i.get("type") == "fluid"]
              for rn in (fuel, ox)}
    gas = None  # {fuel: (machine, recipe), oxidizer: ...} when both are condensed out of nothing
    if fluids[fuel] != ["water"] or fluids[ox] != ["water"]:
        gas = {k: _condenser(data, fluids[rn]) for k, rn in (("fuel", fuel), ("oxidizer", ox))}
        if not all(gas.values()):
            raise PlanError(f"thruster fuel here takes {', '.join(fluids[fuel])}, oxidizer {', '.join(fluids[ox])}: "
                            "only water from ice, or a gas a 5x5 machine makes from nothing, is laid out")
    elif not melt or not crush["oxide"]:
        raise PlanError("no recipe for the ship's " + ("ice melting" if not melt else "oxide crushing"))
    ents = []

    def put(name, x, y, d=None, **kw):
        e = {"name": name, "position": {"x": x, "y": y}}
        if d is not None:
            e["direction"] = d
        e.update(kw)
        ents.append(e)
        return e

    def gate(item, cap):  # (runs while the hub holds fewer than cap: wired to the hub when built)
        return {"tags": {"bpgen_hub": True}, "control_behavior": {"circuit_enabled": True, "circuit_condition": {
            "first_signal": {"name": item, "type": "item"}, "constant": cap, "comparator": "<"}}}

    def ins(x, y, d, item=None, cap=None, only=None):  # an inserter on tile (x, y), picking up from direction d
        kw = gate(item, cap) if item else {}
        if only:
            kw.update(use_filters=True, filters=[{"index": 1, "name": only, "quality": "normal", "comparator": "="}])
        return put(inserter, x + 0.5, y + 0.5, d, **kw)

    chunks = ("metallic-asteroid-chunk", "carbonic-asteroid-chunk", "oxide-asteroid-chunk")
    # north: two collectors, an inserter a chunk kind each; the metallic crusher
    if gas:
        chunks = chunks[:2]  # (no ice wanted: the collectors leave oxide chunks be)
    for i, x0 in enumerate((-4, -1)):
        put("asteroid-collector", x0 + 1.5, -6.5, NORTH, wire_id=f"collector{i}",
            bp_extra={"control_behavior": {"circuit_set_filters": True}})
        for dx, c in enumerate(chunks):
            ins(x0 + dx, -5, NORTH, c, HUB_CAPS["chunk"], only=c)
    # the collectors take only the chunks the hub is short of (else one kind fills them and the others stop coming):
    # a decider reading the hub (wired to it as it's built) plus a constant -cap a chunk sends each one short; a
    # chunk that never comes this way (promethium) keeps the filter from going empty, which would take everything
    want = [{"index": i + 1, "name": c, "quality": "normal", "comparator": "=", "count": -HUB_CAPS["chunk"]}
            for i, c in enumerate(chunks)]
    if "promethium-asteroid-chunk" in raw.get("asteroid-chunk", {}) or "promethium-asteroid-chunk" in raw.get("item", {}):
        want.append({"index": len(want) + 1, "name": "promethium-asteroid-chunk", "quality": "normal",
                     "comparator": "=", "count": -1})
    put("constant-combinator", 5.5, -7.5, wire_id="chunk_want",
        bp_extra={"control_behavior": {"sections": {"sections": [{"index": 1, "filters": want}]}}})
    each = {"type": "virtual", "name": "signal-each"}
    put("decider-combinator", 4.5, -7, NORTH, tags={"bpgen_hub": True},
        wires_to=[("chunk_want", 2, 2), ("collector0", 4, 2), ("collector1", 4, 2)],
        bp_extra={"control_behavior": {"decider_conditions": {
            "conditions": [{"first_signal": each, "constant": 0, "comparator": "<"}],
            "outputs": [{"signal": each, "copy_count_from_input": False, "constant": 1}]}}})
    put("crusher", 3, -6.5, NORTH, recipe=crush["metallic"])
    ins(2, -5, SOUTH)
    ins(3, -5, NORTH, "iron-ore", HUB_CAPS["ore"])
    # east: furnace, magazines, the magazines' way out
    if "electric-furnace" in raw.get("furnace", {}):
        put("electric-furnace", 6.5, -2.5)
    else:  # (an assembler here, Krastorio 2: it wants its recipe set)
        put("electric-furnace", 6.5, -2.5, recipe=_recipe_for(data, "iron-plate", ingredient="iron-ore"))
    ins(4, -4, WEST)
    ins(4, -2, EAST, "iron-plate", HUB_CAPS["plate"])
    put("assembling-machine-2", 6.5, 0.5, recipe=ammo)
    ins(4, -1, WEST)
    ins(4, 1, EAST, "firearm-magazine", HUB_CAPS["ammo"])
    ins(4, 3, WEST, only="firearm-magazine")  # (onto the turrets' belt)
    if gas:
        # south: fuel plant with its outputs to the hub's edge, two hydrogen condensers under it (straight in)
        put("chemical-plant", -2.5, 6.5, SOUTH, recipe=fuel)
        ins(-3, 4, NORTH)
        for cx in (-2.5, 2.5):
            put(gas["fuel"][0], cx, 10.5, recipe=gas["fuel"][1])
        # west: oxidizer plant, its oxygen condenser above it
        put("chemical-plant", -6.5, -2.5, NORTH, recipe=ox)
        ins(-5, -3, EAST)
        put(gas["oxidizer"][0], -6.5, -6.5, recipe=gas["oxidizer"][1])
    else:
        # south: fuel plant with its melter (water straight across), the oxide crusher
        put("chemical-plant", -2.5, 6.5, EAST, recipe=fuel)
        ins(-3, 4, NORTH)
        put("chemical-plant", 0.5, 6.5, EAST, recipe=melt)
        ins(0, 4, NORTH)
        put("crusher", 3, 6.5, NORTH, recipe=crush["oxide"])
        ins(2, 4, NORTH)
        ins(3, 4, SOUTH, "ice", HUB_CAPS["ice"])
        # west: oxidizer plant with its melter below it
        put("chemical-plant", -6.5, -2.5, SOUTH, recipe=ox)
        ins(-5, -3, EAST)
        put("chemical-plant", -6.5, 0.5, SOUTH, recipe=melt)
        ins(-5, 0, EAST)
    # (the carbonic crusher, lying down)
    put("crusher", -6.5, 3, EAST, recipe=crush["carbonic"])
    ins(-5, 2, EAST)
    ins(-5, 3, WEST, "carbon", HUB_CAPS["carbon"])
    # thrusters at the back: fuel header (row fy) into each one's left, oxidizer header (row oy) into its right
    sh = 4 if gas else 0  # (below the condensers)
    oy, fy, ty = 10 + sh, 12 + sh, 16 + sh
    fx = -7 if gas else -5  # (the fuel's way down: clear of the condensers' west pipes)
    cxs = [-2 + PITCH * i for i in range(thrusters)]
    for cx in cxs:
        put("thruster", cx, ty + 0.5, NORTH)
    pipes = set()
    if gas:  # (fuel: out of its plant's top at (-4, 4), west, down; oxidizer: out of its plant's bottom, west, down)
        pipes |= {(-4, 4), (-5, 4), (-6, 4)} | {(fx, y) for y in range(4, fy + 1)}
        pipes |= {(-8, -1)} | {(-9, y) for y in range(-1, oy + 1)}
    else:  # (fuel: down from the fuel plant's outputs at (-5, 5), (-5, 7); oxidizer: out of its plant's top, west, down)
        pipes |= {(fx, y) for y in range(5, fy + 1)}
        pipes |= {(x, -5) for x in range(-8, -5)} | {(-9, y) for y in range(-5, oy + 1)}
    ugs = []  # (x, y, direction of its plain side)
    last_fuel = cxs[-1] - 3
    for x in range(fx, last_fuel + 1):  # fuel header
        pipes.add((x, fy))
    for i, cx in enumerate(cxs):
        for y in range(fy + 1, ty - 1):
            pipes.add((cx - 3, y))
        for y in range(oy, ty - 1):  # (the oxidizer's way down, over the fuel header)
            pipes.add((cx + 2, y))
        if i < len(cxs) - 1:  # (the fuel header goes on under it)
            pipes -= {(cx + 1, fy), (cx + 2, fy), (cx + 3, fy)}
            pipes.add((cx + 2, fy))  # (the oxidizer's own)
            ugs += [(cx + 1, fy, WEST), (cx + 3, fy, EAST)]
    for x in range(-9, cxs[-1] + 3):  # oxidizer header, under the fuel's way down
        if x not in (fx - 1, fx, fx + 1):
            pipes.add((x, oy))
    pipes.add((fx, oy))  # (the fuel column itself)
    ugs += [(fx - 1, oy, WEST), (fx + 1, oy, EAST)]
    for x, y in sorted(pipes):
        put(PIPE, x + 0.5, y + 0.5)
    for x, y, d in ugs:
        put(UNDERGROUND, x + 0.5, y + 0.5, d)
    # turrets down the right flank, their magazines on a belt from the hub (east, then up beside them); nothing can
    # stand in front of the collectors (they reach out over open space) or behind the thrusters
    east = 8
    for x in range(5, east):
        put(belt, x + 0.5, 3.5, EAST)
    top = min(-8, 3 - 2 * turrets + 1)
    for y in range(top, 4):
        put(belt, east + 0.5, y + 0.5, NORTH)
    for i in range(turrets):
        y = top + 2 * i
        put("gun-turret", east + 3, y + 1)
        ins(east + 1, y, WEST)
    # power: what it all draws, met by solar panels at this orbit's sunlight
    draw = sum(_usage_w(next((raw[t][e["name"]] for t in ("assembling-machine", "furnace", "inserter",
                                                          "asteroid-collector") if e["name"] in raw.get(t, {})), {}))
               for e in ents)
    loc = (raw.get("planet") or {}).get(location) or (raw.get("space-location") or {}).get(location) or {}
    sun = (loc.get("solar_power_in_space") or 100) / 100
    panel = raw["solar-panel"]["solar-panel"]
    per = _usage_w({"energy_usage": panel.get("production") or "60kW"}) * sun
    n_panels = max(1, math.ceil(draw * 1.15 / per))
    cols = max(2, math.ceil(math.sqrt(n_panels)))
    sx0, sy0 = east + 5, top
    panels = []
    for k in range(n_panels):
        r, c = divmod(k, cols)
        x, y = sx0 + c * 3 + (c // 2), sy0 + r * 3  # (a gap column every two for the poles)
        panels.append((x, y))
        put("solar-panel", x + 1.5, y + 1.5)
    # poles: on free tiles of a grid, wherever something powered is out of reach
    taken = set()
    sizes = {"asteroid-collector": (3, 3), "crusher": (2, 3), "chemical-plant": (3, 3), "electric-furnace": (3, 3),
             "assembling-machine-2": (3, 3), "thruster": (4, 5), "gun-turret": (2, 2), "solar-panel": (3, 3),
             "decider-combinator": (1, 2)}
    if gas:
        sizes.update({m: (5, 5) for m, _ in gas.values()})
    for e in ents:
        w, h = sizes.get(e["name"], (1, 1))
        if e.get("direction") in (EAST, WEST) and e["name"] == "crusher":
            w, h = h, w
        x0, y0 = math.floor(e["position"]["x"] - w / 2 + 0.01), math.floor(e["position"]["y"] - h / 2 + 0.01)
        e["_tiles"] = [(x0 + i, y0 + j) for i in range(w) for j in range(h)]
        taken |= set(e["_tiles"])
    taken |= {(x, y) for x in range(-4, 4) for y in range(-4, 4)}  # (the hub)
    powered = [e for e in ents if e["name"] not in (PIPE, UNDERGROUND, belt, "gun-turret", "thruster")]
    poles = []
    # (substations in the free corners by the hub: the inserters lining it are out of a medium pole's reach)
    subs = []
    if "substation" in raw.get("electric-pole", {}):
        for sx, sy in ((4, -6), (4, 4), (-7, -7)):
            sq = {(sx, sy), (sx + 1, sy), (sx, sy + 1), (sx + 1, sy + 1)}
            if not sq & taken:
                taken |= sq
                subs.append((sx, sy))
                put("substation", sx + 1, sy + 1)

    def reaches(p, e):  # (a medium pole: 3 tiles each way of its own; a substation 9 from its middle)
        if p in subs:
            return any(p[0] - 8 <= tx_ <= p[0] + 9 and p[1] - 8 <= ty_ <= p[1] + 9 for tx_, ty_ in e["_tiles"])
        return any(abs(p[0] - tx_) <= 3 and abs(p[1] - ty_) <= 3 for tx_, ty_ in e["_tiles"])
    poles += subs


    def covered(e):
        return any(reaches(p, e) for p in poles)
    # each powered thing reached: the free tile reaching the most still unreached ones, nearest the poles so far
    for _ in range(len(powered)):
        left = [e for e in powered if not covered(e)]
        if not left:
            break
        e = left[0]
        cands = {(tx_ + dx, ty_ + dy) for tx_, ty_ in e["_tiles"] for dx in range(-3, 4) for dy in range(-3, 4)} - taken
        if not cands:
            break
        best = max(cands, key=lambda p: (sum(reaches(p, o) for o in left),
                                         -min(((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 for q in poles), default=0)))
        poles.append(best)
        taken.add(best)
    # then one network: groups of poles out of wire reach joined by poles on free tiles in between
    reach2 = 81
    for _ in range(40):
        group = {poles[0]} if poles else set()
        grew = True
        while grew:
            more = {p for p in poles if p not in group and any(
                (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 <= (324 if p in subs and q in subs else reach2) for q in group)}
            group |= more
            grew = bool(more)
        rest = [p for p in poles if p not in group]
        if not rest:
            break
        a_, b_ = min(((a_, b_) for a_ in group for b_ in rest),
                     key=lambda ab: (ab[0][0] - ab[1][0]) ** 2 + (ab[0][1] - ab[1][1]) ** 2)
        mid = ((a_[0] + b_[0]) // 2, (a_[1] + b_[1]) // 2)  # (a free tile near halfway)
        spot = min(({(mid[0] + dx, mid[1] + dy) for dx in range(-3, 4) for dy in range(-3, 4)} - taken),
                   key=lambda p: (p[0] - mid[0]) ** 2 + (p[1] - mid[1]) ** 2, default=None)
        if spot is None:
            break
        poles.append(spot)
        taken.add(spot)
    uncovered = [e["name"] for e in powered if not covered(e)]
    for x, y in poles:
        if (x, y) not in subs:
            put(POLE, x + 0.5, y + 0.5)
    for e in ents:
        e.pop("_tiles", None)
    # the floor: a foundation tile under everything (and a tile round it), the hub's own left as it is
    tile = foundation or next((n for n, t in raw.get("tile", {}).items() if t.get("is_foundation")
                               and "platform" in n), "space-platform-foundation")
    floor = {(x + dx, y + dy) for x, y in taken for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    floor -= {(x, y) for x in range(-4, 4) for y in range(-4, 4)}  # (the hub's own: there already, under it)
    for x0 in (-4, -1):  # (open space where the collectors reach out: their front row and four rows ahead)
        floor -= {(x0 + i, y) for i in range(3) for y in range(-12, -7)}
    for cx in cxs:  # (and behind the thrusters)
        floor -= {(x, y) for x in range(cx - 2, cx + 2) for y in range(ty + 3, ty + 4)}
    tiles = [{"name": tile, "position": {"x": x, "y": y}} for x, y in sorted(floor)]
    notes = [f"{thrusters} thrusters, {turrets} gun turrets, {n_panels} solar panels ({draw / 1e6:.1f} MW at "
             f"{sun:.0%} sunlight in {location}'s orbit), {len(tiles)} foundation tiles",
             "paste it centred on the hub: everything feeding the hub is wired to it as it's built"]
    if gas:
        notes.append(f"fuel from {gas['fuel'][1]}, oxidizer from {gas['oxidizer'][1]} ({gas['fuel'][0]}s): no ice")
    if uncovered:
        notes.append("not reached by a pole: " + ", ".join(sorted(set(uncovered))))
    return {"entities": ents, "tiles": tiles, "notes": notes, "power": draw, "panels": n_panels}
