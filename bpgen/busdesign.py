"""Bus design: from ore patches picked in game to a main bus. Drills cover each patch, their belts gather into trunk
belts (one full belt each), the trunks run to a smelter column apiece at the bus head, and the columns' plates (or a
raw item, like coal, as it comes) feed the bus: lanes in groups (4 belts, 4 tiles free, repeat), a 4-to-4 balancer
at the start of each full group of 4.

Mining, per patch (world tiles), in bands that each fill one trunk:

  D B D   D B D      D: drill (3x3) facing the belt B between them; P: pole (in the drills' columns)
  P | P   P | P      the drills above the header row send their ore south, the ones below north: they side-load
  ->->->->->->->->   onto the header's two lanes, and the header leaves the patch toward the bus head as the trunk
  P | P   P | P
  D B D   D B D

The bus head is laid out flowing north, then turned to the chosen direction and put on free ground near the player:

  |||| ||||          bus lanes, groups of `group` with `gap` free tiles between
  [bal] [bal]        y 0..-6: the balancers (a group that isn't 4 full lanes: straight belts)
  ^^^^^^^^^^         y 1..R: the funnel, each column's output belt turning onto its lane
  [col][col][col]    smelter columns (ore belt in the middle, furnaces both sides, two output belts merged at the top)
"""
import math

from bpgen import chain, extend, planner, router
from bpgen.planner import EAST, NORTH, POLE, SOUTH, WEST, PlanError

DIR_VEC = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}
TURNS = {"north": 0, "east": 1, "south": 2, "west": 3}
MIN_ORE = 3  # a drill goes where its mining area has at least this many ore tiles
COL_GAP = 2  # free tiles between two smelter columns
WOOD = "wood"  # (a lane of its own, filled by hand from a chest: from trees, no patch to mine; it fuels its inserter)
CHEST, CHEST_INSERTER = "wooden-chest", "burner-inserter"
# (bpgen's copy of the mods' data has no mining drills until the game sends it again: vanilla's, then)
ELECTRIC_DRILL = {"name": "electric-mining-drill", "collision_box": [[-1.4, -1.4], [1.4, 1.4]], "mining_speed": 0.5,
                  "resource_searching_radius": 2.49, "resource_categories": ["basic-solid"]}


def ent(name, x, y, d=None, **kw):
    e = {"name": name, "position": {"x": x, "y": y}}
    if d is not None:
        e["direction"] = d
    e.update(kw)
    return e


# ---- the bus head (local frame: the bus flows north, y decreasing) ----

def balancer(bx, splitter, belt, ug):
    """4-to-4 balancer, inputs at columns bx..bx+3 entering row 0 from the south, outputs at bx+1..bx+4 leaving row
    -6 northward. Splitters (0,1)(2,3); lane 1 crosses east under lanes 2 and 3 (which dive north under it) to
    column 4 and lane 0 steps east to column 1: the order is now (P Q Q P), each splitter of (1,2)(3,4) gets one of
    each first-stage pair."""
    out = []

    def b(c, r, d=NORTH):
        out.append(ent(belt, bx + c + 0.5, -r + 0.5, d))

    def u(c, r, d, kind):
        out.append(ent(ug, bx + c + 0.5, -r + 0.5, d, ug_type=kind))

    out.append(ent(splitter, bx + 1.0, 0.5, NORTH))
    out.append(ent(splitter, bx + 3.0, 0.5, NORTH))
    b(0, 1), b(1, 1), u(2, 1, NORTH, "input"), u(3, 1, NORTH, "input")
    b(0, 2), b(1, 2, EAST), u(2, 2, EAST, "input"), u(3, 2, EAST, "output"), b(4, 2)
    b(0, 3), u(2, 3, NORTH, "output"), u(3, 3, NORTH, "output"), b(4, 3)
    b(0, 4, EAST), b(1, 4), b(2, 4), b(3, 4), b(4, 4)
    out.append(ent(splitter, bx + 2.0, -5 + 0.5, NORTH))
    out.append(ent(splitter, bx + 4.0, -5 + 0.5, NORTH))
    for c in range(1, 5):
        b(c, 6)
    return out


