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

from bpgen import chain, extend, oil, planner, router
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


def burner_column(x0, top, n, furnace, inserter, belt, recipe=None):
    """2x2 burner furnaces both sides of an ore belt that carries ore on its west lane and coal on its east (side-loaded
    at the bottom), n a side, 2 rows each. The west output belt (x0) leaves north from row top-2; the east one (x0+10)
    turns west along row top-1 onto it. -> (entities, ore goal (the ore belt's bottom, entered moving north),
    coal goal (the same tile, entered moving west: side-loaded onto its east lane))"""
    out = []
    bottom = top + 2 * n - 1
    for i in range(n):
        r = top + 2 * i
        kw = {"recipe": recipe} if recipe else {}
        out.append(ent(furnace, x0 + 3.0, r + 1.0, **kw))
        out.append(ent(furnace, x0 + 8.0, r + 1.0, **kw))
        out.append(ent(inserter, x0 + 4.5, r + 1.5, EAST))  # ore and coal from the belt into the furnace
        out.append(ent(inserter, x0 + 6.5, r + 1.5, WEST))
        out.append(ent(inserter, x0 + 1.5, r + 1.5, EAST))  # plates out
        out.append(ent(inserter, x0 + 9.5, r + 1.5, WEST))
        if i % 2 == 0:
            out.append(ent(POLE, x0 + 4.5, r + 0.5))
            out.append(ent(POLE, x0 + 9.5, r + 0.5))
    for y in range(top + 1, bottom + 2):  # (a row short at the top, as the electric column; one past the bottom)
        out.append(ent(belt, x0 + 5.5, y + 0.5, NORTH))
    for y in range(top - 1, bottom + 1):
        out.append(ent(belt, x0 + 0.5, y + 0.5, NORTH))
    for y in range(top, bottom + 1):
        out.append(ent(belt, x0 + 10.5, y + 0.5, NORTH))
    for x in range(1, 11):
        out.append(ent(belt, x0 + x + 0.5, top - 1 + 0.5, WEST))
    return out, (x0 + 5, bottom + 1), ((x0 + 5, bottom + 1), WEST)



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


