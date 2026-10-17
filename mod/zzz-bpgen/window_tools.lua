-- A bpgen tool in hand (refit, move, snapshot, ore patches, pick spot): the window and what's stuck to it out of the way,
-- a small card at the top says what to drag; Esc and E closing the window; the sandbox's card.

local W = require("window_core")
local ui, web, CARD, NAME, T, esc_check = W.ui, W.web, W.CARD, W.NAME, W.T, W.esc_check
local patch_areas = W.patch_areas

local TOOLS = {
  ["bpgen-refit"] = { title = T("mode-refit"), text = T("card-refit") },
  ["bpgen-move"] = { title = T("mode-move"), text = T("card-move") },
  ["bpgen-snapshot"] = { title = T("snapshot"), text = T("card-snapshot") },
  ["bpgen-spot"] = { title = T("pick-spot"), text = T("card-spot") },
  ["bpgen-patches"] = { title = T("ore-patches"), text = T("card-patches"), many = true },
}
W.TOOLS = TOOLS

local function patch_count(player)
  local n = #(patch_areas[player.index] or {})
  return n == 0 and T("no-patches") or T("patches-picked", n)
end

local function esc_tick()
  for i, what in pairs(esc_check) do
    esc_check[i] = nil
    local player = game.get_player(i)
    local frame = player and player.connected and player.gui.screen[NAME]
    if frame and frame.valid and frame.visible and player.opened_gui_type == defines.gui_type.none then
      if what == "closed" then W.close(player) else player.opened = frame end
    end
  end
end

