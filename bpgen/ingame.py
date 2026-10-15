"""bpgen inside the game, through the fse "py" plugin (native/README.md): no web app, no files, no second
process. The zzz-bpgen mod sends the same request it writes to request.json:

    local id = native.start("py", "bpgen.ingame:plan", helpers.table_to_json(request))
    ... on_tick: local status, out = native.poll(id)   -- "done", '{"blueprint": "0eNr...", ...}'

The interpreter stays loaded between plans, so bpgen's data (the pack's prototypes, the inserter measurements)
loads once, on the first plan or on warm(). Nothing is measured in game (that needs a headless game): a plan that
needs an unmeasured inserter setup says so instead.
"""
import json
import math
import threading
import time
import traceback
from pathlib import Path

_service = None
_lock = threading.Lock()  # (plans share one Service: one at a time)
_taking = None  # the thread re-reading the mods' data with a headless game (planning waits for it)
# each player's own (a request's "player", 0 without one): "last": the last plan that isn't placed yet (entities,
# sources, sinks...), what place() puts next to the base; "sections": a starter base's prints by name (place() can
# put one of them next to the base); "where": where place() last put "last", {of: that plan, seed, offset} (the
# window's arrows move it from there); "site": the ground round the base it was placed in (site.Site, read once)
_players = {}


def _state(req):
    """the asking player's state (see _players)"""
    return _players.setdefault(int(req.get("player") or 0), {"last": None, "sections": {}, "where": None, "site": None})


def _site(s, st, snap, prune=None):
    """the player's site for this snapshot: the kept one when it is the same snapshot (sent again while the window's
    arrows, Rotate and Redo stay inside it), else read anew and kept. prune: [x1, y1, x2, y2] whose entities are
    left out (a section being moved: its old place is free ground)"""
    from bpgen import site
    variant = tuple(prune) if prune else None
    if st["site"] is None or st["site"].key != site.key_of(snap, variant) or snap.get("tick") is None:
        snap = s.snapshot_view(snap)
        if prune:
            x1, y1, x2, y2 = prune
            snap = dict(snap, entities=[e for e in snap["entities"] if not (
                x1 <= e["position"]["x"] < x2 and y1 <= e["position"]["y"] < y2)])
        st["site"] = site.Site(snap, variant)
    return st["site"]
# what the job running now has done, for the window's progress bar (read by progress(), without _lock: the job holds
# it while it works). Appending to a list is atomic in CPython.
STAGES = ("reading your request", "planning", "placing it next to your base", "finishing")
_prog = {"stage": 0, "lines": [], "seen": 0, "in_stage": 0, "run": 0}


def _post():
    """the progress for the game thread to read without the GIL (fse 0.17.2+: native.call("py", "_note", ...));
    a game thread calling progress() instead waits whenever this worker holds the GIL: the game stuttered"""
    try:
        import fse
        note = fse.note
    except (ImportError, AttributeError):
        return
    st = _prog["stage"]
    frac = min(0.98, (st + min(0.9, 0.12 * _prog["in_stage"])) / len(STAGES))
    note("bpgen.progress", json.dumps({"run": _prog["run"], "fraction": frac, "step": STAGES[st], "lines": _prog["lines"]}))


def _note(text, stage=None):
    """a line for the window's details; a stage (index into STAGES) moves the bar on"""
    if stage is not None and stage != _prog["stage"]:
        _prog["stage"], _prog["in_stage"] = stage, 0
    _prog["in_stage"] += 1
    _prog["lines"].append(str(text))
    _post()


def _begin():
    _prog.update(stage=0, lines=[], seen=0, in_stage=0, run=_prog["run"] + 1)
    _post()


def progress(_: str = "") -> str:
    """the running job's progress: {"fraction", "step", "lines": new ones since the last call}"""
    lines = _prog["lines"]
    new = lines[_prog["seen"]:]
    _prog["seen"] = len(lines)
    st = _prog["stage"]
    frac = min(0.98, (st + min(0.9, 0.12 * _prog["in_stage"])) / len(STAGES))
    return json.dumps({"fraction": frac, "step": STAGES[st], "lines": new})


def _crash(e, request):
    """an unexpected error: the answer the window shows, and the request with its trace in run/ingame-error.json
    (the game shows only the error line), so it can be planned again outside the game"""
    trace = traceback.format_exc()
    try:
        out = Path(__file__).resolve().parents[1] / "run" / "ingame-error.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps({"request": request, "trace": trace}))
    except OSError:
        pass
    return json.dumps({"error": f"{type(e).__name__}: {e}", "trace": trace[-2000:]})


