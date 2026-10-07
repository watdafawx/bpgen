"""bpgen: full-belt production line blueprints.

python -m bpgen serve [--vanilla] [--port 8765]
    web UI: follows Ctrl+Shift+B requests, preview, fine-tune, edit, verify, copy
python -m bpgen watch [--belt NAME] [--beacon-modules speed-module-3:2] [--verify]
    wait for Ctrl+Shift+B requests from the companion mod; the hovered machine's modules are used as-is
python -m bpgen plan RECIPE --machine M --belt B [--modules M:4] [--beacon-modules M:2] [--bonus none|max] [--verify]
    (vanilla data)
modules: "name:count", comma-separated, optional "@quality", e.g. "speed-module-3@rare:2,productivity-module-3:2"
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from bpgen import calibrate, harness, pack, planner
from bpgen.data import Data
from bpgen.effects import Modules

ROOT = Path(__file__).resolve().parent.parent
from bpgen.config import PATHS
REQUEST = PATHS["script_output"] / "bpgen" / "request.json"


def verify(data, p, bp, sources, sinks, bonuses, mod_dir, productivity=0.0):
    res = harness.run({"warmup": planner.warmup_ticks(data, p), "measure": planner.measure_ticks(p), "force": bonuses,
                       "recipe_productivity": {p.recipe: productivity},
                       "cases": [{"id": "bp", "blueprint": bp, "sources": sources, "sinks": sinks}]}, mod_dir=mod_dir)
    c = res["cases"][0]
    got = c["rates"].get(p.output, 0)
    print(f"  verified: {got:.2f}/s measured from the blueprint string ({got / p.expected_output:.1%} of plan)")
    for err in c["errors"]:
        print("  harness:", err)


def emit(p, ents):
    label = f"{p.recipe} x{p.machines} {p.expected_output:g}/s"
    bp = planner.blueprint_string(ents, label)
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / f"{p.recipe}.txt").write_text(bp)
    subprocess.run("clip", input=bp.encode(), shell=True)
    print(f"  blueprint copied to clipboard ({len(bp)} chars, also out/{p.recipe}.txt)")
    return bp


def beacon_args(args):
    mods = Modules.parse(args.beacon_modules)
    if not mods.count:
        return {}
    return {"beacon": args.beacon, "beacon_modules": mods, "beacon_quality": args.beacon_quality}


def add_beacon_args(ap):
    ap.add_argument("--beacon-modules", help="fill beacons with these, e.g. speed-module-3:2 (default: no beacons)")
    ap.add_argument("--beacon", default="beacon", help="beacon entity (default: beacon)")
    ap.add_argument("--beacon-quality", default="normal")


def cmd_plan(args):
    data = Data.load(args.dump)
    calib = json.loads((ROOT / "data" / "calibration.json").read_text())
    bonuses = calibrate.BONUS_LEVELS[args.bonus]
    try:
        p = planner.plan(data, calib, args.recipe, args.machine, args.belt, bonuses=bonuses,
                         modules=Modules.parse(args.modules), **beacon_args(args))
    except planner.PlanError as e:
        print(f"can't plan {args.recipe}: {e}")
        return 1
    ents, sources, sinks = planner.layout(p)
    print(planner.describe(p))
    bp = emit(p, ents)
    if args.verify:
        verify(data, p, bp, sources, sinks, bonuses, ROOT / "mods" / "vanilla")


def handle_request(req, args):
    print(f"\n[{time.strftime('%H:%M:%S')}] request: {req['recipe']} in {req['machine']}", flush=True)
    if pack.sync():
        print("  your mods or settings changed: re-dumped prototype data")
    data = Data.load(pack.DATA)
    bonuses = {k: req["bonuses"].get(k, 0) for k in calibrate.ZERO}
    if bonuses["belt_stack_size_bonus"]:
        print("  note: belt stacking research isn't modelled yet; the plan assumes unstacked input belts")
        bonuses["belt_stack_size_bonus"] = 0
    belts = [b for b in req["belts"] if b in data.raw["transport-belt"]]
    belt = args.belt or max(belts, key=lambda b: data.raw["transport-belt"][b]["speed"])
    if belt not in belts:
        print(f"  warning: {belt} isn't researched in this save")
    inserters = calibrate.template_inserters(data, req["inserters"]) + calibrate.template_inserters(data, req["inserters"], 2)
    table_path = ROOT / "data" / "calibration-pack.json"
    calib = calibrate.calibrate(table_path, pack.PACK, inserters, [belt], [bonuses, calibrate.ZERO], data=data)
    try:
        productivity = req.get("recipe_productivity", 0) or 0
        modules = Modules([(m["name"], m.get("quality") or "normal", m["count"])
                           for m in (req.get("modules") or []) if isinstance(m, dict)])
        p = planner.plan(data, calib, req["recipe"], req["machine"], belt, bonuses=bonuses, allowed=inserters,
                         productivity=productivity, modules=modules,
                         machine_quality=req.get("machine_quality", "normal"), **beacon_args(args))
    except planner.PlanError as e:
        print(f"  can't plan this one: {e}")
        return
    ents, sources, sinks = planner.layout(p)
    print(planner.describe(p))
    bp = emit(p, ents)
    if args.verify:
        print("  verifying in your pack (loads all mods twice, a few minutes) ...", flush=True)
        verify(data, p, bp, sources, sinks, bonuses, pack.PACK, productivity)


def cmd_watch(args):
    print(f"watching {REQUEST} - hover an assembler in game and press Ctrl+Shift+B (Ctrl+C to stop)", flush=True)
    last = REQUEST.stat().st_mtime if REQUEST.exists() else 0
    while True:
        time.sleep(0.5)
        if not REQUEST.exists() or REQUEST.stat().st_mtime == last:
            continue
        last = REQUEST.stat().st_mtime
        try:
            handle_request(json.loads(REQUEST.read_text(encoding="utf-8")), args)
        except Exception as e:  # keep watching after a bad request
            print(f"  error: {e!r}", flush=True)
        print("\nwaiting for the next request ...", flush=True)


def cmd_serve(args):
    from bpgen import web
    defaults = {"belt": args.belt, "beacon_modules": args.beacon_modules, "beacon": args.beacon,
                "beacon_quality": args.beacon_quality}
    web.serve("vanilla" if args.vanilla else "pack", args.port, not args.no_browser, defaults)


def main():
    ap = argparse.ArgumentParser(prog="bpgen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="web UI (follows in-game requests)")
    sv.add_argument("--vanilla", action="store_true", help="plan with vanilla data instead of your pack")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--no-browser", action="store_true")
    sv.add_argument("--belt", help="default belt for in-game requests (default: fastest researched)")
    add_beacon_args(sv)
    sv.set_defaults(func=cmd_serve)
    w = sub.add_parser("watch", help="plan whatever the companion mod requests (your pack)")
    w.add_argument("--belt", help="belt to plan for (default: fastest researched)")
    w.add_argument("--verify", action="store_true", help="also build and measure it in your pack (slow)")
    add_beacon_args(w)
    w.set_defaults(func=cmd_watch)
    pl = sub.add_parser("plan", help="plan one recipe with vanilla data")
    pl.add_argument("recipe")
    pl.add_argument("--machine", default="assembling-machine-2")
    pl.add_argument("--belt", default="transport-belt")
    pl.add_argument("--bonus", default="none", choices=list(calibrate.BONUS_LEVELS))
    pl.add_argument("--dump", default=str(ROOT / "data" / "vanilla-dump.json"))
    pl.add_argument("--verify", action="store_true")
    pl.add_argument("--modules", help="modules in every machine, e.g. speed-module-3:4")
    add_beacon_args(pl)
    pl.set_defaults(func=cmd_plan)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
