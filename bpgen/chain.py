"""Multi-recipe blueprints: a final block plus stage blocks making some of its ingredients, connected by routed belts.

Each made-here ingredient gets its own input belt in the final block (both lanes) on each side of it. One stage
block makes what both final rows eat; a splitter after its output halves it onto the two routes (each stage row
fills one lane, so both halves keep both lanes). If both rows together eat more than one belt carries, the item
gets two stage blocks instead, one per final row. Stage blocks sit in a column west of the final block; their
output belts and every raw input belt are routed with A* (underground belts cross other belts) to the input
belts' first tiles.
"""
import math
from dataclasses import dataclass, field

from bpgen import planner, router
from bpgen.planner import EAST, NORTH, SOUTH


@dataclass
class Block:
    plan: planner.Plan
    ents: list
    sources: list
    sinks: list
    x0: int = 0
    y0: int = 0
    role: str = "final"  # final | stage:<item>:<row a, b, or ab = both rows through a splitter>

    @property
    def width(self):
        return math.ceil(max(e["position"]["x"] for e in self.ents)) + 1

    @property
    def height(self):
        return math.ceil(max(e["position"]["y"] for e in self.ents)) + 1

    def shifted(self, e):
        return dict(e, position={"x": e["position"]["x"] + self.x0, "y": e["position"]["y"] + self.y0})


@dataclass
class Chain:
    final: Block
    stages: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    sinks: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _tiles(ent, size_of):
    w, h = size_of(ent["name"], ent.get("direction", 0))
    x, y = ent["position"]["x"], ent["position"]["y"]
    x1, y1 = math.floor(x - w / 2 + 0.01), math.floor(y - h / 2 + 0.01)
    return {(x1 + i, y1 + j) for i in range(w) for j in range(h)}


def related_belts(data, belt):
    """(underground name, its max distance) for this belt tier"""
    b = data.raw["transport-belt"][belt]
    name = b.get("related_underground_belt")
    if not name or name not in data.raw.get("underground-belt", {}):
        speed = b["speed"]
        name = next((n for n, u in data.raw.get("underground-belt", {}).items() if u["speed"] == speed), None)
    if not name:
        raise planner.PlanError(f"no underground belt matches {belt}")
    return name, data.raw["underground-belt"][name]["max_distance"]


def related_splitter(data, belt):
    """the splitter of this belt tier: same speed, the one named like the belt first"""
    speed = data.raw["transport-belt"][belt]["speed"]
    same = sorted(n for n, s in data.raw.get("splitter", {}).items() if s["speed"] == speed and not s.get("hidden"))
    if not same:
        raise planner.PlanError(f"no splitter matches {belt}")
    named = belt.replace("transport-belt", "splitter")
    return named if named in same else same[0]


def _stage(data, calib, spec, belt, need, stage_kw):
    try:  # the inserters the player pinned, as for the final block
        sp = planner.plan(data, calib, spec["recipe"], spec["machine"], belt, target_rate=need, **stage_kw)
    except planner.PlanError as e:
        pinned = {k: v for k, v in stage_kw.items() if k in ("near", "far", "out") and v}
        if not pinned:
            raise
        stage_kw = {k: v for k, v in stage_kw.items() if k not in pinned}
        sp = planner.plan(data, calib, spec["recipe"], spec["machine"], belt, target_rate=need, **stage_kw)
        sp.notes.append(f"the pinned inserters don't fit here ({e}); picked its own")
    for _ in range(8):  # rounding can leave a stage just short: add a machine per row
        if sp.machines * sp.output_per_machine >= need * 1.01:
            break
        sp = planner.plan(data, calib, spec["recipe"], spec["machine"], belt, target_rate=need,
                          per_row=sp.per_row + 1, **stage_kw)
    return sp


def plan_chain(data, calib, params, make, **plan_kw):
    """params: final plan() kwargs incl. recipe/machine/belt; make: {item: {"recipe":..., "machine":...}}"""
    final_plan = planner.plan(data, calib, params["recipe"], params["machine"], params["belt"],
                              whole_belt_items=list(make), **plan_kw)
    f_ents, f_src, f_sinks = planner.layout(final_plan)
    chain = Chain(Block(final_plan, f_ents, f_src, f_sinks))
    stage_kw = {k: v for k, v in plan_kw.items() if k in ("bonuses", "allowed", "beacon", "beacon_modules", "beacon_quality",
                                                         "near", "far", "out")}
    for item, spec in make.items():
        need = final_plan.input_need[item]  # per final row
        # one block for both rows when its belt carries both (an inserter-filled belt reaches ~97%)
        rows = ("ab",) if 2 * need * 1.01 <= final_plan.belt_capacity * 0.97 else ("a", "b")
        for row in rows:
            sp = _stage(data, calib, spec, params["belt"], need * len(row), stage_kw)
            ents, src, sinks = planner.layout(sp)
            chain.stages.append(Block(sp, ents, src, sinks, role=f"stage:{item}:{row}"))
    # belts shared by two made items get a side-loading junction west of the final block; try it close first
    err = None
    for back in (3, 4, 6, 8, 11):
        try:
            compose(data, chain, params["belt"], back)
            return chain
        except (planner.PlanError, router.RouteError) as e:
            err = e
    raise planner.PlanError(f"couldn't route the belts between the blocks: {err}")


