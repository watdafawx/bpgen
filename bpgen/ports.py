"""What a machine offers a row: its fluid connections as ports on its four edges, as placed facing some way.

The line solver (rowspec.py) asks a machine, for each of the four facings, which fluid boxes open on which edge and
where along it, and whether machines placed touching in a row would join an output to a neighbour's input. The
prototype's fluid_boxes[].pipe_connections[] are read once per facing here; nothing else in bpgen reads them.

    ports = Ports.of(proto, facing)                 # facing: NORTH (0), EAST (4), SOUTH (8), WEST (12)
    ports.w, ports.h                                # tiles along the row (x) and across it (y), as placed
    ports.on(NORTH, ("input", "input-output"))      # [Port], fluid box order: the inputs opening from the north edge
    ports.joins()                                   # touching neighbours would join an input to an output

A Port is one normal pipe connection sitting on an edge tile and opening outwards from it; `at` is its tile along
that edge (0 = west for the north and south edges, 0 = north for the east and west ones). Connections not on an
edge, between tiles, or of another connection_type (underground, linked) are kept in `stray` and never used.
"""
from dataclasses import dataclass, field

from bpgen import geom

NORTH, EAST, SOUTH, WEST = 0, 4, 8, 12
FACINGS = (NORTH, SOUTH, EAST, WEST)  # the order the planners try: as is, turned round, then a quarter either way
INPUT, OUTPUT, IO = "input", "output", "input-output"
INPUTS = (INPUT, IO)
OUTPUTS = (OUTPUT, IO)


@dataclass(frozen=True)
class Port:
    box: int  # index of its fluid box in the prototype (recipes fill inputs and outputs in box order)
    kind: str  # input | output | input-output (the box's production_type)
    edge: int  # NORTH / EAST / SOUTH / WEST as placed: the edge it opens from
    at: int  # tile along that edge, as placed (0 = west on N/S edges, 0 = north on E/W edges)
    x: float = 0.0  # connection position as placed (machine centre 0, 0)
    y: float = 0.0
    snapped: bool = False  # the connection sits between two tiles; `at` is the western / northern one

    @property
    def is_input(self):
        return self.kind in INPUTS

    @property
    def is_output(self):
        return self.kind in OUTPUTS


@dataclass
class Ports:
    name: str
    facing: int
    w: int  # tiles along the row (x), as placed
    h: int  # tiles across the row (y), as placed
    ports: list = field(default_factory=list)  # Port, fluid box order then connection order
    stray: list = field(default_factory=list)  # (box, kind, x, y, direction) connections not usable as ports

    @classmethod
    def of(cls, proto, facing=NORTH):
        """the machine's ports with it placed facing `facing`"""
        w, h = geom.dims(proto)
        if facing in (EAST, WEST):
            w, h = h, w
        half_w, half_h = (w - 1) / 2, (h - 1) / 2
        out = cls(proto["name"], facing, w, h)
        for i, fb in enumerate(proto.get("fluid_boxes") or []):
            kind = fb.get("production_type") or INPUT
            for pc in fb.get("pipe_connections") or []:
                if pc.get("connection_type", "normal") != "normal" or "position" not in pc:
                    out.stray.append((i, kind, None, None, pc.get("direction")))  # (linked or underground)
                    continue
                x, y, d = geom.turn_conn(pc, facing)
                if d == NORTH and y == -half_h:
                    at = x + half_w
                elif d == SOUTH and y == half_h:
                    at = x + half_w
                elif d == EAST and x == half_w:
                    at = y + half_h
                elif d == WEST and x == -half_w:
                    at = y + half_h
                else:
                    out.stray.append((i, kind, x, y, d))
                    continue
                if not 0 <= at < (w if d in (NORTH, SOUTH) else h):
                    out.stray.append((i, kind, x, y, d))
                    continue
                # (a connection between two tiles, a mod's 4x4 with one at x 0: taken on the western / northern of
                # the two, as the planners always have)
                out.ports.append(Port(i, kind, d, int(at), x, y, at != int(at)))
        return out

    @classmethod
    def all(cls, proto):
        """{facing: Ports} for the four facings"""
        return {f: cls.of(proto, f) for f in FACINGS}

    def on(self, edge, kinds=None):
        """the ports opening from `edge`, of the given production types (all when None), fluid box order"""
        return [p for p in self.ports if p.edge == edge and (kinds is None or p.kind in kinds)]

    def first_on(self, edge, kinds=None):
        """as on(), one port per fluid box (its first connection on that edge), fluid box order"""
        seen, out = set(), []
        for p in self.on(edge, kinds):
            if p.box not in seen:
                seen.add(p.box)
                out.append(p)
        return out

    def boxes(self, kinds):
        """fluid box indexes of the given production types, prototype order (the recipe's n-th fluid of that kind
        goes into the n-th of them, unless it names a fluidbox_index)"""
        seen = []
        for p in self.ports + [(s[0], s[1]) for s in self.stray]:
            box, kind = (p.box, p.kind) if isinstance(p, Port) else p
            if kind in kinds and box not in seen:
                seen.append(box)
        return sorted(seen)

    def of_box(self, box, edge=None):
        """the ports of one fluid box (on one edge when given)"""
        return [p for p in self.ports if p.box == box and (edge is None or p.edge == edge)]

    def joins(self, other=None, used_outputs=None):
        """machines placed touching in a row, this one west of `other` (itself when None, both placed alike): does
        a connection on this one's east edge meet one on the other's west edge, an output and an input? Two inputs
        only share, two outputs pass nothing, but an output would flow into the neighbour's input.
        used_outputs: the output boxes the recipe fills (None: all); an output box it leaves empty pushes nothing"""
        other = other or self

        def side(P, edge):
            return {(p.at, p.kind == OUTPUT) for p in P.on(edge)
                    if not (p.kind == OUTPUT and used_outputs is not None and p.box not in used_outputs)}
        east, west = side(self, EAST), side(other, WEST)
        return any(a1 == a2 and o1 != o2 for a1, o1 in east for a2, o2 in west)

    def edges(self, kinds=None):
        """the edges with a port of the given kinds, as a set"""
        return {p.edge for p in self.ports if kinds is None or p.kind in kinds}

    def __repr__(self):
        ps = ", ".join(f"{p.kind[0]}{p.box}@{'NESW'[p.edge // 4]}{p.at}" for p in self.ports)
        return f"Ports({self.name} facing {self.facing}, {self.w}x{self.h}: {ps})"
