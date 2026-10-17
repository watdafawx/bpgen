"""bpgen must not hitch the game: a headless game (hitch-test mod) builds a dense 400x400 base (72,000 entities),
then runs each of bpgen's game-side steps on small, large and huge inputs (preview, placing and Undo, Try in sandbox,
reading the base, the Move tool's area, an answer parsed), one at a time, each until bpgen has nothing left to do.
Per step: the ticks it spread over, each tick's whole update and script time (the benchmark's per-tick timings) and
bpgen's own time (its tasks' calls, logged by the mod), timed on the P-cores. Also checks that the work spread over
ticks gives what doing it at once did: the snapshot (against 0.28's one read), the preview and an absolute paste
(built from pieces against the whole print), the area's blueprint (cells joined against one blueprint).
    python mod/test/hitch_test.py [--budget MS] [--only TEXT]
(fails when a step's ticks of bpgen's own work are over the budget, 8 ms: 99 of 100 under it, none over twice; a
tick's whole and script times also hold the test mod's own and the debug log's writes)"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from bpgen import ents as entlib, extend, ingame, planner, snapfmt  # noqa: E402
from bpgen.config import PATHS  # noqa: E402
from bpgen.service import Service  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--budget", type=float, default=8.0)
ap.add_argument("--only", help="only the steps whose name has this")
args = ap.parse_args()

INS = ["inserter", "long-handed-inserter", "fast-inserter", "bulk-inserter", "stack-inserter"]
BELTS = ["transport-belt", "fast-transport-belt", "express-transport-belt", "turbo-transport-belt"]
POLES = ["small-electric-pole", "medium-electric-pole", "big-electric-pole", "substation"]
REQS = {
    "small": {"recipe": "iron-gear-wheel", "machine": "assembling-machine-1", "belts": ["transport-belt"],
              "inserters": ["inserter"], "poles": ["small-electric-pole"], "rate_per_min": 30},
    "large": {"recipe": "lithium", "machine": "cryogenic-plant", "belts": BELTS, "inserters": INS, "poles": POLES,
              "rate_per_min": 1800},
    "huge": {"recipe": "lithium", "machine": "cryogenic-plant", "belts": BELTS, "inserters": INS, "poles": POLES,
             "rate_per_min": 3000},
    "base": {"recipe": "automation-science-pack", "machine": "assembling-machine-2", "belts": ["transport-belt"],
             "inserters": INS[:3], "poles": POLES[:2],
             "params": {"mode": "base", "layout": "bus", "plates": True, "spm": 30, "mall": False,
                        "belt": "transport-belt"}},
}

s = Service("vanilla")
planner.configure(s.data)
ingame._service = s
answers = {}
for name, req in REQS.items():
    out = json.loads(ingame.plan(json.dumps(dict(req, player=1))))
    assert out.get("blueprint"), (name, out.get("error"))
    answers[name] = out
# an absolute plan (as Place by my base answers): the huge line at a spot over the dense base, snapping to the world
# grid there; its box is where its tiles are
huge = entlib.decode(answers["huge"]["blueprint"])["blueprint"]["entities"]
world = [dict(s.decorate({k: v for k, v in e.items() if k != "entity_number"}),
              position={"x": e["position"]["x"] - 130, "y": e["position"]["y"] - 20}) for e in huge]
abs_bp, box = extend.absolute_blueprint(world, "bpgen: hitch test")
answers["absolute"] = json.loads(ingame._answer({"blueprint": abs_bp, "absolute": {"box": list(box)}}))

RUN = ROOT / "run" / "hitch"
MODS = RUN / "mods"
if MODS.exists():
    shutil.rmtree(MODS)
MODS.mkdir(parents=True)
(RUN / "config.ini").write_text(f"[path]\nread-data={PATHS['game_data']}\nwrite-data={RUN}\n", encoding="utf-8")
shutil.copytree(HERE.parent / "zzz-bpgen", MODS / "zzz-bpgen")
shutil.copytree(HERE / "hitch-test", MODS / "hitch-test")
(MODS / "hitch-test" / "info.json").write_text(json.dumps({
    "name": "hitch-test", "version": "0.0.1", "title": "bpgen hitch test", "author": "mtopfox",
    "factorio_version": "2.0", "dependencies": ["base", "zzz-bpgen"]}))
lua = ["return {"]
for k, v in answers.items():
    lua.append(f"  {k} = {json.dumps(v['blueprint'])},")
    lua.append(f"  {k}_answer = {json.dumps(json.dumps(v))},")
lua.append(f"  absolute_box = {{ {', '.join(str(v) for v in box)} }},")
if args.only:
    lua.append(f"  only = {json.dumps(args.only)},")
if os.environ.get("HITCH_GC_KB"):
    lua.append(f"  gc_kb = {int(os.environ['HITCH_GC_KB'])},")
lua.append("}")
(MODS / "hitch-test" / "bps.lua").write_text("\n".join(lua) + "\n", encoding="utf-8")
names = ["base", "elevated-rails", "quality", "space-age", "zzz-bpgen", "hitch-test"]
(MODS / "mod-list.json").write_text(json.dumps({"mods": [{"name": n, "enabled": True} for n in names]}))
OUT = RUN / "script-output"
for f in list(OUT.glob("ident-*.json")) + [OUT / "hitch.txt"]:
    f.unlink(missing_ok=True)
done = OUT / "hitch.txt"
save = RUN / "hitch.zip"
save.unlink(missing_ok=True)
common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(MODS)]
p = subprocess.run(common + ["--create", str(save)], capture_output=True, text=True, encoding="utf-8", errors="replace")
if p.returncode:
    sys.exit(p.stdout[-3000:])
def finished():
    """the last step's last tick once the mod wrote "done <tick>" (it writes other lines there before), else None"""
    m = done.exists() and re.search(r"^done (\d+)$", done.read_text(encoding="utf-8", errors="replace"), re.M)
    return int(m.group(1)) if m else None


