"""Loop mall: plate lanes and one mixed belt loop. For products that don't share their inputs (a modded mall:
beams, automation cores, inserter parts...), where a grid of pure lanes runs out of lanes.

Between columns of machines run pairs of belts. One of each pair is a plate belt (the two busiest inputs, iron and
copper plate, a lane each, fed at the top): the bulk of what a mall takes, at a whole belt's speed. The other is a
run of one belt loop that snakes down and up the pairs and closes along the top: it carries everything else, the
parts made here and the other inputs, mixed, so any recipe is next to all it needs.

    x:   -5 -4..-2 -1  0  1  2  3..5  6  7  8  9 ..       a machine reaches the pair on either side: the near
          .  M M M  i  L  P  i  M M M  i  L  P  i         belt with an inserter, the far one with a long-handed
          .  M M M  i  L  P  i  M M M  i  L  P  i         one; L: the loop (down at even pairs, up at odd ones),
          .  M M M  i  L  P  i  M M M  i  L  P  i         P: plate belts (down)
          .    o    P  L  P  .    o    P  L  P  .         o: product into its chest c, P (by i): poles
          .    c       L  P  .    c       L  P  .

Nothing jams the loop: each part goes on only while the loop holds fewer than its share of it, and each input
(short belts side-loading onto its top row and bottom turns) the same; one belt reads the whole loop (one red network
through the poles). A part maker takes from the belts only while its part is short too; a product's machine only
while its chest has fewer than `stock`, so the first ones down the loop don't take everything.
"""
import math

from bpgen import base, chain, mall_grid
from bpgen.ents import adder
from bpgen.mall_grid import _ings, _result
from bpgen.planner import EAST, NORTH, SOUTH, WEST, PIPE, POLE, UNDERGROUND, PlanError

PX, PY = 7, 5  # column pitch (inserters, machine, inserters, loop, plates), row pitch (machine, out, chest)
LONG = "long-handed-inserter"


def _top_input(m):
    """does this machine take a fluid in at the middle of its top side (as an assembling machine 2 does)?"""
    for fb in m.get("fluid_boxes") or []:
        if fb.get("production_type") == "input":
            return any(list(c.get("position") or []) == [0, -1] and c.get("direction", 0) == 0
                       for c in fb.get("pipe_connections") or [])
    return False


def _entries(runs, yb):
    """where an input belt may side-load onto the loop: (x, y of its last belt, -1: from the north / +1: from the
    south). Along the top row from the north (not where the plate belts and poles are), along each bottom turn from
    the south; alternating, so one input's entries taken evenly spaced land on both."""
    top = [(x, -3, -1) for x in range(2, PX * (runs - 1)) if x % PX not in (1, 4)]
    bottom = [(PX * k + dx, yb + 2, 1) for k in range(0, runs, 2) for dx in (1, 2, 3, 5, 6)]
    out = []
    for i in range(max(len(top), len(bottom))):
        out += top[i:i + 1] + bottom[i:i + 1]
    return out


