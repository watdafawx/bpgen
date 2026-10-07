"""Mall mode: one blueprint making many products at a trickle into chests, fed from a shared bus.

Layout (y grows downward, the bus flows east):
    chests A            products (with a stack limit)
    output inserters A  machine -> chest
    machine row A
    input inserters A   near inserters take from bus 1, long-handed from bus 2; makers of shared
                        ingredients drop onto the bus from here instead
    BUS 1
    BUS 2
    input inserters B   near: bus 2, long-handed: bus 1
    machine row B
    output inserters B
    chests B
Inserters drop onto a belt's far lane, so where a maker sits decides the lane its item lands on:
    row A near -> bus 1 south lane, row A long -> bus 2 south lane,
    row B near -> bus 2 north lane, row B long -> bus 1 north lane.
Raw materials enter their lanes at the west end. Makers sit west of (upstream of) everything that uses their item.

Direct insertion: a product whose only user is another product skips the bus. The two machines sit side by side in
one row; the first fills its own chest (its last column), an inserter in the chest row moves items on to a buffer
chest over the second machine, and that one takes them from there:
    chest A1 -> [inserter] -> buffer          (chest row)
    out A1                    in B1   out B1  (output row)
    machine A1                machine B1
"""
import itertools
import math
from dataclasses import dataclass, field

from bpgen import planner
from bpgen.calibrate import ZERO, level_key, template_inserters
from bpgen.planner import NORTH, EAST, SOUTH, PlanError, pick_inserter

# lane = (bus index 0/1, side "N"/"S"); maker placement that drops onto it: (row, reach)
LANES = [(0, "S"), (1, "N"), (0, "N"), (1, "S")]
DROP = {(0, "S"): ("a", 1), (1, "S"): ("a", 2), (1, "N"): ("b", 1), (0, "N"): ("b", 2)}
MAX_MAKERS = 12
SHARE = 0.25  # share of the consumers' full speed a shared ingredient's makers are sized for (malls idle a lot)


@dataclass
class MallMachine:
    recipe: str
    role: str  # "product" (-> chest) | "maker" (-> bus lane)
    item: str  # what it makes
    row: str = ""
    col: int = 0
    near: object = None
    far: object = None
    out: object = None
    drop_reach: int = 0
    feeds: str = None  # recipe of the product next to it that takes this one's item by direct insertion
    fed_by: str = None  # recipe of the product next to it that hands it an ingredient


@dataclass
class Mall:
    products: list
    machine: str
    belt: str
    lanes: dict  # item -> (bus, side)
    machines: list
    raw: list
    makers: dict  # item -> number of maker machines
    entities: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    chest: str = "iron-chest"


def _amount(e):
    return planner._amount(e)


def _only_result(data, recipe):
    rs = data.raw["recipe"][recipe].get("results") or []
    return rs[0]["name"] if len(rs) == 1 else None


def direct_links(data, products, size):
    """{item: (maker recipe, user recipe)} for products whose only user among the products is one other product:
    those two sit side by side and pass the item chest to chest instead of over the bus (needs 3-wide machines)"""
    if size < 3:
        return {}
    recipes = data.raw["recipe"]
    made_by = {_only_result(data, r): r for r in products}
    users = {}
    for r in products:
        for ing in recipes[r].get("ingredients", []):
            if ing["name"] in made_by and made_by[ing["name"]] != r:
                users.setdefault(ing["name"], []).append(r)
    links, taken = {}, set()
    for item, us in users.items():
        maker = made_by[item]
        if len(us) == 1 and maker not in taken and us[0] not in taken:  # each machine has one neighbour to link
            links[item] = (maker, us[0])
            taken |= {maker, us[0]}
    return links


