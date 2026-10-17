-- The drawers beside the window: the test run (its bar, chart and fast-forward) and History (a list to take, drag onto
-- the preview or the Recipe slot). Closing the window stops a test run. The test run's measuring runs from bench.lua.

local W = require("window_core")
local ui, web, stdwin, dnd, bench, safe, ctx = W.ui, W.web, W.stdwin, W.dnd, W.bench, W.safe, W.ctx
local NAME, PREVIEW, T, TEST_W, TOOLS_H, adopt = W.NAME, W.PREVIEW, W.T, W.TEST_W, W.TOOLS_H, W.adopt
local find, histories, join, sig, status = W.find, W.histories, W.join, W.sig, W.status

--- the test run's drawer (docked: out of the window's right edge) or its section (inside) shown or hidden; History's
--- drawer makes way for it
local function test_shown(player, frame, on)
  local box = find(frame, "bpgen_test_box")
  if box then box.visible = on end
  local drawer = player.gui.screen[NAME .. "__test"]
  if not drawer then return end
  local tags = drawer.tags
  tags.bpgen_open = on or nil
  drawer.tags = tags
  drawer.visible = on and frame.visible
  if on then
    local hist = player.gui.screen[NAME .. "__right"]
    if hist then hist.destroy() end
    W.place_drawer(player, frame, drawer)
    drawer.bring_to_front()
  end
end

--- the test run's fast-forward off: the game's speed as it was (the ×5 / ×10 button back at ×1)
local function test_speed_off(frame)
  if storage.bpgen_speed_was then
    game.speed = storage.bpgen_speed_was
    storage.bpgen_speed_was = nil
  end
  local b = frame and find(frame, "bpgen_test_speed")
  if b then
    b.caption, b.visible = "×1", false
    local tags = b.tags
    tags.speed = 1
    b.tags = tags
  end
end

