"""Build next to the player's base, from a snapshot taken in game (the zzz-bpgen mod).

place() puts a plan into free ground (no buildings, obstacles, water or ore) near a seed point, taps the existing
belts that carry what each plan input needs (a splitter on the belt, then a routed belt), links power to the
nearest existing pole, and returns the new entities in world coordinates with a blueprint that snaps to the world
grid (absolute snapping), so it pastes exactly where it was placed.
"""
import math

from bpgen import chain, geom, planner, power, router
from bpgen import ents as entlib

DIR = geom.DIRS
BELT_TYPES = ("transport-belt", "underground-belt", "splitter")


runs_tiles = geom.runs_tiles
footprint = geom.footprint


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


def belt_ends(entities, taken):
    """free tiles just past a belt line's end (a belt, an underground's exit or a splitter pointing at nothing): a
    belt routed there would take whatever that line carries, side-loaded"""
    out = set()
    for e in entities:
        if e.get("type") not in BELT_TYPES or e.get("ug_type") == "input" or e.get("ghost"):
            continue
        dx, dy = DIR[e.get("direction", 0)]
        out.update(a for x, y in footprint(e) if (a := (x + dx, y + dy)) not in taken)
    return out


def into_belt(ground, t, new=()):
    """a belt on tile t (the base's, or one of `new`, planned): a lane ending just before it would side-load onto
    it (a new bus lane's circuits onto a coal belt crossing its way, jamming both)"""
    e = ground.taken.get(t)
    if e and e.get("type") in BELT_TYPES:
        return True
    return any((x.get("type") in BELT_TYPES or x["name"].endswith(("belt", "splitter"))) and t in footprint(x)
               for x in new)


plan_tiles = geom.plan_tiles


def find_spot(ground, entities, seed, avoid_ore=True, margin=2, keep_clear=()):
    """integer offset moving the plan (its tiles' top-left at the origin) so it sits on free ground, nearest seed;
    keep_clear: tiles it must not cover either (beside and past a main bus, kept for its taps and lanes)"""
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
    pad |= set(keep_clear)
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
        raise Blocked("no free spot in the snapshot big enough for this build; snapshot a bigger area")
    return best[1], best[2]


pole_chain = power.pole_chain


def pole_tiles(entities):
    return [(math.floor(e["position"]["x"]), math.floor(e["position"]["y"])) for e in entities
            if e["name"] == planner.POLE]


def nearest(tiles, t):
    return min(tiles, key=lambda p: (p[0] - t[0]) ** 2 + (p[1] - t[1]) ** 2) if tiles else None


def shifted(entities, dx, dy):
    return [dict(e, position={"x": e["position"]["x"] + dx, "y": e["position"]["y"] + dy}) for e in entities]


VEC_DIR = {v: d for d, v in DIR.items()}


def tap_candidates(ground, want):
    """(tile, direction, belt entity, score) for straight belts carrying exactly `want` (a set: a belt with only part
    of it would leave the rest out, one with more would clog the new build); lower score is better: on both lanes"""
    out = []
    for e in ground.snap["entities"]:
        if e["type"] != "transport-belt" or e.get("ghost"):
            continue
        lanes = [i for i in (e.get("lanes") or []) if i]
        have = set(lanes)
        if have != want:
            continue
        t = (math.floor(e["position"]["x"]), math.floor(e["position"]["y"]))
        out.append((t, e.get("direction", 0), e, (have != want, -len(lanes))))
    return out


# a tap gives up after this many routes in a row that don't get there: when one can, the first few do (a goal no tap
# reaches - walled in, or too far - fails every one of hundreds of candidates, half a second each)
MAX_FAILS = 16
# and searches at most this far for each (a tap's route that gets there takes a few thousand)
TAP_EXPANSIONS = 25_000


def reachable(ground, blocked, spans, starts, goal_tile, goal_dir, max_ug):
    """can any of `starts` ([(tile, [dirs])]) reach the goal at all: one search from all of them together, on
    `blocked` as it is (each candidate's own route has more blocked, never less: when this fails, they all fail).
    A goal walled in costs this one search, not MAX_FAILS of them"""
    if not starts:
        return False
    try:
        router.route(blocked, starts, goal_tile, goal_dir, ground.bounds, max_ug, underground_spans=set(spans),
                     max_expansions=4 * TAP_EXPANSIONS)
        return True
    except router.RouteError:
        return False


def tap(ground, blocked, spans, want, goal_tile, goal_dir, names, prefer=None, rate=0):
    """a splitter on an existing belt carrying `want` (set of items), and a routed belt from its free side to the
    goal ("enter goal_tile moving goal_dir"); prefer: (main bus, side of it the build is on) to branch off the bus
    first (bus_tap), then tap any belt, the bus's first. -> (new entities, tapped belt) or None"""
    # (a tap that failed with fewer tiles taken fails again: feed's merge variants ask for the same goals)
    key = (goal_tile, goal_dir, frozenset(want))
    failed = ground.__dict__.setdefault("no_tap", {}).setdefault(key, [])
    if any(b <= blocked and t <= ground.tapped and sp <= spans for b, t, sp in failed):
        return None
    got = _tap(ground, blocked, spans, want, goal_tile, goal_dir, names, prefer, rate)
    if not got:
        failed.append((frozenset(blocked), frozenset(ground.tapped), frozenset(spans)))
    return got


