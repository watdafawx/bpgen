"""(mod/test/stutter_test.py) work like a big plan's, for a worker thread: Rust routes for about 6 s.
Imported as harness.stutter_probe through FSE_PYPATH (bpgen's folder)."""
import random
import time

from bpgen import bpgen_fast


def work(_: str = "") -> str:
    rng = random.Random(1)
    w = 260
    blocked = {(x, y) for x in range(w) for y in range(w) if rng.random() < 0.28}
    blocked -= {(0, 0), (w - 1, w - 1)}
    t, n = time.perf_counter(), 0
    while time.perf_counter() - t < 6:
        bpgen_fast.route(blocked, [((0, 0), None)], (w - 1, w - 1), 4, (0, 0, w - 1, w - 1), 5, max_expansions=3_000_000)
        n += 1
    return f"{n} routes in {time.perf_counter() - t:.1f} s"
