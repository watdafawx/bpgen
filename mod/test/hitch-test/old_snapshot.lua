-- (hitch_test.py) the snapshot as bpgen 0.28 read it, in one go over the whole area: what the reader over
-- ticks (reader.lua) must match
local POWER_SOURCES = { ["generator"] = true, ["burner-generator"] = true, ["solar-panel"] = true,
  ["accumulator"] = true, ["electric-energy-interface"] = true, ["fusion-generator"] = true,
  ["lightning-attractor"] = true }
local SKIP = { character = true, ["item-entity"] = true, ["item-request-proxy"] = true, ["entity-ghost"] = false,
  ["character-corpse"] = true, corpse = true, ["flying-text"] = true, ["highlight-box"] = true, explosion = true,
  smoke = true, particle = true, projectile = true, sticker = true, fire = true, ["deconstructible-tile-proxy"] = true,
  ["tile-ghost"] = true }
local OBSTACLE = { tree = true, ["simple-entity"] = true, cliff = true, ["unit-spawner"] = true, turret = false,
  ["fish"] = false }
local BELTS = { ["transport-belt"] = 2, ["underground-belt"] = 2, splitter = 4, loader = 2, ["loader-1x1"] = 2 }

--- the item most present on each lane of a belt-like entity (nil when empty)
local function lanes(ent)
  local out = {}
  for i = 1, BELTS[ent.type] do
    local best, n = nil, 0
    for _, c in pairs(ent.get_transport_line(i).get_contents()) do
      if c.count > n then best, n = c.name, c.count end
    end
    out[i] = best or false
  end
  return out
end

local PIPES = { pipe = true, ["pipe-to-ground"] = true, ["storage-tank"] = true, pump = true }

--- the fluid in a pipe-like entity, or the one its network is locked to while it is empty
local function pipe_fluid(ent)
  local f = ent.get_fluid(1)
  if f then return f.name end
  local ok, sf = pcall(ent.get_fluid_segment_fluid, 1) -- (2.1: no LuaFluidBox)
  if ok and sf then return sf.name end
  local ok2, flt = pcall(ent.get_fluid_segment_filter, 1)
  if ok2 and flt then return type(flt) == "string" and flt or flt.name end
  return nil
end

--- tiles -> {y: [[x1, x2], ...]} runs (much smaller than one entry per tile)
local function runs(positions)
  local rows = {}
  for _, p in pairs(positions) do
    local y = math.floor(p.y)
    rows[y] = rows[y] or {}
    rows[y][#rows[y] + 1] = math.floor(p.x)
  end
  local out = {}
  for y, xs in pairs(rows) do
    table.sort(xs)
    local r, start, prev = {}, xs[1], xs[1]
    for i = 2, #xs + 1 do
      local x = xs[i]
      if x ~= prev + 1 then
        r[#r + 1] = { start, prev }
        start = x
      end
      prev = x
    end
    out[tostring(y)] = r
  end
  return out
end

--- an entity of `force` (or a ghost) as the snapshot records it, or nil (not one bpgen builds round)
local function record(e, force)
  local t = e.type
  if OBSTACLE[t] or SKIP[t] or t == "resource" or not (e.force == force or t == "entity-ghost") then return nil end
  local name, ty = e.name, t
  if t == "entity-ghost" then name, ty = e.ghost_name, e.ghost_type end
  local b = e.bounding_box
  local out = { name = name, type = ty, position = { x = e.position.x, y = e.position.y }, direction = e.direction,
    box = { b.left_top.x, b.left_top.y, b.right_bottom.x, b.right_bottom.y } }
  if t == "entity-ghost" then out.ghost = true end
  if ty == "assembling-machine" or ty == "furnace" then
    local r = e.get_recipe()
    if not r and ty == "furnace" and t ~= "entity-ghost" then r = e.previous_recipe and e.previous_recipe.name end
    out.recipe = type(r) == "string" and r or (r and r.name) or nil
  end
  if ty == "underground-belt" then out.ug_type = e.belt_to_ground_type end
  if (ty == "loader" or ty == "loader-1x1") then out.ug_type = e.loader_type end
  if BELTS[t] then out.lanes = lanes(e) end
  if t == "electric-pole" then out.network = e.electric_network_id end
  if PIPES[t] then out.fluid = pipe_fluid(e) end
  return out
end

return function(player, area)
  local surface = player.surface
  local ents, obstacles = {}, {}
  local powered, poles = {}, {}  -- electric network ids with a power source on them; the poles' records
  for _, e in pairs(surface.find_entities_filtered({ area = area })) do
    local t = e.type
    if OBSTACLE[t] then
      local b = e.bounding_box
      obstacles[#obstacles + 1] = { b.left_top.x, b.left_top.y, b.right_bottom.x, b.right_bottom.y, t }
    else
      local out = record(e, player.force)
      if out then
        if t == "electric-pole" then poles[#poles + 1] = out end
        if POWER_SOURCES[t] and e.electric_network_id then powered[e.electric_network_id] = true end
        ents[#ents + 1] = out
      end
    end
  end
  for _, p in ipairs(poles) do p.powered = p.network ~= nil and powered[p.network] == true end
  local resources, fluid_resources = {}, {}
  for _, r in pairs(surface.find_entities_filtered({ area = area, type = "resource" })) do
    if r.prototype.resource_category == "basic-fluid" then  -- (oil: each one a pumpjack's spot, and its yield)
      fluid_resources[#fluid_resources + 1] = { name = r.name, x = r.position.x, y = r.position.y, amount = r.amount }
    end
    resources[r.name] = resources[r.name] or {}
    local list = resources[r.name]
    list[#list + 1] = r.position
  end
  for name, list in pairs(resources) do resources[name] = runs(list) end
  local water = {}
  for _, tile in pairs(surface.find_tiles_filtered({ area = area, collision_mask = "water_tile" })) do
    water[#water + 1] = tile.position
  end
  local snap = {
    tick = game.tick,
    surface = surface.name,
    area = { area.left_top.x, area.left_top.y, area.right_bottom.x, area.right_bottom.y },
    entities = ents,
    obstacles = obstacles,
    resources = resources,
    fluid_resources = fluid_resources,
    water = runs(water),
  }
  return snap
end
