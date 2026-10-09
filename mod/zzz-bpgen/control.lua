-- Writes files in script-output/bpgen/ for the external bpgen tool (mods can only write files, not read them):
--   request.json   hover an assembler + Ctrl+Shift+B: plan a line for its recipe
--   state.json     what's researched and what the factory makes / uses per minute (on join, after research, every 5 min)
--   snapshot.json  an area of the base picked with the snapshot tool (shortcut bar, Ctrl+Shift+N)

local safe = require("safe")
-- (every handler guarded: an error never takes the game down)
local function on_event(ev, fn, filters) script.on_event(ev, safe.guard("bpgen", fn), filters) end
local function on_nth(n, fn) script.on_nth_tick(n, safe.guard("bpgen", fn)) end

-- items some enabled recipe makes: one pass over the force's recipes (searching per item was far too slow with
-- big packs - tens of thousands of recipes times every placeable entity - and froze the game)
local function craftable(force)
  local out = {}
  for _, r in pairs(force.recipes) do
    -- recycling (always enabled in Space Age) turns better items back into lesser ones; it isn't a way to make them
    if r.enabled and not r.hidden and r.category ~= "recycling" then
      for _, p in pairs(r.products) do
        if p.type == "item" then out[p.name] = true end
      end
    end
  end
  return out
end

