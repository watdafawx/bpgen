"""Read a blueprint of a player's base: what it makes, at what rate, with which tech, and how big it is.

From what's placed only (blueprints don't store belt contents or furnace recipes): every assembler's recipe at
full speed, minus what other machines in the base eat, is what the base puts out. Science packs it puts out are
science targets, buildings are mall products; other surplus is reported as extra output.
"""
import base64
import json
import math
import zlib
from collections import Counter


def decode(bp_string):
    obj = json.loads(zlib.decompress(base64.b64decode(bp_string.strip()[1:])))
    if "blueprint_book" in obj:  # a book: every print in it
        ents = []
        for b in obj["blueprint_book"].get("blueprints") or []:
            ents += (b.get("blueprint") or {}).get("entities") or []
        return ents
    return (obj.get("blueprint") or {}).get("entities") or []


def _type_of(data, name):
    for t in ("assembling-machine", "furnace", "lab", "transport-belt", "underground-belt", "splitter", "inserter",
              "electric-pole", "mining-drill", "container", "logistic-container", "pipe", "pipe-to-ground",
              "beacon", "rocket-silo"):
        if name in data.raw.get(t, {}):
            return t
    return None


def analyze(data, bp_string):
    ents = decode(bp_string)
    if not ents:
        raise ValueError("no entities in that blueprint")
    types = Counter()
    by_type = {}
    prod, cons, machines = Counter(), Counter(), Counter()
    chests = [(e["position"]["x"], e["position"]["y"]) for e in ents
              if _type_of(data, e["name"]) in ("container", "logistic-container")]
    mall_made = set()  # what machines put into chests (an inserter's reach from their edge) make
    for e in ents:
        n = e["name"]
        t = _type_of(data, n)
        types[t or "other"] += 1
        by_type.setdefault(t, Counter())[n] += 1
        r = data.raw["recipe"].get(e.get("recipe") or "")
        if t in ("assembling-machine", "furnace", "rocket-silo") and r:
            m = data.raw.get(t, {}).get(n, {})
            crafts = m.get("crafting_speed", 1) / r.get("energy_required", 0.5)  # per second, full speed, no modules
            machines[(e["recipe"], n)] += 1
            (x1, y1), (x2, y2) = m.get("collision_box") or ((-1, -1), (1, 1))
            reach = 2.6  # the machine's edge, an inserter, the chest
            px, py = e["position"]["x"], e["position"]["y"]
            if any(px + x1 - reach <= cx <= px + x2 + reach and py + y1 - reach <= cy <= py + y2 + reach
                   for cx, cy in chests):
                mall_made.update(res["name"] for res in r.get("results") or [])
            for res in r.get("results") or []:
                prod[res["name"]] += crafts * (res.get("amount") or res.get("amount_max", 1)) * res.get("probability", 1)
            for ing in r.get("ingredients") or []:
                cons[ing["name"]] += crafts * ing.get("amount", 1)
    xs = [e["position"]["x"] for e in ents]
    ys = [e["position"]["y"] for e in ents]
    labs = by_type.get("lab", Counter())
    lab = labs.most_common(1)[0][0] if labs else None
    lab_inputs = set((data.raw.get("lab", {}).get(lab) or {}).get("inputs") or [])
    placeable = {i.get("place_result") for t in ("item", "item-with-entity-data", "rail-planner")
                 for i in data.raw.get(t, {}).values() if i.get("place_result")}
    item_of = {}  # entity -> the item that places it
    for t in ("item", "item-with-entity-data"):
        for n, i in data.raw.get(t, {}).items():
            if i.get("place_result"):
                item_of.setdefault(i["place_result"], n)
    science, mall, extra = {}, [], {}
    for item, p in prod.items():
        net = p - cons.get(item, 0)
        if net <= 1e-6:
            continue
        if item in lab_inputs or (data.raw.get("tool", {}).get(item) and "science" in item):
            science[item] = round(net * 60, 1)
        elif item in mall_made or item in placeable or item in item_of.values() or data.raw.get("item", {}).get(item, {}).get("place_result"):
            mall.append(item)
        else:
            extra[item] = round(net * 60, 1)

    def most(t, default=None):
        c = by_type.get(t)
        return c.most_common(1)[0][0] if c else default
    furnaces = by_type.get("furnace", Counter()) + Counter({n: c for n, c in by_type.get("assembling-machine", Counter()).items()
                                                             if "smelting" in data.raw["assembling-machine"][n].get("crafting_categories", [])})
    assemblers = Counter({n: c for n, c in by_type.get("assembling-machine", Counter()).items()
                          if n not in furnaces and "crafting" in data.raw["assembling-machine"][n].get("crafting_categories", [])})
    return {
        "entities": len(ents),
        "size": [math.ceil(max(xs) - min(xs)) + 1, math.ceil(max(ys) - min(ys)) + 1],
        "machines": [{"recipe": r, "machine": m, "count": c} for (r, m), c in machines.most_common()],
        "science": science,  # pack -> per minute it puts out
        "mall": sorted(mall),
        "extra": dict(sorted(extra.items(), key=lambda kv: -kv[1])[:12]),
        "tech": {
            "belt": most("transport-belt", "transport-belt"),
            "inserters": _with_long_reach(data, sorted(by_type.get("inserter", Counter()))),
            "assembler": assemblers.most_common(1)[0][0] if assemblers else "assembling-machine-2",
            "furnace": furnaces.most_common(1)[0][0] if furnaces else "stone-furnace",
            "lab": lab,
            "labs": sum(labs.values()),
        },
        "counts": {k: v for k, v in types.most_common() if k},
    }


def _with_long_reach(data, names):
    """a base with only 1-tile inserters still has a long-handed one researched (it unlocks alongside): add the
    slowest 2-tile inserter, so recipes with a second input belt can be rebuilt"""
    from bpgen import calibrate
    if not names or any(calibrate.reach(data, n) == 2 for n in names):
        return names
    long = [n for n in calibrate.template_inserters(data, None, 2)]
    if not long:
        return names
    slowest = min(long, key=lambda n: (data.raw["inserter"][n].get("rotation_speed", 1), n))
    return sorted(set(names) | {slowest})
