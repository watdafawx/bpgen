"""Build next to the player's base, from a snapshot taken in game (companion mod).

place() puts a plan into free ground (no buildings, obstacles, water or ore) near a seed point, taps the existing
belts that carry what each plan input needs (a splitter on the belt, then a routed belt), links power to the
nearest existing pole, and returns the new entities in world coordinates with a blueprint that snaps to the world
grid (absolute snapping), so it pastes exactly where it was placed.
"""
import math

from bpgen import chain, planner, router

DIR = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
BELT_TYPES = ("transport-belt", "underground-belt", "splitter")


def runs_tiles(runs):
    """{y: [[x1, x2], ...]} -> set of (x, y)"""
    out = set()
    for y, rs in (runs or {}).items():
        for x1, x2 in rs:
            out.update((x, int(y)) for x in range(int(x1), int(x2) + 1))
    return out


def footprint(e):
    """tiles an entity covers: its box from the game when the snapshot has one, else position, w, h from
    Service.decorate"""
    if e.get("box"):
        x1, y1, x2, y2 = e["box"]
        return [(x, y) for x in range(math.floor(x1 + 0.01), math.ceil(x2 - 0.01))
                for y in range(math.floor(y1 + 0.01), math.ceil(y2 - 0.01))]
    w, h = e.get("w", 1), e.get("h", 1)
    if w != h and e.get("direction", 0) in (4, 12):
        w, h = h, w
    x0 = math.floor(e["position"]["x"] - w / 2 + 0.01)
    y0 = math.floor(e["position"]["y"] - h / 2 + 0.01)
    return [(x0 + i, y0 + j) for i in range(w) for j in range(h)]


class Ground:
    """what's where in the snapshot"""

    def __init__(self, snap):
        self.snap = snap
        x1, y1, x2, y2 = snap["area"]
        self.bounds = (math.floor(x1), math.floor(y1), math.ceil(x2) - 1, math.ceil(y2) - 1)
        self.taken = {}
        for e in snap["entities"]:
            for t in footprint(e):
                self.taken[t] = e
        self.obstacles = set()
        for ob in snap.get("obstacles") or []:
            ox1, oy1, ox2, oy2 = ob[:4]
            if len(ob) > 4 and ob[4] in ("tree", "simple-entity"):
                continue  # a pasted blueprint marks trees and rocks for removal
            for x in range(math.floor(ox1), math.ceil(ox2)):
                for y in range(math.floor(oy1), math.ceil(oy2)):
                    self.obstacles.add((x, y))
        self.water = runs_tiles(snap.get("water"))
        self.tapped = set()  # belt tiles already given a splitter
        self.ore = set()
        for runs in (snap.get("resources") or {}).values():
            self.ore |= runs_tiles(runs)
        # underground pairs already there: a new underground must not sit between them on the same axis
        self.spans = set()
        ugs = [e for e in snap["entities"] if e["type"] == "underground-belt"]
        for a in ugs:
            if a.get("ug_type") != "input":
                continue
            d = a.get("direction", 0)
            dx, dy = DIR[d]
            ax, ay = math.floor(a["position"]["x"]), math.floor(a["position"]["y"])
            for k in range(1, 12):
                t = (ax + dx * k, ay + dy * k)
                b = self.taken.get(t)
                if b and b["type"] == "underground-belt" and b.get("ug_type") == "output" and b.get("direction") == d:
                    axis = d % 8
                    for j in range(k + 1):
                        c = (ax + dx * j, ay + dy * j)
                        self.spans.add((axis, c[0] if axis == 0 else c[1], c[1] if axis == 0 else c[0]))
                    break

    def blocked(self, avoid_ore=True):
        b = set(self.taken) | self.obstacles | self.water
        return b | self.ore if avoid_ore else b

    def inside(self, t, margin=0):
        x1, y1, x2, y2 = self.bounds
        return x1 + margin <= t[0] <= x2 - margin and y1 + margin <= t[1] <= y2 - margin


