-- "Try in sandbox": the window's plan built for real on a lab surface of the player's own, with the base round it
-- copied in (contents, power, what the belts carry) and kept fed from the real base while it runs. The player goes
-- there in god mode with cheat mode (everything at hand, free; ghosts and blueprints build at once, the
-- deconstruction and upgrade planners act at once) and changes what they like. The lab is on the player's own force
-- (the surface hidden from it, the lab's labs switched off so it researches nothing): changing a player's force sets
-- off other mods' handlers (Blueprint Sandboxes syncs a whole force of its own for it, a minute's freeze). "Place in base" takes them back and pastes the area over the base as ghosts (what is
-- unchanged is skipped, as any paste does; what they took away is marked for removal); "Discard" only takes them
-- back. Either way the lab goes.
--
-- The edges: a belt coming in from outside the copy gets the items its real twin gets, as they arrive (and all it
-- takes while the real one is backed up: the real base has more than it uses); a belt going out is emptied as the
-- real base would take its items; a pipe crossing the edge holds what its real twin holds. Each network gets a
-- hidden power source (bpgen-sandbox-power: not blueprinted, not on the map).

local bench = require("bench")
local M = {}

local PREFIX = "bpgen-sandbox-"
local POWER = "bpgen-sandbox-power"
local MARGIN = 32       -- the base copied round the plan, tiles each side (more is copied as the plan moves)
local ROOM = 16         -- the base kept copied round a moved plan, tiles each side
local EVERY = 5         -- ticks between the edges' feeds (an item crosses a turbo belt tile in 16)
local DRY = 400000      -- the paste's test build, this far east on the lab (an even number: rails' 2x2 grid)
local FLUID = { pipe = true, ["pipe-to-ground"] = true, ["storage-tank"] = true, pump = true }
local BELT = { ["transport-belt"] = true, ["underground-belt"] = true }

