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
5-6 belt items: one machine row instead (_plan_two_sided): two input belts north, the output belt south, a third
input belt past it reached by long-handed inserters. Machines 5 wide or more get extra poles (power_up).
Several item outputs share the output belt; past its east end a filter splitter per by-product sorts it onto its
own belt (sort_tail). An item recipe with a fluid output and no fluid input uses the pipe rows to carry it away.
"""
import math
from dataclasses import dataclass, field
from typing import NamedTuple

from bpgen import ents as entlib, geom
from bpgen.ents import adder
from bpgen.calibrate import ZERO, level_key, template_inserters
from bpgen.effects import Modules
# (the rate math lives in rates.py; these names stay here for everything that reads planner.X)
from bpgen.rates import (EPS, INSERTER_PREF, INSERTER_THROTTLE, MARGIN, MAX_PER_ROW, OUTPUT_SLACK, PACKING_FULL_HAND,  # noqa: F401
                         PACKING_STACK, PIPE_FLOW, THROTTLE_DRAIN, InserterChoice, PlanError, _amount, _ceil, _floor,
                         _yield, belt_capacity, energy, fuel_for, lane_options, main_result, pick_inserter, results_of)
from bpgen.rates import (_pick_inputs, byproducts_at, craft_rate_of, crafting, fluid_flow, lane_target, pick_feed,  # noqa: F401
                         pick_feed_row, pick_out_only, row_target, Feed)

NORTH, EAST, SOUTH, WEST = 0, 4, 8, 12
POLE_EVERY = 2  # machines


def categories(r):
    """a recipe's crafting categories: its category and its additional_categories (any one is enough)"""
    return [r.get("category") or "crafting"] + list(r.get("additional_categories") or [])


def category_in(r, cats):
    """the first crafting category of recipe r found in cats (a set, or a dict keyed by category), else None"""
    return next((c for c in categories(r) if c in cats), None)


def in_cats(r, cats):
    return category_in(r, cats) is not None


def can_craft(m, r):
    """machine prototype m has a crafting category of recipe r"""
    return in_cats(r, m.get("crafting_categories") or [])


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
    flipped: bool = False  # machine turned 180 degrees from its side (its fluid input is on its south side)
    turn: int = 0  # (a square machine) turned this much more, both rows: its pipe connection is on a side
    fluid_out: bool = False  # the pipe rows carry a fluid output away (east) instead of an input in
    rows: int = 2  # machine rows: 1 for the two-sided layout (5-6 belt items)
    south: list = field(default_factory=list)  # two-sided: the far belt's lanes south of the output belt
    ins_south: InserterChoice | None = None  # two-sided: the long-handed inserters reaching it
    byproducts: dict = field(default_factory=dict)  # other item outputs -> per machine; they share the output belt
    out_share: float = 1.0  # the main output's share of what the output inserters move
    splitter: str | None = None  # sorts each by-product off the output belt
    pipe_output: bool = False  # nothing leaves on a belt: the line's output is its main fluid out, by pipe
    fluid_needs: dict = field(default_factory=dict)  # every fluid of the recipe -> per second per row
    fluid_inputs: dict = field(default_factory=dict)  # fluid ingredient -> per second per row
    fluid_outputs: dict = field(default_factory=dict)  # fluid result -> per second per row
    spec: object = None  # rowspec.RowSpec: how rowlayout lays it out (None: the old layouts)

    @property
    def machines(self):
        return self.rows * self.per_row

    @property
    def expected_output(self):
        if self.pipe_output:  # (a pipe per row carries it)
            return min(PIPE_FLOW * self.rows, self.machines * self.output_per_machine)
        return min(self.belt_capacity * self.rows / 2 * self.packing * self.out_share,
                   self.machines * self.output_per_machine)


def no_output(r):
    """why a recipe with nothing (measurable) to make has no line"""
    if not list(r.get("results") or []):
        return "this recipe makes nothing (it uses its ingredients up): no line to plan"
    return "this recipe's outputs are left for a script to fill in (0 in its data): no line to plan"


