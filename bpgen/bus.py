"""Main-bus geometry for compose_base: belt columns running south, with taps that cross the other columns underground.

Every item that goes from one block to another (and every raw input) has a column of its own, side by side
(column i at x = 2 * i; the odd x between holds a splitter's second tile). A column starts at its producer's row
(a made item's column is fed by a belt from the block's output; a raw one starts at the top, where the player brings
the belt in) and ends at its last consumer. A consumer's feed leaves the column by a splitter (the last one by a turn)
and runs east along its own row R, crossing every column on the way: those dive underground for that one row.

  column c        |   |   |   |
  row R-1         S   I   I   I      S: splitter on column c (its east half is the spare x+1), I: underground entrance
  row R           | ->->->->->->     the tap's leg runs east on the surface, over the dived columns
  row R+1         |   o   o   o      o: underground exit

Taps are at least GAP rows apart (so no two corridors or splitters meet) and clear of the head of every column
east of theirs (a column that starts inside a corridor couldn't dive).
"""
from bpgen.planner import EAST, SOUTH

GAP = 3  # rows between two taps
HEAD_TO_TAP = 3  # a column's first tap is this many rows below its head


class Column:
    def __init__(self, idx, kind, head, x, name):
        self.idx, self.kind, self.head, self.x, self.name = idx, kind, head, x, name
        self.taps = []  # [row]
        self.rank = {}  # row -> how much its consumer matters (the packs, then the intermediates, then the mall)

    @property
    def last(self):
        return max(self.taps)


def alloc_rows(legs, cols):
    """give every leg a row for its tap: as near its wanted row as the spacing rules allow.
    legs: [{"col": Column, "want": row}]; sets leg["row"] and appends it to the column's taps"""
    used = []
    for leg in sorted(legs, key=lambda l: (l["want"], l["col"].idx)):
        c = leg["col"]
        east_heads = [k.head for k in cols if k.idx > c.idx]
        lo = c.head + HEAD_TO_TAP
        want = max(leg["want"], lo)
        for off in range(0, 100_000):
            hit = None
            for R in ((want + off, want - off) if off else (want,)):
                if R < lo or any(abs(R - r) < GAP for r in used) or any(abs(R - h) < GAP for h in east_heads):
                    continue
                hit = R
                break
            if hit is not None:
                break
        leg["row"] = hit
        used.append(hit)
        c.taps.append(hit)
        c.rank[hit] = leg.get("rank", 0)


def build(cols, belt, ug, splitter, x_end):
    """-> (entities, underground spans). Entities in tile coordinates of the bus; legs' belts run from the column
    to x_end - 1 (the router carries on from x_end)"""
    ents, spans = [], set()

    def put(name, x, y, d, **kw):
        ents.append(dict({"name": name, "position": {"x": x + 0.5, "y": y + 0.5}, "direction": d}, **kw))

    for c in cols:
        taps = sorted(c.taps)
        last = taps[-1]
        splitters = {R - 1 for R in taps[:-1]}
        # corridors of the legs that cross this column: those of every column west of it that this one spans
        cross = sorted(R for k in cols if k.idx < c.idx for R in k.taps if c.head < R < last)
        for y in range(c.head, last + 1):
            if y == last:
                put(belt, c.x, y, EAST)  # the last consumer's feed: the column turns
            elif y in splitters:
                # a tap goes before the rest of the column unless a consumer below it matters more: the nearest
                # first, so the bus fills from the top (a consumer high up would otherwise wait for the whole
                # column below it to fill)
                below = max(c.rank[R] for R in taps if R - 1 > y)
                ents.append({"name": splitter, "position": {"x": c.x + 1.0, "y": y + 0.5}, "direction": SOUTH,
                             "output_priority": "left" if c.rank[y + 1] >= below else "right"})
            elif any(y == R - 1 for R in cross):
                put(ug, c.x, y, SOUTH, ug_type="input")
            elif any(y == R + 1 for R in cross):
                put(ug, c.x, y, SOUTH, ug_type="output")
            elif any(y == R for R in cross):
                continue  # the leg crosses on top
            else:
                put(belt, c.x, y, SOUTH)
        for R in cross:
            for y in range(R - 1, R + 2):
                spans.add((0, c.x, y))
        for R in taps:
            for x in range(c.x + 1, x_end):
                put(belt, x, R, EAST)
    return ents, spans
