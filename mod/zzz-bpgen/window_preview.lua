-- The preview: the plan built for real on a hidden surface of its own force, shown through the window's camera.
-- Built over a few ticks (no tick holds the game): the old preview taken away and the ground made ready a budget
-- at a time, the base round an absolute plan copied in cell by cell, then the plan's pieces built and revived
-- (build.lua). Meanwhile the window says it's drawing; when it's done, W.preview_built shows it.

local W = require("window_core")
local tasks = require("tasks")
local build = require("build")
local bench, PREVIEW, T = W.bench, W.PREVIEW, W.T

-- units of work (tasks.lua: ~4 us each)
local U_DESTROY, U_CLONE, U_CHUNK = 2, 3, 2
local CLONE_CELL = 16

---------------------------------------------------------------------------------------------------------------------
-- the preview: built by the game on its own surface

local function preview_force()
  local f = game.forces[PREVIEW]
  -- (a mod's migration resetting technology effects, or recipes added since, lock some again, and the preview's
  -- machines show them crossed out as not researched: all enabled again then; doing it every time took 5-10 ms)
  if f and not storage.bpgen_preview_stale then return f end
  f = f or game.create_force(PREVIEW)
  if storage.bpgen_preview_stale == nil then
    for name in pairs(prototypes.quality) do pcall(f.unlock_quality, name) end
  end
  f.enable_all_recipes()
  storage.bpgen_preview_stale = false
  return f
