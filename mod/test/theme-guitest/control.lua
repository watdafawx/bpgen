-- The bpgen window on fse-std's themed components in a real client: a line planned, the window shot in five themes
-- (and once more mid-plan, its progress bar showing), Undo asking first. Without fse-std (run_theme_gui.py --plain)
-- the same window in the game's own styles. Results in script-output/theme-gui.txt, shots theme-gui-*.png.
local steps, i, waited = {}, 0, 0
local THEMES = { "vanilla", "vulcanus", "aquilo", "crt", "glassdark" }
local themed = script.active_mods["fse-std"] ~= nil
local fails = 0

local function find_in(el, name)
  if not (el and el.valid) then return nil end
  if el.name == name then return el end
  for _, c in pairs(el.children) do
    local f = find_in(c, name)
    if f then return f end
  end
end
-- (in the window, and in the frames stuck to it: its rail, dock, toolbars, drawer)
local function attached()
  local out = {}
  for _, c in pairs(game.get_player(1).gui.screen.children) do
    local a = c.tags.fstd_attach
    if a and a.owner == "bpgen_window" then out[#out + 1] = c end
  end
  return out
end
local function find(el, name)
  local f = find_in(el, name)
  if f or not (el and el.valid and el.name == "bpgen_window") then return f end
  for _, c in pairs(attached()) do
    f = find_in(c, name)
    if f then return f end
  end
end

local function log(text) helpers.write_file("theme-gui.txt", text .. "\n", true) end
local function check(name, ok, detail)
  if not ok then fails = fails + 1 end
  log((ok and "ok   " or "FAIL ") .. name .. (detail and ("  (" .. tostring(detail) .. ")") or ""))
end
local function p() return game.get_player(1) end
local function w() return p().gui.screen.bpgen_window end
local function shot(name)
  game.take_screenshot({ player = 1, show_gui = true, path = "theme-gui-" .. name .. ".png", zoom = 1,
    resolution = { p().display_resolution.width, p().display_resolution.height }, daytime = 0 })
end
local function rc(fn, ...) return remote.call("bpgen", fn, ...) end
local function planned() local c = find(w(), "bpgen_cam") return c and c.visible end
local function f_or() return w() end
local function confirm() return p().gui.screen.fstd_confirm_bpgen_unplace end
local function ghosts()
  return #p().surface.find_entities_filtered({ type = "entity-ghost", force = p().force })
end

-- styles of the window's elements: how many are themed (fstd_*), each role counted
local function census()
  local n, plain, roles = 0, {}, {}
  local function walk(el)
    local s = el.style and el.style.name
    if s and s:sub(1, 5) == "fstd_" then
      n = n + 1
      local role = el.tags.fstd_role
      if role then roles[role] = (roles[role] or 0) + 1 end
    elseif el.type ~= "flow" and el.type ~= "empty-widget" and el.type ~= "table" and el.type ~= "camera" then
      plain[#plain + 1] = el.type .. ":" .. tostring(s)
    end
    for _, c in pairs(el.children) do walk(c) end
  end
  walk(w())
  for _, c in pairs(attached()) do walk(c) end
  return n, plain, roles
end

script.on_init(function()
  if remote.interfaces["freeplay"] then
    pcall(remote.call, "freeplay", "set_skip_intro", true)
    pcall(remote.call, "freeplay", "set_disable_crashsite", true)
  end
end)

local function set_theme(name)
  if themed then remote.call("fse-std", "set_theme", 1, name) end
end

steps = {
  { run = function()
      helpers.write_file("theme-gui.txt", "", false)
      if p().controller_type == defines.controllers.cutscene then p().exit_cutscene() end
      p().force.research_all_technologies()
      set_theme("vanilla")
    end, ticks = 30 },
  { run = function()
      rc("open_window", 1, { recipe = "iron-gear-wheel", machine = "assembling-machine-2", more = true })
      -- (mid-plan: the progress bar under Plan, a step and the folded details)
      rc("progress", 1, { fraction = 0.45, step = "routing iron-plate in from your base",
        lines = { "reading your request", "planning: 6 assemblers" } })
    end, ticks = 10 },
  { run = function()
      local f = w()
      check("window open", f ~= nil)
      check("progress shown", find(f, "bpgen_progress").visible)
      local bar = find(f, "bpgen_progress_bar")
      check("progress bar at 0.45", bar and math.abs(bar.value - 0.45) < 1e-6, bar and bar.value)
      -- names and tags the tests and the handlers find things by
      for _, n in ipairs({ "bpgen_tabs", "bpgen_plan", "bpgen_recipe", "bpgen_machine",
        "bpgen_rate", "bpgen_feed", "bpgen_make", "bpgen_also", "bpgen_more_toggle", "bpgen_more", "bpgen_status",
        "bpgen_cam", "bpgen_cursor", "bpgen_place", "bpgen_test", "bpgen_next_box", "bpgen_base_tools" }) do
        check("has " .. n, find(f, n) ~= nil)
      end
      local tabs = find(f, "bpgen_tabs")
      check("mode tabs", #tabs.children == 8 and tabs.tags.mode == "line" and tabs.children[1].tags.bpgen == "tab"
        and tabs.children[1].toggled, #tabs.children)
      if themed then
        local n, plain, roles = census()
        check("themed elements", n > 100, n)
        check("sections", (roles.section or 0) >= 6, roles.section)
        check("field captions dim", (roles.dim or 0) >= 15, roles.dim)
        check("rail tabs (8 modes, 8 preview tools)", (roles.rtab or 0) == 16, roles.rtab)
        local screen = p().gui.screen
        check("modes on a rail", screen.bpgen_window__left ~= nil and find_in(screen.bpgen_window__left, "bpgen_tabs") ~= nil)
        check("actions in a dock", screen.bpgen_window__bottom ~= nil and find_in(screen.bpgen_window__bottom, "bpgen_cursor") ~= nil)
        check("folds", (roles.tool or 0) >= 3 and find(f, "bpgen_modules_badge") ~= nil)
        check("progress bars (planning, test run)", (roles.progress or 0) == 2, roles.progress)
        log("not themed: " .. table.concat(plain, ", "))
      end
      shot("progress")
      rc("plan_window", 1)
    end, until_ = planned, ticks = 90 },
  { run = function()
      shot("vanilla")
      if themed then  -- (the preview's tools: a rail out of the window's right edge, as the modes' on the left)
        local rail = p().gui.screen.bpgen_window__tools
        check("preview tools on a rail", rail and find_in(rail, "bpgen_test") ~= nil and find_in(rail, "bpgen_grip") ~= nil)
        if rail and rail.location then
          local wl, sc, tw = w().location, p().display_scale, w().tags.fstd_tweak or {}
          check("the rail on the right edge", rail.location.x > wl.x + ((tw.width or 0) - 20) * sc,
            serpent.line(rail.location) .. " window " .. serpent.line(wl))
        end
        local nb = 0
        for _, row in pairs(find(w(), "bpgen_badges").children) do nb = nb + #row.children end
        check("result shown", find(w(), "bpgen_result").visible and find(w(), "bpgen_tiles").visible and nb >= 3, nb)
      end
    end, ticks = 25 },
}
-- sizes: the smallest the grip allows, and a big one (fse-std's grip: with the loader)
steps[#steps + 1] = { run = function()
    local grip
    local function walk(el) if el.tags.fstd_resize then grip = el end for _, c in pairs(el.children) do walk(c) end end
    walk(w())
    if themed then check("resize grip", grip ~= nil) end
    rc("resize", 1, 744, 666)
  end, ticks = 20 }
steps[#steps + 1] = { run = function() shot("small") end, ticks = 25 }
steps[#steps + 1] = { run = function() rc("resize", 1, 1700, 1050) end, ticks = 20 }
steps[#steps + 1] = { run = function()
    shot("large")
    if themed then
      local res = p().display_resolution
      for _, c in pairs(attached()) do
        if c.visible and c.location then
          local tw = (c.tags.fstd_attach or {})
          check("on the screen: " .. c.name, c.location.x >= 0 and c.location.x + (tw.w or 0) * p().display_scale <= res.width + 1,
            serpent.line(c.location))
        end
      end
    end
  end, ticks = 25 }
steps[#steps + 1] = { run = function() rc("resize", 1, 1240, 860) end, ticks = 20 }
-- the window's settings: the gear by its close button opens them under the title bar
steps[#steps + 1] = { run = function()
    if themed then
      local gear
      local function walk(el) if el.tags.fstd_settings then gear = el end for _, c in pairs(el.children) do walk(c) end end
      walk(w())
      check("settings button", gear ~= nil)
      rc("click", 1, { fstd_settings = "bpgen_window" })
    end
  end, ticks = 20 }
steps[#steps + 1] = { run = function()
    if themed then
      local m = p().gui.screen.bpgen_window__menu
      check("settings open", m ~= nil and find_in(m, "bpgen_docked") ~= nil and find_in(m, "bpgen_only_unlocked") ~= nil)
      if m and m.location then
        local wl, sc, tw = w().location, p().display_scale, w().tags.fstd_tweak or {}
        check("settings under the title bar, right", m.location.y > wl.y and m.location.y < wl.y + 80 * sc
          and m.location.x > wl.x + ((tw.width or 0) - 300) * sc, serpent.line(m.location))
      end
      shot("settings")
    end
  end, ticks = 25 }
steps[#steps + 1] = { run = function()
    if themed then
      rc("only_unlocked", 1, true)
      check("the filter's mark", find(w(), "bpgen_unlocked_badge").visible)
      rc("only_unlocked", 1, false)
      rc("click", 1, { fstd_settings = "bpgen_window" })
      check("settings closed", p().gui.screen.bpgen_window__menu == nil)
      rc("click", 1, { bpgen = "reset_size" })
      local tw = w().tags.fstd_tweak or {}
      check("reset size: the plan kept", find(w(), "bpgen_cam").visible, tw.width)
    end
  end, ticks = 20 }
-- "Docked" off: everything in the window, the plan kept; then on again
steps[#steps + 1] = { run = function() if themed then rc("docked", 1, false) end end, ticks = 30 }
steps[#steps + 1] = { run = function()
    if themed then
      check("not docked: nothing stuck to the window", #attached() == 0, #attached())
      check("not docked: tabs and tools inside", find_in(w(), "bpgen_tabs") ~= nil and find_in(w(), "bpgen_test") ~= nil
        and find_in(w(), "bpgen_cursor") ~= nil)
      check("not docked: the plan kept", find(w(), "bpgen_cam").visible and find(w(), "bpgen_recipe").elem_value == "iron-gear-wheel")
      shot("inside")
    end
  end, ticks = 25 }
steps[#steps + 1] = { run = function() if themed then rc("docked", 1, true) end end, ticks = 30 }
steps[#steps + 1] = { run = function()
    if themed then check("docked again", p().gui.screen.bpgen_window__left ~= nil and find(w(), "bpgen_cam").visible) end
  end, ticks = 5 }
-- the preview's recipes locked again (a mod's migration resetting technology effects): planned again, its machines
-- aren't crossed out as not researched
steps[#steps + 1] = { run = function()
    game.forces["bpgen-preview"].recipes["iron-gear-wheel"].enabled = false
    rc("plan_window", 1)
  end, until_ = planned, ticks = 90 }
steps[#steps + 1] = { run = function()
    local s = game.surfaces["bpgen-preview"]
    local bad, n = 0, 0
    for _, e in pairs(s.find_entities_filtered({ type = "assembling-machine", force = "bpgen-preview" })) do
      n = n + 1
      if e.status == defines.entity_status.recipe_not_researched then bad = bad + 1 end
    end
    check("preview machines not crossed out", n > 0 and bad == 0, bad .. " of " .. n)
  end, ticks = 5 }
-- a selection tool in hand: the window hidden, a card at the top says what to drag; back once the tool goes
steps[#steps + 1] = { run = function() rc("click", 1, { bpgen = "refit" }, true) end, ticks = 10 }
steps[#steps + 1] = { run = function()
    local card = p().gui.screen.bpgen_tool_card
    check("tool card up", card ~= nil and p().cursor_stack.valid_for_read and p().cursor_stack.name == "bpgen-refit")
    check("window hidden", not w().visible)
    local shown = 0
    for _, c in pairs(attached()) do if c.visible then shown = shown + 1 end end
    check("its stuck frames hidden", shown == 0, shown)
    shot("tool-card")
  end, ticks = 25 }
steps[#steps + 1] = { run = function() p().clear_cursor() end, ticks = 5 }  -- (as Q; a shot is taken at the tick's end)
steps[#steps + 1] = { run = function()
    check("Q: card gone, window back", p().gui.screen.bpgen_tool_card == nil and w().visible)
    rc("click", 1, { bpgen = "patches" }, true)
    rc("add_patch", 1, { left_top = { x = 0, y = 0 }, right_bottom = { x = 4, y = 4 } })
  end, ticks = 10 }
steps[#steps + 1] = { run = function()
    local card = p().gui.screen.bpgen_tool_card
    check("patch card counts", card and card.bpgen_card_note and card.bpgen_card_note.caption == "1 patch picked.",
      card and card.bpgen_card_note and card.bpgen_card_note.caption)
    shot("tool-card-patches")
  end, ticks = 25 }
steps[#steps + 1] = { run = function()
    rc("click", 1, { bpgen = "tool_done" })
    check("Done: card gone, window back, tool away", p().gui.screen.bpgen_tool_card == nil and w().visible
      and not p().cursor_stack.valid_for_read)
    rc("click", 1, { bpgen = "patches_clear" })
    rc("click", 1, { bpgen = "tab", mode = "line" })
  end, ticks = 10 }
for k = 2, #THEMES do
  steps[#steps + 1] = { run = function() set_theme(THEMES[k]) end, ticks = 5 }
  steps[#steps + 1] = { run = function()
      if themed then
        local s = find(w(), "bpgen_recipe").parent.parent.parent.style.name  -- (the Recipe section)
        check(THEMES[k] .. ": restyled", s:find(THEMES[k], 1, true) ~= nil, s)
      end
      shot(THEMES[k])
    end, ticks = 25 }
end
steps[#steps + 1] = { run = function()
    set_theme("vanilla")
    rc("click", 1, { bpgen = "place" })
  end, ticks = 10 }
steps[#steps + 1] = { run = function()
    local n = ghosts()
    check("placed ghosts", n > 0, n)
    rc("click", 1, { bpgen = "unplace" }, true)  -- (as a press of Undo)
    if themed then
      check("Undo asks first", confirm() ~= nil)
      check("nothing removed yet", ghosts() == n, ghosts())
    else
      check("Undo without fse-std: removed at once", ghosts() == 0, ghosts())
    end
  end, ticks = 10 }
steps[#steps + 1] = { run = function() shot("confirm") end, ticks = 25 }
steps[#steps + 1] = { run = function()
    if themed then
      local n = ghosts()
      p().opened = nil  -- (as Esc: a no)
      check("Esc: the dialog goes", confirm() == nil)
      check("Esc: the ghosts stay", ghosts() == n, ghosts())
      rc("click", 1, { bpgen = "unplace" })  -- (a script's press: at once)
      check("scripted Undo removes them", ghosts() == 0, ghosts())
      check("toasts", p().gui.screen["fstd_toasts_zzz-bpgen"] ~= nil)
    end
    find(w(), "bpgen_rate").text = "90"
    rc("touch", 1, "bpgen_rate")  -- (as if typed)
    check("changed: the note", find(w(), "bpgen_stale").visible)
    if themed then check("changed: Plan breathes", find(w(), "bpgen_plan").tags.fstd_role == "pulse") end
  end, ticks = 30 }
steps[#steps + 1] = { run = function()
    shot("changed")
    rc("click", 1, { bpgen = "test" })
    rc("click", 1, { bpgen = "test_speed" })  -- (x5)
    rc("click", 1, { bpgen = "test_speed" })  -- (x10: past its warm-up in a few seconds)
  end, ticks = 6600 }
steps[#steps + 1] = { run = function()
    local box = find(w(), "bpgen_test_box")
    check("test run shown", box and box.visible)
    local bar = find(w(), "bpgen_test_bar")
    check("test run measured", bar and bar.value > 0, bar and bar.value)
    log("test: " .. tostring(find(w(), "bpgen_test_status").caption):gsub("\n", " | "))
    shot("test")
  end, ticks = 25 }
steps[#steps + 1] = { run = function()
    rc("click", 1, { bpgen = "test" })  -- (stop)
    rc("click", 1, { bpgen = "history" })
  end, ticks = 90 }
steps[#steps + 1] = { run = function()
    if themed then
      local list = find(w(), "bpgen_history_list")
      check("History drawer", p().gui.screen.bpgen_window__right ~= nil and list ~= nil)
      check("History rows", list and #list.children >= 1, list and #list.children)
    end
    shot("history")
  end, ticks = 25 }
steps[#steps + 1] = { run = function()
    rc("click", 1, { bpgen = "close" })
    check("closed with what's stuck to it", w() == nil and #attached() == 0, #attached())
    log(fails == 0 and "PASS" or ("FAILED " .. fails))
    helpers.write_file("theme-gui-done.txt", "done", false)
  end, ticks = 5 }

script.on_event(defines.events.on_tick, function()
  if not p() or i > #steps then return end
  local s = steps[i]
  local late = s and s.until_ and waited > 3600
  if i == 0 or (waited >= (s.ticks or 0) and (not s.until_ or s.until_() or late)) then
    if late then check("step " .. i .. " in time", false) end
    i = i + 1
    waited = 0
    if steps[i] then steps[i].run() end
  else
    waited = waited + 1
  end
end)
