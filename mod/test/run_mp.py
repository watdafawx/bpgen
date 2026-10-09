"""bpgen in multiplayer: fse's harness (a headless server and one client on this machine, both with fse) with the
bp-mp test mod. Passes when a line planned in the client's Python lands as the same ghosts on both peers and
neither desynced. Needs fse built (FSE_DIR, else ../fse beside bpgen); opens a game window, so not while you play."""
import os, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD = HERE.parent / "zzz-bpgen"
FSE = Path(os.environ.get("FSE_DIR") or next((d / "fse" for d in HERE.parents if (d / "fse").exists()), HERE / "fse"))
sys.exit(subprocess.run([sys.executable, str(FSE / "test" / "run_mp.py"), "--mod", str(MOD), "--mod", str(HERE / "bp-mp"),
                         "--expect", r"ghosts [1-9]", "--no-kick"]).returncode)