def _post_icons():
    """the icon table (pack.icon_specs) for the web window, as fse's note "bpgen.icons": web.lua reads it without
    waiting for Python. A "kind<tab>name<tab>spec" line each: the game splits that in ~30 ms, JSON took ~400 ms"""
    from bpgen import pack
    try:
        import fse
        icons = json.loads(pack.ICONS.read_text(encoding="utf-8"))
        fse.note("bpgen.icons", "".join(f"{kind}\t{n}\t{s}\n" for kind, t in icons.items() for n, s in t.items()))
        fse.note("bpgen.icons.at", str(pack.ICONS.stat().st_mtime_ns))  # (a new table: web.lua reads it again)
    except (ImportError, AttributeError, OSError):
        pass


def _reread():
    """bpgen's copy of the mods' data, made again by a headless game (factorio --dump-data on a mirror of the user's
    mods, about 2 minutes), on a thread: the game goes on. (Not taken from the game's own data stage: a mod named
    zzz- doesn't load last, Factorio orders mods by dependency depth first, so it missed later mods' final fixes.)"""
    global _taking, _service
    from bpgen import pack

    def work():
        global _service
        t = time.perf_counter()
        try:
            pack.sync()
            _post_icons()
            print(f"bpgen: mods' data re-read in {time.perf_counter() - t:.0f} s")
        except Exception:  # noqa: BLE001 - logged; bpgen keeps its old copy
            traceback.print_exc()
        _service = None  # (the next plan loads the new copy)

    _service = None
    _taking = threading.Thread(target=work, name="bpgen-reread")
    _taking.start()


def stale(active: str = "") -> str:
    """at game load (`active`: the game's script.active_mods as JSON): bpgen's copy of the mods' data is stale and
    the game runs the user's own mods -> it is read again, headless, while the game goes on"""
    from bpgen import pack
    if _taking is not None and _taking.is_alive():
        return json.dumps({"stale": True, "reading": True})
    try:
        if pack.up_to_date() or not pack.users_game(json.loads(active or "{}")):
            return json.dumps({"stale": False})
    except (OSError, ValueError):
        traceback.print_exc()
        return json.dumps({"stale": False})
    _reread()
    return json.dumps({"stale": True, "reading": True})


def _svc():
    global _service
    if _taking is not None and _taking.is_alive():
        _note("reading your mods' changes (about 2 minutes, once after they change)")
        _taking.join()
    if _service is None:
        from bpgen.service import Service
        s = Service("pack")
        s.data  # (loads the pack's prototypes now, not inside the first plan)
        _service = s
    return _service


def warm(game: str = "") -> str:
    """load bpgen's data ahead of the first plan (the mod calls this when the game loads, with what of the game's
    settings the planner follows: {"propagation": Power Propagation's startup settings, or absent})"""
    t = time.perf_counter()
    try:
        from bpgen import propagation
        propagation.configure((json.loads(game or "{}") or {}).get("propagation"))
        if propagation.RANGE is not None:
            print(f"bpgen: Power Propagation on (range {propagation.RANGE}): blueprints leave out the poles it covers")
    except ValueError:
        pass
    with _lock:
        _svc()
    _post_icons()
    from bpgen import router
    print(f"bpgen: belt router in {'Rust (bpgen_fast)' if router._fast else 'Python (bpgen_fast not built)'}")
    return json.dumps({"ok": True, "seconds": round(time.perf_counter() - t, 3)})


def _test_spec(out, params):
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


def _mall_test_spec(data, out, params):
    """a mall's test run: its input belts fed, nothing drained (its products stay in their chests); the window
    counts what has reached them"""
    from bpgen import calibrate, mall
    s = out.get("summary") or {}
    return {"warmup": 0, "measure": 20 * 3600, "output": None, "expected": None,
            "force": dict(params.get("bonuses") or calibrate.ZERO, belt_stack_size_bonus=0), "recipe_productivity": {},
            "products": [mall._only_result(data, r) or r for r in s.get("products") or []],
            "case": {"id": "bp", "sources": out.get("sources") or [], "sinks": []}}

# what the window may ask the service for (bpgen.ingame:api {"fn": ..., "params": {...}})
API = {"recipe_tree", "options", "siblings", "mall_candidates", "save_check", "module_ideas", "history", "save_info",
       "alternatives"}


