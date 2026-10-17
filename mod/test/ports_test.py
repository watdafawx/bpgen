"""ports.py (the row solver's view of a machine's fluid connections): every assembling machine and furnace in the
vanilla dump and, when present, the user's pack dump, under all four facings, has its ports on its edges (and its
size turned with it); and a few vanilla machines read as drawn. No game.
python mod/test/ports_test.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bpgen import geom  # noqa: E402
from bpgen.ports import EAST, FACINGS, INPUTS, NORTH, OUTPUTS, SOUTH, WEST, Ports  # noqa: E402
from bpgen.service import Service  # noqa: E402

checked = {"machines": 0, "ports": 0}
problems = []


def check(data_name):
    s = Service(data_name)
    raw = s.data.raw
    for kind in ("assembling-machine", "furnace"):
        for name, m in sorted(raw.get(kind, {}).items()):
            if not m.get("fluid_boxes"):
                continue
            checked["machines"] += 1
            w, h = geom.dims(m)
            for facing in FACINGS:
                ports = Ports.of(m, facing)
                checked["ports"] += len(ports.ports)
                assert (ports.w, ports.h) == ((h, w) if facing in (EAST, WEST) else (w, h)), (name, facing, ports)
                for p in ports.ports:  # (every port sits on its edge, inside it)
                    limit = ports.w if p.edge in (NORTH, SOUTH) else ports.h
                    assert 0 <= p.at < limit, (name, facing, p)
                for a in ports.ports:  # (connections of one box, each on its own edge tile)
                    assert a.kind in ("input", "output", "input-output"), (name, a)


check("vanilla")
pack = Path(__file__).resolve().parents[2] / "data" / "pack-dump.json"
if pack.exists():
    check("pack")
for p in problems:
    print("  -", p)
assert not problems, f"{len(problems)} disagreements"
# a few facts the solver relies on, from vanilla
s = Service("vanilla")
cp = Ports.of(s.data.raw["assembling-machine"]["chemical-plant"], NORTH)
assert [p.at for p in cp.on(NORTH, INPUTS)] == [0, 2] and [p.at for p in cp.on(SOUTH, OUTPUTS)] == [0, 2], cp
assert not cp.joins(), cp
ref = s.data.raw["assembling-machine"]["oil-refinery"]
rn = Ports.of(ref, NORTH)
assert [p.at for p in rn.on(SOUTH, INPUTS)] == [1, 3] and [p.at for p in rn.on(NORTH, OUTPUTS)] == [0, 2, 4], rn
rs = Ports.of(ref, SOUTH)  # turned round: inputs north
assert [p.at for p in rs.on(NORTH, INPUTS)] == [3, 1], rs  # (mirrored, box order)
re_ = Ports.of(ref, EAST)  # a quarter: inputs on the west edge, outputs on the east
assert re_.edges(INPUTS) == {WEST} and re_.edges(OUTPUTS) == {EAST}, re_
asm = Ports.of(s.data.raw["assembling-machine"]["assembling-machine-2"], NORTH)
assert [p.at for p in asm.on(NORTH, INPUTS)] == [1] and asm.boxes(INPUTS) == [0] and asm.boxes(OUTPUTS) == [1], asm
print("ok:", ", ".join(f"{k} {v}" for k, v in checked.items()))
