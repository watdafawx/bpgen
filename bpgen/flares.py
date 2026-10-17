"""Flares: machines that burn a fluid a starter base makes more of than it uses (a recipe with one fluid in and
nothing out), in a row over a pipe. Every production line is planned by planner.plan (the row solver); this is the
one block that isn't a line."""
import math

from bpgen import planner
from bpgen.planner import NORTH, SOUTH, PlanError, _amount, _size


def _opens_south(machine):
    """an input fluid box with a pipe connection facing south (as the machine is drawn): the flare's pipe runs there"""
    return any(pc.get("connection_type", "normal") == "normal" and pc.get("direction", NORTH) == SOUTH
               for fb in machine.get("fluid_boxes", []) if fb.get("production_type") in ("input", "input-output")
               for pc in fb.get("pipe_connections", []))


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
        for t in ("furnace", "assembling-machine"):
            for mn, m in data.raw.get(t, {}).items():
                if m.get("hidden") or not planner.can_craft(m, r):
                    continue
                if m.get("energy_source", {}).get("type") not in ("electric", "burner"):
                    continue  # unpowered copies (bioluminescent-, harene-infused-) and helper entities
                if not _opens_south(m):
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
