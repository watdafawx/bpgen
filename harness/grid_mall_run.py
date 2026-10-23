"""Headless run of a grid mall (bpgen/mall_grid.py): the starter mall's products planned, built, powered, its input
lanes kept fed at the top; after a while every product must be in its chest (parts made inside, components handed
down). --pack: the loop mall of a Krastorio-style list with your own mod pack (bpgen's pack data; a game with your
enabled mods linked in, and your mod settings; bpgen and fse left out). --sushi: the loop mall (bpgen/mall_sushi.py) instead, with products needing lubricant and water
(infinity pipes on its fluid inputs). python harness/grid_mall_run.py [minutes] [--sushi]"""
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
SUSHI = "--sushi" in sys.argv
PACK = "--pack" in sys.argv  # (the loop mall of PACK_MALL with your own mod pack: its data, its game)
sys.argv = [a for a in sys.argv if a not in ("--sushi", "--pack")]
SUSHI = SUSHI or PACK
PACK_MALL = ["transport-belt", "fast-transport-belt", "express-transport-belt", "underground-belt",
             "fast-underground-belt", "express-underground-belt", "splitter", "fast-splitter", "express-splitter",
             "burner-inserter", "inserter", "long-handed-inserter", "fast-inserter", "burner-mining-drill",
             "electric-mining-drill", "assembling-machine-1", "assembling-machine-2", "gun-turret", "small-electric-pole",
             "medium-electric-pole", "pipe", "pipe-to-ground", "stone-furnace", "steel-furnace", "solar-panel",
             "wooden-chest", "iron-chest", "steel-chest", "boiler", "steam-engine", "offshore-pump", "lab", "concrete",
             "stone-wall", "repair-pack"]
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
      if src.kind == "fluid" then  -- (a pipe of it, as from the base's)
        local p = s.create_entity({ name = "infinity-pipe", position = src.position, force = force })
        p.set_infinity_pipe_filter({ name = src.fluid, percentage = 1 })
      else
        feeds[#feeds + 1] = { belt = s.find_entity("transport-belt", src.position), lanes = src.lanes }
      end
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
  if e.tick == spec.ticks - 60 then  -- (where each item on a belt is: a second later, which haven't moved)
    storage.seen = {}
    for _, b in pairs(s.find_entities_filtered({ type = "transport-belt" })) do
      for l = 1, 2 do
        for _, it in pairs(b.get_transport_line(l).get_detailed_contents()) do storage.seen[it.unique_id] = it.position end
      end
    end
  end
  if e.tick == spec.ticks then
    local stuck = {}
    for _, b in pairs(s.find_entities_filtered({ type = "transport-belt" })) do
      for l = 1, 2 do
        for _, it in pairs(b.get_transport_line(l).get_detailed_contents()) do
          if storage.seen and storage.seen[it.unique_id] == it.position and b.belt_neighbours.outputs[1] then
            local k = it.stack.name
            stuck[k] = (stuck[k] or 0) + 1
          end
        end
      end
    end
    for _, b in pairs(s.find_entities_filtered({ type = "transport-belt" })) do  -- (a loop mall: what its reader sees)
      local cb = b.get_control_behavior()
      local net = cb and cb.read_contents and b.get_circuit_network(defines.wire_connector_id.circuit_red)
      if net then
        local sig = {}
        for _, x in pairs(net.signals or {}) do sig[#sig + 1] = x.signal.name .. "=" .. x.count end
        table.sort(sig)
        helpers.write_file("bpgen/gridmall.txt", "status reader " .. table.concat(sig, " ") .. "\n", true)
      end
    end
    local held, cols = {}, {}  -- (items in machines' inputs; gears by belt column)
    for _, m in pairs(s.find_entities_filtered({ type = "assembling-machine" })) do
      for _, it in pairs(m.get_inventory(defines.inventory.crafter_input).get_contents()) do
        held[it.name] = (held[it.name] or 0) + it.count
      end
    end
    for _, b in pairs(s.find_entities_filtered({ type = "transport-belt" })) do
      local n = b.get_item_count("iron-gear-wheel")
      if n > 0 then
        local k = math.floor(b.position.x) .. (b.direction == 0 and "N" or b.direction == 8 and "S" or "x")
        cols[k] = (cols[k] or 0) + n
      end
    end
    local hs, cs = {}, {}
    for k, v in pairs(held) do hs[#hs + 1] = k .. "=" .. v end
    for k, v in pairs(cols) do cs[#cs + 1] = k .. "=" .. v end
    table.sort(hs)
    table.sort(cs)
    helpers.write_file("bpgen/gridmall.txt", "status held " .. table.concat(hs, " ") .. "\nstatus gears " ..
      table.concat(cs, " ") .. "\n", true)
    local st = {}
    for k, v in pairs(stuck) do st[#st + 1] = k .. "=" .. v end
    table.sort(st)
    helpers.write_file("bpgen/gridmall.txt", "status stuck " .. #st .. " " .. table.concat(st, " ", 1, math.min(#st, 40)) .. "\n", true)
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
        for _, it in pairs(m.get_inventory(defines.inventory.crafter_input).get_contents()) do have[#have + 1] = it.name .. "=" .. it.count end
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
    local carried = {}  -- (what all the belts hold: a loop mall's mix)
    for _, b in pairs(s.find_entities_filtered({ type = "transport-belt" })) do
      for l = 1, 2 do
        for _, it in pairs(b.get_transport_line(l).get_contents()) do carried[it.name] = (carried[it.name] or 0) + it.count end
      end
    end
    local cs = {}
    for k, v in pairs(carried) do cs[#cs + 1] = k .. "=" .. v end
    table.sort(cs)
    out[#out + 1] = "status on belts " .. table.concat(cs, " ")
    local onbelts = 0
    for _, f in ipairs(feeds) do if f.belt and f.belt.valid then onbelts = onbelts + f.belt.get_transport_line(1).get_item_count() + f.belt.get_transport_line(2).get_item_count() end end
    out[#out + 1] = "status feeds " .. #feeds .. " items at their heads " .. onbelts
    helpers.write_file("bpgen/gridmall.txt", table.concat(out, "\n") .. "\n", true)
  end
end)
'''


def main():
    s = Service("pack" if PACK else "vanilla")
    planner.configure(s.data)
    prods = PACK_MALL if PACK else base.mall_products(s.data, "assembling-machine-2")
    if SUSHI and not PACK:
        prods = prods + ["express-transport-belt", "concrete"]
    o = s.plan({"mode": "mall", "layout": "sushi" if SUSHI else "grid", "products": prods, "machine": "assembling-machine-2",
                "belt": "transport-belt", "chest_limit": 1})
    print("\n".join(o["summary"]["notes"]))
    ents = o["entities"]
    bp, (x0, y0, w, h) = extend.absolute_blueprint(ents, "grid mall")
    xs = [e["position"]["x"] for e in ents]
    ys = [e["position"]["y"] for e in ents]
    box = [int(min(xs)) - 30, int(min(ys)) - 30, int(max(xs)) + 30, int(max(ys)) + 30]
    from bpgen.mall_grid import _result
    products = [_result(s.data, r) or r for r in o["summary"]["products"]]
    ticks = int(MINUTES * 3600)
    limited = sum(1 for e in ents if e.get("circuit_to") and "inserter" in e["name"])
    spec = {"bp": bp, "at": [x0 + w / 2, y0 + h / 2], "sources": o["sources"], "products": products, "box": box, "ticks": ticks,
            "probe": [float(a) for a in sys.argv[2].split(",")] if len(sys.argv) > 2 else None}
    mods = RUN / ("gridmall-pack-mods" if PACK else "gridmall-mods")
    shutil.rmtree(mods, ignore_errors=True)
    enabled = ["base"]
    if PACK:  # (your enabled mods, linked: nothing copied, nothing of yours changed)
        enabled = harness.link_user_mods(mods)
    mod = mods / "gridmall-test"
    mod.mkdir(parents=True)
    (mod / "info.json").write_text(json.dumps({"name": "gridmall-test", "version": "0.0.1", "title": "t",
                                               "author": "mtopfox", "factorio_version": "2.1", "dependencies": ["base"]}))
    (mod / "control.lua").write_text(CONTROL, encoding="utf-8")
    (mod / "spec.lua").write_text("return " + harness.to_lua(spec), encoding="utf-8")
    (mods / "mod-list.json").write_text(json.dumps({"mods": [{"name": n, "enabled": True}
                                                             for n in enabled + ["gridmall-test"]]}))
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