def plan_mall(data, calib, products, machine, belt, shared=None, bonuses=None, allowed=None, chest="iron-chest",
              chest_limit=4, links=None, exports=None):
    """products: recipe names (one machine + chest each). shared: {item: recipe} made in the mall and put on the bus
    (default: any ingredient another listed product makes)."""
    if not products:
        raise PlanError("pick at least one product")
    m = data.raw["assembling-machine"].get(machine)
    if not m:
        raise PlanError(f"{machine} is not an assembling machine")
    size = planner._size(m)
    bonuses = bonuses or ZERO
    recipes = data.raw["recipe"]
    for r in products:
        if r not in recipes:
            raise PlanError(f"unknown recipe {r}")
        if recipes[r].get("category", "crafting") not in m.get("crafting_categories", []):
            raise PlanError(f"{machine} can't craft {r}")
        if any(i.get("type") == "fluid" for i in recipes[r].get("ingredients", [])):
            raise PlanError(f"{r} needs a fluid: fluids aren't in mall mode yet")
    made_by = {_only_result(data, r): r for r in products}
    if shared is None:
        shared = {}
        for r in products:
            for ing in recipes[r].get("ingredients", []):
                if ing["name"] in made_by and made_by[ing["name"]] != r:
                    shared[ing["name"]] = made_by[ing["name"]]
    links = {it: pair for it, pair in (direct_links(data, products, size) if links is None else links).items()
             if pair[0] in products and pair[1] in products}
    for item in links:
        shared.pop(item, None)  # handed over chest to chest, not on the bus
    linked_use = {(item, user) for item, (_, user) in links.items()}
    for item, r in list(shared.items()):
        if recipes[r].get("category", "crafting") not in m.get("crafting_categories", []):
            raise PlanError(f"{machine} can't craft {r} (for {item})")

    speed = m["crafting_speed"]

    def crafts(r):
        return speed / max(recipes[r].get("energy_required", 0.5), 1e-3)

    # items on the bus: shared ingredients (made here) and raw ones (brought in)
    uses = {}
    for r in list(products) + list(shared.values()):
        for ing in recipes[r].get("ingredients", []):
            if (ing["name"], r) in linked_use:
                continue
            uses.setdefault(ing["name"], []).append((r, crafts(r) * _amount(ing)))
    raw = sorted(it for it in uses if it not in shared)
    items = list(shared) + raw
    if len(items) > 4:
        raise PlanError(f"{len(items)} different items would need to be on the bus ({', '.join(items)}); "
                        f"the bus has 4 lanes - split these products into two malls")

    # makers per shared item, sized to SHARE of what its consumers would eat running flat out - but no consumer
    # can take more than one bus lane delivers (near-instant modded recipes would otherwise ask for thousands)
    lane_cap = planner.belt_capacity(data, belt) / 2
    makers = {}
    for item, r in shared.items():
        # its own users, and those in other sections it is sent to (exports: {item: [their rates]})
        demand = sum(min(rate, lane_cap) for _, rate in uses.get(item, []))
        demand += sum(min(rate, lane_cap) for rate in (exports or {}).get(item, []))
        per = crafts(r) * _amount(recipes[r]["results"][0])
        makers[item] = min(MAX_MAKERS, max(1, math.ceil(demand * SHARE / per)))

    # lane assignment: try every arrangement, keep the one needing the fewest input inserters (a machine whose
    # ingredients are all on its near bus needs one; both buses need two; makers also need a drop inserter)
    def buses(r, row, ln):
        near = 0 if row == "a" else 1
        bs = {ln[i["name"]][0] for i in recipes[r].get("ingredients", []) if (i["name"], r) not in linked_use}
        return (near in bs) + ((1 - near) in bs)

    best = None
    for perm in itertools.permutations(LANES, len(items)):
        ln = dict(zip(items, perm))
        cost = sum(makers[it] * (buses(shared[it], DROP[ln[it]][0], ln) + 1) for it in shared)
        cost += sum(min(buses(r, "a", ln), buses(r, "b", ln)) for r in products)
        if best is None or cost < best[0]:
            best = (cost, ln)
    lanes = best[1]

    # machines in dependency order: makers of an item before its users
    order = []
    placed = set()

    def need(item):
        return [ing["name"] for ing in recipes[shared[item]].get("ingredients", []) if ing["name"] in shared]

    def visit(item, stack=()):
        if item in placed:
            return
        if item in stack:
            raise PlanError(f"shared ingredients depend on each other in a loop ({item})")
        for dep in need(item):
            visit(dep, stack + (item,))
        placed.add(item)
        for _ in range(makers[item]):
            row, reach = DROP[lanes[item]]
            order.append(MallMachine(shared[item], "maker", item, row=row, drop_reach=reach))
    for item in shared:
        visit(item)
    feeds = {maker: user for maker, user in links.values()}
    fed_by = {user: maker for maker, user in links.values()}
    for r in products:
        if r in fed_by:
            continue  # placed right after the product feeding it
        order.append(MallMachine(r, "product", _only_result(data, r) or r, feeds=feeds.get(r)))
        if r in feeds:
            order.append(MallMachine(feeds[r], "product", _only_result(data, feeds[r]) or feeds[r], fed_by=r))

    # columns: a machine sits east of every maker of the shared items it uses; both rows fill left to right
    maker_col = {}
    taken = set()
    by_recipe = {}
    for i, mm in enumerate(order):
        deps = [ing["name"] for ing in recipes[mm.recipe].get("ingredients", []) if ing["name"] in shared]
        if mm.feeds:  # its partner's shared ingredients count too: the pair goes east of all their makers
            deps += [ing["name"] for ing in recipes[mm.feeds].get("ingredients", []) if ing["name"] in shared]
        start = max((maker_col[d] + 1 for d in deps if d in maker_col), default=0)
        rows = [mm.row] if mm.row else sorted(["a", "b"], key=lambda r: buses(mm.recipe, r, lanes))
        if mm.fed_by:  # right next to the product feeding it, same row
            partner = by_recipe[mm.fed_by]
            mm.row, mm.col = partner.row, partner.col + 1
            taken.add((mm.col, mm.row))
            by_recipe[mm.recipe] = mm
            continue
        # even columns keep an input slot for power: a machine needing every slot (both buses + a drop inserter)
        # has to sit in an odd column
        def fits(c, r):
            need_slots = buses(mm.recipe, r, lanes) + (1 if mm.role == "maker" else 0)
            ok = (c, r) not in taken and need_slots <= size - (1 if c % 2 == 0 else 0)
            if ok and mm.feeds:  # the next column must take its partner
                partner_slots = buses(mm.feeds, r, lanes)
                ok = (c + 1, r) not in taken and partner_slots <= size - (1 if (c + 1) % 2 == 0 else 0)
            return ok
        col = start
        while not any(fits(col, r) for r in rows):
            col += 1
        mm.row = next(r for r in rows if fits(col, r))
        mm.col = col
        taken.add((col, mm.row))
        by_recipe[mm.recipe] = mm
        if mm.role == "maker":
            maker_col[mm.item] = max(maker_col.get(mm.item, -1), col)

    # inserters
    zero_key = lambda n: level_key(data, n, ZERO)  # noqa: E731
    key = lambda n: level_key(data, n, bonuses)  # noqa: E731
    near_ins = sorted((n for n in template_inserters(data, allowed, 1) if calib.get(f"{n}|{belt}|belt_to_machine|{key(n)}")),
                      key=lambda n: calib[f"{n}|{belt}|belt_to_machine|{zero_key(n)}"])
    far_ins = sorted((n for n in template_inserters(data, allowed, 2) if calib.get(f"{n}|{belt}|belt_to_machine|{key(n)}")),
                     key=lambda n: calib[f"{n}|{belt}|belt_to_machine|{zero_key(n)}"])
    if not near_ins or not far_ins:
        raise PlanError(f"mall mode needs calibrated normal and long-handed inserters for {belt}")
    slots = size  # power poles only take slots that are left over (see _layout)
    mall_notes = []
    for mm in order:
        rate = crafts(mm.recipe)
        near_bus = 0 if mm.row == "a" else 1
        bus_ings = [i for i in recipes[mm.recipe]["ingredients"] if (i["name"], mm.recipe) not in linked_use]
        need_near = sum(rate * _amount(i) for i in bus_ings if lanes[i["name"]][0] == near_bus)
        need_far = sum(rate * _amount(i) for i in bus_ings if lanes[i["name"]][0] != near_bus)
        # even columns keep one input slot free so a power pole always fits within reach (at most 6 tiles apart)
        free = slots - (1 if mm.col % 2 == 0 else 0) - (1 if mm.role == "maker" else 0)
        slow = []

        def pick(pool, direction, need, n_slots, what):
            """a mall machine may run slower than flat out: if nothing keeps up, use the fastest that fits"""
            try:
                return pick_inserter(calib, pool, belt, direction, key, need, n_slots)
            except PlanError:
                name = max(pool, key=lambda n: calib.get(f"{n}|{belt}|{direction}|{key(n)}", 0))
                rate_one = calib.get(f"{name}|{belt}|{direction}|{key(name)}", 0)
                count = max(1, n_slots)
                slow.append(f"{what} {count * rate_one:.2f}/s of {need:.2f}/s")
                return planner.InserterChoice(name, count, round(count * rate_one, 3), round(need, 3))
        if need_near and need_far:
            mm.near = pick(near_ins, "belt_to_machine", need_near, free - 1, "near input")
            mm.far = pick(far_ins, "belt_to_machine", need_far, free - mm.near.count, "long-handed input")
        elif need_near:
            mm.near = pick(near_ins, "belt_to_machine", need_near, free, "input")
        elif need_far:
            mm.far = pick(far_ins, "belt_to_machine", need_far, free, "long-handed input")
        out_rate = rate * _amount(recipes[mm.recipe]["results"][0])
        pool = near_ins if mm.role == "product" or mm.drop_reach == 1 else far_ins
        mm.out = pick(pool, "machine_to_belt", out_rate, 1, "output")
        if slow:
            mall_notes.append(f"{mm.recipe} ({'makes for the bus' if mm.role == 'maker' else 'product'}) runs below full "
                              f"speed, inserters: " + "; ".join(slow))

    mall = Mall(list(products), machine, belt, lanes, order, raw, makers, chest=chest, notes=mall_notes)
    _layout(mall, size, chest, chest_limit)
    for item, n in makers.items():
        mall.notes.append(f"{n} machine(s) make {item} onto the bus")
    for item, (maker, user) in links.items():
        mall.notes.append(f"{item} goes from its chest straight into {user} next to it (not on the bus)")
    return mall


