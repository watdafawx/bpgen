-- Real client: bpgen's buttons in Recipe Book's recipe page and a Factory Planner production table (here a stand-in
-- built by this mod: FP can't be opened from a script), each clicked. Results in script-output/compat-gui.txt.
local out = {}
local function log(s) out[#out + 1] = s end

local function find(el, name)
  for _, c in pairs(el.children) do
    if c.name == name then return c end
    local f = find(c, name)
    if f then return f end
  end
end

script.on_init(function()
  if remote.interfaces.freeplay then
    remote.call("freeplay", "set_skip_intro", true)
    remote.call("freeplay", "set_disable_crashsite", true)
  end
end)

script.on_event(defines.events.on_tick, function(e)
  local player = game.get_player(1)
  if not player then return end
  local screen = player.gui.screen
  if e.tick == 60 then
    log("open_page: " .. tostring(remote.call("RecipeBook", "open_page", 1, prototypes.recipe["electronic-circuit"])))
    -- a recipe in FP's (empty) main window, as its production table has them
    local fp = screen.fp_frame_main_dialog
    fp.visible = true
    local t = fp.add({ type = "table", name = "compat_rows", column_count = 2 })
    t.add({ type = "flow" }).add({ type = "sprite-button", sprite = "recipe/iron-gear-wheel" })
    t.add({ type = "flow" }).add({ type = "sprite-button", sprite = "item/iron-plate" })
  elseif e.tick == 90 then
    remote.call("bpgen", "compat_scan", 1)
    local rb = screen.rb_main_window and find(screen.rb_main_window, "bpgen_xmod")
    log("recipe book button: " .. (rb and rb.tags.recipe or "missing"))
    local fp = find(screen.fp_frame_main_dialog, "bpgen_xmod")
    log("factory planner button: " .. (fp and fp.tags.recipe or "missing"))
    local n = 0
    for _, c in pairs(screen.fp_frame_main_dialog.compat_rows.children) do if c.bpgen_xmod then n = n + 1 end end
    log("factory planner buttons: " .. n)
    remote.call("bpgen", "compat_scan", 1)  -- again: no duplicates
    n = 0
    for _, c in pairs(screen.fp_frame_main_dialog.compat_rows.children) do if c.bpgen_xmod then n = n + 1 end end
    log("after a second scan: " .. n)
    game.take_screenshot({ player = player, path = "compat-gui.png", show_gui = true })
  elseif e.tick == 100 then
    helpers.write_file("compat-gui.txt", table.concat(out, "\n") .. "\n", false)
    helpers.write_file("compat-gui-done.txt", "1", false)
  end
end)
