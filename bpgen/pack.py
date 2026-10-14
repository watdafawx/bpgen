"""A private mirror of the user's mod folder for headless runs.

Mods are symlinked (no copies), mod-list.json and mod-settings.dat are copied so test runs never touch the
real ones. `sync()` refreshes the mirror and re-dumps prototype data when the real mod list or settings change.
"""
import hashlib
import json
import struct
import os
import shutil
import subprocess
from pathlib import Path

from bpgen import settings_dat
from bpgen.harness import FACTORIO, ROOT, RUN

from bpgen import config
from bpgen.config import PATHS
USER_MODS = PATHS["user_mods"]
PACK = ROOT / "mods" / "pack"
DUMP = ROOT / "data" / "pack-dump.json"
STAMP = ROOT / "data" / "pack-dump.stamp"
OWN = {"bpgen-test", "zzz-bpgen"}


def _stamp():
    """what prototypes depend on: the mod list and *startup* settings. Runtime settings (changed in game all
    the time) live in the same file but can't change prototypes, so they don't force a 2-minute re-dump."""
    mods = json.loads((USER_MODS / "mod-list.json").read_text(encoding="utf-8-sig"))["mods"]
    enabled = sorted(m["name"] for m in mods if m.get("enabled"))
    try:
        startup = settings_dat.read(USER_MODS / "mod-settings.dat").get("startup", {})
    except (OSError, ValueError, struct.error):  # unreadable: fall back to the whole file
        startup = hashlib.sha1((USER_MODS / "mod-settings.dat").read_bytes()).hexdigest()
    versions = sorted(p.name for p in USER_MODS.iterdir() if p.suffix == ".zip")  # updated mods have new zip names
    # (and what bpgen keeps: a type it starts reading is dumped at the next game start)
    # ("headless": copies the game's data stage handed over before 0.24.3 missed some mods' final fixes)
    return hashlib.sha1(json.dumps([enabled, startup, versions, sorted(KEEP), "headless"], sort_keys=True,
                                   default=str).encode()).hexdigest()


def mirror():
    PACK.mkdir(parents=True, exist_ok=True)
    wanted = {p.name for p in USER_MODS.iterdir() if (p.suffix == ".zip" or (p.is_dir() and (p / "info.json").exists()))
              and p.name.split("_")[0] not in OWN}
    for link in PACK.iterdir():
        if link.name in ("mod-list.json", "mod-settings.dat") or link.name in OWN:
            continue
        if link.name not in wanted:
            link.unlink() if link.is_symlink() or link.is_file() else shutil.rmtree(link)
    for name in wanted:
        dest = PACK / name
        if not dest.exists() and not dest.is_symlink():
            os.symlink(USER_MODS / name, dest, target_is_directory=(USER_MODS / name).is_dir())
    mods = json.loads((USER_MODS / "mod-list.json").read_text(encoding="utf-8-sig"))
    mods["mods"] = [m for m in mods["mods"] if m["name"] not in OWN]
    (PACK / "mod-list.json").write_text(json.dumps(mods, indent=1))
    shutil.copy2(USER_MODS / "mod-settings.dat", PACK / "mod-settings.dat")


def dump():
    from bpgen.harness import _RunLock
    with _RunLock(None, None):
        _dump()


