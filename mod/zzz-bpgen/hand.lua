-- A plan put down by hand: bpgen gives it as a blueprint for the cursor (its entities tagged bpgen_place = {id, i}),
-- the player moves, turns (R) and flips it with the game's own controls and pastes it; the ghosts it built are
-- gathered (on_built_entity), the ground round them measured, and bpgen's Python (ingame.connect) works out how it
-- was put down and routes its belt taps, new bus lanes and poles from there, pasted at once as ghosts.
-- Each paste is a placement of its own: the cursor keeps the blueprint for another. The last one is kept (records)
-- with a card on the screen: Re-route (its connections routed again, the base may have changed), Move (its ghosts
-- taken away and the blueprint back in the cursor), Undo (the window's).
local safe = require("safe")
local place = require("place")
local build = require("build")
local ui = require("ui")

local M = {}
local ctx  -- from control.lua: measure(player, area, kind, data), around(c, r), start_job, site_set, site_delta,
-- placing(player, ghosts): the window's Undo takes these from now on; connected(player, res, ghosts, marks, area):
-- a paste tied in (the window's card and its Undo)

local held = safe.stored("hand_held")        -- player index -> {id, blueprint, spot = render ids}: in their cursor
local pending = safe.stored("hand_pending")  -- player index -> {id, tick, built = {{i, x, y, direction}}, ghosts}
-- player index -> the last paste: {id, blueprint, built, plan = its own ghosts, links = what ties it in (ghosts),
-- marks = what that paste marked for removal}
local records = safe.stored("hand_records")
-- player index -> {tokens = {token = true}: pastes being tied in, latest = the last one's}: Undo drops the latest's
-- answer (its ghosts are already gone); an earlier paste's is still tied in, without taking over the card
local inflight = safe.stored("hand_inflight")
local MARGIN = 48  -- round what was pasted, the ground looked at for its taps and power
local CARD = "bpgen_paste_card"

local function T(key, ...) return { "bpgen-window." .. key, ... } end

function M.setup(c) ctx = c end

local function clear_spot(index)
  for _, r in pairs((held[index] or {}).spot or {}) do if r.valid then r.destroy() end end
end

--- a blueprint string into the player's cursor -> true, or false and why
local function to_cursor(player, bp)
  if not player.clear_cursor() then return false, T("hand-full") end
  player.cursor_stack.set_stack({ name = "blueprint" })
  if player.cursor_stack.import_stack(bp) == 1 then
    player.clear_cursor()
    return false, T("hand-bp-not-imported")
  end
  return true
end

--- the plan's blueprint (ingame.hand's answer) into the player's cursor, bpgen's spot for it outlined.
--- -> true, or false and why
function M.give(player, res)
  if not res.blueprint then return false, tostring(res.error) end
  local ok, why = to_cursor(player, res.blueprint)
  if not ok then return false, why end
  clear_spot(player.index)
  local spot = {}
  local s = res.spot
  if s then
    local lt, rb = { s[1], s[2] }, { s[1] + s[3], s[2] + s[4] }
    spot[1] = rendering.draw_rectangle({ color = { 0.04, 0.16, 0.2, 0.2 }, filled = true, left_top = lt,
      right_bottom = rb, surface = player.surface, players = { player }, time_to_live = 60 * 300 })
    spot[2] = rendering.draw_text({ text = T("hand-spot"), surface = player.surface,
      target = { s[1], s[2] - 1.2 }, color = { 0.6, 0.9, 1 }, scale = 1.5, players = { player },
      time_to_live = 60 * 300 })
  end
  held[player.index] = { id = res.id, blueprint = res.blueprint, spot = spot }
  return true
end

local connect  -- (below)

--- the plan put down at bpgen's spot for it (ingame.hand's answer with a spot), as if the player had pasted it
--- there (a part a tick): its ghosts tied into the base, the card to move it, route it again or take it back.
--- -> true, or false (no spot: the caller hands it over instead; nothing built there: handed over then)
function M.put(player, res)
  local s = res.spot
  if not (res.blueprint and s) then return false end
  place.start(player, res, { x = s[1] + s[3] / 2, y = s[2] + s[4] / 2 }, nil, nil, "hand_put",
    { id = res.id, blueprint = res.blueprint, spot = s })
  return true
end

place.after.hand_put = function(player, ghosts, _, _, _, d)
  local built, mine = {}, {}
  for _, g in pairs(ghosts) do
    local t = g.valid and g.type == "entity-ghost" and g.tags and g.tags.bpgen_place
    if t and t.id == d.id then
      built[#built + 1] = { i = t.i, x = g.position.x, y = g.position.y, direction = g.direction }
      mine[#mine + 1] = g
    end
  end
  if #built == 0 then  -- (nothing could be built at the spot: in the hand to put down instead)
    local ok, why = M.give(player, { blueprint = d.blueprint, id = d.id, spot = d.spot })
    if ok then ui.toast(player, T("hand-put-down-spot")) else player.print({ "", "[bpgen] ", why }) end
    return
  end
  clear_spot(player.index)
  held[player.index] = { id = d.id, blueprint = d.blueprint, spot = {} }
  connect(player, { id = d.id, built = built, ghosts = mine, blueprint = d.blueprint })
end

--- a ghost (or, with cheat mode, an entity) built from bpgen's handed blueprint: gathered for its placement
function M.on_built(e)
  local ent = e.entity
  if not (ent and ent.valid and e.player_index) then return end
  local tags = e.tags or (ent.type == "entity-ghost" and ent.tags)
  local t = tags and tags.bpgen_place
  local h = held[e.player_index]
  if not (t and h and t.id == h.id) then return end
  if place.placing(e.player_index, "hand_put") then return end  -- (bpgen putting it down itself: not a paste)
  local p = pending[e.player_index]
  if not p or p.tick ~= game.tick then  -- (a paste builds all its ghosts in one tick: a new tick, a new paste)
    if p then M.flush(e.player_index) end
    p = { id = t.id, tick = game.tick, built = {}, ghosts = {}, blueprint = h.blueprint }
    pending[e.player_index] = p
  end
  p.built[#p.built + 1] = { i = t.i, x = ent.position.x, y = ent.position.y, direction = ent.direction }
  p.ghosts[#p.ghosts + 1] = ent
end

--- bpgen connects a paste (ingame.connect); paste: {id, built, ghosts, blueprint}. Inside the area Python keeps
--- a site of (Place by my base's look round, or the last paste's): only what changed there since, at once; else (or
--- `full`) the ground round it read and measured first
connect = function(player, paste, full)
  if not paste.token then  -- (a new paste: Undo takes its ghosts back even before it's tied in)
    storage.bpgen_paste_n = (storage.bpgen_paste_n or 0) + 1
    paste.token = storage.bpgen_paste_n
    ctx.placing(player, paste.ghosts or {})
  end
  local f = inflight[player.index] or { tokens = {} }
  f.tokens[paste.token], f.latest = true, paste.token
  inflight[player.index] = f
  local x1, y1, x2, y2
  for _, b in ipairs(paste.built) do
    x1, y1 = math.min(x1 or b.x, b.x), math.min(y1 or b.y, b.y)
    x2, y2 = math.max(x2 or b.x, b.x), math.max(y2 or b.y, b.y)
  end
  local r = math.min(250, math.ceil(math.max(x2 - x1, y2 - y1) / 2) + MARGIN)
  local cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
  local delta = not full and ctx.site_delta(player, { cx - r, cy - r, cx + r, cy + r })
  if delta then
    ctx.start_job(player.index, "bpgen.ingame:connect", { player = player.index, id = paste.id,
      built = paste.built, delta = delta }, { player = player.index, connect = true, paste = paste })
    return
  end
  ctx.measure(player, ctx.around({ x = cx, y = cy }, r), "connect", paste)
end

--- Python's site wasn't the one a delta was from (another plan read the ground since): the paste read in full
function M.rescan(player, job)
  local paste = job.paste or {}
  if paste.built and #paste.built > 0 then connect(player, paste, true) end
end

--- a paste gathered: on to be connected
function M.flush(index)
  local p = pending[index]
  pending[index] = nil
  local player = game.get_player(index)
  if not (p and player and player.valid) or #p.built == 0 then return end
  clear_spot(index)
  connect(player, p)
end

--- every few ticks: pastes from an earlier tick go on to be connected
function M.tick()
  for index, p in pairs(pending) do
    if p.tick < game.tick then M.flush(index) end
  end
end

--- the measured ground round a paste: to Python (which keeps it as the player's site: later pastes there send deltas)
function M.measured(player, snap, paste)
  ctx.site_set(player, snap)
  ctx.start_job(player.index, "bpgen.ingame:connect", { player = player.index, id = paste.id,
    built = paste.built, snapshot = snap }, { player = player.index, connect = true, paste = paste })
end

--- ingame.connect's answer: what ties the paste in, pasted as ghosts where it belongs (superforced: its splitters
--- replace the belts they tap), kept as the player's last paste. -> every ghost of it, what the paste marked, its area
--- whether ingame.connect's answer (its job) is for the paste being tied in now: Undo, or a newer paste, drops it
function M.current(player, job)
  local f = inflight[player.index]
  return job.paste ~= nil and job.paste.token ~= nil and f ~= nil and f.tokens[job.paste.token] == true
end

--- whether the job's paste is the player's last one (its answer updates the card and what Undo takes back)
function M.latest(player, job)
  local f = inflight[player.index]
  return f ~= nil and job.paste ~= nil and f.latest == job.paste.token
end

--- what ties a paste in (ingame.connect's answer) placed (a part a tick), then the paste kept as the player's last
--- and, when it is still their latest, ctx.connected(player, res, every ghost of it, what it marked, its area)
function M.connected(player, res, job)
  local paste = job.paste or {}
  local f = inflight[player.index]
  local latest = f and f.latest == paste.token
  if f then f.tokens[paste.token or 0] = nil end
  local d = { paste = paste, latest = latest, res = res }
  if res.blueprint and res.absolute then
    return place.start(player, res, nil, res.absolute.box, nil, "hand_links", d)
  end
  place.after.hand_links(player, {}, nil, nil, {}, d)
end

place.after.hand_links = function(player, more, _, area, marks, d)
  local paste, plan, links = d.paste, {}, {}
  for _, g in ipairs(paste.ghosts or {}) do if g.valid then plan[#plan + 1] = g end end
  for _, g in pairs(more) do links[#links + 1] = g end
  if d.latest then
    records[player.index] = { id = paste.id, blueprint = paste.blueprint, built = paste.built, plan = plan,
      links = links, marks = marks }
  end
  local ghosts = {}
  local x1, y1, x2, y2
  for _, list in ipairs({ plan, links }) do
    for _, g in ipairs(list) do
      if g.valid then
        ghosts[#ghosts + 1] = g
        local b = g.bounding_box
        x1, y1 = math.min(x1 or b.left_top.x, b.left_top.x), math.min(y1 or b.left_top.y, b.left_top.y)
        x2, y2 = math.max(x2 or b.right_bottom.x, b.right_bottom.x), math.max(y2 or b.right_bottom.y, b.right_bottom.y)
      end
    end
  end
  if d.latest then ctx.connected(player, d.res, ghosts, marks, x1 and { { x1, y1 }, { x2, y2 } } or area) end
end

--- the blueprint in the player's cursor now (bpgen's handed one, or nil)
function M.held_id(index)
  return held[index] and held[index].id
end

-- the card after a paste ----------------------------------------------------------------------------------------

function M.close_card(player)
  local c = player.gui.screen[CARD]
  if c then c.destroy() end
end

--- the card for the player's last paste: what tied it in (text), Re-route, Move, Undo
function M.card(player, text)
  M.close_card(player)
  if not records[player.index] then return end
  local card = player.gui.screen.add({ type = "frame", name = CARD, direction = "vertical" })
  card.style.padding = 10
  card.style.width = 380
  local head = card.add({ type = "flow", direction = "horizontal" })
  head.style.vertical_align = "center"
  ui.add(head, { type = "label", caption = T("paste-title") }, "caption")
  head.add({ type = "empty-widget" }).style.horizontally_stretchable = true
  head.add({ type = "sprite-button", name = "bpgen_paste_close", sprite = "utility/close", style = "frame_action_button",
    tags = { bpgen = "paste_close" }, tooltip = T("paste-done-tip") })
  local label = ui.add(card, { type = "label", caption = text }, "text")
  label.style.single_line = false
  label.style.maximal_width = 356
  local row = card.add({ type = "flow", direction = "horizontal" })
  row.style.top_margin = 6
  row.style.vertical_align = "center"
  ui.add(row, { type = "button", name = "bpgen_paste_reroute", caption = T("paste-reroute"),
    tags = { bpgen = "paste_reroute" }, tooltip = T("paste-reroute-tip") }, "button")
  ui.add(row, { type = "button", name = "bpgen_paste_move", caption = T("paste-move"),
    tags = { bpgen = "paste_move" }, tooltip = T("paste-move-tip") }, "button")
  ui.add(row, { type = "button", name = "bpgen_paste_undo", caption = T("undo"),
    tags = { bpgen = "unplace" }, tooltip = T("undo-tip") }, "button")
  ui.adopt(card)
  local scale = player.display_scale * ui.scale_of(card)
  card.location = { math.max(0, (player.display_resolution.width - 380 * scale) / 2), math.floor(60 * scale) }
end

--- the last paste's own ghosts and its links (what it ties in with) removed (a few hundred a tick, raised: the site
--- sees them go), what they marked unmarked; then build.on_done[then_kind] when given. -> how many of the plan's own
--- were built already (those stay)
local function take_away(player, rec, plan_too, then_kind, data)
  local built, doomed = 0, {}
  for _, g in ipairs(rec.links or {}) do
    if g.valid and g.type == "entity-ghost" then doomed[#doomed + 1] = g end
  end
  for _, g in ipairs(plan_too and rec.plan or {}) do
    if g.valid then
      if g.type == "entity-ghost" then doomed[#doomed + 1] = g else built = built + 1 end
    end
  end
  local marks = rec.marks or {}
  rec.links, rec.marks = {}, {}
  build.destroy_later(doomed, true, then_kind, data, player.index, marks, player.force)
  return built
end

--- Re-route: the last paste's connections taken away and routed again from where it stands (the base round it
--- measured anew). -> true, or false and why
function M.reroute(player)
  local rec = records[player.index]
  if not rec then return false, T("paste-none") end
  place.clear_outline(player)
  -- (routed again once the old links are gone: the site's delta has them gone)
  take_away(player, rec, false, "hand_reroute", { id = rec.id })
  return true
end

build.on_done.hand_reroute = function(t)
  local player = game.get_player(t.player)
  local rec = player and records[player.index]
  if not (rec and rec.id == t.data.id) then return end
  local plan = {}
  for _, g in ipairs(rec.plan or {}) do if g.valid then plan[#plan + 1] = g end end
  connect(player, { id = rec.id, built = rec.built, ghosts = plan, blueprint = rec.blueprint })
end

--- Move: the last paste taken away (its ghosts; what's built of it already stays) and its blueprint back in the
--- cursor, to be put down again and tied in where it lands. -> true, or false and why
function M.move(player)
  local rec = records[player.index]
  if not rec then return false, T("paste-none") end
  if not rec.blueprint then return false, T("paste-none") end
  local ok, why = to_cursor(player, rec.blueprint)
  if not ok then return false, why end
  local built = take_away(player, rec, true)
  place.clear_outline(player)
  held[player.index] = { id = rec.id, blueprint = rec.blueprint, spot = {} }
  records[player.index] = nil
  M.close_card(player)
  return true, built
end

--- the window's Undo took the last paste away: forgotten, its card closed
function M.forget(player)
  records[player.index] = nil
  local f = inflight[player.index]  -- (the last paste still being tied in: its answer is dropped)
  if f and f.latest then f.tokens[f.latest], f.latest = nil, nil end
  M.close_card(player)
end

--- (tests) the player's last paste: how many ghosts of its own and links it has
function M.record(index)
  local rec = records[index]
  if not rec then return nil end
  local n, l = 0, 0
  for _, g in ipairs(rec.plan) do if g.valid then n = n + 1 end end
  for _, g in ipairs(rec.links) do if g.valid then l = l + 1 end end
  return { id = rec.id, plan = n, links = l }
end

return M
