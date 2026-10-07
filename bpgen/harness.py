"""Run test cases in headless Factorio: write spec.lua into the harness mod, create a save, benchmark it."""
import json
import shutil
import msvcrt
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from bpgen.config import PATHS
FACTORIO = PATHS["factorio_exe"]
RUN = ROOT / "run"
HARNESS = ROOT / "harness" / "bpgen-test"


def to_lua(v):
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, dict):
        return "{" + ",".join(f"[{json.dumps(k)}]={to_lua(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "{" + ",".join(to_lua(x) for x in v) + "}"
    raise TypeError(type(v))


class Cancelled(Exception):
    pass


class _RunLock:
    """one headless Factorio at a time across processes (web server, watch, scripts all share run/)"""

    def __init__(self, progress, cancel):
        self.progress, self.cancel = progress, cancel

    def __enter__(self):
        RUN.mkdir(exist_ok=True)
        self.f = open(RUN / "harness.lock", "a+")
        while True:
            try:
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                return self
            except OSError:
                if self.progress:
                    self.progress("waiting for another headless Factorio run to finish")
                if self.cancel and self.cancel():
                    self.f.close()
                    raise Cancelled("cancelled")
                time.sleep(1)

    def __exit__(self, *exc):
        self.f.seek(0)
        msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        self.f.close()


def _stream(cmd, stage, total_mods, progress, cancel, timeout, log_path):
    """run factorio, turning its log into progress messages; kill it on cancel/timeout"""
    progress = progress or (lambda msg: None)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", bufsize=1)
    lines, counts, start = [], {}, time.time()
    phases = {"(settings": "reading mod settings", "(data.lua)": "loading prototypes",
              "(data-updates.lua)": "updating prototypes", "(data-final-fixes.lua)": "final fixes"}
    watcher = threading.Thread(target=_watch, args=(p, cancel, timeout, start), daemon=True)
    watcher.start()
    for line in p.stdout:
        lines.append(line)
        if "Loading mod" in line:
            for key, label in phases.items():
                if key in line:
                    counts[label] = counts.get(label, 0) + 1
                    if counts[label] % 10 == 1 or counts[label] == total_mods:
                        progress(f"{stage}: {label} {counts[label]}/{total_mods} mods")
        elif "Checksum of" in line and "checks" not in counts:
            counts["checks"] = 1
            progress(f"{stage}: checking mod checksums")
        elif "Factorio initialised" in line:
            progress(f"{stage}: game started")
        elif "Creating new map" in line or "Loading map" in line:
            progress(f"{stage}: {'creating the test map' if 'Creating' in line else 'loading the test map'}")
        elif "Performed" in line and "updates" in line:
            progress(f"{stage}: {line.strip()}")
    p.wait()
    log_path.write_text("".join(lines), encoding="utf-8")
    if cancel and cancel():
        raise Cancelled("cancelled")
    if time.time() - start > timeout:
        raise RuntimeError(f"factorio timed out after {timeout}s, see {log_path}")
    return p.returncode, "".join(lines)


def _watch(p, cancel, timeout, start):
    while p.poll() is None:
        if (cancel and cancel()) or time.time() - start > timeout:
            p.kill()
            return
        time.sleep(0.2)


def run(spec, mod_dir=ROOT / "mods" / "vanilla", timeout=1800, progress=None, cancel=None):
    """spec: {warmup, measure, force, belt_stack_size, cases:[...]} -> parsed result.json
    progress(msg) gets stage updates; cancel() -> True stops the run (raises Cancelled)"""
    with _RunLock(progress, cancel):
        return _run(spec, mod_dir, timeout, progress, cancel)


def _run(spec, mod_dir, timeout, progress, cancel):
    dest = mod_dir / "bpgen-test"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(HARNESS, dest)
    (dest / "spec.lua").write_text("return " + to_lua(spec), encoding="utf-8")
    mod_list = mod_dir / "mod-list.json"
    mods = json.loads(mod_list.read_text(encoding="utf-8-sig"))
    if not any(m["name"] == "bpgen-test" and m["enabled"] for m in mods["mods"]):
        mods["mods"] = [m for m in mods["mods"] if m["name"] != "bpgen-test"] + [{"name": "bpgen-test", "enabled": True}]
        mod_list.write_text(json.dumps(mods, indent=1))
    total_mods = sum(1 for m in mods["mods"] if m["enabled"])

    save = RUN / "bench.zip"
    result = RUN / "script-output" / "bpgen" / "result.json"
    save.unlink(missing_ok=True)
    result.unlink(missing_ok=True)
    common = [str(FACTORIO), "--config", str(RUN / "config.ini"), "--mod-directory", str(mod_dir)]
    ticks = spec["warmup"] + spec["measure"] + 2
    for args, log, stage in (
        (["--create", str(save)], "create.out", f"building {len(spec['cases'])} test case(s)"),
        (["--benchmark", str(save), "--benchmark-ticks", str(ticks), "--disable-audio"], "bench.out",
         f"simulating {ticks / 3600:.1f} min of game time"),
    ):
        if progress:
            progress(f"{stage}: starting headless Factorio")
        code, out = _stream(common + args, stage, total_mods, progress, cancel, timeout, RUN / log)
        if code != 0 or not save.exists():
            raise RuntimeError(f"factorio {args[0]} failed, see run/{log}:\n" + out[-2000:])
    if not result.exists():
        raise RuntimeError("no result.json, see run/bench.out:\n" + (RUN / "bench.out").read_text()[-2000:])
    return json.loads(result.read_text())
