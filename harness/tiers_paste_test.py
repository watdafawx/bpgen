"""Headless paste of starter-base tiers (run mod/test/tiers_test.py first: run/tier1.txt, run/tier2.txt): tier 1
pasted and built, tier 2 pasted with the cursor about over its C (as a player lines them up). Its absolute snapping
must land its C exactly on tier 1's, and every one of its entities must fit (no clash with tier 1).
    python harness/tiers_paste_test.py"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bpgen import extend, harness, tiers  # noqa: E402
from bpgen.config import PATHS  # noqa: E402
from bpgen.service import Service  # noqa: E402

RUN = ROOT / "run"
OUT = RUN / "script-output" / "bpgen" / "tiers.txt"

CONTROL = r'''
local spec = require("spec")
local function key(p) return math.floor(p.x) .. "," .. math.floor(p.y) end
script.on_event(defines.events.on_tick, function(e)
  if e.tick ~= 5 then return end
  local s, force = game.surfaces.nauvis, game.forces.player
  s.peaceful_mode = true
  s.request_to_generate_chunks({ 0, 0 }, 10)
  s.force_generate_chunk_requests()
  local tiles = {}
  for x = -250, 350 do for y = -250, 250 do tiles[#tiles + 1] = { name = "lab-white", position = { x, y } } end end
  s.set_tiles(tiles)
  for _, en in pairs(s.find_entities_filtered({ area = { { -250, -250 }, { 350, 250 } } })) do
    if en.type ~= "character" then en.destroy() end
  end
  local inv = game.create_inventory(1)
  local out = {}
  -- tier 1: built; its C's tiles noted (and left unpaved, so tier 2's C shows as ghosts too)
  inv[1].import_stack(spec.tier1)
  local c1 = {}
  for _, g in pairs(inv[1].build_blueprint({ surface = s, force = force, position = { 0, 0 }, build_mode = defines.build_mode.forced })) do
    if g.type == "tile-ghost" then c1[#c1 + 1] = key(g.position) g.destroy() else g.revive() end
  end
  table.sort(c1)
  local x0, y0 = math.huge, math.huge
  for _, k in ipairs(c1) do
    local x, y = k:match("(-?%d+),(-?%d+)")
    x0, y0 = math.min(x0, tonumber(x)), math.min(y0, tonumber(y))
  end
  -- tier 2: the cursor about over the C (off by a few tiles, as by hand)
  inv[1].import_stack(spec.tier2)
  local c2, n2 = {}, 0
  local at = { x0 + spec.c_to_center[1] + 3, y0 + spec.c_to_center[2] - 2 }
  for try = 1, 2 do  -- (its ghost C a cell off: moved by that much, as a player would, and pasted again)
    c2, n2 = {}, 0
    local ghosts = inv[1].build_blueprint({ surface = s, force = force, position = at })
    local gx, gy = math.huge, math.huge
    for _, g in pairs(ghosts) do
      if g.type == "tile-ghost" then
        c2[#c2 + 1] = key(g.position)
        gx, gy = math.min(gx, math.floor(g.position.x)), math.min(gy, math.floor(g.position.y))
      else n2 = n2 + 1 end
    end
    if (gx == x0 and gy == y0) or try == 2 then break end
    for _, g in pairs(ghosts) do if g.valid then g.destroy() end end
    at = { at[1] + x0 - gx, at[2] + y0 - gy }
    out[#out + 1] = "moved " .. (x0 - gx) .. "," .. (y0 - gy)
  end
  table.sort(c2)
  out[#out + 1] = "c1 " .. table.concat(c1, " ")
  out[#out + 1] = "c2 " .. table.concat(c2, " ")
  out[#out + 1] = "tier2 ghosts " .. n2
  helpers.write_file("bpgen/tiers.txt", table.concat(out, "\n") .. "\n", false)
end)
'''


def main():
    s = Service("vanilla")
    t1 = (RUN / "tier1.txt").read_text(encoding="utf-8")
    t2 = (RUN / "tier2.txt").read_text(encoding="utf-8")
    ents2, corner2, _ = tiers.held_base(t2)
    occ = extend.plan_tiles([s.decorate(e) for e in ents2]) | set(tiers.c_tiles(corner2))
    cx = (min(t[0] for t in occ) + max(t[0] for t in occ) + 1) / 2
    cy = (min(t[1] for t in occ) + max(t[1] for t in occ) + 1) / 2
    spec = {"tier1": t1, "tier2": t2, "c_to_center": [cx - corner2[0], cy - corner2[1]]}
    mods = RUN / "tiers-mods"
    if mods.exists():
        shutil.rmtree(mods)
    mod = mods / "tiers-test"
    mod.mkdir(parents=True)
    (mod / "info.json").write_text(json.dumps({"name": "tiers-test", "version": "0.0.1", "title": "tiers test",
                                               "author": "mtopfox", "factorio_version": "2.0", "dependencies": ["base"]}))
    (mod / "control.lua").write_text(CONTROL, encoding="utf-8")
    (mod / "spec.lua").write_text("return " + harness.to_lua(spec), encoding="utf-8")
    (mods / "mod-list.json").write_text(json.dumps({"mods": [{"name": "base", "enabled": True},
                                                             {"name": "tiers-test", "enabled": True}]}))
    OUT.unlink(missing_ok=True)
    save = RUN / "tiers.zip"
    save.unlink(missing_ok=True)
    common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(mods)]
    for args in (["--create", str(save)], ["--benchmark", str(save), "--benchmark-ticks", "10", "--disable-audio"]):
        p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode:
            print("\n".join(line for line in p.stdout.splitlines() if "rror" in line)[-2500:])
            sys.exit(1)
    text = OUT.read_text() if OUT.exists() else ""
    lines = dict(line.split(" ", 1) for line in text.splitlines() if " " in line)
    print(lines.get("c1"), "|", lines.get("c2"), "|", lines.get("tier2"))
    assert lines.get("c1") and lines.get("c1") == lines.get("c2"), "tier 2's C didn't land on tier 1's"
    assert lines.get("tier2") == f"ghosts {len(ents2)}", f"tier 2: {lines.get('tier2')} of {len(ents2)} entities fit"
    print("ok")


if __name__ == "__main__":
    main()
