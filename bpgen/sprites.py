"""Top-down entity sprites for the preview, from the game's and the mods' own graphics.

build(raw) turns a full prototype dump into a small table: for each entity, its variants (direction, belt curve,
pipe shape...) as lists of image layers (first animation frame, shadows/light/glow left out). render() composites a
variant at 64 px per tile, centred on the entity's position, so the preview draws it centred on the entity.
"""
import io
import json
import math
from functools import lru_cache

from PIL import Image

from bpgen import icons

PX = 64  # pixels per tile in rendered sprites
DIRS = ["N", "E", "S", "W"]
FOUR = ["north", "east", "south", "west"]
# transport belt animation set rows (1-based indices in the prototype docs)
BELT_ROWS = {"E": 1, "W": 2, "N": 3, "S": 4, "east_to_north": 5, "north_to_east": 6, "west_to_north": 7,
             "north_to_west": 8, "south_to_east": 9, "east_to_south": 10, "south_to_west": 11, "west_to_south": 12}
PIPE_KEYS = ["straight_vertical_single", "straight_vertical", "straight_horizontal", "corner_up_right",
             "corner_up_left", "corner_down_right", "corner_down_left", "t_up", "t_down", "t_right", "t_left", "cross",
             "ending_up", "ending_down", "ending_right", "ending_left"]
TYPES = ["assembling-machine", "furnace", "rocket-silo", "lab", "container", "logistic-container", "electric-pole",
         "beacon", "constant-combinator", "transport-belt", "underground-belt", "splitter", "pipe", "pipe-to-ground",
         "inserter", "mining-drill", "storage-tank", "pump", "boiler", "generator", "offshore-pump", "roboport",
         "radar", "accumulator", "solar-panel", "loader", "loader-1x1", "lamp", "arithmetic-combinator",
         "decider-combinator", "reactor", "heat-pipe", "agricultural-tower", "asteroid-collector", "thruster",
         "cargo-landing-pad", "fusion-reactor", "fusion-generator", "lightning-attractor", "wall", "gate",
         "ammo-turret", "electric-turret", "fluid-turret", "artillery-turret", "simple-entity-with-owner"]


def _shift(spec):
    s = spec.get("shift") or [0, 0]
    return [s["x"], s["y"]] if isinstance(s, dict) else [s[0], s[1]]


def layers(spec, frame=0, row=0):
    """first-frame image layers of a Sprite / Animation / RotatedSprite (or their `layers`)"""
    if not isinstance(spec, dict):
        return []
    if "layers" in spec:
        return [l for sub in spec["layers"] for l in layers(sub, frame, row)]
    if spec.get("draw_as_shadow") or spec.get("draw_as_light") or spec.get("draw_as_glow"):
        return []
    size = spec.get("size")
    w = spec.get("width") or (size if isinstance(size, (int, float)) else (size or [None, None])[0])
    h = spec.get("height") or (size if isinstance(size, (int, float)) else (size or [None, None])[1])
    file = spec.get("filename") or (spec.get("filenames") or [None])[0] \
        or ((spec.get("stripes") or [{}])[0].get("filename"))
    if not file or not w or not h:
        return []
    x0, y0 = spec.get("x", 0), spec.get("y", 0)
    if spec.get("position"):
        x0, y0 = spec["position"]
    line = spec.get("line_length") or 0
    col, r = frame, row
    if line:
        r, col = row + frame // line, frame % line
    out = {"file": file, "x": x0 + col * w, "y": y0 + r * h, "w": w, "h": h, "scale": spec.get("scale", 1),
           "shift": _shift(spec)}
    if spec.get("tint"):
        out["tint"] = spec["tint"]
    return [out]


def four_way(spec, d):
    """Sprite4Way / Animation4Way: d = 0..3 (north, east, south, west)"""
    if not isinstance(spec, dict):
        return []
    if "sheet" in spec:
        return layers(spec["sheet"], frame=d)
    if "sheets" in spec:
        return [l for s in spec["sheets"] for l in layers(s, frame=d)]
    if "north" in spec:
        return layers(spec.get(FOUR[d]) or spec["north"])
    return layers(spec)


def _offset(ls, dx, dy):
    return [dict(l, offset=[dx, dy]) for l in ls]


