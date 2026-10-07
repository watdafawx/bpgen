-- bpgen's test harness (harness/bpgen-test) inside the player's own game, live: builds or takes a design on the
-- preview surface, powers it, feeds its inputs, drains its outputs and counts them. Two uses:
--   "calibrate": inserter setups bpgen hasn't measured (a NotMeasured spec from bpgen.ingame), run all at once in a
--                grid of small cells, results handed back to Python, the plan tried again
--   "test":      the window's test run: the planned line, already built as the preview, run for real
-- Runs live in storage (a save mid-run carries on). Finished runs go to M.on_done(run, cases); M.on_progress(run)
-- is called every second.

local util = require("util")

local M = {}
local SURFACE = "bpgen-preview"
local CAL_ORIGIN = { x = 0, y = 20000 }

local function add(a, b) return { x = a.x + b.x, y = a.y + b.y } end

local function runs()
  storage.bpgen_bench = storage.bpgen_bench or { runs = {}, next = 1 }
  return storage.bpgen_bench
end

-- put requested items (modules) straight in: there are no robots to deliver them
function M.insert_plans(ent, plans)
  for _, plan in pairs(plans or {}) do
    for _, pos in pairs(plan.items.in_inventory or {}) do
      local inv = ent.get_inventory(pos.inventory)
      if inv then inv[pos.stack + 1].set_stack({ name = plan.id.name, quality = plan.id.quality, count = pos.count or 1 }) end
    end
  end
end

-- a force as the harness sets it up: every recipe, no productivity but the run's, the run's bonuses
local function setup_force(name, bonuses, productivity)
  local force = game.forces[name] or game.create_force(name)
  for _, recipe in pairs(force.recipes) do
    recipe.enabled = true
    recipe.productivity_bonus = 0
  end
  for recipe, bonus in pairs(productivity or {}) do
    if force.recipes[recipe] then force.recipes[recipe].productivity_bonus = bonus end
  end
  for k, v in pairs(bonuses or {}) do pcall(function() force[k] = v end) end
  return force
end
M.setup_force = setup_force

