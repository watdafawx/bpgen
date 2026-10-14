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
    """what tells snapshots apart: a new measurement has a new tick, another area or surface; variant: the same
    snapshot with entities left out (a section being moved: its old place)"""
    return snap.get("surface"), snap.get("tick"), tuple(snap.get("area") or ()), variant


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