# a benchmark (as fast as it goes) stopped once the mod wrote "done": how many ticks the steps take; then again for
# exactly that many ticks, every tick's timings printed (the game prints them only when a benchmark ends)
bench_out = RUN / "benchmark.txt"
with open(bench_out, "w", encoding="utf-8", errors="replace") as fh:
    game = subprocess.Popen(common + ["--benchmark", str(save), "--benchmark-ticks", "100000", "--disable-audio"],
                            stdout=fh, stderr=subprocess.STDOUT)
    t0 = time.time()
    while game.poll() is None and not finished() and time.time() - t0 < 1200:
        time.sleep(0.5)
    time.sleep(1)
    game.kill()
log = (RUN / "factorio-current.log").read_text(encoding="utf-8", errors="replace")
if not finished():
    print(bench_out.read_text(encoding="utf-8", errors="replace")[-2000:])
    print("\n".join(l for l in log.splitlines() if "rror" in l)[-3000:])
    sys.exit("the game failed (or didn't finish its steps)")
last = finished()  # (the log may not have the last lines yet)


def fast_cores():
    """affinity mask of this CPU's fastest cores (a hybrid CPU's P-cores, where a played game runs), or None"""
    import ctypes
    k32 = ctypes.windll.kernel32
    size = ctypes.c_ulong(0)
    k32.GetSystemCpuSetInformation(None, 0, ctypes.byref(size), None, 0)
    buf = ctypes.create_string_buffer(size.value)
    if not k32.GetSystemCpuSetInformation(buf, size, ctypes.byref(size), None, 0):
        return None
    cpus, off = [], 0
    while off < size.value:
        n = int.from_bytes(buf.raw[off:off + 4], "little")
        group, lp, eff = int.from_bytes(buf.raw[off + 12:off + 14], "little"), buf.raw[off + 14], buf.raw[off + 18]
        if group == 0:
            cpus.append((lp, eff))
        off += n or 32
    best = max((e for _, e in cpus), default=0)
    mask = sum(1 << lp for lp, e in cpus if e == best)
    return mask if mask and len({e for _, e in cpus}) > 1 else None


