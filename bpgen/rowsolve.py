"""The row solver: how a machine stands in a production line and what surrounds it, from the recipe's shape and
the machine's ports (ports.py) - the geometry step of planner.plan, for every line bpgen plans: which way the
machine faces, whether the machines stand in two mirrored rows or one, which edge each fluid comes in or leaves
by and through which column its riser goes, how many columns are free for inserters and a pole, where the poles
stand.

    shape = Shape.of(data, recipe, machine)            # items / fluids in, items / fluids out, fuel, machine dims
    pose = solve_general(data, shape, beacon=None)      # Pose, or SolveError saying why each facing fails
    spec = pose.spec(belts, south, needs={...})         # the RowSpec for rowlayout once rates.py set the lanes

Each facing is tried (as is, turned round, a quarter either way); the recipe's n-th fluid goes into the machine's
n-th box of its kind (or the one it names), through a port on the north or south edge, or - a box that opens east
and west on one row - through the touching machines. Touching machines may not join a used output to an input.
The best pose: two rows over one, then fluids in on the belt side, then more free columns, then the earlier
facing. rowsolve_test lays out every recipe in every machine and checks the result.
"""
from dataclasses import dataclass, field

from bpgen import geom
from bpgen.ports import EAST, FACINGS, INPUTS, NORTH, OUTPUTS, SOUTH, WEST, Ports
from bpgen.rowspec import Band, RowSpec


class SolveError(ValueError):
    """no pose lays this recipe out in this machine (a planner.PlanError at the caller)"""


@dataclass
class Shape:
    """a recipe in a machine, as the solver sees it"""
    recipe: str
    machine: str
    items: list  # item ingredients (recipe entries)
    fluids: list  # fluid ingredients
    item_outs: list  # item results with an amount
    fluid_outs: list  # fluid results
    burner: bool  # fuel comes in on a belt lane too
    fuel: str | None  # the fuel item of a burner machine
    furnace: bool
    w: int  # machine width facing north
    h: int  # machine depth facing north
    proto: dict = field(repr=False)

    @classmethod
    def of(cls, data, recipe, machine):
        from bpgen import planner
        r = data.raw["recipe"].get(recipe)
        if r is None:
            raise SolveError(f"unknown recipe {recipe}")
        m = data.raw["assembling-machine"].get(machine) or data.raw.get("furnace", {}).get(machine)
        if not m:
            raise SolveError(f"{machine} is not an assembling machine or furnace this template can use")
        if not planner.can_craft(m, r):
            raise SolveError(f"{machine} can't craft {recipe} (category {' or '.join(planner.categories(r))})")
        ings = list(r.get("ingredients") or [])
        outs = planner.results_of(r)
        w, h = geom.dims(m)
        es = m.get("energy_source", {})
        burner = es.get("type") == "burner"
        fuel = None
        if burner:
            try:
                fuel = planner.fuel_for(data, es)[0]
            except planner.PlanError:
                pass  # (solve() refuses it as the planner does)
        return cls(recipe, machine, [i for i in ings if i.get("type", "item") == "item"],
                   [i for i in ings if i.get("type") == "fluid"],
                   [x for x in outs if x.get("type", "item") == "item"], [x for x in outs if x.get("type") == "fluid"],
                   burner, fuel, m.get("type") == "furnace", w, h, m)

    @property
    def belt_items(self):
        """items coming in on belts: the ingredients, plus a burner's fuel when it isn't one of them"""
        return len({i["name"] for i in self.items} | ({self.fuel} if self.fuel else set()))


@dataclass
class Geometry:
    """how the machines stand; the RowSpec follows once the lanes are known"""
    shape: Shape
    facing: int  # row A's machine direction (planner's rotated / flipped / turn folded in)
    rows: int  # 2 mirrored, or 1 (5-6 belt items)
    size: int  # along the row
    height: int  # across the row
    fluid_col: int | None  # riser column (row A) of the one fluid, in or out
    fluid_out: bool
    slots: int  # input-side columns free for inserters (riser and pole taken)
    out_slots: int  # output-side columns free for inserters
    poles_out: bool
    beacon: str | None
    beacon_size: int
    per_machine_beacons: int
    beacon_pad: int
    rotated: bool  # a narrow machine turned so its long side faces the belts
    flipped: bool  # turned round: its fluid input was on its south side
    turn: int  # a quarter turn (its fluid connection on a side)


