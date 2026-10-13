-- The bpgen window (with the fse loader): pick a recipe with the game's own recipe picker, plan it in game
-- (bpgen.ingame on a worker thread), and see the blueprint as the game draws it: it is built for real on a hidden
-- preview surface ("bpgen-preview", its own force, lab tiles) and shown through a camera. Then put it in your hand
-- (the game's own placement preview) or copy its string.
--
-- Opened by Ctrl+Shift+B over a machine (filled in from it, planned at once), the bpgen shortcut, Ctrl+Alt+B, or the
-- fse hub. Results live in this Lua state only (not saved); the preview surface is rebuilt on demand.

local M = {}
local NAME = "bpgen_window"
local PREVIEW = "bpgen-preview"
-- with the fse-std library mod (optional): its window, resized by its corner grip like every fse window
local stdwin = script.active_mods["fse-std"] and require("__fse-std__/window") or nil
local stdinput = stdwin and require("__fse-std__/input") or nil
local dnd = stdwin and require("__fse-std__/dnd") or nil  -- (History's rows dragged onto the preview)
local ui = require("ui")
local web = require("web")  -- (the window as a web page over the game: fse's shells)

--- the window's words: a line of [bpgen-window] in locale/en/window.cfg, with its parameters
local function T(key, ...) return { "bpgen-window." .. key, ... } end

--- localised strings (or plain ones) joined, `sep` between them: nested, as a LocalisedString takes 20 parameters
local function join(list, sep)
  local flat = {}
  for i, x in ipairs(list) do
    if i > 1 and sep then flat[#flat + 1] = sep end
    flat[#flat + 1] = x
  end
  while #flat > 20 do
    local up = {}
    for i = 1, #flat, 20 do up[#up + 1] = { "", table.unpack(flat, i, math.min(i + 19, #flat)) } end
    flat = up
  end
  return { "", table.unpack(flat) }
end

local OLD_FRAME_H = 366  -- (before 0.21: the window's height besides the preview, its rows under it included)
-- (with fse-std's layouts, what sticks out of the window: the rails left and right, the dock below; GUI units)
local OUT_SIDE, OUT_BELOW = 106, 32
local DOCK_W, DOCK_H = 720, 44                                  -- (the blueprint's actions, below the window)
local MAP_H = 390                    -- (the minimap and research box's height, the game's GUI units)
local TEST_W, TEST_H = 300, 330      -- (the test run's drawer on the right edge, under the preview's tools)
local LEFT_W = 344                   -- (the library window's width besides the preview: the left panel, borders)
-- (the game's own GUI the window keeps clear of, in the game's GUI units (times display_scale): the hotbar band at
-- the bottom (quick bar, shortcut bar, the character's guns), the minimap and research box in the top right corner)
local HOTBAR_H, MAP_W = 100, 262
-- (the preview's smallest; on a short screen, this tall: the window's height is the left panel's too, which needs
-- room for its settings, Plan and the result)
local CAM_MIN_W, CAM_MIN_H, CAM_TIGHT_H = 420, 420, 340
local find  -- (below)
local docked  -- (below: does this player's window have its tabs and toolbars outside it)
--- the window's height besides the preview (GUI units at 100 %): its title and the mode's line; inside the window
--- (not docked) also the mode tabs and the rows of tools and actions under the preview
local function frame_h(player) return docked(player) and 100 or 234 end
-- the preview's size: the player's own (resized), else a share of the screen; three presets for the size button.
-- Kept as the player asked for it (storage.bpgen_cam: w, h); shown at what fits now (sw, sh), so a bigger window
-- scale or a smaller screen shrinks the preview, and it grows back when there's room again
local PRESETS = { { 0.45, 0.68 }, { 0.6, 0.8 }, { 0.75, 0.86 } }
local function wanted_size(player)
  storage.bpgen_cam = storage.bpgen_cam or {}
  local s = storage.bpgen_cam[player.index]
  if s and not s.v then  -- (kept before 0.21: the preview takes the window's whole height now, as tall a window)
    s.h, s.v, s.sw, s.sh = s.h + OLD_FRAME_H - frame_h(player), 2, nil, nil
  end
  if s then return s.w, s.h end
  local res, scale = player.display_resolution, player.display_scale
  return math.max(520, math.floor(res.width / scale * PRESETS[1][1])), math.max(380, math.floor(res.height / scale * PRESETS[1][2]))
end
--- the preview's size as shown (GUI units at 100 %)
local function cam_size(player)
  local s = storage.bpgen_cam and storage.bpgen_cam[player.index]
  if s and s.sw then return s.sw, s.sh end
  return wanted_size(player)
end

--- the scale the window wants: its Size, else the player's Window size (fse-std 0.8+), else 1
local function want_scale(player)
  if not (stdwin and stdwin.scale) then return 1 end
  local _, want = stdwin.scale(player, NAME)
  return want or 1
end


--- the most the result's scroll area under Plan takes of a window `height` tall (GUI units at 100 %)
--- (a short window: less of it, the settings above keep room to show a section)
local function info_h(height)
  return math.max(110, math.min(300, math.floor(height * (height < 700 and 0.24 or 0.4))))
end

--- the room the window has at the scale it wants: the screen less the hotbar band, and less the minimap's column
--- when the preview still gets its smallest width without it. -> max_w, max_h (the preview's), min_h, the space
--- {sc = pixels a GUI unit, side, below, right = the free area's right edge in pixels, bottom = its bottom}
local function room(player)
  local res, ds = player.display_resolution, player.display_scale
  local out = docked(player)
  local side, below = out and OUT_SIDE or 0, out and OUT_BELOW or 0
  local right, bottom = res.width, res.height - HOTBAR_H * ds
  -- (the scale it wants; when even its smallest size doesn't fit the free area at that, the largest step at which it
  -- does: the library caps it there (the dock reserves the hotbar band below it), so the preview fills that room)
  local f = want_scale(player)
  local S = ui.theme and ui.theme.scale
  if S and S.snap then
    local g = math.min((right - 16) / ((CAM_MIN_W + LEFT_W + 2 * side) * ds),
      (bottom - 16) / ((CAM_TIGHT_H + frame_h(player) + below) * ds))
    if g < f then f = math.max(S.snap(g), 0.5) end
  end
  local sc = ds * f
  local max_w = right / sc - LEFT_W - 2 * side - 16
  local max_h = bottom / sc - frame_h(player) - below - 16
  if max_w - MAP_W * ds / sc >= CAM_MIN_W then  -- (the rail on the right clear of the minimap)
    right = res.width - MAP_W * ds
    max_w = max_w - MAP_W * ds / sc
  end
  local min_h = max_h < CAM_MIN_H and CAM_TIGHT_H or CAM_MIN_H
  return max_w, max_h, min_h, { sc = sc, f = f, side = side, below = below, right = right, bottom = bottom }
end

--- the dock (docked: a toolbar below the window) also reserves the hotbar band under it: what the library counts as
--- sticking out below the window (keeping it on the screen, capping its scale) keeps the window above the hotbar
local function reserve_below(player, f)
  local dock = player.gui.screen[NAME .. "__bottom"]
  local tags = dock and dock.tags
  local a = tags and tags.fstd_attach
  if not a then return end
  local h = DOCK_H + math.ceil(HOTBAR_H / f)
  if a.h ~= h then
    a.h = h
    dock.tags = tags
  end
end

--- the preview (and what's sized by it) at w x h; kept for this player as what they asked for unless `keep` (a
--- refit: the scale or the screen changed, what they asked for stays)
local function apply_size(player, frame, w, h, keep)
  -- (never bigger than the screen leaves room for at the window's scale: the left panel and the rows under the
  -- preview, the rails and the dock, clear of the hotbar and the minimap)
  local max_w, max_h, min_h, sp = room(player)
  reserve_below(player, sp.f)
  storage.bpgen_cam = storage.bpgen_cam or {}
  if not keep then storage.bpgen_cam[player.index] = { w = math.floor(w), h = math.floor(h), v = 2 } end
  w, h = math.max(CAM_MIN_W, math.floor(math.min(w, max_w))), math.max(min_h, math.floor(math.min(h, max_h)))
  local kept = storage.bpgen_cam[player.index]
  if not kept then  -- (no size of the player's own yet: the default share of the screen stays what's asked for)
    local dw, dh = wanted_size(player)
    kept = { w = dw, h = dh, v = 2 }
  end
  kept.sw, kept.sh = w, h
  storage.bpgen_cam[player.index] = kept
  -- (the library's window: that size, the frames stuck to it following)
  local tw = stdwin and frame.tags.fstd_tweak
  if tw and (tw.width ~= w + LEFT_W or tw.height ~= h + frame_h(player)) then
    local W, H = w + LEFT_W, h + frame_h(player)
    local saved = storage.fstd_windows and storage.fstd_windows[player.index] and storage.fstd_windows[player.index][NAME]
    if saved then saved.width, saved.height = W, H end
    if ui.theme and ui.theme.style then
      ui.theme.style(frame, { width = W, height = H })  -- (drawn at its scale, kept as its tweak)
    else
      frame.style.width, frame.style.height = W, H
      local tags = frame.tags
      tags.fstd_tweak = { width = W, height = H }
      frame.tags = tags
    end
    if ui.theme and ui.theme.follow then ui.theme.follow(frame) end
  end
  for _, n in ipairs({ "bpgen_cam", "bpgen_empty" }) do
    local el = find(frame, n)
    -- (in the library's window the preview stretches into what the window leaves it, both ways)
    if el and not stdwin then el.style.width, el.style.height = w, h end
    if el and stdwin then ui.style(el, { minimal_width = CAM_MIN_W, minimal_height = min_h }) end
  end
  -- (the result under Plan: up to a share of the window's height, the settings above it keep the rest)
  local info = find(frame, "bpgen_info")
  if info then ui.style(info, { maximal_height = info_h(h + frame_h(player)) }) end
  -- (and inside the free area if it grew past its edge: the rails and the dock too; pixels at the window's scale)
  local loc = frame.location
  if loc then
    local sc = player.display_scale * ui.scale_of(frame)
    local x = math.max(sp.side * sc, math.min(loc.x, sp.right - (w + LEFT_W + sp.side) * sc))
    local y = math.max(0, math.min(loc.y, sp.bottom - (h + frame_h(player) + sp.below) * sc))
    x, y = math.floor(x), math.floor(y)
    if x ~= loc.x or y ~= loc.y then
      frame.location = { x = x, y = y }
      local saved = storage.fstd_windows and storage.fstd_windows[player.index] and storage.fstd_windows[player.index][NAME]
      if saved and saved.location then saved.location = frame.location end
      if ui.theme and ui.theme.follow then ui.theme.follow(frame) end
    end
  end
end

--- where a new window of a w x h preview goes: centred in the free area (above the hotbar, left of the minimap when
--- that leaves room for it), at the scale it wants
local function place_new(player, w, h)
  local _, _, _, sp = room(player)
  local sc = sp.sc
  local W, H = (w + LEFT_W + 2 * sp.side) * sc, (h + frame_h(player) + sp.below) * sc
  return { x = math.floor(math.max(0, (sp.right - W) / 2) + sp.side * sc), y = math.floor(math.max(0, (sp.bottom - H) / 2)) }
end

local bench = require("bench")
local safe = require("safe")
local place = require("place")
local sandbox = require("sandbox")
local ctx          -- from control.lua: craftable, unlocked, bonuses, start, measured
-- (per-player state in storage: in multiplayer every peer must show and place the same; see safe.stored)
local results = safe.stored("results") -- player index -> { blueprint, res, center, zoom }
local placed = safe.stored("placed")     -- player index -> the ghosts "Place near me" and "Place by my base" made
local unmark = safe.stored("unmark")     -- player index -> what those pastes marked for deconstruction (Undo unmarks it)
local turns = safe.stored("turns")       -- player index -> quarter turns Rotate added to the plan next to the base
local tried = safe.stored("tried")       -- player index -> the boxes Redo looked past ({x, y, w, h} each)
local to_sandbox = safe.stored("to_sandbox")  -- player index -> true: "Try in sandbox" waits for Place by my base
-- (the preview's drag, zoom and size grip: momentary, not saved)
local cam_hover, drags, wheel_seen, left_during = {}, {}, {}, {}
local grip_hover, resizing, resized_at = {}, {}, {}
-- player index -> what the force can build: read once until the force's research changes (M.forget_unlocked) or the
-- player changes force; 19,000 recipes read on every open took 15 ms in a big pack (in storage: the same on every peer)
local unlocks = safe.stored("unlocks")

function M.setup(c) ctx = c end

local force_pass  -- (below)
--- the items some enabled recipe of the force makes (control.lua's craftable), with which recipes it has enabled
local function craftable(force) return force_pass(force).items end

local function find_in(el, name)
  if el.name == name then return el end
  for _, c in pairs(el.children) do
    local f = find_in(c, name)
    if f then return f end
  end
end

--- an element by name in el; in the window, also in the frames stuck to it (its rail, dock, toolbars, drawer)
function find(el, name)
  if not (el and el.valid) then return nil end
  local f = find_in(el, name)
  if f or el.name ~= NAME or el.parent ~= game.get_player(el.player_index).gui.screen then return f end
  local player = game.get_player(el.player_index)
  for _, c in pairs(ui.attached(player, NAME)) do
    f = find_in(c, name)
    if f then return f end
  end
  return web.find(player, name)  -- (the web window: the preview's camera in its island)
end

local function speed(proto, quality)
  local ok, s = pcall(proto.get_crafting_speed, quality)
  return ok and s or proto.crafting_speed or 1
end

local function unlocked(player)
  local u = unlocks[player.index]
  if not u or u.force ~= player.force.index then
    local made = craftable(player.force)
    u = { machines = ctx.unlocked(made, { "assembling-machine", "furnace" }), belts = ctx.unlocked(made, { "transport-belt" }),
          inserters = ctx.unlocked(made, { "inserter" }), poles = ctx.unlocked(made, { "electric-pole" }),
          force = player.force.index }
    unlocks[player.index] = u
  end
  return u
end

-- the fastest unlocked machine that can make the recipe
-- a free copy that runs without power (bioluminescent / harene-infused machines and the like): never an automatic pick
local function powerless(p)
  local ok, v = pcall(function() return p.void_energy_source_prototype end)
  return ok and v ~= nil
end

local function best_machine(player, recipe)
  local cat = prototypes.recipe[recipe] and prototypes.recipe[recipe].category
  local best, best_speed
  for _, name in ipairs(unlocked(player).machines) do
    local p = prototypes.entity[name]
    if p.crafting_categories and p.crafting_categories[cat] and not powerless(p) then
      local s = speed(p)
      if not best or s > best_speed then best, best_speed = name, s end
    end
  end
  return best
end
M.best_machine = best_machine

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
  if s then return s end
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
  pcall(function() s.show_clouds = false end)
  return s
end


--- builds the blueprint on the preview surface; returns center, width, height (tiles) or nil, error
local function build_preview(player, bp, box, section)
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
  -- (the last preview goes first: around this player's spot, and wherever an absolute one landed)
  storage.bpgen_last_preview = storage.bpgen_last_preview or {}
  local last = storage.bpgen_last_preview[player.index]
  for _, area in ipairs({ { { ox - 1500, oy - 1500 }, { ox + 1500, oy + 1500 } },
                          { { player.index * 4096 - 1500, -1500 }, { player.index * 4096 + 1500, 1500 } }, last }) do
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
      local _, ent, proxy = g.silent_revive({ return_item_request_proxy = true })
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
  return { x = (x1 + x2) / 2, y = (y1 + y2) / 2 }, x2 - x1, y2 - y1, where
end

-- (for headless tests: a stand-in player { index, force })
function M.bench_test(player_index, spec, where)
  return bench.test(player_index, spec, where.origin, where.area, PREVIEW).id
end
function M.bench_calibrate(player_index, spec)
  preview_surface()
  return bench.calibrate(player_index, spec).id
end
function M.bench_last(kind)
  local all = storage.bpgen_bench
  return all and all.last and all.last[kind]
end

function M.preview(player, bp)
  local center, w, h, where = build_preview(player, bp)
  local count = {}
  if center then
    for _, e in pairs(game.surfaces[PREVIEW].find_entities_filtered({ force = PREVIEW })) do
      count[e.name] = (count[e.name] or 0) + 1
    end
  end
  return { center = center, w = w, h = h, built = count, where = where }
end

--- the preview's size in pixels: the web window's island, else its size in GUI units at 100 % times the window's
--- scale and the game's
local function view_px(player)
  local ww, wh = web.view(player)
  if ww then return ww, wh end
  local scale = player.display_scale * ui.scale_of(player.gui.screen[NAME])
  local cw, ch = cam_size(player)
  return cw * scale, ch * scale
end

local function fit(player, w, h)
  local pw, ph = view_px(player)
  local z = math.min(pw / ((w + 2) * 32), ph / ((h + 2) * 32))
  return math.max(0.05, math.min(2, z))
end

---------------------------------------------------------------------------------------------------------------------
-- the window

-- (in groups: making things, a base, reworking a base, space; short names (mode-<mode> in the locale), the full one
-- as their tooltip (mode-<mode>-tip))
local MODES = { "line", "mall", "extend", "base", "busdesign", "refit", "move", "ship" }
local GROUP_START = { base = true, refit = true, ship = true }  -- (a gap above them on the rail)
local RAIL_W, RAIL_H = 112, #MODES * 34 + 3 * 12 + 8          -- (the rail of mode tabs, GUI units)
local TOOLS_H = 4 * 34 + 12 + 34 + 8                          -- (the preview's tools, a rail on the right)
local BUS_DIRS = { "north", "east", "south", "west" }
local patch_areas = safe.stored("patch_areas")  -- player index -> {{area, counts = {resource = tiles}}}: the ore patches picked for a bus design
local bus_shift = safe.stored("bus_shift")  -- player index -> tiles the bus head is moved sideways from the player (the ◀ ▶ arrows)
local BUS_STEP = 16

local function row(parent, caption, tooltip)
  local f = parent.add({ type = "flow", direction = "horizontal" })
  f.style.vertical_align = "center"
  local l = f.add({ type = "label", caption = caption, tooltip = tooltip })
  l.style.width = 80
  return f
end

--- a framed section with its heading (as the web app's panels): fse-std's section, in the player's theme
local function section(parent, title, tooltip, name)
  local f = ui.section(parent, title, tooltip, name, { horizontally_stretchable = true })
  local head = title and f.children[1]  -- (a long heading wraps, in the wide fonts too, instead of being cut)
  if head and ui.themed then
    ui.theme.apply(head, "caption", { single_line = false, maximal_width = 270 })
  elseif head then
    head.style.single_line, head.style.maximal_width = false, 270
  end
  return f
end

--- a field: its caption, small and dim, over the control added to the returned flow
local field = ui.field

--- a section folded unless `open`, a badge in its heading saying what's in it (themed; hidden while badge_text is
--- nil); returns its body
local function fold(parent, title, open, badge_name, badge_text)
  local body = ui.fold(parent, title, open)
  if body.name == "fstd_fold_body" then
    local f = body.parent
    ui.theme.apply(f, "section", { horizontally_stretchable = true })
    local head = f.children[1]
    head.style.horizontally_stretchable = true
    head.add({ type = "empty-widget" }).style.horizontally_stretchable = true
    local b = ui.badge(head, badge_text or "")
    b.name, b.visible = badge_name, badge_text ~= nil
  end
  return body
end

--- the left panel's controls shrink to its width instead of being cut off at its edge (a drop-down is as wide as
--- its longest item: flows and drop-downs may be squashed, the drop-down then shortens its caption)
local function fit_panel(el)
  if el.type == "flow" or el.type == "drop-down" then el.style.horizontally_squashable = true end
  for _, c in pairs(el.children) do fit_panel(c) end
end

--- fields side by side
local function fields(parent)
  local f = parent.add({ type = "flow", direction = "horizontal" })
  f.style.horizontal_spacing = 12
  return f
end

-- "Only unlocked" (the checkbox by the tabs, per player): every picker of the window offers only what the force has
-- unlocked: recipes it may craft, items it can make, entities whose item it can make. A picker's own filter stays
-- (kept in its tags) and the names are added to it; unticked, the own filter comes back.
local only_unlocked = safe.stored("only_unlocked")  -- player index -> true
-- "Docked" (the checkbox by it, per player): the modes, the preview's tools and the actions on frames stuck to the
-- window's edges (fse-std's layouts); unticked, all of it inside the window
local inside = safe.stored("inside")  -- player index -> true: inside the window
function docked(player) return ui.layouts and not inside[player.index] and not web.model(player) end

-- {"item/iron-plate" = true, ...} of a recipe's ingredients or products
local function stuff(list)
  local out = {}
  for _, x in pairs(list) do
    if x.type == "item" or x.type == "fluid" then out[x.type .. "/" .. x.name] = true end
  end
  return out
end

local function has(kind, key, invert)  -- a has-ingredient-/has-product- filter on "item/name"
  local t, name = key:match("^(%a+)/(.+)$")
  return { filter = "has-" .. kind .. "-" .. t, elem_filters = { { filter = "name", name = name } }, invert = invert,
           mode = "and" }
end

-- one "and" group per unlocked recipe: what tells it apart from locked recipes (filters on recipes have no
-- "name"). Its main product, category, subgroup and crafting time, then for each locked recipe still let through
-- (a has-product filter matches any product: advanced oil processing makes petroleum gas too) one more clause:
-- something the locked one has and this one doesn't, or the other way round.
-- what a recipe's prototype says, read once a session (prototypes don't change while the game runs): reading them
-- through the API for thousands of recipes on every pick took seconds in a big pack
local facts_of = {}  -- recipe name -> {product, category, subgroup, energy, sig, hidden, ins, outs}
local function facts(name)
  local f = facts_of[name]
  if not f then
    local p = prototypes.recipe[name]
    local first = p.products[1]
    f = { product = first and (first.type .. "/" .. first.name), category = p.category, subgroup = p.subgroup.name,
          energy = p.energy, hidden = p.hidden }
    f.sig = table.concat({ f.category, f.subgroup, f.energy }, "|")
    facts_of[name] = f
  end
  return f
end
local function sets(name)  -- its ingredients and products as {"item/x" = true}
  local f = facts(name)
  if not f.ins then
    local p = prototypes.recipe[name]
    f.ins, f.outs = stuff(p.ingredients), stuff(p.products)
  end
  return f.ins, f.outs
end

-- One pass over the force's recipes (15 ms in a big pack; reading every recipe's products too took as long again):
-- which it has enabled, and the items the enabled ones make (control.lua's craftable: not hidden, not recycling;
-- their products as the prototypes have them). Once a tick at most: opening the window asked for it three times.
local pass_at = {}  -- force index -> {tick, enabled, items}
function force_pass(force)
  local m = pass_at[force.index]
  if m and m.tick == game.tick then return m end
  local enabled, items = {}, {}
  for name, r in pairs(force.recipes) do
    local on = r.enabled
    enabled[name] = on
    if on and not r.hidden and r.category ~= "recycling" then
      local _, outs = sets(name)
      for k in pairs(outs) do
        local t, item = k:match("^(%a+)/(.+)$")
        if t == "item" then items[item] = true end
      end
    end
  end
  m = { tick = game.tick, enabled = enabled, items = items }
  pass_at[force.index] = m
  return m
end

-- (the facts read ahead, a few hundred recipes a tick, when the window may open soon with "Only unlocked" on: the
-- first build of its filters read 19,000 recipes at once, 100 ms of its 140)
local warming  -- {names, i}
local WARM_PER_TICK = 400
function M.warm(player)
  if warming ~= nil or not only_unlocked[player.index] then return end
  local names = {}
  for name in pairs(prototypes.recipe) do names[#names + 1] = name end
  warming = { names = names, i = 1 }
end
local function warm_tick()
  local w = warming
  if not w or not w.names then return end
  local stop = math.min(#w.names, w.i + WARM_PER_TICK - 1)
  for i = w.i, stop do
    local f = facts(w.names[i])
    if not f.hidden and f.product then sets(w.names[i]) end
  end
  w.i = stop + 1
  if w.i > #w.names then warming = { done = true } end
end

-- The filters are kept small: the game checks every recipe against all of them each time a picker opens, and one
-- group per recipe (21,000 clauses in a big pack) made opening the picker stutter. Most recipes go into one group
-- per crafting category and product type: "has a product among these" and "in this category". A product that a
-- locked recipe of that category also makes can't go there; its recipes get a group each, told apart from the
-- locked ones by category, subgroup, time and, where needed, an ingredient or product (fine groups).
local function recipe_filters(force)
  local enabled = force_pass(force).enabled  -- recipe name -> whether the force has it (a lookup by name is slow)
  local unlocked, listed = {}, {}  -- listed: category -> {"item/x" = true} of the unlocked recipes' main products
  for name, on in pairs(enabled) do
    local f = on and facts(name)
    if f and f.product and not f.hidden then
      unlocked[#unlocked + 1] = name
      listed[f.category] = listed[f.category] or {}
      listed[f.category][f.product] = true
    end
  end
  -- every product a locked recipe of the same category makes: off the coarse lists
  local taken = {}  -- category -> {"item/x" = true}
  for name, on in pairs(enabled) do
    local f = not on and facts(name)
    local mine = f and not f.hidden and listed[f.category]
    if mine then
      local _, outs = sets(name)
      for k in pairs(outs) do
        if mine[k] then
          taken[f.category] = taken[f.category] or {}
          taken[f.category][k] = true
        end
      end
    end
  end

  local coarse, fine, by_sig = {}, {}, {}  -- coarse: category -> type -> {names}
  for _, name in ipairs(unlocked) do
    local f = facts(name)
    local t, item = f.product:match("^(%a+)/(.+)$")
    if (t == "item" or t == "fluid") and not (taken[f.category] and taken[f.category][f.product]) then
      coarse[f.category] = coarse[f.category] or {}
      local list = coarse[f.category][t] or {}
      coarse[f.category][t] = list
      if not list[item] then
        list[item] = true
        list[#list + 1] = item
      end
    else
      local g = { name = name, product = f.product, has("product", f.product),
                  { filter = "category", category = f.category, mode = "and" },
                  { filter = "subgroup", subgroup = f.subgroup, mode = "and" },
                  { filter = "energy", comparison = "=", value = f.energy, mode = "and" } }
      g[1].mode = nil
      fine[#fine + 1] = g
      by_sig[f.sig] = by_sig[f.sig] or {}
      table.insert(by_sig[f.sig], g)
    end
  end
  -- the locked recipes the fine groups still let through (a has-product filter matches any product: advanced oil
  -- processing makes petroleum gas too), each kept out by one more clause in the groups it slips into
  for name, on in pairs(enabled) do
    local f = not on and facts(name)
    local list = f and not f.hidden and by_sig[f.sig]
    if list then
      local li, lp = sets(name)
      local through = false
      for _, g in ipairs(list) do if lp[g.product] then through = true break end end
      if through then
        for _, g in ipairs(list) do
          local ri, rp = sets(g.name)
          local clause
          for k in pairs(li) do if not clause and not ri[k] then clause = has("ingredient", k, true) end end
          for k in pairs(lp) do if not clause and not rp[k] then clause = has("product", k, true) end end
          for k in pairs(ri) do if not clause and not li[k] then clause = has("ingredient", k) end end
          for k in pairs(rp) do if not clause and not lp[k] then clause = has("product", k) end end
          if clause then g[#g + 1] = clause end
        end
      end
    end
  end

  local out = {}
  for category, types in pairs(coarse) do
    for t, list in pairs(types) do
      local names = {}  -- (the list without its lookup keys)
      for i = 1, #list do names[i] = list[i] end
      out[#out + 1] = { filter = "has-product-" .. t, elem_filters = { { filter = "name", name = names } } }
      out[#out + 1] = { filter = "category", category = category, mode = "and" }
    end
  end
  for _, g in ipairs(fine) do for _, x in ipairs(g) do out[#out + 1] = x end end
  return out
end

-- the filters that keep a picker to what the force has unlocked, by its elem_type. Built once per force until its
-- research changes (M.forget_unlocked, from control.lua's research events): a big pack took a second or more to
-- build them, and every pick in the window built them again, so picking a recipe froze the game
local unlocked_cache = {}  -- force index -> filters

--- the force's unlocked filters are out of date (nil: every force's)
function M.forget_unlocked(force)
  if force then unlocked_cache[force.index], pass_at[force.index] = nil, nil else unlocked_cache, pass_at = {}, {} end
  for i, u in pairs(unlocks) do
    if not force or u.force == force.index then unlocks[i] = nil end
  end
end

local function unlocked_filters(force)
  if unlocked_cache[force.index] then return unlocked_cache[force.index] end
  local items, entities = {}, {}
  local recipe = recipe_filters(force)
  for name in pairs(craftable(force)) do
    items[#items + 1] = name
    local proto = prototypes.item[name]
    local place = proto and proto.place_result
    if place then entities[#entities + 1] = place.name end
  end
  local item = { { filter = "name", name = items } }
  local entity = { { filter = "name", name = entities } }
  unlocked_cache[force.index] = { recipe = recipe, ["recipe-with-quality"] = recipe, item = item,
    ["item-with-quality"] = item, entity = entity, ["entity-with-quality"] = entity }
  return unlocked_cache[force.index]
end

local function restrict_el(el, on, names)
  if el.type == "choose-elem-button" then
    local tags = el.tags
    local own = tags.bpgen_own_filters
    if on and names[el.elem_type] then
      if own == nil then
        own = el.elem_filters or false
        tags.bpgen_own_filters = own
        el.tags = tags
      end
      -- ("and" binds tighter than "or": the own filter goes into every group, each own group crossed with each)
      local function groups(list)
        local out = {}
        for i, x in ipairs(list) do
          if i == 1 or x.mode ~= "and" then out[#out + 1] = {} end
          table.insert(out[#out], x)
        end
        return out
      end
      local mine = own and groups(own) or { {} }
      local f = {}
      for _, g in ipairs(groups(names[el.elem_type])) do
        for _, o in ipairs(mine) do
          for _, x in ipairs(g) do f[#f + 1] = x end
          for _, x in ipairs(o) do
            local c = {}
            for k, v in pairs(x) do c[k] = v end
            c.mode = "and"
            f[#f + 1] = c
          end
        end
      end
      el.elem_filters = f
    elseif own ~= nil then
      el.elem_filters = own or nil
      tags.bpgen_own_filters = nil
      el.tags = tags
    end
  end
  for _, c in pairs(el.children) do restrict_el(c, on, names) end
end

function M.set_only_unlocked(player, on)
  only_unlocked[player.index] = on or nil
  local frame = player.gui.screen[NAME]
  local box = frame and find(frame, "bpgen_only_unlocked")
  if box then box.state = on == true end
  local mark = frame and find(frame, "bpgen_unlocked_badge")
  if mark then mark.visible = on == true end
  M.restrict(player)
end

--- the window's pickers as the player's "Only unlocked" says (call after pickers are added or their filters set)
M.recipe_filters = recipe_filters  -- (tests)

function M.restrict(player)
  local frame = player and player.valid and player.gui.screen[NAME]
  if not frame then return end
  local on = only_unlocked[player.index] == true
  restrict_el(frame, on, on and unlocked_filters(player.force) or {})
  web.filters_changed(player)  -- (the web window's pickers: their mirrors get them)
end

local function set_machine_filter(frame, recipe)
  local btn = find(frame, "bpgen_machine")
  local cat = recipe and prototypes.recipe[recipe] and prototypes.recipe[recipe].category
  if btn and cat then
    btn.elem_filters = { { filter = "crafting-category", crafting_category = cat } }
    local tags = btn.tags
    tags.bpgen_own_filters = nil -- (a new own filter: restricted again below)
    btn.tags = tags
    M.restrict(game.get_player(frame.player_index))
  end
end

local function status(frame, text)
  local s = find(frame, "bpgen_status")
  if s then s.caption = text or "" end
  if frame and frame.valid then web.dirty(game.get_player(frame.player_index)) end
end

--- what an action did: in the status line and as a toast (a message at the top that goes away by itself)
local function say(player, text)
  local frame = player.gui.screen[NAME]
  if frame then status(frame, text) end
  if web.model(player) then return web.toast(player, text) end  -- (the page shows it)
  ui.toast(player, text)
end

--- the window themed, the frames stuck to it too (call after adding elements: only new ones are themed)
local function adopt(player)
  local frame = player.gui.screen[NAME]
  if not frame then return end
  ui.adopt(frame)
  for _, c in pairs(ui.attached(player, NAME)) do ui.adopt(c) end
end

--- a drawer stuck to the window's right edge (the test run's, History) beside it when the screen has room for it
--- there (clear of the minimap), else over the preview's right part, inside the window
local function place_drawer(player, frame, drawer)
  local tags = drawer and drawer.valid and drawer.tags
  local a = tags and tags.fstd_attach
  local loc, tw = frame.location, frame.tags.fstd_tweak
  if not (a and loc and type(tw) == "table" and tw.width) then return end
  local ds, res = player.display_scale, player.display_resolution
  local sc = ds * ui.scale_of(frame)
  local top = loc.y + (a.along or 0) * sc
  local edge = top < MAP_H * ds and res.width - MAP_W * ds or res.width
  local beside = loc.x + (tw.width - 8 + a.w) * sc <= edge
  local overlap = beside and 8 or a.w + 8
  if a.overlap ~= overlap then
    a.overlap = overlap
    drawer.tags = tags
  end
  if ui.theme and ui.theme.follow then ui.theme.follow(frame) end
end

--- the test run's drawer (docked: out of the window's right edge) or its section (inside) shown or hidden; History's
--- drawer makes way for it
local function test_shown(player, frame, on)
  local box = find(frame, "bpgen_test_box")
  if box then box.visible = on end
  local drawer = player.gui.screen[NAME .. "__test"]
  if not drawer then return end
  local tags = drawer.tags
  tags.bpgen_open = on or nil
  drawer.tags = tags
  drawer.visible = on and frame.visible
  if on then
    local hist = player.gui.screen[NAME .. "__right"]
    if hist then hist.destroy() end
    place_drawer(player, frame, drawer)
    drawer.bring_to_front()
  end
end

--- the test run's fast-forward off: the game's speed as it was (the ×5 / ×10 button back at ×1)
local function test_speed_off(frame)
  if storage.bpgen_speed_was then
    game.speed = storage.bpgen_speed_was
    storage.bpgen_speed_was = nil
  end
  local b = frame and find(frame, "bpgen_test_speed")
  if b then
    b.caption, b.visible = "×1", false
    local tags = b.tags
    tags.speed = 1
    b.tags = tags
  end
end

--- a test run going on (anyone's)
local function testing()
  for _, run in pairs(storage.bpgen_bench and storage.bpgen_bench.runs or {}) do
    if run.kind == "test" then return true end
  end
  return false
end

--- the player's test run stopped (its window closed, the player gone), and the fast-forward with it once no test
--- runs any more
local function test_stop(player_index)
  local run = bench.find("test", player_index)
  if run then bench.stop(run.id) end
  if not testing() then test_speed_off(nil) end
end

--- the window closed, and what's stuck to it (its test run stops: nothing would show it); the web window's shell too,
--- unless `keep_shell` (it opens again at once, as the web window: the page stays)
local function close(player, keep_shell)
  test_stop(player.index)
  for _, c in pairs(ui.attached(player, NAME)) do c.destroy() end
  if player.gui.screen[NAME] then player.gui.screen[NAME].destroy() end
  if not keep_shell then web.close(player) end
end
M.close = close

--- Plan breathes (and a note says so) when the settings changed since the last plan
local function stale(player, on)
  local frame = player.gui.screen[NAME]
  local plan, note = find(frame, "bpgen_plan"), find(frame, "bpgen_stale")
  if not (plan and note) then return end
  note.visible = on
  ui.set_role(plan, on and "pulse" or "ok")
  web.dirty(player)
end

--- the folds' badges: what's set in them
local function badges_of(frame)
  local n = 0
  for _, name in ipairs({ "bpgen_module", "bpgen_beacon", "bpgen_beacon_module" }) do
    local el = find(frame, name)
    if el and el.elem_value then n = n + 1 end
  end
  ui.set_badge(find(frame, "bpgen_modules_badge"), n > 0 and T("n-set", n) or T("none"))
  local more = find(frame, "bpgen_more_toggle")
  ui.set_badge(find(frame, "bpgen_tune_badge"), T(more and more.state and "set" or "auto"))
end

local glows = safe.stored("glows")  -- player index -> the preview's glow (fse-std's overlay id)
--- the preview glowing (an accent outline, the fse loader's draw plugin) while bpgen plans or tests on it
local function glow(player, on)
  ui.unglow(glows[player.index])
  glows[player.index] = nil
  web.dirty(player)
  if web.model(player) then return end  -- (the page shows it)
  local cam = on and find(player.gui.screen[NAME], "bpgen_cam")
  if cam then glows[player.index] = ui.glow(cam) end
end

--- a change in the window's settings after a plan: Plan breathes
local function changed(e)
  local el = e.element
  if not (el and el.valid) or el.name == "bpgen_only_unlocked" or el.name == "bpgen_section"
    or el.name == "bpgen_docked" or el.name == "bpgen_web" then return end
  local root = el
  while root.parent and root.parent.parent do root = root.parent end
  if root.name ~= NAME or not results[e.player_index] then return end
  local player = game.get_player(e.player_index)
  stale(player, true)
  badges_of(root)
end

--- the mode's tab pressed, its options shown (the mode is kept in the tab row's tags)
local function set_mode(frame, mode)
  local tabs = find(frame, "bpgen_tabs")
  for _, b in pairs(tabs.children) do b.toggled = b.tags.mode == mode end
  tabs.tags = { mode = mode }
  find(frame, "bpgen_line_box").visible = mode == "line"
  find(frame, "bpgen_mall_box").visible = mode == "mall"
  find(frame, "bpgen_base_box").visible = mode == "base"
  find(frame, "bpgen_extend_box").visible = mode == "extend"
  find(frame, "bpgen_bus_box").visible = mode == "busdesign"
  find(frame, "bpgen_ship_box").visible = mode == "ship"
  find(frame, "bpgen_refit_box").visible = mode == "refit"
  find(frame, "bpgen_move_box").visible = mode == "move"
  -- ("Next to my base" and its buttons: for what can go next to the base)
  find(frame, "bpgen_next_box").visible = mode == "line" or mode == "mall" or mode == "extend"
  find(frame, "bpgen_base_tools").visible = mode ~= "ship"
  for i, m in ipairs(MODES) do
    if m == mode then find(frame, "bpgen_mode_tip").caption = T("mode-" .. m .. "-tip") end
  end
  M.steps(frame)
  web.dirty(game.get_player(frame.player_index))
end

--- the step bar: 1 What (a mode, always), 2 How (until there is a plan), 3 Where (a plan to place)
function M.steps(frame)
  local bar = find(frame, "bpgen_steps")
  if not bar then return end
  local r = results[frame.player_index]
  local mode = find(frame, "bpgen_tabs").tags.mode
  local made = r and r.res and r.res.mode
  local planned = r ~= nil and r.blueprint ~= nil and (made == mode or (mode == "refit" and made == "mall")
    or (made == "lane" and mode == "extend"))
  local now = planned and 3 or 2
  for i, el in ipairs(bar.children) do
    if el.tags.step then
      el.toggled = el.tags.step == now
      el.enabled = el.tags.step <= now
    end
  end
end

--- the picked ore patches, listed under the button
local function show_patches(frame, player)
  local note = find(frame, "bpgen_patch_note")
  if not note then return end
  local lines = {}
  for i, p in ipairs(patch_areas[player.index] or {}) do
    local t = {}
    for name, n in pairs(p.counts) do t[#t + 1] = string.format("[entity=%s] %d", name, n) end
    lines[#lines + 1] = T("patch-line", i, #t > 0 and table.concat(t, "  ") or T("no-ore"))
  end
  note.caption = #lines > 0 and join(lines, "\n") or T("no-patches")
  web.dirty(player)
end

-- a bpgen tool in hand: the window (and what's stuck to it) out of the way, a small card at the top says what to
-- drag; the window back once the tool leaves the hand (a drag done, Cancel, Q)
local CARD = "bpgen_tool_card"
local TOOLS = {
  ["bpgen-refit"] = { title = T("mode-refit"), text = T("card-refit") },
  ["bpgen-move"] = { title = T("mode-move"), text = T("card-move") },
  ["bpgen-snapshot"] = { title = T("snapshot"), text = T("card-snapshot") },
  ["bpgen-spot"] = { title = T("pick-spot"), text = T("card-spot") },
  ["bpgen-patches"] = { title = T("ore-patches"), text = T("card-patches"), many = true },
}
M.TOOLS = TOOLS

local function patch_count(player)
  local n = #(patch_areas[player.index] or {})
  return n == 0 and T("no-patches") or T("patches-picked", n)
end

--- the window and its stuck frames shown or hidden
local function window_shown(player, on)
  local frame = player.gui.screen[NAME]
  local as_web = web.model(player)
  if frame then frame.visible = on and not as_web end  -- (the web window's Lua one stays hidden: the shell hides)
  if as_web then web.show(player, on) end
  for _, c in pairs(ui.attached(player, NAME)) do
    c.visible = on and (c.name ~= NAME .. "__test" or c.tags.bpgen_open == true)
  end
  local menu = player.gui.screen[NAME .. "__menu"]
  if menu and not on then menu.destroy() end
end

--- the tool `item` in hand: the card up, the window hidden
function M.tool_card(player, item)
  local tool = TOOLS[item]
  if not tool then return end
  local screen = player.gui.screen
  if screen[CARD] then
    if screen[CARD].tags.item == item then return end
    screen[CARD].destroy()
  end
  window_shown(player, false)
  local card = screen.add({ type = "frame", name = CARD, direction = "vertical", tags = { item = item } })
  card.style.padding = 10
  card.style.width = 380
  ui.add(card, { type = "label", caption = T("card-title", "[item=" .. item .. "]", tool.title) }, "caption")
  local text = ui.add(card, { type = "label", caption = tool.text }, "text")
  text.style.single_line = false
  text.style.maximal_width = 356
  ui.add(card, { type = "label", name = "bpgen_card_note", caption = tool.many and patch_count(player)
    or T("card-note") }, "dim")
  local row = card.add({ type = "flow", direction = "horizontal" })
  row.style.top_margin = 6
  row.style.vertical_align = "center"
  row.add({ type = "empty-widget" }).style.horizontally_stretchable = true
  if tool.many then
    ui.add(row, { type = "button", caption = T("done"), tags = { bpgen = "tool_done" } }, "ok")
  end
  ui.add(row, { type = "button", caption = T("cancel"), tags = { bpgen = "tool_done" } }, "button")
  ui.adopt(card)
  local scale = player.display_scale * ui.scale_of(card)  -- (drawn at the Window size: adopt scaled it)
  card.location = { math.max(0, (player.display_resolution.width - 380 * scale) / 2), math.floor(60 * scale) }
end

--- the tool out of hand: the card gone, the window back
function M.tool_done(player)
  local card = player.gui.screen[CARD]
  if card then card.destroy() end
  window_shown(player, true)
end

--- the cursor changed (a tool taken from a shortcut or a hotkey, put away with Q): the card follows it
function M.on_cursor(player)
  local cs = player.cursor_stack
  local item = cs and cs.valid_for_read and cs.name
  if item and TOOLS[item] then return M.tool_card(player, item) end
  if player.gui.screen[CARD] then M.tool_done(player) end
end

--- an area dragged with the patch tool: added to the bus design's patches
function M.add_patch(player, area)
  local counts = {}
  for _, r in pairs(player.surface.find_entities_filtered({ area = area, type = "resource" })) do
    counts[r.name] = (counts[r.name] or 0) + 1
  end
  local list = patch_areas[player.index] or {}
  list[#list + 1] = { area = area, counts = counts }
  patch_areas[player.index] = list
  local frame = player.gui.screen[NAME]
  if frame then show_patches(frame, player) end
  local note = player.gui.screen[CARD] and player.gui.screen[CARD].bpgen_card_note
  if note then note.caption = patch_count(player) end
end

function M.open(player, prefill)
  prefill = prefill or {}
  local screen = player.gui.screen
  -- (the web window: this Lua window, hidden, is its model; the shell stays open when it was)
  local as_web = web.use(player)
  close(player, as_web)
  web.set_model(player, as_web)
  local frame, holder
  if stdwin and not as_web then
    -- (made at the size that fits the room it has at its scale: never drawn smaller for a size it won't keep)
    local max_w, max_h, min_h = room(player)
    local ww, wh = wanted_size(player)
    local cw, ch = math.max(CAM_MIN_W, math.floor(math.min(ww, max_w))), math.max(min_h, math.floor(math.min(wh, max_h)))
    storage.fstd_windows = storage.fstd_windows or {}
    storage.fstd_windows[player.index] = storage.fstd_windows[player.index] or {}
    local saved = storage.fstd_windows[player.index][NAME] or {}
    storage.fstd_windows[player.index][NAME] = saved
    saved.width, saved.height = cw + LEFT_W, ch + frame_h(player)
    -- (a new window: placed by us, not centred on the whole screen by the library)
    if not saved.location then saved.location = place_new(player, cw, ch) end
    frame, holder = stdwin.create(player, { name = NAME, title = "bpgen", width = cw + LEFT_W, height = ch + frame_h(player),
      min_width = CAM_MIN_W + LEFT_W, min_height = min_h + frame_h(player), themed = ui.themed, settings = ui.menus,
      esc = false })  -- (a companion: it stays open while the player uses the game's own windows)
    holder.style.padding = 0
  else
    frame = screen.add({ type = "frame", name = NAME, direction = "vertical" })
    local bar = frame.add({ type = "flow", direction = "horizontal" })
    bar.drag_target = frame
    bar.add({ type = "label", caption = "bpgen", style = "frame_title", ignored_by_interaction = true })
    local drag = bar.add({ type = "empty-widget", style = "draggable_space_header", ignored_by_interaction = true })
    drag.style.height = 24
    drag.style.horizontally_stretchable = true
    bar.add({ type = "sprite-button", style = "frame_action_button", sprite = "utility/close", tags = { bpgen = "close" } })
    holder = frame
  end

  -- the modes: a rail of tabs out of the window's left edge (fse-std's layouts), else a row of them in the window
  local out = docked(player)
  -- (drawn at another scale, the tabs' spacing rounds up a little: some slack so the last one isn't cut)
  local rail = out and ui.attach(frame, "left", { size = { RAIL_W, RAIL_H + (ui.scale_of(frame) ~= 1 and 20 or 0) },
    along = 40, overlap = 6, role = "inset",
    direction = "vertical", padding = 4 })
  local tabs
  if rail then
    tabs = rail.add({ type = "flow", name = "bpgen_tabs", direction = "vertical", tags = { mode = "line" } })
    tabs.style.vertical_spacing = 4
  else
    tabs = holder.add({ type = "table", name = "bpgen_tabs", column_count = #MODES, tags = { mode = "line" } })
    tabs.style.horizontal_spacing = 4
    tabs.style.left_padding = 6
  end
  for i, m in ipairs(MODES) do
    ui.add(tabs, { type = "button", caption = T("mode-" .. m), tooltip = T("mode-" .. m .. "-tip"), toggled = i == 1,
      tags = { bpgen = "tab", mode = m } }, rail and "rtab" or "tab",
      rail and { width = RAIL_W - 8, height = 30, top_margin = GROUP_START[m] and 12 or nil }
        or { minimal_width = 70, height = 34 })
  end
  -- what the mode does, and "Only unlocked"
  local top = holder.add({ type = "flow", name = "bpgen_top", direction = "horizontal" })
  top.style.vertical_align = "center"
  top.style.left_padding, top.style.right_padding = 6, 6
  ui.add(top, { type = "label", name = "bpgen_mode_tip", caption = T("mode-line-tip") }, "dim")
  top.add({ type = "empty-widget" }).style.horizontally_stretchable = true
  if ui.menus then  -- (the window's settings are behind the gear by its close button: a mark while a filter is on)
    ui.badge(top, T("unlocked-badge"), T("unlocked-badge-tip")).name =
      "bpgen_unlocked_badge"
    find_in(top, "bpgen_unlocked_badge").visible = only_unlocked[player.index] == true
  else
    ui.add(top, { type = "checkbox", name = "bpgen_only_unlocked", caption = T("only-unlocked"),
      state = only_unlocked[player.index] == true, tooltip = T("only-unlocked-tip") }, "check")
  end
  local body = holder.add({ type = "flow", direction = "horizontal" })
  local outer = body.add({ type = "frame", style = "inside_shallow_frame", direction = "vertical" })
  outer.style.width = 320
  outer.style.vertically_stretchable = true
  local left = outer.add({ type = "scroll-pane", horizontal_scroll_policy = "never" })
  left.style.padding = 8
  left.style.vertically_stretchable = true

  -- a production line: sections as the web app's
  local line = left.add({ type = "flow", name = "bpgen_line_box", direction = "vertical" })
  local sec = section(line, T("recipe"))
  local fs = fields(sec)
  field(fs, T("recipe")).add({ type = "choose-elem-button", elem_type = "recipe", name = "bpgen_recipe",
    elem_filters = { { filter = "hidden", invert = true } } })
  field(fs, T("machine"), T("machine-tip")).add({ type = "choose-elem-button", elem_type = "entity-with-quality",
    name = "bpgen_machine", elem_filters = { { filter = "crafting-machine" } } })
  field(fs, T("per-minute"), T("rate-tip")).add({ type = "textfield",
    name = "bpgen_rate", numeric = true, allow_decimal = true, lose_focus_on_confirm = true }).style.width = 90
  fs = fields(sec)
  field(fs, T("belt"), T("belt-tip")).add({ type = "choose-elem-button", elem_type = "entity",
    name = "bpgen_belt", elem_filters = { { filter = "type", type = "transport-belt" } } })
  field(fs, T("feed"), T("feed-tip")).add({
    type = "drop-down", name = "bpgen_feed", items = { T("feed-belts"), T("feed-robots"), T("feed-bus") },
    selected_index = 1 })

  sec = section(line, T("make-here"), T("make-here-tip"))
  sec.add({ type = "flow", name = "bpgen_make", direction = "vertical" }).add({ type = "label", caption = T("pick-a-recipe") })

  -- (what's seldom set: folded, a badge saying what's in it)
  sec = fold(line, T("also-make"), false, "bpgen_also_badge", nil)
  sec.add({ type = "flow", name = "bpgen_also", direction = "vertical" }).add({ type = "label", caption = T("pick-a-recipe") })

  sec = fold(line, T("modules"), prefill.modules ~= nil, "bpgen_modules_badge", T("none"))
  fs = fields(sec)
  field(fs, T("machine-modules"), T("machine-modules-tip")).add({ type = "choose-elem-button", elem_type = "item-with-quality",
    name = "bpgen_module", elem_filters = { { filter = "type", type = "module" } } })
  field(fs, T("beacon"), T("beacon-tip")).add({ type = "choose-elem-button",
    elem_type = "entity-with-quality", name = "bpgen_beacon", elem_filters = { { filter = "type", type = "beacon" } } })
  field(fs, T("beacon-modules")).add({ type = "choose-elem-button", elem_type = "item-with-quality",
    name = "bpgen_beacon_module", elem_filters = { { filter = "type", type = "module" } } })

  sec = fold(line, T("fine-tune"), prefill.more == true, "bpgen_tune_badge", T("auto"))
  sec.add({ type = "checkbox", name = "bpgen_more_toggle", caption = T("fine-tune-toggle"), state = false,
    tags = { bpgen_toggle = "bpgen_more" } })
  local more = sec.add({ type = "flow", name = "bpgen_more", direction = "vertical", visible = false })
  local ins = { { filter = "type", type = "inserter" } }
  fs = fields(more)
  field(fs, T("input"), T("input-tip")).add({ type = "choose-elem-button",
    elem_type = "entity", name = "bpgen_near", elem_filters = ins })
  field(fs, T("long-handed"), T("long-handed-tip")).add({ type = "choose-elem-button", elem_type = "entity",
    name = "bpgen_far", elem_filters = ins })
  field(fs, T("output")).add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_out", elem_filters = ins })
  field(more, T("per-row"), T("per-row-tip")).add({ type = "textfield", name = "bpgen_per_row",
    numeric = true, lose_focus_on_confirm = true }).style.width = 60

  -- a mall: many products into chests
  local mall = left.add({ type = "flow", name = "bpgen_mall_box", direction = "vertical", visible = false })
  local sec = section(mall, T("products"), T("products-tip"))
  sec.add({ type = "table", name = "bpgen_products", column_count = 6 }).add({ type = "choose-elem-button",
    elem_type = "recipe", tags = { bpgen_product = true }, elem_filters = { { filter = "hidden", invert = true } } })
  sec = section(mall, T("machines-chests"))
  local fs = fields(sec)
  field(fs, T("machine"), T("mall-machine-tip")).add({ type = "choose-elem-button", elem_type = "entity",
    name = "bpgen_mall_machine", elem_filters = { { filter = "crafting-machine" } } })
  field(fs, T("chest")).add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_chest",
    elem_filters = { { filter = "type", type = { "container", "logistic-container" } } } })
  fs = fields(sec)  -- (its own row: its longest item is wide)
  field(fs, T("fed-by"), T("fed-by-tip")).add({
    type = "drop-down", name = "bpgen_mall_feed", items = { T("mall-feed-rows"), T("mall-feed-robots"), T("mall-feed-mixed"),
      T("feed-bus"), T("mall-feed-hand"), T("mall-feed-loop"), T("mall-feed-loop-bus") },
    selected_index = 3 })
  fs = fields(sec)
  field(fs, T("buffer"), T("buffer-tip")).add({ type = "textfield", name = "bpgen_mall_buffer", text = "5",
    numeric = true, lose_focus_on_confirm = true }).style.width = 60
  field(fs, T("chest-limit"), T("chest-limit-tip")).add({ type = "textfield", name = "bpgen_chest_limit",
    text = "4", numeric = true, lose_focus_on_confirm = true }).style.width = 60
  field(fs, T("keep"), T("keep-tip")).add({
    type = "textfield", name = "bpgen_mall_stock", text = "10", numeric = true, lose_focus_on_confirm = true }).style.width = 60
  -- refit: an area of the base laid out again (its own mode: Plan gives the refit tool)
  local refitb = left.add({ type = "flow", name = "bpgen_refit_box", direction = "vertical", visible = false })
  sec = section(refitb, T("refit-area"), T("refit-area-tip"))
  ui.add(sec, { type = "label", caption = T("refit-how") }, "dim",
    { single_line = false, maximal_width = 260 })
  fs = fields(sec)
  field(fs, T("refit-as"), T("refit-as-tip")).add({
    type = "drop-down", name = "bpgen_refit_as", items = { T("refit-as-mall"), T("refit-as-base") }, selected_index = 1 })
  fs = fields(sec)
  field(fs, T("layout"), T("refit-layout-tip")).add({
    type = "drop-down", name = "bpgen_refit_layout", items = { T("refit-layout-loop"), T("refit-layout-side") },
    selected_index = 1 })
  fs = fields(sec)
  fs.add({ type = "checkbox", name = "bpgen_refit_add", caption = T("refit-add"), state = false, tooltip = T("refit-add-tip") })

  -- a starter base: science
  local base = left.add({ type = "flow", name = "bpgen_base_box", direction = "vertical", visible = false })
  sec = section(base, T("science"))
  fs = fields(sec)
  field(fs, T("per-minute"), T("spm-tip")).add({ type = "textfield", name = "bpgen_spm", text = "30", numeric = true,
    allow_decimal = true, lose_focus_on_confirm = true }).style.width = 60
  field(fs, T("lab")).add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_lab",
    elem_filters = { { filter = "type", type = "lab" } } })
  fs = fields(sec)
  fs.add({ type = "checkbox", name = "bpgen_base_military", caption = T("military"), state = false,
    tooltip = T("military-tip") })
  fs.add({ type = "checkbox", name = "bpgen_base_chemical", caption = T("chemical"), state = false,
    tooltip = T("chemical-tip") })
  sec = section(base, T("buildings"), T("buildings-base-tip"))
  fs = fields(sec)
  field(fs, T("assembler")).add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_assembler",
    elem_filters = { { filter = "type", type = "assembling-machine" } } })
  field(fs, T("furnace")).add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_furnace",
    elem_filters = { { filter = "crafting-category", crafting_category = "smelting" } } })
  sec = section(base, T("layout"))
  sec.add({ type = "checkbox", name = "bpgen_base_mall", caption = T("base-mall"), state = false })
  sec.add({ type = "checkbox", name = "bpgen_base_bus", caption = T("base-bus"), state = false, tooltip = T("base-bus-tip") })
  sec.add({ type = "checkbox", name = "bpgen_base_plates", caption = T("base-plates"), state = false,
    tooltip = T("base-plates-tip") })
  sec.add({ type = "checkbox", name = "bpgen_base_fit", caption = T("base-fit"), state = true, tooltip = T("base-fit-tip") })
  sec.add({ type = "checkbox", name = "bpgen_base_add", caption = T("base-add"), state = false, tooltip = T("base-add-tip") })
  sec.add({ type = "checkbox", name = "bpgen_base_import", caption = T("base-import"), state = false,
    tooltip = T("base-import-tip") })

  -- next to the base: a whole chain fed by what the base's belts carry, tapped in, powered
  local ext = left.add({ type = "flow", name = "bpgen_extend_box", direction = "vertical", visible = false })
  sec = section(ext, T("make"), T("make-tip"))
  fs = fields(sec)
  field(fs, T("item")).add({ type = "choose-elem-button", elem_type = "item", name = "bpgen_ext_item" })
  field(fs, T("per-minute")).add({ type = "textfield", name = "bpgen_ext_rate", text = "30", numeric = true,
    allow_decimal = true, lose_focus_on_confirm = true }).style.width = 60
  -- move: a section of the base somewhere else (its own mode: Plan gives the move tool)
  local moveb = left.add({ type = "flow", name = "bpgen_move_box", direction = "vertical", visible = false })
  sec = section(moveb, T("move-section"), T("move-section-tip"))
  ui.add(sec, { type = "label", caption = T("move-how") }, "dim", { single_line = false, maximal_width = 260 })
  sec = section(ext, T("your-base"))
  sec.add({ type = "button", caption = T("pick-base-area"), tags = { bpgen = "snapshot" }, tooltip = T("pick-base-area-tip") })
  local snap_note = sec.add({ type = "label", name = "bpgen_snap_note", caption = "" })
  snap_note.style.single_line = false
  snap_note.style.maximal_width = 260

  -- a bus design: ore patches -> drills -> smelter columns -> balancers -> a main bus
  -- a ship: a space platform round its hub, self-sufficient (asteroids to fuel and ammo)
  local shipb = left.add({ type = "flow", name = "bpgen_ship_box", direction = "vertical", visible = false })
  sec = section(shipb, T("shuttle"), T("shuttle-tip"))
  fs = fields(sec)
  field(fs, T("thrusters")).add({ type = "textfield", name = "bpgen_ship_thrusters", text = "2", numeric = true,
    lose_focus_on_confirm = true }).style.width = 60
  field(fs, T("gun-turrets")).add({ type = "textfield", name = "bpgen_ship_turrets", text = "6", numeric = true,
    lose_focus_on_confirm = true }).style.width = 60
  field(fs, T("collectors"), T("collectors-tip")).add({ type = "textfield",
    name = "bpgen_ship_collectors", text = "4", numeric = true, lose_focus_on_confirm = true }).style.width = 60
  fs = fields(sec)
  field(fs, T("thrust"), T("thrust-tip")).add({ type = "drop-down", name = "bpgen_ship_throttle",
    items = { T("thrust-auto"), T("thrust-efficient"), T("thrust-half"), T("thrust-full") },
    selected_index = 1, tags = { values = { -1, 0, 0.5, 1 } } })
  local orbits, names = {}, {}
  for name, loc in pairs(prototypes.space_location or {}) do
    if loc.type == "planet" then orbits[#orbits + 1] = name end
  end
  table.sort(orbits)
  for i, n in ipairs(orbits) do names[i] = { "", "[space-location=" .. n .. "] ", prototypes.space_location[n].localised_name } end
  field(fs, T("solar-for"), T("solar-for-tip")).add({
    type = "drop-down", name = "bpgen_ship_orbit", items = names, tags = { orbits = orbits },
    selected_index = math.max(1, (function() for i, n in ipairs(orbits) do if n == "nauvis" then return i end end end)() or 1) })
  local bus = left.add({ type = "flow", name = "bpgen_bus_box", direction = "vertical", visible = false })
  sec = section(bus, T("ore-patches"), T("ore-patches-tip"))
  local pf = sec.add({ type = "flow", direction = "horizontal" })
  pf.add({ type = "button", caption = T("pick-patches"), tags = { bpgen = "patches" }, tooltip = T("pick-patches-tip") })
  pf.add({ type = "button", caption = T("clear"), tags = { bpgen = "patches_clear" } })
  local pnote = sec.add({ type = "label", name = "bpgen_patch_note", caption = "" })
  pnote.style.single_line = false
  pnote.style.maximal_width = 260
  sec = section(bus, T("mode-busdesign"), T("bus-tip"))
  fs = fields(sec)
  field(fs, T("flows")).add({ type = "drop-down", name = "bpgen_bus_dir",
    items = { T("dir-north"), T("dir-east"), T("dir-south"), T("dir-west") },
    selected_index = 1 })
  field(fs, T("length")).add({ type = "textfield", name = "bpgen_bus_length", text = "40", numeric = true,
    lose_focus_on_confirm = true }).style.width = 50
  fs = fields(sec)  -- (a second row: four fields don't fit the panel's width)
  field(fs, T("belts-a-group")).add({ type = "textfield", name = "bpgen_bus_group", text = "4", numeric = true,
    lose_focus_on_confirm = true }).style.width = 50
  field(fs, T("gap")).add({ type = "textfield", name = "bpgen_bus_gap", text = "4", numeric = true,
    lose_focus_on_confirm = true }).style.width = 50
  sec.add({ type = "checkbox", name = "bpgen_bus_add", caption = T("bus-add"), state = false, tooltip = T("bus-add-tip") })
  sec.add({ type = "checkbox", name = "bpgen_bus_wood", caption = T("bus-wood"), state = true, tooltip = T("bus-wood-tip") })
  sec.add({ type = "checkbox", name = "bpgen_bus_balance", caption = T("balancers"), state = true,
    tooltip = T("balancers-tip") })
  sec = section(bus, T("buildings"), T("buildings-bus-tip"))
  fs = fields(sec)
  field(fs, T("drill"), T("drill-tip")).add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_bus_drill",
    elem_filters = { { filter = "type", type = "mining-drill" } } })
  field(fs, T("furnace"), T("bus-furnace-tip")).add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_bus_furnace",
    elem_filters = { { filter = "crafting-category", crafting_category = "smelting" } } })
  field(fs, T("belt")).add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_bus_belt",
    elem_filters = { { filter = "type", type = "transport-belt" } } })
  show_patches(frame, player)

  sec = section(left, T("next-to-base"), nil, "bpgen_next_box")
  sec.add({ type = "checkbox", name = "bpgen_ext_bus", caption = T("ext-bus"), state = true, tooltip = T("ext-bus-tip") })
  -- pinned under the panel's scroll, always in sight: planning's progress, what was said last, Plan (breathing when
  -- something changed since the last plan), the tools that look at the base
  local foot = outer.add({ type = "flow", name = "bpgen_foot", direction = "vertical" })
  foot.style.padding = { 6, 8, 8, 8 }
  foot.style.vertical_spacing = 4
  foot.style.horizontally_stretchable = true
  local prog = foot.add({ type = "flow", name = "bpgen_progress", direction = "vertical", visible = false })
  prog.style.horizontally_stretchable = true
  local bar = ui.progress(prog, 0, { caption = T("planning"), text = "" })
  bar.name = "bpgen_progress_bar"
  bar.parent.style.horizontally_stretchable = true
  local details = ui.fold(prog, T("details"), false, "bpgen_details")
  ui.add(details, { type = "label", name = "bpgen_log", caption = "" }, "dim",
    { single_line = false, maximal_width = 260, font = "default-small" })
  ui.add(foot, { type = "label", name = "bpgen_status" }, "text", { single_line = false, maximal_width = 270 })
  ui.add(foot, { type = "label", name = "bpgen_stale", caption = T("stale"),
    visible = false }, "caption", { single_line = false, maximal_width = 270 })
  ui.add(foot, { type = "button", name = "bpgen_plan", caption = T("plan"), tags = { bpgen = "plan" } }, "ok",
    { horizontally_stretchable = true, height = 36 })
  local go = foot.add({ type = "flow", name = "bpgen_base_tools", direction = "horizontal" })  -- (looking at the base)
  go.style.horizontal_spacing = 4
  ui.add(go, { type = "button", caption = T("short"), tags = { bpgen = "short" }, tooltip = T("short-tip") }, "button",
    { horizontally_stretchable = true, minimal_width = 0, height = 28 })
  ui.add(go, { type = "button", caption = T("check-bus"), tags = { bpgen = "check_bus" }, tooltip = T("check-bus-tip") },
    "button", { horizontally_stretchable = true, minimal_width = 0, height = 28 })

  -- (what the plan comes to and the test run: under Plan in the left panel, in a scroll area of their own (up to a
  -- share of the window's height), the preview taking the whole right side)
  local info = outer.add({ type = "scroll-pane", name = "bpgen_info", horizontal_scroll_policy = "never" })
  info.style.maximal_height = info_h(wanted_size(player) + frame_h(player))
  info.style.horizontally_stretchable = true
  info.style.padding = { 0, 8, 8, 8 }
  local right = body.add({ type = "frame", style = "inside_deep_frame", direction = "vertical" })
  local cam = right.add({ type = "camera", name = "bpgen_cam", position = { 0, 0 }, surface_index = player.surface.index, zoom = 1,
    raise_hover_events = true, tooltip = safe.has("std") and T("cam-tip") or nil })
  local CAM_W, CAM_H = wanted_size(player)
  local max_w, max_h, min_h = room(player)
  CAM_W, CAM_H = math.max(CAM_MIN_W, math.min(CAM_W, max_w)), math.max(min_h, math.min(CAM_H, max_h))
  if not stdwin then
    cam.style.width = CAM_W
    cam.style.height = CAM_H
  end
  cam.visible = false
  local empty = right.add({ type = "label", name = "bpgen_empty", caption = T("empty") })
  if not stdwin then
    empty.style.width = CAM_W
    empty.style.height = CAM_H
  end
  empty.style.horizontal_align = "center"
  empty.style.vertical_align = "center"
  -- (not docked: the preview's tools and the blueprint's actions in rows under the preview)
  local under = right.add({ type = "flow", direction = "vertical", visible = not out })
  under.style.padding = 8
  under.style.vertical_spacing = 6
  -- the preview's tools: a rail out of the window's right edge, as the modes' on the left (fse-std's layouts), else a
  -- row under the preview
  local vrail = out and ui.attach(frame, "right", { size = { RAIL_W, TOOLS_H }, along = 40, overlap = 6, role = "inset",
    direction = "vertical", padding = 4, name = NAME .. "__tools" })
  local tools
  if vrail then  -- (a flow of its own: a frame's spacing is the theme's, too wide for the rail's height)
    tools = vrail.add({ type = "flow", direction = "vertical" })
    tools.style.vertical_spacing = 4
  else
    tools = under.add({ type = "flow", direction = "horizontal" })
  end
  -- (on the rail: buttons as wide as the mode tabs, the arrows four in a row)
  local wide = vrail and { width = RAIL_W - 8, height = 30 } or nil
  local function tool(spec, label, tweak)
    if vrail then spec.caption = label end
    return ui.add(tools, spec, vrail and "rtab" or "tool", vrail and (tweak or wide) or tweak)
  end
  tool({ type = "button", caption = "+", tags = { bpgen = "zoom", f = 1.25 }, tooltip = T("zoom-in") }, T("zoom-in"))
  tool({ type = "button", caption = "−", tags = { bpgen = "zoom", f = 0.8 }, tooltip = T("zoom-out") }, T("zoom-out"))
  tool({ type = "button", caption = T("fit"), tags = { bpgen = "fit" }, tooltip = T("fit-tip") }, T("fit"),
    not vrail and { width = 40 } or nil)
  local arrows = vrail and tools.add({ type = "flow", direction = "horizontal" }) or tools
  if vrail then arrows.style.horizontal_spacing = 2 end
  for _, a in ipairs({ { "◀", -1, 0 }, { "▲", 0, -1 }, { "▼", 0, 1 }, { "▶", 1, 0 } }) do
    ui.add(arrows, { type = "button", caption = a[1], tags = { bpgen = "pan", dx = a[2], dy = a[3] },
      tooltip = T("pan-tip") }, vrail and "rtab" or "tool", vrail and { width = math.floor((RAIL_W - 14) / 4), height = 30,
      padding = 0 } or nil)
  end
  tools.add({ type = "button", name = "bpgen_grip", caption = "⇲", style = "tool_button", tags = { bpgen = "size" },
    raise_hover_events = true, tooltip = safe.has("std") and T("grip-tip") or T("grip-tip-plain") })
  if not vrail then tools.add({ type = "empty-widget" }).style.horizontally_stretchable = true end
  -- (Test run: starts a run and opens its drawer; while it runs, the button says how far it got)
  ui.add(tools, { type = "button", name = "bpgen_test", caption = T("test-run"), tags = { bpgen = "test" }, enabled = false,
    tooltip = T("test-run-tip") },
    vrail and "rtab" or "button", vrail and { width = RAIL_W - 8, height = 30, top_margin = 12 } or nil)
  -- the test run, live: the rate it reaches (a bar red to green by the share of the expected), a chart of it; in a
  -- drawer out of the window's right edge under the preview's tools (docked), else at the top of the result's area
  local fh = stdwin and select(2, stdwin.size(player, NAME))
  local test_h = math.max(220, math.min(TEST_H, (fh or 600) - 40 - TOOLS_H - 12 - 12))
  local drawer = out and ui.attach(frame, "right", { size = { TEST_W, test_h }, along = 40 + TOOLS_H + 12, overlap = 8,
    role = "toast", direction = "vertical", padding = 8, name = NAME .. "__test" })
  local tb
  if drawer then
    drawer.visible = false
    tb = drawer.add({ type = "flow", name = "bpgen_test_box", direction = "vertical" })
    tb.style.vertical_spacing = 6
    tb.style.horizontally_stretchable = true
  else
    tb = section(info, nil, nil, "bpgen_test_box")
    tb.visible = false
  end
  local th = tb.add({ type = "flow", direction = "horizontal" })  -- (its heading, the fast-forward beside it)
  th.style.vertical_align = "center"
  th.style.horizontally_stretchable = true
  ui.add(th, { type = "label", caption = T("test-run") }, "caption")
  th.add({ type = "empty-widget" }).style.horizontally_stretchable = true
  ui.add(th, { type = "button", name = "bpgen_test_speed", caption = "×1", tags = { bpgen = "test_speed", speed = 1 },
    visible = false, tooltip = T("test-speed-tip") },
    "tool", { width = 44 })
  if drawer then
    ui.add(th, { type = "button", caption = "×", tags = { bpgen = "test_close" }, tooltip = T("test-close-tip") }, "tool",
      { width = 28 })
  end
  local tbar = ui.progress(tb, 0, { caption = T("measured"), text = "", auto = true })
  tbar.name = "bpgen_test_bar"
  tbar.parent.style.horizontally_stretchable = true
  tb.add({ type = "flow", name = "bpgen_test_graph", direction = "vertical" })
  ui.add(tb, { type = "label", name = "bpgen_test_status" }, "dim", { single_line = false, maximal_width = TEST_W - 30 })
  -- the blueprint's actions: a toolbar hanging below the window (fse-std's dock), else a row under the preview
  local dock = out and ui.theme.dock(frame, { size = { DOCK_W, DOCK_H } }) or nil
  if dock then
    tools = dock.add({ type = "flow", direction = "horizontal" })
    tools.style.horizontally_stretchable = true
    tools.style.horizontal_align = "center"
    tools.style.vertical_align = "center"
  else
    tools = under.add({ type = "flow", direction = "horizontal" })
  end
  local small = { minimal_width = 0, left_padding = 8, right_padding = 8 }
  -- (a plain button until there's a blueprint: a disabled main action's faded colours hardly read on some themes)
  ui.add(tools, { type = "button", name = "bpgen_cursor", caption = T("to-cursor"), tags = { bpgen = "cursor" },
    enabled = false }, "button")
  ui.add(tools, { type = "button", name = "bpgen_copy", caption = T("copy"), tags = { bpgen = "copy" }, enabled = false,
    tooltip = T("copy-tip") }, "button", small)
  -- (the dock's buttons as tight as Copy: all of them fit its width in the wide monospace fonts too)
  ui.add(tools, { type = "button", name = "bpgen_place", caption = T("place-near"), tags = { bpgen = "place" },
    enabled = false, tooltip = T("place-near-tip") }, "button", small)
  ui.add(tools, { type = "button", name = "bpgen_place_base", caption = T("place-base"), tags = { bpgen = "place_base" },
    enabled = false, tooltip = T("place-base-tip") }, "button", small)
  ui.add(tools, { type = "button", name = "bpgen_sandbox", caption = T("sandbox"), tags = { bpgen = "sandbox" },
    enabled = false, tooltip = T("sandbox-tip") }, "button", small)
  ui.add(tools, { type = "button", name = "bpgen_base_spot", caption = T("pick-spot"), visible = false,
    tags = { bpgen = "base_spot" }, tooltip = T("pick-spot-tip") }, "button", small)
  for _, a in ipairs({ { "left", "◀", -1, 0 }, { "up", "▲", 0, -1 }, { "down", "▼", 0, 1 }, { "right", "▶", 1, 0 } }) do
    tools.add({ type = "button", name = "bpgen_nudge_" .. a[1], caption = a[2], style = "tool_button", visible = false,
      tags = { bpgen = "nudge", dx = a[3], dy = a[4] },
      tooltip = T("nudge-tip", T("dir-" .. a[1])) })
  end
  tools.add({ type = "button", name = "bpgen_base_rotate", caption = "⟳", style = "tool_button", visible = false,
    tags = { bpgen = "base_rotate" }, tooltip = T("rotate-tip") })
  ui.add(tools, { type = "button", name = "bpgen_base_redo", caption = T("redo"), visible = false,
    tags = { bpgen = "base_redo" }, tooltip = T("redo-tip") }, "button", small)
  ui.add(tools, { type = "button", caption = T("undo"), tags = { bpgen = "unplace" }, tooltip = T("undo-tip") }, "button",
    small)
  ui.add(tools, { type = "button", caption = T("history"), tags = { bpgen = "history" }, tooltip = T("history-tip") },
    "button", small)
  -- the plan's result: its numbers as tiles (three a row), badges for the rest, its notes, the book's prints, the
  -- details folded
  local result = section(info, T("result"), nil, "bpgen_result")
  result.visible = false
  local tiles = result.add({ type = "table", name = "bpgen_tiles", column_count = 3, visible = false })
  tiles.style.horizontal_spacing, tiles.style.vertical_spacing = 4, 4
  result.add({ type = "flow", name = "bpgen_badges", direction = "vertical" }).style.vertical_spacing = 4
  ui.add(result, { type = "label", name = "bpgen_notes" }, "dim", { single_line = false, maximal_width = 266 })
  result.add({ type = "drop-down", name = "bpgen_section", items = {}, visible = false, tags = { bpgen_section = true },
    tooltip = T("section-tip") })
  local dets = ui.fold(result, T("details"), false)
  ui.add(dets, { type = "label", name = "bpgen_stats" }, "text", { single_line = false, maximal_width = 256 })
  -- (after a plan: can your save build it, and what modules would do; or the history list)
  local extra = info.add({ type = "flow", name = "bpgen_extra", direction = "vertical" })
  extra.style.maximal_width = 290
  if stdwin then
    -- (the library keeps the window's size and place: the preview stretches into what the window leaves it)
    find(frame, "bpgen_grip").visible = false
    for _, n in ipairs({ "bpgen_cam", "bpgen_empty" }) do
      local el = find(frame, n)
      el.style.minimal_width, el.style.minimal_height = CAM_MIN_W, select(3, room(player))
      el.style.horizontally_stretchable, el.style.vertically_stretchable = true, true
    end
    body.style.vertically_stretchable, body.style.horizontally_stretchable = true, true
    -- (a flow ignores its stretch here: a natural width past the screen's makes it fill the window, which caps it)
    body.style.natural_width = math.floor(player.display_resolution.width / player.display_scale)
    right.style.vertically_stretchable, right.style.horizontally_stretchable = true, true
    local ww, wh = wanted_size(player)
    apply_size(player, frame, ww, wh, true)
  else
    frame.force_auto_center()
  end

  fit_panel(left)
  if not as_web then adopt(player) end  -- (the game's styles it was built with: each player's theme)
  if dnd then  -- (History's rows dropped on the preview or the Recipe slot)
    dnd.droppable(cam, "bpgen_history")
    dnd.droppable(find(frame, "bpgen_recipe"), "bpgen_history")
  end
  badges_of(frame)

  -- filled in (a hovered machine; tests: a mode, mall products, science per minute, options shown)
  set_mode(frame, prefill.mode or "line")
  for _, r in ipairs(prefill.products or {}) do
    local t = find(frame, "bpgen_products")
    t.children[#t.children].elem_value = r
    t.add({ type = "choose-elem-button", elem_type = "recipe", tags = { bpgen_product = true },
      elem_filters = { { filter = "hidden", invert = true } } })
  end
  if prefill.spm then find(frame, "bpgen_spm").text = tostring(prefill.spm) end
  if prefill.plates then find(frame, "bpgen_base_plates").state = true end
  if prefill.bus_belt then find(frame, "bpgen_bus_belt").elem_value = prefill.bus_belt end
  if prefill.bus_length then find(frame, "bpgen_bus_length").text = tostring(prefill.bus_length) end
  if prefill.add then find(frame, "bpgen_base_add").state = true end
  if prefill.mall == false then find(frame, "bpgen_base_mall").state = false end
  if prefill.feed then find(frame, "bpgen_feed").selected_index = ({ belts = 1, robots = 2, bus = 3 })[prefill.feed] or 1 end
  if prefill.more then
    find(frame, "bpgen_more_toggle").state = true
    find(frame, "bpgen_more").visible = true
  end
  if prefill.recipe then
    find(frame, "bpgen_recipe").elem_value = prefill.recipe
    set_machine_filter(frame, prefill.recipe)
    local m = prefill.machine or best_machine(player, prefill.recipe)
    if m then find(frame, "bpgen_machine").elem_value = { name = m, quality = prefill.machine_quality or "normal" } end
    local mod = prefill.modules and prefill.modules[1]
    if mod then find(frame, "bpgen_module").elem_value = { name = mod.name, quality = mod.quality or "normal" } end
    find(frame, "bpgen_make").clear()  -- (its ingredients, to make here or bring on a belt)
    find(frame, "bpgen_make").add({ type = "label", caption = "..." })
    ctx.api(player, "recipe_tree", { recipe = prefill.recipe, machine = m }, "tree")
    find(frame, "bpgen_also").clear()
    find(frame, "bpgen_also").add({ type = "label", caption = "..." })
    ctx.api(player, "siblings", { recipe = prefill.recipe, machine = m }, "also")
  end
  if as_web and not web.attach(player, frame, not prefill.rebuilt) then  -- (the shell didn't open: the Lua window)
    return M.open(player, prefill)
  end
  M.restrict(player)
  return frame
end

local function mode_of(frame)
  return (find(frame, "bpgen_tabs").tags or {}).mode or "line"
end

local function value(frame, name)
  local el = find(frame, name)
  return el and el.elem_value
end

-- the unlocked machines making every one of these recipes, fastest first (the mall tries them in turn)
local function machines_for_all(player, recipes)
  local list = {}
  for _, name in ipairs(unlocked(player).machines) do
    local p = prototypes.entity[name]
    local ok = p.crafting_categories ~= nil and not powerless(p)
    for _, r in ipairs(recipes) do
      local cat = prototypes.recipe[r] and prototypes.recipe[r].category
      if not (ok and cat and p.crafting_categories[cat]) then ok = false end
    end
    if ok then list[#list + 1] = name end
  end
  table.sort(list, function(a, b) return speed(prototypes.entity[a]) > speed(prototypes.entity[b]) end)
  return list
end

local function best_of_type(player, t, category)
  local best, best_speed
  for _, name in ipairs(ctx.unlocked(craftable(player.force), { t })) do
    local p = prototypes.entity[name]
    if not powerless(p) and (not category or (p.crafting_categories and p.crafting_categories[category])) then
      local sp = p.crafting_categories and speed(p) or (p.get_researching_speed and p.get_researching_speed() or 1)
      if not best or sp > best_speed then best, best_speed = name, sp end
    end
  end
  return best
end

local function request(player, frame)
  local force = player.force
  local u = unlocked(player)
  local req = { tick = game.tick, surface = player.surface.name, seed = player.surface.map_gen_settings.seed,
                bonuses = ctx.bonuses(force),
                inserters = u.inserters, belts = u.belts, poles = u.poles, belt = value(frame, "bpgen_belt") }
  local mode = mode_of(frame)
  if mode == "mall" then
    local products = {}
    for _, b in pairs(find(frame, "bpgen_products").children) do
      if b.elem_value then products[#products + 1] = b.elem_value end
    end
    if #products == 0 then return nil, T("err-pick-products") end
    local picked = value(frame, "bpgen_mall_machine")
    local candidates = picked and { picked } or machines_for_all(player, products)
    if #candidates == 0 then return nil, T("err-no-mall-machine") end
    req.params = { mode = "mall", products = products, machine = candidates[1], machines_try = candidates,
      chest = value(frame, "bpgen_chest") or (prototypes.entity["steel-chest"] and "steel-chest") or "wooden-chest",
      feed = find(frame, "bpgen_mall_feed").selected_index == 2 and "robots" or "belt",
      layout = find(frame, "bpgen_mall_feed").selected_index >= 6 and "sushi"
        or find(frame, "bpgen_mall_feed").selected_index >= 3 and "grid" or nil,
      bus_feed = (find(frame, "bpgen_mall_feed").selected_index == 4 or find(frame, "bpgen_mall_feed").selected_index == 7)
        or nil,
      add_to = find(frame, "bpgen_mall_feed").selected_index == 5 and player.cursor_stack
        and player.cursor_stack.valid_for_read and player.cursor_stack.export_stack() or nil,
      wants_base = find(frame, "bpgen_mall_feed").selected_index == 5 or nil,
      origin = { x = player.position.x, y = player.position.y },
      buffer_crafts = tonumber(find(frame, "bpgen_mall_buffer").text) or 5,
      chest_limit = tonumber(find(frame, "bpgen_chest_limit").text) or 4,
      stock = tonumber(find(frame, "bpgen_mall_stock").text),
      belt = req.belt }
    if req.params.wants_base and not req.params.add_to then
      return nil, T("err-hold-base-mall")
    end
    return req
  elseif mode == "extend" then
    local item = value(frame, "bpgen_ext_item")
    if not item then return nil, T("err-pick-item") end
    req.params = { mode = "extend", item = item, rate_per_min = tonumber(find(frame, "bpgen_ext_rate").text) or 30,
      assembler = best_of_type(player, "assembling-machine", "crafting"), furnace = best_of_type(player, "furnace", "smelting"),
      belt = req.belt or u.belts[#u.belts], bus = find(frame, "bpgen_ext_bus").state and "auto" or "off" }
    return req
  elseif mode == "ship" then
    local dd = find(frame, "bpgen_ship_orbit")
    req.params = { mode = "ship", thrusters = tonumber(find(frame, "bpgen_ship_thrusters").text) or 2,
      turrets = tonumber(find(frame, "bpgen_ship_turrets").text) or 6,
      collectors = tonumber(find(frame, "bpgen_ship_collectors").text) or 4,
      throttle = (function()
        local t = find(frame, "bpgen_ship_throttle")
        return (t.tags.values or {})[t.selected_index] or -1
      end)(),
      location = (dd.tags.orbits or {})[dd.selected_index] or "nauvis", belt = req.belt or u.belts[1] }
    return req
  elseif mode == "busdesign" then
    local list = {}
    for i, p in ipairs(patch_areas[player.index] or {}) do
      local a = p.area
      list[i] = { a.left_top.x, a.left_top.y, a.right_bottom.x, a.right_bottom.y }
    end
    if #list == 0 then return nil, T("err-pick-patches") end
    local dir = BUS_DIRS[find(frame, "bpgen_bus_dir").selected_index] or "north"
    local shift = bus_shift[player.index] or 0  -- (across the bus's flow)
    local across = (dir == "north" or dir == "south") and { shift, 0 } or { 0, shift }
    req.params = { mode = "busdesign", patches = list,
      origin = { x = player.position.x + across[1], y = player.position.y + across[2] },
      direction = BUS_DIRS[find(frame, "bpgen_bus_dir").selected_index] or "north",
      group = tonumber(find(frame, "bpgen_bus_group").text) or 4, gap = tonumber(find(frame, "bpgen_bus_gap").text) or 4,
      length = tonumber(find(frame, "bpgen_bus_length").text) or 40, balance = find(frame, "bpgen_bus_balance").state,
      wood = find(frame, "bpgen_bus_wood").state, add_to_bus = find(frame, "bpgen_bus_add").state or nil,
      drill = value(frame, "bpgen_bus_drill"), furnace = value(frame, "bpgen_bus_furnace"),
      belt = value(frame, "bpgen_bus_belt") or u.belts[#u.belts] }
    return req
  elseif mode == "base" then
    local addons = {}
    if find(frame, "bpgen_base_military").state then addons[#addons + 1] = "military" end
    if find(frame, "bpgen_base_chemical").state then addons[#addons + 1] = "chemical" end
    req.params = { mode = "base", spm = tonumber(find(frame, "bpgen_spm").text) or 30, addons = addons,
      -- (empty pickers: bpgen's starter defaults, not the fastest thing unlocked: a late-game lab takes dozens of
      -- science packs, a "starter base" for it isn't one)
      assembler = value(frame, "bpgen_assembler"), furnace = value(frame, "bpgen_furnace"),
      lab = value(frame, "bpgen_lab"), mall = find(frame, "bpgen_base_mall").state,
      layout = find(frame, "bpgen_base_bus").state and "bus" or "compact",
      belt = req.belt or u.belts[#u.belts], productivity = 0 }
    local plates, add = find(frame, "bpgen_base_plates").state, find(frame, "bpgen_base_add").state
    if plates or add then req.params.layout = "bus" end  -- (the C and the tiers need the main bus)
    req.params.plates = plates or nil
    req.params.fit_bus = plates and not add and find(frame, "bpgen_base_fit").state or nil
    if add then
      local cs = player.cursor_stack
      if not (cs and cs.valid_for_read and (cs.is_blueprint and cs.is_blueprint_setup() or cs.is_blueprint_book)) then
        return nil, T("err-hold-base-add")
      end
      req.params.add_to = cs.export_stack()
    end
    if find(frame, "bpgen_base_import").state and not add then
      local cs = player.cursor_stack
      if not (cs and cs.valid_for_read and cs.is_blueprint and cs.is_blueprint_setup()) then
        return nil, T("err-hold-base-import")
      end
      req.params.import = cs.export_stack()
    end
    return req
  end
  local recipe = value(frame, "bpgen_recipe")
  if not recipe then return nil, T("err-pick-recipe") end
  local machine = value(frame, "bpgen_machine")
  if not machine then
    local m = best_machine(player, recipe)
    if not m then return nil, T("err-no-machine") end
    machine = { name = m, quality = "normal" }
    find(frame, "bpgen_machine").elem_value = machine
  end
  local modules = {}
  local mod = value(frame, "bpgen_module")
  local slots = prototypes.entity[machine.name].module_inventory_size or 0
  if mod and slots > 0 then modules[1] = { name = mod.name, quality = mod.quality or "normal", count = slots } end
  req.recipe, req.recipe_quality = recipe, "normal"
  req.recipe_productivity = force.recipes[recipe] and force.recipes[recipe].productivity_bonus or 0
  req.machine, req.machine_quality, req.modules = machine.name, machine.quality or "normal", modules
  req.rate_per_min = tonumber(find(frame, "bpgen_rate").text)
  req.per_row = tonumber(find(frame, "bpgen_per_row").text)
  local params = { near = value(frame, "bpgen_near"), far = value(frame, "bpgen_far"), out = value(frame, "bpgen_out"),
                   feed = find(frame, "bpgen_feed").selected_index == 2 and "robots" or nil }
  if find(frame, "bpgen_feed").selected_index == 3 then
    params.bus_feed, params.origin = true, { x = player.position.x, y = player.position.y }
  end
  local beacon, bmod = value(frame, "bpgen_beacon"), value(frame, "bpgen_beacon_module")
  if beacon and bmod then
    local bslots = prototypes.entity[beacon.name].module_inventory_size or 0
    params.beacon, params.beacon_quality = beacon.name, beacon.quality or "normal"
    params.beacon_modules = string.format("%s@%s:%d", bmod.name, bmod.quality or "normal", bslots)
  end
  local make = {}
  for _, r in pairs(find(frame, "bpgen_make").children) do
    local on, pick = r.children[1], r.children[2]
    if on and on.type == "checkbox" and on.state and pick and pick.type == "choose-elem-button" and pick.elem_value then
      local m = best_machine(player, pick.elem_value)
      if m then make[on.tags.item] = { recipe = pick.elem_value, machine = m } end
    end
  end
  if next(make) then params.make = make end
  local also = {}
  local function ticked(box)
    for _, r in pairs(box.children) do
      if r.name == "bpgen_also_near" then ticked(r)
      elseif r.type == "flow" and #r.children > 0 then
        local on, rate = r.children[1], r.children[#r.children]
        if on.type == "checkbox" and on.state and on.tags.recipe then
          also[on.tags.item] = { recipe = on.tags.recipe, rate_per_min = rate.type == "textfield" and tonumber(rate.text) or nil }
        end
      end
    end
  end
  ticked(find(frame, "bpgen_also"))
  if next(also) then params.also = also end
  req.params = params
  return req
end

--- an absolute plan (next to the base) as ghosts where it belongs, its taps replacing the belts they cut into,
--- as a Ctrl+Shift paste
--- (tests) the last plan's absolute placement: its box, and what it is
function M.last_absolute(player_index)
  local r = results[player_index]
  return r and r.res.absolute
end

function M.place_absolute(player)
  local frame = player.gui.screen[NAME]
  local r = results[player.index]
  if not (frame and r and r.res.absolute) then return end
  if r.res.mode == "ship" and not player.surface.platform then
    return status(frame, T("ship-stand-on-platform"))
  end
  local cleared = {}  -- (a refit: the area's old entities go, the mall's ghosts on their spot; Undo unmarks them)
  for _, c in ipairs(r.res.absolute.clear or {}) do
    -- (any quality: find_entity with a bare name only finds normal ones)
    for _, e in pairs(player.surface.find_entities_filtered({ name = c.name, position = c.position })) do
      if not e.to_be_deconstructed() and e.order_deconstruction(player.force, player) then cleared[#cleared + 1] = e end
    end
  end
  local ghosts, _, area, marks = place.place(player, r.blueprint, nil, r.res.absolute.box)
  for _, e in ipairs(cleared) do marks[#marks + 1] = e end
  placed[player.index], unmark[player.index] = ghosts, marks
  if area then place.outline(player, area, r.res.label or T("placed-by-bpgen")) end
  local a = r.res.absolute
  if a.head then  -- (a new bus lane: where to feed it)
    rendering.draw_text({ text = T("feed-here", "[item=" .. a.item .. "]"), surface = player.surface, target = a.head,
      color = { 1, 0.85, 0.2 }, scale = 2, alignment = "center", use_rich_text = true, players = { player },
      time_to_live = 60 * 300 })
    rendering.draw_circle({ color = { 1, 0.85, 0.2 }, radius = 0.7, width = 4, filled = false, target = a.head,
      surface = player.surface, players = { player }, time_to_live = 60 * 300 })
    say(player, T("placed-lane", "[item=" .. a.item .. "]", #ghosts))
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    return
  end
  if r.res.mode == "busdesign" or a.fitted then
    say(player, T(a.fitted and "placed-fitted" or "placed-bus", #ghosts))
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    return
  end
  say(player, T("placed-next", #ghosts, a.taps or 0, { "",
    (a.deliveries or 0) > 0 and T("placed-next-deliveries", a.deliveries) or "",
    (a.new_lanes or 0) > 0 and T("placed-next-lanes", a.new_lanes) or "" }))
  ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
end

--- "Try in sandbox": the plan built in a lab of the player's (their base round it copied in, kept fed) and the player
--- taken there; an absolute plan at its box, any other at a free spot near them (as Place near me)
local function try_sandbox(player)
  local frame = player.gui.screen[NAME]
  local r = results[player.index]
  if not (frame and r and r.blueprint) then return end
  local why = sandbox.cant(player)
  if why then return say(player, T(why)) end
  local plan = { blueprint = r.blueprint }
  local a = r.res.absolute
  if a then
    local b = a.box
    plan.box, plan.clear, plan.area = b, a.clear, { b[1], b[2], b[1] + b[3], b[2] + b[4] }
  elseif r.where then
    local c = place.find_spot(player, r.w, r.h)
    if not c then return status(frame, T("no-free-spot", math.ceil(r.w), math.ceil(r.h))) end
    plan.center, plan.area = c, { c.x - r.w / 2, c.y - r.h / 2, c.x + r.w / 2, c.y + r.h / 2 }
  else
    return
  end
  sandbox.enter(player, plan)
end

local SANDBOX_CARD = "bpgen_sandbox_card"
--- the card shown in the sandbox (the window hidden meanwhile): Place in base, Discard
local function sandbox_card(player, on)
  local screen = player.gui.screen
  if screen[SANDBOX_CARD] then screen[SANDBOX_CARD].destroy() end
  if not on then return end
  local card = screen.add({ type = "frame", name = SANDBOX_CARD, direction = "vertical" })
  card.style.padding = 10
  card.style.width = 380
  ui.add(card, { type = "label", caption = T("sandbox-title") }, "caption")
  local text = ui.add(card, { type = "label", caption = T("sandbox-text") }, "text")
  text.style.single_line = false
  text.style.maximal_width = 356
  local row = card.add({ type = "flow", direction = "horizontal" })
  row.style.top_margin = 6
  row.style.vertical_align = "center"
  row.add({ type = "empty-widget" }).style.horizontally_stretchable = true
  ui.add(row, { type = "button", name = "bpgen_sandbox_place", caption = T("sandbox-place"),
    tags = { bpgen = "sandbox_place" }, tooltip = T("sandbox-place-tip") }, "ok")
  ui.add(row, { type = "button", name = "bpgen_sandbox_discard", caption = T("sandbox-discard"),
    tags = { bpgen = "sandbox_discard" }, tooltip = T("sandbox-discard-tip") }, "button")
  ui.adopt(card)
  local scale = player.display_scale * ui.scale_of(card)
  card.location = { math.max(0, (player.display_resolution.width - 380 * scale) / 2), math.floor(60 * scale) }
end

--- belt_speed a belt-like entity needs to carry `per_s` items a second on both lanes
local function speed_for(per_s) return per_s / 480 end

--- name's upgrade (next_upgrade, again and again) that is at least `speed` fast, or nil
local function upgraded(name, speed)
  local p = prototypes.entity[name]
  while p and (p.belt_speed or 0) < speed - 1e-9 do p = p.next_upgrade end
  return p and p.name
end

--- an overdrawn bus lane (from the plan's absolute.upgrades) marked for upgrade: the slowest unlocked belt that
--- carries what it needs, else the fastest; bpgen's own ghosts on it swapped to that tier
function M.upgrade_lane(player, u)
  local frame = player.gui.screen[NAME]
  local belts = {}
  for _, b in ipairs(unlocked(player).belts) do belts[#belts + 1] = prototypes.entity[b] end
  table.sort(belts, function(a, b) return a.belt_speed < b.belt_speed end)
  local target
  for _, b in ipairs(belts) do
    if b.belt_speed >= speed_for(u.need) - 1e-9 then target = b break end
  end
  target = target or belts[#belts]
  local now = prototypes.entity[u.belt]
  if not target or (now and target.belt_speed <= now.belt_speed) then
    return status(frame, T("no-faster-belt"))
  end
  local area = u.axis == 0 and { { u.at + 0.1, u.lo + 0.1 }, { u.at + 0.9, u.hi + 0.9 } }
    or { { u.lo + 0.1, u.at + 0.1 }, { u.hi + 0.9, u.at + 0.9 } }
  local s, n = player.surface, 0
  local mine = placed[player.index] or {}
  for _, e in pairs(s.find_entities_filtered({ area = area, force = player.force,
      type = { "transport-belt", "underground-belt", "splitter", "entity-ghost" } })) do
    local ghost = e.type == "entity-ghost"
    local kind = ghost and e.ghost_type or e.type
    local to = (kind == "transport-belt" or kind == "underground-belt" or kind == "splitter")
      and upgraded(ghost and e.ghost_name or e.name, target.belt_speed)
    if to and to ~= (ghost and e.ghost_name or e.name) then
      if ghost then  -- (a ghost can't be ordered to upgrade: the same ghost, of the faster tier)
        local spec = { name = "entity-ghost", inner_name = to, position = e.position, direction = e.direction,
          force = player.force, player = player }
        if kind == "underground-belt" then spec.type = e.belt_to_ground_type end
        e.destroy()
        local g = s.create_entity(spec)
        if g then mine[#mine + 1] = g n = n + 1 end
      elseif e.order_upgrade({ target = to, force = player.force, player = player }) then
        n = n + 1
      end
    end
  end
  placed[player.index] = mine
  say(player, T("upgrade-marked", n, "[entity=" .. target.name .. "]"))
end

function M.plan(player)
  local frame = player.gui.screen[NAME]
  if not frame then return end
  local mode = mode_of(frame)
  if mode == "refit" or mode == "move" then  -- (what to plan comes from the area dragged over)
    return M.click(player.index, { bpgen = mode })
  end
  local req, err = request(player, frame)
  if not req then return status(frame, err) end
  local ok, why = ctx.start(player, req)
  status(frame, ok and "" or T("cant-plan", tostring(why)))
  stale(player, false)
  if ok then
    M.on_progress(player, { fraction = 0, step = T("starting") })
    glow(player, true)
  end
end

--- a section of the base to move (the move tool): its blueprint (settings, wires) and where it is, to bpgen, which
--- places it near the player with its belts in and out run to the base again (see ingame.move)
--- the pick-spot tool clicked (or dragged): the last plan placed with its middle there, on that side of the bus
function M.spot(player, area)
  local frame = player.gui.screen[NAME]
  if not (frame and results[player.index]) then return say(player, T("plan-first")) end
  local seed = { x = (area.left_top.x + area.right_bottom.x) / 2, y = (area.left_top.y + area.right_bottom.y) / 2 }
  tried[player.index] = nil
  local sec = find(frame, "bpgen_section")
  local ok, why = ctx.place_base(player, { bus = find(frame, "bpgen_ext_bus").state and "auto" or "off", seed = seed,
    spot = true, turn = turns[player.index], section = sec.visible and sec.items[sec.selected_index] or nil })
  status(frame, ok and T("placing-clicked") or T("cant", tostring(why)))
end

function M.move(player, area)
  if not player.gui.screen[NAME] then M.open(player, { mode = "move" }) end
  local frame = player.gui.screen[NAME]
  set_mode(frame, "move")
  local inv = game.create_inventory(1)
  local stack = inv[1]
  stack.set_stack({ name = "blueprint" })
  local map = stack.create_blueprint({ surface = player.surface, force = player.force, area = area,
    include_modules = true, include_station_names = true })
  if not stack.is_blueprint_setup() then
    inv.destroy()
    return status(frame, T("nothing-to-move"))
  end
  local offset
  for _, be in pairs(stack.get_blueprint_entities() or {}) do  -- (a blueprint position plus offset: the world's)
    local ent = map[be.entity_number]
    if ent and ent.valid then
      offset = { ent.position.x - be.position.x, ent.position.y - be.position.y }
      break
    end
  end
  local bp = stack.export_stack()
  inv.destroy()
  if not offset then return status(frame, T("nothing-to-move")) end
  local a = area
  local ok, why = ctx.start(player, { tick = game.tick, surface = player.surface.name, params = { mode = "move",
    move = { area = { math.floor(a.left_top.x), math.floor(a.left_top.y), math.ceil(a.right_bottom.x),
                      math.ceil(a.right_bottom.y) }, blueprint = bp, offset = offset },
    seed = { x = player.position.x, y = player.position.y } } })
  status(frame, ok and T("moving-section") or T("cant-plan", tostring(why)))
end

--- an area picked with the refit tool: its assembling machines' recipes fill the mall's products, then it plans
--- them as a compact mall in the area's place (see ingame._refit)
--- the area as a blueprint string (nil: nothing in it)
local function area_blueprint(player, area)
  local inv = game.create_inventory(1)
  inv[1].set_stack({ name = "blueprint" })
  inv[1].create_blueprint({ surface = player.surface, force = player.force, area = area, include_modules = true })
  local bp = inv[1].is_blueprint_setup() and inv[1].export_stack() or nil
  inv.destroy()
  return bp
end

function M.refit(player, area)
  if not player.gui.screen[NAME] then M.open(player, { mode = "mall" }) end
  local frame = player.gui.screen[NAME]
  if find(frame, "bpgen_refit_as").selected_index == 2 then  -- (a base on a bus: the science the area makes)
    local bp = area_blueprint(player, area)
    if not bp then return status(frame, T("nothing-to-refit")) end
    local u = unlocked(player)
    local a = area
    local ok, why = ctx.start(player, { tick = game.tick, surface = player.surface.name,
      seed = player.surface.map_gen_settings.seed, bonuses = ctx.bonuses(player.force), inserters = u.inserters,
      belts = u.belts, poles = u.poles, belt = u.belts[#u.belts],
      params = { mode = "base", import = bp, layout = "bus", belt = u.belts[#u.belts], productivity = 0,
        refit = { area = { a.left_top.x, a.left_top.y, a.right_bottom.x, a.right_bottom.y } } } })
    return status(frame, ok and T("planning-refit-base") or T("cant-plan", tostring(why)))
  end
  set_mode(frame, "mall")
  local count, machines, order = {}, {}, {}
  for _, e in pairs(player.surface.find_entities_filtered({ area = area, type = "assembling-machine", force = player.force })) do
    local r = e.get_recipe()
    if r then
      if not count[r.name] then order[#order + 1] = r.name end
      count[r.name] = (count[r.name] or 0) + 1
      machines[e.name] = (machines[e.name] or 0) + 1
    end
  end
  if #order == 0 then return status(frame, T("no-machines-in-area")) end
  local t = find(frame, "bpgen_products")
  t.clear()
  for _, r in ipairs(order) do
    t.add({ type = "choose-elem-button", elem_type = "recipe", tags = { bpgen_product = true },
      elem_filters = { { filter = "hidden", invert = true } } }).elem_value = r
  end
  t.add({ type = "choose-elem-button", elem_type = "recipe", tags = { bpgen_product = true },
    elem_filters = { { filter = "hidden", invert = true } } })
  M.restrict(player)
  local req, err = request(player, frame)
  if not req then return status(frame, err) end
  -- (the area's own machine first, when it makes every product)
  local most
  for name, n in pairs(machines) do if not most or n > machines[most] then most = name end end
  local try = req.params.machines_try or {}
  for i, m in ipairs(try) do
    if m == most then table.remove(try, i) table.insert(try, 1, m) break end
  end
  req.params.machine = try[1] or req.params.machine
  local a = area
  req.params.refit = { area = { a.left_top.x, a.left_top.y, a.right_bottom.x, a.right_bottom.y },
                       add = find(frame, "bpgen_refit_add").state,
                       layout = find(frame, "bpgen_refit_layout").selected_index == 2 and "grid" or nil }
  local ok, why = ctx.start(player, req)
  status(frame, ok and T("planning-refit") or T("cant-plan", tostring(why)))
end

local function sig(name)
  if prototypes.item[name] then return "[item=" .. name .. "]" end
  if prototypes.fluid[name] then return "[fluid=" .. name .. "]" end
  return name
end

--- an item's icon and its name
local function named(name)
  local p = prototypes.item[name] or prototypes.fluid[name]
  return p and { "", sig(name), " ", p.localised_name } or name
end

--- a row note's colour: grey, or green when `made` (themed: "dim" with tweaks, so it lasts through a theme change)
local function note_color(l, made)
  if ui.themed then
    ui.theme.apply(l, "dim", { font = "default-small", font_color = made and { 0.5, 0.9, 0.5 } or nil })
  else
    l.style.font_color = made and { 0.5, 0.9, 0.5 } or { 0.6, 0.6, 0.6 }
  end
end

--- a list row's right-hand note (e.g. "on belt"), small and grey, pushed to the row's end
local function row_note(row, caption, name)
  row.add({ type = "empty-widget" }).style.horizontally_stretchable = true
  local l = row.add({ type = "label", name = name, caption = caption })
  l.style.font = "default-small"
  note_color(l, false)
  return l
end

--- a plan's answer (from control.lua's job loop)
--- the plan getting on: {fraction 0..1, step = what it does now, lines = what it did} (from bpgen.ingame:progress)
function M.on_progress(player, p)
  local frame = player.gui.screen[NAME]
  local box = frame and find(frame, "bpgen_progress")
  if not box then return end
  web.dirty(player)
  box.visible = true
  local bar = find(box, "bpgen_progress_bar")
  if bar then ui.set_progress(bar, p.fraction or 0, p.step or "") end
  local log_ = find(box, "bpgen_log")
  if log_ and p.lines and #p.lines > 0 then
    local all = {}
    for line in tostring(log_.caption):gmatch("[^\n]+") do all[#all + 1] = line end
    for _, line in ipairs(p.lines) do all[#all + 1] = line end
    while #all > 14 do table.remove(all, 1) end
    log_.caption = table.concat(all, "\n")
  end
end

--- a few numbers about the plan under the preview
local function show_tiles(frame, res, w, h)
  local row = find(frame, "bpgen_tiles")
  if not row then return end
  row.clear()
  local a, sm = res.absolute or {}, res.summary or {}
  local function tile(v, caption)
    if v then ui.stat(row, type(v) == "table" and v or tostring(v), T(caption), { horizontally_stretchable = false,
      minimal_width = 84 }) end
  end
  if a.box then tile(a.box[3] .. " × " .. a.box[4], "tile-size")
  elseif type(w) == "number" then tile(math.floor(w) .. " × " .. math.floor(h), "tile-size") end
  tile(sm.machines or (res.mall and res.mall.machines), "tile-machines")
  if a.taps then tile(a.taps, "tile-taps") end
  if a.inputs and #a.inputs > 0 then tile(#a.inputs, "tile-by-hand") end
  if res.expected then  -- (whole numbers from 100 on: three tiles fit the left panel's width)
    local m = res.expected * 60
    tile(T("per-min", string.format(m >= 100 and "%.0f" or "%.1f", m)), "tile-made")
  end
  row.visible = #row.children > 0
end

--- a badge in the result's rows, three a row (they'd run past the preview's width in one)
local function add_badge(frame, text, tip)
  local rows = find(frame, "bpgen_badges")
  if not rows then return nil end
  local row = rows.children[#rows.children]
  if not row or #row.children >= 2 then
    row = rows.add({ type = "flow", direction = "horizontal" })
    row.style.horizontal_spacing = 6
  end
  return ui.badge(row, text, tip)
end

--- the plan's result: what the tiles above don't say (its machine and belt, what it takes, how it was planned) as
--- badges, its notes (the details stay in bpgen_stats)
local function show_result(frame, res, w, h, need)
  local box, badges = find(frame, "bpgen_result"), find(frame, "bpgen_badges")
  if not (box and badges) then return end
  box.visible = true
  badges.clear()
  local function badge(text, tip) add_badge(frame, text, tip) end
  local s = res.summary or {}
  if s.machines and not res.mall and not res.base then
    badge(string.format("[entity=%s] %s", s.machine or "?", sig(s.recipe or "")), T("badge-machine-tip"))
    if s.belt then badge(T("badge-belts", "[entity=" .. s.belt .. "]"), T("badge-belts-tip")) end
  end
  for _, n in ipairs(need or {}) do badge(T("badge-needs", n), T("badge-needs-tip")) end
  if res.mall then badge(T("badge-products", #(res.mall.products or {}))) end
  if res.base then badge(T("badge-spm", tostring(res.base.spm))) end
  if res.seconds then badge(T("badge-planned-in", string.format("%.0f", res.seconds * 1000))) end
  local notes = {}
  for _, n in ipairs(res.notes or {}) do notes[#notes + 1] = { "", "• ", n } end
  find(frame, "bpgen_notes").caption = join(notes, "\n")
end

function M.on_result(player, res)
  local frame = player.gui.screen[NAME]
  if not frame then return false end
  web.dirty(player)
  local prog = find(frame, "bpgen_progress")
  if prog and not res.measure then
    prog.visible = false
    local log_ = find(prog, "bpgen_log")
    if log_ then log_.caption = "" end
  end
  if not res.blueprint then
    if not res.measure then to_sandbox[player.index] = nil end
    if res.measure and not M.measuring(player) then
      -- (inserter setups bpgen hasn't measured: measured here and now, live, then planned again)
      preview_surface()
      bench.calibrate(player.index, res.measure)
      status(frame, T("measuring-setups", #res.measure.cases,
        math.ceil(((res.measure.warmup or 300) + (res.measure.measure or 1800)) / 60)))
    else
      say(player, T("cant-plan-this", tostring(res.error)))
    end
    return true
  end
  local old = bench.find("test", player.index)
  if old then bench.stop(old.id) end
  test_speed_off(frame)  -- (a test run's fast-forward: off with it)
  find(frame, "bpgen_test_status").caption = ""
  local center, w, h, where = build_preview(player, res.blueprint, res.absolute and res.absolute.box)
  local sec = find(frame, "bpgen_section")
  sec.items = res.sections or {}
  sec.visible = res.sections ~= nil and #res.sections > 1
  if sec.visible then sec.selected_index = 1 end
  local cam, empty = find(frame, "bpgen_cam"), find(frame, "bpgen_empty")
  if center then
    local z = fit(player, w, h)
    results[player.index] = { blueprint = res.blueprint, res = res, center = center, zoom = z, where = where, w = w, h = h }
    cam.surface_index = game.surfaces[PREVIEW].index
    cam.position = center
    cam.zoom = z
    cam.visible, empty.visible = true, false
  else
    results[player.index] = { blueprint = res.blueprint, res = res }
    cam.visible, empty.visible = false, true
    empty.caption = T("no-preview", type(w) == "table" and w or tostring(w))
  end
  show_tiles(frame, res, w, h)
  glow(player, false)
  stale(player, false)
  test_shown(player, frame, false)
  M.steps(frame)
  local cursor = find(frame, "bpgen_cursor")
  cursor.enabled = true
  if ui.themed then ui.set_role(cursor, "ok") else cursor.style = "confirm_button" end
  find(frame, "bpgen_copy").enabled = safe.has("std")
  find(frame, "bpgen_place").enabled = where ~= nil
  find(frame, "bpgen_place_base").enabled = res.absolute == nil and (res.mode == "line" or res.mode == "mall"
    or res.mode == "base")
  find(frame, "bpgen_sandbox").enabled = (where ~= nil or res.absolute ~= nil) and res.mode ~= "ship"
  local pb = find(frame, "bpgen_place")
  pb.caption = T(res.absolute and "place-it" or "place-near")
  pb.tooltip = T(res.absolute and "place-it-tip" or "place-near-tip")
  local fixed = res.mode == "lane"  -- (where it is is the point: no arrows; a bus design's move its head sideways)
  for _, n in ipairs({ "left", "up", "down", "right" }) do
    find(frame, "bpgen_nudge_" .. n).visible = res.absolute ~= nil and not fixed
  end
  find(frame, "bpgen_base_spot").visible = res.absolute ~= nil and not fixed or find(frame, "bpgen_place_base").enabled
  find(frame, "bpgen_base_rotate").visible = res.absolute ~= nil and not fixed
  find(frame, "bpgen_base_redo").visible = res.absolute ~= nil and not fixed
  local tb = find(frame, "bpgen_test")
  tb.enabled = res.test ~= nil and where ~= nil
  tb.caption = T("test-run")
  local s = res.summary or {}
  local lines = {}
  if s.machines and not res.mall and not res.base then
    lines[#lines + 1] = T("stats-line", sig(s.recipe or ""), s.machines, "[entity=" .. (s.machine or "?") .. "]",
      sig(s.output or ""), string.format("%.1f", (s.expected or 0) * 60),
      s.belt and { "", "   ", T("stats-belt", "[entity=" .. s.belt .. "]") } or "")
  end
  -- (what the line takes a minute: its expected output through the recipe, less what productivity adds)
  local need = {}
  local recipe = s.recipe and prototypes.recipe[s.recipe]
  if recipe and s.expected then
    local made = 0
    for _, pr in pairs(recipe.products) do
      if pr.name == s.output then made = (pr.amount or ((pr.amount_min + pr.amount_max) / 2)) * (pr.probability or 1) end
    end
    if made > 0 then
      -- (an ingredient made inside the blueprint isn't brought in: what its own recipe takes is, instead)
      local stage_of, per_min = {}, {}
      for _, st in pairs(s.stages or {}) do stage_of[st.item] = st end
      local function add(name, n) per_min[name] = (per_min[name] or 0) + n end
      local runs = s.expected * 60 / made / (1 + (s.productivity or 0))
      for _, ing in pairs(recipe.ingredients) do
        local st = stage_of[ing.name]
        local r = st and prototypes.recipe[st.recipe]
        if r then
          local out = 1
          for _, pr in pairs(r.products) do if pr.name == ing.name then out = pr.amount or 1 end end
          for _, i2 in pairs(r.ingredients) do add(i2.name, runs * ing.amount / out * i2.amount) end
        else
          add(ing.name, runs * ing.amount)
        end
      end
      for name, n in pairs(per_min) do need[#need + 1] = string.format("%s %.1f", sig(name), n) end
    end
  end
  table.sort(need)
  if #need > 0 then lines[#lines + 1] = T("stats-needs", table.concat(need, "   ")) end
  -- (parts the plan made inside the blueprint on its own, e.g. fed from the bus: ticked, as if picked)
  local make_box = find(frame, "bpgen_make")
  for _, st in pairs(s.stages or {}) do
    for _, r in pairs(make_box and make_box.children or {}) do
      local on = r.children[1]
      if on and on.type == "checkbox" and on.tags and on.tags.item == st.item then on.state = true end
    end
  end
  local function icons(list, kind)
    local t = {}
    for _, n in ipairs(list or {}) do t[#t + 1] = kind and ("[" .. kind .. "=" .. n .. "]") or sig(n) end
    return table.concat(t, " ")
  end
  if res.mall then
    lines[#lines + 1] = T("stats-mall", icons(res.mall.products, "recipe"), tostring(res.mall.machines))
  end
  if res.base then
    local b = res.base
    lines[#lines + 1] = T("stats-base", tostring(b.spm), icons(b.packs))
    if type(b.bring_in) == "table" and next(b.bring_in) then
      local t = {}
      for k, v in pairs(b.bring_in) do
        t[#t + 1] = type(k) == "string" and (type(v) == "number"
          and { "", sig(k), " ", T("per-min", string.format("%.0f", v)) } or sig(k)) or sig(tostring(v))
      end
      lines[#lines + 1] = T("stats-brought-in", join(t, "  "))
    end
    if type(b.not_automated) == "table" and #b.not_automated > 0 then lines[#lines + 1] = T("stats-not-automated", icons(b.not_automated)) end
    if b.route_note then lines[#lines + 1] = b.route_note end
    lines[#lines + 1] = T("stats-book")
  end
  if center then
    lines[#lines + 1] = T("stats-size", math.ceil(w), math.ceil(h), string.format("%.0f", (res.seconds or 0) * 1000))
  end
  for _, n in ipairs(res.notes or {}) do lines[#lines + 1] = { "", "• ", n } end
  find(frame, "bpgen_stats").caption = join(lines, "\n")
  show_result(frame, res, center and w, h, need)
  adopt(player)
  status(frame, res.mode == "lane" and T("preview-shows-lane") or res.mode == "busdesign" and T("preview-shows-bus")
    or res.absolute and T("preview-shows-base") or "")
  -- what next: can the save build it (and for a line, what modules would do)
  find(frame, "bpgen_extra").clear()
  for i, u in ipairs(res.absolute and res.absolute.upgrades or {}) do
    local icons = {}
    for _, it in ipairs(u.items) do icons[#icons + 1] = sig(it) end
    find(frame, "bpgen_extra").add({ type = "button", caption = T("upgrade-lane", table.concat(icons)),
      tags = { bpgen = "upgrade_lane", i = i }, tooltip = T("upgrade-lane-tip", string.format("%.0f", u.need * 60)) })
  end
  ctx.api(player, "save_check", { blueprint = res.blueprint }, "check")
  if s.recipe and s.machine then
    ctx.api(player, "module_ideas", { recipe = s.recipe, machine = s.machine, belt = s.belt,
      rate_per_min = s.expected and s.expected * 60, belts = unlocked(player).belts }, "ideas")
  end
  if to_sandbox[player.index] then  -- ("Try in sandbox" asked for it by the base first: there now, on to the lab)
    to_sandbox[player.index] = nil
    if res.absolute then try_sandbox(player) end
  end
  return true
end

-- answers of bpgen.ingame:api (from control.lua's job loop): suggestions and history in the window
local function extra(frame)
  return find(frame, "bpgen_extra")
end

local function show_check(frame, r)
  if not r.state then return end
  local box = extra(frame)
  if r.ok then
    local badges = find(frame, "bpgen_badges")
    if badges and ui.themed then add_badge(frame, { "", "[img=utility/check_mark_green] ", T("can-build-badge") }) return end
    box.add({ type = "label", caption = { "", "[img=utility/check_mark_green] ", T("can-build") } })
    return
  end
  local short = {}
  for _, b in ipairs(r.buildings or {}) do
    if not b.craftable and (b.have or 0) < b.need then
      short[#short + 1] = T("short-item", sig(b.item), b.need, b.have or 0)
    end
  end
  local lines = {}
  if #(r.recipes or {}) > 0 then
    local t = {}
    for _, rn in ipairs(r.recipes) do t[#t + 1] = prototypes.recipe[rn] and ("[recipe=" .. rn .. "]") or rn end
    lines[#lines + 1] = T("not-researched", table.concat(t, " "))
  end
  if #short > 0 then lines[#lines + 1] = T("cant-craft", join(short, ", ")) end
  local badges = find(frame, "bpgen_badges")
  if badges and ui.themed then
    add_badge(frame, { "", "[img=utility/warning_icon] ", T("cant-build-badge") }, join(lines, "\n"))
    return
  end
  local l = box.add({ type = "label", caption = { "", "[img=utility/warning_icon] ", join(lines, "\n") } })
  l.style.single_line = false
  l.style.maximal_width = 280
end

local function show_ideas(frame, r)
  local ideas = r.ideas or {}
  if #ideas < 2 then return end
  local box = extra(frame)
  ui.add(box, { type = "label", caption = T("ideas") }, "caption")
  local t = box.add({ type = "table", column_count = 4 })
  t.style.horizontal_spacing = 12
  for _, h in ipairs({ "", T("ideas-machines"), T("ideas-power"), T("ideas-input") }) do t.add({ type = "label", caption = h }) end
  for _, idea in ipairs(ideas) do
    t.add({ type = "button", caption = idea.name, tags = { bpgen = "idea", modules = idea.modules or "",
      beacon = idea.beacon or "", beacon_modules = idea.beacon_modules or "" }, tooltip = T("idea-tip") })
    t.add({ type = "label", caption = idea.beacons and idea.beacons > 0 and T("ideas-beacons", tostring(idea.machines), idea.beacons)
      or tostring(idea.machines) })
    t.add({ type = "label", caption = T("mw", string.format("%.1f", (idea.power_kw or 0) / 1000)) })
    t.add({ type = "label", caption = string.format("%.2f", idea.input or 0) })
  end
end

--- History as a drawer stuck to the window's right edge: opened, or closed when it was open (-> the drawer or nil)
local function history_drawer(player, frame)
  local d = player.gui.screen[NAME .. "__right"]
  if d then d.destroy() return nil end
  test_shown(player, frame, false)
  local _, h = stdwin.size(player, NAME)
  d = ui.attach(frame, "right", { size = { 300, math.max(200, (h or 500) - TOOLS_H - 110) }, along = 40 + TOOLS_H + 12,
    overlap = 8,
    role = "toast", direction = "vertical", padding = 8 })
  if not d then return nil end
  place_drawer(player, frame, d)
  ui.add(d, { type = "label", caption = T("history") }, "caption")
  ui.search(d, "bpgen_history", T("search-history"))
  local list = ui.add(d, { type = "scroll-pane", name = "bpgen_history_list", horizontal_scroll_policy = "never" },
    "scroll", { vertically_stretchable = true, horizontally_stretchable = true })
  ui.add(list, { type = "label", caption = "..." }, "dim")
  ui.add(d, { type = "label", caption = T("history-drag-hint") },
    "dim", { single_line = false, maximal_width = 276, font = "default-small" })
  return d
end

local function show_history(frame, items)
  local list = find(frame, "bpgen_history_list")
  if list then
    list.clear()
    if #items == 0 then
      ui.add(list, { type = "label", caption = T("history-empty") },
        "dim", { single_line = false, maximal_width = 260 })
    end
    for i, h in ipairs(items) do
      if i > 40 then break end
      local icon = h.recipe and prototypes.recipe[h.recipe] and ("[recipe=" .. h.recipe .. "] ") or ""
      local name = h.label or T("blueprint")
      local row = list.add({ type = "flow", direction = "horizontal",
        tags = { bpgen_hist = string.lower(h.label or "blueprint") } })
      row.style.vertical_align = "center"
      row.style.horizontally_stretchable = true
      local l = ui.add(row, { type = "label", caption = { "", icon, name }, tooltip = { "", name, "\n",
        T(h.book and "history-entities-book" or "history-entities", tostring(h.entities)), "\n", T("history-row-tip") } },
        "text", { maximal_width = 170 })
      if dnd then dnd.draggable(l, { kind = "bpgen_history", i = i, caption = { "", icon, name } }) end
      row.add({ type = "empty-widget" }).style.horizontally_stretchable = true
      ui.add(row, { type = "button", caption = T("to-cursor-short"), tags = { bpgen = "history_take", i = i } }, "button",
        { minimal_width = 0, height = 26, left_padding = 6, right_padding = 6 })
    end
    return
  end
  local box = extra(frame)
  box.clear()
  if #items == 0 then
    box.add({ type = "label", caption = T("history-empty") })
    return
  end
  local t = box.add({ type = "table", column_count = 3 })
  for i, h in ipairs(items) do
    if i > 25 then break end
    -- (an icon only for a recipe this game has: a removed mod's shows as raw text otherwise)
    local icon = h.recipe and prototypes.recipe[h.recipe] and ("[recipe=" .. h.recipe .. "] ") or ""
    local l = t.add({ type = "label", caption = { "", icon, h.label or T("blueprint") }, tooltip = h.label })
    l.style.maximal_width = 330
    t.add({ type = "label", caption = T(h.book and "history-entities-book" or "history-entities", tostring(h.entities)) })
    t.add({ type = "button", caption = T("to-cursor-short"), tags = { bpgen = "history_take", i = i } })
  end
end

local histories = safe.stored("histories")  -- player index -> the last history list (its blueprints for "To cursor")

local function show_short(frame, rows)
  local box = extra(frame)
  box.clear()
  if #rows == 0 then
    box.add({ type = "label", caption = T("nothing-short") })
    return
  end
  ui.add(box, { type = "label", caption = T("short-head") }, "caption")
  local t = box.add({ type = "table", column_count = 3 })
  for _, r in ipairs(rows) do
    t.add({ type = "label", caption = T("short-row", sig(r.name), string.format("%.0f", r.short), string.format("%.0f", r.made),
      string.format("%.0f", r.used)) })
    t.add({ type = "empty-widget" })
    t.add({ type = "button", caption = T("plan-it"), tags = { bpgen = "plan_short", recipe = r.recipe, rate = r.short } })
  end
end

local function show_bus(frame, r)
  local box = extra(frame)
  box.clear()
  if not r.bus then
    box.add({ type = "label", caption = T("no-bus") })
    return
  end
  local b = r.bus
  ui.add(box, { type = "label", caption = T("bus-report", b.lanes,
    #b.pipes > 0 and { "", " ", T("bus-pipes", #b.pipes) } or "", b.length,
    b.measured and "" or { "", " ", T("not-measured") }) }, "caption")
  local t = box.add({ type = "table", column_count = 3 })
  for _, it in ipairs(r.items or {}) do
    local full = it.used and (it.used >= 0.9 * it.cap or it.dry > 0)
    local used = it.used and T("bus-used", string.format("%.0f", it.used * 60), string.format("%.0f", it.cap * 60))
      or T("bus-carries", string.format("%.0f", it.cap * 60))
    local single = not it.item:find(" + ", 1, true)
    t.add({ type = "label", caption = { "", full and "[color=yellow]" or "",
      T("bus-row", single and sig(it.item) or it.item, it.lanes, { "", used,
        it.dry > 0 and T("bus-dry", it.dry) or "",
        (it.short or 0) > 0 and T("bus-base-short", string.format("%.0f", it.short)) or "" }), full and "[/color]" or "" } })
    local acts = t.add({ type = "flow", direction = "horizontal" })
    if single then
      acts.add({ type = "button", caption = T("add-lane"), tags = { bpgen = "bus_lane", item = it.item },
        tooltip = T("add-lane-tip") })
    end
    if single and it.makeable then
      acts.add({ type = "button", caption = T("make-more"), tags = { bpgen = "bus_make", item = it.item,
        rate = math.max(it.short or 0, 60) }, tooltip = T("make-more-tip") })
    end
    t.add({ type = "empty-widget" })
  end
  if #(r.short or {}) > 0 then
    ui.add(box, { type = "label", caption = T("lacks-head") }, "caption")
    local s = box.add({ type = "table", column_count = 2 })
    for _, it in ipairs(r.short) do
      s.add({ type = "label", caption = T("lacks-row", sig(it.item), string.format("%.0f", it.short)) })
      s.add({ type = "button", caption = T("make-by-bus"), tags = { bpgen = "bus_make", item = it.item, rate = it.short },
        tooltip = T("make-by-bus-tip") })
    end
  end
end

local function on_api(player, tag, out)
  local frame = player.gui.screen[NAME]
  if not frame then return end
  local r = out and helpers.json_to_table(out) or {}
  if r.error then return safe.log("bpgen window " .. tag .. ": " .. tostring(r.error)) end
  r = r.result
  if tag == "short" and r then show_short(frame, r)
  elseif tag == "bus" and r then show_bus(frame, r) status(frame, "")
  elseif tag == "check" and r then show_check(frame, r)
  elseif tag == "ideas" and r then show_ideas(frame, r)
  elseif tag == "history" and r then
    histories[player.index] = r
    if web.model(player) then return web.history(player) end  -- (the page's History drawer shows it)
    show_history(frame, r)
  elseif tag == "also" and r then
    local box = find(frame, "bpgen_also")
    box.clear()
    local function add_row(parent, sib)
      -- (its name and icon: the item's, else the recipe's - bpgen's data may name an item the game calls otherwise)
      local rp = prototypes.recipe[sib.recipe or ""]
      local caption = (prototypes.item[sib.item] or prototypes.fluid[sib.item]) and named(sib.item)
        or rp and { "", "[recipe=" .. rp.name .. "] ", rp.localised_name }
      if not caption then return end  -- (not in this game: bpgen's copy of the mods is behind it)
      local f = parent.add({ type = "flow", direction = "horizontal" })
      f.style.vertical_align = "center"
      f.style.horizontally_stretchable = true
      local cb = f.add({ type = "checkbox", state = false, caption = caption, tags = { item = sib.item, recipe = sib.recipe } })
      cb.style.maximal_width = 200  -- (a long name shortens: the rate boxes stay in one column)
      cb.style.horizontally_squashable = true
      f.add({ type = "empty-widget" }).style.horizontally_stretchable = true
      f.add({ type = "textfield", numeric = true, allow_decimal = true, tooltip = T("also-rate-tip") }).style.width = 44
    end
    local near = {}
    for _, sib in ipairs(r) do
      if sib.exact then add_row(box, sib) else near[#near + 1] = sib end
    end
    if #near > 0 then
      box.add({ type = "checkbox", state = false, tags = { bpgen_toggle = "bpgen_also_near" },
        caption = T("also-near", #near) })
      local more = box.add({ type = "flow", name = "bpgen_also_near", direction = "vertical", visible = false })
      for i, sib in ipairs(near) do
        if i > 12 then break end
        add_row(more, sib)
      end
    end
    if #r == 0 then box.add({ type = "label", caption = T("also-none") }) end
    fit_panel(box)
    ui.set_badge(find(frame, "bpgen_also_badge"), #r > 0 and tostring(#r) or nil)
  elseif tag == "tree" and r then
    local box = find(frame, "bpgen_make")
    box.clear()
    for _, ing in ipairs(r) do
      if not ing.fluid then
        local f = box.add({ type = "flow", direction = "horizontal" })
        f.style.vertical_align = "center"
        f.style.horizontally_stretchable = true
        if #ing.recipes > 0 then
          f.add({ type = "checkbox", state = false, caption = named(ing.item), tags = { item = ing.item, bpgen_make_row = true } })
          local pick = f.add({ type = "choose-elem-button", elem_type = "recipe", tooltip = T("made-with-tip"),
            elem_filters = { { filter = "has-product-item", elem_filters = { { filter = "name", name = ing.item } } } } })
          pick.elem_value = ing.recipes[1].recipe
          pick.style.size = 28
        else
          f.add({ type = "label", caption = named(ing.item) })
        end
        row_note(f, T("on-belt"), "bpgen_where")
      end
    end
    if #box.children == 0 then box.add({ type = "label", caption = T("only-fluids") }) end
  end
end

function M.measuring(player)
  return bench.find("calibrate", player.index) ~= nil
end

-- the line's starved / blocked machines, outlined on the preview for the next second
local function mark(player, boxes)
  for _, b in ipairs(boxes) do
    rendering.draw_rectangle({ color = { 1, 0.25, 0.1 }, width = 3, filled = false, left_top = b.left_top,
      right_bottom = b.right_bottom, surface = PREVIEW, players = { player }, time_to_live = 62 })
  end
end

local function describe_test(run, final)
  local seconds, cases = bench.rates(run)
  local t = game.tick - run.start
  local count, starved = bench.statuses(run)
  local machines = {}
  for k, n in pairs(count) do machines[#machines + 1] = n .. " " .. k:gsub("_", " ") end  -- (the game's status names)
  table.sort(machines)
  local head
  if run.products then  -- (a mall: nothing to rate, what has reached its chests)
    local got, waiting = bench.chest_contents(run), {}
    for _, p in ipairs(run.products) do if not got[p] then waiting[#waiting + 1] = sig(p) end end
    head = T("test-mall", T(final and "test-done" or "test-mall-prefix"), #run.products - #waiting, #run.products,
      math.floor(t / 60), #waiting > 0 and { "", "\n", T("test-waiting", table.concat(waiting, " ")) } or "")
  elseif t < run.warmup then
    head = T("test-warmup", math.floor(t / 60), math.floor(run.warmup / 60))
  else
    local got = (cases[1].rates or {})[run.output] or 0
    local pct = run.expected and run.expected > 0 and got / run.expected * 100 or 0
    head = T("test-measured", T(final and "test-done" or "test-measuring"), sig(run.output or ""),
      string.format("%.1f", got * 60), string.format("%.0f", pct), string.format("%.1f", (run.expected or 0) * 60),
      { "", " ", final and T("test-over", math.floor(seconds))
        or T("test-progress", math.floor(seconds), math.floor(run.measure / 60)) })
  end
  local errs = cases[1].errors or {}
  return join({ head, T("test-machines", table.concat(machines, ", ")), #errs > 0 and table.concat(errs, "; ") or nil },
    "\n"), starved
end

local test_series = safe.stored("test_series")  -- player index -> the rate measured each second (the chart)
--- the test run's section: started (no run), or where the run is (a bar red to green by the share of the expected
--- rate, a chart of the rate)
local function test_view(player, frame, run, final)
  local box = find(frame, "bpgen_test_box")
  if not box then return end
  if not run then test_shown(player, frame, true) end  -- (a run starting: its drawer opens; later it stays as left)
  local bar, holder = find(box, "bpgen_test_bar"), find(box, "bpgen_test_graph")
  if not run then
    test_series[player.index] = {}
    ui.set_progress(bar, 0, T("starting"))
    holder.clear()
    -- (the web window draws its own chart: a graph on a hidden window would draw over the game)
    local g = not web.model(player) and ui.graph(holder, { 0, 0 }, { width = TEST_W - 40, height = 56 })
    if g then g.name = "bpgen_test_chart" end
    adopt(player)
    return
  end
  local t = game.tick - run.start
  local frac, text, rate = 0, "", nil
  if run.products then
    local got, n = bench.chest_contents(run), 0
    for _, pr in ipairs(run.products) do if got[pr] then n = n + 1 end end
    frac, rate = n / math.max(1, #run.products), n
    text = T("test-bar-mall", n, #run.products)
  elseif t < run.warmup then
    text = T("test-bar-warmup", math.floor(t / 60), math.floor(run.warmup / 60))
  else
    local _, cases = bench.rates(run)
    local got = ((cases[1] or {}).rates or {})[run.output] or 0
    rate = got * 60
    frac = run.expected and run.expected > 0 and got / run.expected or 0
    text = T("test-bar-rate", string.format("%.0f", rate), string.format("%.0f", frac * 100))
  end
  ui.set_progress(bar, math.min(1, frac), text)
  -- (the Test run button says how far it got: seen with the drawer closed)
  local button = find(frame, "bpgen_test")
  if button and not final then
    button.caption = run.products and T("test-live-mall", math.floor(frac * #run.products + 0.5), #run.products)
      or t < run.warmup and T("test-live-warmup") or T("test-live", string.format("%.0f", frac * 100))
    button.tooltip = { "", T("test-live-tip"), "\n", text }
  end
  if rate then
    local list = test_series[player.index] or {}
    list[#list + 1] = rate
    while #list > 60 do table.remove(list, 1) end
    test_series[player.index] = list
    if #list >= 2 then ui.set_graph(find(box, "bpgen_test_chart"), list) end
  end
  if final then glow(player, false) end
end

bench.on_progress = function(run)
  local player = game.get_player(run.player)
  local frame = player and player.gui.screen[NAME]
  if not frame then return end
  web.dirty(player)
  if run.kind == "test" then
    local text, starved = describe_test(run, false)
    find(frame, "bpgen_test_status").caption = text
    mark(player, starved)
    test_view(player, frame, run)
  else
    local left = math.ceil((run.warmup + run.measure - (game.tick - run.start)) / 60)
    status(frame, T("measuring-left", left))
  end
end

bench.on_done = function(run, cases)
  local player = game.get_player(run.player)
  if not player then return end
  if run.kind == "calibrate" then
    ctx.measured(player, cases)
    return
  end
  local frame = player.gui.screen[NAME]
  test_speed_off(frame)  -- (the window closed or not: the game back at its own speed)
  if not frame then return end
  web.dirty(player)
  local text, starved = describe_test(run, true)
  find(frame, "bpgen_test_status").caption = text
  mark(player, starved)
  find(frame, "bpgen_test").caption = T("test-run")
  find(frame, "bpgen_test").tooltip = T("test-run-tip")
  test_view(player, frame, run, true)
end

local function to_cursor(player)
  local r = results[player.index]
  if not r then return end
  if not player.clear_cursor() then return say(player, T("hand-full")) end
  player.cursor_stack.set_stack({ name = "blueprint" })
  if player.cursor_stack.import_stack(r.blueprint) == 1 then
    player.clear_cursor()
    return say(player, T("hand-bp-not-imported"))
  end
  close(player)  -- (out of the way of placing it)
  ui.toast(player, T("bp-in-hand"))
end

--- the ghosts bpgen placed that are still there to remove
local function ghosts_left(player)
  local n = 0
  for _, g in pairs(placed[player.index] or {}) do
    if g.valid and (g.type == "entity-ghost" or g.type == "tile-ghost") then n = n + 1 end
  end
  return n
end

--- Undo: bpgen's unbuilt ghosts removed, what they'd have replaced unmarked
local function unplace(player)
  place.clear_outline(player)
  local n = 0
  for _, g in pairs(placed[player.index] or {}) do
    if g.valid and (g.type == "entity-ghost" or g.type == "tile-ghost") then g.destroy() n = n + 1 end
  end
  for _, e in pairs(unmark[player.index] or {}) do  -- (the belts its splitters would have replaced: kept)
    if e.valid and e.to_be_deconstructed() then e.cancel_deconstruction(player.force, player) end
  end
  placed[player.index], unmark[player.index] = nil, nil
  local frame = player.gui.screen[NAME]
  say(player, T("ghosts-removed", n))
end
ui.on_yes("bpgen_unplace", function(player) unplace(player) end)

--- the window's settings (the gear by its close button): opened, or closed when they were open
local function settings(player)
  local frame = player.gui.screen[NAME]
  local sized = stdwin and stdwin.size_setting ~= nil  -- (fse-std 0.8+: the window's own Size)
  local can_web = web.available()
  local m = frame and ui.menu(frame, { size = { 250, (ui.layouts and 196 or 164) + (sized and 58 or 0)
    + (can_web and 32 or 0) } })
  if not m then return end
  ui.add(m, { type = "label", caption = "bpgen" }, "caption")
  if can_web then  -- (the web window instead: fse's shells, single player)
    ui.add(m, { type = "checkbox", name = "bpgen_web", caption = T("web-window"), state = false,
      tooltip = T("web-window-tip") }, "check")
  end
  if ui.layouts then
    ui.add(m, { type = "checkbox", name = "bpgen_docked", caption = { "fstd.docked" },
      state = docked(player) == true, tooltip = { "fstd.docked-tooltip" } }, "check")
  end
  if sized then stdwin.size_setting(m, NAME).name = "bpgen_size" end
  ui.add(m, { type = "checkbox", name = "bpgen_only_unlocked", caption = T("only-unlocked"),
    state = only_unlocked[player.index] == true, tooltip = T("only-unlocked-tip") }, "check")
  local wide = { horizontally_stretchable = true, minimal_width = 0, height = 28 }
  if remote.interfaces["fse-std"] and remote.interfaces["fse-std"].open_theme then
    ui.add(m, { type = "button", caption = { "fstd.theme-button" }, tags = { bpgen = "theme" },
      tooltip = { "fstd.theme-button-tooltip" } }, "button", wide)
  end
  ui.add(m, { type = "button", caption = { "fstd.reset-place" }, tags = { bpgen = "reset_size" },
    tooltip = { "fstd.reset-place-tooltip" } }, "button", wide)
end

local function on_click(e)
  local el = e.element
  if not (el and el.valid) then return end
  if el.tags and el.tags.fstd_settings == NAME then return settings(game.get_player(e.player_index)) end
  local action = el.tags and el.tags.bpgen
  if not action then return end
  local player = game.get_player(e.player_index)
  local frame = player.gui.screen[NAME]
  local r = results[player.index]
  if action == "close" then
    close(player)
  elseif action == "tool_done" then
    player.clear_cursor()
    M.tool_done(player)
  elseif action == "theme" then
    local menu = player.gui.screen[NAME .. "__menu"]  -- (the settings closed: the theme window opens instead)
    if menu then menu.destroy() end
    remote.call("fse-std", "open_theme", player.index)
  elseif action == "reset_size" then
    if stdwin and stdwin.reset_place then
      stdwin.reset_place(player, NAME)  -- (its place and size; its Size stays)
    elseif storage.fstd_windows and storage.fstd_windows[player.index] then
      storage.fstd_windows[player.index][NAME] = nil
    end
    if storage.bpgen_cam then storage.bpgen_cam[player.index] = nil end
    M.rebuild(player)
  elseif action == "plan" then
    bus_shift[player.index] = nil
    M.plan(player)
  elseif action == "nudge" and r and r.res.mode == "busdesign" then  -- (its head, sideways: planned again)
    bus_shift[player.index] = (bus_shift[player.index] or 0) + (el.tags.dx + el.tags.dy) * BUS_STEP
    status(frame, T("moving-bus-head"))
    M.plan(player)
  elseif action == "recipe" then  -- a bpgen button in another mod's window (compat.lua)
    M.open(player, { recipe = el.tags.recipe })
    M.plan(player)
  elseif action == "test" and r and r.res.test and r.where then
    local run = bench.find("test", player.index)
    local drawer = player.gui.screen[NAME .. "__test"]
    if run and drawer and not drawer.tags.bpgen_open then  -- (a run going on, its drawer closed: opened again)
      test_shown(player, frame, true)
    elseif run then
      bench.stop(run.id)
      find(frame, "bpgen_test").caption = T("test-run")
      find(frame, "bpgen_test").tooltip = T("test-run-tip")
      find(frame, "bpgen_test_status").caption = T("test-stopped")
      test_speed_off(frame)
      glow(player, false)
    else
      bench.test(player.index, r.res.test, r.where.origin, r.where.area, PREVIEW)
      find(frame, "bpgen_test").caption = T("stop-test")
      find(frame, "bpgen_test_status").caption = T("starting-dots")
      find(frame, "bpgen_test_speed").visible = not game.is_multiplayer()
      test_view(player, frame)
      glow(player, true)
    end
  elseif action == "test_close" then  -- (the drawer closed; a run goes on, its button says how far)
    test_shown(player, frame, false)
  elseif action == "test_speed" and not game.is_multiplayer() then
    local b = find(frame, "bpgen_test_speed")
    local tags = b.tags  -- (its step kept in its tags: ×1, ×5, ×10, round again)
    local next_ = ({ [1] = 5, [5] = 10, [10] = 1 })[tags.speed or 1] or 1
    storage.bpgen_speed_was = storage.bpgen_speed_was or game.speed
    game.speed = next_ == 1 and storage.bpgen_speed_was or next_
    if next_ == 1 then storage.bpgen_speed_was = nil end
    tags.speed = next_
    b.tags = tags
    b.caption = "×" .. next_
  elseif action == "cursor" and r then
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    to_cursor(player)
  elseif action == "copy" and r and safe.has("std") then
    native.call("std", "clipboard_set", r.blueprint)
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    say(player, T("bp-copied"))
  elseif action == "short" then
    extra(frame).clear()
    extra(frame).add({ type = "label", caption = "..." })
    ctx.api(player, "shortages", nil, "short")
  elseif action == "plan_short" then
    set_mode(frame, "line")
    find(frame, "bpgen_recipe").elem_value = el.tags.recipe
    set_machine_filter(frame, el.tags.recipe)
    local m = best_machine(player, el.tags.recipe)
    find(frame, "bpgen_machine").elem_value = m and { name = m, quality = "normal" } or nil
    find(frame, "bpgen_rate").text = tostring(math.ceil(el.tags.rate))
    M.plan(player)
  elseif action == "move" then
    if player.clear_cursor() then
      player.cursor_stack.set_stack({ name = "bpgen-move" })
      M.tool_card(player, "bpgen-move")
      status(frame, T("move-tool-hint"))
    end
  elseif action == "refit" then
    if player.clear_cursor() then
      player.cursor_stack.set_stack({ name = "bpgen-refit" })
      M.tool_card(player, "bpgen-refit")
      status(frame, T("refit-tool-hint"))
    end
  elseif action == "snapshot" then
    if player.clear_cursor() then
      player.cursor_stack.set_stack({ name = "bpgen-snapshot" })
      M.tool_card(player, "bpgen-snapshot")
      status(frame, T("snapshot-tool-hint"))
    end
  elseif action == "patches" then
    if player.clear_cursor() then
      player.cursor_stack.set_stack({ name = "bpgen-patches" })
      M.tool_card(player, "bpgen-patches")
      status(frame, T("patch-tool-hint"))
    end
  elseif action == "patches_clear" then
    patch_areas[player.index] = nil
    show_patches(frame, player)
  elseif action == "place" and r and r.res.absolute then
    M.place_absolute(player)
  elseif action == "sandbox" and r then
    if not r.res.absolute and frame and find(frame, "bpgen_place_base").enabled and not sandbox.cant(player) then
      -- (a line, a mall, a base: put by the base first, as Place by my base; the lab follows when it's there)
      local sec = find(frame, "bpgen_section")
      turns[player.index], tried[player.index] = nil, nil
      local ok, why = ctx.place_base(player, { bus = find(frame, "bpgen_ext_bus").state and "auto" or "off",
        seed = { x = player.position.x, y = player.position.y },
        section = sec.visible and sec.items[sec.selected_index] or nil })
      to_sandbox[player.index] = ok or nil
      status(frame, ok and T("looking-around") or T("cant", tostring(why)))
    else
      try_sandbox(player)
    end
  elseif action == "sandbox_place" then
    sandbox.place(player)
  elseif action == "sandbox_discard" then
    sandbox.discard(player)
  elseif action == "tab" then
    set_mode(frame, el.tags.mode)
  elseif action == "check_bus" then
    extra(frame).clear()
    local ok, why = ctx.bus_report(player, unlocked(player).belts)
    status(frame, ok and T("looking-at-bus") or T("cant", tostring(why)))
  elseif action == "bus_lane" then
    local ok, why = ctx.add_lane(player, el.tags.item)
    status(frame, ok and T("laying-lane") or T("cant", tostring(why)))
  elseif action == "bus_make" then
    -- (Extend: that item at that rate, made next to the bus from what it carries)
    set_mode(frame, "extend")
    find(frame, "bpgen_ext_item").elem_value = el.tags.item
    find(frame, "bpgen_ext_rate").text = tostring(math.ceil(el.tags.rate))
    M.plan(player)
  elseif action == "upgrade_lane" and r and r.res.absolute then
    M.upgrade_lane(player, r.res.absolute.upgrades[el.tags.i])
  elseif action == "nudge" and r and r.res.absolute then
    -- (exactly that far from where it is: no looking for another spot, the belts and power connected again)
    local n = e.shift and 5 or 1
    local b = r.res.absolute.box  -- (the snapshot around it; where it goes is bpgen's last spot, shifted)
    local ok, why = ctx.place_base(player, { bus = find(frame, "bpgen_ext_bus").state and "auto" or "off",
      seed = { x = b[1] + b[3] / 2, y = b[2] + b[4] / 2 },
      turn = turns[player.index], shift = { el.tags.dx * n, el.tags.dy * n }, again = true })
    status(frame, ok and T("moving-it") or T("cant", tostring(why)))
  elseif action == "base_spot" then
    if player.clear_cursor() then
      player.cursor_stack.set_stack({ name = "bpgen-spot" })
      M.tool_card(player, "bpgen-spot")
      status(frame, T("click-where"))
    end
  elseif (action == "base_rotate" or action == "base_redo") and r and r.res.absolute then
    -- (both around where it is now: Rotate turns it there, Redo looks past every spot it was shown on)
    local b = r.res.absolute.box
    local seed = { x = b[1] + b[3] / 2, y = b[2] + b[4] / 2 }
    local avoid
    if action == "base_rotate" then
      turns[player.index] = ((turns[player.index] or 0) + 1) % 4
    else
      tried[player.index] = tried[player.index] or {}
      table.insert(tried[player.index], { b[1], b[2], b[3], b[4] })
      avoid = tried[player.index]
    end
    local ok, why = ctx.place_base(player, { bus = find(frame, "bpgen_ext_bus").state and "auto" or "off", seed = seed,
      turn = turns[player.index], avoid = avoid, again = true })
    status(frame, ok and T(action == "base_rotate" and "rotating" or "looking-another-spot") or T("cant", tostring(why)))
  elseif action == "place_base" and r then
    local sec = find(frame, "bpgen_section")  -- (a starter base: the print picked under the preview)
    turns[player.index], tried[player.index] = nil, nil
    local ok, why = ctx.place_base(player, { bus = find(frame, "bpgen_ext_bus").state and "auto" or "off",
      seed = { x = player.position.x, y = player.position.y },
      section = sec.visible and sec.items[sec.selected_index] or nil })
    status(frame, ok and T("looking-around") or T("cant", tostring(why)))
  elseif action == "place" and r and r.where then
    local c = place.find_spot(player, r.w, r.h)
    if not c then
      status(frame, T("no-free-spot", math.ceil(r.w), math.ceil(r.h)))
    else
      local ghosts, origin, area, marks = place.place(player, r.blueprint, c)
      placed[player.index], unmark[player.index] = ghosts, marks
      if origin then
        local found, missing = place.guide(player, area, origin, r.res.test and r.res.test.case.sources or {},
          r.res.label)
        say(player, T("placed-near", #ghosts, #missing > 0 and T("nothing-carries", table.concat(missing, " "))
          or found > 0 and T("dashed-lines") or ""))
      end
      ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    end
  elseif action == "unplace" then
    -- (a press of the button asks first when there are ghosts to remove; a script's press (tests) doesn't)
    local n = ghosts_left(player)
    if n > 0 and not e.scripted then
      ui.confirm(player, "bpgen_unplace", { title = T("undo-title"),
        text = T("undo-text", n, #(unmark[player.index] or {}) > 0 and T("undo-text-keeps") or ""),
        yes = T("undo-yes"), no = T("undo-no") })
    else
      unplace(player)
    end
  elseif action == "history" then
    if not (docked(player) and stdwin) or history_drawer(player, frame) then ctx.api(player, "history", {}, "history") end
  elseif action == "history_take" then
    local h = (histories[player.index] or {})[el.tags.i]
    if h then
      results[player.index] = results[player.index] or { res = {} }
      results[player.index].blueprint = h.blueprint
      to_cursor(player)
    end
  elseif action == "idea" then
    -- "name@quality:count,...": the first module kind into the window's pickers
    local function first(spec)
      local n, q = (spec or ""):match("^([^@,:]+)@([^:,]+)")
      return n and { name = n, quality = q } or nil
    end
    find(frame, "bpgen_module").elem_value = first(el.tags.modules)
    local bm = first(el.tags.beacon_modules)
    find(frame, "bpgen_beacon_module").elem_value = bm
    find(frame, "bpgen_beacon").elem_value = bm and el.tags.beacon ~= "" and { name = el.tags.beacon, quality = "normal" } or nil
    if bm then
      find(frame, "bpgen_more_toggle").state = true
      find(frame, "bpgen_more").visible = true
    end
    local b = find(frame, "bpgen_modules_badge")
    if b then ui.open_fold(b.parent.parent.fstd_fold_body, true) end
    badges_of(frame)
    M.plan(player)
  elseif action == "size" then
    if (resized_at[player.index] or -10) >= game.tick - 3 then return end  -- (the end of a drag, not a click)
    local res, scale = player.display_resolution, player.display_scale
    local w = cam_size(player)
    local next_ = PRESETS[1]
    for i, p in ipairs(PRESETS) do
      if w < math.floor(res.width / scale * p[1]) - 4 then next_ = p break end
      if i == #PRESETS then next_ = PRESETS[1] end
    end
    apply_size(player, frame, res.width / scale * next_[1], res.height / scale * next_[2])
    if r and r.w then
      local cam = find(frame, "bpgen_cam")
      r.zoom = fit(player, r.w, r.h)
      cam.zoom = r.zoom
    end
  elseif action == "pan" and r and r.zoom then
    local cam = find(frame, "bpgen_cam")
    local step = view_px(player) / (32 * cam.zoom) / 4  -- (a quarter of the view)
    cam.position = { x = cam.position.x + el.tags.dx * step, y = cam.position.y + el.tags.dy * step }
  elseif (action == "zoom" or action == "fit") and r and r.zoom then
    local cam = find(frame, "bpgen_cam")
    if action == "fit" then
      cam.zoom, cam.position = r.zoom, r.center
    else
      cam.zoom = math.max(0.03, math.min(4, cam.zoom * el.tags.f))
    end
  end
end

local function tree(player, frame)
  local recipe, machine = value(frame, "bpgen_recipe"), value(frame, "bpgen_machine")
  find(frame, "bpgen_also").clear()
  find(frame, "bpgen_also").add({ type = "label", caption = recipe and "..." or T("pick-a-recipe") })
  if recipe then ctx.api(player, "siblings", { recipe = recipe, machine = machine and machine.name }, "also") end
  find(frame, "bpgen_make").clear()
  find(frame, "bpgen_make").add({ type = "label", caption = recipe and "..." or T("pick-a-recipe") })
  if recipe then ctx.api(player, "recipe_tree", { recipe = recipe, machine = machine and machine.name }, "tree") end
end

-- (pickers an answer adds follow "Only unlocked" too)
function M.on_api(player, tag, out)
  web.dirty(player)
  local r = on_api(player, tag, out)
  M.restrict(player)
  local frame = player.gui.screen[NAME]
  adopt(player)  -- (what the answer added: the player's theme)
  return r
end

local function on_elem_changed(e)
  local el = e.element
  if not (el and el.valid) then return end
  if el.tags and el.tags.bpgen_product then
    -- (the mall's products: an empty slot always at the end, a cleared one goes)
    local t = el.parent
    local kids = t.children
    if el.elem_value and kids[#kids] == el then
      t.add({ type = "choose-elem-button", elem_type = "recipe", tags = { bpgen_product = true },
        elem_filters = { { filter = "hidden", invert = true } } })
    elseif not el.elem_value and kids[#kids] ~= el then
      el.destroy()
    end
    return
  end
  if el.name ~= "bpgen_recipe" then return end
  local player = game.get_player(e.player_index)
  local frame = player.gui.screen[NAME]
  tree(player, frame)
  set_machine_filter(frame, el.elem_value)
  local mbtn = find(frame, "bpgen_machine")
  local cur = mbtn.elem_value
  local cat = el.elem_value and prototypes.recipe[el.elem_value].category
  if el.elem_value and not (cur and prototypes.entity[cur.name].crafting_categories[cat]) then
    local m = best_machine(player, el.elem_value)
    mbtn.elem_value = m and { name = m, quality = "normal" } or nil
  end
  if el.elem_value then M.plan(player) end
end

-- the preview with the loader's std plugin: drag it with the left button, zoom with the wheel (without it: the
-- arrow and -/+ buttons)


local function camera_tick()
  if not next(cam_hover) and not next(drags) and not next(grip_hover) and not next(resizing) then return end
  -- (the mouse is this peer's own: in multiplayer the preview moves with its buttons only)
  if not safe.has("std") or game.is_multiplayer() then return end
  local ok, m = pcall(function() return helpers.json_to_table(native.call("std", "input") or "") end)
  if not (ok and m) then return end
  -- the size grip: dragged, the preview follows the cursor (in GUI units: pixels over the display scale)
  for pi, grip in pairs(grip_hover) do
    local player = game.get_player(pi)
    local frame = player and player.gui.screen[NAME]
    local rz = resizing[pi]
    if not (grip.valid and frame) then
      grip_hover[pi], resizing[pi] = nil, nil
    elseif m.left then
      if not rz then
        local w, h = cam_size(player)
        resizing[pi] = { x = m.x, y = m.y, w = w, h = h }
      else
        local scale = player.display_scale
        apply_size(player, frame, rz.w + (m.x - rz.x) / scale, rz.h + (m.y - rz.y) / scale)
        if m.x ~= rz.x or m.y ~= rz.y then resized_at[pi] = game.tick end
      end
    elseif rz then
      resizing[pi] = nil
      if rz.left then grip_hover[pi] = nil end
      local r = results[pi]
      if r and r.w then r.zoom = fit(player, r.w, r.h) end
    end
  end
  -- (the wheel over the preview zooms it, not the map behind: the game is kept from the wheel while it's hovered)
  if next(cam_hover) then pcall(native.call, "std", "wheel_capture", "250") end
  for pi, cam in pairs(cam_hover) do
    if not cam.valid then
      cam_hover[pi], drags[pi] = nil, nil
    else
      local d = drags[pi]
      if m.left and not (dnd and dnd.dragging(pi)) then
        local k = 32 * cam.zoom  -- (screen pixels a tile)
        if not d then
          drags[pi] = { x = m.x, y = m.y, cx = cam.position.x, cy = cam.position.y }
        else
          cam.position = { x = d.cx - (m.x - d.x) / k, y = d.cy - (m.y - d.y) / k }
        end
      else
        drags[pi] = nil
        if left_during[pi] then  -- (the drag ended outside the preview: it isn't hovered any more)
          cam_hover[pi], left_during[pi] = nil, nil
        end
      end
      local w = m.wheel or 0
      if wheel_seen[pi] == nil then wheel_seen[pi] = w end
      if w ~= wheel_seen[pi] then
        cam.zoom = math.max(0.03, math.min(4, cam.zoom * 1.2 ^ (w - wheel_seen[pi])))
        wheel_seen[pi] = w
      end
    end
  end
end

-- the library's corner grip resizes the window: the preview follows
if stdwin then
  stdwin.on_resize(function(player, name, w, h)
    if name ~= NAME then return end
    local frame = player.gui.screen[NAME]
    if frame then apply_size(player, frame, w - LEFT_W, h - frame_h(player)) end
  end)
end

--- the window fitted again to the room it has (its scale, the game's UI scale or the screen changed): the preview
--- shrinks to fit (or grows back to what the player asked for), then the library checks its scale once more
local refitting = {}
local function reflow(player)
  local frame = player.gui.screen[NAME]
  if frame and frame.valid and web.model(player) then return M.view_resized(player) end  -- (the page lays itself out)
  if not (frame and frame.valid and stdwin) or refitting[player.index] then return end
  refitting[player.index] = true
  local ok, err = pcall(function()
    local w, h = wanted_size(player)
    apply_size(player, frame, w, h, true)
    if ui.theme and ui.theme.refit then ui.theme.refit(player, frame) end  -- (does it fit at its scale now?)
    for _, n in ipairs({ "__test", "__right" }) do
      local d = player.gui.screen[NAME .. n]
      if d and d.visible then place_drawer(player, frame, d) end
    end
    local r = results[player.index]
    if r and r.w then r.zoom = fit(player, r.w, r.h) end
  end)
  refitting[player.index] = nil
  if not ok then error(err) end
end
M.reflow = reflow
if stdwin and stdwin.on_scale then
  stdwin.on_scale(function(player, name)
    if name == NAME then reflow(player) end
  end)
end

ui.on_search("bpgen_history", function(player, text)
  local list = find(player.gui.screen[NAME], "bpgen_history_list")
  text = string.lower(text or "")
  for _, row in pairs(list and list.children or {}) do
    if row.tags.bpgen_hist then row.visible = text == "" or row.tags.bpgen_hist:find(text, 1, true) ~= nil end
  end
end)

--- History's row `i` dropped onto the Recipe slot ("recipe": planned again) or the preview (shown, ready to place)
local function history_use(player, i, target)
  local h = (histories[player.index] or {})[i]
  local frame = player.gui.screen[NAME]
  if not (h and frame) then return end
  if target == "recipe" and h.recipe and prototypes.recipe[h.recipe] then  -- (planned again)
    set_mode(frame, "line")
    find(frame, "bpgen_recipe").elem_value = h.recipe
    set_machine_filter(frame, h.recipe)
    local m = best_machine(player, h.recipe)
    find(frame, "bpgen_machine").elem_value = m and { name = m, quality = "normal" } or nil
    M.plan(player)
  else  -- (shown in the preview, ready to place)
    M.on_result(player, { blueprint = h.blueprint, mode = h.mode, label = h.label,
      notes = { T("from-history", h.label or T("a-blueprint")) } })
  end
end
M.history_use = history_use

if dnd then
  dnd.on_drop(function(player, payload, target)
    if type(payload) ~= "table" or payload.kind ~= "bpgen_history" then return end
    if not (target and target.valid) then return end
    history_use(player, payload.i, target.name == "bpgen_recipe" and "recipe" or "preview")
  end)
end

-- (tests: hover events can't be faked)
function M.test_hover_camera(player_index)
  local frame = game.get_player(player_index).gui.screen[NAME]
  local cam = frame and find(frame, "bpgen_cam")
  cam_hover[player_index], wheel_seen[player_index] = cam, nil
  local w, h = cam_size(game.get_player(player_index))
  return cam and { x = cam.position.x, y = cam.position.y, zoom = cam.zoom,
    sections = #find(frame, "bpgen_section").items, w = w, h = h } or nil
end

function M.test_hover_grip(player_index)
  local frame = game.get_player(player_index).gui.screen[NAME]
  cam_hover[player_index] = nil
  if stdwin then  -- (the library's corner grip: the element tagged fstd_resize)
    local function grip(el)
      if el.tags and el.tags.fstd_resize then return el end
      for _, c in pairs(el.children) do local g = grip(c) if g then return g end end
    end
    stdinput.hovered[player_index] = frame and grip(frame)
    return
  end
  grip_hover[player_index] = frame and find(frame, "bpgen_grip")
end

-- (tests: the window resized as its corner grip does: the size kept, the frames stuck to it following)
function M.test_resize(player_index, w, h)
  local player = game.get_player(player_index)
  local frame = player.gui.screen[NAME]
  if not (stdwin and frame) then return end
  local saved = storage.fstd_windows and storage.fstd_windows[player_index] and storage.fstd_windows[player_index][NAME]
  if saved then saved.width, saved.height = w, h end
  if ui.theme and ui.theme.style then
    ui.theme.style(frame, { width = w, height = h })
  else
    frame.style.width, frame.style.height = w, h
    local tags = frame.tags
    tags.fstd_tweak = { width = w, height = h }
    frame.tags = tags
  end
  if ui.theme then ui.theme.follow(frame) end
  apply_size(player, frame, w - LEFT_W, h - frame_h(player))
end

--- "Docked" on or off: the window built again that way
function M.set_docked(player, on)
  inside[player.index] = (not on) or nil
  M.rebuild(player)
end

--- the window built again (another layout, its first size), its mode, recipe and machine and the last plan kept
function M.rebuild(player)
  local frame = player.gui.screen[NAME]
  if not frame then return end
  local mode = mode_of(frame)
  local recipe, machine = value(frame, "bpgen_recipe"), value(frame, "bpgen_machine")
  M.open(player, { mode = mode, recipe = recipe, machine = machine and machine.name,
    machine_quality = machine and machine.quality, rebuilt = true })
  local r = results[player.index]
  if r and r.blueprint and r.res then M.on_result(player, r.res) end
end

-- (tests: a setting changed, as if by the player)
function M.touch(player_index, name)
  changed({ element = find(game.get_player(player_index).gui.screen[NAME], name), player_index = player_index })
end

-- (tests: a drop-down's item picked by the player, by the element's name (in the window or a frame stuck to it))
function M.pick(player_index, name, index)
  local el = find(game.get_player(player_index).gui.screen[NAME], name)
  if not el then return false end
  el.selected_index = index
  M.handlers[defines.events.on_gui_selection_state_changed]({ element = el, player_index = player_index })
  return true
end

-- (tests: a button press, as its tags)
function M.click(player_index, tags, as_button)
  on_click({ element = { valid = true, tags = tags }, player_index = player_index, scripted = not as_button })
end

M.handlers = {
  [defines.events.on_tick] = function(e)
    bench.handlers[defines.events.on_tick](e)
    warm_tick()
    -- (a fast-forward no test run needs any more, however it ended: a run dropped, a script reloaded: off)
    if storage.bpgen_speed_was and e.tick % 60 == 0 and not testing() then
      test_speed_off(nil)
      for _, p in pairs(game.players) do test_speed_off(p.gui.screen[NAME]) end
    end
    camera_tick()
  end,
  -- (a player gone: their test run, and its fast-forward, with them)
  [defines.events.on_player_left_game] = function(e) test_stop(e.player_index) end,
  [defines.events.on_gui_hover] = function(e)
    if e.element and e.element.valid and e.element.name == "bpgen_grip" then grip_hover[e.player_index] = e.element end
    if e.element and e.element.valid and e.element.name == "bpgen_cam" then
      cam_hover[e.player_index], wheel_seen[e.player_index], left_during[e.player_index] = e.element, nil, nil
    end
  end,
  [defines.events.on_gui_leave] = function(e)
    if e.element and e.element.valid and e.element.name == "bpgen_grip" then
      if resizing[e.player_index] then resizing[e.player_index].left = true else grip_hover[e.player_index] = nil end
    end
    if e.element and e.element.valid and e.element.name == "bpgen_cam" and safe.has("std") then
      pcall(native.call, "std", "wheel_capture", "0")  -- (the wheel back to the game at once)
    end
    -- (a drag goes on outside the preview until the button is let go)
    if e.element and e.element.valid and e.element.name == "bpgen_cam" then
      if drags[e.player_index] then left_during[e.player_index] = true else cam_hover[e.player_index] = nil end
    end
  end,
  [defines.events.on_gui_click] = on_click,
  [defines.events.on_player_cursor_stack_changed] = function(e) M.on_cursor(game.get_player(e.player_index)) end,
  [defines.events.on_gui_elem_changed] = function(e)
    changed(e)
    on_elem_changed(e)
    M.restrict(game.get_player(e.player_index))  -- (pickers it added)
  end,
  [defines.events.on_gui_confirmed] = function(e)
    local n = e.element and e.element.valid and e.element.name
    if n == "bpgen_rate" or n == "bpgen_per_row" or n == "bpgen_spm" then M.plan(game.get_player(e.player_index)) end
  end,
  [defines.events.on_gui_text_changed] = changed,
  [defines.events.on_gui_checked_state_changed] = function(e)
    changed(e)
    local el = e.element
    if el and el.valid and el.name == "bpgen_only_unlocked" then
      return M.set_only_unlocked(game.get_player(e.player_index), el.state)
    end
    if el and el.valid and el.name == "bpgen_docked" then
      return M.set_docked(game.get_player(e.player_index), el.state)
    end
    if el and el.valid and el.name == "bpgen_web" then  -- (the web window instead, or back)
      local player = game.get_player(e.player_index)
      web.set_wanted(player, el.state)
      return M.rebuild_any(player)
    end
    if el and el.valid and el.tags and el.tags.bpgen_make_row then
      local where = el.parent["bpgen_where"]
      where.caption = T(el.state and "made-here" or "on-belt")
      note_color(where, el.state)
      return
    end
    local target = el and el.valid and el.tags and el.tags.bpgen_toggle
    if not target then return end
    local player = game.get_player(e.player_index)
    local frame = player.gui.screen[NAME]
    find(frame, target).visible = el.state
    if target == "bpgen_make" or target == "bpgen_also" then tree(player, frame) end
  end,
  [defines.events.on_gui_selection_state_changed] = function(e)
    changed(e)
    local el = e.element
    if el and el.valid and el.name == "bpgen_size" and el.tags.fstd_scale_pick == NAME and stdwin.set_scale then
      -- (the window's Size: drawn at it, the preview fitted to the room it leaves; the library hears it after us)
      local c = ui.theme.themes.SCALE_CHOICES[el.selected_index - 1]
      local player = game.get_player(e.player_index)
      stdwin.set_scale(player, NAME, c and c / 100 or nil)
      return reflow(player)
    end
    if el and el.valid and el.tags and el.tags.bpgen_section then
      -- (another print of the book in the preview)
      local player = game.get_player(e.player_index)
      local r = results[player.index]
      local frame = player.gui.screen[NAME]
      if r and frame then
        local center, w, h, where = build_preview(player, r.blueprint, nil, el.items[el.selected_index])
        if center then
          local cam = find(frame, "bpgen_cam")
          r.center, r.zoom, r.where, r.w, r.h = center, fit(player, w, h), where, w, h
          cam.position, cam.zoom = center, r.zoom
        end
      end
      return
    end
  end,
  -- (the room the window has changed: the game's UI scale, the screen, the Window size setting)
  [defines.events.on_player_display_scale_changed] = function(e) reflow(game.get_player(e.player_index)) end,
  [defines.events.on_player_display_resolution_changed] = function(e) reflow(game.get_player(e.player_index)) end,
  [defines.events.on_runtime_mod_setting_changed] = function(e)
    if e.setting == "fstd-window-scale" and e.player_index then reflow(game.get_player(e.player_index)) end
    if e.setting == "bpgen-web-window" and e.player_index then  -- (the web window or the Lua one: built again so)
      local player = game.get_player(e.player_index)
      if player.gui.screen[NAME] then M.rebuild_any(player)
      elseif not web.wanted(player) then web.close(player, true) end  -- (a shell kept hidden for it: closed)
    end
  end,
  [defines.events.on_lua_shortcut] = function(e)
    if e.prototype_name ~= "bpgen-window" then return end
    local player = game.get_player(e.player_index)
    local f = player.gui.screen[NAME]
    -- (a tool in hand: its card shows, the window hides; back with the tool put away)
    if f and player.gui.screen[CARD] then player.clear_cursor() M.tool_done(player)
    elseif f then close(player) else M.open(player) end
  end,
  ["bpgen-window"] = function(e)
    local player = game.get_player(e.player_index)
    local f = player.gui.screen[NAME]
    if f and player.gui.screen[CARD] then player.clear_cursor() M.tool_done(player)
    elseif f then close(player) else M.open(player) end
  end,
}

-- (fse-std 0.8+: the theme window's Window size, or a script's set_window_scale)
if stdwin and stdwin.on_scale then
  M.handlers["fstd-scale-changed"] = function(e) reflow(game.get_player(e.player_index)) end
end

--- for other mods (AI Crew's goals): Extend's request for a line of `item` at `rate` a minute next to the base
function M.extend_request(player, item, rate)
  local u = unlocked(player)
  local belt = u.belts[#u.belts]
  return { tick = game.tick, surface = player.surface.name, bonuses = ctx.bonuses(player.force), inserters = u.inserters,
    belts = u.belts, poles = u.poles, belt = belt,
    params = { mode = "extend", item = item, rate_per_min = rate, belt = belt, bus = "auto",
      assembler = best_of_type(player, "assembling-machine", "crafting"), furnace = best_of_type(player, "furnace", "smelting") } }
end

--- the inserter setups a plan asked to measure, measured (bench.on_done then hands them to ctx.measured)
function M.calibrate(player, measure)
  preview_surface()
  bench.calibrate(player.index, measure)
end

--- a plan's ghosts placed now: at its box next to the base when it has one, else around the player; -> how many
function M.place_plan(player, res)
  local box = res.absolute and res.absolute.box
  local ghosts = place.place(player, res.blueprint, not box and player.position or nil, box)
  placed[player.index] = ghosts
  return #ghosts, box
end

--- the window built again as it should be now (the web page or the Lua window: the setting, the shell there or not),
--- its mode, recipe and last plan kept
function M.rebuild_any(player)
  if player.gui.screen[NAME] then return M.rebuild(player) end
  web.close(player)
  web.set_model(player, false)
end

--- the preview got another size (the web window's island, the window's scale): Fit's zoom follows, and the view too
--- while it shows the whole plan
function M.view_resized(player)
  local r = results[player.index]
  if not (r and r.w) then return end
  local old = r.zoom
  r.zoom = fit(player, r.w, r.h)
  local cam = find(player.gui.screen[NAME], "bpgen_cam")
  if cam and old and math.abs(cam.zoom - old) < 1e-6 then cam.zoom = r.zoom end
end

-- the sandbox's view of this file
sandbox.setup({
  shown = function(player, on) window_shown(player, on) end,
  card = sandbox_card,
  placed = function(player, ghosts, marks, area, removed)
    for _, e in ipairs(removed) do marks[#marks + 1] = e end
    placed[player.index], unmark[player.index] = ghosts, marks
    local n = 0
    for _, g in pairs(ghosts) do
      if g.valid and (g.type == "entity-ghost" or g.type == "tile-ghost") then n = n + 1 end
    end
    place.outline(player, { { area[1], area[2] }, { area[3], area[4] } }, T("from-sandbox"))
    say(player, T("sandbox-placed", n, #removed))
    local r = results[player.index]
    if r and r.blueprint then ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add") end
  end,
})

-- the web window's view of this file
M.NAME = NAME
web.setup({ NAME = NAME, find = find, on_click = on_click, handlers = M.handlers, results = results,
  test_series = test_series, histories = histories, ghosts_left = ghosts_left, close = close,
  set_only_unlocked = function(player, on) M.set_only_unlocked(player, on) end,
  history_use = history_use, view_resized = function(player) M.view_resized(player) end,
  rebuild_any = function(player) M.rebuild_any(player) end })
-- (its events, after the window's own)
for ev, fn in pairs(web.handlers) do
  local own = M.handlers[ev]
  M.handlers[ev] = own and function(e) own(e) fn(e) end or fn
end

return M
