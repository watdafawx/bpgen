"""A machine far faster than any inserter pair (a modded assembler crafting 5000 gears a second) still plans:
the row solver slows it - INSERTER_THROTTLE goes below 1% - until the inserters keep up, and says so in the notes. No
game, vanilla only: the fast machine is assembling-machine-3's prototype copied with crafting_speed 10000.
python mod/test/inserter_limit_test.py"""
import copy
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import planner, rates  # noqa: E402
from bpgen.service import Service  # noqa: E402

s = Service("vanilla")
data = s.data
planner.configure(data)
calib = json.loads(s.calib_path.read_text())
BELT = "transport-belt"

fast = copy.deepcopy(data.raw["assembling-machine"]["assembling-machine-3"])
fast["name"] = "assembling-machine-10000"
fast["crafting_speed"] = 10000
data.raw["assembling-machine"][fast["name"]] = fast

# the same machine at its stock speed is no problem: no slowdown note
p0 = planner.plan(data, calib, "iron-gear-wheel", "assembling-machine-3", BELT)
assert not any("can't keep" in n for n in p0.notes), p0.notes

p = planner.plan(data, calib, "iron-gear-wheel", fast["name"], BELT)
slowed = [n for n in p.notes if "its inserters can't keep one" in n and "speed" in n]
assert slowed, p.notes
# speed 10000 crafts a gear (0.5 s) 20000 times a second; two output inserters carry ~15: well below the 1% the old
# steps stopped at (200 crafts/s)
assert 0 < p.craft_rate < 20000 * 0.01, p.craft_rate
# what it makes is what its inserters carry, not more
assert p.output_per_machine <= p.ins_out.capacity * 1.001, (p.output_per_machine, p.ins_out)
assert p.machines >= 1
planner.layout(p)

# the steps themselves: descending, past 1%, positive
steps = rates.INSERTER_THROTTLE
assert list(steps) == sorted(steps, reverse=True) and steps[0] == 1.0 and 0 < steps[-1] < 1e-4, steps
assert rates.pct(0.5) == "50%" and rates.pct(0.0034) == "0.34%", (rates.pct(0.5), rates.pct(0.0034))
print(f"ok: {fast['name']} plans {p.machines} machine(s) at craft_rate {p.craft_rate:.3g}/s - {slowed[0]}")

# a need so small it rounds to nothing still takes one inserter, and none fits in no columns (a machine slowed to a
# millionth "fitted" its far inserter in 0 free columns, then a row of 8 million machines was asked for)
name = next(k.split("|")[0] for k in calib if k.endswith("|belt_to_machine|" + k.split("|")[-1]) and "|transport-belt|" in k)
choice = rates.pick_inserter(calib, [name], "transport-belt", "belt_to_machine", lambda n: next(
    k.split("|")[-1] for k in calib if k.startswith(n + "|transport-belt|belt_to_machine|")), 1e-12, 1)
assert choice.count == 1, choice
try:
    rates.pick_inserter(calib, [name], "transport-belt", "belt_to_machine", lambda n: next(
        k.split("|")[-1] for k in calib if k.startswith(n + "|transport-belt|belt_to_machine|")), 1e-12, 0)
    raise AssertionError("an inserter fitted in 0 columns")
except planner.PlanError:
    pass
print("ok: a vanishing need still takes one inserter")