# ---- the general search ------------------------------------------------------------------------------------------

POLE_SUPPLY = 7  # tiles the layouts' pole powers across


@dataclass
class Fluid:
    """one of the recipe's fluids as the pose puts it: on a side of row A, through a riser column"""
    name: str
    role: str  # in | out
    col: int  # riser column (row A, as placed)
    box: int  # the machine's fluid box


@dataclass
class Pose(Geometry):
    """a Geometry with every fluid placed: the pipe bands north and south (the recipe's first fluid outermost)"""
    north_fluids: list = field(default_factory=list)
    south_fluids: list = field(default_factory=list)
    ins_band: bool = True  # items come in on belts north: an inserter row beside the machines
    out_band: bool = True  # items leave on a belt south
    shared: str = "belt"  # what two rows share: the output belt, or "pipe", the one fluid out
    pole_every: int = 2
    through_fluids: list = field(default_factory=list)  # [Fluid]: chained through touching machines, row `col`

    def spec(self, belts, south=(), fluid=None, fluid_need=0.0, byproducts=False, needs=None):
        """-> RowSpec; belts: [[near lanes], [far lanes]?]; needs: {fluid: rate per row} (fluid / fluid_need: the
        planner's one)"""
        needs = dict(needs or {})
        if fluid is not None:
            needs.setdefault(fluid, fluid_need)
        s, hs = self.size, self.height
        bs = self.beacon_size if self.beacon else 0
        two_sided = self.shape.belt_items > 4  # (the one-row layout with belts on both sides)

        def pipes(fluids, side):
            """riser row then pipe, innermost first; the shared fluid out of two rows lies against the machines"""
            bands = []
            n = len(fluids)
            # innermost first, by riser column: north of the machines the westmost riser reaches farthest out, south
            # of them the westmost reaches the nearest pipe (risers never cross; the routers expect this order)
            order = sorted(fluids, key=lambda f: -f.col if side == "" else f.col)
            for i, f in enumerate(order):
                tag = f"{side}{n - 1 - i}" if n > 1 or side else ""
                shared = side == "s" and self.rows == 2 and self.shared == "pipe" and i == 0
                if not shared:
                    bands.append(Band("riser", "tap" + tag))
                bands.append(Band("pipe", "pipe" + tag, f.role, fluid=f.name,
                                  rate=needs.get((f.role, f.name), needs.get(f.name, 0.0)),
                                  riser=None if shared else f.col,  # (lying against the machines: no riser)
                                  extra=f.role == "out", shared=shared))
            return bands
        north = []
        if self.ins_band:
            north.append(Band("ins", "ins", "in"))
            north.append(Band("belt", "near", "near", list(belts[0])))
            if len(belts) == 2 or two_sided:
                north.append(Band("belt", "far", "far", list(belts[1]) if len(belts) == 2 else [None, None]))
        north += pipes(self.north_fluids, "")
        south_bands = []
        if self.out_band:
            south_bands.append(Band("out", "io" if two_sided else "out", "out"))
            south_bands.append(Band("belt", "out" if two_sided else "belt", "out", extra=True,
                                    shared=self.rows == 2 and self.shared == "belt",
                                    sink="" if self.ins_band else "item"))  # (pipe-fed lines name their output)
            if two_sided:
                south_bands.append(Band("belt", "south", "south", list(south) if any(south) else [None, None]))
        south_bands += pipes(self.south_fluids, "s")
        # poles: in the inserter slots as the planner has them; else in the output row; else in a riser row north
        if not self.ins_band:
            target = south_bands[0] if self.out_band else next((b for b in north if b.kind == "riser"), None)
            if target is not None:
                target.pole = "slot-last" if target.kind == "out" else "first-free"
                target.pole_every = self.pole_every
                target.pole_last = True
                # (two rows about the output belt: row A's pole reaches row B's inserters across it)
                target.pole_b = not (target.kind == "out" and self.rows == 2 and self.shared == "belt")
        plain = self.rows == 2 and self.ins_band and not self.south_fluids  # (the planner's two-row belt line)
        through = [(f.name, needs.get(("in", f.name), needs.get(f.name, 0.0)), f.col) for f in self.through_fluids]
        return RowSpec(self.rows, self.facing, s, hs, north, south_bands, machine_sources=through,
                       pitch=s + bs if self.rows == 2 else 0,
                       shift=self.beacon_pad * (s + bs) + bs, tail=self.beacon_pad * (s + bs), beacon=self.beacon,
                       beacon_size=bs, beacon_pad=self.beacon_pad,
                       pole_every=0 if self.beacon or not self.ins_band else 2,
                       pole_in_tap=bool(self.north_fluids) and self.ins_band and not self.beacon,
                       poles_out=self.poles_out,
                       power_up=not plain or max(s, hs) >= 5 or bool(self.turn) or self.fluid_out or byproducts,
                       sort_tail_bottom="south" if two_sided else "last")


