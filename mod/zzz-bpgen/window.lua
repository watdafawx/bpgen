-- The bpgen window (with the fse loader): pick a recipe with the game's own recipe picker, plan it in game
-- (bpgen.ingame on a worker thread), and see the blueprint as the game draws it: it is built for real on a hidden
-- preview surface ("bpgen-preview", its own force, lab tiles) and shown through a camera. Then put it in your hand
-- (the game's own placement preview) or copy its string.
--
-- Opened by Ctrl+Shift+B over a machine (filled in from it, planned at once), the bpgen shortcut, Ctrl+Alt+B, or the
-- fse hub. Results live in this Lua state only (not saved); the preview surface is rebuilt on demand.
--
-- This file is the entry: setup, open and rebuild, and the public names control.lua, web.lua and the tests call. The
-- window's parts are window_*.lua, which share the table window_core returns (W).

local W = require("window_core")
local web, dnd, place, sandbox, ctx, NAME, T = W.web, W.dnd, W.place, W.sandbox, W.ctx, W.NAME, W.T
local adopt, docked, find, forms, histories, inside = W.adopt, W.docked, W.find, W.forms, W.histories, W.inside
local placed, results, say, unmark = W.placed, W.results, W.say, W.unmark

require("window_size")
require("window_camera")
require("window_unlock")
require("window_preview")
require("window_state")
require("window_tools")
require("window_forms")
require("window_chrome")
require("window_request")
require("window_result")
require("window_answers")
require("window_drawers")
require("window_actions")
require("window_click")
require("window_events")

function W.setup(c) for k, v in pairs(c) do ctx[k] = v end end

--- the kept settings put back, and a prefill made of what open was asked for over what was kept
local function with_kept(frame, prefill, kept)
  W.restore_form(frame, kept)
  local was = prefill
  prefill = setmetatable({}, { __index = was })
  prefill.mode = was.mode or kept.mode
  if was.products == nil then prefill.products = kept.products end
  if was.lines == nil then prefill.lines = kept.lines end
  local v = kept.values or {}
  if was.recipe == nil and v.bpgen_recipe and v.bpgen_recipe.e then
    prefill.recipe = v.bpgen_recipe.e
    local m = v.bpgen_machine and v.bpgen_machine.e
    if m then prefill.machine, prefill.machine_quality = m.name, m.quality end
  end
  if prefill.recipe and not prototypes.recipe[prefill.recipe] then prefill.recipe = nil end
  if prefill.machine and not prototypes.entity[prefill.machine] then prefill.machine = nil end
  return prefill
end

--- then filled in (a hovered machine; tests: a mode, mall products, science per minute, options shown)
local function apply_prefill(player, frame, prefill)
  W.set_mode(frame, prefill.mode or "line")
  for _, r in ipairs(prefill.products or {}) do
    local t = find(frame, "bpgen_products")
    t.children[#t.children].elem_value = r
    t.add({ type = "choose-elem-button", elem_type = "recipe", tags = { bpgen_product = true },
      elem_filters = { { filter = "hidden", invert = true } } })
  end
  if prefill.lines and #prefill.lines > 0 then
    local t = find(frame, "bpgen_lines")
    t.clear()
    for _, r in ipairs(prefill.lines) do W.line_slot(t, r) end
    W.line_slot(t)
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
    W.set_machine_filter(frame, prefill.recipe)
    local m = prefill.machine or W.best_machine(player, prefill.recipe)
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
end

function W.open(player, prefill)
  prefill = prefill or {}
  -- (the web window: this Lua window, hidden, is its model; the shell stays open when it was)
  local as_web = web.use(player)
  W.close(player, as_web)
  web.set_model(player, as_web)
  local frame, holder = W.create_frame(player, as_web)
  local out = docked(player)
  W.build_tabs(frame, holder, out)
  W.build_top(holder, player)
  local body, outer, left = W.build_body(holder)
  W.build_left(left, prefill)
  W.show_patches(frame, player)
  W.build_foot(outer)
  local info, right, cam = W.build_preview_pane(player, body, outer)
  local under, vrail = W.build_tools(frame, right, out)
  W.build_test_drawer(player, frame, info, out)
  W.build_dock(frame, under, vrail, out)
  W.build_result(info)
  W.finish_size(player, frame, body, right)

  W.fit_panel(left)
  if not as_web then adopt(player) end  -- (the game's styles it was built with: each player's theme)
  if dnd then  -- (History's rows dropped on the preview or the Recipe slot)
    dnd.droppable(cam, "bpgen_history")
    dnd.droppable(find(frame, "bpgen_recipe"), "bpgen_history")
  end
  W.badges_of(frame)

  -- as the player left it (prefill.fresh: as new)
  local kept = not prefill.fresh and forms[player.index] or nil
  -- (the last plan back in the preview, unless a recipe comes with it: that one gets planned now)
  local show_last = kept and not prefill.rebuilt and prefill.recipe == nil
  if kept then prefill = with_kept(frame, prefill, kept) end
  apply_prefill(player, frame, prefill)
  if as_web and not web.attach(player, frame, not prefill.rebuilt) then  -- (the shell didn't open: the Lua window)
    return W.open(player, prefill)
  end
  W.restrict(player)
  if show_last then
    local r = results[player.index]
    if r and r.blueprint and r.res then
      W.on_result(player, r.res, true)  -- (the preview as it was left: not built again)
      if kept.stale then W.stale(player, true) end
    end
  end
  W.keep_form(player)
  -- (Esc and E close it as the game's windows: the player's opened GUI, unless another one is open)
  if not as_web and frame.valid and player.opened_gui_type == defines.gui_type.none then player.opened = frame end
  return frame
end

--- "Docked" on or off: the window built again that way
function W.set_docked(player, on)
  inside[player.index] = (not on) or nil
  W.rebuild(player)
end

--- the window built again (another layout, its first size), its mode, recipe and machine and the last plan kept
function W.rebuild(player)
  local frame = player.gui.screen[NAME]
  if not frame then return end
  local mode = W.mode_of(frame)
  local recipe, machine = W.value(frame, "bpgen_recipe"), W.value(frame, "bpgen_machine")
  W.open(player, { mode = mode, recipe = recipe, machine = machine and machine.name,
    machine_quality = machine and machine.quality, lines = W.more_lines(frame), rebuilt = true })
  local r = results[player.index]
  if r and r.blueprint and r.res then W.on_result(player, r.res) end
end

-- (tests: a setting changed, as if by the player)
function W.touch(player_index, name)
  W.changed({ element = find(game.get_player(player_index).gui.screen[NAME], name), player_index = player_index })
end

-- (tests: a drop-down's item picked by the player, by the element's name (in the window or a frame stuck to it))
function W.pick(player_index, name, index)
  local el = find(game.get_player(player_index).gui.screen[NAME], name)
  if not el then return false end
  el.selected_index = index
  W.handlers[defines.events.on_gui_selection_state_changed]({ element = el, player_index = player_index })
  return true
end

--- the window built again as it should be now (the web page or the Lua window: the setting, the shell there or not),
--- its mode, recipe and last plan kept
function W.rebuild_any(player)
  if player.gui.screen[NAME] then return W.rebuild(player) end
  web.close(player)
  web.set_model(player, false)
end

-- the sandbox's view of this file
sandbox.setup({
  shown = function(player, on) W.window_shown(player, on) end,
  card = W.sandbox_card,
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

web.setup({ NAME = NAME, find = find, on_click = W.on_click, handlers = W.handlers, results = results,
  test_series = W.test_series, histories = histories, ghosts_left = W.ghosts_left, close = W.close,
  set_only_unlocked = function(player, on) W.set_only_unlocked(player, on) end,
  history_use = W.history_use, view_resized = function(player) W.view_resized(player) end,
  rebuild_any = function(player) W.rebuild_any(player) end })
-- (its events, after the window's own)
for ev, fn in pairs(web.handlers) do
  local own = W.handlers[ev]
  W.handlers[ev] = own and function(e) own(e) fn(e) end or fn
end

local PUBLIC = { "NAME", "TOOLS", "add_patch", "bench_calibrate", "bench_last", "bench_test", "best_machine", "calibrate", "click", "close", "extend_request", "forget_unlocked", "handlers", "history_use", "last_absolute", "measuring", "more_lines", "move", "on_api", "on_connected", "on_cursor", "on_hand", "on_progress", "on_result", "open", "pick", "place_absolute", "place_plan", "placing", "plan", "plan_size", "preview", "rebuild", "rebuild_any", "recipe_cats", "recipe_filters", "refit", "reflow", "restrict", "set_docked", "set_only_unlocked", "setup", "spot", "steps", "test_hover_camera", "test_hover_grip", "test_resize", "tool_card", "tool_done", "touch", "unplace", "upgrade_lane", "view_resized", "warm" }
local M = {}
for _, name in ipairs(PUBLIC) do M[name] = W[name] end
return M
