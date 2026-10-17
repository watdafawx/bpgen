"""The rate math of the line planner (planner.plan), apart from the geometry.

planner.plan works out what the layout needs (machine size, slot counts, fluid columns, beacon reach) and hands those
in as plain numbers; the functions here answer in rates:

    crafting(...)        crafts/s, speed, productivity, output and by-product yields, ingredient rates, burner fuel
    row_target(...)      machines a row needs for a full output lane (OUTPUT_SLACK) or a target_rate, capped
    pick_feed(...)       input lanes and inserters, the INSERTER_THROTTLE loop, belt-limited scaling, the output
                         inserter and packing: the plan's per-machine numbers as they end up
    lane_target, pick_feed_row   the same for the one-row layout of 5-6 belt items (planner._plan_two_sided)

Every note string and error message is what the planner always said: the golden snapshots compare them.
"""
import contextvars
import itertools
import math
from dataclasses import dataclass, field

from bpgen.effects import EffectError, machine_effects, quality_level


class PlanError(Exception):
    pass


MARGIN = 0.95  # use at most this share of a measured inserter rate
MAX_PER_ROW = 40  # a full belt of a slow recipe can need hundreds of machines
PIPE_FLOW = 6000  # per second, about what one pipe moves
# Inserters can't pack a lane perfectly (they drop into whatever gap passes), so a row sized to exactly
# one lane leaves the belt a few % short. Measured: copper cable 92% at exact size, 100% with one extra.
OUTPUT_SLACK = 1.05
THROTTLE_DRAIN = 0.85  # a belt-limited machine whose inserters have no headroom drains ~85% of its lanes
# How full the output lanes get when output inserters must drop several items per swing (measured with
# speed-moduled gears/cable, 3 machines per row: full hands 94.6-96.1%, stack inserters 91.7%).
PACKING_FULL_HAND, PACKING_STACK = 0.95, 0.92
# (a modded machine crafting 40/s outruns any 2 inserters 10 times over, and one with a 0.002 s recipe at speed 20
# crafts 10000/s: below 1% the steps shrink by 30% each, down to about a millionth, so every machine a belt could
# feed at all plans - slowed to what its inserters move)
INSERTER_THROTTLE = (1.0, 0.85, 0.7, 0.55, 0.4, 0.3, 0.2, 0.15, 0.1, 0.07, 0.05, 0.035, 0.025, 0.015, 0.01,
                     *(0.01 * 0.7 ** i for i in range(1, 25)))


def pct(x):
    """a share as a percentage for a note: whole numbers down to 1%, two significant digits below"""
    return f"{x:.0%}" if x >= 0.0095 else f"{x * 100:.2g}%"


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


# --- recipe numbers -----------------------------------------------------------------------------------------------

def _amount(entry):
    if "amount" in entry:
        return entry["amount"] * entry.get("probability", 1)
    return (entry.get("amount_min", 0) + entry.get("amount_max", 0)) / 2 * entry.get("probability", 1)


def _yield(entry, productivity):
    """per craft, with productivity (which skips the part a recipe marks ignored_by_productivity)"""
    a = _amount(entry)
    return a + max(0, a - entry.get("ignored_by_productivity", 0)) * productivity


def results_of(r):
    """a recipe's outputs, one entry per product (some list one several times, at different chances), what a
    script fills in (amount 0) left out; a single output comes back as it is"""
    res = [x for x in r.get("results") or [] if _amount(x) > 0]
    if len({(x.get("type", "item"), x["name"]) for x in res}) == len(res):
        return res
    merged = {}
    for x in res:
        k = (x.get("type", "item"), x["name"])
        if k in merged:
            m = merged[k]
            m["amount"] += _amount(x)
            m["ignored_by_productivity"] = m.get("ignored_by_productivity", 0) + x.get("ignored_by_productivity", 0)
        else:
            merged[k] = {"type": k[0], "name": k[1], "amount": _amount(x),
                         "ignored_by_productivity": x.get("ignored_by_productivity", 0)}
    return list(merged.values())


def main_result(r, kind=None):
    """the output a line is named and sized for: the recipe's main_product, else the one named like the recipe,
    else the largest (uranium processing: uranium-238); kind "item"/"fluid": among those only"""
    res = [x for x in results_of(r) if kind is None or x.get("type", "item") == kind]
    by_name = {x["name"]: x for x in res}
    return (by_name.get(r.get("main_product") or "") or by_name.get(r.get("name") or "")
            or max(res, key=_amount, default=None))