def _layout(mall, s, chest, chest_limit):
    cols = max(mm.col for mm in mall.machines) + 1
    width = cols * s
    Y = {}
    y = 0
    for name, h in (("chest_a", 1), ("out_a", 1), ("mach_a", s), ("ins_a", 1), ("bus0", 1), ("bus1", 1),
                    ("ins_b", 1), ("mach_b", s), ("out_b", 1), ("chest_b", 1)):
        Y[name] = y
        y += h
    ents = []

    def put(name, x, yy, direction=None, **kw):
        e = {"name": name, "position": {"x": x, "y": yy}}
        if direction is not None:
            e["direction"] = direction
        e.update(kw)
        ents.append(e)

    for x in range(width + 1):
        put(mall.belt, x + 0.5, Y["bus0"] + 0.5, EAST)
        put(mall.belt, x + 0.5, Y["bus1"] + 0.5, EAST)
    used = {}  # row name -> x tiles holding an inserter
    for mm in mall.machines:
        x0 = mm.col * s
        a = mm.row == "a"
        lane = mall.lanes.get(mm.item)
        role = (f"makes {mm.item} onto bus {lane[0] + 1} {'north' if lane[1] == 'N' else 'south'} lane"
                if mm.role == "maker" and lane else f"makes {mm.item} into its chest")
        put(mall.machine, x0 + s / 2, Y["mach_" + mm.row] + s / 2, NORTH, recipe=mm.recipe, role=role)
        # input row: pick from the bus side (facing it); makers' drop inserter faces the machine
        ins = "ins_" + mm.row
        to_bus, to_machine = (SOUTH, NORTH) if a else (NORTH, SOUTH)
        slots = list(range(s))
        placed = []
        for choice in (mm.near, mm.far):
            if choice:
                for _ in range(choice.count):
                    placed.append((choice.name, to_bus))
        if mm.role == "maker":
            placed.append((mm.out.name, to_machine))
        for name, direction in placed:
            j = slots.pop(0)
            put(name, x0 + j + 0.5, Y[ins] + 0.5, direction)
            used.setdefault(ins, set()).add(x0 + j)
        if mm.role == "product":
            # output row: machine -> chest (picks from the machine side). A machine feeding its neighbour keeps
            # its chest in its last column, next to the hand-over; a fed machine keeps its first two columns for it
            out = "out_" + mm.row
            ox = x0 + (s - 1 if mm.feeds or mm.fed_by else 0)
            # the machine is on the bus side of the output row: face it (same way the input row faces the bus)
            put(mm.out.name, ox + 0.5, Y[out] + 0.5, to_bus)
            used.setdefault(out, set()).add(ox)
            put(chest, ox + 0.5, Y["chest_" + mm.row] + 0.5, bar=chest_limit, holds=mm.item,
                role=f"holds {mm.item} (limit {chest_limit} stacks)")
            used.setdefault("chest_" + mm.row, set()).add(ox)  # no pole on a chest
            if mm.fed_by:
                # chest row: neighbour's chest (x0 - 1) -> inserter (x0) -> buffer (x0 + 1); output row: buffer ->
                # this machine. Inserters face where they pick up.
                west = 12
                ins_name = mm.out.name
                put(ins_name, x0 + 0.5, Y["chest_" + mm.row] + 0.5, west, role="hands items to the neighbour")
                used.setdefault("chest_" + mm.row, set()).update({x0, x0 + 1, x0 - 1, ox})
                put(chest, x0 + 1.5, Y["chest_" + mm.row] + 0.5, bar=1, role="buffer for the machine below")
                put(ins_name, x0 + 1.5, Y[out] + 0.5, to_machine)
                used[out].add(x0 + 1)
    # power: per inserter row, a medium pole (supply reaches 3 tiles each way) wherever an inserter isn't covered
    # yet, on a free tile as far right as still covers it; machines sit next to these rows and get covered too
    for row, xs in used.items():
        last = None
        need = sorted(xs)
        if row.startswith("chest_"):  # chest rows only power their hand-over inserters
            need = sorted(x for x in xs if any(e["position"]["x"] == x + 0.5 and e["position"]["y"] == Y[row] + 0.5
                                                and e["name"] != chest for e in ents))
        for x in need:
            if last is not None and x - last <= 3:
                continue
            free = [t for t in range(x + 3, x - 4, -1) if 0 <= t < width and t not in xs]
            if not free:
                raise PlanError("no room for a power pole next to the inserters")
            last = free[0]
            put(planner.POLE, last + 0.5, Y[row] + 0.5)
            xs = xs | {last}
    mall.entities = ents
    mall.bus_y = (Y["bus0"], Y["bus1"])
    mall.width = width
    for bus in (0, 1):
        lanes = [None, None]
        for item, (b, side) in mall.lanes.items():
            if b == bus and item in mall.raw:
                lanes[0 if side == "N" else 1] = item
        if any(lanes):
            rates = {}
            mall.sources.append({"kind": "belt", "position": {"x": 0.5, "y": Y[f"bus{bus}"] + 0.5}, "lanes": lanes,
                                 "rates": rates})


