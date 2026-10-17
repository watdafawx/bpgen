"""What a production line looks like, as a description rowlayout.py turns into entities.

A line is a row of machines along x (or two rows mirrored about a shared band), with bands of infrastructure
stacked north and south of the machines: belts, pipes with their risers, the inserter rows, poles, beacons. A
RowSpec says which bands there are and in what order (from the machine outwards), how the machines stand (facing,
pitch, pattern) and where the poles go. Everything the layout needs that isn't a count comes from the spec; the
counts (machines per row, inserters, lanes, rates) come from the plan.

The row solver (rowsolve.Pose.spec) builds a line's spec; RowSpec.from_direct_plan builds a direct-insertion line's
(its machines in a pattern of two recipes).
"""
from dataclasses import dataclass, field

NORTH, EAST, SOUTH, WEST = 0, 4, 8, 12


@dataclass
class Ins:
    """inserters an ins/out band names itself (the fluid lines'), `count` of `name`. reach "in": they face the machine
    (output inserters, picking up from it); "out": they face away (picking up from the belt beyond). They stand in the
    band's free columns, from the first or, with `end`, from the last; `cols` (row A, as placed) names theirs instead"""
    name: str
    count: int
    reach: str  # "in" | "out"
    end: bool = False
    cols: list | None = None


@dataclass
class Band:
    """one row of stuff beside the machines (per side, mirrored for row B)"""
    kind: str  # belt | pipe | ins | out | riser | gap (an empty row between stacked pipes)
    name: str  # row name: Y[name + "_a"] / Y[name + "_b"] (shared bands: Y[name])
    role: str = ""  # belt: near | far | out | south; pipe: in | out; ins/out: in | out
    lanes: list = field(default_factory=list)  # belt: [left item, right item] (None = empty)
    fluid: str | None = None  # pipe: the fluid
    rate: float = 0.0  # pipe: per row (source / sink rate); belt(out) unused
    riser: int | None = None  # pipe: machine column of the riser into this pipe (as placed, row A)
    extra: bool = False  # belt / pipe: one tile longer than the row (the output passes the end)
    shared: bool = False  # between the two rows: one band, not mirrored
    height: int = 1
    # poles standing in this band, per machine (None: none from this band; the RowSpec's pole rules still apply):
    # "slot-last": the last free column; "col": column pole_col (as placed, row A); "first-free": the first column
    # that holds no riser
    pole: str | None = None
    pole_col: int = 0
    pole_every: int = 1  # every n-th machine (1: each)
    pole_last: bool = False  # the last machine too, whatever pole_every says
    pole_b: bool = True  # row B's copy of the band has the poles too
    inserters: list = field(default_factory=list)  # ins/out band: [Ins] (empty: the plan's own ins_near / ins_far / ins_out)
    # what a sink says: belt: "" the position only, "item" and the output item; pipe: "" item and rate, "item" the
    # item only, "rates" the item and rates {item: rate}
    sink: str = ""


@dataclass
class RowSpec:
    rows: int  # 1 or 2 (mirrored about the last south band, which is then shared)
    facing: int  # machine direction, row A (row B: + 8)
    size: int  # machine width along the row
    height: int  # machine depth across the row
    north: list  # [Band], machine outwards
    south: list  # [Band], machine outwards
    pitch: int = 0  # machine to machine (0: touching, = size)
    shift: int = 0  # free tiles west of the first machine (beacons past the end)
    tail: int = 0  # free tiles east of the last machine (beacons)
    beacon: str | None = None  # beacons between machines (and past both ends), poles above them
    beacon_size: int = 0
    beacon_pad: int = 0
    pole_every: int = 2  # a pole every n machines (0: none from the slots; power_up adds them)
    pole_in_tap: bool = False  # the input-side pole stands in the riser row (col size-1, or 0 when the riser is there)
    poles_out: bool = False  # poles in both rows' output bands, input slots all free
    power_up: bool = False  # run power.power_up afterwards (wide / turned machines outgrow the slot poles)
    byproducts_total: bool = False  # the plan's byproducts are per second for the whole line (the fluid lines')
    machine_sources: list = field(default_factory=list)  # [(fluid, rate, row)]: fluid fed into the machine row itself
    sort_tail_bottom: str = "last"  # sort_tail's bottom row: "last" (the layout's last row) or a band name
    # direct insertion: the machines along a row, one letter each - "E" the line's recipe, "C" the made item's
    # (made_machine, made_recipe); a C beside an E hands its output over through the gap between them
    pattern: str = ""
    made_machine: str | None = None
    made_recipe: str | None = None

    @property
    def width_of(self):
        return self.pitch or self.size

    @classmethod
    def from_direct_plan(cls, p):
        """the spec direct.layout lays out for a direct.DirectPlan: the planner's two rows, a gap after each machine,
        C machines fed from the near belt, E machines from the far belt and from the C beside them"""
        north = [Band("ins", "ins", "in"), Band("belt", "near", "near", list(p.belts[0]))]
        if len(p.belts) > 1:
            north.append(Band("belt", "far", "far", list(p.belts[1])))
        south = [Band("out", "out", "out"), Band("belt", "belt", "out", extra=True, shared=True)]
        return cls(2, NORTH, p.size, p.size, north, south, pitch=p.size + 1, pole_every=2,
                   pattern=p.unit * p.units, made_machine=p.made_machine, made_recipe=p.made_recipe)