def belt_capacity(data, belt, stack=1):
    return data.raw["transport-belt"][belt]["speed"] * 480 * stack


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


# The pieces below: what a line computes before the rows are sized (planner.plan, direct.plan).

def quality_speed(data, machine_quality):
    """the crafting speed multiplier of a machine's quality (before modules and beacons)"""
    return 1 + 0.3 * quality_level(data, machine_quality)


def craft_rate_of(machine, recipe, speed=1):
    """crafts/s of one machine: its crafting speed (times `speed`, the multiplier from quality, modules, beacons)
    over the recipe's time"""
    return machine["crafting_speed"] * speed / recipe.get("energy_required", 0.5)


def main_output(craft_rate, out, productivity):
    """items/s of the main output of one machine (a recipe with one output, productivity on all of it)"""
    return craft_rate * _amount(out) * (1 + productivity)


def full_belt_note(target_rate, belt, cap):
    return f"{target_rate * 60:g}/min is more than one {belt} carries ({cap * 60:g}/min): planned a full belt"


# --- crafting speed, yields, fuel ---------------------------------------------------------------------------------

@dataclass
class Crafting:
    craft_rate: float  # crafts/s per machine
    speed: float  # crafting speed multiplier from quality, modules, beacons
    productivity: float  # (capped at the recipe's maximum)
    out_per_machine: float  # items/s of the main output (just its share with by-products: see full_main)
    byproducts: dict  # other item outputs -> items/s per machine
    full_main: float  # the main output's rate before it is throttled (byproducts scale with out_per_machine/this)
    out_all: float  # what the output inserters move: the main output and the by-products
    rates: dict  # belt item -> items/s one machine takes (ingredients, and a burner's fuel)
    fuel: str | None = None
    fuel_rate: float = 0.0
    notes: list = field(default_factory=list)  # the burner note: planner adds it after the row's own notes


def crafting(data, r, m, out, extra, items, productivity=0.0, modules=None, beacon=None, beacon_modules=None,
             per_machine_beacons=0, beacon_quality="normal", machine_quality="normal", fuel_item=None):
    """what one machine making recipe r does. out: the main item result, extra: the other item results (their
    yields ride the output belt); items: the item ingredients; productivity: the save's recipe productivity
    (research etc.), modules/beacons add to it"""
    try:
        mod_speed, mod_prod, mod_consumption = machine_effects(data, m, r, modules, beacon, beacon_modules,
                                                               per_machine_beacons, beacon_quality)
    except EffectError as e:
        raise PlanError(str(e))
    speed = quality_speed(data, machine_quality) * (1 + mod_speed)
    productivity = min(productivity + mod_prod, r.get("maximum_productivity", 3.0))
    craft_rate = craft_rate_of(m, r, speed)
    out_per_machine = main_output(craft_rate, out, productivity)
    byproducts = {x["name"]: craft_rate * _yield(x, productivity) for x in extra}
    if extra:
        out_per_machine = craft_rate * _yield(out, productivity)
    full_main = out_per_machine
    out_all = out_per_machine + sum(byproducts.values())
    rates = {i["name"]: craft_rate * _amount(i) for i in items}  # per machine
    fuel, fuel_rate, notes = None, 0.0, []
    es = m.get("energy_source", {})
    if es.get("type") == "burner":
        fuel, fuel_value = fuel_for(data, es, fuel_item)
        fuel_rate = energy(m["energy_usage"]) * (1 + mod_consumption) / (fuel_value * es.get("effectivity", 1))
        rates[fuel] = rates.get(fuel, 0) + fuel_rate
        notes.append(f"burner: each machine burns {fuel_rate:.3f} {fuel}/s, fed on the input belt")
    return Crafting(craft_rate, speed, productivity, out_per_machine, byproducts, full_main, out_all, rates, fuel,
                    fuel_rate, notes)


def byproducts_at(c, out_per_machine):
    """the by-products' rates per machine when the main output runs at out_per_machine (throttled)"""
    return {k: v * out_per_machine / c.full_main for k, v in c.byproducts.items()}


def fluid_flow(entry, craft_rate, productivity=None):
    """a fluid's rate per machine: an ingredient (amount), or with productivity an output (yield)"""
    return craft_rate * (_amount(entry) if productivity is None else _yield(entry, productivity))


