"""The ground round a player's base, read once and kept while the player places a plan there.

A snapshot (control.lua's snapshot()) is decorated, turned into an extend.Ground and searched for a main bus once;
every placement after it (the window's arrows, Rotate, Redo, the next plan beside the same bus) reuses that work.
extend.place writes into the ground and the bus while it works (tapped belts, failed taps, each lane's load and
belt tier), so each placement gets a scratch copy (scratch()) and the kept one stays as read.

Sites are kept per player (ingame.py), keyed by the snapshot's surface, tick and area: the same snapshot sent
again (control.lua resends its stored one while the spot stays inside it) finds its site, a new one replaces it.
"""
from bpgen import extend

_UNSET = object()


def key_of(snap, variant=None):
    """what tells snapshots apart: surface, tick and area, and what's in it (two measured in the same tick - other
    players, other saves - differ there); variant: the same snapshot with entities left out (a section being moved:
    its old place)"""
    ents = snap.get("entities") or []
    return (snap.get("surface"), snap.get("tick"), tuple(snap.get("area") or ()), variant,
            hash(tuple((e.get("name"), e["position"]["x"], e["position"]["y"], e.get("direction")) for e in ents)))


class Site:
    """a decorated snapshot, its ground and its main bus (both made on first use)"""

    def __init__(self, snap, variant=None):
        self.snap = snap
        self.key = key_of(snap, variant)
        self._ground = None
        self._bus = _UNSET

    @property
    def ground(self):
        if self._ground is None:
            self._ground = extend.Ground(self.snap)
        return self._ground

    @property
    def bus(self):
        """the main bus (extend.find_bus), or None"""
        if self._bus is _UNSET:
            self._bus = extend.find_bus(self.ground)
        return self._bus

    def scratch(self):
        """(ground, bus) for one placement to write into: the kept ones' tables shared where place() only reads
        them, copied where it writes (tapped, the failed-tap memo - its key leaves out the bus side and rate, so it
        holds for one placement only -, the bus's load and its lanes' cap and tiers)"""
        g = object.__new__(extend.Ground)
        g.__dict__.update({k: v for k, v in self.ground.__dict__.items() if k != "no_tap"})
        g.tapped = set(self.ground.tapped)
        b = self.bus
        if b is not None:
            b = dict(b, load={}, lanes=[dict(ln) for ln in b["lanes"]])
        return g, b

    def with_delta(self, built, removed, tick, decorate):
        """this site as the base is now: what was built since (records as the snapshot's, ghosts too; one standing
        where another was - fast-replaced, a ghost built for real, turned - takes its place) and what was removed
        ({name, position}), read at `tick`. The bus found again only when a real belt or pipe changed.
        -> a new Site (this one stays as it was)"""
        def at(e):
            return round(e["position"]["x"], 2), round(e["position"]["y"], 2)
        gone = {(r["name"], at(r)) for r in removed}
        spots = {at(e) for e in built}
        ents = [e for e in self.snap["entities"] if (e["name"], at(e)) not in gone and at(e) not in spots]
        powered = {e.get("network"): e.get("powered") for e in ents if e.get("network") is not None}
        for e in built:
            e = decorate(dict(e))
            if e.get("network") is not None and "powered" not in e:  # (a new pole: as its network's others)
                e["powered"] = bool(powered.get(e["network"]))
            ents.append(e)
        out = Site(dict(self.snap, entities=ents, tick=tick))
        changed = [e for e in list(built) + list(removed)
                   if not e.get("ghost") and (e.get("type") in extend.BELT_TYPES + ("pipe", "pipe-to-ground")
                                              or "belt" in e["name"] or "splitter" in e["name"] or "pipe" in e["name"])]
        if not changed and self._bus is not _UNSET:
            out._bus = self._bus
        return out
