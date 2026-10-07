"""Lines the belt template (planner.py) can't lay out: recipes with a fluid output, and recipes with two fluid
inputs and no items. Both stand two machine rows around a shared middle, like the template, sized by rate.

fluid-output line (e.g. sulfuric acid: sulfur + iron plate + water -> acid)
    water pipe          (if the recipe takes a fluid)
    tap row             pipe-to-ground down under the belt
    item belt           up to 2 items, one per lane
    input row           pipe-to-ground into the machine, an inserter, a pole on alternate machines
    machine row A
    output pipe         both rows' fluid outputs open onto it; its east end is the output
    machine row B ... mirrored

two-fluid line (e.g. sulfur: water + petroleum gas -> sulfur)
    fluid 1 pipe
    tap row             pipe-to-ground down under the fluid 2 pipe
    fluid 2 pipe
    input row           pipe-to-ground (fluid 1) and a pipe (fluid 2) into the machine's two inputs
    machine row A
    output inserters    and the poles
    output belt
    output inserters B, machine row B, mirrored below

two fluids and 1-2 items (e.g. Krastorio 2 rocket fuel: iron plate + light oil + oxygen): the two-fluid top, one
machine row, then on the south side
    inserter row        an output inserter, a long-handed input inserter, a pole
    output belt
    input belt          the items, one per lane (the long-handed inserters reach over the output belt)
"""
import math
from dataclasses import dataclass, field

from bpgen.calibrate import ZERO, level_key, template_inserters
from bpgen import planner
from bpgen.planner import (EAST, NORTH, SOUTH, PlanError, _amount, _size, belt_capacity,
                           pick_inserter)


@dataclass
class FluidPlan:
    kind: str  # "fluid-out" | "two-fluid"
    recipe: str
    machine: str
    belt: str
    size: int
    per_row: int
    craft_rate: float  # per machine at full speed
    output: str
    output_per_machine: float
    expected: float  # what the line makes (target or capacity)
    items: list  # item ingredients on the input belt, [left lane, right lane]
    fluids: list  # fluid ingredients, in fluid box order
    input_need: dict  # per row
    ins_in: object = None
    ins_out: object = None
    in_cols: list = field(default_factory=list)  # machine-facing-north columns of the fluid inputs, box order
    out_cols: list = field(default_factory=list)  # ... of the fluid outputs (south edge)
    notes: list = field(default_factory=list)
    facing: int = NORTH  # fluid-fluid: turned round (SOUTH) when the machine has its inputs on top (a chemical plant)

    @property
    def machines(self):
        return self.per_row if self.kind in ("fluid-fluid", "fluid-in", "two-fluid-items") else 2 * self.per_row


def _cols(machine, size, kind, direction):
    """columns (0 = west, machine facing north) of its fluid connections of a kind ("input"/"output") on the edge
    facing `direction`, in fluid box order"""
    out = []
    for fb in machine.get("fluid_boxes", []):
        if fb.get("production_type") not in (kind, "input-output"):
            continue
        for pc in fb.get("pipe_connections", []):
            if pc.get("connection_type", "normal") != "normal" or pc.get("direction", NORTH) != direction:
                continue
            x, y = pc["position"] if isinstance(pc["position"], list) else (pc["position"]["x"], pc["position"]["y"])
            out.append(int(x + (size - 1) / 2))
            break
    return out