def compose(data, chain, belt, back=3):
    """back: how far west of the final block a shared belt's side-loading junction sits"""
    chain.sources = []
    f = chain.final
    stages = chain.stages
    gap = 12  # routing room between the stage column and the final block
    col_w = max((s.width for s in stages), default=0)
    total_h = sum(s.height + 3 for s in stages)
    f.x0, f.y0 = 0, 0
    y = round(f.height / 2 - total_h / 2)
    for s in stages:  # row-A stages above row-B ones (they feed the upper/lower final rows)
        s.x0, s.y0 = -(gap + col_w), y
        y += s.height + 3

    blocks = [f] + stages
    size_of = _sizer(data)
    blocked, spans = set(), set()
    for b in blocks:
        tiles = set()
        for e in b.ents:
            tiles |= _tiles(b.shifted(e), size_of)
        xs, ys = [t[0] for t in tiles], [t[1] for t in tiles]
        for x in range(min(xs) - 1, max(xs) + 2):  # block interior + a 1-tile margin (inserters, belt ends)
            for yy in range(min(ys) - 1, max(ys) + 2):
                blocked.add((x, yy))
    for b in blocks:  # keep fluid feed tiles free for the pipe connection
        for s in b.sources:
            if s["kind"] == "fluid":
                blocked.add((b.x0 + math.floor(s["position"]["x"]), b.y0 + math.floor(s["position"]["y"])))

    ug_name, ug_max = related_belts(data, belt)
    xs = [t[0] for t in blocked]
    ys = [t[1] for t in blocked]
    west = min(xs) - 3
    bounds = (west, min(ys) - 8, max(xs) + 4, max(ys) + 8)

    def goal_of(block, src):
        return (block.x0 + math.floor(src["position"]["x"]), block.y0 + math.floor(src["position"]["y"]))

    routes, extra = [], []
    # 0) a belt carrying two made items, one lane each: a junction belt `back` tiles west of it, straight on into
    # it; the item for the left (north) lane side-loads in from the north, the right (south) lane's from the south
    junction = {}
    made = _made(stages)
    for src in f.sources:
        if src["kind"] == "belt" and src["lanes"][0] != src["lanes"][1] and all(l in made for l in src["lanes"]):
            gx, gy = goal_of(f, src)
            j = (gx - back, gy)
            for x in range(j[0], gx):
                if (x, gy) in blocked and x < gx - 1:
                    raise planner.PlanError("junction lands on something")
                blocked.add((x, gy))
                extra.append({"name": belt, "position": {"x": x + 0.5, "y": gy + 0.5}, "direction": EAST})
            junction[id(src)] = j
    # 1) stage outputs -> the final block's input belt for that item and row (both rows: through a splitter)
    for s in stages:
        _, item, row = s.role.split(":")
        end = s.sinks[0]["position"]
        start = (s.x0 + math.floor(end["x"]) + 1, s.y0 + math.floor(end["y"]))
        if row == "ab":
            # splitter on the tile past the belt's end and the one below it; both outputs leave east
            sx, sy = start
            blocked.update({(sx, sy), (sx, sy + 1)})
            extra.append({"name": related_splitter(data, belt), "position": {"x": sx + 0.5, "y": sy + 1.0},
                          "direction": EAST})
            legs = [("a", (sx + 1, sy), [EAST]), ("b", (sx + 1, sy + 1), [EAST])]
        else:
            legs = [(row, start, None)]
        for leg_row, tile, dirs in legs:
            target = next(src for src in f.sources if src["kind"] == "belt" and item in src["lanes"]
                          and _row_of(f, src) == leg_row)
            if id(target) in junction:
                goal = junction[id(target)]
                goal_dir = SOUTH if target["lanes"][0] == item else NORTH
            else:
                goal, goal_dir = goal_of(f, target), EAST
            blocked.discard(tile)
            blocked.discard(router._step(goal, goal_dir, -1))
            r = router.route(blocked, [(tile, dirs)], goal, goal_dir, bounds, ug_max, spans)
            router.reserve(r, blocked, spans)
            routes.append(r)
    # 2) raw inputs (final block's other belts and every stage's belts) from the west edge
    raw = [(f, src) for src in f.sources if src["kind"] == "belt" and not any(l in made for l in src["lanes"])]
    raw += [(s, src) for s in stages for src in s.sources if src["kind"] == "belt"]
    for b, src in raw:
        goal = goal_of(b, src)
        before = (goal[0] - 1, goal[1])
        blocked.discard(before)
        starts = [((west, yy), [EAST]) for yy in range(bounds[1], bounds[3] + 1) if (west, yy) not in blocked]
        starts.sort(key=lambda t: abs(t[0][1] - goal[1]))
        r = router.route(blocked, starts[:40], goal, EAST, bounds, ug_max, spans)
        router.reserve(r, blocked, spans)
        routes.append(r)
        chain.sources.append({"kind": "belt", "position": {"x": r[0][0][0] + 0.5, "y": r[0][0][1] + 0.5},
                              "lanes": src["lanes"], "rates": src.get("rates")})
    for b in blocks:
        for src in b.sources:
            if src["kind"] == "fluid":
                chain.sources.append(dict(src, position={"x": src["position"]["x"] + b.x0, "y": src["position"]["y"] + b.y0}))

    ents = []
    for b in blocks:
        for e in b.ents:
            e = b.shifted(e)
            if e.get("recipe"):
                _, item, row = (b.role.split(":") + ["", ""])[:3]
                e["role"] = ("final block" if b.role == "final" else
                             f"stage: makes {item} for both final rows (split at its end)" if row == "ab" else
                             f"stage: makes {item} for the final block's row {row.upper()}")
            ents.append(e)
    ents += extra
    for r in routes:
        for tile, kind, d in r:
            e = {"name": belt if kind == "belt" else ug_name, "position": {"x": tile[0] + 0.5, "y": tile[1] + 0.5},
                 "direction": d}
            if kind != "belt":
                e["ug_type"] = "input" if kind == "ug-in" else "output"
            ents.append(e)
    # power: link every stage's pole network to the final block's with a line of poles across the gap
    f_poles = [(e["position"]["x"], e["position"]["y"]) for e in (f.shifted(x) for x in f.ents) if e["name"] == planner.POLE]
    for s in stages:
        s_poles = [(e["position"]["x"], e["position"]["y"]) for e in (s.shifted(x) for x in s.ents) if e["name"] == planner.POLE]
        if f_poles and s_poles:
            a, b = min(((p, q) for p in s_poles for q in f_poles), key=lambda pq: _d2(*pq))
            for x, y in _pole_line(a, b, blocked):
                ents.append({"name": planner.POLE, "position": {"x": x, "y": y}})
    chain.sinks = [dict(k, position={"x": k["position"]["x"] + f.x0, "y": k["position"]["y"] + f.y0}) for k in f.sinks]
    # the harness (and a blueprint's own origin) count from the top-left corner: start at (0, 0)
    mx = math.floor(min(e["position"]["x"] for e in ents))
    my = math.floor(min(e["position"]["y"] for e in ents))
    move = lambda p: {"x": p["x"] - mx, "y": p["y"] - my}  # noqa: E731
    chain.entities = [dict(e, position=move(e["position"])) for e in ents]
    chain.sources = [dict(s, position=move(s["position"])) for s in chain.sources]
    chain.sinks = [dict(s, position=move(s["position"])) for s in chain.sinks]


