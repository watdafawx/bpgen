"""Headless check of a robot-fed mall: paste it, play the robots (top requester chests up to what they ask for),
run a while, and count what reaches each passive provider chest.
    python harness/botmall_test.py [vanilla|pack] [minutes]"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bpgen import base, harness  # noqa: E402
from bpgen.config import PATHS  # noqa: E402
from bpgen.service import Service  # noqa: E402

MODE = sys.argv[1] if len(sys.argv) > 1 else "pack"
MINUTES = float(sys.argv[2]) if len(sys.argv) > 2 else 5
RUN = ROOT / "run"
OUT = RUN / "script-output" / "bpgen" / "botmall.txt"

CONTROL = r'''
local spec = require("spec")
local built = false
script.on_event(defines.events.on_tick, function(e)
  local s = game.surfaces.nauvis
  local force = game.forces.player
  if e.tick == 1 then
    s.request_to_generate_chunks({ 0, 0 }, 3)
    s.force_generate_chunk_requests()
    local tiles = {}
    for x = -10, 80 do for y = -10, 40 do tiles[#tiles + 1] = { name = "lab-white", position = { x, y } } end end
    s.set_tiles(tiles)
    force.enable_all_recipes()
    for _, ent in pairs(s.find_entities_filtered({ area = { { -10, -10 }, { 80, 40 } } })) do
      if ent.type ~= "character" then ent.destroy() end
    end
  elseif e.tick == 5 then
    local inv = game.create_inventory(1)
    inv[1].import_stack(spec.blueprint)
    for _, g in pairs(inv[1].build_blueprint({ surface = s, force = force, position = { 30, 15 }, build_mode = defines.build_mode.forced })) do
      g.revive()
    end
    -- power: an energy interface just above the topmost pole of the mall (inside its supply area)
    local top
    for _, p in pairs(s.find_entities_filtered({ type = "electric-pole" })) do
      if not top or p.position.y < top.position.y then top = p end
    end
    local eei = s.create_entity({ name = "electric-energy-interface", position = { top.position.x, top.position.y - 3 }, force = force })
    eei.power_production = 1e10
    eei.electric_buffer_size = 1e10
    built = true
  elseif built and e.tick % 30 == 0 then
    -- the robots: every requester gets what it asks for
    for _, chest in pairs(s.find_entities_filtered({ type = "logistic-container" })) do
      local point = chest.get_requester_point()
      if point then
        local inv = chest.get_inventory(defines.inventory.chest)
        for _, section in pairs(point.sections) do
          for _, f in pairs(section.filters) do
            if f.value and f.value.name then
              local have = inv.get_item_count(f.value.name)
              if have < f.min then inv.insert({ name = f.value.name, count = f.min - have }) end
            end
          end
        end
      end
    end
  end
  if e.tick == spec.ticks then
    local got, requests = {}, 0
    for _, chest in pairs(s.find_entities_filtered({ type = "logistic-container" })) do
      if chest.get_requester_point() then
        for _, section in pairs(chest.get_requester_point().sections) do requests = requests + #section.filters end
      else
        for _, c in pairs(chest.get_inventory(defines.inventory.chest).get_contents()) do
          got[c.name] = (got[c.name] or 0) + c.count
        end
      end
    end
    local machines = #s.find_entities_filtered({ type = "assembling-machine" })
    local names = {}
    for k, v in pairs(defines.entity_status) do names[v] = k end
    local st = {}
    for _, m in pairs(s.find_entities_filtered({ type = "assembling-machine" })) do
      local k = (m.get_recipe() and m.get_recipe().name or "?") .. ":" .. (names[m.status] or "?")
      if m.status == defines.entity_status.no_power then k = k .. "@" .. m.position.x .. "," .. m.position.y .. "/" .. m.name end
      st[#st + 1] = k
    end
    table.sort(st)
    helpers.write_file("bpgen/botmall.txt", table.concat(st, " ") .. "\n", true)
    local parts = {}
    for k, v in pairs(got) do parts[#parts + 1] = k .. "=" .. v end
    table.sort(parts)
    helpers.write_file("bpgen/botmall.txt", "machines " .. machines .. ", request slots set " .. requests
      .. ", products in providers: " .. #parts .. "\n" .. table.concat(parts, " ") .. "\n", true)
  end
end)
'''

s = Service(MODE)
products = base.mall_products(s.data, "assembling-machine-2")
r = s.plan_mall({"mode": "mall", "feed": "robots", "products": products, "machine": "assembling-machine-2",
                 "chest_limit": 2})
print(len(products), "products,", r["summary"]["machines"], "machines;", r["summary"]["notes"])
mods = RUN / "botmall-mods"
if mods.exists():
    shutil.rmtree(mods)
mods.mkdir(parents=True)
src = s.mod_dir
mod = mods / "botmall-test"
mod.mkdir()
(mod / "info.json").write_text(json.dumps({"name": "botmall-test", "version": "0.0.1", "title": "botmall test",
                                           "author": "mtopfox", "factorio_version": "2.0", "dependencies": ["base"]}))
(mod / "control.lua").write_text(CONTROL, encoding="utf-8")
ticks = int(MINUTES * 3600) + 10
(mod / "spec.lua").write_text("return " + harness.to_lua({"blueprint": r["blueprint"], "ticks": ticks}), encoding="utf-8")
# the pack's mods (or vanilla), plus this test mod
mod_list = json.loads((src / "mod-list.json").read_text(encoding="utf-8-sig"))
for p in src.iterdir():
    if p.name not in ("mod-list.json", "bpgen-test", "mod-settings.dat"):
        dst = mods / p.name
        try:
            dst.symlink_to(p.resolve(), target_is_directory=p.is_dir())
        except OSError:
            (shutil.copytree if p.is_dir() else shutil.copy)(p, dst)
if (src / "mod-settings.dat").exists():
    shutil.copy(src / "mod-settings.dat", mods / "mod-settings.dat")
mod_list["mods"] = [m for m in mod_list["mods"] if m["name"] != "bpgen-test"] + [{"name": "botmall-test", "enabled": True}]
(mods / "mod-list.json").write_text(json.dumps(mod_list))
OUT.unlink(missing_ok=True)
save = RUN / "botmall.zip"
save.unlink(missing_ok=True)
common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(mods)]
for args in (["--create", str(save)], ["--benchmark", str(save), "--benchmark-ticks", str(ticks + 5), "--disable-audio"]):
    p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode or "Error" in p.stdout and "non-recoverable" in p.stdout:
        print(p.stdout[-2500:])
        sys.exit(1)
print(OUT.read_text() if OUT.exists() else "no result")
