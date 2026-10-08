"""End to end: snapshot a small base in headless Factorio (pure iron and copper plate belts), let bpgen plan a whole
chain for an item next to it, fed by tapping those belts, paste the blueprint into the same map like a player would,
and check it lands where planned and makes the item at the planned rate.
With "bus" as the fourth argument the base is a main bus of four belts instead (copper, stone, iron, coal): the
build goes beside it, branching off its lanes.
    python mod/test/extend_test.py [item] [rate_per_min] [minutes] [bus]"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from bpgen import harness, service  # noqa: E402
from bpgen.config import PATHS  # noqa: E402

RUN = ROOT / "run"
MODS = RUN / "comp-mods"
OUT = RUN / "script-output" / "bpgen"
SEED = "4242"
ITEM = sys.argv[1] if len(sys.argv) > 1 else "logistic-science-pack"
RATE = float(sys.argv[2]) if len(sys.argv) > 2 else 30
MINUTES = float(sys.argv[3]) if len(sys.argv) > 3 else 15
BUS = len(sys.argv) > 4 and sys.argv[4] == "bus"


def run_game(spec=None):
    if MODS.exists():
        shutil.rmtree(MODS)
    MODS.mkdir(parents=True)
    shutil.copytree(HERE.parent / "zzz-bpgen", MODS / "zzz-bpgen")
    shutil.copytree(HERE / "comp-test", MODS / "comp-test")
    (MODS / "comp-test" / "info.json").write_text(json.dumps({
        "name": "comp-test", "version": "0.0.1", "title": "bpgen mod test", "author": "mtopfox",
        "factorio_version": "2.0", "dependencies": ["base", "zzz-bpgen"]}))
    (MODS / "comp-test" / "layout.lua").write_text(f'return "{"bus" if BUS else "lines"}"', encoding="utf-8")
    if spec:
        (MODS / "comp-test" / "spec.lua").write_text("return " + harness.to_lua(spec), encoding="utf-8")
    names = ["base", "elevated-rails", "quality", "space-age", "zzz-bpgen", "comp-test"]
    (MODS / "mod-list.json").write_text(json.dumps({"mods": [{"name": n, "enabled": True} for n in names]}))
    (OUT / "comp-test.txt").unlink(missing_ok=True)
    save = RUN / "comp-test.zip"
    save.unlink(missing_ok=True)
    common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(MODS)]
    ticks = str(300 + spec["run_ticks"] + 5 if spec else 1560)  # (snapshot at 1500, measured over 30 ticks)
    for args in (["--create", str(save), "--map-gen-seed", SEED],
                 ["--benchmark", str(save), "--benchmark-ticks", ticks, "--disable-audio"]):
        p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode or "Error" in p.stdout:
            print(p.stdout[-2500:])
            sys.exit(1)
    return (OUT / "comp-test.txt").read_text()


print(run_game().strip())
# the web UI reads the mod's files from script-output; point it at this run's
service.SNAPSHOT = OUT / "snapshot.json"
service.SAVE_STATE = OUT / "state.json"
s = service.Service("vanilla")
res = s.plan_extension({"item": ITEM, "rate_per_min": RATE, "belt": "transport-belt",
                        "assembler": "assembling-machine-2", "furnace": "stone-furnace", "bus": "on" if BUS else "off",
                        "inserters": ["inserter", "long-handed-inserter", "fast-inserter"],
                        "bonuses": {"inserter_stack_size_bonus": 0, "bulk_inserter_capacity_bonus": 0,
                                    "belt_stack_size_bonus": 0}})
print("chain:", ", ".join(f"{x['item']} {x['rate']}/min" for x in res["steps"]))
print("needs:", res["needs"], "taps:", [(t["items"], t["from"]) for t in res["taps"]], "notes:", res["notes"])
x0, y0, w, h = res["box"]
cursor = {"x": x0 + w / 2 + 1.3, "y": y0 + h / 2 - 0.7}  # a player's aim: inside the box, off centre
expect = [{"name": e["name"], "position": e["position"]} for e in res["entities"]
          if e["type"] in ("assembling-machine", "furnace", "splitter")]
print("outputs:", res["outputs"])
print(run_game({"blueprint": res["blueprint"], "cursor": cursor, "expect": expect, "target": ITEM,
                "outputs": res["outputs"],
                "run_ticks": int(MINUTES * 3600)}).strip())
print("planned", RATE, "/min")
