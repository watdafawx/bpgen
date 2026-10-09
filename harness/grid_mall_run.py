"""Headless run of a grid mall (bpgen/mall_grid.py): the starter mall's products planned, built, powered, its input
lanes kept fed at the top; after a while every product must be in its chest (parts made inside, components handed
down). python harness/grid_mall_run.py [minutes]"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bpgen import base, extend, harness, planner  # noqa: E402
from bpgen.config import PATHS  # noqa: E402
from bpgen.service import Service  # noqa: E402

RUN = ROOT / "run"
OUT = RUN / "script-output" / "bpgen" / "gridmall.txt"
MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 12  # (every product made: about 9 min)

CONTROL = r'''
local spec = require("spec")
local feeds = {}
script.on_event(defines.events.on_tick, function(e)
  local s, force = game.surfaces.nauvis, game.forces.player
  if e.tick == 1 then
    local b = spec.box
    s.peaceful_mode = true
    force.enable_all_recipes()
    s.request_to_generate_chunks({ (b[1] + b[3]) / 2, (b[2] + b[4]) / 2 }, 4)
    s.force_generate_chunk_requests()
    local tiles = {}
    for x = b[1], b[3] do for y = b[2], b[4] do tiles[#tiles + 1] = { name = "lab-white", position = { x, y } } end end
    s.set_tiles(tiles)
    for _, en in pairs(s.find_entities_filtered({ area = { { b[1], b[2] }, { b[3] + 1, b[4] + 1 } } })) do
      if en.type ~= "character" then en.destroy() end
    end
  elseif e.tick == 2 then
    -- (the blueprint itself, as the player gets it: pasted where it was planned, its ghosts revived)
    local inv = game.create_inventory(1)
    inv[1].import_stack(spec.bp)
    local missing = 0
    for _, g in pairs(inv[1].build_blueprint({ surface = s, force = force, position = spec.at,
        build_mode = defines.build_mode.forced })) do
      if g.valid then
        local _, made = g.revive()
        if not made and g.valid and g.type == "entity-ghost" then missing = missing + 1 end
      end
    end
    local limited = 0  -- (inserters that read their product's chest and run only while it's short)
    for _, ins in pairs(s.find_entities_filtered({ type = "inserter" })) do
      local cb = ins.get_control_behavior()
      if cb and cb.circuit_enable_disable and ins.get_circuit_network(defines.wire_connector_id.circuit_red) then
        limited = limited + 1
      end
    end
    local powered = {}
    for _, p in pairs(s.find_entities_filtered({ type = "electric-pole" })) do
      if not powered[p.electric_network_id] then
        local at = s.find_non_colliding_position("electric-energy-interface", p.position, 4, 1)
        if at then
          local eei = s.create_entity({ name = "electric-energy-interface", position = at, force = force })
          eei.power_production, eei.electric_buffer_size = 1e10, 1e10
          powered[p.electric_network_id] = true
        end
      end
    end
    for _, src in ipairs(spec.sources) do
      feeds[#feeds + 1] = { belt = s.find_entity("transport-belt", src.position), lanes = src.lanes }
    end
    helpers.write_file("bpgen/gridmall.txt", "not built " .. missing .. "\nlimited " .. limited .. "\n", false)
  elseif e.tick > 2 then  -- (the input lanes kept full, as a bus lane would)
    for _, f in ipairs(feeds) do
      if f.belt and f.belt.valid then
        for l = 1, 2 do
          local item = f.lanes[l]
          if item then
            local line = f.belt.get_transport_line(l)
            if line.can_insert_at_back() then line.insert_at_back({ name = item, count = 1 }) end
          end
        end
      end
    end
  end
  if e.tick == spec.ticks then
    local got = {}
    for _, c in pairs(s.find_entities_filtered({ type = "container" })) do
      for _, it in pairs(c.get_inventory(defines.inventory.chest).get_contents()) do
        got[it.name] = (got[it.name] or 0) + it.count
      end
    end
    local out = {}
    for _, p in ipairs(spec.products) do out[#out + 1] = "product " .. p .. " " .. (got[p] or 0) end
    local names, by = {}, {}
    for k, v in pairs(defines.entity_status) do names[v] = k end
    for _, m in pairs(s.find_entities_filtered({ type = { "assembling-machine", "inserter" } })) do
      local k = "status " .. m.type .. ":" .. (names[m.status] or "?")
      by[k] = (by[k] or 0) + 1
    end
    for k, v in pairs(by) do out[#out + 1] = k .. " " .. v end
    for _, m in pairs(s.find_entities_filtered({ type = "assembling-machine" })) do
      if m.status == defines.entity_status.item_ingredient_shortage or m.status == defines.entity_status.full_output then
        local have = {}
        for _, it in pairs(m.get_inventory(defines.inventory.assembling_machine_input).get_contents()) do have[#have + 1] = it.name .. "=" .. it.count end
        out[#out + 1] = "status detail " .. m.get_recipe().name .. " @" .. m.position.x .. "," .. m.position.y .. " " .. (names[m.status] or "?") .. " has " .. table.concat(have, " ")
      end
    end
    for _, m in pairs(s.find_entities_filtered({ type = "inserter" })) do
      if m.status == defines.entity_status.no_power then out[#out + 1] = "status unpowered inserter @" .. m.position.x .. "," .. m.position.y end
    end
    if spec.probe then  -- (a look around one machine: the belts and inserters beside it)
      local m = s.find_entities_filtered({ position = spec.probe, type = "assembling-machine" })[1]
      for _, en in pairs(s.find_entities_filtered({ area = { { spec.probe[1] - 4, spec.probe[2] - 2 }, { spec.probe[1] + 4, spec.probe[2] + 2 } }, type = { "inserter", "transport-belt" } })) do
        local what = en.type == "inserter" and ((names[en.status] or "?") .. " pick " .. en.pickup_position.x .. "," .. en.pickup_position.y .. " drop " .. en.drop_position.x .. "," .. en.drop_position.y)
          or ("L1 " .. en.get_transport_line(1).get_item_count() .. " L2 " .. en.get_transport_line(2).get_item_count() .. " dir " .. en.direction)
        out[#out + 1] = "status probe " .. en.name .. " @" .. en.position.x .. "," .. en.position.y .. " " .. what
      end
    end
    local onbelts = 0
    for _, f in ipairs(feeds) do if f.belt and f.belt.valid then onbelts = onbelts + f.belt.get_transport_line(1).get_item_count() + f.belt.get_transport_line(2).get_item_count() end end
    out[#out + 1] = "status feeds " .. #feeds .. " items at their heads " .. onbelts
    helpers.write_file("bpgen/gridmall.txt", table.concat(out, "\n") .. "\n", true)
  end
end)
'''


def main():
    s = Service("vanilla")
    planner.configure(s.data)
    prods = base.mall_products(s.data, "assembling-machine-2")
    o = s.plan({"mode": "mall", "layout": "grid", "products": prods, "machine": "assembling-machine-2",
                "belt": "transport-belt", "chest_limit": 1})
    print("\n".join(o["summary"]["notes"]))
    ents = o["entities"]
    bp, (x0, y0, w, h) = extend.absolute_blueprint(ents, "grid mall")
    xs = [e["position"]["x"] for e in ents]
    ys = [e["position"]["y"] for e in ents]
    box = [int(min(xs)) - 30, int(min(ys)) - 30, int(max(xs)) + 30, int(max(ys)) + 30]
    products = [base._only_result(s.data, r) if hasattr(base, "_only_result") else r for r in o["summary"]["products"]]
    ticks = int(MINUTES * 3600)
    limited = sum(1 for e in ents if e.get("circuit_to"))
    spec = {"bp": bp, "at": [x0 + w / 2, y0 + h / 2], "sources": o["sources"], "products": products, "box": box, "ticks": ticks,
            "probe": [float(a) for a in sys.argv[2].split(",")] if len(sys.argv) > 2 else None}
    mods = RUN / "gridmall-mods"
    shutil.rmtree(mods, ignore_errors=True)
    mod = mods / "gridmall-test"
    mod.mkdir(parents=True)
    (mod / "info.json").write_text(json.dumps({"name": "gridmall-test", "version": "0.0.1", "title": "t",
                                               "author": "mtopfox", "factorio_version": "2.0", "dependencies": ["base"]}))
    (mod / "control.lua").write_text(CONTROL, encoding="utf-8")
    (mod / "spec.lua").write_text("return " + harness.to_lua(spec), encoding="utf-8")
    (mods / "mod-list.json").write_text(json.dumps({"mods": [{"name": "base", "enabled": True},
                                                             {"name": "gridmall-test", "enabled": True}]}))
    OUT.unlink(missing_ok=True)
    save = RUN / "gridmall.zip"
    save.unlink(missing_ok=True)
    common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(mods)]
    for args in (["--create", str(save)], ["--benchmark", str(save), "--benchmark-ticks", str(ticks + 5),
                                           "--disable-audio"]):
        p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode:
            print("\n".join(line for line in p.stdout.splitlines() if "rror" in line)[-2500:])
            sys.exit(1)
    text = OUT.read_text() if OUT.exists() else ""
    print(text)
    assert "not built 0" in text, "entities not built"
    assert f"limited {limited}\n" in text, f"the game read back fewer than {limited} chest-limited inserters"
    empty = [line.split()[1] for line in text.splitlines() if line.startswith("product ") and line.endswith(" 0")]
    assert not empty, f"nothing made of: {empty}"
    print("ok")


if __name__ == "__main__":
    main()