def _dump():
    out = RUN / "script-output" / "data-raw-dump.json"
    out.unlink(missing_ok=True)
    p = subprocess.run([str(FACTORIO), "--config", str(RUN / "config.ini"), "--mod-directory", str(PACK), "--dump-data"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                       env=config.plain_env())
    (RUN / "dump.out").write_text(p.stdout + p.stderr, encoding="utf-8")
    if not out.exists():
        raise RuntimeError("pack dump failed, see run/dump.out:\n" + (p.stdout + p.stderr)[-2000:])
    DUMP.parent.mkdir(exist_ok=True)
    shutil.move(out, DUMP)
    trim()


KEEP = ["recipe", "assembling-machine", "furnace", "rocket-silo", "inserter", "transport-belt", "underground-belt",
        "splitter", "loader", "loader-1x1", "electric-pole", "item", "fluid", "module", "beacon", "recipe-category",
        "quality", "technology", "pipe", "pipe-to-ground", "container", "logistic-container", "roboport",
        "constant-combinator",
        # the recipe picker: the crafting menu's tabs and rows
        "item-group", "item-subgroup",
        # every item-like type (science packs are "tool", etc.) so the UI can show their icons
        "tool", "capsule", "ammo", "gun", "armor", "repair-tool", "item-with-entity-data", "rail-planner",
        "space-platform-starter-pack", "item-with-tags", "selection-tool", "spidertron-remote",
        # starter base: labs to place, resources and the start planet's map settings to know what is mined there
        "lab", "resource", "planet",
        # the bus design: drills on the ore patches
        "mining-drill",
        # space platforms (the ship generator): what a ship is built of, and the asteroids it collects
        "thruster", "asteroid-collector", "space-platform-hub", "cargo-bay", "ammo-turret", "electric-turret",
        "solar-panel", "accumulator", "asteroid-chunk", "space-location", "space-connection", "pump",
        "decider-combinator"]
# icon paths are kept (tiny) for the web preview
DROP_KEYS = {"graphics_set", "animation", "picture", "pictures", "working_sound", "sprites",
             "structure", "belt_animation_set", "hand_base_picture", "hand_closed_picture", "hand_open_picture",
             "hand_base_shadow", "hand_closed_shadow", "hand_open_shadow", "platform_picture", "circuit_connector",
             "water_reflection", "open_sound", "close_sound", "damaged_trigger_effect", "corpse", "dying_explosion"}
DATA = ROOT / "data" / "pack-data.json"
SPRITES = ROOT / "data" / "pack-sprites.json"
ICONS = ROOT / "data" / "pack-icons.json"


def trim():
    """keep only what the planner reads (the full dump is ~1 GB, mostly graphics)"""
    raw = json.loads(DUMP.read_text(encoding="utf-8"))

    def clean(v):
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()
                    if k not in DROP_KEYS and not (k.startswith("pipe_") and k != "pipe_connections")}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v

    small = {}
    for t in KEEP:
        entity = t in ("assembling-machine", "furnace", "rocket-silo", "inserter", "transport-belt", "underground-belt",
                       "splitter", "loader", "loader-1x1", "electric-pole", "beacon", "pipe", "pipe-to-ground",
                       "container", "logistic-container", "roboport", "constant-combinator", "lab", "thruster",
                       "asteroid-collector", "space-platform-hub", "cargo-bay", "ammo-turret", "electric-turret",
                       "solar-panel", "accumulator", "pump", "decider-combinator")
        # hidden entities are script helpers (e.g. thousands of crafting entities from the "Fixed" mods)
        small[t] = {n: clean(p) for n, p in raw.get(t, {}).items() if not (entity and p.get("hidden"))}
    # tiles: only which fluid they give (water from an offshore pump is raw too)
    # (and which are foundations: a space platform's floor)
    small["tile"] = {n: {k: p[k] for k in ("fluid", "is_foundation") if p.get(k)} for n, p in raw.get("tile", {}).items()
                     if p.get("fluid") or p.get("is_foundation")}
    DATA.write_text(json.dumps(small), encoding="utf-8")
    # top-down entity sprites for the preview (graphics are dropped above)
    from bpgen import sprites
    SPRITES.write_text(json.dumps(sprites.build(raw)), encoding="utf-8")
    ICONS.write_text(json.dumps(icon_specs(raw), separators=(",", ":")), encoding="utf-8")


ICON_KIND = {"fluid": "fluid", "recipe": "recipe", "space-location": "space-location", "planet": "space-location",
             "virtual-signal": "virtual-signal", "quality": "quality", "item-group": "item-group"}
ICON_SKIP = {"technology", "item-subgroup", "tile", "shortcut", "tips-and-tricks-item", "mod-data"}


