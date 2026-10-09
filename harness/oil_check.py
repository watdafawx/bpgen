"""Oil in the bus design: an iron patch and an oil field (6 wells) picked; pumpjacks piped to an oil block whose plastic
and sulfur go onto bus lanes. Fails on overlaps, a crude pipe not routed, no plastic/sulfur lanes or more than one
pole network. With --run: built in a headless game (an infinity pipe of water at the block's water inlet, coal in its
chest), the plastic and sulfur reaching the bus's end counted. python harness/oil_check.py [--run]"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bpgen import extend, harness, planner  # noqa: E402
from bpgen.config import PATHS  # noqa: E402
from bpgen.service import Service  # noqa: E402

RUN = ROOT / "run"
IRON = (100, 129, -50, -21)
WELLS = [(-90 + 7 * i, -70 + 5 * (i % 2)) for i in range(6)]  # (tile; each 50%)

CONTROL = r'''
local spec = require("spec")
local ends, counts = {}, {}
script.on_event(defines.events.on_tick, function(e)
  local s, force = game.surfaces.nauvis, game.forces.player
  if e.tick == 1 then
    local b = spec.box
    s.peaceful_mode = true
    force.enable_all_recipes()
    s.request_to_generate_chunks({ (b[1] + b[3]) / 2, (b[2] + b[4]) / 2 }, 7)
    s.force_generate_chunk_requests()
    for _, en in pairs(s.find_entities_filtered({ force = "enemy" })) do en.destroy() end
    local tiles = {}
    for x = b[1], b[3] do for y = b[2], b[4] do tiles[#tiles + 1] = { name = "lab-white", position = { x, y } } end end
    s.set_tiles(tiles)
    for _, en in pairs(s.find_entities_filtered({ area = { { b[1], b[2] }, { b[3] + 1, b[4] + 1 } } })) do
      if en.type ~= "character" then en.destroy() end
    end
    local p = spec.iron
    for x = p[1], p[2] do for y = p[3], p[4] do s.create_entity({ name = "iron-ore", position = { x + 0.5, y + 0.5 }, amount = 1000000 }) end end
    for _, w in ipairs(spec.wells) do s.create_entity({ name = "crude-oil", position = { w[1] + 0.5, w[2] + 0.5 }, amount = 150000 }) end
  elseif e.tick == 2 then
    local missing, made_ = 0, {}
    for i, en in ipairs(spec.entities) do
      local made = s.create_entity({ name = en.name, position = en.position, direction = en.direction, force = force,
        type = en.ug_type, recipe = en.recipe })
      if made and en.output_priority then made.splitter_output_priority = en.output_priority end
      if not made then missing = missing + 1 end
      made_[i] = made
    end
    for _, w in ipairs(spec.wires) do  -- (the blueprint's copper wires, as a paste makes them)
      local a, b = made_[w[1]], made_[w[3]]
      if a and b then a.get_wire_connector(w[2], true).connect_to(b.get_wire_connector(w[4], true)) end
    end
    for _, w in ipairs(spec.water) do
      local pipe = s.create_entity({ name = "infinity-pipe", position = { w[1] + 0.5, w[2] + 0.5 }, force = force })
      pipe.set_infinity_pipe_filter({ name = "water", percentage = 1, mode = "at-least" })
    end
    for _, c in pairs(s.find_entities_filtered({ name = "wooden-chest" })) do c.insert({ name = "coal", count = 5000 }) end
    for _, p in pairs(s.find_entities_filtered({ type = "electric-pole" })) do
      local at = s.find_non_colliding_position("electric-energy-interface", p.position, 3, 1)
      if at then
        local eei = s.create_entity({ name = "electric-energy-interface", position = at, force = force })
        eei.power_production, eei.electric_buffer_size = 1e10, 1e10
        break
      end
    end
    local nets = {}
    for _, q in pairs(s.find_entities_filtered({ type = "electric-pole" })) do nets[q.electric_network_id] = true end
    for i, ln in ipairs(spec.lanes) do
      ends[i] = s.find_entity("transport-belt", { ln["end"][1] + 0.5, ln["end"][2] + 0.5 })
      counts[i] = 0
    end
    helpers.write_file("bpgen/oil.txt", "not built " .. missing .. "\nnetworks " .. table_size(nets) .. "\n", false)
  elseif e.tick % 4 == 0 then
    for i, belt in pairs(ends) do
      for l = 1, 2 do
        local line = belt.get_transport_line(l)
        if e.tick > spec.warmup then counts[i] = counts[i] + line.get_item_count() end
        line.clear()
      end
    end
  end
  if e.tick == spec.ticks then
    local out = {}
    for i, ln in ipairs(spec.lanes) do
      out[#out + 1] = "lane " .. ln.item .. " " .. string.format("%.3f", counts[i] / ((spec.ticks - spec.warmup) / 60))
    end
    local names, by = {}, {}
    for k, v in pairs(defines.entity_status) do names[v] = k end
    for _, m in pairs(s.find_entities_filtered({ name = { "pumpjack", "oil-refinery", "chemical-plant" } })) do
      local k = m.name .. ":" .. (names[m.status] or "?")
      by[k] = (by[k] or 0) + 1
    end
    for k, v in pairs(by) do out[#out + 1] = "status " .. k .. " " .. v end
    for _, m in pairs(s.find_entities_filtered({ name = "pumpjack" })) do
      local conns = m.fluidbox.get_connections(1)
      out[#out + 1] = "status pj @" .. m.position.x .. "," .. m.position.y .. " " .. (names[m.status] or "?") .. " connections " .. #conns
        .. " pipe at out: " .. tostring(s.find_entities_filtered({ position = { m.position.x + 1, m.position.y - 2 }, type = { "pipe", "pipe-to-ground" } })[1] ~= nil)
    end
    helpers.write_file("bpgen/oil.txt", table.concat(out, "\n") .. "\n", true)
  end
end)
'''


def main():
    s = Service("vanilla")
    planner.configure(s.data)
    x1, x2, y1, y2 = IRON
    snap = {"area": [-150, -200, 250, 200], "entities": [], "obstacles": [], "water": {},
            "resources": {"iron-ore": {str(y): [[x1, x2]] for y in range(y1, y2 + 1)}},
            "fluid_resources": [{"name": "crude-oil", "x": x + 0.5, "y": y + 0.5, "amount": 150000} for x, y in WELLS]}
    out = s.plan({"mode": "busdesign", "snapshot": snap, "origin": {"x": 0, "y": 0}, "belt": "transport-belt",
                  "furnace": "electric-furnace", "length": 20, "wood": False,
                  "patches": [[x1 - 5, y1 - 5, x2 + 6, y2 + 6], [-100, -80, -40, -55]]})
    print("\n".join(out["notes"]))
    seen, bad = {}, []
    for e in out["entities"]:
        for t in extend.footprint(e):
            if t in seen:
                bad.append((t, seen[t]["name"], e["name"]))
            seen[t] = e
    assert not bad, f"{len(bad)} overlapping tiles, first: {bad[:3]}"
    items = [ln["item"] for ln in out["lanes"]]
    assert "plastic-bar" in items and "sulfur" in items, items
    assert not any("no pipe route" in n or "no way found" in n for n in out["notes"]), "not all routed"
    poles = [(e["position"]["x"], e["position"]["y"]) for e in out["entities"] if e["name"] == planner.POLE]
    group = {p: p for p in poles}

    def root(p):
        while group[p] != p:
            p = group[p]
        return p
    for i, p in enumerate(poles):
        for q in poles[i + 1:]:
            if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 <= planner.POLE_REACH ** 2:
                group[root(p)] = root(q)
    nets = len({root(p) for p in poles})
    assert nets == 1, f"{nets} pole networks"
    pumps = sum(e["name"] == "pumpjack" for e in out["entities"])
    print("lanes:", items, "| pumpjacks", pumps, "| water inlets", [i["position"] for i in out["inputs"]])
    if "--run" not in sys.argv:
        print("ok")
        return
    water = [(int(i["position"]["x"] // 1), int(i["position"]["y"] // 1)) for i in out["inputs"] if "water" in i["items"]]
    ents = [{k: e[k] for k in ("name", "position", "direction", "ug_type", "recipe", "output_priority") if k in e}
            for e in out["entities"]]
    xs = [e["position"]["x"] for e in ents]
    ys = [e["position"]["y"] for e in ents]
    warmup, ticks = (8 * 3600, 14 * 3600) if "--quick" not in sys.argv else (60, 600)
    spec = {"entities": ents, "wires": planner.pole_wires(ents), "iron": list(IRON), "wells": [list(w) for w in WELLS], "water": [list(w) for w in water],
            "lanes": out["lanes"], "box": [int(min(xs)) - 10, int(min(ys)) - 10, int(max(xs)) + 10, int(max(ys)) + 10],
            "warmup": warmup, "ticks": ticks}
    mods = RUN / "oil-mods"
    shutil.rmtree(mods, ignore_errors=True)
    mod = mods / "oil-test"
    mod.mkdir(parents=True)
    (mod / "info.json").write_text(json.dumps({"name": "oil-test", "version": "0.0.1", "title": "t", "author": "mtopfox",
                                               "factorio_version": "2.0", "dependencies": ["base"]}))
    (mod / "control.lua").write_text(CONTROL, encoding="utf-8")
    (mod / "spec.lua").write_text("return " + harness.to_lua(spec), encoding="utf-8")
    (mods / "mod-list.json").write_text(json.dumps({"mods": [{"name": "base", "enabled": True},
                                                             {"name": "oil-test", "enabled": True}]}))
    out_file = RUN / "script-output" / "bpgen" / "oil.txt"
    out_file.unlink(missing_ok=True)
    save = RUN / "oil.zip"
    save.unlink(missing_ok=True)
    common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(mods)]
    for args in (["--create", str(save)], ["--benchmark", str(save), "--benchmark-ticks", str(ticks + 5), "--disable-audio"]):
        p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode:
            print("\n".join(line for line in p.stdout.splitlines() if "rror" in line)[-2000:])
            sys.exit(1)
    text = out_file.read_text() if out_file.exists() else ""
    print(text)
    got = {}
    for line in text.splitlines():
        if line.startswith("lane "):
            _, item, rate = line.split()
            got[item] = got.get(item, 0) + float(rate)
    assert "not built 0" in text and "networks 1" in text, "not all built, or more than one pole network"
    for ln in out["lanes"]:
        if ln["item"] in ("plastic-bar", "sulfur"):
            assert got.get(ln["item"], 0) >= 0.85 * ln["rate"], f"{ln['item']}: {got.get(ln['item'])} of {ln['rate']:.3f}/s"
    print("ok")


if __name__ == "__main__":
    main()