def describe(mall):
    lane_txt = ", ".join(f"{it} on bus {b + 1} {'north' if side == 'N' else 'south'} lane"
                         f"{' (made here)' if it in mall.makers else ''}" for it, (b, side) in mall.lanes.items())
    lines = [f"MALL: {len(mall.products)} products, {len(mall.machines)} x {mall.machine}, {mall.belt} bus",
             f"  products: {', '.join(mall.products)}",
             f"  bus: {lane_txt}",
             f"  bring in: {', '.join(mall.raw)}"]
    lines += ["  note: " + n for n in mall.notes]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------
# auto-split: several malls stacked in one blueprint, each with its own 4-lane bus

def bus_items(data, products, shared, links=None):
    """what a mall of these products would need on its bus, given shared {item: recipe} made in it (an item a
    product hands to its neighbour by direct insertion isn't on the bus)"""
    recipes = data.raw["recipe"]
    linked = {(it, user) for it, (maker, user) in (links or {}).items() if maker in products and user in products}
    made, need = {}, set()
    todo = list(products)
    while todo:
        r = todo.pop()
        for ing in recipes[r].get("ingredients", []):
            if (ing["name"], r) in linked:
                continue
            need.add(ing["name"])
            if ing["name"] in shared and ing["name"] not in made:
                made[ing["name"]] = shared[ing["name"]]
                todo.append(shared[ing["name"]])
    return need, made