--- a test run going on (anyone's)
local function testing()
  for _, run in pairs(storage.bpgen_bench and storage.bpgen_bench.runs or {}) do
    if run.kind == "test" then return true end
  end
  return false
end

--- the player's test run stopped (its window closed, the player gone), and the fast-forward with it once no test
--- runs any more
local function test_stop(player_index)
  local run = bench.find("test", player_index)
  if run then bench.stop(run.id) end
  if not testing() then test_speed_off(nil) end
end

--- the window closed, and what's stuck to it (its test run stops: nothing would show it); the web window's shell too,
--- unless `keep_shell` (it opens again at once, as the web window: the page stays)
local function close(player, keep_shell)
  test_stop(player.index)
  for _, c in pairs(ui.attached(player, NAME)) do c.destroy() end
  if player.gui.screen[NAME] then player.gui.screen[NAME].destroy() end
  if not keep_shell then web.close(player) end
end
W.close = close
-- (closed by the library, its X: what's stuck to it went with it, the test run stops as with close)
if stdwin and stdwin.on_close then
  stdwin.on_close(function(player, name) if name == NAME then test_stop(player.index) end end)
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
  for k, n in pairs(count) do machines[#machines + 1] = n .. " " .. k:gsub("_", " ") end  -- (the game's status names)
  table.sort(machines)
  local head
  if run.products then  -- (a mall: nothing to rate, what has reached its chests)
    local got, waiting = bench.chest_contents(run), {}
    for _, p in ipairs(run.products) do if not got[p] then waiting[#waiting + 1] = sig(p) end end
    head = T("test-mall", T(final and "test-done" or "test-mall-prefix"), #run.products - #waiting, #run.products,
      math.floor(t / 60), #waiting > 0 and { "", "\n", T("test-waiting", table.concat(waiting, " ")) } or "")
  elseif t < run.warmup then
    head = T("test-warmup", math.floor(t / 60), math.floor(run.warmup / 60))
  else
    local got = (cases[1].rates or {})[run.output] or 0
    local pct = run.expected and run.expected > 0 and got / run.expected * 100 or 0
    head = T("test-measured", T(final and "test-done" or "test-measuring"), sig(run.output or ""),
      string.format("%.1f", got * 60), string.format("%.0f", pct), string.format("%.1f", (run.expected or 0) * 60),
      { "", " ", final and T("test-over", math.floor(seconds))
        or T("test-progress", math.floor(seconds), math.floor(run.measure / 60)) })
  end
  local errs = cases[1].errors or {}
  return join({ head, T("test-machines", table.concat(machines, ", ")), #errs > 0 and table.concat(errs, "; ") or nil },
    "\n"), starved
end

local test_series = safe.stored("test_series")  -- player index -> the rate measured each second (the chart)
--- the test run's section: started (no run), or where the run is (a bar red to green by the share of the expected
--- rate, a chart of the rate)
local function test_view(player, frame, run, final)
  local box = find(frame, "bpgen_test_box")
  if not box then return end
  if not run then test_shown(player, frame, true) end  -- (a run starting: its drawer opens; later it stays as left)
  local bar, holder = find(box, "bpgen_test_bar"), find(box, "bpgen_test_graph")
  if not run then
    test_series[player.index] = {}
    ui.set_progress(bar, 0, T("starting"))
    holder.clear()
    -- (the web window draws its own chart: a graph on a hidden window would draw over the game)
    local g = not web.model(player) and ui.graph(holder, { 0, 0 }, { width = TEST_W - 40, height = 56 })
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
    text = T("test-bar-mall", n, #run.products)
  elseif t < run.warmup then
    text = T("test-bar-warmup", math.floor(t / 60), math.floor(run.warmup / 60))
  else
    local _, cases = bench.rates(run)
    local got = ((cases[1] or {}).rates or {})[run.output] or 0
    rate = got * 60
    frac = run.expected and run.expected > 0 and got / run.expected or 0
    text = T("test-bar-rate", string.format("%.0f", rate), string.format("%.0f", frac * 100))
  end
  ui.set_progress(bar, math.min(1, frac), text)
  -- (the Test run button says how far it got: seen with the drawer closed)
  local button = find(frame, "bpgen_test")
  if button and not final then
    button.caption = run.products and T("test-live-mall", math.floor(frac * #run.products + 0.5), #run.products)
      or t < run.warmup and T("test-live-warmup") or T("test-live", string.format("%.0f", frac * 100))
    button.tooltip = { "", T("test-live-tip"), "\n", text }
  end
  if rate then
    local list = test_series[player.index] or {}
    list[#list + 1] = rate
    while #list > 60 do table.remove(list, 1) end
    test_series[player.index] = list
    if #list >= 2 then ui.set_graph(find(box, "bpgen_test_chart"), list) end
  end
  if final then W.glow(player, false) end
end

bench.on_progress = function(run)
  local player = game.get_player(run.player)
  local frame = player and player.gui.screen[NAME]
  if not frame then return end
  web.dirty(player)
  if run.kind == "test" then
    local text, starved = describe_test(run, false)
    find(frame, "bpgen_test_status").caption = text
    mark(player, starved)
    test_view(player, frame, run)
  else
    local left = math.ceil((run.warmup + run.measure - (game.tick - run.start)) / 60)
    status(frame, T("measuring-left", left))
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
  test_speed_off(frame)  -- (the window closed or not: the game back at its own speed)
  if not frame then return end
  web.dirty(player)
  local text, starved = describe_test(run, true)
  find(frame, "bpgen_test_status").caption = text
  mark(player, starved)
  find(frame, "bpgen_test").caption = T("test-run")
  find(frame, "bpgen_test").tooltip = T("test-run-tip")
  test_view(player, frame, run, true)
end

--- History as a drawer stuck to the window's right edge: opened, or closed when it was open (-> the drawer or nil)
local function history_drawer(player, frame)
  local d = player.gui.screen[NAME .. "__right"]
  if d then d.destroy() return nil end
  test_shown(player, frame, false)
  local _, h = stdwin.size(player, NAME)
  d = ui.attach(frame, "right", { size = { 300, math.max(200, (h or 500) - TOOLS_H - 110) }, along = 40 + TOOLS_H + 12,
    overlap = 8,
    role = "toast", direction = "vertical", padding = 8 })
  if not d then return nil end
  W.place_drawer(player, frame, d)
  ui.add(d, { type = "label", caption = T("history") }, "caption")
  ui.search(d, "bpgen_history", T("search-history"))
  local list = ui.add(d, { type = "scroll-pane", name = "bpgen_history_list", horizontal_scroll_policy = "never" },
    "scroll", { vertically_stretchable = true, horizontally_stretchable = true })
  ui.add(list, { type = "label", caption = "..." }, "dim")
  ui.add(d, { type = "label", caption = T("history-drag-hint") },
    "dim", { single_line = false, maximal_width = 276, font = "default-small" })
  return d
end

local function show_history(frame, items)
  local list = find(frame, "bpgen_history_list")
  if list then
    list.clear()
    if #items == 0 then
      ui.add(list, { type = "label", caption = T("history-empty") },
        "dim", { single_line = false, maximal_width = 260 })
    end
    for i, h in ipairs(items) do
      if i > 40 then break end
      local icon = h.recipe and prototypes.recipe[h.recipe] and ("[recipe=" .. h.recipe .. "] ") or ""
      local name = h.label or T("blueprint")
      local row = list.add({ type = "flow", direction = "horizontal",
        tags = { bpgen_hist = string.lower(h.label or "blueprint") } })
      row.style.vertical_align = "center"
      row.style.horizontally_stretchable = true
      local l = ui.add(row, { type = "label", caption = { "", icon, name }, tooltip = { "", name, "\n",
        T(h.book and "history-entities-book" or "history-entities", tostring(h.entities)), "\n", T("history-row-tip") } },
        "text", { maximal_width = 170 })
      if dnd then dnd.draggable(l, { kind = "bpgen_history", i = i, caption = { "", icon, name } }) end
      row.add({ type = "empty-widget" }).style.horizontally_stretchable = true
      ui.add(row, { type = "button", caption = T("to-cursor-short"), tags = { bpgen = "history_take", i = i } }, "button",
        { minimal_width = 0, height = 26, left_padding = 6, right_padding = 6 })
    end
    return
  end
  local box = W.extra(frame)
  box.clear()
  if #items == 0 then
    box.add({ type = "label", caption = T("history-empty") })
    return
  end
  local t = box.add({ type = "table", column_count = 3 })
  for i, h in ipairs(items) do
    if i > 25 then break end
    -- (an icon only for a recipe this game has: a removed mod's shows as raw text otherwise)
    local icon = h.recipe and prototypes.recipe[h.recipe] and ("[recipe=" .. h.recipe .. "] ") or ""
    local l = t.add({ type = "label", caption = { "", icon, h.label or T("blueprint") }, tooltip = h.label })
    l.style.maximal_width = 330
    t.add({ type = "label", caption = T(h.book and "history-entities-book" or "history-entities", tostring(h.entities)) })
    t.add({ type = "button", caption = T("to-cursor-short"), tags = { bpgen = "history_take", i = i } })
  end
end

ui.on_search("bpgen_history", function(player, text)
  local list = find(player.gui.screen[NAME], "bpgen_history_list")
  text = string.lower(text or "")
  for _, row in pairs(list and list.children or {}) do
    if row.tags.bpgen_hist then row.visible = text == "" or row.tags.bpgen_hist:find(text, 1, true) ~= nil end
  end
end)

--- History's row `i` dropped onto the Recipe slot ("recipe": planned again) or the preview (shown, ready to place)
local function history_use(player, i, target)
  local h = (histories[player.index] or {})[i]
  local frame = player.gui.screen[NAME]
  if not (h and frame) then return end
  if target == "recipe" and h.recipe and prototypes.recipe[h.recipe] then  -- (planned again)
    W.set_mode(frame, "line")
    find(frame, "bpgen_recipe").elem_value = h.recipe
    W.set_machine_filter(frame, h.recipe)
    local m = W.best_machine(player, h.recipe)
    find(frame, "bpgen_machine").elem_value = m and { name = m, quality = "normal" } or nil
    W.plan(player)
  else  -- (shown in the preview, ready to place)
    W.on_result(player, { blueprint = h.blueprint, mode = h.mode, label = h.label,
      notes = { T("from-history", h.label or T("a-blueprint")) } })
  end
end
W.history_use = history_use

if dnd then
  dnd.on_drop(function(player, payload, target)
    if type(payload) ~= "table" or payload.kind ~= "bpgen_history" then return end
    if not (target and target.valid) then return end
    history_use(player, payload.i, target.name == "bpgen_recipe" and "recipe" or "preview")
  end)
end

--- Test run pressed: starts a run and opens its drawer, opens the drawer of a run going on, or stops it
function W.click_test(player, frame, r)
  local run = bench.find("test", player.index)
  local drawer = player.gui.screen[NAME .. "__test"]
  if run and drawer and not drawer.tags.bpgen_open then  -- (a run going on, its drawer closed: opened again)
    test_shown(player, frame, true)
  elseif run then
    bench.stop(run.id)
    find(frame, "bpgen_test").caption = T("test-run")
    find(frame, "bpgen_test").tooltip = T("test-run-tip")
    find(frame, "bpgen_test_status").caption = T("test-stopped")
    test_speed_off(frame)
    W.glow(player, false)
  else
    W.shown[player.index] = nil  -- (the test run changes the preview: built again next time)
    bench.test(player.index, r.res.test, r.where.origin, r.where.area, PREVIEW)
    find(frame, "bpgen_test").caption = T("stop-test")
    find(frame, "bpgen_test_status").caption = T("starting-dots")
    find(frame, "bpgen_test_speed").visible = not game.is_multiplayer()
    test_view(player, frame)
    W.glow(player, true)
  end
end

--- the fast-forward button: ×1, ×5, ×10, round again (single player)
function W.click_test_speed(frame)
  local b = find(frame, "bpgen_test_speed")
  local tags = b.tags  -- (its step kept in its tags: ×1, ×5, ×10, round again)
  local next_ = ({ [1] = 5, [5] = 10, [10] = 1 })[tags.speed or 1] or 1
  storage.bpgen_speed_was = storage.bpgen_speed_was or game.speed
  game.speed = next_ == 1 and storage.bpgen_speed_was or next_
  if next_ == 1 then storage.bpgen_speed_was = nil end
  tags.speed = next_
  b.tags = tags
  b.caption = "×" .. next_
end

-- (what the other parts call)
W.history_drawer = history_drawer
W.show_history = show_history
W.test_series = test_series
W.test_shown = test_shown
W.test_speed_off = test_speed_off
W.test_stop = test_stop
W.testing = testing
