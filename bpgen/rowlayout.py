"""Entities of a production line from its RowSpec (rowspec.py) and the plan's counts: the one layout behind every
line (planner.layout) and every direct-insertion line.

Rows top to bottom: row A's north bands outermost first, row A's machines, its south bands; with two rows the last
south band is shared and row B follows it mirrored (its south bands, machines, north bands innermost first). Each
band's entities run the row's width; per machine the inserter and output bands take inserters, a pole and the
risers of the pipe bands on their side from the machine's columns ("slots")."""
from bpgen import geom, planner
from bpgen.ents import adder
from bpgen.power import power_up
from bpgen.rowspec import EAST, NORTH, SOUTH, WEST, RowSpec

MACHINE_MODULES = "assembling_machine_modules"
BEACON_MODULES = "beacon_modules"


def _need(p):
    """what the plan says each input item needs per second, per row"""
    return p.input_need


def _pole_here(band, k, per_row, side):
    """does machine k get the pole this band's own rule says (Band.pole)?"""
    if band.pole is None or (side == "b" and not band.pole_b):
        return False
    return k % band.pole_every == 0 or (band.pole_last and k == per_row - 1)


def layout(spec: RowSpec, p):
    """-> (entities, sources, sinks) with positions relative to the top-left corner"""
    from bpgen.effects import BEACON_MODULES as BEACON_INV, MACHINE_MODULES as MACHINE_INV
    s, hs = spec.size, spec.height
    pitch = spec.width_of
    width = spec.shift + p.per_row * pitch + spec.tail + (spec.beacon_size if spec.beacon else 0)
    if spec.beacon:  # B M B M ... B: the beacons' own width past the last machine
        width = p.per_row * pitch + spec.beacon_size + 2 * spec.beacon_pad * pitch
    sides = ["a", "b"] if spec.rows == 2 else ["a"]

    # ---- rows -------------------------------------------------------------------------------------------------
    row = geom.Rows()
    Y = row.Y

    def rname(band, side):
        return band.name if band.shared else f"{band.name}_{side}"
    for band in reversed(spec.north):
        row(rname(band, "a"), band.height)
    row("mach_a", hs)
    shared_at = None
    for i, band in enumerate(spec.south):
        row(rname(band, "a"), band.height)
        if band.shared:
            shared_at = i
            break
    if spec.rows == 2:
        for band in reversed(spec.south[:shared_at]):
            row(rname(band, "b"), band.height)
        row("mach_b", hs)
        for band in spec.north:
            row(rname(band, "b"), band.height)
    elif shared_at is not None:
        for band in spec.south[shared_at + 1:]:
            row(rname(band, "a"), band.height)

    ents = []
    put = adder(ents)

    # ---- the long rows: belts and pipes -------------------------------------------------------------------------
    for side in sides:
        for band in spec.north + spec.south:
            if band.shared and side == "b":
                continue
            y = Y[rname(band, side)] + 0.5
            n = width + (1 if band.extra else 0)
            if band.kind == "belt":
                if band.role != "out" and not any(band.lanes):
                    continue  # (an empty belt row: laid out, nothing on it)
                for x in range(n):
                    put(p.belt, x + 0.5, y, EAST)
            elif band.kind == "pipe":
                for x in range(n):
                    put(planner.PIPE, x + 0.5, y)

    # ---- beacons and their poles --------------------------------------------------------------------------------
    if spec.beacon:
        bs = spec.beacon_size
        for j in range(-spec.beacon_pad, p.per_row + 1 + spec.beacon_pad):
            xb = spec.shift - bs + j * pitch + bs / 2
            for side in sides:
                put(spec.beacon, xb, Y["mach_" + side] + bs / 2, modules=p.beacon_modules.items, inventory=BEACON_INV)
            pole_x = spec.shift - bs + j * pitch + bs // 2 + 0.5
            for name in ("ins_a", "out_a", "ins_b"):
                if name in Y:
                    put(planner.POLE, pole_x, Y[name] + 0.5)

    # ---- machines and what sits in their columns ----------------------------------------------------------------
    mods = getattr(p, "modules", None)
    machine_mods = {"modules": mods.items, "inventory": MACHINE_INV} if mods and mods.count else {}
    recipe = {} if getattr(p, "furnace", False) else {"recipe": p.recipe}
    for k in range(p.per_row):
        x0 = spec.shift + k * pitch
        has_pole = spec.pole_every and k % spec.pole_every == 0
        kind = spec.pattern[k] if spec.pattern else "E"  # (C: a machine making the item it hands the E beside it)
        main = kind == "E"
        for side in sides:
            facing = NORTH if side == "a" else SOUTH  # where the input bands are, as seen from the machine
            toward_machine = SOUTH if side == "a" else NORTH
            m_dir = spec.facing if side == "a" else (spec.facing + 8) % 16
            if main:
                put(p.machine, x0 + s / 2, Y["mach_" + side] + hs / 2, m_dir, **recipe, **machine_mods)
            else:
                put(spec.made_machine, x0 + s / 2, Y["mach_" + side] + hs / 2, m_dir, recipe=spec.made_recipe)

            def col(c):  # a row-A column as placed on this side (row B is turned round)
                return c if side == "a" else s - 1 - c
            for bands, outward, inward in ((spec.north, facing, toward_machine), (spec.south, toward_machine, facing)):
                pipes = [b for b in bands if b.kind == "pipe"]
                ins_band = next((b for b in bands if b.kind in ("ins", "out")), None)
                entry = ins_band or (bands[0] if bands else None)  # (no inserter band: the risers land in the innermost)
                if entry is None:
                    continue
                yi = Y[rname(entry, side)] + 0.5
                slots = list(range(s))
                # risers: from the entry band through to each pipe band's riser row
                for pipe in pipes:
                    if pipe.riser is None:  # (the pipe touches the machine's connections)
                        continue
                    fc = col(pipe.riser)
                    slots.remove(fc)
                    riser_band = bands[bands.index(pipe) - 1]  # the riser row just inside the pipe
                    if riser_band is entry:
                        put(planner.PIPE, x0 + fc + 0.5, yi)
                    else:
                        put(planner.UNDERGROUND, x0 + fc + 0.5, yi, inward)  # normal side into the machine
                        put(planner.UNDERGROUND, x0 + fc + 0.5, Y[rname(riser_band, side)] + 0.5, outward)
                        if has_pole and spec.pole_in_tap and ins_band is not None and ins_band.kind == "ins":
                            put(planner.POLE, x0 + (s - 1 if fc != s - 1 else 0) + 0.5, Y[rname(riser_band, side)] + 0.5)
                # a gap pipe: down the free column beside the machine from its port to the machine's edge, then up or
                # down to its pipe row like a riser (a pipe in the first row, else a pipe-to-ground pair)
                for pipe in pipes:
                    if not pipe.gap:
                        continue
                    gx = x0 - 1 if pipe.gap == "w" else x0 + s
                    ym = Y["mach_" + side]
                    ys = range(0, pipe.gap_row + 1) if bands is spec.north else range(pipe.gap_row, hs)
                    for j in ys:
                        put(planner.PIPE, gx + 0.5, ym + j + 0.5)
                    riser_band = bands[bands.index(pipe) - 1]
                    if riser_band is entry:
                        put(planner.PIPE, gx + 0.5, yi)
                    else:
                        put(planner.UNDERGROUND, gx + 0.5, yi, inward)
                        put(planner.UNDERGROUND, gx + 0.5, Y[rname(riser_band, side)] + 0.5, outward)
                pole_here = ins_band is not None and _pole_here(ins_band, k, p.per_row, side)
                if ins_band is not None and ins_band.kind == "ins":
                    if has_pole and not pipes and not spec.poles_out:
                        put(planner.POLE, x0 + slots.pop() + 0.5, yi)
                    choices = (p.ins_near, p.ins_far) if not spec.pattern else (p.ins_far,) if main else (p.ins_near,)
                    for choice in choices:  # (they face where they pick up)
                        if choice:
                            for _ in range(choice.count):
                                put(choice.name, x0 + slots.pop(0) + 0.5, yi, outward)
                elif ins_band is not None:  # output band: inserters pick from the machine
                    if (has_pole and (side == "a" or spec.poles_out)) or (pole_here and ins_band.pole == "slot-last"):
                        put(planner.POLE, x0 + slots.pop() + 0.5, yi)
                    if p.ins_out and main:
                        extra = {"override_stack_size": p.ins_out.hand} if p.ins_out.hand else {}
                        for _ in range(p.ins_out.count):
                            put(p.ins_out.name, x0 + slots.pop(0) + 0.5, yi, inward, **extra)
                    if getattr(p, "ins_south", None):  # (one row: long-handed ones reaching the belt past the output)
                        for _ in range(p.ins_south.count):
                            put(p.ins_south.name, x0 + slots.pop(0) + 0.5, yi, outward)
                if (bands is spec.south and spec.pattern and k + 1 < p.per_row
                        and {kind, spec.pattern[k + 1]} == {"C", "E"}):
                    # the gap east of this machine: C into E (an inserter faces where it picks up from)
                    pick = WEST if kind == "C" else EAST
                    for j in sorted(range(hs), key=lambda j: abs(j - (hs - 1) / 2))[:p.ins_side.count]:
                        put(p.ins_side.name, x0 + s + 0.5, Y["mach_" + side] + j + 0.5, pick)
                # poles standing in a row of their own (a riser or gap row), by the band's rule
                for band in bands:
                    if band.kind in ("ins", "out") or not _pole_here(band, k, p.per_row, side):
                        continue
                    taken = {b.riser for b in pipes if b.riser is not None}  # (first-free: no riser there)
                    c = col(next(c for c in range(s) if c not in taken))
                    put(planner.POLE, x0 + c + 0.5, Y[rname(band, side)] + 0.5)

    # ---- sources and sinks --------------------------------------------------------------------------------------
    sources, sinks = [], []
    need = _need(p)
    for side in sides:  # (per side: the belts, north then south, then the fluids)
        bands = [b for b in spec.north + spec.south if not (b.shared and side == "b")]
        for band in bands:
            if band.kind == "belt" and band.role != "out" and any(band.lanes):
                sources.append({"kind": "belt", "position": {"x": 0.5, "y": Y[rname(band, side)] + 0.5},
                                "lanes": list(band.lanes), "rates": {it: need[it] for it in set(band.lanes) if it}})
        for band in bands:
            if band.kind == "pipe" and band.role == "in":
                sources.append({"kind": "fluid", "position": {"x": -0.5, "y": Y[rname(band, side)] + 0.5},
                                "fluid": band.fluid, "rates": {band.fluid: band.rate}})
        for fluid, rate, mrow in spec.machine_sources:
            sources.append({"kind": "fluid", "position": {"x": -0.5, "y": Y["mach_" + side] + mrow + 0.5},
                            "fluid": fluid, "rates": {fluid: rate}})
    out_band = next((b for b in spec.north + spec.south if b.kind == "belt" and b.role == "out"), None)
    if out_band is not None:
        sink = {"kind": "belt", "position": {"x": width + 0.5, "y": Y[rname(out_band, "a")] + 0.5}}
        if out_band.sink == "item":
            sink["item"] = p.output
        sinks.append(sink)
    fluid_sinks = []
    for side in sides:
        for band in spec.north + spec.south:
            if band.kind == "pipe" and band.role == "out" and not (band.shared and side == "b"):
                sink = {"kind": "fluid", "position": {"x": width + 0.5, "y": Y[rname(band, side)] + 0.5},
                        "item": band.fluid}
                if band.sink == "":
                    sink["rate"] = band.rate
                fluid_sinks.append(sink)
    sinks += sorted(fluid_sinks, key=lambda k: k["position"]["y"])  # (top to bottom: the order routers take them in)
    byproducts = getattr(p, "byproducts", None)
    if byproducts:
        bottom = row.y - 1 if spec.sort_tail_bottom == "last" else Y[rname(
            next(b for b in spec.south if b.name == spec.sort_tail_bottom), "a")]
        rates = {k: v * p.machines for k, v in byproducts.items()}
        sinks[0:1] = planner.sort_tail(ents, p.belt, p.splitter, rates, p.output, width + 1, Y[rname(out_band, "a")],
                                       bottom)
    if spec.power_up:
        power_up(ents, sources, sinks)
    return _from_corner(ents, sources, sinks, spec)


def _from_corner(ents, sources, sinks, spec):
    """the line moved so its top-left tile is (0, 0) - what every reader of a layout takes it for - when a pole
    power_up added stands west or north of it"""
    def half(e):
        if e["name"] == spec.beacon:
            return spec.beacon_size / 2, spec.beacon_size / 2
        if "recipe" in e or e["name"] in (getattr(spec, "made_machine", None),):
            w, h = spec.size, spec.height
            return (h / 2, w / 2) if e.get("direction", 0) in (EAST, WEST) else (w / 2, h / 2)
        return 0.5, 0.5
    x0 = min(e["position"]["x"] - half(e)[0] for e in ents)
    y0 = min(e["position"]["y"] - half(e)[1] for e in ents)
    dx, dy = -min(0, int(x0 // 1)), -min(0, int(y0 // 1))
    if not dx and not dy:
        return ents, sources, sinks

    def moved(d):
        return dict(d, position={"x": d["position"]["x"] + dx, "y": d["position"]["y"] + dy})
    return [moved(e) for e in ents], [moved(x) for x in sources], [moved(x) for x in sinks]