def smelter_column(x0, top, n, furnace, inserter, belt, recipe=None):
    """furnaces both sides of an ore belt, n a side, from row `top` down (3 rows each, furnace 3x3). The west
    output belt (column x0) leaves north from row top-2; the east one turns west along row top-1 and side-loads
    onto it (each takes one lane: inserters drop on the far lane). -> (entities, ore goal tile: the ore belt's
    bottom, entered moving north)"""
    out = []
    bottom = top + 3 * n - 1
    for i in range(n):
        r = top + 3 * i
        kw = {"recipe": recipe} if recipe else {}  # (a furnace that is an assembling machine in this mod set)
        out.append(ent(furnace, x0 + 3.5, r + 1.5, **kw))
        out.append(ent(furnace, x0 + 9.5, r + 1.5, **kw))
        out.append(ent(inserter, x0 + 5.5, r + 1.5, EAST))   # picks from the ore belt (east), into the furnace
        out.append(ent(inserter, x0 + 7.5, r + 1.5, WEST))
        out.append(ent(inserter, x0 + 1.5, r + 1.5, EAST))   # picks from the furnace, onto the output belt
        out.append(ent(inserter, x0 + 11.5, r + 1.5, WEST))
        for px in (1, 5, 11):
            out.append(ent(POLE, x0 + px + 0.5, r + 0.5))
    for y in range(top + 1, bottom + 1):  # (the ore belt stops a row short: it mustn't feed the merge row)
        out.append(ent(belt, x0 + 6.5, y + 0.5, NORTH))
    for y in range(top - 1, bottom + 1):
        out.append(ent(belt, x0 + 0.5, y + 0.5, NORTH))
    for y in range(top, bottom + 1):
        out.append(ent(belt, x0 + 12.5, y + 0.5, NORTH))
    for x in range(1, 13):
        out.append(ent(belt, x0 + x + 0.5, top - 1 + 0.5, WEST))
    return out, (x0 + 6, bottom)


def lane_layout(items, group, gap):
    """items: [(item, lanes)] -> [{"item", "x" (bus column), "in" (input column at row 0), "group", "balanced"}];
    each item starts a group of its own, a group holds up to `group` lanes"""
    lanes, base = [], 0
    for item, n in items:
        while n > 0:
            k = min(n, group)
            bal = k == group == 4
            for j in range(k):
                lanes.append({"item": item, "x": base + j, "in": base + j - (1 if bal else 0), "group": base,
                              "balanced": bal})
            n -= k
            base += group + gap
    return lanes


def head(lanes, columns, length, names, balance=True):
    """the bus head, local frame. columns: one per lane, in lane order: {"kind": "smelt"|"raw", "n": furnaces a side,
    "item"...}. -> (entities, goals: per column (tile, direction) its trunk enters)"""
    belt, ug, splitter, furnace, inserter = names
    ents = []
    # bus lanes, and the balancers (or straight belts) under them
    groups = {}
    for ln in lanes:
        groups.setdefault(ln["group"], []).append(ln)
    for base, gl in groups.items():
        if balance and gl[0]["balanced"]:
            ents += balancer(base - 1, splitter, belt, ug)
        else:
            pair = balance and len(gl) == 2  # (two lanes: one splitter balances them)
            if pair:
                ents.append(ent(splitter, base + 1.0, 0.5, NORTH))
            for ln in gl:
                ln["in"] = ln["x"]
                for y in range(-6, 0 if pair else 1):
                    ents.append(ent(belt, ln["x"] + 0.5, y + 0.5, NORTH))
        for ln in gl:
            for y in range(-6 - length, -6):
                ents.append(ent(belt, ln["x"] + 0.5, y + 0.5, NORTH))
    # columns side by side, the middle one under its lane
    widths = [13 if c["kind"] == "smelt" else 1 for c in columns]
    xs, x = [], 0
    for w in widths:
        xs.append(x)
        x += w + COL_GAP
    mid = len(columns) // 2
    shift = lanes[mid]["in"] - xs[mid]
    xs = [x + shift for x in xs]
    left = [k for k in range(len(columns)) if xs[k] < lanes[k]["in"]]
    right = [k for k in range(len(columns)) if xs[k] > lanes[k]["in"]]
    if any(k > mid for k in left) or any(k < mid for k in right):
        raise PlanError("the bus is wider than its smelter columns: use a smaller gap between lane groups")
    turn = {k: 1 + i for i, k in enumerate(left)}
    turn.update({k: 1 + i for i, k in enumerate(reversed(right))})
    rows = max([len(left), len(right), 0])
    top = rows + 3  # the columns' first furnace row; their output belts leave at row rows+1
    goals = []
    for k, c in enumerate(columns):
        x0, xin = xs[k], lanes[k]["in"]
        if c["kind"] == "smelt":
            ce, goal = smelter_column(x0, top, c["n"], furnace, inserter, belt, c.get("recipe"))
            ents += ce
            goals.append((goal, NORTH))
        elif c["kind"] == "chest":  # (a lane filled by hand: a chest at its head, a burner inserter onto the belt)
            for y in range(top - 1, top + 3):
                ents.append(ent(belt, x0 + 0.5, y + 0.5, NORTH))
            ents.append(ent(CHEST_INSERTER, x0 + 0.5, top + 3.5, SOUTH))
            ents.append(ent(CHEST, x0 + 0.5, top + 4.5))
            goals.append(None)
        else:
            for y in range(top - 1, top + 3):
                ents.append(ent(belt, x0 + 0.5, y + 0.5, NORTH))
            goals.append(((x0, top + 2), NORTH))
        yk = turn.get(k, 1)
        for y in range(yk + 1, rows + 2):  # up from the column to its turn row
            ents.append(ent(belt, x0 + 0.5, y + 0.5, NORTH))
        if x0 != xin:
            step = 1 if xin > x0 else -1
            for x in range(x0, xin, step):
                ents.append(ent(belt, x + 0.5, yk + 0.5, EAST if step > 0 else WEST))
        for y in range(1, yk + 1):  # up the lane to the balancer's input
            ents.append(ent(belt, xin + 0.5, y + 0.5, NORTH))
    return ents, goals


