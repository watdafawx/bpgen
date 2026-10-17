"""Tile geometry shared by the planners: entity sizes and footprints, quarter turns, belt directions.

Directions are Factorio 2.0's 16-step ones (north 0, east 4, south 8, west 12); y grows downward, so a quarter turn
clockwise takes (x, y) to (-y, x). Nothing here knows about a plan: callers pass prototypes or entity dicts in."""
import math

DIRS = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}  # direction -> (dx, dy) of one step that way
NO_BOX = ((0, 0), (0, 0))
# (sizes of what the bus design places when bpgen's copy of the mods' data has no mining drills yet)
KNOWN_SIZES = {"electric-mining-drill": ("mining-drill", 3), "pumpjack": ("mining-drill", 3),
               "burner-mining-drill": ("mining-drill", 2)}
SIZED_TYPES = ["assembling-machine", "furnace", "beacon", "inserter", "transport-belt", "underground-belt", "splitter",
               "electric-pole", "pipe", "pipe-to-ground"]


# ---- sizes ----------------------------------------------------------------------------------------------------------

def box_dims(box, least=0):
    """(width, height) in tiles of a collision box ((x1, y1), (x2, y2)), each at least `least`"""
    (x1, y1), (x2, y2) = box
    return max(least, math.ceil(x2 - x1)), max(least, math.ceil(y2 - y1))


def dims(proto):
    """(width, height) in tiles of a prototype, placed facing north"""
    if "tile_width" in proto:
        return proto["tile_width"], proto.get("tile_height", proto["tile_width"])
    return box_dims(proto["collision_box"])


def side(proto):
    """the longer side in tiles of a prototype's collision box (0 without one)"""
    return max(box_dims(proto.get("collision_box") or NO_BOX))


def sizer(data):
    """-> size_of(name, direction) -> (w, h) in tiles as placed (a quarter turn swaps them) of a belt, inserter, pole,
    pipe, machine or beacon; (1, 1) for anything else"""
    def size_of(name, direction):
        for t in SIZED_TYPES:
            p = data.raw.get(t, {}).get(name)
            if p and p.get("collision_box"):
                w, h = box_dims(p["collision_box"], 1)
                return (h, w) if direction in (4, 12) else (w, h)
        return 1, 1
    return size_of


def type_and_size(name, kind, proto):
    """(type, w, h) of an entity for the preview: its prototype's, else KNOWN_SIZES', else 1x1"""
    w = h = 1
    if proto is None and name in KNOWN_SIZES:
        kind, w = KNOWN_SIZES[name]
        h = w
    if proto and proto.get("collision_box"):
        w, h = box_dims(proto["collision_box"], 1)
    return kind, w, h


def span(entities):
    """(width - 1, height - 1) of the entities' positions, truncated; None when there are none"""
    if not entities:
        return None
    xs = [e["position"]["x"] for e in entities]
    ys = [e["position"]["y"] for e in entities]
    return int(max(xs) - min(xs)), int(max(ys) - min(ys))


def extent(entities):
    """[w, h] of the box round the entities' positions, in tiles; None when there are none"""
    s = span(entities)
    return [s[0] + 1, s[1] + 1] if s else None


# ---- footprints -----------------------------------------------------------------------------------------------------

def tiles_at(x, y, w, h):
    """the w x h tiles centred on (x, y)"""
    x0 = math.floor(x - w / 2 + 0.01)
    y0 = math.floor(y - h / 2 + 0.01)
    return [(x0 + i, y0 + j) for i in range(w) for j in range(h)]


def tiles(ent, size_of):
    """set of tiles an entity covers, its size from size_of(name, direction)"""
    w, h = size_of(ent["name"], ent.get("direction", 0))
    return set(tiles_at(ent["position"]["x"], ent["position"]["y"], w, h))


def footprint(e):
    """tiles an entity covers: its box from the game when the snapshot has one, else position, w, h from
    Service.decorate"""
    if e.get("box"):
        x1, y1, x2, y2 = e["box"]
        return [(x, y) for x in range(math.floor(x1 + 0.01), math.ceil(x2 - 0.01))
                for y in range(math.floor(y1 + 0.01), math.ceil(y2 - 0.01))]
    w, h = e.get("w", 1), e.get("h", 1)
    if w != h and e.get("direction", 0) in (4, 12):
        w, h = h, w
    return tiles_at(e["position"]["x"], e["position"]["y"], w, h)


def plan_tiles(entities):
    out = set()
    for e in entities:
        out.update(footprint(e))
    return out


def runs_tiles(runs):
    """{y: [[x1, x2], ...]} -> set of (x, y)"""
    out = set()
    for y, rs in (runs or {}).items():
        for x1, x2 in rs:
            out.update((x, int(y)) for x in range(int(x1), int(x2) + 1))
    return out


# ---- turns ----------------------------------------------------------------------------------------------------------

def turn_xy(x, y, k):
    """(x, y) turned k quarter turns clockwise about the origin (y grows downward)"""
    for _ in range(k % 4):
        x, y = -y, x
    return x, y


def turn_dir(d, k):
    """a direction turned k quarter turns clockwise"""
    return (d + 4 * k) % 16


def mirror_dir(d):
    """a direction mirrored left to right (a blueprint flipped horizontally): east and west swap"""
    return (16 - d) % 16


def turn_tile(t, k):
    """the tile (x, y) turned k quarter turns clockwise about the origin, as the tile its centre lands on"""
    x, y = turn_xy(t[0] + 0.5, t[1] + 0.5, k)
    return math.floor(x), math.floor(y)


def turn_entity(e, k):
    """an entity of the local frame turned k quarter turns clockwise about the origin"""
    x, y = turn_xy(e["position"]["x"], e["position"]["y"], k)
    out = dict(e, position={"x": x, "y": y})
    if "direction" in e:
        out["direction"] = turn_dir(e["direction"], k)
    return out


def conn_xy(pc):
    """a pipe connection's position, on the half-tile grid (some mods put them at -2.2 or -2.15 on a 5x5's edge:
    the game opens them onto the same tile as -2)"""
    x, y = pc["position"] if isinstance(pc["position"], list) else (pc["position"]["x"], pc["position"]["y"])
    return round(x * 2) / 2, round(y * 2) / 2


def turn_conn(pc, facing):
    """(x, y, direction) of a pipe connection with the machine placed facing `facing`"""
    x, y = conn_xy(pc)
    d = pc.get("direction", 0)
    if facing // 4:
        x, y = turn_xy(x, y, facing // 4)
        d = (d + 4 * (facing // 4)) % 16
    return x, y, d


# ---- layout rows ----------------------------------------------------------------------------------------------------

class Rows:
    """a layout's rows top to bottom: rows(name, height=1) puts the next one under the last; .Y[name] is a row's
    first tile row, .y where the next would go (the layout's height so far)"""

    def __init__(self):
        self.Y = {}
        self.y = 0

    def __call__(self, name, height=1):
        self.Y[name] = self.y
        self.y += height
