"""A plan's blueprint cut into pieces the game builds a few a tick (zzz-bpgen/build.lua), so placing or previewing
a big plan never holds the game thread: importing and building 2700 entities in one call took a tick of 25-60 ms.

Every answer with a blueprint gets "pieces" (ingame._answer):

    {"prints": [{"path": "" | "3" | "3.1" (a book's print: its slot, 1-based; a book in a book: its first print),
                 "label", "n": entities, "parts": [blueprint strings of up to PER entities, cut by CELL-tile cells],
                 "wires": [[k1, connector, k2, connector]]: wires between parts (their ends tagged bpgen_w = k),
                 "frame": [x1, y1, x2, y2] the tiles it covers in its own coordinates (x2, y2 past the last),
                 "even": true when it has rails (moved in steps of 2)}],
     "active": the path of a book's active print}

Each part keeps the print's own coordinates and snaps absolutely to a 1-tile grid, so built at cursor position P
an entity lands at floor(P) + its position (turned: floor(P) + 1/2 + the turned (position - 1/2)), whatever part
it is in; the game works out P from the frame (centred on a spot, or the frame's corner on an absolute plan's box).
"""
import math

from bpgen import ents as entlib, geom

PER = 250  # entities a part
TILES_PER = 1000  # tiles a part (tiles cost the game far less than entities)
CELL = 32  # (parts are made of whole cells, west to east, north to south)
MAX_ENTITIES = 20000  # a plan bigger than this isn't built in game (refused: see ingame._answer)


def _prints(obj, path=""):
    """(path, blueprint) of every print in a blueprint or book; a book's active print's path"""
    if "blueprint" in obj:
        return [(path, obj["blueprint"])], path
    book = obj.get("blueprint_book") or {}
    out, active = [], None
    for item in sorted(book.get("blueprints") or [], key=lambda b: b.get("index", 0)):
        p = (path + "." if path else "") + str(item.get("index", 0) + 1)
        if "blueprint_book" in item:  # (a book in the book: its first print, as the game shows it)
            inner, _ = _prints(item, p)
            out += inner[:1]
        else:
            out += _prints(item, p)[0]
        if item.get("index", 0) == book.get("active_index", 0):
            active = out[-1][0] if out else None
    return out, active


def count(bp_string):
    """entities in a blueprint string, all prints"""
    prints, _ = _prints(entlib.decode(bp_string))
    return sum(len(b.get("entities") or []) for _, b in prints)


def _cut(bp, size):
    """one print -> its entry of "prints" (see above). size(name) -> (w, h) of an entity facing north"""
    ents = bp.get("entities") or []
    tiles = bp.get("tiles") or []
    covered = []
    for e in ents:
        w, h = size(e["name"])
        covered += geom.footprint({"position": e["position"], "direction": e.get("direction", 0), "w": w, "h": h})
    covered += [(t["position"]["x"], t["position"]["y"]) for t in tiles]
    if covered:
        frame = [min(x for x, _ in covered), min(y for _, y in covered), max(x for x, _ in covered) + 1,
                 max(y for _, y in covered) + 1]
    else:
        frame = [0, 0, 0, 0]

    def cell(p):
        return math.floor(p["y"] / CELL), math.floor(p["x"] / CELL), p["y"], p["x"]

    order = sorted(ents, key=lambda e: cell(e["position"]))
    groups = [order[i:i + PER] for i in range(0, len(order), PER)] or [[]]
    part_of, number = {}, {}  # entity_number -> part index; -> its number in that part
    for gi, g in enumerate(groups):
        for n, e in enumerate(g, 1):
            part_of[e["entity_number"]], number[e["entity_number"]] = gi, n
    inner = [[] for _ in groups]
    cross, tag = [], {}

    def tagged(en):
        if en not in tag:
            tag[en] = len(tag) + 1
        return tag[en]
    for a, ca, b, cb in bp.get("wires") or []:
        if a not in part_of or b not in part_of:
            continue
        if part_of[a] == part_of[b]:
            inner[part_of[a]].append([number[a], ca, number[b], cb])
        else:
            cross.append([tagged(a), ca, tagged(b), cb])

    tile_groups = [sorted(tiles, key=lambda t: cell(t["position"]))[i:i + TILES_PER]
                   for i in range(0, len(tiles), TILES_PER)]
    parts = []
    for gi, g in enumerate(groups):
        out = []
        for e in g:
            e2 = dict(e, entity_number=number[e["entity_number"]])
            if e["entity_number"] in tag:
                e2["tags"] = dict(e.get("tags") or {}, bpgen_w=tag[e["entity_number"]])
            out.append(e2)
        part = {"item": "blueprint", "entities": out, "snap-to-grid": {"x": 1, "y": 1}, "absolute-snapping": True}
        if bp.get("version"):
            part["version"] = bp["version"]
        if inner[gi]:
            part["wires"] = inner[gi]
        if gi < len(tile_groups):
            part["tiles"] = tile_groups[gi]
        if out or part.get("tiles"):
            parts.append(entlib.encode({"blueprint": part}))
    for tg in tile_groups[len(groups):]:  # (more tiles than parts of entities)
        parts.append(entlib.encode({"blueprint": {"item": "blueprint", "tiles": tg, "snap-to-grid": {"x": 1, "y": 1},
                                                   "absolute-snapping": True, "version": bp.get("version")}}))
    even = any("rail" in e["name"] for e in ents)
    return {"label": bp.get("label"), "n": len(ents), "parts": parts, "wires": cross, "frame": frame, "even": even}


