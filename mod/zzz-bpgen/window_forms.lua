-- The window's left panel: the settings of each mode (Line, Mall, Refit, Base, Extend, Move, Ship, Bus design), the "Next
-- to my base" box and the foot pinned under the panel (progress, status, Plan). One builder per mode.

local W = require("window_core")
local ui, T = W.ui, W.T

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

-- Line's "More lines": a recipe slot each, an empty one at the end; each planned as its own line
local function line_slot(t, recipe)
  local b = t.add({ type = "choose-elem-button", elem_type = "recipe", tags = { bpgen_line = true },
    elem_filters = { { filter = "hidden", invert = true } } })
  if recipe then b.elem_value = recipe end
  return b
end

--- a production line: sections as the web app's
local function build_line(left, prefill)
  local sec, fs
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
  line_slot(field(sec, T("more-lines"), T("more-lines-tip")).add({ type = "table", name = "bpgen_lines", column_count = 7 }))
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
end

--- a mall: many products into chests
local function build_mall(left)
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
end

local function build_refit(left)
  local sec, fs
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
end

--- a starter base: science
local function build_base(left)
  local sec, fs
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
end

local function build_extend(left)
  local sec, fs
  -- next to the base: a whole chain fed by what the base's belts carry, tapped in, powered
  local ext = left.add({ type = "flow", name = "bpgen_extend_box", direction = "vertical", visible = false })
  sec = section(ext, T("make"), T("make-tip"))
  fs = fields(sec)
  field(fs, T("item")).add({ type = "choose-elem-button", elem_type = "item", name = "bpgen_ext_item" })
  field(fs, T("per-minute")).add({ type = "textfield", name = "bpgen_ext_rate", text = "30", numeric = true,
    allow_decimal = true, lose_focus_on_confirm = true }).style.width = 60
  sec = section(ext, T("your-base"))
  sec.add({ type = "button", caption = T("pick-base-area"), tags = { bpgen = "snapshot" }, tooltip = T("pick-base-area-tip") })
  local snap_note = sec.add({ type = "label", name = "bpgen_snap_note", caption = "" })
  snap_note.style.single_line = false
  snap_note.style.maximal_width = 260
end

local function build_move(left)
  local sec
  -- move: a section of the base somewhere else (its own mode: Plan gives the move tool)
  local moveb = left.add({ type = "flow", name = "bpgen_move_box", direction = "vertical", visible = false })
  sec = section(moveb, T("move-section"), T("move-section-tip"))
  ui.add(sec, { type = "label", caption = T("move-how") }, "dim", { single_line = false, maximal_width = 260 })
end

--- a ship: a space platform round its hub, self-sufficient (asteroids to fuel and ammo)
local function build_ship(left)
  local sec, fs
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
end

--- a bus design: ore patches -> drills -> smelter columns -> balancers -> a main bus
local function build_bus(left)
  local sec, fs
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
end

local function build_next(left)
  local sec
  sec = section(left, T("next-to-base"), nil, "bpgen_next_box")
  sec.add({ type = "checkbox", name = "bpgen_ext_bus", caption = T("ext-bus"), state = true, tooltip = T("ext-bus-tip") })
end

  --- pinned under the panel's scroll, always in sight: planning's progress, what was said last, Plan (breathing when
  -- something changed since the last plan), the tools that look at the base
local function build_foot(outer)
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
end

--- the left panel's boxes, one per mode (only the mode shown is visible)
local function build_left(left, prefill)
  build_line(left, prefill)
  build_mall(left)
  build_refit(left)
  build_base(left)
  build_extend(left)
  build_move(left)
  build_ship(left)
  build_bus(left)
  build_next(left)
end

-- (what the other parts call)
W.build_foot = build_foot
W.build_left = build_left
W.fit_panel = fit_panel
W.line_slot = line_slot
W.section = section