def splitter_for(data, belt):
    """the splitter of this belt tier: same speed, the one named like the belt first (None: the pack has none)"""
    speed = data.raw["transport-belt"][belt]["speed"]
    same = sorted(n for n, s in data.raw.get("splitter", {}).items() if s["speed"] == speed and not s.get("hidden"))
    named = belt.replace("transport-belt", "splitter")
    return named if named in same else (same[0] if same else None)


_dims = geom.dims  # (width, height) in tiles, placed facing north


def _size(proto):
    """the side of a square entity (beacons, and layouts that need square machines)"""
    w, h = _dims(proto)
    if w != h:
        raise PlanError(f"{proto['name']}: this layout needs a square machine ({w}x{h})")
    return w


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


def plan(data, calib, recipe, machine, belt, bonuses=None, allowed=None, productivity=0.0,
         max_per_row=MAX_PER_ROW, modules=None, machine_quality="normal", beacon=None, beacon_modules=None,
         beacon_quality="normal", per_row=None, near=None, far=None, out=None, fuel_item=None,
         target_rate=None, whole_belt_items=(), shared_fluid=False, output_item=None, one_row=False):
    """productivity: the save's recipe productivity (research etc.); modules/beacons add to it.
    Fine-tuning: per_row forces the machines per row; near/far/out force the inserter for that role.
    shared_fluid: rows that split one fluid pipe with others by demand (an oil block's plastic and sulfur).
    one_row: one row of machines, not two mirrored about the output (a starter base's fluids-only blocks, whose
    pipes its router connects from one side).
    output_item: the result the line is sized and named for (default the recipe's main one; a fluid only when
    nothing leaves on a belt - a starter base wanting petroleum from advanced oil processing).
    How the machines stand (facing, rows, pipes, free columns) comes from the row solver (rowsolve.py); the
    numbers (machines, inserters, lanes) from rates.py; the entities from rowlayout.py through the Plan's spec."""
    from bpgen import rowsolve
    try:
        shape = rowsolve.Shape.of(data, recipe, machine)
        pose = rowsolve.solve_general(data, shape, beacon, data.raw.get("beacon", {}).get(beacon) if beacon else None,
                                      one_row=one_row)
    except rowsolve.SolveError as e:
        raise PlanError(str(e))
    r, m = data.raw["recipe"][recipe], shape.proto
    furnace = shape.furnace
    size, height = pose.size, pose.height
    rotated, flipped, turn, fluid_col = pose.rotated, pose.flipped, pose.turn, pose.fluid_col
    fluids, items, fluid_out = shape.fluids, shape.items, shape.fluid_outs
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

    results = shape.item_outs
    # several outputs: all ride the output belt, a filter splitter per by-product sorts it off at the end
    pipe_output = not results  # (nothing leaves on a belt: the line is sized for its main fluid out)
    out = (main_result(r, "item") if len(results) > 1 else results[0]) if results else main_result(r, "fluid")
    if output_item:
        pool = results if results else fluid_out
        out = next((x for x in pool if x["name"] == output_item), None)
        if out is None:
            raise PlanError(f"{recipe} makes no {output_item}" + (" on its belt" if results else ""))
    extra = [x for x in results if x["name"] != out["name"]]
    splitter = splitter_for(data, belt) if extra else None
    if extra and not splitter:
        raise PlanError(f"{recipe} has several outputs: sorting them needs a splitter for {belt}")
    modules, beacon_modules = modules or Modules(), beacon_modules or Modules()
    beacon_size, per_machine_beacons, pad = pose.beacon_size, pose.per_machine_beacons, pose.beacon_pad
    c = crafting(data, r, m, out, extra, items, productivity, modules, beacon, beacon_modules, per_machine_beacons,
                 beacon_quality, machine_quality, fuel_item)
    notes = []
    two_sided = shape.belt_items > 4  # (5-6 belt items: one machine row with belts on both sides)
    main_fluid_out = next((x for x in fluid_out if x["name"] == out["name"]), None) if pipe_output else (fluid_out[0] if fluid_out else None)
    target, exact, cap, lane_cap = row_target(data, belt, c, target_rate, per_row, max_per_row, two_sided,
                                              main_fluid_out, notes, rows=pose.rows, pipe_output=pipe_output)
    notes += c.notes
    if c.fuel and len(c.rates) > 6:
        raise PlanError(f"{len(c.rates) - 1} ingredients plus fuel: this template handles 6 belt items")
    if two_sided:  # (one machine row with belts on both sides)
        p = _plan_two_sided(
            data, calib, belt, recipe, machine, m, size, height, c, near_ins, far_ins, out_ins, bonus, zero,
            out, fluids, fluid_out, fluid_col, flipped, turn, target_rate, per_row, max_per_row, notes, modules,
            splitter, furnace, rotated)
        by_role = _fluid_needs(shape, p.craft_rate, c.productivity, p.per_row)
        p.fluid_needs = _by_name(by_role)
        p.fluid_inputs, p.fluid_outputs = _of_role(by_role, "in"), _of_role(by_role, "out")
        p.notes += _loop_notes(by_role, p.rows)
        p.spec = pose.spec(p.belts, p.south, needs=by_role, byproducts=bool(p.byproducts))
        return p
    slots, out_slots, poles_out = pose.slots, pose.out_slots, pose.poles_out
    if pose.ins_band:
        f = pick_feed(calib, belt, bonus, zero, near_ins, far_ins, out_ins, slots, out_slots, c, lane_cap, target, exact,
                      per_row, whole_belt_items, machine, len(items), out_needed=pose.out_band,
                      shared_fluid=shared_fluid)
        belts = [list(f.combo[:2])] + ([list(f.combo[2:])] if f.nb == 2 else [])
    else:  # nothing comes in on a belt: as many machines as the target says, output inserters only
        f = pick_out_only(calib, belt, bonus, zero, out_ins, out_slots, c, target, machine) if pose.out_band \
            else Feed(target, 0, (), None, None, None, 1.0, 1.0, 1.0, {}, c.craft_rate, c.out_per_machine, c.out_all)
        belts = []
    notes += f.notes
    per_row = f.per_row
    need = {it: f.rates[it] * per_row for it in f.rates}
    by_role = _fluid_needs(shape, f.craft_rate, c.productivity, per_row)
    fluid_needs = _by_name(by_role)
    fluid = fluids[0]["name"] if fluids else (fluid_out[0]["name"] if fluid_out else None)
    fluid_need = by_role.get(("in" if fluids else "out", fluid), 0.0)
    byproducts = byproducts_at(c, f.out_per_machine)  # (as throttled)
    if extra:
        notes.append(f"its by-products ({', '.join(byproducts)}) share the output belt; a filter splitter per "
                     f"by-product sorts it onto its own belt at the east end")
    notes += _loop_notes(by_role, pose.rows)
    p = Plan(recipe, machine, belt, size, per_row, f.craft_rate, out["name"], f.out_per_machine, cap, lane_cap,
             belts, need, f.ins_near, f.ins_far, f.ins_out, fluid, fluid_need, fluid_col, modules, beacon,
             beacon_modules, beacon_size, per_machine_beacons, pad, c.speed, c.productivity, f.packing,
             notes=notes, furnace=furnace, fuel=c.fuel, fuel_rate=c.fuel_rate, poles_out=poles_out,
             height=height if height != size else 0, rotated=rotated, flipped=flipped, byproducts=byproducts,
             out_share=f.out_per_machine / f.out_all if extra else 1.0, splitter=splitter, turn=turn,
             fluid_out=bool(fluid_out), rows=pose.rows, pipe_output=pipe_output, fluid_needs=fluid_needs,
             fluid_inputs=_of_role(by_role, "in"), fluid_outputs=_of_role(by_role, "out"))
    p.spec = pose.spec(belts, needs=by_role, byproducts=bool(extra))
    return p