-- placeable, non-hidden entities of these types whose item can be crafted
local function unlocked(made, types)
  local out = {}
  for _, t in pairs(types) do
    for name, proto in pairs(prototypes.get_entity_filtered({ { filter = "type", type = t } })) do
      local items = proto.items_to_place_this
      if not proto.hidden and items and items[1] and made[items[1].name] then
        out[#out + 1] = name
      end
    end
  end
  table.sort(out)
  return out
end

local function bonuses(force)
  return {
    inserter_stack_size_bonus = force.inserter_stack_size_bonus,
    bulk_inserter_capacity_bonus = force.bulk_inserter_capacity_bonus,
    belt_stack_size_bonus = force.belt_stack_size_bonus,
  }
end

local recipe_request

--- @return table? request, string? error
local function build_request(force, ent)
  local proto_type = ent and ent.valid and (ent.type == "entity-ghost" and ent.ghost_type or ent.type)
  if not proto_type or (proto_type ~= "assembling-machine" and proto_type ~= "furnace") then
    return nil, "hover an assembling machine first"
  end
  local recipe, quality = ent.get_recipe()
  if not recipe then return nil, "that machine has no recipe set" end
  local modules = {}
  local inv = ent.type ~= "entity-ghost" and ent.get_module_inventory()
  if inv then
    for _, c in pairs(inv.get_contents()) do modules[#modules + 1] = { name = c.name, quality = c.quality, count = c.count } end
  end
  return recipe_request(force, recipe.name, ent.type == "entity-ghost" and ent.ghost_name or ent.name, {
    recipe_quality = quality and quality.name or "normal", machine_quality = ent.quality.name, modules = modules,
    surface = ent.surface.name })
end

--- a request for `recipe` in `machine`; `more` overrides the defaults (normal quality, no modules, nauvis)
function recipe_request(force, recipe, machine, more)
  local made = craftable(force)
  local req = {
    tick = game.tick,
    recipe = recipe,
    recipe_quality = "normal",
    recipe_productivity = force.recipes[recipe] and force.recipes[recipe].productivity_bonus or 0,
    machine = machine,
    machine_quality = "normal",
    modules = {},
    surface = "nauvis",
    bonuses = bonuses(force),
    inserters = unlocked(made, { "inserter" }),
    belts = unlocked(made, { "transport-belt" }),
    poles = unlocked(made, { "electric-pole" }),
  }
  for k, v in pairs(more or {}) do req[k] = v end
  return req
end

local function write_request(force, ent)
  local request, err = build_request(force, ent)
  if request then helpers.write_file("bpgen/request.json", helpers.table_to_json(request), false) end
  return request, err
end

-- In game planning (optional): with the fnative loader and its py plugin, bpgen runs inside the game process
-- (bpgen/ingame.py) on a worker thread and the blueprint lands in the player's hand. Without it, the request file
-- above is all there is (the bpgen web app picks it up). Jobs live only in this Lua state: not saved.
local function in_game()
  return safe.has("py")
end
local jobs = {}  -- job id -> {player = index, recipe = name, window = true if the bpgen window asked}
local warmed = false

local snapshot  -- (below: an area of the base, for bpgen to build next to)
local PLACE_R = 48  -- without an area picked with the snapshot tool: the snapshot reaches this far around a spot
local snaps = {}  -- player index -> the area last picked with the snapshot tool (this session only)
local function around(c, r)
  r = r or PLACE_R
  return { left_top = { x = c.x - r, y = c.y - r }, right_bottom = { x = c.x + r, y = c.y + r } }
end

-- What the belts carry, measured: an area's snapshot, then each belt looked at again MEASURE_TICKS later; a belt's
-- flow is its belt speed times the items that moved on it (items/s, both lanes), so bpgen knows how much of a bus
-- lane is used and whether it runs dry or backs up. Pending measurements live in this Lua state only.
local MEASURE_TICKS = 30
local measuring = {}  -- {tick, snap, belts = {{e, ids, rec}}, done}

local function belt_ids(e)  -- unique id -> position along its line, both lines
  local t = {}
  for i = 1, 2 do
    for _, it in pairs(e.get_transport_line(i).get_detailed_contents()) do t[it.unique_id] = it.position end
  end
  return t
end

--- snapshot `area` and, MEASURE_TICKS later, give it to done(snap) with each transport belt's flow and items
local function measure(player, area, done)
  local snap = snapshot(player, area, true)
  local recs = {}
  for _, r in ipairs(snap.entities) do
    if r.type == "transport-belt" and not r.ghost then recs[string.format("%.1f,%.1f", r.position.x, r.position.y)] = r end
  end
  local belts = {}
  for _, e in pairs(player.surface.find_entities_filtered({ area = area, type = "transport-belt", force = player.force })) do
    local rec = recs[string.format("%.1f,%.1f", e.position.x, e.position.y)]
    if rec then belts[#belts + 1] = { e = e, ids = belt_ids(e), rec = rec } end
  end
  measuring[#measuring + 1] = { tick = game.tick + MEASURE_TICKS, snap = snap, belts = belts, done = done }
end

local function measured_now(m)
  for _, b in ipairs(m.belts) do
    if b.e.valid then
      local now, n, moved = belt_ids(b.e), 0, 0
      for id, pos in pairs(b.ids) do
        n = n + 1
        if now[id] ~= pos then moved = moved + 1 end
      end
      b.rec.items = n
      b.rec.flow = math.floor(b.e.prototype.belt_speed * 60 * moved * 10 + 0.5) / 10
    end
  end
  m.done(m.snap)
end

local window = require("window")
local compat = require("compat")
window.setup({
  craftable = craftable, unlocked = unlocked, bonuses = bonuses,
  start = function(player, request)
    if not in_game() then return false, "start the game with the fnative loader" end
    helpers.write_file("bpgen/request.json", helpers.table_to_json(request), false)
    local function go()
      local id, err = native.start("py", "bpgen.ingame:plan", helpers.table_to_json(request))
      if id then jobs[id] = { player = player.index, recipe = request.recipe, window = true } end
      return id ~= nil, err
    end
    if request.params and request.params.mode == "base" and (request.params.fit_bus or request.params.add_to) then
      -- (a base fitted to a bus lands where the bus ends: the ground there, water and what's built, around you)
      request.params.snapshot = snapshot(player, around(player.position, 200), true)
    end
    if request.params and request.params.mode == "busdesign" then
      -- (the ground from the patches to the player and round it: ore, what's in the way, room for the bus head)
      local pos = player.position
      local x1, y1, x2, y2 = pos.x, pos.y, pos.x, pos.y
      for _, a in ipairs(request.params.patches) do
        x1, y1, x2, y2 = math.min(x1, a[1]), math.min(y1, a[2]), math.max(x2, a[3]), math.max(y2, a[4])
      end
      local m = 100
      request.params.snapshot = snapshot(player,
        { left_top = { x = x1 - m, y = y1 - m }, right_bottom = { x = x2 + m, y = y2 + m } }, true)
    end
    if request.params and (request.params.mode == "extend" or request.params.bus_feed) then  -- (the base it builds next to, with the request)
      if snaps[player.index] then
        request.params.snapshot = snaps[player.index]
      else
        -- (a line fed from the bus: room beside the bus for the line and what it makes for itself)
        measure(player, around(player.position, request.params.bus_feed and 128 or nil),
          function(snap) request.params.snapshot = snap go() end)
        return true
      end
    end
    return go()
  end,
  -- the last plan next to the base: a snapshot around params.seed (the player, or where the window's arrows moved
  -- it), then bpgen places it (taps, a main bus, power); the answer lands in the window as a plan's does
  place_base = function(player, params)
    if not in_game() then return false, "start the game with the fnative loader" end
    measure(player, around(params.seed or player.position), function(snap)
      params.snapshot = snap
      local id, err = native.start("py", "bpgen.ingame:place", helpers.table_to_json(params))
      if id then jobs[id] = { player = player.index, recipe = "", window = true }
      else player.print("[bpgen] in-game planning failed: " .. tostring(err)) end
    end)
    return true
  end,
  -- the bus around the player, looked at (lanes, what they carry, what the base lacks): to window.on_api "bus"
  bus_report = function(player, belts)
    if not in_game() then return false, "start the game with the fnative loader" end
    measure(player, around(player.position), function(snap)
      local id = native.start("py", "bpgen.ingame:bus_report", helpers.table_to_json({ snapshot = snap, belts = belts }))
      if id then jobs[id] = { player = player.index, api = "bus" } end
    end)
    return true
  end,
  -- a new lane of an item along that bus: its answer lands in the window as a plan's does (preview, Place it)
  add_lane = function(player, item)
    if not in_game() then return false, "start the game with the fnative loader" end
    measure(player, around(player.position), function(snap)
      local id = native.start("py", "bpgen.ingame:add_lane", helpers.table_to_json({ snapshot = snap, item = item }))
      if id then jobs[id] = { player = player.index, recipe = "", window = true } end
    end)
    return true
  end,
  -- the web app's helpers (recipe tree, save check, module ideas, history): answers go to window.on_api
  api = function(player, fn, params, tag)
    local id = in_game() and native.start("py", "bpgen.ingame:api", helpers.table_to_json({ fn = fn, params = params }))
    if id then jobs[id] = { player = player.index, api = tag } end
  end,
  -- inserter setups measured in game: into bpgen's table, then the window plans again
  measured = function(player, cases)
    local id = in_game() and native.start("py", "bpgen.ingame:measured", helpers.table_to_json({ cases = cases }))
    if id then jobs[id] = { player = player.index, measured = true } end
  end,
})

on_event("bpgen-request", function(e)
  local player = game.get_player(e.player_index)
  -- over a recipe in a window (Recipe Book, Factory Planner, Factoriopedia...): that recipe, in the best machine
  local recipe = (e.in_gui or not player.selected) and compat.hovered_recipe(e)
  if recipe then
    if in_game() then
      window.open(player, { recipe = recipe })
      return window.plan(player)
    end
    local machine = window.best_machine(player, recipe)
    if not machine then return player.print("[bpgen] no unlocked machine makes [recipe=" .. recipe .. "]") end
    helpers.write_file("bpgen/request.json", helpers.table_to_json(recipe_request(player.force, recipe, machine)), false)
    return player.print({ "", "[bpgen] request sent: [recipe=" .. recipe .. "] in [entity=" .. machine .. "]" })
  end
  local request, err = write_request(player.force, player.selected)
  if in_game() then
    -- the bpgen window: filled in from the hovered machine and planned now, else empty
    window.open(player, request)
    if request then window.plan(player) end
    return
  end
  if not request then
    player.print("[bpgen] " .. err)
    return
  end
  player.print({ "", "[bpgen] request sent: [recipe=" .. request.recipe .. "] in [entity=" .. request.machine .. "]" })
end)

-- lines other mods ask for (remote plan_line, AI Crew's goals): planned like Extend, placed as ghosts straight away,
-- then remote.call(<asker>, "line_placed", player_index, item, ghosts, error, {box, inputs, outputs})
local auto = {} -- player index -> {item, rate, reply}

local function line_done(player, ghosts, err, abs)
  local a = auto[player.index]
  auto[player.index] = nil
  if a and remote.interfaces[a.reply] and remote.interfaces[a.reply].line_placed then
    remote.call(a.reply, "line_placed", player.index, a.item, ghosts, err, abs)
  end
end

local function plan_line(player, item, rate, reply)
  auto[player.index] = { item = item, rate = rate, reply = reply or "ai-crew" }
  if not in_game() then return line_done(player, nil, "bpgen plans only with the fnative loader") end
  local req = window.extend_request(player, item, rate)
  measure(player, around(player.position), function(snap)
    req.params.snapshot = snap
    local id, err = native.start("py", "bpgen.ingame:plan", helpers.table_to_json(req))
    if id then jobs[id] = { player = player.index, auto = true } else line_done(player, nil, err) end
  end)
end

local function finish(job, out)
  local player = game.get_player(job.player)
  if not (player and player.valid) then return end
  local res = out and helpers.json_to_table(out) or { error = "no answer" }
  if job.auto then
    if res.measure then return window.calibrate(player, res.measure) end -- then planned again (job.measured)
    if not res.blueprint then return line_done(player, nil, tostring(res.error)) end
    local n = window.place_plan(player, res)
    return line_done(player, n, nil, res.absolute or {})
  end
  if job.window and window.on_result(player, res) then return end
  if not res.blueprint then
    player.print("[bpgen] can't plan [recipe=" .. job.recipe .. "]: " .. tostring(res.error))
    if res.trace then safe.log("bpgen.ingame: " .. res.trace) end
    return
  end
  if not player.clear_cursor() then
    player.print("[bpgen] your hand is full: the blueprint is in the bpgen web app's history instead")
    return
  end
  player.cursor_stack.set_stack({ name = "blueprint" })
  if player.cursor_stack.import_stack(res.blueprint) == -1 then
    player.clear_cursor()
    player.print("[bpgen] the blueprint string didn't import")
    return
  end
  local msg = { "", "[bpgen] [recipe=" .. job.recipe .. "] blueprint in your hand: ", res.label or "",
    string.format(" (%.0f ms)", (res.seconds or 0) * 1000) }
  player.print(msg)
  for _, n in ipairs(res.notes or {}) do player.print("[bpgen]   " .. n) end
end

on_nth(6, function()
  for i = #measuring, 1, -1 do
    if game.tick >= measuring[i].tick then measured_now(table.remove(measuring, i)) end
  end
  if not warmed then
    warmed = true
    -- (loads bpgen's data before the first plan; its answer is collected and dropped like any job's)
    local id = in_game() and native.start("py", "bpgen.ingame:warm", "")
    if id then jobs[id] = { warm = true } end
    -- (bpgen's copy of the mods' data stale although the data stage ran: the game used its data stage cache; then
    -- that cache goes, and the next start hands the data over)
    local sid = in_game() and native.start("py", "bpgen.ingame:stale", "")
    if sid then jobs[sid] = { stale = true } end
  end
  for id, job in pairs(jobs) do
    local status, out = native.poll(id)
    if status ~= "pending" then
      jobs[id] = nil
      if job.warm then
        if status ~= "done" then safe.log("bpgen.ingame:warm failed: " .. tostring(out)) end
      elseif job.api then
        local player = game.get_player(job.player)
        if player then window.on_api(player, job.api, status == "done" and out or nil) end
      elseif job.measured then
        local player = game.get_player(job.player)
        if status ~= "done" then safe.log("bpgen.ingame:measured failed: " .. tostring(out)) end
        if player then
          local a = auto[player.index]
          if a then plan_line(player, a.item, a.rate, a.reply) else window.plan(player) end
        end
      elseif job.stale then
        local r = status == "done" and helpers.json_to_table(out) or {}
        if r.stale then
          game.print("[bpgen] your mods changed since bpgen read them: it reads them at the next game start")
        end
      elseif status == "done" then finish(job, out)
      else
        local player = game.get_player(job.player)
        if player then player.print("[bpgen] in-game planning failed: " .. tostring(out)) end
      end
    end
  end
end)

-- State: tech and production --------------------------------------------------------------------------------

--- per-minute production (made) and consumption (used) over the last 10 minutes, all surfaces added up
local function flows(force, getter)
  local made, used = {}, {}
  local precision = defines.flow_precision_index.ten_minutes
  for _, surface in pairs(game.surfaces) do
    local stats = force[getter](surface)
    for name in pairs(stats.input_counts) do
      local v = stats.get_flow_count({ name = name, category = "input", precision_index = precision })
      if v > 0 then made[name] = (made[name] or 0) + v end
    end
    for name in pairs(stats.output_counts) do
      local v = stats.get_flow_count({ name = name, category = "output", precision_index = precision })
      if v > 0 then used[name] = (used[name] or 0) + v end
    end
  end
  return made, used
end

-- What research decides (unlocked buildings, modules, recipe productivity, packs needed next) is worked out from
-- lists read once per load, and kept up to date from each research's own effects, so a finished research costs
-- next to nothing (scanning every recipe each time was a visible hitch in big packs). Not saved: rebuilt after a load.
local static = nil  -- recipe -> item products; type -> {{entity, item}}; tech -> {prereqs, ingredients}
local research_cache = {}  -- force index -> {made = set, productivity = {}, dirty = bool, out = ...}

local TYPES = { inserters = { "inserter" }, belts = { "transport-belt" }, undergrounds = { "underground-belt" },
  splitters = { "splitter" }, poles = { "electric-pole" }, assemblers = { "assembling-machine" },
  furnaces = { "furnace" }, labs = { "lab" }, beacons = { "beacon" }, drills = { "mining-drill" },
  chests = { "container", "logistic-container" }, pipes = { "pipe", "pipe-to-ground" } }

local function get_static()
  if static then return static end
  static = { products = {}, placeable = {}, techs = {}, modules = {}, stockable = {} }
  for name, r in pairs(prototypes.recipe) do
    if not r.hidden and r.category ~= "recycling" then
      local items = {}
      for _, p in pairs(r.products) do
        if p.type == "item" then items[#items + 1] = p.name end
      end
      static.products[name] = items
    end
  end
  for key, types in pairs(TYPES) do
    local list = {}
    for _, t in pairs(types) do
      for name, proto in pairs(prototypes.get_entity_filtered({ { filter = "type", type = t } })) do
        local items = proto.items_to_place_this
        if not proto.hidden and items and items[1] then list[#list + 1] = { name, items[1].name } end
      end
    end
    static.placeable[key] = list
  end
  for name in pairs(prototypes.get_item_filtered({ { filter = "type", type = "module" } })) do
    static.modules[#static.modules + 1] = name
    static.stockable[name] = true
  end
  -- what a blueprint needs: buildings (and modules, above), counted for the save check
  for name in pairs(prototypes.get_item_filtered({ { filter = "place-result" } })) do static.stockable[name] = true end
  for name, t in pairs(prototypes.technology) do
    local pre, ings = {}, {}
    for p in pairs(t.prerequisites) do pre[#pre + 1] = p end
    for _, i in pairs(t.research_unit_ingredients) do ings[#ings + 1] = i.name end
    static.techs[name] = { pre = pre, ings = ings, hidden = t.hidden }
  end
  return static
end

local function research_part(force)
  local st = get_static()
  local c = research_cache[force.index]
  if not c then  -- the first time after a load: one pass over the force's recipes
    c = { made = {}, productivity = {}, recipes = {}, dirty = true }
    for name, r in pairs(force.recipes) do
      if r.productivity_bonus ~= 0 then c.productivity[name] = r.productivity_bonus end
      if r.enabled then
        for _, item in pairs(st.products[name] or {}) do c.made[item] = true end
        if st.products[name] then c.recipes[name] = true end
      end
    end
    research_cache[force.index] = c
  end
  if c.dirty then
    local unlocked = {}
    for key, list in pairs(st.placeable) do
      local out = {}
      for _, e in pairs(list) do
        if c.made[e[2]] then out[#out + 1] = e[1] end
      end
      table.sort(out)
      unlocked[key] = out
    end
    local modules = {}
    for _, m in pairs(st.modules) do
      if c.made[m] then modules[#modules + 1] = m end
    end
    table.sort(modules)
    unlocked.modules = modules
    -- packs the technologies you can research next need: what blocks progress
    local researched = {}
    for name, tech in pairs(force.technologies) do researched[name] = tech.researched end
    local next_packs = {}
    for name, t in pairs(st.techs) do
      if not researched[name] and not t.hidden and force.technologies[name].enabled then
        local ready = true
        for _, p in pairs(t.pre) do
          if not researched[p] then ready = false break end
        end
        if ready then
          for _, ing in pairs(t.ings) do next_packs[ing] = (next_packs[ing] or 0) + 1 end
        end
      end
    end
    local recipes = {}
    for name in pairs(c.recipes) do recipes[#recipes + 1] = name end
    table.sort(recipes)
    c.out = { unlocked = unlocked, next_packs = next_packs, recipe_productivity = c.productivity, recipes = recipes }
    c.dirty = false
  end
  return c.out
end

--- a research finished: add what it unlocked (only its own effects are read)
local function on_research(tech)
  local c = research_cache[tech.force.index]
  if not c then return end
  local st = get_static()
  for _, eff in pairs(tech.prototype.effects or {}) do
    if eff.type == "unlock-recipe" then
      for _, item in pairs(st.products[eff.recipe] or {}) do c.made[item] = true end
      if st.products[eff.recipe] then c.recipes[eff.recipe] = true end
    elseif eff.type == "change-recipe-productivity" then
      local b = tech.force.recipes[eff.recipe] and tech.force.recipes[eff.recipe].productivity_bonus or 0
      c.productivity[eff.recipe] = b ~= 0 and b or nil
    end
  end
  c.dirty = true
end

--- buildings and modules on hand: the player's inventory and the logistic networks on their surface
local function stock(force)
  local player = game.connected_players[1]
  local surface = player and player.surface or game.surfaces.nauvis  -- (headless tests: no player)
  if not surface then return nil end
  local want, out = get_static().stockable, {}
  local function add(contents)
    for _, c in pairs(contents) do
      if want[c.name] then out[c.name] = (out[c.name] or 0) + c.count end
    end
  end
  local inv = player and player.get_main_inventory()
  if inv then add(inv.get_contents()) end
  for _, net in pairs(force.logistic_networks[surface.name] or {}) do
    if net.valid then add(net.get_contents()) end
  end
  return out
end

-- The production numbers are read a few hundred per second (every item and fluid ever made, on every surface, in one
-- tick was a 100 ms hitch in a big pack); the file is written once they're all in. Not saved: a load starts over.
local READS_PER_STEP = 400
local job = nil  -- {force, parts = {{getter, surface, names, made, used}}, part, key, made = {}, used = {}}

local function start_state(force)
  if job then return end
  local parts = {}
  for _, getter in pairs({ "get_item_production_statistics", "get_fluid_production_statistics" }) do
    for _, surface in pairs(game.surfaces) do
      parts[#parts + 1] = { getter = getter, surface = surface.index, category = "input" }
      parts[#parts + 1] = { getter = getter, surface = surface.index, category = "output" }
    end
  end
  job = { force = force, parts = parts, part = 1, key = nil,
    out = { get_item_production_statistics = { {}, {} }, get_fluid_production_statistics = { {}, {} } } }
end

--- one step of the job; true when the file was written
local function step_state()
  if not job then return false end
  local force = job.force
  if not force.valid then job = nil return false end
  local precision = defines.flow_precision_index.ten_minutes
  local reads = 0
  while reads < READS_PER_STEP do
    local part = job.parts[job.part]
    if not part then break end
    local surface = game.get_surface(part.surface)
    if not surface then
      job.part, job.key = job.part + 1, nil
    else
      local stats = force[part.getter](surface)
      part.names = part.names or (part.category == "input" and stats.input_counts or stats.output_counts)
      local dest = job.out[part.getter][part.category == "input" and 1 or 2]
      local name = next(part.names, job.key)
      while name and reads < READS_PER_STEP do
        local v = stats.get_flow_count({ name = name, category = part.category, precision_index = precision })
        if v > 0 then dest[name] = (dest[name] or 0) + v end
        reads = reads + 1
        job.key = name
        name = next(part.names, name)
      end
      if not name then job.part, job.key = job.part + 1, nil end
    end
  end
  if job.parts[job.part] then return false end
  local items, fluids = job.out.get_item_production_statistics, job.out.get_fluid_production_statistics
  job = nil
  local items_made, items_used, fluids_made, fluids_used = items[1], items[2], fluids[1], fluids[2]
  local r = research_part(force)  -- {unlocked, next_packs, recipe_productivity}
  local state = {
    tick = game.tick,
    next_packs = r.next_packs,
    force = force.name,
    bonuses = bonuses(force),
    recipe_productivity = r.recipe_productivity,
    unlocked = r.unlocked,
    recipes = r.recipes,
    stock = stock(force),
    production = { items_made = items_made, items_used = items_used, fluids_made = fluids_made, fluids_used = fluids_used },
  }
  helpers.write_file("bpgen/state.json", helpers.table_to_json(state), false)
  return true
end

--- the whole state now (the snapshot tool, tests): every step at once
local function write_state(force)
  job = nil
  start_state(force)
  while job and not step_state() do end
end

local function player_force()
  local player = game.connected_players[1]
  return player and player.force
end

local dirty_tick = nil
local function state_soon()
  dirty_tick = dirty_tick or game.tick + 600  -- research can finish in bursts: one write 10 s after the first
end

on_event(defines.events.on_player_joined_game, state_soon)
on_event(defines.events.on_research_finished, function(e)
  on_research(e.research)
  state_soon()
end)
on_event(defines.events.on_research_reversed, function(e)
  research_cache[e.research.force.index] = nil  -- rare: just start over
end)
script.on_configuration_changed(safe.guard("bpgen", function() research_cache, static = {}, nil end))
on_nth(60, function(e)
  if dirty_tick and e.tick >= dirty_tick then
    dirty_tick = nil
    local force = player_force()
    if force then start_state(force) end
  end
  step_state()
end)
on_nth(60 * 60 * 5, function()
  local force = player_force()
  if force then start_state(force) end
end)

-- Snapshot of an area ------------------------------------------------------------------------------------------

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
  local f = ent.fluidbox[1]
  if f then return f.name end
  local ok, locked = pcall(ent.fluidbox.get_locked_fluid, 1)
  return ok and locked or nil
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

snapshot = function(player, area, keep)  -- keep: the snapshot back instead of written to snapshot.json
  local surface = player.surface
  local ents, obstacles = {}, {}
  for _, e in pairs(surface.find_entities_filtered({ area = area })) do
    local t = e.type
    if OBSTACLE[t] then
      local b = e.bounding_box
      obstacles[#obstacles + 1] = { b.left_top.x, b.left_top.y, b.right_bottom.x, b.right_bottom.y, t }
    elseif not SKIP[t] and t ~= "resource" and (e.force == player.force or t == "entity-ghost") then
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
      ents[#ents + 1] = out
    end
  end
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
  if keep then return snap end
  helpers.write_file("bpgen/snapshot.json", helpers.table_to_json(snap), false)
  write_state(player.force)
  return #ents
end

local function on_selected(e)
  if e.item == "bpgen-patches" then return window.add_patch(game.get_player(e.player_index), e.area) end
  if e.item ~= "bpgen-snapshot" then return end
  local player = game.get_player(e.player_index)
  local area = e.area
  measure(player, area, function(snap)
    snaps[player.index] = snap  -- (in game: sent with the next extend; the file is for the web app)
    helpers.write_file("bpgen/snapshot.json", helpers.table_to_json(snap), false)
    write_state(player.force)
    player.print({ "", "[bpgen] snapshot sent: ", #snap.entities, " entities, ",
      math.floor(area.right_bottom.x - area.left_top.x), "x", math.floor(area.right_bottom.y - area.left_top.y), " tiles" })
  end)
end
on_event(defines.events.on_player_selected_area, on_selected)
on_event(defines.events.on_player_alt_selected_area, on_selected)

-- for automated tests (no player to press the hotkey in a headless run)
remote.add_interface("bpgen", {
  -- a production line of `item` at `rate` a minute next to the player's base (Extend), placed as ghosts; the answer
  -- comes back as remote.call(reply or "ai-crew", "line_placed", player_index, item, ghosts, error, absolute) where
  -- absolute = {box = {x, y, w, h}, inputs = {{items, position}} to feed by hand, outputs = {{item, position}}}
  plan_line = function(player_index, item, rate, reply) plan_line(game.get_player(player_index), item, rate, reply) end,
  -- the bpgen window (the fnative hub has a button for it); for tests also: fill it in and plan
  -- an ore patch for the window's bus design, as if dragged with the patch tool
  add_patch = function(player_index, area) window.add_patch(game.get_player(player_index), area) end,
  open_window = function(player_index, prefill) window.open(game.get_player(player_index), prefill) end,
  plan_window = function(player_index) window.plan(game.get_player(player_index)) end,
  click = function(player_index, tags) window.click(player_index, tags) end,
  hover_camera = function(player_index) return window.test_hover_camera(player_index) end,
  last_absolute = function(player_index) return window.last_absolute(player_index) end,
  hover_grip = function(player_index) window.test_hover_grip(player_index) end,
  compat_scan = function(player_index) compat.scan(game.get_player(player_index)) end,
  preview = function(bp, force_name) return window.preview({ index = 1, force = game.forces[force_name or "player"] }, bp) end,
  bench_test = function(spec, where) return window.bench_test(1, spec, where) end,
  bench_calibrate = function(spec) return window.bench_calibrate(1, spec) end,
  bench_last = function(kind) return window.bench_last(kind) end,
  write_request = function(entity) local r, err = write_request(entity.force, entity) return err end,
  write_state = function(force_name) write_state(game.forces[force_name or "player"]) end,
  snapshot_area = function(surface_name, area, force_name)
    -- headless: a pretend player on that surface and force
    local fake = { surface = game.surfaces[surface_name or "nauvis"], force = game.forces[force_name or "player"] }
    return snapshot(fake, area)
  end,
  -- the same, with the belts' flows measured: snapshot.json is written MEASURE_TICKS later
  measure_area = function(surface_name, area, force_name)
    local fake = { surface = game.surfaces[surface_name or "nauvis"], force = game.forces[force_name or "player"] }
    measure(fake, area, function(snap)
      helpers.write_file("bpgen/snapshot.json", helpers.table_to_json(snap), false)
      write_state(fake.force)
    end)
  end,
})

for event, handler in pairs(window.handlers) do on_event(event, handler) end
-- bpgen buttons in Recipe Book's and Factory Planner's windows (with in-game planning: they open the bpgen window)
on_nth(compat.RESCAN, function()
  if not in_game() then return end
  for _, player in pairs(game.connected_players) do compat.scan(player) end
end)
-- (with the fnative-std library: its window and input handlers, for the bpgen window's corner grip)
if script.active_mods["fnative-std"] then
  safe.chain("bpgen", { require("__fnative-std__/input").handlers, require("__fnative-std__/window").handlers })
end