done.unlink(missing_ok=True)  # (the timed run writes it again)
game = subprocess.Popen(common + ["--benchmark", str(save), "--benchmark-ticks", str(last + 3), "--benchmark-verbose",
                                  "all", "--disable-audio"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")
mask = fast_cores()
if mask:  # (timed on the P-cores: on an E-core the same work takes about twice as long)
    import ctypes
    h = ctypes.windll.kernel32.OpenProcess(0x0200 | 0x0400, False, game.pid)
    ctypes.windll.kernel32.SetProcessAffinityMask(h, ctypes.c_size_t(mask))
    ctypes.windll.kernel32.CloseHandle(h)
stdout, _ = game.communicate()
bench_out.write_text(stdout, encoding="utf-8")
log = (RUN / "factorio-current.log").read_text(encoding="utf-8", errors="replace")

# per tick: wholeUpdate and scriptUpdate (ns -> ms)
cols, ticks = None, {}
for line in stdout.splitlines():
    if line.startswith("tick,"):
        cols = line.split(",")
    elif cols and re.match(r"t\d+,", line):
        v = line.split(",")
        row = dict(zip(cols, v))
        ticks[int(v[0][1:])] = (int(row["wholeUpdate"]) / 1e6, int(row["scriptUpdate"]) / 1e6)
print(re.search(r"HITCHSETUP.*", log).group(0))
calls = dict(re.findall(r"HITCHCALL (.*?) \| Duration: ([\d.]+)ms", log))
bad = [f"timeout {n}" for n in re.findall(r"HITCH-TIMEOUT (.*)", log)]
# a step passes when 99 of 100 of its ticks run under the budget and none over twice it: a single long tick now
# and then is Lua's collector or Windows (the same work takes 4 ms in one tick and 20 in another; idle ticks shown)
STEPS = re.findall(r"HITCH (.*) (\d+) (\d+)\n", log)
first = min((int(a) for _, a, _ in STEPS), default=0)
idle = [ticks[t][1] for t in range(10, first) if t in ticks]
print(f"idle ticks before the steps: {len(idle)}, worst script {max(idle, default=0):.2f} ms")
# bpgen's own time each tick: its tasks' calls (the mod logs each: tasks.debug) and the step's first call (its tick
# 0). A tick's script time also holds the other mods' (this test mod's Lua state holds megabytes of answers: its
# collector's pauses show up there), so the verdict is on bpgen's own; both are shown
ours = {}
for m in re.finditer(r"(?:TASK|LATER|GCSTEP) (\d+) .*?Duration: ([\d.]+)ms", log):
    ours[int(m.group(1))] = ours.get(int(m.group(1)), 0) + float(m.group(2))
for name, a, _ in STEPS:
    ours[int(a)] = ours.get(int(a), 0) + float(calls.get(name, 0))
print(f"{'step':<46} {'ticks':>5} {'whole':>6} {'script':>7} {'bpgen':>6} {'p99':>6}")
for name, a, b in STEPS:
    rng = range(int(a), int(b) + 1)
    span = [ticks[t] for t in rng if t in ticks]
    worst = max((w for w, _ in span), default=-1)
    script = max((sc for _, sc in span), default=-1)
    own = sorted(ours.get(t, 0) for t in rng)
    mine = own[-1] if own else 0
    p99 = own[min(len(own) - 1, int(len(own) * 0.99))] if own else 0
    over = (p99 > args.budget or mine > 2 * args.budget) and not name.startswith("identity")
    flag = " OVER" if over else ""
    print(f"{name:<46} {len(span):>5} {worst:>6.1f} {script:>7.1f} {mine:>6.1f} {p99:>6.1f}{flag}")
    if over:
        bad.append(name)
        top = sorted(((ours.get(t, 0), t) for t in rng), reverse=True)[:4]
        print("      worst ticks: " + ", ".join(f"{t - int(a)}: {ms:.1f} ms" for ms, t in top))

# the snapshot tool's file (written a part a tick): JSON, every transport belt measured
snap_file = OUT / "bpgen" / "snapshot.json"
if any(n.startswith("snapshot tool") for n, _, _ in STEPS) and snap_file.exists():
    try:
        sn = snapfmt.expand(json.loads(snap_file.read_text(encoding="utf-8")))
        belts = [e for e in sn["entities"] if e["type"] == "transport-belt"]
        flows = [e for e in belts if "flow" in e]
        moving = sum(1 for e in flows if e["flow"] > 0)
        print(f"snapshot.json: {len(sn['entities'])} entities, {len(flows)} of {len(belts)} belts measured, "
              f"{moving} moving")
        if len(flows) != len(belts) or not belts:
            bad.append("snapshot.json flows")
    except ValueError as e:
        print("snapshot.json doesn't read:", e)
        bad.append("snapshot.json")

# the Move tool's area blueprinted a cell a tick, joined, against the whole area at once: the same entities in the
# same places, the same wires
if (OUT / "area-whole.json").exists():
    from bpgen import pieces
    whole = json.loads((OUT / "area-whole.json").read_text())
    joined = entlib.decode(pieces.join(json.loads((OUT / "area-parts.json").read_text())))["blueprint"]
    wb = entlib.decode(whole["blueprint"])["blueprint"]
    ox, oy = whole["offset"]

    def sig(bp, dx, dy):
        ents = {e["entity_number"]: (e["name"], round(e["position"]["x"] + dx, 2), round(e["position"]["y"] + dy, 2),
                                     e.get("direction", 0)) for e in bp.get("entities") or []}
        ws = {tuple(sorted(((ents[a], ca), (ents[b], cb)))) for a, ca, b, cb in bp.get("wires") or []}
        return sorted(ents.values()), ws
    e1, w1 = sig(wb, ox, oy)
    e2, w2 = sig(joined, 0, 0)
    same = e1 == e2 and w1 == w2
    print(f"identity area blueprint: {len(e1)} entities {len(w1)} wires at once, {len(e2)} and {len(w2)} joined: "
          + ("same" if same else "DIFFERENT"))
    if not same:
        bad.append("identity area blueprint")

# what the mod compared itself (built from pieces against built at once)
for line in done.read_text(encoding="utf-8").splitlines():
    if line.startswith("identity"):
        print(line)
        if "DIFFERENT" in line:
            bad.append(line.split(":")[0])

# the reader's snapshot against 0.28's one-go read: the same records
for name in ("base", "wild"):
    new_p, old_p = OUT / f"ident-new-{name}.json", OUT / f"ident-old-{name}.json"
    if not new_p.exists():
        continue
    new, old = snapfmt.expand(json.loads(new_p.read_text())), json.loads(old_p.read_text())

    def norm(sn):
        out = {}
        for k, v in sn.items():
            if isinstance(v, list) and k in ("entities", "obstacles", "fluid_resources"):
                v = sorted(json.dumps(x, sort_keys=True) for x in v)
            out[k] = v
        return out
    a, b = norm(new), norm(old)
    diff = [k for k in set(a) | set(b) if a.get(k) != b.get(k) and (a.get(k) or b.get(k))]
    print(f"identity {name}: {len(new.get('entities') or [])} entities, {len(new.get('obstacles') or [])} obstacles,"
          f" {len(new.get('resources') or {})} resources, {len(new.get('water') or {})} water rows: "
          + ("same as one read" if not diff else f"DIFFERS in {diff}"))
    if diff:
        bad.append(f"identity {name}")
print("FAIL" if bad else "PASS", f"bpgen's time in every step's ticks under {args.budget} ms (99 of 100; none over twice)" +
      (f" (failed: {', '.join(bad)})" if bad else ""))
sys.exit(1 if bad else 0)