def plan_fluid(data, calib, recipe, machine, belt, bonuses=None, allowed=None, target_rate=None, productivity=0.0):
    r = data.raw["recipe"].get(recipe)
    m = data.raw["assembling-machine"].get(machine) or data.raw.get("furnace", {}).get(machine)
    if not r or not m:
        raise PlanError(f"unknown recipe or machine ({recipe}, {machine})")
    if r.get("category", "crafting") not in m.get("crafting_categories", []):
        raise PlanError(f"{machine} can't craft {recipe}")
    size = _size(m)
    ings = r.get("ingredients", [])
    items = [i for i in ings if i.get("type", "item") == "item"]
    fluids = [i for i in ings if i.get("type") == "fluid"]
    results = r.get("results") or []
    if len(results) != 1:
        raise PlanError(f"{recipe}: one product only")
    out = results[0]
    if out.get("type") == "fluid" and not items and len(fluids) == 1:
        return _plan_fluid_fluid(m, r, recipe, machine, belt, size, fluids[0], out, target_rate, productivity)
    if out.get("type", "item") == "item" and not items and len(fluids) == 1:
        return _plan_fluid_in(data, calib, m, r, recipe, machine, belt, size, fluids[0], out, bonuses, allowed,
                              target_rate, productivity)
    if size != 3:
        raise PlanError(f"{machine}: the fluid lines are laid out for 3x3 machines")
    if out.get("type") == "fluid":
        kind = "fluid-out"
        if len(fluids) > 1 or len(items) > 2 or not items:
            raise PlanError(f"{recipe}: a fluid-output line takes 1-2 items and at most one fluid")
    elif not items and len(fluids) == 2:
        kind = "two-fluid"
    elif len(fluids) == 2 and len(items) <= 2:
        kind = "two-fluid-items"
    else:
        raise PlanError(f"{recipe}: not a fluid line recipe")
    in_cols = _cols(m, size, "input", NORTH)
    out_cols = _cols(m, size, "output", SOUTH)
    if len(in_cols) < len(fluids) or (kind == "fluid-out" and not out_cols):
        raise PlanError(f"{machine}: its fluid connections don't fit this layout")
    if kind in ("two-fluid", "two-fluid-items") and set(in_cols[:2]) != {0, 2}:
        raise PlanError(f"{machine}: two-fluid lines need inputs at both north corners")
    bonuses = bonuses or ZERO
    craft_rate = m["crafting_speed"] / r.get("energy_required", 0.5)
    opm = craft_rate * _amount(out) * (1 + productivity)
    rows = 1 if kind == "two-fluid-items" else 2
    target = target_rate or opm * rows
    per_row = max(1, math.ceil(target / rows / opm - 1e-9))
    use = target / (rows * per_row * opm)  # share of full speed each machine runs at
    item_rates = {i["name"]: craft_rate * use * _amount(i) for i in items}  # per machine
    notes = []
    if use < 0.5:
        notes.append(f"each machine runs at {use:.0%} of full speed" + (
            f": {per_row * 2} machines is the fewest that keeps rows even" if rows == 2 else ""))

    def key(n):
        return level_key(data, n, bonuses)
    ins_in = ins_out = None
    if kind == "fluid-out":
        names = template_inserters(data, allowed, 1)
        names = [n for n in names if calib.get(f"{n}|{belt}|belt_to_machine|{key(n)}")]
        names.sort(key=lambda n: calib[f"{n}|{belt}|belt_to_machine|{key(n)}"])
        ins_in = pick_inserter(calib, names, belt, "belt_to_machine", key, sum(item_rates.values()), 1)
    elif kind == "two-fluid-items":  # one long-handed inserter in (over the output belt), one out
        far = [n for n in template_inserters(data, allowed, 2) if calib.get(f"{n}|{belt}|belt_to_machine|{key(n)}")]
        if not far:
            raise PlanError(f"no long-handed inserter measured on {belt}")
        far.sort(key=lambda n: calib[f"{n}|{belt}|belt_to_machine|{key(n)}"])
        ins_in = pick_inserter(calib, far, belt, "belt_to_machine", key, sum(item_rates.values()), 1)
        names = template_inserters(data, allowed, 1)
        names = [n for n in names if calib.get(f"{n}|{belt}|machine_to_belt|{key(n)}")]
        names.sort(key=lambda n: calib[f"{n}|{belt}|machine_to_belt|{key(n)}"])
        ins_out = pick_inserter(calib, names, belt, "machine_to_belt", key, opm * use, 1)
    else:
        names = template_inserters(data, allowed, 1)
        names = [n for n in names if calib.get(f"{n}|{belt}|machine_to_belt|{key(n)}")]
        names.sort(key=lambda n: calib[f"{n}|{belt}|machine_to_belt|{key(n)}"])
        ins_out = pick_inserter(calib, names, belt, "machine_to_belt", key, opm * use, 2)
    lanes = [i["name"] for i in items] + [None] * (2 - len(items))
    if len(items) == 1:
        lanes = [items[0]["name"]] * 2
    need = {k: v * per_row for k, v in item_rates.items()}
    for f in fluids:
        need[f["name"]] = craft_rate * use * _amount(f) * per_row
    return FluidPlan(kind, recipe, machine, belt, size, per_row, craft_rate, out["name"], opm, target, lanes,
                     [f["name"] for f in fluids], need, ins_in, ins_out, in_cols, out_cols, notes)


