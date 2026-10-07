"""Headless check of the companion's exports: builds a small setup, snapshots it, prints the files."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from bpgen.config import PATHS  # noqa: E402

RUN = ROOT / "run"
MODS = RUN / "comp-mods"
OUT = RUN / "script-output" / "bpgen"
if MODS.exists():
    shutil.rmtree(MODS)
MODS.mkdir(parents=True)
shutil.copytree(HERE.parent / "bpgen-companion", MODS / "bpgen-companion")
shutil.copytree(HERE / "comp-test", MODS / "comp-test")
(MODS / "comp-test" / "info.json").write_text(json.dumps({
    "name": "comp-test", "version": "0.0.1", "title": "companion test", "author": "mtopfox",
    "factorio_version": "2.0", "dependencies": ["base", "bpgen-companion"]}))
names = ["base", "elevated-rails", "quality", "space-age", "bpgen-companion", "comp-test"]
(MODS / "mod-list.json").write_text(json.dumps({"mods": [{"name": n, "enabled": True} for n in names]}))
for f in ("state.json", "snapshot.json", "comp-test.txt"):
    (OUT / f).unlink(missing_ok=True)
save = RUN / "comp-test.zip"
save.unlink(missing_ok=True)
common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(MODS)]
for args in (["--create", str(save)], ["--benchmark", str(save), "--benchmark-ticks", "320", "--disable-audio"]):
    p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode or "Error" in p.stdout:
        print("\n".join(l for l in p.stdout.splitlines() if "rror" in l)[-3000:] or p.stdout[-3000:])
        sys.exit(1)
print((OUT / "comp-test.txt").read_text() if (OUT / "comp-test.txt").exists() else "no test output")
for f in ("snapshot.json", "state.json"):
    print(f, (OUT / f).stat().st_size if (OUT / f).exists() else "missing")
