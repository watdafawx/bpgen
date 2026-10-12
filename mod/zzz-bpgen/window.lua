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
local FRAME_W, FRAME_H = 360, 366  -- (the window around the preview: the tabs, the left panel, the rows under it)
-- (with fse-std's layouts, what sticks out of the window: the rails left and right, the dock below; GUI units)
local OUT_SIDE, OUT_BELOW = 106, 32
local INFO_H = 220                   -- (the scroll area under the preview, at most)
local LEFT_W = 344                   -- (the library window's width besides the preview: the left panel, borders)
-- the preview's size: the player's own (resized), else a share of the screen; three presets for the size button
local PRESETS = { { 0.45, 0.5 }, { 0.6, 0.66 }, { 0.75, 0.78 } }
local function cam_size(player)
  storage.bpgen_cam = storage.bpgen_cam or {}
  local s = storage.bpgen_cam[player.index]
  if s then return s.w, s.h end
  local res, scale = player.display_resolution, player.display_scale
  return math.max(520, math.floor(res.width / scale * PRESETS[1][1])), math.max(380, math.floor(res.height / scale * PRESETS[1][2]))
end

local find  -- (below)
local docked  -- (below: does this player's window have its tabs and toolbars outside it)
--- the preview (and what's sized by it) at w x h, kept for this player
local function apply_size(player, frame, w, h)
  -- (never bigger than the screen leaves room for: the left panel and the rows under the preview)
  local res, scale = player.display_resolution, player.display_scale
  local out = docked(player)
  local side, below = out and OUT_SIDE or 0, out and OUT_BELOW or 0
  local max_w = res.width / scale - LEFT_W - 2 * side - 8
  local max_h = res.height / scale - FRAME_H - below - 20
  w, h = math.max(420, math.floor(math.min(w, max_w))), math.max(300, math.floor(math.min(h, max_h)))
  -- (the library's window: kept to that size too, the frames stuck to it following)
  local tw = stdwin and frame.tags.fstd_tweak
  if tw and (tw.width > w + LEFT_W or tw.height > h + FRAME_H) then
    local W, H = math.min(tw.width, w + LEFT_W), math.min(tw.height, h + FRAME_H)
    local saved = storage.fstd_windows and storage.fstd_windows[player.index] and storage.fstd_windows[player.index][NAME]
    if saved then saved.width, saved.height = W, H end
    frame.style.width, frame.style.height = W, H
    local tags = frame.tags
    tags.fstd_tweak = { width = W, height = H }
    frame.tags = tags
  end
  storage.bpgen_cam = storage.bpgen_cam or {}
  storage.bpgen_cam[player.index] = { w = w, h = h }
  for _, n in ipairs({ "bpgen_cam", "bpgen_empty" }) do
    local el = find(frame, n)
    -- (in the library's window the preview stretches into what the window leaves it, both ways)
    if el and not stdwin then el.style.width, el.style.height = w, h end
  end
  for n, d in pairs({ bpgen_test_status = 40, bpgen_stats = 48, bpgen_extra = 24, bpgen_notes = 40 }) do
    local el = find(frame, n)
    if el then el.style.maximal_width = w - d end
  end
  -- (and back on the screen if it grew past its edge: the rails and the dock too)
  local loc = frame.location
  if loc then
    local x = math.max(side * scale, math.min(loc.x, res.width - (w + LEFT_W + side) * scale))
    local y = math.max(0, math.min(loc.y, res.height - (h + FRAME_H + below) * scale))
    if x ~= loc.x or y ~= loc.y then frame.location = { x = x, y = y } end
  end
end

local bench = require("bench")
local safe = require("safe")
local place = require("place")
local ctx          -- from control.lua: craftable, unlocked, bonuses, start, measured
-- (per-player state in storage: in multiplayer every peer must show and place the same; see safe.stored)
local results = safe.stored("results") -- player index -> { blueprint, res, center, zoom }
local placed = safe.stored("placed")     -- player index -> the ghosts "Place near me" and "Next to my base" made
local unmark = safe.stored("unmark")     -- player index -> what those pastes marked for deconstruction (Undo unmarks it)
-- (the preview's drag, zoom and size grip: momentary, not saved)
local cam_hover, drags, wheel_seen, left_during = {}, {}, {}, {}
local grip_hover, resizing, resized_at = {}, {}, {}
local unlocks = safe.stored("unlocks") -- player index -> what the force can build (read once per window)

function M.setup(c) ctx = c end

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
  for _, c in pairs(ui.attached(game.get_player(el.player_index), NAME)) do
    f = find_in(c, name)
    if f then return f end
  end
end

local function speed(proto, quality)
  local ok, s = pcall(proto.get_crafting_speed, quality)
  return ok and s or proto.crafting_speed or 1
end

local function unlocked(player)
  local u = unlocks[player.index]
  if not u then
    local made = ctx.craftable(player.force)
    u = { machines = ctx.unlocked(made, { "assembling-machine", "furnace" }), belts = ctx.unlocked(made, { "transport-belt" }),
          inserters = ctx.unlocked(made, { "inserter" }), poles = ctx.unlocked(made, { "electric-pole" }) }
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
    return nil, "the blueprint string didn't import"
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
    return nil, "nothing to show"
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
  if not x1 then return nil, "nothing was built" end
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

local function fit(player, w, h)
  local scale = player.display_scale
  local cw, ch = cam_size(player)
  local z = math.min(cw * scale / ((w + 2) * 32), ch * scale / ((h + 2) * 32))
  return math.max(0.05, math.min(2, z))
end

---------------------------------------------------------------------------------------------------------------------
-- the window

-- (in groups: making things, a base, reworking a base, space; short names, the full one as their tooltip)
local MODES = { "line", "mall", "extend", "base", "busdesign", "refit", "move", "ship" }
local MODE_NAMES = { "Line", "Mall", "Extend", "Base", "Bus", "Refit", "Move", "Ship" }
local MODE_TIPS = { "One recipe at a rate, on the belt you pick", "Many products, a chest each",
  "Make an item next to your base, fed by its belts", "Science per minute, up to the labs",
  "Ore patches to plates on a main bus", "Tidy a part of your base that grew all over the place",
  "Move a section of your base, its belts reconnected", "A space platform round its hub (Space Age)" }
local GROUP_START = { base = true, refit = true, ship = true }  -- (a gap above them on the rail)
local RAIL_W, RAIL_H = 112, #MODES * 34 + 3 * 12 + 8          -- (the rail of mode tabs, GUI units)
local DOCK_W, DOCK_H = 720, 44                                  -- (the blueprint's actions, below the window)
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

--- a section folded unless `open`, a badge in its heading saying what's in it (themed); returns its body
local function fold(parent, title, open, badge_name, badge_text)
  local body = ui.fold(parent, title, open)
  if body.name == "fstd_fold_body" then
    local f = body.parent
    ui.theme.apply(f, "section", { horizontally_stretchable = true })
    local head = f.children[1]
    head.style.horizontally_stretchable = true
    head.add({ type = "empty-widget" }).style.horizontally_stretchable = true
    ui.badge(head, badge_text).name = badge_name
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
function docked(player) return ui.layouts and not inside[player.index] end

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
local function recipe_filters(force)
  local groups, by_sig = {}, {}
  local function ok(r) return r.enabled and not r.hidden end
  for _, r in pairs(force.recipes) do
    local p = ok(r) and r.products[1]
    if p then
      local g = { recipe = r, has("product", p.type .. "/" .. p.name),
                  { filter = "category", category = r.category, mode = "and" },
                  { filter = "subgroup", subgroup = r.subgroup.name, mode = "and" },
                  { filter = "energy", comparison = "=", value = r.energy, mode = "and" } }
      g[1].mode = nil
      groups[#groups + 1] = g
      local s = table.concat({ r.category, r.subgroup.name, r.energy }, "|")
      by_sig[s] = by_sig[s] or {}
      table.insert(by_sig[s], g)
    end
  end
  local function flat()
    local f = {}
    for _, g in ipairs(groups) do for _, x in ipairs(g) do f[#f + 1] = x end end
    return f
  end
  local f = flat()
  local leaked = false
  for name in pairs(#f > 0 and prototypes.get_recipe_filtered(f) or {}) do
    local l = force.recipes[name]
    if l and not ok(l) and not l.hidden then
      leaked = true
      local li, lp = stuff(l.ingredients), stuff(l.products)
      for _, g in ipairs(by_sig[table.concat({ l.category, l.subgroup.name, l.energy }, "|")] or {}) do
        local ri, rp = stuff(g.recipe.ingredients), stuff(g.recipe.products)
        local clause
        for k in pairs(li) do if not clause and not ri[k] then clause = has("ingredient", k, true) end end
        for k in pairs(lp) do if not clause and not rp[k] then clause = has("product", k, true) end end
        for k in pairs(ri) do if not clause and not li[k] then clause = has("ingredient", k) end end
        for k in pairs(rp) do if not clause and not lp[k] then clause = has("product", k) end end
        if clause then g[#g + 1] = clause end
      end
    end
  end
  return leaked and flat() or f
end

-- the filters that keep a picker to what the force has unlocked, by its elem_type
local function unlocked_filters(force)
  local items, entities = {}, {}
  local recipe = recipe_filters(force)
  for name in pairs(ctx.craftable(force)) do
    items[#items + 1] = name
    local proto = prototypes.item[name]
    local place = proto and proto.place_result
    if place then entities[#entities + 1] = place.name end
  end
  local item = { { filter = "name", name = items } }
  local entity = { { filter = "name", name = entities } }
  return { recipe = recipe, ["recipe-with-quality"] = recipe, item = item, ["item-with-quality"] = item,
           entity = entity, ["entity-with-quality"] = entity }
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
end

--- what an action did: in the status line and as a toast (a message at the top that goes away by itself)
local function say(player, text)
  local frame = player.gui.screen[NAME]
  if frame then status(frame, text) end
  ui.toast(player, text)
end

--- the window themed, the frames stuck to it too (call after adding elements: only new ones are themed)
local function adopt(player)
  local frame = player.gui.screen[NAME]
  if not frame then return end
  ui.adopt(frame)
  for _, c in pairs(ui.attached(player, NAME)) do ui.adopt(c) end
end

--- the window closed, and what's stuck to it
local function close(player)
  for _, c in pairs(ui.attached(player, NAME)) do c.destroy() end
  if player.gui.screen[NAME] then player.gui.screen[NAME].destroy() end
end
M.close = close

--- Plan breathes (and a note says so) when the settings changed since the last plan
local function stale(player, on)
  local frame = player.gui.screen[NAME]
  local plan, note = find(frame, "bpgen_plan"), find(frame, "bpgen_stale")
  if not (plan and note) then return end
  note.visible = on
  ui.set_role(plan, on and "pulse" or "ok")
end

--- the folds' badges: what's set in them
local function badges_of(frame)
  local n = 0
  for _, name in ipairs({ "bpgen_module", "bpgen_beacon", "bpgen_beacon_module" }) do
    local el = find(frame, name)
    if el and el.elem_value then n = n + 1 end
  end
  ui.set_badge(find(frame, "bpgen_modules_badge"), n > 0 and (n .. " set") or "none")
  local more = find(frame, "bpgen_more_toggle")
  ui.set_badge(find(frame, "bpgen_tune_badge"), more and more.state and "set" or "auto")
end

local glows = safe.stored("glows")  -- player index -> the preview's glow (fse-std's overlay id)
--- the preview glowing (an accent outline, the fse loader's draw plugin) while bpgen plans or tests on it
local function glow(player, on)
  ui.unglow(glows[player.index])
  glows[player.index] = nil
  local cam = on and find(player.gui.screen[NAME], "bpgen_cam")
  if cam then glows[player.index] = ui.glow(cam) end
end

--- a change in the window's settings after a plan: Plan breathes
local function changed(e)
  local el = e.element
  if not (el and el.valid) or el.name == "bpgen_only_unlocked" or el.name == "bpgen_section"
    or el.name == "bpgen_docked" then return end
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
    if m == mode then find(frame, "bpgen_mode_tip").caption = MODE_TIPS[i] end
  end
  M.steps(frame)
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
    lines[#lines + 1] = i .. ". " .. (#t > 0 and table.concat(t, "  ") or "no ore")
  end
  note.caption = #lines > 0 and table.concat(lines, "\n") or "No patches picked yet."
end

-- a bpgen tool in hand: the window (and what's stuck to it) out of the way, a small card at the top says what to
-- drag; the window back once the tool leaves the hand (a drag done, Cancel, Q)
local CARD = "bpgen_tool_card"
local TOOLS = {
  ["bpgen-refit"] = { title = "Refit", text = "Drag over the part of your base to lay out again as a compact mall." },
  ["bpgen-move"] = { title = "Move", text = "Stand where the section should go, then drag over the section." },
  ["bpgen-snapshot"] = { title = "Snapshot", text = "Drag over your base: bpgen plans new builds that fit next to it." },
  ["bpgen-patches"] = { title = "Ore patches", text = "Drag over each ore patch the bus should mine, then press Done.",
    many = true },
}
M.TOOLS = TOOLS

local function patch_count(player)
  local n = #(patch_areas[player.index] or {})
  return n == 0 and "No patches picked yet." or (n == 1 and "1 patch picked." or (n .. " patches picked."))
end

--- the window and its stuck frames shown or hidden
local function window_shown(player, on)
  local frame = player.gui.screen[NAME]
  if frame then frame.visible = on end
  for _, c in pairs(ui.attached(player, NAME)) do c.visible = on end
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
  ui.add(card, { type = "label", caption = "[item=" .. item .. "]  bpgen: " .. tool.title }, "caption")
  local text = ui.add(card, { type = "label", caption = tool.text }, "text")
  text.style.single_line = false
  text.style.maximal_width = 356
  ui.add(card, { type = "label", name = "bpgen_card_note", caption = tool.many and patch_count(player)
    or "Q or Cancel puts the tool away." }, "dim")
  local row = card.add({ type = "flow", direction = "horizontal" })
  row.style.top_margin = 6
  row.style.vertical_align = "center"
  row.add({ type = "empty-widget" }).style.horizontally_stretchable = true
  if tool.many then
    ui.add(row, { type = "button", caption = "Done", tags = { bpgen = "tool_done" } }, "ok")
  end
  ui.add(row, { type = "button", caption = "Cancel", tags = { bpgen = "tool_done" } }, "button")
  ui.adopt(card)
  local scale = player.display_scale
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
  unlocks[player.index] = nil
  local screen = player.gui.screen
  close(player)
  local frame, holder
  if stdwin then
    local cw, ch = cam_size(player)
    frame, holder = stdwin.create(player, { name = NAME, title = "bpgen", width = cw + LEFT_W, height = ch + FRAME_H,
      min_width = 420 + LEFT_W, min_height = 300 + FRAME_H, themed = ui.themed, settings = ui.menus })
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
  local rail = out and ui.attach(frame, "left", { size = { RAIL_W, RAIL_H }, along = 40, overlap = 6, role = "inset",
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
    ui.add(tabs, { type = "button", caption = MODE_NAMES[i], tooltip = MODE_TIPS[i], toggled = i == 1,
      tags = { bpgen = "tab", mode = m } }, rail and "rtab" or "tab",
      rail and { width = RAIL_W - 8, height = 30, top_margin = GROUP_START[m] and 12 or nil }
        or { minimal_width = 70, height = 34 })
  end
  -- what the mode does, and "Only unlocked"
  local top = holder.add({ type = "flow", name = "bpgen_top", direction = "horizontal" })
  top.style.vertical_align = "center"
  top.style.left_padding, top.style.right_padding = 6, 6
  ui.add(top, { type = "label", name = "bpgen_mode_tip", caption = MODE_TIPS[1] }, "dim")
  top.add({ type = "empty-widget" }).style.horizontally_stretchable = true
  if ui.menus then  -- (the window's settings are behind the gear by its close button: a mark while a filter is on)
    ui.badge(top, "only unlocked", "Pickers offer only what you can make now (the gear by the close button)").name =
      "bpgen_unlocked_badge"
    find_in(top, "bpgen_unlocked_badge").visible = only_unlocked[player.index] == true
  else
    ui.add(top, { type = "checkbox", name = "bpgen_only_unlocked", caption = "Only unlocked",
      state = only_unlocked[player.index] == true,
      tooltip = "Pickers offer only recipes you can craft and what you can build now" }, "check")
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
  local sec = section(line, "Recipe")
  local fs = fields(sec)
  field(fs, "Recipe").add({ type = "choose-elem-button", elem_type = "recipe", name = "bpgen_recipe",
    elem_filters = { { filter = "hidden", invert = true } } })
  field(fs, "Machine", "and its quality").add({ type = "choose-elem-button", elem_type = "entity-with-quality",
    name = "bpgen_machine", elem_filters = { { filter = "crafting-machine" } } })
  field(fs, "Per minute", "Items a minute; empty: as much as one belt carries").add({ type = "textfield",
    name = "bpgen_rate", numeric = true, allow_decimal = true, lose_focus_on_confirm = true }).style.width = 90
  fs = fields(sec)
  field(fs, "Belt", "empty: the fastest you have").add({ type = "choose-elem-button", elem_type = "entity",
    name = "bpgen_belt", elem_filters = { { filter = "type", type = "transport-belt" } } })
  field(fs, "Feed", "my main bus: stand by your bus; what it carries is tapped from its lanes, the other ingredients are made in the blueprint, and the line is placed beside it").add({
    type = "drop-down", name = "bpgen_feed", items = { "belts", "robots (logistic chests)", "my main bus" },
    selected_index = 1 })

  sec = section(line, "Make in this blueprint",
    "Tick an ingredient to make it inside this blueprint too (its recipe on the right); unticked ones come in on a belt")
  sec.add({ type = "flow", name = "bpgen_make", direction = "vertical" }).add({ type = "label", caption = "(pick a recipe)" })

  -- (what's seldom set: folded, a badge saying what's in it)
  sec = fold(line, "Also make", false, "bpgen_also_badge", "-")
  sec.add({ type = "flow", name = "bpgen_also", direction = "vertical" }).add({ type = "label", caption = "(pick a recipe)" })

  sec = fold(line, "Modules", prefill.modules ~= nil, "bpgen_modules_badge", "none")
  fs = fields(sec)
  field(fs, "Machine modules", "fills every module slot").add({ type = "choose-elem-button", elem_type = "item-with-quality",
    name = "bpgen_module", elem_filters = { { filter = "type", type = "module" } } })
  field(fs, "Beacon", "and its quality (empty: no beacons)").add({ type = "choose-elem-button",
    elem_type = "entity-with-quality", name = "bpgen_beacon", elem_filters = { { filter = "type", type = "beacon" } } })
  field(fs, "Beacon modules").add({ type = "choose-elem-button", elem_type = "item-with-quality",
    name = "bpgen_beacon_module", elem_filters = { { filter = "type", type = "module" } } })

  sec = fold(line, "Fine-tune", prefill.more == true, "bpgen_tune_badge", "auto")
  sec.add({ type = "checkbox", name = "bpgen_more_toggle", caption = "Inserters, machines a row", state = false,
    tags = { bpgen_toggle = "bpgen_more" } })
  local more = sec.add({ type = "flow", name = "bpgen_more", direction = "vertical", visible = false })
  local ins = { { filter = "type", type = "inserter" } }
  fs = fields(more)
  field(fs, "Input", "the inserters feeding the machines (empty: bpgen picks)").add({ type = "choose-elem-button",
    elem_type = "entity", name = "bpgen_near", elem_filters = ins })
  field(fs, "Long-handed", "the ones reaching the far belt").add({ type = "choose-elem-button", elem_type = "entity",
    name = "bpgen_far", elem_filters = ins })
  field(fs, "Output").add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_out", elem_filters = ins })
  field(more, "Machines per row", "empty: what fills a belt").add({ type = "textfield", name = "bpgen_per_row",
    numeric = true, lose_focus_on_confirm = true }).style.width = 60

  -- a mall: many products into chests
  local mall = left.add({ type = "flow", name = "bpgen_mall_box", direction = "vertical", visible = false })
  local sec = section(mall, "Products", "Each product gets its machine and a chest; an empty slot is always at the end")
  sec.add({ type = "table", name = "bpgen_products", column_count = 6 }).add({ type = "choose-elem-button",
    elem_type = "recipe", tags = { bpgen_product = true }, elem_filters = { { filter = "hidden", invert = true } } })
  sec = section(mall, "Machines & chests")
  local fs = fields(sec)
  field(fs, "Machine", "empty: one that makes every product").add({ type = "choose-elem-button", elem_type = "entity",
    name = "bpgen_mall_machine", elem_filters = { { filter = "crafting-machine" } } })
  field(fs, "Chest").add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_chest",
    elem_filters = { { filter = "type", type = { "container", "logistic-container" } } } })
  fs = fields(sec)  -- (its own row: its longest item is wide)
  field(fs, "Fed by", "mixed belts: columns of machines between mixed belts, gears, cable, circuits... made at the top, only plates come in. My main bus: the same, the parts your bus carries taken from it, placed beside it. A belt loop: plate belts and one mixed loop carrying every part, for products that don't share their inputs (modded malls; fluids piped in too); from my bus: the same beside your bus. Rows: the older layout, its own 4-lane bus").add({
    type = "drop-down", name = "bpgen_mall_feed", items = { "rows on a 4-lane bus", "robots", "mixed belts", "my main bus",
      "the base in my hand", "a belt loop", "a belt loop, from my bus" },
    selected_index = 3 })
  fs = fields(sec)
  field(fs, "Buffer", "crafts each machine keeps ahead").add({ type = "textfield", name = "bpgen_mall_buffer", text = "5",
    numeric = true, lose_focus_on_confirm = true }).style.width = 60
  field(fs, "Chest limit", "slots each product's chest may fill").add({ type = "textfield", name = "bpgen_chest_limit",
    text = "4", numeric = true, lose_focus_on_confirm = true }).style.width = 60
  field(fs, "Keep", "mixed belts: each machine takes from the belts only while its chest has fewer than this (wired to the chest), so the ones down a column get plates soon; 0: no limit").add({
    type = "textfield", name = "bpgen_mall_stock", text = "10", numeric = true, lose_focus_on_confirm = true }).style.width = 60
  -- refit: an area of the base laid out again (its own mode: Plan gives the refit tool)
  local refitb = left.add({ type = "flow", name = "bpgen_refit_box", direction = "vertical", visible = false })
  sec = section(refitb, "Refit an area", "A part of your base that grew all over the place: drag over it, and its machines are laid out again, compact, in its place (fed from your main bus when there is one). Place it marks the old ones for removal")
  ui.add(sec, { type = "label", caption = "Press Plan, then drag over the area with the refit tool." }, "dim",
    { single_line = false, maximal_width = 260 })
  fs = fields(sec)
  field(fs, "Refit as", "a mall: the area's products, compact. A base on a bus: the science the area makes (its labs and what feeds them), rebuilt as a main-bus base").add({
    type = "drop-down", name = "bpgen_refit_as", items = { "a mall", "a base on a bus" }, selected_index = 1 })
  fs = fields(sec)
  field(fs, "Layout", "As a mall: Compact puts every part on one belt loop, its inserters wired so the loop never jams and each chest keeps a few. Side by side: columns of machines between plain belts, parts made at the top and handed straight down, no circuits").add({
    type = "drop-down", name = "bpgen_refit_layout", items = { "compact loop (circuits)", "side by side (no circuits)" },
    selected_index = 1 })
  fs = fields(sec)
  fs.add({ type = "checkbox", name = "bpgen_refit_add", caption = "Add machines", state = false,
    tooltip = "Off: the area's machines only (what none of them makes comes in on the belts). On: the mall makes the parts it needs, more machines" })

  -- a starter base: science
  local base = left.add({ type = "flow", name = "bpgen_base_box", direction = "vertical", visible = false })
  sec = section(base, "Science")
  fs = fields(sec)
  field(fs, "Per minute", "each pack, a minute").add({ type = "textfield", name = "bpgen_spm", text = "30", numeric = true,
    allow_decimal = true, lose_focus_on_confirm = true }).style.width = 60
  field(fs, "Lab").add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_lab",
    elem_filters = { { filter = "type", type = "lab" } } })
  fs = fields(sec)
  fs.add({ type = "checkbox", name = "bpgen_base_military", caption = "Military", state = false,
    tooltip = "Military science too" })
  fs.add({ type = "checkbox", name = "bpgen_base_chemical", caption = "Chemical (blue)", state = false,
    tooltip = "Chemical science too: plastic and sulfur from your bus (a bus design with an oil field), or made here" })
  sec = section(base, "Buildings", "empty: bpgen's starter picks")
  fs = fields(sec)
  field(fs, "Assembler").add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_assembler",
    elem_filters = { { filter = "type", type = "assembling-machine" } } })
  field(fs, "Furnace").add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_furnace",
    elem_filters = { { filter = "crafting-category", crafting_category = "smelting" } } })
  sec = section(base, "Layout")
  sec.add({ type = "checkbox", name = "bpgen_base_mall", caption = "With a mall for its buildings", state = false })
  sec.add({ type = "checkbox", name = "bpgen_base_bus", caption = "As a main bus", state = false,
    tooltip = "One column of blocks beside a bus of belts that each block takes from and puts back onto; the mall takes from it too. Unticked: blocks in columns by recipe depth, the mall a print of its own" })
  sec.add({ type = "checkbox", name = "bpgen_base_plates", caption = "Plates in at the bus head", state = false,
    tooltip = "A main bus fed iron and copper plates, stone bricks... at its head (from the Bus design tab, or your smelters): no smelting in the base. Every main-bus base has a stone-brick C over its bus head; the next tier pastes onto it" })
  sec.add({ type = "checkbox", name = "bpgen_base_fit", caption = "Its head on my bus design", state = true,
    tooltip = "With plates in: its inputs right where the lanes of the bus you last planned (Bus design tab) end, belts from each lane to the input of its item; turned the way the bus flows; Place it puts it there. Lanes it doesn't need end there, for the next tier" })
  sec.add({ type = "checkbox", name = "bpgen_base_add", caption = "Add to the base in my hand", state = false,
    tooltip = "Hold a bpgen main-bus base (with its C): the science it doesn't make yet at this rate, as a base of its own beside it, its bus inputs on the same row. Paste it with its C on the old base's C. (A bigger base instead: just plan it and paste it over the old one, C on C)" })
  sec.add({ type = "checkbox", name = "bpgen_base_import", caption = "Rebuild the blueprint in my hand", state = false,
    tooltip = "a blueprint of your own base in your hand: rebuilt for the science it makes, with these buildings" })

  -- next to the base: a whole chain fed by what the base's belts carry, tapped in, powered
  local ext = left.add({ type = "flow", name = "bpgen_extend_box", direction = "vertical", visible = false })
  sec = section(ext, "Make", "the whole chain for it, fed by what your belts carry, placed next to your base")
  fs = fields(sec)
  field(fs, "Item").add({ type = "choose-elem-button", elem_type = "item", name = "bpgen_ext_item" })
  field(fs, "Per minute").add({ type = "textfield", name = "bpgen_ext_rate", text = "30", numeric = true,
    allow_decimal = true, lose_focus_on_confirm = true }).style.width = 60
  -- move: a section of the base somewhere else (its own mode: Plan gives the move tool)
  local moveb = left.add({ type = "flow", name = "bpgen_move_box", direction = "vertical", visible = false })
  sec = section(moveb, "Move a section", "Drag over a part of your base: it's previewed where you stand (the arrows move it), Place it builds it there, marks the old one for removal and runs its belts in and out to your base again")
  ui.add(sec, { type = "label", caption = "Stand where it should go, press Plan, then drag over the section with the move tool. Its settings and wires go with it." }, "dim", { single_line = false, maximal_width = 260 })
  sec = section(ext, "Your base")
  sec.add({ type = "button", caption = "Pick my base area", tags = { bpgen = "snapshot" },
    tooltip = "Optional: gives you the snapshot tool, to drag over the part of your base to build next to (its belts feed the new part). Without it, bpgen looks around you" })
  local snap_note = sec.add({ type = "label", name = "bpgen_snap_note", caption = "" })
  snap_note.style.single_line = false
  snap_note.style.maximal_width = 260

  -- a bus design: ore patches -> drills -> smelter columns -> balancers -> a main bus
  -- a ship: a space platform round its hub, self-sufficient (asteroids to fuel and ammo)
  local shipb = left.add({ type = "flow", name = "bpgen_ship_box", direction = "vertical", visible = false })
  sec = section(shipb, "Inner-planet shuttle", "Collectors, crushers, fuel and oxidizer, ammo and solar power round the platform's hub: plan it, stand on the platform and Place it (it lands on the hub)")
  fs = fields(sec)
  field(fs, "Thrusters").add({ type = "textfield", name = "bpgen_ship_thrusters", text = "2", numeric = true,
    lose_focus_on_confirm = true }).style.width = 60
  field(fs, "Gun turrets").add({ type = "textfield", name = "bpgen_ship_turrets", text = "6", numeric = true,
    lose_focus_on_confirm = true }).style.width = 60
  field(fs, "Collectors", "asteroid collectors in a row out front").add({ type = "textfield",
    name = "bpgen_ship_collectors", text = "4", numeric = true, lose_focus_on_confirm = true }).style.width = 60
  fs = fields(sec)
  field(fs, "Thrust", "pumps on the thrusters' way in, opened one tick in so many (a thruster burns fuel most "
    .. "efficiently nearly empty, half as efficiently full)\nAuto: what the fuel plant makes\nEfficient: slowest, "
    .. "least fuel\nHalf\nFull: fastest, runs dry").add({ type = "drop-down", name = "bpgen_ship_throttle",
    items = { "Auto", "Efficient", "Half", "Full" },
    selected_index = 1, tags = { values = { -1, 0, 0.5, 1 } } })
  local orbits, names = {}, {}
  for name, loc in pairs(prototypes.space_location or {}) do
    if loc.type == "planet" then orbits[#orbits + 1] = name end
  end
  table.sort(orbits)
  for i, n in ipairs(orbits) do names[i] = { "", "[space-location=" .. n .. "] ", prototypes.space_location[n].localised_name } end
  field(fs, "Solar sized for", "the orbit with the least sunlight it will be in (solar power in space differs)").add({
    type = "drop-down", name = "bpgen_ship_orbit", items = names, tags = { orbits = orbits },
    selected_index = math.max(1, (function() for i, n in ipairs(orbits) do if n == "nauvis" then return i end end end)() or 1) })
  local bus = left.add({ type = "flow", name = "bpgen_bus_box", direction = "vertical", visible = false })
  sec = section(bus, "Ore patches", "drills cover each patch; their ore runs on belts to smelters at the bus head")
  local pf = sec.add({ type = "flow", direction = "horizontal" })
  pf.add({ type = "button", caption = "Pick ore patches", tags = { bpgen = "patches" },
    tooltip = "Gives you the patch tool: drag over each ore patch to mine (one drag a patch)" })
  pf.add({ type = "button", caption = "Clear", tags = { bpgen = "patches_clear" } })
  local pnote = sec.add({ type = "label", name = "bpgen_patch_note", caption = "" })
  pnote.style.single_line = false
  pnote.style.maximal_width = 260
  sec = section(bus, "Bus", "starts near you and runs the way you pick; lanes in groups with free tiles between")
  fs = fields(sec)
  field(fs, "Flows").add({ type = "drop-down", name = "bpgen_bus_dir", items = { "north", "east", "south", "west" },
    selected_index = 1 })
  field(fs, "Length").add({ type = "textfield", name = "bpgen_bus_length", text = "40", numeric = true,
    lose_focus_on_confirm = true }).style.width = 50
  fs = fields(sec)  -- (a second row: four fields don't fit the panel's width)
  field(fs, "Belts a group").add({ type = "textfield", name = "bpgen_bus_group", text = "4", numeric = true,
    lose_focus_on_confirm = true }).style.width = 50
  field(fs, "Gap").add({ type = "textfield", name = "bpgen_bus_gap", text = "4", numeric = true,
    lose_focus_on_confirm = true }).style.width = 50
  sec.add({ type = "checkbox", name = "bpgen_bus_add", caption = "Add to my bus here", state = false,
    tooltip = "Stand by your main bus (a lane running dry: more of its item): the patches' ore is smelted upstream of the bus and goes onto new lanes laid along it" })
  sec.add({ type = "checkbox", name = "bpgen_bus_wood", caption = "A wood lane", state = true,
    tooltip = "A lane of wood beside the others, for wooden chests and small poles in a mall: fill the chest at its head (its burner inserter runs on the wood)" })
  sec.add({ type = "checkbox", name = "bpgen_bus_balance", caption = "Balancers", state = true,
    tooltip = "A 4-to-4 balancer at the start of each full group of 4 lanes of one item (2 lanes: a splitter)" })
  sec = section(bus, "Buildings", "empty: the electric mining drill and furnace, the fastest belt you have")
  fs = fields(sec)
  field(fs, "Drill", "3x3").add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_bus_drill",
    elem_filters = { { filter = "type", type = "mining-drill" } } })
  field(fs, "Furnace", "an electric furnace, or stone / steel (burner: coal goes onto each column's ore belt from a belt under them, fed by your coal patch or a chest)").add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_bus_furnace",
    elem_filters = { { filter = "crafting-category", crafting_category = "smelting" } } })
  field(fs, "Belt").add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_bus_belt",
    elem_filters = { { filter = "type", type = "transport-belt" } } })
  show_patches(frame, player)

  sec = section(left, "Next to my base", nil, "bpgen_next_box")
  sec.add({ type = "checkbox", name = "bpgen_ext_bus", caption = "Build from my main bus", state = true,
    tooltip = "Extend and \"Next to my base\": when your base has a main bus there (3+ long straight belts side by side, flowing the same way), build beside it, branching off its lanes, with a new lane for an output it doesn't carry yet, and the bus continued past the build when it ends before it. Unticked: free ground nearest the belts" })
  -- pinned under the panel's scroll, always in sight: planning's progress, what was said last, Plan (breathing when
  -- something changed since the last plan), the tools that look at the base
  local foot = outer.add({ type = "flow", name = "bpgen_foot", direction = "vertical" })
  foot.style.padding = { 6, 8, 8, 8 }
  foot.style.vertical_spacing = 4
  foot.style.horizontally_stretchable = true
  local prog = foot.add({ type = "flow", name = "bpgen_progress", direction = "vertical", visible = false })
  prog.style.horizontally_stretchable = true
  local bar = ui.progress(prog, 0, { caption = "Planning", text = "" })
  bar.name = "bpgen_progress_bar"
  bar.parent.style.horizontally_stretchable = true
  local details = ui.fold(prog, "Details", false, "bpgen_details")
  ui.add(details, { type = "label", name = "bpgen_log", caption = "" }, "dim",
    { single_line = false, maximal_width = 260, font = "default-small" })
  ui.add(foot, { type = "label", name = "bpgen_status" }, "text", { single_line = false, maximal_width = 270 })
  ui.add(foot, { type = "label", name = "bpgen_stale", caption = "Changed since the last plan: Plan again.",
    visible = false }, "caption", { single_line = false, maximal_width = 270 })
  ui.add(foot, { type = "button", name = "bpgen_plan", caption = "Plan", tags = { bpgen = "plan" } }, "ok",
    { horizontally_stretchable = true, height = 36 })
  local go = foot.add({ type = "flow", name = "bpgen_base_tools", direction = "horizontal" })  -- (looking at the base)
  go.style.horizontal_spacing = 4
  ui.add(go, { type = "button", caption = "What's short?", tags = { bpgen = "short" },
    tooltip = "What your factory uses faster than it makes (last 10 minutes): plan one of them" }, "button",
    { horizontally_stretchable = true, minimal_width = 0, height = 28 })
  ui.add(go, { type = "button", caption = "Check my bus", tags = { bpgen = "check_bus" },
    tooltip = "Stand by your main bus: what each item's lanes carry and how much of it is used, which run dry, what your base lacks that isn't on the bus; add a lane of something, or make more of it beside the bus" },
    "button", { horizontally_stretchable = true, minimal_width = 0, height = 28 })

  local right = body.add({ type = "frame", style = "inside_deep_frame", direction = "vertical" })
  local cam = right.add({ type = "camera", name = "bpgen_cam", position = { 0, 0 }, surface_index = player.surface.index, zoom = 1,
    raise_hover_events = true, tooltip = safe.has("std") and "Drag to move, mouse wheel to zoom" or nil })
  local CAM_W, CAM_H = cam_size(player)
  -- what the plan comes to: a few numbers under the preview
  local res, scale = player.display_resolution, player.display_scale  -- (a size kept from a bigger screen: fit it)
  CAM_W, CAM_H = math.min(CAM_W, res.width / scale - 380), math.min(CAM_H, res.height / scale - FRAME_H - 20)
  if not stdwin then
    cam.style.width = CAM_W
    cam.style.height = CAM_H
  end
  cam.visible = false
  local empty = right.add({ type = "label", name = "bpgen_empty", caption = "Pick a recipe and press Plan: the blueprint shows here, drawn by the game." })
  if not stdwin then
    empty.style.width = CAM_W
    empty.style.height = CAM_H
  end
  empty.style.horizontal_align = "center"
  empty.style.vertical_align = "center"
  local under = right.add({ type = "flow", direction = "vertical" })
  under.style.padding = 8
  under.style.vertical_spacing = 6
  -- the preview's tools: toolbars over the camera (fse-std's over(), with the fse loader), else a row under it
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
  tool({ type = "button", caption = "+", tags = { bpgen = "zoom", f = 1.25 }, tooltip = "Zoom in" }, "Zoom in")
  tool({ type = "button", caption = "−", tags = { bpgen = "zoom", f = 0.8 }, tooltip = "Zoom out" }, "Zoom out")
  tool({ type = "button", caption = "Fit", tags = { bpgen = "fit" }, tooltip = "The whole plan in view" }, "Fit",
    not vrail and { width = 40 } or nil)
  local arrows = vrail and tools.add({ type = "flow", direction = "horizontal" }) or tools
  if vrail then arrows.style.horizontal_spacing = 2 end
  for _, a in ipairs({ { "◀", -1, 0 }, { "▲", 0, -1 }, { "▼", 0, 1 }, { "▶", 1, 0 } }) do
    ui.add(arrows, { type = "button", caption = a[1], tags = { bpgen = "pan", dx = a[2], dy = a[3] },
      tooltip = "Move the view" }, vrail and "rtab" or "tool", vrail and { width = math.floor((RAIL_W - 14) / 4), height = 30,
      padding = 0 } or nil)
  end
  tools.add({ type = "button", name = "bpgen_grip", caption = "⇲", style = "tool_button", tags = { bpgen = "size" },
    raise_hover_events = true, tooltip = safe.has("std") and "Drag to resize the preview, click for the next size"
      or "Next size (small, medium, large)" })
  if not vrail then tools.add({ type = "empty-widget" }).style.horizontally_stretchable = true end
  ui.add(tools, { type = "button", name = "bpgen_test", caption = "Test run", tags = { bpgen = "test" }, enabled = false,
    tooltip = "Runs the line for real on the preview: inputs fed, output counted, starved machines marked" },
    vrail and "rtab" or "button", vrail and { width = RAIL_W - 8, height = 30, top_margin = 12 } or nil)
  -- the test run, live: the rate it reaches (a bar red to green by the share of the expected), a chart of it
  local tb = section(under, nil, nil, "bpgen_test_box")
  tb.visible = false
  local th = tb.add({ type = "flow", direction = "horizontal" })  -- (its heading, the fast-forward beside it)
  th.style.vertical_align = "center"
  ui.add(th, { type = "label", caption = "Test run" }, "caption")
  th.add({ type = "empty-widget" }).style.horizontally_stretchable = true
  ui.add(th, { type = "button", name = "bpgen_test_speed", caption = "×1", tags = { bpgen = "test_speed" }, visible = false,
    tooltip = "Fast-forward the test run: the whole game runs this much faster until the test ends (single player)" },
    "tool", { width = 44 })
  local tbar = ui.progress(tb, 0, { caption = "measured", text = "", auto = true })
  tbar.name = "bpgen_test_bar"
  tbar.parent.style.horizontally_stretchable = true
  tb.add({ type = "flow", name = "bpgen_test_graph", direction = "vertical" })
  ui.add(tb, { type = "label", name = "bpgen_test_status" }, "dim", { single_line = false, maximal_width = CAM_W - 24 })
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
  ui.add(tools, { type = "button", name = "bpgen_cursor", caption = "Blueprint to cursor", tags = { bpgen = "cursor" },
    enabled = false }, "ok")
  ui.add(tools, { type = "button", name = "bpgen_copy", caption = "Copy", tags = { bpgen = "copy" }, enabled = false,
    tooltip = "Copy the blueprint string" }, "button", small)
  tools.add({ type = "button", name = "bpgen_place", caption = "Place near me", tags = { bpgen = "place" }, enabled = false,
    tooltip = "Ghosts at a free spot next to you, inputs pointed at your belts that carry them" })
  tools.add({ type = "button", name = "bpgen_place_base", caption = "Next to my base", tags = { bpgen = "place_base" },
    enabled = false, tooltip = "Stand by the part of your base to build next to: bpgen looks around you, puts this on free ground beside it (beside your main bus when there is one), taps the belts that carry its inputs, takes its output where it's needed and links the power. Ghosts, ready for your bots" })
  tools.add({ type = "button", name = "bpgen_nudge_back", caption = "◀", style = "tool_button", visible = false,
    tags = { bpgen = "nudge", d = -1 }, tooltip = "Move it back along your bus (or west), then look again" })
  tools.add({ type = "button", name = "bpgen_nudge_fwd", caption = "▶", style = "tool_button", visible = false,
    tags = { bpgen = "nudge", d = 1 }, tooltip = "Move it on along your bus (or east), then look again" })
  ui.add(tools, { type = "button", caption = "Undo", tags = { bpgen = "unplace" },
    tooltip = "Removes the ghosts bpgen put down (still unbuilt)" }, "button", small)
  ui.add(tools, { type = "button", caption = "History", tags = { bpgen = "history" },
    tooltip = "Blueprints you took from bpgen (here or in the web app)" }, "button", small)
  -- (what's said about the plan, in one scroll area of a fixed height: the preview gets the rest of the window)
  local info = under.add({ type = "scroll-pane", name = "bpgen_info", horizontal_scroll_policy = "never" })
  info.style.maximal_height = INFO_H  -- (as tall as what it says, up to this: the preview takes the rest)
  info.style.horizontally_stretchable = true
  -- the plan's result: badges, its notes, the book's prints, the details folded
  local result = section(info, "Result", nil, "bpgen_result")
  result.visible = false
  -- (its numbers as tiles, then badges three a row for the rest: nothing runs past the preview's width)
  local tiles = result.add({ type = "flow", name = "bpgen_tiles", direction = "horizontal", visible = false })
  tiles.style.horizontal_spacing = 6
  result.add({ type = "flow", name = "bpgen_badges", direction = "vertical" }).style.vertical_spacing = 4
  ui.add(result, { type = "label", name = "bpgen_notes" }, "dim", { single_line = false, maximal_width = CAM_W - 40 })
  result.add({ type = "drop-down", name = "bpgen_section", items = {}, visible = false, tags = { bpgen_section = true },
    tooltip = "Which print of the book the preview shows" })
  local dets = ui.fold(result, "Details", false)
  ui.add(dets, { type = "label", name = "bpgen_stats" }, "text", { single_line = false, maximal_width = CAM_W - 40 })
  -- (after a plan: can your save build it, and what modules would do; or the history list)
  local extra = info.add({ type = "flow", name = "bpgen_extra", direction = "vertical" })
  extra.style.maximal_width = CAM_W - 24
  if stdwin then
    -- (the library keeps the window's size and place: the preview stretches into what the window leaves it)
    find(frame, "bpgen_grip").visible = false
    for _, n in ipairs({ "bpgen_cam", "bpgen_empty" }) do
      local el = find(frame, n)
      el.style.minimal_width, el.style.minimal_height = 420, 300
      el.style.horizontally_stretchable, el.style.vertically_stretchable = true, true
    end
    body.style.vertically_stretchable, body.style.horizontally_stretchable = true, true
    -- (a flow ignores its stretch here: a natural width past the screen's makes it fill the window, which caps it)
    body.style.natural_width = math.floor(player.display_resolution.width / player.display_scale)
    right.style.vertically_stretchable, right.style.horizontally_stretchable = true, true
    local fw, fh = stdwin.size(player, NAME)
    if fw then apply_size(player, frame, fw - LEFT_W, fh - FRAME_H) end
  else
    frame.force_auto_center()
  end

  fit_panel(left)
  adopt(player)  -- (the game's styles it was built with: each player's theme)
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
  for _, name in ipairs(ctx.unlocked(ctx.craftable(player.force), { t })) do
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
    if #products == 0 then return nil, "pick the products" end
    local picked = value(frame, "bpgen_mall_machine")
    local candidates = picked and { picked } or machines_for_all(player, products)
    if #candidates == 0 then return nil, "no machine you have makes all of these: split them, or pick one" end
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
      return nil, "hold the blueprint of the base on your bus to put the mall beside"
    end
    return req
  elseif mode == "extend" then
    local item = value(frame, "bpgen_ext_item")
    if not item then return nil, "pick the item to make" end
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
    if #list == 0 then return nil, "pick the ore patches first" end
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
        return nil, "hold the blueprint of the base to add to"
      end
      req.params.add_to = cs.export_stack()
    end
    if find(frame, "bpgen_base_import").state and not add then
      local cs = player.cursor_stack
      if not (cs and cs.valid_for_read and cs.is_blueprint and cs.is_blueprint_setup()) then
        return nil, "hold a blueprint of your base to rebuild it"
      end
      req.params.import = cs.export_stack()
    end
    return req
  end
  local recipe = value(frame, "bpgen_recipe")
  if not recipe then return nil, "pick a recipe" end
  local machine = value(frame, "bpgen_machine")
  if not machine then
    local m = best_machine(player, recipe)
    if not m then return nil, "you have no machine that makes this" end
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
    return status(frame, "Stand on the space platform first: the ship is built round its hub.")
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
  if area then place.outline(player, area, r.res.label or "placed by bpgen") end
  local a = r.res.absolute
  if a.head then  -- (a new bus lane: where to feed it)
    rendering.draw_text({ text = { "", "feed [item=" .. a.item .. "] here" }, surface = player.surface, target = a.head,
      color = { 1, 0.85, 0.2 }, scale = 2, alignment = "center", use_rich_text = true, players = { player },
      time_to_live = 60 * 300 })
    rendering.draw_circle({ color = { 1, 0.85, 0.2 }, radius = 0.7, width = 4, filled = false, target = a.head,
      surface = player.surface, players = { player }, time_to_live = 60 * 300 })
    say(player, string.format("Placed a new [item=%s] lane (%d ghosts): feed it at its head (marked).", a.item, #ghosts))
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    return
  end
  if r.res.mode == "busdesign" or a.fitted then
    say(player, string.format(a.fitted and "Placed %d ghosts (outlined): the base at the end of your bus, its head on the lanes."
      or "Placed %d ghosts (outlined): drills on your patches, the bus head near you, one pole network: wire one of its poles to your grid.", #ghosts))
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    return
  end
  say(player, string.format("Placed %d ghosts next to your base (outlined): %d belt taps%s%s.", #ghosts, a.taps or 0,
    (a.deliveries or 0) > 0 and string.format(", output onto %d of your belts", a.deliveries) or "",
    (a.new_lanes or 0) > 0 and string.format(", %d new bus lane%s", a.new_lanes, a.new_lanes > 1 and "s" or "") or ""))
  ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
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
    return status(frame, "No belt you have carries more than that lane's: add another lane of it instead.")
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
  say(player, string.format("%d pieces of that lane marked for upgrade to [entity=%s].", n, target.name))
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
  status(frame, ok and "" or ("Can't plan in game: " .. tostring(why)))
  stale(player, false)
  if ok then
    M.on_progress(player, { fraction = 0, step = "starting" })
    glow(player, true)
  end
end

--- a section of the base to move (the move tool): its blueprint (settings, wires) and where it is, to bpgen, which
--- places it near the player with its belts in and out run to the base again (see ingame.move)
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
    return status(frame, "Nothing to move there.")
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
  if not offset then return status(frame, "Nothing to move there.") end
  local a = area
  local ok, why = ctx.start(player, { tick = game.tick, surface = player.surface.name, params = { mode = "move",
    move = { area = { math.floor(a.left_top.x), math.floor(a.left_top.y), math.ceil(a.right_bottom.x),
                      math.ceil(a.right_bottom.y) }, blueprint = bp, offset = offset },
    seed = { x = player.position.x, y = player.position.y } } })
  status(frame, ok and "Moving the section..." or ("Can't plan in game: " .. tostring(why)))
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
    if not bp then return status(frame, "Nothing to refit there.") end
    local u = unlocked(player)
    local a = area
    local ok, why = ctx.start(player, { tick = game.tick, surface = player.surface.name,
      seed = player.surface.map_gen_settings.seed, bonuses = ctx.bonuses(player.force), inserters = u.inserters,
      belts = u.belts, poles = u.poles, belt = u.belts[#u.belts],
      params = { mode = "base", import = bp, layout = "bus", belt = u.belts[#u.belts], productivity = 0,
        refit = { area = { a.left_top.x, a.left_top.y, a.right_bottom.x, a.right_bottom.y } } } })
    return status(frame, ok and "Planning the refit as a base..." or ("Can't plan in game: " .. tostring(why)))
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
  if #order == 0 then return status(frame, "No assembling machines with a recipe in that area.") end
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
  status(frame, ok and "Planning the refit..." or ("Can't plan in game: " .. tostring(why)))
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

--- a list row's right-hand note (e.g. "on belt"), small and grey, pushed to the row's end
local function row_note(row, caption, name)
  row.add({ type = "empty-widget" }).style.horizontally_stretchable = true
  local l = row.add({ type = "label", name = name, caption = caption })
  l.style.font = "default-small"
  l.style.font_color = { 0.6, 0.6, 0.6 }
  return l
end

--- a plan's answer (from control.lua's job loop)
--- the plan getting on: {fraction 0..1, step = what it does now, lines = what it did} (from bpgen.ingame:progress)
function M.on_progress(player, p)
  local frame = player.gui.screen[NAME]
  local box = frame and find(frame, "bpgen_progress")
  if not box then return end
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
    if v then ui.stat(row, tostring(v), caption, { horizontally_stretchable = false, minimal_width = 96 }) end
  end
  if a.box then tile(a.box[3] .. " × " .. a.box[4], "size")
  elseif type(w) == "number" then tile(math.floor(w) .. " × " .. math.floor(h), "size") end
  tile(sm.machines or (res.mall and res.mall.machines), "machines")
  if a.taps then tile(a.taps, "taps on your belts") end
  if a.inputs and #a.inputs > 0 then tile(#a.inputs, "to feed by hand") end
  if res.expected then tile(string.format("%.1f/min", res.expected * 60), "made") end
  row.visible = #row.children > 0
end

--- a badge in the result's rows, three a row (they'd run past the preview's width in one)
local function add_badge(frame, text, tip)
  local rows = find(frame, "bpgen_badges")
  if not rows then return nil end
  local row = rows.children[#rows.children]
  if not row or #row.children >= 3 then
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
    badge(string.format("[entity=%s] %s", s.machine or "?", sig(s.recipe or "")), "the machine and its recipe")
    if s.belt then badge("[entity=" .. s.belt .. "] belts", "its belts") end
  end
  for _, n in ipairs(need or {}) do badge("needs " .. n .. "/min", "brought in a minute") end
  if res.mall then badge(#(res.mall.products or {}) .. " products") end
  if res.base then badge(tostring(res.base.spm) .. "/min of each pack") end
  if res.seconds then badge(string.format("planned in %.0f ms", res.seconds * 1000)) end
  local notes = {}
  for _, n in ipairs(res.notes or {}) do notes[#notes + 1] = "• " .. n end
  find(frame, "bpgen_notes").caption = table.concat(notes, "\n")
end

function M.on_result(player, res)
  local frame = player.gui.screen[NAME]
  if not frame then return false end
  local prog = find(frame, "bpgen_progress")
  if prog and not res.measure then
    prog.visible = false
    local log_ = find(prog, "bpgen_log")
    if log_ then log_.caption = "" end
  end
  if not res.blueprint then
    if res.measure and not M.measuring(player) then
      -- (inserter setups bpgen hasn't measured: measured here and now, live, then planned again)
      preview_surface()
      bench.calibrate(player.index, res.measure)
      status(frame, string.format("Measuring %d inserter setups bpgen hasn't seen yet (%d s), then planning...",
        #res.measure.cases, math.ceil(((res.measure.warmup or 300) + (res.measure.measure or 1800)) / 60)))
    else
      say(player, "Can't plan this: " .. tostring(res.error))
    end
    return true
  end
  local old = bench.find("test", player.index)
  if old then bench.stop(old.id) end
  if storage.bpgen_speed_was then  -- (a test run's fast-forward: off with it)
    game.speed, storage.bpgen_speed_was = storage.bpgen_speed_was, nil
  end
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
    empty.caption = "No preview: " .. tostring(w)
  end
  show_tiles(frame, res, w, h)
  glow(player, false)
  stale(player, false)
  find(frame, "bpgen_test_box").visible = false
  M.steps(frame)
  find(frame, "bpgen_cursor").enabled = true
  find(frame, "bpgen_copy").enabled = safe.has("std")
  find(frame, "bpgen_place").enabled = where ~= nil
  find(frame, "bpgen_place_base").enabled = res.absolute == nil and (res.mode == "line" or res.mode == "mall"
    or res.mode == "base")
  local pb = find(frame, "bpgen_place")
  pb.caption = res.absolute and "Place it" or "Place near me"
  pb.tooltip = res.absolute and "Ghosts where the preview shows it, its taps replacing the belts they cut into"
    or "Ghosts at a free spot next to you, inputs pointed at your belts that carry them"
  local fixed = res.mode == "lane"  -- (where it is is the point: no arrows; a bus design's move its head sideways)
  find(frame, "bpgen_nudge_back").visible = res.absolute ~= nil and not fixed
  find(frame, "bpgen_nudge_fwd").visible = res.absolute ~= nil and not fixed
  local tb = find(frame, "bpgen_test")
  tb.enabled = res.test ~= nil and where ~= nil
  tb.caption = "Test run"
  local s = res.summary or {}
  local lines = {}
  if s.machines and not res.mall and not res.base then
    lines[#lines + 1] = string.format("%s × %d [entity=%s]   %s %.1f/min expected%s",
      sig(s.recipe or ""), s.machines, s.machine or "?", sig(s.output or ""), (s.expected or 0) * 60,
      s.belt and ("   belt [entity=" .. s.belt .. "]") or "")
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
  if #need > 0 then lines[#lines + 1] = "Needs a minute: " .. table.concat(need, "   ") end
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
    lines[#lines + 1] = string.format("Mall: %s   (%s machines)", icons(res.mall.products, "recipe"), tostring(res.mall.machines))
  end
  if res.base then
    local b = res.base
    lines[#lines + 1] = string.format("Starter base: %s/min each of %s", tostring(b.spm), icons(b.packs))
    if type(b.bring_in) == "table" and next(b.bring_in) then
      local t = {}
      for k, v in pairs(b.bring_in) do
        t[#t + 1] = type(k) == "string" and (sig(k) .. (type(v) == "number" and string.format(" %.0f/min", v) or ""))
          or sig(tostring(v))
      end
      lines[#lines + 1] = "Brought in: " .. table.concat(t, "  ")
    end
    if type(b.not_automated) == "table" and #b.not_automated > 0 then lines[#lines + 1] = "Not automated: " .. icons(b.not_automated) end
    if b.route_note then lines[#lines + 1] = b.route_note end
    lines[#lines + 1] = "(a book of prints: pick one under the preview)"
  end
  if center then lines[#lines + 1] = string.format("%d × %d tiles · planned in %.0f ms", math.ceil(w), math.ceil(h), (res.seconds or 0) * 1000) end
  for _, n in ipairs(res.notes or {}) do lines[#lines + 1] = "• " .. n end
  find(frame, "bpgen_stats").caption = table.concat(lines, "\n")
  show_result(frame, res, center and w, h, need)
  adopt(player)
  status(frame, res.mode == "lane" and "The preview shows the new lane along your bus: Place it puts the ghosts down."
    or res.mode == "busdesign" and "The preview shows the drills on your patches and the bus head near you: Place it puts the ghosts down."
    or res.absolute and "The preview shows it with your base around it: ◀ ▶ move it, Place it puts the ghosts down." or "")
  -- what next: can the save build it (and for a line, what modules would do)
  find(frame, "bpgen_extra").clear()
  for i, u in ipairs(res.absolute and res.absolute.upgrades or {}) do
    local icons = {}
    for _, it in ipairs(u.items) do icons[#icons + 1] = sig(it) end
    find(frame, "bpgen_extra").add({ type = "button", caption = "Upgrade the " .. table.concat(icons) .. " lane",
      tags = { bpgen = "upgrade_lane", i = i },
      tooltip = string.format("Marks that bus lane's belts, undergrounds and splitters for upgrade to the slowest belt you have that carries %.0f/min (else your fastest), bpgen's ghosts on it too", u.need * 60) })
  end
  ctx.api(player, "save_check", { blueprint = res.blueprint }, "check")
  if s.recipe and s.machine then
    ctx.api(player, "module_ideas", { recipe = s.recipe, machine = s.machine, belt = s.belt,
      rate_per_min = s.expected and s.expected * 60, belts = unlocked(player).belts }, "ideas")
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
    if badges and ui.themed then add_badge(frame, "[img=utility/check_mark_green] your save can build it") return end
    box.add({ type = "label", caption = "[img=utility/check_mark_green] Your save can build this." })
    return
  end
  local short = {}
  for _, b in ipairs(r.buildings or {}) do
    if not b.craftable and (b.have or 0) < b.need then
      short[#short + 1] = string.format("%s %d (have %d)", sig(b.item), b.need, b.have or 0)
    end
  end
  local lines = {}
  if #(r.recipes or {}) > 0 then
    local t = {}
    for _, rn in ipairs(r.recipes) do t[#t + 1] = prototypes.recipe[rn] and ("[recipe=" .. rn .. "]") or rn end
    lines[#lines + 1] = "Not researched yet: " .. table.concat(t, " ")
  end
  if #short > 0 then lines[#lines + 1] = "Can't craft yet and not enough on hand: " .. table.concat(short, ", ") end
  local badges = find(frame, "bpgen_badges")
  if badges and ui.themed then
    add_badge(frame, "[img=utility/warning_icon] can't build it yet", table.concat(lines, "\n"))
    return
  end
  local l = box.add({ type = "label", caption = "[img=utility/warning_icon] " .. table.concat(lines, "\n") })
  l.style.single_line = false
  l.style.maximal_width = cam_size(game.get_player(frame.player_index)) - 24
end

local function show_ideas(frame, r)
  local ideas = r.ideas or {}
  if #ideas < 2 then return end
  local box = extra(frame)
  ui.add(box, { type = "label", caption = "Modules would make it:" }, "caption")
  local t = box.add({ type = "table", column_count = 4 })
  t.style.horizontal_spacing = 12
  for _, h in ipairs({ "", "machines", "power", "input per output" }) do t.add({ type = "label", caption = h }) end
  for _, idea in ipairs(ideas) do
    t.add({ type = "button", caption = idea.name, tags = { bpgen = "idea", modules = idea.modules or "",
      beacon = idea.beacon or "", beacon_modules = idea.beacon_modules or "" }, tooltip = "Use these and plan again" })
    t.add({ type = "label", caption = tostring(idea.machines) .. (idea.beacons and idea.beacons > 0 and (" + " .. idea.beacons .. " beacons") or "") })
    t.add({ type = "label", caption = string.format("%.1f MW", (idea.power_kw or 0) / 1000) })
    t.add({ type = "label", caption = string.format("%.2f", idea.input or 0) })
  end
end

--- History as a drawer stuck to the window's right edge: opened, or closed when it was open (-> the drawer or nil)
local function history_drawer(player, frame)
  local d = player.gui.screen[NAME .. "__right"]
  if d then d.destroy() return nil end
  local _, h = stdwin.size(player, NAME)
  d = ui.attach(frame, "right", { size = { 300, math.max(200, (h or 500) - TOOLS_H - 110) }, along = 40 + TOOLS_H + 12,
    overlap = 8,
    role = "toast", direction = "vertical", padding = 8 })
  if not d then return nil end
  ui.add(d, { type = "label", caption = "History" }, "caption")
  ui.search(d, "bpgen_history", "Search your blueprints")
  local list = ui.add(d, { type = "scroll-pane", name = "bpgen_history_list", horizontal_scroll_policy = "never" },
    "scroll", { vertically_stretchable = true, horizontally_stretchable = true })
  ui.add(list, { type = "label", caption = "..." }, "dim")
  ui.add(d, { type = "label", caption = "Drag a row onto the preview to see it again, or onto the Recipe slot to plan it again." },
    "dim", { single_line = false, maximal_width = 276, font = "default-small" })
  return d
end

local function show_history(frame, items)
  local list = find(frame, "bpgen_history_list")
  if list then
    list.clear()
    if #items == 0 then
      ui.add(list, { type = "label", caption = "Nothing yet: blueprints you take go here (shared with the web app)." },
        "dim", { single_line = false, maximal_width = 260 })
    end
    for i, h in ipairs(items) do
      if i > 40 then break end
      local icon = h.recipe and prototypes.recipe[h.recipe] and ("[recipe=" .. h.recipe .. "] ") or ""
      local name = h.label or "blueprint"
      local row = list.add({ type = "flow", direction = "horizontal", tags = { bpgen_hist = string.lower(name) } })
      row.style.vertical_align = "center"
      row.style.horizontally_stretchable = true
      local l = ui.add(row, { type = "label", caption = icon .. name, tooltip = { "", name, "\n",
        (h.book and "book, " or "") .. tostring(h.entities) .. " entities", "\nDrag onto the preview or the Recipe slot" } },
        "text", { maximal_width = 170 })
      if dnd then dnd.draggable(l, { kind = "bpgen_history", i = i, caption = icon .. name }) end
      row.add({ type = "empty-widget" }).style.horizontally_stretchable = true
      ui.add(row, { type = "button", caption = "To cursor", tags = { bpgen = "history_take", i = i } }, "button",
        { minimal_width = 0, height = 26, left_padding = 6, right_padding = 6 })
    end
    return
  end
  local box = extra(frame)
  box.clear()
  if #items == 0 then
    box.add({ type = "label", caption = "Nothing yet: blueprints you take go here (shared with the web app)." })
    return
  end
  local t = box.add({ type = "table", column_count = 3 })
  for i, h in ipairs(items) do
    if i > 25 then break end
    -- (an icon only for a recipe this game has: a removed mod's shows as raw text otherwise)
    local icon = h.recipe and prototypes.recipe[h.recipe] and ("[recipe=" .. h.recipe .. "] ") or ""
    local l = t.add({ type = "label", caption = icon .. (h.label or "blueprint"), tooltip = h.label })
    l.style.maximal_width = 330
    t.add({ type = "label", caption = (h.book and "book, " or "") .. tostring(h.entities) .. " entities" })
    t.add({ type = "button", caption = "To cursor", tags = { bpgen = "history_take", i = i } })
  end
end

local histories = safe.stored("histories")  -- player index -> the last history list (its blueprints for "To cursor")

local function show_short(frame, rows)
  local box = extra(frame)
  box.clear()
  if #rows == 0 then
    box.add({ type = "label", caption = "Nothing's short (or bpgen hasn't counted 10 minutes yet)." })
    return
  end
  ui.add(box, { type = "label", caption = "Used faster than made (a minute):" }, "caption")
  local t = box.add({ type = "table", column_count = 3 })
  for _, r in ipairs(rows) do
    t.add({ type = "label", caption = string.format("%s  short %.0f  (made %.0f, used %.0f)", sig(r.name), r.short, r.made, r.used) })
    t.add({ type = "empty-widget" })
    t.add({ type = "button", caption = "Plan it", tags = { bpgen = "plan_short", recipe = r.recipe, rate = r.short } })
  end
end

local function show_bus(frame, r)
  local box = extra(frame)
  box.clear()
  if not r.bus then
    box.add({ type = "label", caption = "No main bus around you (3+ long straight belts side by side, flowing the same way)." })
    return
  end
  local b = r.bus
  ui.add(box, { type = "label", caption = string.format("Your bus: %d lanes%s, %d tiles%s", b.lanes,
    #b.pipes > 0 and string.format(" and %d pipe%s", #b.pipes, #b.pipes > 1 and "s" or "") or "", b.length,
    b.measured and "" or " (not measured)") }, "caption")
  local t = box.add({ type = "table", column_count = 3 })
  for _, it in ipairs(r.items or {}) do
    local full = it.used and (it.used >= 0.9 * it.cap or it.dry > 0)
    local used = it.used and string.format("%.0f of %.0f/min used", it.used * 60, it.cap * 60)
      or string.format("carries up to %.0f/min", it.cap * 60)
    local single = not it.item:find(" + ", 1, true)
    t.add({ type = "label", caption = string.format("%s%s  %d lane%s, %s%s%s%s", full and "[color=yellow]" or "",
      single and sig(it.item) or it.item, it.lanes, it.lanes > 1 and "s" or "", used,
      it.dry > 0 and string.format(", %d run%s dry", it.dry, it.dry > 1 and "" or "s") or "",
      (it.short or 0) > 0 and string.format(", your base is %.0f/min short", it.short) or "", full and "[/color]" or "") })
    local acts = t.add({ type = "flow", direction = "horizontal" })
    if single then
      acts.add({ type = "button", caption = "Add a lane", tags = { bpgen = "bus_lane", item = it.item },
        tooltip = "A new lane of it along the bus (previewed first): you feed it at its head" })
    end
    if single and it.makeable then
      acts.add({ type = "button", caption = "Make more", tags = { bpgen = "bus_make", item = it.item,
        rate = math.max(it.short or 0, 60) }, tooltip = "Plan making it next to the bus from what the bus carries (Extend)" })
    end
    t.add({ type = "empty-widget" })
  end
  if #(r.short or {}) > 0 then
    ui.add(box, { type = "label", caption = "Your base lacks, and the bus doesn't carry:" }, "caption")
    local s = box.add({ type = "table", column_count = 2 })
    for _, it in ipairs(r.short) do
      s.add({ type = "label", caption = string.format("%s  %.0f/min short", sig(it.item), it.short) })
      s.add({ type = "button", caption = "Make it by the bus", tags = { bpgen = "bus_make", item = it.item, rate = it.short },
        tooltip = "Plan making it next to the bus from what the bus carries, its output on a new lane (Extend)" })
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
      f.add({ type = "textfield", numeric = true, allow_decimal = true, tooltip = "a minute (empty: what fits)" }).style.width = 44
    end
    local near = {}
    for _, sib in ipairs(r) do
      if sib.exact then add_row(box, sib) else near[#near + 1] = sib end
    end
    if #near > 0 then
      box.add({ type = "checkbox", state = false, tags = { bpgen_toggle = "bpgen_also_near" },
        caption = string.format("%d that need one input more or less", #near) })
      local more = box.add({ type = "flow", name = "bpgen_also_near", direction = "vertical", visible = false })
      for i, sib in ipairs(near) do
        if i > 12 then break end
        add_row(more, sib)
      end
    end
    if #r == 0 then box.add({ type = "label", caption = "(nothing else is made from the same inputs)" }) end
    fit_panel(box)
    ui.set_badge(find(frame, "bpgen_also_badge"), #r > 0 and tostring(#r) or "none")
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
          local pick = f.add({ type = "choose-elem-button", elem_type = "recipe", tooltip = "made with this recipe",
            elem_filters = { { filter = "has-product-item", elem_filters = { { filter = "name", name = ing.item } } } } })
          pick.elem_value = ing.recipes[1].recipe
          pick.style.size = 28
        else
          f.add({ type = "label", caption = named(ing.item) })
        end
        row_note(f, "on belt", "bpgen_where")
      end
    end
    if #box.children == 0 then box.add({ type = "label", caption = "(nothing but fluids)" }) end
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

--- the test run's fast-forward off: the game's speed as it was
local function test_speed_off(frame)
  if storage.bpgen_speed_was then
    game.speed = storage.bpgen_speed_was
    storage.bpgen_speed_was = nil
  end
  local b = frame and find(frame, "bpgen_test_speed")
  if b then b.caption, b.visible = "×1", false end
end

local function describe_test(run, final)
  local seconds, cases = bench.rates(run)
  local t = game.tick - run.start
  local count, starved = bench.statuses(run)
  local machines = {}
  for k, n in pairs(count) do machines[#machines + 1] = n .. " " .. k:gsub("_", " ") end
  table.sort(machines)
  local head
  if run.products then  -- (a mall: nothing to rate, what has reached its chests)
    local got, waiting = bench.chest_contents(run), {}
    for _, p in ipairs(run.products) do if not got[p] then waiting[#waiting + 1] = sig(p) end end
    head = string.format("%s: %d of %d products in their chests (%d s)%s", final and "Test run done" or "Test run (mall)",
      #run.products - #waiting, #run.products, math.floor(t / 60),
      #waiting > 0 and ("\nStill waiting for: " .. table.concat(waiting, " ")) or "")
  elseif t < run.warmup then
    head = string.format("Test run: warming up %d / %d s (inputs filling the belts)", math.floor(t / 60),
      math.floor(run.warmup / 60))
  else
    local got = (cases[1].rates or {})[run.output] or 0
    local pct = run.expected and run.expected > 0 and got / run.expected * 100 or 0
    head = string.format("%s: %s %.1f/min measured = %.0f%% of the %.1f/min expected%s",
      final and "Test run done" or "Test run, measuring", sig(run.output or ""), got * 60, pct, (run.expected or 0) * 60,
      final and string.format(" (over %d s)", math.floor(seconds))
        or string.format(" (%d / %d s)", math.floor(seconds), math.floor(run.measure / 60)))
  end
  local errs = cases[1].errors or {}
  return head .. "\nMachines: " .. table.concat(machines, ", ")
    .. (#errs > 0 and ("\n" .. table.concat(errs, "; ")) or ""), starved
end

local test_series = safe.stored("test_series")  -- player index -> the rate measured each second (the chart)
--- the test run's section: started (no run), or where the run is (a bar red to green by the share of the expected
--- rate, a chart of the rate)
local function test_view(player, frame, run, final)
  local box = find(frame, "bpgen_test_box")
  if not box then return end
  box.visible = true
  local bar, holder = find(box, "bpgen_test_bar"), find(box, "bpgen_test_graph")
  if not run then
    test_series[player.index] = {}
    ui.set_progress(bar, 0, "starting")
    holder.clear()
    local g = ui.graph(holder, { 0, 0 }, { width = math.max(200, cam_size(player) - 60), height = 56 })
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
    text = string.format("%d of %d products in their chests", n, #run.products)
  elseif t < run.warmup then
    text = string.format("warming up %d / %d s", math.floor(t / 60), math.floor(run.warmup / 60))
  else
    local _, cases = bench.rates(run)
    local got = ((cases[1] or {}).rates or {})[run.output] or 0
    rate = got * 60
    frac = run.expected and run.expected > 0 and got / run.expected or 0
    text = string.format("%.0f/min, %.0f%% of expected", rate, frac * 100)
  end
  ui.set_progress(bar, math.min(1, frac), text)
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
  if run.kind == "test" then
    local text, starved = describe_test(run, false)
    find(frame, "bpgen_test_status").caption = text
    mark(player, starved)
    test_view(player, frame, run)
  else
    local left = math.ceil((run.warmup + run.measure - (game.tick - run.start)) / 60)
    status(frame, string.format("Measuring inserters: %d s left, then planning...", left))
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
  if not frame then return end
  local text, starved = describe_test(run, true)
  find(frame, "bpgen_test_status").caption = text
  mark(player, starved)
  find(frame, "bpgen_test").caption = "Test run"
  test_speed_off(frame)
  test_view(player, frame, run, true)
end

local function to_cursor(player)
  local r = results[player.index]
  if not r then return end
  if not player.clear_cursor() then return say(player, "Your hand is full") end
  player.cursor_stack.set_stack({ name = "blueprint" })
  if player.cursor_stack.import_stack(r.blueprint) == 1 then
    player.clear_cursor()
    return say(player, "The blueprint string didn't import")
  end
  close(player)  -- (out of the way of placing it)
  ui.toast(player, "The blueprint is in your hand")
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
  say(player, n .. " placed ghosts removed")
end
ui.on_yes("bpgen_unplace", function(player) unplace(player) end)

--- the window's settings (the gear by its close button): opened, or closed when they were open
local function settings(player)
  local frame = player.gui.screen[NAME]
  local m = frame and ui.menu(frame, { size = { 250, ui.layouts and 196 or 164 } })
  if not m then return end
  ui.add(m, { type = "label", caption = "bpgen" }, "caption")
  if ui.layouts then
    ui.add(m, { type = "checkbox", name = "bpgen_docked", caption = "Docked", state = docked(player) == true,
      tooltip = "The modes, the preview's tools and the blueprint's actions on the window's edges; unticked, all inside it" },
      "check")
  end
  ui.add(m, { type = "checkbox", name = "bpgen_only_unlocked", caption = "Only unlocked",
    state = only_unlocked[player.index] == true,
    tooltip = "Pickers offer only recipes you can craft and what you can build now" }, "check")
  local wide = { horizontally_stretchable = true, minimal_width = 0, height = 28 }
  if remote.interfaces["fse-std"] and remote.interfaces["fse-std"].open_theme then
    ui.add(m, { type = "button", caption = "Theme...", tags = { bpgen = "theme" },
      tooltip = "How every window using fse-std looks to you (/fse-theme)" }, "button", wide)
  end
  ui.add(m, { type = "button", caption = "Reset size and place", tags = { bpgen = "reset_size" },
    tooltip = "The window back to its first size, in the middle of the screen" }, "button", wide)
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
    if storage.fstd_windows and storage.fstd_windows[player.index] then storage.fstd_windows[player.index][NAME] = nil end
    if storage.bpgen_cam then storage.bpgen_cam[player.index] = nil end
    M.rebuild(player)
  elseif action == "plan" then
    bus_shift[player.index] = nil
    M.plan(player)
  elseif action == "nudge" and r and r.res.mode == "busdesign" then  -- (its head, sideways: planned again)
    bus_shift[player.index] = (bus_shift[player.index] or 0) + el.tags.d * BUS_STEP
    status(frame, "Moving the bus head...")
    M.plan(player)
  elseif action == "recipe" then  -- a bpgen button in another mod's window (compat.lua)
    M.open(player, { recipe = el.tags.recipe })
    M.plan(player)
  elseif action == "test" and r and r.res.test and r.where then
    local run = bench.find("test", player.index)
    if run then
      bench.stop(run.id)
      find(frame, "bpgen_test").caption = "Test run"
      find(frame, "bpgen_test_status").caption = "Test stopped."
      test_speed_off(frame)
      glow(player, false)
    else
      bench.test(player.index, r.res.test, r.where.origin, r.where.area, PREVIEW)
      find(frame, "bpgen_test").caption = "Stop test"
      find(frame, "bpgen_test_status").caption = "Starting..."
      find(frame, "bpgen_test_speed").visible = not game.is_multiplayer()
      test_view(player, frame)
      glow(player, true)
    end
  elseif action == "test_speed" and not game.is_multiplayer() then
    local b = find(frame, "bpgen_test_speed")
    local next_ = ({ ["×1"] = 5, ["×5"] = 10, ["×10"] = 1 })[tostring(b.caption)] or 1
    storage.bpgen_speed_was = storage.bpgen_speed_was or game.speed
    game.speed = next_ == 1 and storage.bpgen_speed_was or next_
    if next_ == 1 then storage.bpgen_speed_was = nil end
    b.caption = "×" .. next_
  elseif action == "cursor" and r then
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    to_cursor(player)
  elseif action == "copy" and r and safe.has("std") then
    native.call("std", "clipboard_set", r.blueprint)
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    say(player, "Blueprint string copied")
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
      status(frame, "Stand where the section should go, then drag over it with the move tool.")
    end
  elseif action == "refit" then
    if player.clear_cursor() then
      player.cursor_stack.set_stack({ name = "bpgen-refit" })
      M.tool_card(player, "bpgen-refit")
      status(frame, "Drag over the area to refit with the refit tool.")
    end
  elseif action == "snapshot" then
    if player.clear_cursor() then
      player.cursor_stack.set_stack({ name = "bpgen-snapshot" })
      M.tool_card(player, "bpgen-snapshot")
      status(frame, "Drag over your base with the snapshot tool, then press Plan.")
    end
  elseif action == "patches" then
    if player.clear_cursor() then
      player.cursor_stack.set_stack({ name = "bpgen-patches" })
      M.tool_card(player, "bpgen-patches")
      status(frame, "Drag over each ore patch with the patch tool, then stand where the bus should start and press Plan.")
    end
  elseif action == "patches_clear" then
    patch_areas[player.index] = nil
    show_patches(frame, player)
  elseif action == "place" and r and r.res.absolute then
    M.place_absolute(player)
  elseif action == "tab" then
    set_mode(frame, el.tags.mode)
  elseif action == "check_bus" then
    extra(frame).clear()
    local ok, why = ctx.bus_report(player, unlocked(player).belts)
    status(frame, ok and "Looking at your bus..." or ("Can't: " .. tostring(why)))
  elseif action == "bus_lane" then
    local ok, why = ctx.add_lane(player, el.tags.item)
    status(frame, ok and "Laying out a new lane..." or ("Can't: " .. tostring(why)))
  elseif action == "bus_make" then
    -- (Extend: that item at that rate, made next to the bus from what it carries)
    set_mode(frame, "extend")
    find(frame, "bpgen_ext_item").elem_value = el.tags.item
    find(frame, "bpgen_ext_rate").text = tostring(math.ceil(el.tags.rate))
    M.plan(player)
  elseif action == "upgrade_lane" and r and r.res.absolute then
    M.upgrade_lane(player, r.res.absolute.upgrades[el.tags.i])
  elseif action == "nudge" and r and r.res.absolute then
    local b, axis = r.res.absolute.box, r.res.absolute.axis
    local along = axis == 0 and b[4] or b[3]
    local step = el.tags.d * math.max(8, math.ceil(along / 2))
    local seed = { x = b[1] + b[3] / 2 + (axis == 0 and 0 or step), y = b[2] + b[4] / 2 + (axis == 0 and step or 0) }
    local ok, why = ctx.place_base(player, { bus = find(frame, "bpgen_ext_bus").state and "auto" or "off", seed = seed })
    status(frame, ok and "Moving it..." or ("Can't: " .. tostring(why)))
  elseif action == "place_base" and r then
    local sec = find(frame, "bpgen_section")  -- (a starter base: the print picked under the preview)
    local ok, why = ctx.place_base(player, { bus = find(frame, "bpgen_ext_bus").state and "auto" or "off",
      seed = { x = player.position.x, y = player.position.y },
      section = sec.visible and sec.items[sec.selected_index] or nil })
    status(frame, ok and "Looking around you for a spot next to your base..." or ("Can't: " .. tostring(why)))
  elseif action == "place" and r and r.where then
    local c = place.find_spot(player, r.w, r.h)
    if not c then
      status(frame, "No free spot near you for " .. math.ceil(r.w) .. " × " .. math.ceil(r.h) .. " tiles: move somewhere open")
    else
      local ghosts, origin, area, marks = place.place(player, r.blueprint, c)
      placed[player.index], unmark[player.index] = ghosts, marks
      if origin then
        local found, missing = place.guide(player, area, origin, r.res.test and r.res.test.case.sources or {},
          r.res.label)
        say(player, string.format("Placed %d ghosts next to you (outlined). %s", #ghosts,
          #missing > 0 and ("Nothing of yours nearby carries " .. table.concat(missing, " ")) or
          (found > 0 and "Dashed lines: where each input can come from." or "")))
      end
      ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    end
  elseif action == "unplace" then
    -- (a press of the button asks first when there are ghosts to remove; a script's press (tests) doesn't)
    local n = ghosts_left(player)
    if n > 0 and not e.scripted then
      ui.confirm(player, "bpgen_unplace", { title = "Undo the placement?",
        text = string.format("Removes the %d ghost%s bpgen put down that aren't built yet%s.", n, n > 1 and "s" or "",
          #(unmark[player.index] or {}) > 0 and ", and keeps what they were to replace" or ""),
        yes = "Remove them", no = "Keep" })
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
    local step = cam_size(player) * player.display_scale / (32 * cam.zoom) / 4  -- (a quarter of the view)
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
  find(frame, "bpgen_also").add({ type = "label", caption = recipe and "..." or "(pick a recipe)" })
  if recipe then ctx.api(player, "siblings", { recipe = recipe, machine = machine and machine.name }, "also") end
  find(frame, "bpgen_make").clear()
  find(frame, "bpgen_make").add({ type = "label", caption = recipe and "..." or "(pick a recipe)" })
  if recipe then ctx.api(player, "recipe_tree", { recipe = recipe, machine = machine and machine.name }, "tree") end
end

-- (pickers an answer adds follow "Only unlocked" too)
function M.on_api(player, tag, out)
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
    if frame then apply_size(player, frame, w - LEFT_W, h - FRAME_H) end
  end)
end

ui.on_search("bpgen_history", function(player, text)
  local list = find(player.gui.screen[NAME], "bpgen_history_list")
  text = string.lower(text or "")
  for _, row in pairs(list and list.children or {}) do
    if row.tags.bpgen_hist then row.visible = text == "" or row.tags.bpgen_hist:find(text, 1, true) ~= nil end
  end
end)

if dnd then
  dnd.on_drop(function(player, payload, target)
    if type(payload) ~= "table" or payload.kind ~= "bpgen_history" then return end
    local h = (histories[player.index] or {})[payload.i]
    local frame = player.gui.screen[NAME]
    if not (h and frame and target and target.valid) then return end
    if target.name == "bpgen_recipe" and h.recipe and prototypes.recipe[h.recipe] then  -- (planned again)
      set_mode(frame, "line")
      target.elem_value = h.recipe
      set_machine_filter(frame, h.recipe)
      local m = best_machine(player, h.recipe)
      find(frame, "bpgen_machine").elem_value = m and { name = m, quality = "normal" } or nil
      M.plan(player)
    else  -- (shown in the preview, ready to place)
      M.on_result(player, { blueprint = h.blueprint, mode = h.mode, label = h.label,
        notes = { "From your history: " .. (h.label or "a blueprint") } })
    end
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
  frame.style.width, frame.style.height = w, h
  local tags = frame.tags
  tags.fstd_tweak = { width = w, height = h }
  frame.tags = tags
  if ui.theme then ui.theme.follow(frame) end
  apply_size(player, frame, w - LEFT_W, h - FRAME_H)
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
    machine_quality = machine and machine.quality })
  local r = results[player.index]
  if r and r.blueprint and r.res then M.on_result(player, r.res) end
end

-- (tests: a setting changed, as if by the player)
function M.touch(player_index, name)
  changed({ element = find(game.get_player(player_index).gui.screen[NAME], name), player_index = player_index })
end

-- (tests: a button press, as its tags)
function M.click(player_index, tags, as_button)
  on_click({ element = { valid = true, tags = tags }, player_index = player_index, scripted = not as_button })
end

M.handlers = {
  [defines.events.on_tick] = function(e)
    bench.handlers[defines.events.on_tick](e)
    camera_tick()
  end,
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
    if el and el.valid and el.tags and el.tags.bpgen_make_row then
      local where = el.parent["bpgen_where"]
      where.caption = el.state and "made here" or "on belt"
      where.style.font_color = el.state and { 0.5, 0.9, 0.5 } or { 0.6, 0.6, 0.6 }
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
  [defines.events.on_lua_shortcut] = function(e)
    if e.prototype_name ~= "bpgen-window" then return end
    local player = game.get_player(e.player_index)
    local f = player.gui.screen[NAME]
    if f and not f.visible then player.clear_cursor() M.tool_done(player)
    elseif f then close(player) else M.open(player) end
  end,
  ["bpgen-window"] = function(e)
    local player = game.get_player(e.player_index)
    local f = player.gui.screen[NAME]
    if f and not f.visible then player.clear_cursor() M.tool_done(player)
    elseif f then close(player) else M.open(player) end
  end,
}

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

return M
