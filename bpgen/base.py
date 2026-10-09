"""Starter base: everything a science target needs, from ore belts up, as a blueprint book.

solve() walks the recipes from the science packs down to mined resources and sizes every step; plan_base() turns
each step into a section (a smelting or production line, the labs, the mall) using the planners that already
exist, so every section is one that verifies on its own. Sections take and give labelled belts; the book's
description lists them in bus order.
"""
import base64
import json
import math
import zlib
from dataclasses import dataclass, field

from bpgen import planner

CORE_PACKS = 2  # the first packs of the tech tree make the core book; later ones are add-ons
ADDON_PACKS = 6  # add-ons offered after the core


def _ingredients(tech):
    unit = tech.get("unit") or {}
    return [i[0] if isinstance(i, list) else i.get("name") for i in unit.get("ingredients") or []]


def science_info(data, lab="lab", picks=("assembling-machine-2", "stone-furnace")):
    """the packs this lab takes, each with whether the start planet's resources make it (and what's missing if not).
    Order: the two most used makeable packs (the core), then the other makeable ones by recipe tree size, then the
    rest. Works for any overhaul's packs."""
    from collections import Counter
    uses = Counter(p for t in data.raw.get("technology", {}).values() if not t.get("hidden") for p in _ingredients(t))
    raw = raw_items(data)
    info = []
    for pk in (data.raw.get("lab", {}).get(lab) or {}).get("inputs") or []:
        if not uses[pk]:
            continue
        try:
            st = solve(data, {pk: 1}, list(picks), raw)
        except RecursionError:
            continue
        missing = sorted(i for i, x in st.items() if not x.recipe and i not in raw)
        info.append({"pack": pk, "makeable": not missing, "missing": missing[:4], "steps": len(st), "uses": uses[pk]})
    made = [i for i in info if i["makeable"]]
    core = sorted(made, key=lambda i: -i["uses"])[:CORE_PACKS]
    rest = sorted((i for i in made if i not in core), key=lambda i: (i["steps"], -i["uses"]))
    other = sorted((i for i in info if not i["makeable"]), key=lambda i: (len(i["missing"]), i["steps"]))
    return core + rest + other


def science_order(data, lab="lab"):
    return [i["pack"] for i in science_info(data, lab)]


def pack_progression(data, lab="lab"):
    """the lab's packs in the order research needs them: by the tech-tree depth of the first technology using each"""
    techs = data.raw.get("technology", {})
    depth = {}

    def d(name, seen=()):
        if name not in depth:
            t = techs.get(name, {})
            depth[name] = 1 + max((d(p, seen + (name,)) for p in t.get("prerequisites") or [] if p not in seen),
                                  default=0)
        return depth[name]
    first = {}
    for name, t in techs.items():
        if t.get("hidden"):
            continue
        for pk in _ingredients(t):
            first[pk] = min(first.get(pk, 1e9), d(name))
    inputs = (data.raw.get("lab", {}).get(lab) or {}).get("inputs") or []
    return sorted((pk for pk in inputs if pk in first), key=lambda pk: (first[pk], pk))


