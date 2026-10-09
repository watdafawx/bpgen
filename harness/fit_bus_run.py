"""Headless run of a starter base fitted to a bus design (run mod/test/fit_bus_test.py first: run/fit-<dir>.json):
the ore patches made, the bus design and the base built together, every pole network powered, then run. Plates
must reach the base through its head and science packs come out of it.
    python harness/fit_bus_run.py [direction] [minutes]"""
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
OUT = RUN / "script-output" / "bpgen" / "fit.txt"
DIRECTION = sys.argv[1] if len(sys.argv) > 1 else "north"
MINUTES = float(sys.argv[2]) if len(sys.argv) > 2 else 10

CONTROL = r'''
local spec = require("spec")
script.on_event(defines.events.on_tick, function(e)
  local s, force = game.surfaces.nauvis, game.forces.player
  if e.tick == 1 then
    local b = spec.box
    s.peaceful_mode = true
    force.enable_all_recipes()
    s.request_to_generate_chunks({ (b[1] + b[3]) / 2, (b[2] + b[4]) / 2 }, math.ceil(math.max(b[3] - b[1], b[4] - b[2]) / 64) + 1)
    s.force_generate_chunk_requests()
    for _, en in pairs(s.find_entities_filtered({ force = "enemy" })) do en.destroy() end
    local tiles = {}
    for x = b[1], b[3] do for y = b[2], b[4] do tiles[#tiles + 1] = { name = "lab-white", position = { x, y } } end end
    s.set_tiles(tiles)
    for _, en in pairs(s.find_entities_filtered({ area = { { b[1], b[2] }, { b[3] + 1, b[4] + 1 } } })) do
      if en.type ~= "character" then en.destroy() end
    end
    for name, p in pairs(spec.patches) do
      for x = p[1], p[2] do for y = p[3], p[4] do
        s.create_entity({ name = name, position = { x + 0.5, y + 0.5 }, amount = 1000000 })
      end end
    end
  elseif e.tick == 2 then
    local missing = 0
    for _, en in ipairs(spec.entities) do
      local made = s.create_entity({ name = en.name, position = en.position, direction = en.direction, force = force,
        type = en.ug_type, recipe = en.recipe })
      if not made then missing = missing + 1 end
    end
    local powered = {}
    for _, p in pairs(s.find_entities_filtered({ type = "electric-pole" })) do
      if not powered[p.electric_network_id] then
        local at = s.find_non_colliding_position("electric-energy-interface", p.position, 3, 1)
        if at then
          local eei = s.create_entity({ name = "electric-energy-interface", position = at, force = force })
          eei.power_production, eei.electric_buffer_size = 1e10, 1e10
          powered[p.electric_network_id] = true
        end
      end
    end
    helpers.write_file("bpgen/fit.txt", "not built " .. missing .. "\n", false)
  elseif e.tick == spec.ticks then
    local st = force.get_item_production_statistics(s)
    local out = {}
    for _, n in ipairs({ "iron-plate", "copper-plate", "automation-science-pack", "logistic-science-pack" }) do
      out[#out + 1] = n .. " " .. st.get_input_count(n)
    end
    local names, by = {}, {}
    for k, v in pairs(defines.entity_status) do names[v] = k end
    for _, m in pairs(s.find_entities_filtered({ type = "assembling-machine" })) do
      local k = "status " .. (m.get_recipe() and m.get_recipe().name or "?") .. ":" .. (names[m.status] or "?")
      by[k] = (by[k] or 0) + 1
    end
    for k, v in pairs(by) do out[#out + 1] = k .. " " .. v end
    local t2 = 0
    for _, pos in ipairs(spec.tier2) do
      local m = s.find_entities_filtered({ position = pos, type = "assembling-machine" })[1]
      if m then t2 = t2 + m.products_finished end
    end
    out[#out + 1] = "tier2 " .. t2
    helpers.write_file("bpgen/fit.txt", table.concat(out, "\n") .. "\n", true)
  end
end)
'''


def main():
    d = json.loads((RUN / f"fit-{DIRECTION}.json").read_text(encoding="utf-8"))
    ents = [{k: e[k] for k in ("name", "position", "direction", "ug_type", "recipe") if k in e}
            for e in d["bus"] + d["base"] + d.get("base2", [])]
    xs = [e["position"]["x"] for e in ents]
    ys = [e["position"]["y"] for e in ents]
    box = [int(min(xs)) - 10, int(min(ys)) - 10, int(max(xs)) + 10, int(max(ys)) + 10]
    ticks = int(MINUTES * 3600)
    spec = {"entities": ents, "patches": {k: list(v) for k, v in d["patches"].items()}, "box": box, "ticks": ticks,
            # (the second tier's science machines: they must make packs too, from the lanes the first passes on)
            "tier2": [e["position"] for e in d.get("base2", []) if (e.get("recipe") or "").endswith("science-pack")]}
    mods = RUN / "fit-mods"
    shutil.rmtree(mods, ignore_errors=True)
    mod = mods / "fit-test"
    mod.mkdir(parents=True)
    (mod / "info.json").write_text(json.dumps({"name": "fit-test", "version": "0.0.1", "title": "fit test",
                                               "author": "mtopfox", "factorio_version": "2.0", "dependencies": ["base"]}))
    (mod / "control.lua").write_text(CONTROL, encoding="utf-8")
    (mod / "spec.lua").write_text("return " + harness.to_lua(spec), encoding="utf-8")
    (mods / "mod-list.json").write_text(json.dumps({"mods": [{"name": "base", "enabled": True},
                                                             {"name": "fit-test", "enabled": True}]}))
    OUT.unlink(missing_ok=True)
    save = RUN / "fit.zip"
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
    got = dict(line.rsplit(" ", 1) for line in text.splitlines() if line)
    assert got.get("not built") == "0", "entities not built"
    for pack in ("automation-science-pack", "logistic-science-pack"):
        assert float(got.get(pack, 0)) > 0, f"no {pack} made"
    if d.get("base2"):
        assert float(got.get("tier2", 0)) > 0, "the second tier made no science"
    print("ok")


if __name__ == "__main__":
    main()