def _d2(p, q):
    return (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2


def _pole_line(a, b, blocked, reach=None):
    """poles on free tiles, each within wire reach of the previous, from pole a to pole b"""
    reach = reach or planner.POLE_REACH - 0.5
    out, cur = [], a
    while _d2(cur, b) > reach ** 2:
        # any free tile within wire reach: the one nearest the target (fewest poles), ties to the straightest
        r = math.ceil(reach)
        cands = []
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                t = (math.floor(cur[0]) + dx, math.floor(cur[1]) + dy)
                c = (t[0] + 0.5, t[1] + 0.5)
                if t not in blocked and _d2(c, cur) <= reach ** 2 and _d2(c, b) < _d2(cur, b):
                    cands.append((round(_d2(c, b), 3), abs(c[1] - cur[1]), c, t))
        if not cands:
            raise planner.PlanError("couldn't run power poles between the blocks")
        _, _, c, t = min(cands)
        blocked.add(t)
        out.append(c)
        cur = c
    return out


def _made(stages):
    return {s.role.split(":")[1] for s in stages}


def _row_of(block, src):
    """which machine row an input belt feeds: rows above the output belt are A"""
    out_y = block.sinks[0]["position"]["y"]
    return "a" if src["position"]["y"] < out_y else "b"


def _sizer(data):
    types = ["assembling-machine", "furnace", "beacon", "inserter", "transport-belt", "underground-belt", "splitter",
             "electric-pole", "pipe", "pipe-to-ground"]

    def size_of(name, direction):
        for t in types:
            p = data.raw.get(t, {}).get(name)
            if p and p.get("collision_box"):
                (x1, y1), (x2, y2) = p["collision_box"]
                w, h = max(1, math.ceil(x2 - x1)), max(1, math.ceil(y2 - y1))
                return (h, w) if direction in (4, 12) else (w, h)
        return 1, 1
    return size_of


def warmup_ticks(data, chain):
    blocks = [chain.final] + chain.stages
    return max(planner.warmup_ticks(data, b.plan) for b in blocks) + 60 * 60 * len(chain.stages and [1])


def describe(chain):
    lines = ["CHAIN " + planner.describe(chain.final.plan)]
    seen = set()
    for s in chain.stages:
        _, item, row = s.role.split(":")
        if item in seen:
            continue
        seen.add(item)
        how = "1 stage block split to both final rows:" if row == "ab" else "2 stage blocks (one per final row), each"
        lines.append(f"  makes {item} here: {how} " + planner.describe(s.plan).split("\n")[0])
    return "\n".join(lines)
