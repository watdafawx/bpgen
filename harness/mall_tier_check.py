"""A grid mall as a tier on the bus: the bus design of fit_bus_test (iron, copper, coal, a wood lane), a starter base
fitted to it, then the starter mall beside that base on the lanes it passes on. Fails when the mall doesn't fit or
lands on the base or the bus. With --run: the bus, the base and the mall built and run headless (wood in the wood
lane's chest): the products in the mall's chests counted, most must be there.
python harness/mall_tier_check.py [--run] [minutes]"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bpgen import base, busdesign, extend, harness, planner  # noqa: E402
from bpgen.config import PATHS  # noqa: E402
from bpgen.service import Service  # noqa: E402

RUN = ROOT / "run"
PATCHES = {"iron-ore": (100, 139, -60, -11), "copper-ore": (100, 129, 20, 49), "coal": (160, 179, -20, -6)}
MINUTES = float(next((a for a in sys.argv[1:] if not a.startswith("--")), 30))
CONTROL = r'''
local spec = require("spec")
script.on_event(defines.events.on_tick, function(e)
  local s, force = game.surfaces.nauvis, game.forces.player
  if e.tick == 1 then
    local b = spec.box
    s.peaceful_mode = true
    force.enable_all_recipes()
    s.request_to_generate_chunks({ (b[1] + b[3]) / 2, (b[2] + b[4]) / 2 }, 8)
    s.force_generate_chunk_requests()
    for _, en in pairs(s.find_entities_filtered({ force = "enemy" })) do en.destroy() end
    local tiles = {}
    for x = b[1], b[3] do for y = b[2], b[4] do tiles[#tiles + 1] = { name = "lab-white", position = { x, y } } end end
    s.set_tiles(tiles)
    for _, en in pairs(s.find_entities_filtered({ area = { { b[1], b[2] }, { b[3] + 1, b[4] + 1 } } })) do
      if en.type ~= "character" then en.destroy() end
    end
    for name, p in pairs(spec.patches) do
      for x = p[1], p[2] do for y = p[3], p[4] do s.create_entity({ name = name, position = { x + 0.5, y + 0.5 }, amount = 1000000 }) end end
    end
  elseif e.tick == 2 then
    local missing, made_ = 0, {}
    for i, en in ipairs(spec.entities) do
      local made = s.create_entity({ name = en.name, position = en.position, direction = en.direction, force = force,
        type = en.ug_type, recipe = en.recipe, bar = en.bar })
      if made and en.output_priority then made.splitter_output_priority = en.output_priority end
      if made and en.control_behavior and en.control_behavior.circuit_condition then
        local cb = made.get_or_create_control_behavior()
        cb.circuit_enable_disable = true
        local c = en.control_behavior.circuit_condition
        cb.circuit_condition = { first_signal = { type = "item", name = c.first_signal.name }, constant = c.constant, comparator = c.comparator }
      end
      if not made then missing = missing + 1 end
      made_[i] = made
    end
    for _, w in ipairs(spec.wires) do
      local a, b = made_[w[1]], made_[w[3]]
      if a and b then a.get_wire_connector(w[2], true).connect_to(b.get_wire_connector(w[4], true)) end
    end
    for _, c in pairs(s.find_entities_filtered({ name = "wooden-chest" })) do
      if #c.get_inventory(defines.inventory.chest) > 0 and c.get_inventory(defines.inventory.chest).is_empty() then
        local near = s.find_entities_filtered({ position = c.position, radius = 1.6, name = "burner-inserter" })[1]
        if near then c.insert({ name = "wood", count = 2000 }) end
      end
    end
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
    helpers.write_file("bpgen/malltier.txt", "not built " .. missing .. "\nnetworks " .. table_size(nets) .. "\n", false)
  elseif e.tick == spec.ticks then
    local got = {}
    local b = spec.mall_box
    for _, c in pairs(s.find_entities_filtered({ area = { { b[1], b[2] }, { b[1] + b[3], b[2] + b[4] } }, type = "container" })) do
      for _, it in pairs(c.get_inventory(defines.inventory.chest).get_contents()) do got[it.name] = (got[it.name] or 0) + it.count end
    end
    local out = {}
    for _, p in ipairs(spec.products) do out[#out + 1] = "product " .. p .. " " .. (got[p] or 0) end
    helpers.write_file("bpgen/malltier.txt", table.concat(out, "\n") .. "\n", true)
  end
end)
'''


def main():
    last = Path(tempfile.mkdtemp()) / "bus_design.json"
    busdesign._last_file = lambda key=None: last
    s = Service("vanilla")
    planner.configure(s.data)
    runs = {n: {str(y): [[x1, x2]] for y in range(y1, y2 + 1)} for n, (x1, x2, y1, y2) in PATCHES.items()}
    snap = {"area": [-300, -300, 300, 300], "entities": [], "obstacles": [], "water": {}, "resources": runs}
    bus = s.plan({"mode": "busdesign", "snapshot": snap, "direction": "north", "origin": {"x": 0, "y": 0},
                  "patches": [[x1 - 5, y1 - 5, x2 + 6, y2 + 6] for x1, x2, y1, y2 in PATCHES.values()],
                  "belt": "transport-belt", "furnace": "electric-furnace", "length": 110})
    busdesign.save_last(bus, {"direction": "north"})
    t1 = s.plan({"mode": "base", "layout": "bus", "plates": True, "fit_bus": True, "mall": False, "belt": "transport-belt",
                 "spm": 10})
    assert t1.get("absolute"), t1["summary"].get("notes")
    products = base.mall_products(s.data, "assembling-machine-2")
    mall = s.plan({"mode": "mall", "layout": "grid", "products": products, "machine": "assembling-machine-2",
                   "belt": "transport-belt", "chest_limit": 1, "add_to": t1["blueprint"]})
    print("\n".join(mall["summary"]["notes"]))
    assert mall.get("absolute"), "not fitted"
    old = extend.plan_tiles(t1["sections"][0]["result"]["entities"]) | extend.plan_tiles(bus["entities"])
    clash = extend.plan_tiles(mall["entities"]) & old
    assert not clash, f"the mall sits on the base or the bus at {sorted(clash)[:4]}"
    made = mall["summary"]["products"]
    print(f"{len(made)} of {len(products)} products")
    if "--run" not in sys.argv:
        print("ok")
        return
    ents = [{k: e[k] for k in ("name", "position", "direction", "ug_type", "recipe", "output_priority", "bar",
                               "control_behavior") if k in e}
            for e in bus["entities"] + t1["sections"][0]["result"]["entities"] + mall["entities"]]
    xs = [e["position"]["x"] for e in ents]
    ys = [e["position"]["y"] for e in ents]
    items = [base_item for base_item in (s.data.raw["recipe"][r]["results"][0]["name"] for r in made)]
    ticks = int(MINUTES * 3600)
    spec = {"entities": ents, "wires": planner.pole_wires(ents), "patches": {k: list(v) for k, v in PATCHES.items()},
            "box": [int(min(xs)) - 10, int(min(ys)) - 10, int(max(xs)) + 10, int(max(ys)) + 10],
            "mall_box": list(mall["absolute"]["box"]), "products": items, "ticks": ticks}
    mods = RUN / "malltier-mods"
    shutil.rmtree(mods, ignore_errors=True)
    mod = mods / "malltier-test"
    mod.mkdir(parents=True)
    (mod / "info.json").write_text(json.dumps({"name": "malltier-test", "version": "0.0.1", "title": "t",
                                               "author": "mtopfox", "factorio_version": "2.0", "dependencies": ["base"]}))
    (mod / "control.lua").write_text(CONTROL, encoding="utf-8")
    (mod / "spec.lua").write_text("return " + harness.to_lua(spec), encoding="utf-8")
    (mods / "mod-list.json").write_text(json.dumps({"mods": [{"name": "base", "enabled": True},
                                                             {"name": "malltier-test", "enabled": True}]}))
    out_file = RUN / "script-output" / "bpgen" / "malltier.txt"
    out_file.unlink(missing_ok=True)
    save = RUN / "malltier.zip"
    save.unlink(missing_ok=True)
    common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(mods)]
    for args in (["--create", str(save)], ["--benchmark", str(save), "--benchmark-ticks", str(ticks + 5), "--disable-audio"]):
        p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode:
            print("\n".join(line for line in p.stdout.splitlines() if "rror" in line)[-2000:])
            sys.exit(1)
    text = out_file.read_text() if out_file.exists() else ""
    print(text)
    got = {line.split()[1]: int(line.split()[2]) for line in text.splitlines() if line.startswith("product ")}
    assert "not built 0" in text and "networks 1" in text, "not all built, or more than one pole network"
    have = [k for k, v in got.items() if v > 0]
    print(f"{len(have)} of {len(got)} products in their chests")
    assert len(have) >= 0.8 * len(got), f"too few products made: {sorted(set(got) - set(have))}"
    print("ok")


if __name__ == "__main__":
    main()
