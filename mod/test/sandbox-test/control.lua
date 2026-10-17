-- "Try in sandbox", no player: a belt of iron plates going east through the base, a plan (an inserter, a chest, a
-- pole) beside it copied into a lab with the base round it; the lab's belt fed from the real one, the plan run, a
-- few changes made in the lab, then pasted back: only the changes as ghosts, what was taken away marked for removal.
-- The lab is made, moved and pasted back over a few ticks (bpgen's tasks): each step below waits until bpgen has
-- nothing left to do.
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

local function rc(fn, ...) return remote.call("bpgen", fn, ...) end
local function ls() return game.surfaces[lab.surface] end
local function move(dx, dy, turn)
  local m = rc("sandbox_move", 1, dx, dy, turn)
  if not m.ok then out("FAIL: move refused: " .. tostring(m.why)) end
end

-- the steps, one at a time, each once bpgen is idle (and not before its tick, when it has one: {tick, fn})
local steps = {
  { 600, function()  -- (the real belt full of plates by now)
    lab = rc("sandbox_open", 1, "nauvis", "player", { blueprint = storage.bp, center = { x = 11, y = 2 }, area = { 10, 1, 12, 3 } })
  end },
  function()
    local st = rc("sandbox_state", 1)
    out(string.format("lab %s: %d belts fed, %d drained, %d pipes", lab.surface, st.feeds, st.drains, st.pipes))
    if st.feeds ~= 1 or st.drains ~= 1 then out("FAIL: expected one belt in and one out") end
    move(3, 0, false)  -- the plan moved 3 east, turned four times (back as it was), moved back
  end,
  function()
    local s = ls()
    if s.find_entity("inserter", { 10.5, 1.5 }) or not s.find_entity("inserter", { 13.5, 1.5 }) then
      out("FAIL: the plan not moved 3 east")
    end
    move(0, 0, true)
  end,
  function()
    local ins = ls().find_entities_filtered({ name = "inserter", area = { { 11, -1 }, { 16, 5 } } })[1]
    if not ins or ins.direction == defines.direction.north then out("FAIL: the plan not turned") end
    move(0, 0, true)
  end,
  function() move(0, 0, true) end,
  function() move(0, 0, true) end,
  function() move(-3, 0, false) end,
  function()
    local s = ls()
    local ins = s.find_entity("inserter", { 10.5, 1.5 })
    if not (ins and ins.direction == defines.direction.north and s.find_entity("wooden-chest", { 10.5, 2.5 })) then
      out("FAIL: the plan not back where it was")
    end
    if s.count_entities_filtered({ name = "inserter", area = { { 8, -2 }, { 18, 6 } } }) ~= 1 then
      out("FAIL: the plan's old copies left behind")
    end
    move(60, 0, false)  -- (moved far east: the base there copied in as it goes, nothing twice; then back)
  end,
  function()
    local s = ls()
    if not s.find_entity("inserter", { 70.5, 1.5 }) then out("FAIL: the plan not moved 60 east") end
    -- (the lab now reaches past the belt's east end: x -22 .. 88, the belt -45 .. 70)
    local lab_belts = s.count_entities_filtered({ name = "express-transport-belt", area = { { -20, 0 }, { 71, 1 } } })
    local real_belts = game.surfaces.nauvis.count_entities_filtered({ name = "express-transport-belt",
      area = { { -20, 0 }, { 71, 1 } } })
    if lab_belts ~= real_belts then out("FAIL: the belt copied " .. lab_belts .. " times, the base has " .. real_belts) end
    for x = -45, 70 do
      if s.count_entities_filtered({ name = "express-transport-belt", position = { x + 0.5, 0.5 } }) > 1 then
        out("FAIL: two belts at " .. x) break
      end
    end
    move(-60, 0, false)
  end,
  function()
    if not ls().find_entity("inserter", { 10.5, 1.5 }) then out("FAIL: the plan not back from the long move") end
    out("moved and turned")
  end,
  { 2400, function()
    local s = ls()
    local chest = s.find_entity("wooden-chest", { 10.5, 2.5 })
    local got = chest and chest.get_item_count("iron-plate") or 0
    out("the plan's chest in the lab: " .. got .. " plates")
    if got == 0 then out("FAIL: the plan got nothing from the fed belt") end
    if s.count_entities_filtered({ name = "bpgen-sandbox-power" }) == 0 then out("FAIL: no power in the lab") end
    -- the player's changes: a belt taken away, one turned, a chest added
    s.find_entity("express-transport-belt", { 30.5, 0.5 }).destroy()
    s.find_entity("express-transport-belt", { 25.5, 0.5 }).direction = defines.direction.south
    s.create_entity({ name = "iron-chest", position = { 20.5, 5.5 }, force = s.find_entity("wooden-chest", { 10.5, 2.5 }).force })
    rc("sandbox_paste", 1)
  end },
  function()
    local r = rc("sandbox_pasted")
    out(#r.ghosts .. " ghosts, " .. #r.removed .. " marked for removal")
    for _, w in ipairs({ { "inserter", 10.5, 1.5 }, { "wooden-chest", 10.5, 2.5 }, { "small-electric-pole", 11.5, 2.5 },
        { "iron-chest", 20.5, 5.5 }, { "express-transport-belt", 25.5, 0.5 } }) do
      if not has(r.ghosts, w[1], w[2], w[3]) then out("FAIL: no ghost of " .. w[1] .. " at " .. w[2] .. "," .. w[3]) end
    end
    if #r.ghosts ~= 5 then out("FAIL: unchanged entities pasted too (" .. #r.ghosts .. " ghosts, 5 expected)") end
    if not (has(r.removed, "express-transport-belt", 30.5, 0.5) and has(r.removed, "express-transport-belt", 25.5, 0.5)) then
      out("FAIL: the removed and the turned belt not marked for removal")
    end
    local s = ls()
    if not s then out("FAIL: the lab surface was deleted (it is kept for the next try)")
    elseif s.count_entities_filtered({}) > 0 then out("FAIL: the lab not cleared") end
    -- (a second try reuses it)
    local again = rc("sandbox_open", 1, "nauvis", "player",
      { blueprint = storage.bp, center = { x = 11, y = 2 }, area = { 10, 1, 12, 3 } })
    if again.surface ~= lab.surface then out("FAIL: a second try made another lab surface") end
  end,
  function() rc("sandbox_paste", 1) end,
  function()
    -- (as Place by my base leaves it since 0.26.3: the plan already there as ghosts on the real base, an absolute
    -- plan at its box; the lab must still build it and move it)
    local real = game.surfaces.nauvis
    for _, g in pairs(real.find_entities_filtered({ type = "entity-ghost" })) do g.destroy() end
    local inv = game.create_inventory(1)
    inv[1].import_stack(storage.bp)
    inv[1].build_blueprint({ surface = real, force = "player", position = { 41, 2 }, build_mode = defines.build_mode.superforced })
    inv.destroy()
    rc("sandbox_open", 1, "nauvis", "player", { blueprint = storage.bp, box = { 40, 1, 2, 2 }, area = { 40, 1, 42, 3 } })
  end,
  function() move(3, 0, false) end,
  function()
    local s2 = ls()
    out("over its own ghosts: inserter at 43.5: " .. tostring(s2.find_entity("inserter", { 43.5, 1.5 }) ~= nil)
      .. ", ghosts left in the lab: " .. s2.count_entities_filtered({ type = "entity-ghost" }))
    if not s2.find_entity("inserter", { 43.5, 1.5 }) then out("FAIL: the plan over its own ghosts didn't move") end
    if s2.count_entities_filtered({ type = "entity-ghost" }) > 0 then out("FAIL: the real base's ghosts left standing in the lab") end
    rc("sandbox_paste", 1)
  end,
  function()
    -- (the same, the plan already built (bots, or an instant-build mod): the lab must move it, not leave its copy of
    -- the real one standing, and the paste back takes the old one away)
    local real = game.surfaces.nauvis
    for _, g in pairs(real.find_entities_filtered({ area = { { 30, 1 }, { 60, 10 } } })) do g.destroy() end
    local inv = game.create_inventory(1)
    inv[1].import_stack(storage.bp)
    for _, g in pairs(inv[1].build_blueprint({ surface = real, force = "player", position = { 41, 2 },
        build_mode = defines.build_mode.superforced })) do
      if g.valid then g.revive() end
    end
    inv.destroy()
    for _, e in pairs(real.find_entities_filtered({ area = { { 30, -5 }, { 60, 10 } }, to_be_deconstructed = true })) do
      e.cancel_deconstruction("player")
    end
    rc("sandbox_open", 1, "nauvis", "player", { blueprint = storage.bp, box = { 40, 1, 2, 2 }, area = { 40, 1, 42, 3 } })
  end,
  function() move(3, 0, false) end,
  function()
    local s2 = ls()
    local n = s2.count_entities_filtered({ name = "inserter", area = { { 35, -5 }, { 50, 8 } } })
    out("over itself built: inserters in the lab: " .. n .. ", at 43.5: "
      .. tostring(s2.find_entity("inserter", { 43.5, 1.5 }) ~= nil))
    if n ~= 1 or not s2.find_entity("inserter", { 43.5, 1.5 }) then
      out("FAIL: the built plan didn't move (its copy of the real one left standing)")
    end
    rc("sandbox_paste", 1)
  end,
  function()
    local r = rc("sandbox_pasted")
    if not has(r.removed, "inserter", 40.5, 1.5) then out("FAIL: the paste back left the old plan in the base") end
    if not has(r.ghosts, "inserter", 43.5, 1.5) then out("FAIL: the paste back didn't put the moved plan down") end
    -- (an absolute plan's print snapped to the world grid, as bpgen's are: each build near its spot landed on it
    -- again, so the arrows moved nothing)
    local real = game.surfaces.nauvis
    local inv = game.create_inventory(1)
    inv[1].import_stack(storage.bp)
    for _, g in pairs(real.find_entities_filtered({ area = { { 30, 1 }, { 60, 10 } } })) do g.destroy() end
    inv[1].blueprint_snap_to_grid = { 8, 8 }
    inv[1].blueprint_absolute_snapping = true
    storage.snapped = inv[1].export_stack()
    inv.destroy()
    rc("sandbox_open", 1, "nauvis", "player", { blueprint = storage.snapped, box = { 40, 1, 2, 2 }, area = { 40, 1, 42, 3 } })
  end,
  function()
    local before = ls().find_entities_filtered({ name = "inserter", area = { { 30, -5 }, { 60, 8 } } })
    storage.x0 = #before == 1 and before[1].position.x
    move(3, 0, false)
  end,
  function()
    local after = ls().find_entities_filtered({ name = "inserter", area = { { 30, -5 }, { 60, 8 } } })
    local x0 = storage.x0
    out("snapped: inserter at " .. tostring(x0) .. ", moved to " .. tostring(#after == 1 and after[1].position.x))
    if not (x0 and #after == 1 and after[1].position.x == x0 + 3) then out("FAIL: the snapped plan didn't move 3 east") end
    rc("sandbox_paste", 1)
  end,
  function() out("done") end,
}

local i = 0
script.on_event(defines.events.on_tick, function(e)
  if e.tick == 2 then setup() end
  if e.tick < 600 or rc("busy") then return end
  local st = steps[i + 1]
  if not st then return end
  if type(st) == "table" then
    if e.tick < st[1] then return end
    st = st[2]
  end
  i = i + 1
  st()
end)
