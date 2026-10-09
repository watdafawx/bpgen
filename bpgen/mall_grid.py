"""Grid mall: columns of machines between pairs of mixed belts, as players build them by hand.

    x:  0 1 2 3-5 6 7 8 9 10-12 13 14 15 ...      belts flow south, fed at the top (row 0)
        B B i  M  i B B i   M    i  B  B          a column's machines reach the two belts on each side:
        B B i  M  i B B i   M    i  B  B          near and long-handed inserters, 8 lanes, any of them
        B B .  o  . B B .   o    .  B  B          o: output inserter, c: the product's chest (limited)
        B B P  c  P B B P   c    P  B  B          P: poles
        B B .  h  . B B .   h    .  B  B          h: hands the chest on to the machine below, when that one
                                                     uses it (a component goes straight into what it's for)

Inputs come in at the top, one item per lane: plates, and whatever parts the player's bus carries. Parts the bus
doesn't carry (gears, cable, circuits...) are made in a band of machines at the top, each dropping its part onto a
lane of a belt beside it (an inserter drops on a belt's far lane: west lanes from the column east of the belt, east
lanes from the column west of it); the products below take them from there.

Placement is greedy: products in an order that keeps those with the same ingredients together, each into the
current column if its 8 lanes can carry what it needs (adding input lanes, or a maker for a part, into free lanes),
else into the next column, which shares the belt pair between them.
"""
import copy
import math

from bpgen import base, planner
from bpgen.planner import EAST, NORTH, SOUTH, WEST, POLE, PlanError

PX, PY = 7, 6  # column pitch (2 belts, inserters, a 3x3 machine, inserters), cell pitch (machine, out, chest, hand)
MAX_ROWS = 7  # products a column, below the makers
STOCK = 10  # a product's machine takes from the belts only while its chest has fewer than this (0: no limit)
WOOD = "wood"  # (from trees, not mined: always a belt input, for wooden chests and small poles)
EAST_LANE, WEST_LANE = "E", "W"
RATE = {"fast-inserter": 2.3, "inserter": 0.83, "long-handed-inserter": 1.15, "burner-inserter": 0.6}  # items/s, roughly


