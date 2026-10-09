"""Bus design "Add to my bus", no game unless asked: an existing 4-lane bus flowing north, a copper patch picked beside it;
the copper is mined, smelted and fed onto a new lane laid along the bus. Fails on overlaps, unrouted belts or no new
lane. With --run, builds it in a headless game (the old bus as plain belts) and counts the plates reaching the new
lane's end. python harness/addbus_check.py [--run] [furnace]"""
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
FURNACE = next((a for a in sys.argv[1:] if not a.startswith("--")), "electric-furnace")
PATCH = (60, 89, -80, -51)  # copper, x1 x2 y1 y2
BUS_X, BUS_Y = (20, 22, 24, 26), (-120, 0)  # 4 lanes, flowing north from y 0 to -120

CONTROL = r'''
local spec = require("spec")
local drain, count = nil, 0
script.on_event(defines.events.on_tick, function(e)
  local s, force = game.surfaces.nauvis, game.forces.player
  if e.tick == 1 then
    local b = spec.box
    s.peaceful_mode = true
    s.request_to_generate_chunks({ (b[1] + b[3]) / 2, (b[2] + b[4]) / 2 }, 6)
    s.force_generate_chunk_requests()
    for _, en in pairs(s.find_entities_filtered({ force = "enemy" })) do en.destroy() end
    local tiles = {}
    for x = b[1], b[3] do for y = b[2], b[4] do tiles[#tiles + 1] = { name = "lab-white", position = { x, y } } end end
    s.set_tiles(tiles)
    for _, en in pairs(s.find_entities_filtered({ area = { { b[1], b[2] }, { b[3] + 1, b[4] + 1 } } })) do
      if en.type ~= "character" then en.destroy() end
    end
    local p = spec.patch
    for x = p[1], p[2] do for y = p[3], p[4] do s.create_entity({ name = "copper-ore", position = { x + 0.5, y + 0.5 }, amount = 1000000 }) end end
  elseif e.tick == 2 then
    local missing = 0
    for _, en in ipairs(spec.entities) do
      local made = s.create_entity({ name = en.name, position = en.position, direction = en.direction, force = force,
        type = en.ug_type, recipe = en.recipe })
      if made and en.output_priority then made.splitter_output_priority = en.output_priority end
      if not made then missing = missing + 1 end
    end
    for _, p in pairs(s.find_entities_filtered({ type = "electric-pole" })) do  -- (one energy interface: one network)
      local at = s.find_non_colliding_position("electric-energy-interface", p.position, 3, 1)
      if at then
        local eei = s.create_entity({ name = "electric-energy-interface", position = at, force = force })
        eei.power_production, eei.electric_buffer_size = 1e10, 1e10
        break
      end
    end
    for _, c in pairs(s.find_entities_filtered({ name = "wooden-chest" })) do c.insert({ name = "coal", count = 5000 }) end
    drain = s.find_entity(spec.end_name, { spec["end"][1] + 0.5, spec["end"][2] + 0.5 })
    local nets = {}
    for _, q in pairs(s.find_entities_filtered({ type = "electric-pole" })) do nets[q.electric_network_id] = true end
    helpers.write_file("bpgen/addbus.txt", "not built " .. missing .. "\nnetworks " .. table_size(nets) .. "\n", false)
  elseif drain and e.tick % 4 == 0 then
    for l = 1, 2 do
      local line = drain.get_transport_line(l)
      if e.tick > spec.warmup then count = count + line.get_item_count() end
      line.clear()
    end
  end
  if e.tick == spec.ticks then
    helpers.write_file("bpgen/addbus.txt", "rate " .. string.format("%.2f", count / ((spec.ticks - spec.warmup) / 60)) .. "\n", true)
  end
end)
'''


