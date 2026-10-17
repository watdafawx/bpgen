-- The window's settings as the player left them (kept on every change, put back when it opens again), which mode is
-- shown, the step bar, the badges on the folds, Plan breathing when the settings changed since the last plan.

local W = require("window_core")
local ui, web, safe, MODES, NAME, T, find, forms = W.ui, W.web, W.safe, W.MODES, W.NAME, W.T, W.find, W.forms
local join, patch_areas, results = W.join, W.patch_areas, W.results

local NOT_KEPT = { bpgen_only_unlocked = true, bpgen_section = true, bpgen_docked = true, bpgen_web = true,
  bpgen_size = true }

local function keep_form(player)
  local frame = player.gui.screen[NAME]
  if not (frame and frame.valid) then return end
  local old = forms[player.index]
  local out = { values = {}, products = {}, stale = old and old.stale or nil }
  local function walk(el)
    for _, c in pairs(el.children) do
      local n = c.name
      if n ~= "" and n:sub(1, 6) == "bpgen_" and not NOT_KEPT[n] then
        local t = c.type
        if t == "choose-elem-button" then out.values[n] = { e = c.elem_value or false }
        elseif t == "textfield" then out.values[n] = { t = c.text }
        elseif t == "checkbox" then out.values[n] = { s = c.state }
        elseif t == "drop-down" then out.values[n] = { i = c.selected_index } end
      end
      if c.type == "choose-elem-button" and c.tags.bpgen_product and c.elem_value then
        out.products[#out.products + 1] = c.elem_value
      end
      walk(c)
    end
  end
  walk(frame)
  local tabs = find(frame, "bpgen_tabs")
  out.mode = tabs and tabs.tags.mode
  out.lines = W.more_lines(frame)
  forms[player.index] = out
end

--- the kept settings put back in a new window (not the recipe and machine: open's prefill does those, with what
--- follows from them)
local function restore_form(frame, kept)
  for name, v in pairs(kept.values or {}) do
    local el = name ~= "bpgen_recipe" and name ~= "bpgen_machine" and find(frame, name)
    if el then
      -- (a picked thing gone from the game since, or an index past a list that got shorter: left as it is)
      pcall(function()
        if v.e ~= nil and el.type == "choose-elem-button" then el.elem_value = v.e or nil
        elseif v.t and el.type == "textfield" then el.text = v.t
        elseif v.s ~= nil and el.type == "checkbox" then
          el.state = v.s
          local target = el.tags.bpgen_toggle and find(frame, el.tags.bpgen_toggle)
          if target then target.visible = v.s end
        elseif v.i and el.type == "drop-down" and v.i <= #el.items then el.selected_index = v.i end
      end)
    end
  end
end

--- Plan breathes (and a note says so) when the settings changed since the last plan
local function stale(player, on)
  local frame = player.gui.screen[NAME]
  local plan, note = find(frame, "bpgen_plan"), find(frame, "bpgen_stale")
  if not (plan and note) then return end
  if forms[player.index] then forms[player.index].stale = on or nil end
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
  if root.name ~= NAME then return end
  local player = game.get_player(e.player_index)
  keep_form(player)
  if not results[e.player_index] then return end
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
  W.steps(frame)
  local player = game.get_player(frame.player_index)
  if forms[player.index] then forms[player.index].mode = mode end
  web.dirty(player)
end

--- the step bar: 1 What (a mode, always), 2 How (until there is a plan), 3 Where (a plan to place)
function W.steps(frame)
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

local function mode_of(frame)
  return (find(frame, "bpgen_tabs").tags or {}).mode or "line"
end

local function value(frame, name)
  local el = find(frame, name)
  return el and el.elem_value
end

--- Line's More lines: the recipes picked, in order (the empty slot left out)
function W.more_lines(frame)
  local out = {}
  local t = find(frame, "bpgen_lines")
  for _, b in pairs(t and t.children or {}) do
    if b.elem_value then out[#out + 1] = b.elem_value end
  end
  return out
end

-- (what the other parts call)
W.badges_of = badges_of
W.changed = changed
W.glow = glow
W.keep_form = keep_form
W.mode_of = mode_of
W.restore_form = restore_form
W.set_mode = set_mode
W.show_patches = show_patches
W.stale = stale
W.value = value
