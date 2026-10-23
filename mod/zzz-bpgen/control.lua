-- Writes files in script-output/bpgen/ for the external bpgen tool (mods can only write files, not read them):
--   request.json   hover an assembler + Ctrl+Shift+B: plan a line for its recipe
--   state.json     what's researched and what the factory makes / uses per minute (on join, after research, every 5 min)
--   snapshot.json  an area of the base picked with the snapshot tool (the window's Extend tab)

local safe = require("safe")
local tasks = require("tasks")  -- (work spread over ticks)
local reader = require("reader")  -- (an area of the base read for Python, over ticks)
local place = require("place")  -- (ghosts placed a part a tick)
local build = require("build")
-- (every handler guarded: an error never takes the game down)
local function on_event(ev, fn, filters) script.on_event(ev, safe.guard("bpgen", fn), filters) end
local function on_nth(n, fn) script.on_nth_tick(n, safe.guard("bpgen", fn)) end

-- items some enabled recipe makes: one pass over the force's recipes (searching per item was far too slow with
-- big packs - tens of thousands of recipes times every placeable entity - and froze the game)
local function is_recycling(r) -- (2.1: recipes have a list of categories)
  for _, c in pairs(r.categories) do if c == "recycling" then return true end end
  return false
end

local function craftable(force)
  local out = {}
  for _, r in pairs(force.recipes) do
    -- recycling (always enabled in Space Age) turns better items back into lesser ones; it isn't a way to make them
    if r.enabled and not r.hidden and not is_recycling(r) then
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