def _tap(ground, blocked, spans, want, goal_tile, goal_dir, names, prefer=None, rate=0):
    if prefer:
        got = bus_tap(ground, blocked, spans, prefer, want, goal_tile, goal_dir, names, rate)
        if got:
            return got
        prefer = prefer[0]["tiles"]
    belt_name, ug_name, splitter_name, max_ug = names
    end = (goal_tile[0] - DIR[goal_dir][0], goal_tile[1] - DIR[goal_dir][1])
    cands = tap_candidates(ground, want)
    cands.sort(key=lambda c: (bool(prefer) and c[0] not in prefer, c[3],
                              abs(c[0][0] - goal_tile[0]) + abs(c[0][1] - goal_tile[1])))
    cands = [c for c in cands if c[0] not in ground.tapped][:40]
    starts = []
    for t, d, _, _ in cands:
        dx, dy = DIR[d]
        for side in (-1, 1):
            s_tile = (t[0] - dy * side, t[1] + dx * side)
            starts.append(((s_tile[0] + dx, s_tile[1] + dy), [d]))
    if not reachable(ground, blocked, spans, starts, goal_tile, goal_dir, max_ug):
        return None
    fails = 0
    for t, d, belt, _ in cands:
        if fails >= MAX_FAILS:
            break
        dx, dy = DIR[d]
        ahead = (t[0] + dx, t[1] + dy)
        if ahead not in ground.taken:  # the belt line must go on past the splitter
            continue
        for side in (-1, 1):  # the splitter's second half on the left or right of the belt
            s_tile = (t[0] - dy * side, t[1] + dx * side)
            start = (s_tile[0] + dx, s_tile[1] + dy)
            if s_tile in blocked or start in blocked or s_tile == end:  # (not on the tile its route must end on)
                continue
            try:
                # (the splitter's own tiles too: a route under them would put an underground on its half)
                path = router.route(blocked | {t, s_tile}, [(start, [d])], goal_tile, goal_dir, ground.bounds,
                                    max_ug, underground_spans=set(spans), max_expansions=TAP_EXPANSIONS)
            except router.RouteError:
                fails += 1
                continue
            cx = (t[0] + s_tile[0]) / 2 + 0.5
            cy = (t[1] + s_tile[1]) / 2 + 0.5
            ents = [{"name": splitter_name, "position": {"x": cx, "y": cy}, "direction": d}]
            ents += path_entities(path, belt_name, ug_name)
            router.reserve(path, blocked, spans)
            blocked.add(s_tile)
            ground.tapped.update({t, s_tile, ahead, (t[0] - dx, t[1] - dy)})  # no splitters touching
            return ents, belt
    return None


def bus_tap(ground, blocked, spans, bus_side, want, goal, goal_dir, names, rate=0):
    """take `want` off a bus lane the way a bus does it: a splitter on the lane with its spare half on the build's
    side, then a branch straight out across the lanes between, each of them diving underground under it (a pair one
    tile either side of the branch, replacing the bus's belts: paste with Ctrl+Shift; a lane right beside the tapped
    one dives a row earlier, under the splitter's spare half), then a routed belt from the bus's edge to the goal.
    rate (items/s it takes): a lane with room for it first (its "cap"), then the least loaded, so taps for the same
    item spread over the lanes carrying it. -> (new entities, tapped belt) or None"""
    main, side = bus_side
    load = main["load"]
    belt_name, ug_name, splitter_name, max_ug = names
    d, f, ax = main["dir"], main["flow"], main["axis"]
    sd = VEC_DIR[(side, 0) if ax == 0 else (0, side)]  # the branch's direction
    lanes = main["lanes"]
    outer = lanes[-1]["at"] if side > 0 else lanes[0]["at"]
    goal_a = goal[1] if ax == 0 else goal[0]

    pipes = {ln["at"] for ln in lanes if ln.get("pipe")}

    def plain(a, c):  # a straight belt (or pipe, on a pipe lane) of the bus nothing has been done to yet
        t = bus_xy(main, a, c)
        e = ground.taken.get(t)
        if c in pipes:
            return e and e["type"] == "pipe" and t not in ground.tapped
        return e and e["type"] == "transport-belt" and e.get("direction", 0) == d and t not in ground.tapped

    cands = [(ln["items"] != want, load.get(ln["at"], 0) + ln.get("used", 0) + rate > ln.get("cap", math.inf) + 1e-9,
              load.get(ln["at"], 0) + ln.get("used", 0), abs(a + f - goal_a), a, ln)
             for ln in lanes if ln["items"] == want for a in range(ln["lo"] + 1, ln["hi"] - 1)]
    cands.sort(key=lambda c: c[:5])
    cands = cands[:300]
    if not reachable(ground, blocked, spans, list({(bus_xy(main, a + f, outer + side), (sd, d)) for *_, a, ln in cands}),
                     goal, goal_dir, max_ug):
        return None
    fails = 0
    for *_, a, ln in cands:
        if fails >= MAX_FAILS:
            break
        lb, lug, lspl, _ = ln.get("names") or names  # (on the bus: the lane's own tier; routed on: the plan's)
        r = a + f  # the branch's row
        cross = [c["at"] for c in lanes if (c["at"] - ln["at"]) * side > 0]
        row = list(range(ln["at"] + side, outer + side, side))  # the branch across the bus
        end = bus_xy(main, r, outer + side)  # where it leaves the bus: routed on from there
        spare = bus_xy(main, a, ln["at"] + side)
        tight = ln["at"] + side in cross  # a lane right beside it: that one dives a row early, under the spare half
        dive = {c: a - f if tight and c == ln["at"] + side else a for c in cross}  # where each crossed lane dives
        if not (plain(a, ln["at"]) and plain(r, ln["at"])) or (spare in blocked and not tight) or end in blocked                 or spare == (goal[0] - DIR[goal_dir][0], goal[1] - DIR[goal_dir][1]):  # (not on its route's end)
            continue
        if not all(plain(aa, c) for c in cross for aa in range(dive[c], r + 2 * f, f)):
            continue
        if any(bus_xy(main, r, c) in blocked for c in row if c not in cross):
            continue
        try:
            path = router.route(blocked | {bus_xy(main, a, ln["at"]), spare} | {bus_xy(main, r, c) for c in row},
                                [(end, [sd] if row else [d, sd])], goal, goal_dir, ground.bounds,
                                max_ug, underground_spans=set(spans), max_expansions=TAP_EXPANSIONS)
        except router.RouteError:
            fails += 1
            continue
        st = bus_xy(main, a, ln["at"])
        ents = [{"name": lspl, "position": {"x": (st[0] + spare[0]) / 2 + 0.5, "y": (st[1] + spare[1]) / 2 + 0.5},
                 "direction": d}]
        for c in row:
            t = bus_xy(main, r, c)
            ents.append({"name": lb, "position": {"x": t[0] + 0.5, "y": t[1] + 0.5}, "direction": sd})
            blocked.add(t)
        for c in cross:
            if c in pipes:  # (a pipe lane: a pipe-to-ground pair, each facing the pipe it joins)
                for aa, open_ in ((dive[c], -f), (r + f, f)):
                    t = bus_xy(main, aa, c)
                    ents.append({"name": planner.UNDERGROUND, "position": {"x": t[0] + 0.5, "y": t[1] + 0.5},
                                 "direction": VEC_DIR[(open_, 0) if ax == 4 else (0, open_)]})
                continue
            cn = next((x.get("names") for x in lanes if x["at"] == c), None) or names  # (each lane dives in its tier)
            for aa, kind in ((dive[c], "input"), (r + f, "output")):
                t = bus_xy(main, aa, c)
                ents.append({"name": cn[1], "position": {"x": t[0] + 0.5, "y": t[1] + 0.5}, "direction": d,
                             "ug_type": kind})
            if c not in pipes:
                spans.update((ax, c, aa) for aa in range(dive[c], r + 2 * f, f))
        ents += path_entities(path, belt_name, ug_name)
        router.reserve(path, blocked, spans)
        blocked.add(spare)
        for c in [ln["at"], ln["at"] + side] + cross:  # no other tap or dive touching this one
            ground.tapped.update(bus_xy(main, a + k * f, c) for k in range(-2, 4))
        load[ln["at"]] = load.get(ln["at"], 0) + rate
        return ents, ground.taken[st]
    return None


