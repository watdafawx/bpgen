-- The answers of bpgen.ingame:api in the window (control.lua's job loop): the ingredients to make here, the other recipes
-- a machine could make, the save check, module ideas, the shortages, the bus report and History.

local W = require("window_core")
local ui, web, safe, ctx, NAME, T, adopt, find = W.ui, W.web, W.safe, W.ctx, W.NAME, W.T, W.adopt, W.find
local histories, join, sig, status = W.histories, W.join, W.sig, W.status

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

-- answers of bpgen.ingame:api (from control.lua's job loop): suggestions and history in the window
local function extra(frame)
  return find(frame, "bpgen_extra")
end

local function show_check(frame, r)
  if not r.state then return end
  local box = extra(frame)
  if r.ok then
    local badges = find(frame, "bpgen_badges")
    if badges and ui.themed then W.add_badge(frame, { "", "[img=utility/check_mark_green] ", T("can-build-badge") }) return end
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
    W.add_badge(frame, { "", "[img=utility/warning_icon] ", T("cant-build-badge") }, join(lines, "\n"))
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
    t.add({ type = "button", caption = T("plan-it"), tags = { bpgen = "plan_short", bpgen_recipe = r.recipe, rate = r.short } })
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

--- the other recipes the machine could make, a tick each (the near ones folded)
local function show_also(frame, r)
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
    local cb = f.add({ type = "checkbox", state = false, caption = caption, tags = { item = sib.item, bpgen_recipe = sib.recipe } })
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
  W.fit_panel(box)
  ui.set_badge(find(frame, "bpgen_also_badge"), #r > 0 and tostring(#r) or nil)
end

--- the recipe's ingredients: made here or brought on a belt
local function show_tree(frame, r)
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
    W.show_history(frame, r)
  elseif tag == "also" and r then show_also(frame, r)
  elseif tag == "tree" and r then show_tree(frame, r)
  end
end

local function tree(player, frame)
  local recipe, machine = W.value(frame, "bpgen_recipe"), W.value(frame, "bpgen_machine")
  find(frame, "bpgen_also").clear()
  find(frame, "bpgen_also").add({ type = "label", caption = recipe and "..." or T("pick-a-recipe") })
  if recipe then ctx.api(player, "siblings", { recipe = recipe, machine = machine and machine.name }, "also") end
  find(frame, "bpgen_make").clear()
  find(frame, "bpgen_make").add({ type = "label", caption = recipe and "..." or T("pick-a-recipe") })
  if recipe then ctx.api(player, "recipe_tree", { recipe = recipe, machine = machine and machine.name }, "tree") end
end

-- (pickers an answer adds follow "Only unlocked" too)
function W.on_api(player, tag, out)
  web.dirty(player)
  local r = on_api(player, tag, out)
  W.restrict(player)
  local frame = player.gui.screen[NAME]
  adopt(player)  -- (what the answer added: the player's theme)
  return r
end

-- (what the other parts call)
W.extra = extra
W.note_color = note_color
W.tree = tree