def split_products(data, products, shared, links=None):
    """greedy grouping so each group's bus has at most 4 items; a group either makes the shared ingredients it uses
    or (when that can't fit) brings everything in; directly linked pairs stay together.
    -> ([(products, shared made in that group)], notes)"""
    links = links or {}
    linked_items = set(links)
    shared = {k: v for k, v in shared.items() if k not in linked_items}

    def bus(prods, sh):
        return bus_items(data, prods, sh, links)

    def fits(prods, sh):
        return len(bus(prods, sh)[0]) <= len(LANES)
    feeds = {maker: user for maker, user in links.values()}
    fed = set(feeds.values())
    units = [[p] + ([feeds[p]] if p in feeds else []) for p in products if p not in fed]
    groups = []  # [products, makes_shared]
    notes = []
    order = sorted(units, key=lambda u: -len(bus(u, shared)[0]))
    for unit in order:
        # best fit: first don't give up making p's shared ingredients if p could make them in a group of its own,
        # then grow a bus as little as possible (opening a new group is a candidate too)
        uses_shared = bool(bus(unit, shared)[1])
        can_make = uses_shared and fits(unit, shared)
        best = None
        for g in groups:
            sh = shared if g[1] else {}
            if fits(g[0] + unit, sh):
                grow = len(bus(g[0] + unit, sh)[0]) - len(bus(g[0], sh)[0])
                key = (can_make and not g[1], grow)
                if best is None or key < best[0]:
                    best = (key, g)
        if best and not best[0][0]:  # joining doesn't cost p its in-house making
            best[1][0].extend(unit)
        else:
            if fits(unit, shared):
                groups.append([list(unit), True])
            elif fits(unit, {}):
                groups.append([list(unit), False])
            else:
                raise PlanError(f"{', '.join(unit)} alone needs {len(bus(unit, {})[0])} different ingredients on the bus; "
                                f"a mall bus has 4 lanes")
    out = []
    for prods, makes in groups:
        made = bus(prods, shared)[1] if makes else {}
        skipped = sorted({it for it in shared if it in bus(prods, {})[0]} - set(made))
        if skipped:
            notes.append(f"{', '.join(prods)}: {', '.join(skipped)} brought in rather than made, to fit a 4-lane bus")
        out.append((prods, made))
    return out, notes


def plan_mall_auto(data, calib, products, machine, belt, shared=None, **kw):
    """one mall if everything fits on a 4-lane bus, otherwise several stacked malls; -> (malls, entities, sources, notes)"""
    recipes = data.raw["recipe"]
    if shared is None:  # default: anything another listed product makes
        made_by = {_only_result(data, r): r for r in products}
        shared = {}
        for r in products:
            for ing in recipes[r].get("ingredients", []):
                if ing["name"] in made_by and made_by[ing["name"]] != r:
                    shared[ing["name"]] = made_by[ing["name"]]
    m_proto = data.raw["assembling-machine"].get(machine)
    links = direct_links(data, list(products), planner._size(m_proto)) if m_proto else {}
    links = {it: pair for it, pair in links.items() if it in shared  # "brought in" items stay brought in
             and len(bus_items(data, list(pair), {}, {it: pair})[0]) <= len(LANES)}  # a pair must fit one bus
    groups, notes = split_products(data, list(products), shared, links)
    # what other sections will take of each shared item, for sizing its makers
    speed = (m_proto or {}).get("crafting_speed", 1)
    exports = {}
    for prods, made in groups:
        for r in prods:
            for ing in recipes[r].get("ingredients", []):
                if ing["name"] in shared and ing["name"] not in made and (ing["name"], r) not in                         {(it, u) for it, (_, u) in links.items()}:
                    rate = speed / max(recipes[r].get("energy_required", 0.5), 1e-3) * _amount(ing)
                    exports.setdefault(ing["name"], []).append(rate)
    malls, ents, sources, offsets = [], [], [], []
    y = 0
    for prods, made in groups:
        m = plan_mall(data, calib, prods, machine, belt, shared=made, links=links,
                      exports={it: v for it, v in exports.items() if it in made}, **kw)
        offsets.append(y)
        height = math.ceil(max(e["position"]["y"] for e in m.entities)) + 1
        shift = lambda items: [dict(i, position={"x": i["position"]["x"], "y": i["position"]["y"] + y}) for i in items]  # noqa: E731
        ents += shift(m.entities)
        sources += shift(m.sources)
        malls.append(m)
        y += height + 3
    if len(malls) > 1:
        notes.insert(0, f"split into {len(malls)} malls stacked top to bottom, each with its own 4-lane bus")
        ents, sources = _chain(data, belt, malls, offsets, ents, sources, notes)
        ents += _link_power(data, ents)
    return malls, ents, sources, notes