local cb = {}  -- (window.lua's: shown(player, on), card(player, on), placed(player, ghosts, marks, area, removed))
function M.setup(callbacks) cb = callbacks end

local function all()
  storage.bpgen_sandbox = storage.bpgen_sandbox or {}
  return storage.bpgen_sandbox
end
function M.state(index) return all()[index] end
function M.is_lab(surface) return surface and surface.name:sub(1, #PREFIX) == PREFIX end

local function inside(a, p) return p.x >= a[1] and p.x <= a[3] and p.y >= a[2] and p.y <= a[4] end
local function box(a) return { { a[1], a[2] }, { a[3], a[4] } } end
local function key(e)
  return string.format("%s|%s|%.1f|%.1f|%d", e.name, e.quality and e.quality.name or "normal", e.position.x,
    e.position.y, e.direction or 0)
end

--- a lab's entity kept from touching the real base: its labs research nothing (the lab is on the real force)
local function tame(e)
  if e.valid and e.type == "lab" then pcall(function() e.disabled_by_script = true end) end
end

--- the player's lab surface: made once and kept (closing a lab only clears it), so trying again needs no new surface
local function lab_surface(index)
  local name = PREFIX .. index
  local s = game.get_surface(name)
  if s then return s end
  s = game.create_surface(name, {
    default_enable_all_autoplace_controls = false,
    autoplace_settings = {
      entity = { treat_missing_as_default = false, settings = {} },
      decorative = { treat_missing_as_default = false, settings = {} },
      tile = { treat_missing_as_default = false, settings = {} },
    },
    no_enemies_mode = true,
    peaceful_mode = true,
  })
  s.always_day = true
  pcall(function() s.show_clouds = false end)
  return s
end

--- the blueprint string's print (a book: the one named `section`, else its first) in a scratch inventory
local function print_of(bp, section)
  local inv = game.create_inventory(1)
  local stack = inv[1]
  stack.set_stack({ name = "blueprint" })
  if stack.import_stack(bp) == 1 then inv.destroy() return nil end
  if stack.is_blueprint_book then
    local inner = stack.get_inventory(defines.inventory.item_main)
    local pick
    for i = 1, inner and #inner or 0 do
      local it = inner[i]
      if it.valid_for_read then
        if section and it.label == section then pick = it break end
        pick = pick or it
      end
    end
    if pick and pick.is_blueprint_book then pick = pick.get_inventory(defines.inventory.item_main)[1] end
    stack = pick
  end
  if not (stack and stack.valid_for_read and stack.is_blueprint and stack.is_blueprint_setup()) then
    inv.destroy()
    return nil
  end
  return inv, stack
end

--- ghosts built for real (modules in): what build_blueprint gave -> the entities built
local function revive(ghosts)
  local built = {}
  for _, g in pairs(ghosts) do
    if g.valid and (g.type == "entity-ghost" or g.type == "tile-ghost") then
      local plans = g.type == "entity-ghost" and g.insert_plan or nil
      local _, ent, proxy = g.silent_revive({ return_item_request_proxy = true })
      if ent then tame(ent) built[#built + 1] = ent end
      if ent and plans then bench.insert_plans(ent, plans) end
      if proxy and proxy.valid then proxy.destroy() end
    end
  end
  return built
end

--- the lab's ground round `a` = {x1, y1, x2, y2}, `pad` tiles past it: chunks made, lab tiles on the ring outside `a`
--- only (the base copied into `a` brings its own tiles)
local function ground(s, a, pad)
  local x1, y1, x2, y2 = a[1] - pad, a[2] - pad, a[3] + pad, a[4] + pad
  for cx = math.floor(x1 / 32), math.floor(x2 / 32) do
    for cy = math.floor(y1 / 32), math.floor(y2 / 32) do
      s.request_to_generate_chunks({ cx * 32 + 16, cy * 32 + 16 }, 0)
    end
  end
  s.force_generate_chunk_requests()
  local tiles = {}
  local function lay(xa, ya, xb, yb)
    for x = xa, xb - 1 do
      for y = ya, yb - 1 do
        tiles[#tiles + 1] = { name = (x + y) % 2 == 0 and "lab-dark-1" or "lab-dark-2", position = { x, y } }
      end
    end
  end
  lay(x1, y1, x2, a[2])  -- (above, below, left, right)
  lay(x1, a[4], x2, y2)
  lay(x1, a[2], a[1], a[4])
  lay(a[3], a[2], x2, a[4])
  s.set_tiles(tiles, true, false, false, false)
end

--- the plan (st.plan: its print, where, which way) built for real on the lab: st.plan_ents, st.plan_box
local function build_plan(st, s)
  local p = st.plan
  st.plan_ents, st.plan_box = {}, nil
  local inv, stack = print_of(p.blueprint, p.section)
  if not inv then return end
  -- (an absolute plan superforced: its taps replace the belts they cut into)
  local ghosts = stack.build_blueprint({ surface = s, force = game.forces[st.force], position = p.at, direction = p.dir,
    build_mode = p.superforced and defines.build_mode.superforced or defines.build_mode.forced, skip_fog_of_war = false })
  inv.destroy()
  for _, e in pairs(s.find_entities_filtered({ area = box(st.area), to_be_deconstructed = true })) do
    if e.valid then e.destroy() end
  end
  st.plan_ents = revive(ghosts)
  local b
  for _, e in pairs(st.plan_ents) do
    local bb = e.valid and e.bounding_box
    if bb then
      b = b or { bb.left_top.x, bb.left_top.y, bb.right_bottom.x, bb.right_bottom.y }
      b = { math.min(b[1], bb.left_top.x), math.min(b[2], bb.left_top.y), math.max(b[3], bb.right_bottom.x),
        math.max(b[4], bb.right_bottom.y) }
    end
  end
  st.plan_box = b and { math.floor(b[1]), math.floor(b[2]), math.ceil(b[3]), math.ceil(b[4]) } or nil
end

-- the edges ---------------------------------------------------------------------------------------------------

local function ids(e)
  local t = {}
  for i = 1, e.get_max_transport_line_index() do
    for _, it in pairs(e.get_transport_line(i).get_detailed_contents()) do t[it.unique_id] = i end
  end
  return t
end

--- the copy's edges: belts in and out, pipes across, and power for each network
local function edges(st)
  local s, real, a = game.surfaces[st.surface], game.surfaces[st.home_surface], st.area
  local function outside(e) return e and e.valid and not inside(a, e.position) end
  st.feeds, st.drains, st.pipes = {}, {}, {}
  for _, l in pairs(s.find_entities_filtered({ area = box(a), type = { "transport-belt", "underground-belt" } })) do
    local r = real.find_entities_filtered({ position = l.position, name = l.name, limit = 1 })[1]
    if r and inside(a, l.position) then
      local n = r.belt_neighbours
      local into, out = false, false
      for _, x in pairs(n.inputs) do into = into or outside(x) end
      for _, x in pairs(n.outputs) do out = out or outside(x) end
      if r.type == "underground-belt" and outside(r.neighbours) then
        if r.belt_to_ground_type == "output" then into = true else out = true end
      end
      if into then st.feeds[#st.feeds + 1] = { real = r, lab = l, seen = ids(r) } end
      if out then st.drains[#st.drains + 1] = l end
    end
  end
  for _, l in pairs(s.find_entities_filtered({ area = box(a), type = { "pipe", "pipe-to-ground", "storage-tank", "pump" } })) do
    local r = real.find_entities_filtered({ position = l.position, name = l.name, limit = 1 })[1]
    if r and inside(a, l.position) then
      for i = 1, #r.fluidbox do
        for _, c in pairs(r.fluidbox.get_connections(i)) do
          if outside(c.owner) then st.pipes[#st.pipes + 1] = { real = r, lab = l, i = i } break end
        end
      end
    end
  end
  M.power(st)
end

--- a hidden power source on each electric network of the lab (one more for networks a change made)
function M.power(st)
  local s = game.surfaces[st.surface]
  local force = game.forces[st.force]
  local powered = {}
  for _, p in pairs(s.find_entities_filtered({ name = POWER })) do
    local pole = s.find_entities_filtered({ position = p.position, type = "electric-pole", limit = 1 })[1]
    if pole and pole.electric_network_id then powered[pole.electric_network_id] = true end
  end
  for _, pole in pairs(s.find_entities_filtered({ type = "electric-pole", force = force })) do
    local id = pole.electric_network_id
    if id and not powered[id] then
      powered[id] = true
      local e = s.create_entity({ name = POWER, position = pole.position, force = force })
      if e then
        e.power_production = 1e12 / 60
        e.electric_buffer_size = 1e12
        e.destructible, e.minable = false, false
      end
    end
  end
end

--- the edges fed from the real base (every EVERY ticks)
local function feed(st)
  for _, f in pairs(st.feeds) do
    if f.real.valid and f.lab.valid then
      local now, new = ids(f.real), {}
      for i = 1, f.real.get_max_transport_line_index() do
        for _, it in pairs(f.real.get_transport_line(i).get_detailed_contents()) do
          if not f.seen[it.unique_id] then
            local li = (i - 1) % 2 + 1
            new[li] = new[li] or {}
            new[li][#new[li] + 1] = { name = it.stack.name, quality = it.stack.quality.name }
          end
        end
      end
      f.seen = now
      for li = 1, 2 do
        local line = f.lab.get_transport_line(li)
        local list = new[li] or {}
        -- (the real belt backed up: its base has more than it takes, so the lab gets all it takes)
        local rl = f.real.get_transport_line(li)
        if #list == 0 and #rl >= 4 then
          local top, n = nil, 0
          for _, c in pairs(rl.get_contents()) do if c.count > n then top, n = c, c.count end end
          if top then list = { { name = top.name, quality = top.quality }, { name = top.name, quality = top.quality } } end
        end
        for _, it in ipairs(list) do
          if not line.can_insert_at_back() then break end
          line.insert_at_back({ name = it.name, quality = it.quality, count = 1 })
        end
      end
    end
  end
  for _, l in pairs(st.drains) do
    if l.valid then
      for i = 1, l.get_max_transport_line_index() do l.get_transport_line(i).clear() end
    end
  end
  for _, p in pairs(st.pipes) do
    if p.real.valid and p.lab.valid then
      local fl = p.real.fluidbox[p.i]
      if fl then p.lab.fluidbox[p.i] = fl end
    end
  end
end

-- the lab -------------------------------------------------------------------------------------------------------

--- the base round `area` ({x1, y1, x2, y2}, the plan's) copied to a new lab, the plan built in it: the lab's state.
--- `plan` = {blueprint, section, box = {x, y, w, h} (an absolute plan) | center = {x, y}, clear = {{name, position}}}
--- `rect` of the real base copied onto the lab (tiles and entities), cleaned up (no characters, enemies, turrets, and
--- no ghosts: the base as built; Place by my base leaves the plan itself there as ghosts, and the lab builds it anew;
--- labs tamed; the plan's st.clear gone), its entities added to st.originals
local function copy_in(st, s, rect)
  local real, force = game.surfaces[st.home_surface], game.forces[st.force]
  for _, e in pairs(s.find_entities_filtered({ area = box(rect) })) do
    if e.valid and not inside(st.area, e.position) then e.destroy() end
  end
  real.clone_area({ source_area = box(rect), destination_area = box(rect), destination_surface = s,
    clone_tiles = true, clone_entities = true, clone_decoratives = false,
    clear_destination_entities = false, expand_map = true, create_build_effect_smoke = false })
  for _, e in pairs(s.find_entities_filtered({ area = box(rect), type = { "character", "unit", "unit-spawner", "turret",
      "character-corpse", "item-request-proxy", "entity-ghost", "tile-ghost" } })) do
    if e.valid then e.destroy() end
  end
  for _, e in pairs(s.find_entities_filtered({ area = box(rect), type = "lab" })) do tame(e) end
  for _, c in ipairs(st.clear or {}) do  -- (a refit's, a move's: the old part goes)
    if inside(rect, c.position) then
      for _, e in pairs(s.find_entities_filtered({ name = c.name, position = c.position })) do e.destroy() end
    end
  end
  for _, e in pairs(s.find_entities_filtered({ area = box(rect), force = force })) do
    if e.type ~= "entity-ghost" and e.type ~= "tile-ghost" then st.originals[key(e)] = true end
  end
end

--- the base copied out to `want` ({x1, y1, x2, y2}, the area grows to cover it): only the strips not copied yet
local function grow(st, s, want)
  local a = st.area
  local b = { math.min(a[1], math.floor(want[1])), math.min(a[2], math.floor(want[2])), math.max(a[3], math.ceil(want[3])),
    math.max(a[4], math.ceil(want[4])) }
  if b[1] == a[1] and b[2] == a[2] and b[3] == a[3] and b[4] == a[4] then return end
  ground(s, b, 8)
  local strips = {}
  if b[2] < a[2] then strips[#strips + 1] = { b[1], b[2], b[3], a[2] } end  -- (above, below, left, right)
  if b[4] > a[4] then strips[#strips + 1] = { b[1], a[4], b[3], b[4] } end
  if b[1] < a[1] then strips[#strips + 1] = { b[1], a[2], a[1], a[4] } end
  if b[3] > a[3] then strips[#strips + 1] = { a[3], a[2], b[3], a[4] } end
  for _, r in ipairs(strips) do copy_in(st, s, r) end
  st.area = b
  game.forces[st.force].chart(s, box(b))
end

--- the base round `area` ({x1, y1, x2, y2}, the plan's) copied to the player's lab, the plan built in it: the lab's state.
--- `plan` = {blueprint, section, box = {x, y, w, h} (an absolute plan) | center = {x, y}, clear = {{name, position}}}
function M.open(index, home_surface, home_force, plan)
  M.close(index)
  local a = plan.area
  a = { math.floor(a[1] - MARGIN), math.floor(a[2] - MARGIN), math.ceil(a[3] + MARGIN), math.ceil(a[4] + MARGIN) }
  local s = lab_surface(index)
  local force = home_force
  pcall(home_force.set_surface_hidden, s, true)
  ground(s, a, 8)
  local st = { surface = s.name, force = force.name, home_surface = home_surface.name, home_force = home_force.name,
    area = { 0, 0, 0, 0 }, originals = {}, clear = plan.clear }
  for _, e in pairs(s.find_entities_filtered({ area = box(a) })) do e.destroy() end
  copy_in(st, s, a)
  st.area = a
  -- the plan, built for real (an absolute one at its box, any other at its center); the card's arrows move it
  local b = plan.box
  st.plan = { blueprint = plan.blueprint, section = plan.section, superforced = b ~= nil, dir = defines.direction.north,
    at = b and { x = b[1] + b[3] / 2, y = b[2] + b[4] / 2 } or plan.center }
  build_plan(st, s)
  edges(st)
  force.chart(s, box(a))
  all()[index] = st
  return st
end

--- what changed in the lab: the area as a blueprint pasted onto the base (ghosts; unchanged entities skip), the
--- originals no longer there marked for removal. -> ghosts, marks (what the paste marked), removed
function M.paste(st, by_player)
  local s, real = game.surfaces[st.surface], game.surfaces[st.home_surface]
  local home = game.forces[st.home_force]
  local a = st.area
  local now, removed = {}, {}
  for _, e in pairs(s.find_entities_filtered({ area = box(a), force = st.force })) do
    if e.name ~= POWER and e.type ~= "entity-ghost" and e.type ~= "tile-ghost" then now[key(e)] = true end
  end
  for k in pairs(st.originals) do
    if not now[k] then
      local name, quality, x, y = k:match("^(.-)|(.-)|(.-)|(.-)|")
      for _, e in pairs(real.find_entities_filtered({ name = name, position = { tonumber(x), tonumber(y) },
          force = home, quality = quality })) do
        if not e.to_be_deconstructed() and e.order_deconstruction(home, by_player) then removed[#removed + 1] = e end
      end
    end
  end
  local inv = game.create_inventory(1)
  local stack = inv[1]
  stack.set_stack({ name = "blueprint" })
  local map = stack.create_blueprint({ surface = s, force = st.force, area = box(a), include_modules = true,
    always_include_tiles = true,
    include_station_names = true, include_trains = false, include_fuel = false })
  if not stack.is_blueprint_setup() then inv.destroy() return {}, {}, removed end
  -- (where the paste lands: a test build far off on the lab, lined up with what it was made of: the same entities,
  -- so their leftmost and topmost positions match; the game centres a paste on the print's own extent, which is
  -- not the area's once the lab grew to one side)
  local cx, cy = (a[1] + a[3]) / 2, (a[2] + a[4]) / 2
  local mx, my
  for _, e in pairs(map) do
    if e.valid then
      mx, my = math.min(mx or e.position.x, e.position.x), math.min(my or e.position.y, e.position.y)
    end
  end
  bench.ground(s, a[1] + DRY - 8, a[2] - 8, a[3] + DRY + 8, a[4] + 8)
  local dry = stack.build_blueprint({ surface = s, force = st.force, position = { cx + DRY, cy },
    build_mode = defines.build_mode.forced })
  local gx, gy
  for _, g in pairs(dry) do
    if g.valid and g.type == "entity-ghost" then
      gx, gy = math.min(gx or g.position.x, g.position.x), math.min(gy or g.position.y, g.position.y)
    end
  end
  local best = mx and gx and { mx - (gx - DRY), my - gy }
  for _, g in pairs(dry) do if g.valid then g.destroy() end end
  if best then cx, cy = cx + best[1], cy + best[2] end
  local before = {}
  for _, e in pairs(real.find_entities_filtered({ area = box(a), to_be_deconstructed = true })) do
    if e.unit_number then before[e.unit_number] = true end
  end
  local ghosts = stack.build_blueprint({ surface = real, force = home, position = { cx, cy },
    build_mode = defines.build_mode.superforced, skip_fog_of_war = false, raise_built = true, by_player = by_player })
  inv.destroy()
  local marks = {}
  for _, e in pairs(real.find_entities_filtered({ area = box(a), to_be_deconstructed = true })) do
    if e.unit_number and not before[e.unit_number] then marks[#marks + 1] = e end
  end
  return ghosts, marks, removed
end

--- the plan in the lab moved (dx, dy tiles) and/or turned a quarter clockwise: the base where it stood put back as
--- the real base has it, the plan built again there (changes made to the plan itself in the lab go with it)
--- -> true, or false and why (a locale key)
function M.move(index, dx, dy, turn)
  local st = all()[index]
  local s = st and game.surfaces[st.surface]
  local real = st and game.surfaces[st.home_surface]
  if not (s and real and st.plan and st.plan_box) then return false, "sandbox-cant-move" end
  local p, pb = st.plan, st.plan_box
  local at = { x = p.at.x + dx, y = p.at.y + dy }
  -- where it will stand (a quarter turn clockwise about its middle), and the base round that copied in if it isn't yet
  local nb = { pb[1] + dx, pb[2] + dy, pb[3] + dx, pb[4] + dy }
  if turn then
    nb = { at.x - (pb[4] - p.at.y), at.y + (pb[1] - p.at.x), at.x - (pb[2] - p.at.y), at.y + (pb[3] - p.at.x) }
  end
  grow(st, s, { nb[1] - ROOM, nb[2] - ROOM, nb[3] + ROOM, nb[4] + ROOM })
  for _, e in pairs(st.plan_ents or {}) do if e.valid then e.destroy() end end
  -- the base under it as it was: its tiles, and each of the base's own entities missing there, cloned again
  local look = box({ pb[1] - 1, pb[2] - 1, pb[3] + 1, pb[4] + 1 })
  real.clone_area({ source_area = look, destination_area = look, destination_surface = s, clone_tiles = true,
    clone_entities = false, clone_decoratives = false, clear_destination_entities = false,
    clear_destination_decoratives = false, expand_map = false, create_build_effect_smoke = false })
  local here = {}
  for _, e in pairs(s.find_entities_filtered({ area = look })) do if e.valid then here[key(e)] = true end end
  for _, e in pairs(real.find_entities_filtered({ area = look, force = st.home_force })) do
    local k = key(e)
    if st.originals[k] and not here[k] then
      local c = e.clone({ position = e.position, surface = s, force = st.force, create_build_effect_smoke = false })
      if c then tame(c) here[k] = true end
    end
  end
  p.at = at
  if turn then p.dir = (p.dir + 4) % 16 end
  build_plan(st, s)
  edges(st)
  return true
end

--- the lab cleared and forgotten (its surface kept for the next one)
function M.close(index)
  local st = all()[index]
  all()[index] = nil
  local s = st and game.surfaces[st.surface]
  if not s then return end
  if s.name ~= PREFIX .. index then game.delete_surface(s) return end  -- (a lab from before 0.25.1: one per try)
  local a = st.area
  for _, e in pairs(s.find_entities_filtered({ area = { { a[1] - 40, a[2] - 40 }, { a[3] + 40, a[4] + 40 } } })) do
    if e.valid then e.destroy() end
  end
end

-- the player ----------------------------------------------------------------------------------------------------

--- why `player` can't go to a sandbox now, or nil
function M.cant(player)
  if all()[player.index] then return "in-sandbox" end
  if player.controller_type ~= defines.controllers.character or not player.character then return "sandbox-character" end
  if player.surface.platform then return "sandbox-platform" end
end

--- the plan built in a lab and the player taken there
function M.enter(player, plan)
  local why = M.cant(player)
  if why then return false, why end
  local home_surface, home_force = player.surface, player.force
  local st = M.open(player.index, home_surface, home_force, plan)
  local character = player.character
  st.character, st.position = character, { x = player.position.x, y = player.position.y }
  st.surface_list = player.game_view_settings.show_surface_list
  if character.walking_state.walking then
    character.walking_state = { walking = false, direction = character.walking_state.direction }
  end
  player.clear_cursor()
  player.set_controller({ type = defines.controllers.god })
  if character.valid and not character.associated_player then player.associate_character(character) end
  local a = plan.area
  player.teleport({ (a[1] + a[3]) / 2, (a[2] + a[4]) / 2 }, game.surfaces[st.surface])
  player.cheat_mode = true
  player.game_view_settings.show_surface_list = false  -- (no remote view to the real base with cheat mode on)
  if cb.shown then cb.shown(player, false) end
  if cb.card then cb.card(player, true) end
  return true
end

--- the player back in their character on the real base (the lab's god inventory and cursor left behind)
local function leave(player, st)
  player.clear_cursor()
  local main = player.get_main_inventory()
  if main then main.clear() end
  player.cheat_mode = false
  if player.force.name ~= st.home_force then player.force = st.home_force end  -- (a lab from before 0.24.2)
  player.game_view_settings.show_surface_list = st.surface_list ~= false
  local character = st.character
  if character and character.valid then
    player.teleport(character.position, character.surface)
    player.set_controller({ type = defines.controllers.character, character = character })
  else  -- (their character died meanwhile: a new one where they left)
    player.teleport(st.position, st.home_surface)
    player.set_controller({ type = defines.controllers.god })
    player.create_character()
  end
  if cb.card then cb.card(player, false) end
  if cb.shown then cb.shown(player, true) end
end

--- "Place in base": back, the lab's version pasted as ghosts, the lab gone
function M.place(player)
  local st = all()[player.index]
  if not st then return end
  leave(player, st)
  local ghosts, marks, removed = M.paste(st, player)
  M.close(player.index)
  if cb.placed then cb.placed(player, ghosts, marks, st.area, removed) end
end

--- "Discard": back, the lab gone
function M.discard(player)
  local st = all()[player.index]
  if not st then return end
  leave(player, st)
  M.close(player.index)
end

-- events --------------------------------------------------------------------------------------------------------

local function in_lab(e) return e and e.valid and M.is_lab(e.surface) end

M.handlers = {
  [defines.events.on_tick] = function(e)
    if e.tick % EVERY ~= 0 or not storage.bpgen_sandbox then return end
    for _, st in pairs(storage.bpgen_sandbox) do
      if game.surfaces[st.surface] then
        feed(st)
        if e.tick % 60 == 0 then M.power(st) end
      end
    end
  end,
  -- (god mode builds at once: a ghost (shift-placed, a blueprint) built for real)
  [defines.events.on_built_entity] = function(e)
    if not in_lab(e.entity) then return end
    if e.entity.type == "entity-ghost" or e.entity.type == "tile-ghost" then revive({ e.entity }) else tame(e.entity) end
  end,
  [defines.events.on_marked_for_deconstruction] = function(e)
    local ent = e.entity
    if not in_lab(ent) or not e.player_index then return end
    if ent.name == POWER then return ent.cancel_deconstruction(ent.force) end
    ent.destroy({ raise_destroy = true })
  end,
  [defines.events.on_marked_for_upgrade] = function(e)
    local ent = e.entity
    if not in_lab(ent) or not e.player_index then return end
    local p = { name = e.target.name, position = ent.position, direction = ent.direction, force = ent.force,
      quality = e.quality, fast_replace = true, spill = false, raise_built = true }
    if ent.type == "underground-belt" then p.type = ent.belt_to_ground_type end
    if ent.type == "loader" or ent.type == "loader-1x1" then p.type = ent.loader_type end
    ent.surface.create_entity(p)
  end,
}

return M
