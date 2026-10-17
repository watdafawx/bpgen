"""Direct insertion: a line whose main machines (E) get one ingredient straight from the machines making it (C) next
to them, the way players build electronic circuits (copper cable assemblers inserting into circuit assemblers, 3:2).

The belt-fed template (planner.py) feeds every ingredient from belts; a machine there has only its free inserter slots
on one side, so an ingredient needed 3 to 1 (cable for circuits) can't get in fast enough and the line stays far below
a belt. Here only the made item's own ingredient (copper) and the main recipe's other items (iron) come on belts.

One row, top to bottom (side A; side B is the same mirrored below the output belt, as in planner.layout):

    far belt    the main recipe's other items (iron, iron)
    near belt   the made item's ingredient (copper, copper)
    inserters   C <- near belt; E <- far belt (long-handed); poles in free slots
    machines    C > E < C > E < C   C > E < C ...   (> <: inserters in the 1-tile gaps, C into E)
    inserters   E -> output belt
    output belt (each row fills one lane)

A unit (CE, CECEC, CEC) is picked from k = what one E takes of the made item / what one C makes of it.
"""
from dataclasses import dataclass, field

from bpgen import geom, planner
from bpgen.ents import adder
from bpgen.calibrate import ZERO, level_key, template_inserters
from bpgen.effects import Modules, quality_level
from bpgen.planner import EAST, NORTH, SOUTH, WEST, PlanError

UNITS = (("CE", 1.0), ("CECEC", 1.5), ("CEC", 2.0))  # unit, the k it feeds (each C making as much as one E takes)


def _item_ings(r):
    return [i for i in r.get("ingredients") or [] if i.get("type", "item") == "item"]


def fits(data, recipe, machine, made, made_recipe, made_machine):
    """this template can lay out `recipe` in `machine` with `made` made next to it by `made_recipe` in
    `made_machine`: one item result each, no fluids, the made item's recipe from one item, at most two other items,
    square machines of one size. -> True, or a reason (str)"""
    rs = data.raw["recipe"]
    r, mr = rs.get(recipe), rs.get(made_recipe)
    if not r or not mr:
        return "unknown recipe"
    for x in (r, mr):
        if any(i.get("type") == "fluid" for i in (x.get("ingredients") or []) + (x.get("results") or [])):
            return "fluids aren't laid out by direct insertion"
        if len(x.get("results") or []) != 1:
            return "recipes with several results aren't laid out by direct insertion"
    if (mr["results"][0].get("name")) != made or made not in {i["name"] for i in _item_ings(r)}:
        return f"{made_recipe} doesn't make {made} for {recipe}"
    if len(_item_ings(mr)) != 1:
        return f"{made_recipe} takes {len(_item_ings(mr))} items: direct insertion feeds one (its own belt)"
    if len([i for i in _item_ings(r) if i["name"] != made]) > 2:
        return f"{recipe} takes more than 2 other items: they come on one belt"
    ms = [data.raw["assembling-machine"].get(n) for n in (machine, made_machine)]
    if not all(ms):
        return "direct insertion needs assembling machines"
    if not all(planner.can_craft(m, x) for m, x in zip(ms, (r, mr))):
        return "a machine can't craft its recipe"
    dims = {planner._dims(m) for m in ms}
    if len(dims) != 1 or len({d for wh in dims for d in wh}) != 1:
        return "direct insertion needs square machines of one size"
    return True