--- the window and its stuck frames shown or hidden
local function window_shown(player, on)
  local frame = player.gui.screen[NAME]
  local as_web = web.model(player)
  -- (hidden: Esc isn't its any more, it doesn't close it; shown again: Esc is)
  if frame and not as_web then
    if not on and player.opened_gui_type == defines.gui_type.custom and player.opened == frame then
      frame.visible = false
      player.opened = nil
    elseif on and player.opened_gui_type == defines.gui_type.none then
      esc_check[player.index] = "back"
    end
  end
  if frame then frame.visible = on and not as_web end  -- (the web window's Lua one stays hidden: the shell hides)
  if as_web then web.show(player, on) end
  for _, c in pairs(ui.attached(player, NAME)) do
    c.visible = on and (c.name ~= NAME .. "__test" or c.tags.bpgen_open == true)
  end
  local menu = player.gui.screen[NAME .. "__menu"]
  if menu and not on then menu.destroy() end
end

--- the tool `item` in hand: the card up, the window hidden
function W.tool_card(player, item)
  local tool = TOOLS[item]
  if not tool then return end
  local screen = player.gui.screen
  if screen[CARD] then
    if screen[CARD].tags.item == item then return end
    screen[CARD].destroy()
  end
  window_shown(player, false)
  local card = screen.add({ type = "frame", name = CARD, direction = "vertical", tags = { item = item } })
  card.style.padding = 10
  card.style.width = 380
  ui.add(card, { type = "label", caption = T("card-title", "[item=" .. item .. "]", tool.title) }, "caption")
  local text = ui.add(card, { type = "label", caption = tool.text }, "text")
  text.style.single_line = false
  text.style.maximal_width = 356
  ui.add(card, { type = "label", name = "bpgen_card_note", caption = tool.many and patch_count(player)
    or T("card-note") }, "dim")
  local row = card.add({ type = "flow", direction = "horizontal" })
  row.style.top_margin = 6
  row.style.vertical_align = "center"
  row.add({ type = "empty-widget" }).style.horizontally_stretchable = true
  if tool.many then
    ui.add(row, { type = "button", caption = T("done"), tags = { bpgen = "tool_done" } }, "ok")
  end
  ui.add(row, { type = "button", caption = T("cancel"), tags = { bpgen = "tool_done" } }, "button")
  ui.adopt(card)
  local scale = player.display_scale * ui.scale_of(card)  -- (drawn at the Window size: adopt scaled it)
  card.location = { math.max(0, (player.display_resolution.width - 380 * scale) / 2), math.floor(60 * scale) }
end

--- the tool out of hand: the card gone, the window back
function W.tool_done(player)
  local card = player.gui.screen[CARD]
  if card then card.destroy() end
  window_shown(player, true)
end

--- the cursor changed (a tool taken from a shortcut or a hotkey, put away with Q): the card follows it
function W.on_cursor(player)
  local cs = player.cursor_stack
  local item = cs and cs.valid_for_read and cs.name
  if item and TOOLS[item] then return W.tool_card(player, item) end
  if player.gui.screen[CARD] then W.tool_done(player) end
end

--- an area dragged with the patch tool: added to the bus design's patches
function W.add_patch(player, area)
  local counts = {}
  for _, r in pairs(player.surface.find_entities_filtered({ area = area, type = "resource" })) do
    counts[r.name] = (counts[r.name] or 0) + 1
  end
  local list = patch_areas[player.index] or {}
  list[#list + 1] = { area = area, counts = counts }
  patch_areas[player.index] = list
  local frame = player.gui.screen[NAME]
  if frame then W.show_patches(frame, player) end
  local note = player.gui.screen[CARD] and player.gui.screen[CARD].bpgen_card_note
  if note then note.caption = patch_count(player) end
end

local SANDBOX_CARD = "bpgen_sandbox_card"
--- the card shown in the sandbox (the window hidden meanwhile): Place in base, Discard
local function sandbox_card(player, on)
  local screen = player.gui.screen
  if screen[SANDBOX_CARD] then screen[SANDBOX_CARD].destroy() end
  if not on then return end
  local card = screen.add({ type = "frame", name = SANDBOX_CARD, direction = "vertical" })
  card.style.padding = 10
  card.style.width = 380
  ui.add(card, { type = "label", caption = T("sandbox-title") }, "caption")
  local text = ui.add(card, { type = "label", caption = T("sandbox-text") }, "text")
  text.style.single_line = false
  text.style.maximal_width = 356
  local moves = card.add({ type = "flow", direction = "horizontal" })
  moves.style.top_margin = 6
  moves.style.vertical_align = "center"
  ui.add(moves, { type = "label", caption = T("sandbox-move") }, "text")
  for _, a in ipairs({ { "left", "◀", -1, 0 }, { "up", "▲", 0, -1 }, { "down", "▼", 0, 1 }, { "right", "▶", 1, 0 } }) do
    moves.add({ type = "button", name = "bpgen_sandbox_move_" .. a[1], caption = a[2], style = "tool_button",
      tags = { bpgen = "sandbox_move", dx = a[3], dy = a[4] }, tooltip = T("sandbox-move-tip", T("dir-" .. a[1])) })
  end
  moves.add({ type = "button", name = "bpgen_sandbox_turn", caption = "⟳", style = "tool_button",
    tags = { bpgen = "sandbox_turn" }, tooltip = T("sandbox-turn-tip") })
  local row = card.add({ type = "flow", direction = "horizontal" })
  row.style.top_margin = 6
  row.style.vertical_align = "center"
  row.add({ type = "empty-widget" }).style.horizontally_stretchable = true
  ui.add(row, { type = "button", name = "bpgen_sandbox_place", caption = T("sandbox-place"),
    tags = { bpgen = "sandbox_place" }, tooltip = T("sandbox-place-tip") }, "ok")
  ui.add(row, { type = "button", name = "bpgen_sandbox_discard", caption = T("sandbox-discard"),
    tags = { bpgen = "sandbox_discard" }, tooltip = T("sandbox-discard-tip") }, "button")
  ui.adopt(card)
  local scale = player.display_scale * ui.scale_of(card)
  card.location = { math.max(0, (player.display_resolution.width - 380 * scale) / 2), math.floor(60 * scale) }
end

-- (what the other parts call)
W.esc_tick = esc_tick
W.sandbox_card = sandbox_card
W.window_shown = window_shown