def unit_time(data, packs, default=30):
    """median research time per unit of the technologies that take exactly these packs (or a subset of them)"""
    want = set(packs)
    times = [t["unit"].get("time", default) for t in data.raw.get("technology", {}).values()
             if t.get("unit") and set(_ingredients(t)) == want]
    if not times:
        times = [t["unit"].get("time", default) for t in data.raw.get("technology", {}).values()
                 if t.get("unit") and set(_ingredients(t)) <= want and _ingredients(t)]
    return sorted(times)[len(times) // 2] if times else default


def packs_for(data, lab, addons):
    """the core packs plus the chosen add-ons (pack names; "military"/"chemical" pick the pack with that word)"""
    order = science_order(data, lab)
    core = order[:CORE_PACKS]
    out = list(core)
    for a in addons or []:
        pk = a if a in order else next((p for p in order[CORE_PACKS:] if a in p), None)
        if pk and pk not in out:
            out.append(pk)
    return out
SKIP_CATEGORIES = ("recycling",)  # recipes that turn things back into what they came from
MADE_HERE = set()  # kept for callers: what's raw now comes from the start planet (see raw_items)


@dataclass
class Step:
    item: str
    rate: float  # items/s
    recipe: str | None  # None: raw (mined or piped in)
    machine: str | None = None
    used_by: dict = field(default_factory=dict)  # item -> items/s it takes of this
    depth: int = 0  # 0 = raw, then one more than its deepest ingredient
    kind: str = "line"  # line | multi (a recipe with several products) | convert | flare | out
    of: str = None  # multi: the product it is sized for; convert / flare / out: the by-product it takes


START_PLANET = "nauvis"


def raw_items(data, planet=START_PLANET):
    """what the start planet gives without crafting: its resources' mining results (ores, stone, coal, crude oil...)
    and the fluids of its tiles (water for offshore pumps). Without planet data: every resource in the game."""
    pl = data.raw.get("planet", {}).get(planet) or next(iter(data.raw.get("planet", {}).values()), None)
    auto = ((pl or {}).get("map_gen_settings") or {}).get("autoplace_settings") or {}
    placed = set((auto.get("entity") or {}).get("settings") or {}) if pl else None
    tiles = set((auto.get("tile") or {}).get("settings") or {}) if pl else None
    out = set()
    for n, p in data.raw.get("resource", {}).items():
        if placed is not None and n not in placed:
            continue
        m = p.get("minable") or {}
        for r in m.get("results") or ([{"name": m["result"]}] if m.get("result") else []):
            out.add(r["name"])
    for n, t in data.raw.get("tile", {}).items():
        if t.get("fluid") and (tiles is None or n in tiles):
            out.add(t["fluid"])
    return out


def plate_items(data, raw):
    """what smelting makes straight from a raw item (iron and copper plate, stone brick...): what a bus head brings"""
    out = set()
    for r in data.raw["recipe"].values():
        ing, res = r.get("ingredients") or [], r.get("results") or []
        if (r.get("category", "crafting") == "smelting" and not r.get("hidden") and len(ing) == 1
                and ing[0].get("name") in raw and len(res) == 1 and res[0].get("type", "item") == "item"):
            out.add(res[0]["name"])
    return out


def _cached(data, key, make):
    """per loaded data: computed once (the data doesn't change while it's loaded)"""
    store = data.__dict__.setdefault("_base_cache", {})
    if key not in store:
        store[key] = make()
    return store[key]


def _makers(data):
    """item -> [(recipe name, recipe)] of the visible recipes with that one product (an index: scanning every recipe
    for each item was nearly all of a starter base's planning time with a big mod pack)"""
    def make():
        out = {}
        for rn, r in data.raw["recipe"].items():
            res = r.get("results") or []
            if not r.get("hidden") and len(res) == 1:
                out.setdefault(res[0].get("name"), []).append((rn, r))
        return out
    return _cached(data, "makers", make)


def machines_by_category(data, picks):
    """category -> the machine to use. picks: preferred machines (assembler, furnace...) tried first for any
    category they can craft; other categories get their first powered, non-variant machine"""
    return dict(_cached(data, ("machines", tuple(picks)), lambda: _machines_by_category(data, picks)))


def _machines_by_category(data, picks):
    cats = {}
    for t in ("assembling-machine", "furnace"):
        for n, m in data.raw.get(t, {}).items():
            if m.get("hidden") or m.get("energy_source", {}).get("type") == "void":
                continue
            for c in m.get("crafting_categories", []):
                cats.setdefault(c, []).append(n)
    unlock = unlock_depth(data)
    out = {}
    for c, ms in cats.items():
        chosen = next((p for p in picks if p in ms), None)
        out[c] = chosen or min(ms, key=lambda n: (unlock.get(n, 999), _proto(data, n).get("crafting_speed", 1), n))
    return out


def unlock_depth(data):
    """entity -> how deep in the tech tree the earliest recipe placing it unlocks (0 = from the start)"""
    return _cached(data, "unlock_depth", lambda: _unlock_depth(data))


def _unlock_depth(data):
    techs = data.raw.get("technology", {})
    depth = {}

    def d(name, seen=()):
        if name not in depth:
            t = techs.get(name, {})
            depth[name] = 1 + max((d(p, seen + (name,)) for p in t.get("prerequisites") or [] if p not in seen), default=0)
        return depth[name]
    recipes = {rn: r for rn, r in data.raw["recipe"].items() if not r.get("hidden")}  # not recycling and such
    recipe_at = {rn: 0 for rn, r in recipes.items() if r.get("enabled", True) is not False}
    for tn, t in techs.items():
        for e in t.get("effects") or []:
            if e.get("type") == "unlock-recipe" and e.get("recipe") in recipes:
                rn = e.get("recipe")
                recipe_at[rn] = min(recipe_at.get(rn, 999), d(tn))
    items = data.raw.get("item", {})
    out = {}
    for rn, at in recipe_at.items():
        for res in recipes[rn].get("results") or []:
            place = items.get(res.get("name"), {}).get("place_result")
            if place:
                out[place] = min(out.get(place, 999), at)
    return out


def _proto(data, name):
    return data.raw["assembling-machine"].get(name) or data.raw.get("furnace", {}).get(name, {})


def recipe_choices(data, item, machine_for):
    """every recipe a base could make `item` with (one product, a machine for it, not recycling)"""
    out = []
    for rn, r in _makers(data).get(item, ()):
        cat = r.get("category", "crafting")
        if cat in machine_for and not any(s in cat for s in SKIP_CATEGORIES):
            out.append(rn)
    return sorted(out, key=lambda n: (n != item, n))


def pick_recipe(data, item, machine_for, raw, avoid=()):
    """the recipe the base makes `item` with: named like it if possible, else the simplest; never recycling,
    never one needing `avoid` (what it is being made for: no loops)"""
    best = None
    for rn, r in _makers(data).get(item, ()):
        cat = r.get("category", "crafting")
        if cat not in machine_for or any(s in cat for s in SKIP_CATEGORIES):
            continue
        ings = [i["name"] for i in r.get("ingredients", [])]
        if any(i in avoid or i == item for i in ings):
            continue
        key = (rn != item, len(ings), sum(i not in raw for i in ings), rn)
        if best is None or key < best[0]:
            best = (key, rn)
    return best[1] if best else None


def _amt(e):
    return planner._amount(e)


def by_product_flows(data, recipe, byproducts):
    """per craft of a multi-product recipe: what's left of each product once by-products are converted.
    byproducts: {fluid: "flare" | "out" | {"convert": recipe}}. -> (flows {product: amount}, [(converter, its
    crafts per craft, the by-product it eats)])"""
    r = data.raw["recipe"][recipe]
    flows = {}
    for o in r.get("results") or []:
        flows[o["name"]] = flows.get(o["name"], 0) + _amt(o)
    for i in r.get("ingredients") or []:  # a product it also eats (coal liquefaction's heavy oil): what's left over
        if i["name"] in flows:
            flows[i["name"]] -= _amt(i)
    convs, done = [], set()
    for _ in range(6):  # converters can feed each other (heavy -> light -> gas): repeat until nothing changes
        changed = False
        for b, act in (byproducts or {}).items():
            c = act.get("convert") if isinstance(act, dict) else None
            if not c or b in done or flows.get(b, 0) <= 0:
                continue
            cr = data.raw["recipe"].get(c)
            c_in = sum(_amt(i) for i in (cr or {}).get("ingredients", []) if i["name"] == b)
            if not c_in:
                continue
            y = flows[b] / c_in
            flows[b] = 0
            for o in cr.get("results") or []:
                flows[o["name"]] = flows.get(o["name"], 0) + y * _amt(o)
            convs.append((c, y, b))
            done.add(b)
            changed = True
        if not changed:
            break
    return flows, convs


def solve(data, targets, picks, raw=None, overrides=None, fluid_plans=None):
    """targets: {item: items/s}; overrides: {item: recipe} the player chose; fluid_plans: {item: {"recipe": a
    recipe with several products, "byproducts": {product: "flare" | "out" | {"convert": recipe}}}}.
    -> {key: Step}, ordered raw-first (the order things go on the bus)"""
    raw = raw if raw is not None else raw_items(data)
    machine_for = machines_by_category(data, picks)
    overrides = overrides or {}
    fluid_plans = fluid_plans or {}
    steps = {}

    def machine_of(rn):
        return machine_for.get(data.raw["recipe"][rn].get("category", "crafting"))

    def multi(item, rate, by, path):
        """a product of a several-product recipe: size it with its converters, and burn or put out the rest"""
        fp = fluid_plans[item]
        rn = fp["recipe"]
        st = steps.get(item)
        if st is None:
            st = steps[item] = Step(item, 0.0, rn, machine_of(rn), kind="multi", of=item)
        st.rate += rate
        if by:
            st.used_by[by] = st.used_by.get(by, 0) + rate
        flows, convs = by_product_flows(data, rn, fp.get("byproducts"))
        if flows.get(item, 0) <= 0:
            raise planner.PlanError(f"{rn} doesn't end up making {item} with these by-product choices")
        crafts = rate / flows[item]
        for ing in data.raw["recipe"][rn].get("ingredients", []):
            if ing["name"] not in flows:  # its own products come back round from its outputs
                need(ing["name"], crafts * _amt(ing), item, path | {item})
        for c, y, b in convs:
            cr = data.raw["recipe"][c]
            main = (cr.get("results") or [{}])[0]
            key = f"{c}@{item}"
            cst = steps.get(key)
            if cst is None:
                cst = steps[key] = Step(main.get("name"), 0.0, c, machine_of(c), kind="convert", of=b)
            cst.rate += crafts * y * _amt(main)
            for ing in cr.get("ingredients", []):
                if ing["name"] != b:
                    need(ing["name"], crafts * y * _amt(ing), key, path | {item})
        for b, act in (fp.get("byproducts") or {}).items():
            left = flows.get(b, 0) * crafts
            if act in ("flare", "out") and left > 1e-9:
                key = f"{act}:{b}"
                lst = steps.get(key)
                if lst is None:
                    lst = steps[key] = Step(b, 0.0, None, None, kind=act, of=b)
                lst.rate += left

    def need(item, rate, by, path):
        if item in fluid_plans:
            return multi(item, rate, by, path)
        st = steps.get(item)
        if st is None:
            chosen = overrides.get(item)
            if chosen and chosen in recipe_choices(data, item, machine_for):
                rn = chosen
            else:
                rn = None if item in raw else pick_recipe(data, item, machine_for, raw, avoid=path)
            r = data.raw["recipe"].get(rn) if rn else None
            st = steps[item] = Step(item, 0.0, rn, machine_for.get(r.get("category", "crafting")) if r else None)
        st.rate += rate
        if by:
            st.used_by[by] = st.used_by.get(by, 0) + rate
        if not st.recipe:
            return
        r = data.raw["recipe"][st.recipe]
        made = next((_amt(o) for o in r["results"] if o["name"] == item), None) or _amt(r["results"][0])
        for ing in r.get("ingredients", []):
            need(ing["name"], rate * ing.get("amount", 1) / made, item, path | {item})

    for item, rate in targets.items():
        need(item, rate, None, frozenset())
    # bus order: deepest ingredients first
    depth = {}
    made_by = {}  # by-products (no step of their own) -> the step making them
    for key, st in steps.items():
        if st.kind == "multi":
            for o in data.raw["recipe"][st.recipe].get("results") or []:
                made_by.setdefault(o["name"], key)

    def d(item):
        if item not in depth:
            if item not in steps:
                depth[item] = d(made_by[item]) if item in made_by else 0
                return depth[item]
            st = steps[item]
            if st.kind in ("flare", "out"):
                depth[item] = d(st.of) + 1 if st.of in steps or st.of in made_by else 1
                return depth[item]
            r = data.raw["recipe"][st.recipe] if st.recipe else {}
            outs = {o["name"] for o in r.get("results") or []}
            ings = [i for i in r.get("ingredients", []) if i["name"] not in outs]  # not what it feeds back to itself
            depth[item] = 1 + max((d(i["name"]) for i in ings), default=-1)
        return depth[item]
    for item, st in steps.items():
        st.depth = d(item)
    return dict(sorted(steps.items(), key=lambda kv: (d(kv[0]), kv[0])))


# ---- sections -----------------------------------------------------------------------------------------------
EARLY_MALL = ["transport-belt", "underground-belt", "splitter", "burner-inserter", "inserter", "long-handed-inserter",
              "fast-inserter", "small-electric-pole", "medium-electric-pole", "pipe", "pipe-to-ground",
              "stone-furnace", "assembling-machine-1", "assembling-machine-2", "lab", "electric-mining-drill",
              "burner-mining-drill", "offshore-pump", "boiler", "steam-engine", "wooden-chest", "iron-chest",
              "repair-pack", "gun-turret", "firearm-magazine", "stone-wall", "radar"]
EARLY_INSERTERS = ("inserter", "long-handed-inserter")  # unless the player picks others
INTERMEDIATE_HEADROOM = 1.25  # intermediate lines are sized for this much more than the science needs
# plates more still: while the belts fill every consumer draws flat out, and the plates' priority splitters serve
# the deepest consumer's leg first; spare plates get the rest started sooner (measured on the vanilla red/green/chem
# base: chemical science at 15 min 0 -> 8/min, at ~25 min 0.8 -> 22/min, settled the same; stone furnaces are cheap)
SMELTING_HEADROOM = 1.6
RESEARCH_UNIT_TIME = 30  # seconds per unit of a typical early technology: sizes the labs


def mall_products(data, assembler):
    """the early buildings this assembler can make from items alone"""
    cats = set(_proto(data, assembler).get("crafting_categories", []))
    out = []
    for item in EARLY_MALL:
        r = data.raw["recipe"].get(item)
        if (r and not r.get("hidden") and r.get("category", "crafting") in cats and len(r.get("results") or []) == 1
                and all(i.get("type", "item") == "item" for i in r.get("ingredients", []))):
            out.append(item)
    return out


def labs_layout(data, lab, count, belt, packs, inserter="inserter", long_inserter="long-handed-inserter"):
    """labs in a row below 1-2 input belts (2 packs per belt): an inserter from the near belt, a long-handed one
    from the far belt, a pole beside every other lab. Belts on top, so the layout starts at (0, 0) like the
    other planners'. -> (entities, sources)"""
    size = planner._size(data.raw["lab"][lab])
    belts = [packs[i:i + 2] for i in range(0, len(packs), 2)]
    belts = [b + [None] * (2 - len(b)) for b in belts]
    if len(belts) > 2:
        raise planner.PlanError("labs: at most 4 science packs (2 belts)")
    width = count * size + 1
    ents, sources = [], []
    ins_y = len(belts)  # belts in rows 0..nb-1 (the near one last), inserters, then labs
    for i, lanes in enumerate(belts):
        y = ins_y - 1 - i
        for x in range(width):
            ents.append({"name": belt, "position": {"x": x + 0.5, "y": y + 0.5}, "direction": planner.EAST})
        sources.append({"kind": "belt", "position": {"x": 0.5, "y": y + 0.5}, "lanes": lanes,
                        "rates": {p: 1 for p in lanes if p}})
    for k in range(count):
        x0 = k * size
        ents.append({"name": lab, "position": {"x": x0 + size / 2, "y": ins_y + 1 + size / 2}})
        ents.append({"name": inserter, "position": {"x": x0 + 1.5, "y": ins_y + 0.5}, "direction": planner.NORTH})
        if len(belts) > 1:
            ents.append({"name": long_inserter, "position": {"x": x0 + 2.5, "y": ins_y + 0.5},
                         "direction": planner.NORTH})
        if k % 2 == 0 or k == count - 1:
            ents.append({"name": planner.POLE, "position": {"x": x0 + 0.5, "y": ins_y + 0.5}})
    return ents, sources


def lab_count(data, lab, rate, packs=None):
    """labs to keep up with `rate` packs/s of each kind, at the unit time of the technologies these packs research"""
    speed = data.raw["lab"][lab].get("researching_speed", 1)
    t = unit_time(data, packs) if packs else RESEARCH_UNIT_TIME
    return max(1, math.ceil(rate * t / speed - 1e-9))


def _decode(bp_string):
    return json.loads(zlib.decompress(base64.b64decode(bp_string[1:])))


def _encode(obj):
    return "0" + base64.b64encode(zlib.compress(json.dumps(obj, separators=(",", ":")).encode(), 9)).decode()


def plan_base(service, params, progress=None, cancel=None):
    """params: spm (per pack), addons ["military", "chemical"], belt, assembler, furnace, inserters, bonuses.
    -> {"sections": [...], "book": str, "bus": [...], "bring_in": {...}, "not_automated": [...]}"""
    from bpgen import labels, tiers

    data = service.data
    spm = float(params.get("spm") or 30)
    rate = spm / 60
    lab = params.get("lab") or "lab"
    targets = {k: v / 60 for k, v in (params.get("targets") or {}).items() if v}  # a rebuilt base's outputs
    if targets:
        lab_inputs = set((data.raw.get("lab", {}).get(lab) or {}).get("inputs") or [])
        packs = [k for k in targets if k in lab_inputs]
        rate = max((targets[p] for p in packs), default=max(targets.values()))
        spm = rate * 60
    else:
        packs = packs_for(data, lab, params.get("addons"))
        targets = {p: rate for p in packs}
    belt = params.get("belt") or "transport-belt"
    assembler = params.get("assembler") or "assembling-machine-2"
    furnace = params.get("furnace") or "stone-furnace"
    picks = [assembler, furnace] + list(params.get("machines") or [])
    # an extension of the player's base: what its belts carry is "raw" (taken from the base, not made here)
    raw = set(params["raw"]) if params.get("raw") is not None else raw_items(data) - MADE_HERE
    if params.get("plates"):  # (plates come in at the bus head: from a bus design, or smelted elsewhere)
        raw |= plate_items(data, raw)
    overrides = params.get("recipes") or {}
    fluid_plans = params.get("fluid_plans") or {}
    steps = solve(data, targets, picks, raw, overrides, fluid_plans)
    machine_for = machines_by_category(data, picks)
    common = {k: params[k] for k in ("bonuses", "inserters", "productivity") if params.get(k) is not None}
    common["inserters"] = params.get("inserters") or list(EARLY_INSERTERS)

    sections, not_automated, outputs = [], [], []
    for item, st in steps.items():
        if st.kind == "out":
            outputs.append({"item": st.of, "rate": round(st.rate * 60, 1)})
            continue
        if st.kind == "flare":
            try:
                res = special_section(service, {"flare": st.of, "rate": st.rate * INTERMEDIATE_HEADROOM})
            except planner.PlanError as e:
                not_automated.append({"item": st.of, "rate": round(st.rate * 60, 1), "why": str(e)})
                continue
            sections.append({"name": f"burn {st.of}", "kind": "line", "item": item, "rate": round(st.rate * 60, 1),
                             "copies": 1, "result": res, "recipe": None, "choices": []})
            continue
        if not st.recipe:
            continue
        if cancel and cancel():
            from bpgen.harness import Cancelled
            raise Cancelled("cancelled")
        if progress:
            progress(f"section {len(sections) + 1}: {item} ({st.rate * 60:.0f}/min in {st.machine})")
        # intermediates get headroom: with supply exactly at demand the belts and buffers between them take hours
        # to fill, and the last branch of a splitter chain starves meanwhile
        head = 1 if item in targets else INTERMEDIATE_HEADROOM
        if st.recipe and item not in targets                 and (data.raw["recipe"].get(st.recipe) or {}).get("category", "crafting") == "smelting":
            head = SMELTING_HEADROOM
        p = dict(common, recipe=st.recipe, machine=st.machine, belt=belt, rate_per_min=st.rate * 60 * head)
        res = None
        if st.kind == "multi" or (st.kind == "convert" and _all_fluid(data, st.recipe)):
            try:  # a recipe with several products, or fluids only (cracking): its own layout
                res = special_section(service, {"multi": True, "recipe": st.recipe, "machine": st.machine,
                                                "target": st.item, "rate": st.rate * head,
                                                "productivity": params.get("productivity") or 0, "belt": belt})
            except planner.PlanError as e:
                if st.kind == "multi":
                    not_automated.append({"item": item, "rate": round(st.rate * 60, 1), "recipe": st.recipe,
                                          "machine": st.machine, "why": str(e)})
                    continue
        if res is None:
            try:
                res = service.plan(p)
            except Exception as e:  # noqa: BLE001 - try a fluid line, else list it for the player
                try:
                    res = service.plan_fluid(p)
                except Exception as e2:  # noqa: BLE001
                    try:  # fluids only, in a machine the fluid lines don't fit: the several-fluid layout
                        if not _all_fluid(data, st.recipe):
                            raise
                        res = special_section(service, {"multi": True, "recipe": st.recipe, "machine": st.machine,
                                                        "target": st.item, "rate": st.rate * head,
                                                        "productivity": params.get("productivity") or 0, "belt": belt})
                    except Exception as e3:  # noqa: BLE001
                        not_automated.append({"item": item, "rate": round(st.rate * 60, 1), "recipe": st.recipe,
                                              "machine": st.machine, "why": f"{e}; {e2}; {e3}"})
                        continue
        made = res["summary"]["expected"]
        copies = max(1, math.ceil(st.rate / made - 1e-6)) if made else 1
        name = item if st.kind != "convert" else f"{st.of} -> {st.item}"
        sections.append({"name": name + (f" x{copies}" if copies > 1 else ""), "kind": "line",
                         "item": item, "rate": round(st.rate * 60, 1), "copies": copies, "result": res,
                         "recipe": st.recipe,
                         "choices": recipe_choices(data, st.item, machine_for) if st.kind == "line" else []})

    if params.get("labs", True) and lab in data.raw.get("lab", {}) and 0 < len(packs) <= 4:
        # enough labs for the research, and enough inserters for the packs (fast labs outrun one inserter)
        from bpgen import calibrate
        table = json.loads(service.calib_path.read_text()) if service.calib_path.exists() else {}
        bonuses = dict(params.get("bonuses") or calibrate.ZERO)

        def fastest(tiles):
            names = calibrate.template_inserters(data, common["inserters"], tiles)
            rated = [(table.get(f"{n}|{belt}|belt_to_machine|{calibrate.level_key(data, n, bonuses)}", 0), n)
                     for n in names]
            return max(rated) if rated else (0, None)
        (near_rate, near_ins), (far_rate, far_ins) = fastest(1), fastest(2)
        far_packs = len(packs) - 2
        n = lab_count(data, lab, rate, packs)
        if near_rate:
            n = max(n, math.ceil(2 * rate / (near_rate * 0.9)))
        if far_packs > 0 and far_rate:
            n = max(n, math.ceil(far_packs * rate / (far_rate * 0.9)))
        labs_params = (lab, n, belt, packs, near_ins or "inserter", far_ins or "long-handed-inserter")
        ents, srcs = labs_layout(data, *labs_params)
        for s in srcs:
            s["rates"] = {pk: round(targets.get(pk, rate), 3) for pk in s["lanes"] if pk}
        ents, srcs, _, desc = labels.add_labels(data, ents, srcs, [], "", 0)
        bp = planner.blueprint_string(ents, f"labs x{n}", description=desc)
        sections.append({"name": f"labs x{n}", "kind": "labs", "item": lab, "copies": 1,
                         "result": {"mode": "labs", "entities": [service.decorate(e) for e in ents], "sources": srcs,
                                    "sinks": [], "blueprint": bp, "description": desc,
                                    "summary": {"mode": "labs", "labs": n, "lab": lab, "packs": packs,
                                                "rate": rate, "inserter": near_ins, "long_inserter": far_ins}}})

    if params.get("mall", True):
        cats = set(_proto(data, assembler).get("crafting_categories", []))
        products = [p for p in params.get("mall_products") or [] if p in data.raw["recipe"]
                    and data.raw["recipe"][p].get("category", "crafting") in cats] or mall_products(data, assembler)
        if progress:
            progress(f"mall: {len(products)} buildings")
        try:
            res = service.plan({**common, "mode": "mall", "products": products, "machine": assembler, "belt": belt,
                                "chest": params.get("chest") or "wooden-chest", "chest_limit": 2})
            sections.append({"name": "mall", "kind": "mall", "item": None, "copies": 1, "result": res})
        except Exception as e:  # noqa: BLE001
            not_automated.append({"item": "mall", "why": str(e)})

    # everything as one blueprint, belts routed between the sections: in the compact layout all but the mall (a print
    # of its own), in the main-bus layout the mall takes its items from the bus too
    route_note = None
    fit_notes, absolute = [], None
    if params.get("routed", True) and any(s["kind"] == "line" for s in sections):
        if progress:
            progress("routing belts between the sections")
        lp = labs_params if params.get("labs", True) and lab in data.raw.get("lab", {}) and 0 < len(packs) <= 4 else None
        want = params.get("layout") or "compact"
        err = None
        has_mall = any(s["kind"] == "mall" for s in sections)
        # (a bus that won't route with the mall: the bus without it, then the compact layout)
        for layout, with_mall in dict.fromkeys([(want, True)] + ([(want, False)] if want == "bus" and has_mall else [])
                                               + [("compact", True)]):
            info = {}
            try:
                ents, srcs, sinks, desc = routed(service, sections, steps, packs, rate, belt, labs=bool(lp),
                                                 labs_params=lp, raw=raw, layout=layout, info=info, with_mall=with_mall)
            except planner.PlanError as e:
                err = err or e
                continue
            bp = planner.blueprint_string(ents, "starter base (connected)", description=desc)
            corner = tiers.head_corner(srcs) if layout == "bus" else None
            fit = None
            if corner and params.get("plates") and params.get("fit_bus"):  # (its head on a bus design's lanes)
                from bpgen import busdesign
                bus = busdesign.load_last()
                try:
                    if bus:  # (clear of the bus design's own belts and of what's on the ground around you)
                        bus = dict(bus, tiles=[list(t) for t in {tuple(t) for t in bus.get("tiles") or []}
                                               | tiers.ground_taken(service, params.get("snapshot"), buildings=False)])
                    fit = tiers.fit_to_bus(service, ents, srcs, bus, belt) if bus else None
                except planner.PlanError as e:
                    fit_notes.append(f"not fitted to your bus design: {e}")
                if not bus:
                    fit_notes.append("no bus design planned yet (Bus design tab): the head is as usual")
            if fit:
                bp, ents, absolute = fit["blueprint"], fit["entities"], {"box": list(fit["box"]), "taps": 0,
                                                                          "fitted": True}
                fit_notes += fit["notes"]
            elif corner:  # (the stone-brick C over the bus head: the next tier pastes onto it)
                bp = tiers.marked(bp, corner)
            sections.insert(0, {"name": "whole base (connected)", "kind": "routed", "item": None, "copies": 1,
                                "result": {"mode": "routed", "entities": [service.decorate(e) for e in ents],
                                           "sources": srcs, "sinks": sinks, "blueprint": bp, "description": desc,
                                           "summary": {"mode": "routed", "packs": packs, "rate": rate,
                                                       "layout": layout, "mall": info.get("mall", False),
                                                       "inputs": [x.get("lanes") or [x.get("fluid") + " (pipe)"] for x in srcs]}}})
            if layout != want:
                route_note = f"couldn't lay it out as a main bus ({err}); the compact layout instead"
            elif has_mall and want == "bus" and not with_mall:
                route_note = f"couldn't fit the mall onto the main bus ({err}); it is a print of its own"
            err = None
            break
        if err:
            route_note = f"couldn't connect the sections automatically ({err}); build them from the separate prints"

    # what comes in from outside: the raw sources of every section (x copies), and what isn't automated
    bring_in = {}
    made_here = {st.item for st in steps.values() if st.recipe}  # by-products count as made here too
    for st in steps.values():
        if st.kind == "multi":
            made_here |= {o["name"] for o in data.raw["recipe"][st.recipe].get("results") or []}
    for s in sections:
        if s["kind"] in ("mall", "routed"):
            continue  # the mall draws a trickle; it isn't sized by rate
        for src in s["result"].get("sources", []):
            for it, r in (src.get("rates") or {}).items():
                if it in raw and it not in made_here:
                    bring_in[it] = bring_in.get(it, 0) + r * 60 * s["copies"]
    for na in not_automated:
        if na.get("rate"):
            bring_in[na["item"]] = bring_in.get(na["item"], 0) + na["rate"]
    skipped = {n["item"] for n in not_automated}
    bus = [st.item for it, st in steps.items() if st.recipe and st.kind == "line" and it not in packs and it not in skipped]

    prints = []
    for i, s in enumerate(sections):
        bp = _decode(s["result"]["blueprint"])
        # (a section can be a book of its own, e.g. a mall split in two: nested books are fine in a book)
        bp["blueprint" if "blueprint" in bp else "blueprint_book"]["label"] = s["name"]
        prints.append({"index": i, **bp})
    lines = [f"bpgen starter base: {', '.join(packs)} at {spm:g}/min each", "",
             "Bus (deepest first): " + ", ".join(bus), "",
             "Bring in: " + ", ".join(f"{k} {v:.0f}/min" for k, v in sorted(bring_in.items()))]
    if not_automated:
        lines += ["", "Not automated here (bring in): " + ", ".join(n["item"] for n in not_automated)]
    if route_note:
        lines += ["", route_note]
    book = {"blueprint_book": {"item": "blueprint-book", "label": f"starter base {spm:g} spm",
                               "description": "\n".join(lines), "blueprints": prints, "active_index": 0,
                               "version": prints[0]["blueprint"]["version"] if prints else 0}}
    return {"sections": sections, "book": _encode(book), "bus": bus, "packs": packs, "spm": spm, "steps": steps,
            "outputs": outputs,
            "bring_in": {k: round(v, 1) for k, v in sorted(bring_in.items())}, "not_automated": not_automated,
            "route_note": route_note, "text": "\n".join(lines), "absolute": absolute, "notes": fit_notes}


def _all_fluid(data, recipe):
    r = data.raw["recipe"].get(recipe) or {}
    parts = (r.get("ingredients") or []) + (r.get("results") or [])
    return bool(parts) and all(x.get("type", "item") == "fluid" for x in parts)


def _special_layout(data, params):
    """(entities, sources, sinks) of a several-product / all-fluid block or a flare block"""
    from bpgen import fluidlines
    if params.get("flare"):
        ents, srcs, sinks, _ = fluidlines.layout_flare(data, params["flare"], params["rate"])
        return ents, srcs, sinks
    p = fluidlines.plan_multi(data, params["recipe"], params["machine"], params["target"], params["rate"],
                              params.get("productivity") or 0, params.get("belt") or "transport-belt")
    return fluidlines.layout_multi(p)


def special_section(service, params):
    """a section result for a several-product / all-fluid block, or a flare block"""
    from bpgen import fluidlines
    data = service.data
    if params.get("flare"):
        ents, srcs, sinks, notes = fluidlines.layout_flare(data, params["flare"], params["rate"])
        summary = {"mode": "fluid", "kind": "flare", "output": params["flare"], "expected": round(params["rate"], 3),
                   "machines": sum(1 for e in ents if e["name"] != planner.PIPE and e["name"] != planner.POLE),
                   "notes": notes, "text": "; ".join(notes)}
        label = f"burn {params['flare']}"
    else:
        p = fluidlines.plan_multi(data, params["recipe"], params["machine"], params["target"], params["rate"],
                                  params.get("productivity") or 0, params.get("belt") or "transport-belt")
        ents, srcs, sinks = fluidlines.layout_multi(p)
        text = (f"{p.recipe}: {p.machines} x {p.machine}; makes " +
                ", ".join(f"{k} {v * 60:.0f}/min" for k, v in p.outputs.items()))
        summary = {"mode": "fluid", "kind": "multi", "recipe": p.recipe, "machine": p.machine, "machines": p.machines,
                   "output": p.target, "expected": round(p.outputs[p.target], 3),
                   "outputs": {k: round(v, 3) for k, v in p.outputs.items()},
                   "input_need": {k: round(v, 3) for k, v in p.inputs.items()}, "notes": p.notes, "text": text}
        label = f"{p.recipe} x{p.machines}"
    return {"mode": "fluid", "params": dict(params), "summary": summary, "text": summary["text"],
            "entities": [service.decorate(e) for e in ents], "sources": srcs, "sinks": sinks,
            "blueprint": planner.blueprint_string(ents, label)}


# ---- one connected blueprint ----------------------------------------------------------------------------------
ROUTED_SECONDS = 45


def routed(service, sections, steps, packs, rate, belt, labs=True, labs_params=None, raw=None, layout="compact", info=None, with_mall=True):
    """compose the line sections (and the labs) into one blueprint with routed belts.
    labs=False leaves the science outputs as sinks (that is what verification measures).
    layout "bus": one column of blocks beside a main bus, the mall included. info (a dict) gets what was built.
    -> (entities, sources, sinks, description)"""
    from bpgen import compose_base, labels

    data = service.data
    blocks = []
    for s in sections:
        if s["kind"] != "line":
            continue
        params = s["result"]["params"]
        st = steps[s["item"]]
        if params.get("multi") or params.get("flare"):
            ents, srcs, sinks = _special_layout(data, params)
            capacity = params.get("rate", 0)
            for c in range(s["copies"]):
                blocks.append(compose_base.Block(s["name"] + (f" #{c + 1}" if s["copies"] > 1 else ""),
                                                 st.item if params.get("multi") else None, ents, srcs, sinks,
                                                 st.depth, capacity))
            continue
        calib = service.ensure_calibrated(params)
        if params.get("fluid_line"):
            from bpgen import fluidlines
            p = fluidlines.plan_fluid(data, calib, params["recipe"], params["machine"], params["belt"],
                                      bonuses=dict(params.get("bonuses") or {}), allowed=service._inserters(params),
                                      target_rate=(params.get("rate_per_min") or 0) / 60 or None)
            ents, srcs, sinks = fluidlines.layout(p)
            capacity = p.expected
        else:
            p = planner.plan(data, calib, params["recipe"], params["machine"], params["belt"],
                             **service._line_kwargs(params))
            ents, srcs, sinks = planner.layout(p)
            capacity = p.expected_output
        for c in range(s["copies"]):
            name = s["item"] + (f" #{c + 1}" if s["copies"] > 1 else "")
            blocks.append(compose_base.Block(name, st.item, ents, srcs, sinks, st.depth, capacity))
    if labs and labs_params:
        ents, srcs = labs_layout(data, *labs_params)
        for x in srcs:
            x["rates"] = {pk: rate for pk in x["lanes"] if pk}
        blocks.append(compose_base.Block("labs", None, ents, srcs, [], max(b.depth for b in blocks) + 1))
    if layout == "bus" and with_mall:  # (in the compact layout the mall is a print of its own)
        for s in sections:
            if s["kind"] == "mall":
                m = s["result"]
                ents = [{k: v for k, v in e.items() if k not in ("type", "w", "h")} for e in m["entities"]
                        if e["name"] != labels.COMBINATOR]
                blocks.append(compose_base.Block("mall", None, ents, [dict(x) for x in m["sources"]], [],
                                                 max(b.depth for b in blocks) + 1, last=True))
                if info is not None:
                    info["mall"] = True
    # try tight to loose layouts (column height, routing slack) and keep the smallest one that routes
    area = sum(b.width * b.height for b in blocks)
    side = math.sqrt(area * 3)  # blocks fill about a third of the ground once the channels are in
    heights = [None] + [round(side * f) for f in (0.6, 0.8, 1.0, 1.2, 1.5)]
    if layout == "bus":
        heights = [None]  # (one column beside the bus: nothing to tune but the slack)
    raw = raw if raw is not None else raw_items(data) - MADE_HERE
    best, err = None, None
    # (all the layouts tried, together, within a budget: past it the best so far is kept, or the base falls back to
    # its separate prints)
    import time
    give_up = time.monotonic() + ROUTED_SECONDS
    for slack in (2, 5, 10):
        for max_h in heights:
            if max_h is not None and max_h < max(b.height for b in blocks):
                continue
            if time.monotonic() > give_up:
                err = err or planner.PlanError(f"no connected layout within {ROUTED_SECONDS} s")
                break
            try:
                nt = []
                out = compose_base.compose(data, blocks, belt, raw, slack=slack, row_gap=5 if layout == "bus" else 3,
                                           max_col_h=max_h, bus=layout == "bus", notes=nt)
            except planner.PlanError as e:
                err = e
                continue
            xs = [e["position"]["x"] for e in out[0]]
            ys = [e["position"]["y"] for e in out[0]]
            size = (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1)
            if best is None or size < best[0]:
                best = (size, out, nt)
        if best:
            break  # looser slack only if nothing tight routed
    if best is None:
        raise err
    ents, sources, sinks = best[1]
    ents, sources, sinks, desc = labels.add_labels(data, ents, sources, sinks, "", 0)
    return ents, sources, sinks, "\n".join(best[2] + [desc]) if best[2] else desc