def solve_general(data, shape: Shape, beacon=None, beacon_proto=None, one_row=False):
    """-> Pose: how this recipe's machines stand in a row, trying each facing (as is, turned round, a quarter either
    way) and keeping the best: two mirrored rows over one, the fluids in on the belt side (north), more free
    columns, the earlier facing. SolveError says why every facing failed."""
    from bpgen import planner
    m, machine = shape.proto, shape.machine
    if not shape.item_outs and not shape.fluid_outs:
        raise SolveError(planner.no_output(data.raw["recipe"][shape.recipe]))
    if shape.burner and not shape.fuel:
        raise SolveError("no fuel item found for this burner")
    if len(shape.items) > 6:
        raise SolveError(f"{len(shape.items)} item ingredients: this template handles 1-6 (5-6: one row, belts on both sides)")
    wet = bool(shape.fluids or shape.fluid_outs)
    two_sided = shape.belt_items > 4
    if two_sided and beacon:
        raise SolveError(f"{shape.belt_items} belt items: their one-row layout has no beacons yet")
    if beacon and shape.w != shape.h:  # (a narrow machine lies with its long side along the row)
        raise SolveError(f"{machine} is {max(shape.w, shape.h)}x{min(shape.w, shape.h)}: beacons are laid out for "
                         f"square machines")
    ports = Ports.all(m)
    why, found = [], []
    for facing in FACINGS:
        P = ports[facing]
        size, height = P.w, P.h
        if beacon and size != height:
            why.append(f"{machine} is {size}x{height}: beacons are laid out for square machines")
            continue
        placed = _place_fluids(shape, P, why, facing)
        if placed is None:
            continue
        if wet and P.joins(used_outputs={f.box for side, f in placed if f.role == "out"}):
            why.append(f"facing {facing}: machines side by side would join fluid boxes")
            continue
        north_f = [f for side, f in placed if side == "n"]
        south_f = [f for side, f in placed if side == "s"]
        through_f = [f for side, f in placed if side == "t"]
        ins_band = shape.belt_items > 0
        out_band = bool(shape.item_outs)
        # a pipe lying against the machines' south edges reaches every port there: only when all of them are
        # outputs the recipe doesn't feed (an unused output box takes nothing; an input box would drink from it)
        in_boxes = {f.box for side, f in placed if f.role == "in"}
        edge_free = all(p.is_output and p.box not in in_boxes for p in P.on(SOUTH))
        if two_sided or through_f or one_row:  # (a chain through the machines runs along one row; one_row: asked)
            rows, shared = 1, "belt"
        elif out_band and not south_f:
            rows, shared = 2, "belt"  # (two rows share the output belt)
        elif not out_band and len(south_f) == 1 and south_f[0].role == "out" and edge_free:
            rows, shared = 2, "pipe"  # (two rows share the one output pipe, against both)
        else:
            rows, shared = 1, "belt"
        if beacon and not (rows == 2 and shared == "belt" and ins_band and out_band and not south_f
                           and len(north_f) <= 1):
            # (beacons stand between the machines with poles above them: laid out for two rows about the output
            # belt, belts and at most one pipe north)
            why.append(f"facing {facing}: beacons are laid out for two rows about an output belt with one pipe at most")
            continue
        rotated = facing in (EAST, WEST) and shape.w != shape.h
        # a riser takes a column; so does a pole, unless it stands in the riser row or above a beacon
        poles_out = not beacon and not wet and height <= 2 and size <= 2 and shape.belt_items > 2
        slots = size - len(north_f) - (1 if not beacon and not north_f and not poles_out else 0)
        if ins_band and slots < 1:
            why.append(f"facing {facing}: no column left for an inserter beside {len(north_f)} pipe risers")
            continue
        out_slots = size - len(south_f) - (0 if beacon else 1)
        if out_band and out_slots < 1:
            why.append(f"facing {facing}: no column left for an output inserter beside {len(south_f)} pipe risers")
            continue
        turn = facing if facing in (EAST, WEST) and not rotated else 0
        flipped = facing == SOUTH
        first = (north_f + south_f)[0] if north_f or south_f else None
        found.append(Pose(shape, facing, rows, size, height, first.col if first else None, bool(shape.fluid_outs),
                          slots, out_slots, poles_out, beacon, 0, 0, 0, rotated, flipped, turn,
                          north_f, south_f, ins_band, out_band, shared, 2 if 2 * size <= POLE_SUPPLY else 1,
                          through_f))
    if not found:
        raise SolveError("; ".join(why) if why else f"{machine}: no facing lays {shape.recipe} out")
    # (one_row: a starter base's block - its fluids in on the south side, out on the north, the way its router
    # has always connected them)
    inward = 1 if one_row else -1
    found.sort(key=lambda g: (-g.rows, inward * sum(1 for f in g.north_fluids if f.role == "in"), -g.slots,
                              FACINGS.index(g.facing)))
    best = found[0]
    if beacon:
        if beacon_proto is None:
            raise SolveError(f"unknown beacon {beacon}")
        best.beacon_size = geom.dims(beacon_proto)[0]
        if best.beacon_size > best.size:
            raise SolveError(f"{beacon} is bigger than {machine}")
        best.per_machine_beacons, best.beacon_pad = planner.beacon_reach(m, beacon_proto, best.size, best.beacon_size)
        if not best.per_machine_beacons:
            raise SolveError(f"{beacon} doesn't reach neighbouring machines")
    return best


