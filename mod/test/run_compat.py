"""Real client: bpgen's buttons in Recipe Book and Factory Planner (compat.lua). Needs both mods (and flib) in your
mods folder. Prints compat-gui.txt; screenshot in run/script-output/compat-gui.png. The game closes itself."""
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

RUN = ROOT / "run"
MODS = RUN / "compat-mods"
OUT = RUN / "script-output"
if MODS.exists():
    shutil.rmtree(MODS)
MODS.mkdir(parents=True)
shutil.copytree(HERE.parent / "zzz-bpgen", MODS / "zzz-bpgen")
shutil.copytree(HERE / "compat-guitest", MODS / "compat-guitest")
(MODS / "compat-guitest" / "info.json").write_text(json.dumps({
    "name": "compat-guitest", "version": "0.0.1", "title": "bpgen compat test", "author": "mtopfox",
    "factorio_version": "2.0", "dependencies": ["base", "zzz-bpgen", "RecipeBook", "factoryplanner"]}))
user = Path(PATHS["user_mods"])
for mod in ("flib", "RecipeBook", "factoryplanner"):
    shutil.copy(max(user.glob(mod + "_*.zip")), MODS)
names = ["base", "elevated-rails", "quality", "space-age", "flib", "RecipeBook", "factoryplanner", "zzz-bpgen",
         "compat-guitest"]
(MODS / "mod-list.json").write_text(json.dumps({"mods": [{"name": n, "enabled": True} for n in names]}))
for f in OUT.glob("compat-gui*"):
    f.unlink()
save = RUN / "compat-gui.zip"
save.unlink(missing_ok=True)
common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(MODS)]
subprocess.run(common + ["--create", str(save)], capture_output=True, check=True)
# Steam restarts a directly started game (dropping our arguments) unless it looks Steam-launched
env = dict(os.environ, SteamAppId="427520", SteamGameId="427520")
game = subprocess.Popen(common + ["--load-game", str(save.resolve())], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, env=env)
start = time.time()
while time.time() - start < 150 and not (OUT / "compat-gui-done.txt").exists() and game.poll() is None:
    time.sleep(1)
time.sleep(2)
game.kill()
log = (RUN / "factorio-current.log").read_text(encoding="utf-8", errors="replace").splitlines()
print((OUT / "compat-gui.txt").read_text() if (OUT / "compat-gui.txt").exists() else "no test output")
print("errors:", "\n".join(l for l in log if "Error" in l or "non-recoverable" in l)[-2000:] or "none")