def plan_tiles(entities):
    out = set()
    for e in entities:
        out.update(footprint(e))
    return out


def find_spot(ground, entities, seed, avoid_ore=True, margin=2):
    """integer offset moving the plan (its tiles' top-left at the origin) so it sits on free ground, nearest seed"""
    tiles = plan_tiles(entities)
    mx, my = min(t[0] for t in tiles), min(t[1] for t in tiles)
    tiles = [(x - mx, y - my) for x, y in tiles]
    w, h = max(t[0] for t in tiles) + 1, max(t[1] for t in tiles) + 1
    blocked = ground.blocked(avoid_ore)
    pad = set()
    for x, y in blocked:  # keep a gap around the existing base for the belts and walking
        for i in range(-margin, margin + 1):
            for j in range(-margin, margin + 1):
                pad.add((x + i, y + j))
    sx, sy = round(seed[0] - w / 2), round(seed[1] - h / 2)
    x1, y1, x2, y2 = ground.bounds
    best = None
    for r in range(0, max(x2 - x1, y2 - y1) + 1):
        for ox in range(sx - r, sx + r + 1):
            for oy in (sy - r, sy + r) if r else (sy,):
                if best and abs(ox - sx) + abs(oy - sy) >= best[0]:
                    continue
                if not (ground.inside((ox, oy)) and ground.inside((ox + w - 1, oy + h - 1))):
                    continue
                if any((ox + x, oy + y) in pad for x, y in tiles):
                    continue
                best = (abs(ox - sx) + abs(oy - sy), ox - mx, oy - my)
        for oy in range(sy - r + 1, sy + r):
            for ox in (sx - r, sx + r) if r else ():
                if best and abs(ox - sx) + abs(oy - sy) >= best[0]:
                    continue
                if not (ground.inside((ox, oy)) and ground.inside((ox + w - 1, oy + h - 1))):
                    continue
                if any((ox + x, oy + y) in pad for x, y in tiles):
                    continue
                best = (abs(ox - sx) + abs(oy - sy), ox - mx, oy - my)
        if best and best[0] <= r:
            break
    if not best:
        raise ValueError("no free spot in the snapshot big enough for this build; snapshot a bigger area")
    return best[1], best[2]


def shifted(entities, dx, dy):
    return [dict(e, position={"x": e["position"]["x"] + dx, "y": e["position"]["y"] + dy}) for e in entities]


VEC_DIR = {v: d for d, v in DIR.items()}


def tap_candidates(ground, want):
    """(tile, direction, belt entity, score) for straight belts carrying only items in `want` (a set); lower score
    is better: carries everything wanted, then on both lanes"""
    out = []
    for e in ground.snap["entities"]:
        if e["type"] != "transport-belt" or e.get("ghost"):
            continue
        lanes = [i for i in (e.get("lanes") or []) if i]
        have = set(lanes)
        if not have or not have <= want:  # anything else on it would clog the new build
            continue
        t = (math.floor(e["position"]["x"]), math.floor(e["position"]["y"]))
        out.append((t, e.get("direction", 0), e, (have != want, -len(lanes))))
    return out


