"""Belt routing on the tile grid (A*), with underground belts to cross obstacles.

A route is a chain of belts from a start tile to a goal: the goal is "enter tile G moving in direction D"
(e.g. the first tile of a block's input belt, moving east). Tiles are integer (x, y) = the tile's top-left.
"""
import heapq
import itertools

DIRS = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}


class RouteError(Exception):
    pass


MAX_EXPANSIONS = 200_000


def _step(p, d, k=1):
    dx, dy = DIRS[d]
    return p[0] + dx * k, p[1] + dy * k


try:  # (the same search in Rust, bpgen_fast/, built by python -m bpgen.build_fast into bpgen/bpgen_fast.pyd; Python below otherwise)
    from bpgen import bpgen_fast as _fast
except ImportError:
    _fast = None


def route(blocked, starts, goal_tile, goal_dir, bounds, max_underground, underground_spans=None, forbidden_gaps=None):
    if _fast is not None:
        try:
            return _fast.route(blocked, [(t, list(d) if d else None) for t, d in starts], goal_tile, goal_dir, bounds,
                               max_underground, underground_spans, forbidden_gaps, MAX_EXPANSIONS)
        except LookupError as e:
            raise RouteError(str(e)) from None
    return route_py(blocked, starts, goal_tile, goal_dir, bounds, max_underground, underground_spans, forbidden_gaps)


def route_py(blocked, starts, goal_tile, goal_dir, bounds, max_underground, underground_spans=None, forbidden_gaps=None):
    """blocked: set of tiles nothing may occupy; starts: [(tile, allowed first directions or None)].
    Returns [(tile, kind, direction)] with kind 'belt' | 'ug-in' | 'ug-out'.
    underground_spans: set of (axis, fixed, tile-along) already used by other undergrounds (updated in place)."""
    x1, y1, x2, y2 = bounds
    spans = underground_spans if underground_spans is not None else set()
    before_goal = _step(goal_tile, goal_dir, -1)

    def free(t):
        return x1 <= t[0] <= x2 and y1 <= t[1] <= y2 and t not in blocked

    def h(t):
        return abs(t[0] - before_goal[0]) + abs(t[1] - before_goal[1])

    tie = itertools.count()
    open_ = []
    came = {}
    best = {}
    for tile, dirs in starts:
        if not free(tile):
            continue
        for d in (dirs or DIRS):
            state = (tile, d, "belt")
            best[state] = 0
            came[state] = None
            heapq.heappush(open_, (h(tile), next(tie), 0, state))
    expanded = 0
    while open_:
        _, _, g, state = heapq.heappop(open_)
        # (a route that can't be found would search every state of a big area, each looking back along its path:
        # minutes. Past this budget it's given up like any route that isn't there.)
        expanded += 1
        if expanded > MAX_EXPANSIONS:
            raise RouteError(f"no belt route found to {goal_tile} (gave up after {MAX_EXPANSIONS} steps)")
        if g > best.get(state, 1e18):
            continue
        tile, d, kind = state
        if tile == before_goal and d == goal_dir and kind in ("belt", "ug-out"):
            return _unwind(came, state)
        nxt = _step(tile, d)
        mine = _recent_tiles(came, state)  # the route may not come back over its own pieces
        # a belt/underground exit at `tile` pointing `d` feeds `nxt`: continue with a belt there
        for nd in (d, (d + 4) % 16, (d - 4) % 16):
            if not free(nxt) or nxt in mine:
                break
            cost = g + 1 + (0.3 if nd != d else 0)
            _push(open_, best, came, (nxt, nd, "belt"), cost, state, h, tie)
        # or an underground entrance at `nxt` (fed straight), exit k tiles further
        if free(nxt) and nxt not in mine and kind != "ug-in":
            axis = d % 8
            for k in range(2, max_underground + 1):
                out = _step(nxt, d, k)
                if not free(out) or out in mine:
                    continue
                cells = [_step(nxt, d, i) for i in range(0, k + 1)]
                key_cells = {(axis, c[0] if axis == 0 else c[1], c[1] if axis == 0 else c[0]) for c in cells}
                if key_cells & spans:
                    continue
                if forbidden_gaps and any(c in forbidden_gaps for c in cells[1:-1]):
                    continue
                cost = g + 6 + k * 0.6
                st = (out, d, "ug-out")
                if cost < best.get(st, 1e18):
                    best[st] = cost
                    came[st] = (state, ("ug", nxt, out, d))
                    heapq.heappush(open_, (cost + h(out), next(tie), cost, st))
    raise RouteError(f"no route to {goal_tile}")


def _recent_tiles(came, state, depth=24):
    """tiles the best path to `state` occupies, for its last `depth` pieces (loops are local)"""
    tiles = set()
    while state is not None and depth > 0:
        tiles.add(state[0])
        link = came.get(state)
        if not link:
            break
        prev, extra = link
        if extra:
            tiles.add(extra[1])  # the underground entrance
        state = prev
        depth -= 1
    return tiles


def _push(open_, best, came, st, cost, prev, h, tie):
    if cost < best.get(st, 1e18):
        best[st] = cost
        came[st] = (prev, None)
        heapq.heappush(open_, (cost + h(st[0]), next(tie), cost, st))


def _unwind(came, state):
    out = []
    while state is not None:
        link = came[state]
        tile, d, kind = state
        if link is None:
            out.append((tile, "belt", d))
            break
        prev, extra = link
        if extra:  # underground jump from prev: entrance at extra[1], exit at extra[2]
            _, ent, ex, ud = extra
            out.append((ex, "ug-out", ud))
            out.append((ent, "ug-in", ud))
        else:
            out.append((tile, "belt", d))
        state = prev
    out.reverse()
    return out


def reserve(route_tiles, blocked, spans):
    """mark a finished route as obstacles (and its underground spans) for the next routes"""
    ents = route_tiles
    for i, (tile, kind, d) in enumerate(ents):
        blocked.add(tile)
    for i in range(len(ents) - 1):
        if ents[i][1] == "ug-in":
            ent, ex, d = ents[i][0], ents[i + 1][0], ents[i][2]
            axis = d % 8
            k = abs(ex[0] - ent[0]) + abs(ex[1] - ent[1])
            for j in range(k + 1):
                c = _step(ent, d, j)
                spans.add((axis, c[0] if axis == 0 else c[1], c[1] if axis == 0 else c[0]))
    # the tile in front of the route's last belt is its goal; nothing else may sit in front of a belt end
