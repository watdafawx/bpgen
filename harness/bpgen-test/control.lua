local util = require("util")
-- bpgen test harness: builds each case from spec.lua on a lab surface, feeds saturated inputs,
-- counts outputs exactly, writes script-output/bpgen/result.json.
local spec = require("spec")

-- cases sit in a grid of cells; only the chunks under each cell are generated (with hundreds of mods
-- reacting to every generated chunk, a big square of map never finishes)
local cell = spec.cell or {}
local CELL_W, CELL_H, COLS = cell.w or 200, cell.h or 64, cell.cols or 10

local function cell_origin(index)
  return { x = ((index - 1) % COLS) * CELL_W, y = math.floor((index - 1) / COLS) * CELL_H }
end

local function cell_area(index)
  local o = cell_origin(index)
  return { { o.x - 16, o.y - 16 }, { o.x + CELL_W - 16, o.y + CELL_H - 16 } }
end

local function add(a, b) return { x = a.x + b.x, y = a.y + b.y } end

-- put requested modules straight in (there are no robots to deliver them)
local function insert_modules(ent, plans)
  for _, plan in pairs(plans or {}) do
    for _, pos in pairs(plan.items.in_inventory or {}) do
      local inv = ent.get_inventory(pos.inventory)
      if inv then inv[pos.stack + 1].set_stack({ name = plan.id.name, quality = plan.id.quality, count = pos.count or 1 }) end
    end
  end
end

local function make_surface()
  local surface = game.create_surface("bpgen", {
    default_enable_all_autoplace_controls = false,
    autoplace_settings = {
      entity = { treat_missing_as_default = false, settings = {} },
      decorative = { treat_missing_as_default = false, settings = {} },
      tile = { treat_missing_as_default = false, settings = { ["lab-dark-1"] = {} } },
    },
  })
  surface.always_day = true
  for i = 1, #spec.cases do
    local a = cell_area(i)
    for cx = math.floor(a[1][1] / 32), math.floor(a[2][1] / 32) do
      for cy = math.floor(a[1][2] / 32), math.floor(a[2][2] / 32) do
        surface.request_to_generate_chunks({ cx * 32 + 16, cy * 32 + 16 }, 0)
      end
    end
  end
  surface.force_generate_chunk_requests()
  -- clear anything other mods spawned on the fresh chunks (ruins etc.)
  for _, ent in pairs(surface.find_entities()) do
    if ent.valid and ent.type ~= "character" then ent.destroy() end
  end
  return surface
end

local function setup_force(name, bonuses)
  local force = game.forces[name] or game.create_force(name)
  -- unlock recipes directly: researching everything would also grant bonuses the player's save may not
  -- have (e.g. recipe productivity research from mods, measured +13% on circuits)
  for _, recipe in pairs(force.recipes) do
    recipe.enabled = true
    recipe.productivity_bonus = 0 -- mods may grant recipe productivity at runtime; only the spec's counts
  end
  for recipe, bonus in pairs(spec.recipe_productivity or {}) do
    if force.recipes[recipe] then force.recipes[recipe].productivity_bonus = bonus end
  end
  for k, v in pairs(bonuses or {}) do force[k] = v end
  return force
end

