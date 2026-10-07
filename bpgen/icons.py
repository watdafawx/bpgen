"""Icons for the web UI, read from the game's data folder or from mod zips, with layered icons composited.

Factorio 2.0 icon layers: each `icons` entry has icon, icon_size, scale, shift, tint. Sizes are relative to a
32 px "expected" icon: a layer is drawn at icon_size * scale * 2 px on a 64 px canvas, and its default scale is
32 / icon_size (i.e. it fills the icon). Shift is in those 32 px units too.
"""
import io
import json
import zipfile
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops

from bpgen import pack

from bpgen.config import PATHS
GAME_DATA = PATHS["game_data"]
CANVAS = 64


@lru_cache(maxsize=1)
def _mod_index():
    """mod name -> (path, zip root or None), newest version wins"""
    index, versions = {}, {}
    for p in pack.USER_MODS.iterdir():
        try:
            if p.suffix == ".zip":
                with zipfile.ZipFile(p) as z:
                    info_name = next(n for n in z.namelist() if n.count("/") == 1 and n.endswith("/info.json"))
                    info = json.loads(z.read(info_name).decode("utf-8-sig"))
                    root = info_name.rsplit("/", 1)[0]
            elif (p / "info.json").exists():
                info = json.loads((p / "info.json").read_text(encoding="utf-8-sig"))
                root = None
            else:
                continue
        except (StopIteration, ValueError, zipfile.BadZipFile, OSError):
            continue
        v = tuple(int(x) for x in info.get("version", "0").split(".") if x.isdigit())
        if v >= versions.get(info["name"], ()):
            versions[info["name"]] = v
            index[info["name"]] = (p, root)
    return index


@lru_cache(maxsize=2048)
def _read(path):
    """'__mod__/graphics/x.png' -> bytes"""
    mod, _, rest = path[2:].partition("__/")
    if (GAME_DATA / mod).is_dir():
        return (GAME_DATA / mod / rest).read_bytes()
    where = _mod_index().get(mod)
    if not where:
        raise FileNotFoundError(path)
    p, root = where
    if root is None:
        return (p / rest).read_bytes()
    with zipfile.ZipFile(p) as z:
        return z.read(f"{root}/{rest}")


def _layers(proto):
    if proto.get("icons"):
        return proto["icons"], proto.get("icon_size", 64)
    if proto.get("icon"):
        return [{"icon": proto["icon"], "icon_size": proto.get("icon_size", 64)}], proto.get("icon_size", 64)
    return None, None


def _tint(img, tint):
    if isinstance(tint, dict):
        c = [tint.get(k, 1 if k == "a" else 0) for k in "rgba"]
    else:
        c = list(tint) + [1] * (4 - len(tint))
    if max(c) > 1:  # 0-255 form
        c = [x / 255 for x in c]
    layer = Image.new("RGBA", img.size, tuple(int(max(0, min(1, x)) * 255) for x in c))
    return ImageChops.multiply(img, layer)


def render(proto):
    """composite a prototype's icon layers onto a 64 px canvas -> PIL image, or None"""
    layers, default_size = _layers(proto)
    if not layers:
        return None
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    for layer in layers:
        size = layer.get("icon_size", default_size) or 64
        try:
            img = Image.open(io.BytesIO(_read(layer["icon"]))).convert("RGBA")
        except (OSError, KeyError, FileNotFoundError):
            continue
        img = img.crop((0, 0, min(size, img.width), min(size, img.height)))  # first mip level
        scale = layer.get("scale", 32 / size)
        px = max(1, round(size * scale * 2))
        img = img.resize((px, px), Image.LANCZOS)
        if layer.get("tint"):
            img = _tint(img, layer["tint"])
        shift = layer.get("shift") or [0, 0]
        sx, sy = (shift["x"], shift["y"]) if isinstance(shift, dict) else shift
        x = round(CANVAS / 2 + sx * 2 - px / 2)
        y = round(CANVAS / 2 + sy * 2 - px / 2)
        canvas.alpha_composite(img, (x, y)) if 0 <= x and 0 <= y and x + px <= CANVAS and y + px <= CANVAS \
            else canvas.paste(img, (x, y), img)
    return canvas


@lru_cache(maxsize=4096)
def png(data_id, key, lookup):
    """PNG bytes for `key`; lookup(key) -> prototypes to try in order (recipes fall back to their product)"""
    for proto in lookup(key):
        img = render(proto)
        if img is not None and img.getbbox():
            out = io.BytesIO()
            img.save(out, "PNG")
            return out.getvalue()
    return None
