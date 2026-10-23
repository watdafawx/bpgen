"""Test recipes with 7-11 item ingredients (no vanilla or Space Age recipe has more than 6): the same definitions go
into a Data's raw tables (golden_test, rowsolve_test) and, as a tiny mod's data.lua, into a game
(harness/many_items_check.py). Each makes its own item from vanilla intermediates.

    with many_items.added(data): ...      # the recipes and items in data.raw, removed again after
    many_items.write_mod(folder)          # bpgen-test-recipes: info.json + data.lua
    with many_items.reach3(data, calib):  # a fake inserter reaching 3 tiles, calibrated as the long-handed one
"""
import contextlib
import copy
import json
from pathlib import Path

INGREDIENTS = [("iron-gear-wheel", 2), ("electronic-circuit", 2), ("copper-cable", 3), ("iron-plate", 1),
               ("copper-plate", 1), ("steel-plate", 1), ("plastic-bar", 1), ("stone-brick", 1), ("pipe", 1),
               ("engine-unit", 1), ("advanced-circuit", 1)]
NAMES = {7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven"}
RECIPES = {f"bpgen-test-{word}": INGREDIENTS[:n] for n, word in NAMES.items()}
MOD = "bpgen-test-recipes"
REACH3 = "bpgen-test-three-long-inserter"


def recipe(name):
    # (assembling machines 2 and 3, 3x3, and the electromagnetic plant, 4x4)
    return {"type": "recipe", "name": name, "categories": ["crafting-with-fluid", "electromagnetics"], "energy_required": 2, "enabled": True,
            "ingredients": [{"type": "item", "name": i, "amount": a} for i, a in RECIPES[name]],
            "results": [{"type": "item", "name": name, "amount": 1}]}


def item(name):
    return {"type": "item", "name": name, "stack_size": 50, "icon": "__base__/graphics/icons/wooden-chest.png",
            "subgroup": "intermediate-product"}


@contextlib.contextmanager
def added(data):
    """the test recipes and their items in data.raw for the with block"""
    for name in RECIPES:
        data.raw["recipe"][name] = recipe(name)
        data.raw["item"][name] = item(name)
    try:
        yield list(RECIPES)
    finally:
        for name in RECIPES:
            data.raw["recipe"].pop(name, None)
            data.raw["item"].pop(name, None)


@contextlib.contextmanager
def reach3(data, calib):
    """an electric inserter picking 3 tiles out and dropping 3 tiles in (the pack's planetaris three-long-handed
    reaches as far), calibrated with the long-handed inserter's rows"""
    proto = copy.deepcopy(data.raw["inserter"]["long-handed-inserter"])
    proto.update(name=REACH3, pickup_position=[0, -3], insert_position=[0, 3.2])
    data.raw["inserter"][REACH3] = proto
    rows = {k.replace("long-handed-inserter|", REACH3 + "|", 1): v for k, v in calib.items()
            if k.startswith("long-handed-inserter|")}
    calib.update(rows)
    try:
        yield REACH3
    finally:
        data.raw["inserter"].pop(REACH3, None)
        for k in rows:
            calib.pop(k, None)


def _lua(v):
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {_lua(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "{" + ", ".join(_lua(x) for x in v) + "}"
    if isinstance(v, bool):
        return "true" if v else "false"
    return json.dumps(v)


def write_mod(folder):
    """the test recipes as a mod in folder/bpgen-test-recipes (info.json, data.lua) -> its path"""
    d = Path(folder) / MOD
    d.mkdir(parents=True, exist_ok=True)
    (d / "info.json").write_text(json.dumps({"name": MOD, "version": "0.1.0", "title": "bpgen test recipes",
                                             "author": "bpgen", "factorio_version": "2.1",
                                             "dependencies": ["base"]}, indent=1), encoding="utf-8")
    protos = [item(n) for n in RECIPES] + [recipe(n) for n in RECIPES]
    (d / "data.lua").write_text("data:extend({\n" + ",\n".join("  " + _lua(p) for p in protos) + "\n})\n",
                                encoding="utf-8")
    return d