def join(spec):
    """an area of the base the game blueprinted a cell a tick (zzz-bpgen/build.lua: area_print) -> one blueprint
    string of it in the world's coordinates. spec: {"parts": [{"blueprint", "offset": [x, y] (its coordinates plus
    this are the world's), "cell"}], "wires": [[name, x, y, connector, name, x, y, connector]]: wires between
    cells}. An entity on a cell's edge is in both cells' prints: kept once"""
    ents, tiles, wires, at, seen_tiles, seen_wires = [], [], [], {}, set(), set()
    version = None

    def wire(a, ca, b, cb):
        k = (min((a, ca), (b, cb)), max((a, ca), (b, cb)))
        if a != b and k not in seen_wires:
            seen_wires.add(k)
            wires.append([a, ca, b, cb])
    for part in spec.get("parts") or []:
        bp = entlib.decode(part["blueprint"]).get("blueprint") or {}
        version = version or bp.get("version")
        ox, oy = part["offset"]
        local = {}
        for e in bp.get("entities") or []:
            x, y = e["position"]["x"] + ox, e["position"]["y"] + oy
            k = (e["name"], round(x, 3), round(y, 3))
            if k not in at:
                at[k] = len(ents) + 1
                ents.append(dict(e, entity_number=at[k], position={"x": x, "y": y}))
            local[e["entity_number"]] = at[k]
        for t in bp.get("tiles") or []:
            p = (t["position"]["x"] + ox, t["position"]["y"] + oy)
            if p not in seen_tiles:
                seen_tiles.add(p)
                tiles.append(dict(t, position={"x": p[0], "y": p[1]}))
        for a, ca, b, cb in bp.get("wires") or []:
            if a in local and b in local:
                wire(local[a], ca, local[b], cb)
    for n1, x1, y1, c1, n2, x2, y2, c2 in spec.get("wires") or []:
        a, b = at.get((n1, round(x1, 3), round(y1, 3))), at.get((n2, round(x2, 3), round(y2, 3)))
        if a and b:
            wire(a, c1, b, c2)
    out = {"item": "blueprint", "entities": ents}
    if tiles:
        out["tiles"] = tiles
    if wires:
        out["wires"] = wires
    if version:
        out["version"] = version
    return entlib.encode({"blueprint": out})


def pieces(bp_string, size):
    """the "pieces" of an answer's blueprint string (see above). size(name) -> (w, h)"""
    prints, active = _prints(entlib.decode(bp_string))
    out = []
    for path, bp in prints:
        p = _cut(bp, size)
        p["path"] = path
        out.append(p)
    return {"prints": out, "active": active}
