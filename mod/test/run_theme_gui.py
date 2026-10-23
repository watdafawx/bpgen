"""Real client: the bpgen window on fse-std's themed components (fse-std from fse/mods), shot in five themes, its
progress bar, Undo asking first. --plain: without fse-std, the game's own styles (Undo at once). --scale 2: the
game's GUI scale (a config of its own: config-scale.ini). Started through the
fse launcher (FSE_DIR, else an fse/dist above this folder): planning runs in game. Prints theme-gui.txt; shots in
run/script-output/theme-gui-*.png; exit code 1 when a check failed. Don't run it while you play."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from bpgen.config import PATHS  # noqa: E402

PLAIN = "--plain" in sys.argv
SCALE = sys.argv[sys.argv.index("--scale") + 1] if "--scale" in sys.argv else None
FSE = Path(os.environ.get("FSE_DIR") or next((d / "fse" / "dist" for d in ROOT.parents
                                              if (d / "fse" / "dist" / "fse-launcher.exe").exists()), ROOT / "fse"))
STD = next((d / "fse" / "mods" / "fse-std" for d in [ROOT] + list(ROOT.parents) if (d / "fse" / "mods" / "fse-std").exists()))
# (a game with a window: yours, most likely; headless runs of other tests don't count)
running = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"name='factorio.exe'\" | ForEach-Object { $_.CommandLine }"],
                         capture_output=True, text=True).stdout.splitlines()
# (other tests' games use a run folder's config: they don't count; this test only uses script hooks and the game's
# own screenshots, so it runs fine beside them)
if any(line.strip() and not any(f in line for f in ("--benchmark", "--create", "--start-server", "--dump-data"))
       and "\\run\\config.ini" not in line.lower() and "\\run\\all\\" not in line.lower()
       for line in running):
    raise SystemExit("Factorio is running with a window (your game?): close it first, this test opens one")

RUN = ROOT / "run"
RUN.mkdir(exist_ok=True)
ini = RUN / "config.ini"
if not ini.exists():
    ini.write_text(f"; version=13\n[path]\nread-data={PATHS['game_data'].as_posix()}\nwrite-data={RUN.as_posix()}\n",
                   encoding="utf-8")
MODS = RUN / "theme-mods"
OUT = RUN / "script-output"
shutil.rmtree(MODS, ignore_errors=True)
MODS.mkdir(parents=True)
shutil.copytree(HERE.parent / "zzz-bpgen", MODS / "zzz-bpgen")
if not PLAIN:
    shutil.copytree(STD, MODS / "fse-std")
shutil.copytree(HERE / "theme-guitest", MODS / "theme-guitest")
(MODS / "theme-guitest" / "info.json").write_text(json.dumps({
    "name": "theme-guitest", "version": "0.0.1", "title": "bpgen theme test", "author": "mtopfox",
    "factorio_version": "2.1", "dependencies": ["base", "zzz-bpgen", "? fse-std"]}))
names = ["base", "elevated-rails", "quality", "space-age", "zzz-bpgen", "theme-guitest"] + ([] if PLAIN else ["fse-std"])
(MODS / "mod-list.json").write_text(json.dumps({"mods": [{"name": n, "enabled": True} for n in names]}))
OUT.mkdir(exist_ok=True)
for f in OUT.glob("theme-gui*"):
    f.unlink()
if SCALE:  # (the game's GUI scale: a config of its own, the usual one untouched)
    scaled = RUN / "config-scale.ini"
    scaled.write_text(ini.read_text(encoding="utf-8").rstrip() + f"\n[interface]\nui-scale-mode=manual-pixels\n"
                      f"custom-ui-scale={SCALE}\n", encoding="utf-8")
    ini = scaled
launch = [str(FSE / "fse-launcher.exe"), "--config", str(ini), "--mod-directory", str(MODS)]
save = RUN / "theme-gui.zip"
save.unlink(missing_ok=True)
made = subprocess.run(launch + ["--create", str(save)], capture_output=True, text=True, errors="replace")
if not save.exists():
    raise SystemExit("--create failed: " + " | ".join(l for l in made.stdout.splitlines() if "Error" in l))
# (planning in game adds to bpgen's history - the one of the bpgen the launcher's FSE_PYPATH names: the user's is put
# back afterwards)
pypath = [d for line in (FSE / "fse.env").read_text().splitlines() if line.startswith("FSE_PYPATH=")
          for d in line.split("=", 1)[1].split(";")]
hists = {ROOT / "data" / "history.json"} | {Path(d) / "data" / "history.json" for d in pypath
                                             if Path(d).name.lower() == "bpgen"}
kept = {h: h.read_bytes() if h.exists() else None for h in hists}
game = subprocess.Popen(launch + ["--load-game", str(save)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
start = time.time()
current = RUN / "factorio-current.log"
while time.time() - start < 600 and not (OUT / "theme-gui-done.txt").exists() and game.poll() is None:
    time.sleep(1)
    # (a script error stops the game with a dialog on the screen: close it at once, don't wait it out)
    if current.exists() and "non-recoverable" in current.read_text(encoding="utf-8", errors="replace"):
        break
time.sleep(1)
subprocess.run(["taskkill", "/PID", str(game.pid), "/T", "/F"], capture_output=True)
for h, data in kept.items():
    if data is not None:
        h.write_bytes(data)
    else:
        h.unlink(missing_ok=True)
log = (RUN / "factorio-current.log").read_text(encoding="utf-8", errors="replace").splitlines()
errors = [l for l in log if "Error" in l or "error (handled)" in l or "non-recoverable" in l]
print("errors:", "\n".join(errors)[-2000:] or "none")
text = (OUT / "theme-gui.txt").read_text() if (OUT / "theme-gui.txt").exists() else "no result"
print(text)
print("screenshots:", ", ".join(sorted(f.name for f in OUT.glob("theme-gui-*.png"))))
sys.exit(0 if text.rstrip().endswith("PASS") and not errors else 1)
