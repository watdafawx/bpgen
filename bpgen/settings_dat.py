"""Read Factorio's mod-settings.dat (a binary PropertyTree).

Layout: version (4 x uint16) + one bool, then a tree: type byte, any-type flag byte, payload.
Types: 0 none, 1 bool, 2 double, 3 string, 4 list, 5 dictionary, 6 signed int64, 7 unsigned int64.
Strings: empty-flag byte, then a length (one byte, or 0xFF followed by uint32) and UTF-8 bytes.
Lists/dictionaries: uint32 count, then (key string, tree) pairs.
"""
import struct


class _Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, fmt):
        v = struct.unpack_from("<" + fmt, self.data, self.pos)
        self.pos += struct.calcsize("<" + fmt)
        return v[0] if len(v) == 1 else v

    def string(self):
        if self.take("?"):
            return ""
        n = self.take("B")
        if n == 255:
            n = self.take("I")
        s = self.data[self.pos:self.pos + n].decode("utf-8", "replace")
        self.pos += n
        return s

    def tree(self):
        kind = self.take("B")
        self.take("?")  # any-type flag
        if kind == 0:
            return None
        if kind == 1:
            return self.take("?")
        if kind == 2:
            return self.take("d")
        if kind == 3:
            return self.string()
        if kind in (4, 5):
            items = [(self.string(), self.tree()) for _ in range(self.take("I"))]
            return dict(items) if kind == 5 else [v for _, v in items]
        if kind == 6:
            return self.take("q")
        if kind == 7:
            return self.take("Q")
        raise ValueError(f"unknown property tree type {kind} at byte {self.pos}")


def read(path):
    r = _Reader(open(path, "rb").read())
    r.take("4H")  # game version
    r.take("?")
    return r.tree()
