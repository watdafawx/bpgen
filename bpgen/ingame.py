"""bpgen inside the game, through the fse "py" plugin (native/README.md): no web app, no files, no second
process. The zzz-bpgen mod sends the same request it writes to request.json:

    local id = native.start("py", "bpgen.ingame:plan", helpers.table_to_json(request))
    ... on_tick: local status, out = native.poll(id)   -- "done", '{"blueprint": "0eNr...", ...}'

The interpreter stays loaded between plans, so bpgen's data (the pack's prototypes, the inserter measurements)
loads once, on the first plan or on warm(). Nothing is measured in game (that needs a headless game): a plan that
needs an unmeasured inserter setup says so instead.

This module is the transport: each function parses what the mod sends, calls the one Session (session.py: the
Service, each player's plans, the progress) and answers in JSON. A plan itself is Service.request (request.py), as
for the web app and the console.
"""
import json
import math
import sys
import time
import traceback
import types

from bpgen import geom
from bpgen.request import absolute as _absolute, line_needs as _line_needs, section_needs as _section_needs  # noqa: F401
from bpgen.session import Session, crash, post_icons

SESSION = Session()


class _Module(types.ModuleType):
    """ingame._service and ingame._players (the tests set and read them): the Session's"""
    _service = property(lambda m: SESSION.service, lambda m, v: setattr(SESSION, "service", v))
    _players = property(lambda m: SESSION.players)


sys.modules[__name__].__class__ = _Module


def _crash(e, request):
    return json.dumps(crash(e, request))


def progress(_: str = "") -> str:
    """the running job's progress: {"fraction", "step", "lines": new ones since the last call}"""
    return SESSION.progress.read()


def plan(request: str) -> str:
    """a request from the mod (recipe, machine, unlocked belts and inserters, bonuses...) -> {blueprint, label, text}
    or {error} (Session.plan: Service.params, then Service.request)"""
    return json.dumps(SESSION.plan(request))


def stale(active: str = "") -> str:
    """at game load (`active`: the game's script.active_mods as JSON): bpgen's copy of the mods' data is stale and
    the game runs the user's own mods -> it is read again, headless, while the game goes on"""
    from bpgen import pack
    if SESSION.reading():
        return json.dumps({"stale": True, "reading": True})
    try:
        if pack.up_to_date() or not pack.users_game(json.loads(active or "{}")):
            return json.dumps({"stale": False})
    except (OSError, ValueError):
        traceback.print_exc()
        return json.dumps({"stale": False})
    SESSION.reread()
    return json.dumps({"stale": True, "reading": True})


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
    with SESSION.lock:
        SESSION.svc()
    post_icons()
    from bpgen import router
    print(f"bpgen: belt router in {'Rust (bpgen_fast)' if router._fast else 'Python (bpgen_fast not built)'}")
    return json.dumps({"ok": True, "seconds": round(time.perf_counter() - t, 3)})


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
            with SESSION.lock:
                st = json.loads(SAVE_STATE.read_text(encoding="utf-8"))
                rows = [r for r in SESSION.svc().production(st) if r["short"] > 0 and r["makeable"] and not r["fluid"]]
            return json.dumps({"result": rows[:10]})
        except (OSError, ValueError) as e:
            return json.dumps({"error": f"no production numbers yet ({e})"})
    if fn not in API:
        return json.dumps({"error": f"no {fn}"})
    try:
        with SESSION.lock:
            m = getattr(SESSION.svc(), fn)
            out = m(req["params"]) if "params" in req else m()
        return json.dumps({"result": out})
    except Exception as e:  # noqa: BLE001 - shown in the window
        traceback.print_exc()
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def measured(results: str) -> str:
    """the game measured inserter setups (a NotMeasured spec run on its preview surface): into the table"""
    from bpgen import calibrate
    cases = json.loads(results).get("cases") or []
    with SESSION.lock:
        calibrate.store(SESSION.svc().calib_path, cases)
    print(f"bpgen: {len(cases)} inserter setups measured in game")
    return json.dumps({"stored": len(cases)})


BELTISH = ("transport-belt", "underground-belt", "splitter")
DIRS = geom.DIRS


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
        st = SESSION.player(req)
        params = req.get("params") or {}
        SESSION.progress.begin()
        SESSION.progress.note("reading the section", 0)
        with SESSION.lock:
            s = SESSION.svc()
            s.progress = lambda text: SESSION.progress.note(text, 2)
            ents, sources, sinks, clear, notes = _section(s, params)
            SESSION.progress.note(f"{len(ents)} entities, {len(sources)} belts in, {len(sinks)} out", 1)
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
                      "label": f"bpgen: {sec['name']} next to the base", "needs": _section_needs(SESSION.svc().data, sec, {})}


def place(request: str) -> str:
    """the last plan next to the player's base: {"bus": "auto"|"on"|"off", "seed": {x, y}, "snapshot": the area
    around the player, as the snapshot tool takes it} -> as plan(), an absolute blueprint with taps on the base's
    belts, or {error}"""
    t = time.perf_counter()
    from bpgen import extend
    try:
        req = json.loads(request or "{}")
        st = SESSION.player(req)
        _pick_section(st, req.get("section"))
        if not st["last"] or not st["last"].get("entities"):
            return json.dumps({"error": "plan something first"})
        snap = req.get("snapshot")
        seed, at = req.get("seed"), None
        if req.get("shift") and st["where"] and st["where"]["of"] is st["last"]:  # (the arrows: that far from the last spot)
            seed = st["where"]["seed"]
            at = (st["where"]["offset"][0] + req["shift"][0], st["where"]["offset"][1] + req["shift"][1])
        SESSION.progress.begin()
        SESSION.progress.note("placing the last plan where you asked", 2)
        with SESSION.lock:
            SESSION.svc().progress = lambda text: SESSION.progress.note(text, 2)
            site = SESSION.site(st, snap, st["last"].get("prune")) if snap else None
            placed = SESSION.svc().extend(dict(st["last"], bus="here" if st["last"].get("mode") == "move" else req.get("bus") or "auto",
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
        st = SESSION.player(req)
        if req.get("section"):
            _pick_section(st, req["section"])
        last = st["last"]
        if not last or not last.get("entities"):
            return json.dumps({"error": "plan something first"})
        seed = req.get("seed")
        with SESSION.lock:
            s = SESSION.svc()
            site = SESSION.site(st, req["snapshot"], last.get("prune")) if req.get("snapshot") else None
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
        st = SESSION.player(req)
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
        SESSION.progress.begin()
        SESSION.progress.note("tying it into your base", 2)
        with SESSION.lock:
            s = SESSION.svc()
            s.progress = lambda text: SESSION.progress.note(text, 2)
            delta = req.get("delta")
            if delta:
                site = st["site"]
                if site is None or site.snap.get("tick") != delta.get("since")                         or site.snap.get("surface") != delta.get("surface"):
                    return json.dumps({"rescan": True})
                site = st["site"] = site.with_delta(delta.get("built") or [], delta.get("removed") or [],
                                                    delta.get("now"), s.decorate)
            else:
                site = SESSION.site(st, req["snapshot"])
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
        with SESSION.lock:
            s = SESSION.svc()
            main = SESSION.site(SESSION.player(req), req["snapshot"]).bus if req.get("snapshot") else None
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
        with SESSION.lock:
            s = SESSION.svc()
            ground, main = SESSION.site(SESSION.player(req), req["snapshot"]).scratch() if req.get("snapshot") else (None, None)
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
