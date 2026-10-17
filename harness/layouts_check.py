"""A line's smallest layout in a headless game (layouts.py, the window's layout picker): the tightest layout bpgen
offers must still make what the line is planned for. Each request is planned as the window asks (layouts_test's
requests), its smallest layout built with its inputs fed and its output drained, and measured after a warmup:
PASS at >= 97% of the planned rate.
python harness/layouts_check.py [NAME ...] [--minutes M]"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mod" / "test"))
from bpgen import harness, ingame, planner  # noqa: E402
from bpgen.calibrate import BONUS_LEVELS  # noqa: E402
from bpgen.service import Service  # noqa: E402
import layouts_test  # noqa: E402

NAMES = ["plastic-petroleum", "adv-circuit-circuit-cable", "inserter-gear-circuit", "red-science-gear"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--minutes", type=float, default=5)
    args = ap.parse_args()
    s = Service("vanilla")
    planner.configure(s.data)
    ingame._service = s
    cases, want = [], {}
    for name in args.names or NAMES:
        req = layouts_test.REQUESTS[name]
        st = {}
        out = s.request(s.params(json.loads(json.dumps(req))), measure=False, keep=st)
        lays = out.get("layouts") or []
        i = min(range(len(lays)), key=lambda k: lays[k]["area"])
        last = st["layouts"][i]["last"]
        ents = [{k: v for k, v in e.items() if k not in ("type", "w", "h")} for e in last["entities"]]
        product = (s.data.raw["recipe"][req["recipe"]].get("results") or [{}])[0]["name"]
        want[name] = (product, req["rate_per_min"] / 60, lays[i])
        cases.append({"id": name, "blueprint": planner.blueprint_string(ents, name), "sources": last["sources"],
                      "sinks": last["sinks"]})
    ticks = int(args.minutes * 3600)
    res = harness.run({"warmup": ticks, "measure": ticks, "force": BONUS_LEVELS["none"], "cases": cases})
    by_id = {c["id"]: c for c in res["cases"]}
    ok = 0
    for name, (product, rate, lay) in want.items():
        c = by_id[name]
        got = c["rates"].get(product, 0)
        ratio = got / rate if rate else 0
        status = "PASS" if ratio >= 0.97 and not c["errors"] else "FAIL"
        ok += status == "PASS"
        print(f"{status} {name}: {lay['kind']} {lay['w']}x{lay['h']} ({lay['area']} tiles, {lay['machines']} machines): "
              f"{product} {got:.3f}/s measured vs {rate:.3f}/s planned ({ratio:.1%})")
        for err in c["errors"]:
            print("  harness:", err)
    print(f"{ok}/{len(want)} passed")
    return 0 if ok == len(want) else 1


if __name__ == "__main__":
    sys.exit(main())