# --- machines per row ---------------------------------------------------------------------------------------------

def row_target(data, belt, c, target_rate, per_row, max_per_row, two_sided, fluid_out, notes, rows=2,
               pipe_output=False):
    """-> (machines per row, the same before rounding, belt capacity, lane capacity). Each row fills one output
    lane; `per_row` and the notes are what the caller asked for. fluid_out: the fluid output entry carried away
    by one pipe per row (or None). two_sided: the one-row layout sizes itself (lane_target) - only the pipe applies.
    pipe_output: nothing leaves on a belt (the line's output is fluid_out): the rows are sized from the rate alone
    (one machine a row with none), and only the pipe caps them"""
    cap = belt_capacity(data, belt)
    lane_cap = cap / 2
    exact = lane_cap * OUTPUT_SLACK / c.out_all  # machines a row needs (each row fills one output lane)
    target = _ceil(exact)
    if pipe_output and not per_row:
        exact = target_rate / rows / c.out_per_machine if target_rate else 1
        target = max(1, _ceil(exact))
    elif two_sided:
        pass
    elif target_rate:  # a set rate, or a chain stage making what the next block eats (each row fills one output lane)
        wanted = max(1, _ceil(target_rate / rows * 1.03 / c.out_per_machine))
        if wanted > target:
            notes.append(full_belt_note(target_rate, belt, cap))
        else:
            target, exact = wanted, target_rate / rows * 1.03 / c.out_per_machine
    if per_row and not two_sided:
        if per_row != target:
            notes.append(f"machines per row set to {per_row} (a full belt needs {target})")
        target = exact = per_row
    elif target > max_per_row and not target_rate and not two_sided and not pipe_output:
        notes.append(f"a full belt needs {target} machines per row; capped at {max_per_row}")
        target = exact = max_per_row

    if fluid_out and not per_row:  # (one pipe per row carries the fluid output away)
        most = max(1, _floor(PIPE_FLOW / fluid_flow(fluid_out, c.craft_rate, c.productivity)))
        if target > most:
            notes.append(f"a pipe carries about {PIPE_FLOW}/s of {fluid_out['name']}: {most} machines per row")
            target = exact = most
    return target, exact, cap, lane_cap


def lane_target(data, belt, c, target_rate, per_row, max_per_row, notes):
    """the one-row layout (5-6 belt items): its machines fill the output belt's far lane only.
    -> (machines, belt capacity, lane capacity)"""
    cap = belt_capacity(data, belt)
    lane_cap = cap / 2
    target = _ceil(lane_cap * OUTPUT_SLACK / c.out_all)  # the output belt's far lane
    if target_rate:
        wanted = max(1, _ceil(target_rate * 1.03 / c.out_per_machine))
        if wanted > target:
            notes.append(f"{target_rate * 60:g}/min is more than a lane of {belt} carries: planned a full lane")
        else:
            target = wanted
    if per_row:
        target = per_row
    elif target > max_per_row:
        notes.append(f"a full lane needs {target} machines; capped at {max_per_row}")
        target = max_per_row
    return target, cap, lane_cap


# --- inserters and lanes ------------------------------------------------------------------------------------------

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


def _output_choice(calib, names, belt, key, bonus, zero, need, slots, capped):
    """the output inserters for `need` items/s per machine. capped: the blueprint caps the hand at 1 (when the
    hand-size bonus would otherwise apply), and the rates come from the no-bonus calibration"""
    ins_out = pick_inserter(calib, names, belt, "machine_to_belt", key, need, slots)
    ins_out.hand = 1 if capped and bonus(ins_out.name) != zero(ins_out.name) else 0
    return ins_out


# --- the inserter throttle: lanes, inserters, machine speed ------------------------------------------------------

@dataclass
class Feed:
    per_row: int  # machines per row the input lanes feed
    nb: int  # input belts
    combo: tuple  # lane items: near belt's two, then the far belt's two
    ins_near: InserterChoice
    ins_far: InserterChoice | None
    ins_out: InserterChoice
    throttle: float  # share of full speed the inserters allow (INSERTER_THROTTLE)
    scale: float  # share of full speed the belt allows (1: not belt-limited), drain included
    packing: float  # share of the output belt the output inserters manage to fill
    rates: dict  # belt item -> items/s per machine, as throttled and scaled
    craft_rate: float  # (as throttled and scaled)
    out_per_machine: float
    out_all: float
    notes: list = field(default_factory=list)


