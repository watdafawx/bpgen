"""Headless run of a generated ship (bpgen/ship.py) on a real space platform: created over Nauvis with the starter
pack, the ship pasted round its hub (bpgen's mod wires the hub's inserters as they're built), left in orbit while
asteroids come by, then sent to Vulcanus. Reports what the hub holds, the thrusters' fuel and oxidizer, the turrets'
ammo and whether it moves. python harness/ship_run.py [minutes in orbit] [thrusters]"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bpgen import extend, harness, planner, ship  # noqa: E402
from bpgen.config import PATHS, plain_env  # noqa: E402
from bpgen.service import Service  # noqa: E402

RUN = ROOT / "run"
OUT = RUN / "script-output" / "bpgen" / "ship.txt"
MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 10
THRUSTERS = int(sys.argv[2]) if len(sys.argv) > 2 else 2

CONTROL = r'''
local spec = require("spec")
local function out(s) helpers.write_file("bpgen/ship.txt", s .. "\n", true) end
local function report(p, what)
  local hub = p.hub
  local inv = hub.get_inventory(defines.inventory.hub_main)
  local c = {}
  for _, it in pairs(inv.get_contents()) do c[#c + 1] = it.name .. "=" .. it.count end
  table.sort(c)
  out(what .. " hub " .. table.concat(c, " "))
  local fl = {}
  for _, t in pairs(p.surface.find_entities_filtered({ type = "thruster" })) do
    for i = 1, #t.fluidbox do
      local f = t.fluidbox[i]
      fl[#fl + 1] = (f and f.name or "empty") .. "=" .. (f and math.floor(f.amount) or 0)
    end
  end
  out(what .. " thrusters " .. table.concat(fl, " "))
  local ammo = 0
  for _, t in pairs(p.surface.find_entities_filtered({ type = "ammo-turret" })) do
    ammo = ammo + t.get_inventory(defines.inventory.turret_ammo).get_item_count()
  end
  out(what .. " turret ammo " .. ammo)
  local names = {}
  for k, v in pairs(defines.entity_status) do names[v] = k end
  local st = {}
  for _, m in pairs(p.surface.find_entities_filtered({ type = { "assembling-machine", "furnace", "asteroid-collector" } })) do
    local k = m.name .. ":" .. (names[m.status] or "?")
    st[k] = (st[k] or 0) + 1
  end
  local s = {}
  for k, v in pairs(st) do s[#s + 1] = k .. "=" .. v end
  table.sort(s)
  out(what .. " machines " .. table.concat(s, " "))
  out(what .. " speed " .. string.format("%.2f", p.speed) .. " distance " .. string.format("%.3f", p.distance or 0)
    .. " state " .. tostring(p.state))
end
script.on_event(defines.events.on_tick, function(e)
  local f = game.forces.player
  if e.tick == 2 then
    f.research_all_technologies()
    local p = f.create_space_platform({ name = "bpgen ship", planet = "nauvis", starter_pack = "space-platform-starter-pack" })
    p.apply_starter_pack()
    storage.p = p
    local s = p.surface
    local tiles = {}
    for _, t in ipairs(spec.tiles) do tiles[#tiles + 1] = { name = t.name, position = { t.position.x, t.position.y } } end
    s.set_tiles(tiles)
    local inv = game.create_inventory(1)
    inv[1].import_stack(spec.bp)
    local bad = 0
    local ghosts = inv[1].build_blueprint({ surface = s, force = f, position = spec.at,
        build_mode = defines.build_mode.superforced })
    out("pasted " .. #ghosts .. " on " .. s.name .. " (hub " .. tostring(p.hub and p.hub.valid) .. ", blueprint "
      .. tostring(inv[1].is_blueprint_setup()) .. ", tile " .. s.get_tile(10, 10).name .. ")")
    if #ghosts == 0 then  -- (why: the first planned entity the game won't take there)
      local n = 0
      for _, e in ipairs(spec.check) do
        if not s.can_place_entity({ name = "entity-ghost", inner_name = e[1], position = { e[2], e[3] }, direction = e[4],
            force = f, build_check_type = defines.build_check_type.manual_ghost }) then
          n = n + 1
          if n <= 5 then out("can't place " .. e[1] .. " at " .. e[2] .. "," .. e[3] .. " dir " .. e[4] .. " tile "
            .. s.get_tile(math.floor(e[2]), math.floor(e[3])).name) end
        end
      end
      out("can't place " .. n .. " of " .. #spec.check)
    end
    for _, g in pairs(ghosts) do
      if g.valid and g.type == "entity-ghost" then
        local _, made = g.revive({ raise_revive = true })
        if not made and g.valid then bad = bad + 1 end
      elseif g.valid and g.type == "tile-ghost" then
        g.revive()
      end
    end
    local wired = 0
    for _, i in pairs(s.find_entities_filtered({ type = "inserter" })) do
      local n = i.get_wire_connector(defines.wire_connector_id.circuit_red, true).connection_count
      if n > 0 then wired = wired + 1 end
    end
    out("not built " .. bad .. "\nwired to the hub " .. wired)
  elseif e.tick == spec.orbit then
    report(storage.p, "orbit")
    storage.p.schedule = { current = 1, records = { { station = "vulcanus", wait_conditions = {} } } }
    storage.p.paused = false
  elseif e.tick == spec.orbit + spec.travel then
    report(storage.p, "travel")
  end
end)
'''


def main():
    s = Service("vanilla")
    planner.configure(s.data)
    r = ship.plan_ship(s.data, thrusters=THRUSTERS)
    print("\n".join(r["notes"]))
    ents = [s.decorate(e) for e in r["entities"]]
    bp, (x0, y0, w, h) = extend.absolute_blueprint(ents, "bpgen shuttle", floor=r["tiles"])
    spec = {"bp": bp, "at": [x0 + w / 2, y0 + h / 2], "tiles": r["tiles"],
            "check": [[e["name"], e["position"]["x"], e["position"]["y"], e.get("direction", 0)] for e in r["entities"]],
            "orbit": int(MINUTES * 3600), "travel": 3600}
    mods = RUN / "ship-mods"
    shutil.rmtree(mods, ignore_errors=True)
    mod = mods / "ship-test"
    mod.mkdir(parents=True)
    (mod / "info.json").write_text(json.dumps({"name": "ship-test", "version": "0.0.1", "title": "t", "author": "mtopfox",
                                               "factorio_version": "2.0", "dependencies": ["base", "space-age"]}))
    (mod / "control.lua").write_text(CONTROL, encoding="utf-8")
    (mod / "spec.lua").write_text("return " + harness.to_lua(spec), encoding="utf-8")
    shutil.copytree(ROOT / "mod" / "zzz-bpgen", mods / "zzz-bpgen")  # (wires the hub's inserters as they're built)
    names = ["base", "elevated-rails", "quality", "space-age", "zzz-bpgen", "ship-test"]
    (mods / "mod-list.json").write_text(json.dumps({"mods": [{"name": n, "enabled": True} for n in names]}))
    OUT.unlink(missing_ok=True)
    save = RUN / "ship.zip"
    save.unlink(missing_ok=True)
    common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(mods)]
    for args in (["--create", str(save)], ["--benchmark", str(save), "--benchmark-ticks",
                                           str(spec["orbit"] + spec["travel"] + 5), "--disable-audio"]):
        p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=plain_env())
        if p.returncode:
            print("\n".join(line for line in p.stdout.splitlines() if "rror" in line)[-2500:])
            sys.exit(1)
    text = OUT.read_text() if OUT.exists() else ""
    print(text)
    assert "not built 0" in text, "entities not built"
    orbit = {line.split(" ", 2)[1]: line.split(" ", 2)[2] for line in text.splitlines() if line.startswith("orbit ")}
    travel = {line.split(" ", 2)[1]: line.split(" ", 2)[2] for line in text.splitlines() if line.startswith("travel ")}
    assert "thruster-fuel=" in orbit.get("thrusters", "") and "thruster-oxidizer=" in orbit.get("thrusters", ""), \
        "no fuel or oxidizer reached the thrusters"
    assert orbit.get("turret", "ammo 0") != "ammo 0", "no ammo in the turrets"
    assert float(travel.get("speed", "speed 0").split()[0]) > 0, "it doesn't move"
    print("ok")


if __name__ == "__main__":
    main()