@dataclass
class DirectPlan:
    recipe: str
    machine: str
    belt: str
    size: int
    unit: str  # the machines of a unit, west to east: C (making the made item) and E (the main recipe)
    units: int  # per row
    craft_rate: float  # crafts/s per E
    output: str
    output_per_machine: float  # per E
    belt_capacity: float
    lane_capacity: float
    made: str
    made_recipe: str
    made_machine: str
    made_rate: float  # made items/s per C
    belts: list  # [near lanes, far lanes]
    input_need: dict  # item -> items/s per row
    ins_near: planner.InserterChoice  # C <- near belt
    ins_far: planner.InserterChoice | None  # E <- far belt
    ins_side: planner.InserterChoice  # C -> E, in a gap
    ins_out: planner.InserterChoice  # E -> output belt
    speed: float = 1.0
    productivity: float = 0.0
    notes: list = field(default_factory=list)
    # (as planner.Plan, for what reads a plan: the summary, labels, test runs)
    fluid: str | None = None
    fluid_need: float = 0.0
    beacon: str | None = None
    beacons_per_machine: int = 0
    packing: float = 1.0
    modules: Modules = field(default_factory=Modules)

    @property
    def per_row(self):  # machines along a row, C and E
        return self.units * len(self.unit)

    @property
    def machines(self):
        return 2 * self.per_row

    @property
    def main_machines(self):
        return 2 * self.units * self.unit.count("E")

    @property
    def expected_output(self):
        return min(self.belt_capacity * self.packing, self.main_machines * self.output_per_machine)


