"""Planning service shared by the CLI watcher and the web UI.

Params are plain JSON-able dicts so the web UI can send them as-is:
    recipe, machine, belt, bonuses{...}, inserters[...] (researched), belts[...] (researched), productivity,
    modules "name:count,...", machine_quality, beacon, beacon_modules, beacon_quality,
    per_row, near, far, out (fine-tuning overrides)
"""
import collections
import json
import math
import os
import re
import threading
import time
from pathlib import Path

from bpgen import calibrate, chain, harness, labels, mall, pack, planner
from bpgen.data import Data
from bpgen.effects import MACHINE_MODULES, Modules, quality_level

ROOT = Path(__file__).resolve().parent.parent
from bpgen.config import PATHS
REQUEST = PATHS["script_output"] / "bpgen" / "request.json"
SAVE_STATE = PATHS["script_output"] / "bpgen" / "state.json"  # the mod: research and production of the save
SNAPSHOT = PATHS["script_output"] / "bpgen" / "snapshot.json"  # the mod: an area of the base
VANILLA_MODS = ("base", "core", "space-age", "quality", "elevated-rails")
ENTITY_TYPES = ["assembling-machine", "furnace", "beacon", "constant-combinator", "underground-belt", "splitter", "container", "inserter", "transport-belt", "electric-pole", "pipe", "pipe-to-ground", "logistic-container", "roboport", "lab"]


def _size_of(entities):
    xs = [e["position"]["x"] for e in entities]
    ys = [e["position"]["y"] for e in entities]
    return [int(max(xs) - min(xs)) + 1, int(max(ys) - min(ys)) + 1] if entities else None


def _cell_for(entities):
    """harness cell big enough for this design (the default cell is ~48 tiles tall below its origin)"""
    xs = [e["position"]["x"] for e in entities]
    ys = [e["position"]["y"] for e in entities]
    return {"w": int(max(xs) - min(xs)) + 60, "h": int(max(ys) - min(ys)) + 60, "cols": 1}


def short_notes(needs, spare):
    """notes for what a build takes from the base ({item: per minute}) beyond what the base can spare"""
    return [f"{it}: this needs {need:.0f}/min and your base has {max(spare[it], 0):.0f}/min spare; the rest comes "
            f"out of what your base already uses" for it, need in sorted(needs.items())
            if it in spare and spare[it] < need * 0.95]