def head(lanes, columns, length, names, balance=True, max_ug=4):
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
    widths = [(11 if c.get("burner") else 13) if c["kind"] == "smelt" else 1 for c in columns]
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
    goals, coal_goals, columns_bottom = [], [], {}
    coal_from = coal_lane_goal = None
    for k, c in enumerate(columns):
        x0, xin = xs[k], lanes[k]["in"]
        if c["kind"] == "smelt" and c.get("burner"):
            ce, goal, (coal_tile, _) = burner_column(x0, top, c["n"], furnace, inserter, belt, c.get("recipe"))
            ents += ce
            goals.append((goal, NORTH))
            columns_bottom[k] = goal[1]
            # (coal onto the lane the ore isn't on: the ore came on its left lane (west, flowing north) -> from east)
            coal_goals.append((k, coal_tile, WEST if c.get("ore_lane", "L") == "L" else EAST))
        elif c["kind"] == "smelt":
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
            if c.get("item") == "coal" and coal_from is None:  # (the first coal lane: its coal feeds the furnaces)
                coal_from, coal_lane_goal = k, goals[-1]
        yk = turn.get(k, 1)
        for y in range(yk + 1, rows + 2):  # up from the column to its turn row
            ents.append(ent(belt, x0 + 0.5, y + 0.5, NORTH))
        if x0 != xin:
            step = 1 if xin > x0 else -1
            for x in range(x0, xin, step):
                ents.append(ent(belt, x + 0.5, yk + 0.5, EAST if step > 0 else WEST))
        for y in range(1, yk + 1):  # up the lane to the balancer's input
            ents.append(ent(belt, xin + 0.5, y + 0.5, NORTH))
    # burner columns: their coal, from a belt under them all (the coal patch's, else a chest filled by hand), a
    # splitter at each, its branch side-loaded onto the column's ore belt (the lane the ore isn't on)
    if coal_goals:
        from bpgen import extend as ext, router as rt
        occupied = ext.plan_tiles(ents)
        m_row = max(g[1] for _, g, _ in coal_goals) + 5
        bx0 = min(xs[k] for k, _, _ in coal_goals) - 3
        bx1 = max(xs[k] for k, _, _ in coal_goals) + 12
        splitters = {xs[k] + 7 for k, _, _ in coal_goals}
        for x in range(bx0, bx1 + 1):
            if x in splitters:
                # (rows m_row - 1 and m_row: the branch above, first: the furnaces get their coal, the bus what they leave)
                ents.append(ent(splitter, x + 0.5, m_row + 0.0, EAST, output_priority="left"))
            else:
                ents.append(ent(belt, x + 0.5, m_row + 0.5, EAST))
        blocked = occupied | ext.plan_tiles([e for e in ents if e["position"]["y"] > m_row - 2])
        for k, _, _ in coal_goals:  # (each column's ore comes up from the south: its way kept clear)
            for y in range(columns_bottom[k] + 1, m_row - 1):
                blocked.add((xs[k] + 5, y))
        lo_x = min(t[0] for t in occupied | blocked) - 8
        hi_x = max(t[0] for t in occupied | blocked) + 8
        bounds = (lo_x, -10, hi_x, m_row + 2)
        spans = set()
        for k, goal, gdir in coal_goals:
            sx = xs[k] + 7
            blocked.discard(goal)
            try:
                path = rt.route(blocked, [((sx + 1, m_row - 1), [EAST, NORTH])], goal, gdir, bounds, max_ug,
                                underground_spans=spans)
            except rt.RouteError:
                raise PlanError("no room to bring the coal to the burner furnaces") from None
            rt.reserve(path, blocked, spans)
            blocked.add(goal)
            ents += ext.path_entities(path, belt, ug)
        if coal_from is not None:  # (the coal patch's belt comes in at its west end; its east end goes on to the bus)
            goals[coal_from] = ((bx0, m_row), EAST)
            raw_goal = coal_lane_goal
            try:
                path = rt.route(blocked, [((bx1 + 1, m_row), [EAST, NORTH, SOUTH])], raw_goal[0], raw_goal[1],
                                (lo_x, -10, hi_x + 20, m_row + 6), max_ug, underground_spans=spans)
                ents += ext.path_entities(path, belt, ug)
            except rt.RouteError:
                pass  # (the coal lane then only gets what the furnaces leave: nothing; it stays empty)
        else:  # (no coal patch: a chest at its start, its burner inserter runs on the coal it moves)
            ents.append(ent(CHEST_INSERTER, bx0 - 0.5, m_row + 0.5, WEST))
            ents.append(ent(CHEST, bx0 - 1.5, m_row + 0.5))
    return ents, goals


# ---- mining ----

def _drill_ok(ore, cx, cy, r):
    """ore tiles in the mining area of a drill whose top-left tile is (cx, cy) (3x3)"""
    n = 0
    for x in range(cx + 1 - r, cx + 2 + r):
        for y in range(cy + 1 - r, cy + 2 + r):
            n += (x, y) in ore
    return n >= MIN_ORE


def band(ore, ya, yb, x0, x1, drill, belt, toward_east, r, sides=(-1, 1)):
    """drills, collector belts, poles and the header for rows ya..yb of a patch. -> (entities, drills, header end:
    the trunk's first tile, its direction) or None (no drill fits)"""
    H = (ya + yb) // 2 if len(sides) > 1 else yb - 1  # (one side: the header at the band's foot, drills above)
    ents, n, xs = [], 0, []
    cxs = list(range(x0 - 1, x1 + 1, 7))
    for cx in cxs:
        got = False
        for sgn in sides:  # -1: above the header (drills send south), 1: below (north)
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
        for p in [H + sgn for sgn in sides]:
            for px in (cx + 1.5, cx + 5.5):
                if (px, p + 0.5) not in have:
                    ents.append(ent(POLE, px, p + 0.5))
    lo, hi = min(xs) - 1, max(xs) + 1
    d = EAST if toward_east else WEST
    for x in range(lo, hi + 1):
        ents.append(ent(belt, x + 0.5, H + 0.5, d))
    start = (hi + 1, H) if toward_east else (lo - 1, H)
    return ents, n, (start, d)


def mine_patch(ore, drill, belt, rate, cap, toward_east, r, sides=(-1, 1)):
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
            got = band(ore, ya, yb, x0, x1, drill, belt, toward_east, r, sides)
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