# ---- mining ----

def _drill_ok(ore, cx, cy, r):
    """ore tiles in the mining area of a drill whose top-left tile is (cx, cy) (3x3)"""
    n = 0
    for x in range(cx + 1 - r, cx + 2 + r):
        for y in range(cy + 1 - r, cy + 2 + r):
            n += (x, y) in ore
    return n >= MIN_ORE


def band(ore, ya, yb, x0, x1, drill, belt, toward_east, r):
    """drills, collector belts, poles and the header for rows ya..yb of a patch. -> (entities, drills, header end:
    the trunk's first tile, its direction) or None (no drill fits)"""
    H = (ya + yb) // 2
    ents, n, xs = [], 0, []
    cxs = list(range(x0 - 1, x1 + 1, 7))
    for cx in cxs:
        got = False
        for sgn in (-1, 1):  # -1: above the header (drills send south), 1: below (north)
            rows = []  # drill top rows, nearest the header first
            poles = [H + sgn]
            cur = H + 2 * sgn  # the header-side row of the next drill
            fits = True
            while fits:  # two drills, a pole row, two drills...
                for _ in range(2):
                    t = cur - 2 if sgn < 0 else cur
                    if t < ya or t + 2 > yb:
                        fits = False
                        break
                    rows.append(t)
                    cur += 3 * sgn
                if fits:
                    poles.append(cur)
                    cur += sgn
            placed = []
            for t in rows:
                for dx, d in ((0, EAST), (4, WEST)):
                    if _drill_ok(ore, cx + dx, t, r):
                        ents.append(ent(drill, cx + dx + 1.5, t + 1.5, d))
                        placed.append(t)
                        n += 1
            if not placed:
                continue
            got = True
            far = min(placed) if sgn < 0 else max(placed) + 2
            for y in (range(far, H) if sgn < 0 else range(H + 1, far + 1)):
                ents.append(ent(belt, cx + 3.5, y + 0.5, SOUTH if sgn < 0 else NORTH))
            for p in poles:
                if (sgn < 0 and p >= far - 1) or (sgn > 0 and p <= far + 1):
                    ents.append(ent(POLE, cx + 1.5, p + 0.5))
                    ents.append(ent(POLE, cx + 5.5, p + 0.5))
        if got:
            xs.append(cx + 3)
    if not xs:
        return None
    # header-side poles across the band's empty columns too, so the wires reach from one column to the next
    have = {(e["position"]["x"], e["position"]["y"]) for e in ents if e["name"] == POLE}
    for cx in range(min(xs) - 3, max(xs) - 2, 7):
        for p in (H - 1, H + 1):
            for px in (cx + 1.5, cx + 5.5):
                if (px, p + 0.5) not in have:
                    ents.append(ent(POLE, px, p + 0.5))
    lo, hi = min(xs) - 1, max(xs) + 1
    d = EAST if toward_east else WEST
    for x in range(lo, hi + 1):
        ents.append(ent(belt, x + 0.5, H + 0.5, d))
    start = (hi + 1, H) if toward_east else (lo - 1, H)
    return ents, n, (start, d)


