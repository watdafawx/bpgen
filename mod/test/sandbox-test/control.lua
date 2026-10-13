-- "Try in sandbox", no player: a belt of iron plates going east through the base, a plan (an inserter, a chest, a
-- pole) beside it copied into a lab with the base round it; the lab's belt fed from the real one, the plan run, a
-- few changes made in the lab, then pasted back: only the changes as ghosts, what was taken away marked for removal.
local function out(line) helpers.write_file("bpgen/sandbox-test.txt", line .. "\n", true) end
local E = defines.direction.east
local lab

local function setup()
  local s, f = game.surfaces.nauvis, game.forces.player
  s.request_to_generate_chunks({ 0, 0 }, 4)
  s.force_generate_chunk_requests()
  for _, e in pairs(s.find_entities_filtered({ area = { { -60, -20 }, { 80, 20 } } })) do
    if e.type ~= "character" then e.destroy() end
  end
  local tiles = {}
  for x = -60, 80 do for y = -20, 20 do tiles[#tiles + 1] = { name = "grass-1", position = { x, y } } end end
  s.set_tiles(tiles)
  for x = -45, 70 do
    s.create_entity({ name = "express-transport-belt", position = { x + 0.5, 0.5 }, direction = E, force = f })
  end
  local eei = s.create_entity({ name = "electric-energy-interface", position = { -50, 3 }, force = f })
  eei.power_production, eei.electric_buffer_size = 1e9, 1e9
  s.create_entity({ name = "substation", position = { -46, 3 }, force = f })
  for _, x in ipairs({ -44.5, -43.5 }) do
    local ch = s.create_entity({ name = "infinity-chest", position = { x, 2.5 }, force = f })
    ch.set_infinity_container_filter(1, { name = "iron-plate", count = 100, mode = "exactly", index = 1 })
    s.create_entity({ name = "fast-inserter", position = { x, 1.5 }, direction = defines.direction.south, force = f })
  end
  -- the plan, as a blueprint string (built on a scratch surface)
  local t = game.create_surface("scratch")
  t.request_to_generate_chunks({ 0, 0 }, 1)
  t.force_generate_chunk_requests()
  t.create_entity({ name = "inserter", position = { 10.5, 1.5 }, direction = defines.direction.north, force = f })
  t.create_entity({ name = "wooden-chest", position = { 10.5, 2.5 }, force = f })
  t.create_entity({ name = "small-electric-pole", position = { 11.5, 2.5 }, force = f })
  local inv = game.create_inventory(1)
  inv[1].set_stack({ name = "blueprint" })
  inv[1].create_blueprint({ surface = t, force = f, area = { { 9, 0 }, { 13, 4 } } })
  storage.bp = inv[1].export_stack()
  inv.destroy()
end

local function has(list, name, x, y)
  for _, e in pairs(list) do
    if e.name == name and e.position.x == x and e.position.y == y then return true end
  end
  return false
end

script.on_event(defines.events.on_tick, function(e)
  if e.tick == 2 then setup() end
  if e.tick == 600 then  -- (the real belt full of plates by now)
    lab = remote.call("bpgen", "sandbox_open", 1, "nauvis", "player",
      { blueprint = storage.bp, center = { x = 11, y = 2 }, area = { 10, 1, 12, 3 } })
    out(string.format("lab %s: %d belts fed, %d drained, %d pipes", lab.surface, lab.feeds, lab.drains, lab.pipes))
    if lab.feeds ~= 1 or lab.drains ~= 1 then out("FAIL: expected one belt in and one out") end
  end
  if e.tick == 2400 then
    local s = game.surfaces[lab.surface]
    local chest = s.find_entity("wooden-chest", { 10.5, 2.5 })
    local got = chest and chest.get_item_count("iron-plate") or 0
    out("the plan's chest in the lab: " .. got .. " plates")
    if got == 0 then out("FAIL: the plan got nothing from the fed belt") end
    if s.count_entities_filtered({ name = "bpgen-sandbox-power" }) == 0 then out("FAIL: no power in the lab") end
    -- the player's changes: a belt taken away, one turned, a chest added
    s.find_entity("express-transport-belt", { 30.5, 0.5 }).destroy()
    s.find_entity("express-transport-belt", { 25.5, 0.5 }).direction = defines.direction.south
    s.create_entity({ name = "iron-chest", position = { 20.5, 5.5 }, force = s.find_entity("wooden-chest", { 10.5, 2.5 }).force })
    local r = remote.call("bpgen", "sandbox_paste", 1)
    out(#r.ghosts .. " ghosts, " .. #r.removed .. " marked for removal")
    for _, w in ipairs({ { "inserter", 10.5, 1.5 }, { "wooden-chest", 10.5, 2.5 }, { "small-electric-pole", 11.5, 2.5 },
        { "iron-chest", 20.5, 5.5 }, { "express-transport-belt", 25.5, 0.5 } }) do
      if not has(r.ghosts, w[1], w[2], w[3]) then out("FAIL: no ghost of " .. w[1] .. " at " .. w[2] .. "," .. w[3]) end
    end
    if #r.ghosts ~= 5 then out("FAIL: unchanged entities pasted too (" .. #r.ghosts .. " ghosts, 5 expected)") end
    if not (has(r.removed, "express-transport-belt", 30.5, 0.5) and has(r.removed, "express-transport-belt", 25.5, 0.5)) then
      out("FAIL: the removed and the turned belt not marked for removal")
    end
  end
  if e.tick == 2460 then
    if game.surfaces[lab.surface] then out("FAIL: the lab still there") end
    out("done")
  end
end)