def _plan_fluid_fluid(m, r, recipe, machine, belt, size, fluid, out, target_rate, productivity):
    """fluid in, fluid out, nothing else (e.g. basic oil processing): one row, inputs on the machines' south edge,
    outputs on their north edge (an oil refinery facing north)"""
    ins, outs, facing = _cols(m, size, "input", SOUTH), _cols(m, size, "output", NORTH), NORTH
    if not ins or not outs:  # inputs on top (a chemical plant): turned round, its columns mirrored as placed
        ins = [size - 1 - c for c in _cols(m, size, "input", NORTH)]
        outs = [size - 1 - c for c in _cols(m, size, "output", SOUTH)]
        facing = SOUTH
    if not ins or not outs:
        raise PlanError(f"{machine}: needs its fluid inputs on one edge and its outputs on the opposite edge")
    # the recipe names the fluid box each fluid uses (1-based among that kind); tap only those, so the pipes never
    # touch the boxes other recipes use (an oil refinery's heavy and light oil outputs in basic processing)
    in_col = ins[min(len(ins), fluid.get("fluidbox_index", 1)) - 1]
    out_col = outs[min(len(outs), out.get("fluidbox_index", 1)) - 1]
    craft_rate = m["crafting_speed"] / r.get("energy_required", 0.5)
    opm = craft_rate * _amount(out) * (1 + productivity)
    target = target_rate or opm
    n = max(1, math.ceil(target / opm - 1e-9))
    use = target / (n * opm)
    notes = [f"each machine runs at {use:.0%} of full speed"] if use < 0.5 else []
    return FluidPlan("fluid-fluid", recipe, machine, belt, size, n, craft_rate, out["name"], opm, target, [], [fluid["name"]],
                     {fluid["name"]: craft_rate * use * _amount(fluid) * n}, in_cols=[in_col], out_cols=[out_col],
                     notes=notes, facing=facing)


def _plan_fluid_in(data, calib, m, r, recipe, machine, belt, size, fluid, out, bonuses, allowed, target_rate,
                   productivity):
    """one fluid in, an item out, no item ingredients (solid fuel from heavy oil, sulfur-like recipes): one row,
    the fluid's input facing the pipe row above, output inserters below onto a belt"""
    ins, facing = _cols(m, size, "input", NORTH), NORTH
    if not ins:  # input on the other edge: turned round, its columns mirrored as placed
        ins, facing = [size - 1 - c for c in _cols(m, size, "input", SOUTH)], SOUTH
    if not ins:
        raise PlanError(f"{machine}: no fluid input on its north or south edge")
    in_col = ins[min(len(ins), fluid.get("fluidbox_index", 1)) - 1]
    craft_rate = m["crafting_speed"] / r.get("energy_required", 0.5)
    opm = craft_rate * _amount(out) * (1 + productivity)
    target = target_rate or opm
    n = max(1, math.ceil(target / opm - 1e-9))
    use = target / (n * opm)
    bonuses = bonuses or ZERO

    def key(name):
        return level_key(data, name, bonuses)
    names = [x for x in template_inserters(data, allowed, 1) if calib.get(f"{x}|{belt}|machine_to_belt|{key(x)}")]
    names.sort(key=lambda x: calib[f"{x}|{belt}|machine_to_belt|{key(x)}"])
    ins_out = pick_inserter(calib, names, belt, "machine_to_belt", key, opm * use, size - 1)  # (one column: a pole)
    notes = [f"each machine runs at {use:.0%} of full speed"] if use < 0.5 else []
    return FluidPlan("fluid-in", recipe, machine, belt, size, n, craft_rate, out["name"], opm, target, [],
                     [fluid["name"]], {fluid["name"]: craft_rate * use * _amount(fluid) * n}, ins_out=ins_out,
                     in_cols=[in_col], notes=notes, facing=facing)