def _fluid_needs(shape, craft_rate, productivity, per_row):
    """{(role, fluid): per second per row} for every fluid of the recipe: "in" by amount, "out" by yield"""
    needs = {}
    for x in shape.fluids:
        needs[("in", x["name"])] = needs.get(("in", x["name"]), 0.0) + fluid_flow(x, craft_rate) * per_row
    for x in shape.fluid_outs:
        needs[("out", x["name"])] = needs.get(("out", x["name"]), 0.0) + fluid_flow(x, craft_rate, productivity) * per_row
    return needs


def _of_role(by_role, role):
    """{fluid: per second per row} of one role ("in" / "out")"""
    return {name: v for (r, name), v in by_role.items() if r == role}


def _by_name(by_role):
    """{fluid: per second per row}, a fluid both in and out summed"""
    out = {}
    for (_, name), v in by_role.items():
        out[name] = out.get(name, 0.0) + v
    return out


def _loop_notes(by_role, rows):
    """a fluid the recipe both eats and makes (coal liquefaction's heavy oil): say to pipe it back"""
    return [f"it eats {name} too: pipe {v * rows * 60:.0f}/min of its {name} output back to its {name} input "
            f"(and start it with some {name} from elsewhere)"
            for (role, name), v in by_role.items() if role == "in" and ("out", name) in by_role]


