"""Plan a full-output-belt production line for one recipe and lay it out.

Layout (y grows downward, everything flows east), mirrored around the output belt:
    pipe line            (only with a fluid ingredient)
    tap row              underground pipes down to each machine's fluid input, power poles
    far input belt       (only with 3-4 ingredients, reached by long-handed inserters)
    near input belt
    input inserters      near + long-handed inserters, fluid entry in the machine's input column
    machine row A
    output inserters A   -> drop on the far (south) lane
    OUTPUT BELT
    output inserters B   -> drop on the far (north) lane
    machine row B
    ... input side mirrored
An inserter only fills the far lane of a belt, so one machine row per output lane.
"""
import base64
import contextvars
import itertools
import json
import math
import zlib
from dataclasses import dataclass, field

from bpgen.calibrate import ZERO, level_key, template_inserters
from bpgen.effects import BEACON_MODULES, MACHINE_MODULES, EffectError, Modules, machine_effects, quality_level

NORTH, EAST, SOUTH, WEST = 0, 4, 8, 12
POLE = "medium-electric-pole"  # supply 7x7, wire reach 9
POLE_EVERY = 2  # machines
PIPE, UNDERGROUND = "pipe", "pipe-to-ground"
MARGIN = 0.95  # use at most this share of a measured inserter rate
MAX_PER_ROW = 40  # a full belt of a slow recipe can need hundreds of machines
# Inserters can't pack a lane perfectly (they drop into whatever gap passes), so a row sized to exactly
# one lane leaves the belt a few % short. Measured: copper cable 92% at exact size, 100% with one extra.
OUTPUT_SLACK = 1.05
THROTTLE_DRAIN = 0.85  # a belt-limited machine whose inserters have no headroom drains ~85% of its lanes
# How full the output lanes get when output inserters must drop several items per swing (measured with
# speed-moduled gears/cable, 3 machines per row: full hands 94.6-96.1%, stack inserters 91.7%).
PACKING_FULL_HAND, PACKING_STACK = 0.95, 0.92
# Stack inserters drop stacks; without belt-stacking research that measured worse on output belts,
# so output rows skip anything named *stack-inserter* (covers modded variants).


class PlanError(Exception):
    pass


EPS = 1e-9  # effect sums like 2.0000000000000004 must not cost a machine in floor()/ceil()


def _floor(x):
    return math.floor(x + EPS)


def _ceil(x):
    return math.ceil(x - EPS)


@dataclass
class InserterChoice:
    name: str
    count: int  # per machine side
    capacity: float  # items/s for `count` inserters
    need: float  # items/s per machine
    hand: int = 0  # >0: blueprint caps the hand size (inserter stack size override)


@dataclass
class Plan:
    recipe: str
    machine: str
    belt: str
    size: int  # machine width along the row (and the side of a square machine)
    per_row: int
    craft_rate: float  # crafts/s per machine
    output: str
    output_per_machine: float
    belt_capacity: float  # items/s, both lanes
    lane_capacity: float
    belts: list  # input belts per row, nearest first; each [left lane item, right lane item] (None = empty)
    input_need: dict  # item -> items/s per row
    ins_near: InserterChoice
    ins_far: InserterChoice | None
    ins_out: InserterChoice
    fluid: str | None = None
    fluid_need: float = 0.0  # per row
    fluid_col: int | None = None  # machine column (row A) holding the fluid input
    modules: Modules = field(default_factory=Modules)
    beacon: str | None = None  # one beacon between neighbouring machines (and at both ends)
    beacon_modules: Modules = field(default_factory=Modules)
    beacon_size: int = 0
    beacons_per_machine: int = 0  # from the beacon's reach (vanilla 2)
    beacon_pad: int = 0  # extra beacons past each end so end machines get as many as the middle ones
    speed: float = 1.0  # crafting speed multiplier from quality, modules, beacons
    productivity: float = 0.0
    packing: float = 1.0  # share of the output belt the output inserters manage to fill
    furnace: bool = False  # picks its recipe from its input: the blueprint doesn't set one
    fuel: str | None = None  # burner machines: fuel fed alongside the ingredients
    fuel_rate: float = 0.0  # per machine
    notes: list = field(default_factory=list)
    poles_out: bool = False  # small machines with 2 input belts: poles in both output rows, input slots all free
    height: int = 0  # machine depth across the row when it isn't square (0: same as size)
    rotated: bool = False  # placed facing east/west (a narrow machine turned so its long side faces the belts)

    @property
    def machines(self):
        return 2 * self.per_row

    @property
    def expected_output(self):
        return min(self.belt_capacity * self.packing, self.machines * self.output_per_machine)


