"""bpgen's regression runner: every test script in mod/test/ and harness/ as a case in one table (CASES below).
Run from bpgen/:
  python mod/test/run.py CASE [CASE ...]    those cases, side by side (-j 4)
  python mod/test/run.py --changed [CASE ...]   the cases whose script (or golden/ files) differ from git HEAD (new
                                            ones too), plus any named
  python mod/test/run.py --nogame           only the cases that need no game (Service("vanilla") and the planners): ~20 s
  python mod/test/run.py --all              every case; the real-client (gui) ones are skipped while factorio.exe runs
  python mod/test/run.py --list             the table
Each case runs its script as a process; the exit code is its verdict. Prints name, pass/fail and seconds, and the last
20 lines of a failing case's output; the exit code is 1 when any case failed. Game cases start a headless Factorio
(they share run/, so they take turns; base and stutter have a write-data of their own and run beside them), gui cases
open a game window: not while you play. A case that needs another (needs) runs it first."""
import argparse
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # (bpgen/)
T, H = "mod/test/", "harness/"

# case: script (relative to bpgen/), args, game (starts a headless Factorio), gui (a real client window),
#       needs (cases to run first: the files they leave in run/), own_data (its own write-data: runs beside other
#       games), watch (more paths whose change counts for --changed), timeout (seconds), known_fail (fails at HEAD
#       for the reason given: reported as XFAIL, not counted; a pass is reported as XPASS)
CASES = {
    # no game: Service("vanilla") and the planners
    "golden": {"script": T + "golden_test.py", "watch": [T + "golden"]},
    "bus-extend": {"script": T + "bus_extend_test.py"},
    "bus-feed": {"script": T + "bus_feed_test.py"},
    "direct": {"script": T + "direct_test.py"},
    "fit-bus": {"script": T + "fit_bus_test.py"},
    "hand": {"script": T + "hand_test.py"},
    "lines": {"script": T + "lines_test.py"},
    "mall-bus": {"script": T + "mall_bus_test.py"},
    "mall-loop": {"script": T + "mall_loop_test.py"},
    "move": {"script": T + "move_test.py"},
    "power": {"script": T + "power_test.py"},
    "ports": {"script": T + "ports_test.py"},
    "inserter-limit": {"script": T + "inserter_limit_test.py"},
    "rowsolve": {"script": T + "rowsolve_test.py"},
    "refit": {"script": T + "refit_test.py"},
    "ship": {"script": T + "ship_test.py"},
    "site": {"script": T + "site_test.py"},
    "tiers": {"script": T + "tiers_test.py"},
    "busdesign-check": {"script": H + "busdesign_check.py"},
    "addbus-check": {"script": H + "addbus_check.py"},
    "blue-check": {"script": H + "blue_check.py"},
    "mall-tier-check": {"script": H + "mall_tier_check.py"},
    "oil-check": {"script": H + "oil_check.py"},
    # a headless game
    "comp": {"script": T + "comp_test.py", "game": True},  # (the mod's exports: a setup built, snapshotted)
    "base": {"script": T + "base_test.py", "game": True, "own_data": True, "timeout": 1500},
    "extend": {"script": T + "extend_test.py", "args": ["logistic-science-pack", "30", "8"], "game": True},
    "extend-bus": {"script": T + "extend_test.py", "args": ["logistic-science-pack", "30", "8", "bus"], "game": True},
    "sandbox": {"script": T + "sandbox_test.py", "game": True},
    "stutter": {"script": T + "stutter_test.py", "game": True, "own_data": True},  # (through fse: needs it built)
    "addbus-run": {"script": H + "addbus_check.py", "args": ["--run"], "game": True},
    "blue-run": {"script": H + "blue_check.py", "args": ["--run", "--quick"], "game": True},
    "mall-tier-run": {"script": H + "mall_tier_check.py", "args": ["--run"], "game": True},
    "oil-run": {"script": H + "oil_check.py", "args": ["--run"], "game": True},
    "many-items-run": {"script": H + "many_items_check.py", "game": True},  # (7+ item test recipes, a mod of their own)
    "botmall": {"script": H + "botmall_test.py", "args": ["vanilla", "3"], "game": True},
    "busdesign-run": {"script": H + "busdesign_test.py", "args": ["vanilla", "3"], "game": True},
    "fit-bus-run": {"script": H + "fit_bus_run.py", "game": True, "needs": ["fit-bus"]},
    "fit-place": {"script": H + "fit_place_test.py", "game": True, "needs": ["fit-bus"]},
    "grid-mall": {"script": H + "grid_mall_run.py", "args": ["12"], "game": True},
    "ship-run": {"script": H + "ship_run.py", "game": True},
    "tiers-paste": {"script": H + "tiers_paste_test.py", "game": True, "needs": ["tiers"]},
    # a real client window: not while you play
    "compat-gui": {"script": T + "run_compat.py", "gui": True},
    "theme-gui": {"script": T + "run_theme_gui.py", "gui": True},
    "mp": {"script": T + "run_mp.py", "gui": True},
}
HELPERS = {H + "stutter_probe.py", T + "many_items.py"}  # (imported by stutter_test; by golden, rowsolve, many-items-run)