def _chain(data, belt, malls, offsets, ents, sources, notes):
    """a section bringing in an item another section makes onto its bus gets it from there: a filter splitter at
    the end of the maker's bus sends the item off on its own belt, routed to the user's west end and side-loaded
    onto the lane it expects (the bus comes in through a bend, so both lanes have a free side)"""
    from bpgen import chain as chainmod, router
    size_of = chainmod._sizer(data)
    blocked = set()
    for e in ents:
        blocked |= chainmod._tiles(e, size_of)
    ug, ug_max = chainmod.related_belts(data, belt)
    splitter = chainmod.related_splitter(data, belt)
    # who needs what from whom
    wants = {}  # (maker section, item) -> [(user section, bus, side)]
    for j, m in enumerate(malls):
        for item in m.raw:
            i = next((i for i, o in enumerate(malls) if i != j and item in o.makers), None)
            if i is not None:
                b, side = m.lanes[item]
                wants.setdefault((i, item), []).append((j, b, side))
    if not wants:
        return ents, sources
    new = []

    def add(name, x, y, d=None, **kw):
        e = {"name": name, "position": {"x": x, "y": y}}
        if d is not None:
            e["direction"] = d
        e.update(kw)
        new.append(e)
        blocked.update(chainmod._tiles(e, size_of))
        return e

    # 1) every user bus that gets a chained item comes in through a bend, with side-load points for both lanes
    #    bus 0: down a column at x = -3, east into the bus; bus 1: up a column, east into the bus
    goals = {}  # (user section, item) -> (goal tile, goal direction)
    rebuilt = set()
    for (i, item), users in wants.items():
        for j, b, side in users:
            if (j, b) in rebuilt:
                continue
            rebuilt.add((j, b))
            y = offsets[j] + malls[j].bus_y[b]
            v = -1 if b == 0 else 1  # the column runs away from the other bus
            col_d = SOUTH if b == 0 else NORTH
            add(belt, -0.5, y + 0.5, EAST)
            add(belt, -1.5, y + 0.5, EAST)
            for k in range(0, 4):
                add(belt, -2.5, y + 0.5 + v * k, col_d if k else EAST)
            # inner lane (towards the other bus) fills from the column's west side; the outer one from the
            # bend's outer side
            inner = "S" if b == 0 else "N"
            for it2, (b2, side2) in malls[j].lanes.items():
                if b2 != b:
                    continue
                if side2 == inner:
                    goals[(j, it2)] = ((-3, y + v), EAST)
                else:
                    goals[(j, it2)] = ((-2, y), col_d)
            # the hand-fed lanes now come in at the top of the column
            hand = [None, None]
            for it2, (b2, side2) in malls[j].lanes.items():
                if b2 == b and it2 in malls[j].raw and not any(it2 == it and (j, b, side2) in us
                                                                for (_, it), us in wants.items()):
                    hand[0 if side2 == "N" else 1] = it2
            sources[:] = [src for src in sources if not (abs(src["position"]["y"] - (y + 0.5)) < 0.01
                                                          and src["position"]["x"] < 1)]
            if any(hand):
                sources.append({"kind": "belt", "position": {"x": -2.5, "y": y + 0.5 + v * 3}, "lanes": hand,
                                "rates": {}, "direction": col_d})
                blocked.update((-3, y + v * k) for k in range(4, 9))  # where your belt comes in: keep it clear
    # 2) maker side: filter splitter at the end of its bus, a plain splitter when two sections take it
    xs = [e["position"]["x"] for e in ents]
    ys = [e["position"]["y"] for e in ents]
    bounds = (math.floor(min(xs)) - 12, math.floor(min(ys)) - 6, math.ceil(max(xs)) + 12, math.ceil(max(ys)) + 6)
    spans = set()
    # every route's last tile (where it side-loads) is kept free of the other routes
    vec = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), 12: (-1, 0)}
    ends = {key: (g[0] - vec[d][0], g[1] - vec[d][1]) for key, (g, d) in goals.items()}
    blocked.update(ends.values())
    for (i, item), users in wants.items():
        m = malls[i]
        b = m.lanes[item][0]
        y = offsets[i] + m.bus_y[b]
        out = -1 if b == 0 else 1
        w = m.width
        add(belt, w + 1.5, y + 0.5, EAST)
        # a splitter facing east covers two tiles stacked north-south: its centre is on the line between them
        add(splitter, w + 2.5, y + (1 if out > 0 else 0),
            EAST, filter={"name": item, "quality": "normal", "comparator": "="},
            output_priority="left" if out < 0 else "right")
        starts = [(w + 3, y + out)]
        if len(users) > 1:
            add(splitter, w + 3.5, y + out + (1 if out > 0 else 0), EAST)
            starts = [(w + 4, y + out), (w + 4, y + 2 * out)]
        # upper exit to the higher section, so the two belts don't cross on the way
        users = sorted(users, key=lambda u: goals[(u[0], item)][0][1])
        starts = sorted(starts, key=lambda t: t[1])
        for (j, b2, side2), start in zip(users, starts):
            goal, gd = goals[(j, item)]
            blocked.discard(ends[(j, item)])
            try:
                path = router.route(blocked, [(start, [EAST])], goal, gd, bounds, ug_max, underground_spans=spans)
            except router.RouteError:
                notes.append(f"section {j + 1}: couldn't run {item} over from section {i + 1}; bring it in by hand")
                continue
            for tile, kind, d in path:
                e = add(belt if kind == "belt" else ug, tile[0] + 0.5, tile[1] + 0.5, d)
                if kind != "belt":
                    e["ug_type"] = "input" if kind == "ug-in" else "output"
            router.reserve(path, blocked, spans)
            notes.append(f"section {j + 1} gets {item} from section {i + 1}")
        if len(users) > 2:
            notes.append(f"{item}: only two sections can take it from section {i + 1}; the rest bring it in")
    return ents + new, sources


def _link_power(data, ents):
    """poles joining separate pole networks (stacked sections): each network to its nearest neighbour network"""
    from bpgen.chain import _pole_line, _sizer, _tiles
    size_of = _sizer(data)
    blocked = set()
    for e in ents:
        blocked |= _tiles(e, size_of)
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
        for x, y in _pole_line(pa, pb, blocked):
            added.append({"name": planner.POLE, "position": {"x": x, "y": y}})
        nets[j] = nets[j] | a_net
    return added