def mine_patch(ore, drill, belt, rate, cap, toward_east, r):
    """bands over the patch, as many as it takes for each header to stay within one belt. -> [(entities, drills,
    (start, dir))]"""
    x0, x1 = min(t[0] for t in ore), max(t[0] for t in ore)
    y0, y1 = min(t[1] for t in ore), max(t[1] for t in ore)
    nb = 1
    while nb <= 12:
        h = (y1 - y0 + 1) / nb
        bands = []
        for i in range(nb):
            ya, yb = y0 + round(i * h), y0 + round((i + 1) * h) - 1
            if yb - ya < 4:
                continue
            got = band(ore, ya, yb, x0, x1, drill, belt, toward_east, r)
            if got:
                bands.append(got)
        if all(b[1] * rate <= cap * 1.001 for b in bands):
            return bands
        nb += 1
    return bands


# ---- the whole design ----

def smelt_recipe(data, ore, fp):
    """the smelting recipe taking only this ore -> (recipe, ingredient amount, result item, result amount) or None;
    one named after its result first (iron-plate, not a mod's variant)"""
    cats = set(fp.get("crafting_categories") or [])
    found = []
    for name, rc in data.raw["recipe"].items():
        ing, res = rc.get("ingredients") or [], rc.get("results") or []
        if (rc.get("category", "crafting") in cats and len(ing) == 1 and ing[0].get("name") == ore
                and len(res) == 1 and res[0].get("type", "item") == "item" and not rc.get("hidden")):
            found.append((name != res[0]["name"], name, ing[0]["amount"], res[0]["name"], res[0].get("amount", 1)))
    return min(found)[1:] if found else None


def _last_file():
    from bpgen.config import PATHS
    return PATHS["script_output"] / "bpgen" / "bus_design.json"


def save_last(out, params):
    """the planned bus's lane ends (world tiles), for a starter base to fit its head to (tiers.fit_to_bus)"""
    import json
    f = _last_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"direction": params.get("direction") or "north", "surface": params.get("surface"),
                             "lanes": out.get("lanes") or [],
                             # (what it covers: a base fitted to it mustn't land on its trunks)
                             "tiles": sorted(extend.plan_tiles(out.get("entities") or []))}), encoding="utf-8")