def pick_feed(calib, belt, bonus, zero, near_ins, far_ins, out_ins, slots, out_slots, c, lane_cap, target, exact,
              per_row, whole_belt_items, machine, n_items, out_needed=True, shared_fluid=False):
    """lanes and inserters for a row of machines doing `c`, `target` of them (`exact` before rounding), and the
    speed they end up at. Fastest first; when no inserters can feed a machine at full speed (slow early
    inserters, no research), plan each machine slower - inserter-limited - with more of them per row.
    per_row: the machines per row the caller forced (0/None: not). n_items: item ingredients (the message)"""
    craft_rate, out_per_machine, out_all, rates = c.craft_rate, c.out_per_machine, c.out_all, c.rates
    failures = []
    notes = []

    def out_keeps_up(per_machine):
        if not out_needed:  # (the output leaves by pipe)
            return True
        try:
            pick_inserter(calib, out_ins, belt, "machine_to_belt", bonus, per_machine, out_slots)
            return True
        except PlanError as e:
            failures.append(str(e))
            return False
    throttle = 1.0
    found = None
    for throttle in INSERTER_THROTTLE:
        if not out_keeps_up(out_all * throttle):
            continue
        t_rates = {it: v * throttle for it, v in rates.items()}
        # (slower machines: as many more as the row really needs, from its need before rounding - one machine at
        # 70% may still be plenty for a small rate)
        # (shared_fluid: rows that split one fluid pipe by demand, like an oil block's plastic and sulfur, keep the
        # machines a full speed row needs, scaled up for the slowdown: fewer machines would draw less of the pipe
        # than the rows beside them and starve)
        if shared_fluid:
            t_target = max(1, _ceil(target / throttle)) if throttle < 1 else target
        else:
            t_target = max(target if per_row else 1, _ceil(exact / throttle)) if throttle < 1 else target
        found = _pick_inputs(calib, belt, bonus, near_ins, far_ins, slots, t_rates, lane_cap, t_target,
                             whole_belt_items, failures)
        if found:
            break
    if not found and not out_keeps_up(out_all * INSERTER_THROTTLE[-1]):
        raise PlanError(failures[-1])
    if not found:
        def best(names):
            return max((calib.get(f"{n}|{belt}|belt_to_machine|{bonus(n)}", 0), n) for n in names) if names else (0, "none")
        (nr, nn), (fr, fn) = best(near_ins), best(far_ins)
        per_machine = ", ".join(f"{it} {v:.2f}/s" for it, v in rates.items())
        raise PlanError(f"can't feed one machine through its {slots} input inserter slots: it needs {per_machine}; "
                        f"fastest inserter {nn} moves {nr}/s" + (f", fastest long-handed {fn} {fr}/s" if n_items > 2 else ""))
    per_row, nb, combo, scale, drain, ins_near, ins_far = found
    if throttle < 1:
        craft_rate *= throttle
        out_per_machine *= throttle
        out_all *= throttle
        rates = t_rates
        target = t_target
        notes.append(f"its inserters can't keep one {machine} busy: each runs at {pct(throttle)} speed, "
                     f"{target} per row instead")

    if scale < 1:
        scale *= drain
        craft_rate *= scale
        out_per_machine *= scale
        out_all *= scale
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
    # (Stack inserters drop stacks; without belt-stacking research that measured worse on output belts,
    # so the first two tries skip anything named *stack-inserter*, which covers modded variants.)
    ins_out, packing = (_pick_out(calib, belt, bonus, zero, out_ins, out_slots, out_all, notes) if out_needed
                        else (None, 1.0))
    return Feed(per_row, nb, combo, ins_near, ins_far, ins_out, throttle, scale, packing, rates, craft_rate,
                out_per_machine, out_all, notes)