def tap(ground, blocked, spans, want, goal_tile, goal_dir, names):
    """a splitter on an existing belt carrying `want` (set of items), and a routed belt from its free side to the
    goal ("enter goal_tile moving goal_dir"). -> (new entities, tapped belt) or None"""
    belt_name, ug_name, splitter_name, max_ug = names
    cands = tap_candidates(ground, want)
    cands.sort(key=lambda c: (c[3], abs(c[0][0] - goal_tile[0]) + abs(c[0][1] - goal_tile[1])))
    for t, d, belt, _ in [c for c in cands if c[0] not in ground.tapped][:40]:
        dx, dy = DIR[d]
        ahead = (t[0] + dx, t[1] + dy)
        if ahead not in ground.taken:  # the belt line must go on past the splitter
            continue
        for side in (-1, 1):  # the splitter's second half on the left or right of the belt
            s_tile = (t[0] - dy * side, t[1] + dx * side)
            start = (s_tile[0] + dx, s_tile[1] + dy)
            if s_tile in blocked or start in blocked:
                continue
            try:
                path = router.route(blocked, [(start, [d])], goal_tile, goal_dir, ground.bounds, max_ug,
                                    underground_spans=set(spans))
            except router.RouteError:
                continue
            cx = (t[0] + s_tile[0]) / 2 + 0.5
            cy = (t[1] + s_tile[1]) / 2 + 0.5
            ents = [{"name": splitter_name, "position": {"x": cx, "y": cy}, "direction": d}]
            for tile, kind, rd in path:
                e = {"name": belt_name if kind == "belt" else ug_name,
                     "position": {"x": tile[0] + 0.5, "y": tile[1] + 0.5}, "direction": rd}
                if kind != "belt":
                    e["ug_type"] = "input" if kind == "ug-in" else "output"
                ents.append(e)
            router.reserve(path, blocked, spans)
            blocked.add(s_tile)
            ground.tapped.update({t, s_tile, ahead, (t[0] - dx, t[1] - dy)})  # no splitters touching
            return ents, belt
    return None


def feed(ground, blocked, spans, lanes, goal, goal_dir, names):
    """bring a plan input's lanes from the base: one tap carrying them, or (two items on separate belts) one tap
    per item side-loading onto its own lane just before the input. -> (entities, taps, problem)"""
    want = {i for i in lanes if i}
    got = tap(ground, blocked, spans, want, goal, goal_dir, names)
    if got:
        return got[0], [(" + ".join(sorted(want)), got[1])], None
    if len(want) < 2:
        return [], [], f"{' + '.join(want)}: no belt carrying only that could be reached"
    gx, gy = DIR[goal_dir]
    p = (goal[0] - gx, goal[1] - gy)  # the belt feeding the input; the two items come in from its sides
    left = (gy, -gx)
    if p in blocked:
        return [], [], f"{' + '.join(sorted(want))}: no room to merge them in front of the input"
    blocked.add(p)
    ents = [{"name": names[0], "position": {"x": p[0] + 0.5, "y": p[1] + 0.5}, "direction": goal_dir}]
    taps = []
    for item, side in ((lanes[0], left), (lanes[1], (-left[0], -left[1]))):
        into = VEC_DIR[(-side[0], -side[1])]  # moving from that side onto the belt: fills that side's lane
        got = tap(ground, blocked, spans, {item}, p, into, names)
        if not got:
            return ents, taps, f"{item}: no belt carrying only that could be reached"
        ents += got[0]
        taps.append((item, got[1]))
    return ents, taps, None


def link_power(ground, blocked, new_ents, pole, reach):
    """poles from the nearest existing pole to the nearest new one, when they are out of wire reach"""
    poles_old = [e for e in ground.snap["entities"] if e["type"] == "electric-pole" and not e.get("ghost")]
    poles_new = [e for e in new_ents if e["name"] == pole or e.get("type") == "electric-pole"]
    if not poles_old or not poles_new:
        return []
    pos = lambda e: (e["position"]["x"], e["position"]["y"])  # noqa: E731
    a, b = min(((o, n) for o in poles_old for n in poles_new),
               key=lambda p: math.dist(pos(p[0]), pos(p[1])))
    dist = math.dist(pos(a), pos(b))
    if dist <= reach:
        return []
    steps = math.ceil(dist / (reach - 1))
    out = []
    for k in range(1, steps):
        fx = pos(a)[0] + (pos(b)[0] - pos(a)[0]) * k / steps
        fy = pos(a)[1] + (pos(b)[1] - pos(a)[1]) * k / steps
        spot = None
        for r in range(0, 4):  # nearest free tile to the ideal point
            for i in range(-r, r + 1):
                for j in range(-r, r + 1):
                    t = (math.floor(fx) + i, math.floor(fy) + j)
                    if t not in blocked and (not spot or math.dist(t, (fx, fy)) < math.dist(spot, (fx, fy))):
                        spot = t
            if spot:
                break
        if spot:
            blocked.add(spot)
            out.append({"name": pole, "position": {"x": spot[0] + 0.5, "y": spot[1] + 0.5}})
    return out


