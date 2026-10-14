"""Placing into player-style bases, end to end, with a real game: each case builds a base like a player's (base-test
mod: a bus in two groups with side-loads and a line crossing it underground; the same bus with a mall begun beside
it; spaghetti belts with no bus), the mod measures it, bpgen plans a line and places it there as Place by my base
does, and a second game pastes that blueprint (superforced, as the mod does), builds it and runs it 15 minutes.
Fails unless every entity of the paste was built, the paste replaced nothing but the belts its splitters cut into,
every machine is powered, every input was tied in, and the line makes 95 % of its rate or more.
    python mod/test/base_test.py [CASE ...]    (cases run side by side, each in its own write-data folder)"""
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from bpgen import base, harness  # noqa: E402
from bpgen.config import PATHS  # noqa: E402

MINUTES = 15
INSERTERS = ["inserter", "long-handed-inserter", "fast-inserter"]
# case -> what is planned there, how (bus_feed: Feed from my bus; else a plain line then Place by my base), where the
# player stands, which player (each case its own: they run side by side)
CASES = {
    "gapped": {"recipe": "electronic-circuit", "rate": 60, "bus_feed": True, "seed": {"x": 15, "y": -5}, "player": 1},
    "mall": {"recipe": "electronic-circuit", "rate": 60, "bus_feed": True, "seed": {"x": 20, "y": -40}, "player": 2},
    "spaghetti": {"recipe": "iron-gear-wheel", "rate": 60, "bus_feed": False, "seed": {"x": 0, "y": -5}, "player": 3},
}


def run_game(case, spec=None):
    run = ROOT / "run" / f"base-{case}"
    mods = run / "mods"
    out = run / "script-output" / "bpgen"
    if mods.exists():
        shutil.rmtree(mods)
    mods.mkdir(parents=True)
    (run / "config.ini").write_text(f"[path]\nread-data={Path(PATHS['factorio_exe']).parents[2] / 'data'}\n"
                                    f"write-data={run}\n", encoding="utf-8")
    shutil.copytree(HERE.parent / "zzz-bpgen", mods / "zzz-bpgen")
    shutil.copytree(HERE / "base-test", mods / "base-test")
    (mods / "base-test" / "info.json").write_text(json.dumps({
        "name": "base-test", "version": "0.0.1", "title": "bpgen base test", "author": "mtopfox",
        "factorio_version": "2.0", "dependencies": ["base", "zzz-bpgen"]}))
    (mods / "base-test" / "case.lua").write_text(f'return "{case}"', encoding="utf-8")
    if spec:
        (mods / "base-test" / "spec.lua").write_text("return " + harness.to_lua(spec), encoding="utf-8")
    names = ["base", "elevated-rails", "quality", "space-age", "zzz-bpgen", "base-test"]
    (mods / "mod-list.json").write_text(json.dumps({"mods": [{"name": n, "enabled": True} for n in names]}))
    (out / "base-test.txt").unlink(missing_ok=True)
    save = run / "base-test.zip"
    save.unlink(missing_ok=True)
    common = [str(PATHS["factorio_exe"]), "--config", str(run / "config.ini"), "--mod-directory", str(mods)]
    ticks = str(300 + spec["run_ticks"] + 5 if spec else 1560)
    for args in (["--create", str(save)], ["--benchmark", str(save), "--benchmark-ticks", ticks, "--disable-audio"]):
        p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode or "Error" in p.stdout:
            raise RuntimeError(p.stdout[-2500:])
    return (out / "base-test.txt").read_text(), out


def plan(case, snap):
    """bpgen's answer as the window gets it: (blueprint, absolute, notes, output item)"""
    from bpgen import ingame
    c = CASES[case]
    req = {"recipe": c["recipe"], "machine": "assembling-machine-2", "belts": ["transport-belt"],
           "inserters": INSERTERS, "rate_per_min": c["rate"], "player": c["player"]}
    if c["bus_feed"]:  # (Feed from my bus: planned and placed beside the bus in one go)
        req["params"] = {"bus_feed": True, "snapshot": snap, "origin": c["seed"]}
        out = json.loads(ingame.plan(json.dumps(req)))
    else:  # (a plain line, then Place by my base)
        out = json.loads(ingame.plan(json.dumps(req)))
        assert out.get("blueprint"), out
        notes = out.get("notes") or []
        out = json.loads(ingame.place(json.dumps({"player": c["player"], "snapshot": snap, "seed": c["seed"], "bus": "auto"})))
        out["output"] = c["recipe"]
        out["notes"] = notes + (out.get("notes") or [])
    assert out.get("absolute"), f"{case}: not placed: {out.get('error') or out.get('notes')}"
    notes = out.get("notes") or (out.get("summary") or {}).get("notes") or []
    return out["blueprint"], out["absolute"], notes, out.get("output") or c["recipe"]


def check(case):
    """-> (case, [failures], lines)"""
    from bpgen import ingame, planner
    from bpgen.service import Service
    fails = []
    text, out = run_game(case)
    snap = json.loads((out / "snapshot.json").read_text(encoding="utf-8"))
    bp, absolute, notes, item = plan(case, snap)
    lines = [f"placed at {absolute['box'][:4]}: {absolute['taps']} taps, {absolute['new_lanes']} new lanes"]
    lines += [f"  {n}" for n in notes]
    if absolute.get("inputs"):
        fails.append(f"inputs not tied in: {absolute['inputs']}")
    n = len(base._decode(bp)["blueprint"]["entities"])
    text, _ = run_game(case, {"blueprint": bp, "box": absolute["box"], "entities": n, "target": item,
                              "run_ticks": MINUTES * 3600})
    lines += text.strip().splitlines()
    got = {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] if " " in ln else "" for ln in text.splitlines()}
    built = int(got.get("ghosts", "0 of 0").split()[0])
    if built != n:
        fails.append(f"{n - built} of {n} entities not built (something in the way)")
    replaced = dict(p.split("=") for p in got.get("replaced", "").split())
    if set(replaced) - {"transport-belt"}:
        fails.append(f"the paste replaced more than belts: {replaced}")
    status = dict(p.split("=") for p in got.get("machines", "0").split()[1:] if "=" in p)
    # (short of ingredients is fine: a line has more machines than its rate needs, the rate check says enough)
    if set(status) - {"working", "full_output", "item_ingredient_shortage"}:
        fails.append(f"machines not working: {status}")
    rate = float(got.get("rate", "0"))
    want = CASES[case]["rate"]
    if rate < 0.95 * want:
        fails.append(f"{item}: {rate:.1f}/min, planned {want}/min")
    return case, fails, lines


def main():
    cases = sys.argv[1:] or list(CASES)
    from bpgen import ingame, planner
    from bpgen.service import Service
    s = Service("vanilla")
    planner.configure(s.data)
    ingame._service = s
    with ThreadPoolExecutor(max_workers=3) as pool:  # (the games run side by side; planning takes bpgen's lock)
        results = list(pool.map(check, cases))
    bad = 0
    for case, fails, lines in results:
        print(f"== {case}: {'ok' if not fails else 'FAILED'}")
        for ln in lines:
            print("  " + ln)
        for f in fails:
            print("  FAIL " + f)
        bad += bool(fails)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
