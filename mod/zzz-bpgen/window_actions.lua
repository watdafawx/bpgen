-- What the buttons do to a plan: Plan, to the cursor, Undo, Place it next to the base (and Try in sandbox, the bus lane
-- upgrades), the tools' areas (Refit, Move, Pick spot), and the answers of the hand and the paste tied into the base.

local W = require("window_core")
local ui, safe, place, hand, sandbox, ctx, NAME = W.ui, W.safe, W.place, W.hand, W.sandbox, W.ctx, W.NAME
local build = W.build
local T, find, placed, results, say, status, tried = W.T, W.find, W.placed, W.results, W.say, W.status, W.tried
local turns, unmark = W.turns, W.unmark

local function to_cursor(player)
  local r = results[player.index]
  if not r then return end
  if not player.clear_cursor() then return say(player, T("hand-full")) end
  player.cursor_stack.set_stack({ name = "blueprint" })
  if player.cursor_stack.import_stack(r.blueprint) == 1 then
    player.clear_cursor()
    return say(player, T("hand-bp-not-imported"))
  end
  W.close(player)  -- (out of the way of placing it)
  ui.toast(player, T("bp-in-hand"))
end

--- the ghosts bpgen placed that are still there to remove
local function ghosts_left(player)
  local n = 0
  for _, g in pairs(placed[player.index] or {}) do
    if g.valid and (g.type == "entity-ghost" or g.type == "tile-ghost") then n = n + 1 end
  end
  return n
end

