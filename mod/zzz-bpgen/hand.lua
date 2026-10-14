-- A plan put down by hand: bpgen gives it as a blueprint for the cursor (its entities tagged bpgen_place = {id, i}),
-- the player moves, turns (R) and flips it with the game's own controls and pastes it; the ghosts it built are
-- gathered (on_built_entity), the ground round them measured, and bpgen's Python (ingame.connect) works out how it
-- was put down and routes its belt taps, new bus lanes and poles from there, pasted at once as ghosts.
-- Each paste is a placement of its own: the cursor keeps the blueprint for another.
local safe = require("safe")
local place = require("place")

local M = {}
local ctx  -- from control.lua: measure(player, area, kind, data), around(c, r), start_job, connected(player, res, rec)

local held = safe.stored("hand_held")        -- player index -> {id, spot = render ids}: the blueprint in their cursor
local pending = safe.stored("hand_pending")  -- player index -> {id, tick, built = {{i, x, y, direction}}, ghosts}
local MARGIN = 48  -- round what was pasted, the ground looked at for its taps and power

function M.setup(c) ctx = c end

local function clear_spot(index)
  for _, r in pairs((held[index] or {}).spot or {}) do if r.valid then r.destroy() end end
end

--- the plan's blueprint (ingame.hand's answer) into the player's cursor, bpgen's spot for it outlined.
--- -> true, or false and why
function M.give(player, res)
  if not res.blueprint then return false, tostring(res.error) end
  if not player.clear_cursor() then return false, { "bpgen-window.hand-full" } end
  player.cursor_stack.set_stack({ name = "blueprint" })
  if player.cursor_stack.import_stack(res.blueprint) == 1 then
    player.clear_cursor()
    return false, { "bpgen-window.hand-bp-not-imported" }
  end
  clear_spot(player.index)
  local spot = {}
  local s = res.spot
  if s then
    local lt, rb = { s[1], s[2] }, { s[1] + s[3], s[2] + s[4] }
    spot[1] = rendering.draw_rectangle({ color = { 0.04, 0.16, 0.2, 0.2 }, filled = true, left_top = lt,
      right_bottom = rb, surface = player.surface, players = { player }, time_to_live = 60 * 300 })
    spot[2] = rendering.draw_text({ text = { "bpgen-window.hand-spot" }, surface = player.surface,
      target = { s[1], s[2] - 1.2 }, color = { 0.6, 0.9, 1 }, scale = 1.5, players = { player },
      time_to_live = 60 * 300 })
  end
  held[player.index] = { id = res.id, spot = spot }
  return true
end

--- a ghost (or, with cheat mode, an entity) built from bpgen's handed blueprint: gathered for its placement
function M.on_built(e)
  local ent = e.entity
  if not (ent and ent.valid and e.player_index) then return end
  local tags = e.tags or (ent.type == "entity-ghost" and ent.tags)
  local t = tags and tags.bpgen_place
  local h = held[e.player_index]
  if not (t and h and t.id == h.id) then return end
  local p = pending[e.player_index]
  if not p or p.tick ~= game.tick then  -- (a paste builds all its ghosts in one tick: a new tick, a new paste)
    if p then M.flush(e.player_index) end
    p = { id = t.id, tick = game.tick, built = {}, ghosts = {} }
    pending[e.player_index] = p
  end
  local d = ent.direction
  p.built[#p.built + 1] = { i = t.i, x = ent.position.x, y = ent.position.y, direction = d }
  p.ghosts[#p.ghosts + 1] = ent
end

--- a paste gathered: the ground round it measured, then bpgen connects it (ingame.connect)
function M.flush(index)
  local p = pending[index]
  pending[index] = nil
  local player = game.get_player(index)
  if not (p and player and player.valid) or #p.built == 0 then return end
  clear_spot(index)
  local x1, y1, x2, y2
  for _, b in ipairs(p.built) do
    x1, y1 = math.min(x1 or b.x, b.x), math.min(y1 or b.y, b.y)
    x2, y2 = math.max(x2 or b.x, b.x), math.max(y2 or b.y, b.y)
  end
  local r = math.min(250, math.ceil(math.max(x2 - x1, y2 - y1) / 2) + MARGIN)
  ctx.measure(player, ctx.around({ x = (x1 + x2) / 2, y = (y1 + y2) / 2 }, r), "connect",
    { id = p.id, built = p.built, ghosts = p.ghosts })
end

--- every few ticks: pastes from an earlier tick go on to be connected
function M.tick()
  for index, p in pairs(pending) do
    if p.tick < game.tick then M.flush(index) end
  end
end

--- the measured ground round a paste: to Python
function M.measured(player, snap, data)
  ctx.start_job(player.index, "bpgen.ingame:connect", helpers.table_to_json({ player = player.index, id = data.id,
    built = data.built, snapshot = snap }), { player = player.index, connect = true, ghosts = data.ghosts })
end

--- ingame.connect's answer: what ties the paste in, pasted as ghosts where it belongs (superforced: its splitters
--- replace the belts they tap). -> the plan's and the connection's ghosts, what the paste marked, its area
function M.connected(player, res, job)
  local ghosts, marks, area = {}, {}, nil
  for _, g in ipairs(job.ghosts or {}) do if g.valid then ghosts[#ghosts + 1] = g end end
  if res.blueprint and res.absolute then
    local more, _, a, m = place.place(player, res.blueprint, nil, res.absolute.box)
    for _, g in pairs(more) do ghosts[#ghosts + 1] = g end
    marks, area = m, a
  end
  local x1, y1, x2, y2
  for _, g in ipairs(ghosts) do
    if g.valid then
      local b = g.bounding_box
      x1, y1 = math.min(x1 or b.left_top.x, b.left_top.x), math.min(y1 or b.left_top.y, b.left_top.y)
      x2, y2 = math.max(x2 or b.right_bottom.x, b.right_bottom.x), math.max(y2 or b.right_bottom.y, b.right_bottom.y)
    end
  end
  return ghosts, marks, x1 and { { x1, y1 }, { x2, y2 } } or area
end

--- the blueprint in the player's cursor now (bpgen's handed one, or nil)
function M.held_id(index)
  return held[index] and held[index].id
end

return M