def describe_all(malls, notes):
    if len(malls) == 1:
        return describe(malls[0]) + "".join("\n  note: " + n for n in notes)
    parts = [f"MALLS: {len(malls)} sections, {sum(len(m.products) for m in malls)} products"]
    parts += ["  note: " + n for n in notes]
    for i, m in enumerate(malls, 1):
        parts.append(f"section {i}: " + describe(m).replace("\n", "\n  "))
    return "\n".join(parts)


# ---------------------------------------------------------------------------------------------------------------
# robot-fed mall: no bus. Each product is a cell: requester chest -> inserter -> machine -> inserter -> passive
# provider chest (stack limited). Two rows of machines back to back, chests on the outside:
#     R . P  R . P        requester, pole, provider
#     i o o  i o o        input inserter, pole, output inserter (o = the output column)
#     machine row A
#     machine row B
#     i o o  ...
#     R . P  ...

def logistic_chest(data, mode, unlocked=None):
    """a 1x1 logistic chest of this mode (requester, passive-provider...), one the player has if known"""
    out = []
    for n, p in data.raw.get("logistic-container", {}).items():
        if p.get("logistic_mode") != mode or p.get("hidden"):
            continue
        (x1, y1), (x2, y2) = p.get("collision_box") or [[-0.4, -0.4], [0.4, 0.4]]
        if x2 - x1 > 1 or y2 - y1 > 1:
            continue
        out.append(n)
    if unlocked:
        mine = [n for n in out if n in unlocked]
        out = mine or out
    # the plainest one: the shortest name is the base game's (modded variants add prefixes)
    return min(out, key=lambda n: (len(n), n)) if out else None


def plan_bot_mall(data, products, machine, inserter="inserter", chest_limit=4, buffer_crafts=5, unlocked=None):
    """-> (entities, notes) for a robot-fed mall of these recipes"""
    recipes = data.raw["recipe"]
    m = data.raw["assembling-machine"].get(machine)
    if not m:
        raise PlanError(f"{machine} is not an assembling machine")
    requester = logistic_chest(data, "requester", unlocked)
    provider = logistic_chest(data, "passive-provider", unlocked)
    if not requester or not provider:
        raise PlanError("this pack has no 1x1 requester or passive provider chest")
    s = planner._size(m)
    w = max(s, 3)  # a cell needs 3 tiles: requester, pole, provider
    notes, cells = [], []
    for r in products:
        rec = recipes.get(r)
        if not rec:
            raise PlanError(f"unknown recipe {r}")
        if rec.get("category", "crafting") not in m.get("crafting_categories", []):
            notes.append(f"{r}: {machine} can't craft it, left out")
            continue
        if any(i.get("type") == "fluid" for i in rec.get("ingredients", [])):
            notes.append(f"{r}: needs a fluid, left out (robots can't carry fluids)")
            continue
        cells.append(r)
    if not cells:
        raise PlanError("none of these products can be made here")
    ents = []

    def put(name, x, y, direction=None, **kw):
        e = {"name": name, "position": {"x": x, "y": y}}
        if direction is not None:
            e["direction"] = direction
        e.update(kw)
        ents.append(e)

    top_ins, mach_a, mach_b = 1, 2, 2 + s
    bot_ins, bot_chest = 2 + 2 * s, 3 + 2 * s
    for k, r in enumerate(cells):
        col, row_b = k // 2, k % 2 == 1
        x0 = col * w
        item = _only_result(data, r) or r
        my = (mach_b if row_b else mach_a) + s / 2
        put(machine, x0 + s / 2, my, NORTH, recipe=r, role=f"makes {item}")
        ins_y = bot_ins if row_b else top_ins
        chest_y = bot_chest if row_b else 0
        # inserters face where they pick up: the input one faces its requester, the output one the machine
        to_chest, to_machine = (SOUTH, NORTH) if row_b else (NORTH, SOUTH)
        put(inserter, x0 + 0.5, ins_y + 0.5, to_chest)
        put(inserter, x0 + w - 0.5, ins_y + 0.5, to_machine)
        filters = [{"index": i + 1, "name": ing["name"], "quality": "normal", "comparator": "=",
                    "count": max(1, math.ceil(_amount(ing) * buffer_crafts))}
                   for i, ing in enumerate(recipes[r].get("ingredients", []))]
        put(requester, x0 + 0.5, chest_y + 0.5, request_filters={"sections": [{"index": 1, "filters": filters}]},
            role=f"requests {', '.join(f['name'] for f in filters)}")
        put(provider, x0 + w - 0.5, chest_y + 0.5, bar=chest_limit, holds=item,
            role=f"holds {item} (limit {chest_limit} stacks)")
        put(planner.POLE, x0 + 1.5, ins_y + 0.5)  # each cell's pole: its inserters and machine
    notes.append(f"{len(cells)} products, fed by robots: build it inside your logistic network (roboport coverage)")
    return ents, notes


# ---------------------------------------------------------------------------------------------------------------
# robot-fed production line: one recipe, as many machines as the rate needs, in one row. Per machine:
#     R . R        requester chests (one per input inserter)
#     i p i        input inserters (1-2), pole
#     machine
#     o p o        output inserters (1-2), pole
#     P . P        passive provider chests (stack limited)

def chest_rate(data, inserter, bonuses=None):
    """items/s an inserter moves chest to machine (or back): a full swing there and back per hand"""
    p = data.raw["inserter"][inserter]
    bonuses = bonuses or ZERO
    hand = 1 + (p.get("stack_size_bonus") or (1 if p.get("bulk") else 0))
    hand += bonuses.get("bulk_inserter_capacity_bonus", 0) if p.get("bulk") else bonuses.get("inserter_stack_size_bonus", 0)
    return hand * p.get("rotation_speed", 0.014) * 60 * 0.9  # (extension and pickup cost a little)