def feed(ground, blocked, spans, lanes, goal, goal_dir, names, prefer=None, rates=None):
    """bring a plan input's lanes from the base: one tap carrying them, or (two items on separate belts) one tap
    per item side-loading onto its own lane just before the input; prefer: see tap; rates: {item: items/s the input
    takes}. -> (entities, taps, problem)"""
    want = {i for i in lanes if i}
    rates = rates or {}
    got = tap(ground, blocked, spans, want, goal, goal_dir, names, prefer, sum(rates.get(i, 0) for i in want))
    if got:
        return got[0], [(" + ".join(sorted(want)), got[1])], None
    if len(want) < 2:
        return [], [], f"{' + '.join(want)}: no belt carrying only that could be reached"
    gx, gy = DIR[goal_dir]
    left = (gy, -gx)
    # a short straight belt into the input (one to three tiles), both items side-loading onto its first tile from
    # either side (a T: each fills its side's lane; one side alone would make it a curve, the item on both lanes),
    # nearest first, both orders (the first route may cut off the second's way in)
    chain = [(goal[0] - gx * n, goal[1] - gy * n) for n in (1, 2, 3)]
    sides = ((lanes[0], left), (lanes[1], (-left[0], -left[1])))
    tries = [(k, k) for k in range(3)]
    problem = f"{' + '.join(sorted(want))}: no room to merge them in front of the input"
    for i, j in tries:
        used = chain[:max(i, j) + 1]
        if any(t in blocked for t in used):
            continue
        for order in ((0, i), (1, j)), ((1, j), (0, i)):
            trial, tapped = set(blocked) | set(used), set(ground.tapped)
            ents = [{"name": names[0], "position": {"x": t[0] + 0.5, "y": t[1] + 0.5}, "direction": goal_dir}
                    for t in used]
            taps, failed = [], None
            for k, at in order:
                item, side = sides[k]
                into = VEC_DIR[(-side[0], -side[1])]
                got = tap(ground, trial, spans, {item}, chain[at], into, names, prefer, rates.get(item, 0))
                if not got:
                    failed = f"{item}: no belt carrying only that could be reached"
                    break
                ents += got[0]
                taps.append((item, got[1]))
            if not failed:
                blocked |= trial
                return ents, taps, None
            problem = failed
            ground.tapped.clear()
            ground.tapped.update(tapped)
    return [], [], problem


link_power = power.link_to_grid


def absolute_blueprint(entities, label, description=None, floor=None):
    """blueprint string that snaps to the world grid where these (world-coordinate) entities are; floor: tiles
    ({"name", "position"}, world) it lays too (a space platform's foundation)"""
    tiles = plan_tiles(entities) | {(t["position"]["x"], t["position"]["y"]) for t in floor or []}
    x0, y0 = min(t[0] for t in tiles), min(t[1] for t in tiles)
    w, h = max(t[0] for t in tiles) - x0 + 1, max(t[1] for t in tiles) - y0 + 1
    local = shifted(entities, -x0, -y0)
    s = planner.blueprint_string([{k: v for k, v in e.items() if k not in ("type", "w", "h", "fluid", "new")}
                                  for e in local], label, description=description,
                                 tiles=[{"name": t["name"], "position": {"x": t["position"]["x"] - x0,
                                                                         "y": t["position"]["y"] - y0}}
                                        for t in floor or []])
    bp = entlib.decode(s)
    bp["blueprint"]["snap-to-grid"] = {"x": w, "y": h}
    bp["blueprint"]["absolute-snapping"] = True
    bp["blueprint"]["position-relative-to-grid"] = {"x": x0 % w, "y": y0 % h}
    return entlib.encode(bp), (x0, y0, w, h)


def deliver(ground, blocked, spans, item, start, start_dir, names, prefer=None):
    """route an output belt from `start` (moving start_dir) onto an existing belt that carries `item`, side-loading
    onto the lane it is on; belts on `prefer` tiles (a main bus) first. -> (entities, the belt it joins) or None"""
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
            cands.append(((bool(prefer) and t not in prefer, abs(t[0] - start[0]) + abs(t[1] - start[1])), t, into, e))
    cands.sort(key=lambda c: c[0])
    for _, t, into, e in cands[:30]:
        try:
            path = router.route(blocked, [(start, [start_dir])], t, into, ground.bounds, max_ug,
                                underground_spans=set(spans))
        except router.RouteError:
            continue
        router.reserve(path, blocked, spans)
        return path_entities(path, belt_name, ug_name), e
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


BUS_MIN_LEN = 16  # tiles a belt column runs straight to count as a bus lane
BUS_MIN_LANES = 3
BUS_ROOM = 4  # tiles kept free beside the bus for new lanes (one every 2 tiles, a splitter's spare tile between)
BUS_PAST = 32  # how far past its end the bus is kept clear, and may be continued
FLOW_NAME = {0: "north", 4: "east", 8: "south", 12: "west"}