def _plan_two_sided(data, calib, belt, recipe, machine, m, size, height, c, near_ins, far_ins, out_ins, bonus,
                    zero, out, fluids, fluid_out, fluid_col, flipped, turn, target_rate, per_row, max_per_row, notes,
                    modules, splitter, furnace, rotated):
    """5-6 belt items: one machine row, two input belts north (near and long-handed inserters, as the template),
    the output belt south, and a third input belt past it that long-handed inserters reach over the output belt.
    One item per lane, the hungriest nearest. c: the machine's rates (rates.crafting)"""
    rates = c.rates
    if not far_ins:
        raise PlanError(f"{len(rates)} belt items need long-handed inserters calibrated for {belt}")
    order = sorted(rates, key=lambda it: -rates[it])
    lanes = order + [None] * (6 - len(order))
    near, far, south = lanes[0:2], lanes[2:4], lanes[4:6]
    # (north: the fluid's tap or a pole takes a slot; south: a pole on alternate machines)
    slots_n, slots_s = size - 1, size - 1
    target, cap, lane_cap = lane_target(data, belt, c, target_rate, per_row, max_per_row, notes)
    f = pick_feed_row(calib, belt, bonus, zero, near_ins, far_ins, out_ins, slots_n, slots_s, c, near, far, south,
                      lane_cap, target, per_row, machine)
    per, craft_rate, out_per_machine, rates = f.per, f.craft_rate, f.out_per_machine, f.rates
    notes += f.notes
    byproducts = byproducts_at(c, out_per_machine)
    notes.append(f"{len(rates)} belt items: one row of machines, two input belts north, the output belt south and "
                 f"a third input belt past it ({', '.join(i for i in south if i)}) that long-handed inserters reach "
                 f"over the output belt")
    if byproducts:
        notes.append(f"its by-products ({', '.join(byproducts)}) share the output belt; a filter splitter per "
                     f"by-product sorts it onto its own belt at the east end")
    fluid = fluids[0]["name"] if fluids else None
    fluid_need = craft_rate * _amount(fluids[0]) * per if fluids else 0.0
    if fluid_out:
        fluid, fluid_need = fluid_out[0]["name"], fluid_flow(fluid_out[0], craft_rate, c.productivity) * per
    need = {it: v * per for it, v in rates.items()}
    return Plan(recipe, machine, belt, size, per, craft_rate, out["name"], out_per_machine, cap, lane_cap,
                [list(near), list(far)], need, f.ins_near, f.ins_far, f.ins_out, fluid, fluid_need, fluid_col, modules,
                None, Modules(), 0, 0, 0, c.speed, c.productivity, 1.0, notes=notes, furnace=furnace, fuel=c.fuel,
                fuel_rate=c.fuel_rate, height=height if height != size else 0, rotated=rotated, flipped=flipped,
                byproducts=byproducts, out_share=out_per_machine / (out_per_machine + sum(byproducts.values())),
                splitter=splitter, turn=turn, fluid_out=bool(fluid_out), rows=1, south=list(south),
                ins_south=f.ins_south)