-- In game planning (optional): with the fse loader and its py plugin, bpgen runs inside the game process
-- (bpgen/ingame.py) on a worker thread and the blueprint lands in the player's hand. Without it, the request file
-- above is all there is (the bpgen web app picks it up). Jobs live only in this Lua state: not saved.
local function in_game()
  -- (multiplayer answers travel through fse-std's sync)
  return safe.has("py") and (not game.is_multiplayer() or script.active_mods["fse-std"] ~= nil)
end

-- Jobs (bpgen's Python: plans, placing, the bus). Every peer records a job in storage the same way; only the asking
-- player's own peer runs it and, in multiplayer, sends the answer to all with native.sync (fse-std raises
-- "fse-sync"), so every peer shows and places the same thing in the same tick. Single player: the answer applies
-- at once. A job: {player = index, recipe = name, window = true | api = tag | measured = true | auto = true}
local running = {}     -- native job id -> job number (only on the peer running it)
local local_jobs = {}  -- native job id -> {warm | stale}: this peer's own housekeeping, never applied to the game
local warmed = false
local apply  -- (below)

local function runs_here(player_index)
  if not game.is_multiplayer() then return true end
  return native.local_player ~= nil and native.local_player() == player_index
end

local function deliver(n, status, out)
  if game.is_multiplayer() then
    native.sync("bpgen:answer", helpers.table_to_json({ n = n, status = status, out = out }))
  else
    apply(n, status, out)
  end
end

--- a part of a big snapshot handed to Python ahead of its job (reader.request_json)
local function send_part(text)
  local id = native.start("py", "bpgen.ingame:part", text)
  if id then local_jobs[id] = { part = true } end
end

--- `input`: the request as text, or as a table (its snapshot is written as text over the next ticks, by this peer
--- only, before the job starts)
local function start_job(player_index, fn, input, job)
  storage.bpgen_jobs = storage.bpgen_jobs or {}
  storage.bpgen_job_n = (storage.bpgen_job_n or 0) + 1
  local n = storage.bpgen_job_n
  job.at = game.tick
  storage.bpgen_jobs[n] = job
  if runs_here(player_index) then
    local function go(text)
      local id, err = native.start("py", fn, text)
      if id then running[id] = n else deliver(n, "error", tostring(err)) end
    end
    if type(input) == "table" then
      local ok, err = pcall(reader.request_json, input, go)
      if not ok then deliver(n, "error", tostring(err)) end  -- (a request that won't write: said, not waited for)
    else
      go(input)
    end
  end
  return true
end

local PLACE_R = 48  -- without an area picked with the snapshot tool: the snapshot reaches this far around a spot
local snaps = safe.stored("snaps")  -- player index -> the area last picked with the snapshot tool
local function around(c, r)
  r = r or PLACE_R
  return { left_top = { x = c.x - r, y = c.y - r }, right_bottom = { x = c.x + r, y = c.y + r } }
end
local record = reader.record  -- (one entity as the snapshot records it)

-- An area of the base read for Python (reader.lua: over ticks, a few cells a tick), with the belts' flows measured
-- unless `flows` is false; then after[kind](player, snapshot, data). The reader is kept in storage, so a peer
-- joining meanwhile finishes it too; `data` is plain data.
local after = {}  -- kind -> function(player, snap, data): what follows a measurement
-- Its records go to Python as they are read, from the peer that runs the job (a big base's never pile up in Lua);
-- KEEP: kinds whose snapshot is also written to a file (kept by this peer). `flows` false: belts not measured.
local KEEP = { snapshot_tool = true, measure_area = true }
local SINK_ONLY = { test_request = true }  -- (tests: sent nowhere, as a job's would be)
local function measure(player, area, kind, data, flows)
  local send = player.index and in_game() and runs_here(player.index) and send_part
    or SINK_ONLY[kind] and function() end or nil
  reader.start(player.surface, player.force, area, flows ~= false, { then_kind = kind, data = data,
    player = player.index, fake = not player.index and { surface = player.surface.name, force = player.force.name } or nil },
    send, KEEP[kind])
end
reader.done = function(r)
  if not r.then_kind then return end
  local player = r.player and game.get_player(r.player)
    or r.fake and { surface = game.get_surface(r.fake.surface), force = game.forces[r.fake.force] }
  if player and player.surface then after[r.then_kind](player, r.snap, r.data) end
end

local window = require("window")
local compat = require("compat")
local web = require("web")  -- (the window as a web page: fse's shells)
local sandbox = require("sandbox")  -- ("Try in sandbox": the plan in a lab with the base round it)
local hand = require("hand")  -- (a plan put down by hand, then tied into the base)

-- The area round the base Python keeps a site of for the player (the snapshot Place by my base read, or the last
-- paste's): what was built or removed in it since, so the next paste or Re-route there sends only that (a delta)
-- instead of reading the area again. {tick, surface, area = {x1, y1, x2, y2}, built = {records}, removed = {...}}
local sites = safe.stored("sites")
local DELTA_MAX = 3000  -- (more changes than this: read the area again)

local function site_set(player, snap)
  sites[player.index] = { tick = snap.tick, surface = snap.surface, area = snap.area, built = {}, removed = {} }
end

--- what changed in the player's site since it was read, when `box` ({x1, y1, x2, y2}, with its margin) lies in it;
--- the site moves on to now (Python applies it). -> {since, now, surface, built, removed} or nil (read it again)
local function site_delta(player, box)
  local s = sites[player.index]
  if not (s and s.surface == player.surface.name and box[1] >= s.area[1] and box[2] >= s.area[2]
      and box[3] <= s.area[3] and box[4] <= s.area[4]) then
    return nil
  end
  local d = { since = s.tick, now = game.tick, surface = s.surface, built = s.built, removed = s.removed }
  s.tick, s.built, s.removed = game.tick, {}, {}
  return d
end

local function inside(s, e)
  local p = e.position
  return s.surface == e.surface.name and p.x >= s.area[1] and p.x <= s.area[3] and p.y >= s.area[2] and p.y <= s.area[4]
end

local function site_built(e)
  local ent = e.entity or e.created_entity
  if not (ent and ent.valid) then return end
  for index, s in pairs(sites) do
    if inside(s, ent) then
      local r = record(ent, ent.force)
      if r then
        s.built[#s.built + 1] = r
        if #s.built + #s.removed > DELTA_MAX then sites[index] = nil end
      end
    end
  end
end

local function site_removed(e)
  local ent = e.entity
  if not (ent and ent.valid) then return end
  for index, s in pairs(sites) do
    if inside(s, ent) then
      local name = ent.type == "entity-ghost" and ent.ghost_name or ent.name
      local p, dropped = ent.position, false
      for i = #s.built, 1, -1 do  -- (built since the site was read, and gone again: as if never built)
        local b = s.built[i]
        if b.name == name and math.abs(b.position.x - p.x) < 0.01 and math.abs(b.position.y - p.y) < 0.01 then
          table.remove(s.built, i)
          dropped = true
          break
        end
      end
      if not dropped then s.removed[#s.removed + 1] = { name = name, position = { x = p.x, y = p.y } } end
      if #s.built + #s.removed > DELTA_MAX then sites[index] = nil end
    end
  end
end

-- (what a reading for the window shows in its progress bar)
local READ_SHOWN = { plan = true, move = true, place_base = true, add_lane = true }
reader.progress = function(r, fraction)
  local player = r.player and READ_SHOWN[r.then_kind] and game.get_player(r.player)
  if player then window.on_progress(player, { fraction = fraction, step = { "bpgen-window.reading-base",
    math.floor(fraction * 100) } }) end
end

hand.setup({ measure = measure, around = around, start_job = start_job, site_set = site_set, site_delta = site_delta,
  placing = function(player, ghosts) window.placing(player, ghosts) end,
  connected = function(player, res, ghosts, marks, area) window.on_connected(player, res, ghosts, marks, area) end })
-- the last plan as a blueprint for the player's cursor (params: seed, bus, section, turn): measured round them first
local function hand_plan(player, params)
  if not in_game() then return false, "start the game with the fse loader" end
  local w, h = window.plan_size(player)
  -- (room for the plan, its taps and poles (PLACE_R round it) and for moving it about with the cursor: a paste in
  -- this area is tied in from what changed since, without reading the ground again)
  local r = math.min(250, math.ceil(math.max(w or 0, h or 0) / 2) + PLACE_R + 32)
  measure(player, around(params.seed or player.position, r), "hand", params)
  return true
end
-- (a bpgen button in Recipe Book / Factory Planner: the window may be opened from it soon, so the web window's page
-- loads ahead, hidden, and what "Only unlocked" needs is read ahead a little each tick)
compat.on_button = function(player)
  web.prewarm(player)
  window.warm(player)
end
window.setup({
  craftable = craftable, unlocked = unlocked, bonuses = bonuses,
  start = function(player, request)
    if not in_game() then return false, "start the game with the fse loader" end
    request.player = player.index  -- (bpgen keeps each player's last plan: Place by my base finds this one)
    helpers.write_file("bpgen/request.json", helpers.table_to_json(request), false)
    local function go()
      return start_job(player.index, "bpgen.ingame:plan", request,
        { player = player.index, recipe = request.recipe, window = true })
    end
    if request.params and (request.params.mode == "base" and (request.params.fit_bus or request.params.add_to)
        or request.params.mode == "mall" and request.params.add_to) then
      -- (a base fitted to a bus lands where the bus ends: the ground there, water and what's built, around you)
      measure(player, around(player.position, 200), "plan", request, false)
      return true
    end
    if request.params and request.params.mode == "busdesign" then
      -- (the ground from the patches to the player and round it: ore, what's in the way, room for the bus head)
      local pos = player.position
      local x1, y1, x2, y2 = pos.x, pos.y, pos.x, pos.y
      for _, a in ipairs(request.params.patches) do
        x1, y1, x2, y2 = math.min(x1, a[1]), math.min(y1, a[2]), math.max(x2, a[3]), math.max(y2, a[4])
      end
      local m = 100
      measure(player, { left_top = { x = x1 - m, y = y1 - m }, right_bottom = { x = x2 + m, y = y2 + m } }, "plan",
        request, false)
      return true
    end
    if request.params and request.params.move then
      -- (the section and where it goes, round both: the ground it lands on, the belts it's run to)
      local a, p, m = request.params.move.area, player.position, 48
      measure(player, { left_top = { x = math.min(a[1], p.x) - m, y = math.min(a[2], p.y) - m },
        right_bottom = { x = math.max(a[3], p.x) + m, y = math.max(a[4], p.y) + m } }, "move", request)
      return true
    end
    if request.params and request.params.refit then
      -- (the area to refit and round it: the ground the mall goes on, the bus it is fed from)
      local a, m = request.params.refit.area, 48
      measure(player, { left_top = { x = a[1] - m, y = a[2] - m }, right_bottom = { x = a[3] + m, y = a[4] + m } },
        "plan", request)
      return true
    end
    if request.params and (request.params.mode == "extend" or request.params.bus_feed) then  -- (the base it builds next to, with the request)
      local picked = snaps[player.index]
      local pos = player.position
      if picked and (picked.surface ~= player.surface.name or pos.x < picked.area[1] - PLACE_R
          or pos.x > picked.area[3] + PLACE_R or pos.y < picked.area[2] - PLACE_R or pos.y > picked.area[4] + PLACE_R) then
        snaps[player.index], picked = nil, nil  -- (an area picked somewhere else: the player has moved on)
      end
      if picked and not reader.sent(picked) then  -- (picked before the game was loaded: Python hasn't its parts)
        measure(player, { left_top = { x = picked.area[1], y = picked.area[2] },
          right_bottom = { x = picked.area[3], y = picked.area[4] } }, "plan", request)
        return true
      end
      if picked then
        request.params.snapshot = picked
      else
        -- (a line fed from the bus: room beside the bus for the line and what it makes for itself)
        measure(player, around(player.position, request.params.bus_feed and 128 or nil), "plan", request)
        return true
      end
    end
    return go()
  end,
  -- the last plan next to the base: a snapshot around params.seed (the player, or where the window's arrows moved
  -- it), then bpgen places it (taps, a main bus, power); the answer lands in the window as a plan's does
  -- the last plan as a blueprint for the player's cursor (turned to face the bus round them, bpgen's spot shown);
  -- where they paste it, it is tied into the base (hand.lua). The answer goes to window.on_hand.
  hand = function(player, params) return hand_plan(player, params) end,
  place_base = function(player, params)
    if not in_game() then return false, "start the game with the fse loader" end
    local seed = params.seed or player.position
    -- (a plan bigger than the usual snapshot gets one it fits in, with room round it for the bus taps and poles)
    local w, h = window.plan_size(player)
    local r = math.min(250, math.max(PLACE_R, math.ceil(math.max(w or 0, h or 0) / 2) + PLACE_R / 2))
    local m = r - PLACE_R / 2
    -- (the window's arrows, Rotate, Redo: the last snapshot again while the spot is well inside it, not measured anew)
    local last = params.again and (storage.bpgen_place_snap or {})[player.index]
    if not reader.sent(last) then last = nil end  -- (read before the game was loaded: read again)
    if last and seed.x - last.area[1] > m and last.area[3] - seed.x > m
        and seed.y - last.area[2] > m and last.area[4] - seed.y > m then
      after.place_base(player, last, params)
      return true
    end
    measure(player, around(seed, r), "place_base", params)
    return true
  end,
  -- the bus around the player, looked at (lanes, what they carry, what the base lacks): to window.on_api "bus"
  bus_report = function(player, belts)
    if not in_game() then return false, "start the game with the fse loader" end
    measure(player, around(player.position), "bus_report", belts)
    return true
  end,
  -- a new lane of an item along that bus: its answer lands in the window as a plan's does (preview, Place it)
  add_lane = function(player, item)
    if not in_game() then return false, "start the game with the fse loader" end
    measure(player, around(player.position), "add_lane", item)
    return true
  end,
  -- the web app's helpers (recipe tree, save check, module ideas, history): answers go to window.on_api
  api = function(player, fn, params, tag)
    if in_game() then
      start_job(player.index, "bpgen.ingame:api", { fn = fn, params = params },
        { player = player.index, api = tag })
    end
  end,
  -- inserter setups measured in game: into bpgen's table, then the window plans again
  measured = function(player, cases)
    if in_game() then
      start_job(player.index, "bpgen.ingame:measured", helpers.table_to_json({ cases = cases }),
        { player = player.index, measured = true })
    end
  end,
})

--- a recipe planned as a line: in the bpgen window (planned now), or without the fse loader sent to the web app
local function request_recipe(player, recipe)
  if in_game() then
    window.open(player, { recipe = recipe })
    return window.plan(player)
  end
  local machine = window.best_machine(player, recipe)
  if not machine then return player.print("[bpgen] no unlocked machine makes [recipe=" .. recipe .. "]") end
  helpers.write_file("bpgen/request.json", helpers.table_to_json(recipe_request(player.force, recipe, machine)), false)
  player.print({ "", "[bpgen] request sent: [recipe=" .. recipe .. "] in [entity=" .. machine .. "]" })
end

-- middle click: only over an item, fluid or recipe shown in a window (not bpgen's own pickers, not with something in
-- hand, not over the world): what makes it, as a line
on_event("bpgen-pick", function(e)
  local player = game.get_player(e.player_index)
  if player.selected or (player.cursor_stack and player.cursor_stack.valid_for_read) then return end
  local el = e.element
  if el and el.valid and el.get_mod() == script.mod_name then return end
  local recipe = compat.hovered_recipe(e)
  if recipe then request_recipe(player, recipe) end
end)

on_event("bpgen-request", function(e)
  local player = game.get_player(e.player_index)
  -- over a recipe in a window (Recipe Book, Factory Planner, Factoriopedia...): that recipe, in the best machine
  local recipe = (e.in_gui or not player.selected) and compat.hovered_recipe(e)
  if recipe then return request_recipe(player, recipe) end
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
local auto = safe.stored("auto") -- player index -> {item, rate, reply}

local function line_done(player, ghosts, err, abs)
  local a = auto[player.index]
  auto[player.index] = nil
  if a and remote.interfaces[a.reply] and remote.interfaces[a.reply].line_placed then
    remote.call(a.reply, "line_placed", player.index, a.item, ghosts, err, abs)
  end
end

build.on_done.test_area = function(t) storage.bpgen_test_area = { parts = t.parts, wires = t.wires } end
place.after.test = function(_, ghosts, _, _, marks) storage.bpgen_test_placed, storage.bpgen_test_marks = ghosts, marks end
-- (a line placed for another mod: its ghosts are what Undo takes back, the asker told)
place.after.auto_line = function(player, ghosts, _, _, _, d)
  window.placing(player, ghosts)
  line_done(player, #ghosts, nil, d.absolute)
end

local function plan_line(player, item, rate, reply)
  auto[player.index] = { item = item, rate = rate, reply = reply or "ai-crew" }
  if not in_game() then return line_done(player, nil, "bpgen plans only with the fse loader") end
  local req = window.extend_request(player, item, rate)
  measure(player, around(player.position), "plan_line", req)
end

local function finish(job, out)
  local player = game.get_player(job.player)
  if not (player and player.valid) then return end
  local res = out and helpers.json_to_table(out) or { error = "no answer" }
  if job.auto then
    if res.measure then return window.calibrate(player, res.measure) end -- then planned again (job.measured)
    if not res.blueprint then return line_done(player, nil, tostring(res.error)) end
    return window.place_plan(player, res, "auto_line", { absolute = res.absolute or {} })
  end
  if job.hand then return window.on_hand(player, res) end
  if job.connect then
    if not hand.current(player, job) then return end  -- (undone, or another paste since: dropped)
    if res.rescan then return hand.rescan(player, job) end  -- (Python's site isn't the one the delta was from)
    if res.error then return window.on_connected(player, res) end
    return hand.connected(player, res, job)  -- (then window.on_connected, once its links are placed)
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

-- the running plan's progress for the window: {fraction, step, lines = the new ones}. Read from fse's notes (0.17.2+),
-- which needs no Python: asking bpgen itself waits whenever the planning worker holds Python's lock, and the game
-- stuttered for as long as that took (up to a second a call)
local notes, seen_run, seen_lines = nil, nil, 0
local function progress()
  if notes ~= false then
    local text = native.call("py", "_note", "bpgen.progress")
    notes = text ~= nil
    if notes then
      local p = text ~= "" and helpers.json_to_table(text) or nil
      if not p then return nil end
      local all = p.lines or {}
      if p.run ~= seen_run then seen_run, seen_lines = p.run, 0 end  -- (a new plan)
      p.lines = { table.unpack(all, seen_lines + 1) }
      seen_lines = #all
      return p
    end
  end
  local text = native.call("py", "bpgen.ingame:progress", "")  -- (an older fse)
  return text and helpers.json_to_table(text) or nil
end

on_nth(6, function()
  hand.tick()
  if not warmed then
    warmed = true
    -- (loads bpgen's data before the first plan; its answer is collected and dropped)
    -- (with Power Propagation's startup settings: its powered buildings carry power, plans need fewer poles)
    local pp = script.active_mods["power-propagation"] and settings.startup["power-propagation-range"] and {
      range = settings.startup["power-propagation-range"].value,
      buildings = settings.startup["power-propagation-through-powered-buildings"] == nil
        or settings.startup["power-propagation-through-powered-buildings"].value } or nil
    local id = in_game() and native.start("py", "bpgen.ingame:warm", helpers.table_to_json({ propagation = pp }))
    if id then local_jobs[id] = { warm = true } end
    -- (bpgen's copy of the mods' data stale: the mods changed; a headless game reads them again meanwhile)
    local sid = in_game() and native.start("py", "bpgen.ingame:stale", helpers.table_to_json(script.active_mods))
    if sid then local_jobs[sid] = { stale = true } end
  end
  for id, job in pairs(local_jobs) do
    local status, out = native.poll(id)
    if status ~= "pending" then
      local_jobs[id] = nil
      if job.part then
        if status ~= "done" then safe.log("bpgen.ingame:part failed: " .. tostring(out)) end
      elseif job.warm then
        if status ~= "done" then safe.log("bpgen.ingame:warm failed: " .. tostring(out)) end
      else
        local r = status == "done" and helpers.json_to_table(out) or {}
        if r.stale then
          local msg = "[bpgen] your mods changed since bpgen read them: it reads them again now (about 2 minutes, "
            .. "plans wait for it)"
          -- (this peer's own news: printing it would make its console differ from the others')
          if game.is_multiplayer() then safe.log(msg) else game.print(msg) end
        end
      end
    end
  end
  for id, n in pairs(running) do
    local status, out = native.poll(id)
    if status ~= "pending" then
      running[id] = nil
      deliver(n, status, out)
    elseif game.tick % 30 == 0 and not game.is_multiplayer() then
      -- (the plan getting on, in the window: single player only, a GUI only one peer changed would desync)
      local job = storage.bpgen_jobs and storage.bpgen_jobs[n]
      local player = job and job.window and game.get_player(job.player)
      local p = player and progress()
      if p then window.on_progress(player, p) end
    end
  end
  if game.tick % 600 == 0 and storage.bpgen_jobs then -- (a job whose peer left, or never answered)
    for n, job in pairs(storage.bpgen_jobs) do if game.tick - job.at > 36000 then storage.bpgen_jobs[n] = nil end end
  end
end)

-- a job's answer, on every peer
function apply(n, status, out)
  local job = storage.bpgen_jobs and storage.bpgen_jobs[n]
  if not job then return end
  storage.bpgen_jobs[n] = nil
  do
    do
      if job.api then
        local player = game.get_player(job.player)
        if player then window.on_api(player, job.api, status == "done" and out or nil) end
      elseif job.measured then
        local player = game.get_player(job.player)
        if status ~= "done" then safe.log("bpgen.ingame:measured failed: " .. tostring(out)) end
        if player then
          local a = auto[player.index]
          if a then plan_line(player, a.item, a.rate, a.reply) else window.plan(player) end
        end
      elseif status == "done" then finish(job, out)
      elseif job.auto then
        local player = game.get_player(job.player)
        if player then line_done(player, nil, tostring(out)) end
      else
        local player = game.get_player(job.player)
        if player then player.print("[bpgen] in-game planning failed: " .. tostring(out)) end
      end
    end
  end
end

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
    if not r.hidden and not is_recycling(r) then
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
  window.forget_unlocked(e.research.force)
  state_soon()
end)
on_event(defines.events.on_research_reversed, function(e)
  research_cache[e.research.force.index] = nil  -- rare: just start over
  window.forget_unlocked(e.research.force)
end)
-- (what a force has unlocked changed without a research: the window's "Only unlocked" pickers build theirs again)
on_event(defines.events.on_force_reset, function(e) window.forget_unlocked(e.force) end)
on_event(defines.events.on_technology_effects_reset, function(e)
  window.forget_unlocked(e.force)
  window.preview_stale()  -- (the preview's force: its recipes enabled again next time)
end)
script.on_configuration_changed(safe.guard("bpgen", function()
  research_cache, static = {}, nil
  storage.bpgen_measuring = nil  -- (before 0.29.0: readings done in one tick)
  window.forget_unlocked()  -- (other mods, other recipes and machines)
  window.preview_stale()
end))
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

--- the area read now and written to snapshot.json (with state.json): the web app's, and the tests'
local function snapshot_now(player, area, file)
  local snap, kept = reader.read_now(player.surface, player.force, area)
  reader.write_file(file or "bpgen/snapshot.json", reader.pieces(snap, kept), true)
  write_state(player.force)
  return snap.count
end

-- what follows a measurement (see measure): one function per kind, the data plain
local function plan_with(player, snap, request, fn, job)
  request.player = player.index  -- (bpgen keeps each player's last plan and ground apart)
  request.params.snapshot = snap
  start_job(player.index, fn, request, job)
end
after.plan = function(player, snap, request)
  plan_with(player, snap, request, "bpgen.ingame:plan", { player = player.index, recipe = request.recipe, window = true })
end
after.move = function(player, snap, request)
  plan_with(player, snap, request, "bpgen.ingame:move", { player = player.index, recipe = "", window = true })
end
after.plan_line = function(player, snap, req)
  plan_with(player, snap, req, "bpgen.ingame:plan", { player = player.index, auto = true })
end
after.place_base = function(player, snap, params)
  storage.bpgen_place_snap = storage.bpgen_place_snap or {}
  storage.bpgen_place_snap[player.index] = snap
  params.again = nil
  params.snapshot = snap
  params.player = player.index
  start_job(player.index, "bpgen.ingame:place", params, { player = player.index, recipe = "", window = true })
end
after.hand = function(player, snap, params)
  site_set(player, snap)  -- (Python keeps this one: pastes in it send what changed since)
  params.snapshot, params.player = snap, player.index
  params.seed = params.seed or { x = player.position.x, y = player.position.y }
  start_job(player.index, "bpgen.ingame:hand", params, { player = player.index, hand = true })
end
after.connect = function(player, snap, data) hand.measured(player, snap, data) end
after.bus_report = function(player, snap, belts)
  start_job(player.index, "bpgen.ingame:bus_report", { snapshot = snap, belts = belts },
    { player = player.index, api = "bus" })
end
after.add_lane = function(player, snap, item)
  start_job(player.index, "bpgen.ingame:add_lane", { snapshot = snap, item = item },
    { player = player.index, recipe = "", window = true })
end
after.snapshot_tool = function(player, snap, area)
  snaps[player.index] = snap  -- (in game: sent with the next extend; the file is for the web app)
  reader.write_file("bpgen/snapshot.json", reader.pieces(snap, reader.kept(snap)))
  start_state(player.force)
  player.print({ "", "[bpgen] snapshot sent: ", snap.count, " entities, ",
    math.floor(area.right_bottom.x - area.left_top.x), "x", math.floor(area.right_bottom.y - area.left_top.y), " tiles" })
end
after.test_request = function(fake, snap)  -- (tests: a request as a plan's, handed to nobody)
  reader.request_json({ recipe = "test", params = { snapshot = snap } }, function() end)
end
after.measure_area = function(fake, snap)
  reader.write_file("bpgen/snapshot.json", reader.pieces(snap, reader.kept(snap)))
  start_state(fake.force)
end

-- answers from the peer that ran a job (multiplayer)
if script.active_mods["fse-std"] then
  on_event("fse-sync", function(e)
    if e.key ~= "bpgen:answer" then return end
    local d = helpers.json_to_table(e.data)
    if d and d.n then apply(d.n, d.status, d.out) end
  end)
end

local function on_selected(e)
  local player = game.get_player(e.player_index)
  if e.item == "bpgen-patches" then return window.add_patch(player, e.area) end
  if not window.TOOLS[e.item] then return end
  -- (one drag is all these take: the tool put away, the window back to show what it does)
  player.clear_cursor()
  window.tool_done(player)
  if e.item == "bpgen-refit" then return window.refit(player, e.area) end
  if e.item == "bpgen-move" then return window.move(player, e.area) end
  if e.item == "bpgen-spot" then return window.spot(player, e.area) end
  measure(player, e.area, "snapshot_tool", e.area)
end
on_event(defines.events.on_player_selected_area, on_selected)

-- a bpgen ship's inserters that feed the hub run only while it holds few of their item: a blueprint can't hold the
-- hub, so each one tagged "bpgen_hub" is wired to its platform's hub as it's built (and the hub reads its contents)
local function hub_wire(e)
  local ent, tags = e.entity, e.tags
  if not (ent and ent.valid and tags and tags.bpgen_hub) then return end
  local platform = ent.surface.platform
  local hub = platform and platform.hub
  if not (hub and hub.valid) then return end
  hub.get_or_create_control_behavior().read_contents = true
  local red = defines.wire_connector_id.circuit_red
  ent.get_wire_connector(red, true).connect_to(hub.get_wire_connector(red, true), false)
end
for _, ev in pairs({ defines.events.on_space_platform_built_entity, defines.events.on_built_entity,
                     defines.events.on_robot_built_entity, defines.events.script_raised_revive,
                     defines.events.script_raised_built }) do
  on_event(ev, function(e)
    hub_wire(e)
    site_built(e)
    hand.on_built(e)
  end)
end
for _, ev in pairs({ defines.events.on_player_mined_entity, defines.events.on_robot_mined_entity,
                     defines.events.on_entity_died, defines.events.script_raised_destroy,
                     defines.events.on_space_platform_mined_entity }) do
  on_event(ev, site_removed)
end
on_event(defines.events.on_player_rotated_entity, site_built)  -- (turned: recorded again, in its place)
on_event(defines.events.on_player_alt_selected_area, on_selected)

-- for automated tests (no player to press the hotkey in a headless run)
remote.add_interface("bpgen", {
  -- the player's last plan into their cursor, as Place by my base does (params: seed, bus, section, turn)
  hand = function(player_index, params) return hand_plan(game.get_player(player_index), params or {}) end,
  -- the player's last paste: {id, plan = its own ghosts left, links = what ties it in}, or nil; its card's buttons
  hand_record = function(player_index) return hand.record(player_index) end,
  hand_reroute = function(player_index) return hand.reroute(game.get_player(player_index)) end,
  hand_move = function(player_index) return hand.move(game.get_player(player_index)) end,
  unplace = function(player_index) window.unplace(game.get_player(player_index)) end,
  -- a production line of `item` at `rate` a minute next to the player's base (Extend), placed as ghosts; the answer
  -- comes back as remote.call(reply or "ai-crew", "line_placed", player_index, item, ghosts, error, absolute) where
  -- absolute = {box = {x, y, w, h}, inputs = {{items, position}} to feed by hand, outputs = {{item, position}}}
  plan_line = function(player_index, item, rate, reply) plan_line(game.get_player(player_index), item, rate, reply) end,
  -- the bpgen window (the fse hub has a button for it); for tests also: fill it in and plan
  -- an ore patch for the window's bus design, as if dragged with the patch tool
  add_patch = function(player_index, area) window.add_patch(game.get_player(player_index), area) end,
  open_window = function(player_index, prefill) window.open(game.get_player(player_index), prefill) end,
  -- (tests) "Only unlocked" set; the recipe picker's filters back
  only_unlocked = function(player_index, on)
    local player = game.get_player(player_index)
    window.set_only_unlocked(player, on)
    local frame = player.gui.screen.bpgen_window
    local function find(el, name)
      if el.name == name then return el end
      for _, c in pairs(el.children) do local r = find(c, name) if r then return r end end
    end
    local b = frame and find(frame, "bpgen_recipe")
    return b and b.elem_filters
  end,
  -- (tests) the recipe filters "Only unlocked" sets, for a force
  recipe_filters = function(force_name) return window.recipe_filters(game.forces[force_name or "player"]) end,
  plan_window = function(player_index) window.plan(game.get_player(player_index)) end,
  -- (tests) the blueprint the window's buttons act on now, and which of a line's layouts it is
  last_blueprint = function(player_index) return window.last_blueprint(player_index) end,
  -- (tests) a button press, as its tags; as_button: as the player's own press (Undo asks first, as for a click)
  click = function(player_index, tags, as_button) window.click(player_index, tags, as_button) end,
  -- (tests) the planning progress bar at {fraction, step, lines}
  progress = function(player_index, p) window.on_progress(game.get_player(player_index), p) end,
  -- (tests) a setting changed by the player (scripts raise no GUI events), by its element's name
  touch = function(player_index, name) window.touch(player_index, name) end,
  -- (tests) a drop-down's item picked, by its element's name (the window's settings too)
  pick = function(player_index, name, index) return window.pick(player_index, name, index) end,
  -- (tests) the window resized (GUI units), as by its corner grip
  resize = function(player_index, w, h) window.test_resize(player_index, w, h) end,
  -- (tests) "Docked" ticked or not
  docked = function(player_index, on) window.set_docked(game.get_player(player_index), on) end,
  hover_camera = function(player_index) return window.test_hover_camera(player_index) end,
  -- (tests) the web window: its state (the shell, its islands, what the page reported), the setting switched
  web_state = function(player_index) return web.state(game.get_player(player_index)) end,
  web_pick = function(player_index, ref, value) return web.test_pick(game.get_player(player_index), ref, value) end,
  web_wanted = function(player_index, on)
    local player = game.get_player(player_index)
    web.set_wanted(player, on)
    window.rebuild_any(player)
  end,
  last_absolute = function(player_index) return window.last_absolute(player_index) end,
  hover_grip = function(player_index) window.test_hover_grip(player_index) end,
  compat_scan = function(player_index) compat.scan(game.get_player(player_index)) end,
  hovered_recipe = function(e) return compat.hovered_recipe(e) end,  -- (tests: Ctrl+Shift+B over that)
  -- (tests) a blueprint's preview, built at once; or an answer's (JSON, its pieces): built over the next ticks
  -- ({pending = true}), preview_done then tells what was built
  preview = function(bp, force_name, answer)
    return window.preview({ index = 1, force = game.forces[force_name or "player"] }, bp,
      answer and helpers.json_to_table(answer))
  end,
  preview_done = function(force_name)
    return window.preview_result({ index = 1, force = game.forces[force_name or "player"] })
  end,
  bench_test = function(spec, where) return window.bench_test(1, spec, where) end,
  bench_calibrate = function(spec) return window.bench_calibrate(1, spec) end,
  bench_last = function(kind) return window.bench_last(kind) end,
  write_request = function(entity) local r, err = write_request(entity.force, entity) return err end,
  write_state = function(force_name) write_state(game.forces[force_name or "player"]) end,
  snapshot_area = function(surface_name, area, force_name, file)
    -- headless: a pretend player on that surface and force
    local fake = { surface = game.surfaces[surface_name or "nauvis"], force = game.forces[force_name or "player"] }
    return snapshot_now(fake, area, file)
  end,
  -- (tests) an answer's plan placed as ghosts (its pieces, a part a tick) by a stand-in player at c or at box, as
  -- Place it does; test_placed: what was placed ({name, x, y, direction} each, and how many), test_unplace: Undo
  test_place = function(answer, c, box, surface_name, force_name)
    local fake = { surface = game.surfaces[surface_name or "nauvis"], force = game.forces[force_name or "player"] }
    storage.bpgen_test_placed = nil
    place.start(fake, helpers.json_to_table(answer), c, box, nil, "test", {})
  end,
  test_placed = function()
    local out = {}
    for _, g in ipairs(storage.bpgen_test_placed or {}) do
      if g.valid then out[#out + 1] = { name = g.ghost_name or g.name, x = g.position.x, y = g.position.y,
        direction = g.direction } end
    end
    return out
  end,
  -- (tests) an area blueprinted a cell a tick, as the Move tool does; test_area_printed: its parts and wires
  test_area_print = function(area, surface_name, force_name)
    storage.bpgen_test_area = nil
    build.area_print(game.surfaces[surface_name or "nauvis"], game.forces[force_name or "player"], area, "test_area", {})
  end,
  test_area_printed = function() return storage.bpgen_test_area end,
  test_unplace = function()
    build.destroy_later(storage.bpgen_test_placed or {}, true, nil, nil, nil, storage.bpgen_test_marks or {})
  end,
  -- (tests) whether bpgen still has work spread over the coming ticks
  busy = function() return tasks.busy() end,
  debug_tasks = function(on, gc_kb) tasks.debug, tasks.gc_kb = on, gc_kb or tasks.gc_kb end,
  heap = function() return collectgarbage("count") end,
  -- the same, with the belts' flows measured: snapshot.json is written MEASURE_TICKS later
  -- the sandbox without a player: a lab of `plan` (see sandbox.open) -> its surface and edges; then what pasting it
  -- back would place (ghost names and positions) and remove, the lab closed
  sandbox_open = function(index, surface_name, force_name, plan)
    local st = sandbox.open(index, game.get_surface(surface_name), game.forces[force_name],
      type(plan) == "string" and helpers.json_to_table(plan) or plan)
    return { surface = st.surface, area = st.area }
  end,
  -- (tests, debugging) where the lab's plan stands: its area, the plan's spot and box, how many entities it has
  sandbox_state = function(index)
    local st = sandbox.state(index)
    if not st then return nil end
    local n, real = 0, 0
    for _, e in pairs(st.plan_ents or {}) do if e.valid then n = n + 1 end end
    for _ in pairs(st.plan_real or {}) do real = real + 1 end
    return { surface = st.surface, area = st.area, at = st.plan and st.plan.at, dir = st.plan and st.plan.dir,
      plan_box = st.plan_box, plan_ents = n, plan_real = real, feeds = #st.feeds, drains = #st.drains,
      pipes = #st.pipes, busy = sandbox.busy(index) }
  end,
  sandbox_move = function(index, dx, dy, turn)
    local ok, why = sandbox.move(index, dx, dy, turn)
    return { ok = ok, why = why }
  end,
  -- (over the next ticks, then the lab closed: sandbox_pasted tells what it placed)
  sandbox_paste = function(index) sandbox.paste(index) end,
  sandbox_pasted = function()
    local last = storage.bpgen_sandbox_last or {}
    local g, r = {}, {}
    for _, e in pairs(last.ghosts or {}) do
      if e.valid then g[#g + 1] = { name = e.ghost_name or e.name, position = e.position } end
    end
    for _, e in pairs(last.removed or {}) do if e.valid then r[#r + 1] = { name = e.name, position = e.position } end end
    return { ghosts = g, removed = r, marks = #(last.marks or {}) }
  end,
  -- (tests) the area measured and its snapshot made into a request for Python, as a plan's (sent nowhere)
  test_request = function(surface_name, area, force_name, flows)
    local fake = { surface = game.surfaces[surface_name or "nauvis"], force = game.forces[force_name or "player"] }
    measure(fake, area, "test_request", nil, flows)
  end,
  measure_area = function(surface_name, area, force_name)
    local fake = { surface = game.surfaces[surface_name or "nauvis"], force = game.forces[force_name or "player"] }
    measure(fake, area, "measure_area")
  end,
})

for event, handler in pairs(window.handlers) do on_event(event, handler) end
-- (the sandbox: its edges fed, god mode's instant building; after the others, onto them)
safe.chain("bpgen", { tasks.handlers, build.handlers, sandbox.handlers })
-- bpgen buttons in Recipe Book's and Factory Planner's windows (with in-game planning: they open the bpgen window)
on_nth(compat.RESCAN, function()
  if not in_game() then return end
  for _, player in pairs(game.connected_players) do compat.scan(player) end
end)
-- (with the fse-std library: its window and input handlers, for the bpgen window's corner grip)
if script.active_mods["fse-std"] then
  safe.chain("bpgen", { require("__fse-std__/input").handlers, require("__fse-std__/window").handlers,
    require("ui").handlers,  -- (the theme: restyled when a player picks another, folds, toasts)
    require("__fse-std__/dnd").handlers })  -- (History's rows dragged onto the preview)
  -- (the web window: fse-std's shells, when this fse-std has them)
  local web_shell = web.shell
  if web_shell then safe.chain("bpgen", { web_shell.handlers }) end
end
