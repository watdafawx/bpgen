"""Headless check of "Place it" for a base fitted to a bus design (run mod/test/fit_bus_test.py first): the book's
first print pasted at its box's centre, as the zzz-bpgen mod's place.lua does (a whole snapping cell off: pasted
again that far over), must land with its ghosts exactly on the planned box.
    python harness/fit_place_test.py [direction ...]"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bpgen import harness  # noqa: E402
from bpgen.config import PATHS  # noqa: E402

RUN = ROOT / "run"
OUT = RUN / "script-output" / "bpgen" / "fitplace.txt"
CONTROL = r'''
local spec = require("spec")
script.on_event(defines.events.on_tick, function(e)
  if e.tick ~= 5 then return end
  local s, force = game.surfaces.nauvis, game.forces.player
  local out = {}
  for _, case in ipairs(spec.cases) do
    local inv = game.create_inventory(1)
    inv[1].import_stack(case.book)
    local stack = inv[1]
    if stack.is_blueprint_book then stack = stack.get_inventory(defines.inventory.item_main)[stack.active_index or 1] end
    local box = case.box
    local area = { { box[1] - 140, box[2] - 140 }, { box[1] + box[3] + 140, box[2] + box[4] + 140 } }
    s.request_to_generate_chunks({ box[1] + box[3] / 2, box[2] + box[4] / 2 }, 6)
    s.force_generate_chunk_requests()
    local tiles = {}
    for x = area[1][1], area[2][1] do for y = area[1][2], area[2][2] do tiles[#tiles + 1] = { name = "lab-white", position = { x, y } } end end
    s.set_tiles(tiles)
    for _, en in pairs(s.find_entities_filtered({ area = area })) do if en.type ~= "character" then en.destroy() end end
    local c ={ x = box[1] + box[3] / 2, y = box[2] + box[4] / 2 }
    local function build() return stack.build_blueprint({ surface = s, force = force, position = c, build_mode = defines.build_mode.forced }) end
    local function min_of(ghosts)
      local ex, ey
      for _, g in pairs(ghosts) do
        if g.valid and g.type ~= "tile-ghost" then
          local b = g.bounding_box
          ex, ey = math.min(ex or b.left_top.x, b.left_top.x), math.min(ey or b.left_top.y, b.left_top.y)
        end
      end
      return math.floor(ex + 0.5), math.floor(ey + 0.5)
    end
    local ghosts = build()
    local ex, ey = min_of(ghosts)
    local retried = false
    for _ = 1, 3 do  -- (as place.lua)
      local dx = math.floor((box[1] - ex) / box[5] + 0.5) * box[5]
      local dy = math.floor((box[2] - ey) / box[5] + 0.5) * box[5]
      if dx == 0 and dy == 0 then break end
      for _, g in pairs(ghosts) do if g.valid then g.destroy() end end
      c = { x = c.x + dx, y = c.y + dy }
      ghosts = build()
      ex, ey = min_of(ghosts)
      retried = true
    end
    out[#out + 1] = case.name .. " box " .. box[1] .. "," .. box[2] .. " ghosts " .. ex .. "," .. ey .. (retried and " (pasted again)" or "") .. " n " .. #ghosts .. " of " .. tostring(case.n)
    for _, g in pairs(ghosts) do if g.valid then g.destroy() end end
    inv.destroy()
  end
  helpers.write_file("bpgen/fitplace.txt", table.concat(out, "\n") .. "\n", false)
end)
'''


def main():
    cases = []
    for d in sys.argv[1:] or ["north", "south", "west"]:
        f = json.loads((RUN / f"fit-{d}.json").read_text(encoding="utf-8"))
        cases.append({"name": d, "book": f["book"], "box": f["box"]})
        from bpgen import base
        pr = base._decode(f["book"])["blueprint_book"]["blueprints"][0]
        cases.append({"name": d + "-print", "book": base._encode({"blueprint": pr["blueprint"]}), "box": f["box"],
                      "n": len(pr["blueprint"]["entities"])})
    mods = RUN / "fitplace-mods"
    shutil.rmtree(mods, ignore_errors=True)
    mod = mods / "fitplace-test"
    mod.mkdir(parents=True)
    (mod / "info.json").write_text(json.dumps({"name": "fitplace-test", "version": "0.0.1", "title": "t",
                                               "author": "mtopfox", "factorio_version": "2.0", "dependencies": ["base"]}))
    (mod / "control.lua").write_text(CONTROL, encoding="utf-8")
    (mod / "spec.lua").write_text("return " + harness.to_lua({"cases": cases}), encoding="utf-8")
    (mods / "mod-list.json").write_text(json.dumps({"mods": [{"name": "base", "enabled": True},
                                                             {"name": "fitplace-test", "enabled": True}]}))
    OUT.unlink(missing_ok=True)
    save = RUN / "fitplace.zip"
    save.unlink(missing_ok=True)
    common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(mods)]
    for args in (["--create", str(save)], ["--benchmark", str(save), "--benchmark-ticks", "10", "--disable-audio"]):
        p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode:
            print("\n".join(line for line in p.stdout.splitlines() if "rror" in line)[-2500:])
            sys.exit(1)
    text = OUT.read_text() if OUT.exists() else ""
    print(text)
    for line in text.splitlines():
        _, _, box, _, ghosts = line.split()[:5]
        assert box == ghosts, f"placed off its box: {line}"
    assert len(text.splitlines()) == len(cases), "not every case ran"
    print("ok")


if __name__ == "__main__":
    main()