def belt_x(b):
    return PX * (b // 2) + b % 2


def reach(c):
    """the belts a column-c machine takes from: (belt, side of the column, long-handed)"""
    return [(2 * c + 1, "L", False), (2 * c, "L", True), (2 * c + 2, "R", False), (2 * c + 3, "R", True)]


def drop_lanes(c):
    """the lanes a column-c maker can drop onto: (belt, lane, side, long-handed)"""
    return [(2 * c + 1, WEST_LANE, "L", False), (2 * c, WEST_LANE, "L", True),
            (2 * c + 2, EAST_LANE, "R", False), (2 * c + 3, EAST_LANE, "R", True)]


def _ings(data, recipe):
    return [i["name"] for i in data.raw["recipe"][recipe].get("ingredients") or [] if i.get("type") != "fluid"]


def _result(data, recipe):
    res = data.raw["recipe"][recipe].get("results") or []
    return res[0]["name"] if len(res) == 1 else None


class State:
    def __init__(self):
        self.lanes = {}  # (belt, lane) -> {"item", "kind": "in" | "made"}
        self.cols = []  # [{"makers": [recipe...], "cells": [[recipe...] stacks]}]

    def items_near(self, c):
        out = {}
        for b, _, _ in reach(c):
            for ln in (EAST_LANE, WEST_LANE):
                v = self.lanes.get((b, ln))
                if v:
                    out.setdefault(v["item"], b)
        return out


def plan_grid(data, products, machine, belt, inputs=None, allowed=None, chest="wooden-chest", chest_limit=2,
              max_rows=MAX_ROWS, stock=STOCK):
    """products: recipes, one machine and chest each. inputs: items that come in on the belts (default: the plates and
    what's mined). -> dict(entities, sources, notes, products, makers, inputs, columns)"""
    recipes = data.raw["recipe"]
    m = data.raw["assembling-machine"].get(machine)
    if not m or planner._dims(m) != (3, 3):
        raise PlanError(f"{machine}: the grid mall lays out 3x3 assembling machines")
    cats = set(m.get("crafting_categories") or [])
    products = [p for p in dict.fromkeys(products) if p in recipes]
    for r in products:
        if recipes[r].get("category", "crafting") not in cats:
            raise PlanError(f"{machine} can't craft {r}")
    raw = base.raw_items(data) - base.MADE_HERE
    if inputs is None:  # (the plates, and what's smelted from them: steel)
        plates = base.plate_items(data, raw)
        inputs = raw | plates | base.plate_items(data, plates)
    inputs = set(inputs) | ({WOOD} if WOOD in data.raw.get("item", {}) else set())
    notes = []
    made_by = {_result(data, r): r for r in products}
    # a component of exactly one product, used nowhere else: right above it, handed on through its chest
    users = {}
    for r in products:
        for i in _ings(data, r):
            users.setdefault(i, []).append(r)
    above = {}  # user -> the product right above it
    for item, us in users.items():
        maker = made_by.get(item)
        if maker and len(us) == 1 and us[0] != maker and us[0] not in above and maker not in above.values():
            above[us[0]] = maker
    stacks, placed = [], set()
    for r in products:
        if r in placed or r in above.values():
            continue
        st = [r]
        while st[0] in above:
            st.insert(0, above[st[0]])
        stacks.append(st)
        placed.update(st)
    for r in products:  # (a chain's top that is also below something, in a loop: on its own)
        if r not in placed:
            stacks.append([r])
            placed.add(r)

    def needs(stack):
        out = []
        for i, r in enumerate(stack):
            for it in _ings(data, r):
                if i > 0 and _result(data, stack[i - 1]) == it:
                    continue  # (handed down from the one above)
                if it not in out:
                    out.append(it)
        return out

    machine_for = base.machines_by_category(data, [machine])
    part_recipe = {}

    def recipe_for(item):
        if item not in part_recipe:
            rn = base.pick_recipe(data, item, machine_for, inputs)
            part_recipe[item] = rn if rn and recipes[rn].get("category", "crafting") in cats else None
        return part_recipe[item]

    # products with the same ingredients next to each other
    order, left = [], list(stacks)
    while left:
        if not order:
            nxt = max(left, key=lambda s: len(needs(s)))
        else:
            have = set(needs(order[-1]))
            nxt = max(left, key=lambda s: (len(have & set(needs(s))), -len(needs(s))))
        order.append(nxt)
        left.remove(nxt)

    def supply(st, c, item, depth=0):
        """put `item` within column c's reach: already there, an input lane, or a maker in c's band -> bool"""
        if item in st.items_near(c):
            return True
        free = [(b, ln) for b, _, _ in reach(c) for ln in (EAST_LANE, WEST_LANE) if (b, ln) not in st.lanes]
        if item in inputs:
            if not free:
                return False
            st.lanes[free[0]] = {"item": item, "kind": "in"}
            return True
        rn = recipe_for(item)
        if not rn or depth > 6:
            return False
        # a lane of column c's reach that a maker can drop onto: from c itself, or from the column on the other side
        # of that belt pair (c - 1 drops on the east lanes of c's west pair, c + 1 on the west lanes of its east pair)
        mine = {b for b, _, _ in reach(c)}
        cands = [(mc, (b, ln)) for mc in (c, c + 1, c - 1) if mc >= 0
                 for b, ln, _, _ in drop_lanes(mc) if b in mine and (b, ln) not in st.lanes]
        if not cands:
            return False
        mc, lane = cands[0]
        while len(st.cols) <= mc:
            st.cols.append({"makers": [], "cells": []})
        st.lanes[lane] = {"item": item, "kind": "made", "maker": rn}
        c = mc  # (the maker's own ingredients: within its column's reach)
        col = st.cols[c]
        at = len(col["makers"])
        col["makers"].append({"recipe": rn, "lane": lane})
        for it in _ings(data, rn):  # (its own ingredients: within the same reach, made above it if parts)
            before = len(col["makers"])
            if not supply(st, c, it, depth + 1):
                return False
            if len(col["makers"]) > before:  # (makers it needed go above it)
                new = col["makers"][before:]
                del col["makers"][before:]
                col["makers"][at:at] = new
                at += len(new)
        return True

    st = State()
    st.cols.append({"makers": [], "cells": []})
    missing = []
    for stack in order:
        for _ in range(2):
            c = len(st.cols) - 1
            col = st.cols[c]
            rows = sum(len(s) for s in col["cells"])
            if rows + len(stack) <= max_rows:
                trial = copy.deepcopy(st)
                if all(supply(trial, c, it) for it in needs(stack)):
                    st = trial
                    st.cols[c]["cells"].append(stack)
                    break
            if not col["cells"]:
                missing.append(stack)
                break
            st.cols.append({"makers": [], "cells": []})
        else:
            missing.append(stack)
    st.cols = [c for c in st.cols if c["cells"] or c["makers"]]
    # a part a maker uses, made by fewer machines than keep it busy (cable for circuits): more of them, on its lane
    speed = m.get("crafting_speed", 1)

    def per_s(r, item):
        rc = recipes[r]
        crafts = speed / max(rc.get("energy_required", 0.5), 1e-3)
        if item is None:
            return crafts * (rc.get("results") or [{}])[0].get("amount", 1)
        return crafts * sum(i.get("amount", 1) for i in rc.get("ingredients") or [] if i["name"] == item)
    for col in st.cols:
        mk = col["makers"]
        i = 0
        while i < len(mk):
            part = _result(data, mk[i]["recipe"])
            want = sum(per_s(o["recipe"], part) for o in mk if part in _ings(data, o["recipe"]))
            have = sum(per_s(o["recipe"], None) for o in mk if o["recipe"] == mk[i]["recipe"])
            if want > have * 1.05 and sum(o["recipe"] == mk[i]["recipe"] for o in mk) < 4:
                mk.insert(i + 1, dict(mk[i]))  # (right below it, same lane: still above what uses it)
                continue
            i += 1
    if missing:
        notes.append("left out (their ingredients don't fit 8 lanes, or can't be made here): "
                     + ", ".join(r for s in missing for r in s))

    # ---- entities ----
    allowed = set(allowed or [])
    near = next((n for n in ("fast-inserter", "inserter", "burner-inserter") if not allowed or n in allowed), "inserter")
    long_ = "long-handed-inserter"
    ents = []

    def put(name, x, y, d=None, **kw):
        e = {"name": name, "position": {"x": x, "y": y}}
        if d is not None:
            e["direction"] = d
        e.update(kw)
        ents.append(e)

    # the makers' rows, all columns together: each below every maker of a part it takes (a lane only carries a part
    # downstream of where it's dropped on), one machine a row in a column
    every = [(c, mk) for c, col in enumerate(st.cols) for mk in col["makers"]]
    for i, (_, mk) in enumerate(every):
        mk["row"], mk["id"] = 0, i
    deps = {}
    for c, mk in every:
        lanes_of = {(b, ln) for b, _, _ in reach(c) for ln in (EAST_LANE, WEST_LANE)
                    if st.lanes.get((b, ln), {}).get("kind") == "made"
                    and st.lanes[(b, ln)]["item"] in _ings(data, mk["recipe"])}
        deps[mk["id"]] = [o for _, o in every if o["lane"] in lanes_of and o is not mk]
    for _ in range(4 * len(every) + 4):
        changed = False
        for c, mk in every:
            want_row = max([o["row"] + 1 for o in deps[mk["id"]]] + [0])
            if want_row > mk["row"]:
                mk["row"], changed = want_row, True
        for col in st.cols:  # (two in one row of a column: the later one lower)
            taken = set()
            for mk in sorted(col["makers"], key=lambda o: (o["row"], o["id"])):
                while mk["row"] in taken:
                    mk["row"], changed = mk["row"] + 1, True
                taken.add(mk["row"])
        if not changed:
            break
    band = max([mk["row"] + 1 for _, mk in every] + [0])
    rows_total = band + max(sum(len(s) for s in c["cells"]) for c in st.cols)
    bottom = 1 + PY * rows_total
    nbelts = 2 * len(st.cols) + 2
    for b in range(nbelts):
        for y in range(0, bottom):
            put(belt, belt_x(b) + 0.5, y + 0.5, SOUTH)

    def cell(c, row, recipe, out_lane=None, chest_out=True, hand_down=False):
        x0, y0 = PX * c + 3, 1 + PY * row
        put(machine, x0 + 1.5, y0 + 1.5, recipe=recipe)
        near_items = st.items_near(c)
        want = set(_ings(data, recipe))
        slots = {"L": [0, 1, 2], "R": [0, 1, 2]}
        if out_lane:  # (a maker: its part onto its lane, first slot of that side)
            b, ln, side, is_long = next(t for t in drop_lanes(c) if (t[0], t[1]) == out_lane)
            x = x0 - 1 if side == "L" else x0 + 3
            put(long_ if is_long else near, x + 0.5, y0 + slots[side].pop(1) + 0.5, EAST if side == "L" else WEST)
        # an inserter for each belt carrying something it needs (also the belt a maker drops onto: its ingredient may
        # be on the other lane); a maker, busy all the time, gets more on a belt it takes a lot from, slots allowing
        # each ingredient from one belt (an item on belts both sides would take slots twice): the busiest first, onto
        # a belt already taken from, else the side with more room, near before long-handed
        info = {b: (side, is_long) for b, side, is_long in reach(c)}
        on = {}
        for (bb, _), v in st.lanes.items():
            if bb in info and v["item"] in want:
                on.setdefault(v["item"], []).append(bb)
        room = {s_: len(slots[s_]) for s_ in slots}
        chosen = {}  # belt -> items
        for item in sorted(on, key=lambda i: -per_s(recipe, i)):
            cands = sorted(on[item], key=lambda bb: (bb not in chosen, -room[info[bb][0]], info[bb][1]))
            bb = cands[0]
            if bb not in chosen:
                room[info[bb][0]] -= 1
            chosen.setdefault(bb, []).append(item)
        feeds = []
        for bb, items in chosen.items():
            side, is_long = info[bb]
            need = sum(per_s(recipe, i) for i in items) if out_lane else 0
            feeds.append([need / RATE.get(long_ if is_long else near, 1), 1, bb, side, is_long])
        for f in sorted(feeds, key=lambda f: -f[0]):  # (one each first)
            if not slots[f[3]]:
                f[1] = 0
        for f in sorted(feeds, key=lambda f: -f[0]):  # (then more where they're short, busiest first)
            spare = len(slots[f[3]]) - sum(g[1] for g in feeds if g[3] == f[3])
            f[1] += max(0, min(spare, math.ceil(f[0]) - f[1]))
        # a product's inputs: wired to its chest, they run only while it has fewer than `stock` (the products up a
        # column then stop early, and the plates get down to the ones below)
        chest_id = f"chest {c},{row}"
        limit = {}
        if chest_out and stock and _result(data, recipe):
            limit = {"circuit_to": [chest_id], "control_behavior": {  # (2.0's blueprint keys, as the game writes them)
                "circuit_enabled": True,
                "circuit_condition": {"first_signal": {"name": _result(data, recipe)},
                                      "constant": stock, "comparator": "<"}}}
        for share, n, b, side, is_long in feeds:
            x = x0 - 1 if side == "L" else x0 + 3
            for _ in range(n):
                put(long_ if is_long else near, x + 0.5, y0 + slots[side].pop(0) + 0.5, WEST if side == "L" else EAST,
                    **limit)
        if chest_out:
            put(near, x0 + 1.5, y0 + 3.5, NORTH)
            put(chest, x0 + 1.5, y0 + 4.5, bar=chest_limit, wire_id=chest_id)
            if hand_down:
                put(near, x0 + 1.5, y0 + 5.5, NORTH)
        for px in (x0 - 1, x0 + 3):
            put(POLE, px + 0.5, y0 + 4.5)
            if row == 0:  # (the top row's first inserters: out of reach of the pole below them)
                put(POLE, px + 0.5, 0.5)

    makers = {}
    for c, col in enumerate(st.cols):
        used = {mk["row"] for mk in col["makers"]}
        for i in range(band):  # (rows of the band this column doesn't use: poles all the same)
            if i in used:
                continue
            for px in (PX * c + 2, PX * c + 6):
                put(POLE, px + 0.5, 1 + PY * i + 4.5)
                if i == 0:
                    put(POLE, px + 0.5, 0.5)
        for mk in col["makers"]:
            cell(c, mk["row"], mk["recipe"], out_lane=mk["lane"], chest_out=False)
            makers[mk["recipe"]] = makers.get(mk["recipe"], 0) + 1
        row = band
        for stack in col["cells"]:
            for j, r in enumerate(stack):
                cell(c, row, r, hand_down=j < len(stack) - 1)
                row += 1
    # inputs: each belt's input lanes, fed at the top (lanes[0]: east, the left of a belt flowing south)
    sources = []
    for b in range(nbelts):
        lanes = [None, None]
        for ln, i in ((EAST_LANE, 0), (WEST_LANE, 1)):
            v = st.lanes.get((b, ln))
            if v and v["kind"] == "in":
                lanes[i] = v["item"]
        if any(lanes):
            sources.append({"kind": "belt", "position": {"x": belt_x(b) + 0.5, "y": 0.5}, "lanes": lanes,
                            "rates": {i: 0.5 for i in lanes if i}})
    # belts no lane of which is used: dropped (the outermost ones of an odd count)
    used = {b for b, _ in st.lanes}
    ents = [e for e in ents if e["name"] != belt or any(abs(e["position"]["x"] - belt_x(b) - 0.5) < 0.01 for b in used)]
    lanes_note = {}
    for (b, ln), v in sorted(st.lanes.items()):
        lanes_note.setdefault(v["item"], []).append(f"belt {b + 1} {'east' if ln == EAST_LANE else 'west'}"
                                                   + ("" if v["kind"] == "in" else " (made here)"))
    ins = sorted({v["item"] for v in st.lanes.values() if v["kind"] == "in"})
    notes.insert(0, f"{len(st.cols)} column{'s' if len(st.cols) > 1 else ''}, {len(products) - sum(len(s) for s in missing)}"
                    f" products; in at the top: {', '.join(ins)}"
                    + (f"; made here: {', '.join(sorted({_result(data, r) for r in makers}))}" if makers else ""))
    chains = [s for s in stacks if len(s) > 1]
    if chains:
        notes.append("handed straight down: " + "; ".join(" -> ".join(s) for s in chains))
    return {"entities": ents, "sources": sources, "notes": notes, "products": [r for s in order if s not in missing
                                                                                 for r in s],
            "makers": makers, "inputs": ins, "lanes": lanes_note, "columns": len(st.cols)}
