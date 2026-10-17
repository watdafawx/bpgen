"""Lines for recipes with 7+ item ingredients in a headless game: no vanilla or Space Age recipe has more than 6, so
mod/test/many_items.py's test recipes go into the game as a tiny mod (bpgen-test-recipes, written into a mod folder
of the run's own beside the vanilla mod list) and into the planner's data. Each line is planned, built and measured
like bpgen.verify's cases: PASS at >= 97% of its planned output, and nothing of the ingredient riding the output
belt's near lane may reach the sink (the filter splitter past the east end stops that lane).
python harness/many_items_check.py [--minutes M]"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mod" / "test"))
from bpgen import harness, planner  # noqa: E402
from bpgen.calibrate import BONUS_LEVELS  # noqa: E402
from bpgen.data import Data  # noqa: E402
import many_items  # noqa: E402

CASES = [
    ("bpgen-test-seven", "assembling-machine-3", "transport-belt"),
    ("bpgen-test-seven", "assembling-machine-2", "fast-transport-belt"),
    ("bpgen-test-seven", "electromagnetic-plant", "transport-belt"),
]


def mod_dir():
    """run/many-items-mods: the vanilla mod list plus bpgen-test-recipes"""
    d = harness.RUN / "many-items-mods"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    mods = json.loads((ROOT / "mods" / "vanilla" / "mod-list.json").read_text(encoding="utf-8-sig"))
    mods["mods"] = [m for m in mods["mods"] if m["name"] != many_items.MOD] + [{"name": many_items.MOD, "enabled": True}]
    (d / "mod-list.json").write_text(json.dumps(mods, indent=1), encoding="utf-8")
    many_items.write_mod(d)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=2)
    args = ap.parse_args()
    data = Data.load(ROOT / "data" / "vanilla-dump.json")
    calib = json.loads((ROOT / "data" / "calibration.json").read_text())
    plans, cases = [], []
    with many_items.added(data):
        for recipe, machine, belt in CASES:
            cid = f"{recipe}|{machine}|{belt}"
            p = planner.plan(data, calib, recipe, machine, belt)
            ents, sources, sinks = planner.layout(p)
            plans.append((cid, p))
            cases.append({"id": cid, "blueprint": planner.blueprint_string(ents, cid), "sources": sources,
                          "sinks": sinks})
        warmup = max(planner.warmup_ticks(data, p) for _, p in plans)
    res = harness.run({"warmup": warmup, "measure": max(planner.measure_ticks(p, int(args.minutes * 3600))
                                                        for _, p in plans),
                       "force": BONUS_LEVELS["none"], "cases": cases}, mod_dir=mod_dir())
    by_id = {c["id"]: c for c in res["cases"]}
    ok = 0
    for cid, p in plans:
        c = by_id[cid]
        got = c["rates"].get(p.output, 0)
        leaked = c["rates"].get(p.out_lane, 0)
        ratio = got / p.expected_output if p.expected_output else 0
        status = "PASS" if ratio >= 0.97 and not leaked and not c["errors"] else "FAIL"
        ok += status == "PASS"
        print(f"{status} {got:6.2f}/s measured vs {p.expected_output:6.2f}/s planned ({ratio:5.1%}); "
              f"{p.out_lane} past the lane stop: {leaked:.2f}/s")
        print(planner.describe(p))
        for err in c["errors"]:
            print("  harness:", err)
        print()
    print(f"{ok}/{len(plans)} passed")
    return 0 if ok == len(plans) else 1


if __name__ == "__main__":
    sys.exit(main())