def box_slots(k, n):
    """which of a machine's n fluid boxes of a kind (0-based, prototype order) the game gives a recipe's k fluids of
    that kind, in recipe order: spread over them, first and last first - 2 of 3 boxes: the 1st and the 3rd (the
    cryogenic plant's lithium, sulfur, fluoroketone; read from the game for every vanilla and Space Age machine)"""
    if k <= 1:
        return [0] * k
    return [round(i * (n - 1) / (k - 1)) for i in range(k)]


def _place_fluids(shape, P, why, facing):
    """[(side, Fluid)]: the recipe's fluids into the machine's boxes (the box it names, else as the game spreads them:
    box_slots), each through a port on the north or south edge; None when one can't be"""
    in_boxes, out_boxes = P.boxes(INPUTS), P.boxes(OUTPUTS)
    placed, used = [], set()
    for role, fluids, boxes in (("in", shape.fluids, in_boxes), ("out", shape.fluid_outs, out_boxes)):
        if len(fluids) > len(boxes):
            why.append(f"facing {facing}: {P.name} has {len(boxes)} {role}put fluid boxes for {len(fluids)} fluids")
            return None
        slots = box_slots(len(fluids), len(boxes))
        for k, fl in enumerate(fluids):
            idx = fl.get("fluidbox_index")
            box = boxes[min(idx, len(boxes)) - 1] if idx else boxes[slots[k]]
            if box in used:
                why.append(f"facing {facing}: {fl['name']} has no {role}put fluid box of its own on {P.name}")
                return None
            used.add(box)
            north_p, south_p = P.of_box(box, NORTH), P.of_box(box, SOUTH)
            # fluids in go north, the belt side; a fluid out north too while items leave on a belt, else south
            order = ((north_p, "n"), (south_p, "s")) if role == "in" or shape.item_outs else ((south_p, "s"), (north_p, "n"))
            pick = next(((side, ps[0].at) for ps, side in order if ps), None)
            if pick is None and role == "in":  # (opening east and west on one row: it chains along the touching
                # machines, fed at the row's west end)
                rows_ew = {p.at for p in P.of_box(box, EAST)} & {p.at for p in P.of_box(box, WEST)}
                if rows_ew:
                    pick = ("t", min(rows_ew))
            if pick is None:
                why.append(f"facing {facing}: {fl['name']}'s fluid box opens on no north or south edge")
                return None
            placed.append((pick[0], Fluid(fl["name"], role, pick[1], box)))
    return placed
