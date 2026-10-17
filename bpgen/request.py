"""One plan request, whoever asks: the in-game window (ingame.plan, through the Session), the web app (web.App.plan)
and the console watcher (__main__) all call Service.request(params), which runs run() below. Service.params made the
params from the request (the default belt, the inserters, the bonuses); here the plan is made and, for the window,
fed from the player's bus, rebuilt in a refit area or placed next to the base, and its answer put together with the
notes and the test run's spec."""
import math


def test_spec(out, params):
    s = out["summary"]
    from bpgen import calibrate
    return {
        # (as Service.verify: the planner's own warmup and measuring time, else what fluid lines need)
        "warmup": s.get("warmup") or 60 * 60, "measure": s.get("measure") or 60 * 60,
        "force": dict(params.get("bonuses") or calibrate.ZERO, belt_stack_size_bonus=0),
        "recipe_productivity": {s["recipe"]: params.get("productivity") or 0} if s.get("recipe") else {},
        "output": s.get("output"), "expected": s.get("expected"),
        "case": {"id": "bp", "sources": out.get("sources") or [], "sinks": out.get("sinks") or []},
    }


def mall_test_spec(data, out, params):
    """a mall's test run: its input belts fed, nothing drained (its products stay in their chests); the window
    counts what has reached them"""
    from bpgen import calibrate, mall
    s = out.get("summary") or {}
    return {"warmup": 0, "measure": 20 * 3600, "output": None, "expected": None,
            "force": dict(params.get("bonuses") or calibrate.ZERO, belt_stack_size_bonus=0), "recipe_productivity": {},
            "products": [mall._only_result(data, r) or r for r in s.get("products") or []],
            "case": {"id": "bp", "sources": out.get("sources") or [], "sinks": []}}


def line_needs(data, sm):
    """{ingredient: items/s} a line takes at its expected output (its summary), or None (a mall, no one recipe)"""
    r = data.raw["recipe"].get(sm.get("recipe") or "")
    if not r or not sm.get("expected"):
        return None
    made = sum(p.get("amount", (p.get("amount_min", 0) + p.get("amount_max", 0)) / 2) * p.get("probability", 1)
               for p in r.get("results") or [] if p.get("name") == sm.get("output"))
    if not made:
        return None
    runs = sm["expected"] / made / (1 + (sm.get("productivity") or 0))
    need = {i["name"]: runs * i["amount"] for i in r.get("ingredients") or [] if i.get("type") != "fluid"}
    for st in sm.get("stages") or []:  # (made in the blueprint: what it's made from comes in instead)
        sr = data.raw["recipe"].get(st.get("recipe") or "")
        v = need.pop(st.get("item"), None)
        out = sum(p.get("amount", 0) for p in (sr or {}).get("results") or [] if p.get("name") == st.get("item"))
        if v is None or not out:
            continue
        for i in sr.get("ingredients") or []:
            if i.get("type") != "fluid":
                need[i["name"]] = need.get(i["name"], 0) + v / out * i["amount"]
    return need


def section_needs(data, sec, base_summary):
    """{item: items/s} a starter base's print takes from outside: a line's through its recipe, the whole base's what
    it brings in; else None"""
    if sec.get("kind") == "line":
        return line_needs(data, (sec.get("result") or {}).get("summary") or {})
    if sec.get("kind") == "routed":
        return {k: v / 60 for k, v in (base_summary.get("bring_in") or {}).items() if isinstance(v, (int, float))} or None
    return None


def absolute(placed):
    """what the window needs of a plan placed next to the base: its box, what it ties into, the bus's axis (its
    arrows move it along the bus) and the bus lanes it overdraws (its upgrade buttons)"""
    dl = placed.get("deliveries") or []
    return {"box": placed["box"], "taps": len(placed.get("taps") or []),
            "deliveries": len([d for d in dl if not d.get("new_lane")]),
            "new_lanes": len([d for d in dl if d.get("new_lane")]), "axis": (placed.get("bus") or {}).get("axis"),
            "upgrades": placed.get("upgrades") or [],
            # (what has to be brought by hand, and where the product comes out: AI Crew feeds and empties these)
            "inputs": placed.get("inputs") or [], "outputs": placed.get("outputs") or []}


