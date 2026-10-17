-- The window's buttons: every element tagged tags.bpgen = "<action>" runs ACTIONS[action]. (Tests press them with
-- M.click(player_index, tags).)

local W = require("window_core")
local ui, web, stdwin, safe, hand, sandbox, ctx = W.ui, W.web, W.stdwin, W.safe, W.hand, W.sandbox, W.ctx
local BUS_STEP, NAME, T, bus_shift, docked, find = W.BUS_STEP, W.NAME, W.T, W.bus_shift, W.docked, W.find
local histories, only_unlocked, patch_areas, results = W.histories, W.only_unlocked, W.patch_areas, W.results
local say, status, to_sandbox, tried, turns, unmark = W.say, W.status, W.to_sandbox, W.tried, W.turns, W.unmark

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

--- a tool of bpgen's put in the hand (its card up, the window out of the way) and what to do with it said
local function take_tool(player, frame, item, hint)
  if player.clear_cursor() then
    player.cursor_stack.set_stack({ name = item })
    W.tool_card(player, item)
    status(frame, T(hint))
  end
end

local ACTIONS = {}  -- the button's tag (tags.bpgen) -> function(player, el, e, frame, r): r is the player's last plan

ACTIONS.close = function(player, el, e, frame, r)
  W.close(player)
end

ACTIONS.tool_done = function(player, el, e, frame, r)
  player.clear_cursor()
  W.tool_done(player)
end

ACTIONS.theme = function(player, el, e, frame, r)
  local menu = player.gui.screen[NAME .. "__menu"]  -- (the settings closed: the theme window opens instead)
  if menu then menu.destroy() end
  remote.call("fse-std", "open_theme", player.index)
end

ACTIONS.reset_size = function(player, el, e, frame, r)
  if stdwin and stdwin.reset_place then
    stdwin.reset_place(player, NAME)  -- (its place and size; its Size stays)
  elseif storage.fstd_windows and storage.fstd_windows[player.index] then
    storage.fstd_windows[player.index][NAME] = nil
  end
  if storage.bpgen_cam then storage.bpgen_cam[player.index] = nil end
  W.rebuild(player)
end

ACTIONS.plan = function(player, el, e, frame, r)
  bus_shift[player.index] = nil
  W.plan(player)
end

