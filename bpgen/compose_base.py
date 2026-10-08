"""Lay the starter base's sections out as one connected blueprint.

Blocks (production lines, labs) stand in columns by recipe depth: smelting first, science and labs last, inputs
on each block's west edge and its output on the east edge. Belts between them are routed with A* (router.py):

  consumer side  one feed per input belt kind of a block. Both machine rows' copies of that belt are fed through
                 a splitter station west of the block (splitters keep lanes). A belt carrying two items is built
                 at a merge station in open ground: a belt fed from both sides, each item side-loading onto its
                 own lane, then routed on (turns and undergrounds keep lanes).
  producer side  a staircase of splitters after the output belt, one leg per consumer feed. Producers make at
                 least what all their consumers take, so a backed-up leg sends the surplus down the others.
  raw items      routed from the west edge, where the player brings ore (and coal) in.

Power: every block's pole network joined by pole lines over free tiles.

With bus=True the blocks stand in one column instead, beside a main bus of belt columns (bus.py): a producer's output
goes to the head of its column, each consumer's feed is a tap off it, and the router only lays the short legs from the
taps to the feeds, the returns to the column heads and the pipes.
"""
import math
import time
from collections import Counter

from bpgen import bus as bus_layout, planner, router
from bpgen.chain import _d2, _pole_line, _sizer, _tiles, related_belts, related_splitter
from bpgen.planner import EAST, NORTH, SOUTH


class ComposeError(planner.PlanError):
    pass


class Block:
    def __init__(self, name, item, ents, sources, sinks, depth, capacity=0.0, last=False):
        self.name, self.item, self.ents, self.sources, self.sinks = name, item, ents, sources, sinks
        self.depth, self.capacity = depth, capacity
        self.last = last  # takes what the rest leave (the mall)
        self.x0 = self.y0 = 0
        self.width = math.ceil(max(e["position"]["x"] for e in ents)) + 1
        self.height = math.ceil(max(e["position"]["y"] for e in ents)) + 1

    def at(self, pos):
        """a block-local position -> its world tile"""
        return self.x0 + math.floor(pos["x"]), self.y0 + math.floor(pos["y"])

    def shifted(self, e):
        return dict(e, position={"x": e["position"]["x"] + self.x0, "y": e["position"]["y"] + self.y0})


def _rank(block, feeds):
    """how much a consumer matters when taps compete: the packs (nobody takes from them) before the intermediates,
    the mall last"""
    return 0 if block.last else 1 if feeds.get(id(block)) else 2


# a layout that hasn't come together in this many seconds gives up like one that can't (the base falls back to its
# separate prints): with extreme machines (e.g. crafting speed 240) a connected layout could take many minutes
COMPOSE_SECONDS = 20