def _bus_feed(s, params, site):
    """a production line fed from the player's main bus: the bus in the snapshot, and what the line takes from it.
    Ingredients the bus doesn't carry are made inside the blueprint (params["make"], as if ticked in the window)
    when there's a recipe for them. -> (the bus or None, notes)"""
    from bpgen import base, planner
    main = site.bus if site else None
    if not main:
        return None, ["no main bus around you (3+ long straight belts side by side): planned as a plain line"]
    data = s.data
    on_bus = {i for ln in main["lanes"] for i in ln.get("items") or [] if i}
    r = data.raw["recipe"][params["recipe"]]
    out_items = {x.get("name") for x in r.get("results") or []}
    make = dict(params.get("make") or {})
    machine_for = base.machines_by_category(data, [params.get("machine"), "assembling-machine-2", "electric-furnace",
                                                   "stone-furnace"])
    taken, made, missing = [], [], []
    for ing in r.get("ingredients") or []:
        n = ing["name"]
        if ing.get("type") == "fluid":
            continue
        if n in make:
            made.append(n)
        elif n in on_bus:
            taken.append(n)
        else:
            rn = base.pick_recipe(data, n, machine_for, on_bus, avoid=out_items)
            if rn:
                make[n] = {"recipe": rn, "machine": machine_for[planner.category_in(data.raw["recipe"][rn], machine_for)]}
                made.append(n)
            else:
                missing.append(n)
    params["make"] = make
    notes = [f"from your bus: {' '.join(f'[item={i}]' for i in taken) or 'nothing'}"]
    if made:
        notes.append(f"made in the blueprint (not on your bus): {' '.join(f'[item={i}]' for i in made)}")
    if missing:
        notes.append(f"not on your bus and not made here: {' '.join(f'[item={i}]' for i in missing)}")
    return main, notes


def _mall_bus_feed(s, params, site):
    """a grid mall fed from the player's main bus: what the bus carries comes in on its belts (tapped from the bus),
    the parts it doesn't are made at the top. -> (the bus or None, notes)"""
    from bpgen import base
    main = site.bus if site else None
    if not main:
        return None, ["no main bus around you (3+ long straight belts side by side): only plates come in"]
    on_bus = {i for ln in main["lanes"] for i in ln.get("items") or [] if i}
    raw = base.raw_items(s.data) - base.MADE_HERE
    plates = base.plate_items(s.data, raw)
    params["inputs"] = sorted(raw | plates | base.plate_items(s.data, plates) | on_bus)
    return main, [f"your bus carries {' '.join(f'[item={i}]' for i in sorted(on_bus))}: taken from it"]


def _refit(s, params):
    """a messy area of the base rebuilt as a compact grid mall in its place: params["refit"] = {"area": [x1, y1, x2,
    y2], "add": more machines allowed} with params["snapshot"] (the area and round it). The area's entities leave
    the snapshot (free ground for the mall) but for the main bus's belts and machines making what the mall won't.
    -> (what to deconstruct: [{name, position}], the area's assembling machines rebuilt, notes)"""
    from bpgen import extend, planner
    snap = s.snapshot_view(params.get("snapshot"))
    if not snap:
        raise planner.PlanError("no snapshot of the area")
    x1, y1, x2, y2 = params["refit"]["area"]
    products = set(params.get("products") or [])
    main = extend.find_bus(extend.Ground(snap))
    bus_tiles = main["tiles"] if main else set()
    keep, clear, machines, kept = [], [], 0, set()
    for e in snap["entities"]:
        x, y = e["position"]["x"], e["position"]["y"]
        inside = x1 <= x <= x2 and y1 <= y <= y2
        machine = e["type"] in ("assembling-machine", "furnace", "rocket-silo", "lab")
        if not inside or (math.floor(x), math.floor(y)) in bus_tiles or (machine and not params["refit"].get("all")
                                                                          and not (e["type"] == "assembling-machine"
                                                                                   and e.get("recipe") in products)):
            keep.append(e)
            if inside and machine and not e.get("ghost"):
                kept.add(e.get("recipe") or e["name"])
            continue
        if not e.get("ghost"):
            clear.append({"name": e["name"], "position": e["position"]})
            machines += e["type"] == "assembling-machine"
    params["snapshot"] = dict(snap, entities=keep)
    notes = [f"refit: {len(clear)} entities in the area removed, the mall built in their place"]
    if kept:
        notes.append("left standing (the mall doesn't make them): " + " ".join(
            f"[recipe={k}]" if k in s.data.raw["recipe"] else f"[entity={k}]" for k in sorted(kept)))
    if any(e["type"] == "electric-pole" for e in snap["entities"] if e not in keep and not e.get("ghost")):
        notes.append("its poles are replaced by the mall's: check power still reaches past the area")
    return clear, machines, notes