def _amount(entry):
    if "amount" in entry:
        return entry["amount"]
    return (entry.get("amount_min", 0) + entry.get("amount_max", 0)) / 2 * entry.get("probability", 1)


def energy(text):
    """'90kW' / '4MJ' -> watts / joules"""
    text = str(text).strip()
    num = text.rstrip("WJwj")
    mult = {"k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}.get(num[-1:], 1)
    return float(num[:-1] if num[-1:] in "kMGT" else num) * mult


def fuel_for(data, energy_source, fuel=None):
    """a fuel item this burner accepts: the requested one, else one mined on the start planet (the most energy
    first: coal in vanilla), else the first matching item"""
    from bpgen.base import raw_items
    cats = set(energy_source.get("fuel_categories") or [energy_source.get("fuel_category", "chemical")])
    items = data.raw.get("item", {})
    mined = sorted((n for n in raw_items(data) if n in items and items[n].get("fuel_value")),
                   key=lambda n: -energy(items[n]["fuel_value"]))
    for name in ([fuel] if fuel else []) + mined + sorted(items):
        it = items.get(name)
        if it and it.get("fuel_value") and it.get("fuel_category", "chemical") in cats:
            return name, energy(it["fuel_value"])
    raise PlanError("no fuel item found for this burner")


def _dims(proto):
    """(width, height) in tiles, placed facing north"""
    if "tile_width" in proto:
        return proto["tile_width"], proto.get("tile_height", proto["tile_width"])
    (x1, y1), (x2, y2) = proto["collision_box"]
    return math.ceil(x2 - x1), math.ceil(y2 - y1)


def _size(proto):
    """the side of a square entity (beacons, and layouts that need square machines)"""
    w, h = _dims(proto)
    if w != h:
        raise PlanError(f"{proto['name']}: this layout needs a square machine ({w}x{h})")
    return w


def belt_capacity(data, belt, stack=1):
    return data.raw["transport-belt"][belt]["speed"] * 480 * stack


def fluid_input_column(machine, size, height=None):
    """column (0 = west) of a fluid input on the machine's north edge, facing north, when placed facing north"""
    height = height or size
    for fb in machine.get("fluid_boxes", []):
        if fb.get("production_type") not in ("input", "input-output"):
            continue
        for pc in fb.get("pipe_connections", []):
            if pc.get("connection_type", "normal") != "normal" or pc.get("direction", NORTH) != NORTH:
                continue
            x, y = pc["position"] if isinstance(pc["position"], list) else (pc["position"]["x"], pc["position"]["y"])
            col = x + (size - 1) / 2
            if y == -(height - 1) / 2 and col == int(col):
                return int(col)
    raise PlanError(f"{machine['name']} has no north-facing fluid input this template can feed")


def _half(proto):
    (x1, _), (x2, _) = proto["collision_box"]
    return (x2 - x1) / 2


def beacon_reach(machine, beacon, size, bsize):
    """(beacons reaching a machine, extra beacons needed past each end) for the B M B M ... B layout.

    A beacon affects a machine when its collision box grown by supply_area_distance overlaps the machine's
    collision box. Sideways the beacons sit at 0.5, 1.5, 2.5... pitches from a machine; across the output belt
    the other row's beacons are size + 3 rows away (vanilla reach 3: 2 per machine; this pack's reach 6: 4).
    """
    limit = _half(beacon) + beacon.get("supply_area_distance", 0) + _half(machine)
    pitch = size + bsize
    sideways = 0
    while (sideways + 0.5) * pitch < limit:
        sideways += 1
    rows = 2 if size + 3 < limit else 1
    return 2 * sideways * rows, max(sideways - 1, 0)


# "auto" inserters: the cheapest that keep up (default), or ("best", bulk names): the fastest non-bulk type, bulk
# only where nothing else fits. Set per plan by the web job runner (each job is its own thread and context).
INSERTER_PREF = contextvars.ContextVar("inserter_pref", default=None)


def pick_inserter(calib, allowed, belt, direction, keyf, need, slots):
    """fewest inserters first, then the slowest (cheapest) type that keeps up; keyf(name) -> calibration level.
    With INSERTER_PREF "best": the fastest type, non-bulk ones first, still the fewest inserters"""
    options = []
    for rank, name in enumerate(allowed):  # `allowed` is ordered cheapest first; breaks speed ties
        rate = calib.get(f"{name}|{belt}|{direction}|{keyf(name)}")
        if not rate:
            continue
        n = _ceil(need / (rate * MARGIN)) if need > 0 else 1
        if n <= slots:
            options.append((n, rate, rank, name))
    if not options:
        raise PlanError(f"no inserter keeps up with {need:.2f}/s {direction} on {belt} within {slots} slots")
    pref = INSERTER_PREF.get()
    if pref and pref[0] == "best":
        plain = [o for o in options if o[3] not in pref[1]] or options
        least = min(o[0] for o in plain)
        n, rate, _, name = max((o for o in plain if o[0] == least), key=lambda o: (o[1], o[2]))
    else:
        n, rate, _, name = min(options)
    return InserterChoice(name, n, round(n * rate, 3), round(need, 3))


def lane_options(rates, lane_cap, target, whole_belt=()):
    """every way to put the item ingredients on 1-2 input belts (an item's lanes stay on one belt),
    best first: most machines per row, then fewer belts, then least load on the far belt.
    whole_belt: items made in another block of a chain. A belt carrying one carries only such items - both lanes
    of one, or one lane each of two (the chain side-loads each onto its lane) - never a raw item"""
    items = list(rates)
    options = []
    for nb in (1, 2):
        for combo in itertools.product(items + [None], repeat=2 * nb):
            if any(it not in combo for it in items) or (nb == 2 and combo[2] is None and combo[3] is None):
                continue
            if any(len({i // 2 for i, c in enumerate(combo) if c == it}) > 1 for it in items):
                continue
            if any(any(c in whole_belt for c in combo[h:h + 2]) and not all(c in whole_belt for c in combo[h:h + 2])
                   for h in range(0, 2 * nb, 2)):
                continue
            limit = min(_floor(combo.count(it) * lane_cap / rates[it]) for it in items)
            scale = 1.0
            if limit < 1:  # one machine is faster than these lanes: it runs throttled at what they deliver
                scale = min(combo.count(it) * lane_cap / rates[it] for it in items)
                limit = 1
            far = sum(rates[it] for it in set(combo[2:]) if it) * scale
            options.append((min(target, limit), nb, far, combo, scale))
    options.sort(key=lambda o: (-o[0], -o[4], o[1], o[2]))
    return options



INSERTER_THROTTLE = (1.0, 0.85, 0.7, 0.55, 0.4, 0.3, 0.2)


def _pick_inputs(calib, belt, bonus, near_ins, far_ins, slots, rates, lane_cap, target, whole_belt_items, failures):
    """lane layout and input inserters for machines needing `rates` each, `target` per row.
    -> (per_row, belts, combo, belt scale, drain, near inserter, far inserter) or None"""
    options = lane_options(rates, lane_cap, target, tuple(whole_belt_items))
    if not options and whole_belt_items:
        raise PlanError(f"{', '.join(whole_belt_items)} don't fit on the input belts here: items made in the "
                        f"blueprint need belts of their own (2 per belt at most, one lane each) and a row has 2 "
                        f"input belts")
    if not options:
        hungry = max(rates, key=rates.get)
        raise PlanError(f"one machine needs {rates[hungry]:.1f}/s {hungry}, more than input belts of {belt} carry")
    for per_row, nb, _, combo, scale in options:
        near_need = sum(rates[it] for it in set(combo[:2]) if it) * scale
        far_need = sum(rates[it] for it in set(combo[2:]) if it) * scale
        # a belt-limited machine must drain whole lanes: that needs headroom (measured: 5-15% spare drained 85-93%,
        # 2x drained 100%); ask for 2x, else accept less and plan for ~85% of the belt
        tries = [(2.0, 1.0), (1.0, THROTTLE_DRAIN)] if scale < 1 else [(1.0, 1.0)]
        for headroom, expect in tries:
            try:
                ins_near = pick_inserter(calib, near_ins, belt, "belt_to_machine", bonus, near_need * headroom, slots)
                ins_far = None
                if nb == 2:
                    if not far_ins:
                        raise PlanError("no long-handed inserter available for a second input belt")
                    ins_far = pick_inserter(calib, far_ins, belt, "belt_to_machine", bonus, far_need * headroom,
                                            slots - ins_near.count)
                return per_row, nb, combo, scale, expect, ins_near, ins_far
            except PlanError as e:
                failures.append(str(e))
    return None

def plan(data, calib, recipe, machine, belt, bonuses=None, allowed=None, productivity=0.0,
         max_per_row=MAX_PER_ROW, modules=None, machine_quality="normal", beacon=None, beacon_modules=None,
         beacon_quality="normal", per_row=None, near=None, far=None, out=None, fuel_item=None,
         target_rate=None, whole_belt_items=()):
    """productivity: the save's recipe productivity (research etc.); modules/beacons add to it.
    Fine-tuning: per_row forces the machines per row; near/far/out force the inserter for that role."""
    if recipe not in data.raw["recipe"]:
        raise PlanError(f"unknown recipe {recipe}")
    r = data.raw["recipe"][recipe]
    m = data.raw["assembling-machine"].get(machine) or data.raw.get("furnace", {}).get(machine)
    if not m:
        raise PlanError(f"{machine} is not an assembling machine or furnace this template can use")
    furnace = m.get("type") == "furnace"
    category = r.get("category", "crafting")
    if category not in m.get("crafting_categories", []):
        raise PlanError(f"{machine} can't craft {recipe} (category {category})")
    size, height = _dims(m)  # width along the row, depth across it (machine facing north)
    rotated = False
    if size < height and not any(i.get("type") == "fluid" for i in r.get("ingredients", [])):
        size, height, rotated = height, size, True  # turned sideways: its long side gets the inserters
    bonuses = bonuses or ZERO

    def bonus(n):
        return level_key(data, n, bonuses)

    def zero(n):
        return level_key(data, n, ZERO)

    def usable(tiles):
        names = [n for n in template_inserters(data, allowed, tiles)
                 if calib.get(f"{n}|{belt}|belt_to_machine|{zero(n)}") and calib.get(f"{n}|{belt}|belt_to_machine|{bonus(n)}")]
        return sorted(names, key=lambda n: calib[f"{n}|{belt}|belt_to_machine|{zero(n)}"])  # slowest ~ cheapest first

    near_ins, far_ins = usable(1), usable(2)
    if not near_ins:
        raise PlanError(f"no calibrated inserters for {belt}")
    out_ins = near_ins
    for role, name, pool in (("input", near, near_ins), ("long-handed", far, far_ins), ("output", out, near_ins)):
        if name and name not in pool:
            raise PlanError(f"{name} can't be the {role} inserter here (not calibrated/researched for {belt})")
    near_ins, far_ins, out_ins = [near] if near else near_ins, [far] if far else far_ins, [out] if out else out_ins

    ingredients = r.get("ingredients", [])
    fluids = [i for i in ingredients if i.get("type") == "fluid"]
    items = [i for i in ingredients if i.get("type", "item") == "item"]
    if len(fluids) > 1:
        raise PlanError(f"{len(fluids)} fluid ingredients: this template handles one")
    if not 1 <= len(items) <= 4:
        raise PlanError(f"{len(items)} item ingredients: this template handles 1-4")
    results = [x for x in r.get("results", []) if x.get("type", "item") == "item"]
    if len(results) != 1 or len(r.get("results", [])) != 1:
        raise PlanError("recipes with several outputs or a fluid output: not supported yet")
    out = results[0]
    if beacon and size != height:
        raise PlanError(f"{machine} is {size}x{height}: beacons are laid out for square machines")
    if fluids and size % 2 == 0:
        raise PlanError(f"{machine}: fluid recipes in even-sized machines aren't supported yet")
    fluid_col = fluid_input_column(m, size, height) if fluids else None

    modules, beacon_modules = modules or Modules(), beacon_modules or Modules()
    beacon_size = per_machine_beacons = pad = 0
    if beacon:
        if beacon not in data.raw["beacon"]:
            raise PlanError(f"unknown beacon {beacon}")
        beacon_size = _size(data.raw["beacon"][beacon])
        if beacon_size > size:
            raise PlanError(f"{beacon} is bigger than {machine}")
        per_machine_beacons, pad = beacon_reach(m, data.raw["beacon"][beacon], size, beacon_size)
        if not per_machine_beacons:
            raise PlanError(f"{beacon} doesn't reach neighbouring machines")
    try:
        mod_speed, mod_prod, mod_consumption = machine_effects(data, m, r, modules, beacon, beacon_modules,
                                                               per_machine_beacons, beacon_quality)
    except EffectError as e:
        raise PlanError(str(e))
    speed = (1 + 0.3 * quality_level(data, machine_quality)) * (1 + mod_speed)
    productivity = min(productivity + mod_prod, r.get("maximum_productivity", 3.0))
    craft_rate = m["crafting_speed"] * speed / r.get("energy_required", 0.5)
    out_per_machine = craft_rate * _amount(out) * (1 + productivity)
    cap = belt_capacity(data, belt)
    lane_cap = cap / 2
    target = _ceil(lane_cap * OUTPUT_SLACK / out_per_machine)  # each row fills one output lane
    notes = []
    if target_rate:  # a set rate, or a chain stage making what the next block eats (each row fills one output lane)
        wanted = max(1, _ceil(target_rate / 2 * 1.03 / out_per_machine))
        if wanted > target:
            notes.append(f"{target_rate * 60:g}/min is more than one {belt} carries ({cap * 60:g}/min): planned a full belt")
        else:
            target = wanted
    if per_row:
        if per_row != target:
            notes.append(f"machines per row set to {per_row} (a full belt needs {target})")
        target = per_row
    elif target > max_per_row and not target_rate:
        notes.append(f"a full belt needs {target} machines per row; capped at {max_per_row}")
        target = max_per_row

    rates = {i["name"]: craft_rate * _amount(i) for i in items}  # per machine
    fuel, fuel_rate = None, 0.0
    es = m.get("energy_source", {})
    if es.get("type") == "burner":
        fuel, fuel_value = fuel_for(data, es, fuel_item)
        fuel_rate = energy(m["energy_usage"]) * (1 + mod_consumption) / (fuel_value * es.get("effectivity", 1))
        rates[fuel] = rates.get(fuel, 0) + fuel_rate
        notes.append(f"burner: each machine burns {fuel_rate:.3f} {fuel}/s, fed on the input belt")
        if len(rates) > 4:
            raise PlanError(f"{len(rates) - 1} ingredients plus fuel: this template handles 4 belt items")
    # the fluid entry takes one input slot; without beacons so does a power pole on alternate machines
    # (with beacons the poles sit above the beacons instead)
    slots = size - (1 if fluids else 0) if beacon else size - 1
    # a 2x2 with 3+ belt items needs both input tiles for inserters: its poles move to the output rows, where
    # the 7x7 supply still reaches the input inserters 3 rows away
    poles_out = not beacon and not fluids and height <= 2 and size <= 2 and len(rates) > 2
    if poles_out:
        slots = size
    failures = []
    # fastest first; when no inserters can feed a machine at full speed (slow early inserters, no research),
    # plan each machine slower - inserter-limited - with more of them per row
    out_slots = size if beacon else size - 1  # with beacons the output row has no pole either

    def out_keeps_up(per_machine):
        try:
            pick_inserter(calib, out_ins, belt, "machine_to_belt", bonus, per_machine, out_slots)
            return True
        except PlanError as e:
            failures.append(str(e))
            return False
    throttle = 1.0
    found = None
    for throttle in INSERTER_THROTTLE:
        if not out_keeps_up(out_per_machine * throttle):
            continue
        t_rates = {it: v * throttle for it, v in rates.items()}
        t_target = max(1, _ceil(target / throttle)) if throttle < 1 else target
        found = _pick_inputs(calib, belt, bonus, near_ins, far_ins, slots, t_rates, lane_cap, t_target,
                             whole_belt_items, failures)
        if found:
            break
    if not found and not out_keeps_up(out_per_machine * INSERTER_THROTTLE[-1]):
        raise PlanError(failures[-1])
    if not found:
        def best(names):
            return max((calib.get(f"{n}|{belt}|belt_to_machine|{bonus(n)}", 0), n) for n in names) if names else (0, "none")
        (nr, nn), (fr, fn) = best(near_ins), best(far_ins)
        per_machine = ", ".join(f"{it} {v:.2f}/s" for it, v in rates.items())
        raise PlanError(f"can't feed one machine through its {slots} input inserter slots: it needs {per_machine}; "
                        f"fastest inserter {nn} moves {nr}/s" + (f", fastest long-handed {fn} {fr}/s" if len(items) > 2 else ""))
    per_row, nb, combo, scale, drain, ins_near, ins_far = found
    if throttle < 1:
        craft_rate *= throttle
        out_per_machine *= throttle
        rates = t_rates
        target = t_target
        notes.append(f"its inserters can't keep one {machine} busy: each runs at {throttle:.0%} speed, "
                     f"{target} per row instead")

    if scale < 1:
        scale *= drain
        craft_rate *= scale
        out_per_machine *= scale
        rates = {it: r * scale for it, r in rates.items()}
        notes.append(f"one {machine} is faster than its input belt: it runs at {scale:.1%} speed (the belt is the limit)"
                     + ("; its inserters have little headroom, so planned for ~85% of the belt" if drain < 1 else ""))
        target = per_row
    if per_row < target:
        tight = min(rates, key=lambda it: combo.count(it) * lane_cap / rates[it])
        notes.append(f"input-limited: {tight} ({combo.count(tight)} lane(s), {lane_cap:.1f}/s each) feeds only "
                     f"{per_row} machines per row")

    # Output inserters dropping several items per swing pack the lane worse (measured 95.8% vs 100% for gears
    # at max research, stack inserters 96.9%), so prefer: hand capped at 1 (rates = no-bonus calibration),
    # then full hands, then stack inserters - whichever keeps up first.
    plain = [n for n in out_ins if "stack-inserter" not in n]
    ins_out = None
    for names, key, capped in ((plain, zero, True), (plain, bonus, False), (out_ins, bonus, False)):
        try:
            ins_out = pick_inserter(calib, names, belt, "machine_to_belt", key, out_per_machine, out_slots)
            ins_out.hand = 1 if capped and bonus(ins_out.name) != zero(ins_out.name) else 0
            break
        except PlanError as e:
            err = e
    if ins_out is None:
        raise err
    packing = 1.0
    if bonus(ins_out.name) != zero(ins_out.name) and not ins_out.hand or "stack-inserter" in ins_out.name:
        packing = PACKING_STACK if "stack-inserter" in ins_out.name else PACKING_FULL_HAND
        notes.append(f"output inserters must drop several items per swing: the output belt fills to ~{packing:.0%}")

    belts = [list(combo[:2])] + ([list(combo[2:])] if nb == 2 else [])
    need = {it: rates[it] * per_row for it in rates}
    fluid = fluids[0]["name"] if fluids else None
    fluid_need = craft_rate * _amount(fluids[0]) * per_row if fluids else 0.0
    return Plan(recipe, machine, belt, size, per_row, craft_rate, out["name"], out_per_machine, cap, lane_cap,
                belts, need, ins_near, ins_far, ins_out, fluid, fluid_need, fluid_col, modules, beacon, beacon_modules,
                beacon_size, per_machine_beacons, pad, speed, productivity, packing, notes=notes,
                furnace=furnace, fuel=fuel, fuel_rate=fuel_rate, poles_out=poles_out,
                height=height if height != size else 0, rotated=rotated)


def layout(p: Plan):
    """-> (entities, sources, sinks) with positions relative to the top-left corner"""
    s = p.size
    hs = p.height or s  # machine rows' depth
    bs = p.beacon_size
    pitch = s + bs  # machine + the beacon after it
    shift = p.beacon_pad * pitch  # room for the extra beacons past the west end
    width = p.per_row * pitch + bs + 2 * shift
    nb = len(p.belts)
    fluid = p.fluid is not None

    # rows, top to bottom
    y = 0
    Y = {}

    def row(name, height=1):
        nonlocal y
        Y[name] = y
        y += height

    if fluid:
        row("pipe_a"), row("tap_a")
    if nb == 2:
        row("far_a")
    row("near_a"), row("ins_a"), row("mach_a", hs), row("out_a"), row("belt")
    row("out_b"), row("mach_b", hs), row("ins_b"), row("near_b")
    if nb == 2:
        row("far_b")
    if fluid:
        row("tap_b"), row("pipe_b")

    ents = []

    def put(name, x, yy, direction=None, **kw):
        e = {"name": name, "position": {"x": x, "y": yy}}
        if direction is not None:
            e["direction"] = direction
        e.update(kw)
        ents.append(e)

    belt_rows = ["near_a", "near_b"] + (["far_a", "far_b"] if nb == 2 else [])
    for x in range(width):
        for name in belt_rows:
            put(p.belt, x + 0.5, Y[name] + 0.5, EAST)
        if fluid:
            put(PIPE, x + 0.5, Y["pipe_a"] + 0.5)
            put(PIPE, x + 0.5, Y["pipe_b"] + 0.5)
    for x in range(width + 1):  # one extra tile so every output passes the end
        put(p.belt, x + 0.5, Y["belt"] + 0.5, EAST)

    # side A faces north (inputs above), side B is side A turned 180 degrees
    sides = (
        ("a", NORTH, SOUTH, p.fluid_col),
        ("b", SOUTH, NORTH, None if p.fluid_col is None else s - 1 - p.fluid_col),
    )
    if p.beacon:
        # beacons between machines (and at both ends); poles above them: every inserter slot stays free
        for j in range(-p.beacon_pad, p.per_row + 1 + p.beacon_pad):
            xb = shift + j * pitch + bs / 2
            for side in "ab":
                put(p.beacon, xb, Y["mach_" + side] + bs / 2, modules=p.beacon_modules.items,
                    inventory=BEACON_MODULES)
            pole_x = shift + j * pitch + bs // 2 + 0.5
            for name in ("ins_a", "out_a", "ins_b"):
                put(POLE, pole_x, Y[name] + 0.5)
    machine_mods = {"modules": p.modules.items, "inventory": MACHINE_MODULES} if p.modules.count else {}
    for k in range(p.per_row):
        x0 = shift + bs + k * pitch
        has_pole = k % POLE_EVERY == 0 and not p.beacon
        for side, facing, toward_machine, fcol in sides:
            recipe = {} if p.furnace else {"recipe": p.recipe}
            m_dir = ({NORTH: EAST, SOUTH: WEST}[facing]) if p.rotated else facing
            put(p.machine, x0 + s / 2, Y["mach_" + side] + hs / 2, m_dir, **recipe, **machine_mods)
            # input row: inserters pick from the input side (they face it)
            slots = list(range(s))
            yi = Y["ins_" + side] + 0.5
            if fluid:
                slots.remove(fcol)
                put(UNDERGROUND, x0 + fcol + 0.5, yi, toward_machine)  # normal side into the machine
                put(UNDERGROUND, x0 + fcol + 0.5, Y["tap_" + side] + 0.5, facing)  # normal side up to the pipe
                if has_pole:
                    put(POLE, x0 + (s - 1 if fcol != s - 1 else 0) + 0.5, Y["tap_" + side] + 0.5)
            elif has_pole and not p.poles_out:
                put(POLE, x0 + slots.pop() + 0.5, yi)
            for choice in (p.ins_near, p.ins_far):
                if choice:
                    for _ in range(choice.count):
                        put(choice.name, x0 + slots.pop(0) + 0.5, yi, facing)
            # output row: inserters pick from the machine
            slots = list(range(s))
            yo = Y["out_" + side] + 0.5
            if has_pole and (side == "a" or p.poles_out):
                put(POLE, x0 + slots.pop() + 0.5, yo)
            extra = {"override_stack_size": p.ins_out.hand} if p.ins_out.hand else {}
            for j in slots[:p.ins_out.count]:
                put(p.ins_out.name, x0 + j + 0.5, yo, facing, **extra)

    sources = []
    for side in "ab":
        for i, lanes in enumerate(p.belts):
            sources.append({"kind": "belt", "position": {"x": 0.5, "y": Y[("near_", "far_")[i] + side] + 0.5},
                            "lanes": lanes, "rates": {it: p.input_need[it] for it in set(lanes) if it}})
        if fluid:
            sources.append({"kind": "fluid", "position": {"x": -0.5, "y": Y["pipe_" + side] + 0.5}, "fluid": p.fluid,
                            "rates": {p.fluid: p.fluid_need}})
    sinks = [{"kind": "belt", "position": {"x": width + 0.5, "y": Y["belt"] + 0.5}}]
    return ents, sources, sinks


def measure_ticks(p: Plan, minimum=7200):
    """long enough to average over many craft cycles (machines finishing in step make short samples lumpy)"""
    return max(minimum, int(30 / p.craft_rate * 60))


def warmup_ticks(data, p: Plan):
    """time before measuring: belts fill end to end twice (input, then output), plus several craft cycles
    so slow recipes settle (measured: 16 s crafts read 95% after 3 min, 100% after 10 min)"""
    tiles_per_s = data.raw["transport-belt"][p.belt]["speed"] * 60
    return int((60 + 2 * p.per_row * p.size / tiles_per_s + 12 / p.craft_rate) * 60)


POLE_COPPER = 5  # defines.wire_connector_id.pole_copper
CIRCUIT_RED = 1  # defines.wire_connector_id.circuit_red
POLE_REACH = 9
PIPE_UG_MAX = 10  # tiles a pipe-to-ground pair may span


def configure(data):
    """take the power pole and pipes from the game data: the layouts need a 1x1 pole that powers 7x7 and wires 9
    tiles (the medium pole, or the pack's nearest equivalent), a pipe and a pipe-to-ground"""
    global POLE, POLE_REACH, PIPE, UNDERGROUND, PIPE_UG_MAX

    def one_by_one(p):
        (x1, y1), (x2, y2) = p.get("collision_box") or ((0, 0), (0, 0))
        return math.ceil(x2 - x1) <= 1 and math.ceil(y2 - y1) <= 1
    poles = {n: p for n, p in data.raw.get("electric-pole", {}).items()
             if not p.get("hidden") and one_by_one(p) and p.get("supply_area_distance", 0) >= 3.5
             and p.get("maximum_wire_distance", 0) >= 9}
    if poles:
        POLE = "medium-electric-pole" if "medium-electric-pole" in poles else             min(poles, key=lambda n: (poles[n]["supply_area_distance"], poles[n]["maximum_wire_distance"], n))
        POLE_REACH = poles[POLE]["maximum_wire_distance"]
    pipes = [n for n, p in data.raw.get("pipe", {}).items() if not p.get("hidden")]
    if pipes:
        PIPE = "pipe" if "pipe" in pipes else sorted(pipes)[0]
    ptgs = {n: p for n, p in data.raw.get("pipe-to-ground", {}).items() if not p.get("hidden")}
    if ptgs:
        UNDERGROUND = "pipe-to-ground" if "pipe-to-ground" in ptgs else sorted(ptgs)[0]
        conns = (ptgs[UNDERGROUND].get("fluid_box") or {}).get("pipe_connections") or []
        PIPE_UG_MAX = max((c.get("max_underground_distance") or 0 for c in conns), default=0) or PIPE_UG_MAX


def pole_wires(entities):
    """Copper wires: a minimum spanning tree over all poles within wire reach (fewest, shortest wires that
    connect everything reachable).

    Poles built from a blueprint only get the wires the blueprint lists (unlike hand/script placement,
    which auto-connects), so without these most of the line has no power.
    """
    poles = [(i, e["position"]["x"], e["position"]["y"]) for i, e in enumerate(entities, 1) if e["name"] == POLE]
    edges = sorted(((x1 - x2) ** 2 + (y1 - y2) ** 2, i, j) for n, (i, x1, y1) in enumerate(poles)
                   for (j, x2, y2) in poles[n + 1:] if (x1 - x2) ** 2 + (y1 - y2) ** 2 <= POLE_REACH ** 2)
    parent = {i: i for i, _, _ in poles}

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


def insert_plans(modules, inventory):
    """blueprint item requests putting modules into consecutive slots of one inventory"""
    plans, slot = [], 0
    for name, quality, count in modules:
        stacks = [{"inventory": inventory, "stack": slot + i} for i in range(count)]
        slot += count
        plans.append({"id": {"name": name, "quality": quality}, "items": {"in_inventory": stacks}})
    return plans


def blueprint_string(entities, label, game_version=(2, 0, 77), description=None):
    bp_entities = []
    for i, e in enumerate(entities, 1):
        be = {"entity_number": i, "name": e["name"], "position": e["position"]}
        if e.get("direction"):
            be["direction"] = e["direction"]
        if e.get("recipe"):
            be["recipe"] = e["recipe"]
        if e.get("override_stack_size"):
            be["override_stack_size"] = e["override_stack_size"]
        if e.get("modules"):
            be["items"] = insert_plans(e["modules"], e["inventory"])
        if e.get("ug_type"):
            be["type"] = e["ug_type"]  # underground belt entrance/exit
        # label combinators, chest limits, splitter filters
        for key in ("control_behavior", "player_description", "bar", "filter", "output_priority", "input_priority",
                    "request_filters"):  # requester chests
            if e.get(key):
                be[key] = e[key]
        bp_entities.append(be)
    # circuit wires (red): an entity's "circuit_to" names others by their "wire_id" (a mall's chest and the inserters
    # that only run while it's short)
    number = {e["wire_id"]: i for i, e in enumerate(entities, 1) if e.get("wire_id") is not None}
    circuit = [[i, CIRCUIT_RED, number[w], CIRCUIT_RED] for i, e in enumerate(entities, 1)
               for w in e.get("circuit_to") or [] if w in number]
    major, minor, patch = game_version
    bp = {"blueprint": {"item": "blueprint", "label": label, "entities": bp_entities,
                        "wires": pole_wires(entities) + circuit,
                        "version": (major << 48) | (minor << 32) | (patch << 16)}}
    if description:
        bp["blueprint"]["description"] = description
    raw = json.dumps(bp, separators=(",", ":")).encode()
    return "0" + base64.b64encode(zlib.compress(raw, 9)).decode()


def describe(p: Plan):
    def ins(c):
        return f"{c.count} x {c.name} ({c.capacity}/s for {c.need}/s)"

    belts = "; ".join(f"{('near', 'far')[i]} belt lanes {[x or '-' for x in lanes]}" for i, lanes in enumerate(p.belts))
    lines = [
        f"{p.recipe}: {p.machines} x {p.machine} ({p.per_row} per row), {p.belt}",
        f"  output  {p.output}: {p.expected_output:.2f}/s of {p.belt_capacity:.2f}/s belt "
        f"({p.output_per_machine:.3f}/s per machine)",
        f"  inputs  per row: " + ", ".join(f"{k} {v:.2f}/s" for k, v in p.input_need.items())
        + (f", {p.fluid} {p.fluid_need:.1f}/s (pipe)" if p.fluid else ""),
        f"          {belts}",
        f"  inserters near {ins(p.ins_near)}" + (f"; far {ins(p.ins_far)}" if p.ins_far else ""),
        f"  inserters out  {ins(p.ins_out)}" + (f", hand size capped at {p.ins_out.hand}" if p.ins_out.hand else ""),
    ]
    if p.modules.count or p.beacon:
        n = 2 * (p.per_row + 1 + 2 * p.beacon_pad)
        beacons = f"; {n} x {p.beacon} with {p.beacon_modules} ({p.beacons_per_machine} per machine)" if p.beacon else ""
        lines.insert(1, f"  modules {p.modules}{beacons} -> speed x{p.speed:.2f}, productivity +{p.productivity:.0%}")
    lines += ["  note: " + n for n in p.notes]
    return "\n".join(lines)