def layout(p: Plan):
    """-> (entities, sources, sinks) with positions relative to the top-left corner (rowlayout, from the plan's
    RowSpec)"""
    from bpgen import rowlayout
    return rowlayout.layout(p.spec, p)


def sort_tail(ents, belt, splitter, rates, output, x, yb, bottom):
    """sorts by-products off an output belt on row yb that ends at x - 1: past its end a filter splitter per
    by-product, 3 tiles apart; the by-product leaves by its south half, one tile east, then turns south down to row
    `bottom`; the rest goes on east. -> sinks, the main output's first"""
    put = adder(ents)
    sinks = []
    for item, rate in rates.items():
        put(splitter, x + 0.5, yb + 1.0, EAST, filter={"name": item, "quality": "normal", "comparator": "="},
            output_priority="right")
        put(belt, x + 1.5, yb + 0.5, EAST)
        put(belt, x + 2.5, yb + 0.5, EAST)
        put(belt, x + 1.5, yb + 1.5, EAST)
        for yy in range(yb + 1, bottom + 1):
            put(belt, x + 2.5, yy + 0.5, SOUTH)
        sinks.append({"kind": "belt", "position": {"x": x + 2.5, "y": bottom + 0.5}, "item": item, "rate": rate})
        x += 3
    return [{"kind": "belt", "position": {"x": x - 0.5, "y": yb + 0.5}, "item": output}] + sinks


def measure_ticks(p: Plan, minimum=7200):
    """long enough to average over many craft cycles (machines finishing in step make short samples lumpy)"""
    return max(minimum, int(30 / p.craft_rate * 60))


def warmup_ticks(data, p: Plan):
    """time before measuring: belts fill end to end twice (input, then output), plus several craft cycles
    so slow recipes settle (measured: 16 s crafts read 95% after 3 min, 100% after 10 min)"""
    tiles_per_s = data.raw["transport-belt"][p.belt]["speed"] * 60
    return int((60 + 2 * p.per_row * p.size / tiles_per_s + 12 / p.craft_rate) * 60)


CIRCUIT_RED = entlib.CIRCUIT_RED
WIRE_REACH = {"medium-electric-pole": 9, "small-electric-pole": 7.5, "substation": 18, "big-electric-pole": 32}  # (vanilla)


class Settings(NamedTuple):
    """what the layouts take from the game data: a 1x1 pole that powers 7x7 and wires 9 tiles (the medium pole, or the
    pack's nearest equivalent), a pipe and a pipe-to-ground (and how far a pair of those may span)"""
    POLE: str = "medium-electric-pole"
    POLE_REACH: float = 9
    PIPE: str = "pipe"
    UNDERGROUND: str = "pipe-to-ground"
    PIPE_UG_MAX: int = 10


def settings(data):
    """the Settings for this game data (worked out once, kept on it); vanilla's without data"""
    if data is None:
        return Settings()
    got = data.__dict__.get("_planner_settings")
    if got is None:
        got = data.__dict__["_planner_settings"] = _settings_of(data)
    return got


def _settings_of(data):
    d = Settings()
    pole, reach, pipe, ug, ug_max = d

    def one_by_one(p):
        (x1, y1), (x2, y2) = p.get("collision_box") or ((0, 0), (0, 0))
        return math.ceil(x2 - x1) <= 1 and math.ceil(y2 - y1) <= 1
    poles = {n: p for n, p in data.raw.get("electric-pole", {}).items()
             if not p.get("hidden") and one_by_one(p) and p.get("supply_area_distance", 0) >= 3.5
             and p.get("maximum_wire_distance", 0) >= 9}
    if poles:
        pole = "medium-electric-pole" if "medium-electric-pole" in poles else             min(poles, key=lambda n: (poles[n]["supply_area_distance"], poles[n]["maximum_wire_distance"], n))
        reach = poles[pole]["maximum_wire_distance"]
    pipes = [n for n, p in data.raw.get("pipe", {}).items() if not p.get("hidden")]
    if pipes:
        pipe = "pipe" if "pipe" in pipes else sorted(pipes)[0]
    ptgs = {n: p for n, p in data.raw.get("pipe-to-ground", {}).items() if not p.get("hidden")}
    if ptgs:
        ug = "pipe-to-ground" if "pipe-to-ground" in ptgs else sorted(ptgs)[0]
        conns = (ptgs[ug].get("fluid_box") or {}).get("pipe_connections") or []
        ug_max = max((c.get("max_underground_distance") or 0 for c in conns), default=0) or ug_max
    return Settings(pole, reach, pipe, ug, ug_max)