local function build_case(surface, case, index)
  local origin = cell_origin(index)
  local force = case.force_name and game.forces[case.force_name] or game.forces.player
  -- blueprints are not built into uncharted (fog-of-war) chunks
  force.chart(surface, cell_area(index))
  local state = { id = case.id, origin = origin, sources = {}, sinks = {}, counts = {}, errors = {}, probes = case.probes }
  local min_x, min_y = math.huge, math.huge

  if case.blueprint then
    -- build from the real blueprint string, then treat the built area's top-left as the case origin
    local inv = game.create_inventory(1)
    inv[1].set_stack({ name = "blueprint" })
    if inv[1].import_stack(case.blueprint) ~= 0 then
      table.insert(state.errors, "blueprint string failed to import")
    else
      -- build_blueprint centres the print on `position`; shift so it lands inside this case's cell
      local bx1, by1, bx2, by2 = math.huge, math.huge, -math.huge, -math.huge
      for _, be in pairs(inv[1].get_blueprint_entities() or {}) do
        bx1, by1 = math.min(bx1, be.position.x), math.min(by1, be.position.y)
        bx2, by2 = math.max(bx2, be.position.x), math.max(by2, be.position.y)
      end
      local at = { x = origin.x + math.ceil((bx2 - bx1) / 2) + 2, y = origin.y + math.ceil((by2 - by1) / 2) + 2 }
      local ghosts = inv[1].build_blueprint({ surface = surface, force = force, position = at,
        build_mode = defines.build_mode.forced, skip_fog_of_war = false })
      local lx, ly = math.huge, math.huge
      for _, g in pairs(ghosts) do
        local bb = g.bounding_box
        lx, ly = math.min(lx, bb.left_top.x), math.min(ly, bb.left_top.y)
        local plans = g.insert_plan
        local _, ent, proxy = g.revive({ return_item_request_proxy = true })
        if ent then insert_modules(ent, plans) end
        if proxy and proxy.valid then proxy.destroy() end
        if ent and (ent.type == "assembling-machine" or ent.type == "furnace" or ent.type == "inserter" or ent.type == "lab") then
          state.watch = state.watch or {}
          table.insert(state.watch, ent)
        elseif not ent then
          table.insert(state.errors, "could not build " .. g.ghost_name)
        end
      end
      if lx == math.huge then
        table.insert(state.errors, "blueprint built nothing")
        inv.destroy()
        return state
      end
      origin = { x = math.floor(lx + 0.5), y = math.floor(ly + 0.5) }
      state.origin = origin
      min_x, min_y = origin.x, origin.y
    end
    inv.destroy()
  end

  for _, e in ipairs(case.entities or {}) do
    local pos = add(origin, e.position)
    min_x, min_y = math.min(min_x, pos.x), math.min(min_y, pos.y)
    local ent = surface.create_entity({
      name = e.name, position = pos, direction = e.direction, force = force,
      recipe = e.recipe, quality = e.quality, type = e.ug_type,
      raise_built = false, create_build_effect_smoke = false,
    })
    if ent and e.override_stack_size then ent.inserter_stack_size_override = e.override_stack_size end
    if ent and e.modules then
      local inv, slot = ent.get_inventory(e.inventory), 1
      for _, m in ipairs(e.modules) do
        for _ = 1, m[3] do inv[slot].set_stack({ name = m[1], quality = m[2] }); slot = slot + 1 end
      end
    end
    if ent and (ent.type == "assembling-machine" or ent.type == "furnace" or ent.type == "inserter" or ent.type == "lab") then
      state.watch = state.watch or {}
      table.insert(state.watch, ent)
    end
    if not ent then
      table.insert(state.errors, "could not place " .. e.name .. " at " .. serpent.line(e.position))
    elseif e.debug then
      table.insert(state.errors, "debug " .. e.name .. " dir=" .. ent.direction ..
        " pickup=" .. serpent.line(ent.pickup_position) .. " drop=" .. serpent.line(ent.drop_position))
    end
  end

  if case.power ~= false then
    local p = { x = min_x - 4, y = min_y - 4 }
    -- a design with its own poles: put the power source next to one of them (the case's corner may be
    -- far from any pole, e.g. a chain's raw input belts)
    local cell_poles = surface.find_entities_filtered({ area = cell_area(index), type = "electric-pole", force = force })
    local sub_at = nil
    if cell_poles[1] then
      local spot = surface.find_non_colliding_position("electric-energy-interface", cell_poles[1].position, 3, 0.5)
      if spot then
        p = { x = spot.x - 2, y = spot.y - 2 }
      else
        -- (a dense design has no room right by its poles: look further, and wire the substation to the pole)
        sub_at = surface.find_non_colliding_position("substation", cell_poles[1].position, 8, 0.5)
        local e_spot = sub_at and surface.find_non_colliding_position("electric-energy-interface", sub_at, 8, 0.5)
        if e_spot then p = e_spot end
      end
    end
    local eei = surface.create_entity({ name = "electric-energy-interface", position = p, force = force })
    eei.power_production = 1e12
    eei.electric_buffer_size = 1e12
    local sub = surface.create_entity({ name = "substation", position = sub_at or add(p, { x = 2, y = 2 }), force = force })
    if sub_at and sub then
      local best, bd
      for _, pole in pairs(cell_poles) do
        local d = (pole.position.x - sub.position.x) ^ 2 + (pole.position.y - sub.position.y) ^ 2
        if not bd or d < bd then best, bd = pole, d end
      end
      sub.get_wire_connector(defines.wire_connector_id.pole_copper, true)
        .connect_to(best.get_wire_connector(defines.wire_connector_id.pole_copper, true), false)
    end
    for _, pos in ipairs(case.extra_substations or {}) do
      surface.create_entity({ name = "substation", position = add(origin, pos), force = force })
    end
  end

  for _, s in ipairs(case.sources or {}) do
    local pos = add(origin, s.position)
    if s.kind == "belt" then
      local belt = surface.find_entities_filtered({ position = pos, type = { "transport-belt", "underground-belt" }, limit = 1 })[1]
      if belt then
        -- feed through a lead-in so items reach the design at real belt speed (feeding the design's
        -- own first tile lets an inserter there take items faster than the belt could deliver them)
        local v = util.direction_vectors[belt.direction]
        local first = belt
        for k = 1, (s.lead or 4) do
          first = surface.create_entity({ name = belt.name, position = { x = pos.x - v[1] * k, y = pos.y - v[2] * k },
            direction = belt.direction, force = force }) or first
        end
        table.insert(state.sources, { kind = "belt", belt = first, lanes = s.lanes })
      else
        table.insert(state.errors, "no belt for source at " .. serpent.line(s.position))
      end
    elseif s.kind == "fluid" then
      local pipe = surface.create_entity({ name = "infinity-pipe", position = pos, force = force })
      pipe.set_infinity_pipe_filter({ name = s.fluid, percentage = 1 })
    elseif s.kind == "chest" then
      local chest = surface.create_entity({ name = "infinity-chest", position = pos, force = force })
      chest.set_infinity_container_filter(1, { name = s.item, count = prototypes.item[s.item].stack_size * 10, mode = "exactly", index = 1 })
    end
  end

  -- case.prime {fluid = amount}: a one-off fill of machines' input boxes for that fluid, for a recipe that eats
  -- some of its own output (coal liquefaction's heavy oil) and needs a kickstart, as a player gives it by hand
  for fluid, amount in pairs(case.prime or {}) do
    for _, ent in pairs(surface.find_entities_filtered({ type = { "assembling-machine", "furnace" }, force = force })) do
      for i = 1, #ent.fluidbox do
        local f = ent.fluidbox.get_filter(i)
        if f and f.name == fluid and ent.fluidbox.get_prototype(i).production_type == "input" then
          ent.fluidbox[i] = { name = fluid, amount = amount }
        end
      end
    end
  end

  for _, s in ipairs(case.sinks or {}) do
    local pos = add(origin, s.position)
    if s.kind == "belt" then
      local belt = surface.find_entities_filtered({ position = pos, type = { "transport-belt", "underground-belt" }, limit = 1 })[1]
      -- s.rate (items/s): drain no faster than that, like labs eating science at the planned rate
      if belt then table.insert(state.sinks, { kind = "belt", entity = belt, rate = s.rate, budget = 0 }) else
        table.insert(state.errors, "no belt for sink at " .. serpent.line(s.position)) end
    elseif s.kind == "chest" then
      local chest = surface.create_entity({ name = "steel-chest", position = pos, force = force })
      table.insert(state.sinks, { kind = "chest", entity = chest })
    elseif s.kind == "fluid" then
      -- past the design's last pipe: an infinity pipe that keeps itself empty; the output is read from the
      -- force's fluid production statistics (emptying a pipe by script only drains its share of a 2.0 segment)
      local void = surface.create_entity({ name = "infinity-pipe", position = { x = pos.x + 1, y = pos.y }, force = force })
      if void and s.item then
        void.set_infinity_pipe_filter({ name = s.item, percentage = 0, mode = "exactly" })
        table.insert(state.sinks, { kind = "fluid", entity = void, fluid = s.item })
      else
        table.insert(state.errors, "no room for a fluid sink at " .. serpent.line(s.position))
      end
    end
  end
  return state
end

local function feed(src, fed, measuring)
  local stack = spec.belt_stack_size or 1
  for lane = 1, 2 do
    local item = src.lanes[lane]
    if item then
      local line = src.belt.get_transport_line(lane)
      while line.can_insert_at_back() do
        if not line.insert_at_back({ name = item, count = stack }, stack) then break end
        if measuring then fed[item] = (fed[item] or 0) + stack end
      end
    end
  end
end

local function drain(sink, counts, measuring)
  if not sink.entity.valid then return end
  if sink.kind == "fluid" then return end  -- measured from production statistics
  if sink.kind == "belt" and sink.rate then
    sink.budget = math.min(sink.budget + sink.rate / 60, math.max(sink.rate * 2, 2))  -- room for whole items at slow rates
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

script.on_init(function()
  setup_force("player", spec.force)
  for name, bonuses in pairs(spec.forces or {}) do setup_force(name, bonuses) end
  storage.surface = make_surface()
  storage.cases = {}
  for i, case in ipairs(spec.cases) do storage.cases[i] = build_case(storage.surface, case, i) end
  if spec.companion_request and remote.interfaces["bpgen"] then
    for _, st in ipairs(storage.cases) do
      for _, ent in ipairs(st.watch or {}) do
        if ent.type == "assembling-machine" then
          local err = remote.call("bpgen", "write_request", ent)
          if err then table.insert(st.errors, "bpgen mod: " .. err) end
          break
        end
      end
    end
  end
end)

-- spec.research = {pack names}: keep the player force researching a technology that uses exactly those packs
-- (its prerequisites are granted), so labs in the cases have something to work on
local function keep_researching(force)
  if force.current_research then return end
  local want = {}
  for _, p in ipairs(spec.research) do want[p] = true end
  local function grant(tech)
    for _, pre in pairs(tech.prerequisites) do
      if not pre.researched then grant(pre); pre.researched = true end
    end
  end
  for _, tech in pairs(force.technologies) do
    if tech.enabled and not tech.researched then
      local ings, n, ok = tech.research_unit_ingredients, 0, true
      for _, ing in pairs(ings) do
        if want[ing.name] then n = n + 1 else ok = false end
      end
      if ok and n == #spec.research then
        grant(tech)
        if force.add_research(tech) then return end
      end
    end
  end
end

script.on_event(defines.events.on_tick, function(e)
  if spec.research and e.tick % 60 == 1 then keep_researching(game.forces.player) end
  if not storage.forces_settled then
    -- other mods' on_init can run after ours and reset technology effects (re-locking recipes, resetting
    -- bonuses), so apply the forces' setup again once every on_init has run
    storage.forces_settled = true
    setup_force("player", spec.force)
    for name, bonuses in pairs(spec.forces or {}) do setup_force(name, bonuses) end
  end
  if spec.recipe_productivity then
    -- mods may keep adjusting recipe productivity (Level Up raises it as recipes get crafted);
    -- hold it at the player's value from the request
    for _, force in pairs(game.forces) do
      for recipe, bonus in pairs(spec.recipe_productivity) do
        if force.recipes[recipe] then force.recipes[recipe].productivity_bonus = bonus end
      end
    end
  end
  local measuring = e.tick >= spec.warmup
  if e.tick == spec.warmup then
    -- every item made on the lab surface while measuring (what each block inside a blueprint really makes)
    storage.made_start = {}
    for name, n in pairs(game.forces.player.get_item_production_statistics(storage.surface).input_counts) do
      storage.made_start[name] = n
    end
    local stats = game.forces.player.get_fluid_production_statistics(storage.surface)
    for _, st in ipairs(storage.cases) do
      for _, sink in ipairs(st.sinks) do
        if sink.kind == "fluid" then sink.start = stats.get_input_count(sink.fluid) end
      end
    end
  end
  for _, st in ipairs(storage.cases) do
    st.fed = st.fed or {}
    for _, src in ipairs(st.sources) do if src.belt.valid then feed(src, st.fed, measuring) end end
    for _, sink in ipairs(st.sinks) do drain(sink, st.counts, measuring) end
  end
  if e.tick == spec.warmup + spec.measure then
    local seconds = spec.measure / 60
    local out = {}
    for _, st in ipairs(storage.cases) do
      local rates = {}
      for name, n in pairs(st.counts) do rates[name] = n / seconds end
      local stats = game.forces.player.get_fluid_production_statistics(storage.surface)
      for _, sink in ipairs(st.sinks) do
        if sink.kind == "fluid" and sink.start then
          rates[sink.fluid] = (stats.get_input_count(sink.fluid) - sink.start) / seconds
        end
      end
      local fed = {}
      for name, n in pairs(st.fed or {}) do fed[name] = n / seconds end
      local made = {}
      for name, n in pairs(game.forces.player.get_item_production_statistics(storage.surface).input_counts) do
        local d = n - ((storage.made_start or {})[name] or 0)
        if d > 0 then made[name] = d / seconds end
      end
      local lines = {}
      for _, sink in ipairs(st.sinks) do
        if sink.kind == "belt" and sink.entity.valid then
          lines[#lines + 1] = sink.entity.get_transport_line(1).line_length
        end
      end
      local status = {}
      local names = {}
      for k, v in pairs(defines.entity_status) do names[v] = k end
      for _, ent in ipairs(st.watch or {}) do
        if ent.valid then
          local key = ent.type .. ":" .. (names[ent.status] or tostring(ent.status))
          if spec.status_by_recipe and ent.type == "assembling-machine" and ent.get_recipe() then
            key = key .. ":" .. ent.get_recipe().name
          elseif spec.status_by_recipe and ent.type == "furnace" then
            -- (an idle furnace has no recipe: named by what it last made, else what it holds)
            local r = ent.get_recipe() or ent.previous_recipe
            local name = r and (r.name and (type(r.name) == "string" and r.name or r.name.name))
            if not name then
              for _, inv in pairs({ defines.inventory.furnace_result, defines.inventory.furnace_source }) do
                local c = ent.get_inventory(inv) and ent.get_inventory(inv).get_contents()[1]
                if c then name = "holds " .. c.name break end
              end
            end
            key = key .. ":" .. (name or "empty")
          end
          status[key] = (status[key] or 0) + 1
        end
      end
      -- belt probes (case.probes = {{x, y}, ...} relative to the case): what is on those belts at the end
      local probes = {}
      for _, pr in ipairs(st.probes or {}) do
        local pos = { x = st.origin.x + pr[1], y = st.origin.y + pr[2] }
        local b = storage.surface.find_entities_filtered({ position = pos, type = { "transport-belt", "underground-belt", "splitter" }, limit = 1 })[1]
        local items = {}
        if b then
          for lane = 1, 2 do
            for _, c in pairs(b.get_transport_line(lane).get_contents()) do items[c.name .. "@" .. lane] = c.count end
          end
        end
        probes[#probes + 1] = { at = pr, belt = b and b.name or "none", items = items }
      end
      -- what starved machines hold (spec.starved_report): tells a missing ingredient from a slow one
      local starved = {}
      if spec.starved_report then
        for _, ent in ipairs(st.watch or {}) do
          if ent.valid and ent.type == "assembling-machine" and ent.get_recipe()
              and ent.status == defines.entity_status.item_ingredient_shortage then
            local inv = ent.get_inventory(defines.inventory.assembling_machine_input)
            local have = {}
            for _, c in pairs(inv and inv.get_contents() or {}) do have[c.name] = c.count end
            starved[#starved + 1] = { recipe = ent.get_recipe().name, x = ent.position.x - st.origin.x,
                                      y = ent.position.y - st.origin.y, has = have }
          end
        end
      end
      local prod = {}
      for _, ent in ipairs(st.watch or {}) do
        if ent.valid and ent.type == "assembling-machine" and ent.get_recipe() then
          prod[ent.get_recipe().name] = ent.force.recipes[ent.get_recipe().name].productivity_bonus
        end
      end
      local machine
      for _, ent in ipairs(st.watch or {}) do
        if ent.valid and ent.type == "assembling-machine" then
          machine = { crafting_speed = ent.crafting_speed, effects = ent.effects, productivity_bonus = ent.productivity_bonus,
                      speed_bonus = ent.speed_bonus, recipe = ent.get_recipe() and ent.get_recipe().name, beacons = {} }
          for _, m2 in ipairs(st.watch) do
            if m2.valid and m2.type == "assembling-machine" then
              local n = #(m2.get_beacons() or {})
              machine.beacons[tostring(n)] = (machine.beacons[tostring(n)] or 0) + 1
            end
          end
          break
        end
      end
      local combinators = {}
      for _, c in pairs(storage.surface.find_entities_filtered({ area = cell_area(_), name = "constant-combinator" })) do
        local cb = c.get_control_behavior()
        local sec = cb and cb.sections_count > 0 and cb.get_section(1)
        local f = sec and sec.filters[1]
        combinators[#combinators + 1] = { signal = f and f.value and f.value.name, count = f and f.min,
                                          description = c.combinator_description }
      end
      -- which pipe network each machine's fluid boxes are on (spec.fluid_report): finds disconnected pipes
      local fluids = {}
      if spec.fluid_report then
        for _, ent in pairs(storage.surface.find_entities_filtered({ area = cell_area(_), type = { "assembling-machine", "furnace" } })) do
          local boxes = {}
          for i = 1, #ent.fluidbox do
            local f = ent.fluidbox[i]
            boxes[#boxes + 1] = { index = i, fluid = f and f.name, amount = f and math.floor(f.amount),
                                  filter = ent.fluidbox.get_filter(i) and ent.fluidbox.get_filter(i).name,
                                  segment = ent.fluidbox.get_fluid_segment_id(i) }
          end
          fluids[#fluids + 1] = { recipe = ent.get_recipe() and ent.get_recipe().name, x = ent.position.x - st.origin.x,
                                  y = ent.position.y - st.origin.y, boxes = boxes }
        end
      end
      local chests = {}
      local bars = {}
      for _, c in pairs(storage.surface.find_entities_filtered({ area = cell_area(_), type = "container" })) do
        bars[#bars + 1] = c.get_inventory(defines.inventory.chest).get_bar()
        for _, it in pairs(c.get_inventory(defines.inventory.chest).get_contents()) do
          chests[it.name] = (chests[it.name] or 0) + it.count
        end
      end
      -- where the items are at the end: on belts (per entity type), in machine output slots
      local held = {}
      for _, ent in pairs(storage.surface.find_entities_filtered({ area = cell_area(_), type = { "transport-belt", "underground-belt", "splitter" } })) do
        for i = 1, ent.get_max_transport_line_index() do
          for _, c in pairs(ent.get_transport_line(i).get_contents()) do
            held["belt:" .. c.name] = (held["belt:" .. c.name] or 0) + c.count
          end
        end
      end
      for _, ent in pairs(storage.surface.find_entities_filtered({ area = cell_area(_), type = "assembling-machine" })) do
        local inv = ent.get_output_inventory()
        for _, c in pairs(inv and inv.get_contents() or {}) do
          held["out:" .. c.name] = (held["out:" .. c.name] or 0) + c.count
        end
      end
      out[#out + 1] = { id = st.id, rates = rates, errors = st.errors, status = status, fed = fed, made = made, held = held, starved = starved, probes = probes,
                        sink_line_length = lines, recipe_productivity = prod, machine = machine, labels = combinators,
                        chests = chests, chest_bars = bars, fluids = fluids }
    end
    helpers.write_file("bpgen/result.json", helpers.table_to_json({ tick = e.tick, cases = out }), false)
  end
end)
