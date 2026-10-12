"""bpgen planning must not stutter the game: a headless game through fse with the stutter-test mod (stutter-test/
control.lua) measures how long the game thread waits on bpgen's progress call while Rust routes run on a worker.
Passes when the longest wait is under 20 ms and a real plan's
progress arrives as a note (fse 0.17.2+). Needs fse built (FSE_DIR, else ../fse beside bpgen)."""
import json, os, re, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FSE = Path(os.environ.get("FSE_DIR") or next((d / "fse" for d in HERE.parents if (d / "fse").exists()), HERE / "fse"))
DIST = FSE / "dist" if (FSE / "dist").exists() else FSE
sys.path.insert(0, str(FSE / "test"))
from factorio_paths import run_dir, watch_game  # noqa: E402
RUN = run_dir(HERE.parents[1] / "run" / "stutter")  # (its own write-data: config.ini, the log, script-output)
MODS = RUN / "mods"
shutil.rmtree(MODS, ignore_errors=True)
MODS.mkdir(parents=True)
shutil.copytree(HERE.parent / "zzz-bpgen", MODS / "zzz-bpgen")
shutil.copytree(HERE / "stutter-test", MODS / "stutter-test")
names = ["base", "elevated-rails", "quality", "space-age", "zzz-bpgen", "stutter-test"]
(MODS / "mod-list.json").write_text(json.dumps({"mods": [{"name": n, "enabled": True} for n in names]}))
out = RUN / "script-output" / "stutter.txt"
out.unlink(missing_ok=True)
save = RUN / "stutter.zip"
save.unlink(missing_ok=True)
common = [str(DIST / "fse-launcher.exe"), "--config", str(RUN / "config.ini"), "--mod-directory", str(MODS)]
subprocess.run(common + ["--create", str(save)], capture_output=True)
# (a benchmark: ticks as fast as they go, so many samples; stopped once the mod wrote its result)
game = watch_game(common + ["--benchmark", str(save), "--benchmark-ticks", "10000000", "--disable-audio"], timeout=180)
while game.poll() is None and not out.exists():
    time.sleep(0.5)
time.sleep(0.5)
game.kill()
text = out.read_text() if out.exists() else ""
print(text.strip() or "no stutter.txt (see " + str(RUN / "factorio-current.log") + ")")
m = re.search(r"max (-?\d+) ms", text)
ok1 = bool(m) and 0 <= int(m.group(1)) < 20
print("PASS" if ok1 else "FAIL", "the game waits under 20 ms on bpgen's progress while it plans")
n = re.search(r"plan ok, progress note: run (\d+), (\d+) lines", text)
ok2 = bool(n) and int(n.group(2)) > 0
print("PASS" if ok2 else "FAIL", "a real plan's progress reaches the game as a note")
sys.exit(0 if ok1 and ok2 else 1)
