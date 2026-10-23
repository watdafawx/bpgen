"""Try in sandbox, headless (no player: the lab and the paste back, through bpgen's remote interface): a plan copied
into a lab with the base round it, fed from the real belt, changed, pasted back as only the changes.
python mod/test/sandbox_test.py"""
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
MODS = RUN / "sandbox-mods"
OUT = RUN / "script-output" / "bpgen" / "sandbox-test.txt"
if MODS.exists():
    shutil.rmtree(MODS)
MODS.mkdir(parents=True)
shutil.copytree(HERE.parent / "zzz-bpgen", MODS / "zzz-bpgen")
shutil.copytree(HERE / "sandbox-test", MODS / "sandbox-test")
(MODS / "sandbox-test" / "info.json").write_text(json.dumps({
    "name": "sandbox-test", "version": "0.0.1", "title": "bpgen sandbox test", "author": "mtopfox",
    "factorio_version": "2.1", "dependencies": ["base", "zzz-bpgen"]}))
names = ["base", "elevated-rails", "quality", "space-age", "zzz-bpgen", "sandbox-test"]
(MODS / "mod-list.json").write_text(json.dumps({"mods": [{"name": n, "enabled": True} for n in names]}))
OUT.unlink(missing_ok=True)
save = RUN / "sandbox-test.zip"
save.unlink(missing_ok=True)
common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(MODS)]
for args in (["--create", str(save)], ["--benchmark", str(save), "--benchmark-ticks", "3600", "--disable-audio"]):
    p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode or "Error" in p.stdout:
        print("\n".join(l for l in p.stdout.splitlines() if "rror" in l)[-3000:] or p.stdout[-3000:])
        sys.exit(1)
text = OUT.read_text() if OUT.exists() else "no test output"
print(text)
sys.exit(1 if "FAIL" in text or "done" not in text else 0)
