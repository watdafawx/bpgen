"""Where Factorio lives on this machine.

bpgen.json (next to the bpgen folder) wins; anything it leaves out is detected:
  factorio_exe   the game binary (Steam's default library, or FACTORIO_EXE)
  game_data      the game's data folder (next to bin/)
  write_data     the user data folder: from the game's config-path.cfg and config.ini, else %APPDATA%/Factorio
  user_mods      <write_data>/mods
  script_output  <write_data>/script-output
"""
import json
import os
import re
from pathlib import Path

os.environ.setdefault("FSE_OFF", "1")  # (games started from here run plain even with the fse loader installed)

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "bpgen.json"
STEAM_GUESSES = [Path(p) / "steamapps/common/Factorio" for p in (
    "C:/Program Files (x86)/Steam", "C:/Program Files/Steam", "D:/SteamLibrary", "E:/SteamLibrary", "G:/SteamLibrary")]


def _read():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _game_root(cfg):
    if cfg.get("factorio_exe"):
        return Path(cfg["factorio_exe"]).parents[2]
    if os.environ.get("FACTORIO_EXE"):
        return Path(os.environ["FACTORIO_EXE"]).parents[2]
    for g in STEAM_GUESSES:
        if (g / "bin/x64/factorio.exe").exists():
            return g
    return STEAM_GUESSES[0]


def _write_data(root):
    """the game's write-data folder, as the game itself resolves it"""
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Factorio"
    try:
        cfgpath = (root / "config-path.cfg").read_text(encoding="utf-8")
        m = re.search(r"^config-path=(.*)$", cfgpath, re.M)
        config_dir = m.group(1).strip() if m else "__PATH__system-write-data__/config"
        config_dir = config_dir.replace("__PATH__system-write-data__", str(appdata)).replace(
            "__PATH__executable__", str(root / "bin/x64"))
        ini = (Path(config_dir) / "config.ini").read_text(encoding="utf-8")
        m = re.search(r"^write-data=(.*)$", ini, re.M)
        if m:
            return Path(m.group(1).strip().replace("__PATH__system-write-data__", str(appdata))
                        .replace("__PATH__executable__", str(root / "bin/x64")))
    except OSError:
        pass
    return appdata


def load():
    cfg = _read()
    root = _game_root(cfg)
    exe = Path(cfg.get("factorio_exe") or root / "bin/x64/factorio.exe")
    data = Path(cfg.get("game_data") or root / "data")
    write = Path(cfg.get("write_data") or _write_data(root))
    return {"factorio_exe": exe, "game_data": data, "write_data": write,
            "user_mods": Path(cfg.get("user_mods") or write / "mods"),
            # BPGEN_SCRIPT_OUTPUT: tests point the web UI at a test run's mod files
            "script_output": Path(os.environ.get("BPGEN_SCRIPT_OUTPUT") or cfg.get("script_output") or write / "script-output")}


PATHS = load()