def absolute_blueprint(entities, label, description=None):
    """blueprint string that snaps to the world grid where these (world-coordinate) entities are"""
    tiles = plan_tiles(entities)
    x0, y0 = min(t[0] for t in tiles), min(t[1] for t in tiles)
    w, h = max(t[0] for t in tiles) - x0 + 1, max(t[1] for t in tiles) - y0 + 1
    local = shifted(entities, -x0, -y0)
    s = planner.blueprint_string([{k: v for k, v in e.items() if k not in ("type", "w", "h", "fluid", "new")}
                                  for e in local], label, description=description)
    from bpgen import base
    bp = base._decode(s)
    bp["blueprint"]["snap-to-grid"] = {"x": w, "y": h}
    bp["blueprint"]["absolute-snapping"] = True
    bp["blueprint"]["position-relative-to-grid"] = {"x": x0 % w, "y": y0 % h}
    return base._encode(bp), (x0, y0, w, h)


def deliver(ground, blocked, spans, item, start, start_dir, names):
    """route an output belt from `start` (moving start_dir) onto an existing belt that carries `item`, side-loading
    onto the lane it is on. -> (entities, the belt it joins) or None"""
    belt_name, ug_name, _, max_ug = names
    cands = []
    for e in ground.snap["entities"]:
        if e["type"] != "transport-belt" or e.get("ghost"):
            continue
        lanes = (e.get("lanes") or []) + [None, None]
        t = (math.floor(e["position"]["x"]), math.floor(e["position"]["y"]))
        d = e.get("direction", 0)
        dx, dy = DIR[d]
        behind = ground.taken.get((t[0] - dx, t[1] - dy))
        if not behind or behind.get("type") != "transport-belt" or behind.get("direction", 0) != d:
            continue  # a straight piece of a line: side-loading onto a bend or a line's first tile misbehaves
        left = (dy, -dx)
        for idx in (0, 1):
            if lanes[idx] != item:
                continue
            side = left if idx == 0 else (-left[0], -left[1])  # a belt coming in from that side fills that lane
            s_tile = (t[0] + side[0], t[1] + side[1])
            if s_tile in blocked:
                continue
            into = VEC_DIR[(-side[0], -side[1])]
            cands.append((abs(t[0] - start[0]) + abs(t[1] - start[1]), t, into, e))
    cands.sort(key=lambda c: c[0])
    for _, t, into, e in cands[:30]:
        try:
            path = router.route(blocked, [(start, [start_dir])], t, into, ground.bounds, max_ug,
                                underground_spans=set(spans))
        except router.RouteError:
            continue
        ents = []
        for tile, kind, rd in path:
            x = {"name": belt_name if kind == "belt" else ug_name,
                 "position": {"x": tile[0] + 0.5, "y": tile[1] + 0.5}, "direction": rd}
            if kind != "belt":
                x["ug_type"] = "input" if kind == "ug-in" else "output"
            ents.append(x)
        router.reserve(path, blocked, spans)
        return ents, e
    return None


FLUID_TYPES = {"pipe", "pipe-to-ground", "storage-tank", "pump", "offshore-pump", "assembling-machine", "furnace",
               "boiler", "generator", "fluid-turret", "mining-drill", "fusion-reactor", "fusion-generator",
               "thruster", "valve", "infinity-pipe"}