def _refit_inputs(s, params):
    """no machines added: what the area's recipes need and none of them makes comes in on the belts (from the bus
    or by hand) instead of being made at the mall's top"""
    from bpgen import base
    recipes = s.data.raw["recipe"]
    made = {x.get("name") for p in params.get("products") or [] for x in (recipes.get(p) or {}).get("results") or []}
    need = {i["name"] for p in params.get("products") or [] for i in (recipes.get(p) or {}).get("ingredients") or []
            if i.get("type") != "fluid"}
    inputs = params.get("inputs")
    if inputs is None:  # (as the grid mall's default: the plates, and what's smelted from them)
        raw = base.raw_items(s.data) - base.MADE_HERE
        plates = base.plate_items(s.data, raw)
        inputs = raw | plates | base.plate_items(s.data, plates)
    params["inputs"] = sorted(set(inputs) | (need - made))


def run(s, params, progress=None, cancel=None, measure=True, keep=None, site=None, stage=None):
    """one plan request, whoever asks (Service.request): params (Service.params) -> the answer the window shows
    (blueprint, label, mode, absolute, mall, base, sections, expected, output, notes, summary, test) plus "result":
    Service.plan's own output (the web page's and the console's). keep: the asking player's state ("last": the plan
    place() puts next to the base, "sections": a book's prints by name), written as it goes. site: snapshot -> the
    player's site.Site (kept between requests), else one is read when the bus feed needs it. measure=False: an
    inserter setup not measured yet raises calibrate.NotMeasured (the game measures it) instead of running one.
    stage(text, n): a progress line that moves the window's bar to stage n (else progress(text))"""
    from bpgen import calibrate, pack, planner
    st = keep if keep is not None else {}
    st.setdefault("sections", {})
    stage = stage or (lambda text, n: progress and progress(text))
    if params.get("mode") == "base" and params.get("import"):
        # (a blueprint of the player's own base, held in game: rebuilt for the science it makes, as the web
        # app's import does)
        a = s.analyze_blueprint(params.pop("import"))
        params.update({"targets": a.get("science") or {}, "mall_products": a.get("mall") or [],
                       "mall": bool(a.get("mall")),
                       "machines": sorted({m["machine"] for m in a.get("machines") or []}),
                       "before": {"size": a.get("size"), "entities": a.get("entities")}})
    bus, bus_notes = None, []
    refit = params.get("refit") if params.get("mode") in ("mall", "base") else None
    if refit:
        params["origin"] = {"x": (refit["area"][0] + refit["area"][2]) / 2,
                            "y": (refit["area"][1] + refit["area"][3]) / 2}
    if refit and params["mode"] == "base":  # (an area rebuilt as a base on a main bus, for the science it
        refit["all"] = True  # made: everything in it goes but the main bus's own belts)
        params.update(layout="bus", fit_bus=None, add_to=None, plates=True)  # (plates off the bus)
        clear, had, refit_notes = _refit(s, params)
    elif refit:  # (an area rebuilt as a mall, on a belt loop: any recipes, and its inputs an item each, which
        # always tap - the grid's two-item input belts sit side by side, nowhere to merge their items)
        params.update(layout="sushi", bus_feed=True, feed="belt")
        if refit.get("layout") == "grid":  # (side by side: columns between mixed belts, the parts made at
            # the top, nothing wired - no chest limits, no loop to keep from jamming)
            params.update(layout="grid", stock=0)
        clear, had, refit_notes = _refit(s, params)
    # (the ground round the player, read once: the bus feed and the placing after the plan both use it; a
    # refit's has the area's entities left out, made by _refit)
    snap = params.get("snapshot") if not refit else None
    if snap and site:
        site = site(snap)
    elif snap and params.get("bus_feed"):
        from bpgen import site as sitelib
        site = sitelib.Site(s.snapshot_view(snap))
    else:
        site = None
    if params.get("bus_feed") and params.get("recipe"):
        bus, bus_notes = _bus_feed(s, params, site)
    elif params.get("bus_feed") and params.get("mode") == "mall":
        bus, bus_notes = _mall_bus_feed(s, params, site)
    if refit and params["mode"] == "mall":
        params["extra"] = bool(refit.get("add"))  # (off: no machine the area didn't have)
        if not refit.get("add"):
            _refit_inputs(s, params)
        bus_notes = refit_notes + [n for n in bus_notes if not n.startswith("no main bus")]
    token = calibrate.NO_MEASURE.set(True) if not measure else None
    try:
        tries = [m for m in (params.pop("machines_try", None) or []) if m] if params.get("mode") == "mall" else []
        for i, m in enumerate(tries[:4] or [params.get("machine")]):
            # (a mall: the window's candidate machines in turn, fastest first, until one lays out)
            if tries:
                params["machine"] = m
            try:
                stage(f"planning {params.get('mode') or 'a line'}"
                      + (f" with {params.get('machine')}" if params.get("machine") else ""), 1)
                out = result = s.plan(params, progress=progress, cancel=cancel)
                break
            except planner.PlanError:
                if i == len(tries[:4]) - 1 or not tries:
                    raise
        if refit and params.get("mode") == "base":
            # (the base's connected print, where the area was - beside the bus if it doesn't fit there -
            # its inputs taken off the bus)
            whole = next((x for x in out.get("sections") or [] if x["kind"] == "routed"), None)
            if not whole:
                raise planner.PlanError(out.get("route_note") or "the base didn't come out as one connected build")
            res = whole["result"]
            st["last"] = {"entities": res["entities"], "sources": res.get("sources") or [],
                          "sinks": res.get("sinks") or [], "belt": params.get("belt"), "mode": "base",
                          "label": "bpgen: the area refit as a base", "clear": clear}
            o = params["origin"]
            placed = s.extend(dict(st["last"], bus="here", seed=o, snapshot=params.get("snapshot")))
            if placed.get("inputs"):
                placed = s.extend(dict(st["last"], bus="auto", seed=o, snapshot=params.get("snapshot")))
            n = sum(1 for e in placed["entities"] if e.get("recipe"))
            out = {"blueprint": placed["blueprint"], "absolute": dict(absolute(placed), clear=clear),
                   "summary": {"mode": "base", "packs": out.get("packs"), "spm": out.get("spm"),
                               "bring_in": out.get("bring_in"),
                               "notes": refit_notes + [f"{n} machines (the area had {had})"]
                               + list(out.get("notes") or []) + placed["notes"]}}
        elif params.get("mode") == "extend":
            # (next to the base: plan_extension already placed the chain into the snapshot, taps on its
            # belts, power: an absolute blueprint that lands where it belongs)
            st["last"] = dict(out["plan"], belt=params.get("belt"), mode="extend",
                              label=f"bpgen: {params.get('item')} next to the base")
            out = {"blueprint": out["blueprint"], "summary": {"notes": out.get("notes") or []},
                   "absolute": absolute(out)}
        elif params.get("mode") == "ship":  # (absolute too: it lands on the platform's hub, at 0,0)
            st["last"] = None
            out = {"blueprint": out["blueprint"], "summary": {"notes": out.get("notes") or []},
                   "absolute": absolute(out)}
        elif params.get("mode") == "busdesign":  # (absolute, like extend: it lands on the patches)
            from bpgen import busdesign
            if not params.get("add_to_bus"):  # (more lanes on a bus: the bus design it fits stays)
                busdesign.save_last(out, params)
            st["last"] = None
            out = {"blueprint": out["blueprint"], "summary": {"notes": out.get("notes") or []},
                   "absolute": absolute(out)}
        elif out.get("entities"):  # (for place(): this plan, put next to the base on request)
            sm = out.get("summary") or {}
            st["sections"] = {x["name"]: x for x in out.get("sections") or []}
            st["last"] = {"entities": out["entities"], "sources": out.get("sources") or [],
                          "needs": line_needs(s.data, sm) if not st["sections"] else section_needs(
                              s.data, (out.get("sections") or [{}])[0], sm),
                          "sinks": [dict(k, item=k.get("item") or sm.get("output")) for k in out.get("sinks") or []],
                          "belt": sm.get("belt") or params.get("belt"), "mode": params.get("mode") or "line",
                          "label": f"bpgen: {sm.get('recipe') or params.get('recipe') or params.get('mode')} next to the base"}
            if bus or refit:  # (fed from the bus: placed beside it straight away, its inputs tapped from
                o = params.get("origin") or {}  # the lanes; a refit: where the area was)
                try:
                    placed = None
                    if refit:  # (in the area's own place when it fits there, its belts tapped nearby)
                        placed = s.extend(dict(st["last"], bus="here", seed=o, snapshot=params.get("snapshot")))
                        bx, by, bw, bh = placed["box"][:4]
                        ax1, ay1, ax2, ay2 = refit["area"]
                        if not (ax1 <= bx + bw / 2 <= ax2 and ay1 <= by + bh / 2 <= ay2) or placed.get("inputs"):
                            placed = None  # (it doesn't fit, or not every input could be tapped there:
                            # beside the bus, as a mall from it goes)
                    if placed is None:
                        placed = s.extend(dict(st["last"], bus="on" if bus else "off", seed=o if o else None,
                                               site=site, snapshot=params.get("snapshot")))
                    placed_at = absolute(placed)
                    if refit:
                        n = sum(1 for e in placed["entities"] if e.get("recipe"))
                        bus_notes.append(f"{n} machines (the area had {had})")
                        placed_at["clear"] = clear
                    out = {"blueprint": placed["blueprint"], "absolute": placed_at,
                           "summary": dict(sm, notes=bus_notes + list(sm.get("notes") or []) + placed["notes"])}
                except (ValueError, planner.PlanError) as e:  # (no room beside the bus: the plain line)
                    if refit:
                        raise planner.PlanError(f"no room for the refit mall: {e}")
                    bus, bus_notes = None, bus_notes + [f"not placed beside your bus ({e}): place it yourself"]
        if bus_notes and not bus:
            out.setdefault("summary", {})["notes"] = bus_notes + list(out["summary"].get("notes") or [])
    finally:
        if token is not None:
            calibrate.NO_MEASURE.reset(token)
    summary = out.get("summary") or {}
    notes = list(summary.get("notes") or [])
    if not pack.up_to_date():
        notes.append("your mods changed since bpgen last read them: it reads them at the next game start")
    return {
        "blueprint": out["blueprint"],
        "label": (f"{params.get('item')} next to the base" if params.get("mode") == "extend" else f"{summary.get('recipe') or params.get('recipe') or params.get('mode')} x{summary.get('machines', '?')}"),
        "mode": params.get("mode") or ("robots" if params.get("feed") == "robots" else "line"),
        "absolute": out.get("absolute"),
        "mall": {"products": summary.get("products"), "machines": summary.get("machines")} if summary.get("mode") == "mall" else None,
        "base": {k: summary.get(k) for k in ("packs", "spm", "bring_in", "not_automated", "route_note")}
        if summary.get("mode") == "base" else None,
        # (a starter base's book: its prints by name, for the window's picker)
        "sections": [x["name"] for x in out.get("sections") or []] or None,
        "expected": summary.get("expected"), "output": summary.get("output"), "notes": notes,
        "summary": {k: summary.get(k) for k in ("recipe", "machine", "machines", "per_row", "belt", "output",
                                                "expected", "productivity", "beacons_per_machine")}
        | {"stages": [{k: x.get(k) for k in ("item", "recipe")} for x in summary.get("stages") or []]},
        # (the window's test run: the harness case for this line, run live on its preview)
        "test": test_spec(out, params) if summary.get("expected") and out.get("sinks")
        else mall_test_spec(s.data, out, params) if summary.get("mode") == "mall" and out.get("sources") else None,
        "result": result,
    }
