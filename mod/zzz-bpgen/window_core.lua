-- The bpgen window's shared ground: its names, sizes, per-player state, the libraries it uses and the few helpers every
-- part of it needs (find, status, say, adopt). The window's parts (window_*.lua) take this table as W and put what
-- other parts call on it too (W.plan, W.on_result, ...), so no part needs another's file at load time.
-- Per-player state lives in storage (safe.stored), what is momentary in each part's own locals.
local W = {}


local NAME = "bpgen_window"
local PREVIEW = "bpgen-preview"

-- with the fse-std library mod (optional): its window, resized by its corner grip like every fse window
local stdwin = script.active_mods["fse-std"] and require("__fse-std__/window") or nil
local stdinput = stdwin and require("__fse-std__/input") or nil
local dnd = stdwin and require("__fse-std__/dnd") or nil  -- (History's rows dragged onto the preview)
local ui = require("ui")
local web = require("web")  -- (the window as a web page over the game: fse's shells)

--- the window's words: a line of [bpgen-window] in locale/en/window.cfg, with its parameters
local function T(key, ...) return { "bpgen-window." .. key, ... } end

--- localised strings (or plain ones) joined, `sep` between them: nested, as a LocalisedString takes 20 parameters
local function join(list, sep)
  local flat = {}
  for i, x in ipairs(list) do
    if i > 1 and sep then flat[#flat + 1] = sep end
    flat[#flat + 1] = x
  end
  while #flat > 20 do
    local up = {}
    for i = 1, #flat, 20 do up[#up + 1] = { "", table.unpack(flat, i, math.min(i + 19, #flat)) } end
    flat = up
  end
  return { "", table.unpack(flat) }
end

-- (with fse-std's layouts, what sticks out of the window: the rails left and right, the dock below; GUI units)
local OUT_SIDE, OUT_BELOW = 106, 32
local DOCK_W, DOCK_H = 720, 44                                  -- (the blueprint's actions, below the window)
local MAP_H = 390                    -- (the minimap and research box's height, the game's GUI units)
local TEST_W, TEST_H = 300, 330      -- (the test run's drawer on the right edge, under the preview's tools)
local LEFT_W = 344                   -- (the library window's width besides the preview: the left panel, borders)
-- (the game's own GUI the window keeps clear of, in the game's GUI units (times display_scale): the hotbar band at
-- the bottom (quick bar, shortcut bar, the character's guns), the minimap and research box in the top right corner)
local HOTBAR_H, MAP_W = 100, 262
-- (the preview's smallest; on a short screen, this tall: the window's height is the left panel's too, which needs
-- room for its settings, Plan and the result)
local CAM_MIN_W, CAM_MIN_H, CAM_TIGHT_H = 420, 420, 340

local bench = require("bench")
local safe = require("safe")
local place = require("place")
local hand = require("hand")
local sandbox = require("sandbox")

local ctx = {}  -- from control.lua (window.setup): craftable, unlocked, bonuses, start, measured, api, ...

-- (per-player state in storage: in multiplayer every peer must show and place the same; see safe.stored)
local results = safe.stored("results") -- player index -> { blueprint, res, center, zoom }
local placed = safe.stored("placed")     -- player index -> the ghosts "Place near me" and "Place by my base" made
local unmark = safe.stored("unmark")     -- player index -> what those pastes marked for deconstruction (Undo unmarks it)
local turns = safe.stored("turns")       -- player index -> quarter turns Rotate added to the plan next to the base
local tried = safe.stored("tried")       -- player index -> the boxes Redo looked past ({x, y, w, h} each)
local to_sandbox = safe.stored("to_sandbox")  -- player index -> true: "Try in sandbox" waits for Place by my base

local function find_in(el, name)
  if el.name == name then return el end
  for _, c in pairs(el.children) do
    local f = find_in(c, name)
    if f then return f end
  end
end

--- an element by name in el; in the window, also in the frames stuck to it (its rail, dock, toolbars, drawer)
local function find(el, name)
  if not (el and el.valid) then return nil end
  local f = find_in(el, name)
  if f or el.name ~= NAME or el.parent ~= game.get_player(el.player_index).gui.screen then return f end
  local player = game.get_player(el.player_index)
  for _, c in pairs(ui.attached(player, NAME)) do
    f = find_in(c, name)
    if f then return f end
  end
  return web.find(player, name)  -- (the web window: the preview's camera in its island)
end

-- (in groups: making things, a base, reworking a base, space; short names (mode-<mode> in the locale), the full one
-- as their tooltip (mode-<mode>-tip))
local MODES = { "line", "mall", "extend", "base", "busdesign", "refit", "move", "ship" }
local GROUP_START = { base = true, refit = true, ship = true }  -- (a gap above them on the rail)
local RAIL_W, RAIL_H = 112, #MODES * 34 + 3 * 12 + 8          -- (the rail of mode tabs, GUI units)
local MOVE_H = 12 + 22 + 3 * 34                                 -- (moving a plan next to the base: on the rail)
local TOOLS_H = 4 * 34 + 12 + 34 + 8 + MOVE_H                 -- (the preview's tools, a rail on the right)
local BUS_DIRS = { "north", "east", "south", "west" }
local patch_areas = safe.stored("patch_areas")  -- player index -> {{area, counts = {resource = tiles}}}: the ore patches picked for a bus design
local bus_shift = safe.stored("bus_shift")  -- player index -> tiles the bus head is moved sideways from the player (the ◀ ▶ arrows)
local BUS_STEP = 16

-- "Only unlocked" (the checkbox by the tabs, per player): every picker of the window offers only what the force has
-- unlocked: recipes it may craft, items it can make, entities whose item it can make. A picker's own filter stays
-- (kept in its tags) and the names are added to it; unticked, the own filter comes back.
local only_unlocked = safe.stored("only_unlocked")  -- player index -> true
-- "Docked" (the checkbox by it, per player): the modes, the preview's tools and the actions on frames stuck to the
-- window's edges (fse-std's layouts); unticked, all of it inside the window
local inside = safe.stored("inside")  -- player index -> true: inside the window
local function docked(player) return ui.layouts and not inside[player.index] and not web.model(player) end

local function status(frame, text)
  local s = find(frame, "bpgen_status")
  if s then s.caption = text or "" end
  if frame and frame.valid then web.dirty(game.get_player(frame.player_index)) end
end

--- what an action did: in the status line and as a toast (a message at the top that goes away by itself)
local function say(player, text)
  local frame = player.gui.screen[NAME]
  if frame then status(frame, text) end
  if web.model(player) then return web.toast(player, text) end  -- (the page shows it)
  ui.toast(player, text)
end

--- the window themed, the frames stuck to it too (call after adding elements: only new ones are themed)
local function adopt(player)
  local frame = player.gui.screen[NAME]
  if not frame then return end
  ui.adopt(frame)
  for _, c in pairs(ui.attached(player, NAME)) do ui.adopt(c) end
end

-- the window's settings as the player last left them (its pickers, fields, ticks, mode, products, more lines): kept
-- on every change, so the window opens again as it was closed (the X, Esc, the shortcut)
local forms = safe.stored("forms")  -- player index -> { mode, values = {name -> {e|t|s|i}}, products, lines, stale }

local CARD = "bpgen_tool_card"

-- Esc: the window is the player's opened GUI (as the game's own windows), so Esc and E close it. Another window
-- opened over it (the inventory, a machine's) leaves it open, and it is the opened GUI again once that one closes.
-- Looked at on the next tick: by then the game says whether something else took its place.
local esc_check = safe.stored("esc_check")  -- player index -> "closed" (it lost Esc) or "back" (another GUI closed)

local histories = safe.stored("histories")  -- player index -> the last history list (its blueprints for "To cursor")

local function sig(name)
  if prototypes.item[name] then return "[item=" .. name .. "]" end
  if prototypes.fluid[name] then return "[fluid=" .. name .. "]" end
  return name
end

W.ui = ui
W.web = web
W.stdwin = stdwin
W.stdinput = stdinput
W.dnd = dnd
W.bench = bench
W.safe = safe
W.place = place
W.hand = hand
W.sandbox = sandbox
W.ctx = ctx
W.BUS_DIRS = BUS_DIRS
W.BUS_STEP = BUS_STEP
W.CAM_MIN_H = CAM_MIN_H
W.CAM_MIN_W = CAM_MIN_W
W.CAM_TIGHT_H = CAM_TIGHT_H
W.CARD = CARD
W.DOCK_H = DOCK_H
W.DOCK_W = DOCK_W
W.GROUP_START = GROUP_START
W.HOTBAR_H = HOTBAR_H
W.LEFT_W = LEFT_W
W.MAP_H = MAP_H
W.MAP_W = MAP_W
W.MODES = MODES
W.NAME = NAME
W.OUT_BELOW = OUT_BELOW
W.OUT_SIDE = OUT_SIDE
W.PREVIEW = PREVIEW
W.RAIL_H = RAIL_H
W.RAIL_W = RAIL_W
W.T = T
W.TEST_H = TEST_H
W.TEST_W = TEST_W
W.TOOLS_H = TOOLS_H
W.adopt = adopt
W.bus_shift = bus_shift
W.docked = docked
W.esc_check = esc_check
W.find = find
W.find_in = find_in
W.forms = forms
W.histories = histories
W.inside = inside
W.join = join
W.only_unlocked = only_unlocked
W.patch_areas = patch_areas
W.placed = placed
W.results = results
W.say = say
W.sig = sig
W.status = status
W.to_sandbox = to_sandbox
W.tried = tried
W.turns = turns
W.unmark = unmark

return W