def plan_sushi(data, products, machine, belt, inputs=None, allowed=None, chest="wooden-chest", chest_limit=2,
               keep=None, stock=mall_grid.STOCK, extra=True):
    """products: recipes, one machine and chest each; the parts they need (and those parts' parts) made here, once.
    inputs: items brought in (default: the plates and what's mined). keep: the most of an item on the loop (default:
    its share of half the loop, by what's taken of it). stock: see the module. extra: busy parts and products may
    get more machines (off: one each, a refit that adds none). -> dict as mall_grid.plan_grid"""
    recipes = data.raw["recipe"]
    allowed = set(allowed or [])
    m, cats, machine_for, products, inputs, notes = mall_grid.resolve(
        data, products, machine, inputs, fluids=_top_input(data.raw["assembling-machine"].get(machine) or {}))
    if not products:
        raise PlanError("nothing to make: " + "; ".join(notes))
    # the parts: everything a product (or a part) needs that isn't brought in, made here when this machine can
    made = {_result(data, r) for r in products}
    makers, brought, order = {}, set(), []

    def need(item, depth=0):
        if item in inputs or item in makers or item in brought or item in made:
            return
        rn = base.pick_recipe(data, item, machine_for, inputs)
        if not rn or mall_grid._fluids(data, rn) or depth > 8:
            brought.add(item)
            return
        makers[item] = rn
        for it in _ings(data, rn):
            need(it, depth + 1)
        order.append(item)  # (after its own parts)
    for r in products:
        for it in _ings(data, r):
            need(it)
    shared = set()  # products another product (or part) takes: onto the loop too
    for r in list(products) + [makers[i] for i in order]:
        shared |= set(_ings(data, r)) & made
    speed = m.get("crafting_speed", 1)

    def per_s(rn, item=None):
        rc = recipes[rn]
        crafts = speed / max(rc.get("energy_required", 0.5), 0.5)  # (a mod's instant crafts: inserters can't keep up)
        if item is None:
            return crafts * (rc.get("results") or [{}])[0].get("amount", 1)
        return crafts * sum(i.get("amount", 1) for i in rc.get("ingredients") or [] if i.get("name") == item)
    # a busy part gets more machines: what its users take, a part maker running all the time, a product a quarter
    users = list(products) + [makers[i] for i in order]
    part_makers = set(makers.values())
    count = {}
    for it in reversed(order):  # (users first: a part's users are counted with their extra machines)
        want = sum((1 if u in part_makers else 0.25) * per_s(u, it) * count.get(_result(data, u), 1)
                   for u in users if it in _ings(data, u))
        count[it] = max(1, min(4, math.ceil(want / per_s(makers[it]) - 0.05))) if extra else 1
    # a product others take too (belts for splitters, and for faster belts: 12 a craft) gets more machines as busy
    # parts do, the others only making it for the loop
    of = {_result(data, r): r for r in products}
    more = {}
    for it in shared:
        want = sum((1 if u in part_makers else 0.25) * per_s(u, it) for u in users if it in _ings(data, u))
        more[it] = 0 if not extra or mall_grid._fluids(data, of[it]) else (
            max(0, min(3, math.ceil(want / per_s(of[it]) - 0.05) - 1)))
    cells = [(makers[it], it, False) for it in order for _ in range(count[it])] + [
        (of[it], it, False) for it in sorted(shared) for _ in range(more[it])] + [
        (r, _result(data, r) if _result(data, r) in shared else None, True) for r in products]
    feeds = sorted({it for r, _, _ in cells for it in _ings(data, r) if it not in makers} - made)
    use = {it: sum(per_s(r, it) * (0.25 if product else 1) for r, _, product in cells)
           for it in set(feeds) | set(makers) | shared}
    plates = sorted(feeds, key=lambda it: -use[it])[:2]  # (the plate belts' two lanes)
    loop_feeds = [it for it in feeds if it not in plates]
    wet = [k for k, c in enumerate(cells) if mall_grid._fluids(data, c[0])]  # (top row of an odd column: piped in)

    # ---- the shape: pairs of belts (even, at least 2) for a roughly square block, room for the inputs ----
    n = len(cells)
    best = None
    for runs in range(2, 41, 2):
        rows = math.ceil(n / (runs + 1))
        if (len(_entries(runs, 0)) < len(loop_feeds) or runs // 2 < len(wet)) and runs < 40:
            continue
        w, h = PX * runs + 5, PY * rows + 8
        score = max(w, h) * 2 + w + h
        if best is None or score < best[0]:
            best = (score, runs, rows)
    _, runs, rows = best
    if runs // 2 < len(wet):
        raise PlanError(f"{len(wet)} products need a fluid: too many for the loop's top")
    yb = PY * rows - 1  # (the last row's chest)
    ins = next((x for x in ("fast-inserter", "inserter", "burner-inserter") if not allowed or x in allowed), "inserter")
    long_ = LONG if not allowed or LONG in allowed else ins
    pole = POLE if POLE in data.raw.get("electric-pole", {}) else next(iter(data.raw.get("electric-pole", {})), POLE)
    ug, _ = chain.related_belts(data, belt)
    ents = []

    put = adder(ents, tile=True)

    # the loop: down the even pairs' left belts, up the odd ones', round the bottom and top, back along the top
    loop = []
    for k in range(runs):
        x = PX * k
        if k % 2 == 0:
            for y in range(-1, yb + 1):
                loop.append(put(belt, x, y, SOUTH))
            for xx in range(x, x + PX):
                loop.append(put(belt, xx, yb + 1, EAST))
        else:
            for y in range(yb + 1, -1, -1):
                loop.append(put(belt, x, y, NORTH))
            if k < runs - 1:
                for xx in range(x, x + PX):
                    loop.append(put(belt, xx, -1, EAST))
            else:
                loop.append(put(belt, x, -1, NORTH))
                for xx in range(x, 0, -1):
                    loop.append(put(belt, xx, -2, WEST))
                loop.append(put(belt, 0, -2, SOUTH))
    # the plate belts: one input, a row east along the top with a splitter for each plate belt (its other half
    # turns down into it), under the loop's top rows, down to above its bottom turns; one tap feeds them all
    splitter = chain.related_splitter(data, belt)
    put(belt, -1, -8, EAST)
    # (two items: one in from the north, one from the south, side-loading onto a lane each; a single-item input is
    # one tap, where one belt carrying both is rarely there to tap)
    sources = []
    for p_, y0_, flow in ((plates[0], -10, SOUTH), (plates[-1], -6, NORTH)) if len(set(plates)) == 2 else (
            (plates[0], -10, SOUTH),):
        put(belt, -1, y0_, flow)
        put(belt, -1, y0_ + (1 if flow == SOUTH else -1), flow)
        sources.append({"kind": "belt", "position": {"x": -0.5, "y": y0_ + 0.5}, "lanes": [p_, p_], "rates": {p_: 1}})
    for k in range(runs):
        x = PX * k + 1
        put(splitter, x - 1, -7.5, EAST)  # (tiles (x - 1, -8) and (x - 1, -7))
        if k < runs - 1:
            for xx in range(x, x + PX - 1):
                put(belt, xx, -8, EAST)
        for y in (-7, -6, -5, -4):
            put(belt, x, y, SOUTH)
        put(ug, x, -3, SOUTH, ug_type="input")
        put(ug, x, 0, SOUTH, ug_type="output")
        for y in range(1, yb - 1):
            put(belt, x, y, SOUTH)
    # how many of each item the loop may hold: what it takes of each in one lap of the loop (a part far from its
    # makers waits that long), all together at most about 60% of what the loop carries (it never fills up).
    on_loop = sorted(set(loop_feeds) | set(makers) | shared)
    most = {it: max([i.get("amount", 1) for r, _, _ in cells for i in recipes[r].get("ingredients") or []
                     if i.get("name") == it] or [1]) for it in on_loop}
    lap = len(loop) / (data.raw["transport-belt"][belt]["speed"] * 60)
    want = {it: max(8, 2 * most[it], use.get(it, 0) * lap) for it in on_loop}
    scale = min(1, max(0.6 * len(loop) * 8, 8 * len(want)) / (sum(want.values()) or 1))
    caps = {it: max(8, 2 * most[it], int(v * scale)) for it, v in want.items()}
    if keep:
        caps = {it: keep for it in caps}
    if scale < 0.5:
        notes.append(f"the loop is slow for this mall ({lap:.0f} s a lap on {belt}): a faster belt gets the parts "
                     "round sooner")
    reader = loop[0]
    reader["control_behavior"] = {"circuit_read_hand_contents": True, "circuit_contents_read_mode": 2}
    reader["wire_id"] = "loop"
    wired = [reader]  # (on the loop's network: each wired to its nearest pole below)

    def below(item, n_, wire=None):
        return {"circuit_to": [wire] if wire else [], "control_behavior": {"circuit_enabled": True,
                "circuit_condition": {"first_signal": {"name": item, "type": "item"}, "constant": n_,
                                      "comparator": "<"}}}

    # the machines: (cell index -> column, row); fluid ones in the top row of odd columns, then the rest
    spots = {c: (2 * k + 1, 0) for k, c in enumerate(wet)}
    free = [(j, i) for j in range(runs + 1) for i in range(rows) if (j, i) not in spots.values()]
    for k in range(len(cells)):
        if k not in spots:
            spots[k] = free.pop(0)
    for k, (rn, part, product) in enumerate(cells):
        j, i = spots[k]
        x0, y0 = PX * j - 4, PY * i
        put(machine, x0 + 1, y0 + 1, recipe=rn)
        if k in wet:  # (its input pipe above it: under the loop's top to a pipe beyond the inputs)
            x = x0 + 1
            put(UNDERGROUND, x, -1, SOUTH)
            put(UNDERGROUND, x, -6, NORTH)
            put(PIPE, x, -7)
            sources.append({"kind": "fluid", "fluid": mall_grid._fluids(data, rn)[0],
                            "position": {"x": x - 0.5, "y": -6.5}})
        ings = set(_ings(data, rn))
        from_plates, from_loop = bool(ings & set(plates)), bool(ings - set(plates))
        # what gates its inserters taking in: a part maker the loop's count of its part, a product its chest
        if part:  # (a product the loop carries too: while it's short there; its chest fills meanwhile)
            gate = "loop"
        elif product and not part and stock and _result(data, rn):
            gate = f"chest {k}"
        else:
            gate = None
        right, left = j < runs, j > 0
        # (x, picks from, takes from: "loop" / "plates" / "out", long-handed) - the right side's near belt is the
        # loop, the left side's the plates
        slots = []
        if right:
            slots += [(x0 + 3, y0, "loop", False), (x0 + 3, y0 + 1, "out" if part else "loop", False),
                      (x0 + 3, y0 + 2, "plates", True)]
        if left:
            slots += [(x0 - 1, y0, "plates", False), (x0 - 1, y0 + 1, "loop", True),
                      (x0 - 1, y0 + 2, "out" if part and not right else "plates", part and not right)]
        for x, y, what, is_long in slots:
            name = long_ if is_long else ins
            side_in = WEST if x < x0 else EAST  # (picking from the belts on that side)
            if what == "out":  # its part onto the loop, while the loop is short of it
                wired.append(put(name, x, y, EAST if x < x0 else WEST, **below(part, caps[part])))
                continue
            if (what == "plates" and not from_plates) or (what == "loop" and not from_loop):
                continue
            if gate == "loop":
                wired.append(put(name, x, y, side_in, **below(part, caps[part])))
            elif gate:
                put(name, x, y, side_in, **below(_result(data, rn), stock, gate))
            else:
                put(name, x, y, side_in)
        if product and part and stock:  # (the loop takes it too: its chest keeps only `stock`, the rest goes round)
            put(ins, x0 + 1, y0 + 3, NORTH, **below(part, stock, f"chest {k}"))
        elif product:
            put(ins, x0 + 1, y0 + 3, NORTH)
        if product:
            put(chest, x0 + 1, y0 + 4, bar=chest_limit, wire_id=f"chest {k}")
    for j in range(runs + 1):  # (poles right of each column, below its inserters: they reach the whole row)
        for i in range(rows):
            put(pole, PX * j - 1, PY * i + 3, wire_id=f"pole {j},{i}")
    # the loop's other inputs: short belts side-loading onto it, a busy one at several places round it (what the
    # machines between took is topped up at the next); each one's last belt runs only while the loop is short of it
    spots_ = _entries(runs, yb)
    if len(spots_) < len(loop_feeds):
        raise PlanError(f"{len(loop_feeds)} inputs don't fit round the loop")
    share = [1] * len(loop_feeds)
    for _ in range(min(len(spots_) - len(loop_feeds), 3 * len(loop_feeds))):
        i = max((i for i in range(len(loop_feeds)) if share[i] < 4),
                key=lambda i: use[loop_feeds[i]] / share[i], default=None)
        if i is None or use[loop_feeds[i]] / share[i] < 1:
            break
        share[i] += 1
    queue = [i for n_ in range(max(share, default=0)) for i in range(len(loop_feeds)) if share[i] > n_]
    for n_, i in enumerate(queue):
        it = loop_feeds[i]
        x, y, d = spots_[int(n_ * len(spots_) / len(queue))]
        flow = SOUTH if d < 0 else NORTH
        put(belt, x, y + 2 * d, flow)
        put(belt, x, y + d, flow)
        wired.append(put(belt, x, y, flow, **below(it, caps[it])))
        sources.append({"kind": "belt", "position": {"x": x + 0.5, "y": y + 2 * d + 0.5}, "lanes": [it, it],
                        "rates": {it: 1}})
    for k in range(runs - 1):
        put(pole, PX * k + 4, -3, wire_id=f"pole top {k}")
    for k in range(0, runs, 2):
        put(pole, PX * k + 4, yb + 2, wire_id=f"pole bottom {k}")
    # the loop's network: poles chained (nearest first), each wired entity to its nearest pole
    poles = [e for e in ents if e["name"] == pole]

    def d2(a, b):
        return (a["position"]["x"] - b["position"]["x"]) ** 2 + (a["position"]["y"] - b["position"]["y"]) ** 2
    linked, rest = [poles[0]], poles[1:]
    while rest:
        a, b = min(((a, b) for a in rest for b in linked), key=lambda p: d2(*p))
        a["circuit_to"] = [b["wire_id"]]
        linked.append(a)
        rest.remove(a)
    for e in wired:
        e.setdefault("circuit_to", []).append(min(poles, key=lambda q: d2(q, e))["wire_id"])
    notes.insert(0, f"{runs + 1} columns on {runs} plate belts ({' and '.join(plates)}) and a loop of {len(loop)} "
                    f"belts: {len(products)} products, {len(cells) - len(products)} machines making parts "
                    f"({', '.join(order) or 'none'}); onto the loop: {', '.join(loop_feeds) or 'nothing'}")
    if brought - set(feeds):
        notes.append("not made here, brought in: " + ", ".join(sorted(brought)))
    return {"entities": ents, "sources": sources, "notes": notes, "products": list(products),
            "makers": {makers[it]: count[it] for it in order}, "inputs": feeds,
            "lanes": {it: ["the loop"] for it in on_loop} | {p: ["the plate belts"] for p in plates},
            "columns": runs + 1}