ACTIONS.nudge = function(player, el, e, frame, r)
  if not r then return end
  if r.res.mode == "busdesign" then  -- (its head, sideways: planned again)
    bus_shift[player.index] = (bus_shift[player.index] or 0) + (el.tags.dx + el.tags.dy) * BUS_STEP
    status(frame, T("moving-bus-head"))
    W.plan(player)
  elseif r.res.absolute then
    -- (exactly that far from where it is: no looking for another spot, the belts and power connected again)
    local n = e.shift and 5 or 1
    local b = r.res.absolute.box  -- (the snapshot around it; where it goes is bpgen's last spot, shifted)
    local ok, why = ctx.place_base(player, { bus = find(frame, "bpgen_ext_bus").state and "auto" or "off",
      seed = { x = b[1] + b[3] / 2, y = b[2] + b[4] / 2 },
      turn = turns[player.index], shift = { el.tags.dx * n, el.tags.dy * n }, again = true })
    status(frame, ok and T("moving-it") or T("cant", tostring(why)))
  end
end

-- a bpgen button in another mod's window (compat.lua)
ACTIONS.recipe = function(player, el, e, frame, r)
  W.open(player, { recipe = el.tags.bpgen_recipe })
  W.plan(player)
end

ACTIONS.test = function(player, el, e, frame, r)
  if not (r and r.res.test and r.where) then return end
  W.click_test(player, frame, r)
end

-- (the drawer closed; a run goes on, its button says how far)
ACTIONS.test_close = function(player, el, e, frame, r)
  W.test_shown(player, frame, false)
end

ACTIONS.test_speed = function(player, el, e, frame, r)
  if game.is_multiplayer() then return end
  W.click_test_speed(frame)
end

ACTIONS.cursor = function(player, el, e, frame, r)
  if not r then return end
  ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
  W.to_cursor(player)
end

ACTIONS.copy = function(player, el, e, frame, r)
  if not (r and safe.has("std")) then return end
  native.call("std", "clipboard_set", r.blueprint)
  ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
  say(player, T("bp-copied"))
end

ACTIONS.short = function(player, el, e, frame, r)
  W.extra(frame).clear()
  W.extra(frame).add({ type = "label", caption = "..." })
  ctx.api(player, "shortages", nil, "short")
end

ACTIONS.plan_short = function(player, el, e, frame, r)
  W.set_mode(frame, "line")
  find(frame, "bpgen_recipe").elem_value = el.tags.bpgen_recipe
  W.set_machine_filter(frame, el.tags.bpgen_recipe)
  local m = W.best_machine(player, el.tags.bpgen_recipe)
  find(frame, "bpgen_machine").elem_value = m and { name = m, quality = "normal" } or nil
  find(frame, "bpgen_rate").text = tostring(math.ceil(el.tags.rate))
  W.plan(player)
end

ACTIONS.move = function(player, el, e, frame, r)
  take_tool(player, frame, "bpgen-move", "move-tool-hint")
end

ACTIONS.refit = function(player, el, e, frame, r)
  take_tool(player, frame, "bpgen-refit", "refit-tool-hint")
end

ACTIONS.snapshot = function(player, el, e, frame, r)
  take_tool(player, frame, "bpgen-snapshot", "snapshot-tool-hint")
end

ACTIONS.patches = function(player, el, e, frame, r)
  take_tool(player, frame, "bpgen-patches", "patch-tool-hint")
end

ACTIONS.base_spot = function(player, el, e, frame, r)
  take_tool(player, frame, "bpgen-spot", "click-where")
end

ACTIONS.patches_clear = function(player, el, e, frame, r)
  patch_areas[player.index] = nil
  W.show_patches(frame, player)
end

ACTIONS.place = function(player, el, e, frame, r)
  if not (r and r.res.absolute) then return end
  W.place_absolute(player)
end

ACTIONS.sandbox = function(player, el, e, frame, r)
  if not r then return end
  if not r.res.absolute and frame and find(frame, "bpgen_place_base").enabled and not sandbox.cant(player) then
    -- (a line, a mall, a base: put by the base first, as Place by my base; the lab follows when it's there)
    local sec = find(frame, "bpgen_section")
    local ok, why = ctx.place_base(player, { bus = find(frame, "bpgen_ext_bus").state and "auto" or "off",
      seed = { x = player.position.x, y = player.position.y },
      section = sec.visible and sec.items[sec.selected_index] or nil, layout = W.layout_pick(frame) })
    to_sandbox[player.index] = ok or nil
    status(frame, ok and T("looking-around") or T("cant", tostring(why)))
  else
    W.try_sandbox(player)
  end
end

-- (the plan in the lab: a tile, five with Shift, or a quarter turn)
local function sandbox_nudge(turn)
  return function(player, el, e)
    local n = e.shift and 5 or 1
    local ok, why = sandbox.move(player.index, (el.tags.dx or 0) * n, (el.tags.dy or 0) * n, turn)
    if not ok then player.create_local_flying_text({ text = T(why), create_at_cursor = true }) end
  end
end
ACTIONS.sandbox_move = sandbox_nudge(false)
ACTIONS.sandbox_turn = sandbox_nudge(true)

ACTIONS.sandbox_place = function(player, el, e, frame, r)
  sandbox.place(player)
end

ACTIONS.sandbox_discard = function(player, el, e, frame, r)
  sandbox.discard(player)
end

ACTIONS.tab = function(player, el, e, frame, r)
  W.set_mode(frame, el.tags.mode)
end

ACTIONS.check_bus = function(player, el, e, frame, r)
  W.extra(frame).clear()
  local ok, why = ctx.bus_report(player, W.unlocked(player).belts)
  status(frame, ok and T("looking-at-bus") or T("cant", tostring(why)))
end

ACTIONS.bus_lane = function(player, el, e, frame, r)
  local ok, why = ctx.add_lane(player, el.tags.item)
  status(frame, ok and T("laying-lane") or T("cant", tostring(why)))
end

ACTIONS.bus_make = function(player, el, e, frame, r)
  -- (Extend: that item at that rate, made next to the bus from what it carries)
  W.set_mode(frame, "extend")
  find(frame, "bpgen_ext_item").elem_value = el.tags.item
  find(frame, "bpgen_ext_rate").text = tostring(math.ceil(el.tags.rate))
  W.plan(player)
end

ACTIONS.upgrade_lane = function(player, el, e, frame, r)
  if not (r and r.res.absolute) then return end
  W.upgrade_lane(player, r.res.absolute.upgrades[el.tags.i])
end

-- (both around where it is now: Rotate turns it there, Redo looks past every spot it was shown on)
local function base_again(rotate)
  return function(player, el, e, frame, r)
    if not (r and r.res.absolute) then return end
    local b = r.res.absolute.box
    local seed = { x = b[1] + b[3] / 2, y = b[2] + b[4] / 2 }
    local avoid
    if rotate then
      turns[player.index] = ((turns[player.index] or 0) + 1) % 4
    else
      tried[player.index] = tried[player.index] or {}
      table.insert(tried[player.index], { b[1], b[2], b[3], b[4] })
      avoid = tried[player.index]
    end
    local ok, why = ctx.place_base(player, { bus = find(frame, "bpgen_ext_bus").state and "auto" or "off", seed = seed,
      turn = turns[player.index], avoid = avoid, again = true })
    status(frame, ok and T(rotate and "rotating" or "looking-another-spot") or T("cant", tostring(why)))
  end
end
ACTIONS.base_rotate = base_again(true)
ACTIONS.base_redo = base_again(false)

ACTIONS.place_base = function(player, el, e, frame, r)
  if not r then return end
  -- (bpgen picks the spot beside the base and shows it in the preview: the arrows, Rotate, Redo and Pick spot
  -- adjust it there, Place it puts the ghosts down)
  local sec = find(frame, "bpgen_section")  -- (a starter base: the print picked under the preview)
  turns[player.index], tried[player.index] = nil, nil
  local ok, why = ctx.place_base(player, { bus = find(frame, "bpgen_ext_bus").state and "auto" or "off",
    seed = { x = player.position.x, y = player.position.y },
    section = sec.visible and sec.items[sec.selected_index] or nil, layout = W.layout_pick(frame) })
  status(frame, ok and T("looking-around") or T("cant", tostring(why)))
end

-- (the card after a paste: hand.lua)
ACTIONS.paste_reroute = function(player, el, e, frame, r)
  local ok, why = hand.reroute(player)
  say(player, ok and T("paste-rerouting") or why)
end

ACTIONS.paste_move = function(player, el, e, frame, r)
  local ok, why = hand.move(player)
  if ok then
    ui.toast(player, T((why or 0) > 0 and "paste-moving-built" or "paste-moving", why or 0))
  else
    say(player, why)
  end
end

ACTIONS.paste_close = function(player, el, e, frame, r)
  hand.close_card(player)
end

ACTIONS.unplace = function(player, el, e, frame, r)
  -- (a press of the button asks first when there are ghosts to remove; a script's press (tests) doesn't)
  local n = W.ghosts_left(player)
  if n > 0 and not e.scripted then
    ui.confirm(player, "bpgen_unplace", { title = T("undo-title"),
      text = T("undo-text", n, #(unmark[player.index] or {}) > 0 and T("undo-text-keeps") or ""),
      yes = T("undo-yes"), no = T("undo-no") })
  else
    W.unplace(player)
  end
end

ACTIONS.history = function(player, el, e, frame, r)
  if not (docked(player) and stdwin) or W.history_drawer(player, frame) then ctx.api(player, "history", {}, "history") end
end

ACTIONS.history_take = function(player, el, e, frame, r)
  local h = (histories[player.index] or {})[el.tags.i]
  if h then
    results[player.index] = results[player.index] or { res = {} }
    results[player.index].blueprint = h.blueprint
    W.to_cursor(player)
  end
end

ACTIONS.idea = function(player, el, e, frame, r)
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
  W.badges_of(frame)
  W.plan(player)
end

ACTIONS.size = function(player, el, e, frame, r)
  W.click_size(player, frame, r)
end

ACTIONS.pan = function(player, el, e, frame, r)
  if not (r and r.zoom) then return end
  W.click_pan(player, frame, el)
end

ACTIONS.zoom = function(player, el, e, frame, r)
  if not (r and r.zoom) then return end
  W.click_zoom(frame, el)
end

ACTIONS.fit = function(player, el, e, frame, r)
  if not (r and r.zoom) then return end
  W.click_fit(frame, r)
end

local function on_click(e)
  local el = e.element
  if not (el and el.valid) then return end
  if el.tags and el.tags.fstd_settings == NAME then return settings(game.get_player(e.player_index)) end
  local action = el.tags and el.tags.bpgen
  local handler = action and ACTIONS[action]
  if not handler then return end
  local player = game.get_player(e.player_index)
  handler(player, el, e, player.gui.screen[NAME], results[player.index])
end
W.on_click = on_click

-- (tests: a button press, as its tags)
function W.click(player_index, tags, as_button)
  on_click({ element = { valid = true, tags = tags }, player_index = player_index, scripted = not as_button })
end
