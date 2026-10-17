-- A plan's answer in the window: the progress bar, the preview, the tiles and badges, the numbers and notes, which buttons
-- may be pressed now, and what to ask Python next (can the save build it, module ideas).

local W = require("window_core")
local ui, web, bench, safe, ctx, NAME, PREVIEW, T = W.ui, W.web, W.bench, W.safe, W.ctx, W.NAME, W.PREVIEW, W.T
local adopt, find, join, results, say, sig, status = W.adopt, W.find, W.join, W.results, W.say, W.sig, W.status
local to_sandbox = W.to_sandbox

--- a plan's answer (from control.lua's job loop)
--- the plan getting on: {fraction 0..1, step = what it does now, lines = what it did} (from bpgen.ingame:progress)
function W.on_progress(player, p)
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

--- the progress box away (a measuring run keeps it)
local function hide_progress(frame, res)
  local prog = find(frame, "bpgen_progress")
  if prog and not res.measure then
    prog.visible = false
    local log_ = find(prog, "bpgen_log")
    if log_ then log_.caption = "" end
  end
end

--- a plan that came back without a blueprint: an error to say, or inserter setups to measure first
local function no_blueprint(player, frame, res)
  if not res.measure then to_sandbox[player.index] = nil end
  if res.measure and not W.measuring(player) then
    -- (inserter setups bpgen hasn't measured: measured here and now, live, then planned again)
    W.preview_surface()
    bench.calibrate(player.index, res.measure)
    status(frame, T("measuring-setups", #res.measure.cases,
      math.ceil(((res.measure.warmup or 300) + (res.measure.measure or 1800)) / 60)))
  else
    say(player, T("cant-plan-this", tostring(res.error)))
  end
  return true
end

--- the plan built on the preview and the camera on it; -> center, w, h, where (or the error in w)
local function show_preview(player, frame, res, reuse)
  local old = bench.find("test", player.index)
  if old then bench.stop(old.id) end
  W.test_speed_off(frame)  -- (a test run's fast-forward: off with it)
  find(frame, "bpgen_test_status").caption = ""
  local center, w, h, where = W.build_preview(player, res.blueprint, res.absolute and res.absolute.box, nil, reuse)
  local sec = find(frame, "bpgen_section")
  sec.items = res.sections or {}
  sec.visible = res.sections ~= nil and #res.sections > 1
  if sec.visible then sec.selected_index = 1 end
  local cam, empty = find(frame, "bpgen_cam"), find(frame, "bpgen_empty")
  if center then
    local z = W.fit(player, w, h)
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
  return center, w, h, where
end

--- the buttons that have a plan to act on now (To cursor, Copy, Place it, Place by my base, Sandbox, the moves, Test run)
local function result_buttons(frame, res, where)
  local cursor = find(frame, "bpgen_cursor")
  cursor.enabled = true
  if ui.themed then ui.set_role(cursor, "ok") else cursor.style = "confirm_button" end
  find(frame, "bpgen_copy").enabled = safe.has("std")
  local pl = find(frame, "bpgen_place")  -- (Place it: where bpgen placed it, next to the base)
  pl.enabled, pl.visible = res.absolute ~= nil, res.absolute ~= nil
  find(frame, "bpgen_place_base").enabled = res.absolute == nil and (res.mode == "line" or res.mode == "mall"
    or res.mode == "base")
  find(frame, "bpgen_sandbox").enabled = (where ~= nil or res.absolute ~= nil) and res.mode ~= "ship"
  -- (next to the base: moved, turned, another spot, each tied in again; a lane's place is the point, and a bus
  -- design's arrows move its head sideways)
  local adjust = res.absolute ~= nil and res.mode ~= "lane" and res.mode ~= "busdesign"
  local spot = adjust or find(frame, "bpgen_place_base").enabled
  find(frame, "bpgen_moves").visible = adjust or spot or res.mode == "busdesign"
  for _, n in ipairs({ "left", "up", "down", "right" }) do
    find(frame, "bpgen_nudge_" .. n).visible = adjust or res.mode == "busdesign"
  end
  find(frame, "bpgen_base_spot").visible = spot
  find(frame, "bpgen_base_rotate").visible = adjust
  find(frame, "bpgen_base_redo").visible = adjust
  local tb = find(frame, "bpgen_test")
  tb.enabled = res.test ~= nil and where ~= nil
  tb.caption = T("test-run")
end

--- what the line takes a minute ("icon n" each, sorted): its expected output through the recipe, less what productivity adds
local function needs_of(s)
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
  return need
end

--- parts the plan made inside the blueprint on its own, e.g. fed from the bus: ticked, as if picked
local function tick_stages(frame, s)
  -- (parts the plan made inside the blueprint on its own, e.g. fed from the bus: ticked, as if picked)
  local make_box = find(frame, "bpgen_make")
  for _, st in pairs(s.stages or {}) do
    for _, r in pairs(make_box and make_box.children or {}) do
      local on = r.children[1]
      if on and on.type == "checkbox" and on.tags and on.tags.item == st.item then on.state = true end
    end
  end
end

local function mall_base_lines(lines, res)
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
end

--- the details (bpgen_stats) of the plan; -> what it needs
local function stats_text(frame, res, s, center, w, h)
  local lines = {}
  if s.machines and not res.mall and not res.base then
    lines[#lines + 1] = T("stats-line", sig(s.recipe or ""), s.machines, "[entity=" .. (s.machine or "?") .. "]",
      sig(s.output or ""), string.format("%.1f", (s.expected or 0) * 60),
      s.belt and { "", "   ", T("stats-belt", "[entity=" .. s.belt .. "]") } or "")
  end
  local need = needs_of(s)
  if #need > 0 then lines[#lines + 1] = T("stats-needs", table.concat(need, "   ")) end
  tick_stages(frame, s)
  mall_base_lines(lines, res)
  if center then
    lines[#lines + 1] = T("stats-size", math.ceil(w), math.ceil(h), string.format("%.0f", (res.seconds or 0) * 1000))
  end
  for _, n in ipairs(res.notes or {}) do lines[#lines + 1] = { "", "• ", n } end
  find(frame, "bpgen_stats").caption = join(lines, "\n")
  return need
end

--- what next: an overdrawn bus lane can be upgraded
local function upgrade_buttons(frame, res)
  find(frame, "bpgen_extra").clear()
  for i, u in ipairs(res.absolute and res.absolute.upgrades or {}) do
    local icons = {}
    for _, it in ipairs(u.items) do icons[#icons + 1] = sig(it) end
    find(frame, "bpgen_extra").add({ type = "button", caption = T("upgrade-lane", table.concat(icons)),
      tags = { bpgen = "upgrade_lane", i = i }, tooltip = T("upgrade-lane-tip", string.format("%.0f", u.need * 60)) })
  end
end

--- can the save build it, and for a line, what modules would do
local function ask_helpers(player, res, s)
  ctx.api(player, "save_check", { blueprint = res.blueprint }, "check")
  if s.recipe and s.machine then
    ctx.api(player, "module_ideas", { recipe = s.recipe, machine = s.machine, belt = s.belt,
      rate_per_min = s.expected and s.expected * 60, belts = W.unlocked(player).belts }, "ideas")
  end
end

function W.on_result(player, res, reuse)
  local frame = player.gui.screen[NAME]
  if not frame then return false end
  web.dirty(player)
  hide_progress(frame, res)
  if not res.blueprint then return no_blueprint(player, frame, res) end
  local center, w, h, where = show_preview(player, frame, res, reuse)
  show_tiles(frame, res, w, h)
  W.glow(player, false)
  W.stale(player, false)
  W.test_shown(player, frame, false)
  W.steps(frame)
  result_buttons(frame, res, where)
  local s = res.summary or {}
  local need = stats_text(frame, res, s, center, w, h)
  show_result(frame, res, center and w, h, need)
  adopt(player)
  status(frame, res.mode == "lane" and T("preview-shows-lane") or res.mode == "busdesign" and T("preview-shows-bus")
    or res.absolute and T("preview-shows-base") or "")
  upgrade_buttons(frame, res)
  ask_helpers(player, res, s)
  if to_sandbox[player.index] then  -- ("Try in sandbox" asked for it by the base first: there now, on to the lab)
    to_sandbox[player.index] = nil
    if res.absolute then W.try_sandbox(player) end
  end
  return true
end

-- (what the other parts call)
W.add_badge = add_badge