def pipe_ground(ground, plan_ents):
    """for routing a pipe through the base: (foreign {tile: fluid or "?"} - what a new pipe may not touch unless
    it carries the same fluid - and the pipe-to-ground lines already claimed)"""
    foreign, pspans = {}, set()
    for e in ground.snap["entities"]:
        if e["type"] in FLUID_TYPES:
            f = e.get("fluid") if e["type"] in ("pipe", "pipe-to-ground") else None
            for t in footprint(e):
                foreign[t] = f or "?"
    for e in plan_ents:
        f = e.get("fluid") if e["name"] in (planner.PIPE, planner.UNDERGROUND) else None
        for t in footprint(e):
            if f or e.get("type") in FLUID_TYPES or e["name"] in (planner.PIPE, planner.UNDERGROUND):
                foreign[t] = f or "?"
    ptg = {(math.floor(e["position"]["x"]), math.floor(e["position"]["y"])): e.get("direction", 0)
           for e in ground.snap["entities"] if e["type"] == "pipe-to-ground"}
    for ta, da in ptg.items():  # a pair: the one past it on its underground side, facing back
        back = (da + 8) % 16
        v = DIR[back]
        for k in range(1, planner.PIPE_UG_MAX + 1):
            tb = (ta[0] + v[0] * k, ta[1] + v[1] * k)
            if ptg.get(tb) == back:
                axis = back % 8
                for i in range(k + 1):
                    c = (ta[0] + v[0] * i, ta[1] + v[1] * i)
                    pspans.add((axis, c[1] if axis else c[0], c[0] if axis else c[1]))
                break
    return foreign, pspans


def pipe_in(ground, blocked, foreign, pspans, fluid, goal, into=4):
    """a pipe from a pipe of the base carrying `fluid` to `goal` (west of the plan's input pipe).
    -> (entities, the pipe it starts from) or None"""
    from bpgen import compose_base
    network = {t for t, f in foreign.items() if f == fluid and isinstance(ground.taken.get(t), dict)
               and ground.taken[t]["type"] == "pipe"}
    if not network:
        return None
    if goal in blocked:
        return None
    v = DIR[into]
    foreign[goal] = foreign[(goal[0] + v[0], goal[1] + v[1])] = fluid  # the plan's own pipe it joins
    try:
        path = compose_base._route_pipe(network, goal, fluid, blocked, foreign, ground.bounds, pspans, into=into)
    except planner.PlanError:
        return None
    if not path:
        return None
    ents = []
    for t, kind, d in path:
        e = {"name": planner.PIPE if kind == "pipe" else planner.UNDERGROUND,
             "position": {"x": t[0] + 0.5, "y": t[1] + 0.5}}
        if kind != "pipe":
            e["direction"] = d
        ents.append(e)
        foreign[t] = fluid
        blocked.add(t)
    compose_base._claim_spans(path, pspans)
    first = path[0][0]
    start = next((ground.taken[n] for n in network if abs(n[0] - first[0]) + abs(n[1] - first[1]) == 1), None)
    return ents, start