def api(request: str) -> str:
    """the web app's helpers for the in-game window: {"fn": name, "params": {...}} -> the method's answer as JSON"""
    req = json.loads(request)
    fn = req.get("fn")
    if fn == "shortages":  # (what the save uses faster than it makes, that bpgen can plan: the window's "what's short")
        from bpgen.service import SAVE_STATE
        try:
            with _lock:
                st = json.loads(SAVE_STATE.read_text(encoding="utf-8"))
                rows = [r for r in _svc().production(st) if r["short"] > 0 and r["makeable"] and not r["fluid"]]
            return json.dumps({"result": rows[:10]})
        except (OSError, ValueError) as e:
            return json.dumps({"error": f"no production numbers yet ({e})"})
    if fn not in API:
        return json.dumps({"error": f"no {fn}"})
    try:
        with _lock:
            m = getattr(_svc(), fn)
            out = m(req["params"]) if "params" in req else m()
        return json.dumps({"result": out})
    except Exception as e:  # noqa: BLE001 - shown in the window
        traceback.print_exc()
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def measured(results: str) -> str:
    """the game measured inserter setups (a NotMeasured spec run on its preview surface): into the table"""
    from bpgen import calibrate
    cases = json.loads(results).get("cases") or []
    with _lock:
        calibrate.store(_svc().calib_path, cases)
    print(f"bpgen: {len(cases)} inserter setups measured in game")
    return json.dumps({"stored": len(cases)})


def _line_needs(data, sm):
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


def _section_needs(data, sec, base_summary):
    """{item: items/s} a starter base's print takes from outside: a line's through its recipe, the whole base's what
    it brings in; else None"""
    if sec.get("kind") == "line":
        return _line_needs(data, (sec.get("result") or {}).get("summary") or {})
    if sec.get("kind") == "routed":
        return {k: v / 60 for k, v in (base_summary.get("bring_in") or {}).items() if isinstance(v, (int, float))} or None
    return None


def _absolute(placed):
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


BELTISH = ("transport-belt", "underground-belt", "splitter")
DIRS = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}


def _section(s, params):
    """a section of the base to move: params["move"] = {"area": [x1, y1, x2, y2], "blueprint": the area as the game
    blueprints it, "offset": [dx, dy] (a blueprint position plus it is the world's)}, params["snapshot"] (the area and
    round it). -> (plan entities in world coordinates, sources: belts coming in over the area's edge, sinks: belts
    going out, what to deconstruct, notes)"""
    from bpgen import base
    mv = params["move"]
    x1, y1, x2, y2 = mv["area"]
    ox, oy = mv["offset"]
    bp = base._decode(mv["blueprint"])["blueprint"]
    keep = {"name", "position", "direction", "entity_number"}
    ents, by_number = [], {}
    for be in bp.get("entities") or []:
        e = {"name": be["name"], "position": {"x": be["position"]["x"] + ox, "y": be["position"]["y"] + oy},
             "bp_extra": {k: v for k, v in be.items() if k not in keep}, "wire_id": f"m{be['entity_number']}"}
        if be.get("direction"):
            e["direction"] = be["direction"]
        if be.get("recipe"):
            e["recipe"] = be["recipe"]
        ents.append(e)
        by_number[be["entity_number"]] = e
    for a, ca, b, cb in bp.get("wires") or []:  # (each wire once, from its first end)
        if a in by_number and b in by_number:
            by_number[a].setdefault("wires_to", []).append((f"m{b}", ca, cb))
    # what comes in and goes out: belts on the area's edge whose neighbour outside runs into / takes from them
    snap = s.snapshot_view(params.get("snapshot"))
    inside = lambda x, y: x1 <= x < x2 and y1 <= y < y2  # noqa: E731
    belts = {(math.floor(e["position"]["x"]), math.floor(e["position"]["y"])): e for e in snap["entities"]
             if e["type"] in BELTISH and not e.get("ghost")}
    sources, sinks, notes = [], [], []
    for (x, y), e in belts.items():
        if not inside(x, y) or e["type"] == "splitter":
            continue
        dx, dy = DIRS.get(e.get("direction", 0), (0, -1))
        up, down = belts.get((x - dx, y - dy)), belts.get((x + dx, y + dy))
        items = [i for i in e.get("lanes") or [] if i]
        if up and not inside(x - dx, y - dy) and up.get("direction") == e.get("direction") and items:
            sources.append({"kind": "belt", "position": {"x": x + 0.5, "y": y + 0.5},
                            "lanes": [items[0], items[-1]], "rates": {i: 1 for i in items}})
        if down and not inside(x + dx, y + dy) and items:
            sinks.append({"kind": "belt", "position": {"x": x + 0.5, "y": y + 0.5}, "item": items[0]})
    if any(e["type"] in ("pipe", "pipe-to-ground") and not inside(math.floor(e["position"]["x"]),
                                                                  math.floor(e["position"]["y"]))
           and any(abs(e["position"]["x"] - p["position"]["x"]) + abs(e["position"]["y"] - p["position"]["y"]) <= 1
                   for p in ents if p["name"] in ("pipe", "pipe-to-ground")) for e in snap["entities"]):
        notes.append("pipes run over the section's edge: connect them again where it lands")
    clear = [{"name": e["name"], "position": e["position"]} for e in snap["entities"]
             if inside(math.floor(e["position"]["x"]), math.floor(e["position"]["y"])) and not e.get("ghost")
             and e["type"] not in ("resource",)]
    params["snapshot"] = dict(snap, entities=[e for e in snap["entities"] if not inside(
        math.floor(e["position"]["x"]), math.floor(e["position"]["y"]))])
    notes.insert(0, f"moving {len(ents)} entities: {len(sources)} belts in, {len(sinks)} out, reconnected where "
                    f"it lands")
    return ents, sources, sinks, clear, notes


