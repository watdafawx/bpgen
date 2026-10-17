"""Power poles: wires between a layout's poles, poles to cover what needs power, and lines of poles across gaps.

One implementation per problem:
  pole_wires    the copper wires a blueprint lists between its poles (a minimum spanning tree)
  power_up      poles on free tiles until every electric machine, beacon and inserter of a layout is covered and wired
  pole_line     poles on free tiles from pole a to pole b, each within wire reach of the last
  pole_chain    pole_line on tiles, as entities (None when stuck)
  link_networks poles joining a build's separate pole networks (stacked sections)
  link_to_grid  poles from the base's nearest powered pole to a new build's nearest one

The pole kind and its reach are planner's (configure() sets them from the game's data); they are read when called."""
import math

from bpgen import geom

POLE_COPPER = 5  # defines.wire_connector_id.pole_copper


def d2(p, q):
    """squared distance"""
    return (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2


def pole_wires(entities):
    """Copper wires: a minimum spanning tree over all poles within wire reach (fewest, shortest wires that
    connect everything reachable).

    Poles built from a blueprint only get the wires the blueprint lists (unlike hand/script placement,
    which auto-connects), so without these most of the line has no power.
    """
    WIRE_REACH = planner.WIRE_REACH
    poles = [(i, e["position"]["x"], e["position"]["y"], WIRE_REACH[e["name"]]) for i, e in enumerate(entities, 1)
             if e["name"] in WIRE_REACH]
    edges = sorted(((x1 - x2) ** 2 + (y1 - y2) ** 2, i, j) for n, (i, x1, y1, r1) in enumerate(poles)
                   for (j, x2, y2, r2) in poles[n + 1:] if (x1 - x2) ** 2 + (y1 - y2) ** 2 <= min(r1, r2) ** 2)
    parent = {i: i for i, _, _, _ in poles}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    wires = []
    for _, i, j in edges:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
            wires.append([i, POLE_COPPER, j, POLE_COPPER])
    return wires


def power_up(ents, sources=(), sinks=()):
    """adds poles (POLE) on free tiles, within the layout's box grown by one, until every electric machine, beacon and
    inserter overlaps a pole's supply area and the poles' wires join them all: most uncovered entities first, then a
    pole at a time from the nearest unwired group towards the rest. Tiles where a belt or pipe comes in or goes out
    stay free. -> poles added (raises PlanError when there is no room)"""
    data, POLE, POLE_REACH, WIRE_REACH, PlanError = (planner._DATA, planner.POLE, planner.POLE_REACH, planner.WIRE_REACH,
                                                     planner.PlanError)
    if data is None or POLE not in data.raw.get("electric-pole", {}) or POLE not in WIRE_REACH:
        return 0  # (pole_wires couldn't wire it anyway)
    size_of = geom.sizer(data)
    taken = set()
    for e in ents:
        taken |= geom.tiles(e, size_of)
    xs, ys = [t[0] for t in taken], [t[1] for t in taken]
    box = (min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1)
    for src in sources:  # (the tile a belt or pipe comes in from, and the one an output leaves onto)
        x, y = math.floor(src["position"]["x"]), math.floor(src["position"]["y"])
        taken |= {(x - 1, y), (x, y)}
    for k in sinks:
        x, y = math.floor(k["position"]["x"]), math.floor(k["position"]["y"])
        taken |= {(x + 1, y), (x, y + 1)}

    def proto(name):
        for t in ("assembling-machine", "furnace", "inserter", "beacon", "electric-pole"):
            if name in data.raw.get(t, {}):
                return t, data.raw[t][name]
        return None, None
    poles = []  # (x, y, supply, wire reach) of pole centers
    needs = []  # tile boxes (x1, y1, x2, y2) of what needs power
    for e in ents:
        t, pr = proto(e["name"])
        if t == "electric-pole":
            poles.append((e["position"]["x"], e["position"]["y"], pr.get("supply_area_distance", 3.5),
                          WIRE_REACH.get(e["name"], 0)))
        elif t and (pr.get("energy_source") or {}).get("type") == "electric":
            ts = geom.tiles(e, size_of)
            needs.append((min(a for a, _ in ts), min(b for _, b in ts), max(a for a, _ in ts) + 1,
                          max(b for _, b in ts) + 1))
    supply = data.raw["electric-pole"][POLE].get("supply_area_distance", 3.5)
    wire = WIRE_REACH.get(POLE, POLE_REACH)  # (as far as pole_wires will wire it)

    def covers(px, py, sup, b):
        return abs(px - (b[0] + b[2]) / 2) < sup + (b[2] - b[0]) / 2 and abs(py - (b[1] + b[3]) / 2) < sup + (b[3] - b[1]) / 2

    def free(t):
        return t not in taken and box[0] <= t[0] <= box[2] and box[1] <= t[1] <= box[3]
    added = []

    def add(t):
        taken.add(t)
        poles.append((t[0] + 0.5, t[1] + 0.5, supply, wire))
        added.append(t)
        ents.append({"name": POLE, "position": {"x": t[0] + 0.5, "y": t[1] + 0.5}})
    left = [b for b in needs if not any(covers(px, py, sp, b) for px, py, sp, _ in poles)]
    while left:
        count = {}
        r = math.ceil(supply)
        for b in left:
            for x in range(b[0] - r, b[2] + r):
                for y in range(b[1] - r, b[3] + r):
                    if free((x, y)) and covers(x + 0.5, y + 0.5, supply, b):
                        count[(x, y)] = count.get((x, y), 0) + 1
        if not count:
            raise PlanError("no room for a power pole within reach of some of its inserters (a row packed with them)")
        b0 = left[0]  # (ties: inside the layout's own box, then nearest the first thing left unpowered)
        best = max(count, key=lambda t: (count[t], box[0] < t[0] < box[2] and box[1] < t[1] < box[3],
                                         -abs(t[0] + 0.5 - (b0[0] + b0[2]) / 2) - abs(t[1] + 0.5 - (b0[1] + b0[3]) / 2),
                                         -t[1], -t[0]))
        add(best)
        left = [b for b in left if not covers(best[0] + 0.5, best[1] + 0.5, supply, b)]
    while True:  # wires: grow the first pole's group until it holds them all
        group, edge = {0}, [0]
        while edge:
            i = edge.pop()
            for j, q in enumerate(poles):
                if j not in group and (poles[i][0] - q[0]) ** 2 + (poles[i][1] - q[1]) ** 2 <= min(poles[i][3], q[3]) ** 2:
                    group.add(j)
                    edge.append(j)
        if len(group) == len(poles):
            return len(added)
        # a pole within reach of the group, nearer the closest pole outside it; when that is boxed in (a row of
        # big machines between), the fewest poles on free tiles around, each hop within wire reach
        _, i, j = min(((poles[i][0] - q[0]) ** 2 + (poles[i][1] - q[1]) ** 2, i, j) for i in group
                      for j, q in enumerate(poles) if j not in group)
        (ax, ay), (bx, by) = poles[i][:2], poles[j][:2]
        r = min(poles[i][3], wire)
        before = (ax - bx) ** 2 + (ay - by) ** 2
        spots = [t for t in near_tiles(ax, ay, r)
                 if free(t) and (t[0] + 0.5 - bx) ** 2 + (t[1] + 0.5 - by) ** 2 < before]
        if spots:
            add(min(spots, key=lambda t: ((t[0] + 0.5 - bx) ** 2 + (t[1] + 0.5 - by) ** 2, t[1], t[0])))
            continue
        prev = {}
        for k in group:
            for t in near_tiles(poles[k][0], poles[k][1], min(poles[k][3], wire)):
                if free(t):
                    prev.setdefault(t, None)
        goals = {t for k, q in enumerate(poles) if k not in group for t in near_tiles(q[0], q[1], min(q[3], wire))}
        frontier, goal = list(prev), None
        while frontier and goal is None:
            nxt = []
            for c in frontier:
                if c in goals:
                    goal = c
                    break
                for t in near_tiles(c[0] + 0.5, c[1] + 0.5, wire):
                    if t not in prev and free(t):
                        prev[t] = c
                        nxt.append(t)
            frontier = nxt
        if goal is None:
            raise PlanError("no free tiles to wire its power poles together")
        while goal is not None:
            add(goal)
            goal = prev[goal]


def near_tiles(x, y, r):
    """tiles whose centers are within r of (x, y)"""
    return [(tx, ty) for tx in range(math.floor(x - r), math.ceil(x + r) + 1)
            for ty in range(math.floor(y - r), math.ceil(y + r) + 1)
            if (tx + 0.5 - x) ** 2 + (ty + 0.5 - y) ** 2 <= r * r]


def pole_line(a, b, blocked, reach=None):
    """poles on free tiles, each within wire reach of the previous, from pole a to pole b"""
    reach = reach or planner.POLE_REACH - 0.5
    out, cur = [], a
    while d2(cur, b) > reach ** 2:
        # any free tile within wire reach: the one nearest the target (fewest poles), ties to the straightest
        r = math.ceil(reach)
        cands = []
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                t = (math.floor(cur[0]) + dx, math.floor(cur[1]) + dy)
                c = (t[0] + 0.5, t[1] + 0.5)
                if t not in blocked and d2(c, cur) <= reach ** 2 and d2(c, b) < d2(cur, b):
                    cands.append((round(d2(c, b), 3), abs(c[1] - cur[1]), c, t))
        if not cands:
            raise planner.PlanError("couldn't run power poles between the blocks")
        _, _, c, t = min(cands)
        blocked.add(t)
        out.append(c)
        cur = c
    return out


def pole_chain(a, b, blocked, reach=8):
    """medium poles from the pole on tile a to within wire reach of tile b, round what's in the way (pole_line). Their tiles join `blocked`. -> entities, or None (stuck)"""
    try:
        line = pole_line((a[0] + 0.5, a[1] + 0.5), (b[0] + 0.5, b[1] + 0.5), blocked, reach)
    except planner.PlanError:
        return None
    return [{"name": planner.POLE, "position": {"x": x, "y": y}} for x, y in line]


def link_networks(data, ents):
    """poles joining separate pole networks (stacked sections): each network to its nearest neighbour network"""
    size_of = geom.sizer(data)
    blocked = set()
    for e in ents:
        blocked |= geom.tiles(e, size_of)
    poles = [(e["position"]["x"], e["position"]["y"]) for e in ents if e["name"] == planner.POLE]
    reach2 = planner.POLE_REACH ** 2
    # networks = connected components of poles within wire reach
    groups = {i: {p} for i, p in enumerate(poles)}
    owner = {p: i for i, p in enumerate(poles)}
    for a in poles:
        for b in poles:
            if a < b and (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 <= reach2 and owner[a] != owner[b]:
                ga, gb = owner[a], owner[b]
                for q in groups[gb]:
                    owner[q] = ga
                groups[ga] |= groups.pop(gb)
    added = []
    nets = list(groups.values())
    while len(nets) > 1:
        a_net = nets.pop(0)
        best = min(((pa, pb, j) for j, net in enumerate(nets) for pa in a_net for pb in net),
                   key=lambda t: (t[0][0] - t[1][0]) ** 2 + (t[0][1] - t[1][1]) ** 2)
        pa, pb, j = best
        for x, y in pole_line(pa, pb, blocked):
            added.append({"name": planner.POLE, "position": {"x": x, "y": y}})
        nets[j] = nets[j] | a_net
    return added


def link_to_grid(ground, blocked, new_ents, pole, reach):
    """poles from the nearest existing pole to the nearest new one, when they are out of wire reach (None: no way
    round what's in between); only poles
    whose network has power when the snapshot says (a mall begun beside the bus may have poles of its own, wired
    to nothing)"""
    poles_old = [e for e in ground.snap["entities"] if e["type"] == "electric-pole" and not e.get("ghost")]
    if any(e.get("powered") for e in poles_old):
        poles_old = [e for e in poles_old if e.get("powered")]
    elif any(e.get("network") is not None for e in poles_old):  # (no power source in sight: the biggest network)
        sizes = {}
        for e in poles_old:
            sizes[e.get("network")] = sizes.get(e.get("network"), 0) + 1
        big = max(sizes, key=sizes.get)
        poles_old = [e for e in poles_old if e.get("network") == big]
    poles_new = [e for e in new_ents if e["name"] == pole or e.get("type") == "electric-pole"]
    if not poles_old or not poles_new:
        return []
    pos = lambda e: (e["position"]["x"], e["position"]["y"])  # noqa: E731
    a, b = min(((o, n) for o in poles_old for n in poles_new),
               key=lambda p: math.dist(pos(p[0]), pos(p[1])))
    if math.dist(pos(a), pos(b)) <= reach:
        return []
    # (each pole within wire reach of the last, round what's in the way)
    try:
        line = pole_line(pos(a), pos(b), blocked, reach - 0.5)
    except planner.PlanError:
        return None
    return [{"name": pole, "position": {"x": x, "y": y}} for x, y in line]


from bpgen import planner  # noqa: E402  (planner's globals are read when a function runs; last, as planner imports this module too)
