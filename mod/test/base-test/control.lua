-- Player-style bases for bpgen to build next to (base_test.py). Run 1 (no spec) builds the base named in case.lua,
-- keeps its belts fed, and has the mod measure a snapshot of it. Run 2 (spec.lua: bpgen's absolute blueprint, as
-- Place by my base gives it) builds the same base again, pastes the blueprint where it belongs (superforced, as the
-- mod does), builds it for real and runs it, then reports what a player would see: what got built, what the paste
-- replaced, whether it is powered, and the target's rate.
local CASE = require("case")
local ok_spec, spec = pcall(require, "spec")
if not ok_spec then spec = nil end
local SNAP_TICK = 1500  -- (the belts full by then: a lane nothing takes from stands, a used one flows)
local PASTE_TICK = 300
local E, S, W, N = defines.direction.east, defines.direction.south, defines.direction.west, defines.direction.north
local STEP = { [N] = { 0, -1 }, [E] = { 1, 0 }, [S] = { 0, 1 }, [W] = { -1, 0 } }

local function out(line) helpers.write_file("bpgen/base-test.txt", line .. "\n", true) end

local feeds = {}   -- {belt, item}: topped up every tick (a smelter column's output, a chest unloading)
local s, force

local function put(name, x, y, dir, extra)
  local t = { name = name, position = { x, y }, direction = dir, force = force }
  for k, v in pairs(extra or {}) do t[k] = v end
  return s.create_entity(t)
end

--- a belt path: {x, y} start, then {dir, n} legs; the first belt fed with `item` (both lanes)
local function path(x, y, legs, item, belt)
  local first
  for _, leg in ipairs(legs) do
    local d, n = leg[1], leg[2]
    for _ = 1, n do
      local b = put(belt or "transport-belt", x + 0.5, y + 0.5, d)
      first = first or b
      x, y = x + STEP[d][1], y + STEP[d][2]
    end
  end
  if item then feeds[#feeds + 1] = { belt = first, item = item, at = first.position } end
  return x, y
end

--- a main bus flowing south from y1 to y2: lanes {x, item}
local function bus(lanes, y1, y2)
  for _, ln in ipairs(lanes) do path(ln[1], y1, { { S, y2 - y1 + 1 } }, ln[2]) end
end

local function poles(xs, ys)
  for _, x in ipairs(xs) do for _, y in ipairs(ys) do put("medium-electric-pole", x + 0.5, y + 0.5) end end
end

local function power(x, y)
  local eei = put("electric-energy-interface", x, y)
  eei.power_production, eei.electric_buffer_size = 1e9, 1e9
end

local LAYOUTS = {}

-- a main bus in two groups (iron iron copper copper | stone coal), 2 tiles between them; something side-loads
-- coal onto the last lane, a line crosses the first group underground and runs on down the gap
LAYOUTS.gapped = function()
  bus({ { 0, "iron-plate" }, { 1, "iron-plate" }, { 2, "copper-plate" }, { 3, "copper-plate" },
        { 6, "stone" }, { 7, "coal" } }, -50, 40)
  path(11, -30, { { W, 4 } }, "coal")  -- (side-loads onto the coal lane at x = 7)
  path(-6, -17, { { E, 4 } }, "stone")
  put("fast-underground-belt", -1.5, -16.5, E, { type = "input" })
  put("fast-underground-belt", 4.5, -16.5, E, { type = "output" })
  path(5, -17, { { S, 12 } })
  power(-12, -50)
  local ys = {}
  for y = -50, 40, 7 do ys[#ys + 1] = y end
  poles({ -3 }, ys)
  poles({ -10 }, { -50 })
end

-- the same bus, and a mall begun beside it on both sides near its head: assemblers, inserters, chests, poles; a few
-- left unfinished (just the assembler)
LAYOUTS.mall = function()
  LAYOUTS.gapped()
  local function cell(x, y, done)
    put("assembling-machine-2", x + 1.5, y + 1.5, nil, { recipe = "iron-gear-wheel" })
    if done then
      put("inserter", x + 1.5, y - 0.5, N)
      put("wooden-chest", x + 1.5, y - 1.5)
      put("medium-electric-pole", x + 3.5, y + 1.5)
    end
  end
  for i = 0, 3 do cell(12 + 5 * i, -44, i < 3) cell(12 + 5 * i, -36, i % 2 == 0) end
  for i = 0, 2 do cell(-20 - 5 * i, -40, true) end
end

-- no bus: an iron belt and a copper belt winding through the base, machines on them, poles where they were needed
LAYOUTS.spaghetti = function()
  path(-40, -30, { { E, 30 }, { S, 15 }, { E, 20 }, { S, 30 } }, "iron-plate")
  path(-40, -20, { { E, 20 }, { S, 25 }, { W, 10 }, { S, 15 } }, "copper-plate")
  for i = 0, 3 do  -- (gear machines taking from the iron belt)
    put("assembling-machine-1", -35.5 + 4 * i, -32.5, nil, { recipe = "iron-gear-wheel" })
    put("inserter", -35.5 + 4 * i, -30.5, S)
  end
  put("assembling-machine-1", -22.5, -12.5, nil, { recipe = "copper-cable" })
  put("inserter", -20.5, -12.5, E)
  for _, p in ipairs({ { -38, -32 }, { -32, -36 }, { -26, -32 }, { -20, -27 }, { -14, -24 }, { -11, -18 },
                       { -7, -12 }, { -3, -16 }, { 4, -14 }, { 12, -12 }, { 12, -5 } }) do
    put("medium-electric-pole", p[1] + 0.5, p[2] + 0.5)
  end
  power(-44, -32)
  put("medium-electric-pole", -40.5, -32.5)
  for _, p in ipairs({ { 30, 5 }, { -5, 10 }, { 25, -40 } }) do
    s.create_entity({ name = "huge-rock", position = p })
  end
end

local built, ends = {}, {}

--- every belt line's last belt (nothing after it): emptied each tick, as labs, a mall, the rest of the base take
local function find_ends()
  ends = {}
  for _, b in pairs(s.find_entities_filtered({ type = "transport-belt" })) do
    if #b.belt_neighbours.outputs == 0 then ends[#ends + 1] = b end
  end
end

local function setup()
  s, force = game.surfaces.nauvis, game.forces.player
  s.request_to_generate_chunks({ 0, 0 }, 5)
  s.force_generate_chunk_requests()
  local area = { { -70, -70 }, { 70, 70 } }
  for _, ent in pairs(s.find_entities_filtered({ area = area })) do
    if ent.type ~= "character" then ent.destroy() end
  end
  local tiles = {}
  for x = -70, 70 do for y = -70, 70 do tiles[#tiles + 1] = { name = "grass-1", position = { x, y } } end end
  s.set_tiles(tiles)
  for _, t in pairs({ "automation", "automation-2", "logistics", "electronics", "steel-processing" }) do
    force.technologies[t].researched = true
  end
  LAYOUTS[CASE]()
  find_ends()
end

local function paste()
  local inv = game.create_inventory(1)
  local stack = inv[1]
  stack.import_stack(spec.blueprint)
  local b = spec.box
  local ghosts = stack.build_blueprint({ surface = s, force = force, position = { b[1] + b[3] / 2, b[2] + b[4] / 2 },
    build_mode = defines.build_mode.superforced })
  inv.destroy()
  out("ghosts " .. #ghosts .. " of " .. spec.entities)
  local replaced = {}
  for _, old in pairs(s.find_entities_filtered({ to_be_deconstructed = true })) do
    if old.type ~= "tree" and old.type ~= "simple-entity" then
      replaced[old.type] = (replaced[old.type] or 0) + 1
    end
    old.destroy()
  end
  local r = {}
  for k, v in pairs(replaced) do r[#r + 1] = k .. "=" .. v end
  table.sort(r)
  out("replaced " .. table.concat(r, " "))
  for _, g in pairs(ghosts) do
    if g.valid then
      local _, ent = g.revive()
      if ent and (ent.type == "assembling-machine" or ent.type == "furnace") then built[#built + 1] = ent end
    end
  end
  find_ends()
end

local function report()
  local names = {}
  for k, v in pairs(defines.entity_status) do names[v] = k end
  local st = {}
  for _, m in pairs(built) do
    if m.valid then
      local k = names[m.status] or tostring(m.status)
      st[k] = (st[k] or 0) + 1
    end
  end
  local parts = {}
  for k, v in pairs(st) do parts[#parts + 1] = k .. "=" .. v end
  table.sort(parts)
  out("machines " .. #built .. " " .. table.concat(parts, " "))
  local by = {}
  for _, m in pairs(built) do
    if m.valid then
      local k = (m.get_recipe() and m.get_recipe().name or "?") .. ":" .. (names[m.status] or "?") .. ":" .. m.products_finished
      by[#by + 1] = k
    end
  end
  out("each " .. table.concat(by, " "))
  local stats = force.get_item_production_statistics(s)
  local rate = stats.get_flow_count({ name = spec.target, category = "input",
    precision_index = defines.flow_precision_index.ten_minutes })
  out(string.format("rate %.2f", rate))
end

script.on_event(defines.events.on_tick, function(e)
  if e.tick == 1 then return setup() end
  if not s then s, force = game.surfaces.nauvis, game.forces.player end
  for _, f in ipairs(feeds) do
    if not f.belt.valid then  -- (a splitter of the paste took the belt's place: fed the same, as upstream would)
      f.belt = s.find_entities_filtered({ position = f.at, type = { "transport-belt", "splitter",
        "underground-belt" }, limit = 1 })[1] or f.belt
    end
    if f.belt.valid then
      for i = 1, 2 do f.belt.get_transport_line(i).insert_at_back({ name = f.item, count = 1 }) end
    end
  end
  for _, b in ipairs(ends) do
    if b.valid then for i = 1, 2 do b.get_transport_line(i).clear() end end
  end
  if not spec and e.tick == SNAP_TICK then
    remote.call("bpgen", "measure_area", "nauvis", { left_top = { x = -70, y = -70 }, right_bottom = { x = 70, y = 70 } },
      "player")
    out("snapshot measured")
  elseif spec and e.tick == PASTE_TICK then
    paste()
  elseif spec and e.tick == PASTE_TICK + spec.run_ticks then
    report()
  end
end)
