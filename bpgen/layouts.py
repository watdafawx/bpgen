"""Several layouts for one Line plan with ingredients made in the blueprint, for the window's layout picker.

A line with "make" ingredients lays out three ways: by direct insertion (one made ingredient, recipes that fit), as a
chain (made items on the final block's own belts) or composed (each recipe its own block, belts routed between them:
compose_base). Service._plan picks the first that fits, as before; collect() then plans the others too, and the
composed one under several compose settings (base.routed's variants: tighter channels and slack, other column
heights and orders, the main-bus layout), scores them (score: area, a long thin shape costs extra, belt and pipe
tiles), drops the ones that are the same, and keeps up to SHOWN: the plan's own first, then the best of the rest.
Everything here runs in the Python worker (the game asks and waits for one answer)."""
import time

LAYOUT_SECONDS = 8   # composed variants after the first that routes (base.routed)
COLLECT_SECONDS = 20  # the other kinds (direct, chain, composed) together
SHOWN = 4
KINDS = ("direct", "chain", "composed")
BELTS = ("transport-belt", "underground-belt", "splitter")
PIPES = ("pipe", "pipe-to-ground")


def _kind_of(data, name):
    for t in BELTS + PIPES:
        if name in data.raw.get(t, {}):
            return t
    return None


def stats(data, ents):
    """{w, h, area, belts, pipes, machines} of a print's entities (belts: belt, underground and splitter tiles)"""
    if not ents:
        return {"w": 0, "h": 0, "area": 0, "belts": 0, "pipes": 0, "machines": 0}
    xs = [e["position"]["x"] for e in ents]
    ys = [e["position"]["y"] for e in ents]
    w, h = round(max(xs) - min(xs)) + 1, round(max(ys) - min(ys)) + 1
    belts = pipes = machines = 0
    for e in ents:
        k = _kind_of(data, e["name"])
        if k in BELTS:
            belts += 1
        elif k in PIPES:
            pipes += 1
        elif e.get("recipe"):
            machines += 1
    return {"w": w, "h": h, "area": w * h, "belts": belts, "pipes": pipes, "machines": machines}


def score(data, ents, st=None):
    """smaller is better: the area, more for a shape over twice as long as wide, plus the belt and pipe tiles"""
    st = st or stats(data, ents)
    long_, short = max(st["w"], st["h"]), max(1, min(st["w"], st["h"]))
    return st["area"] * (1 + 0.1 * max(0.0, long_ / short - 2)) + st["belts"] + st["pipes"]


def signature(ents):
    return tuple(sorted((e["name"], e["position"]["x"], e["position"]["y"], e.get("direction", 0)) for e in ents))


def _composed_variants(service, out):
    """a composed answer (Service.plan_base's) -> one answer per layout its routed section has in "variants" (the
    one chosen included), each with that layout as its connected print"""
    from bpgen import ents as entlib, planner
    secs = out.get("sections") or []
    if not secs or secs[0]["kind"] != "routed":
        return [out]
    res = secs[0]["result"]
    variants = res.get("variants") or []
    if not variants:
        return [out]
    old = entlib.decode(res["blueprint"])["blueprint"]
    label, head = old.get("label", "connected"), (old.get("description") or "").split("\n")[0]
    answers = []
    for v in variants:
        ents = [service.decorate(e) for e in v["ents"]]
        bp = planner.blueprint_string(v["ents"], label, description=head + "\n" + (v["desc"] or ""))
        r = dict(res, entities=ents, sources=v["sources"], sinks=v["sinks"], blueprint=bp, variants=None,
                 settings=v["settings"])
        o = dict(out, entities=ents, sources=v["sources"], sinks=v["sinks"],
                 sections=[dict(secs[0], result=r)] + list(secs[1:]),
                 blueprint=entlib.replace_print(out["blueprint"], 0, bp, label))
        answers.append(o)
    return answers


def _entities(out):
    return out.get("entities") or []


def collect(service, params, make, out, kind, progress=None, cancel=None):
    """the plan's answer `out` (laid out as `kind`) and the other ways to lay it out -> up to SHOWN candidates
    [{kind, w, h, area, belts, pipes, machines, score, out}], the plan's own (out itself) first"""
    from bpgen import planner
    data = service.data
    found = [(kind, dict(out))]  # (kind, answer): the plan's own first (a copy: out gets the list), then its other
    # composed layouts
    if kind == "composed":
        found += [(kind, x) for x in _composed_variants(service, out)]
    give_up = time.monotonic() + COLLECT_SECONDS
    for k in KINDS:
        if k == kind or (k == "direct" and len(make) != 1):
            continue
        if k != "composed" and any(i in data.raw.get("fluid", {}) for i in make):
            continue  # (a fluid made here: only the composer pipes it)
        if time.monotonic() > give_up or (cancel and cancel()):
            break
        if progress:
            progress(f"another layout: {k}")
        try:
            if k == "composed":
                o = service.plan_fluid_chain(params, make, cancel=cancel, layouts=True)
                found += [(k, x) for x in _composed_variants(service, o)]
            else:
                found.append((k, service.line_as(params, make, k)))
        except planner.PlanError:
            continue
    cands, seen = [], set()
    for k, o in found:
        ents = _entities(o)
        if not ents:
            continue
        sig = signature(ents)
        if sig in seen:
            continue
        seen.add(sig)
        st = stats(data, ents)
        cands.append(dict(st, kind=k, score=round(score(data, ents, st), 1), out=o))
    if not cands:
        return []
    mine, rest = cands[0], sorted(cands[1:], key=lambda c: c["score"])
    shown = [mine]
    # the best of each other kind first (so every way it lays out is offered), then other shapes by score

    def unlike(c):
        return all(abs(c["area"] - s["area"]) > 0.1 * s["area"] or
                   abs(c["w"] / max(1, c["h"]) - s["w"] / max(1, s["h"])) > 0.1 * s["w"] / max(1, s["h"])
                   for s in shown)
    for k in KINDS:
        best = next((c for c in rest if c["kind"] == k and c not in shown), None)
        if best and len(shown) < SHOWN and unlike(best):
            shown.append(best)
    for c in rest:
        if len(shown) >= SHOWN:
            break
        if c not in shown and unlike(c):
            shown.append(c)
    return shown[:1] + sorted(shown[1:], key=lambda c: c["score"])