def move(request: str) -> str:
    """a section of the base moved: {"params": {"move": ..., "snapshot": ..., "seed": {x, y}}} (see _section) ->
    as plan(): an absolute blueprint (its belts in and out routed to the base), "absolute.clear": the old one"""
    from bpgen import planner
    t = time.perf_counter()
    try:
        req = json.loads(request)
        st = _state(req)
        params = req.get("params") or {}
        _begin()
        _note("reading the section", 0)
        with _lock:
            s = _svc()
            s.progress = lambda text: _note(text, 2)
            ents, sources, sinks, clear, notes = _section(s, params)
            _note(f"{len(ents)} entities, {len(sources)} belts in, {len(sinks)} out", 1)
            x1, y1 = params["move"]["area"][:2]
            plan = [dict(e, position={"x": e["position"]["x"] - x1, "y": e["position"]["y"] - y1}) for e in ents]
            shift = lambda p: {"x": p["x"] - x1, "y": p["y"] - y1}  # noqa: E731
            st["last"] = {"entities": [s.decorate(e) for e in plan],
                     "sources": [dict(k, position=shift(k["position"])) for k in sources],
                     "sinks": [dict(k, position=shift(k["position"])) for k in sinks],
                     "belt": "transport-belt", "mode": "move", "label": "bpgen: a section moved",
                     "prune": params["move"]["area"], "clear": clear}
            placed = s.extend(dict(st["last"], bus="here", seed=params.get("seed"), snapshot=params["snapshot"]))
            absolute = dict(_absolute(placed), clear=clear)
        return json.dumps({"blueprint": placed["blueprint"], "label": "a section moved", "mode": "move",
                           "absolute": absolute, "notes": notes + list(placed.get("notes") or []), "summary": {},
                           "seconds": round(time.perf_counter() - t, 3)})
    except planner.PlanError as e:
        return json.dumps({"error": str(e), "seconds": round(time.perf_counter() - t, 3)})
    except Exception as e:  # noqa: BLE001 - shown in game
        return _crash(e, request)


def _pick_section(st, name):
    """one print of the last plan's book (a starter base's, More lines') becomes the plan to place"""
    sec = st["sections"].get(name or "")
    if sec and sec.get("last"):  # (one of More lines' lines: that one next to the base)
        st["last"] = sec["last"]
    elif sec and st["last"]:  # (a print of the starter base: that one next to the base, and the arrows move it)
        res = sec.get("result") or {}
        st["last"] = {"entities": res.get("entities") or [], "sources": res.get("sources") or [],
                      "sinks": [dict(k, item=k.get("item") or sec.get("item")) for k in res.get("sinks") or []],
                      "belt": (res.get("summary") or {}).get("belt") or st["last"].get("belt"), "mode": "base",
                      "label": f"bpgen: {sec['name']} next to the base", "needs": _section_needs(_svc().data, sec, {})}


def place(request: str) -> str:
    """the last plan next to the player's base: {"bus": "auto"|"on"|"off", "seed": {x, y}, "snapshot": the area
    around the player, as the snapshot tool takes it} -> as plan(), an absolute blueprint with taps on the base's
    belts, or {error}"""
    t = time.perf_counter()
    from bpgen import extend
    try:
        req = json.loads(request or "{}")
        st = _state(req)
        _pick_section(st, req.get("section"))
        if not st["last"] or not st["last"].get("entities"):
            return json.dumps({"error": "plan something first"})
        snap = req.get("snapshot")
        seed, at = req.get("seed"), None
        if req.get("shift") and st["where"] and st["where"]["of"] is st["last"]:  # (the arrows: that far from the last spot)
            seed = st["where"]["seed"]
            at = (st["where"]["offset"][0] + req["shift"][0], st["where"]["offset"][1] + req["shift"][1])
        _begin()
        _note("placing the last plan where you asked", 2)
        with _lock:
            _svc().progress = lambda text: _note(text, 2)
            site = _site(_svc(), st, snap, st["last"].get("prune")) if snap else None
            placed = _svc().extend(dict(st["last"], bus="here" if st["last"].get("mode") == "move" else req.get("bus") or "auto",
                                        seed=seed, site=site, snapshot=snap, turn=req.get("turn") or 0,
                                        avoid=req.get("avoid"), spot=bool(req.get("spot")), at=at))
        st["where"] = {"of": st["last"], "seed": seed, "offset": placed["offset"]}
        absolute = _absolute(placed)
        if st["last"].get("clear"):
            absolute["clear"] = st["last"]["clear"]
        return json.dumps({
            "blueprint": placed["blueprint"], "label": st["last"]["label"], "mode": st["last"]["mode"],
            "absolute": absolute,
            "notes": placed.get("notes") or [], "summary": {}, "seconds": round(time.perf_counter() - t, 3)})
    except extend.Blocked as e:  # (no room there: said in the window, not a crash)
        return json.dumps({"error": str(e), "seconds": round(time.perf_counter() - t, 3)})
    except Exception as e:  # noqa: BLE001 - shown in game
        return _crash(e, request)