def place(data, ground, plan_entities, sources, seed=None, avoid_ore=True, belt="transport-belt", connect=True,
          sinks=None):
    """-> dict(offset, entities (new, world coords), taps, deliveries, notes)"""
    notes = []
    if seed is None:  # near the belts carrying what the plan needs, else the middle of the snapshot
        pts = [c[0] for s in sources if s.get("kind") == "belt" for i in (s.get("lanes") or []) if i
               for c in tap_candidates(ground, {i})]
        if pts:
            seed = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        else:
            x1, y1, x2, y2 = ground.bounds
            seed = ((x1 + x2) / 2, (y1 + y2) / 2)
    dx, dy = find_spot(ground, plan_entities, seed, avoid_ore)
    new = shifted(plan_entities, dx, dy)
    blocked = ground.blocked(avoid_ore=False) | plan_tiles(new)
    spans = set(ground.spans)
    taps = []
    ug, ug_max = chain.related_belts(data, belt)
    names = (belt, ug, chain.related_splitter(data, belt), ug_max)
    foreign = pspans = None
    if connect:
        for s in sources:
            if s.get("kind") == "fluid":
                if foreign is None:
                    foreign, pspans = pipe_ground(ground, new)
                goal = (math.floor(s["position"]["x"] + dx), math.floor(s["position"]["y"] + dy))
                got = pipe_in(ground, blocked, foreign, pspans, s["fluid"], goal)
                if got:
                    new += got[0]
                    at = got[1]["position"] if got[1] else got[0][0]["position"]
                    taps.append({"items": s["fluid"], "from": at, "belts": len(got[0])})
                else:
                    notes.append(f"{s.get('fluid')}: no pipe of yours carrying it could be reached; pipe it to the "
                                 f"input marked on the build")
                continue
            if s.get("kind") != "belt":
                continue
            lanes = list(s.get("lanes") or [None, None]) + [None, None]
            goal = (math.floor(s["position"]["x"] + dx), math.floor(s["position"]["y"] + dy))
            goal_dir = 4
            for e in new:
                if footprint(e) == [goal] and e.get("direction") is not None:
                    goal_dir = e.get("direction", 4)
            # the tile before the input belt is the route's end; it must not be part of the plan
            blocked.discard((goal[0] - DIR[goal_dir][0], goal[1] - DIR[goal_dir][1]))
            ents, got, problem = feed(ground, blocked, spans, lanes[:2], goal, goal_dir, names)
            new += ents
            for items, belt_e in got:
                taps.append({"items": items, "from": belt_e["position"], "belts": len(ents)})
            if problem:
                notes.append(problem + "; bring it to the input by hand")
    # outputs: onto a belt of the base that already carries the item (a science line, a bus lane)
    deliveries = []
    for k in (sinks or []) if connect else []:
        if k.get("kind") == "fluid" and k.get("item"):  # its pipe's end: on into a pipe of the base with that fluid
            if foreign is None:
                foreign, pspans = pipe_ground(ground, new)
            end = (math.floor(k["position"]["x"] + dx), math.floor(k["position"]["y"] + dy))
            goal = (end[0] + 1, end[1])
            blocked.discard(goal) if goal not in ground.taken else None
            got = pipe_in(ground, blocked, foreign, pspans, k["item"], goal, into=12) if goal not in ground.taken else None
            if got:
                new += got[0]
                deliveries.append({"item": k["item"], "to": got[1]["position"] if got[1] else got[0][0]["position"],
                                   "belts": len(got[0])})
            else:
                notes.append(f"{k['item']}: no pipe of yours carries it within reach; pipe the output where it's needed")
            continue
        if k.get("kind", "belt") != "belt" or not k.get("item"):
            continue
        start = (math.floor(k["position"]["x"] + dx), math.floor(k["position"]["y"] + dy))
        here = {t: e for e in new for t in footprint(e)}
        if start in here:  # a planner's sink is its output belt's last tile: the route starts just past it
            start = (start[0] + DIR[here[start].get("direction", 4)][0], start[1] + DIR[here[start].get("direction", 4)][1])
        d = next((dd for dd, v in DIR.items() if (here.get((start[0] - v[0], start[1] - v[1])) or {})
                  .get("direction", 0) == dd and (start[0] - v[0], start[1] - v[1]) in here), 4)
        got = None if start in blocked else deliver(ground, blocked, spans, k["item"], start, d, names)
        if got:
            new += got[0]
            deliveries.append({"item": k["item"], "to": got[1]["position"], "belts": len(got[0])})
        else:
            notes.append(f"{k['item']}: no belt of yours carries it within reach; take the output where it's needed")
    poles = link_power(ground, blocked, new, planner.POLE, planner.POLE_REACH)
    if poles:
        notes.append(f"{len(poles)} poles link it to your power")
    return {"offset": [dx, dy], "entities": new + poles, "taps": taps, "deliveries": deliveries, "notes": notes}

