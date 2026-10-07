"""Measure real inserter throughput in the harness, per inserter x belt x direction x research bonuses.

belt->chest stands in for belt->machine, chest->belt for machine->belt. Results are cached per pack in a JSON
table keyed "inserter|belt|direction|bonus-key"; only missing entries are measured.
"""
import contextvars
import json
import math
from pathlib import Path

from bpgen import harness
from bpgen.data import Data

ROOT = Path(__file__).resolve().parent.parent
NORTH, EAST, SOUTH = 0, 4, 8

# named levels for the vanilla CLI
BONUS_LEVELS = {
    "none": {"inserter_stack_size_bonus": 0, "bulk_inserter_capacity_bonus": 0, "belt_stack_size_bonus": 0},
    "max": {"inserter_stack_size_bonus": 2, "bulk_inserter_capacity_bonus": 11, "belt_stack_size_bonus": 0},
}
ZERO = BONUS_LEVELS["none"]


def bonus_key(b):
    return "i{}-b{}-s{}".format(b.get("inserter_stack_size_bonus", 0), b.get("bulk_inserter_capacity_bonus", 0),
                                b.get("belt_stack_size_bonus", 0))


def is_bulk(data, inserter):
    return bool(data.raw["inserter"][inserter].get("bulk"))


def level_key(data, inserter, bonuses):
    """an inserter's hand only depends on one research bonus: bulk (and stack) inserters on the bulk bonus,
    the rest on the inserter bonus - so calibration is keyed on that one value"""
    b = bonuses or {}
    if is_bulk(data, inserter):
        return f"b{b.get('bulk_inserter_capacity_bonus', 0)}"
    return f"i{b.get('inserter_stack_size_bonus', 0)}"


def research_levels(data):
    """bonus values research can reach: (inserter bonus values, bulk bonus values)"""
    techs = data.raw.get("technology", {})
    steps = {"inserter-stack-size-bonus": [], "bulk-inserter-capacity-bonus": []}
    depth = {}

    def d(name, seen=()):
        if name not in depth:
            t = techs.get(name, {})
            depth[name] = 1 + max((d(p, seen + (name,)) for p in t.get("prerequisites") or [] if p not in seen), default=0)
        return depth[name]
    for name, t in techs.items():
        for e in t.get("effects") or []:
            if e.get("type") in steps:
                steps[e["type"]].append((d(name), e.get("modifier", 0)))
    ins = sum(m for _, m in steps["inserter-stack-size-bonus"])
    bulk, total = {0}, 0
    for _, m in sorted(steps["bulk-inserter-capacity-bonus"]):
        total += m
        bulk.add(total)
    return list(range(int(ins) + 1)), sorted(int(x) for x in bulk)


def _vec(v):
    return (v[0], v[1]) if isinstance(v, list) else (v["x"], v["y"])


def reach(data, inserter):
    """(pickup, drop) distance in tiles for a straight inserter, else None"""
    p = data.raw["inserter"][inserter]
    (px, py), (dx, dy) = _vec(p["pickup_position"]), _vec(p["insert_position"])
    if px != 0 or dx != 0:
        return None
    return round(abs(py)), round(abs(dy))


def template_inserters(data, names=None, tiles=1):
    """electric inserters reaching exactly `tiles` straight ahead on both sides (what the line template uses)"""
    out = []
    for n, p in data.raw["inserter"].items():
        if names is not None and n not in names:
            continue
        if p.get("hidden") or p.get("energy_source", {}).get("type") != "electric" or "connector" in n:
            # "connector": Quantum Logistics network ports use the inserter type but aren't inserters
            continue
        if reach(data, n) == (tiles, tiles):
            out.append(n)
    return sorted(out)