def hand(request: str) -> str:
    """the last plan as a blueprint for the player's cursor, to put down where they like (the game's own moving,
    R and flips: no planning while they do): {"player", "snapshot" (round them: the plan turned to face a main bus
    there, and the spot bpgen would pick), "seed", "bus", "turn", "section"} -> {"blueprint", "id" (its entities
    carry the tag bpgen_place = {id, i}: connect() finds them by it), "spot": [x, y, w, h] or None, "notes"}"""
    from bpgen import extend, planner
    t = time.perf_counter()
    try:
        req = json.loads(request or "{}")
        st = _state(req)
        if req.get("section"):
            _pick_section(st, req["section"])
        last = st["last"]
        if not last or not last.get("entities"):
            return json.dumps({"error": "plan something first"})
        seed = req.get("seed")
        with _lock:
            s = _svc()
            site = _site(s, st, req["snapshot"], last.get("prune")) if req.get("snapshot") else None
            spot = posed = None
            if site:
                ground, main = site.scratch()
                try:
                    posed = extend.pose(ground, last["entities"], last.get("sources") or [], last.get("sinks") or [],
                                        seed=(seed["x"], seed["y"]) if seed else None,
                                        bus="here" if last.get("mode") == "move" else req.get("bus") or "auto",
                                        turn=req.get("turn") or 0, main=main)
                    tiles = extend.plan_tiles(posed["entities"])
                    x0, y0 = min(a for a, _ in tiles), min(b for _, b in tiles)
                    spot = [x0, y0, max(a for a, _ in tiles) - x0 + 1, max(b for _, b in tiles) - y0 + 1]
                except extend.Blocked:
                    posed = None
            if posed is None:  # (no snapshot, or no room in it: as planned, the player finds it a place)
                ents, srcs, snks = extend.turn_plan(last["entities"], last.get("sources") or [],
                                                    last.get("sinks") or [], req.get("turn") or 0)
                posed = {"offset": [0, 0], "turn": (req.get("turn") or 0) % 4, "side": 0, "main": None,
                         "entities": ents, "sources": srcs, "sinks": snks or [], "notes": []}
            st["hand_n"] = n = st.get("hand_n", 0) + 1
            # (the last few handed out: a paste picked up again (Move) is put down with the blueprint it came from)
            hands = st.setdefault("hands", {})
            hands[n] = dict(posed, main=None, plan=last)
            for old in [k for k in hands if k <= n - 5]:
                del hands[old]
            ents = [{k: v for k, v in s.decorate(e).items() if k not in ("type", "w", "h", "fluid", "new")}
                    | {"tags": dict(e.get("tags") or {}, bpgen_place={"id": n, "i": i})}
                    for i, e in enumerate(posed["entities"])]
            bp = planner.blueprint_string(ents, last.get("label") or "bpgen",
                                          description="Put it down where you like; bpgen ties it into your base.")
        return json.dumps({"blueprint": bp, "id": n, "spot": spot, "notes": posed["notes"],
                           "seconds": round(time.perf_counter() - t, 3)})
    except Exception as e:  # noqa: BLE001 - shown in game
        return _crash(e, request)