def layout(p: FluidPlan):
    """-> (entities, sources, sinks), top-left at (0, 0)"""
    s = p.size
    width = p.per_row * s
    ents, sources, sinks = [], [], []
    if p.kind == "fluid-in":
        # input pipe / risers / machines / output inserters with a pole per machine / output belt
        ic = p.in_cols[0]
        y_ins, y_belt = 2 + s, 3 + s
        for x in range(width):
            ents.append({"name": planner.PIPE, "position": {"x": x + 0.5, "y": 0.5}})
        for x in range(width + 1):
            ents.append({"name": p.belt, "position": {"x": x + 0.5, "y": y_belt + 0.5}, "direction": EAST})
        for k in range(p.per_row):
            x0 = k * s
            ents.append({"name": p.machine, "position": {"x": x0 + s / 2, "y": 2 + s / 2}, "direction": p.facing,
                         "recipe": p.recipe})
            ents.append({"name": planner.PIPE, "position": {"x": x0 + ic + 0.5, "y": 1.5}})
            ents.append({"name": planner.POLE, "position": {"x": x0 + 0.5, "y": y_ins + 0.5}})
            for j in range(1, 1 + p.ins_out.count):  # picking from the machine above, dropping onto the belt
                ents.append({"name": p.ins_out.name, "position": {"x": x0 + j + 0.5, "y": y_ins + 0.5}, "direction": NORTH})
        sources.append({"kind": "fluid", "position": {"x": -0.5, "y": 0.5}, "fluid": p.fluids[0],
                        "rates": {p.fluids[0]: p.input_need[p.fluids[0]]}})
        sinks.append({"kind": "belt", "position": {"x": width + 0.5, "y": y_belt + 0.5}})
        return ents, sources, sinks
    if p.kind == "fluid-fluid":
        # output pipe / risers to the used output, poles between / machines (facing north) / risers / input pipe
        oc, ic = p.out_cols[0], p.in_cols[0]
        pole_col = next(c for c in range(s) if c != oc)
        for x in range(width + 1):
            ents.append({"name": planner.PIPE, "position": {"x": x + 0.5, "y": 0.5}})
        for x in range(width):
            ents.append({"name": planner.PIPE, "position": {"x": x + 0.5, "y": s + 3.5}})
        for k in range(p.per_row):
            x0 = k * s
            ents.append({"name": p.machine, "position": {"x": x0 + s / 2, "y": 2 + s / 2}, "direction": p.facing,
                         "recipe": p.recipe})
            ents.append({"name": planner.PIPE, "position": {"x": x0 + oc + 0.5, "y": 1.5}})
            ents.append({"name": planner.PIPE, "position": {"x": x0 + ic + 0.5, "y": s + 2.5}})
            ents.append({"name": planner.POLE, "position": {"x": x0 + pole_col + 0.5, "y": 1.5}})
        sources.append({"kind": "fluid", "position": {"x": -0.5, "y": s + 3.5}, "fluid": p.fluids[0],
                        "rates": {p.fluids[0]: p.input_need[p.fluids[0]]}})
        sinks.append({"kind": "fluid", "position": {"x": width + 0.5, "y": 0.5}, "item": p.output})
        return ents, sources, sinks

    def put(name, x, y, direction=None, **kw):
        e = {"name": name, "position": {"x": x, "y": y}}
        if direction is not None:
            e["direction"] = direction
        e.update(kw)
        ents.append(e)

    def row_of(name, y, n=width, direction=None):
        for x in range(n):
            put(name, x + 0.5, y + 0.5, direction)

    recipe = {"recipe": p.recipe}
    if p.kind == "fluid-out":
        fl = bool(p.fluids)
        fa = p.in_cols[0] if fl else None  # row A's fluid column; row B (turned round) uses s-1-fa
        y = 0
        if fl:
            row_of(planner.PIPE, 0)
            y = 2
        Y = {"belt_a": y, "ins_a": y + 1, "mach_a": y + 2, "out": y + 2 + s, "mach_b": y + 3 + s}
        Y["ins_b"] = Y["mach_b"] + s
        Y["belt_b"] = Y["ins_b"] + 1
        row_of(p.belt, Y["belt_a"], direction=EAST)
        row_of(p.belt, Y["belt_b"], direction=EAST)
        row_of(planner.PIPE, Y["out"], width + 1)
        if fl:
            row_of(planner.PIPE, Y["belt_b"] + 2)
        for k in range(p.per_row):
            x0 = k * s
            for side, facing, back in (("a", NORTH, SOUTH), ("b", SOUTH, NORTH)):
                put(p.machine, x0 + s / 2, Y["mach_" + side] + s / 2, facing, **recipe)
                yi = Y["ins_" + side] + 0.5
                cols = list(range(s))
                if fl:
                    fc = fa if side == "a" else s - 1 - fa
                    cols.remove(fc)
                    put(planner.UNDERGROUND, x0 + fc + 0.5, yi, back)  # normal side into the machine
                    tap_y = (Y["belt_a"] - 1) if side == "a" else (Y["belt_b"] + 1)
                    put(planner.UNDERGROUND, x0 + fc + 0.5, tap_y + 0.5, facing)  # normal side to the water pipe
                put(p.ins_in.name, x0 + cols.pop(0) + 0.5, yi, facing)
                if k % 2 == 0 or k == p.per_row - 1:
                    put(planner.POLE, x0 + cols.pop() + 0.5, yi)
        for side in "ab":
            sources.append({"kind": "belt", "position": {"x": 0.5, "y": Y["belt_" + side] + 0.5},
                            "lanes": list(p.items), "rates": {k: v for k, v in p.input_need.items() if k in p.items}})
            if fl:
                yy = 0.5 if side == "a" else Y["belt_b"] + 2.5
                sources.append({"kind": "fluid", "position": {"x": -0.5, "y": yy}, "fluid": p.fluids[0],
                                "rates": {p.fluids[0]: p.input_need[p.fluids[0]]}})
        sinks.append({"kind": "fluid", "position": {"x": width + 0.5, "y": Y["out"] + 0.5}, "item": p.output})
        return ents, sources, sinks

    if p.kind == "two-fluid-items":
        c1, c2 = p.in_cols[0], p.in_cols[1]
        Y = {"f1": 0, "tap": 1, "f2": 2, "ins": 3, "mach": 4, "io": 4 + s, "out": 5 + s, "in": 6 + s}
        row_of(planner.PIPE, Y["f1"])
        row_of(planner.PIPE, Y["f2"])
        row_of(p.belt, Y["out"], width + 1, EAST)
        row_of(p.belt, Y["in"], direction=EAST)
        free = [c for c in range(s)]
        for k in range(p.per_row):
            x0 = k * s
            put(p.machine, x0 + s / 2, Y["mach"] + s / 2, NORTH, **recipe)
            put(planner.UNDERGROUND, x0 + c1 + 0.5, Y["ins"] + 0.5, SOUTH)  # into the machine
            put(planner.UNDERGROUND, x0 + c1 + 0.5, Y["tap"] + 0.5, NORTH)  # up to fluid 1's pipe
            put(planner.PIPE, x0 + c2 + 0.5, Y["ins"] + 0.5)  # fluid 2's pipe straight in
            cols = list(free)
            # (inserters face where they pick up: the output one the machine, the long one the far belt)
            put(p.ins_out.name, x0 + cols.pop(0) + 0.5, Y["io"] + 0.5, NORTH)
            put(p.ins_in.name, x0 + cols.pop(0) + 0.5, Y["io"] + 0.5, SOUTH)
            put(planner.POLE, x0 + cols.pop() + 0.5, Y["io"] + 0.5)
        for i, name in ((0, "f1"), (1, "f2")):
            sources.append({"kind": "fluid", "position": {"x": -0.5, "y": Y[name] + 0.5}, "fluid": p.fluids[i],
                            "rates": {p.fluids[i]: p.input_need[p.fluids[i]]}})
        sources.append({"kind": "belt", "position": {"x": 0.5, "y": Y["in"] + 0.5}, "lanes": list(p.items),
                        "rates": {k: v for k, v in p.input_need.items() if k in p.items}})
        sinks.append({"kind": "belt", "position": {"x": width + 0.5, "y": Y["out"] + 0.5}, "item": p.output})
        return ents, sources, sinks

    # two-fluid line: fluid 1 (box at in_cols[0]) by underground under fluid 2's pipe
    c1, c2 = p.in_cols[0], p.in_cols[1]
    Y = {"f1_a": 0, "tap_a": 1, "f2_a": 2, "ins_a": 3, "mach_a": 4, "out_a": 4 + s, "belt": 5 + s, "out_b": 6 + s,
         "mach_b": 7 + s}
    Y["ins_b"] = Y["mach_b"] + s
    Y["f2_b"], Y["tap_b"], Y["f1_b"] = Y["ins_b"] + 1, Y["ins_b"] + 2, Y["ins_b"] + 3
    for name in ("f1_a", "f2_a", "f2_b", "f1_b"):
        row_of(planner.PIPE, Y[name])
    row_of(p.belt, Y["belt"], width + 1, EAST)
    for k in range(p.per_row):
        x0 = k * s
        for side, facing, back in (("a", NORTH, SOUTH), ("b", SOUTH, NORTH)):
            a1, a2 = (c1, c2) if side == "a" else (s - 1 - c1, s - 1 - c2)
            put(p.machine, x0 + s / 2, Y["mach_" + side] + s / 2, facing, **recipe)
            put(planner.UNDERGROUND, x0 + a1 + 0.5, Y["ins_" + side] + 0.5, back)  # into the machine
            put(planner.UNDERGROUND, x0 + a1 + 0.5, Y["tap_" + side] + 0.5, facing)  # up/down to fluid 1's pipe
            put(planner.PIPE, x0 + a2 + 0.5, Y["ins_" + side] + 0.5)  # fluid 2's pipe straight in
            yo = Y["out_" + side] + 0.5
            cols = list(range(s))
            for _ in range(p.ins_out.count):
                put(p.ins_out.name, x0 + cols.pop(0) + 0.5, yo, facing)
            if side == "a" and (k % 2 == 0 or k == p.per_row - 1):
                put(planner.POLE, x0 + cols.pop() + 0.5, yo)
    for side in "ab":
        for i, name in ((0, "f1_"), (1, "f2_")):
            sources.append({"kind": "fluid", "position": {"x": -0.5, "y": Y[name + side] + 0.5}, "fluid": p.fluids[i],
                            "rates": {p.fluids[i]: p.input_need[p.fluids[i]]}})
    sinks.append({"kind": "belt", "position": {"x": width + 0.5, "y": Y["belt"] + 0.5}, "item": p.output})
    return ents, sources, sinks