XFAILED = []  # (known_fail cases that failed)
GAME_LOCK = threading.Lock()  # (the games that share run/'s write-data take turns)


def is_game(c):
    return bool(c.get("game") or c.get("gui"))


def git_changed(paths):
    """whether any of the paths differs from git HEAD (untracked files count)"""
    p = subprocess.run(["git", "status", "--porcelain", "--"] + paths, cwd=ROOT, capture_output=True, text=True)
    return bool(p.stdout.strip())


def factorio_running():
    p = subprocess.run(["tasklist", "/FI", "IMAGENAME eq factorio.exe", "/NH"], capture_output=True, text=True)
    return "factorio.exe" in p.stdout.lower()


def unlisted():
    """test scripts that are neither a case nor a helper"""
    known = {c["script"] for c in CASES.values()} | HELPERS
    found = {f"{d}{p.name}" for d in (T, H) for p in (ROOT / d).glob("*.py")} - {T + "run.py"}
    return sorted(found - known)


def run_case(name, done, results):
    c = CASES[name]
    for n in c.get("needs", ()):
        done[n].wait()
        if not results[n][0]:
            results[name] = (False, 0.0, f"skipped: {n} (needed first) failed")
            print(f"FAIL {name:<16}   skipped: {n} (needed first) failed", flush=True)
            done[name].set()
            return
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    cmd = [sys.executable, str(ROOT / c["script"])] + c.get("args", [])
    shared = is_game(c) and not c.get("own_data")  # (real-client cases write to run/ too)
    if shared:
        GAME_LOCK.acquire()
    t = time.perf_counter()
    try:
        p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=c.get("timeout", 900 if is_game(c) else 180), stdin=subprocess.DEVNULL)
        ok, out = p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        ok, out = False, f"timed out after {e.timeout} s\n" + ((e.stdout or b"").decode("utf-8", "replace")
                                                              if isinstance(e.stdout, bytes) else e.stdout or "")
    finally:
        if shared:
            GAME_LOCK.release()
    known = c.get("known_fail")
    results[name] = (ok or bool(known), time.perf_counter() - t, out)
    secs = results[name][1]
    verdict = ("XPASS" if ok else "XFAIL") if known else "PASS" if ok else "FAIL"
    if verdict == "XFAIL":
        XFAILED.append(name)
    print(f"{verdict} {name:<16} {secs:6.1f} s" + (f"  ({known})" if known and not ok else ""), flush=True)
    if not ok and not known:
        print("\n".join("    " + l for l in out.rstrip().splitlines()[-20:]), flush=True)
    done[name].set()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", metavar="CASE")
    ap.add_argument("--changed", action="store_true")
    ap.add_argument("--nogame", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("-j", type=int, default=4)
    a = ap.parse_args()
    if a.list:
        for n, c in CASES.items():
            kind = "gui" if c.get("gui") else "game" if c.get("game") else "nogame"
            print(f"{n:<16} {kind:<7} {c['script']} {' '.join(c.get('args', []))}")
        return 0
    bad = [n for n in a.names if n not in CASES]
    if bad:
        sys.exit(f"no such case: {', '.join(bad)} (run.py --list)")
    names = list(a.names)
    if a.changed:
        names += [n for n, c in CASES.items() if git_changed([c["script"]] + c.get("watch", []))]
    if a.nogame:
        names += [n for n, c in CASES.items() if not is_game(c)]
    if a.all:
        names += list(CASES)
    if not (a.changed or a.nogame or a.all) and not names:
        ap.print_help()
        return 2
    if not names:
        print("no case changed since git HEAD")
        return 0
    for n in list(names):  # (a case's needs first)
        names += [d for d in CASES[n].get("needs", ()) if d not in names]
    names = sorted(dict.fromkeys(names), key=lambda n: (bool(CASES[n].get("needs")), list(CASES).index(n)))
    skipped = []
    if any(CASES[n].get("gui") for n in names) and factorio_running():
        skipped = [n for n in names if CASES[n].get("gui") and n not in a.names]  # (named ones run anyway)
        names = [n for n in names if n not in skipped]
    for n in unlisted():
        print(f"note: {n} is not in the case table")
    done = {n: threading.Event() for n in CASES}
    results = {}
    t = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, min(a.j, 4))) as ex:
        # (submitted in order: a case's needs are always ahead of it in the queue, so waiting for them can't deadlock)
        for f in [ex.submit(run_case, n, done, results) for n in names]:
            f.result()
    failed = [n for n in names if not results[n][0]]
    for n in skipped:
        print(f"SKIP {n}: factorio.exe is running (a real-client case; name it to run it anyway)")
    print(f"{len(names) - len(failed) - len(XFAILED)} of {len(names)} passed in {time.perf_counter() - t:.0f} s"
          + (f"; known failures: {', '.join(XFAILED)}" if XFAILED else "")
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
