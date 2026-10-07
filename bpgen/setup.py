"""First run on a new machine: the headless game's config (run/config.ini) and the vanilla prototype data.

    python -m bpgen.setup

Headless runs use run/ as their own write-data folder, so they never touch your game's saves, mods or settings.
"""
import shutil
import subprocess

from bpgen.config import PATHS
from bpgen.harness import FACTORIO, ROOT, RUN


def main():
    if not FACTORIO.exists():
        raise SystemExit(f"Factorio not found at {FACTORIO}: set factorio_exe in bpgen.json (see bpgen.example.json)")
    RUN.mkdir(exist_ok=True)
    ini = RUN / "config.ini"
    if not ini.exists():
        ini.write_text(f"[path]\nread-data={PATHS['game_data'].as_posix()}\nwrite-data={RUN.as_posix()}\n",
                       encoding="utf-8")
        print("wrote", ini)
    (ROOT / "data").mkdir(exist_ok=True)
    dump = ROOT / "data" / "vanilla-dump.json"
    if not dump.exists():
        print("dumping vanilla prototypes (a minute)...")
        out = RUN / "script-output" / "data-raw-dump.json"
        out.unlink(missing_ok=True)
        subprocess.run([str(FACTORIO), "--config", str(ini), "--mod-directory", str(ROOT / "mods" / "vanilla"),
                        "--dump-data"], capture_output=True, timeout=900)
        if not out.exists():
            raise SystemExit("the dump failed: see run/factorio-current.log")
        shutil.move(out, dump)
        print("wrote", dump)
    print("ready: python -m bpgen serve")


if __name__ == "__main__":
    main()