def describe(p: FluidPlan):
    ins = p.ins_in or p.ins_out
    what = {"fluid-out": "fluid output into a pipe", "two-fluid": "two fluid inputs, items out",
            "two-fluid-items": "two fluids and items in, items out",
            "fluid-fluid": "fluid in, fluid out", "fluid-in": "fluid in, items out"}[p.kind]
    return (f"{p.output}: {p.machines} x {p.machine}, {p.expected:.2f}/s; {what}"
            + (f"; inserter {ins.count} x {ins.name}" if ins else ""))


# ---------------------------------------------------------------------------------------------------------------
# all-fluid recipes with several outputs (advanced oil processing: crude oil + water -> heavy, light oil, gas)
#     output 0 pipe          each output fluid has its own pipe row; the nearest one is reached by a plain pipe,
#     (riser)                the others by a pipe-to-ground pair passing under the rows in between
#     output 1 pipe
#     ...                    the gap row above the machines also holds a pole per machine
#     machine row            facing north: outputs on the north edge, inputs on the south edge
#     input 0 pipe ...       mirrored below

@dataclass
class MultiPlan:
    recipe: str
    machine: str
    size: int
    per_row: int
    craft_rate: float
    target: str  # the output the line is sized for
    outputs: dict  # fluid -> per second for the whole line
    inputs: dict  # fluid -> per second for the whole line
    in_cols: dict  # fluid -> column (as placed) of its input box, on the machine's south side
    out_cols: dict  # fluid -> column of its output box, on the north side
    notes: list = field(default_factory=list)
    facing: int = NORTH  # turned round (SOUTH) when the machine has its inputs on top (a chemical plant)
    items: list = field(default_factory=list)  # item ingredients on the belt under the machines, [left, right] lane
    ins_name: str = None  # the long-handed inserters taking them off it
    ins_cols: list = field(default_factory=list)  # their columns under each machine
    belt: str = "transport-belt"

    @property
    def machines(self):
        return self.per_row


