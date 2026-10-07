"""Mark inputs and outputs in the blueprint with constant combinators (visible in alt-mode while building).

Each input belt gets a combinator beside the tile where you connect it, set to that belt's items with the count
= items per minute it needs; its description says the same in words. The output end gets one too. Tiles in front
of any connection point are never used, so labels don't block belts or pipes.
"""
import math

from bpgen.chain import _sizer, _tiles

COMBINATOR = "constant-combinator"


def _signal(name, fluids):
    return "fluid" if name in fluids else "item"


def _combinator(tile, signals, text, fluids):
    filters = [{"index": i, "type": _signal(n, fluids), "name": n, "quality": "normal", "comparator": "=",
                "count": max(1, round(per_min))} for i, (n, per_min) in enumerate(signals, 1)]
    return {"name": COMBINATOR, "position": {"x": tile[0] + 0.5, "y": tile[1] + 0.5},
            "control_behavior": {"sections": {"sections": [{"index": 1, "filters": filters}]}},
            "player_description": text}


def add_labels(data, entities, sources, sinks, output, output_rate):
    """-> (entities + label combinators, sources, sinks, text description), shifted so the top-left is (0, 0)"""
    size_of = _sizer(data)
    used = set()
    for e in entities:
        used |= _tiles(e, size_of)
    fluids = set(data.raw.get("fluid", {}))
    # connection points: the tile just outside each input (where your belt/pipe comes in) and past the output
    approach = set()
    for s in sources:
        x, y = math.floor(s["position"]["x"]), math.floor(s["position"]["y"])
        approach.add((x - 1, y) if s["kind"] == "belt" else (x, y))
    for k in sinks:
        approach.add((math.floor(k["position"]["x"]) + 1, math.floor(k["position"]["y"])))

    def spot(prefer):
        for t in prefer:
            if t not in used and t not in approach:
                used.add(t)
                return t
        return None

    labels, lines = [], []
    for s in sources:
        x, y = math.floor(s["position"]["x"]), math.floor(s["position"]["y"])
        if s["kind"] == "belt":
            lanes = s["lanes"]
            rates = s.get("rates") or {}
            items = [n for n in dict.fromkeys(lanes) if n]
            where = "both lanes" if len(items) == 1 and lanes[0] == lanes[1] else \
                " / ".join(f"{side} lane {n or 'empty'}" for side, n in zip(("left", "right"), lanes))
            text = "bpgen input: " + ", ".join(items) + f" ({where})" + \
                "".join(f", {n} {rates[n]:.2f}/s" for n in items if n in rates)
            t = spot([(x - 1, y + 1), (x - 1, y - 1), (x, y + 1), (x, y - 1), (x - 2, y + 1), (x - 2, y - 1)])
            signals = [(n, rates.get(n, 0) * 60) for n in items]
        else:
            rate = (s.get("rates") or {}).get(s["fluid"], 0)
            text = f"bpgen input: {s['fluid']} (pipe)" + (f", {rate:.1f}/s" if rate else "")
            t = spot([(x, y + 1), (x, y - 1), (x - 1, y + 1), (x - 1, y - 1)])
            signals = [(s["fluid"], rate * 60)]
        lines.append(text[len("bpgen "):])
        if t:
            labels.append(_combinator(t, signals, text, fluids))
    for k in sinks:
        x, y = math.floor(k["position"]["x"]), math.floor(k["position"]["y"])
        what = k.get("item") or output
        text = f"bpgen output: {what}" + (f" {output_rate:.2f}/s" if what == output and output_rate else "")
        lines.append(text[len("bpgen "):])
        t = spot([(x, y - 1), (x, y + 1), (x + 1, y - 1), (x + 1, y + 1)])
        if t and what:
            labels.append(_combinator(t, [(what, output_rate * 60 if what == output else 0)], text, fluids))
    # a blueprint's origin is its top-left corner and labels can sit outside the old box: shift back to (0, 0)
    ents = entities + labels
    mx = min(math.floor(e["position"]["x"] - size_of(e["name"], e.get("direction", 0))[0] / 2 + 0.01) for e in ents)
    my = min(math.floor(e["position"]["y"] - size_of(e["name"], e.get("direction", 0))[1] / 2 + 0.01) for e in ents)

    def move(items):
        return [dict(i, position={"x": i["position"]["x"] - mx, "y": i["position"]["y"] - my}) for i in items]
    return move(ents), move(sources), move(sinks), "\n".join(lines)