end
--- (control.lua: the mods or a force's technology effects changed)
function W.preview_stale() storage.bpgen_preview_stale = true end

local function preview_surface()
  local s = game.surfaces[PREVIEW]
  if s then
    if not s.generate_with_lab_tiles then s.generate_with_lab_tiles = true end  -- (saves before it)
    return s
  end
  s = game.create_surface(PREVIEW, {
    default_enable_all_autoplace_controls = false,
    autoplace_settings = {
      entity = { treat_missing_as_default = false, settings = {} },
      decorative = { treat_missing_as_default = false, settings = {} },
      tile = { treat_missing_as_default = false, settings = {} },
    },
    no_enemies_mode = true,
    peaceful_mode = true,
  })
  s.always_day = true
  s.generate_with_lab_tiles = true  -- (new chunks come as lab ground: bench.ground has little left to set)
  pcall(function() s.show_clouds = false end)
  return s
end

-- player index -> the plan on the preview now: {key, out = {center, w, h, where}}: a reopened window shows it as it
-- is, not built again (in storage: every peer agrees whether it's built again)
local function shown()
  storage.bpgen_shown = storage.bpgen_shown or {}
  return storage.bpgen_shown
end

local function key_of(bp, box, section)
  return bp .. "|" .. tostring(section) .. "|" .. (box and table.concat(box, ",") or "")
end

--- the built preview's center, size and where a test run finds it, from what was built
local function finish(player, s, box, built_box, key)
  local x1, y1, x2, y2
  if box then x1, y1, x2, y2 = box[1], box[2], box[1] + box[3], box[2] + box[4]  -- (not the copied base too)
  elseif built_box then x1, y1, x2, y2 = built_box[1], built_box[2], built_box[3], built_box[4] end
  if not x1 then return nil, T("nothing-built") end
  player.force.chart(s, { { x1 - 4, y1 - 4 }, { x2 + 4, y2 + 4 } })
  -- (where a test run finds the plan's sources and sinks: as the harness, the built area's top-left)
  local where = { origin = { x = math.floor(x1 + 0.5), y = math.floor(y1 + 0.5) }, area = { { x1 - 8, y1 - 8 }, { x2 + 8, y2 + 8 } } }
  local center = { x = (x1 + x2) / 2, y = (y1 + y2) / 2 }
  shown()[player.index] = { key = key, out = { center, x2 - x1, y2 - y1, where } }
  return center, x2 - x1, y2 - y1, where
end

-- the preview made over ticks ---------------------------------------------------------------------------------

local function player_of(t)
  return t.fake or game.get_player(t.player)
end

--- one tick's work of a preview being made: {player, key, box, area, ox, oy, print, phase, ...}
local function prepare(t, budget)
  local s = game.surfaces[PREVIEW]
  local player = player_of(t)
  if not (s and player) then return 1, true end
  local used = 0
  if t.phase == "old" then  -- (the last preview, where it was built; older saves: around this player's spot)
    t.doomed = t.doomed or {}
    for _, area in ipairs(t.old) do
      for _, e in pairs(s.find_entities_filtered({ area = area })) do
        if e.valid and e.type ~= "character" then t.doomed[#t.doomed + 1] = e end
      end
    end
    used, t.phase, t.di = used + 20, "destroy", 1
  end
  if t.phase == "destroy" then
    local d = t.doomed
    while t.di <= #d and used < budget do
      if d[t.di].valid then d[t.di].destroy() end
      t.di, used = t.di + 1, used + U_DESTROY
    end
    if t.di > #d then t.doomed, t.phase = nil, t.after_destroy or "chunks" end
  end
  if t.phase == "chunks" then  -- (the ground under it: chunks the game makes in the background, waited for)
    local a = t.area
    if not t.chunks then
      t.chunks = {}
      for cx = math.floor(a[1][1] / 32), math.floor(a[2][1] / 32) do
        for cy = math.floor(a[1][2] / 32), math.floor(a[2][2] / 32) do
          if not s.is_chunk_generated({ cx, cy }) then
            s.request_to_generate_chunks({ cx * 32 + 16, cy * 32 + 16 }, 0)
            t.chunks[#t.chunks + 1] = { cx, cy }
          end
        end
      end
      used = used + #t.chunks * U_CHUNK
    end
    for i = #t.chunks, 1, -1 do
      if s.is_chunk_generated(t.chunks[i]) then table.remove(t.chunks, i) end
    end
    if t.chunks[1] then return used + 1, false end
    t.chunks = nil
    bench.ground(s, a[1][1], a[1][2], a[2][1], a[2][2])  -- (what isn't lab ground yet: nothing on a new chunk)
    -- (what other mods put on the new chunks: ruins, trees, rocks)
    t.doomed, t.di, t.phase, t.after_destroy = {}, 1, "destroy", t.box and "clone" or "build"
    for _, e in pairs(s.find_entities_filtered({ area = a })) do
      if e.valid and e.type ~= "character" then t.doomed[#t.doomed + 1] = e end
    end
    return used + 20, false
  end
  if t.phase == "clone" then  -- (next to the base: the base around it copied in, a cell at a time)
    local a = t.area
    local real = game.get_surface(t.surface)
    local f = game.forces[PREVIEW]
    local nx = math.ceil((a[2][1] - a[1][1]) / CLONE_CELL)
    local ny = math.ceil((a[2][2] - a[1][2]) / CLONE_CELL)
    t.ci = t.ci or 0
    while real and t.ci < nx * ny and used < budget do
      local cx, cy = t.ci % nx, math.floor(t.ci / nx)
      local x1, y1 = a[1][1] + cx * CLONE_CELL, a[1][2] + cy * CLONE_CELL
      local cell = { { x1, y1 }, { math.min(x1 + CLONE_CELL, a[2][1]), math.min(y1 + CLONE_CELL, a[2][2]) } }
      local n = real.count_entities_filtered({ area = cell })
      real.clone_area({ source_area = cell, destination_area = cell, destination_surface = s,
        destination_force = f, clone_tiles = true, clone_entities = true, clone_decoratives = false,
        clear_destination_entities = true, expand_map = true, create_build_effect_smoke = false })
      t.ci, used = t.ci + 1, used + 5 + n * U_CLONE
    end
    if real and t.ci < nx * ny then return used, false end
    t.doomed, t.di, t.phase, t.after_destroy = {}, 1, "destroy", "build"
    for _, e in pairs(s.find_entities_filtered({ area = a, type = { "character", "unit", "unit-spawner", "turret" } })) do
      if e.valid and not e.player then t.doomed[#t.doomed + 1] = e end
    end
    return used + 20, false
  end
  if t.phase == "build" then
    local p = t.print
    build.start({ surface = s, force = PREVIEW, print = p, revive = true, superforced = t.box ~= nil, on_revived = "preview",
      origin = build.origin(p, { x = t.ox, y = t.oy }, t.box), then_kind = "preview", player = t.player,
      fake = t.fake, key = t.key, box = t.box, area = t.area })
    return used + 5, true
  end
  return used, false
end
tasks.kind("preview", prepare)

-- (a machine's recipe the preview's force has locked again (a mod's migration resetting technology effects):
-- enabled, or the preview shows it crossed out as not researched)
build.on_revived.preview = function(ent)
  local t = ent.type
  if t ~= "assembling-machine" and t ~= "furnace" then return end
  local r = ent.get_recipe()
  if r and not r.enabled then r.enabled = true end
end

build.on_done.preview = function(t)
  local s = game.surfaces[PREVIEW]
  local player = player_of(t)
  if not (s and player) then return end
  if t.box then  -- (what the paste replaces, the belts under its splitters and undergrounds: gone, as bots would do)
    for _, e in ipairs(t.marks) do if e.valid then e.destroy() end end
  end
  finish(player, s, t.box, t.extent, t.key)
  if not t.fake and W.preview_built then W.preview_built(player, t.key) end
end

--- builds the plan's blueprint on the preview surface (its pieces, a part a tick) -> center, width, height (tiles),
--- where; or nil, "pending" (being built: W.preview_built follows), or nil, error. `reuse`: the same plan already
--- there (untouched since) is kept as it is. `pieces`: the answer's (bpgen/pieces.py); without them (an old answer, a
--- tiny print) the whole print is built at once.
local function build_preview(player, bp, box, section, reuse, pieces)
  local key = key_of(bp, box, section)
  local sh = shown()
  local was = sh[player.index]
  sh[player.index] = nil
  local last_area = storage.bpgen_last_preview and storage.bpgen_last_preview[player.index]
  if reuse and was and was.key == key and last_area and game.surfaces[PREVIEW]
      and game.surfaces[PREVIEW].count_entities_filtered({ area = last_area, force = PREVIEW, limit = 1 }) > 0 then
    sh[player.index] = was
    return was.out[1], was.out[2], was.out[3], was.out[4]
  end
  for _, t in ipairs(storage.bpgen_tasks or {}) do  -- (a preview still being made for this player: superseded)
    if (t.kind == "preview" or t.kind == "build" and t.then_kind == "preview") and t.player == player.index
        and t.key == key then
      return nil, "pending"
    end
  end
  W.cancel_preview(player.index)
  local s = preview_surface()
  preview_force()
  pcall(player.force.set_surface_hidden, s, true)
  local print = build.pick(pieces, section)
  if not print then return W.build_preview_now(player, bp, box, section, key) end
  local ox, oy = player.index * 4096, 0
  local half
  if box then  -- (an absolute blueprint, next to the base: it lands at its own world position)
    ox, oy = box[1] + box[3] / 2, box[2] + box[4] / 2
    half = math.max(box[3], box[4]) / 2 + 12
  else
    local f = print.frame
    half = math.max(f[3] - f[1], f[4] - f[2]) / 2 + 12
  end
  storage.bpgen_last_preview = storage.bpgen_last_preview or {}
  local last = storage.bpgen_last_preview[player.index]
  local old = last and { last } or { { { ox - 1500, oy - 1500 }, { ox + 1500, oy + 1500 } },
                                     { { player.index * 4096 - 1500, -1500 }, { player.index * 4096 + 1500, 1500 } } }
  local area = { { ox - half, oy - half }, { ox + half, oy + half } }
  storage.bpgen_last_preview[player.index] = area
  tasks.start("preview", { player = player.index, fake = not player.object_name and player or nil, key = key,
    box = box, area = area, ox = ox, oy = oy, print = print, old = old, phase = "old",
    surface = player.surface and player.surface.name })
  return nil, "pending"
end

--- (tests, a stand-in player) whether a preview is still being made for this player
function W.preview_pending(player_index)
  for _, t in ipairs(storage.bpgen_tasks or {}) do
    if (t.kind == "preview" or t.kind == "build" and t.then_kind == "preview") and t.player == player_index then
      return true
    end
  end
  return false
end

--- a preview being made for this player dropped
function W.cancel_preview(player_index)
  local l = storage.bpgen_tasks or {}
  for i = #l, 1, -1 do
    local t = l[i]
    if (t.kind == "preview" or t.kind == "build" and t.then_kind == "preview") and t.player == player_index then
      table.remove(l, i)
    end
  end
end

--- the whole print built at once (no pieces: a tiny print, an answer from before 0.29) -> as build_preview
function W.build_preview_now(player, bp, box, section, key)
  local s = preview_surface()
  local f = preview_force()
  local ox, oy = player.index * 4096, 0
  if box then ox, oy = box[1] + box[3] / 2, box[2] + box[4] / 2 end
  local inv = game.create_inventory(1)
  local stack = inv[1]
  stack.set_stack({ name = "blueprint" })
  if stack.import_stack(bp) == 1 then
    inv.destroy()
    return nil, T("bp-not-imported")
  end
  if stack.is_blueprint_book then  -- (a starter base: the print picked, else the first: the connected base)
    local inner = stack.get_inventory(defines.inventory.item_main)
    local pick
    for i = 1, inner and #inner or 0 do
      local it = inner[i]
      if it.valid_for_read then
        if section and it.label == section then pick = it break end
        pick = pick or it
      end
    end
    if pick and pick.is_blueprint_book then  -- (a section that is a book itself: its first print)
      local deeper = pick.get_inventory(defines.inventory.item_main)
      pick = deeper and deeper[1]
    end
    stack = pick
  end
  if not (stack and stack.valid_for_read and stack.is_blueprint and stack.is_blueprint_setup()) then
    inv.destroy()
    return nil, T("nothing-to-show")
  end
  local w, h = 0, 0
  for _, e in pairs(stack.get_blueprint_entities() or {}) do
    w = math.max(w, math.abs(e.position.x))
    h = math.max(h, math.abs(e.position.y))
  end
  local half = math.max(w, h) + 12
  if box then half = math.max(box[3], box[4]) / 2 + 12 end
  storage.bpgen_last_preview = storage.bpgen_last_preview or {}
  local last = storage.bpgen_last_preview[player.index]
  local old = last and { last } or { { { ox - 1500, oy - 1500 }, { ox + 1500, oy + 1500 } },
                                     { { player.index * 4096 - 1500, -1500 }, { player.index * 4096 + 1500, 1500 } } }
  for _, area in ipairs(old) do
    for _, e in pairs(s.find_entities_filtered({ area = area })) do
      if e.valid and e.type ~= "character" then e.destroy() end
    end
  end
  storage.bpgen_last_preview[player.index] = { { ox - half, oy - half }, { ox + half, oy + half } }
  bench.ground(s, ox - half, oy - half, ox + half, oy + half)
  local area = { { ox - half, oy - half }, { ox + half, oy + half } }
  for _, e in pairs(s.find_entities_filtered({ area = area })) do
    if e.valid and e.type ~= "character" then e.destroy() end
  end
  if box then
    player.surface.clone_area({ source_area = area, destination_area = area, destination_surface = s,
      destination_force = f, clone_tiles = true, clone_entities = true, clone_decoratives = false,
      clear_destination_entities = true, expand_map = true, create_build_effect_smoke = false })
    for _, e in pairs(s.find_entities_filtered({ area = area, type = { "character", "unit", "unit-spawner", "turret" } })) do
      if e.valid and not e.player then e.destroy() end
    end
  end
  local ghosts = stack.build_blueprint({ surface = s, force = f, position = { ox, oy },
    build_mode = box and defines.build_mode.superforced or defines.build_mode.forced, skip_fog_of_war = false })
  inv.destroy()
  if box then
    for _, e in pairs(s.find_entities_filtered({ area = area, to_be_deconstructed = true })) do
      if e.valid then e.destroy() end
    end
  end
  local bb
  for _, g in pairs(ghosts) do
    if g.valid and (g.type == "entity-ghost" or g.type == "tile-ghost") then
      local plans = g.type == "entity-ghost" and g.insert_plan or nil
      local _, ent, proxy = g.silent_revive({ return_item_request_proxy = true,
        raise_revive = script.active_mods["power-propagation"] ~= nil })  -- (its hidden poles power the plan)
      if ent then
        if plans then bench.insert_plans(ent, plans) end
        build.on_revived.preview(ent)
        local b = ent.bounding_box
        bb = bb or { b.left_top.x, b.left_top.y, b.right_bottom.x, b.right_bottom.y }
        bb = { math.min(bb[1], b.left_top.x), math.min(bb[2], b.left_top.y), math.max(bb[3], b.right_bottom.x),
          math.max(bb[4], b.right_bottom.y) }
      end
      if proxy and proxy.valid then proxy.destroy() end
    end
  end
  return finish(player, s, box, bb, key)
end

-- (for headless tests: a stand-in player { index, force })
function W.bench_test(player_index, spec, where)
  return bench.test(player_index, spec, where.origin, where.area, PREVIEW).id
end
function W.bench_calibrate(player_index, spec)
  preview_surface()
  return bench.calibrate(player_index, spec).id
end
function W.bench_last(kind)
  local all = storage.bpgen_bench
  return all and all.last and all.last[kind]
end

--- (tests) the preview of a blueprint string (built at once), or of an answer (its pieces, over ticks: then
--- {pending = true}; W.preview_result tells what was built once it's done)
function W.preview(player, bp, answer)
  local center, w, h, where = build_preview(player, bp, nil, nil, nil, answer and answer.pieces)
  if w == "pending" then return { pending = true } end
  return W.preview_result(player, center, w, h, where)
end
function W.preview_result(player, center, w, h, where)
  if not center then
    local was = shown()[player.index]
    if not was then return { pending = W.preview_pending(player.index) } end
    center, w, h, where = was.out[1], was.out[2], was.out[3], was.out[4]
  end
  local count = {}
  for _, e in pairs(game.surfaces[PREVIEW].find_entities_filtered({ force = PREVIEW, area = where.area })) do
    count[e.name] = (count[e.name] or 0) + 1
  end
  return { center = center, w = w, h = h, built = count, where = where }
end

function W.measuring(player)
  return bench.find("calibrate", player.index) ~= nil
end

--- the inserter setups a plan asked to measure, measured (bench.on_done then hands them to ctx.measured)
function W.calibrate(player, measure)
  preview_surface()
  bench.calibrate(player.index, measure)
end

--- another print of the book in the preview (the window's section picker); `reuse`: built already
function W.show_section(player, section, reuse)
  local r = W.results[player.index]
  local frame = player.gui.screen[W.NAME]
  if not (r and frame) then return end
  local center, w, h, where = build_preview(player, r.blueprint, nil, section, reuse, r.res and r.res.pieces)
  if center then
    local cam = W.find(frame, "bpgen_cam")
    r.center, r.zoom, r.where, r.w, r.h = center, W.fit(player, w, h), where, w, h
    cam.position, cam.zoom, cam.visible = center, r.zoom, true
    W.find(frame, "bpgen_empty").visible = false
  elseif w == "pending" then
    r.redo = { "section", section }
    W.find(frame, "bpgen_cam").visible = false
    local empty = W.find(frame, "bpgen_empty")
    empty.visible, empty.caption = true, T("drawing-preview")
  end
end

-- (what the other parts call)
W.build_preview = build_preview
W.preview_surface = preview_surface
W.shown = shown
