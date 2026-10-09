"""Offline check of the bus design (bpgen/busdesign.py) on a made-up snapshot: iron, copper and coal patches, the
player west of them. Fails on overlapping entities, unrouted trunks or a wrong lane count; writes the blueprint
string to run/busdesign.txt to paste in game.
    python harness/busdesign_check.py [north|east|south|west]"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bpgen import extend, planner  # noqa: E402
from bpgen.service import Service  # noqa: E402


def runs(x1, x2, y1, y2):
    return {str(y): [[x1, x2]] for y in range(y1, y2 + 1)}


def main():
    direction = sys.argv[1] if len(sys.argv) > 1 else "north"
    snap = {"area": [-150, -200, 250, 200], "entities": [], "obstacles": [], "water": {},
            "resources": {"iron-ore": runs(100, 139, -60, -11), "copper-ore": runs(100, 129, 20, 49),
                          "coal": runs(160, 179, -20, -6)}}
    patches = [[95, -65, 145, -5], [95, 15, 135, 55], [155, -25, 185, 0]]
    s = Service("pack")
    out = s.plan({"mode": "busdesign", "snapshot": snap, "patches": patches, "origin": {"x": 0, "y": 0},
                  "direction": direction, "belt": "transport-belt", "furnace": "electric-furnace",
                  "drill": "electric-mining-drill", "group": 4, "gap": 4, "length": 30})
    for n in out["notes"]:
        print("note:", n)
    seen, bad = {}, []
    for e in out["entities"]:
        for t in extend.footprint(e):
            if t in seen:
                bad.append((t, seen[t]["name"], e["name"]))
            seen[t] = e
    assert not bad, f"{len(bad)} overlapping tiles, first: {bad[:3]}"
    assert not out["inputs"], f"trunks not routed: {out['inputs']}"
    poles = [(e["position"]["x"], e["position"]["y"]) for e in out["entities"] if e["name"] == planner.POLE]
    group = {p: p for p in poles}  # (pole networks: wire reach)

    def root(p):
        while group[p] != p:
            p = group[p]
        return p
    for i, p in enumerate(poles):
        for q in poles[i + 1:]:
            if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 <= planner.POLE_REACH ** 2:
                group[root(p)] = root(q)
    nets = len({root(p) for p in poles})
    print("pole networks:", nets)
    assert nets == 1, f"{nets} pole networks: the drills, smelters and bus end should be one"
    items = [ln["item"] for ln in out["lanes"]]
    print("lanes:", items)
    assert items.count("iron-plate") >= 2 and "copper-plate" in items and "coal" in items, items
    (ROOT / "run").mkdir(exist_ok=True)
    (ROOT / "run" / "busdesign.txt").write_text(out["blueprint"], encoding="utf-8")
    print(f"ok: {len(out['entities'])} entities, box {out['box']}")


if __name__ == "__main__":
    main()