def find_bus(ground):
    """the main bus in the snapshot: the biggest group of BUS_MIN_LANES or more belt columns side by side (at most 3
    tiles apart, pipe runs between them bridging), each running straight for BUS_MIN_LEN tiles or more, all
    flowing the same way, with the pipe runs among and beside them as pipe lanes (see pipe_lanes). Splitters and
    underground pairs along a column don't break it. -> dict(dir, axis (0: flows north/south, a lane is a column at
    x = its "at"; 4: east/west, a row), flow (+1 south/east, -1), lanes [{at, lo, hi, items, belt}] (lo..hi along
    the flow axis), lo, hi, tiles (the lanes' belt tiles), load ({lane's at: items/s this build takes from it})) or
    None"""
    cols = {}  # (direction, across) -> {along: entity, or None between an underground pair}
    for e in ground.snap["entities"]:
        if e["type"] not in BELT_TYPES or e.get("ghost"):
            continue
        d = e.get("direction", 0)
        for x, y in footprint(e):
            a, c = (y, x) if d in (0, 8) else (x, y)
            cols.setdefault((d, c), {})[a] = e
    for axis, c, a in ground.spans:
        for d in ((0, 8) if axis == 0 else (4, 12)):
            if (d, c) in cols:
                cols[(d, c)].setdefault(a, None)
    lanes = {}
    for (d, c), at in cols.items():
        best = start = prev = None
        for a in sorted(at):
            if prev is None or a != prev + 1:
                start = a
            prev = a
            if a - start + 1 >= BUS_MIN_LEN and (not best or a - start > best[1] - best[0]):
                best = (start, a)
        if best:
            run = [e for a, e in sorted(at.items(), key=lambda p: p[0] * (1 if d in (4, 8) else -1))
                   if best[0] <= a <= best[1] and e and e["type"] == "transport-belt"]  # (upstream first)
            items = {i for e in run for i in e.get("lanes") or [] if i}
            names = [e["name"] for e in run]
            lanes.setdefault(d, []).append(dict({"at": c, "lo": best[0], "hi": best[1], "items": items,
                                                 "belt": max(set(names), key=names.count) if names else None},
                                                **lane_usage(run)))
    found = None
    runs = {ax: pipe_runs(ground, ax) for ax in {d % 8 for d in lanes}}
    for d, ls in lanes.items():
        ls.sort(key=lambda ln: ln["at"])
        group = []
        for ln in ls + [None]:
            if ln and group and bridged(group[-1]["at"], ln["at"], runs[d % 8]) \
                    and min(ln["hi"], group[-1]["hi"]) - max(ln["lo"], group[-1]["lo"]) >= BUS_MIN_LEN // 2:
                group.append(ln)
                continue
            if len(group) >= BUS_MIN_LANES:
                score = sum(g["hi"] - g["lo"] + 1 for g in group)
                if not found or score > found[0]:
                    found = (score, d, group)
            group = [ln] if ln else []
    if not found:
        return None
    _, d, group = found
    axis = d % 8
    tiles = {(ln["at"], a) if axis == 0 else (a, ln["at"]) for ln in group for a in range(ln["lo"], ln["hi"] + 1)}
    group = sorted(group + pipe_lanes(runs[axis], group), key=lambda ln: ln["at"])
    return {"dir": d, "axis": axis, "flow": 1 if d in (4, 8) else -1, "lanes": group,
            "lo": min(g["lo"] for g in group), "hi": max(g["hi"] for g in group), "tiles": tiles, "load": {}}


def lane_usage(run):
    """what a bus lane's belts (upstream first) were measured carrying, when the snapshot has flows (the mod looks
    at every belt twice): used (items/s past its upstream end: what everything downstream takes), dry (its
    last belts empty: it runs out before its end), backed (its upstream belts full and standing: more comes than is
    taken). -> {} without measurements"""
    m = [e for e in run if "flow" in e]
    if len(m) < 4:
        return {}
    head, tail = m[:6], m[-4:]
    used = sum(e["flow"] for e in head) / len(head)
    full = sum(e.get("items", 0) for e in head) / len(head) >= 6
    standing = sum(1 for e in head if e["flow"] < 0.5) >= len(head) - 1
    return {"used": used, "dry": sum(e.get("items", 0) for e in tail) / len(tail) < 0.5, "backed": full and standing}


def pipe_runs(ground, axis):
    """straight pipe runs along that axis, BUS_MIN_LEN / 2 tiles or more; pipe-to-ground pairs along a run don't
    break it. -> {across: (lo, hi, fluid)}"""
    cols = {}  # across -> {along: entity, or None between a pipe-to-ground pair}
    for e in ground.snap["entities"]:
        if e["type"] not in ("pipe", "pipe-to-ground") or e.get("ghost"):
            continue
        if e["type"] == "pipe-to-ground" and e.get("direction", 0) % 8 != axis:
            continue  # (one across the bus: not part of a lane)
        x, y = math.floor(e["position"]["x"]), math.floor(e["position"]["y"])
        a, c = (y, x) if axis == 0 else (x, y)
        cols.setdefault(c, {})[a] = e
    out = {}
    for c, at in cols.items():
        ptg = sorted(a for a, e in at.items() if e["type"] == "pipe-to-ground")
        for a1, a2 in zip(ptg[::2], ptg[1::2]):  # (underground between a pair: still the run)
            if a2 - a1 <= 11:
                for a in range(a1 + 1, a2):
                    at.setdefault(a, None)
        best = start = prev = None
        for a in sorted(at):
            if prev is None or a != prev + 1:
                start = a
            prev = a
            if not best or a - start > best[1] - best[0]:
                best = (start, a)
        if best and best[1] - best[0] + 1 >= BUS_MIN_LEN // 2:
            out[c] = (best[0], best[1], next((e.get("fluid") for e in at.values() if e and e.get("fluid")), "?"))
    return out


BUS_GAP = 8  # belt lanes this far apart or closer are one bus (lanes in groups with gaps between: no build fits in)


def bridged(a, b, pipes):
    """belt lanes at a and b (a < b) close enough for one bus: at most BUS_GAP tiles apart, or pipe runs between
    them in steps of at most that (a fluid bus among the belts)"""
    pts = [a] + sorted(c for c in pipes if a < c < b) + [b]
    return all(q - p <= BUS_GAP for p, q in zip(pts, pts[1:]))


