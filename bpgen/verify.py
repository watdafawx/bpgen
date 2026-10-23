"""Plan designs, build them in headless Factorio, compare measured output with the plan."""
import argparse
import json
from pathlib import Path

from bpgen import chain, direct, harness, planner
from bpgen.calibrate import BONUS_LEVELS
from bpgen.data import Data
from bpgen.effects import Modules

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CASES = [
    ("iron-gear-wheel", "assembling-machine-2", "transport-belt"),
    ("iron-gear-wheel", "assembling-machine-2", "fast-transport-belt"),
    ("iron-gear-wheel", "assembling-machine-3", "express-transport-belt"),
    ("copper-cable", "assembling-machine-2", "fast-transport-belt"),
    ("electronic-circuit", "assembling-machine-2", "transport-belt"),
    ("electronic-circuit", "assembling-machine-3", "fast-transport-belt"),
    ("automation-science-pack", "assembling-machine-2", "transport-belt"),
    ("logistic-science-pack", "assembling-machine-3", "transport-belt"),
    ("iron-gear-wheel", "assembling-machine-3", "turbo-transport-belt"),
    # 3-4 ingredients (second input belt, long-handed inserters)
    ("inserter", "assembling-machine-2", "transport-belt"),
    ("engine-unit", "assembling-machine-2", "transport-belt"),
    ("flying-robot-frame", "assembling-machine-3", "transport-belt"),
    # one fluid ingredient (pipe row + underground taps)
    ("electric-engine-unit", "assembling-machine-3", "transport-belt"),
    ("express-transport-belt", "assembling-machine-3", "fast-transport-belt"),
    ("processing-unit", "assembling-machine-3", "transport-belt"),
    ("express-splitter", "assembling-machine-3", "transport-belt"),
    # furnaces (vanilla: real furnace type, no recipe in the blueprint; burners get a coal lane)
    ("copper-plate", "stone-furnace", "transport-belt"),
    ("iron-plate", "electric-furnace", "fast-transport-belt"),
    ("steel-plate", "steel-furnace", "transport-belt"),
    # chains: ingredients made inside the same blueprint (stage blocks + routed belts)
    ("automation-science-pack", "assembling-machine-2", "transport-belt",
     {"make": {"iron-gear-wheel": {"recipe": "iron-gear-wheel", "machine": "assembling-machine-2"}}}),
    ("electronic-circuit", "assembling-machine-3", "transport-belt",
     {"make": {"copper-cable": {"recipe": "copper-cable", "machine": "assembling-machine-3"}}}),
    # direct insertion: cable assemblers inserting into circuit assemblers (direct.py), a full belt and a few
    ("electronic-circuit", "assembling-machine-2", "transport-belt",
     {"direct": {"copper-cable": {"recipe": "copper-cable", "machine": "assembling-machine-2"}}}),
    ("electronic-circuit", "assembling-machine-2", "transport-belt",
     {"direct": {"copper-cable": {"recipe": "copper-cable", "machine": "assembling-machine-2"}}, "rate": 120}),
    ("electronic-circuit", "assembling-machine-2", "transport-belt",  # (early game: no bulk or stack inserters)
     {"direct": {"copper-cable": {"recipe": "copper-cable", "machine": "assembling-machine-2"}},
      "inserters": ["inserter", "long-handed-inserter", "fast-inserter"]}),
    ("electronic-circuit", "assembling-machine-3", "fast-transport-belt",
     {"direct": {"copper-cable": {"recipe": "copper-cable", "machine": "assembling-machine-3"}}}),
    # modules and beacons (one beacon between neighbouring machines: 2 per machine)
    ("iron-gear-wheel", "assembling-machine-3", "express-transport-belt", {"modules": "speed-module-3:4"}),
    ("copper-cable", "assembling-machine-2", "fast-transport-belt", {"modules": "speed-module-2@rare:2"}),
    ("electronic-circuit", "assembling-machine-3", "fast-transport-belt",
     {"modules": "productivity-module-3:4", "beacon": "beacon", "beacon_modules": "speed-module-3:2"}),
    ("engine-unit", "assembling-machine-3", "transport-belt", {"beacon": "beacon", "beacon_modules": "speed-module-3:2"}),
    ("processing-unit", "assembling-machine-3", "transport-belt",
     {"modules": "productivity-module-3:4", "beacon": "beacon", "beacon_modules": "speed-module-3:2"}),
    # lines with fluids the row solver lays out (0.28): fluid in, items out (foundry, 5x5); items and a fluid in, a
    # fluid out; items in, a fluid out (foundry); fluids only; two fluids and an item in a 5x5
    ("casting-iron", "foundry", "transport-belt"),
    ("sulfuric-acid", "chemical-plant", "transport-belt"),
    ("iron-ore-melting", "foundry", "transport-belt"),
    ("heavy-oil-cracking", "chemical-plant", "transport-belt"),
    ("lithium", "cryogenic-plant", "transport-belt", {"rate": 300}),  # (5/s: a full belt of it is 32 plants)
    # a fluid that opens only on the machine's west or east edge: a pipe down a gap beside each machine (0.28.1)
    ("electrolyte", "electromagnetic-plant", "transport-belt"),
    # (quantum processors, the gap east of each plant, plan too, but only Aquilo's pressure crafts them: the lab
    # surface the harness builds on is Nauvis)
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=str(ROOT / "data" / "vanilla-dump.json"))
    ap.add_argument("--bonus", default="none", choices=list(BONUS_LEVELS))
    ap.add_argument("--minutes", type=float, default=2)
    ap.add_argument("--only", default="", help="only the cases whose id contains this")
    args = ap.parse_args()

    data = Data.load(args.dump)  # (the planner follows it: its pole, pipes and power pass)
    calib = json.loads((ROOT / "data" / "calibration.json").read_text())
    plans, cases = [], []
    for recipe, machine, belt, *extra in DEFAULT_CASES:
        opts = extra[0] if extra else {}
        cid = (f"{recipe}|{machine}|{belt}" + "".join(f"|{v}" for v in opts.values() if not isinstance(v, (dict, list)))
               + ("|early" if opts.get("inserters") else "") + "".join(f"|make {k}" for k in opts.get("make", {}))
               + "".join(f"|direct {k}" for k in opts.get("direct", {})))
        if args.only not in cid:
            continue
        try:
            if opts.get("direct"):
                (mi, mv), = opts["direct"].items()
                p = direct.plan(data, calib, recipe, machine, belt, mi, mv["recipe"], mv["machine"],
                                bonuses=BONUS_LEVELS[args.bonus], target_rate=(opts.get("rate") or 0) / 60 or None,
                                allowed=opts.get("inserters"))
                from bpgen import rowlayout, rowspec
                ents, sources, sinks = rowlayout.layout(rowspec.RowSpec.from_direct_plan(p), p)
            elif opts.get("make"):
                c = chain.plan_chain(data, calib, {"recipe": recipe, "machine": machine, "belt": belt}, opts["make"],
                                     bonuses=BONUS_LEVELS[args.bonus])
                p, ents, sources, sinks = c.final.plan, c.entities, c.sources, c.sinks
            else:
                p = planner.plan(data, calib, recipe, machine, belt, bonuses=BONUS_LEVELS[args.bonus],
                                 modules=Modules.parse(opts.get("modules")), beacon=opts.get("beacon"),
                                 beacon_modules=Modules.parse(opts.get("beacon_modules")),
                                 target_rate=(opts.get("rate") or 0) / 60 or None)
                ents, sources, sinks = planner.layout(p)
        except planner.PlanError as e:
            print(f"SKIP {cid}: {e}")
            continue
        plans.append((cid, p))
        bp = planner.blueprint_string(ents, cid)
        cases.append({"id": cid, "blueprint": bp, "sources": sources, "sinks": sinks})

    def mod(p):
        return direct if isinstance(p, direct.DirectPlan) else planner
    res = harness.run({"warmup": max(mod(p).warmup_ticks(data, p) for _, p in plans),
                       "measure": max(mod(p).measure_ticks(p, int(args.minutes * 3600)) for _, p in plans),
                       "force": BONUS_LEVELS[args.bonus], "cases": cases})
    by_id = {c["id"]: c for c in res["cases"]}
    ok = 0
    for cid, p in plans:
        c = by_id[cid]
        got = c["rates"].get(p.output, 0)
        ratio = got / p.expected_output if p.expected_output else 0
        status = "PASS" if ratio >= 0.97 else "FAIL"
        ok += status == "PASS"
        print(f"{status} {got:6.2f}/s measured vs {p.expected_output:6.2f}/s planned ({ratio:5.1%})")
        print(mod(p).describe(p))
        for err in c["errors"]:
            print("  harness:", err)
        print()
    print(f"{ok}/{len(plans)} passed")


if __name__ == "__main__":
    main()