def _last_file(key=None):
    """one a save (its map seed) and surface: a base planned in one save doesn't fit to another's bus"""
    import re
    from bpgen.config import PATHS
    name = "bus_design" + (f"-{re.sub(r'[^A-Za-z0-9_-]', '_', key)}" if key else "") + ".json"
    return PATHS["script_output"] / "bpgen" / name


def save_key(params):
    return f"{params['seed']}-{params.get('surface') or 'nauvis'}" if params.get("seed") is not None else None


def save_last(out, params):
    """the planned bus's lane ends (world tiles), for a starter base to fit its head to (tiers.fit_to_bus)"""
    import json
    f = _last_file(save_key(params))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"direction": params.get("direction") or "north", "surface": params.get("surface"),
                             "lanes": out.get("lanes") or [],
                             # (what it covers: a base fitted to it mustn't land on its trunks)
                             "tiles": sorted(extend.plan_tiles(out.get("entities") or [])),
                             "end_pole": out.get("end_pole")}), encoding="utf-8")


def load_last(params=None):
    """the last bus design's lanes {direction, lanes: [{item, x, rate, end}]} in this save, or None"""
    import json
    try:
        return json.loads(_last_file(save_key(params or {})).read_text(encoding="utf-8"))
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
    burner = (fp.get("energy_source") or {}).get("type") == "burner"
    if planner._dims(fp) != ((2, 2) if burner else (3, 3)):
        raise PlanError(f"{furnace}: the bus design's smelter columns take a 3x3 electric furnace or a 2x2 burner one "
                        "(stone, steel)")
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
    notes, inputs = [], []  # (inputs: what has to be brought by hand, and where)

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
            # (burner furnaces: the ore on one lane of its trunk, half a belt, coal goes on the other at the furnaces)
            half = burner and smelt_recipe(data, item, fp)
            for ents, _, (start, d) in mine_patch(ore, drill, belt, rate, cap / 2 if half else cap, ox > cx, r,
                                                  (-1,) if half else (-1, 1)):
                ents = [e for e in ents if not (e["name"] == drill and any(
                    t in blocked for t in extend.footprint(svc.decorate(e))))]
                n = sum(e["name"] == drill for e in ents)
                hit = [e for e in ents if any(t in blocked for t in extend.footprint(svc.decorate(e)))]
                if hit:
                    p = hit[0]["position"]
                    raise PlanError(f"the {name} patch has something in the way at ({p['x']:.0f}, {p['y']:.0f}): "
                                    "clear it or pick a patch without buildings")
                mine_ents += ents
                trunks.append({"item": item, "start": start, "dir": d, "rate": min(cap / 2 if half else cap, n * rate),
                               "drills": n, "ore_lane": "L" if d == EAST else "R"})  # (north collectors: header's north lane)
    # oil fields picked: pumpjacks, their crude to an oil block (plastic, sulfur), its products onto bus lanes
    oil_fields = oil.fields(snap, patches)
    oil_block = None
    if oil_fields:
        crude = sum(oil.crude_rate(data, n, a) for n, _, a in oil_fields)
        oil_block = oil.plan_block(svc, crude, belt, water=True)
        oil_block["crude"] = crude
        for item, r in oil_block["products"].items():
            trunks.append({"item": item, "rate": r, "drills": 0, "oil": True, "start": None, "dir": EAST})
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
            tr.update(kind="smelt", bus_item=out_item, recipe=rname if fp.get("type") == "assembling-machine" else None,
                      n=max(1, math.ceil(out_rate / per / 2)), out_rate=out_rate, burner=burner)
        else:
            tr.update(kind="raw", bus_item=tr["item"], n=0, out_rate=tr["rate"])
        by_item.setdefault(tr["bus_item"], []).append(tr)
    order = sorted(by_item, key=lambda i: (-len(by_item[i]), i))
    if params.get("wood", True) and WOOD in data.raw.get("item", {}) and WOOD not in by_item:
        by_item[WOOD] = [{"item": WOOD, "bus_item": WOOD, "kind": "chest", "n": 0, "out_rate": 0, "rate": 0}]
        order.append(WOOD)  # (last: the one lane that isn't mined)
    lanes = lane_layout([(i, len(by_item[i])) for i in order], group, gap)
    cols = [tr for i in order for tr in by_item[i]]
    if burner:  # (the first coal lane feeds the furnaces first: the bus gets what they don't burn)
        coal = next((tr for tr in cols if tr["item"] == "coal" and tr["kind"] == "raw"), None)
        fuel = (data.raw.get("item", {}).get("coal") or {}).get("fuel_value")
        if coal and fuel:
            burn = planner.energy(fp.get("energy_usage") or "90kW") / planner.energy(fuel)
            coal["out_rate"] = max(0.0, coal["rate"] - burn * sum(2 * tr["n"] for tr in cols if tr["kind"] == "smelt"))
    adding = None
    if params.get("add_to_bus"):  # (more for the bus that's here: a lane of its own beside it for each new trunk)
        main = extend.find_bus(ground)
        if not main:
            raise PlanError("no main bus around you to add to (3+ long straight belts side by side): stand by it")
        cols = [tr for tr in cols if tr["kind"] != "chest"]
        new_lanes = []
        for tr in cols:
            got = extend.new_bus_lane(data, ground, main, tr["bus_item"], max_ug)
            if not got:
                raise PlanError(f"no room beside your bus for another {tr['bus_item']} lane")
            lents, lhead, _ = got
            ground.taken.update({t: {"type": "new"} for e in lents for t in extend.footprint(svc.decorate(e))})
            main["lanes"].append({"at": lhead[0] if main["axis"] == 0 else lhead[1], "items": {tr["bus_item"]},
                                  "belt": lents[0]["name"], "lo": main["lo"], "hi": main["hi"]})
            main["lanes"].sort(key=lambda ln: ln["at"])
            new_lanes.append((lents, lhead))
        # (the smelters upstream of the bus, flowing its way, their lanes short stubs belted onto the new lanes)
        k, length, balance = main["dir"] // 4, 0, False
        lanes = [{"item": tr["bus_item"], "x": 2 * i, "in": 2 * i, "group": 2 * i, "balanced": False}
                 for i, tr in enumerate(cols)]
        dv = router.DIRS[main["dir"]]
        ox, oy = new_lanes[0][1][0] - 25 * dv[0], new_lanes[0][1][1] - 25 * dv[1]
        adding = (main, new_lanes)
    for ln, tr in zip(lanes, cols):
        tr["lane"] = ln

    # the bus head: laid out flowing north, turned, put on free ground near the player
    local, goals = head(lanes, cols, length, (belt, ug, splitter, furnace, inserter), balance, max_ug)
    turned = [svc.decorate(_turn(e, k)) for e in local]
    goals = [g and (_turn_tile(g[0], k), (g[1] + 4 * k) % 16) for g in goals]
    ground.taken.update({t: {"type": "new"} for e in mine_ents for t in extend.footprint(svc.decorate(e))})
    keep = ()
    if adding:  # (upstream of the bus by the block's own length, the way into each new lane's head kept clear)
        main, new_lanes = adding
        dv = router.DIRS[main["dir"]]
        occ = extend.plan_tiles(turned)
        along = (max(t[1] for t in occ) - min(t[1] for t in occ)) if dv[0] == 0 else \
            (max(t[0] for t in occ) - min(t[0] for t in occ))
        hx, hy = new_lanes[0][1]
        ox, oy = hx - (along // 2 + 8) * dv[0], hy - (along // 2 + 8) * dv[1]
        keep = {(lh[0] - j * dv[0] + i * dv[1], lh[1] - j * dv[1] + i * dv[0]) for _, lh in new_lanes
                for j in range(1, 5) for i in (-1, 0, 1)}
    dx, dy = extend.find_spot(ground, turned, (ox, oy), avoid_ore=True, keep_clear=keep)
    head_ents = extend.shifted(turned, dx, dy)
    goals = [g and ((g[0][0] + dx, g[0][1] + dy), g[1]) for g in goals]
    oil_ents, crude_goal = [], None
    if oil_block:  # (the oil block beside the head: its products start where its output belts end)
        ground.taken.update({t: {"type": "new"} for e in head_ents for t in extend.footprint(e)})
        block = [svc.decorate(e) for e in oil_block["entities"]]
        bdx, bdy = extend.find_spot(ground, block, (ox, oy), avoid_ore=True)
        oil_ents = extend.shifted(block, bdx, bdy)
        at = {t: e for e in oil_ents for t in extend.footprint(e)}
        for sk in oil_block["sinks"]:
            t = (math.floor(sk["position"]["x"]) + bdx, math.floor(sk["position"]["y"]) + bdy)
            d = (at.get(t) or {}).get("direction", EAST)
            tr = next((p for p in cols if p.get("oil") and p["item"] == sk.get("item")), None)
            if tr:
                tr["start"], tr["dir"] = (t[0] + router.DIRS[d][0], t[1] + router.DIRS[d][1]), d
        for src in oil_block["sources"]:
            t = (math.floor(src["position"]["x"]) + bdx, math.floor(src["position"]["y"]) + bdy)
            if src.get("fluid") == "crude-oil":
                crude_goal = t
            elif src.get("fluid"):
                inputs.append({"items": [src["fluid"]], "position": {"x": t[0] + 0.5, "y": t[1] + 0.5}})
                notes.append(f"oil: the [fluid={src['fluid']}] inlet is at ({t[0]}, {t[1]}): an offshore pump's pipe "
                             "goes there")
            elif src.get("kind") == "belt":  # (its coal: a chest at the belt's start, filled by hand)
                oil_ents += [svc.decorate({"name": oil.CHEST_INSERTER, "position": {"x": t[0] - 0.5, "y": t[1] + 0.5},
                                           "direction": WEST}),
                             svc.decorate({"name": oil.CHEST, "position": {"x": t[0] - 1.5, "y": t[1] + 0.5}})]
                notes.append(f"oil: fill the chest at ({t[0] - 2}, {t[1]}) with "
                             + " ".join(f"[item={i}]" for i in dict.fromkeys(i for i in src.get("lanes") or [] if i)))

    # the trunks, each routed from its patch to its column
    all_new = [svc.decorate(e) for e in mine_ents] + head_ents + oil_ents
    blocked = ground.blocked(avoid_ore=False) | extend.plan_tiles(all_new)
    starts = []
    if adding:  # (each stub's way out to its new lane: kept for its own belt, from the trunks too)
        for tr in cols:
            st = _turn_tile((tr["lane"]["x"], -7), k)
            starts.append((st[0] + dx, st[1] + dy))
        blocked |= set(starts)
    spans = set(ground.spans)
    x1, y1, x2, y2 = ground.bounds
    routed = []
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
        tr["goal"] = goal
    pj_poles = []
    if oil_block:  # (pumpjacks on the fields, piped to the block's crude inlet, never touching its other fluids)
        pj, outs, _ = oil.pumpjacks(svc, oil_fields, blocked)
        pj = [svc.decorate(e) for e in pj]
        foreign = oil.foreign_fluids(oil_ents, crude_goal) if crude_goal else {}
        foreign.update({t: "crude-oil" for e in pj if e["name"] == oil.PUMPJACK for t in extend.footprint(e)})
        pipes = oil.pipe_crude(crude_goal, outs, blocked, foreign, (x1, y1, x2, y2)) if crude_goal else None
        if pipes is None:
            notes.append("oil: no pipe route from the pumpjacks to the refineries: pipe the crude in yourself")
            if crude_goal:
                inputs.append({"items": ["crude-oil"], "position": {"x": crude_goal[0] + 0.5, "y": crude_goal[1] + 0.5}})
        routed += pj + [svc.decorate(e) for e in pipes or []]
        pj_poles = extend.pole_tiles(pj)
        n_pj = sum(e["name"] == oil.PUMPJACK for e in pj)
        notes.insert(0, f"oil: {n_pj} pumpjacks, {oil_block['crude']:.0f} crude a second: "
                     + ", ".join(f"[item={k}] {v * 60:.0f}/min" for k, v in oil_block["products"].items()))
    if adding:  # (each stub onto its new lane's head)
        main, new_lanes = adding
        for tr, (lents, lhead), st in zip(cols, new_lanes, starts):
            blocked.discard(st)
            try:
                path = router.route(blocked, [(st, [main["dir"], (main["dir"] + 4) % 16, (main["dir"] + 12) % 16])],
                                    tuple(lhead), main["dir"], (x1, y1, x2, y2), max_ug, underground_spans=spans)
            except router.RouteError:
                raise PlanError(f"no way for the {tr['bus_item']} from its smelters to its new lane") from None
            router.reserve(path, blocked, spans)
            routed += [svc.decorate(e) for e in extend.path_entities(path, belt, ug) + lents]
        notes.append(f"added to your bus: {len(new_lanes)} new lane{'s' if len(new_lanes) > 1 else ''} beside it ("
                     + ", ".join(f"[item={tr['bus_item']}]" for tr in cols) + "), fed from these patches")
    # power, one network: a pole line from each patch along its trunk to the smelters, and on to the bus's end
    mine_poles = extend.pole_tiles(mine_ents)
    head_poles = extend.pole_tiles(head_ents)
    lines, unpowered = [], 0
    for tr in cols:
        if tr.get("goal") and mine_poles and head_poles:
            run = extend.pole_chain(extend.nearest(mine_poles, tr["start"]), extend.nearest(head_poles, tr["goal"]),
                                      blocked)
            lines += run or []
            unpowered += run is None
    end_pole = None
    if head_poles:
        t = _turn_tile((lanes[0]["x"] - 2, -6 - length), k)  # (beside the first lane, where the bus ends)
        t = (t[0] + dx, t[1] + dy)
        run = extend.pole_chain(extend.nearest(head_poles, t), t, blocked) if t not in blocked else None
        if run is not None:
            blocked.add(t)
            lines += run + [{"name": POLE, "position": {"x": t[0] + 0.5, "y": t[1] + 0.5}}]
            end_pole = list(t)
    if oil_block:  # (the oil block and each pumpjack's pole: onto the network, each from the nearest pole so far)
        net = extend.pole_tiles(head_ents) + extend.pole_tiles(lines)
        for group in (extend.pole_tiles(oil_ents), *[[p] for p in pj_poles]):
            if not group or not net:
                continue
            a, b = min(((extend.nearest(net, g), g) for g in group),
                       key=lambda ab: (ab[0][0] - ab[1][0]) ** 2 + (ab[0][1] - ab[1][1]) ** 2)
            run = extend.pole_chain(a, b, blocked)
            if run is None:
                unpowered += 1
                continue
            lines += run
            net += extend.pole_tiles(run) + group
    ents = [dict(e, new=True) for e in all_new + routed + [svc.decorate(e) for e in lines]]

    nd = sum(tr["drills"] for tr in trunks)
    summary = {}
    for tr in trunks:
        summary.setdefault(tr["bus_item"], [0, 0.0])
        summary[tr["bus_item"]][0] += 1
        summary[tr["bus_item"]][1] += tr["out_rate"]
    notes.insert(0, "bus: " + ", ".join(f"{i} {summary[i][0]} lane{'s' if summary[i][0] > 1 else ''} "
                                        f"({summary[i][1] * 60:.0f}/min)" for i in order if i in summary)
                 + f"; {nd} drills, {sum(2 * tr['n'] for tr in trunks)} furnaces")
    if burner:
        notes.append("burner furnaces: coal goes onto each column's ore belt from a belt under them, fed "
                     + ("by your coal patch (the rest goes on to the bus)" if any(tr["item"] == "coal" for tr in trunks)
                        else "from a chest at its west end: fill it with coal (no coal patch picked)"))
    if WOOD in by_item and by_item[WOOD][0]["kind"] == "chest":
        notes.append(f"[item={WOOD}] lane: fill the chest at its head with wood (it feeds itself from it)")
    if balance and any(not ln["balanced"] for ln in lanes):
        notes.append("balancers only on full groups of 4 lanes of one item (2 lanes: one splitter)")
    if inserter == "inserter" and any(tr["kind"] == "smelt" and tr["item"] == "stone" for tr in trunks):
        notes.append("stone bricks: basic inserters may not keep up; research fast inserters")
    notes.append("power: one pole network, drills to the bus's end: wire one of its poles to your grid"
                 + (f" ({unpowered} trunk{'s' if unpowered > 1 else ''} without a pole line: wire those drills too)"
                    if unpowered else ""))
    label = "bpgen: bus from " + ", ".join(sorted({tr["item"] for tr in trunks}))
    bp, box = extend.absolute_blueprint(ents, label, description="Ore patches to a main bus, by bpgen.")
    return {"entities": ents, "notes": notes, "inputs": inputs, "blueprint": bp, "box": box, "end_pole": end_pole,
            "lanes": [{"item": ln["item"], "x": ln["x"], "rate": tr["out_rate"],
                       "end": [c + o for c, o in zip(_turn_tile((ln["x"], -6 - length), k), (dx, dy))]}
                      for ln, tr in zip(lanes, cols)]}
