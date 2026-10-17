-- The window's frame and what's stuck to it: the title bar, the mode tabs (a rail out of the left edge, or a row), the
-- preview with its tools (a rail out of the right edge, or a row), the test run's drawer, the dock of the blueprint's
-- actions below, and the area for the plan's result. window.lua's open puts them together.

local W = require("window_core")
local ui, stdwin, safe, CAM_MIN_W, DOCK_H, DOCK_W = W.ui, W.stdwin, W.safe, W.CAM_MIN_W, W.DOCK_H, W.DOCK_W
local GROUP_START, LEFT_W, MODES, NAME, RAIL_H = W.GROUP_START, W.LEFT_W, W.MODES, W.NAME, W.RAIL_H
local RAIL_W, T, TEST_H, TEST_W, TOOLS_H, find = W.RAIL_W, W.T, W.TEST_H, W.TEST_W, W.TOOLS_H, W.find
local find_in, only_unlocked = W.find_in, W.only_unlocked

--- the plan next to the base moved (◀▲▼▶, Shift: 5), turned, put elsewhere (Redo, Pick spot): on the preview's rail
--- when docked (`wide`: its buttons' size), else in the row of actions (`small`). Shown by on_result
local function moves(parent, wide, small)
  local box = parent.add({ type = "flow", name = "bpgen_moves", direction = wide and "vertical" or "horizontal",
    visible = false })
  if wide then
    box.style.vertical_spacing = 4
    box.style.top_margin = 12
    ui.add(box, { type = "label", caption = T("move-it") }, "caption")
  end
  local arrows = wide and box.add({ type = "flow", direction = "horizontal" }) or box
  if wide then arrows.style.horizontal_spacing = 2 end
  local aw = wide and { width = math.floor((RAIL_W - 14) / 4), height = 30, padding = 0 } or nil
  for _, a in ipairs({ { "left", "◀", -1, 0 }, { "up", "▲", 0, -1 }, { "down", "▼", 0, 1 }, { "right", "▶", 1, 0 } }) do
    local b = arrows.add({ type = "button", name = "bpgen_nudge_" .. a[1], caption = a[2], style = "tool_button",
      tags = { bpgen = "nudge", dx = a[3], dy = a[4] }, tooltip = T("nudge-tip", T("dir-" .. a[1])) })
    if aw then ui.style(b, aw) end
  end
  local row = wide and box.add({ type = "flow", direction = "horizontal" }) or box
  if wide then row.style.horizontal_spacing = 2 end
  local half = wide and { width = math.floor((RAIL_W - 10) / 2), height = 30, padding = 0 } or nil
  local rot = row.add({ type = "button", name = "bpgen_base_rotate", caption = "⟳", style = "tool_button",
    tags = { bpgen = "base_rotate" }, tooltip = T("rotate-tip") })
  if half then ui.style(rot, half) end
  ui.add(row, { type = "button", name = "bpgen_base_redo", caption = T("redo"), tags = { bpgen = "base_redo" },
    tooltip = T("redo-tip") }, wide and "rtab" or "button", half or small)
  ui.add(box, { type = "button", name = "bpgen_base_spot", caption = T("pick-spot"), tags = { bpgen = "base_spot" },
    tooltip = T("pick-spot-tip") }, wide and "rtab" or "button", wide or small)
end

--- the window itself: fse-std's (resized by its corner grip) or a plain frame; -> frame, holder (what the rest goes in)
local function create_frame(player, as_web)
  local screen = player.gui.screen
  local frame, holder
  if stdwin and not as_web then
    -- (made at the size that fits the room it has at its scale: never drawn smaller for a size it won't keep)
    local max_w, max_h, min_h = W.room(player)
    local ww, wh = W.wanted_size(player)
    local cw, ch = math.max(CAM_MIN_W, math.floor(math.min(ww, max_w))), math.max(min_h, math.floor(math.min(wh, max_h)))
    storage.fstd_windows = storage.fstd_windows or {}
    storage.fstd_windows[player.index] = storage.fstd_windows[player.index] or {}
    local saved = storage.fstd_windows[player.index][NAME] or {}
    storage.fstd_windows[player.index][NAME] = saved
    saved.width, saved.height = cw + LEFT_W, ch + W.frame_h(player)
    -- (a new window: placed by us, not centred on the whole screen by the library)
    if not saved.location then saved.location = W.place_new(player, cw, ch) end
    frame, holder = stdwin.create(player, { name = NAME, title = "bpgen", width = cw + LEFT_W, height = ch + W.frame_h(player),
      min_width = CAM_MIN_W + LEFT_W, min_height = min_h + W.frame_h(player), themed = ui.themed, settings = ui.menus,
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
  return frame, holder
end

--- the modes: a rail of tabs out of the window's left edge (fse-std's layouts), else a row of them in the window
local function build_tabs(frame, holder, out)
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
end

local function build_top(holder, player)
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
end

--- the body: the left panel (in its frame `outer`, with `left` scrolling) and the room for the preview; -> body, outer, left
local function build_body(holder)
  local body = holder.add({ type = "flow", direction = "horizontal" })
  local outer = body.add({ type = "frame", style = "inside_shallow_frame", direction = "vertical" })
  outer.style.width = 320
  outer.style.vertically_stretchable = true
  local left = outer.add({ type = "scroll-pane", horizontal_scroll_policy = "never" })
  left.style.padding = 8
  left.style.vertically_stretchable = true
  return body, outer, left
end

--- the result's scroll area under Plan and the preview's camera; -> info, right, cam
local function build_preview_pane(player, body, outer)
  -- (what the plan comes to and the test run: under Plan in the left panel, in a scroll area of their own (up to a
  -- share of the window's height), the preview taking the whole right side)
  local info = outer.add({ type = "scroll-pane", name = "bpgen_info", horizontal_scroll_policy = "never" })
  info.style.maximal_height = W.info_h(W.wanted_size(player) + W.frame_h(player))
  info.style.horizontally_stretchable = true
  info.style.padding = { 0, 8, 8, 8 }
  local right = body.add({ type = "frame", style = "inside_deep_frame", direction = "vertical" })
  local cam = right.add({ type = "camera", name = "bpgen_cam", position = { 0, 0 }, surface_index = player.surface.index, zoom = 1,
    raise_hover_events = true, tooltip = safe.has("std") and T("cam-tip") or nil })
  local CAM_W, CAM_H = W.wanted_size(player)
  local max_w, max_h, min_h = W.room(player)
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
  return info, right, cam
end

--- the preview's tools and the Test run button; -> the row under the preview (when not docked), the rail (docked)
local function build_tools(frame, right, out)
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
  if vrail then moves(vrail, wide, nil) end
  return under, vrail
end

local function build_test_drawer(player, frame, info, out)
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
    tb = W.section(info, nil, nil, "bpgen_test_box")
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
end

local function build_dock(frame, under, vrail, out)
  local tools
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
  ui.add(tools, { type = "button", name = "bpgen_place", caption = T("place-it"), tags = { bpgen = "place" },
    enabled = false, visible = false, tooltip = T("place-it-tip") }, "button", small)
  ui.add(tools, { type = "button", name = "bpgen_place_base", caption = T("place-base"), tags = { bpgen = "place_base" },
    enabled = false, tooltip = T("place-base-tip") }, "button", small)
  ui.add(tools, { type = "button", name = "bpgen_sandbox", caption = T("sandbox"), tags = { bpgen = "sandbox" },
    enabled = false, tooltip = T("sandbox-tip") }, "button", small)
  if not vrail then moves(tools, nil, small) end
  ui.add(tools, { type = "button", caption = T("undo"), tags = { bpgen = "unplace" }, tooltip = T("undo-tip") }, "button",
    small)
  ui.add(tools, { type = "button", caption = T("history"), tags = { bpgen = "history" }, tooltip = T("history-tip") },
    "button", small)
end

local function build_result(info)
  -- the plan's result: its numbers as tiles (three a row), badges for the rest, its notes, the book's prints, the
  -- details folded
  local result = W.section(info, T("result"), nil, "bpgen_result")
  result.visible = false
  local tiles = result.add({ type = "table", name = "bpgen_tiles", column_count = 3, visible = false })
  tiles.style.horizontal_spacing, tiles.style.vertical_spacing = 4, 4
  result.add({ type = "flow", name = "bpgen_badges", direction = "vertical" }).style.vertical_spacing = 4
  ui.add(result, { type = "label", name = "bpgen_notes" }, "dim", { single_line = false, maximal_width = 266 })
  -- (a line with ingredients made here: its other layouts, the plan's own first; then the book's prints)
  result.add({ type = "drop-down", name = "bpgen_layout", items = {}, visible = false, tags = { bpgen_layout = true },
    tooltip = T("layout-tip") }).style.horizontally_stretchable = true
  result.add({ type = "drop-down", name = "bpgen_section", items = {}, visible = false, tags = { bpgen_section = true },
    tooltip = T("section-tip") })
  local dets = ui.fold(result, T("details"), false)
  ui.add(dets, { type = "label", name = "bpgen_stats" }, "text", { single_line = false, maximal_width = 256 })
  -- (after a plan: can your save build it, and what modules would do; or the history list)
  local extra = info.add({ type = "flow", name = "bpgen_extra", direction = "vertical" })
  extra.style.maximal_width = 290
end

--- the library keeps the window's size and place (the preview stretches into what the window leaves it); a plain frame is centred
local function finish_size(player, frame, body, right)
  if stdwin then
    -- (the library keeps the window's size and place: the preview stretches into what the window leaves it)
    find(frame, "bpgen_grip").visible = false
    for _, n in ipairs({ "bpgen_cam", "bpgen_empty" }) do
      local el = find(frame, n)
      el.style.minimal_width, el.style.minimal_height = CAM_MIN_W, select(3, W.room(player))
      el.style.horizontally_stretchable, el.style.vertically_stretchable = true, true
    end
    body.style.vertically_stretchable, body.style.horizontally_stretchable = true, true
    -- (a flow ignores its stretch here: a natural width past the screen's makes it fill the window, which caps it)
    body.style.natural_width = math.floor(player.display_resolution.width / player.display_scale)
    right.style.vertically_stretchable, right.style.horizontally_stretchable = true, true
    local ww, wh = W.wanted_size(player)
    W.apply_size(player, frame, ww, wh, true)
  else
    frame.force_auto_center()
  end
end

-- (what the other parts call)
W.build_body = build_body
W.build_dock = build_dock
W.build_preview_pane = build_preview_pane
W.build_result = build_result
W.build_tabs = build_tabs
W.build_test_drawer = build_test_drawer
W.build_tools = build_tools
W.build_top = build_top
W.create_frame = create_frame
W.finish_size = finish_size