def _belt_rows(belt_set):
    anim = (belt_set or {}).get("animation_set")
    return {k: layers(anim, row=r - 1) for k, r in BELT_ROWS.items()} if anim else {}


def _variants(t, p, belts_by_name):
    """entity prototype -> {variant: [layers]} (empty when it has nothing usable)"""
    out = {}
    if t in ("assembling-machine", "furnace", "rocket-silo", "mining-drill", "agricultural-tower"):
        gs = p.get("graphics_set") or {}
        anim = gs.get("animation") or gs.get("idle_animation") or p.get("base_picture") or p.get("animation")
        if isinstance(anim, dict) and "north" in anim:
            out = {DIRS[d]: four_way(anim, d) for d in range(4)}
        else:
            out = {"": layers(anim)}
            if not out[""] and gs.get("working_visualisations"):  # machines drawn only by visualisations
                out = {"": [l for v in gs["working_visualisations"] if v.get("always_draw")
                            for l in layers(v.get("animation"))]}
    elif t == "lab":
        out = {"": layers(p.get("off_animation") or p.get("on_animation"))}
    elif t in ("container", "logistic-container"):
        out = {"": layers(p.get("picture") or p.get("animation"))}
    elif t == "electric-pole":
        out = {"": layers(p.get("pictures"))}
    elif t == "beacon":
        gs = p.get("graphics_set") or {}
        out = {"": [l for a in gs.get("animation_list") or [] for l in layers(a.get("animation"))]
               or layers(p.get("base_picture"))}
    elif t in ("constant-combinator", "arithmetic-combinator", "decider-combinator", "pump", "storage-tank",
               "boiler", "offshore-pump", "radar", "accumulator", "solar-panel", "lamp", "roboport", "reactor",
               "generator", "wall", "gate", "ammo-turret", "electric-turret", "fluid-turret", "artillery-turret",
               "thruster", "cargo-landing-pad", "fusion-reactor", "fusion-generator", "lightning-attractor",
               "asteroid-collector", "simple-entity-with-owner", "heat-pipe"):
        spec = (p.get("sprites") or p.get("animations") or p.get("pictures") or p.get("picture")
                or (p.get("graphics_set") or {}).get("animation") or p.get("structure") or p.get("animation")
                or p.get("base_picture") or p.get("chargable_graphics", {}).get("picture")
                or (p.get("graphics_set") or {}).get("base_visualisation", {}).get("animation"))
        if isinstance(spec, dict) and ("north" in spec or "sheet" in spec or "sheets" in spec):
            out = {DIRS[d]: four_way(spec, d) for d in range(4)}
        elif isinstance(spec, dict) and "picture" in spec:  # storage tank
            out = {"": four_way(spec["picture"], 0)}
        else:
            out = {"": layers(spec)}
    elif t == "transport-belt":
        out = _belt_rows(p.get("belt_animation_set"))
    elif t == "underground-belt":
        belt = _belt_rows(p.get("belt_animation_set"))
        st = p.get("structure") or {}
        for d in range(4):
            for kind in ("in", "out"):
                out[f"{kind}:{DIRS[d]}"] = belt.get(DIRS[d], []) + four_way(st.get(f"direction_{kind}"), d)
    elif t in ("loader", "loader-1x1"):
        belt = _belt_rows(p.get("belt_animation_set"))
        st = p.get("structure") or {}
        for d in range(4):
            for kind in ("in", "out"):
                out[f"{kind}:{DIRS[d]}"] = belt.get(DIRS[d], []) + four_way(st.get(f"direction_{kind}"), d)
    elif t == "splitter":
        belt = _belt_rows(p.get("belt_animation_set"))
        for d in range(4):
            ox, oy = (0.5, 0) if d in (0, 2) else (0, 0.5)  # the two belts under it
            under = _offset(belt.get(DIRS[d], []), -ox, -oy) + _offset(belt.get(DIRS[d], []), ox, oy)
            out[DIRS[d]] = under + four_way(p.get("structure"), d) + four_way(p.get("structure_patch"), d)
    elif t == "pipe":
        pics = p.get("pictures") or {}
        out = {k: layers(pics.get(k)) for k in PIPE_KEYS if pics.get(k)}
    elif t == "pipe-to-ground":
        out = {DIRS[d]: four_way(p.get("pictures"), d) for d in range(4)}
    elif t == "inserter":
        out = {DIRS[d]: four_way(p.get("platform_picture"), d) for d in range(4)}
        out["hand_base"] = layers(p.get("hand_base_picture"))
        out["hand_open"] = layers(p.get("hand_open_picture"))
    return {k: v for k, v in out.items() if v}