LONG_INSERTER = "long-handed-inserter"
ITEM_MARGIN = 0.8  # of an inserter's swing rate, for picking off a moving belt


def plan_multi(data, recipe, machine, target, target_rate, productivity=0.0, belt="transport-belt"):
    """an all-fluid recipe with up to 3 fluid outputs, sized for `target_rate` of the output `target`"""
    r = data.raw["recipe"].get(recipe)
    m = data.raw["assembling-machine"].get(machine) or data.raw.get("furnace", {}).get(machine)
    if not r or not m:
        raise PlanError(f"unknown recipe or machine ({recipe}, {machine})")
    ings, res = r.get("ingredients", []), r.get("results") or []
    if any(o.get("type", "item") != "fluid" for o in res):
        raise PlanError(f"{recipe}: only recipes making fluids have a multi-output layout")
    item_ings = [i for i in ings if i.get("type", "item") != "fluid"]
    ings = [i for i in ings if i.get("type", "item") == "fluid"]
    if len(item_ings) > 2:
        raise PlanError(f"{recipe}: up to 2 items in (one belt)")
    if not (len(ings) <= 3 and 1 <= len(res) <= 3) or not (ings or item_ings):
        raise PlanError(f"{recipe}: up to 3 fluids in and 3 out")
    size = _size(m)
    # inputs below, outputs above: as it is, or turned round
    facing = NORTH
    ins_all, outs_all = _cols(m, size, "input", SOUTH), _cols(m, size, "output", NORTH)
    if len(ins_all) < len(ings) or len(outs_all) < len(res):
        facing = SOUTH
        ins_all = [size - 1 - c for c in _cols(m, size, "input", NORTH)]
        outs_all = [size - 1 - c for c in _cols(m, size, "output", SOUTH)]
    in_cols = {}
    for k, i in enumerate(ings):
        idx = i.get("fluidbox_index", k + 1)
        if idx > len(ins_all):
            raise PlanError(f"{machine}: its fluid inputs are not all on the south edge")
        in_cols[i["name"]] = ins_all[idx - 1]
    out_cols = {}
    for k, o in enumerate(res):
        idx = o.get("fluidbox_index", k + 1)
        if idx > len(outs_all):
            raise PlanError(f"{machine}: its fluid outputs are not all on the north edge")
        out_cols[o["name"]] = outs_all[idx - 1]
    if len(set(in_cols.values())) < len(in_cols) or len(set(out_cols.values())) < len(out_cols):
        raise PlanError(f"{machine}: two fluids share a connection column")
    craft_rate = m["crafting_speed"] / r.get("energy_required", 0.5)
    t = next((o for o in res if o["name"] == target), None)
    if not t:
        raise PlanError(f"{recipe} does not make {target}")
    looped = {i["name"]: _amount(i) for i in ings if any(o["name"] == i["name"] for o in res)}
    per_machine = craft_rate * (_amount(t) * (1 + productivity) - looped.get(target, 0))
    if per_machine <= 0:
        raise PlanError(f"{recipe} eats as much {target} as it makes")
    max_use, ins_name, ins_rate, free = 1.0, None, 0, []
    if item_ings:  # long-handed inserters under each machine, between its fluid inputs, off a belt 2 tiles away
        ins = data.raw.get("inserter", {}).get(LONG_INSERTER)
        if not ins:
            raise PlanError(f"{recipe} takes items: needs {LONG_INSERTER}")
        ins_name = LONG_INSERTER
        ins_rate = 60 * ins.get("rotation_speed", 0.02) * ITEM_MARGIN  # items/s with a hand of one
        free = [c for c in range(size) if c not in set(in_cols.values())]
        if not free:
            raise PlanError(f"{machine}: no room under it for an inserter")
        item_full = craft_rate * sum(_amount(i) for i in item_ings)
        max_use = min(1.0, len(free) * ins_rate / item_full)
    n = max(1, math.ceil(target_rate / (per_machine * max_use) - 1e-9))
    use = target_rate / (n * per_machine)
    crafts = craft_rate * use * n
    outputs = {o["name"]: crafts * _amount(o) * (1 + productivity) for o in res}
    inputs = {i["name"]: crafts * _amount(i) for i in ings}
    items, ins_cols = [], []
    if item_ings:
        inputs.update({i["name"]: crafts * _amount(i) for i in item_ings})
        items = [i["name"] for i in item_ings] if len(item_ings) == 2 else [item_ings[0]["name"]] * 2
        k = min(len(free), math.ceil(crafts / n * sum(_amount(i) for i in item_ings) / ins_rate - 1e-9))
        mid = (size - 1) / 2
        ins_cols = sorted(sorted(free, key=lambda c: abs(c - mid))[:max(1, k)])
    notes = [f"each machine runs at {use:.0%} of full speed"] if use < 0.5 else []
    for f in looped:
        notes.append(f"it eats {f} too: pipe {inputs[f] * 60:.0f}/min of its {f} output back to its {f} input "
                     f"(and start it with some {f} from elsewhere)")
    if max_use < 1:
        notes.append(f"{len(free)} {ins_name} per machine can't keep it running at full speed: "
                     f"{n} machines at {use:.0%} instead")
    return MultiPlan(recipe, machine, size, n, craft_rate, target, outputs, inputs, in_cols, out_cols, notes, facing,
                     items, ins_name, ins_cols, belt)