def pipe_lanes(runs, belts):
    """the pipe runs alongside a bus's belt lanes (among them, or beside them within 3 tiles of the bus or of
    another pipe lane), overlapping the belts' stretch by BUS_MIN_LEN / 2 or more.
    -> lanes {at, lo, hi, items: set(), belt: None, pipe: fluid}"""
    lo_b, hi_b = min(ln["lo"] for ln in belts), max(ln["hi"] for ln in belts)
    used = {ln["at"] for ln in belts}
    ok = {c: r for c, r in runs.items() if c not in used and min(r[1], hi_b) - max(r[0], lo_b) >= BUS_MIN_LEN // 2}
    out = [c for c in ok if min(used) - 3 <= c <= max(used) + 3]
    grown = True
    while grown:
        grown = False
        for c in ok:
            if c not in out and any(abs(c - o) <= 3 for o in out):
                out.append(c)
                grown = True
    return [{"at": c, "lo": ok[c][0], "hi": ok[c][1], "items": set(), "belt": None, "pipe": ok[c][2]} for c in out]


def bus_xy(bus, along, across):
    return (across, along) if bus["axis"] == 0 else (along, across)


def facing(plan_entities, sources):
    """the edge a plan takes its belt inputs on, as a unit vector from its middle ((-1, 0): west), or None"""
    pts = [(s["position"]["x"], s["position"]["y"]) for s in sources if s.get("position") and s.get("kind") == "belt"]
    if not pts:
        return None
    tiles = plan_tiles(plan_entities)
    mx = (min(t[0] for t in tiles) + max(t[0] for t in tiles) + 1) / 2
    my = (min(t[1] for t in tiles) + max(t[1] for t in tiles) + 1) / 2
    dx, dy = sum(p[0] for p in pts) / len(pts) - mx, sum(p[1] for p in pts) / len(pts) - my
    if max(abs(dx), abs(dy)) < 1:
        return None
    return (1 if dx > 0 else -1, 0) if abs(dx) >= abs(dy) else (0, 1 if dy > 0 else -1)


turned = geom.turn_xy
mirrored_dir = geom.mirror_dir


def solve_pose(pairs):
    """how a plan was put down by hand: pairs [((x, y) where pose() had it, (x, y) where the game built it)] ->
    (mirror (flipped left to right first), k (then quarter turns clockwise), (ox, oy) (then moved)) taking every
    first point onto its second, or None"""
    if not pairs:
        return None
    for mirror in (False, True):
        for k in range(4):
            (ax, ay), (bx, by) = pairs[0]
            tx, ty = turned(-ax if mirror else ax, ay, k)
            ox, oy = bx - tx, by - ty
            if all(abs(turned(-x if mirror else x, y, k)[0] + ox - u) < 0.01
                   and abs(turned(-x if mirror else x, y, k)[1] + oy - v) < 0.01 for (x, y), (u, v) in pairs):
                return mirror, k, (ox, oy)
    return None


def repose(posed, how, built=None):
    """pose()'s answer moved the way solve_pose() found the player put it down; built: {entity index: direction the
    game gave it} (its own say, flips included), else the direction worked out. -> a posed dict for connect_posed"""
    mirror, k, (ox, oy) = how

    def at(p):
        x, y = turned(-p["x"] if mirror else p["x"], p["y"], k)
        return {"x": x + ox, "y": y + oy}

    def way(d):
        return ((mirrored_dir(d) if mirror else d) + 4 * k) % 16
    ents = []
    for i, e in enumerate(posed["entities"]):
        e = dict(e, position=at(e["position"]))
        if (built or {}).get(i) is not None:
            e["direction"] = built[i]
        elif e.get("direction") is not None:
            e["direction"] = way(e["direction"])
        ents.append(e)
    xs = [x["position"]["x"] for x in ents]
    ys = [x["position"]["y"] for x in ents]
    dx, dy = round(min(xs) - min(e["position"]["x"] for e in posed["entities"])), round(
        min(ys) - min(e["position"]["y"] for e in posed["entities"]))
    return dict(posed, entities=ents, turn=(posed["turn"] + k) % 4,
                offset=[posed["offset"][0] + dx, posed["offset"][1] + dy],
                sources=[dict(x, position=at(x["position"])) for x in posed["sources"]],
                sinks=[dict(x, position=at(x["position"])) for x in posed["sinks"]])


def turn_plan(entities, sources, sinks, k):
    """the plan turned k quarter turns clockwise: positions, and every direction +4 a turn (sizes follow the
    direction, see footprint). -> (entities, sources, sinks)"""
    if not k % 4:
        return entities, sources, sinks

    def moved(x):
        px, py = turned(x["position"]["x"], x["position"]["y"], k)
        return dict(x, position={"x": px, "y": py})
    ents = [dict(moved(e), direction=(e.get("direction", 0) + 4 * k) % 16) for e in entities]
    return ents, [moved(s) for s in sources], [moved(s) for s in sinks or []]


def bus_side(bus, plan_entities, sources, seed):
    """which side of the bus to build on: the seed's (where the player stands), else the side the plan's inputs
    already face, else east/south. -> +1 (east/south) or -1"""
    ax = bus["axis"]
    if seed:
        mid = (bus["lanes"][0]["at"] + bus["lanes"][-1]["at"]) / 2
        return 1 if (seed[0] if ax == 0 else seed[1]) >= mid else -1
    f = facing(plan_entities, sources)
    across = f and (f[0] if ax == 0 else f[1])
    return -across if across else 1  # (inputs west: the build east of the bus)


def bus_spot(bus, plan_entities, side, seed):
    """where to look for room beside the bus, on that side: level with the seed, else near the bus's downstream
    end. -> (seed point, the corridor to keep clear)"""
    tiles = plan_tiles(plan_entities)
    xs, ys = [t[0] for t in tiles], [t[1] for t in tiles]
    w, h = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    ax = bus["axis"]
    along_n, across_n = (h, w) if ax == 0 else (w, h)
    lo_c, hi_c = bus["lanes"][0]["at"], bus["lanes"][-1]["at"]
    if seed:
        along = min(max(seed[1] if ax == 0 else seed[0], bus["lo"]), bus["hi"])
    else:  # past the branches already there
        along = bus["hi"] - along_n / 2 if bus["flow"] > 0 else bus["lo"] + along_n / 2
    edge = hi_c if side > 0 else lo_c
    across = edge + side * (BUS_ROOM + 1 + across_n / 2)
    past = range(bus["lo"], bus["hi"] + BUS_PAST + 1) if bus["flow"] > 0 else range(bus["lo"] - BUS_PAST, bus["hi"] + 1)
    corridor = {bus_xy(bus, a, c) for a in past for c in range(lo_c - BUS_ROOM, hi_c + BUS_ROOM + 1)}
    return bus_xy(bus, along, across), corridor


def bus_items(data, main):
    """the bus by item: {item (lanes carrying more than one: joined by " + "): {lanes, cap, used (None unmeasured),
    dry, belt}}, items/s"""
    out = {}
    for ln in main["lanes"]:
        if not ln["items"]:
            continue
        k = " + ".join(sorted(ln["items"]))
        o = out.setdefault(k, {"lanes": 0, "cap": 0, "used": None, "dry": 0, "belt": ln["belt"]})
        o["lanes"] += 1
        o["cap"] += (data.raw["transport-belt"].get(ln["belt"]) or {}).get("speed", 0.03125) * 480
        if "used" in ln:
            o["used"] = (o["used"] or 0) + ln["used"]
        o["dry"] += 1 if ln.get("dry") else 0
    return out


def new_bus_lane(data, ground, main, item, max_ug=4):
    """a new lane for `item` along the bus, outside its outermost lane on one side, from the bus's upstream end to
    its downstream end: belts in the tier of the lanes already carrying it (else the bus's commonest), diving under
    whatever crosses its way (a branch leaving the bus) when the other side is free within reach, stopping where it
    can't. The side that gets furthest. -> (entities, head tile (where it is fed), length) or None"""
    f, d = main["flow"], main["dir"]
    tiers = [ln["belt"] for ln in main["lanes"] if ln["belt"] and item in ln["items"]] or \
            [ln["belt"] for ln in main["lanes"] if ln["belt"]]
    belt = max(set(tiers), key=tiers.count) if tiers else "transport-belt"
    ug, ug_max = chain.related_belts(data, belt)
    head, end = (main["lo"], main["hi"]) if f > 0 else (main["hi"], main["lo"])
    blocked = ground.blocked(avoid_ore=False)
    best = None
    for side in (1, -1):
        at = (main["lanes"][-1]["at"] if side > 0 else main["lanes"][0]["at"]) + 2 * side
        tiles, ents, a = [], [], head
        while (end - a) * f >= 0:
            t = bus_xy(main, a, at)
            if t not in blocked and ground.inside(t):
                ents.append({"name": belt, "position": {"x": t[0] + 0.5, "y": t[1] + 0.5}, "direction": d})
                tiles.append(t)
                a += f
                continue
            # something in the way: under it, from the belt before to the first free tile past it
            k = next((k for k in range(1, ug_max + 1) if bus_xy(main, a + k * f, at) not in blocked
                      and ground.inside(bus_xy(main, a + k * f, at))), None)
            if not ents or k is None or any((main["axis"], at, a + j * f) in ground.spans for j in range(-1, k + 1)):
                if ents and ents[-1].get("ug_type") is None and into_belt(ground, t):  # (not fed onto that belt)
                    ents.pop()
                    tiles.pop()
                break
            ents[-1] = dict(ents[-1], name=ug, ug_type="input")
            t = bus_xy(main, a + k * f, at)
            ents.append({"name": ug, "position": {"x": t[0] + 0.5, "y": t[1] + 0.5}, "direction": d,
                         "ug_type": "output"})
            tiles.append(t)
            a += (k + 1) * f
        if len(ents) >= 2 and (not best or len(tiles) > best[2]):
            best = (ents, bus_xy(main, head, at), abs(a - head))
    return best


def path_entities(path, belt_name, ug_name):
    out = []
    for tile, kind, rd in path:
        e = {"name": belt_name if kind == "belt" else ug_name,
             "position": {"x": tile[0] + 0.5, "y": tile[1] + 0.5}, "direction": rd}
        if kind != "belt":
            e["ug_type"] = "input" if kind == "ug-in" else "output"
        out.append(e)
    return out


FIND = object()  # (place's main: look for the bus in the ground)


class Blocked(ValueError):
    """no room for the plan: nowhere free in the snapshot, or something in the way where it was asked to go"""


def place(data, ground, plan_entities, sources, seed=None, avoid_ore=True, belt="transport-belt", connect=True,
          sinks=None, bus="auto", needs=None, extend_bus=True, progress=None, turn=0, avoid=None, spot=False, at=None,
          main=FIND):
    """bus: "auto" (build beside a main bus when the snapshot has one), "on" (the same, saying so when there is
    none), "here" (at the seed - a refit, in the area it replaces - its inputs still taken off the bus's lanes) or
    "off" (free ground nearest the seed, taps from any belt). needs: {item: items/s the build really takes}
    (a source's "rates" are what its belt is sized for, more than that): the sources' rates scaled to it, for the
    bus lanes' load. extend_bus: a bus that ends before the build's far edge (or its new lanes') gets its lanes
    continued that far, the ones ending in the open (BUS_PAST at most).
    With a bus the plan is turned so its inputs face it. turn: quarter turns clockwise on top of that (the
    window's Rotate). avoid: [x, y, w, h] boxes it must not land on (spots the window's Redo already showed).
    spot: the seed is where the player clicked, kept beside a bus too (else the bus picks the spot along it). at:
    the offset to use, exactly (the window's arrows), Blocked when something is in the way there. main: the
    bus already found in this ground (site.Site.scratch: a copy connect_posed() writes into), else it is looked for.
    -> dict(offset (of the plan as turned), turn (quarter turns clockwise), entities (new, world coords), taps,
    deliveries, outputs (where its sinks landed), notes, bus (what was found, or None))"""
    posed = pose(ground, plan_entities, sources, sinks, seed=seed, avoid_ore=avoid_ore, bus=bus, turn=turn,
                 avoid=avoid, spot=spot, at=at, main=main)
    return connect_posed(data, ground, posed, belt=belt, connect=connect, needs=needs, extend_bus=extend_bus,
                         progress=progress)


def pose(ground, plan_entities, sources, sinks=None, seed=None, avoid_ore=True, bus="auto", turn=0, avoid=None,
         spot=False, at=None, main=FIND):
    """where the plan goes (no routing: cheap). Arguments as place(). Blocked when there's no room.
    -> dict(offset, turn, side (of the bus, or 0), main (the bus or None), entities, sources, sinks (in world
    coordinates, as turned), notes)"""
    notes = []
    main = None if bus == "off" else find_bus(ground) if main is FIND else main
    keep_clear, side, k = (), 0, 0  # (k: quarter turns; kept as `turn`, the sinks loop reuses k)
    if main:
        side = bus_side(main, plan_entities, sources, seed)
        f = facing(plan_entities, sources)
        want = (-side, 0) if main["axis"] == 0 else (0, -side)  # (inputs toward the bus)
        k = (next((n for n in range(4) if f and turned(f[0], f[1], n) == want), 0) + turn) % 4
        plan_entities, sources, sinks = turn_plan(plan_entities, sources, sinks, k)
        if bus != "here":
            spot_, keep_clear = bus_spot(main, plan_entities, side, seed)
            seed = seed if spot and seed else spot_  # (a spot the player picked: kept, off the bus's room)
        np_ = sum(1 for ln in main["lanes"] if ln.get("pipe"))
        notes.append(f"main bus: {len(main['lanes']) - np_} lanes flowing {FLOW_NAME[main['dir']]}"
                     + (f" and {np_} pipe{'s' if np_ > 1 else ''} beside them" if np_ else "")
                     + ("; built where you asked, taking from its lanes" if bus == "here" else "; built beside it on the "
                        f"{FLOW_NAME[(main['axis'] + 4 + (0 if side > 0 else 8)) % 16]} side, taking from its lanes")
                     + (" (turned to face it)" if k else ""))
    elif bus == "on":
        notes.append("no main bus found in the snapshot (3+ long straight belts side by side, flowing the same way); "
                     "placed near the belts it taps instead")
    if not main and turn % 4:
        k = turn % 4
        plan_entities, sources, sinks = turn_plan(plan_entities, sources, sinks, k)
    turn = k
    if avoid:  # (spots already shown: kept clear by the spot search only, the taps may still cross them)
        keep_clear = set(keep_clear) | {(x, y) for ax, ay, aw, ah in avoid
                                        for x in range(math.floor(ax), math.ceil(ax + aw))
                                        for y in range(math.floor(ay), math.ceil(ay + ah))}
    if seed is None:  # near the belts carrying what the plan needs, else the middle of the snapshot
        pts = [c[0] for s in sources if s.get("kind") == "belt" for i in (s.get("lanes") or []) if i
               for c in tap_candidates(ground, {i})]
        if pts:
            seed = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        else:
            x1, y1, x2, y2 = ground.bounds
            seed = ((x1 + x2) / 2, (y1 + y2) / 2)
    if at is not None:  # (exactly there: the window's arrows)
        dx, dy = at
        hit = (ground.blocked(avoid_ore=False) | set(keep_clear)) & plan_tiles(shifted(plan_entities, dx, dy))
        if hit:
            raise Blocked("something's in the way there" + (" (the room kept beside your bus)" if not
                             hit & ground.blocked(avoid_ore=False) else "") + ": move it the other way")
    else:
        dx, dy = find_spot(ground, plan_entities, seed, avoid_ore, keep_clear=keep_clear)
    def moved(x):
        return dict(x, position={"x": x["position"]["x"] + dx, "y": x["position"]["y"] + dy})
    return {"offset": [dx, dy], "turn": turn, "side": side, "main": main, "entities": shifted(plan_entities, dx, dy),
            "sources": [moved(x) for x in sources], "sinks": [moved(x) for x in sinks or []], "notes": notes}


def connect_posed(data, ground, posed, belt="transport-belt", connect=True, needs=None, extend_bus=True,
                  progress=None):
    """the plan where pose() put it (or where the player pasted it: entities, sources and sinks in world
    coordinates) tied into the base: belt taps for its inputs, a new bus lane or a belt of the base for its outputs,
    the bus continued past it, power. ground and posed["main"] are written into (site.Site.scratch: copies).
    -> dict(offset, turn, entities (new, world coords), taps, deliveries, outputs (where its sinks are), inputs (fed
    by hand), notes, upgrades, bus (what was found, or None))"""
    main, side, turn = posed["main"], posed["side"], posed["turn"]
    new, sources, sinks = list(posed["entities"]), posed["sources"], posed["sinks"]
    notes = list(posed["notes"])
    dx = dy = 0  # (sources and sinks are in world coordinates already)
    blocked = ground.blocked(avoid_ore=False) | plan_tiles(new)
    ends = belt_ends(ground.snap["entities"] + new, blocked)
    blocked |= ends
    spans = set(ground.spans)
    taps = []
    inputs = []  # belt inputs nothing could be routed to: {items, position} (fed by hand)
    ug, ug_max = chain.related_belts(data, belt)
    names = (belt, ug, chain.related_splitter(data, belt), ug_max)
    foreign = pspans = None
    prefer = (main, side) if main else None
    sized = {}  # item -> what the sources' belts are sized for, all together
    for s in sources:
        for it, r in (s.get("rates") or {}).items():
            sized[it] = sized.get(it, 0) + r
    scale = {it: needs[it] / sized[it] for it in sized if needs and it in needs and sized[it] > 0}
    if main:  # (what each lane can carry: its belt's speed, both lanes; its tier, for the taps on it)
        for ln in main["lanes"]:
            ln["cap"] = (data.raw["transport-belt"].get(ln["belt"]) or {}).get("speed", 0.03125) * 480
            if ln["belt"] in data.raw["transport-belt"]:
                lug, lmax = chain.related_belts(data, ln["belt"])
                ln["names"] = (ln["belt"], lug, chain.related_splitter(data, ln["belt"]), lmax)
    # an output the bus doesn't carry yet gets a lane of its own beside the bus, from just past the build to the
    # bus's end; reserved before the taps so they route around it
    new_lanes = {}  # item -> the lane's tiles, upstream first
    if main and connect:
        on_bus = set().union(*(ln["items"] for ln in main["lanes"]))
        at = (main["lanes"][-1]["at"] if side > 0 else main["lanes"][0]["at"]) + 2 * side
        f = main["flow"]
        end = main["hi"] if f > 0 else main["lo"]
        along = [t[1] if main["axis"] == 0 else t[0] for t in plan_tiles(new)]
        past = (max(along) if f > 0 else min(along)) + f
        lane_items = list(dict.fromkeys(k["item"] for k in sinks or [] if k.get("kind", "belt") == "belt"
                                        and k.get("item") and k["item"] not in on_bus))
        # the bus continued past the build (and as far as its new lanes go), so the next build has a bus too
        reach = past + 8 * f if lane_items else past - f
        reach = min(reach, end + BUS_PAST) if f > 0 else max(reach, end - BUS_PAST)
        if extend_bus and (reach - end) * f > 0:
            longest = 0
            for ln in main["lanes"]:
                last = ln["hi"] if f > 0 else ln["lo"]
                e = ground.taken.get(bus_xy(main, last, ln["at"]))
                if last != end or not e or e["type"] != "transport-belt" or e.get("direction", 0) != main["dir"]:
                    continue  # (a lane that ends earlier, or in something: left as it is)
                run = []
                for a in range(last + f, reach + f, f):
                    t = bus_xy(main, a, ln["at"])
                    if (t in blocked and t not in ends) or not ground.inside(t):
                        if run and into_belt(ground, t, new):  # (stopped by a belt: not fed onto it)
                            run.pop()
                        break
                    run.append(t)
                blocked.update(run)
                new += [{"name": ln["belt"], "position": {"x": t[0] + 0.5, "y": t[1] + 0.5}, "direction": main["dir"]}
                        for t in run]
                longest = max(longest, len(run))
            if longest:
                end += longest * f
                notes.append(f"bus extended {longest} tiles past its end, so it reaches past this build")
        main["end"] = end
        for item in lane_items:
            head = past  # (past the build: the taps' branches, all level with it, never cross the new lane)
            last = end if (end - head) * main["flow"] >= 8 else head + 8 * main["flow"]
            lane = []
            for a in range(head, last + main["flow"], main["flow"]):
                t = bus_xy(main, a, at)
                if t in blocked or not ground.inside(t):
                    if lane and into_belt(ground, t, new):  # (stopped by a belt: not fed onto it)
                        lane.pop()
                    break
                lane.append(t)
            if len(lane) >= 4:
                new_lanes[item] = lane
                blocked.update(lane)
                at += 2 * side
    if connect:
        def front(s):  # (the input belt's tile, its direction, and the tile before it: where its route ends)
            goal = (math.floor(s["position"]["x"] + dx), math.floor(s["position"]["y"] + dy))
            goal_dir = (4 + 4 * turn) % 16
            for e in new:
                if footprint(e) == [goal] and e.get("direction") is not None:
                    goal_dir = e.get("direction", 4)
            return goal, goal_dir, (goal[0] - DIR[goal_dir][0], goal[1] - DIR[goal_dir][1])
        # every input's route end kept clear of the others' routes (a route passing through it left that input
        # nothing to end on: the next route then ended on top of it)
        blocked |= {front(s)[2] for s in sources if s.get("kind") == "belt"}
        routed = set()
        for s in sources:
            if s.get("kind") == "fluid":
                if foreign is None:
                    foreign, pspans = pipe_ground(ground, new)
                goal = (math.floor(s["position"]["x"] + dx), math.floor(s["position"]["y"] + dy))
                got = pipe_in(ground, blocked, foreign, pspans, s["fluid"], goal, into=(4 + 4 * turn) % 16)
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
            if progress:
                progress("routing " + " + ".join(sorted({x for x in lanes[:2] if x})) + " in from your base")
            goal, goal_dir, end = front(s)
            # the tile before the input belt is the route's end; it must not be part of the plan
            if end not in routed:
                blocked.discard(end)
            ents, got, problem = feed(ground, blocked, spans, lanes[:2], goal, goal_dir, names, prefer,
                                      {it: r * scale.get(it, 1) for it, r in (s.get("rates") or {}).items()})
            new += ents
            routed.update(t for e in ents for t in footprint(e))
            blocked.update(routed)
            for items, belt_e in got:
                taps.append({"items": items, "from": belt_e["position"], "belts": len(ents)})
            if problem:
                notes.append(problem + "; bring it to the input by hand")
                inputs.append({"items": sorted(s.get("rates") or [l for l in lanes[:2] if l]),
                               "position": {"x": goal[0] + 0.5, "y": goal[1] + 0.5}})
    upgrades = []  # overdrawn bus lanes: where they run, what they need (the window offers to upgrade them)
    for ln in main["lanes"] if main else []:
        took, used = main["load"].get(ln["at"], 0), ln.get("used", 0)
        what = " + ".join(sorted(ln["items"]))
        # (this build's take only with real needs, rates overstate; what the lane already carries is measured)
        if took and took + used > ln["cap"] * 1.02 and (ln["items"] & set(scale) or used):
            end = main.get("end", ln["hi"] if main["flow"] > 0 else ln["lo"])
            upgrades.append({"items": sorted(ln["items"]), "need": took + used, "belt": ln["belt"],
                             "axis": main["axis"], "at": ln["at"], "lo": min(ln["lo"], end), "hi": max(ln["hi"], end)})
            notes.append(f"{what}: this takes {took * 60:.0f}/min from one bus lane"
                         + (f" that already carries about {used * 60:.0f}/min" if used >= 0.5 else "")
                         + f", and its {ln['belt']} carries at most {ln['cap'] * 60:.0f}/min; upgrade that lane or "
                         f"add another lane of it")
        elif took and ln.get("dry"):
            notes.append(f"{what}: that bus lane already runs dry before its end, so what's downstream of this "
                         f"build gets less; add another lane of it")
    # outputs: onto a belt of the base that already carries the item (a science line, a bus lane)
    deliveries = []
    for k in (sinks or []) if connect else []:
        if k.get("kind") == "fluid" and k.get("item"):  # its pipe's end: on into a pipe of the base with that fluid
            if foreign is None:
                foreign, pspans = pipe_ground(ground, new)
            end = (math.floor(k["position"]["x"] + dx), math.floor(k["position"]["y"] + dy))
            ox_, oy_ = turned(1, 0, turn)
            goal = (end[0] + ox_, end[1] + oy_)
            blocked.discard(goal) if goal not in ground.taken else None
            got = pipe_in(ground, blocked, foreign, pspans, k["item"], goal, into=(12 + 4 * turn) % 16) \
                if goal not in ground.taken else None
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
                  .get("direction", 0) == dd and (start[0] - v[0], start[1] - v[1]) in here), (4 + 4 * turn) % 16)
        if start in ends:  # (the output belt's own end: where its route starts)
            blocked.discard(start)
        lane = new_lanes.pop(k["item"], None)
        if lane:  # into the head of its new bus lane
            blocked.difference_update(lane)
            path = None
            if start not in blocked:
                blocked.update(lane[1:])
                into = VEC_DIR[(-side, 0) if main["axis"] == 0 else (0, -side)]
                for gd in (into, main["dir"]):  # from the build's side (the head turns), else from behind
                    try:
                        path = router.route(blocked, [(start, [d])], lane[0], gd, ground.bounds, names[3],
                                            underground_spans=set(spans))
                        break
                    except router.RouteError:
                        pass
                else:
                    blocked.difference_update(lane[1:])
            if path:
                router.reserve(path, blocked, spans)
                blocked.add(lane[0])
                belts = path_entities(path, names[0], names[1]) + [
                    {"name": names[0], "position": {"x": t[0] + 0.5, "y": t[1] + 0.5}, "direction": main["dir"]}
                    for t in lane]
                new += belts
                deliveries.append({"item": k["item"], "to": belts[-len(lane)]["position"], "belts": len(belts),
                                   "new_lane": True})
                notes.append(f"{k['item']}: not on the bus yet, so it gets a new bus lane of its own ({len(lane)} "
                             f"belts beside the bus)")
                continue
        got = None if start in blocked else deliver(ground, blocked, spans, k["item"], start, d, names,
                                                    main and main["tiles"])
        if got:
            new += got[0]
            deliveries.append({"item": k["item"], "to": got[1]["position"], "belts": len(got[0])})
        else:
            notes.append(f"{k['item']}: no belt of yours carries it within reach; take the output where it's needed")
    poles = link_power(ground, blocked, new, planner.POLE, planner.POLE_REACH)
    if poles is None:
        poles = []
        notes.append("no way to run power poles to your network from here: connect its poles to your power yourself")
    elif poles:
        notes.append(f"{len(poles)} poles link it to your power")
    for lane in new_lanes.values():  # (lanes whose output never came: free again)
        blocked.difference_update(lane)
    outputs = [{"item": s.get("item"), "position": {"x": s["position"]["x"] + dx, "y": s["position"]["y"] + dy}}
               for s in sinks or []]
    return {"offset": posed["offset"], "turn": turn, "entities": new + poles, "taps": taps, "deliveries": deliveries,
            "outputs": outputs, "inputs": inputs, "notes": notes, "upgrades": upgrades,
            "bus": {k: v for k, v in main.items() if k != "tiles"} | {"lanes": len(main["lanes"])} if main else None}

