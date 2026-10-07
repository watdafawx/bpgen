-- Builds a small base, snapshots it with the companion; with a spec (a bpgen extension blueprint) it also pastes
-- that like a player would, builds it, runs a while and reports whether it landed where planned and works.
local ok_spec, spec = pcall(require, "spec")
if not ok_spec then spec = nil end
local RUN_TICKS = spec and spec.run_ticks or 0

local function out(line)
  helpers.write_file("bpgen/comp-test.txt", line .. "\n", true)
end

-- two long belts going east: pure iron plate at y = 0, pure copper plate at y = 6; kept full by script
local LINES = { { item = "iron-plate", y = 0 }, { item = "copper-plate", y = 6 } }
local X1, X2 = -14, 14

local function setup(s, force)
  s.request_to_generate_chunks({ 0, 0 }, 4)
  s.force_generate_chunk_requests()
  local area = { { -20, -12 }, { 20, 14 } }
  for _, ent in pairs(s.find_entities_filtered({ area = area, type = { "tree", "simple-entity", "cliff", "fish" } })) do
    ent.destroy()
  end
  local tiles = {}
  for x = -20, 20 do for y = -12, 14 do tiles[#tiles + 1] = { name = "grass-1", position = { x, y } } end end
  s.set_tiles(tiles)
  for _, line in pairs(LINES) do
    for x = X1, X2 do
      s.create_entity({ name = "transport-belt", position = { x + 0.5, line.y + 0.5 }, direction = defines.direction.east, force = force })
    end
  end
  local eei = s.create_entity({ name = "electric-energy-interface", position = { -17, 3 }, force = force })
  eei.power_production = 1e9
  eei.electric_buffer_size = 1e9
  for x = -16, 16, 8 do
    s.create_entity({ name = "medium-electric-pole", position = { x + 0.5, 3.5 }, force = force })
  end
  s.create_entity({ name = "medium-electric-pole", position = { -15.5, -1.5 }, force = force })
  s.create_entity({ name = "medium-electric-pole", position = { -15.5, 8.5 }, force = force })
  -- each line is filled like a player's: infinity chests and fast inserters on both sides of its first tiles
  for _, line in pairs(LINES) do
    for x = X1, X1 + 2 do
      for _, side in pairs({ -1, 1 }) do
        local chest = s.create_entity({ name = "infinity-chest", position = { x + 0.5, line.y + 0.5 + 2 * side }, force = force })
        chest.set_infinity_container_filter(1, { name = line.item, count = 50, index = 1 })
        s.create_entity({ name = "fast-inserter", position = { x + 0.5, line.y + 0.5 + side },
          direction = side < 0 and defines.direction.north or defines.direction.south, force = force })
      end
    end
  end
  s.create_entity({ name = "iron-ore", position = { 8.5, 10.5 }, amount = 500 })
  s.create_entity({ name = "iron-ore", position = { 9.5, 10.5 }, amount = 500 })
  for _, t in pairs({ "automation", "logistics", "electronics", "logistic-science-pack" }) do
    force.technologies[t].researched = true
  end
  -- a logistic network with buildings in it: the state's stock (the save check)
  s.create_entity({ name = "roboport", position = { 40, 40 }, force = force })
  local store = s.create_entity({ name = "storage-chest", position = { 43.5, 40.5 }, force = force })
  store.insert({ name = "assembling-machine-1", count = 7 })
  store.insert({ name = "iron-plate", count = 50 })
end

local built = {}
script.on_event(defines.events.on_tick, function(e)
  local s = game.surfaces.nauvis
  local force = game.forces.player
  if e.tick == 1 then
    setup(s, force)
    return
  end
  -- the build's output belts are emptied, as labs or the next machines would
  if spec and spec.outputs and e.tick > 300 then
    for _, o in pairs(spec.outputs) do
      local belt = s.find_entities_filtered({ position = o.position, type = { "transport-belt", "underground-belt" }, limit = 1 })[1]
      if belt then
        for i = 1, 2 do belt.get_transport_line(i).clear() end
      end
    end
  end
  if e.tick == 300 and not spec then
    local area = { left_top = { x = -80, y = -80 }, right_bottom = { x = 80, y = 80 } }
    local n = remote.call("bpgen-companion", "snapshot_area", "nauvis", area, "player")
    out("snapshot entities: " .. n)
  elseif e.tick == 300 and spec then
    -- paste like a player (Ctrl+Shift): build_blueprint snaps it (absolute snapping) next to the cursor
    local inv = game.create_inventory(1)
    local stack = inv[1]
    stack.import_stack(spec.blueprint)
    local ghosts = stack.build_blueprint({ surface = s, force = force, position = spec.cursor, build_mode = defines.build_mode.superforced })
    local wrong = 0
    for _, want in pairs(spec.expect) do
      local found = s.find_entities_filtered({ position = want.position, ghost_name = want.name, radius = 0.1 })
      if #found == 0 then wrong = wrong + 1 end
    end
    out("ghosts built: " .. #ghosts .. ", misplaced: " .. wrong .. " of " .. #spec.expect)
    -- what the paste marked for removal (belts under the new splitters) goes first, as bots would do
    local cleared = {}
    for _, old in pairs(s.find_entities_filtered({ to_be_deconstructed = true })) do
      if old.type ~= "tree" and old.type ~= "simple-entity" then
        cleared[#cleared + 1] = old.name .. "@" .. old.position.x .. "," .. old.position.y
      end
      old.destroy()
    end
    out("cleared for the paste: " .. table.concat(cleared, " "))
    for _, g in pairs(ghosts) do
      if g.valid then
        local _, ent = g.revive()
        if ent and (ent.type == "assembling-machine" or ent.type == "furnace") then built[#built + 1] = ent end
      end
    end
    out("splitters: " .. #s.find_entities_filtered({ type = "splitter" }))
  elseif spec and e.tick == 300 + RUN_TICKS then
    local made = {}
    for _, m in pairs(built) do
      if m.valid and m.get_recipe() then
        local r = m.get_recipe().name
        made[r] = (made[r] or 0) + m.products_finished
      end
    end
    local parts = {}
    for r, n in pairs(made) do parts[#parts + 1] = r .. "=" .. n end
    table.sort(parts)
    out("machines: " .. #built .. ", crafted in " .. (RUN_TICKS / 60) .. " s: " .. table.concat(parts, " "))
    local names = {}
    for k, v in pairs(defines.entity_status) do names[v] = k end
    local statuses = {}
    for _, m in pairs(built) do
      if m.valid then
        local k = (m.get_recipe() and m.get_recipe().name or "?") .. ":" .. (names[m.status] or tostring(m.status))
        statuses[k] = (statuses[k] or 0) + 1
      end
    end
    local st = {}
    for k, v in pairs(statuses) do st[#st + 1] = k .. "=" .. v end
    table.sort(st)
    out("status: " .. table.concat(st, " "))
    local poles = s.find_entities_filtered({ type = "electric-pole" })
    local nets = {}
    for _, p in pairs(poles) do nets[p.electric_network_id or -1] = (nets[p.electric_network_id or -1] or 0) + 1 end
    local ns = {}
    for k, v in pairs(nets) do ns[#ns + 1] = k .. ":" .. v end
    out("pole networks: " .. table.concat(ns, " "))
    if spec.target then
      -- the target's rate over the last 10 minutes, from the production statistics
      local stats = force.get_item_production_statistics(s)
      local rate = stats.get_flow_count({ name = spec.target, category = "input", precision_index = defines.flow_precision_index.ten_minutes })
      out("target " .. spec.target .. ": " .. string.format("%.1f", rate) .. "/min over the last 10 min")
    end
  end
end)