def plan_bot_line(data, recipe, machine, rate, inserters, bonuses=None, unlocked=None, chest_limit=4,
                  buffer_s=30, speed=0.0, productivity=0.0):
    """-> (entities, notes, info) for `rate` items/s of the recipe's product made from robot-delivered ingredients.
    inserters: the allowed types, cheapest first; the cheapest that keeps up (1-2 per side) is used."""
    recipes = data.raw["recipe"]
    r = recipes.get(recipe)
    m = data.raw.get("assembling-machine", {}).get(machine)
    if not r or not m:
        raise PlanError(f"{machine} can't be robot-fed here (assembling machines only)")
    if r.get("category", "crafting") not in m.get("crafting_categories", []):
        raise PlanError(f"{machine} can't craft {recipe}")
    if any(i.get("type") == "fluid" for i in r.get("ingredients", []) + (r.get("results") or [])):
        raise PlanError(f"{recipe} uses a fluid: robots can't carry fluids")
    item = _only_result(data, recipe)
    if not item:
        raise PlanError(f"{recipe} has several products: not supported robot-fed yet")
    s = planner._size(m)
    if s < 3:
        raise PlanError(f"{machine} is smaller than 3x3: no room for chests, inserters and a pole on a side")
    requester = logistic_chest(data, "requester", unlocked)
    provider = logistic_chest(data, "passive-provider", unlocked)
    if not requester or not provider:
        raise PlanError("this pack has no 1x1 requester or passive provider chest")
    craft = m["crafting_speed"] * (1 + speed) / r.get("energy_required", 0.5)  # crafts/s per machine
    made = craft * _amount(r["results"][0]) * (1 + productivity)
    n = max(1, math.ceil(rate / made - 1e-9))
    use = rate / (n * made)  # (the last machines idle a little)
    need_in = craft * use * sum(_amount(i) for i in r.get("ingredients", []))
    need_out = made * use
    def reach(name):
        pos = data.raw["inserter"][name].get("pickup_position") or [0, -1]
        return abs(pos[1] if isinstance(pos, list) else pos.get("y", -1))
    order = sorted((n for n in inserters if n in data.raw["inserter"] and reach(n) < 1.6),
                   key=lambda n: chest_rate(data, n, bonuses))  # slowest first: the cheapest that keeps up
    pref = planner.INSERTER_PREF.get()
    if pref and pref[0] == "best":  # the fastest plain one first, bulk only if nothing else does
        order = sorted(order, key=lambda n: (n in pref[1], -chest_rate(data, n, bonuses)))
    pick = None
    for name in order:  # moving what one machine needs with at most two per side
        per = chest_rate(data, name, bonuses)
        k_in, k_out = math.ceil(need_in / per - 1e-9), math.ceil(need_out / per - 1e-9)
        if max(k_in, k_out) <= 2:
            pick = (name, per, max(1, k_in), max(1, k_out))
            break
    if not pick:
        raise PlanError(f"no allowed inserter moves {need_in:.1f}/s into one {machine} with two inserters")
    ins, per, k_in, k_out = pick
    ents = []

    def put(name, x, y, direction=None, **kw):
        e = {"name": name, "position": {"x": x, "y": y}}
        if direction is not None:
            e["direction"] = direction
        e.update(kw)
        ents.append(e)

    filters = [{"index": i + 1, "name": ing["name"], "quality": "normal", "comparator": "=",
                "count": max(1, math.ceil(_amount(ing) * craft * buffer_s / k_in))}
               for i, ing in enumerate(r.get("ingredients", []))]
    top, mach, bot = 1, 2, 2 + s
    for k in range(n):
        x0 = k * s
        put(machine, x0 + s / 2, mach + s / 2, NORTH, recipe=recipe, role=f"makes {item}")
        for j, x in enumerate((x0 + 0.5, x0 + s - 0.5)):
            if j < k_in:  # inserters face where they pick up
                put(ins, x, top + 0.5, NORTH)
                put(requester, x, 0.5, request_filters={"sections": [{"index": 1, "filters": filters}]},
                    role=f"requests {', '.join(f['name'] for f in filters)}")
            if j < k_out:
                put(ins, x, bot + 0.5, NORTH)
                put(provider, x, bot + 1.5, bar=chest_limit, holds=item, role=f"holds {item} (limit {chest_limit} stacks)")
        put(planner.POLE, x0 + 1.5, top + 0.5)
        put(planner.POLE, x0 + 1.5, bot + 0.5)
    notes = [f"{n} x {machine}, fed by robots: build it inside your logistic network (roboport coverage)",
             f"{k_in} {ins} in and {k_out} out per machine (about {per:.1f}/s each; one machine takes {need_in:.1f}/s)",
             f"requesters hold about {buffer_s}s of ingredients; providers stop at {chest_limit} stacks"]
    if use < 0.9:
        notes.append(f"machines run at {use:.0%} of full speed")
    return ents, notes, {"machines": n, "inserter": ins, "per_machine": made, "use": use,
                         "inputs": {i["name"]: craft * use * n * _amount(i) for i in r.get("ingredients", [])}}
