-- The window's game events: the picker and checkbox changes, the camera, the screen and scale, the shortcut. control.lua
-- registers window.handlers.

local W = require("window_core")
local ui, web, stdwin, bench, CARD, NAME, T = W.ui, W.web, W.stdwin, W.bench, W.CARD, W.NAME, W.T
local esc_check, find, results = W.esc_check, W.find, W.results

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
  if el.tags and el.tags.bpgen_line then
    -- (Line's more lines: as the mall's products; planned again with the line added or gone)
    local t = el.parent
    local kids = t.children
    if el.elem_value and kids[#kids] == el then W.line_slot(t)
    elseif not el.elem_value and kids[#kids] ~= el then el.destroy() end
    local player = game.get_player(e.player_index)
    local frame = player.gui.screen[NAME]
    W.restrict(player)
    if frame and W.value(frame, "bpgen_recipe") then W.plan(player) end
    return
  end
  if el.name ~= "bpgen_recipe" then return end
  local player = game.get_player(e.player_index)
  local frame = player.gui.screen[NAME]
  W.tree(player, frame)
  W.set_machine_filter(frame, el.elem_value)
  local mbtn = find(frame, "bpgen_machine")
  local cur = mbtn.elem_value
  if el.elem_value and not (cur and W.crafts(prototypes.entity[cur.name], el.elem_value)) then
    local m = W.best_machine(player, el.elem_value)
    mbtn.elem_value = m and { name = m, quality = "normal" } or nil
  end
  if el.elem_value then W.plan(player) end
end

--- the shortcut and Ctrl+Alt+B: the window closed, or opened; a tool in hand: its card shows, the window hides (back with the tool put away)
local function toggle_window(e)
  local player = game.get_player(e.player_index)
  local f = player.gui.screen[NAME]
  if f and player.gui.screen[CARD] then player.clear_cursor() W.tool_done(player)
  elseif f then W.close(player) else W.open(player) end
end

W.handlers = {
  [defines.events.on_tick] = function(e)
    bench.handlers[defines.events.on_tick](e)
    W.warm_tick()
    -- (a fast-forward no test run needs any more, however it ended: a run dropped, a script reloaded: off)
    if storage.bpgen_speed_was and e.tick % 60 == 0 and not W.testing() then
      W.test_speed_off(nil)
      for _, p in pairs(game.players) do W.test_speed_off(p.gui.screen[NAME]) end
    end
    W.camera_tick()
    if storage.bpgen_state and next(storage.bpgen_state.esc_check or {}) then W.esc_tick() end  -- (stored: no next())
  end,
  [defines.events.on_gui_closed] = function(e)
    local el = e.element
    local player = game.get_player(e.player_index)
    local frame = player.gui.screen[NAME]
    if not (frame and frame.valid and frame.visible) then return end
    if el and el.valid and el == frame then esc_check[e.player_index] = "closed"
    elseif not esc_check[e.player_index] then esc_check[e.player_index] = "back" end
  end,
  -- (a player gone: their test run, and its fast-forward, with them)
  [defines.events.on_player_left_game] = function(e) W.test_stop(e.player_index) end,
  [defines.events.on_gui_hover] = function(e) W.on_hover(e) end,
  [defines.events.on_gui_leave] = function(e) W.on_leave(e) end,
  [defines.events.on_gui_click] = function(e) W.on_click(e) end,
  [defines.events.on_player_cursor_stack_changed] = function(e) W.on_cursor(game.get_player(e.player_index)) end,
  [defines.events.on_gui_elem_changed] = function(e)
    W.changed(e)
    on_elem_changed(e)
    W.restrict(game.get_player(e.player_index))  -- (pickers it added)
    W.keep_form(game.get_player(e.player_index))  -- (and what it filled in: a recipe's machine)
  end,
  [defines.events.on_gui_confirmed] = function(e)
    local n = e.element and e.element.valid and e.element.name
    if n == "bpgen_rate" or n == "bpgen_per_row" or n == "bpgen_spm" then W.plan(game.get_player(e.player_index)) end
  end,
  [defines.events.on_gui_text_changed] = W.changed,
  [defines.events.on_gui_checked_state_changed] = function(e)
    W.changed(e)
    local el = e.element
    if el and el.valid and el.name == "bpgen_only_unlocked" then
      return W.set_only_unlocked(game.get_player(e.player_index), el.state)
    end
    if el and el.valid and el.name == "bpgen_docked" then
      return W.set_docked(game.get_player(e.player_index), el.state)
    end
    if el and el.valid and el.name == "bpgen_web" then  -- (the web window instead, or back)
      local player = game.get_player(e.player_index)
      web.set_wanted(player, el.state)
      return W.rebuild_any(player)
    end
    if el and el.valid and el.tags and el.tags.bpgen_make_row then
      local where = el.parent["bpgen_where"]
      where.caption = T(el.state and "made-here" or "on-belt")
      W.note_color(where, el.state)
      return
    end
    local target = el and el.valid and el.tags and el.tags.bpgen_toggle
    if not target then return end
    local player = game.get_player(e.player_index)
    local frame = player.gui.screen[NAME]
    find(frame, target).visible = el.state
    if target == "bpgen_make" or target == "bpgen_also" then W.tree(player, frame) end
  end,
  [defines.events.on_gui_selection_state_changed] = function(e)
    W.changed(e)
    local el = e.element
    if el and el.valid and el.name == "bpgen_size" and el.tags.fstd_scale_pick == NAME and stdwin.set_scale then
      -- (the window's Size: drawn at it, the preview fitted to the room it leaves; the library hears it after us)
      local c = ui.theme.themes.SCALE_CHOICES[el.selected_index - 1]
      local player = game.get_player(e.player_index)
      stdwin.set_scale(player, NAME, c and c / 100 or nil)
      return W.reflow(player)
    end
    if el and el.valid and el.tags and el.tags.bpgen_section then
      -- (another print of the book in the preview)
      local player = game.get_player(e.player_index)
      local r = results[player.index]
      local frame = player.gui.screen[NAME]
      if r and frame then
        local center, w, h, where = W.build_preview(player, r.blueprint, nil, el.items[el.selected_index])
        if center then
          local cam = find(frame, "bpgen_cam")
          r.center, r.zoom, r.where, r.w, r.h = center, W.fit(player, w, h), where, w, h
          cam.position, cam.zoom = center, r.zoom
        end
      end
      return
    end
  end,
  -- (the room the window has changed: the game's UI scale, the screen, the Window size setting)
  [defines.events.on_player_display_scale_changed] = function(e) W.reflow(game.get_player(e.player_index)) end,
  [defines.events.on_player_display_resolution_changed] = function(e) W.reflow(game.get_player(e.player_index)) end,
  [defines.events.on_runtime_mod_setting_changed] = function(e)
    if e.setting == "fstd-window-scale" and e.player_index then W.reflow(game.get_player(e.player_index)) end
    if e.setting == "bpgen-web-window" and e.player_index then  -- (the web window or the Lua one: built again so)
      local player = game.get_player(e.player_index)
      if player.gui.screen[NAME] then W.rebuild_any(player)
      elseif not web.wanted(player) then web.close(player, true) end  -- (a shell kept hidden for it: closed)
    end
  end,
  [defines.events.on_lua_shortcut] = function(e)
    if e.prototype_name == "bpgen-window" then toggle_window(e) end
  end,
  ["bpgen-window"] = toggle_window,
}

-- (fse-std 0.8+: the theme window's Window size, or a script's set_window_scale)
if stdwin and stdwin.on_scale then
  W.handlers["fstd-scale-changed"] = function(e) W.reflow(game.get_player(e.player_index)) end
end
