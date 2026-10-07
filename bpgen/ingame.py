"""bpgen inside the game, through the fnative "py" plugin (native/README.md): no web app, no files, no second
process. The companion mod sends the same request it writes to request.json:

    local id = native.start("py", "bpgen.ingame:plan", helpers.table_to_json(request))
    ... on_tick: local status, out = native.poll(id)   -- "done", '{"blueprint": "0eNr...", ...}'

The interpreter stays loaded between plans, so bpgen's data (the pack's prototypes, the inserter measurements)
loads once, on the first plan or on warm(). Nothing is measured in game (that needs a headless game): a plan that
needs an unmeasured inserter setup says so instead.
"""
import json
import threading
import time
import traceback

_service = None
_lock = threading.Lock()  # (plans share one Service: one at a time)
_taking = None  # the thread writing data the game sent (planning waits for it)


def data_wanted(active: str = "") -> str:
    """the data stage asks (mod zzz-bpgen-data): "" if bpgen's copy of the mods' data is current (or the game runs
    other mods than the user's), else "type,type,...|skip,skip,..." for native.to_json"""
    from bpgen import pack
    try:
        want = pack.game_wants(json.loads(active or "{}"))
    except (OSError, ValueError):
        traceback.print_exc()
        return ""
    return "" if not want else ",".join(want[0]) + "|" + ",".join(want[1])


def take_data(text: str) -> str:
    """data.raw from the data stage -> bpgen's data files, on a thread (the game goes on loading)"""
    global _taking, _service
    from bpgen import pack

    def work():
        t = time.perf_counter()
        try:
            pack.from_game(text)
            print(f"bpgen: mods' data from the game in {time.perf_counter() - t:.1f} s")
        except Exception:  # noqa: BLE001 - logged; bpgen keeps its old copy
            traceback.print_exc()

    _service = None
    _taking = threading.Thread(target=work, name="bpgen-take-data")
    _taking.start()
    return f"taking {len(text) / 1e6:.1f} MB"


def stale(has_data_mod: str = "") -> str:
    """at game load: bpgen's copy is stale although the data stage didn't send it (the game loaded its data stage
    cache): the game's data-cache.dat is removed so the next start runs the data stage and sends it"""
    from bpgen import pack
    from bpgen.config import PATHS
    if _taking is not None or pack.up_to_date():
        return json.dumps({"stale": False})
    cache = PATHS["write_data"] / "data-cache.dat"
    removed = False
    if has_data_mod and cache.exists():  # (without the mod a new data stage wouldn't help)
        try:
            cache.unlink()
            removed = True
        except OSError:
            pass
    return json.dumps({"stale": True, "cache_removed": removed})


def _svc():
    global _service
    if _taking is not None:
        _taking.join()
    if _service is None:
        from bpgen.service import Service
        s = Service("pack")
        s.data  # (loads the pack's prototypes now, not inside the first plan)
        _service = s
    return _service


def warm(_: str = "") -> str:
    """load bpgen's data ahead of the first plan (the companion calls this when the game loads)"""
    t = time.perf_counter()
    with _lock:
        _svc()
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


def plan(request: str) -> str:
    """a companion request (recipe, machine, unlocked belts and inserters, bonuses...) -> {blueprint, label, text}
    or {error}"""
    from bpgen import calibrate, pack, planner
    t = time.perf_counter()
    try:
        req = json.loads(request)
        with _lock:
            s = _svc()
            if req.get("recipe") and req.get("machine"):
                params = s.params_from_request(req)
            else:  # (a mall or a starter base: no one recipe)
                belts = s.base_belts(req.get("belts") or [])
                params = {"belts": belts, "inserters": req.get("inserters") or [],
                          "bonuses": {k: (req.get("bonuses") or {}).get(k, 0) for k in calibrate.ZERO},
                          # (the fastest of the save's belts, as params_from_request picks for a line)
                          "belt": max(belts, key=lambda b: s.data.raw["transport-belt"][b]["speed"]) if belts
                          else "transport-belt"}
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
            token = calibrate.NO_MEASURE.set(True)
            try:
                tries = [m for m in (params.pop("machines_try", None) or []) if m] if params.get("mode") == "mall" else []
                for i, m in enumerate(tries[:4] or [params.get("machine")]):
                    # (a mall: the window's candidate machines in turn, fastest first, until one lays out)
                    if tries:
                        params["machine"] = m
                    try:
                        out = s.plan(params)
                        break
                    except planner.PlanError:
                        if i == len(tries[:4]) - 1 or not tries:
                            raise
                if params.get("mode") == "extend":
                    # (next to the base: the chain placed into the snapshot, taps on its belts, power: an absolute
                    # blueprint that lands where it belongs)
                    placed = s.extend({"entities": out["entities"], "sources": out.get("sources") or [],
                                       "sinks": out.get("sinks") or [], "belt": params.get("belt"),
                                       "label": f"bpgen: {params.get('item')} next to the base"})
                    summary = dict(out.get("summary") or {})
                    summary["notes"] = list(summary.get("notes") or []) + list(placed.get("notes") or [])
                    out = {"blueprint": placed["blueprint"], "summary": summary,
                           "absolute": {"box": placed["box"], "taps": len(placed.get("taps") or []),
                                        "deliveries": len(placed.get("deliveries") or [])}}
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
                                                    "expected", "productivity", "beacons_per_machine")},
            "seconds": round(time.perf_counter() - t, 3),
            # (the window's test run: the harness case for this line, run live on its preview)
            "test": _test_spec(out, params) if summary.get("expected") and out.get("sinks") else None,
        })
    except calibrate.NotMeasured as e:
        # (the game measures them itself on the preview surface, then plans again: measured() below)
        return json.dumps({"error": str(e), "measure": e.spec, "seconds": round(time.perf_counter() - t, 3)})
    except planner.PlanError as e:
        return json.dumps({"error": str(e), "seconds": round(time.perf_counter() - t, 3)})
    except Exception as e:  # noqa: BLE001 - shown in game, with the trace in the fnative log via the error text
        return json.dumps({"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]})
