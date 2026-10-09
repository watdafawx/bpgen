"""Headless check of the bus design: the ore patches of busdesign_check made for real, the planned drills, trunks,
smelter columns, balancers and bus built, every pole network powered; after a warmup, what reaches each bus lane's
end is counted (and cleared). Fails when a lane gets much less than planned or a balanced group is uneven.
    python harness/busdesign_test.py [vanilla|pack] [minutes] [north|east|south|west] [furnace]"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bpgen import harness  # noqa: E402
from bpgen.config import PATHS  # noqa: E402
from bpgen.service import Service  # noqa: E402

MODE = sys.argv[1] if len(sys.argv) > 1 else "vanilla"
MINUTES = float(sys.argv[2]) if len(sys.argv) > 2 else 3
DIRECTION = sys.argv[3] if len(sys.argv) > 3 else "north"
FURNACE = sys.argv[4] if len(sys.argv) > 4 else "electric-furnace"  # (stone-furnace: burner columns, coal from the patch)
WARMUP = (12 if "stone" in FURNACE or "steel" in FURNACE else 4) * 3600  # (the trunks are long: ore takes a while to
# reach the furnaces; burner ones also fill their fuel, one column after the other along the coal belt)
RUN = ROOT / "run"
OUT = RUN / "script-output" / "bpgen" / "busdesign.txt"
PATCHES = {"iron-ore": (100, 139, -60, -11), "copper-ore": (100, 129, 20, 49), "coal": (160, 179, -20, -6)}

CONTROL = r'''
local spec = require("spec")
local ends, counts = {}, {}
script.on_event(defines.events.on_tick, function(e)
  local s = game.surfaces.nauvis
  local force = game.forces.player
  if e.tick == 1 then
    local b = spec.box
    s.peaceful_mode = true  -- (biters would eat the drills)
    for _, en in pairs(s.find_entities_filtered({ force = "enemy" })) do en.destroy() end
    s.request_to_generate_chunks({ (b[1] + b[3]) / 2, (b[2] + b[4]) / 2 },
      math.ceil(math.max(b[3] - b[1], b[4] - b[2]) / 64) + 1)
    s.force_generate_chunk_requests()
    local tiles = {}
    for x = b[1], b[3] do for y = b[2], b[4] do tiles[#tiles + 1] = { name = "lab-white", position = { x, y } } end end
    s.set_tiles(tiles)
    for _, ent in pairs(s.find_entities_filtered({ area = { { b[1], b[2] }, { b[3] + 1, b[4] + 1 } } })) do
      if ent.type ~= "character" then ent.destroy() end
    end
    for name, p in pairs(spec.patches) do
      for x = p[1], p[2] do for y = p[3], p[4] do
        s.create_entity({ name = name, position = { x + 0.5, y + 0.5 }, amount = 1000000 })
      end end
    end
  elseif e.tick == 2 then
    for _, en in ipairs(spec.entities) do
      local made = s.create_entity({ name = en.name, position = en.position, direction = en.direction, force = force,
        type = en.ug_type, recipe = en.recipe })
      if made and en.output_priority then made.splitter_output_priority = en.output_priority end
      if not made then helpers.write_file("bpgen/busdesign.txt", "not built: " .. en.name .. " at " .. en.position.x .. "," .. en.position.y .. "\n", true) end
    end
    local powered = {}
    for _, p in pairs(s.find_entities_filtered({ type = "electric-pole" })) do
      if not powered[p.electric_network_id] then
        local at = s.find_non_colliding_position("electric-energy-interface", p.position, 3, 1)
        if at then
          local eei = s.create_entity({ name = "electric-energy-interface", position = at, force = force })
          eei.power_production = 1e10
          eei.electric_buffer_size = 1e10
          powered[p.electric_network_id] = true
        end
      end
    end
    for _, c in pairs(s.find_entities_filtered({ name = "wooden-chest" })) do c.insert({ name = "wood", count = 2000 }) end
    for i, ln in ipairs(spec.lanes) do
      ends[i] = s.find_entity("transport-belt", { ln["end"][1] + 0.5, ln["end"][2] + 0.5 })
      counts[i] = 0
    end
  end
  if e.tick > 2 and e.tick % 3600 == 0 then  -- (burner columns: how many are short of fuel, minute by minute)
    local s = game.surfaces.nauvis
    local n, fuel, ore = 0, 0, 0
    for _, f in pairs(s.find_entities_filtered({ type = "furnace" })) do
      n = n + 1
      if f.status == defines.entity_status.no_fuel then fuel = fuel + 1 end
      if f.status == defines.entity_status.no_ingredients then ore = ore + 1 end
    end
    if n > 0 then helpers.write_file("bpgen/busdesign.txt", "status minute " .. math.floor(e.tick / 3600) .. ": " .. n
      .. " furnaces, " .. fuel .. " without fuel, " .. ore .. " without ore\n", true) end
  end
  if e.tick < 3 then
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
      out[#out + 1] = ln.item .. " " .. string.format("%.2f", counts[i] / ((spec.ticks - spec.warmup) / 60))
    end
    local st = {}
    local names = {}
    for k, v in pairs(defines.entity_status) do names[v] = k end
    for _, m in pairs(s.find_entities_filtered({ type = { "mining-drill", "furnace", "assembling-machine" } })) do
      local k = m.name .. ":" .. (names[m.status] or "?")
      st[k] = (st[k] or 0) + 1
    end
    for k, v in pairs(st) do out[#out + 1] = "status " .. k .. " " .. v end
    out[#out + 1] = "status drills " .. #s.find_entities_filtered({ name = "electric-mining-drill" })
    local ps = force.get_item_production_statistics(s)
    for _, n in ipairs({ "iron-plate", "copper-plate", "iron-ore", "copper-ore", "coal" }) do
      out[#out + 1] = "status made " .. n .. " " .. ps.get_input_count(n) .. " used " .. ps.get_output_count(n)
    end
    -- (burner columns: what's on each ore belt's lanes at its foot, where the coal comes on)
    for _, f in pairs(s.find_entities_filtered({ name = { "stone-furnace", "steel-furnace" } })) do
      local below = s.find_entities_filtered({ position = { f.position.x + 2.5, f.position.y + 1.5 }, type = "transport-belt" })[1]
      if below and not s.find_entities_filtered({ position = { f.position.x + 2.5, f.position.y + 3.5 }, name = { "stone-furnace", "steel-furnace" } })[1]
          and not s.find_entities_filtered({ position = { f.position.x + 0.5, f.position.y + 2.5 }, name = { "stone-furnace", "steel-furnace" } })[1] then
        local foot = below
        for _ = 1, 0 do
          local nxt = s.find_entities_filtered({ position = { foot.position.x, foot.position.y + 1 }, type = "transport-belt" })[1]
          if not nxt then break end
          foot = nxt
        end
        local function lane(l)
          local t = {}
          for _, it in pairs(foot.get_transport_line(l).get_contents()) do t[#t + 1] = it.name .. "=" .. it.count end
          return table.concat(t, ",")
        end
        out[#out + 1] = "status foot @" .. foot.position.x .. "," .. foot.position.y .. " left[" .. lane(1) .. "] right[" .. lane(2) .. "]"
      end
    end
    local nets = {}
    for _, p in pairs(s.find_entities_filtered({ type = "electric-pole" })) do nets[p.electric_network_id] = true end
    out[#out + 1] = "status networks " .. table_size(nets)
    helpers.write_file("bpgen/busdesign.txt", table.concat(out, "\n") .. "\n", true)
  end
end)
'''


def main():
    s = Service(MODE)
    runs = {name: {str(y): [[x1, x2]] for y in range(y1, y2 + 1)} for name, (x1, x2, y1, y2) in PATCHES.items()}
    snap = {"area": [-150, -200, 250, 200], "entities": [], "obstacles": [], "water": {}, "resources": runs}
    patches = [[x1 - 5, y1 - 5, x2 + 6, y2 + 6] for x1, x2, y1, y2 in PATCHES.values()]
    furnace = FURNACE
    out = s.plan({"mode": "busdesign", "snapshot": snap, "patches": patches, "origin": {"x": 0, "y": 0},
                  "direction": DIRECTION, "belt": "transport-belt", "furnace": furnace, "group": 4, "gap": 4,
                  "length": 20})
    print("\n".join(out["notes"]))
    x0, y0, w, h = out["box"]
    ticks = WARMUP + int(MINUTES * 3600)
    spec = {"entities": [{k: e[k] for k in ("name", "position", "direction", "ug_type", "recipe", "output_priority") if k in e}
                         for e in out["entities"]],
            "lanes": out["lanes"], "patches": {k: list(v) for k, v in PATCHES.items()},
            "box": [x0 - 8, y0 - 8, x0 + w + 8, y0 + h + 8], "ticks": ticks, "warmup": WARMUP}
    mods = RUN / "busdesign-mods"
    if mods.exists():
        shutil.rmtree(mods)
    mods.mkdir(parents=True)
    src = s.mod_dir
    mod = mods / "busdesign-test"
    mod.mkdir()
    (mod / "info.json").write_text(json.dumps({"name": "busdesign-test", "version": "0.0.1", "title": "bus design test",
                                               "author": "mtopfox", "factorio_version": "2.0", "dependencies": ["base"]}))
    (mod / "control.lua").write_text(CONTROL, encoding="utf-8")
    (mod / "spec.lua").write_text("return " + harness.to_lua(spec), encoding="utf-8")
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
    mod_list["mods"] = ([m for m in mod_list["mods"] if m["name"] != "bpgen-test"]
                        + [{"name": "busdesign-test", "enabled": True}])
    (mods / "mod-list.json").write_text(json.dumps(mod_list))
    OUT.unlink(missing_ok=True)
    save = RUN / "busdesign.zip"
    save.unlink(missing_ok=True)
    common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(mods)]
    for args in (["--create", str(save)], ["--benchmark", str(save), "--benchmark-ticks", str(ticks + 5),
                                           "--disable-audio"]):
        p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode or "Error" in p.stdout and "non-recoverable" in p.stdout:
            print(p.stdout[-2500:])
            sys.exit(1)
    text = OUT.read_text() if OUT.exists() else ""
    print(text or "no result")
    got = [float(line.split()[1]) for line in text.splitlines() if line and not line.startswith(("status", "not "))]
    assert len(got) == len(out["lanes"]), "lanes not measured"
    bad = [(ln["item"], g, ln["rate"]) for ln, g in zip(out["lanes"], got)
           if g < 0.85 * ln["rate"] or (ln["item"] == "wood" and g <= 0)]  # (wood: from its chest, by hand)
    groups = {}
    for ln, g in zip(out["lanes"], got):
        groups.setdefault((ln["item"], ln["x"] // 8), []).append(g)
    uneven = [v for v in groups.values() if len(v) == 4 and max(v) - min(v) > 0.1 * max(v)]
    assert "not built" not in text, "entities not built"
    drills = sum(e["name"] == "electric-mining-drill" for e in out["entities"])
    assert f"electric-mining-drill:working {drills}\n" in text, f"not all {drills} drills working"
    assert not bad, f"lanes short of plan (item, got/s, planned/s): {bad}"
    assert not uneven, f"balanced groups uneven: {uneven}"
    print("ok")


if __name__ == "__main__":
    main()