def layout_multi(p: MultiPlan):
    """-> (entities, sources, sinks); a sink per output fluid (its pipe row's east end), a source per input"""
    s, n = p.size, p.per_row
    width = n * s
    outs = sorted(p.out_cols, key=lambda f: p.out_cols[f])
    ins = sorted(p.in_cols, key=lambda f: p.in_cols[f])
    K = len(outs)
    y_top = 2 * K  # the machines' top row
    y_bot = y_top + s  # the first row under them
    ents, sources, sinks = [], [], []

    def put(name, x, y, direction=None, **kw):
        e = {"name": name, "position": {"x": x + 0.5, "y": y + 0.5}}
        if direction is not None:
            e["direction"] = direction
        e.update(kw)
        ents.append(e)
    out_row = {f: 2 * j for j, f in enumerate(outs)}
    # with items: inserters, a row for poles, the item belt, then the fluid rows (reached under the belt)
    first_in = y_bot + (4 if p.items else 1)
    in_row = {f: first_in + 2 * j for j, f in enumerate(ins)}
    if p.items:
        y_belt = y_bot + 2
        for x in range(width):
            put(p.belt, x, y_belt, EAST)
        sources.append({"kind": "belt", "position": {"x": 0.5, "y": y_belt + 0.5}, "lanes": list(p.items),
                        "rates": {i: p.inputs[i] for i in set(p.items)}})
    for f, y in out_row.items():
        for x in range(width + 1):
            put(planner.PIPE, x, y)
        sinks.append({"kind": "fluid", "position": {"x": width + 0.5, "y": y + 0.5}, "item": f,
                      "rates": {f: p.outputs[f]}})
    for f, y in in_row.items():
        for x in range(width):
            put(planner.PIPE, x, y)
        sources.append({"kind": "fluid", "position": {"x": -0.5, "y": y + 0.5}, "fluid": f,
                        "rates": {f: p.inputs[f]}})
    used = set(p.out_cols.values())
    pole_col = next((c for c in range(s) if c not in used), None)
    if pole_col is None:
        raise PlanError(f"{p.machine}: no free column above it for a power pole")
    for k in range(n):
        x0 = k * s
        e = {"name": p.machine, "position": {"x": x0 + s / 2, "y": y_top + s / 2}, "direction": p.facing,
             "recipe": p.recipe}
        ents.append(e)
        put(planner.POLE, x0 + pole_col, y_top - 1)
        for f, c in p.out_cols.items():
            if out_row[f] == y_top - 2:  # the nearest row: a plain pipe
                put(planner.PIPE, x0 + c, y_top - 1)
            else:  # under the rows in between (a pipe-to-ground faces its open side)
                put(planner.UNDERGROUND, x0 + c, y_top - 1, SOUTH)
                put(planner.UNDERGROUND, x0 + c, out_row[f] + 1, NORTH)
        for c in p.ins_cols:  # picking from the belt (south), dropping into the machine
            put(p.ins_name, x0 + c, y_bot, SOUTH)
        if p.items:
            put(planner.POLE, x0 + s // 2, y_bot + 1)
        for f, c in p.in_cols.items():
            if in_row[f] == y_bot + 1:
                put(planner.PIPE, x0 + c, y_bot)
            else:
                put(planner.UNDERGROUND, x0 + c, y_bot, NORTH)
                put(planner.UNDERGROUND, x0 + c, in_row[f] - 1, SOUTH)
    return ents, sources, sinks


# ---------------------------------------------------------------------------------------------------------------
# burning a fluid nobody needs: a row of flare stacks (any machine with a no-result recipe eating that fluid)

def flare_for(data, fluid, prefer=None):
    """(machine, recipe, fluid per second one machine burns, prototype type) for burning `fluid`, or None.
    Electric (or burner) machines only - the unpowered copies some mods add are skipped - the one unlocked
    earliest first (as machines are chosen), then the smallest"""
    depth = _unlock_depth(data)
    best = None
    for rn, r in data.raw["recipe"].items():
        ings = r.get("ingredients") or []
        if r.get("results") or len(ings) != 1 or ings[0].get("type") != "fluid" or ings[0]["name"] != fluid:
            continue
        cat = r.get("category", "crafting")
        for t in ("furnace", "assembling-machine"):
            for mn, m in data.raw.get(t, {}).items():
                if m.get("hidden") or cat not in m.get("crafting_categories", []):
                    continue
                if m.get("energy_source", {}).get("type") not in ("electric", "burner"):
                    continue  # unpowered copies (bioluminescent-, harene-infused-) and helper entities
                if not _cols(m, _size(m), "input", SOUTH):
                    continue
                rate = m.get("crafting_speed", 1) / max(r.get("energy_required", 0.5), 1e-3) * _amount(ings[0])
                key = (mn != prefer, depth.get(mn, 999), t != "furnace", _size(m), -rate, mn)
                if best is None or key < best[0]:
                    best = (key, (mn, rn, rate, t))
    return best[1] if best else None


_DEPTH = {}


def _unlock_depth(data):
    if id(data) not in _DEPTH:
        from bpgen import base
        _DEPTH.clear()
        _DEPTH[id(data)] = base.unlock_depth(data)
    return _DEPTH[id(data)]


def layout_flare(data, fluid, rate, prefer=None):
    """-> (entities, sources, sinks, notes) for burning `rate` per second of `fluid`"""
    f = flare_for(data, fluid, prefer)
    if not f:
        raise PlanError(f"nothing in this pack burns {fluid}")
    machine, recipe, per, kind = f
    m = data.raw[kind][machine]
    s = _size(m)
    n = max(1, math.ceil(rate / per - 1e-9))
    width = n * s
    ents = []
    for k in range(n):
        e = {"name": machine, "position": {"x": k * s + s / 2, "y": s / 2}, "direction": NORTH}
        if kind != "furnace":
            e["recipe"] = recipe
        ents.append(e)
    for x in range(width):
        ents.append({"name": planner.PIPE, "position": {"x": x + 0.5, "y": s + 0.5}})
    if m.get("energy_source", {}).get("type") == "electric":
        for x in range(0, width, 6):
            ents.append({"name": planner.POLE, "position": {"x": x + 0.5, "y": s + 1.5}})
    sources = [{"kind": "fluid", "position": {"x": -0.5, "y": s + 0.5}, "fluid": fluid, "rates": {fluid: rate}}]
    return ents, sources, [], [f"{n} x {machine} burn {rate * 60:.0f}/min {fluid}"]