function M.ground(s, x1, y1, x2, y2)
  for cx = math.floor(x1 / 32), math.floor(x2 / 32) do
    for cy = math.floor(y1 / 32), math.floor(y2 / 32) do
      s.request_to_generate_chunks({ cx * 32 + 16, cy * 32 + 16 }, 0)
    end
  end
  s.force_generate_chunk_requests()
  local tiles = {}
  for x = math.floor(x1), math.ceil(x2) do
    for y = math.floor(y1), math.ceil(y2) do
      tiles[#tiles + 1] = { name = (x + y) % 2 == 0 and "lab-dark-1" or "lab-dark-2", position = { x, y } }
    end
  end
  s.set_tiles(tiles, true, false, false, false)
end

-- power: an electric energy interface and a substation by one of the design's poles (or its corner)
local function power(s, force, area, min_pos)
  local poles = s.find_entities_filtered({ area = area, type = "electric-pole", force = force })
  local p = { x = min_pos.x - 4, y = min_pos.y - 4 }
  local sub_at
  if poles[1] then
    local spot = s.find_non_colliding_position("electric-energy-interface", poles[1].position, 3, 0.5)
    if spot then
      p = { x = spot.x - 2, y = spot.y - 2 }
    else
      sub_at = s.find_non_colliding_position("substation", poles[1].position, 8, 0.5)
      local e_spot = sub_at and s.find_non_colliding_position("electric-energy-interface", sub_at, 8, 0.5)
      if e_spot then p = e_spot end
    end
  end
  local eei = s.create_entity({ name = "electric-energy-interface", position = p, force = force })
  if eei then
    eei.power_production = 1e12
    eei.electric_buffer_size = 1e12
  end
  local sub = s.create_entity({ name = "substation", position = sub_at or add(p, { x = 2, y = 2 }), force = force })
  if sub_at and sub and poles[1] then
    local best, bd
    for _, pole in pairs(poles) do
      local d = (pole.position.x - sub.position.x) ^ 2 + (pole.position.y - sub.position.y) ^ 2
      if not bd or d < bd then best, bd = pole, d end
    end
    sub.get_wire_connector(defines.wire_connector_id.pole_copper, true)
      .connect_to(best.get_wire_connector(defines.wire_connector_id.pole_copper, true), false)
  end
end

-- sources and sinks of a case at `origin` (as the harness: belt sources get a lead-in, fluids an infinity pipe)
local function hook_up(s, force, case, origin, st)
  for _, src in ipairs(case.sources or {}) do
    local pos = add(origin, src.position)
    if src.kind == "belt" then
      local belt = s.find_entities_filtered({ position = pos, type = { "transport-belt", "underground-belt" }, limit = 1 })[1]
      if belt then
        local v = util.direction_vectors[belt.direction]
        local first = belt
        for k = 1, 4 do
          first = s.create_entity({ name = belt.name, position = { x = pos.x - v[1] * k, y = pos.y - v[2] * k },
            direction = belt.direction, force = force }) or first
        end
        st.sources[#st.sources + 1] = { kind = "belt", belt = first, lanes = src.lanes }
      else
        st.errors[#st.errors + 1] = "no belt for a source at " .. serpent.line(src.position)
      end
    elseif src.kind == "fluid" then
      local pipe = s.create_entity({ name = "infinity-pipe", position = pos, force = force })
      if pipe then pipe.set_infinity_pipe_filter({ name = src.fluid, percentage = 1 }) end
    elseif src.kind == "chest" then
      local chest = s.create_entity({ name = "infinity-chest", position = pos, force = force })
      if chest then
        chest.set_infinity_container_filter(1, { name = src.item, count = prototypes.item[src.item].stack_size * 10,
          mode = "exactly", index = 1 })
      end
    end
  end
  for _, sink in ipairs(case.sinks or {}) do
    local pos = add(origin, sink.position)
    if sink.kind == "belt" then
      local belt = s.find_entities_filtered({ position = pos, type = { "transport-belt", "underground-belt" }, limit = 1 })[1]
      if belt then st.sinks[#st.sinks + 1] = { kind = "belt", entity = belt, rate = sink.rate, budget = 0 }
      else st.errors[#st.errors + 1] = "no belt for a sink at " .. serpent.line(sink.position) end
    elseif sink.kind == "chest" then
      st.sinks[#st.sinks + 1] = { kind = "chest", entity = s.create_entity({ name = "steel-chest", position = pos, force = force }) }
    elseif sink.kind == "fluid" then
      local void = s.create_entity({ name = "infinity-pipe", position = { x = pos.x + 1, y = pos.y }, force = force })
      if void and sink.item then
        void.set_infinity_pipe_filter({ name = sink.item, percentage = 0, mode = "exactly" })
        st.sinks[#st.sinks + 1] = { kind = "fluid", entity = void, fluid = sink.item }
      end
    end
  end
end

local function feed(src, fed, measuring)
  for lane = 1, 2 do
    local item = src.lanes[lane]
    if item then
      local line = src.belt.get_transport_line(lane)
      while line.can_insert_at_back() do
        if not line.insert_at_back({ name = item, count = 1 }, 1) then break end
        if measuring then fed[item] = (fed[item] or 0) + 1 end
      end
    end
  end
end

local function drain(sink, counts, measuring)
  if not (sink.entity and sink.entity.valid) or sink.kind == "fluid" then return end
  if sink.kind == "belt" and sink.rate then
    sink.budget = math.min(sink.budget + sink.rate / 60, math.max(sink.rate * 2, 2))
    for lane = 1, 2 do
      local line = sink.entity.get_transport_line(lane)
      for _, c in pairs(line.get_contents()) do
        local n = math.min(c.count, math.floor(sink.budget))
        if n > 0 then
          line.remove_item({ name = c.name, count = n })
          sink.budget = sink.budget - n
          if measuring then counts[c.name] = (counts[c.name] or 0) + n end
        end
      end
    end
  elseif sink.kind == "belt" then
    for lane = 1, 2 do
      local line = sink.entity.get_transport_line(lane)
      if measuring then
        for _, c in pairs(line.get_contents()) do counts[c.name] = (counts[c.name] or 0) + c.count end
      end
      line.clear()
    end
  else
    local inv = sink.entity.get_inventory(defines.inventory.chest)
    if measuring then
      for _, c in pairs(inv.get_contents()) do counts[c.name] = (counts[c.name] or 0) + c.count end
    end
    inv.clear()
  end
end

--- calibration: a NotMeasured spec (cases with their own entities, forces with bonus levels)
function M.calibrate(player_index, spec)
  local s = game.surfaces[SURFACE]
  local cell = spec.cell or { w = 16, h = 8, cols = 40 }
  local n = #spec.cases
  local rows = math.ceil(n / cell.cols)
  M.ground(s, CAL_ORIGIN.x - 20, CAL_ORIGIN.y - 20, CAL_ORIGIN.x + cell.w * math.min(n, cell.cols) + 4,
    CAL_ORIGIN.y + cell.h * rows + 4)
  for name, bonuses in pairs(spec.forces or {}) do setup_force("bpgen-" .. name, bonuses) end
  local run = { kind = "calibrate", player = player_index, start = game.tick, warmup = spec.warmup or 300,
                measure = spec.measure or 1800, cases = {} }
  for i, case in ipairs(spec.cases) do
    local origin = { x = CAL_ORIGIN.x + ((i - 1) % cell.cols) * cell.w, y = CAL_ORIGIN.y + math.floor((i - 1) / cell.cols) * cell.h }
    local force = game.forces["bpgen-" .. (case.force_name or "")] or setup_force("bpgen-preview")
    local st = { id = case.id, sources = {}, sinks = {}, counts = {}, fed = {}, errors = {}, built = {} }
    for _, e in ipairs(case.entities or {}) do
      local ent = s.create_entity({ name = e.name, position = add(origin, e.position), direction = e.direction,
        force = force, raise_built = false, create_build_effect_smoke = false })
      if ent then st.built[#st.built + 1] = ent else st.errors[#st.errors + 1] = "could not place " .. e.name end
    end
    power(s, force, { { origin.x - 2, origin.y - 2 }, { origin.x + cell.w - 2, origin.y + cell.h - 2 } }, origin)
    hook_up(s, force, case, origin, st)
    run.cases[i] = st
  end
  run.area = { { CAL_ORIGIN.x - 20, CAL_ORIGIN.y - 20 }, { CAL_ORIGIN.x + cell.w * cell.cols + 4, CAL_ORIGIN.y + cell.h * rows + 4 } }
  local r = runs()
  run.id = r.next
  r.next = r.next + 1
  r.runs[run.id] = run
  return run
end

--- a test run of the preview at `origin` (its top-left), already built under `force_name`
function M.test(player_index, spec, origin, area, force_name)
  local s = game.surfaces[SURFACE]
  local force = setup_force(force_name, spec.force, spec.recipe_productivity)
  local st = { id = "bp", sources = {}, sinks = {}, counts = {}, fed = {}, errors = {} }
  power(s, force, area, origin)
  hook_up(s, force, spec.case, origin, st)
  local run = { kind = "test", player = player_index, start = game.tick, warmup = spec.warmup, measure = spec.measure,
                cases = { st }, area = area, force = force_name, output = spec.output, expected = spec.expected }
  local r = runs()
  run.id = r.next
  r.next = r.next + 1
  for id, other in pairs(r.runs) do  -- (one test per player)
    if other.kind == "test" and other.player == player_index then r.runs[id] = nil end
  end
  r.runs[run.id] = run
  return run
end

function M.stop(run_id)
  runs().runs[run_id] = nil
end

function M.find(kind, player_index)
  for _, run in pairs(runs().runs) do
    if run.kind == kind and run.player == player_index then return run end
  end
end

--- seconds measured so far, and each case's rates over them
function M.rates(run)
  local seconds = math.max(0, game.tick - run.start - run.warmup) / 60
  local out = {}
  for i, st in ipairs(run.cases) do
    local rates = {}
    if seconds > 0 then
      for name, n in pairs(st.counts) do rates[name] = n / seconds end
      local stats = game.forces[run.force or "player"] and game.forces[run.force or "player"].get_fluid_production_statistics(SURFACE)
      for _, sink in ipairs(st.sinks) do
        if sink.kind == "fluid" and sink.start and stats then
          rates[sink.fluid] = (stats.get_input_count(sink.fluid) - sink.start) / seconds
        end
      end
    end
    out[i] = { id = st.id, rates = rates, errors = st.errors }
  end
  return seconds, out
end

--- what each machine in the run's area is doing: {status name = count}, and the starved ones' positions
function M.statuses(run)
  local names = {}
  for k, v in pairs(defines.entity_status) do names[v] = k end
  local count, starved = {}, {}
  local s = game.surfaces[SURFACE]
  for _, ent in pairs(s.find_entities_filtered({ area = run.area, type = { "assembling-machine", "furnace" }, force = run.force })) do
    local k = names[ent.status] or tostring(ent.status)
    count[k] = (count[k] or 0) + 1
    if ent.status == defines.entity_status.item_ingredient_shortage or ent.status == defines.entity_status.fluid_ingredient_shortage
        or ent.status == defines.entity_status.full_output then
      starved[#starved + 1] = ent.bounding_box
    end
  end
  return count, starved
end

M.on_done = function(run, cases) end
M.on_progress = function(run) end

local function tick(e)
  local all = storage.bpgen_bench
  if not (all and next(all.runs)) then return end
  for id, run in pairs(all.runs) do
    local t = e.tick - run.start
    local measuring = t >= run.warmup
    if t == run.warmup then
      local stats = game.forces[run.force or "player"] and game.forces[run.force or "player"].get_fluid_production_statistics(SURFACE)
      for _, st in ipairs(run.cases) do
        for _, sink in ipairs(st.sinks) do
          if sink.kind == "fluid" and stats then sink.start = stats.get_input_count(sink.fluid) end
        end
      end
    end
    for _, st in ipairs(run.cases) do
      for _, src in ipairs(st.sources) do if src.belt.valid then feed(src, st.fed, measuring) end end
      for _, sink in ipairs(st.sinks) do drain(sink, st.counts, measuring) end
    end
    if t % 60 == 0 then M.on_progress(run) end
    if t >= run.warmup + run.measure then
      all.runs[id] = nil
      local _, cases = M.rates(run)
      if run.kind == "calibrate" then  -- (the cells go; the preview stays)
        for _, ent in pairs(game.surfaces[SURFACE].find_entities_filtered({ area = run.area })) do
          if ent.valid then ent.destroy() end
        end
      end
      all.last = all.last or {}
      all.last[run.kind] = { cases = cases, statuses = run.kind == "test" and M.statuses(run) or nil }  -- (for tests)
      M.on_done(run, cases)
    end
  end
end

M.handlers = { [defines.events.on_tick] = tick }

return M