def icon_specs(raw):
    """the prototypes' icons for the web window: "item" / "fluid" / "recipe" / "entity" / ... -> name -> its layers,
    ";" between them, each "<mod>/<path>|<icon size>[|<scale>|<shift x>|<shift y>|<r,g,b,a>]". The game reads it
    from fse's note "bpgen.icons" (web.lua). Made from the dump: every mod's final fixes included."""
    def num(x):
        return f"{x:.4g}"

    def xy(v, k, i):
        return v.get(k, 0) if isinstance(v, dict) else (v[i] if len(v) > i else 0)

    def tint(c):
        if isinstance(c, dict):
            c = [c.get("r", 0), c.get("g", 0), c.get("b", 0), c.get("a", 1)]
        elif isinstance(c, list):
            c = (list(c) + [0, 0, 0, 1][len(c):])[:4]
        else:
            return None
        if any(v > 1 for v in c):
            c = [v / 255 for v in c]
        return ",".join(num(v) for v in c)

    def spec(p):
        layers = p.get("icons") or ([{"icon": p["icon"], "icon_size": p.get("icon_size")}] if p.get("icon") else None)
        if not layers:
            return None
        out = []
        for i, l in enumerate(layers):
            icon = l.get("icon")
            if not isinstance(icon, str) or not icon.startswith("__") or "__/" not in icon[2:]:
                return None
            mod, rest = icon[2:].split("__/", 1)
            size = l.get("icon_size") or p.get("icon_size") or 64
            s = f"{mod}/{rest}|{size:g}" if isinstance(size, float) else f"{mod}/{rest}|{size}"
            sh = l.get("shift")
            tn = tint(l.get("tint"))
            if l.get("scale") or sh or tn or i > 0:
                s += (f"|{num(l.get('scale') or 32 / size)}|{num(xy(sh, 'x', 0) if sh else 0)}|"
                      f"{num(xy(sh, 'y', 1) if sh else 0)}|{tn or ''}")
            out.append(s)
        return ";".join(out)

    out = {}
    for t, protos in raw.items():
        if t in ICON_SKIP or not isinstance(protos, dict):
            continue
        for name, p in protos.items():
            if not isinstance(p, dict):
                continue
            kind = ICON_KIND.get(t) or ("item" if p.get("stack_size") else None) or (
                "entity" if p.get("collision_box") or p.get("selection_box") or p.get("max_health") else None)
            if kind and name not in out.get(kind, {}):
                s = spec(p)
                if s:
                    out.setdefault(kind, {})[name] = s
    return out


def user_enabled():
    """the user's enabled mods as the game sees them: listed enabled in mod-list.json, plus mods in the folder that
    aren't listed (the game enables a new mod by default)"""
    listed = {m["name"]: m.get("enabled", True)
              for m in json.loads((USER_MODS / "mod-list.json").read_text(encoding="utf-8-sig"))["mods"]}
    import zipfile
    present = set()
    for p in USER_MODS.iterdir():
        try:
            if p.suffix == ".zip":  # (its name inside: a zip's file name needn't match)
                with zipfile.ZipFile(p) as z:
                    info = next(n for n in z.namelist() if n.count("/") == 1 and n.endswith("/info.json"))
                    present.add(json.loads(z.read(info).decode("utf-8-sig"))["name"])
            elif p.is_dir() and (p / "info.json").exists():
                present.add(json.loads((p / "info.json").read_text(encoding="utf-8-sig"))["name"])
        except (OSError, ValueError, KeyError, StopIteration, zipfile.BadZipFile):
            pass
    return ({n for n, on in listed.items() if on} | {n for n in present if n not in listed}) - OWN


def users_game(active):
    """the game runs the user's own mods (not a test's mod folder): `active` is its script.active_mods"""
    # (the user's mods but for a few: one the game couldn't load (a broken zip), a name a mod adds to `mods` for
    # another's sake; a test's mod folder is hundreds off)
    run, user = set(active) - OWN, user_enabled()
    if len(run - user) > 5 or len(user - run) > 5:
        print(f"bpgen: not the user's mods (foreign {sorted(run - user)[:5]}, missing {sorted(user - run)[:5]})")
        return False
    return True


def up_to_date():
    return DUMP.exists() and STAMP.exists() and STAMP.read_text() == _stamp()


def sync(force=False):
    """Mirror the user's mods and re-dump if their mod list or settings changed. Returns True if re-dumped."""
    if not force and DUMP.exists() and STAMP.exists() and STAMP.read_text() == _stamp():
        return False
    mirror()
    dump()
    STAMP.write_text(_stamp())
    return True


if __name__ == "__main__":
    print("re-dumped" if sync(force=True) else "up to date")