class Service:
    def __init__(self, mode="pack"):
        self.mode = mode
        self.lock = threading.Lock()  # one headless Factorio at a time
        self._data = None

    # ---- data -------------------------------------------------------------------------------------------------
    @property
    def data(self):
        if self._data is None:
            self._data = Data.load(pack.DATA if self.mode == "pack" else ROOT / "data" / "vanilla-dump.json")
            planner.configure(self._data)
        return self._data

    @property
    def mod_dir(self):
        return pack.PACK if self.mode == "pack" else ROOT / "mods" / "vanilla"

    @property
    def calib_path(self):
        return ROOT / "data" / ("calibration-pack.json" if self.mode == "pack" else "calibration.json")

    def _locked(self, progress=None, cancel=None):
        """take the one-headless-Factorio lock, reporting while waiting and giving up on cancel"""
        while not self.lock.acquire(timeout=0.5):
            if progress:
                progress("waiting for another headless Factorio run to finish")
            if cancel and cancel():
                raise harness.Cancelled("cancelled")

    def sync(self, progress=None, cancel=None):
        """re-dump the pack if the real mod list/settings changed; True if it did"""
        if self.mode != "pack":
            return False
        self._locked(progress, cancel)
        try:
            if progress:
                progress("checking whether your mods or settings changed")
            if not pack.up_to_date() and progress:
                progress("your mods or settings changed: re-reading all prototypes (about 2 minutes)")
            changed = pack.sync()
        finally:
            self.lock.release()
        if changed:
            self._data = None
        return changed

    def proto(self, name):
        for t in ENTITY_TYPES:
            if name in self.data.raw.get(t, {}):
                return t, self.data.raw[t][name]
        return None, None

    # ---- params -----------------------------------------------------------------------------------------------
    def base_belts(self, names):
        """drop per-quality copies some quality mods create ("rare-transport-belt", ...)"""
        prefixes = tuple(q + "-" for q in self.data.raw.get("quality", {}) if q != "normal")
        names = [b for b in names if b in self.data.raw["transport-belt"] and not b.startswith(prefixes)]
        # variant copies ("bioluminescent-transport-belt"): a prefix put in front of at least two other belts
        # (a lone "fast-" + "transport-belt" is a real tier, not a copy)
        uses = {}
        for b in names:
            for o in names:
                if b != o and b.endswith("-" + o):
                    uses.setdefault(b[:-len(o) - 1], set()).add(o)
        variant = tuple(p + "-" for p, bases in uses.items() if len(bases) >= 2)
        return [b for b in names if not b.startswith(variant)]

    def params_from_request(self, req, defaults=None):
        d = self.data
        belts = self.base_belts(req.get("belts", []))
        mods = [m for m in (req.get("modules") or []) if isinstance(m, dict)]
        p = {
            "recipe": req["recipe"], "machine": req["machine"],
            "belt": max(belts, key=lambda b: d.raw["transport-belt"][b]["speed"]) if belts else "transport-belt",
            "belts": belts, "inserters": req.get("inserters") or [],
            "bonuses": {k: (req.get("bonuses") or {}).get(k, 0) for k in calibrate.ZERO},
            "productivity": req.get("recipe_productivity", 0) or 0,
            "modules": ",".join(f"{m['name']}@{m.get('quality') or 'normal'}:{m['count']}" for m in mods),
            "machine_quality": req.get("machine_quality", "normal"),
            "beacon": "beacon", "beacon_modules": "", "beacon_quality": "normal",
        }
        p.update({k: v for k, v in (defaults or {}).items() if v})
        self._early_inserters(p)
        return p

    def _early_inserters(self, p):
        """bpgen's lines use electric inserters; early on there may be none researched (the pack: assemblers come
        with automation, the inserter with electronics; the burner inserter can't feed a line). Then plan with the
        earliest ones there are and say what to research, instead of failing."""
        d = self.data
        have = p.get("inserters") or []
        if not have or calibrate.template_inserters(d, have, 1):
            return
        from bpgen import base  # (local: base imports service)
        depth = base.unlock_depth(d)
        picks = []
        for tiles in (1, 2):
            names = calibrate.template_inserters(d, None, tiles)
            if names:
                picks.append(min(names, key=lambda n: (depth.get(n, 999), n)))
        if not picks:
            return
        p["inserters"] = sorted(set(have) | set(picks))
        all_techs = d.raw.get("technology", {})
        tdepth = {}

        def td(name, seen=()):  # (how deep a technology sits: the shallowest one unlocking an inserter is named)
            if name not in tdepth:
                pre = [x for x in all_techs.get(name, {}).get("prerequisites") or [] if x not in seen]
                tdepth[name] = 1 + max((td(x, seen + (name,)) for x in pre), default=0)
            return tdepth[name]

        techs = []
        for n in picks:
            items = {i for i, it in d.raw.get("item", {}).items() if it.get("place_result") == n}
            recipes = {rn for rn, r in d.raw["recipe"].items() if any(x.get("name") in items for x in r.get("results") or [])}
            found = [tn for tn, t in all_techs.items()
                     if any(e.get("type") == "unlock-recipe" and e.get("recipe") in recipes for e in t.get("effects") or [])]
            techs += [min(found, key=lambda tn: (td(tn), tn))] if found else []
        p["early_note"] = (f"no electric inserter researched yet: planned with {', '.join(picks)}"
                           + (f" (research {', '.join(dict.fromkeys(techs))})" if techs else ""))

    def _inserters(self, params):
        allowed = params.get("inserters") or None
        return calibrate.template_inserters(self.data, allowed) + calibrate.template_inserters(self.data, allowed, 2)

    def ensure_calibrated(self, params, progress=None, cancel=None):
        bonuses = dict(params.get("bonuses") or calibrate.ZERO, belt_stack_size_bonus=0)
        self._locked(progress, cancel)
        try:
            return calibrate.calibrate(self.calib_path, self.mod_dir, self._inserters(params), [params["belt"]],
                                       [bonuses, calibrate.ZERO], data=self.data, progress=progress, cancel=cancel)
        finally:
            self.lock.release()

    def missing_calibration(self):
        """(inserters, belts, bonus sets) covering every template inserter on every normal belt at every research
        level, and how many setups of that are not measured yet"""
        d = self.data
        inserters = calibrate.template_inserters(d) + calibrate.template_inserters(d, tiles=2)
        belts = self.base_belts([n for n, b in d.raw["transport-belt"].items() if not b.get("hidden")])
        levels = calibrate.all_levels(d)
        table = json.loads(self.calib_path.read_text()) if self.calib_path.exists() else {}
        keys = {f"{i}|{b}|{direction}|{calibrate.level_key(d, i, lv)}" for i in inserters for b in belts
                for lv in levels for direction in ("belt_to_machine", "machine_to_belt")}
        return inserters, belts, levels, len(keys - table.keys())

    def precalibrate(self, progress=None, cancel=None):
        """measure everything research could make you need, so requests never wait for a measurement"""
        if self.sync(progress, cancel):
            pass
        inserters, belts, levels, missing = self.missing_calibration()
        if not missing:
            return {"measured": 0}
        if progress:
            progress(f"{missing} inserter setups to measure ({len(inserters)} inserters, {len(belts)} belts, "
                     f"all research levels)")
        self._locked(progress, cancel)
        try:
            calibrate.calibrate(self.calib_path, self.mod_dir, inserters, belts, levels, data=self.data,
                                progress=progress, cancel=cancel)
        finally:
            self.lock.release()
        return {"measured": missing}

    # ---- planning ---------------------------------------------------------------------------------------------
    def analyze_blueprint(self, bp_string):
        """what a pasted base makes and with which tech (see importer.py)"""
        from bpgen import importer
        return importer.analyze(self.data, bp_string)

    def science(self, lab):
        """the starter base's science tiers for this lab (cached: it solves every pack's recipe tree)"""
        from bpgen import base
        cache = self.__dict__.setdefault("_science", {})
        if lab not in cache:
            cache[lab] = base.science_info(self.data, lab) if lab in self.data.raw.get("lab", {}) else []
        return cache[lab]

    def plan_base(self, params, progress=None, cancel=None):
        """starter base: a blueprint book of sections (see base.py); the preview opens on the first section"""
        from bpgen import base
        r = base.plan_base(self, params, progress, cancel)
        first = r["sections"][0]["result"] if r["sections"] else {"entities": [], "sources": [], "sinks": []}
        return {
            "params": params, "mode": "base", "blueprint": r["book"], "text": r["text"],
            "entities": first["entities"], "sources": first.get("sources", []), "sinks": first.get("sinks", []),
            "sections": [{"name": s["name"], "kind": s["kind"], "item": s["item"], "rate": s.get("rate"),
                          "copies": s["copies"], "result": s["result"], "recipe": s.get("recipe"),
                          "choices": s.get("choices") or []} for s in r["sections"]],
            "summary": {"mode": "base", "packs": r["packs"], "spm": r["spm"], "bus": r["bus"],
                        "size": _size_of(first["entities"]) if r["sections"] and r["sections"][0]["kind"] == "routed" else None,
                        "entity_count": len(first["entities"]), "before": params.get("before"),
                        "bring_in": r["bring_in"], "not_automated": r["not_automated"], "route_note": r.get("route_note"),
                        "outputs": r.get("outputs") or [], "targets": {k: round(v, 1) for k, v in (params.get("targets") or {}).items()}},
        }

    def plan_fluid(self, params, progress=None, cancel=None):
        """a fluid line (fluid output, or two fluid inputs): see fluidlines.py"""
        from bpgen import fluidlines
        calib = self.ensure_calibrated(params, progress, cancel)
        p = fluidlines.plan_fluid(self.data, calib, params["recipe"], params["machine"], params["belt"],
                                  bonuses=dict(params.get("bonuses") or calibrate.ZERO),
                                  allowed=self._inserters(params),
                                  target_rate=(params.get("rate_per_min") or 0) / 60 or None)
        ents, sources, sinks = fluidlines.layout(p)
        ents, sources, sinks, description = labels.add_labels(self.data, ents, sources, sinks, p.output, p.expected)
        ins = p.ins_in or p.ins_out
        return {
            "params": dict(params, fluid_line=True), "mode": "fluid", "description": description,
            "summary": {"mode": "fluid", "kind": p.kind, "recipe": p.recipe, "machine": p.machine, "belt": p.belt,
                        "machines": p.machines, "per_row": p.per_row, "output": p.output, "expected": round(p.expected, 3),
                        "input_need": {k: round(v, 3) for k, v in p.input_need.items()}, "notes": p.notes,
                        "inserter": {"name": ins.name, "count": ins.count} if ins else None,
                        "text": fluidlines.describe(p)},
            "entities": [self.decorate(e) for e in ents], "sources": sources, "sinks": sinks,
            "blueprint": planner.blueprint_string(ents, f"{p.output} x{p.machines}", description=description),
        }

    def plan_mall(self, params, progress=None, cancel=None):
        if params.get("feed") == "robots":
            return self.plan_bot_mall(params)
        calib = self.ensure_calibrated(params, progress, cancel)
        if progress:
            progress("planning the mall")
        bonuses = dict(params.get("bonuses") or calibrate.ZERO, belt_stack_size_bonus=0)
        shared = params.get("shared")
        malls, m_ents, m_sources, split_notes = mall.plan_mall_auto(
            self.data, calib, params.get("products") or [], params["machine"], params["belt"],
            shared=None if shared is None else {k: v for k, v in shared.items() if v},
            bonuses=bonuses, allowed=self._inserters(params), chest=params.get("chest") or "iron-chest",
            chest_limit=int(params.get("chest_limit") or 4))
        ents, sources, sinks, description = labels.add_labels(self.data, m_ents, m_sources, [], "", 0)
        products = [p for m in malls for p in m.products]
        label = f"mall: {', '.join(products)}"[:60]
        text = mall.describe_all(malls, split_notes)
        blueprint = planner.blueprint_string(ents, label, description=description)
        prints = []  # a split mall: each section is its own print, and the string is a book - unless they are
        chained = any(" gets " in n for n in split_notes)  # chained by belts: then it stays one print
        if len(malls) > 1 and not chained:
            from bpgen import base
            for i, m in enumerate(malls):
                p_ents, p_sources, _, p_desc = labels.add_labels(self.data, m.entities, m.sources, [], "", 0)
                p_label = f"mall {i + 1}: {', '.join(m.products)}"[:60]
                prints.append({"entities": [self.decorate(e) for e in p_ents], "sources": p_sources, "sinks": [],
                               "blueprint": planner.blueprint_string(p_ents, p_label, description=p_desc)})
            book = [dict(base._decode(pr["blueprint"]), index=i) for i, pr in enumerate(prints)]
            blueprint = base._encode({"blueprint_book": {
                "item": "blueprint-book", "label": label, "description": text, "blueprints": book, "active_index": 0,
                "version": book[0]["blueprint"]["version"]}})
        return {
            "params": params, "mode": "mall", "description": description,
            "summary": {"mode": "mall", "products": products, "machines": sum(len(m.machines) for m in malls),
                        "machine": params["machine"], "belt": params["belt"], "chest": params.get("chest") or "iron-chest",
                        "notes": split_notes + [n for m in malls for n in m.notes],
                        "raw_inputs": sorted({r for m in malls for r in m.raw}),
                        "makers": {k: v for m in malls for k, v in m.makers.items()},
                        "sections": [{"products": m.products, "raw": m.raw, "makers": m.makers,
                                      "lanes": {it: f"bus {b + 1} {'north' if side == 'N' else 'south'}"
                                                for it, (b, side) in m.lanes.items()},
                                      "print": prints[i] if prints else None} for i, m in enumerate(malls)],
                        "lanes": {it: f"bus {b + 1} {'north' if side == 'N' else 'south'}"
                                  for m in malls for it, (b, side) in m.lanes.items()} if len(malls) == 1 else {},
                        "text": text},
            "text": text,
            "entities": [self.decorate(e) for e in ents], "sources": sources, "sinks": sinks,
            "blueprint": blueprint,
        }

    def plan_bot_mall(self, params):
        """a robot-fed mall (see mall.plan_bot_mall)"""
        st = json.loads(SAVE_STATE.read_text(encoding="utf-8")) if SAVE_STATE.exists() else {}
        unlocked = set((st.get("unlocked") or {}).get("chests") or [])
        products = params.get("products") or []
        inserters = [n for n in calibrate.template_inserters(self.data, params.get("inserters") or None)]
        inserter = inserters[0] if params.get("inserters") and inserters else (
            "inserter" if "inserter" in self.data.raw["inserter"] else (inserters or ["inserter"])[0])
        ents, notes = mall.plan_bot_mall(self.data, products, params["machine"], inserter,
                                         chest_limit=int(params.get("chest_limit") or 4),
                                         buffer_crafts=float(params.get("buffer_crafts") or 5), unlocked=unlocked)
        ents, _, _, description = labels.add_labels(self.data, ents, [], [], "", 0)
        label = f"robot mall: {', '.join(products)}"[:60]
        made = [e for e in ents if e.get("recipe")]
        return {
            "params": params, "mode": "mall", "description": description,
            "summary": {"mode": "mall", "feed": "robots", "products": [e["recipe"] for e in made],
                        "machines": len(made), "machine": params["machine"], "belt": None,
                        "chest": next((e["name"] for e in ents if e.get("bar")), ""), "notes": notes,
                        "raw_inputs": [], "makers": {}, "sections": [], "lanes": {}, "text": "\n".join(notes)},
            "text": "\n".join(notes),
            "entities": [self.decorate(e) for e in ents], "sources": [], "sinks": [],
            "blueprint": planner.blueprint_string(ents, label, description=description),
        }

    def plan_bot_line(self, params):
        """a production line fed by robots (see mall.plan_bot_line): requesters in, passive providers out"""
        from bpgen.effects import EffectError, Modules, machine_effects
        st = json.loads(SAVE_STATE.read_text(encoding="utf-8")) if SAVE_STATE.exists() else {}
        unlocked = set((st.get("unlocked") or {}).get("chests") or [])
        d = self.data.raw
        r, m = d["recipe"].get(params.get("recipe")), d.get("assembling-machine", {}).get(params.get("machine"))
        if not r or not m:
            raise planner.PlanError("robot-fed lines need a recipe and an assembling machine")
        try:
            sp, pr, _ = machine_effects(self.data, m, r, Modules.parse(params.get("modules")))
        except EffectError as e:
            raise planner.PlanError(str(e))
        sp = (1 + 0.3 * quality_level(self.data, params.get("machine_quality") or "normal")) * (1 + sp) - 1
        rate = float(params.get("rate_per_min") or 60) / 60
        ents, notes, info = mall.plan_bot_line(
            self.data, params["recipe"], params["machine"], rate, self._inserters(params),
            bonuses=params.get("bonuses"), unlocked=unlocked, chest_limit=int(params.get("chest_limit") or 4),
            speed=sp, productivity=float(params.get("productivity") or 0) + pr)
        if not params.get("rate_per_min"):
            notes.insert(0, "no rate set: planned 60/min (Output per minute sets it)")
        if Modules.parse(params.get("modules")).count:
            for e in ents:
                if e.get("recipe"):
                    e["modules"] = Modules.parse(params["modules"]).items
                    e["inventory"] = MACHINE_MODULES
        ents, _, _, description = labels.add_labels(self.data, ents, [], [], "", 0)
        label = f"{params['recipe']} x{info['machines']} (robots)"
        return {
            "params": params, "mode": "mall", "description": description,
            "summary": {"mode": "mall", "feed": "robots", "products": [params["recipe"]],
                        "machines": info["machines"], "machine": params["machine"], "belt": None,
                        "chest": next((e["name"] for e in ents if e.get("bar")), ""), "notes": notes,
                        "raw_inputs": [], "makers": {}, "sections": [], "lanes": {}, "text": "\n".join(notes)},
            "text": "\n".join(notes),
            "entities": [self.decorate(e) for e in ents], "sources": [], "sinks": [],
            "blueprint": planner.blueprint_string(ents, label, description=description),
        }

    def mall_candidates(self, params):
        """shared-ingredient candidates for a product list: made by another product, or used by 2+ products"""
        d = self.data
        recipes = d.raw["recipe"]
        products = [p for p in params.get("products") or [] if p in recipes]
        made = {}
        for p in products:
            rs = recipes[p].get("results") or []
            if len(rs) == 1:
                made[rs[0]["name"]] = p
        count = {}
        for p in products:
            for ing in recipes[p].get("ingredients", []):
                if ing.get("type", "item") == "item":
                    count[ing["name"]] = count.get(ing["name"], 0) + 1
        out = []
        for item, n in sorted(count.items(), key=lambda kv: -kv[1]):
            if item in made or n >= 2:
                options = sorted((rn for rn, rr in recipes.items() if not rr.get("hidden")
                                  and len(rr.get("results") or []) == 1 and rr["results"][0]["name"] == item),
                                 key=lambda rn: (rn != item, rn))[:20]
                if options:
                    out.append({"item": item, "users": n, "made_by_product": item in made,
                                "default_on": item in made, "recipes": options,
                                "default_recipe": made.get(item) or options[0]})
        return out

    def _line_kwargs(self, params):
        bonuses = dict(params.get("bonuses") or calibrate.ZERO, belt_stack_size_bonus=0)
        mods = Modules.parse(params.get("modules"))
        bmods = Modules.parse(params.get("beacon_modules"))
        beacon = {"beacon": params.get("beacon") or "beacon", "beacon_modules": bmods,
                  "beacon_quality": params.get("beacon_quality") or "normal"} if bmods.count else {}
        return dict(bonuses=bonuses, allowed=self._inserters(params), productivity=params.get("productivity") or 0,
                    modules=mods, machine_quality=params.get("machine_quality") or "normal",
                    per_row=params.get("per_row") or None, near=params.get("near") or None,
                    far=params.get("far") or None, out=params.get("out") or None,
                    target_rate=(params.get("rate_per_min") or 0) / 60 or None, **beacon)

    def pick_machine(self, params, machines):
        """the machine to show for a newly picked recipe: the current one if it plans, else the first that does
        (machines not copied from another by a mod first, then slowest ~ earliest first). Uses measurements
        already on disk only, so it never runs the game."""
        d = self.data
        table = json.loads(self.calib_path.read_text()) if self.calib_path.exists() else {}
        if not machines or not table or not params.get("belt"):
            return None
        kw = self._line_kwargs(params)

        def proto(n):
            return d.raw["assembling-machine"].get(n) or d.raw.get("furnace", {}).get(n, {})

        def copy(n):  # bioluminescent-/harene-infused- variants and the like run without power: not a default
            return proto(n).get("energy_source", {}).get("type") == "void"

        from bpgen import base, fluidlines
        depth = base.unlock_depth(d)
        current = params.get("machine")
        order = ([current] if current in machines else []) + sorted(
            (n for n in machines if n != current),
            key=lambda n: (copy(n), depth.get(n, 999), proto(n).get("crafting_speed", 1), n))
        r = d.raw["recipe"].get(params["recipe"]) or {}
        product = (r.get("results") or [{}])[0].get("name")
        for n in order:
            try:
                planner.plan(d, table, params["recipe"], n, params["belt"], **kw)
                return n
            except (planner.PlanError, KeyError, ValueError):
                pass
            try:  # fluid recipes: the fluid line layouts
                fluidlines.plan_fluid(d, table, params["recipe"], n, params["belt"], allowed=self._inserters(params))
                return n
            except (planner.PlanError, KeyError, ValueError, TypeError):
                pass
            try:
                fluidlines.plan_multi(d, params["recipe"], n, product, 1.0)
                return n
            except (planner.PlanError, KeyError, ValueError, TypeError):
                pass
        return order[0] if order else None  # nothing lays out: still the likeliest machine, not the first by name

    def plan(self, params, progress=None, cancel=None):
        """-> result dict (plan summary, entities with sizes, blueprint string)"""
        out = self._plan(params, progress, cancel)
        if params.get("early_note") and isinstance(out.get("summary"), dict):
            out["summary"]["notes"] = [params["early_note"]] + list(out["summary"].get("notes") or [])
        return out

    def _plan(self, params, progress=None, cancel=None):
        if params.get("mode") == "mall":
            return self.plan_mall(params, progress, cancel)
        if params.get("mode") == "base":
            return self.plan_base(params, progress, cancel)
        if params.get("mode") == "extend":
            return self.plan_extension(params, progress, cancel)
        if params.get("feed") == "robots":
            return self.plan_bot_line(params)
        if progress:
            progress("checking inserter measurements for this belt and research level")
        calib = self.ensure_calibrated(params, progress, cancel)
        if progress:
            progress("planning the layout")
        kw = self._line_kwargs(params)
        make = {k: v for k, v in (params.get("make") or {}).items() if v and v.get("recipe") and v.get("machine")}
        also = {k: v for k, v in (params.get("also") or {}).items() if v and v.get("recipe")}
        if also or any(k in self.data.raw.get("fluid", {}) for k in make):
            return self.plan_fluid_chain(params, make, progress, cancel, also)
        stages, warmup = [], None
        if make:
            if progress:
                progress(f"planning the chain: {', '.join(make)} made in the blueprint, routing belts")
            try:
                c = chain.plan_chain(self.data, calib, params, make, **kw)
            except planner.PlanError as e:
                # the chain template feeds made items on the final block's own belts (2 per belt, 2 belts): when
                # they don't fit there, the starter-base composer lays each out as its own block with routed belts
                if progress:
                    progress(f"{e}: planning it as separate blocks with routed belts instead")
                return self.plan_fluid_chain(params, make, progress, cancel)
            p, ents, sources, sinks = c.final.plan, c.entities, c.sources, c.sinks
            warmup = chain.warmup_ticks(self.data, c)
            for s in c.stages:
                _, item, row = s.role.split(":")
                if row == "b":
                    continue  # the twin of its row-a block
                blocks = 1 if row == "ab" else 2
                stages.append({"item": item, "recipe": s.plan.recipe, "machine": s.plan.machine,
                               "machines": blocks * s.plan.machines, "per_block": s.plan.machines, "blocks": blocks,
                               "output": round(blocks * min(s.plan.expected_output, s.plan.machines * s.plan.output_per_machine), 3),
                               "need": round(2 * p.input_need[item], 3), "notes": s.plan.notes})
        else:
            try:
                p = planner.plan(self.data, calib, params["recipe"], params["machine"], params["belt"], **kw)
            except planner.PlanError as e:
                # fluid output, or more fluids than the belt template's one pipe: the fluid lines (fluidlines.py)
                r = self.data.raw["recipe"].get(params["recipe"]) or {}
                if not any(x.get("type") == "fluid" for x in (r.get("ingredients") or []) + (r.get("results") or [])):
                    raise
                try:
                    return self.plan_fluid(params, progress, cancel)
                except planner.PlanError as e2:
                    raise planner.PlanError(f"{e}; as a fluid line: {e2}") from e2
            ents, sources, sinks = planner.layout(p)
        ents, sources, sinks, description = labels.add_labels(self.data, ents, sources, sinks, p.output, p.expected_output)
        label = f"{p.recipe} x{p.machines} {p.expected_output:g}/s"
        return {
            "params": params,
            "description": description,
            "summary": {
                "recipe": p.recipe, "machine": p.machine, "belt": p.belt, "machines": p.machines, "per_row": p.per_row,
                "output": p.output, "expected": round(p.expected_output, 3), "belt_capacity": p.belt_capacity,
                "per_machine": round(p.output_per_machine, 4), "speed": round(p.speed, 3),
                "productivity": round(p.productivity, 3), "input_need": {k: round(v, 3) for k, v in p.input_need.items()},
                "fluid": p.fluid, "fluid_need": round(p.fluid_need, 2), "belts": p.belts,
                "near": vars(p.ins_near), "far": vars(p.ins_far) if p.ins_far else None, "out": vars(p.ins_out),
                "beacon": p.beacon, "beacons_per_machine": p.beacons_per_machine, "packing": p.packing,
                "notes": p.notes, "warmup": warmup or planner.warmup_ticks(self.data, p),
                "measure": planner.measure_ticks(p), "stages": stages,
                "raw_inputs": sorted({l for s in sources if s["kind"] == "belt" for l in s["lanes"] if l}
                                     | {s["fluid"] for s in sources if s["kind"] == "fluid"}),
            },
            "text": planner.describe(p),
            "entities": [self.decorate(e) for e in ents],
            "sources": sources, "sinks": sinks,
            "blueprint": planner.blueprint_string(ents, label, description=description),
        }

    def plan_fluid_chain(self, params, make, progress=None, cancel=None, also=None):
        """a line with ingredients made in the blueprint, fluids among them, and/or other products bundled in (`also`:
        {item: {recipe, rate_per_min}}, sharing its inputs): planned like a starter base (blocks, belts and pipes
        routed between them), with by-products burnt, sent out or converted as chosen"""
        also = also or {}
        d = self.data
        recipe = d.raw["recipe"][params["recipe"]]
        product = (recipe.get("results") or [{}])[0].get("name")
        rate = params.get("rate_per_min")
        if not rate:  # what the line would make on its own
            kw = self._line_kwargs(params)
            try:
                rate = planner.plan(d, self.ensure_calibrated(params, progress, cancel), params["recipe"],
                                    params["machine"], params["belt"], **kw).expected_output * 60
            except planner.PlanError:
                rate = self.plan_fluid(params)["summary"]["expected"] * 60
        overrides = {product: params["recipe"]}
        targets = {product: rate}
        for item, spec in also.items():  # bundled products: each its own block on the shared inputs
            overrides[item] = spec["recipe"]
            targets[item] = spec.get("rate_per_min") or rate
        fluid_plans = {}
        for item, spec in make.items():
            r = d.raw["recipe"].get(spec["recipe"]) or {}
            if len(r.get("results") or []) > 1:
                bps = {}
                for b, act in (spec.get("byproducts") or {}).items():
                    bps[b] = {"convert": act[8:]} if isinstance(act, str) and act.startswith("convert:") else act
                for o in r["results"]:  # anything not chosen: sent out
                    if o["name"] != item:
                        bps.setdefault(o["name"], "out")
                fluid_plans[item] = {"recipe": spec["recipe"], "byproducts": bps}
            else:
                overrides[item] = spec["recipe"]
        names = set(d.raw.get("fluid", {})) | {n for t in ("item", "tool", "capsule", "ammo", "module") for n in d.raw.get(t, {})}
        raw = names - set(targets) - set(make)
        machines = [params["machine"]] + [v["machine"] for v in make.values()]
        out = self.plan_base({"mode": "base", "targets": targets, "raw": sorted(raw), "recipes": overrides,
                              "fluid_plans": fluid_plans, "labs": False, "mall": False, "routed": True,
                              "belt": params["belt"], "assembler": params["machine"], "furnace": "stone-furnace",
                              # (a production line may use every calibrated inserter, not only the early ones the
                              # starter base defaults to)
                              "machines": machines, "inserters": params.get("inserters") or self._inserters(params),
                              "bonuses": params.get("bonuses"), "productivity": params.get("productivity")},
                             progress, cancel)
        if also and params.get("share_lanes"):
            self._share_lanes(out, params["belt"], targets)
        out["params"] = dict(params, mode=None)
        return out

    def _share_lanes(self, out, belt, targets):
        """bundled products two to a belt, one per lane (lanes.py): the connected print and the book get the merge
        belts; what couldn't pair up keeps its own belt (notes in the route note)"""
        from bpgen import base, lanes
        routed = next((sec for sec in out["sections"] if sec["kind"] == "routed"), None)
        if not routed:
            return
        res = routed["result"]
        rates = {k: v / 60 for k, v in targets.items()}
        added, sinks, notes = lanes.share_output_lanes(self.data, res["entities"], res["sinks"], belt, rates,
                                                       planner.belt_capacity(self.data, belt) / 2)
        res["entities"] = res["entities"] + [self.decorate(e) for e in added]
        res["sinks"] = sinks
        label = base._decode(res["blueprint"])["blueprint"].get("label", "bundle")
        res["blueprint"] = planner.blueprint_string(res["entities"], label)
        book = base._decode(out["blueprint"])
        prints = book["blueprint_book"]["blueprints"]
        i = out["sections"].index(routed)
        if i < len(prints):
            new = base._decode(res["blueprint"])
            new["blueprint"]["label"] = prints[i]["blueprint"].get("label", label)
            prints[i] = dict(new, index=prints[i].get("index", i))
            out["blueprint"] = base._encode(book)
        if i == 0:
            out["entities"], out["sinks"] = res["entities"], sinks
        note = "; ".join(notes)
        if note:
            out["summary"]["route_note"] = (out["summary"].get("route_note") + "; " if out["summary"].get("route_note") else "") + note

    def decorate(self, e):
        """add type and footprint so the preview can draw it"""
        t, proto = self.proto(e["name"])
        w = h = 1
        if proto and proto.get("collision_box"):
            (x1, y1), (x2, y2) = proto["collision_box"]
            w, h = max(1, math.ceil(x2 - x1)), max(1, math.ceil(y2 - y1))
        out = dict(e, type=t or "unknown", w=w, h=h)
        if proto and (proto.get("fluid_boxes") or proto.get("fluid_box") or proto.get("output_fluid_box")):
            out["fluid"] = True  # pipes next to it draw connected
        return out

    def blueprint(self, entities, label, description=None):
        clean = [{k: v for k, v in e.items() if k not in ("type", "w", "h")} for e in entities]
        return planner.blueprint_string(clean, label, description=description)

    def verify_mall(self, result, progress=None, cancel=None):
        """build the mall and check every product ends up in a chest"""
        params = result["params"]
        bp = self.blueprint(result["entities"], "verify")
        self._locked(progress, cancel)
        try:
            res = harness.run({"warmup": 60, "measure": 60 * 60 * 30, "cell": _cell_for(result["entities"]),
                               "force": dict(params.get("bonuses") or calibrate.ZERO, belt_stack_size_bonus=0),
                               "cases": [{"id": "mall", "blueprint": bp, "sources": result["sources"], "sinks": []}]},
                              mod_dir=self.mod_dir, progress=progress, cancel=cancel)
        finally:
            self.lock.release()
        c = res["cases"][0]
        chests = c.get("chests") or {}
        wanted = {}
        for r in result.get("verify_products") or result["summary"]["products"]:  # one section of a split mall
            rs = self.data.raw["recipe"][r].get("results") or []
            wanted[r] = rs[0]["name"] if rs else r
        got = {r: chests.get(item, 0) for r, item in wanted.items()}
        ok = sum(1 for v in got.values() if v > 0)
        return {"mode": "mall", "chests": got, "ratio": ok / max(1, len(got)), "measured": ok,
                "errors": c["errors"] or [], "status": c.get("status")}

    def verify_labs(self, result, progress=None, cancel=None):
        """build the labs, feed their packs, keep research going: they should consume every pack at the target rate
        (the feeders keep the belts full, so what they feed is what the labs eat)"""
        s = result["summary"]
        bp = self.blueprint(result["entities"], "verify")
        self._locked(progress, cancel)
        try:
            res = harness.run({"warmup": 60 * 60, "measure": 60 * 60, "cell": _cell_for(result["entities"]),
                               "research": s["packs"],
                               "cases": [{"id": "labs", "blueprint": bp, "sources": result["sources"], "sinks": []}]},
                              mod_dir=self.mod_dir, progress=progress, cancel=cancel)
        finally:
            self.lock.release()
        c = res["cases"][0]
        fed = c.get("fed") or {}
        eaten = min(fed.get(p, 0) for p in s["packs"])
        return {"mode": "labs", "measured": round(eaten, 3), "ratio": round(eaten / s["rate"], 4) if s.get("rate") else 0,
                "fed": fed, "errors": c["errors"] or [], "status": c.get("status")}

    def verify(self, result, progress=None, cancel=None):
        """build result['entities'] (possibly edited) from its blueprint string and measure the output"""
        if result.get("mode") == "mall":
            return self.verify_mall(result, progress, cancel)
        if result.get("mode") == "labs":
            return self.verify_labs(result, progress, cancel)
        s = result["summary"]
        params = result["params"]
        bp = self.blueprint(result["entities"], "verify")
        self._locked(progress, cancel)
        try:
            res = harness.run({
                # (fluid lines carry no timings of their own: pipes and slow recipes want a long warmup)
                "warmup": s.get("warmup") or 60 * 60 * 4, "measure": s.get("measure") or 60 * 60 * 2,
                "cell": _cell_for(result["entities"]),
                "force": dict(params.get("bonuses") or calibrate.ZERO, belt_stack_size_bonus=0),
                "recipe_productivity": {s["recipe"]: params.get("productivity") or 0},
                "cases": [{"id": "bp", "blueprint": bp, "sources": result["sources"], "sinks": result["sinks"]}],
            }, mod_dir=self.mod_dir, progress=progress, cancel=cancel)
        finally:
            self.lock.release()
        c = res["cases"][0]
        got = c["rates"].get(s["output"], 0)
        return {"measured": round(got, 3), "ratio": round(got / s["expected"], 4) if s["expected"] else 0,
                "errors": c["errors"], "status": c.get("status"), "machine": c.get("machine")}

    # ---- options for the UI -----------------------------------------------------------------------------------
    def options(self, params):
        d = self.data
        recipe = d.raw["recipe"].get(params.get("recipe"), {})
        category = recipe.get("category", "crafting")
        machines = sorted(n for t in ("assembling-machine", "furnace") for n, m in d.raw.get(t, {}).items()
                          if category in m.get("crafting_categories", []) and not m.get("hidden"))
        belts = self.base_belts(params.get("belts") or [n for n, b in d.raw["transport-belt"].items() if not b.get("hidden")])
        allowed = params.get("inserters") or None
        machine = d.raw["assembling-machine"].get(params.get("machine")) or d.raw.get("furnace", {}).get(params.get("machine"), {})
        out = {
            "machines": machines,
            "belts": sorted(belts, key=lambda b: d.raw["transport-belt"][b]["speed"]),
            "near": calibrate.template_inserters(d, allowed), "far": calibrate.template_inserters(d, allowed, 2),
            "modules": sorted(n for n, m in d.raw["module"].items() if not m.get("hidden")),
            "beacons": sorted(n for n, b in d.raw["beacon"].items() if not b.get("hidden")),
            "qualities": [q for q, v in sorted(d.raw.get("quality", {}).items(), key=lambda kv: kv[1].get("level", 0))
                          if "unknown" not in q and not v.get("hidden")],
            "module_slots": machine.get("module_slots", 0),
            "all_machines": sorted(n for n, m in d.raw["assembling-machine"].items() if not m.get("hidden")),
            "furnaces": sorted(n for t in ("assembling-machine", "furnace") for n, m in d.raw.get(t, {}).items()
                               if "smelting" in m.get("crafting_categories", []) and not m.get("hidden")
                               and m.get("energy_source", {}).get("type") != "void"),
            "labs": sorted(n for n, m in d.raw.get("lab", {}).items() if not m.get("hidden")),
            "science": self.science(params.get("lab") or "lab"),
            "chests": sorted(n for n, c in d.raw.get("container", {}).items()
                             if not c.get("hidden") and c.get("inventory_size", 0) >= 8 and "infinity" not in n),
            "recipes": sorted(n for n, r in d.raw["recipe"].items() if not r.get("hidden")),
        }
        # mod of every option, for the dropdowns' @mod filter
        mods = {}
        for n in out["recipes"]:
            mods["recipe/" + n] = self.mod_of("recipe/" + n)
        for key in ("machines", "belts", "near", "far", "modules", "beacons", "all_machines", "chests"):
            for n in out.get(key, []):
                mods[n] = self.mod_of(n)
        out["mod_of"] = {k: v for k, v in mods.items() if v}
        if params.get("pick_machine") and params.get("recipe"):
            out["machine_pick"] = self.pick_machine(params, machines)
        return out

    BOOK_ITEM_TYPES = ("item", "module", "tool", "capsule", "ammo", "gun", "armor", "repair-tool", "item-with-entity-data",
                       "rail-planner", "space-platform-starter-pack", "item-with-tags", "selection-tool",
                       "spidertron-remote", "fluid")

    def recipe_book(self, params=None):
        """the game's crafting menu: item groups (tabs), their subgroups (rows) and every recipe in its subgroup, in
        the game's order. A recipe without its own subgroup/order takes its main product's.
        -> {groups: [{name, order}], subgroups: {name: {group, order}}, recipes: {name: {...}}}"""
        if getattr(self, "_book", (None, None))[0] is self.data:  # (a data reload starts over)
            return self._book[1]
        raw = self.data.raw
        products = {}
        for t in self.BOOK_ITEM_TYPES:
            for n, p in raw.get(t, {}).items():
                products.setdefault(n, p)
        subgroups = {n: {"group": g.get("group", "other"), "order": g.get("order", "")}
                     for n, g in raw.get("item-subgroup", {}).items()}
        recipes = {}
        for n, r in raw["recipe"].items():
            if r.get("hidden"):  # (not pickable anyway; some packs have thousands of helper recipes)
                continue
            res = r.get("results") or []
            main = r.get("main_product") or (res[0]["name"] if len(res) == 1 else None)
            prod = products.get(main) or {}
            sub = r.get("subgroup") or prod.get("subgroup") or ("fluid" if prod.get("type") == "fluid" else "other")
            if sub not in subgroups:
                sub = "other"
                subgroups.setdefault("other", {"group": "other", "order": "z"})
            cat = r.get("category", "crafting")
            recipes[n] = {
                "sub": sub, "order": r.get("order") or prod.get("order") or "", "mod": self.mod_of("recipe/" + n),
                "recycling": "recycling" in cat or n.endswith("-recycling"), "time": r.get("energy_required", 0.5),
                "in": [[i.get("type", "item"), i["name"], i.get("amount", 1)] for i in r.get("ingredients") or []],
                "out": [[x.get("type", "item"), x["name"], x.get("amount", (x.get("amount_min", 0) + x.get("amount_max", 0)) / 2),
                         x.get("probability", 1)] for x in res],
            }
        by_prefix = {}
        vanilla = self._vanilla_recipes()
        for n, r in recipes.items():
            pre = re.match(r"[a-z0-9]+[-_]", n, re.I)
            if pre and r["mod"] and r["mod"] not in VANILLA_MODS:
                by_prefix.setdefault(pre.group(0), collections.Counter())[r["mod"]] += 1
        for n, r in recipes.items():
            pre = re.match(r"[a-z0-9]+[-_]", n, re.I)
            if n in vanilla:  # the game's own recipe, maybe re-skinned by a graphics mod
                if r["mod"] not in VANILLA_MODS:
                    r["mod"] = "base"
                continue
            c = by_prefix.get(pre.group(0)) if pre else None  # (icons are often borrowed)
            if c:
                mod, k = c.most_common(1)[0]
                if k >= 3 and k >= 0.8 * sum(c.values()):
                    r["mod"] = mod
        used = {subgroups[r["sub"]]["group"] for r in recipes.values()}
        groups = [{"name": n, "order": g.get("order", "")} for n, g in raw.get("item-group", {}).items() if n in used]
        if "other" in used and not any(g["name"] == "other" for g in groups):
            groups.append({"name": "other", "order": "zzz"})
        groups.sort(key=lambda g: (g["order"], g["name"]))
        self._book = (self.data, {"groups": groups, "subgroups": subgroups, "recipes": recipes})
        return self._book[1]

    def siblings(self, params):
        """recipes that could share this recipe's input belts: the same item ingredients (exact first), or all but one
        of them plus one more, at most 4 items in all (2 belts), the same fluids, made in the same machine.
        -> [{recipe, item, exact, extra}]"""
        d = self.data
        recipes = d.raw["recipe"]
        r = recipes.get(params.get("recipe"))
        if not r:
            return []

        def parts(rr):
            items = {i["name"] for i in rr.get("ingredients") or [] if i.get("type", "item") == "item"}
            fluids = {i["name"] for i in rr.get("ingredients") or [] if i.get("type") == "fluid"}
            return items, fluids
        items, fluids = parts(r)
        if not items and not fluids:
            return []
        machine = d.raw["assembling-machine"].get(params.get("machine")) or d.raw.get("furnace", {}).get(params.get("machine"))
        cats = set(machine.get("crafting_categories", [])) if machine else {r.get("category", "crafting")}
        main = {x.get("name") for x in r.get("results") or []}
        out = []
        for rn, rr in recipes.items():
            if rn == params.get("recipe") or rr.get("hidden") or "recycling" in rr.get("category", "") \
                    or rr.get("category", "crafting") not in cats:
                continue
            res = rr.get("results") or []
            if len(res) != 1 or res[0]["name"] in main:  # (one product: an item, or a fluid piped out)
                continue
            its, fls = parts(rr)
            if fls != fluids or (items and not its) or len(items | its) > 4:
                continue
            missing, extra = len(items - its), len(its - items)
            if missing > 1 or extra > 1 or (missing and extra and len(items) == 1):
                continue
            if not items and extra:  # fluid-only: the same fluids, no items
                continue
            out.append({"recipe": rn, "item": res[0]["name"], "exact": its == items, "extra": extra, "missing": missing})
        out.sort(key=lambda x: (not x["exact"], x["extra"], x["missing"], x["recipe"]))
        return out[:12]

    def recipe_tree(self, params):
        """the final recipe's item ingredients, each with the recipes that make it and the machines for those"""
        d = self.data
        r = d.raw["recipe"].get(params.get("recipe"))
        if not r:
            return []
        machines_for = {}
        for t in ("assembling-machine", "furnace"):
            for n, m in d.raw.get(t, {}).items():
                if not m.get("hidden"):
                    for cat in m.get("crafting_categories", []):
                        machines_for.setdefault(cat, []).append(n)
        makers = {}
        for rn, rr in d.raw["recipe"].items():
            if rr.get("hidden") or len(rr.get("results") or []) != 1:
                continue
            res = rr["results"][0]
            if res.get("type", "item") == "item" and rr.get("category", "crafting") in machines_for:
                makers.setdefault(res["name"], []).append(rn)
        final_machine = params.get("machine")
        out = []
        for ing in r.get("ingredients", []):
            if ing.get("type") == "fluid":
                out.append({"item": ing["name"], "fluid": True, "amount": ing.get("amount"),
                            "recipes": self._fluid_makers(ing["name"], machines_for, final_machine)})
                continue
            recipes = sorted(makers.get(ing["name"], []), key=lambda n: (n != ing["name"], n))
            opts = []
            for rn in recipes[:40]:
                ms = sorted(machines_for.get(d.raw["recipe"][rn].get("category", "crafting"), []))
                opts.append({"recipe": rn, "machines": ms,
                             "default_machine": final_machine if final_machine in ms else (ms[0] if ms else None)})
            out.append({"item": ing["name"], "fluid": False, "amount": ing.get("amount"), "recipes": opts})
        return out

    def _fluid_makers(self, fluid, machines_for, final_machine=None):
        """recipes that make a fluid: one-product ones, and fluid-only ones with several products (each other
        product then needs a choice: burn it, send it out, or convert it)"""
        from bpgen import fluidlines
        d = self.data
        recipes = d.raw["recipe"]

        def all_fluid(r):
            parts = (r.get("ingredients") or []) + (r.get("results") or [])
            return parts and all(x.get("type", "item") == "fluid" for x in parts)
        opts = []
        for rn, r in recipes.items():
            res = r.get("results") or []
            cat = r.get("category", "crafting")
            if r.get("hidden") or "recycling" in cat or cat not in machines_for:
                continue
            if not any(o["name"] == fluid for o in res):
                continue
            if len(res) > 1 and not all_fluid(r):
                continue  # several products with items among them: no layout for that yet
            ms = sorted(machines_for[cat])
            byproducts = []
            others = [o["name"] for o in res if o["name"] != fluid]
            for b in others:
                choices = []
                if fluidlines.flare_for(d, b):
                    choices.append({"value": "flare", "label": "burn it"})
                choices.append({"value": "out", "label": "send it out (output pipe)"})
                for cn, c in recipes.items():
                    cres = c.get("results") or []
                    ccat = c.get("category", "crafting")
                    if c.get("hidden") or len(cres) != 1 or ccat not in machines_for:
                        continue
                    if any(i["name"] == b for i in c.get("ingredients", [])) and cres[0]["name"] in [fluid] + others                             and cres[0]["name"] != b:
                        choices.append({"value": "convert:" + cn, "label": f"convert: {cn} -> {cres[0]['name']}"})
                byproducts.append({"fluid": b, "choices": choices[:30]})
            opts.append({"recipe": rn, "machines": ms, "byproducts": byproducts,
                         "default_machine": final_machine if final_machine in ms else (ms[0] if ms else None)})
        opts.sort(key=lambda o: (len(o["byproducts"]) > 0, o["recipe"] != fluid, o["recipe"]))
        return opts[:40]

    def _icon_mod(self, proto):
        """the mod a prototype comes from, judged by its icon path (__mod__/...)"""
        if not proto:
            return None
        icon = proto.get("icon") or (proto.get("icons") or [{}])[0].get("icon")
        if icon and icon.startswith("__"):
            mod = icon[2:].split("__", 1)[0]
            return self._asset_owners().get(mod, mod)
        return None

    def _vanilla_recipes(self):
        """the game's own recipe names (base and its expansions): never credited to a mod"""
        self._vanilla_protos()
        return self._vanilla_names

    def _vanilla_protos(self):
        """every prototype name of the game itself (any type)"""
        if getattr(self, "_vanilla_names", None) is None:
            vanilla = ROOT / "data" / "vanilla-dump.json"
            if self.mode != "pack":
                raw = self.data.raw
            elif vanilla.exists():
                raw = Data.load(vanilla).raw
            else:  # (no vanilla dump yet: python -m bpgen.setup) every recipe is credited by its icon / name only
                raw = {"recipe": {}}
            self._vanilla_names = set(raw["recipe"])
            self._vanilla_all = {n for t in raw.values() if isinstance(t, dict) for n in t}
        return self._vanilla_all

    def _mod_by_name(self, name):
        """a modded prototype drawn with a vanilla icon: the installed mod its name mentions (hephaestus-processor ->
        Hephaestus), else None"""
        self._asset_owners()
        words = re.split(r"[-_]", name.lower())
        for i in range(len(words)):
            for j in range(len(words), i, -1):  # the longest run of words first (space-exploration over space)
                for sep in ("-", "_"):
                    hit = self._mod_names.get(sep.join(words[i:j]))
                    if hit and len(sep.join(words[i:j])) >= 4:
                        return hit
        return None

    def _fix_mod(self, name, mod):
        """icons are often borrowed: a modded name that mentions an installed mod is credited to it; one drawn with a
        vanilla icon and mentioning none gets no mod rather than 'factorio'"""
        if self.mode != "pack" or name in self._vanilla_protos():
            return mod
        return self._mod_by_name(name) or (None if mod in VANILLA_MODS else mod)

    def _asset_owners(self):
        """graphics-only mods -> the mod that uses them (Krastorio2Assets -> Krastorio2): an asset mod's name less its
        Assets/graphics suffix, matched to an installed mod that depends on it"""
        if getattr(self, "_owners", (None, None))[0] is self.data:
            return self._owners[1]
        import zipfile
        deps = {}  # mod -> the mods it requires
        folder = pack.PACK if self.mode == "pack" else None
        for f in (os.listdir(folder) if folder and os.path.isdir(folder) else []):
            path = os.path.join(folder, f)
            try:
                if f.endswith(".zip"):
                    with zipfile.ZipFile(path) as z:
                        info = next((n for n in z.namelist() if n.count("/") == 1 and n.endswith("/info.json")), None)
                        meta = json.loads(z.read(info).decode("utf-8-sig")) if info else None
                else:
                    with open(os.path.join(path, "info.json"), encoding="utf-8-sig") as fh:
                        meta = json.load(fh)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                continue
            if meta and meta.get("name"):
                deps[meta["name"]] = {re.split(r"[<>=\s]", d.strip().lstrip("~").strip(), 1)[0]
                                      for d in meta.get("dependencies") or [] if not d.strip().startswith(("?", "!", "(?)"))}
        owners = {}
        for mod in deps:
            stem = re.sub(r"[-_ ]?(assets?|graphics?|sprites?|gfx|art)$", "", mod, flags=re.I)
            if stem == mod or not stem:
                continue
            users = sorted((m for m, d in deps.items() if mod in d and m.lower().startswith(stem.lower())), key=len)
            if users:
                owners[mod] = users[0]
        self._owners = (self.data, owners)
        self._mod_names = {m.lower(): m for m in deps}
        return owners

    def mod_of(self, key):
        """'recipe/name' or an entity/item name -> mod name (recipes without an icon use their product's)"""
        raw = self.data.raw
        kind, _, name = key.rpartition("/")
        if kind == "recipe":
            r = raw["recipe"].get(name, {})
            mod = self._icon_mod(r)
            if not mod:
                res = r.get("results") or []
                main = r.get("main_product") or (res[0]["name"] if res else None)
                for t in ("item", "tool", "module", "capsule", "ammo", "fluid", "armor", "gun", "item-with-entity-data",
                          "rail-planner", "repair-tool"):
                    mod = self._icon_mod(raw.get(t, {}).get(main))
                    if mod:
                        break
            return self._fix_mod(name, mod)
        for t in ENTITY_TYPES + ["item", "module", "quality", "container"]:
            mod = self._icon_mod(raw.get(t, {}).get(name))
            if mod:
                return self._fix_mod(name, mod)
        return self._fix_mod(name, None)

    def alternatives(self, name):
        """same-type entities an edit can swap to (inserters keep their reach)"""
        t, proto = self.proto(name)
        if not t:
            return []
        names = [n for n, p in self.data.raw[t].items() if not p.get("hidden")]
        if t == "inserter":
            reach = calibrate.reach(self.data, name)
            names = [n for n in names if calibrate.reach(self.data, n) == reach]
        return sorted(names)

    # ---- the player's save (the mod's files) ----

    def save_info(self):
        """research, best unlocked tech, production shortfalls and the snapshot's outline, or {} without a state"""
        out = {}
        if SAVE_STATE.exists():
            st = json.loads(SAVE_STATE.read_text(encoding="utf-8"))
            out = {"tick": st["tick"], "mtime": SAVE_STATE.stat().st_mtime, "picks": self.save_picks(st),
                   "production": self.production(st), "science": self.save_science(st), "items": self.makeable_items()}
        if SNAPSHOT.exists():
            snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
            x1, y1, x2, y2 = snap["area"]
            out["snapshot"] = {"tick": snap["tick"], "mtime": SNAPSHOT.stat().st_mtime, "surface": snap["surface"],
                               "size": [round(x2 - x1), round(y2 - y1)], "entities": len(snap["entities"])}
        return out

    def save_picks(self, st):
        """the best unlocked machines etc. for the pickers (unknown names, e.g. from another mod set, are skipped)"""
        d = self.data.raw
        u = st.get("unlocked") or {}

        def known(names, t):
            return [n for n in names or [] if n in d.get(t, {})]

        def best(names, t, key):
            names = known(names, t)
            return max(names, key=lambda n: (key(d[t][n]), n)) if names else None

        belts = self.base_belts(known(u.get("belts"), "transport-belt"))
        machines = known(u.get("assemblers"), "assembling-machine") + known(u.get("furnaces"), "furnace")
        proto = lambda n: d.get("assembling-machine", {}).get(n) or d["furnace"][n]  # noqa: E731
        crafting = [n for n in machines if "crafting" in proto(n).get("crafting_categories", [])]
        smelting = [n for n in machines if "smelting" in proto(n).get("crafting_categories", [])]
        speed = lambda n: proto(n).get("crafting_speed", 1)  # noqa: E731
        return {
            "belts": belts,
            "belt": max(belts, key=lambda b: d["transport-belt"][b]["speed"]) if belts else None,
            "assembler": max(crafting, key=lambda n: (speed(n), n)) if crafting else None,
            "furnace": max(smelting, key=lambda n: (speed(n), n)) if smelting else None,
            "lab": best(u.get("labs"), "lab", lambda p: p.get("researching_speed", 1)),
            "inserters": known(u.get("inserters"), "inserter"),
            "modules": known(u.get("modules"), "module"),
            # requester and passive provider chests: a robot-fed mall is an option
            "robots": all(any(d.get("logistic-container", {}).get(n, {}).get("logistic_mode") == mode
                              for n in u.get("chests") or []) for mode in ("requester", "passive-provider")),
            "bonuses": {k: (st.get("bonuses") or {}).get(k, 0) for k in calibrate.ZERO},
        }

    def save_check(self, params):
        """can the save build this blueprint? params: entities. Against the mod's state: recipes not unlocked,
        buildings and modules it can't craft yet (and has none of), and what the logistic network and inventory
        hold of each (bots build ghosts only from what's there).
        -> {state, age, recipes: [locked], buildings: [{item, need, have, craftable}], ok}"""
        if not SAVE_STATE.exists():
            return {"state": False}
        st = json.loads(SAVE_STATE.read_text(encoding="utf-8"))
        if st.get("recipes") is None:  # an older mod: no recipe list
            return {"state": False, "old": True}
        d = self.data.raw
        enabled = set(st["recipes"])
        made = set()
        for n in enabled:
            for res in (d["recipe"].get(n) or {}).get("results") or []:
                made.add(res["name"])
        stock = st.get("stock")
        places = self._place_items()
        need, recipes = collections.Counter(), set()
        for e in params.get("entities") or self._bp_entities(params.get("blueprint")):
            need[places.get(e["name"], e["name"])] += 1
            if e.get("recipe"):
                recipes.add(e["recipe"])
            for m in e.get("modules") or []:
                need[m[0]] += m[2]
        locked = sorted(r for r in recipes if r not in enabled)
        buildings = []
        for item, n in sorted(need.items()):
            have = (stock or {}).get(item, 0) if stock is not None else None
            buildings.append({"item": item, "need": n, "have": have, "craftable": item in made})
        ok = not locked and all(b["craftable"] or (b["have"] or 0) >= b["need"] for b in buildings)
        return {"state": True, "age": max(0, round(time.time() - SAVE_STATE.stat().st_mtime)), "ok": ok,
                "recipes": locked, "buildings": buildings, "stock": stock is not None}

    def module_ideas(self, params):
        """what modules would do for this line: none, productivity, speed, efficiency, productivity + speed beacons,
        each with the machines it takes for the rate (a full belt if none set), power and input per output.
        Modules: the save's unlocked ones when the mod says, else every visible one; the best of each kind.
        -> {target, ideas: [{name, modules, beacon_modules, machines, power_kw, input, beacons}]}"""
        from bpgen.effects import EffectError, Modules, machine_effects, module_effect
        d = self.data.raw
        r = d["recipe"].get(params.get("recipe"))
        m = d.get("assembling-machine", {}).get(params.get("machine")) or d.get("furnace", {}).get(params.get("machine"))
        if not r or not m:
            return {"ideas": []}
        slots = m.get("module_slots", 0)
        unlocked = None
        if SAVE_STATE.exists():
            unlocked = (json.loads(SAVE_STATE.read_text(encoding="utf-8")).get("unlocked") or {}).get("modules")
        pool = [n for n, p in d["module"].items() if not p.get("hidden") and (unlocked is None or n in unlocked)]

        def best(kind):
            # the module with the biggest bonus of this kind (category names differ between packs: go by effect)
            cands = [(module_effect(self.data, n).get(kind, 0), n) for n in pool]
            cands = [c for c in cands if c[0] > 0]
            return max(cands)[1] if cands else None
        prod, speed, eff = best("productivity"), best("speed"), None
        effs = [(-module_effect(self.data, n).get("consumption", 0), n) for n in pool]
        effs = [e for e in effs if e[0] > 0 and not any(v > 0 for k, v in module_effect(self.data, e[1]).items()
                                                        if k in ("speed", "productivity"))]
        eff = max(effs)[1] if effs else None
        res = [x for x in r.get("results") or [] if x["name"] == (r.get("main_product") or x["name"])][:1]
        if not res:
            return {"ideas": []}
        out = res[0]
        amount = planner._amount(out)
        belt = params.get("belt") or "transport-belt"
        target = (float(params["rate_per_min"]) / 60) if params.get("rate_per_min") else planner.belt_capacity(self.data, belt)
        base_prod = float(params.get("productivity") or 0)
        beacon = params.get("beacon") if params.get("beacon") in d.get("beacon", {}) else None
        if not beacon:
            beacon = next(iter(sorted(n for n, b in d.get("beacon", {}).items() if not b.get("hidden"))), None)
        per_beacon = 0
        if beacon:
            size = planner._size(m)
            bp = d["beacon"][beacon]
            if planner._size(bp) <= size:
                per_beacon, _ = planner.beacon_reach(m, bp, size, planner._size(bp))
        b_slots = d["beacon"][beacon].get("module_slots", 0) if beacon else 0
        variants = [("no modules", Modules(), Modules(), 0)]
        if prod and slots:
            variants.append(("productivity", Modules([(prod, "normal", slots)]), Modules(), 0))
        if speed and slots:
            variants.append(("speed", Modules([(speed, "normal", slots)]), Modules(), 0))
        if eff and slots:
            variants.append(("efficiency", Modules([(eff, "normal", slots)]), Modules(), 0))
        if prod and speed and slots and per_beacon and b_slots:
            variants.append((f"productivity + speed {beacon}s", Modules([(prod, "normal", slots)]),
                             Modules([(speed, "normal", b_slots)]), per_beacon))
        ideas = []
        for name, mods, bmods, nb in variants:
            try:
                sp, pr, cons = machine_effects(self.data, m, r, mods, beacon if nb else None, bmods, nb)
            except EffectError:
                continue  # (e.g. productivity not allowed for this recipe)
            productivity = min(base_prod + pr, r.get("maximum_productivity", 3.0))
            per_machine = m["crafting_speed"] * (1 + sp) / r.get("energy_required", 0.5) * amount * (1 + productivity)
            n = math.ceil(target / per_machine - 1e-9)
            power = n * planner.energy(m.get("energy_usage", "0W")) * (1 + cons)
            beacons = 0
            if nb:  # B M B M rows: about one beacon per machine, plus the row ends
                beacons = n + 4
                power += beacons * planner.energy(d["beacon"][beacon].get("energy_usage", "0W"))
            ideas.append({"name": name, "modules": ",".join(f"{a}:{c}" for a, _, c in mods.items),
                          "beacon": beacon if nb else None, "beacon_modules": ",".join(f"{a}:{c}" for a, _, c in bmods.items),
                          "machines": n, "beacons": beacons, "power_kw": round(power / 1000),
                          "input": round(1 / (1 + productivity), 3),
                          "module_count": n * mods.count + beacons * bmods.count})
        return {"target": round(target * 60, 1), "ideas": ideas, "from_save": unlocked is not None}

    HISTORY = ROOT / "data" / "history.json"
    HISTORY_MAX = 100

    def history(self, params=None):
        """blueprints copied from the page, newest first. params {add: string, mode}: record one (a string already
        there moves to the top); {remove: time}: drop one"""
        try:
            items = json.loads(self.HISTORY.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            items = []
        params = params or {}
        if params.get("add"):
            bp = params["add"].strip()
            ents = self._bp_entities(bp)
            from bpgen import base
            obj = base._decode(bp)
            label = (obj.get("blueprint") or obj.get("blueprint_book") or {}).get("label") or "blueprint"
            made = collections.Counter(e["recipe"] for e in ents if e.get("recipe"))
            items = [h for h in items if h["blueprint"] != bp]
            items.insert(0, {"time": time.time(), "mode": params.get("mode"), "label": label,
                             "recipe": made.most_common(1)[0][0] if made else None, "entities": len(ents),
                             "book": "blueprint_book" in obj, "blueprint": bp})
            items = items[:self.HISTORY_MAX]
        elif params.get("remove") is not None:
            items = [h for h in items if h["time"] != params["remove"]]
        else:
            return items
        self.HISTORY.parent.mkdir(parents=True, exist_ok=True)
        self.HISTORY.write_text(json.dumps(items), encoding="utf-8")
        return items

    def unlocked_recipes(self):
        """the save's enabled recipes (the mod, 0.2.6+), or None"""
        if not SAVE_STATE.exists():
            return None
        return json.loads(SAVE_STATE.read_text(encoding="utf-8")).get("recipes")

    def _bp_entities(self, bp):
        """a blueprint (or book) string -> entities as the planner writes them: name, recipe, modules"""
        if not bp:
            return []
        from bpgen import base
        obj, out = base._decode(bp.strip()), []
        stack = [obj]
        while stack:
            o = stack.pop()
            if "blueprint_book" in o:
                stack += o["blueprint_book"].get("blueprints") or []
                continue
            for e in (o.get("blueprint") or {}).get("entities") or []:
                mods = [[p["id"]["name"], p["id"].get("quality", "normal"), len((p.get("items") or {}).get("in_inventory") or [])]
                        for p in e.get("items") or [] if isinstance(p, dict) and "id" in p]
                out.append({"name": e["name"], "recipe": e.get("recipe"), "modules": mods})
        return out

    def _place_items(self):
        """entity -> the item that places it"""
        if getattr(self, "_places", (None, None))[0] is self.data:
            return self._places[1]
        out = {}
        for t in self.BOOK_ITEM_TYPES:
            for n, p in self.data.raw.get(t, {}).items():
                if p.get("place_result"):
                    out.setdefault(p["place_result"], n)
        self._places = (self.data, out)
        return out

    def makeable_items(self):
        """every item or fluid some (non-recycling, visible) recipe makes, sorted"""
        out = set()
        for r in self.data.raw["recipe"].values():
            if r.get("hidden") or "recycling" in r.get("category", ""):
                continue
            for res in r.get("results") or []:
                out.add(res["name"])
        return sorted(out)

    def save_science(self, st):
        """the research rate now (the least made of the packs the labs use) and the next pack to make: the one
        the researchable technologies need most that the base doesn't make yet"""
        from bpgen import base
        made = (st.get("production") or {}).get("items_made") or {}
        used = (st.get("production") or {}).get("items_used") or {}
        packs = set()
        for lab in self.data.raw.get("lab", {}).values():
            packs.update(lab.get("inputs") or [])
        current = {p: round(made.get(p, 0), 1) for p in packs if used.get(p, 0) > 0 or made.get(p, 0) > 0}
        rate = min((v for v in current.values() if v > 0), default=0)
        order = base.pack_progression(self.data)
        wanted = st.get("next_packs") or {}
        candidates = [p for p in packs if p not in current and p in self.data.raw["recipe"]]
        if wanted:
            nxt = max((p for p in candidates if wanted.get(p)), default=None,
                      key=lambda p: (wanted[p], -(order.index(p) if p in order else 99)))
        else:
            nxt = next((p for p in order if p in candidates), None)
        return {"current": current, "rate": rate, "next": nxt}

    def plan_extension(self, params, progress=None, cancel=None):
        """a whole production chain for params["item"] at params["rate_per_min"], fed only by what the snapshot's
        belts carry, placed next to the base with taps (see extend.py)"""
        from bpgen import base, extend
        snap = self.snapshot_view(params.get("snapshot"))
        if not snap:
            raise planner.PlanError("no snapshot yet: in game, use the bpgen snapshot tool (shortcut bar) on your base")
        item, rate = params["item"], float(params.get("rate_per_min") or 30)
        d = self.data
        if item not in d.raw["recipe"] and not any(
                len(r.get("results") or []) == 1 and r["results"][0]["name"] == item for r in d.raw["recipe"].values()):
            raise planner.PlanError(f"nothing makes {item}")
        spare = self.spare()
        on_belts = {i for e in snap["entities"] if e["type"] in extend.BELT_TYPES for i in (e.get("lanes") or []) if i}
        assembler = params.get("assembler") or "assembling-machine-2"
        furnace = params.get("furnace") or "stone-furnace"
        picks = [assembler, furnace] + list(params.get("machines") or [])
        machine_for = base.machines_by_category(d, picks)
        raw = set(on_belts) - {item}
        made_here = []
        # what the base can't spare enough of is made here too, when its own ingredients run on the belts
        for _ in range(4):
            steps = base.solve(d, {item: rate / 60}, picks, raw, params.get("recipes"))
            changed = False
            for it in sorted(raw):
                st_ = steps.get(it)
                if not st_ or it not in spare or spare[it] >= st_.rate * 60:
                    continue
                rn = base.pick_recipe(d, it, machine_for, raw - {it}, avoid=(item,))
                ings = [i["name"] for i in d.raw["recipe"][rn].get("ingredients", [])] if rn else []
                if rn and ings and all(i in raw - {it} for i in ings):
                    raw.discard(it)
                    made_here.append(it)
                    changed = True
            if not changed:
                break
        if progress:
            progress(f"planning {item} at {rate:g}/min from your belts")
        r = base.plan_base(self, {
            "targets": {item: rate}, "raw": sorted(raw), "labs": False, "mall": False, "routed": True,
            "belt": params.get("belt") or "transport-belt", "assembler": assembler, "furnace": furnace,
            "machines": params.get("machines"), "inserters": params.get("inserters"), "bonuses": params.get("bonuses"),
            "productivity": params.get("productivity"), "recipes": params.get("recipes")}, progress, cancel)
        whole = next((s for s in r["sections"] if s["kind"] == "routed"), None)
        if whole is None:
            lines = [s for s in r["sections"] if s["kind"] == "line"]
            if len(lines) != 1:
                raise planner.PlanError(r.get("route_note") or "couldn't plan this chain as one connected build")
            whole = lines[0]
        res = whole["result"]
        if progress:
            progress("placing it next to your base")
        ground = extend.Ground(snap)
        seed = params.get("seed")
        sinks = [dict(k, item=k.get("item") or item) for k in res.get("sinks") or []]
        placed = extend.place(d, ground, res["entities"], res.get("sources") or [],
                              seed=(seed["x"], seed["y"]) if seed else None, avoid_ore=params.get("avoid_ore", True),
                              belt=params.get("belt") or "transport-belt", sinks=sinks, bus=params.get("bus") or "auto",
                              needs={k: v.rate for k, v in r["steps"].items() if not v.recipe})
        needs = {k: v.rate * 60 for k, v in r["steps"].items() if not v.recipe}  # what it takes from the base
        notes = list(placed["notes"]) + short_notes(needs, spare)
        if r["not_automated"]:
            notes.append("not automated here (bring in): " + ", ".join(n["item"] for n in r["not_automated"]))
        ents = [dict(self.decorate(e), new=True) for e in placed["entities"]]
        bp, box = extend.absolute_blueprint(ents, f"bpgen: {item} {rate:g}/min",
                                            description="Paste it with Ctrl+Shift so splitters replace the belts they tap.")
        outputs = [dict(o, item=o.get("item") or item) for o in placed["outputs"]]  # (the plan may be turned)
        return {"mode": "extend", "item": item, "rate": rate, "entities": ents, "blueprint": bp, "box": box,
                "outputs": outputs,
                "offset": placed["offset"], "taps": placed["taps"], "deliveries": placed["deliveries"], "notes": notes,
                "bus": placed.get("bus"), "upgrades": placed.get("upgrades") or [],
                "needs": {k: round(v, 1) for k, v in needs.items()}, "made_here": made_here,
                "steps": [{"item": k, "rate": round(v.rate * 60, 1), "recipe": v.recipe} for k, v in r["steps"].items()
                          if v.recipe],
                "plan": {"entities": res["entities"], "sources": res.get("sources") or [], "sinks": sinks,
                         "needs": {k: v.rate for k, v in r["steps"].items() if not v.recipe}}}

    def spare(self):
        """{item or fluid: per minute the base makes less what it uses} over the last 10 minutes, from the
        mod's state ({} without one)"""
        try:
            st = json.loads(SAVE_STATE.read_text(encoding="utf-8")) if SAVE_STATE.exists() else {}
        except (OSError, ValueError):
            return {}
        prod = st.get("production") or {}
        made = {**(prod.get("items_made") or {}), **(prod.get("fluids_made") or {})}
        used = {**(prod.get("items_used") or {}), **(prod.get("fluids_used") or {})}
        return {k: made.get(k, 0) - used.get(k, 0) for k in set(made) | set(used)}

    def production(self, st):
        """items and fluids the factory uses faster than it makes (per minute, last 10 minutes), worst first"""
        p = st.get("production") or {}
        recipes = self.data.raw["recipe"]
        makers = {}  # item -> the recipe to plan it with: the one named after it, else the first making only it
        for rn, r in sorted(recipes.items()):
            res = r.get("results") or []
            if not r.get("hidden") and len(res) == 1 and "recycling" not in r.get("category", ""):
                if res[0]["name"] not in makers or rn == res[0]["name"]:
                    makers[res[0]["name"]] = rn
        rows = []
        for kind in ("items", "fluids"):
            made, used = p.get(f"{kind}_made") or {}, p.get(f"{kind}_used") or {}
            for name in set(made) | set(used):
                m, u = made.get(name, 0), used.get(name, 0)
                rows.append({"name": name, "fluid": kind == "fluids", "made": round(m, 1), "used": round(u, 1),
                             "short": round(u - m, 1), "makeable": name in makers, "recipe": makers.get(name)})
        rows.sort(key=lambda r: -r["short"])
        return rows

    def snapshot_view(self, snap=None):
        """the snapshot (given, else the mod's file) with entities sized for drawing"""
        if snap is None:
            if not SNAPSHOT.exists():
                return None
            snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        snap["entities"] = [self.decorate(e) for e in snap["entities"]]
        return snap

    def extend(self, params):
        """place a plan (entities + sources) into the snapshot: free ground, belt taps, power, absolute blueprint"""
        from bpgen import extend
        snap = self.snapshot_view(params.get("snapshot"))
        if not snap:
            raise planner.PlanError("no snapshot yet: in game, use the bpgen snapshot tool (shortcut bar) on your base")
        ground = extend.Ground(snap)
        seed = params.get("seed")
        placed = extend.place(self.data, ground, params["entities"], params.get("sources") or [],
                              seed=(seed["x"], seed["y"]) if seed else None,
                              avoid_ore=params.get("avoid_ore", True), belt=params.get("belt") or "transport-belt",
                              connect=params.get("connect", True), sinks=params.get("sinks") or [],
                              bus=params.get("bus") or "auto", needs=params.get("needs"),
                              extend_bus=params.get("extend_bus", True))
        if params.get("needs"):  # (per minute, what the plan takes from the base)
            placed["notes"] = list(placed["notes"]) + short_notes(
                {k: v * 60 for k, v in params["needs"].items()}, self.spare())
        ents = [dict(self.decorate(e), new=True) for e in placed["entities"]]
        bp, box = extend.absolute_blueprint(ents, params.get("label") or "bpgen extension",
                                            description="Paste it with Ctrl+Shift so splitters replace the belts they tap.")
        return {"entities": ents, "offset": placed["offset"], "taps": placed["taps"], "deliveries": placed["deliveries"],
                "notes": placed["notes"], "bus": placed.get("bus"), "upgrades": placed.get("upgrades") or [],
                "blueprint": bp, "box": box}

    def last_request(self):
        return json.loads(REQUEST.read_text(encoding="utf-8")) if REQUEST.exists() else None