def build(raw):
    """full prototype dump -> {name: {"type": t, "variants": {...}}}"""
    table = {}
    for t in TYPES:
        for name, p in (raw.get(t) or {}).items():
            if p.get("hidden"):
                continue
            try:
                v = _variants(t, p, None)
            except (TypeError, KeyError, IndexError, AttributeError):
                continue
            if v:
                table[name] = {"type": t, "variants": v}
    return table


def build_file(dump_path, out_path):
    raw = json.loads(dump_path.read_text(encoding="utf-8"))
    out_path.write_text(json.dumps(build(raw)), encoding="utf-8")


@lru_cache(maxsize=512)
def _image(file):
    return Image.open(io.BytesIO(icons._read(file))).convert("RGBA")


def _layer_image(l):
    img = _image(l["file"])
    box = (l["x"], l["y"], l["x"] + l["w"], l["y"] + l["h"])
    if box[2] > img.width or box[3] > img.height:
        box = (0, 0, min(l["w"], img.width), min(l["h"], img.height))
    img = img.crop(box)
    f = 2 * l.get("scale", 1) * PX / 64
    img = img.resize((max(1, round(img.width * f)), max(1, round(img.height * f))), Image.LANCZOS)
    if l.get("tint"):
        img = icons._tint(img, l["tint"])
    return img


def _place(parts):
    """[(image, centre x px, centre y px)] relative to the entity centre -> image centred on the entity"""
    if not parts:
        return None
    half_w = max(abs(cx) + im.width / 2 for im, cx, cy in parts)
    half_h = max(abs(cy) + im.height / 2 for im, cx, cy in parts)
    W, H = math.ceil(half_w) * 2, math.ceil(half_h) * 2
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for im, cx, cy in parts:
        canvas.alpha_composite(im, (round(W / 2 + cx - im.width / 2), round(H / 2 + cy - im.height / 2)))
    return canvas


def render(entry, variant):
    v = entry["variants"]
    if entry["type"] == "inserter":
        return _inserter(v, variant)
    ls = v.get(variant) or v.get("") or next(iter(v.values()))
    parts = []
    for l in ls:
        try:
            im = _layer_image(l)
        except (OSError, FileNotFoundError, KeyError, ValueError):
            continue
        ox, oy = l.get("offset") or [0, 0]
        parts.append((im, (l["shift"][0] + ox) * PX, (l["shift"][1] + oy) * PX))
    return _place(parts)


def _inserter(v, variant):
    """platform for its direction, and the arm resting over the drop side"""
    d = variant if variant in DIRS else "N"
    parts = []
    for l in v.get(d) or []:
        try:
            parts.append((_layer_image(l), l["shift"][0] * PX, l["shift"][1] * PX))
        except (OSError, FileNotFoundError, KeyError, ValueError):
            pass
    # the entity faces its pickup side; the arm points the other way, towards the drop
    angle = {"N": 180, "E": 90, "S": 0, "W": 270}[d]  # PIL rotates counter-clockwise; sprites point up
    vec = {"N": (0, 1), "E": (-1, 0), "S": (0, -1), "W": (1, 0)}[d]
    try:
        base = _layer_image(v["hand_base"][0])
        base = base.resize((base.width, max(1, round(PX * 0.55))), Image.LANCZOS).rotate(angle, expand=True)
        hand = _layer_image(v["hand_open"][0])
        hand = hand.resize((round(hand.width * 0.6), round(hand.height * 0.6)), Image.LANCZOS).rotate(angle, expand=True)
        parts.append((base, vec[0] * PX * 0.27, vec[1] * PX * 0.27 - PX * 0.1))
        parts.append((hand, vec[0] * PX * 0.55, vec[1] * PX * 0.55 - PX * 0.1))
    except (KeyError, IndexError, OSError, FileNotFoundError, ValueError):
        pass
    return _place(parts)


def png(table, name, variant):
    entry = table.get(name)
    if not entry:
        return None
    img = render(entry, variant)
    if img is None or not img.getbbox():
        return None
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