def connect(request: str) -> str:
    """the plan from hand() put down by the player, tied into their base: {"player", "id", "built": [{i, x, y,
    direction}] (its ghosts, as the game built them), "snapshot" (round it, measured after) or "delta" ({since,
    now, surface, built, removed}: what changed since the snapshot this player's site was read from: hand()'s, or
    the last connect's)} -> {"blueprint" (only what connects it: taps, lanes, poles; absolute, so it lands where it
    belongs) or None, "absolute", "notes"}, or {"rescan": true} (a delta that doesn't fit the site kept here: the
    game sends a snapshot instead)"""
    from bpgen import extend
    t = time.perf_counter()
    try:
        req = json.loads(request or "{}")
        st = _state(req)
        held = (st.get("hands") or {}).get(req.get("id"))
        if not held:
            return json.dumps({"error": "that blueprint is from an older plan: take it from the window again"})
        built = {b["i"]: b for b in req.get("built") or [] if 0 <= b.get("i", -1) < len(held["entities"])}
        how = extend.solve_pose([((held["entities"][i]["position"]["x"], held["entities"][i]["position"]["y"]),
                                  (b["x"], b["y"])) for i, b in built.items()])
        if not how:
            return json.dumps({"error": "bpgen couldn't tell how the blueprint was put down (was it edited?)"})
        posed = extend.repose(held, how, {i: b.get("direction") for i, b in built.items()})
        last = held["plan"]
        _begin()
        _note("tying it into your base", 2)
        with _lock:
            s = _svc()
            s.progress = lambda text: _note(text, 2)
            delta = req.get("delta")
            if delta:
                site = st["site"]
                if site is None or site.snap.get("tick") != delta.get("since")                         or site.snap.get("surface") != delta.get("surface"):
                    return json.dumps({"rescan": True})
                site = st["site"] = site.with_delta(delta.get("built") or [], delta.get("removed") or [],
                                                    delta.get("now"), s.decorate)
            else:
                site = _site(s, st, req["snapshot"])
            placed = s.connect({"site": site, "posed": posed, "belt": last.get("belt"), "needs": last.get("needs"),
                                "label": (last.get("label") or "bpgen") + ": connected"})
        absolute = _absolute(placed) if placed["box"] else None
        if absolute and last.get("clear"):
            absolute["clear"] = last["clear"]
        return json.dumps({"blueprint": placed["blueprint"], "absolute": absolute, "notes": placed["notes"],
                           "mirror": how[0], "turns": how[1], "seconds": round(time.perf_counter() - t, 3)})
    except Exception as e:  # noqa: BLE001 - shown in game
        return _crash(e, request)


