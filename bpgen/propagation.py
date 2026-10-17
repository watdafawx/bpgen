"""Power Propagation (mod "power-propagation"): every powered building carries a hidden pole that powers
0.5 + size/2 + range/2 tiles from its centre and wires itself to any pole whose supply reach adds up to the gap
(its own hidden ones included). A row of touching machines and inserters then powers itself: a blueprint needs
only enough real poles to tie each group of buildings into the grid.

The game tells bpgen whether the mod is on and its startup settings (ingame.warm): configure() keeps them;
prune() drops the poles the mod makes redundant from a blueprint's entities.
"""
from bpgen import geom

RANGE = None  # the mod's "power-propagation-range" while it is on (and spreads through powered buildings), else None


def configure(settings):
    """settings: {"range": int, "buildings": bool} from the game, or None (the mod is off)"""
    global RANGE
    on = bool(settings) and settings.get("buildings", True) is not False
    RANGE = int(settings.get("range", 1)) if on else None


def _index(data):
    idx = getattr(data, "_by_name", None)
    if idx is None:
        idx = {}
        for kind, protos in data.raw.items():
            for name, p in protos.items():
                # (entities only: an item or recipe often has the same name)
                if isinstance(p, dict) and "collision_box" in p:
                    idx.setdefault(name, (kind, p))
        try:
            data._by_name = idx
        except AttributeError:
            pass
    return idx


_size = geom.side


def _electric(p):
    src = p.get("energy_source")
    return isinstance(src, dict) and src.get("type") == "electric"


def prune(data, entities, rng=None):
    """-> entities without the real poles Power Propagation makes redundant (each group of powered buildings keeps a
    pole of its own to tie into the grid; a pole carrying circuit wires, tags or a moved entity's fields stays)"""
    rng = RANGE if rng is None else rng
    if rng is None or not entities:
        return entities
    idx = _index(data)
    nodes = []  # (entity index, x, y, supply reach, wire reach or None, is a real pole)
    removable = []
    for i, e in enumerate(entities):
        kind, p = idx.get(e["name"], (None, None))
        if p is None:
            continue
        x, y = e["position"]["x"], e["position"]["y"]
        if kind == "electric-pole":
            nodes.append((i, x, y, p.get("supply_area_distance", 0), p.get("maximum_wire_distance", 0), True))
            if not any(e.get(k) for k in ("wire_id", "circuit_to", "wires_to", "bp_extra", "tags")):
                removable.append(len(nodes) - 1)
        elif _electric(p) and 1 <= _size(p) <= 30:
            nodes.append((i, x, y, 0.5 + _size(p) * 0.5 + rng * 0.5, None, False))
    if not removable or not any(not n[5] for n in nodes):
        return entities

    # links: two real poles by a wire (bpgen wires poles within both reaches); anything else as the mod joins them
    cell = 16.0
    grid = {}
    for k, (_, x, y, *_rest) in enumerate(nodes):
        grid.setdefault((int(x // cell), int(y // cell)), []).append(k)
    near = [[] for _ in nodes]
    for k, (_, x, y, s, w, pole) in enumerate(nodes):
        cx, cy = int(x // cell), int(y // cell)
        for gx in range(cx - 2, cx + 3):
            for gy in range(cy - 2, cy + 3):
                for j in grid.get((gx, gy), ()):
                    if j <= k:
                        continue
                    _, x2, y2, s2, w2, pole2 = nodes[j]
                    d2 = (x - x2) ** 2 + (y - y2) ** 2
                    reach = min(w, w2) if pole and pole2 else s + s2
                    if d2 <= reach * reach:
                        near[k].append(j)
                        near[j].append(k)

    def groups(gone):
        """-> the groups of linked buildings, each as (has a real pole)"""
        seen, out = set(gone), []
        for k, n in enumerate(nodes):
            if n[5] or k in seen:
                continue
            seen.add(k)
            stack, has_pole = [k], False
            while stack:
                a = stack.pop()
                has_pole = has_pole or nodes[a][5]
                for b in near[a]:
                    if b not in seen:
                        seen.add(b)
                        stack.append(b)
            out.append(has_pole)
        return out

    before = groups(set())
    want_groups, want_bare = len(before), before.count(False)
    gone = set()
    # (the poles furthest east and south go first: the ones kept sit at the west end, where a line is fed)
    for k in sorted(removable, key=lambda k: (-nodes[k][1], -nodes[k][2])):
        trial = gone | {k}
        g = groups(trial)
        if len(g) <= want_groups and g.count(False) <= want_bare:
            gone = trial
    if not gone:
        return entities
    drop = {nodes[k][0] for k in gone}
    return [e for i, e in enumerate(entities) if i not in drop]