def _pick_out(calib, belt, bonus, zero, out_ins, out_slots, out_all, notes):
    """the output inserter for out_all/s per machine and the output belt's packing with it.
    Output inserters dropping several items per swing pack the lane worse (measured 95.8% vs 100% for gears
    at max research, stack inserters 96.9%), so prefer: hand capped at 1 (rates = no-bonus calibration),
    then full hands, then stack inserters - whichever keeps up first.
    (Stack inserters drop stacks; without belt-stacking research that measured worse on output belts,
    so the first two tries skip anything named *stack-inserter*, which covers modded variants.)"""
    plain = [n for n in out_ins if "stack-inserter" not in n]
    ins_out = None
    for names, key, capped in ((plain, zero, True), (plain, bonus, False), (out_ins, bonus, False)):
        try:
            ins_out = _output_choice(calib, names, belt, key, bonus, zero, out_all, out_slots, capped)
            break
        except PlanError as e:
            err = e
    if ins_out is None:
        raise err
    packing = 1.0
    if bonus(ins_out.name) != zero(ins_out.name) and not ins_out.hand or "stack-inserter" in ins_out.name:
        packing = PACKING_STACK if "stack-inserter" in ins_out.name else PACKING_FULL_HAND
        notes.append(f"output inserters must drop several items per swing: the output belt fills to ~{packing:.0%}")
    return ins_out, packing


def pick_out_only(calib, belt, bonus, zero, out_ins, out_slots, c, target, machine):
    """a row fed by pipe alone (no belt items): `target` machines, and the output inserter that keeps up with
    them, each machine slowed (INSERTER_THROTTLE) when none can at full speed"""
    failures, notes = [], []
    for throttle in INSERTER_THROTTLE:
        try:
            pick_inserter(calib, out_ins, belt, "machine_to_belt", bonus, c.out_all * throttle, out_slots)
            break
        except PlanError as e:
            failures.append(str(e))
    else:
        raise PlanError(failures[-1])
    out_all = c.out_all * throttle
    if throttle < 1:
        notes.append(f"its inserters can't keep one {machine} busy: each runs at {pct(throttle)} speed")
    ins_out, packing = _pick_out(calib, belt, bonus, zero, out_ins, out_slots, out_all, notes)
    return Feed(target, 0, (), None, None, ins_out, throttle, 1.0, packing, {}, c.craft_rate * throttle,
                c.out_per_machine * throttle, out_all, notes)


@dataclass
class RowFeed:
    per: int  # machines in the row
    throttle: float
    ins_near: InserterChoice
    ins_far: InserterChoice
    ins_out: InserterChoice
    ins_south: InserterChoice | None
    rates: dict  # belt item -> items/s per machine, as throttled
    craft_rate: float
    out_per_machine: float
    notes: list = field(default_factory=list)


def pick_feed_row(calib, belt, bonus, zero, near_ins, far_ins, out_ins, slots_n, slots_s, c, near, far, south,
                  lane_cap, target, per_row, machine):
    """the one-row layout: input inserters for the near, far and south belts (lane items near, far, south; one
    item per lane), the output inserter, the machines' speed under INSERTER_THROTTLE, how many the lanes feed"""
    rates = c.rates
    craft_rate, out_per_machine = c.craft_rate, c.out_per_machine
    notes = []
    failures = []
    found = None
    for throttle in INSERTER_THROTTLE:
        t = {it: v * throttle for it, v in rates.items()}
        try:
            ins_out = _output_choice(calib, [n for n in out_ins if "stack-inserter" not in n] or out_ins, belt, zero,
                                     bonus, zero, c.out_all * throttle, slots_s, True)
            ins_s = pick_inserter(calib, far_ins, belt, "belt_to_machine", bonus, sum(t[i] for i in south if i),
                                  slots_s - ins_out.count) if any(south) else None
            ins_near = pick_inserter(calib, near_ins, belt, "belt_to_machine", bonus, sum(t[i] for i in near if i),
                                     slots_n)
            ins_far = pick_inserter(calib, far_ins, belt, "belt_to_machine", bonus, sum(t[i] for i in far if i),
                                    slots_n - ins_near.count)
            found = throttle
            break
        except PlanError as e:
            failures.append(str(e))
    if found is None:
        raise PlanError(failures[-1])
    # (each item has a lane: as many machines as the hungriest lane feeds)
    fed = min(_floor(lane_cap / (v * found)) for v in rates.values())
    per = max(1, min(target, fed) if not per_row else per_row)
    if not per_row and fed < target:
        tight = max(rates, key=rates.get)
        notes.append(f"input-limited: {tight} (one lane, {lane_cap:.1f}/s) feeds only {per} machines")
    if found < 1:
        craft_rate *= found
        out_per_machine *= found
        notes.append(f"its inserters can't keep one {machine} busy: each runs at {pct(found)} speed")
    rates = {it: v * found for it, v in rates.items()}
    return RowFeed(per, found, ins_near, ins_far, ins_out, ins_s, rates, craft_rate, out_per_machine, notes)