def plan(data, calib, recipe, machine, belt, made, made_recipe, made_machine, bonuses=None, allowed=None,
         productivity=0.0, machine_quality="normal", target_rate=None, max_per_row=planner.MAX_PER_ROW,
         modules=None, beacon=None, per_row=None, near=None, far=None, out=None, **_):
    """-> DirectPlan, or PlanError (the caller plans it another way)"""
    ok = fits(data, recipe, machine, made, made_recipe, made_machine)
    if ok is not True:
        raise PlanError(ok)
    if (modules and modules.count) or beacon or per_row or near or far or out:
        raise PlanError("direct insertion doesn't take modules, beacons or fine-tuning yet")
    rs = data.raw["recipe"]
    r, mr = rs[recipe], rs[made_recipe]
    m, mm = data.raw["assembling-machine"][machine], data.raw["assembling-machine"][made_machine]
    size = planner._dims(m)[0]
    bonuses = bonuses or ZERO
    speed = 1 + 0.3 * quality_level(data, machine_quality)
    craft = m["crafting_speed"] * speed / r.get("energy_required", 0.5)  # E crafts/s
    made_craft = mm["crafting_speed"] * speed / mr.get("energy_required", 0.5)  # C crafts/s
    made_per = planner._amount(mr["results"][0])
    out_item = r["results"][0]["name"]
    out_per = craft * planner._amount(r["results"][0]) * (1 + productivity)
    need = {i["name"]: craft * i["amount"] for i in _item_ings(r)}  # per E
    made_need = need.pop(made)
    sub_ing = _item_ings(mr)[0]
    k = made_need / (made_craft * made_per)
    fitting = [u for u, cap in UNITS if k <= cap + 1e-9]
    if not fitting:
        raise PlanError(f"one {machine} takes {k:.2f}x what one {made_machine} makes of {made}: more than direct "
                        f"insertion feeds (2 at most)")
    cap = planner.belt_capacity(data, belt)
    lane = cap / 2
    others = sorted(need)
    far_lanes = [others[0], others[0]] if len(others) == 1 else (others + [None, None])[:2] if others else None

    def bonus(n):
        return level_key(data, n, bonuses)

    def usable(tiles):
        names = [n for n in template_inserters(data, allowed, tiles)
                 if calib.get(f"{n}|{belt}|belt_to_machine|{bonus(n)}")]
        return sorted(names, key=lambda n: calib[f"{n}|{belt}|belt_to_machine|{bonus(n)}"])
    near_ins, far_ins = usable(1), usable(2)
    if not near_ins or (others and not far_ins):
        raise PlanError(f"no calibrated inserters for {belt}")
    slots = size - 1  # (a pole takes one slot on every other machine)
    failures = []
    for throttle in planner.INSERTER_THROTTLE:
        try:
            # C -> E through a gap: one C's whole output at most (sideways, `size` tiles; the machine-to-machine rate
            # taken as the belt-to-machine one: a machine's output is no slower to take from than a belt)
            side = planner.pick_inserter(calib, near_ins, belt, "belt_to_machine", bonus,
                                         made_craft * made_per * throttle, size)
            ins_near = planner.pick_inserter(calib, near_ins, belt, "belt_to_machine", bonus,
                                             made_craft * sub_ing["amount"] * throttle, slots)
            ins_far = planner.pick_inserter(calib, far_ins, belt, "belt_to_machine", bonus,
                                            sum(need.values()) * throttle, slots) if others else None
            ins_out = planner.pick_inserter(calib, near_ins, belt, "machine_to_belt", bonus, out_per * throttle, slots)
            break
        except PlanError as e:
            failures.append(str(e))
    else:
        raise PlanError(failures[-1])
    notes = []
    if throttle < 1:
        notes.append(f"its inserters can't keep the machines at full speed: they run at {throttle:.0%}")
    e_out = out_per * throttle
    want = target_rate / 2 * 1.03 if target_rate else lane * planner.OUTPUT_SLACK  # per row (one lane)
    if target_rate and want > lane * planner.OUTPUT_SLACK:
        notes.append(f"{target_rate * 60:g}/min is more than one {belt} carries ({cap * 60:g}/min): planned a full belt")
        want = lane * planner.OUTPUT_SLACK
    # the unit needing the fewest machines for this row (a few circuits: CEC; a belt of them: CECEC), then how many
    unit, units = min(((u, max(1, planner._ceil(want / (u.count("E") * e_out)))) for u in fitting),
                      key=lambda o: (o[1] * len(o[0]), -len(o[0])))
    n_c, n_e = unit.count("C"), unit.count("E")
    # the input belts: the made item's ingredient on both near lanes, the other items on the far belt
    per_unit = {sub_ing["name"]: n_e * made_need * throttle / made_per * sub_ing["amount"]}
    for it, v in need.items():
        per_unit[it] = per_unit.get(it, 0) + n_e * v * throttle
    lanes_of = {sub_ing["name"]: 2}
    for it in others:
        lanes_of[it] = lanes_of.get(it, 0) + (far_lanes or []).count(it)
    fit = min(planner._floor(lanes_of[it] * lane / v) for it, v in per_unit.items() if v > 0)
    if fit < 1:
        raise PlanError(f"one unit of {unit} needs more than its input belts of {belt} carry")
    if fit < units:
        tight = min(per_unit, key=lambda it: lanes_of[it] * lane / per_unit[it])
        notes.append(f"input-limited: {tight} ({lanes_of[tight]} lane(s), {lane:.1f}/s each) feeds {fit} units per row")
        units = fit
    while units * len(unit) > max_per_row and units > 1:
        units -= 1
    notes.append(f"made by direct insertion: {made} from {n_c * units * 2} {made_machine} into {n_e * units * 2} "
                 f"{machine} ({unit} units)")
    return DirectPlan(recipe, machine, belt, size, unit, units, craft * throttle, out_item, e_out, cap, lane, made,
                      made_recipe, made_machine, made_craft * made_per * throttle,
                      [[sub_ing["name"], sub_ing["name"]]] + ([far_lanes] if far_lanes else []),
                      {it: v * units for it, v in per_unit.items()}, ins_near, ins_far, side, ins_out,
                      speed=speed * throttle, productivity=productivity, notes=notes)