def load_last():
    """the last bus design's lanes {direction, lanes: [{item, x, rate, end}]}, or None"""
    import json
    try:
        return json.loads(_last_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _turn(e, k):
    """an entity of the local frame turned k quarter turns clockwise about the origin"""
    x, y = e["position"]["x"], e["position"]["y"]
    for _ in range(k):
        x, y = -y, x
    out = dict(e, position={"x": x, "y": y})
    if "direction" in e:
        out["direction"] = (e["direction"] + 4 * k) % 16
    return out


def _turn_tile(t, k):
    x, y = t[0] + 0.5, t[1] + 0.5
    for _ in range(k):
        x, y = -y, x
    return math.floor(x), math.floor(y)


def plan(svc, params):
    """params: snapshot (an area around the patches and the player), patches ([[x1, y1, x2, y2]] picked areas),
    origin {x, y} (the bus head goes near it), direction (north|east|south|west: the bus flows that way), belt,
    drill, furnace, inserters, group, gap, balance, length -> dict(entities (world), notes, inputs, box, blueprint)"""
    data = svc.data
    snap = svc.snapshot_view(params.get("snapshot"))
    if not snap:
        raise PlanError("no snapshot of the area: pick the ore patches again")
    patches = params.get("patches") or []
    if not patches:
        raise PlanError("pick at least one ore patch (the \"Pick ore patches\" button)")
    ground = extend.Ground(snap)
    belt = params.get("belt") or "transport-belt"
    ug, max_ug = chain.related_belts(data, belt)
    splitter = chain.related_splitter(data, belt)
    cap = planner.belt_capacity(data, belt)
    drill = params.get("drill") or "electric-mining-drill"
    dp = data.raw.get("mining-drill", {}).get(drill) or (ELECTRIC_DRILL if drill == ELECTRIC_DRILL["name"] else None)
    if not dp or planner._dims(dp) != (3, 3):
        raise PlanError(f"{drill}: the bus design lays out 3x3 drills (like the electric mining drill)")
    furnace = params.get("furnace") or "electric-furnace"
    fp = data.raw.get("furnace", {}).get(furnace) or data.raw["assembling-machine"].get(furnace)
    if not fp:
        raise PlanError(f"{furnace} is not a furnace")
    if (fp.get("energy_source") or {}).get("type") != "electric" or planner._dims(fp) != (3, 3):
        raise PlanError(f"{furnace}: the bus design's smelter columns take a 3x3 electric furnace (like the "
                        "electric furnace) for now; burner furnaces need a fuel lane, not laid out yet")
    allowed = params.get("inserters") or []
    inserter = next((n for n in ("fast-inserter", "inserter") if not allowed or n in allowed), "inserter")
    group, gap = int(params.get("group") or 4), int(params.get("gap") or 4)
    if group < 1 or gap < 1:
        raise PlanError("lanes per group and the gap must be at least 1")
    length = int(params.get("length") or 40)
    balance = params.get("balance", True)
    k = TURNS.get(params.get("direction") or "north", 0)
    origin = params.get("origin") or {}
    ox, oy = origin.get("x", 0), origin.get("y", 0)
    r = math.floor((dp.get("resource_searching_radius") or 2.49) + 0.5)
    notes = []

    # mining: every patch's ore tiles, per resource, in bands of one trunk each
    resources = {name: extend.runs_tiles(runs) for name, runs in (snap.get("resources") or {}).items()}
    mine_ents, trunks = [], []  # trunks: {"item" (ore), "start", "dir", "rate"}
    blocked = ground.blocked(avoid_ore=False)
    for area in patches:
        x1, y1, x2, y2 = (math.floor(area[0]), math.floor(area[1]), math.ceil(area[2]) - 1, math.ceil(area[3]) - 1)
        for name, tiles in sorted(resources.items()):
            ore = {t for t in tiles if x1 <= t[0] <= x2 and y1 <= t[1] <= y2}
            if len(ore) < 20:
                continue
            rp = data.raw.get("resource", {}).get(name) or {}
            mined = (rp.get("minable") or {})
            item = (mined.get("results") or [{}])[0].get("name") or mined.get("result") or name
            if (rp.get("category") or "basic-solid") not in (dp.get("resource_categories") or ["basic-solid"]):
                notes.append(f"{name}: {drill} can't mine it, left out")
                continue
            rate = dp.get("mining_speed", 0.5) / (mined.get("mining_time") or 1)
            cx = sum(t[0] for t in ore) / len(ore)
            for ents, _, (start, d) in mine_patch(ore, drill, belt, rate, cap, ox > cx, r):
                ents = [e for e in ents if not (e["name"] == drill and any(
                    t in blocked for t in extend.footprint(svc.decorate(e))))]
                n = sum(e["name"] == drill for e in ents)
                hit = [e for e in ents if any(t in blocked for t in extend.footprint(svc.decorate(e)))]
                if hit:
                    p = hit[0]["position"]
                    raise PlanError(f"the {name} patch has something in the way at ({p['x']:.0f}, {p['y']:.0f}): "
                                    "clear it or pick a patch without buildings")
                mine_ents += ents
                trunks.append({"item": item, "start": start, "dir": d, "rate": min(cap, n * rate), "drills": n})
    if not trunks:
        raise PlanError("no ore in the picked areas that the drills can mine")

    # what each trunk becomes on the bus
    by_item = {}
    for tr in trunks:
        sr = smelt_recipe(data, tr["item"], fp)
        if sr:
            rname, ing, out_item, res = sr
            per = fp.get("crafting_speed", 1) / (data.raw["recipe"][rname].get("energy_required") or 0.5) * res
            out_rate = tr["rate"] / ing * res
            tr.update(kind="smelt", bus_item=out_item, recipe=rname if fp.get("type") == "assembling-machine" else None, n=max(1, math.ceil(out_rate / per / 2)), out_rate=out_rate)
        else:
            tr.update(kind="raw", bus_item=tr["item"], n=0, out_rate=tr["rate"])
        by_item.setdefault(tr["bus_item"], []).append(tr)
    order = sorted(by_item, key=lambda i: (-len(by_item[i]), i))
    if params.get("wood", True) and WOOD in data.raw.get("item", {}) and WOOD not in by_item:
        by_item[WOOD] = [{"item": WOOD, "bus_item": WOOD, "kind": "chest", "n": 0, "out_rate": 0, "rate": 0}]
        order.append(WOOD)  # (last: the one lane that isn't mined)
    lanes = lane_layout([(i, len(by_item[i])) for i in order], group, gap)
    cols = [tr for i in order for tr in by_item[i]]
    for ln, tr in zip(lanes, cols):
        tr["lane"] = ln

    # the bus head: laid out flowing north, turned, put on free ground near the player
    local, goals = head(lanes, cols, length, (belt, ug, splitter, furnace, inserter), balance)
    turned = [svc.decorate(_turn(e, k)) for e in local]
    goals = [g and (_turn_tile(g[0], k), (g[1] + 4 * k) % 16) for g in goals]
    ground.taken.update({t: {"type": "new"} for e in mine_ents for t in extend.footprint(svc.decorate(e))})
    dx, dy = extend.find_spot(ground, turned, (ox, oy), avoid_ore=True)
    head_ents = extend.shifted(turned, dx, dy)
    goals = [g and ((g[0][0] + dx, g[0][1] + dy), g[1]) for g in goals]

    # the trunks, each routed from its patch to its column
    all_new = [svc.decorate(e) for e in mine_ents] + head_ents
    blocked = ground.blocked(avoid_ore=False) | extend.plan_tiles(all_new)
    spans = set(ground.spans)
    x1, y1, x2, y2 = ground.bounds
    inputs, routed = [], []
    for tr, (goal, gd) in sorted(((t, g) for t, g in zip(cols, goals) if g), key=lambda p: abs(p[0]["start"][0] - p[1][0][0])
                                 + abs(p[0]["start"][1] - p[1][0][1])):
        try:
            path = router.route(blocked, [(tr["start"], [tr["dir"], (tr["dir"] + 4) % 16, (tr["dir"] + 12) % 16])],
                                goal, gd, (x1, y1, x2, y2), max_ug, underground_spans=spans)
        except router.RouteError:
            gx, gy = goal
            inputs.append({"items": [tr["item"]], "position": {"x": gx + 0.5, "y": gy + 0.5}})
            notes.append(f"no way found for the {tr['item']} belt from its patch to its column: bring it to "
                         f"({gx}, {gy}) yourself")
            continue
        router.reserve(path, blocked, spans)
        routed += [svc.decorate(e) for e in extend.path_entities(path, belt, ug)]
    ents = [dict(e, new=True) for e in all_new + routed]

    nd = sum(tr["drills"] for tr in trunks)
    summary = {}
    for tr in trunks:
        summary.setdefault(tr["bus_item"], [0, 0.0])
        summary[tr["bus_item"]][0] += 1
        summary[tr["bus_item"]][1] += tr["out_rate"]
    notes.insert(0, "bus: " + ", ".join(f"{i} {summary[i][0]} lane{'s' if summary[i][0] > 1 else ''} "
                                        f"({summary[i][1] * 60:.0f}/min)" for i in order if i in summary)
                 + f"; {nd} drills, {sum(2 * tr['n'] for tr in trunks)} furnaces")
    if WOOD in by_item and by_item[WOOD][0]["kind"] == "chest":
        notes.append(f"[item={WOOD}] lane: fill the chest at its head with wood (it feeds itself from it)")
    if balance and any(not ln["balanced"] for ln in lanes):
        notes.append("balancers only on full groups of 4 lanes of one item (2 lanes: one splitter)")
    if inserter == "inserter" and any(tr["kind"] == "smelt" and tr["item"] == "stone" for tr in trunks):
        notes.append("stone bricks: basic inserters may not keep up; research fast inserters")
    notes.append("power: wire the drills' and the smelters' poles to your grid")
    label = "bpgen: bus from " + ", ".join(sorted({tr["item"] for tr in trunks}))
    bp, box = extend.absolute_blueprint(ents, label, description="Ore patches to a main bus, by bpgen.")
    return {"entities": ents, "notes": notes, "inputs": inputs, "blueprint": bp, "box": box,
            "lanes": [{"item": ln["item"], "x": ln["x"], "rate": tr["out_rate"],
                       "end": [c + o for c, o in zip(_turn_tile((ln["x"], -6 - length), k), (dx, dy))]}
                      for ln, tr in zip(lanes, cols)]}