def bus_report(request: str) -> str:
    """the player's main bus, looked at: {"snapshot": measured area around the player, "belts": unlocked belts} ->
    {"result": {"bus": what it is, "items": [{item, lanes, cap, used, dry, belt, made, short}], "short": [{item,
    short, recipe}]: what the base lacks and the bus doesn't carry}} (the window's "Check my bus")"""
    from bpgen import extend
    from bpgen.service import SAVE_STATE
    try:
        req = json.loads(request or "{}")
        with _lock:
            s = _svc()
            main = _site(s, _state(req), req["snapshot"]).bus if req.get("snapshot") else None
            if not main:
                return json.dumps({"result": {"bus": None}})
            try:
                st = json.loads(SAVE_STATE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                st = {}
            prod = {r["name"]: r for r in s.production(st)} if st else {}
            mined = set()  # (ores, coal, stone: mined, never made by the bus)
            for res in s.data.raw.get("resource", {}).values():
                m = res.get("minable") or {}
                mined |= {m["result"]} if m.get("result") else {x.get("name") for x in m.get("results") or []}
            belts = sorted(req.get("belts") or [], key=lambda b: s.data.raw["transport-belt"].get(b, {}).get("speed", 0))
            items = []
            for k, o in sorted(extend.bus_items(s.data, main).items()):
                p = prod.get(k) or {}
                faster = [b for b in belts if s.data.raw["transport-belt"][b]["speed"] >
                          s.data.raw["transport-belt"].get(o["belt"], {}).get("speed", 0)]
                items.append(dict(o, item=k, short=p.get("short"), makeable=p.get("makeable") and k not in mined,
                                  recipe=p.get("recipe"), upgrade=faster[-1] if faster else None))
            on_bus = {i for ln in main["lanes"] for i in ln["items"]}
            short = [{"item": r["name"], "short": r["short"], "recipe": r["recipe"]} for r in prod.values()
                     if r["short"] > 0 and r["makeable"] and not r["fluid"] and r["name"] not in on_bus | mined]
            short.sort(key=lambda r: -r["short"])
            pipes = [ln.get("pipe") for ln in main["lanes"] if ln.get("pipe")]
        return json.dumps({"result": {
            "bus": {"lanes": len(main["lanes"]) - len(pipes), "pipes": pipes, "dir": main["dir"],
                    "length": main["hi"] - main["lo"] + 1, "measured": any("used" in ln for ln in main["lanes"])},
            "items": items, "short": short[:6]}})
    except Exception as e:  # noqa: BLE001 - shown in game
        return _crash(e, request)


def add_lane(request: str) -> str:
    """a new lane of an item along the player's bus: {"snapshot", "item"} -> as plan(), an absolute blueprint (the
    window previews it, Place it puts the ghosts down), its head (where the item is to be fed in) in absolute"""
    from bpgen import extend
    t = time.perf_counter()
    try:
        req = json.loads(request or "{}")
        item = req.get("item")
        with _lock:
            s = _svc()
            ground, main = _site(s, _state(req), req["snapshot"]).scratch() if req.get("snapshot") else (None, None)
            if not main:
                return json.dumps({"error": "no main bus around you (3+ long straight belts side by side)"})
            got = extend.new_bus_lane(s.data, ground, main, item)
            if not got:
                return json.dumps({"error": "no room beside the bus for another lane"})
            ents, head, length = got
            bp, box = extend.absolute_blueprint([dict(s.decorate(e), new=True) for e in ents],
                                                f"bpgen: a new {item} lane", description="Feed it at its head.")
        return json.dumps({
            "blueprint": bp, "label": f"a new {item} lane", "mode": "lane",
            "absolute": {"box": box, "taps": 0, "deliveries": 0, "new_lanes": 1, "axis": main["axis"],
                         "upgrades": [], "head": {"x": head[0] + 0.5, "y": head[1] + 0.5}, "item": item},
            "notes": [f"a new {item} lane along the bus, {length} tiles: feed {item} in at its head (marked when "
                      f"placed)"],
            "summary": {}, "seconds": round(time.perf_counter() - t, 3)})
    except Exception as e:  # noqa: BLE001 - shown in game
        return _crash(e, request)


def _lines(req, t):
    """Line's More lines: the window's line and each of req["lines"] ({recipe, machine, modules...}) planned as its own
    line, as if picked alone (no Make here / Also make: those are the first line's) -> as plan(), a blueprint book of
    them, a print each ("sections": their labels, for the window's picker; place() puts the one picked by the base)"""
    from bpgen import base
    st = _state(req)
    first = dict(req)
    extra = first.pop("lines")
    plain = {k: v for k, v in (first.get("params") or {}).items() if k not in ("make", "also")}
    done, notes = [], []  # (label, its answer, its st["last"])
    for i, sub in enumerate([first] + [dict(first, params=plain, **ln) for ln in extra]):
        out = json.loads(plan(json.dumps(sub)))
        if out.get("measure") or (i == 0 and not out.get("blueprint")):
            return json.dumps(dict(out, seconds=round(time.perf_counter() - t, 3)))  # (measured, then all again)
        if not out.get("blueprint"):
            notes.append(f"{sub.get('recipe')}: not planned ({out.get('error')})")
            continue
        label, n = out["label"], 2
        while any(d[0] == label for d in done):  # (a recipe twice: told apart)
            label, n = f"{out['label']} ({n})", n + 1
        done.append((label, out, st["last"]))
    prints = []
    for i, (label, out, _) in enumerate(done):
        bp = base._decode(out["blueprint"])
        bp["blueprint"]["label"] = label
        prints.append({"index": i, **bp})
    book = {"blueprint_book": {"item": "blueprint-book", "label": f"bpgen: {len(done)} lines",
                               "description": "\n".join(d[0] for d in done), "blueprints": prints, "active_index": 0,
                               "version": prints[0]["blueprint"]["version"]}}
    st["sections"] = {label: {"name": label, "last": last} for label, _, last in done}
    st["last"] = done[0][2]
    head = done[0][1]
    for label, out, _ in done:
        notes += [f"{label}: {x}" for x in out.get("notes") or []]
    return json.dumps(dict(head, blueprint=base._encode(book), label=f"{len(done)} lines: " + ", ".join(
        d[0] for d in done), sections=[d[0] for d in done], notes=notes, test=None, absolute=None,
        seconds=round(time.perf_counter() - t, 3)))


def plan(request: str) -> str:
    """a request from the mod (recipe, machine, unlocked belts and inserters, bonuses...) -> {blueprint, label, text}
    or {error}"""
    from bpgen import calibrate, pack, planner
    t = time.perf_counter()
    _begin()
    _note("request received", 0)
    try:
        req = json.loads(request)
        st = _state(req)
        if req.get("lines"):  # (Line's More lines: each its own line, one book)
            return _lines(req, t)
        st["sections"] = {}
        with _lock:
            s = _svc()
            s.progress = lambda text: _note(text, 2)  # (placing next to the base: each input routed)
            _note("bpgen's data loaded")
            if req.get("recipe") and req.get("machine"):
                params = s.params_from_request(req)
            else:  # (a mall or a starter base: no one recipe)
                belts = s.base_belts(req.get("belts") or [])
                params = {"belts": belts, "inserters": req.get("inserters") or [],
                          "bonuses": {k: (req.get("bonuses") or {}).get(k, 0) for k in calibrate.ZERO},
                          # (the fastest of the save's belts, as params_from_request picks for a line)
                          "belt": max(belts, key=lambda b: s.data.raw["transport-belt"][b]["speed"]) if belts
                          else "transport-belt"}
            params.setdefault("surface", req.get("surface"))  # (which save and surface: the bus design kept for it)
            if req.get("seed") is not None:
                params.setdefault("seed", req["seed"])
            for k in ("rate_per_min", "per_row", "belt"):  # (optional, from the in-game bpgen window)
                if req.get(k):
                    params[k] = req[k]
            # (the window's other options, as the web app sends them: near/far/out, beacon, make, feed, mode...)
            params.update({k: v for k, v in (req.get("params") or {}).items() if v not in (None, "", {}, [])})
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
            site = _site(s, st, params["snapshot"]) if params.get("snapshot") and not refit else None
            if params.get("bus_feed") and params.get("recipe"):
                bus, bus_notes = _bus_feed(s, params, site)
            elif params.get("bus_feed") and params.get("mode") == "mall":
                bus, bus_notes = _mall_bus_feed(s, params, site)
            if refit and params["mode"] == "mall":
                params["extra"] = bool(refit.get("add"))  # (off: no machine the area didn't have)
                if not refit.get("add"):
                    _refit_inputs(s, params)
                bus_notes = refit_notes + [n for n in bus_notes if not n.startswith("no main bus")]
            token = calibrate.NO_MEASURE.set(True)
            try:
                tries = [m for m in (params.pop("machines_try", None) or []) if m] if params.get("mode") == "mall" else []
                for i, m in enumerate(tries[:4] or [params.get("machine")]):
                    # (a mall: the window's candidate machines in turn, fastest first, until one lays out)
                    if tries:
                        params["machine"] = m
                    try:
                        _note(f"planning {params.get('mode') or 'a line'}"
                              + (f" with {params.get('machine')}" if params.get("machine") else ""), 1)
                        out = s.plan(params, progress=_note)
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
                    out = {"blueprint": placed["blueprint"], "absolute": dict(_absolute(placed), clear=clear),
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
                           "absolute": _absolute(out)}
                elif params.get("mode") == "ship":  # (absolute too: it lands on the platform's hub, at 0,0)
                    st["last"] = None
                    out = {"blueprint": out["blueprint"], "summary": {"notes": out.get("notes") or []},
                           "absolute": _absolute(out)}
                elif params.get("mode") == "busdesign":  # (absolute, like extend: it lands on the patches)
                    from bpgen import busdesign
                    if not params.get("add_to_bus"):  # (more lanes on a bus: the bus design it fits stays)
                        busdesign.save_last(out, params)
                    st["last"] = None
                    out = {"blueprint": out["blueprint"], "summary": {"notes": out.get("notes") or []},
                           "absolute": _absolute(out)}
                elif out.get("entities"):  # (for place(): this plan, put next to the base on request)
                    sm = out.get("summary") or {}
                    st["sections"] = {x["name"]: x for x in out.get("sections") or []}
                    st["last"] = {"entities": out["entities"], "sources": out.get("sources") or [],
                             "needs": _line_needs(s.data, sm) if not st["sections"] else _section_needs(
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
                            absolute = _absolute(placed)
                            if refit:
                                n = sum(1 for e in placed["entities"] if e.get("recipe"))
                                bus_notes.append(f"{n} machines (the area had {had})")
                                absolute["clear"] = clear
                            out = {"blueprint": placed["blueprint"], "absolute": absolute,
                                   "summary": dict(sm, notes=bus_notes + list(sm.get("notes") or []) + placed["notes"])}
                        except (ValueError, planner.PlanError) as e:  # (no room beside the bus: the plain line)
                            if refit:
                                raise planner.PlanError(f"no room for the refit mall: {e}")
                            bus, bus_notes = None, bus_notes + [f"not placed beside your bus ({e}): place it yourself"]
                if bus_notes and not bus:
                    out.setdefault("summary", {})["notes"] = bus_notes + list(out["summary"].get("notes") or [])
            finally:
                calibrate.NO_MEASURE.reset(token)
        summary = out.get("summary") or {}
        notes = list(summary.get("notes") or [])
        if not pack.up_to_date():
            notes.append("your mods changed since bpgen last read them: it reads them at the next game start")
        return json.dumps({
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
            | {"stages": [{k: st.get(k) for k in ("item", "recipe")} for st in summary.get("stages") or []]},
            "seconds": round(time.perf_counter() - t, 3),
            # (the window's test run: the harness case for this line, run live on its preview)
            "test": _test_spec(out, params) if summary.get("expected") and out.get("sinks")
            else _mall_test_spec(s.data, out, params) if summary.get("mode") == "mall" and out.get("sources") else None,
        })
    except calibrate.NotMeasured as e:
        # (the game measures them itself on the preview surface, then plans again: measured() below)
        return json.dumps({"error": str(e), "measure": e.spec, "seconds": round(time.perf_counter() - t, 3)})
    except planner.PlanError as e:
        return json.dumps({"error": str(e), "seconds": round(time.perf_counter() - t, 3)})
    except Exception as e:  # noqa: BLE001 - shown in game, with the trace in the fse log via the error text
        return _crash(e, request)