def main():
    s = Service("vanilla")
    planner.configure(s.data)
    old = [{"name": "transport-belt", "type": "transport-belt", "position": {"x": x + 0.5, "y": y + 0.5}, "direction": 0,
            "lanes": [it, it]} for x, it in zip(BUS_X, ("iron-plate", "iron-plate", "copper-plate", "stone-brick"))
           for y in range(BUS_Y[0], BUS_Y[1] + 1)]
    x1, x2, y1, y2 = PATCH
    snap = {"area": [-150, -250, 250, 150], "entities": old, "obstacles": [], "water": {},
            "resources": {"copper-ore": {str(y): [[x1, x2]] for y in range(y1, y2 + 1)}}}
    out = s.plan({"mode": "busdesign", "add_to_bus": True, "snapshot": snap, "patches": [[x1 - 5, y1 - 5, x2 + 6, y2 + 6]],
                  "origin": {"x": 30, "y": 10}, "belt": "transport-belt", "furnace": FURNACE, "wood": False})
    print("\n".join(out["notes"]))
    seen, bad = {}, []
    for e in out["entities"] + [s.decorate(e) for e in old]:
        for t in extend.footprint(e):
            if t in seen:
                bad.append((t, seen[t]["name"], e["name"]))
            seen[t] = e
    assert not bad, f"{len(bad)} overlapping tiles, first: {bad[:3]}"
    assert not out["inputs"], f"not routed: {out['inputs']}"
    lane_x = {e["position"]["x"] for e in out["entities"] if e["name"] == "transport-belt"
              and BUS_Y[0] + 5 < e["position"]["y"] < BUS_Y[1] - 5 and e.get("direction", 0) == 0
              and abs(e["position"]["x"] - 23) < 12}
    new = sorted(x for x in lane_x if x - 0.5 not in BUS_X)
    assert new, "no new lane along the bus"
    print("new lane(s) at x", new)
    if "--run" not in sys.argv:
        print("ok")
        return
    end = (int(new[0] - 0.5), BUS_Y[0])
    ents = [{k: e[k] for k in ("name", "position", "direction", "ug_type", "recipe", "output_priority") if k in e}
            for e in out["entities"] + old]
    xs = [e["position"]["x"] for e in ents]
    ys = [e["position"]["y"] for e in ents]
    warmup, ticks = 5 * 3600, 9 * 3600
    spec = {"entities": ents, "patch": list(PATCH), "end": list(end), "end_name": "transport-belt",
            "box": [int(min(xs)) - 10, int(min(ys)) - 10, int(max(xs)) + 10, int(max(ys)) + 10],
            "warmup": warmup, "ticks": ticks}
    mods = RUN / "addbus-mods"
    shutil.rmtree(mods, ignore_errors=True)
    mod = mods / "addbus-test"
    mod.mkdir(parents=True)
    (mod / "info.json").write_text(json.dumps({"name": "addbus-test", "version": "0.0.1", "title": "t", "author": "mtopfox",
                                               "factorio_version": "2.0", "dependencies": ["base"]}))
    (mod / "control.lua").write_text(CONTROL, encoding="utf-8")
    (mod / "spec.lua").write_text("return " + harness.to_lua(spec), encoding="utf-8")
    (mods / "mod-list.json").write_text(json.dumps({"mods": [{"name": "base", "enabled": True},
                                                             {"name": "addbus-test", "enabled": True}]}))
    out_file = RUN / "script-output" / "bpgen" / "addbus.txt"
    out_file.unlink(missing_ok=True)
    save = RUN / "addbus.zip"
    save.unlink(missing_ok=True)
    common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(mods)]
    for args in (["--create", str(save)], ["--benchmark", str(save), "--benchmark-ticks", str(ticks + 5), "--disable-audio"]):
        p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode:
            print("\n".join(line for line in p.stdout.splitlines() if "rror" in line)[-2000:])
            sys.exit(1)
    text = out_file.read_text() if out_file.exists() else ""
    print(text)
    got = dict(line.rsplit(" ", 1) for line in text.splitlines() if line)
    assert got.get("not built") == "0" and got.get("networks") == "1", text
    planned = min(ln["rate"] for ln in out["lanes"])  # (one new lane measured: each carries about this)
    assert float(got.get("rate", 0)) >= 0.85 * planned, f"new lane carries {got.get('rate')}/s of {planned:.2f} planned"
    print("ok")


if __name__ == "__main__":
    main()
