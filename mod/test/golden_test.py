"""Golden snapshots of plans, no game: every request in golden/requests.json (a plain line, each item count, fluids,
furnaces, by-products, modules and beacons, quality, rates, belt tiers, direct insertion, chains, More lines) is planned
as the window sends it (ingame.plan on Service("vanilla")), and its summary, notes, sources, sinks, entities (sorted by
name and position) and wires are compared with golden/expected/<name>.json. Any change to what a planner produces
shows up as a diff; if it is meant, rewrite the snapshots and read the git diff.
A request with "test_recipes": true plans with many_items.py's 7-11 item recipes in the data (no vanilla recipe has
more than 6 items); one with "refused": true snapshots the error it gets instead of a plan.
python mod/test/golden_test.py [NAME ...] [--update]"""
import contextlib
import difflib
import json
import sys
from pathlib import Path

# a note about the test machine, not the plan (pack.up_to_date() reads the user's mod list)
ENV_NOTE = "your mods changed since bpgen last read them"

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from bpgen import base, ingame, planner  # noqa: E402
from bpgen.service import Service  # noqa: E402
sys.path.insert(0, str(HERE))
import many_items  # noqa: E402

REQUESTS = HERE / "golden" / "requests.json"
EXPECTED = HERE / "golden" / "expected"
LISTS = ("entities", "wires")  # (kept one element a line, so a diff reads as entities added, moved or changed)
TIMING = ("seconds", "time", "elapsed")


def clean(v):
    """floats rounded, timing fields dropped"""
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items() if not any(t in k for t in TIMING)}
    if isinstance(v, list):
        return [clean(x) for x in v]
    return v


def key(e):
    p = e["position"]
    return f"{e['name']}@{p['x']},{p['y']}"


def entities(bp):
    """a blueprint's entities sorted by (name, x, y), numbers dropped; its wires ([a, connector, b, connector]) as
    entity keys"""
    ents = bp.get("entities") or []
    order = sorted(ents, key=lambda e: (e["name"], e["position"]["x"], e["position"]["y"]))
    by_number = {e["entity_number"]: key(e) for e in ents}
    flat = [{k: v for k, v in e.items() if k != "entity_number"} for e in order]
    wires = sorted([by_number[a], ca, by_number[b], cb] for a, ca, b, cb in bp.get("wires") or [])
    return clean(flat), wires


def snapshot(out):
    """a plan answer -> what the snapshot keeps"""
    case = (out.get("test") or {}).get("case") or {}
    snap = {"label": out.get("label"), "mode": out.get("mode"), "output": out.get("output"),
            "expected": out.get("expected"), "summary": out.get("summary"),
            "notes": [n for n in out.get("notes") or [] if ENV_NOTE not in n],
            "sections": out.get("sections"), "absolute": out.get("absolute"),
            "sources": case.get("sources"), "sinks": case.get("sinks")}
    bp = base._decode(out["blueprint"])
    if "blueprint_book" in bp:
        for p in bp["blueprint_book"]["blueprints"]:
            label = p["blueprint"].get("label")
            snap[f"entities[{label}]"], snap[f"wires[{label}]"] = entities(p["blueprint"])
    else:
        snap["entities"], snap["wires"] = entities(bp["blueprint"])
    return clean(snap)


def render(snap):
    lines = ["{"]
    items = list(snap.items())
    for i, (k, v) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        if k.split("[")[0] in LISTS and v:
            lines.append(f" {json.dumps(k)}: [")
            lines += [f"  {json.dumps(x, sort_keys=True)}{',' if j < len(v) - 1 else ''}" for j, x in enumerate(v)]
            lines.append(f" ]{comma}")
        else:
            lines.append(f" {json.dumps(k)}: {json.dumps(v, sort_keys=True)}{comma}")
    return "\n".join(lines + ["}"]) + "\n"


def _duplicates(out):
    """(name, x, y) placed more than once in any print of the answer"""
    try:
        bp = base._decode(out["blueprint"])
    except Exception:  # noqa: BLE001 (no print: the caller reports that)
        return []
    prints = [p["blueprint"] for p in bp["blueprint_book"]["blueprints"]] if "blueprint_book" in bp else [bp["blueprint"]]
    dup = []
    for p in prints:
        seen = set()
        for e in p.get("entities") or []:
            k = (e["name"], e["position"]["x"], e["position"]["y"])
            if k in seen:
                dup.append(k)
            seen.add(k)
    return dup


def main():
    args = sys.argv[1:]
    update = "--update" in args
    names = [a for a in args if not a.startswith("--")]
    requests = json.loads(REQUESTS.read_text(encoding="utf-8"))
    unknown = [n for n in names if n not in requests]
    if unknown:
        sys.exit(f"no such request: {', '.join(unknown)}")
    s = Service("vanilla")
    planner.configure(s.data)
    ingame._service = s
    EXPECTED.mkdir(parents=True, exist_ok=True)
    failed = []
    for name in names or list(requests):
        req = dict(requests[name])
        tests, refused = req.pop("test_recipes", False), req.pop("refused", False)
        with many_items.added(s.data) if tests else contextlib.nullcontext():
            out = json.loads(ingame.plan(json.dumps(req)))
        if refused:  # (what it says instead of planning)
            if not out.get("error"):
                failed.append(name)
                print(f"FAIL {name}: planned, expected a refusal")
                continue
            out = {"error": out["error"]}
        dup = _duplicates(out)
        if dup:  # (two entities of a name on one spot: a layout bug whatever the snapshot says)
            print(f"FAIL {name}: entities on top of each other: {dup[:4]}")
            failed.append(name)
            continue
        if not refused and (out.get("error") or out.get("measure") or not out.get("blueprint")):
            failed.append(name)
            print(f"FAIL {name}: no plan ({'needs a measurement' if out.get('measure') else out.get('error')})")
            continue
        text = render(clean(out) if refused else snapshot(out))
        path = EXPECTED / f"{name}.json"
        if update:
            path.write_text(text, encoding="utf-8", newline="\n")
            continue
        if not path.exists():
            failed.append(name)
            print(f"FAIL {name}: no snapshot yet (run with --update)")
            continue
        want = path.read_text(encoding="utf-8")
        if want != text:
            failed.append(name)
            diff = [l for l in difflib.unified_diff(want.splitlines(), text.splitlines(), "expected", "now", n=0, lineterm="")
                    if not l.startswith(("---", "+++", "@@"))]
            print(f"FAIL {name}: {len(diff)} differing lines (- expected, + now)")
            for l in diff[:30]:
                print("  " + (l if len(l) < 240 else l[:237] + "..."))
            if len(diff) > 30:
                print(f"  ... {len(diff) - 30} more")
    if update:
        print(f"wrote {len(names or requests)} snapshots to {EXPECTED}")
        stale = [] if names else [p.name for p in EXPECTED.glob("*.json") if p.stem not in requests]
        for n in stale:
            (EXPECTED / n).unlink()
            print("removed stale", n)
        return 0
    if not names:
        stale = [p.name for p in EXPECTED.glob("*.json") if p.stem not in requests]
        if stale:
            failed += stale
            print("FAIL snapshots without a request:", ", ".join(stale))
    if failed:
        print(f"{len(failed)} of {len(names or requests)} failed")
        return 1
    print(f"ok: {len(names or requests)} plans match their snapshots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
