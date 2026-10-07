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

from bpgen.config import PATHS
USER_MODS = PATHS["user_mods"]
PACK = ROOT / "mods" / "pack"
DUMP = ROOT / "data" / "pack-dump.json"
STAMP = ROOT / "data" / "pack-dump.stamp"
OWN = {"bpgen-test", "bpgen-companion", "zzz-bpgen-data"}


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
    return hashlib.sha1(json.dumps([enabled, startup, versions], sort_keys=True, default=str).encode()).hexdigest()


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
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
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
        "lab", "resource", "planet"]
# icon paths are kept (tiny) for the web preview
DROP_KEYS = {"graphics_set", "animation", "picture", "pictures", "working_sound", "sprites",
             "structure", "belt_animation_set", "hand_base_picture", "hand_closed_picture", "hand_open_picture",
             "hand_base_shadow", "hand_closed_shadow", "hand_open_shadow", "platform_picture", "circuit_connector",
             "water_reflection", "open_sound", "close_sound", "damaged_trigger_effect", "corpse", "dying_explosion"}
DATA = ROOT / "data" / "pack-data.json"
SPRITES = ROOT / "data" / "pack-sprites.json"


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
                       "container", "logistic-container", "roboport", "constant-combinator", "lab")
        # hidden entities are script helpers (e.g. thousands of crafting entities from the "Fixed" mods)
        small[t] = {n: clean(p) for n, p in raw.get(t, {}).items() if not (entity and p.get("hidden"))}
    # tiles: only which fluid they give (water from an offshore pump is raw too)
    small["tile"] = {n: {"fluid": p["fluid"]} for n, p in raw.get("tile", {}).items() if p.get("fluid")}
    DATA.write_text(json.dumps(small), encoding="utf-8")
    # top-down entity sprites for the preview (graphics are dropped above)
    from bpgen import sprites
    SPRITES.write_text(json.dumps(sprites.build(raw)), encoding="utf-8")


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


def game_wants(active):
    """from the game's data stage (mod zzz-bpgen-data, fnative loader): bpgen's copy is stale and the game runs the
    user's own mods (not a test's mod folder) -> what to send: (types, keys to leave out), else None"""
    if up_to_date():
        return None
    # (the user's mods but for a few: one the game couldn't load (a broken zip), a name a mod adds to `mods` for
    # another's sake; a test's mod folder is hundreds off)
    run, user = set(active) - OWN, user_enabled()
    if len(run - user) > 5 or len(user - run) > 5:
        print(f"bpgen: not the user's mods (foreign {sorted(run - user)[:5]}, missing {sorted(user - run)[:5]})")
        return None
    from bpgen import sprites
    types = sorted(set(KEEP) | set(sprites.TYPES) | {"tile"})
    # (sounds and effects: neither the planner nor the sprites read them)
    skip = ["working_sound", "open_sound", "close_sound", "mined_sound", "repair_sound", "rotated_sound", "build_sound",
            "vehicle_impact_sound", "damaged_trigger_effect", "dying_explosion", "dying_trigger_effect", "corpse",
            "water_reflection", "created_effect", "transitions", "transitions_between_transitions", "variants",
            "effect_color", "effect_color_secondary"]
    return types, skip


def from_game(text):
    """the game's data.raw (the types game_wants named) -> the dump, trimmed, stamped: as if dumped headless"""
    DUMP.parent.mkdir(exist_ok=True)
    DUMP.write_text(text, encoding="utf-8")
    trim()
    STAMP.write_text(_stamp())
    mirror()


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