_followed = [None]  # (the game data the planner follows: the last loaded, Data.load hands it over)


def configure(data):
    """follow this game data: planner.POLE, PIPE, UNDERGROUND, PIPE_UG_MAX, POLE_REACH and _DATA are read from it.
    Data.load calls it, so nobody else has to; calling it again changes nothing"""
    _followed[0] = data


def __getattr__(name):  # (planner.POLE and the rest: the followed data's, whenever they are read)
    if name in Settings._fields:
        return getattr(settings(_followed[0]), name)
    if name == "_DATA":
        return _followed[0]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


insert_plans = entlib.insert_plans


def blueprint_string(entities, label, game_version=(2, 0, 77), description=None, tiles=None):
    return entlib.blueprint_string(entities, label, game_version, description, tiles, data=_followed[0])


def describe(p: Plan):
    def ins(c):
        return f"{c.count} x {c.name} ({c.capacity}/s for {c.need}/s)"

    belts = "; ".join(f"{('near', 'far')[i]} belt lanes {[x or '-' for x in lanes]}" for i, lanes in enumerate(p.belts))
    lines = [
        f"{p.recipe}: {p.machines} x {p.machine} ({p.per_row} per row), {p.belt}",
        f"  output  {p.output}: {p.expected_output:.2f}/s of {p.belt_capacity:.2f}/s belt "
        f"({p.output_per_machine:.3f}/s per machine)"
        + "".join(f"; {k} {v * p.machines:.2f}/s" for k, v in p.byproducts.items())
        + (f"; {p.fluid} {p.fluid_need:.1f}/s per row (pipe)" if p.fluid_out else ""),
        "  inputs  per row: " + ", ".join(f"{k} {v:.2f}/s" for k, v in p.input_need.items())
        + (f", {p.fluid} {p.fluid_need:.1f}/s (pipe)" if p.fluid and not p.fluid_out else ""),
        f"          {belts}",
        (f"  inserters near {ins(p.ins_near)}" + (f"; far {ins(p.ins_far)}" if p.ins_far else "")) if p.ins_near else "  no input inserters (fed by pipe)",
        (f"  inserters out  {ins(p.ins_out)}" + (f", hand size capped at {p.ins_out.hand}" if p.ins_out.hand else "")) if p.ins_out else "  no output inserters (a pipe carries it away)",
    ]
    if p.rows == 1:
        lines[0] = lines[0].replace(f"({p.per_row} per row)", "(one row, belts on both sides)")
        lines.insert(-1, f"          south belt lanes {[x or '-' for x in p.south]}"
                     + (f", long-handed {ins(p.ins_south)}" if p.ins_south else ""))
    if p.modules.count or p.beacon:
        n = 2 * (p.per_row + 1 + 2 * p.beacon_pad)
        beacons = f"; {n} x {p.beacon} with {p.beacon_modules} ({p.beacons_per_machine} per machine)" if p.beacon else ""
        lines.insert(1, f"  modules {p.modules}{beacons} -> speed x{p.speed:.2f}, productivity +{p.productivity:.0%}")
    lines += ["  note: " + n for n in p.notes]
    return "\n".join(lines)


from bpgen.power import POLE_COPPER, near_tiles, pole_wires, power_up  # noqa: E402,F401  (power reads planner's globals)