--- Undo: bpgen's unbuilt ghosts removed (a few hundred a tick), what they'd have replaced unmarked
local function unplace(player)
  place.clear_outline(player)
  local doomed = {}
  for _, g in pairs(placed[player.index] or {}) do
    if g.valid and (g.type == "entity-ghost" or g.type == "tile-ghost") then doomed[#doomed + 1] = g end
  end
  for _, g in ipairs(place.cancel(player.index)) do  -- (a placement still going on: stopped, what it put down too)
    if g.valid and (g.type == "entity-ghost" or g.type == "tile-ghost") then doomed[#doomed + 1] = g end
  end
  -- (the belts its splitters would have replaced: kept)
  local kept = {}
  for _, e in pairs(unmark[player.index] or {}) do kept[#kept + 1] = e end
  placed[player.index], unmark[player.index] = nil, nil
  hand.forget(player)
  build.destroy_later(doomed, true, nil, nil, player.index, kept, player.force)
  say(player, T("ghosts-removed", #doomed))
end
ui.on_yes("bpgen_unplace", function(player) unplace(player) end)
W.unplace = unplace  -- (tests: Undo, as its button does once asked)

--- an absolute plan (next to the base) as ghosts where it belongs, its taps replacing the belts they cut into,
--- as a Ctrl+Shift paste
--- (tests) the last plan's absolute placement: its box, and what it is
function W.last_absolute(player_index)
  local r = results[player_index]
  return r and r.res.absolute
end

function W.place_absolute(player)
  local frame = player.gui.screen[NAME]
  local r = results[player.index]
  if not (frame and r and r.res.absolute) then return end
  if r.res.mode == "ship" and not player.surface.platform then
    return status(frame, T("ship-stand-on-platform"))
  end
  local cleared = {}  -- (a refit: the area's old entities go, the mall's ghosts on their spot; Undo unmarks them)
  for _, c in ipairs(r.res.absolute.clear or {}) do
    -- (any quality: find_entity with a bare name only finds normal ones)
    for _, e in pairs(player.surface.find_entities_filtered({ name = c.name, position = c.position })) do
      if not e.to_be_deconstructed() and e.order_deconstruction(player.force, player) then cleared[#cleared + 1] = e end
    end
  end
  unmark[player.index] = cleared
  say(player, T("placing"))
  place.start(player, r.res, nil, r.res.absolute.box, nil, "absolute", { label = r.res.label, absolute = r.res.absolute,
    blueprint = r.blueprint, mode = r.res.mode, cleared = cleared })
end

--- the absolute plan's ghosts placed (place.start): what Undo takes back, outlined, said
place.after.absolute = function(player, ghosts, _, area, marks, d)
  for _, e in ipairs(d.cleared or {}) do marks[#marks + 1] = e end
  placed[player.index], unmark[player.index] = ghosts, marks
  if area then place.outline(player, area, d.label or T("placed-by-bpgen")) end
  local a = d.absolute
  local r = { blueprint = d.blueprint, res = { mode = d.mode } }
  if a.head then  -- (a new bus lane: where to feed it)
    rendering.draw_text({ text = T("feed-here", "[item=" .. a.item .. "]"), surface = player.surface, target = a.head,
      color = { 1, 0.85, 0.2 }, scale = 2, alignment = "center", use_rich_text = true, players = { player },
      time_to_live = 60 * 300 })
    rendering.draw_circle({ color = { 1, 0.85, 0.2 }, radius = 0.7, width = 4, filled = false, target = a.head,
      surface = player.surface, players = { player }, time_to_live = 60 * 300 })
    say(player, T("placed-lane", "[item=" .. a.item .. "]", #ghosts))
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    return
  end
  if r.res.mode == "busdesign" or a.fitted then
    say(player, T(a.fitted and "placed-fitted" or "placed-bus", #ghosts))
    ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
    return
  end
  say(player, T("placed-next", #ghosts, a.taps or 0, { "",
    (a.deliveries or 0) > 0 and T("placed-next-deliveries", a.deliveries) or "",
    (a.new_lanes or 0) > 0 and T("placed-next-lanes", a.new_lanes) or "" }))
  ctx.api(player, "history", { add = r.blueprint, mode = r.res.mode }, "history_add")
end

--- "Try in sandbox": the plan built in a lab of the player's (their base round it copied in, kept fed) and the player
--- taken there; an absolute plan at its box, any other at a free spot near them (as Place near me)
local function try_sandbox(player)
  local frame = player.gui.screen[NAME]
  local r = results[player.index]
  if not (frame and r and r.blueprint) then return end
  local why = sandbox.cant(player)
  if why then return say(player, T(why)) end
  local plan = { blueprint = r.blueprint, pieces = r.res.pieces }
  local a = r.res.absolute
  if a then
    local b = a.box
    plan.box, plan.clear, plan.area = b, a.clear, { b[1], b[2], b[1] + b[3], b[2] + b[4] }
  elseif r.where then
    local c = place.find_spot(player, r.w, r.h)
    if not c then return status(frame, T("no-free-spot", math.ceil(r.w), math.ceil(r.h))) end
    plan.center, plan.area = c, { c.x - r.w / 2, c.y - r.h / 2, c.x + r.w / 2, c.y + r.h / 2 }
  else
    return
  end
  sandbox.enter(player, plan)
end

--- belt_speed a belt-like entity needs to carry `per_s` items a second on both lanes
local function speed_for(per_s) return per_s / 480 end

--- name's upgrade (next_upgrade, again and again) that is at least `speed` fast, or nil
local function upgraded(name, speed)
  local p = prototypes.entity[name]
  while p and (p.belt_speed or 0) < speed - 1e-9 do p = p.next_upgrade end
  return p and p.name
end

--- an overdrawn bus lane (from the plan's absolute.upgrades) marked for upgrade: the slowest unlocked belt that
--- carries what it needs, else the fastest; bpgen's own ghosts on it swapped to that tier
function W.upgrade_lane(player, u)
  local frame = player.gui.screen[NAME]
  local belts = {}
  for _, b in ipairs(W.unlocked(player).belts) do belts[#belts + 1] = prototypes.entity[b] end
  table.sort(belts, function(a, b) return a.belt_speed < b.belt_speed end)
  local target
  for _, b in ipairs(belts) do
    if b.belt_speed >= speed_for(u.need) - 1e-9 then target = b break end
  end
  target = target or belts[#belts]
  local now = prototypes.entity[u.belt]
  if not target or (now and target.belt_speed <= now.belt_speed) then
    return status(frame, T("no-faster-belt"))
  end
  local area = u.axis == 0 and { { u.at + 0.1, u.lo + 0.1 }, { u.at + 0.9, u.hi + 0.9 } }
    or { { u.lo + 0.1, u.at + 0.1 }, { u.hi + 0.9, u.at + 0.9 } }
  local s, n = player.surface, 0
  local mine = placed[player.index] or {}
  for _, e in pairs(s.find_entities_filtered({ area = area, force = player.force,
      type = { "transport-belt", "underground-belt", "splitter", "entity-ghost" } })) do
    local ghost = e.type == "entity-ghost"
    local kind = ghost and e.ghost_type or e.type
    local to = (kind == "transport-belt" or kind == "underground-belt" or kind == "splitter")
      and upgraded(ghost and e.ghost_name or e.name, target.belt_speed)
    if to and to ~= (ghost and e.ghost_name or e.name) then
      if ghost then  -- (a ghost can't be ordered to upgrade: the same ghost, of the faster tier)
        local spec = { name = "entity-ghost", inner_name = to, position = e.position, direction = e.direction,
          force = player.force, player = player, raise_built = true }
        if kind == "underground-belt" then spec.type = e.belt_to_ground_type end
        e.destroy({ raise_destroy = true })
        local g = s.create_entity(spec)
        if g then mine[#mine + 1] = g n = n + 1 end
      elseif e.order_upgrade({ target = to, force = player.force, player = player }) then
        n = n + 1
      end
    end
  end
  placed[player.index] = mine
  say(player, T("upgrade-marked", n, "[entity=" .. target.name .. "]"))
end

function W.plan(player)
  local frame = player.gui.screen[NAME]
  if not frame then return end
  local mode = W.mode_of(frame)
  if mode == "refit" or mode == "move" then  -- (what to plan comes from the area dragged over)
    return W.click(player.index, { bpgen = mode })
  end
  local req, err = W.request(player, frame)
  if not req then return status(frame, err) end
  local ok, why = ctx.start(player, req)
  status(frame, ok and "" or T("cant-plan", tostring(why)))
  W.stale(player, false)
  if ok then
    W.on_progress(player, { fraction = 0, step = T("starting") })
    W.glow(player, true)
  end
end

--- a section of the base to move (the move tool): its blueprint (settings, wires) and where it is, to bpgen, which
--- places it near the player with its belts in and out run to the base again (see ingame.move)
--- the last plan's size in tiles (where it was placed: its box there), or nil before a plan
function W.plan_size(player)
  local r = results[player.index]
  local b = r and r.res and r.res.absolute and r.res.absolute.box
  if b then return b[3], b[4] end
  if r and type(r.w) == "number" then return r.w, r.h end
end

--- the pick-spot tool clicked (or dragged): the last plan placed with its middle there, on that side of the bus
function W.spot(player, area)
  local frame = player.gui.screen[NAME]
  if not (frame and results[player.index]) then return say(player, T("plan-first")) end
  local seed = { x = (area.left_top.x + area.right_bottom.x) / 2, y = (area.left_top.y + area.right_bottom.y) / 2 }
  tried[player.index] = nil
  local sec = find(frame, "bpgen_section")
  local ok, why = ctx.place_base(player, { bus = find(frame, "bpgen_ext_bus").state and "auto" or "off", seed = seed,
    spot = true, turn = turns[player.index], section = sec.visible and sec.items[sec.selected_index] or nil, layout = W.layout_pick(frame) })
  status(frame, ok and T("placing-clicked") or T("cant", tostring(why)))
end

function W.move(player, area)
  if not player.gui.screen[NAME] then W.open(player, { mode = "move" }) end
  local frame = player.gui.screen[NAME]
  W.set_mode(frame, "move")
  status(frame, T("reading-area"))
  -- (the area blueprinted a cell a tick: bpgen joins the cells into the one print)
  build.area_print(player.surface, player.force, area, "move_area", { area = { math.floor(area.left_top.x),
    math.floor(area.left_top.y), math.ceil(area.right_bottom.x), math.ceil(area.right_bottom.y) } }, player.index)
end

build.on_done.move_area = function(t)
  local player = game.get_player(t.player)
  local frame = player and player.gui.screen[NAME]
  if not frame then return end
  if not t.parts[1] then return status(frame, T("nothing-to-move")) end
  local ok, why = ctx.start(player, { tick = game.tick, surface = player.surface.name, params = { mode = "move",
    move = { area = t.data.area, blueprint = { parts = t.parts, wires = t.wires } },
    seed = { x = player.position.x, y = player.position.y } } })
  status(frame, ok and T("moving-section") or T("cant-plan", tostring(why)))
end

--- an area picked with the refit tool as a base on a bus (blueprinted a cell a tick): planned for the science it makes
build.on_done.refit_area = function(t)
  local player = game.get_player(t.player)
  local frame = player and player.gui.screen[NAME]
  if not frame then return end
  if not t.parts[1] then return status(frame, T("nothing-to-refit")) end
  local u = W.unlocked(player)
  local ok, why = ctx.start(player, { tick = game.tick, surface = player.surface.name,
    seed = player.surface.map_gen_settings.seed, bonuses = ctx.bonuses(player.force), inserters = u.inserters,
    belts = u.belts, poles = u.poles, belt = u.belts[#u.belts],
    params = { mode = "base", import = { parts = t.parts, wires = t.wires }, layout = "bus", belt = u.belts[#u.belts],
      productivity = 0, refit = { area = t.data.area } } })
  status(frame, ok and T("planning-refit-base") or T("cant-plan", tostring(why)))
end

--- an area picked with the refit tool: its assembling machines' recipes fill the mall's products, then it plans
--- them as a compact mall in the area's place (see ingame._refit)
function W.refit(player, area)
  if not player.gui.screen[NAME] then W.open(player, { mode = "mall" }) end
  local frame = player.gui.screen[NAME]
  if find(frame, "bpgen_refit_as").selected_index == 2 then  -- (a base on a bus: the science the area makes)
    status(frame, T("reading-area"))
    local a = area
    build.area_print(player.surface, player.force, area, "refit_area",
      { area = { a.left_top.x, a.left_top.y, a.right_bottom.x, a.right_bottom.y } }, player.index)
    return
  end
  W.set_mode(frame, "mall")
  local count, machines, order = {}, {}, {}
  for _, e in pairs(player.surface.find_entities_filtered({ area = area, type = "assembling-machine", force = player.force })) do
    local r = e.get_recipe()
    if r then
      if not count[r.name] then order[#order + 1] = r.name end
      count[r.name] = (count[r.name] or 0) + 1
      machines[e.name] = (machines[e.name] or 0) + 1
    end
  end
  if #order == 0 then return status(frame, T("no-machines-in-area")) end
  local t = find(frame, "bpgen_products")
  t.clear()
  for _, r in ipairs(order) do
    t.add({ type = "choose-elem-button", elem_type = "recipe", tags = { bpgen_product = true },
      elem_filters = { { filter = "hidden", invert = true } } }).elem_value = r
  end
  t.add({ type = "choose-elem-button", elem_type = "recipe", tags = { bpgen_product = true },
    elem_filters = { { filter = "hidden", invert = true } } })
  W.restrict(player)
  local req, err = W.request(player, frame)
  if not req then return status(frame, err) end
  -- (the area's own machine first, when it makes every product)
  local most
  for name, n in pairs(machines) do if not most or n > machines[most] then most = name end end
  local try = req.params.machines_try or {}
  for i, m in ipairs(try) do
    if m == most then table.remove(try, i) table.insert(try, 1, m) break end
  end
  req.params.machine = try[1] or req.params.machine
  local a = area
  req.params.refit = { area = { a.left_top.x, a.left_top.y, a.right_bottom.x, a.right_bottom.y },
                       add = find(frame, "bpgen_refit_add").state,
                       layout = find(frame, "bpgen_refit_layout").selected_index == 2 and "grid" or nil }
  local ok, why = ctx.start(player, req)
  status(frame, ok and T("planning-refit") or T("cant-plan", tostring(why)))
end

--- a plan's ghosts placed now: at its box next to the base when it has one, else around the player; -> how many
--- ingame.hand's answer: the plan in the player's cursor (the window out of the way), or why not
function W.on_hand(player, res)
  -- (bpgen's spot found: built there at once and tied in, the card moves it; else in the hand to put down)
  if hand.put(player, res) then
    W.close(player)
    return ui.toast(player, T("placed-at-spot"))
  end
  local ok, why = hand.give(player, res)
  local frame = player.gui.screen[NAME]
  if not ok then
    if frame then status(frame, T("cant", why)) end
    return say(player, T("cant", why))
  end
  W.close(player)
  ui.toast(player, T(res.spot and "hand-put-down-spot" or "hand-put-down"))
end

--- a paste of the handed plan tied into the base (ingame.connect's answer and every ghost of it), or why not
function W.on_connected(player, res, ghosts, marks, area)
  if res.error then
    safe.log("bpgen.ingame:connect: " .. tostring(res.error) .. (res.trace and (" | " .. res.trace) or ""))
    return say(player, T("cant", tostring(res.error)))
  end
  placed[player.index], unmark[player.index] = ghosts, marks
  if area then place.outline(player, area, T("placed-by-bpgen")) end
  local a = res.absolute or {}
  local text = T("placed-next", #ghosts, a.taps or 0, { "",
    (a.deliveries or 0) > 0 and T("placed-next-deliveries", a.deliveries) or "",
    (a.new_lanes or 0) > 0 and T("placed-next-lanes", a.new_lanes) or "" })
  say(player, text)
  hand.card(player, text)  -- (Re-route, Move, Undo)
  for _, n in ipairs(res.notes or {}) do  -- (what it couldn't tie in, and the bus's load: in the chat)
    if n:find("by hand", 1, true) or n:find("upgrade", 1, true) or n:find("runs dry", 1, true)
        or n:find("no belt", 1, true) or n:find("no pipe", 1, true) then
      player.print({ "", "[bpgen] ", n })
    end
  end
end

--- ghosts bpgen just put down (a paste being tied in): what Undo takes back from now on
function W.placing(player, ghosts)
  placed[player.index], unmark[player.index] = ghosts, {}
end

--- a plan's ghosts placed (another mod's plan_line): at its box next to the base, else around the player; then
--- place.after[then_kind] (placed: what Undo takes back)
function W.place_plan(player, res, then_kind, data)
  local box = res.absolute and res.absolute.box
  place.start(player, res, not box and player.position or nil, box, nil, then_kind, data)
end

-- (what the other parts call)
W.ghosts_left = ghosts_left
W.to_cursor = to_cursor
W.try_sandbox = try_sandbox