def layout(p: DirectPlan):
    """-> (entities, sources, sinks) with positions relative to the top-left corner, as planner.layout"""
    s = p.size
    kinds = p.unit * p.units
    pitch = s + 1  # a machine and the gap after it
    width = len(kinds) * pitch
    has_far = len(p.belts) > 1
    row = geom.Rows()
    Y = row.Y
    if has_far:
        row("far_a")
    row("near_a"), row("ins_a"), row("mach_a", s), row("out_a"), row("belt")
    row("out_b"), row("mach_b", s), row("ins_b"), row("near_b")
    if has_far:
        row("far_b")
    ents = []

    put = adder(ents)
    for x in range(width):
        for name in ("near_a", "near_b") + (("far_a", "far_b") if has_far else ()):
            put(p.belt, x + 0.5, Y[name] + 0.5, EAST)
    for x in range(width + 1):  # (one extra tile: every output passes the end)
        put(p.belt, x + 0.5, Y["belt"] + 0.5, EAST)
    for side, facing in (("a", NORTH), ("b", SOUTH)):
        for i, kind in enumerate(kinds):
            x0 = i * pitch
            main = kind == "E"
            put(p.machine if main else p.made_machine, x0 + s / 2, Y["mach_" + side] + s / 2, facing,
                recipe=p.recipe if main else p.made_recipe)
            has_pole = i % planner.POLE_EVERY == 0
            slots = list(range(s))
            yi = Y["ins_" + side] + 0.5
            if has_pole:
                put(planner.POLE, x0 + slots.pop() + 0.5, yi)
            choice = p.ins_far if main else p.ins_near
            for _ in range(choice.count if choice else 0):
                put(choice.name, x0 + slots.pop(0) + 0.5, yi, facing)
            slots = list(range(s))
            yo = Y["out_" + side] + 0.5
            if has_pole and side == "a":
                put(planner.POLE, x0 + slots.pop() + 0.5, yo)
            if main:
                extra = {"override_stack_size": p.ins_out.hand} if p.ins_out.hand else {}
                for j in slots[:p.ins_out.count]:
                    put(p.ins_out.name, x0 + j + 0.5, yo, facing, **extra)
            # the gap east of this machine: C into E (an inserter faces where it picks up from)
            if i + 1 < len(kinds) and {kind, kinds[i + 1]} == {"C", "E"}:
                pick = WEST if kind == "C" else EAST
                rows = sorted(range(s), key=lambda j: abs(j - (s - 1) / 2))[:p.ins_side.count]
                for j in rows:
                    put(p.ins_side.name, x0 + s + 0.5, Y["mach_" + side] + j + 0.5, pick)
    sources = []
    for side in "ab":
        for i, lanes in enumerate(p.belts):
            sources.append({"kind": "belt", "position": {"x": 0.5, "y": Y[("near_", "far_")[i] + side] + 0.5},
                            "lanes": lanes, "rates": {it: p.input_need[it] for it in set(lanes) if it}})
    sinks = [{"kind": "belt", "position": {"x": width + 0.5, "y": Y["belt"] + 0.5}}]
    return ents, sources, sinks


def describe(p: DirectPlan):
    def ins(c):
        return f"{c.count} x {c.name} ({c.capacity}/s for {c.need}/s)"
    lines = [
        f"{p.recipe}: {p.main_machines} x {p.machine} fed {p.made} by {p.machines - p.main_machines} x "
        f"{p.made_machine} ({p.units} x {p.unit} per row), {p.belt}",
        f"  output  {p.output}: {p.expected_output:.2f}/s of {p.belt_capacity:.2f}/s belt "
        f"({p.output_per_machine:.3f}/s per machine)",
        "  inputs  per row: " + ", ".join(f"{k} {v:.2f}/s" for k, v in p.input_need.items()),
        f"  inserters near {ins(p.ins_near)}" + (f"; far {ins(p.ins_far)}" if p.ins_far else "")
        + f"; between {ins(p.ins_side)}",
        f"  inserters out  {ins(p.ins_out)}",
    ]
    lines += ["  note: " + n for n in p.notes]
    return "\n".join(lines)


def warmup_ticks(data, p: DirectPlan):
    tiles_per_s = data.raw["transport-belt"][p.belt]["speed"] * 60
    return int((60 + 2 * p.per_row * (p.size + 1) / tiles_per_s + 12 / p.craft_rate) * 60)


def measure_ticks(p: DirectPlan, minimum=7200):
    return max(minimum, int(30 / p.craft_rate * 60))