def _cases(inserter, belt, force_name, key, item, tiles=1):
    belt_row = [{"name": belt, "position": {"x": x + 0.5, "y": 0.5}, "direction": EAST} for x in range(10)]
    iy = 0.5 + tiles
    return [
        {   # belt -> chest: inserter faces north (picks from the belt above it)
            "id": f"{inserter}|{belt}|belt_to_machine|{key}", "force_name": force_name,
            "entities": belt_row + [{"name": inserter, "position": {"x": 5.5, "y": iy}, "direction": NORTH}],
            "sources": [{"kind": "belt", "position": {"x": 0.5, "y": 0.5}, "lanes": [item, item]}],
            "sinks": [{"kind": "chest", "position": {"x": 5.5, "y": iy + tiles}}],
        },
        {   # chest -> belt: inserter faces south (picks from the chest below), belt drained at its end
            "id": f"{inserter}|{belt}|machine_to_belt|{key}", "force_name": force_name,
            "entities": belt_row + [{"name": inserter, "position": {"x": 5.5, "y": iy}, "direction": SOUTH}],
            "sources": [{"kind": "chest", "position": {"x": 5.5, "y": iy + tiles}, "item": item}],
            "sinks": [{"kind": "belt", "position": {"x": 9.5, "y": 0.5}}],
        },
    ]


# in game (bpgen.ingame through the fnative py plugin) nothing is measured: a headless game would be a second
# process and minutes of waiting. A plan needing an unmeasured setup fails with NotMeasured instead.
NO_MEASURE = contextvars.ContextVar("no_measure", default=False)


class NotMeasured(Exception):
    """`spec`: the harness run that would measure what's missing (bpgen.ingame runs it in the game itself)"""
    def __init__(self, msg, spec=None):
        super().__init__(msg)
        self.spec = spec


SPEC_CELL = {"w": 16, "h": 8, "cols": 40}


def store(table_path, cases):
    """harness results of calibration cases -> the table (what calibrate() does with a headless run's)"""
    table_path = Path(table_path)
    table = json.loads(table_path.read_text()) if table_path.exists() else {}
    for c in cases:
        if c.get("errors"):
            print("  calibration error", c["id"], c["errors"])
        table[c["id"]] = round(sum((c.get("rates") or {}).values()), 3)
    table_path.parent.mkdir(exist_ok=True)
    table_path.write_text(json.dumps(table, indent=1, sort_keys=True))
    return table


def calibrate(table_path, mod_dir, inserters, belts, bonus_sets, item="iron-plate", data=None, progress=None,
              cancel=None):
    """measure whatever is missing from the table for these inserters/belts/bonus sets; returns the table"""
    table_path = Path(table_path)
    table = json.loads(table_path.read_text()) if table_path.exists() else {}
    forces, cases, seen = {}, [], set()
    for bonuses in bonus_sets:
        for i in inserters:
            key = level_key(data, i, bonuses)
            force_name = "cal-" + key
            for b in belts:
                tiles = reach(data, i)[0]
                missing = [c for c in _cases(i, b, force_name, key, item, tiles) if c["id"] not in table and c["id"] not in seen]
                if missing:
                    seen.update(c["id"] for c in missing)
                    forces[force_name] = {"inserter_stack_size_bonus": int(key[1:])} if key[0] == "i" else \
                        {"bulk_inserter_capacity_bonus": int(key[1:])}
                    cases += missing
    spec = {"warmup": 300, "measure": 1800, "forces": forces, "cases": cases, "cell": SPEC_CELL}
    if cases and NO_MEASURE.get():
        raise NotMeasured(f"{len(cases)} inserter setups ({', '.join(sorted({c['id'].split('|')[0] for c in cases}))}"
                          f" on {', '.join(sorted({c['id'].split('|')[1] for c in cases}))}) aren't measured yet",
                          spec)
    if cases:
        print(f"calibrating {len(cases)} inserter setups ...", flush=True)

        def say(m):
            if progress:
                progress(f"measuring {len(cases)} inserter setups (once per belt/research level) - {m}")
        res = harness.run(spec, mod_dir=mod_dir, progress=say, cancel=cancel)
        table = store(table_path, res["cases"])
    return table


def all_levels(data):
    """bonus sets covering every research level on both axes"""
    ins, bulk = research_levels(data)
    return [{"inserter_stack_size_bonus": a} for a in ins] + [{"bulk_inserter_capacity_bonus": b} for b in bulk]


def main():
    data = Data.load(ROOT / "data" / "vanilla-dump.json")
    belts = [n for n, p in data.raw["transport-belt"].items() if not p.get("hidden")]
    inserters = template_inserters(data) + template_inserters(data, tiles=2)
    table = calibrate(ROOT / "data" / "calibration.json", ROOT / "mods" / "vanilla", inserters, belts,
                      list(BONUS_LEVELS.values()), data=data)
    for k, v in sorted(table.items()):
        print(f"{v:8.3f}  {k}")


if __name__ == "__main__":
    main()
