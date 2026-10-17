-- The preview: the plan built for real on a hidden surface of its own force, shown through the window's camera.

local W = require("window_core")
local bench, PREVIEW, T = W.bench, W.PREVIEW, W.T

---------------------------------------------------------------------------------------------------------------------
-- the preview: built by the game on its own surface

local function preview_force()
  local f = game.forces[PREVIEW]
  -- (every time: a mod's migration resetting technology effects, or recipes added since, lock some again, and the
  -- preview's machines show them crossed out as not researched)
  if f then f.enable_all_recipes() return f end
  f = game.create_force(PREVIEW)
  f.enable_all_recipes()
  for name in pairs(prototypes.quality) do pcall(f.unlock_quality, name) end
  return f
end

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
-- is, not built again (not saved: after a load it's built again once)
local shown = {}

--- builds the blueprint on the preview surface; returns center, width, height (tiles) or nil, error. `reuse`: the
--- same plan already there (untouched since) is kept as it is
local function build_preview(player, bp, box, section, reuse)
  local key = bp .. "|" .. tostring(section) .. "|" .. (box and table.concat(box, ",") or "")
  local was = shown[player.index]
  shown[player.index] = nil
  local last_area = storage.bpgen_last_preview and storage.bpgen_last_preview[player.index]
  if reuse and was and was.key == key and last_area and game.surfaces[PREVIEW]
      and game.surfaces[PREVIEW].count_entities_filtered({ area = last_area, force = PREVIEW, limit = 1 }) > 0 then
    shown[player.index] = was
    return was.out[1], was.out[2], was.out[3], was.out[4]
  end
  local s = preview_surface()
  local f = preview_force()
  pcall(player.force.set_surface_hidden, s, true)
  local ox, oy = player.index * 4096, 0
  if box then  -- (an absolute blueprint, next to the base: it lands at its own world position)
    ox, oy = box[1] + box[3] / 2, box[2] + box[4] / 2
  end
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
  -- (the last preview goes first: where it was built; not known (older saves): around this player's spot, and
  -- wherever an absolute one landed)
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
  -- (what other mods put on the new chunks: ruins, trees, rocks)
  for _, e in pairs(s.find_entities_filtered({ area = area })) do
    if e.valid and e.type ~= "character" then e.destroy() end
  end
  if box then
    -- (next to the base: the base around it copied in, so the preview shows where it sits and what it taps)
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
  if box then  -- (what the paste replaces, the belts under its splitters and undergrounds: gone, as bots would do)
    for _, e in pairs(s.find_entities_filtered({ area = area, to_be_deconstructed = true })) do
      if e.valid then e.destroy() end
    end
  end
  for _, g in pairs(ghosts) do
    if g.valid and (g.type == "entity-ghost" or g.type == "tile-ghost") then
      -- (modules go straight in: the preview shows them, and a test run needs them)
      local plans = g.type == "entity-ghost" and g.insert_plan or nil
      local _, ent, proxy = g.silent_revive({ return_item_request_proxy = true,
        raise_revive = script.active_mods["power-propagation"] ~= nil })  -- (its hidden poles power the plan)
      if ent and plans then bench.insert_plans(ent, plans) end
      if proxy and proxy.valid then proxy.destroy() end
    end
  end
  local x1, y1, x2, y2
  if box then x1, y1, x2, y2 = box[1], box[2], box[1] + box[3], box[2] + box[4] end  -- (not the copied base too)
  for _, e in pairs(box and {} or s.find_entities_filtered({ area = area, force = f })) do
    local b = e.bounding_box
    x1 = math.min(x1 or b.left_top.x, b.left_top.x)
    y1 = math.min(y1 or b.left_top.y, b.left_top.y)
    x2 = math.max(x2 or b.right_bottom.x, b.right_bottom.x)
    y2 = math.max(y2 or b.right_bottom.y, b.right_bottom.y)
  end
  if not x1 then return nil, T("nothing-built") end
  player.force.chart(s, { { x1 - 4, y1 - 4 }, { x2 + 4, y2 + 4 } })
  -- (where a test run finds the plan's sources and sinks: as the harness, the built area's top-left)
  local where = { origin = { x = math.floor(x1 + 0.5), y = math.floor(y1 + 0.5) }, area = { { x1 - 8, y1 - 8 }, { x2 + 8, y2 + 8 } } }
  local center = { x = (x1 + x2) / 2, y = (y1 + y2) / 2 }
  shown[player.index] = { key = key, out = { center, x2 - x1, y2 - y1, where } }
  return center, x2 - x1, y2 - y1, where
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

function W.preview(player, bp)
  local center, w, h, where = build_preview(player, bp)
  local count = {}
  if center then
    for _, e in pairs(game.surfaces[PREVIEW].find_entities_filtered({ force = PREVIEW })) do
      count[e.name] = (count[e.name] or 0) + 1
    end
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

-- (what the other parts call)
W.build_preview = build_preview
W.preview_surface = preview_surface
W.shown = shown
