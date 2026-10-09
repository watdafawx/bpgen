-- The bpgen window (with the fnative loader): pick a recipe with the game's own recipe picker, plan it in game
-- (bpgen.ingame on a worker thread), and see the blueprint as the game draws it: it is built for real on a hidden
-- preview surface ("bpgen-preview", its own force, lab tiles) and shown through a camera. Then put it in your hand
-- (the game's own placement preview) or copy its string.
--
-- Opened by Ctrl+Shift+B over a machine (filled in from it, planned at once), the bpgen shortcut, Ctrl+Alt+B, or the
-- fnative hub. Results live in this Lua state only (not saved); the preview surface is rebuilt on demand.

local M = {}
local NAME = "bpgen_window"
local PREVIEW = "bpgen-preview"
-- with the fnative-std library mod (optional): its window, resized by its corner grip like every fnative window
local stdwin = script.active_mods["fnative-std"] and require("__fnative-std__/window") or nil
local stdinput = stdwin and require("__fnative-std__/input") or nil
local FRAME_W, FRAME_H = 360, 366  -- (the window around the preview: the tabs, the left panel, the rows under it)
local INFO_H = 170                   -- (the scroll area under the preview, at most)
local LEFT_W = 324                   -- (the library window's width besides the preview: the left panel, borders)
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
--- the preview (and what's sized by it) at w x h, kept for this player
local function apply_size(player, frame, w, h)
  -- (never bigger than the screen leaves room for: the left panel and the rows under the preview)
  local res, scale = player.display_resolution, player.display_scale
  local max_w, max_h = res.width / scale - 380, res.height / scale - FRAME_H - 20
  w, h = math.max(420, math.floor(math.min(w, max_w))), math.max(300, math.floor(math.min(h, max_h)))
  storage.bpgen_cam = storage.bpgen_cam or {}
  storage.bpgen_cam[player.index] = { w = w, h = h }
  for _, n in ipairs({ "bpgen_cam", "bpgen_empty" }) do
    local el = find(frame, n)
    if el and not stdwin then el.style.width, el.style.height = w, h end
    -- (in the library's window the preview stretches down; sideways it gets the window's width less the left panel)
    if el and stdwin then el.style.width = w end
  end
  for n, d in pairs({ bpgen_test_status = 24, bpgen_stats = 24, bpgen_extra = 24 }) do
    local el = find(frame, n)
    if el then el.style.maximal_width = w - d end
  end
  -- (and back on the screen if it grew past its edge)
  local loc = frame.location
  if loc then
    local x = math.max(0, math.min(loc.x, res.width - (w + 360) * scale))
    local y = math.max(0, math.min(loc.y, res.height - (h + FRAME_H) * scale))
    if x ~= loc.x or y ~= loc.y then frame.location = { x = x, y = y } end
  end
end

local bench = require("bench")
local safe = require("safe")
local place = require("place")
local ctx          -- from control.lua: craftable, unlocked, bonuses, start, measured
local results = {} -- player index -> { blueprint, res, center, zoom }
local placed = {}     -- player index -> the ghosts "Place near me" and "Next to my base" made
local unmark = {}     -- player index -> what those pastes marked for deconstruction (Undo unmarks it)
-- (the preview's drag, zoom and size grip: momentary, not saved)
local cam_hover, drags, wheel_seen, left_during = {}, {}, {}, {}
local grip_hover, resizing, resized_at = {}, {}, {}
local unlocks = {} -- player index -> what the force can build (read once per window)

function M.setup(c) ctx = c end

function find(el, name)
  if not (el and el.valid) then return nil end
  if el.name == name then return el end
  for _, c in pairs(el.children) do
    local f = find(c, name)
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
  if f then return f end
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

local MODES = { "line", "mall", "base", "extend", "busdesign" }
local MODE_NAMES = { "Production line", "Mall", "Starter base", "Extend my base", "Bus design" }  -- (the tabs along the top)
local BUS_DIRS = { "north", "east", "south", "west" }
local patch_areas = {}  -- player index -> {{area, counts = {resource = tiles}}}: the ore patches picked for a bus design

local function row(parent, caption, tooltip)
  local f = parent.add({ type = "flow", direction = "horizontal" })
  f.style.vertical_align = "center"
  local l = f.add({ type = "label", caption = caption, tooltip = tooltip })
  l.style.width = 80
  return f
end

--- a framed section with its heading (as the web app's panels)
local function section(parent, title, tooltip)
  local f = parent.add({ type = "frame", style = "bordered_frame", direction = "vertical" })
  f.style.horizontally_stretchable = true
  f.add({ type = "label", caption = title, style = "caption_label", tooltip = tooltip })
  return f
end

--- a field: its caption, small and grey, over the control added to the returned flow
local function field(parent, caption, tooltip)
  local f = parent.add({ type = "flow", direction = "vertical" })
  f.style.vertical_spacing = 0
  local l = f.add({ type = "label", caption = caption, tooltip = tooltip })
  l.style.font = "default-small"
  l.style.font_color = { 0.7, 0.7, 0.7 }
  return f
end

--- fields side by side
local function fields(parent)
  local f = parent.add({ type = "flow", direction = "horizontal" })
  f.style.horizontal_spacing = 12
  return f
end

local function set_machine_filter(frame, recipe)
  local btn = find(frame, "bpgen_machine")
  local cat = recipe and prototypes.recipe[recipe] and prototypes.recipe[recipe].category
  if btn and cat then btn.elem_filters = { { filter = "crafting-category", crafting_category = cat } } end
end

local function status(frame, text)
  local s = find(frame, "bpgen_status")
  if s then s.caption = text or "" end
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
end

function M.open(player, prefill)
  prefill = prefill or {}
  unlocks[player.index] = nil
  local screen = player.gui.screen
  if screen[NAME] then screen[NAME].destroy() end
  local frame, holder
  if stdwin then
    local cw, ch = cam_size(player)
    frame, holder = stdwin.create(player, { name = NAME, title = "bpgen", width = cw + LEFT_W, height = ch + FRAME_H,
      min_width = 420 + LEFT_W, min_height = 300 + FRAME_H })
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

  -- what to make: tabs along the top
  local tabs = holder.add({ type = "flow", name = "bpgen_tabs", direction = "horizontal", tags = { mode = "line" } })
  tabs.style.horizontal_spacing = 0
  tabs.style.left_padding = 4
  for i, m in ipairs(MODES) do
    tabs.add({ type = "button", caption = MODE_NAMES[i], toggled = i == 1, tags = { bpgen = "tab", mode = m } })
  end
  local body = holder.add({ type = "flow", direction = "horizontal" })
  local outer = body.add({ type = "frame", style = "inside_shallow_frame", direction = "vertical" })
  outer.style.width = 300
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

  sec = section(line, "Also make (same inputs)",
    "Ticked products get their own machines and output belt, fed from the same input belts")
  sec.add({ type = "flow", name = "bpgen_also", direction = "vertical" }).add({ type = "label", caption = "(pick a recipe)" })

  sec = section(line, "Modules & beacons")
  fs = fields(sec)
  field(fs, "Machine modules", "fills every module slot").add({ type = "choose-elem-button", elem_type = "item-with-quality",
    name = "bpgen_module", elem_filters = { { filter = "type", type = "module" } } })
  field(fs, "Beacon", "and its quality (empty: no beacons)").add({ type = "choose-elem-button",
    elem_type = "entity-with-quality", name = "bpgen_beacon", elem_filters = { { filter = "type", type = "beacon" } } })
  field(fs, "Beacon modules").add({ type = "choose-elem-button", elem_type = "item-with-quality",
    name = "bpgen_beacon_module", elem_filters = { { filter = "type", type = "module" } } })

  sec = section(line, "Fine-tune")
  sec.add({ type = "checkbox", name = "bpgen_more_toggle", caption = "Inserters and machines per row", state = false,
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
  field(fs, "Fed by", "mixed belts: columns of machines between mixed belts, gears, cable, circuits... made at the top, only plates come in. My main bus: the same, the parts your bus carries taken from it, placed beside it. Rows: the older layout, its own 4-lane bus").add({
    type = "drop-down", name = "bpgen_mall_feed", items = { "rows on a 4-lane bus", "robots", "mixed belts", "my main bus" },
    selected_index = 3 })
  fs = fields(sec)
  field(fs, "Buffer", "crafts each machine keeps ahead").add({ type = "textfield", name = "bpgen_mall_buffer", text = "5",
    numeric = true, lose_focus_on_confirm = true }).style.width = 60
  field(fs, "Chest limit", "slots each product's chest may fill").add({ type = "textfield", name = "bpgen_chest_limit",
    text = "4", numeric = true, lose_focus_on_confirm = true }).style.width = 60

  -- a starter base: science
  local base = left.add({ type = "flow", name = "bpgen_base_box", direction = "vertical", visible = false })
  sec = section(base, "Science")
  fs = fields(sec)
  field(fs, "Per minute", "each pack, a minute").add({ type = "textfield", name = "bpgen_spm", text = "30", numeric = true,
    allow_decimal = true, lose_focus_on_confirm = true }).style.width = 60
  field(fs, "Lab").add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_lab",
    elem_filters = { { filter = "type", type = "lab" } } })
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
  sec = section(ext, "Your base")
  sec.add({ type = "button", caption = "Pick my base area", tags = { bpgen = "snapshot" },
    tooltip = "Optional: gives you the snapshot tool, to drag over the part of your base to build next to (its belts feed the new part). Without it, bpgen looks around you" })
  local snap_note = sec.add({ type = "label", name = "bpgen_snap_note", caption = "" })
  snap_note.style.single_line = false
  snap_note.style.maximal_width = 260

  -- a bus design: ore patches -> drills -> smelter columns -> balancers -> a main bus
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
  field(fs, "Belts a group").add({ type = "textfield", name = "bpgen_bus_group", text = "4", numeric = true,
    lose_focus_on_confirm = true }).style.width = 50
  field(fs, "Gap").add({ type = "textfield", name = "bpgen_bus_gap", text = "4", numeric = true,
    lose_focus_on_confirm = true }).style.width = 50
  field(fs, "Length").add({ type = "textfield", name = "bpgen_bus_length", text = "40", numeric = true,
    lose_focus_on_confirm = true }).style.width = 50
  sec.add({ type = "checkbox", name = "bpgen_bus_wood", caption = "A wood lane", state = true,
    tooltip = "A lane of wood beside the others, for wooden chests and small poles in a mall: fill the chest at its head (its burner inserter runs on the wood)" })
  sec.add({ type = "checkbox", name = "bpgen_bus_balance", caption = "Balancers", state = true,
    tooltip = "A 4-to-4 balancer at the start of each full group of 4 lanes of one item (2 lanes: a splitter)" })
  sec = section(bus, "Buildings", "empty: the electric mining drill and furnace, the fastest belt you have")
  fs = fields(sec)
  field(fs, "Drill", "3x3").add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_bus_drill",
    elem_filters = { { filter = "type", type = "mining-drill" } } })
  field(fs, "Furnace", "3x3, electric").add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_bus_furnace",
    elem_filters = { { filter = "crafting-category", crafting_category = "smelting" } } })
  field(fs, "Belt").add({ type = "choose-elem-button", elem_type = "entity", name = "bpgen_bus_belt",
    elem_filters = { { filter = "type", type = "transport-belt" } } })
  show_patches(frame, player)

  sec = section(left, "Next to my base")
  sec.add({ type = "checkbox", name = "bpgen_ext_bus", caption = "Build from my main bus", state = true,
    tooltip = "Extend and \"Next to my base\": when your base has a main bus there (3+ long straight belts side by side, flowing the same way), build beside it, branching off its lanes, with a new lane for an output it doesn't carry yet, and the bus continued past the build when it ends before it. Unticked: free ground nearest the belts" })
  local go = left.add({ type = "flow", direction = "horizontal" })
  go.style.top_margin = 8
  go.add({ type = "button", caption = "Plan", style = "confirm_button", tags = { bpgen = "plan" } })
  go = left.add({ type = "flow", direction = "horizontal" })  -- (looking at the base: a row of their own)
  go.add({ type = "button", caption = "What's short?", tags = { bpgen = "short" },
    tooltip = "What your factory uses faster than it makes (last 10 minutes): plan one of them" })
  go.add({ type = "button", caption = "Check my bus", tags = { bpgen = "check_bus" },
    tooltip = "Stand by your main bus: what each item's lanes carry and how much of it is used, which run dry, what your base lacks that isn't on the bus; add a lane of something, or make more of it beside the bus" })
  local st = left.add({ type = "label", name = "bpgen_status" })
  st.style.single_line = false
  st.style.maximal_width = 270

  local right = body.add({ type = "frame", style = "inside_deep_frame", direction = "vertical" })
  local cam = right.add({ type = "camera", name = "bpgen_cam", position = { 0, 0 }, surface_index = player.surface.index, zoom = 1,
    raise_hover_events = true, tooltip = safe.has("std") and "Drag to move, mouse wheel to zoom" or nil })
  local CAM_W, CAM_H = cam_size(player)
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
  local tools = under.add({ type = "flow", direction = "horizontal" })
  tools.add({ type = "button", caption = "−", style = "tool_button", tags = { bpgen = "zoom", f = 0.8 }, tooltip = "Zoom out" })
  tools.add({ type = "button", caption = "+", style = "tool_button", tags = { bpgen = "zoom", f = 1.25 }, tooltip = "Zoom in" })
  tools.add({ type = "button", caption = "Fit", tags = { bpgen = "fit" } })
  for _, a in ipairs({ { "◀", -1, 0 }, { "▲", 0, -1 }, { "▼", 0, 1 }, { "▶", 1, 0 } }) do
    tools.add({ type = "button", caption = a[1], style = "tool_button", tags = { bpgen = "pan", dx = a[2], dy = a[3] } })
  end
  tools.add({ type = "drop-down", name = "bpgen_section", items = {}, visible = false, tags = { bpgen_section = true },
    tooltip = "Which print of the book the preview shows" })
  tools.add({ type = "button", name = "bpgen_grip", caption = "⇲", style = "tool_button", tags = { bpgen = "size" },
    raise_hover_events = true, tooltip = safe.has("std") and "Drag to resize the preview, click for the next size"
      or "Next size (small, medium, large)" })
  local push = tools.add({ type = "empty-widget" })
  push.style.horizontally_stretchable = true
  tools.add({ type = "button", name = "bpgen_test", caption = "Test run", tags = { bpgen = "test" }, enabled = false,
    tooltip = "Runs the line for real on the preview: inputs fed, output counted, starved machines marked" })
  tools = under.add({ type = "flow", direction = "horizontal" })
  tools.add({ type = "button", name = "bpgen_cursor", caption = "Blueprint to cursor", style = "confirm_button",
    tags = { bpgen = "cursor" }, enabled = false })
  tools.add({ type = "button", name = "bpgen_copy", caption = "Copy string", tags = { bpgen = "copy" }, enabled = false })
  tools.add({ type = "button", name = "bpgen_place", caption = "Place near me", tags = { bpgen = "place" }, enabled = false,
    tooltip = "Ghosts at a free spot next to you, inputs pointed at your belts that carry them" })
  tools.add({ type = "button", name = "bpgen_place_base", caption = "Next to my base", tags = { bpgen = "place_base" },
    enabled = false, tooltip = "Stand by the part of your base to build next to: bpgen looks around you, puts this on free ground beside it (beside your main bus when there is one), taps the belts that carry its inputs, takes its output where it's needed and links the power. Ghosts, ready for your bots" })
  tools.add({ type = "button", name = "bpgen_nudge_back", caption = "◀", style = "tool_button", visible = false,
    tags = { bpgen = "nudge", d = -1 }, tooltip = "Move it back along your bus (or west), then look again" })
  tools.add({ type = "button", name = "bpgen_nudge_fwd", caption = "▶", style = "tool_button", visible = false,
    tags = { bpgen = "nudge", d = 1 }, tooltip = "Move it on along your bus (or east), then look again" })
  tools.add({ type = "button", caption = "Undo", tags = { bpgen = "unplace" }, tooltip = "Removes the ghosts bpgen put down (still unbuilt)" })
  tools.add({ type = "button", caption = "History", tags = { bpgen = "history" },
    tooltip = "Blueprints you took from bpgen (here or in the web app)" })
  -- (what's said about the plan, in one scroll area of a fixed height: the preview gets the rest of the window)
  local info = under.add({ type = "scroll-pane", name = "bpgen_info", horizontal_scroll_policy = "never" })
  info.style.maximal_height = INFO_H  -- (as tall as what it says, up to this: the preview takes the rest)
  info.style.horizontally_stretchable = true
  local test = info.add({ type = "label", name = "bpgen_test_status" })
  test.style.single_line = false
  test.style.maximal_width = CAM_W - 24
  local stats = info.add({ type = "label", name = "bpgen_stats" })
  stats.style.single_line = false
  stats.style.maximal_width = CAM_W - 24
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
    right.style.vertically_stretchable, right.style.horizontally_stretchable = true, true
    local fw, fh = stdwin.size(player, NAME)
    if fw then apply_size(player, frame, fw - LEFT_W, fh - FRAME_H) end
  else
    frame.force_auto_center()
  end

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
  local req = { tick = game.tick, surface = player.surface.name, bonuses = ctx.bonuses(force),
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
      layout = find(frame, "bpgen_mall_feed").selected_index >= 3 and "grid" or nil,
      bus_feed = find(frame, "bpgen_mall_feed").selected_index == 4 or nil,
      origin = { x = player.position.x, y = player.position.y },
      buffer_crafts = tonumber(find(frame, "bpgen_mall_buffer").text) or 5,
      chest_limit = tonumber(find(frame, "bpgen_chest_limit").text) or 4,
      belt = req.belt }
    return req
  elseif mode == "extend" then
    local item = value(frame, "bpgen_ext_item")
    if not item then return nil, "pick the item to make" end
    req.params = { mode = "extend", item = item, rate_per_min = tonumber(find(frame, "bpgen_ext_rate").text) or 30,
      assembler = best_of_type(player, "assembling-machine", "crafting"), furnace = best_of_type(player, "furnace", "smelting"),
      belt = req.belt or u.belts[#u.belts], bus = find(frame, "bpgen_ext_bus").state and "auto" or "off" }
    return req
  elseif mode == "busdesign" then
    local list = {}
    for i, p in ipairs(patch_areas[player.index] or {}) do
      local a = p.area
      list[i] = { a.left_top.x, a.left_top.y, a.right_bottom.x, a.right_bottom.y }
    end
    if #list == 0 then return nil, "pick the ore patches first" end
    req.params = { mode = "busdesign", patches = list, origin = { x = player.position.x, y = player.position.y },
      direction = BUS_DIRS[find(frame, "bpgen_bus_dir").selected_index] or "north",
      group = tonumber(find(frame, "bpgen_bus_group").text) or 4, gap = tonumber(find(frame, "bpgen_bus_gap").text) or 4,
      length = tonumber(find(frame, "bpgen_bus_length").text) or 40, balance = find(frame, "bpgen_bus_balance").state,
      wood = find(frame, "bpgen_bus_wood").state,
      drill = value(frame, "bpgen_bus_drill"), furnace = value(frame, "bpgen_bus_furnace"),
      belt = value(frame, "bpgen_bus_belt") or u.belts[#u.belts] }
    return req
  elseif mode == "base" then
    req.params = { mode = "base", spm = tonumber(find(frame, "bpgen_spm").text) or 30,
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
  local ghosts, _, area, marks = place.place(player, r.blueprint, nil, r.res.absolute.box)
  placed[player.index], unmark[player.index] = ghosts, marks
  if area then
    rendering.draw_rectangle({ color = { 0.2, 0.8, 1 }, width = 4, filled = false, left_top = area[1],
      right_bottom = area[2], surface = player.surface, players = { player }, time_to_live = 60 * 120 })
  end
  local a = r.res.absolute
  if a.head then  -- (a new bus lane: where to feed it)
    rendering.draw_text({ text = { "", "feed [item=" .. a.item .. "] here" }, surface = player.surface, target = a.head,
      color = { 1, 0.85, 0.2 }, scale = 2, alignment = "center", use_rich_text = true, players = { player },
      time_to_live = 60 * 300 })
    rendering.draw_circle({ color = { 1, 0.85, 0.2 }, radius = 0.7, width = 4, filled = false, target = a.head,
      surface = player.surface, players = { player }, time_to_live = 60 * 300 })
    status(frame, string.format("Placed a new [item=%s] lane (%d ghosts): feed it at its head (marked).", a.item, #ghosts))
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    return
  end
  if r.res.mode == "busdesign" or a.fitted then
    status(frame, string.format(a.fitted and "Placed %d ghosts (outlined): the base at the end of your bus, its head on the lanes."
      or "Placed %d ghosts (outlined): drills on your patches, the bus head near you. Wire its poles to your grid.", #ghosts))
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    return
  end
  status(frame, string.format("Placed %d ghosts next to your base (outlined): %d belt taps%s%s.", #ghosts, a.taps or 0,
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
  status(frame, string.format("%d pieces of that lane marked for upgrade to [entity=%s].", n, target.name))
end

function M.plan(player)
  local frame = player.gui.screen[NAME]
  if not frame then return end
  local req, err = request(player, frame)
  if not req then return status(frame, err) end
  local ok, why = ctx.start(player, req)
  status(frame, ok and "Planning..." or ("Can't plan in game: " .. tostring(why)))
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
function M.on_result(player, res)
  local frame = player.gui.screen[NAME]
  if not frame then return false end
  if not res.blueprint then
    if res.measure and not M.measuring(player) then
      -- (inserter setups bpgen hasn't measured: measured here and now, live, then planned again)
      preview_surface()
      bench.calibrate(player.index, res.measure)
      status(frame, string.format("Measuring %d inserter setups bpgen hasn't seen yet (%d s), then planning...",
        #res.measure.cases, math.ceil(((res.measure.warmup or 300) + (res.measure.measure or 1800)) / 60)))
    else
      status(frame, "Can't plan this: " .. tostring(res.error))
    end
    return true
  end
  local old = bench.find("test", player.index)
  if old then bench.stop(old.id) end
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
  find(frame, "bpgen_cursor").enabled = true
  find(frame, "bpgen_copy").enabled = safe.has("std")
  find(frame, "bpgen_place").enabled = where ~= nil
  find(frame, "bpgen_place_base").enabled = res.absolute == nil and (res.mode == "line" or res.mode == "mall"
    or res.mode == "base")
  local pb = find(frame, "bpgen_place")
  pb.caption = res.absolute and "Place it" or "Place near me"
  pb.tooltip = res.absolute and "Ghosts where the preview shows it, its taps replacing the belts they cut into"
    or "Ghosts at a free spot next to you, inputs pointed at your belts that carry them"
  local fixed = res.mode == "lane" or res.mode == "busdesign"  -- (where it is is the point: no arrows)
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
      local runs = s.expected * 60 / made / (1 + (s.productivity or 0))
      for _, ing in pairs(recipe.ingredients) do
        need[#need + 1] = string.format("%s %.1f", sig(ing.name), runs * ing.amount)
      end
    end
  end
  table.sort(need)
  if #need > 0 then lines[#lines + 1] = "Needs a minute: " .. table.concat(need, "   ") end
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
  local l = box.add({ type = "label", caption = "[img=utility/warning_icon] " .. table.concat(lines, "\n") })
  l.style.single_line = false
  l.style.maximal_width = cam_size(game.get_player(frame.player_index)) - 24
end

local function show_ideas(frame, r)
  local ideas = r.ideas or {}
  if #ideas < 2 then return end
  local box = extra(frame)
  box.add({ type = "label", caption = "Modules would make it:", style = "caption_label" })
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

local function show_history(frame, items)
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

local histories = {}  -- player index -> the last history list (its blueprints for "To cursor")

local function show_short(frame, rows)
  local box = extra(frame)
  box.clear()
  if #rows == 0 then
    box.add({ type = "label", caption = "Nothing's short (or bpgen hasn't counted 10 minutes yet)." })
    return
  end
  box.add({ type = "label", caption = "Used faster than made (a minute):", style = "caption_label" })
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
  box.add({ type = "label", style = "caption_label", caption = string.format("Your bus: %d lanes%s, %d tiles%s", b.lanes,
    #b.pipes > 0 and string.format(" and %d pipe%s", #b.pipes, #b.pipes > 1 and "s" or "") or "", b.length,
    b.measured and "" or " (not measured)") })
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
    box.add({ type = "label", style = "caption_label", caption = "Your base lacks, and the bus doesn't carry:" })
    local s = box.add({ type = "table", column_count = 2 })
    for _, it in ipairs(r.short) do
      s.add({ type = "label", caption = string.format("%s  %.0f/min short", sig(it.item), it.short) })
      s.add({ type = "button", caption = "Make it by the bus", tags = { bpgen = "bus_make", item = it.item, rate = it.short },
        tooltip = "Plan making it next to the bus from what the bus carries, its output on a new lane (Extend)" })
    end
  end
end

function M.on_api(player, tag, out)
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
      local f = parent.add({ type = "flow", direction = "horizontal" })
      f.style.vertical_align = "center"
      f.style.horizontally_stretchable = true
      f.add({ type = "checkbox", state = false, caption = named(sib.item), tags = { item = sib.item, recipe = sib.recipe } })
      row_note(f, "")
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

local function describe_test(run, final)
  local seconds, cases = bench.rates(run)
  local t = game.tick - run.start
  local count, starved = bench.statuses(run)
  local machines = {}
  for k, n in pairs(count) do machines[#machines + 1] = n .. " " .. k:gsub("_", " ") end
  table.sort(machines)
  local head
  if t < run.warmup then
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

bench.on_progress = function(run)
  local player = game.get_player(run.player)
  local frame = player and player.gui.screen[NAME]
  if not frame then return end
  if run.kind == "test" then
    local text, starved = describe_test(run, false)
    find(frame, "bpgen_test_status").caption = text
    mark(player, starved)
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
end

local function to_cursor(player)
  local r = results[player.index]
  if not r then return end
  if not player.clear_cursor() then return player.print("[bpgen] your hand is full") end
  player.cursor_stack.set_stack({ name = "blueprint" })
  if player.cursor_stack.import_stack(r.blueprint) == 1 then
    player.clear_cursor()
    return player.print("[bpgen] the blueprint string didn't import")
  end
  local frame = player.gui.screen[NAME]
  if frame then frame.destroy() end  -- (out of the way of placing it)
end

local function on_click(e)
  local el = e.element
  if not (el and el.valid) then return end
  local action = el.tags and el.tags.bpgen
  if not action then return end
  local player = game.get_player(e.player_index)
  local frame = player.gui.screen[NAME]
  local r = results[player.index]
  if action == "close" then
    if frame then frame.destroy() end
  elseif action == "plan" then
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
    else
      bench.test(player.index, r.res.test, r.where.origin, r.where.area, PREVIEW)
      find(frame, "bpgen_test").caption = "Stop test"
      find(frame, "bpgen_test_status").caption = "Starting..."
    end
  elseif action == "cursor" and r then
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    to_cursor(player)
  elseif action == "copy" and r and safe.has("std") then
    native.call("std", "clipboard_set", r.blueprint)
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    player.print("[bpgen] blueprint string copied")
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
  elseif action == "snapshot" then
    if player.clear_cursor() then
      player.cursor_stack.set_stack({ name = "bpgen-snapshot" })
      status(frame, "Drag over your base with the snapshot tool, then press Plan.")
    end
  elseif action == "patches" then
    if player.clear_cursor() then
      player.cursor_stack.set_stack({ name = "bpgen-patches" })
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
        local found, missing = place.guide(player, area, origin, r.res.test and r.res.test.case.sources or {})
        status(frame, string.format("Placed %d ghosts next to you (outlined). %s", #ghosts,
          #missing > 0 and ("Nothing of yours nearby carries " .. table.concat(missing, " ")) or
          (found > 0 and "Dashed lines: where each input can come from." or "")))
      end
      ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    end
  elseif action == "unplace" then
    local n = 0
    for _, g in pairs(placed[player.index] or {}) do
      if g.valid and (g.type == "entity-ghost" or g.type == "tile-ghost") then g.destroy() n = n + 1 end
    end
    for _, e in pairs(unmark[player.index] or {}) do  -- (the belts its splitters would have replaced: kept)
      if e.valid and e.to_be_deconstructed() then e.cancel_deconstruction(player.force, player) end
    end
    placed[player.index], unmark[player.index] = nil, nil
    status(frame, n .. " placed ghosts removed")
  elseif action == "history" then
    ctx.api(player, "history", {}, "history")
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
  if not safe.has("std") then return end
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
      if m.left then
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

-- (tests: a button press, as its tags)
function M.click(player_index, tags)
  on_click({ element = { valid = true, tags = tags }, player_index = player_index })
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
  [defines.events.on_gui_elem_changed] = on_elem_changed,
  [defines.events.on_gui_confirmed] = function(e)
    local n = e.element and e.element.valid and e.element.name
    if n == "bpgen_rate" or n == "bpgen_per_row" or n == "bpgen_spm" then M.plan(game.get_player(e.player_index)) end
  end,
  [defines.events.on_gui_checked_state_changed] = function(e)
    local el = e.element
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
    if player.gui.screen[NAME] then player.gui.screen[NAME].destroy() else M.open(player) end
  end,
  ["bpgen-window"] = function(e)
    local player = game.get_player(e.player_index)
    if player.gui.screen[NAME] then player.gui.screen[NAME].destroy() else M.open(player) end
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
