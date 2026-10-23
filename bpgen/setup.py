"""First run on a new machine: the headless game's config (run/config.ini) and the vanilla prototype data.

    python -m bpgen.setup

Headless runs use run/ as their own write-data folder, so they never touch your game's saves, mods or settings.
"""
import shutil
import subprocess

from bpgen import config
from bpgen.config import PATHS
from bpgen.harness import FACTORIO, HARNESS, ROOT, RUN


def main():
    if not FACTORIO.exists():
        raise SystemExit(f"Factorio not found at {FACTORIO}: set factorio_exe in bpgen.json (see bpgen.example.json)")
    RUN.mkdir(exist_ok=True)
    ini = RUN / "config.ini"
    if not ini.exists():
        # (without the version line the game calls the file invalid and asks, over the main menu, to reset it)
        ini.write_text(f"; version=13\n[path]\nread-data={PATHS['game_data'].as_posix()}\nwrite-data={RUN.as_posix()}\n",
                       encoding="utf-8")
        print("wrote", ini)
    (ROOT / "data").mkdir(exist_ok=True)
    dump = ROOT / "data" / "vanilla-dump.json"
    stamp = ROOT / "data" / "vanilla-dump.version"  # (the game version it was dumped from: a game update re-dumps)
    version = config.game_version()
    if not dump.exists() or not stamp.exists() or stamp.read_text().strip() != version:
        print("dumping vanilla prototypes (a minute)...")
        out = RUN / "script-output" / "data-raw-dump.json"
        out.unlink(missing_ok=True)
        test_mod = ROOT / "mods" / "vanilla" / "bpgen-test"  # (the harness's last copy: a stale one can fail to load)
        if test_mod.exists():
            shutil.copytree(HARNESS, test_mod, dirs_exist_ok=True)
        subprocess.run([str(FACTORIO), "--config", str(ini), "--mod-directory", str(ROOT / "mods" / "vanilla"),
                        "--dump-data"], capture_output=True, timeout=900, env=config.plain_env())
        if not out.exists():
            raise SystemExit("the dump failed: see run/factorio-current.log")
        shutil.move(out, dump)
        stamp.write_text(version)
        print("wrote", dump, "for", version)
    print("ready: python -m bpgen serve")


if __name__ == "__main__":
    main()
