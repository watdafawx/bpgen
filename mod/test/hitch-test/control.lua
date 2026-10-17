-- (hitch_test.py) how long bpgen holds the game thread: a dense base built on nauvis, then each of bpgen's game-side
-- steps run one at a time (preview, placing, the sandbox, reading the base, an answer applied) on small, large and
-- huge inputs. The steps are logged with the ticks they took ("HITCH name first last"); the runner reads each tick's
-- script time from the benchmark's per-tick output.
local BP = require("bps")  -- (written by hitch_test.py: name -> blueprint string)

local function out(line) helpers.write_file("hitch.txt", line .. "\n", true) end
local E, S = defines.direction.east, defines.direction.south
local BASE = 200  -- the dense base: -BASE..BASE both ways

-- a dense base: rows of belts, inserters and assemblers with poles, every third belt carrying plates
local function build_base(s, f)
  s.request_to_generate_chunks({ 0, 0 }, math.ceil(BASE / 32) + 2)
  s.force_generate_chunk_requests()
  for _, e in pairs(s.find_entities_filtered({ area = { { -BASE - 40, -BASE - 40 }, { BASE + 40, BASE + 40 } } })) do
    if e.valid and e.type ~= "character" then e.destroy() end
  end
  local tiles = {}
  for x = -BASE - 40, BASE + 39 do
    for y = -BASE - 40, BASE + 39 do tiles[#tiles + 1] = { name = "refined-concrete", position = { x, y } } end
  end
  s.set_tiles(tiles)
  local n = 0
  for y0 = -BASE, BASE - 7, 7 do
    for x = -BASE, BASE - 1 do
      for _, y in ipairs({ y0, y0 + 6 }) do
        local b = s.create_entity({ name = "transport-belt", position = { x + 0.5, y + 0.5 }, direction = E, force = f })
        if b and (x % 3 == 0) then
          for lane = 1, 2 do
            local line = b.get_transport_line(lane)
            for _ = 1, 4 do if line.can_insert_at_back() then line.insert_at_back({ name = "iron-plate", count = 1 }) end end
          end
        end
        n = n + 1
      end
    end
    for x = -BASE + 1, BASE - 3, 3 do
      local a = s.create_entity({ name = "assembling-machine-2", position = { x + 0.5, y0 + 3.5 }, force = f })
      if a then a.set_recipe("iron-gear-wheel") end
      s.create_entity({ name = "inserter", position = { x + 0.5, y0 + 1.5 }, direction = S, force = f })
      s.create_entity({ name = "inserter", position = { x + 0.5, y0 + 5.5 }, direction = S, force = f })
      n = n + 3
    end
    for x = -BASE + 2, BASE - 3, 6 do
      s.create_entity({ name = "small-electric-pole", position = { x + 0.5, y0 + 1.5 }, force = f })
      n = n + 1
    end
  end
  -- (free ground east of it, for the plans placed)
  s.request_to_generate_chunks({ 420, 0 }, 11)
  s.force_generate_chunk_requests()
  for _, e in pairs(s.find_entities_filtered({ area = { { 260, -360 }, { 600, 360 } } })) do
    if e.valid and e.type ~= "character" then e.destroy() end
  end
  tiles = {}
  for x = 260, 599 do
    for y = -360, 359 do tiles[#tiles + 1] = { name = "refined-concrete", position = { x, y } } end
  end
  s.set_tiles(tiles)
  f.chart(s, { { -BASE - 40, -360 }, { 600, 360 } })
  local eei = s.create_entity({ name = "electric-energy-interface", position = { -BASE - 10, -BASE }, force = f })
  if eei then eei.power_production, eei.electric_buffer_size = 1e9, 1e9 end
  return n
end

-- what place.place does (no player for build_blueprint here: a headless game has none)
local function place(s, f, bp, c, box)
  local inv = game.create_inventory(1)
  local stack = inv[1]
  stack.set_stack({ name = "blueprint" })
  stack.import_stack(bp)
  if stack.is_blueprint_book then stack = stack.get_inventory(defines.inventory.item_main)[1] end
  if box then c = { x = box[1] + box[3] / 2, y = box[2] + box[4] / 2 } end
  local reach = box and { { box[1] - 1, box[2] - 1 }, { box[1] + box[3] + 1, box[2] + box[4] + 1 } }
    or { { c.x - 200, c.y - 200 }, { c.x + 200, c.y + 200 } }
  local before = #s.find_entities_filtered({ area = reach, to_be_deconstructed = true })
  local ghosts = stack.build_blueprint({ surface = s, force = f, position = c,
    build_mode = box and defines.build_mode.superforced or defines.build_mode.forced, skip_fog_of_war = false,
    raise_built = true })
  inv.destroy()
  local after = #s.find_entities_filtered({ area = reach, to_be_deconstructed = true })
  return ghosts, after - before
end

local old_snapshot = require("old_snapshot")
local ONLY = BP.only  -- (hitch_test.py --only: the steps whose name has it)

local steps = {}
--- a step: fn(surface, force) runs in one tick; the step lasts until bpgen has nothing left to do (at least `wait`
--- ticks)
local function step(name, fn, wait)
  if ONLY and not name:find(ONLY, 1, true) then return end
  steps[#steps + 1] = { name = name, fn = fn, wait = wait or 2 }
end

local placed = {}
for _, k in ipairs({ "small", "large", "huge", "base" }) do
  step("preview " .. k, function() remote.call("bpgen", "preview", BP[k], "player", BP[k .. "_answer"]) end)
end
-- (not timed: the preview built from the answer's pieces, over ticks, against the whole print built at once: the
-- same entities, the same way round, the same distances apart, the same wires)
local function preview_signature()
  local s = game.surfaces["bpgen-preview"]
  local ents = s.find_entities_filtered({ force = "bpgen-preview" })
  local x0, y0 = math.huge, math.huge
  for _, e in pairs(ents) do
    if e.name ~= "electric-energy-interface" then x0, y0 = math.min(x0, e.position.x), math.min(y0, e.position.y) end
  end
  local sig, wires = {}, 0
  for _, e in pairs(ents) do
    if e.name ~= "electric-energy-interface" then
      local n = 0
      for _, c in pairs(e.get_wire_connectors(false) or {}) do n = n + c.connection_count end
      wires = wires + n
      sig[#sig + 1] = string.format("%s|%d|%.1f|%.1f|w%d", e.name, e.direction, e.position.x - x0, e.position.y - y0, n)
    end
  end
  table.sort(sig)
  return table.concat(sig, ";"), #sig, wires
end
for _, k in ipairs({ "huge", "base" }) do
  step("identity preview " .. k, function()
    remote.call("bpgen", "preview", BP[k])  -- (at once: the whole print)
    storage.sig, storage.sig_n, storage.sig_w = preview_signature()
    remote.call("bpgen", "preview", BP[k] .. " ", "player", BP[k .. "_answer"])  -- (its pieces; " ": not the same key)
  end)
  step("identity preview " .. k .. " (compared)", function()
    local sig, n, w = preview_signature()
    if sig ~= storage.sig then  -- (for a look at what differs)
      helpers.write_file("sig-" .. k .. "-whole.txt", (storage.sig:gsub(";", "\n")))
      helpers.write_file("sig-" .. k .. "-pieces.txt", (sig:gsub(";", "\n")))
    end
    out(string.format("identity preview %s: %d entities %d wire ends at once, %d and %d from pieces: %s", k,
      storage.sig_n, storage.sig_w, n, w, (sig == storage.sig and w == storage.sig_w) and "same" or "DIFFERENT"))
  end)
end
for _, k in ipairs({ "small", "large", "huge", "base" }) do
  step("place " .. k, function()
    remote.call("bpgen", "test_place", BP[k .. "_answer"],
      { x = 430, y = ({ small = -300, large = -200, huge = -100, base = 100 })[k] })
  end)
  step("undo " .. k, function() remote.call("bpgen", "test_unplace") end)
end
step("place absolute huge (over the base)", function()
  remote.call("bpgen", "test_place", BP.absolute_answer, nil, BP.absolute_box)
end)
step("undo absolute", function() remote.call("bpgen", "test_unplace") end)
-- (not timed: the absolute plan's pieces land where the whole print lands, pasted as 0.28 did it)
local function ghost_sig(list)
  local sig = {}
  for _, g in pairs(list) do
    sig[#sig + 1] = string.format("%s|%d|%.2f|%.2f", g.ghost_name or g.name, g.direction or 0, g.position.x or g.x,
      g.position.y or g.y)
  end
  table.sort(sig)
  return table.concat(sig, ";"), #sig
end
step("identity place absolute", function(s, f)
  local b = BP.absolute_box
  local area = { { b[1] - 1, b[2] - 1 }, { b[1] + b[3] + 1, b[2] + b[4] + 1 } }
  log("IDENT before: ghosts " .. s.count_entities_filtered({ area = area, type = "entity-ghost" }) .. ", marked "
    .. s.count_entities_filtered({ area = area, to_be_deconstructed = true }) .. ", all "
    .. s.count_entities_filtered({ area = area }))
  local ghosts = place(s, f, BP.absolute, nil, BP.absolute_box)
  log("IDENT whole: " .. #ghosts)
  local list = {}
  for _, g in pairs(ghosts) do
    if g.valid then list[#list + 1] = { name = g.ghost_name, direction = g.direction, position = g.position } end
  end
  storage.sig, storage.sig_n = ghost_sig(list)
  for _, g in pairs(ghosts) do if g.valid then g.destroy() end end
  for _, e in pairs(s.find_entities_filtered({ to_be_deconstructed = true, area = { { -200, -40 }, { 200, 40 } } })) do
    e.cancel_deconstruction(f)
  end
  remote.call("bpgen", "test_place", BP.absolute_answer, nil, BP.absolute_box)
end)
step("identity place absolute (compared)", function()
  local got = remote.call("bpgen", "test_placed")
  local list = {}
  for _, g in ipairs(got) do list[#list + 1] = { name = g.name, direction = g.direction, position = { x = g.x, y = g.y } } end
  local sig, n = ghost_sig(list)
  if sig ~= storage.sig then
    helpers.write_file("sig-abs-whole.txt", (storage.sig:gsub(";", "\n")))
    helpers.write_file("sig-abs-pieces.txt", (sig:gsub(";", "\n")))
  end
  out(string.format("identity place absolute: %d ghosts pasted whole, %d from pieces: %s", storage.sig_n, n,
    sig == storage.sig and "same" or "DIFFERENT"))
  remote.call("bpgen", "test_unplace")
end)
for _, r in ipairs({ 50, 200 }) do
  step("base read for a plan " .. (2 * r) .. "x" .. (2 * r), function()
    remote.call("bpgen", "test_request", "nauvis", { left_top = { x = -r, y = -r }, right_bottom = { x = r, y = r } })
  end, 32)
end
step("base read, no flows 400x400 (Plan base by my bus)", function()
  remote.call("bpgen", "test_request", "nauvis", { left_top = { x = -200, y = -200 }, right_bottom = { x = 200, y = 200 } },
    "player", false)
end)
step("snapshot tool 400x400 (snapshot.json)", function()
  remote.call("bpgen", "measure_area", "nauvis", { left_top = { x = -200, y = -200 }, right_bottom = { x = 200, y = 200 } })
end, 32)
step("blueprint of an area 200x200 (Move/Refit tools)", function()
  remote.call("bpgen", "test_area_print", { left_top = { x = -100.3, y = -100.6 }, right_bottom = { x = 100.4, y = 100.2 } })
end)
-- (not timed: the cells joined (in hitch_test.py) against the whole area blueprinted at once)
step("identity area blueprint", function(s, f)
  local inv = game.create_inventory(1)
  inv[1].set_stack({ name = "blueprint" })
  local map = inv[1].create_blueprint({ surface = s, force = f,
    area = { { -100.3, -100.6 }, { 100.4, 100.2 } }, include_modules = true, include_station_names = true })
  local off
  for _, be in pairs(inv[1].get_blueprint_entities() or {}) do
    local e = map[be.entity_number]
    if e then off = { e.position.x - be.position.x, e.position.y - be.position.y } break end
  end
  helpers.write_file("area-whole.json", helpers.table_to_json({ blueprint = inv[1].export_stack(), offset = off }))
  helpers.write_file("area-parts.json", helpers.table_to_json(remote.call("bpgen", "test_area_printed")))
  inv.destroy()
end)
step("answer parsed (json_to_table, huge)", function()
  local json = helpers.table_to_json({ blueprint = BP.huge, notes = { "a", "b" }, absolute = { box = { 0, 0, 262, 26 } } })
  storage.parsed = helpers.json_to_table(json)
end)
local function plan_of(k, more)
  local ans = helpers.json_to_table(BP[k .. "_answer"])
  local plan = { blueprint = ans.blueprint, pieces = ans.pieces }
  for x, v in pairs(more) do plan[x] = v end
  return plan
end
step("sandbox open (huge line, the base round it)", function()
  remote.call("bpgen", "sandbox_open", 1, "nauvis", "player", plan_of("huge", { center = { x = 0, y = 0 },
    area = { -131, -13, 131, 13 } }))
end)
step("sandbox move (huge line, 20 east)", function() remote.call("bpgen", "sandbox_move", 1, 20, 0, false) end)
step("sandbox turn (huge line)", function() remote.call("bpgen", "sandbox_move", 1, 0, 0, true) end)
step("sandbox paste back (huge line)", function() remote.call("bpgen", "sandbox_paste", 1) end)
step("sandbox open (absolute huge over the base)", function()
  local b = BP.absolute_box
  remote.call("bpgen", "sandbox_open", 1, "nauvis", "player", plan_of("absolute", { box = b,
    area = { b[1], b[2], b[1] + b[3], b[2] + b[4] } }))
end)
step("sandbox paste back (absolute huge)", function() remote.call("bpgen", "sandbox_paste", 1) end)
step("sandbox open (starter base, 200x200 round it)", function()
  remote.call("bpgen", "sandbox_open", 1, "nauvis", "player", plan_of("base", { center = { x = 0, y = 0 },
    area = { -68, -68, 68, 68 } }))
end)
step("sandbox paste back (starter base)", function() remote.call("bpgen", "sandbox_paste", 1) end)
-- (not timed: the reader's snapshot against 0.28's one-go read, over the base and over wild ground with water,
-- ore and trees, the area's edges off the tile grid)
local IDENT = {
  base = { left_top = { x = -60.3, y = -45.7 }, right_bottom = { x = 70.6, y = 52.2 } },
  wild = { left_top = { x = -760.3, y = -160.7 }, right_bottom = { x = -540.2, y = 61.9 } },
}
for name, area in pairs(IDENT) do
  step("identity " .. name, function(s, f)
    remote.call("bpgen", "snapshot_area", "nauvis", area, "player", "ident-new-" .. name .. ".json")
    helpers.write_file("ident-old-" .. name .. ".json", helpers.table_to_json(old_snapshot({ surface = s, force = f }, area)), false)
  end)
end

local i, started, setup_at = 0, nil, nil
script.on_event(defines.events.on_tick, function(e)
  local s, f = game.surfaces.nauvis, game.forces.player
  if e.tick == 2 then
    local p = helpers.create_profiler()
    local n = build_base(s, f)
    s.request_to_generate_chunks({ -650, -50 }, 5)
    s.force_generate_chunk_requests()
    p.stop()
    log({ "", "HITCHSETUP ", n, " entities ", p })
    remote.call("bpgen", "debug_tasks", true, BP.gc_kb)
    setup_at = e.tick
    return
  end
  if not setup_at or e.tick < setup_at + 120 then return end
  if started then
    if e.tick < started + steps[i].wait or remote.call("bpgen", "busy") then
      if e.tick % 30 == 0 then log("HEAPT " .. (e.tick - started) .. " " .. remote.call("bpgen", "heap")) end
      if e.tick < started + 6000 then return end
      log("HITCH-TIMEOUT " .. steps[i].name)
    end
    log("HITCH " .. steps[i].name .. " " .. started .. " " .. (e.tick - 1))
    log("HITCHHEAP " .. steps[i].name .. " " .. remote.call("bpgen", "heap"))
    started = nil
  end
  i = i + 1
  local st = steps[i]
  if not st then
    if i == #steps + 1 then out("done " .. (e.tick - 1)) end  -- (the last step's last tick: the log may lag)
    return
  end
  local p = helpers.create_profiler()
  st.fn(s, f)
  p.stop()
  log({ "", "HITCHCALL " .. st.name .. " | ", p })
  started = e.tick
end)