def compose(data, blocks, belt, raw, slack=4, row_gap=3, max_col_h=None, bus=False, notes=None):
    """blocks: [Block]; raw: items brought in from outside. -> (entities, sources, sinks)
    bus: one column of blocks beside a main bus (see bus_layout) instead of columns by recipe depth; notes (a list)
    gets a line about it"""
    deadline = time.monotonic() + COMPOSE_SECONDS

    def in_time():
        if time.monotonic() > deadline:
            raise ComposeError(f"the connected layout took over {COMPOSE_SECONDS} s: given up")

    size_of = _sizer(data)
    ug_name, ug_max = related_belts(data, belt)
    splitter = related_splitter(data, belt)

    # ---- consumer groups: (block, lanes) -> its row sources --------------------------------------------------
    # rows sharing one feed (split at a station) get one belt between them: when they need more than that,
    # each row gets a feed of its own
    belt_cap = data.raw["transport-belt"][belt]["speed"] * 480 * 0.97
    groups = []
    for b in blocks:
        by_lanes = {}
        for s in b.sources:
            if s["kind"] == "belt":
                by_lanes.setdefault(tuple(s["lanes"]), []).append(s)
        for lanes, srcs in by_lanes.items():
            need = {}
            for s_ in srcs:
                for it, r in (s_.get("rates") or {}).items():
                    need[it] = need.get(it, 0) + r
            per_item = {it: belt_cap * (1 if lanes[0] == lanes[1] else 0.5) for it in lanes if it}
            if len(srcs) > 1 and any(r > per_item.get(it, belt_cap) for it, r in need.items()):
                for s_ in srcs:
                    groups.append({"block": b, "lanes": lanes, "sources": [s_]})
            else:
                groups.append({"block": b, "lanes": lanes, "sources": srcs})
    producers, fluid_producers = {}, {}  # item -> [block]; fluid -> [(block, its sink for that fluid)]
    for b in blocks:
        for k, sk in enumerate(b.sinks):
            item = sk.get("item") or b.item
            if not item:
                continue
            if sk.get("kind", "belt") == "belt":
                if k == 0:
                    producers.setdefault(item, []).append(b)
            else:  # a block can put out several fluids (a refinery)
                fluid_producers.setdefault(item, []).append((b, sk))
    fluid_users = {s_["fluid"] for b in blocks for s_ in b.sources if s_["kind"] == "fluid"}

    # ---- feeds: what each group needs from whom -----------------------------------------------------------
    for g in groups:
        items = [l for l in g["lanes"] if l]
        made = [l for l in items if l in producers]
        if len(set(items)) == 2 and made:
            g["merge"] = True  # two items, at least one made here: build the belt at a merge station
        elif not made:
            g["raw_only"] = True  # the player's belt, as it is (e.g. ore | coal)
        rates = {}
        for s in g["sources"]:
            for it, r in (s.get("rates") or {}).items():
                rates[it] = rates.get(it, 0) + r
        g["rates"] = rates
    # producer copies: hand each group's need to a copy with room for it
    # a group needing more than one copy has makes is fed by several, merged in front of it (see "merge splitters")
    feeds = {}  # producer block -> [(group, item)]
    for item, copies in producers.items():
        left = {id(c): c.capacity for c in copies}
        for g in groups:
            if item not in g["lanes"] or g["block"].item == item:
                continue
            need = g["rates"].get(item, 0)
            used = []
            # the copies with the most to spare first, until the need is covered (or every copy is in)
            for c in sorted(copies, key=lambda c: -left[id(c)]):
                take = min(need, max(left[id(c)], 0)) if len(used) < len(copies) - 1 else need
                if used and take <= 1e-6:
                    break
                left[id(c)] -= take
                need -= take
                used.append(id(c))
                feeds.setdefault(id(c), []).append((g, item))
                if need <= 1e-6:
                    break
            g.setdefault("copies", {})[item] = used

    # ---- placement: blocks in depth order, filling columns ------------------------------------------------------
    if not bus:
        # max_col_h None: one column per depth. Otherwise a column takes blocks until it is that tall.
        order = sorted(blocks, key=lambda b: (b.depth, b.name))
        cols, cur, h = [], [], 0
        for b in order:
            if cur and ((b.depth != cur[-1].depth) if max_col_h is None else (h + b.height > max_col_h)):
                cols.append(cur)
                cur, h = [], 0
            cur.append(b)
            h += b.height + row_gap
        cols.append(cur)
        # each channel: the column's output splitters (2 tiles each), the next column's input stations, some slack
        # a block goes near the blocks that feed it: each column is ordered by the mean height of its producers
        makers_of = {}
        by_id = {id(b): b for b in blocks}
        for pid, legs in feeds.items():
            for g, _ in legs:
                makers_of.setdefault(id(g["block"]), set()).add(pid)
        for b in blocks:
            for s_ in b.sources:
                if s_["kind"] == "fluid":
                    for m, _ in fluid_producers.get(s_["fluid"], []):
                        makers_of.setdefault(id(b), set()).add(id(m))
        placed = set()
        x = 0
        for i, col in enumerate(cols):
            def mean_y(b, k):
                ys = [by_id[m].y0 + by_id[m].height / 2 for m in makers_of.get(id(b), ()) if m in placed]
                return (0, sum(ys) / len(ys), k) if ys else (1, 0, k)
            order = {id(b): k for k, b in enumerate(col)}
            col.sort(key=lambda b: mean_y(b, order[id(b)]))
            y = 0
            for b in col:
                placed.add(id(b))
                b.x0, b.y0 = x, y
                y += b.height + row_gap
            k = max(len(feeds.get(id(b), [])) for b in col)
            x += max(b.width for b in col) + max(2 * k + 3, 11) + slack
    else:
        # one column of blocks, every producer above its consumers, the bus on their west:
        # a column per raw input belt (they start at the top) and one per block that feeds others (from its row down)
        cap_b = data.raw["transport-belt"][belt]["speed"] * 480 * 0.95
        raw_bundles, by_lanes = [], {}
        for g in sorted(groups, key=lambda g: (g["block"].depth, g["block"].name)):
            if g.get("raw_only"):
                need = [(None, tuple(g["lanes"]), dict(g["rates"]))]
            elif g.get("merge"):
                need = [(it, (it, it), {it: g["rates"].get(it, 0)}) for it in dict.fromkeys(g["lanes"])
                        if it and it not in producers]
            else:
                need = []
            for it, lanes, rates in need:
                item_cap = {i: cap_b * (1 if lanes[0] == lanes[1] else 0.5) for i in lanes if i}
                bd = by_lanes.get(lanes)
                if bd is None or any(bd["rates"].get(i, 0) + r > item_cap.get(i, cap_b) for i, r in rates.items()):
                    bd = by_lanes[lanes] = {"lanes": lanes, "rates": {}, "members": []}
                    raw_bundles.append(bd)
                for i, r in rates.items():
                    bd["rates"][i] = bd["rates"].get(i, 0) + r
                bd["members"].append((g, it))
        # top to bottom: every block below the ones that feed it. Of the blocks that could go next the ones nobody
        # takes from (the packs) go first, so they take what they need before the intermediates above them fill the
        # long columns below; then the shallower and smaller ones; the mall last
        needs = {id(b): set() for b in blocks}
        for pid, legs_ in feeds.items():
            for g, _ in legs_:
                needs[id(g["block"])].add(pid)
        order, left = [], list(blocks)
        while left:
            ready = [b for b in left if not needs[id(b)] - {id(p) for p in order}]
            if not ready:
                raise ComposeError("the blocks feed each other in a loop")
            nxt = min(ready, key=lambda b: (b.last, bool(feeds.get(id(b))), b.depth, b.capacity, b.name))
            order.append(nxt)
            left.remove(nxt)
        made = [b for b in order if feeds.get(id(b))]
        bus_w = 2 * (len(raw_bundles) + len(made))
        y = 0
        for b in order:
            b.x0, b.y0 = bus_w + 8 + 2 * slack, y
            y += b.height + row_gap
        cols_bus, raw_col, made_col = [], {}, {}
        for bd in raw_bundles:
            raw_col[id(bd)] = bus_layout.Column(len(cols_bus), "raw", -3, 2 * len(cols_bus), "|".join(
                i for i in dict.fromkeys(bd["lanes"]) if i))
            cols_bus.append(raw_col[id(bd)])
        for b in made:
            made_col[id(b)] = bus_layout.Column(len(cols_bus), "made", b.y0 + b.height + 2, 2 * len(cols_bus), b.name)
            cols_bus.append(made_col[id(b)])
        sources = []
    kmax = max((len(v) for v in feeds.values()), default=1)
    blocked, spans, solid = set(), set(), set()  # solid: real entity tiles (blocked adds a margin)
    for b in blocks:
        tiles = set()
        for e in b.ents:
            tiles |= _tiles(b.shifted(e), size_of)
        solid |= tiles
        xs, ys = [t[0] for t in tiles], [t[1] for t in tiles]
        for tx in range(min(xs) - 1, max(xs) + 2):
            for ty in range(min(ys) - 1, max(ys) + 2):
                blocked.add((tx, ty))
    xs = [t[0] for t in blocked]
    ys = [t[1] for t in blocked]
    # room west of the blocks for the raw inputs: the widest splitter staircase sharing one input belt
    raw_kinds = Counter()
    for g in groups:
        if g.get("raw_only"):
            raw_kinds[tuple(g["lanes"])] += 1
        elif g.get("merge"):
            for it in g["lanes"]:
                if it and it not in producers:
                    raw_kinds[(it, it)] += 1
    k_raw = max(raw_kinds.values(), default=1)
    west = min(xs) - (2 * k_raw + 5 + slack)
    inner = (min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2)  # the blocks' outline: pipes pay to leave it
    bounds = (west, min(ys) - 6 - slack, max(xs) + 2 * kmax + 6 + slack, max(ys) + 6 + slack)
    if bus:
        west = -3
        bounds = (west, min(ys) - 8 - slack, max(xs) + 8 + slack, max(ys) + 6 + slack)
        inner = (west + 1, inner[1], inner[2], inner[3])  # (pipes go through the strip beside the bus too)
    # nothing west of the raw inputs (column west + 1): that is where the player's belts come in. The router
    # treats the bounds as inclusive, so close the edge column itself
    for yy in range(bounds[1], bounds[3] + 1):
        blocked.add((west, yy))
    taken = set()  # tiles holding a placed belt, underground or splitter: never freed again

    class Placed(list):
        def append(self, e):
            taken.update(_tiles(e, size_of))
            super().append(e)
    extra = Placed()  # splitters, merge belts, staircase links

    def free(t):
        return (t not in blocked and bounds[0] < t[0] < bounds[2] and bounds[1] < t[1] < bounds[3]
                and not (bus and t[0] <= bus_w))  # (stations stay out of the bus; belts may cross it)

    def find_spot(around, cells, xs_back=range(3, 40), ys_off=(0, 2, -2, 4, -4, 6, -6, 8, -8, 10, -10, 12, -12)):
        """a free place for a station: cells are offsets that must all be free"""
        for dx in xs_back:
            for dy in ys_off:
                o = (around[0] - dx, around[1] + dy)
                if all(free((o[0] + cx, o[1] + cy)) for cx, cy in cells):
                    return o
        raise ComposeError(f"no room for a station near {around}")

    routes = []
    merges = set()  # merge station tiles: the only belts things may side-load onto

    def route(starts, goal, goal_dir):
        in_time()
        before = router._step(goal, goal_dir, -1)
        if before in taken:
            raise ComposeError(f"the way into {goal} is already used by another belt")
        blocked.discard(before)
        starts = [(t, d) for t, d in starts if t not in taken]
        for t, _ in starts:
            blocked.discard(t)
        try:
            r = router.route(blocked, starts, goal, goal_dir, bounds, ug_max, spans)
        except router.RouteError as e:
            raise ComposeError(str(e))
        r = _cut_loops(r)
        seen = [t for t, _, _ in r]
        if len(set(seen)) != len(seen):
            import os
            if os.environ.get("BPGEN_DEBUG"):
                print("SELF", goal, r)
            raise ComposeError(f"a belt route crosses itself near {goal}")
        clash = [t for t, _, _ in r if t in taken]
        if clash:
            raise ComposeError(f"a belt would be laid over another at {clash[0]}")
        router.reserve(r, blocked, spans)
        taken.update(t for t, _, _ in r)
        routes.append(r)
        return r

    # ---- 0) producer side first: a splitter staircase after each output, one leg per consumer feed -------------
    if not bus:
        leg_starts = {}
        leg_splitters = {}  # block -> its staircase splitters, top first (priorities set once the legs are known)
        for b in blocks:
            k = len(feeds.get(id(b), []))
            if not k:
                continue
            end = b.at(b.sinks[0]["position"])
            sx, sy = end[0] + 1, end[1]
            starts = []
            if k == 1:
                starts = [(sx, sy)]
            else:
                for i in range(k - 1):  # splitter i: tiles (sx+2i, sy+i), (sx+2i, sy+i+1)
                    px, py = sx + 2 * i, sy + i
                    if i:
                        blocked.add((px - 1, py))  # belt from the previous splitter's lower output
                        extra.append({"name": belt, "position": {"x": px - 0.5, "y": py + 0.5}, "direction": EAST})
                    for t in ((px, py), (px, py + 1)):
                        if t in solid:
                            raise ComposeError(f"no room for {b.name}'s output splitters")
                        blocked.add(t)
                        solid.add(t)
                    spl = {"name": splitter, "position": {"x": px + 0.5, "y": py + 1.0}, "direction": EAST}
                    extra.append(spl)
                    leg_splitters.setdefault(id(b), []).append(spl)
                    starts.append((px + 1, py))
                starts.append((sx + 2 * (k - 2) + 1, sy + k - 1))
            for t in starts:
                blocked.add(t)
            leg_starts[id(b)] = starts
        # keep a strip east of every staircase clear for its legs to leave by
        keep = {}  # start -> the tiles in front of it, held until its own leg is routed
        for st in leg_starts.values():
            for (x, y) in st:
                keep[(x, y)] = {(x + dx, y) for dx in range(1, 4) if free((x + dx, y))}
                blocked |= keep[(x, y)]

    # ---- 1) consumer side: splitter stations to the rows, merge stations ---------------------------------------
    for g in groups:
        b = g["block"]
        goals = sorted((b.at(s["position"]) for s in g["sources"]), key=lambda t: t[1])
        if len(goals) == 2:
            mid = (goals[0][0], (goals[0][1] + goals[1][1]) // 2)
            box = [(cx, cy) for cx in range(-1, 4) for cy in range(-1, 3)]  # room to leave in any direction
            sx, sy = find_spot(mid, box, xs_back=range(5, 30))
            blocked.update({(sx, sy), (sx, sy + 1), (sx - 1, sy)})  # (sx-1, sy): its feed comes in there
            extra.append({"name": splitter, "position": {"x": sx + 0.5, "y": sy + 1.0}, "direction": EAST})
            route([((sx + 1, sy), None)], goals[0], EAST)  # any first direction: a turn keeps the lanes
            route([((sx + 1, sy + 1), None)], goals[1], EAST)
            g["feed"] = ((sx, sy), EAST)  # enter the splitter's upper input moving east
        else:
            g["feed"] = (goals[0], EAST)
    for g in groups:
        if not g.get("merge"):
            continue
        fx, fy = g["feed"][0]
        box = [(cx, cy) for cx in range(-1, 3) for cy in range(-2, 3)]
        mx, my = find_spot((fx, fy), box, xs_back=range(3, 30))
        blocked.update({(mx, my), (mx, my - 1), (mx, my + 1)})  # the lane feeds come in from above and below
        merges.add((mx, my))
        extra.append({"name": belt, "position": {"x": mx + 0.5, "y": my + 0.5}, "direction": EAST})
        route([((mx + 1, my), None)], g["feed"][0], g["feed"][1])
        left, right = g["lanes"]
        g["lane_feeds"] = [(left, ((mx, my), SOUTH)), (right, ((mx, my), NORTH))]  # from the north, from the south

    # what each group's items must reach: (item, goal, dir, group, producer copy or None for any)
    targets = []
    for g in groups:
        if g.get("merge"):
            targets += [(it, goal, d, g, None) for it, (goal, d) in g["lane_feeds"] if it]
        elif g.get("raw_only"):
            targets.append((None, g["feed"][0], g["feed"][1], g, None))  # one raw belt as the player brings it
        else:
            it = next(l for l in g["lanes"] if l)
            targets.append((it, g["feed"][0], g["feed"][1], g, None))
    # merge splitters: an item a group takes from several producer copies arrives on several belts; a chain of
    # splitters with one output used turns them into the one belt the group needs (splitters keep lanes)
    merged = []
    for it, goal, d, g, _ in targets:
        ids = (g.get("copies") or {}).get(it) or []
        if len(ids) < 2:
            merged.append((it, goal, d, g, None))
            continue
        out_goal, out_dir = goal, d
        for i in range(len(ids) - 1, 0, -1):  # from the group backwards: the last splitter feeds the group
            box = [(cx, cy) for cx in range(-1, 3) for cy in range(-1, 3)]
            sx, sy = find_spot(out_goal, box, xs_back=range(4, 30))
            blocked.update({(sx, sy), (sx, sy + 1), (sx - 1, sy), (sx - 1, sy + 1), (sx + 1, sy + 1)})
            extra.append({"name": splitter, "position": {"x": sx + 0.5, "y": sy + 1.0}, "direction": EAST})
            route([((sx + 1, sy), None)], out_goal, out_dir)  # its lower output stays empty: all goes out the top
            merged.append((it, (sx, sy + 1), EAST, g, ids[i]))  # one copy into the lower input
            out_goal, out_dir = (sx, sy), EAST  # the rest (one copy, or the next splitter) into the upper
        merged.append((it, out_goal, out_dir, g, ids[0]))
    targets = merged

    if bus:
        # ---- the bus: columns, taps and the legs to the consumers' feeds ---------------------------------------
        raw_of = {(id(g), it): bd for bd in raw_bundles for g, it in bd["members"]}
        legs = []
        for it, goal, d, g, _ in targets:
            if it is None or it not in producers:
                legs.append({"col": raw_col[id(raw_of[(id(g), it)])], "want": goal[1], "goal": goal, "d": d, "g": g,
                             "rank": _rank(g["block"], feeds)})
        for b in made:
            mine = sorted(((t[1], t[2], t[3]) for t in targets
                           if t[3] in [g for g, _ in feeds[id(b)]] and t[0] == b.item and t[4] in (None, id(b))),
                          key=lambda x: x[0][1])
            if len(mine) != len(feeds[id(b)]):
                raise ComposeError(f"{b.name}: {len(feeds[id(b)])} consumers but {len(mine)} feeds")
            for goal, d, g in mine:
                if g["block"].y0 <= b.y0:
                    raise ComposeError(f"{g['block'].name} sits above {b.name}, which feeds it")
                legs.append({"col": made_col[id(b)], "want": goal[1], "goal": goal, "d": d, "g": g,
                             "rank": _rank(g["block"], feeds)})
        bus_layout.alloc_rows(legs, cols_bus)
        if notes is not None:
            notes.append("MAIN BUS: " + str(len(cols_bus)) + " belts side by side on the west, flowing south: " +
                         ", ".join(c.name + (" (brought in from the north)" if c.kind == "raw" else "") for c in cols_bus) +
                         ". Each block takes what it needs from them (a splitter on the belt, a feed to its west edge)"
                         " and puts what it makes onto its own belt.")
        bus_ents, bus_spans = bus_layout.build(cols_bus, belt, ug_name, splitter, bus_w)
        for e in bus_ents:
            extra.append(e)
            blocked.update(_tiles(e, size_of))
        spans |= bus_spans
        for bd in raw_bundles:
            c = raw_col[id(bd)]
            sources.append({"kind": "belt", "position": {"x": c.x + 0.5, "y": c.head + 0.5}, "lanes": list(bd["lanes"]),
                            "rates": bd["rates"], "from": "north"})
        holds = {}  # the way out of each leg's corridor, kept clear until its own route is laid
        for leg in legs:
            holds[id(leg)] = {(bus_w + dx, leg["row"]) for dx in range(4)} - blocked
            blocked.update(holds[id(leg)])
        for b in made:  # each block's output to the head of its column (from the east, down in the gap below it)
            c = made_col[id(b)]
            end = b.at(b.sinks[0]["position"])
            route([((end[0] + 1, end[1]), None)], (c.x, c.head), 12)
        for leg in sorted(legs, key=lambda l: l["row"]):
            blocked.difference_update(holds[id(leg)])
            route([((bus_w, leg["row"]), None)], leg["goal"], leg["d"])

    # ---- 2) producer side: route each staircase leg to its consumer ------------------------------------------
    if not bus:
        for b in blocks:
            legs = [(g, it) for g, it in feeds.get(id(b), [])]
            if not legs:
                continue
            k = len(legs)
            starts = leg_starts[id(b)]
            # the topmost leg to the topmost consumer keeps routes from crossing at the start
            goals = sorted(((t[1], t[2], t[3]) for t in targets
                            if t[3] in [g for g, _ in legs] and t[0] == b.item and t[4] in (None, id(b))),
                           key=lambda x: x[0][1])
            mine = [(goal, d, g) for goal, d, g in goals]
            if len(mine) != k:
                raise ComposeError(f"{b.name}: {k} consumers but {len(mine)} feeds")
            order = sorted(range(k), key=lambda i: starts[i][1])
            import os
            if os.environ.get("BPGEN_DEBUG"):
                print("LEGS", b.name, [(starts[i], g["block"].name, goal) for i, (goal, d, g) in zip(order, mine)])
            for start, (goal, d, g) in zip([starts[i] for i in order], mine):
                blocked.difference_update(keep.pop(start, ()))
                route([(start, None)], goal, d)
            # the deepest consumer (nearest the final product) gets served first: an intermediate block running ahead
            # of what's needed (machine counts round up) would otherwise take an ingredient both need and starve it
            # until its own output belt is full. Splitter i's upper output is leg i, its lower output the rest.
            spls = leg_splitters.get(id(b), [])
            if spls:
                legs_in_order = [mine[order.index(i)] for i in range(k)]
                j = max(range(k), key=lambda i: (legs_in_order[i][2]["block"].depth, i))
                for i, spl in enumerate(spls):
                    if i < j:
                        spl["output_priority"] = "right"  # (facing east: right = the lower output, on down the chain)
                    elif i == j:
                        spl["output_priority"] = "left"   # its own leg

    # ---- 3) raw items from the west edge -----------------------------------------------------------------------
    if not bus:
        # feeds carrying the same thing share one input belt (as many as one belt carries), split at the west edge
        sources = []
        cap = data.raw["transport-belt"][belt]["speed"] * 480 * 0.95
        raw_feeds = {}
        for it, goal, d, g, _ in targets:
            if it is not None and it in producers:
                continue
            lanes = tuple(g["lanes"]) if it is None else (it, it)
            rates = dict(g["rates"]) if it is None else {it: g["rates"].get(it, 0)}
            raw_feeds.setdefault(lanes, []).append((goal, d, rates))
        W = bounds[0] + 1
        for lanes, fl in raw_feeds.items():
            fl.sort(key=lambda f: f[0][1])
            # one shared input carries what its lanes carry: an item on one lane gets half the belt
            item_cap = {it: cap * (1 if lanes[0] == lanes[1] else 0.5) for it in lanes if it}
            bundles, cur, load = [], [], {}
            for f in fl:
                if cur and any(load.get(it, 0) + r > item_cap.get(it, cap) for it, r in f[2].items()):
                    bundles.append(cur)
                    cur, load = [], {}
                cur.append(f)
                for it, r in f[2].items():
                    load[it] = load.get(it, 0) + r
            bundles.append(cur)
            for bundle in bundles:
                rates = {}
                for _, _, r in bundle:
                    for k2, v in r.items():
                        rates[k2] = rates.get(k2, 0) + v
                k = len(bundle)
                if k == 1:
                    goal, d, _ = bundle[0]
                    starts = [((W, yy), [EAST]) for yy in range(bounds[1] + 1, bounds[3]) if free((W, yy))]
                    starts.sort(key=lambda s: abs(s[0][1] - goal[1]))
                    r = route(starts[:40], goal, d)
                    src = r[0][0]
                else:
                    # input belt at (W, y0), then a splitter staircase like a producer's
                    mid = bundle[k // 2][0][1]
                    cells = [(1 + 2 * i, i) for i in range(k - 1)] + [(1 + 2 * i, i + 1) for i in range(k - 1)] + \
                            [(2 * i, i) for i in range(1, k - 1)] + [(2 + 2 * i, i) for i in range(k - 1)] + [(0, 0)]
                    y0 = next((y for off in range(0, 60) for y in (mid - off, mid + off)
                               if all(free((W + cx, y + cy)) for cx, cy in cells + [(2 * (k - 2) + 2, k - 1)])), None)
                    if y0 is None:
                        raise ComposeError(f"no room at the west edge to split the {'|'.join(l for l in lanes if l)} input")
                    ents_in = {"name": belt, "position": {"x": W + 0.5, "y": y0 + 0.5}, "direction": EAST}
                    extra.append(ents_in)
                    blocked.add((W, y0))
                    starts = []
                    for i in range(k - 1):
                        px, py = W + 1 + 2 * i, y0 + i
                        if i:
                            blocked.add((px - 1, py))
                            extra.append({"name": belt, "position": {"x": px - 0.5, "y": py + 0.5}, "direction": EAST})
                        blocked.update({(px, py), (px, py + 1)})
                        extra.append({"name": splitter, "position": {"x": px + 0.5, "y": py + 1.0}, "direction": EAST})
                        starts.append((px + 1, py))
                    starts.append((W + 1 + 2 * (k - 2) + 1, y0 + k - 1))
                    held = {}
                    for (x, y) in starts:
                        blocked.add((x, y))
                        held[(x, y)] = {(x + dx, y) for dx in range(1, 3) if free((x + dx, y))}
                        blocked |= held[(x, y)]
                    for (goal, d, _), start in zip(bundle, sorted(starts, key=lambda t: t[1])):
                        blocked.difference_update(held.pop(start))
                        route([(start, None)], goal, d)
                    src = (W, y0)
                sources.append({"kind": "belt", "position": {"x": src[0] + 0.5, "y": src[1] + 0.5},
                                "lanes": list(lanes), "rates": rates})
    no_under = set(solid) if bus else None  # (beside a bus pipes go around the blocks: under them a route can cross its own line)
    # ---- 4) fluids made here: pipes from the producer's output to every block that takes it -----------------------
    pipes = []  # (tile, "pipe" | "ptg", direction)
    fluid_inputs = [(b, s, b.at(s["position"])) for b in blocks for s in b.sources if s["kind"] == "fluid"]
    foreign = {}  # tile -> fluid, for every pipe tile and every fluid input a pipe will come to
    for b, s, t in fluid_inputs:
        foreign[t] = s["fluid"]
    pspans = set()
    pipe_checks = []  # (fluid, root tiles, goals): every goal must connect to a root
    for fluid, makers in fluid_producers.items():
        if fluid not in fluid_users and len(makers) < 2:
            continue  # nobody here takes it: its pipe ends at the blueprint's edge (an output)
        maker, sink = makers[0]
        end = maker.at(sink["position"])
        network = {end}
        pipe_checks.append((fluid, [end], []))
        foreign[end] = fluid
        if (end[0] + 1, end[1]) not in taken:
            blocked.discard((end[0] + 1, end[1]))  # the way out of the block's output pipe
        # more blocks making the same fluid (a refinery and a converter): join their outputs to this network first
        for other, osink in makers[1:]:
            oend = other.at(osink["position"])
            goal = (oend[0] + 1, oend[1])
            foreign[oend] = foreign[goal] = fluid
            blocked.discard(goal)
            # the other block's output pipe is west of the goal
            path = _route_pipe(network, goal, fluid, blocked, foreign, bounds, pspans, inner=inner, into=12, no_under=no_under)
            pipe_checks[-1][2].append(goal)
            for t, kind, d in path:
                if kind == "pipe":
                    network.add(t)
                foreign[t] = fluid
                blocked.add(t)
                taken.add(t)
            pipes += path
            _claim_spans(path, pspans)
        for b, s, goal in sorted(fluid_inputs, key=lambda x: abs(x[2][0] - end[0]) + abs(x[2][1] - end[1])):
            if s["fluid"] != fluid:
                continue
            path = _route_pipe(network, goal, fluid, blocked, foreign, bounds, pspans, inner=inner, no_under=no_under)
            pipe_checks[-1][2].append(goal)
            for t, kind, d in path:
                if kind == "pipe":
                    network.add(t)  # a pipe-to-ground only connects at its open side: no branching off it
                foreign[t] = fluid
                blocked.add(t)
                taken.add(t)
            pipes += path
            _claim_spans(path, pspans)
            s["routed"] = True
    # fluids brought in: one input at the west edge per fluid, piped on to every block that takes it. One fluid's
    # pipe can wall off another's input (they may not touch), so when one gets stuck try another order
    raw_fluids = {}
    for b, s, t in fluid_inputs:
        if not s.get("routed"):
            raw_fluids.setdefault(s["fluid"], []).append((b, s, t))
    import itertools
    saved = (set(blocked), dict(foreign), set(taken), list(pipes), set(pspans), list(pipe_checks), list(sources))
    orders = list(itertools.permutations(sorted(raw_fluids, key=lambda f: -len(raw_fluids[f]))))[:24]
    last_err = None
    for order in orders:
        blocked, foreign, taken = set(saved[0]), dict(saved[1]), set(saved[2])
        pipes, pspans, pipe_checks, sources = list(saved[3]), set(saved[4]), list(saved[5]), list(saved[6])
        try:
            for fluid in order:
                ins = raw_fluids[fluid]
                # enter at the point of the blocks' outline nearest the blocks that take it (not always the west edge)
                gx = sorted(t[0] for _, _, t in ins)[len(ins) // 2]
                gy = sorted(t[1] for _, _, t in ins)[len(ins) // 2]
                ix1, iy1, ix2, iy2 = inner
                ring = [(x, iy1) for x in range(ix1, ix2 + 1)] + [(x, iy2) for x in range(ix1, ix2 + 1)] +                [(ix1, y) for y in range(iy1, iy2 + 1)] + [(ix2, y) for y in range(iy1, iy2 + 1)]
                ring = [t for t in ring if t not in blocked
                        and all(foreign.get((t[0] + dx, t[1] + dy), fluid) == fluid for dx, dy in STEP.values())]
                if not ring:
                    raise ComposeError(f"no room on the outline for the {fluid} input")
                entry = min(ring, key=lambda t: (abs(t[0] - gx) + abs(t[1] - gy), t))
                foreign[entry] = fluid
                blocked.add(entry)
                network = {entry}  # the player's pipe comes to this tile
                pipe_checks.append((fluid, [(entry[0] + dx, entry[1] + dy) for dx, dy in STEP.values()], []))
                rate = 0.0
                for b, s, goal in sorted(ins, key=lambda x: abs(x[2][0] - entry[0]) + abs(x[2][1] - entry[1])):
                    path = _route_pipe(network, goal, fluid, blocked, foreign, bounds, pspans, inner=inner, no_under=no_under)
                    pipe_checks[-1][2].append(goal)
                    for t, kind, d in path:
                        if kind == "pipe":
                            network.add(t)  # a pipe-to-ground only connects at its open side: no branching off it
                        foreign[t] = fluid
                        blocked.add(t)
                        taken.add(t)
                    pipes += path
                    _claim_spans(path, pspans)
                    rate += (s.get("rates") or {}).get(fluid, 0)
                sources.append({"kind": "fluid", "position": {"x": entry[0] + 0.5, "y": entry[1] + 0.5}, "fluid": fluid,
                                "rates": {fluid: rate}})
            break
        except ComposeError as e:
            last_err = e
    else:
        if last_err:
            raise last_err

    for b, s, t in fluid_inputs:
        s.pop("routed", None)

    # ---- entities ---------------------------------------------------------------------------------------------
    ents = [b.shifted(e) for b in blocks for e in b.ents] + extra
    for t, kind, d in pipes:
        e = {"name": planner.PIPE if kind == "pipe" else planner.UNDERGROUND,
             "position": {"x": t[0] + 0.5, "y": t[1] + 0.5}}
        if kind != "pipe":
            e["direction"] = d
        ents.append(e)
    for r in routes:
        for tile, kind, d in r:
            e = {"name": belt if kind == "belt" else ug_name, "position": {"x": tile[0] + 0.5, "y": tile[1] + 0.5},
                 "direction": d}
            if kind != "belt":
                e["ug_type"] = "input" if kind == "ug-in" else "output"
            ents.append(e)
    # outputs nobody here consumes (science packs when there are no labs) stay as sinks
    sinks = []
    for b in blocks:
        if b.sinks and b.sinks[0].get("kind", "belt") == "belt" and not feeds.get(id(b)):
            tx, ty = b.at(b.sinks[0]["position"])
            sinks.append({"kind": "belt", "position": {"x": tx + 0.5, "y": ty + 0.5}, "item": b.item})
        for sk in b.sinks:  # fluids nobody here takes: their pipes end at the edge (outputs of the blueprint)
            fluid = sk.get("item") or b.item
            # several makers of it are joined: one output, at the first
            if sk.get("kind") == "fluid" and fluid not in fluid_users and fluid_producers[fluid][0][1] is sk:
                tx, ty = b.at(sk["position"])
                sinks.append({"kind": "fluid", "position": {"x": tx + 0.5, "y": ty + 0.5}, "item": fluid})

    # ---- power: join every block's poles ------------------------------------------------------------------------
    poles = [[(e["position"]["x"], e["position"]["y"]) for e in (b.shifted(x) for x in b.ents) if e["name"] == planner.POLE]
             for b in blocks]
    poles = [p for p in poles if p]
    if poles:
        joined = list(poles[0])
        rest = poles[1:]
        while rest:
            in_time()
            i, (a, c) = min(((i, min(((p, q) for p in joined for q in ps), key=lambda pq: _d2(*pq)))
                             for i, ps in enumerate(rest)), key=lambda t: _d2(*t[1]))
            for px, py in _pole_line(a, c, blocked):
                ents.append({"name": planner.POLE, "position": {"x": px, "y": py}})
                joined.append((px, py))
            joined += rest.pop(i)

    check_belts(ents, merges, size_of, data)
    for src in sources:  # the player brings each input belt in from the west: that tile must be free
        if src["kind"] == "belt":
            t = (math.floor(src["position"]["x"]) - 1, math.floor(src["position"]["y"]))
            if src.get("from") == "north":
                t = (t[0] + 1, t[1] - 1)
            if t in taken or t in solid:
                raise ComposeError(f"the way into the input at {t} is blocked")
    check_pipes(ents, pipe_checks)

    # ---- the blueprint starts at (0, 0) -------------------------------------------------------------------------
    mx = math.floor(min(e["position"]["x"] for e in ents))
    my = math.floor(min(e["position"]["y"] for e in ents))

    def move(p):
        return {"x": p["x"] - mx, "y": p["y"] - my}
    ents = [dict(e, position=move(e["position"])) for e in ents]
    sources = [dict(s, position=move(s["position"])) for s in sources]
    sinks = [dict(s, position=move(s["position"])) for s in sinks]
    return ents, sources, sinks


STEP = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), 12: (-1, 0)}


def check_belts(ents, merges, size_of, data):
    """every belt end must feed straight on, turn, or side-load only onto a merge station; nothing head-on.
    A layout that fails this would mix items where they don't belong. Kinds come from the prototypes, so modded
    belts with any name are checked too."""
    def kind(name):
        for t in ("transport-belt", "underground-belt", "splitter"):
            if name in data.raw.get(t, {}):
                return t
        return None
    at = {}
    for e in ents:
        if kind(e["name"]):
            for t in _tiles(e, size_of):
                at[t] = e
    feeds = {}  # tile -> directions of the belts pointing into it
    for t, e in at.items():
        k = kind(e["name"])
        if k == "transport-belt" or (k == "underground-belt" and e.get("ug_type") == "output"):
            dx, dy = STEP[e.get("direction", 0)]
            feeds.setdefault((t[0] + dx, t[1] + dy), []).append(e.get("direction", 0))
    for t, ins in feeds.items():
        e = at.get(t)
        if not e or kind(e["name"]) != "transport-belt":
            continue
        d = e.get("direction", 0)
        if any((x + 8) % 16 == d for x in ins):
            raise ComposeError(f"belts meet head-on at {t}")
        sides = [x for x in ins if x != d]
        if sides and (d in ins or len(sides) >= 2) and t not in merges:
            raise ComposeError(f"a belt side-loads onto another at {t}")
    # every underground entrance must meet its own exit: the first exit of its kind ahead, facing the same way
    for t, e in at.items():
        if kind(e["name"]) == "underground-belt" and e.get("ug_type") == "input":
            d = e.get("direction", 0)
            dx, dy = STEP[d]
            for k in range(1, data.raw["underground-belt"][e["name"]].get("max_distance", 10) + 2):
                f = at.get((t[0] + dx * k, t[1] + dy * k))
                if f and f["name"] == e["name"] and f.get("direction", 0) == d:
                    if f.get("ug_type") == "output":
                        break
                    raise ComposeError(f"two underground entrances in a row at {t}")
            else:
                raise ComposeError(f"an underground entrance at {t} has no exit")


def _cut_loops(r):
    """A* can bring a route back over a tile it already used (e.g. under itself by underground and out on the
    same tile). That stretch is a loop: drop it and keep one piece on the tile, facing the way the route leaves."""
    while True:
        first = {}
        cut = None
        for j, (t, kind, d) in enumerate(r):
            if t in first:
                cut = (first[t], j)
                break
            first[t] = j
        if not cut:
            return r
        i, j = cut
        _, kind_j, d_j = r[j]
        if r[i][1] != "belt":
            return r  # an underground entrance at the first visit: leave it, the caller rejects the route
        if kind_j == "ug-in":
            if i and r[i - 1][2] != d_j:
                return r  # would feed the entrance from its side
            piece = (r[i][0], "ug-in", d_j)
        else:
            piece = (r[i][0], "belt", d_j)  # a belt or an underground exit there becomes a plain belt
        r = r[:i] + [piece] + r[j + 1:]


def _claim_spans(path, pspans):
    """pipe-to-ground pairs claim the line under them: another pair on the same line would pair wrongly"""
    for (pa, ka, da), (pb, kb, db) in zip(path, path[1:]):
        if ka == kb == "ptg" and da != db:
            axis = db % 8
            step = STEP[db]
            k = abs(pb[0] - pa[0]) + abs(pb[1] - pa[1])
            for i in range(k + 1):
                c = (pa[0] + step[0] * i, pa[1] + step[1] * i)
                pspans.add((axis, c[1] if axis else c[0], c[0] if axis else c[1]))


def _joins(a, b):
    """do two consecutive path pieces connect? (tile, kind, dir); a ptg's dir is its open side"""
    (ta, ka, da), (tb, kb, db) = a, b
    step = (tb[0] - ta[0], tb[1] - ta[1])
    if abs(step[0]) + abs(step[1]) > 1:  # an underground jump: entrance then exit, facing apart along it
        return ka == kb == "ptg" and da == (db + 8) % 16
    d = next(k for k, v in STEP.items() if v == step)
    return (ka == "pipe" or da == d) and (kb == "pipe" or db == (d + 8) % 16)


def _route_pipe(network, goal, fluid, blocked, foreign, bounds, pspans, max_ug=None, inner=None, into=EAST,
                no_under=None):
    """A* for a pipe from any tile of `network` to `goal`. A pipe tile may not touch another fluid's pipe or fluid
    input; pipe-to-ground pairs (entrance facing back, exit facing on) jump up to max_ug tiles over anything.
    into: which way from the goal the block's pipe is (a pipe-to-ground ending there must open that way).
    -> [(tile, "pipe" | "ptg", direction)] (direction: the pipe-to-ground's open side)"""
    import heapq
    import itertools
    max_ug = max_ug or min(planner.PIPE_UG_MAX - 1, 12)
    x1, y1, x2, y2 = bounds
    tie = itertools.count()

    def ok(n, prev):
        if n != goal and (n in blocked or not (x1 < n[0] < x2 and y1 < n[1] < y2)):
            return False
        for d in STEP.values():
            q = (n[0] + d[0], n[1] + d[1])
            if q != prev and foreign.get(q, fluid) != fluid:
                return False
        return True

    def h(t):
        return abs(t[0] - goal[0]) + abs(t[1] - goal[1])

    def outside(t):  # leaving the blocks' outline costs extra: pipes go through the channels, not around
        return 1.5 if inner and not (inner[0] <= t[0] <= inner[2] and inner[1] <= t[1] <= inner[3]) else 0
    open_, came, best = [], {}, {}
    for t in network:
        st = (t, "pipe", None)
        best[st] = 0
        came[st] = None
        heapq.heappush(open_, (h(t), next(tie), 0, st))
    while open_:
        _, _, g, st = heapq.heappop(open_)
        if g > best.get(st, 1e18):
            continue
        t, mode, d = st
        if t == goal:
            path = []
            while came[st] is not None:
                prev, piece = came[st]
                path += piece
                st = prev
            path = path[::-1]
            start = (st[0], "pipe", None)
            for a, b in zip([start] + path, path):
                if not _joins(a, b):
                    import os
                    if os.environ.get("BPGEN_DEBUG"):
                        print("BADJOIN", fluid, a, b, "path", [start] + path)
                    raise ComposeError(f"{fluid} pipe pieces don't join at {a[0]}-{b[0]}")
            return path
        dirs = [d] if mode == "exit" else list(STEP)
        mine = set()  # the tiles this path already uses: it may not come back over them
        walk = st
        while walk is not None and came.get(walk):
            prev, piece = came[walk]
            mine.update(t2 for t2, _, _ in piece)
            walk = prev
        for nd in dirs:
            dx, dy = STEP[nd]
            n = (t[0] + dx, t[1] + dy)
            if n in mine:
                continue
            if ok(n, t):
                ns = (n, "pipe", None)
                c = g + 1 + outside(n)
                if c < best.get(ns, 1e18):
                    best[ns], came[ns] = c, (st, [(n, "pipe", None)])
                    heapq.heappush(open_, (c + h(n), next(tie), c, ns))
            # a pipe-to-ground pair: entrance at n, exit k tiles further on
            if n == goal or n in blocked or not (x1 < n[0] < x2 and y1 < n[1] < y2):
                continue
            back = (nd + 8) % 16
            for k in range(2, max_ug + 1):
                ex = (n[0] + dx * k, n[1] + dy * k)
                if ex in mine:
                    continue
                if ex == goal:
                    if nd != into:
                        continue  # at the goal an exit must open into the block's pipe
                elif ex in blocked or not (x1 < ex[0] < x2 and y1 < ex[1] < y2):
                    continue
                cells = {(nd % 8, n[1] if nd % 8 else n[0], (n[0] if nd % 8 else n[1]) + i * (dx or dy)) for i in range(k + 1)}
                if cells & pspans:
                    continue
                if no_under and any((n[0] + dx * i, n[1] + dy * i) in no_under for i in range(1, k)):
                    continue
                ns = (ex, "exit", nd)
                cost = g + 3 + 0.2 * k + outside(ex) * k
                if cost < best.get(ns, 1e18):
                    best[ns], came[ns] = cost, (st, [(ex, "ptg", nd), (n, "ptg", back)])
                    heapq.heappush(open_, (cost + h(ex), next(tie), cost, ns))
    raise ComposeError(f"no pipe route for {fluid} to {goal}")


def check_pipes(ents, checks):
    """every routed fluid input must be connected to its source by pipes (pipe-to-ground joins only at its open
    side and to its partner underground)"""
    pieces = {}
    for e in ents:
        if e["name"] in (planner.PIPE, planner.UNDERGROUND):
            pieces[(math.floor(e["position"]["x"]), math.floor(e["position"]["y"]))] = e

    def opens(t):
        e = pieces[t]
        return set(STEP) if e["name"] == planner.PIPE else {e.get("direction", 0)}

    def links(t):
        out = []
        for d in opens(t):
            n = (t[0] + STEP[d][0], t[1] + STEP[d][1])
            if n in pieces and (d + 8) % 16 in opens(n):
                out.append(n)
        e = pieces[t]
        if e["name"] == planner.UNDERGROUND:
            d = (e.get("direction", 0) + 8) % 16
            for k in range(1, planner.PIPE_UG_MAX + 2):
                n = (t[0] + STEP[d][0] * k, t[1] + STEP[d][1] * k)
                f = pieces.get(n)
                if f and f["name"] == planner.UNDERGROUND and f.get("direction", 0) == d:  # opens the far way
                    out.append(n)
                    break
        return out
    for fluid, roots, goals in checks:
        net, todo = set(), [r for r in roots if r in pieces]
        while todo:
            c = todo.pop()
            if c not in net:
                net.add(c)
                todo += links(c)
        for g in goals:
            if g not in net:
                import os
                if os.environ.get("BPGEN_DEBUG"):
                    allat = {}
                    for e in ents:
                        allat.setdefault((math.floor(e["position"]["x"]), math.floor(e["position"]["y"])), []).append(e["name"])
                    dup = {t: v for t, v in allat.items() if len(v) > 1 and t in pieces}
                    print("PIPEFAIL", fluid, g, "net size", len(net), "dups on pipe tiles", dup)
                raise ComposeError(f"the {fluid} pipe doesn't reach {g}")
