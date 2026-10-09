"""Optional: builds the Rust belt router (bpgen_fast) into bpgen/bpgen_fast.pyd. Planning is about 5x faster with it;
without it bpgen uses the same search in Python.

    python -m bpgen.build_fast          (needs Rust: https://rustup.rs)
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    crate = next((p for p in (ROOT / "bpgen_fast", ROOT.parent / "fse" / "py-ext" / "bpgen_fast") if p.exists()), None)
    if crate is None:
        raise SystemExit("bpgen_fast not found")
    env = dict(os.environ, PYO3_PYTHON=sys.executable)  # (built for the Python running this)
    subprocess.run(["cargo", "build", "--release"], cwd=crate, env=env, check=True)
    shutil.copy2(crate / "target" / "release" / "bpgen_fast.dll", ROOT / "bpgen" / "bpgen_fast.pyd")
    print("built bpgen/bpgen_fast.pyd")


if __name__ == "__main__":
    main()
