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
--
-- Every step is spread over ticks (a lab task in storage, its steps one after another: the lab of a 200x200 base
-- was a 600-1000 ms tick each way): the lab's ground made, the base copied in cell by cell (the wires between cells
-- joined again after), the plan built from its pieces (build.lua), the edges found; and back: what changed found
-- cell by cell, pasted as blueprints of those cells only, their wires joined, the lab cleared.

local bench = require("bench")
local tasks = require("tasks")
local build = require("build")
local M = {}

local PREFIX = "bpgen-sandbox-"
local POWER = "bpgen-sandbox-power"
local MARGIN = 32       -- the base copied round the plan, tiles each side (more is copied as the plan moves)
local ROOM = 16         -- the base kept copied round a moved plan, tiles each side
local EVERY = 5         -- ticks between the edges' feeds (an item crosses a turbo belt tile in 16)
local DRY = 400000      -- the paste's test build, this far east on the lab (an even number: rails' 2x2 grid)
local FLUID = { pipe = true, ["pipe-to-ground"] = true, ["storage-tank"] = true, pump = true }
local BELT = { ["transport-belt"] = true, ["underground-belt"] = true }
local CELL = 16         -- (the base copied, compared and pasted back in cells of this many tiles)
-- units of work (tasks.lua: ~4 us each)
local U_CLONE, U_ENTITY, U_DESTROY, U_TILE, U_CELL, U_WIRE, U_EDGE = 3, 0.5, 2, 0.05, 5, 3, 5
local U_KEY = 1.5  -- (an entity's key: its name, quality, place and direction as text)

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
build.on_revived.lab = tame

--- the player's lab surface: made once and kept (closing a lab only clears it), so trying again needs no new surface
local function lab_surface(index)
  local name = PREFIX .. index
  local s = game.get_surface(name)
  if s then
    if not s.generate_with_lab_tiles then s.generate_with_lab_tiles = true end  -- (labs before 0.29)
    return s
  end
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
  s.generate_with_lab_tiles = true  -- (new chunks lab ground: the ring round the copy has little left to lay)
  pcall(function() s.show_clouds = false end)
  return s
end

--- cells of CELL tiles covering rect {x1, y1, x2, y2} -> {{x1, y1, x2, y2}}, row by row
local function cells_of(rect)
  local out = {}
  for y = rect[2], rect[4] - 1, CELL do
    for x = rect[1], rect[3] - 1, CELL do
      out[#out + 1] = { x, y, math.min(x + CELL, rect[3]), math.min(y + CELL, rect[4]) }
    end
  end
  return out
end
--- which of cells_of(rect) holds position p (clamped into it)
local function cell_index(rect, p)
  local nx = math.ceil((rect[3] - rect[1]) / CELL)
  local ny = math.ceil((rect[4] - rect[2]) / CELL)
  local cx = math.min(math.max(math.floor((p.x - rect[1]) / CELL), 0), nx - 1)
  local cy = math.min(math.max(math.floor((p.y - rect[2]) / CELL), 0), ny - 1)
  return cy * nx + cx + 1
end

-- the plan's print, built at once (a blueprint without pieces: tests, an answer from before 0.29) ----------------

--- the blueprint string's print (a book: the one named `section`, else its first) in a scratch inventory; not
--- snapped to the grid unless `snapped`
local function print_of(bp, section, snapped)
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
  -- (snapped to the grid, every build at a spot near the first lands on it again: the lab's plan never moved; the
  -- lab builds it free, from the spot calibrate found)
  if not snapped and stack.blueprint_snap_to_grid then stack.blueprint_snap_to_grid = nil end
  return inv, stack
end

--- the lowest entity-ghost position `stack` builds at `at` (then gone)
local function lowest(s, force, stack, at, dir)
  local x, y
  for _, g in pairs(stack.build_blueprint({ surface = s, force = force, position = at, direction = dir,
      build_mode = defines.build_mode.forced })) do
    if g.valid then
      if g.type == "entity-ghost" then
        x, y = math.min(x or g.position.x, g.position.x), math.min(y or g.position.y, g.position.y)
      end
      g.destroy()
    end
  end
  return x, y
end

--- ghosts built for real (modules in): what build_blueprint gave -> the entities built
local function revive(ghosts)
  local built = {}
  for _, g in pairs(ghosts) do
    if g.valid and (g.type == "entity-ghost" or g.type == "tile-ghost") then
      local plans = g.type == "entity-ghost" and g.insert_plan or nil
      local _, ent, proxy = g.silent_revive({ return_item_request_proxy = true,
        raise_revive = script.active_mods["power-propagation"] ~= nil })  -- (its hidden poles power the plan)
      if ent then tame(ent) built[#built + 1] = ent end
      if ent and plans then bench.insert_plans(ent, plans) end
      if proxy and proxy.valid then proxy.destroy() end
    end
  end
  return built
end

local function plan_box_of(ents)
  local b
  for _, e in pairs(ents) do
    local bb = e.valid and e.bounding_box
    if bb then
      b = b or { bb.left_top.x, bb.left_top.y, bb.right_bottom.x, bb.right_bottom.y }
      b = { math.min(b[1], bb.left_top.x), math.min(b[2], bb.left_top.y), math.max(b[3], bb.right_bottom.x),
        math.max(b[4], bb.right_bottom.y) }
    end
  end
  return b and { math.floor(b[1]), math.floor(b[2]), math.ceil(b[3]), math.ceil(b[4]) } or nil
end

--- the plan (st.plan: its print, where, which way) built for real on the lab at once: st.plan_ents, st.plan_box
local function build_plan_now(st, s, marks)
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
  st.plan_box = plan_box_of(st.plan_ents)
end

--- an absolute plan's print snaps to the grid: the spot that builds it free where it lands snapped (st.plan.at)
local function calibrate(st, s)
  local p = st.plan
  local inv, stack = print_of(p.blueprint, p.section, true)
  if not inv then return end
  local snap = stack.blueprint_snap_to_grid
  if not snap then inv.destroy() return end
  -- (a whole number of grid cells off: the snapped print lands exactly that far from where it lands on the base)
  local off = math.ceil(DRY / snap.x) * snap.x
  if off % 2 == 1 then off = off + snap.x end  -- (rails: an even shift)
  local a = st.area
  bench.ground(s, a[1] + off - 8, a[2] - 8, a[3] + off + 8, a[4] + 8)
  local force = game.forces[st.force]
  local at = { x = p.at.x + off, y = p.at.y }
  local x1, y1 = lowest(s, force, stack, at, p.dir)
  stack.blueprint_snap_to_grid = nil
  local x2, y2 = lowest(s, force, stack, at, p.dir)
  inv.destroy()
  if x1 and x2 then p.at = { x = p.at.x + (x1 - x2), y = p.at.y + (y1 - y2) } end
end

--- the plan already built on the real base (Place by my base, then bots or an instant-build mod) came into the lab
--- with the copy: those entities taken out, so the plan built anew is the lab's own and moves. They stay in
--- st.originals (moved away, the paste back marks them for removal) and in st.plan_real (a move doesn't clone them
--- back where the plan stood). `stack`: a print to build (dry, DRY tiles east) at `at` turned `dir` as `how`
local function take_out(st, s, stack, at, dir)
  local a = st.area
  bench.ground(s, a[1] + DRY - 8, a[2] - 8, a[3] + DRY + 8, a[4] + 8)
  local dry = stack.build_blueprint({ surface = s, force = game.forces[st.force], position = at, direction = dir,
    build_mode = defines.build_mode.forced })
  local n = 0
  for _, g in pairs(dry) do
    if g.valid and g.type == "entity-ghost" then
      local pos = { g.position.x - DRY, g.position.y }
      for _, e in pairs(s.find_entities_filtered({ name = g.ghost_name, position = pos, force = st.force })) do
        if e.direction == g.direction and (e.quality and e.quality.name) == (g.quality and g.quality.name) then
          st.plan_real[key(e)] = true
          e.destroy()
        end
      end
    end
    if g.valid then g.destroy() end
    n = n + 1
  end
  return n
end

-- the edges ---------------------------------------------------------------------------------------------------

local function ids(e)
  local t = {}
  for i = 1, e.get_max_transport_line_index() do
    for _, it in pairs(e.get_transport_line(i).get_detailed_contents()) do t[it.unique_id] = i end
  end
  return t
end

--- the copy's edges in one cell of the area: belts in and out, pipes across -> units used
local function edges_in(st, cell)
  local s, real, a = game.surfaces[st.surface], game.surfaces[st.home_surface], st.area
  local function outside(e) return e and e.valid and not inside(a, e.position) end
  local used = U_CELL
  for _, l in pairs(s.find_entities_filtered({ area = box(cell), type = { "transport-belt", "underground-belt" } })) do
    if inside(cell, l.position) then  -- (a belt on a cell's edge: in one cell)
      used = used + U_EDGE
      local r = real.find_entities_filtered({ position = l.position, name = l.name, limit = 1 })[1]
      if r and inside(a, l.position) then
        local n = r.belt_neighbours
        local into, out = false, false
        for _, x in pairs(n.inputs) do into = into or outside(x) end
        for _, x in pairs(n.outputs) do out = out or outside(x) end
        if r.type == "underground-belt" and outside(r.underground_belt_neighbour) then
          if r.belt_to_ground_type == "output" then into = true else out = true end
        end
        if into then st.feeds[#st.feeds + 1] = { real = r, lab = l, seen = ids(r) } end
        if out then st.drains[#st.drains + 1] = l end
      end
    end
  end
  for _, l in pairs(s.find_entities_filtered({ area = box(cell), type = { "pipe", "pipe-to-ground", "storage-tank", "pump" } })) do
    if inside(cell, l.position) then
      used = used + U_EDGE
      local r = real.find_entities_filtered({ position = l.position, name = l.name, limit = 1 })[1]
      if r and inside(a, l.position) then
        for i, list in pairs(r.fluidbox_neighbours) do
          for _, o in pairs(list) do
            if outside(o) then st.pipes[#st.pipes + 1] = { real = r, lab = l, i = i } break end
          end
        end
      end
    end
  end
  return used
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
        e.destructible, e.minable_flag = false, false
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
      local fl = p.real.get_fluid(p.i)
      if fl then p.lab.set_fluid(p.i, fl) end
    end
  end
end

-- the lab's steps: each a function(t, st, s, budget) -> units used, done -------------------------------------------

local STEP = {}
local CLEAN = { "character", "unit", "unit-spawner", "turret", "character-corpse", "item-request-proxy", "entity-ghost",
  "tile-ghost" }

--- wires of `e` (real) to entities in another cell of t.rect (inside it), once each -> onto t.cut
local function cut_wires(t, e, ci)
  local ok, cons = pcall(e.get_wire_connectors, false)
  if not ok or not cons then return 0 end
  local n = 0
  for id, c in pairs(cons) do
    for _, link in ipairs(c.connections) do
      local o = link.target.owner
      if o.valid then
        local mine = inside(t.rect, o.position)
        -- (to a later cell: once, from the earlier; to what the lab has already, round a strip it grows by)
        if mine and cell_index(t.rect, o.position) > ci or not mine and t.copied and inside(t.copied, o.position) then
          t.cut[#t.cut + 1] = { e, id, o, link.target.wire_connector_id }
          n = n + 1
        end
      end
    end
  end
  return n
end

--- the lab's ground round t.rect, t.pad tiles past it: chunks the game makes (waited for), lab tiles on the ring
--- outside t.rect only (the base copied into it brings its own tiles)
STEP.ground = function(t, st, s, budget)
  local a, pad = t.rect, t.pad or 8
  local x1, y1, x2, y2 = a[1] - pad, a[2] - pad, a[3] + pad, a[4] + pad
  if not t.chunks then
    t.chunks = {}
    for cx = math.floor(x1 / 32), math.floor(x2 / 32) do
      for cy = math.floor(y1 / 32), math.floor(y2 / 32) do
        if not s.is_chunk_generated({ cx, cy }) then
          s.request_to_generate_chunks({ cx * 32 + 16, cy * 32 + 16 }, 0)
          t.chunks[#t.chunks + 1] = { cx, cy }
        end
      end
    end
    t.ring = { { x1, y1, x2, a[2] }, { x1, a[4], x2, y2 }, { x1, a[2], a[1], a[4] }, { a[3], a[2], x2, a[4] } }
    t.ri, t.sy = 1, nil
    return 10, false
  end
  for i = #t.chunks, 1, -1 do
    if s.is_chunk_generated(t.chunks[i]) then table.remove(t.chunks, i) end
  end
  if t.chunks[1] then return 1, false end
  local used = 0
  while t.ring[t.ri] and used < budget do  -- (lab tiles on the ring, a few rows a tick)
    local r = t.ring[t.ri]
    t.sy = t.sy or r[2]
    local tiles = {}
    local y2r = math.min(r[4], t.sy + 16)
    for x = r[1], r[3] - 1 do
      for y = t.sy, y2r - 1 do
        tiles[#tiles + 1] = { name = (x + y) % 2 == 0 and "lab-dark-1" or "lab-dark-2", position = { x, y } }
      end
    end
    if tiles[1] then s.set_tiles(tiles, true, false, false, false) end
    used = used + 5 + #tiles * U_TILE * 4
    t.sy = y2r
    if t.sy >= r[4] then t.ri, t.sy = t.ri + 1, nil end
  end
  if t.ring[t.ri] then return used, false end
  t.chunks, t.ring = nil, nil
  return used, true
end

--- what's on the lab in t.clear (an area) destroyed, a cell at a time
STEP.clear = function(t, st, s, budget)
  if not t.dcells then t.dcells, t.dci = cells_of(t.clear), 1 end
  local used = 0
  while t.dcells[t.dci] and used < budget do
    local c = t.dcells[t.dci]
    for _, e in pairs(s.find_entities_filtered({ area = box(c) })) do
      if e.valid and e.type ~= "character" then
        e.destroy()
        used = used + U_DESTROY
      end
    end
    t.dci, used = t.dci + 1, used + U_CELL
  end
  if t.dcells[t.dci] then return used, false end
  t.dcells = nil
  return used, true
end

--- the ground of the plan's test builds (DRY tiles east): made, as STEP.ground
STEP.dryground = function(t, st, s, budget)
  if not (st.plan and st.plan.print) then return 0, true end
  local a = st.area
  t.rect, t.pad = { a[1] + DRY, a[2], a[3] + DRY, a[4] }, 8
  local used, done = STEP.ground(t, st, s, budget)
  if done then t.rect = a end
  return used, done
end

--- the real base in t.rect copied onto the lab, a cell at a time (tiles and entities), cleaned up (no characters,
--- enemies, turrets, and no ghosts: the base as built; Place by my base leaves the plan itself there as ghosts, and
--- the lab builds it anew; labs tamed; the plan's st.clear gone), its entities added to st.originals; the wires
--- between cells kept in t.cut (joined in STEP.wires)
STEP.copy = function(t, st, s, budget)
  local real, force = game.surfaces[st.home_surface], game.forces[st.force]
  t.cells, t.cut, t.ci = t.cells or cells_of(t.rect), t.cut or {}, t.ci or 1
  local used = 0
  while t.cells[t.ci] and used < budget do
    local c = t.cells[t.ci]
    local cb_ = box(c)
    local src = real.find_entities_filtered({ area = cb_ })
    used = used + U_CELL + #src * U_CLONE
    for _, e in pairs(s.find_entities_filtered({ area = cb_ })) do  -- (the lab's own, outside what's kept)
      if e.valid and not inside(st.area, e.position) and inside(c, e.position) then e.destroy() end
    end
    real.clone_area({ source_area = cb_, destination_area = cb_, destination_surface = s,
      clone_tiles = true, clone_entities = true, clone_decoratives = false,
      clear_destination_entities = false, expand_map = true, create_build_effect_smoke = false })
    for _, e in pairs(src) do
      if e.valid and inside(c, e.position) then used = used + cut_wires(t, e, t.ci) end
    end
    for _, e in pairs(s.find_entities_filtered({ area = cb_, type = CLEAN })) do
      if e.valid then e.destroy() end
    end
    for _, e in pairs(s.find_entities_filtered({ area = cb_, type = "lab" })) do tame(e) end
    for _, x in ipairs(st.clear or {}) do  -- (a refit's, a move's: the old part goes)
      if inside(c, x.position) then
        for _, e in pairs(s.find_entities_filtered({ name = x.name, position = x.position })) do e.destroy() end
      end
    end
    for _, e in pairs(s.find_entities_filtered({ area = cb_, force = force })) do
      if e.type ~= "entity-ghost" and e.type ~= "tile-ghost" and inside(c, e.position) then
        st.originals[key(e)] = true
        used = used + U_ENTITY
      end
    end
    t.ci = t.ci + 1
  end
  if t.cells[t.ci] then return used, false end
  t.cells, t.ci, t.wi = nil, nil, 1
  return used, true
end

--- the real base's wires between the cells copied, joined again between their lab twins
STEP.wires = function(t, st, s, budget)
  local used = 0
  local function twin(e)
    return s.find_entities_filtered({ position = e.position, name = e.name, limit = 1 })[1]
  end
  while t.cut and t.cut[t.wi] and used < budget do
    local w = t.cut[t.wi]
    if w[1].valid and w[3].valid then
      local a, b = twin(w[1]), twin(w[3])
      if a and b then
        pcall(function() a.get_wire_connector(w[2], true).connect_to(b.get_wire_connector(w[4], true), false) end)
      end
    end
    t.wi, used = t.wi + 1, used + U_WIRE
  end
  if t.cut and t.cut[t.wi] then return used, false end
  t.cut = nil
  return used, true
end

--- the plan's entities already on the base (copied in with it) taken out, the plan dry-built a part a tick
STEP.dry = function(t, st, s, budget)
  local p = st.plan
  st.plan_real = st.plan_real or {}
  if not p.print then  -- (a print without pieces: at once)
    if p.superforced then calibrate(st, s) end
    local inv, stack = print_of(p.blueprint, p.section)
    if inv then
      take_out(st, s, stack, { p.at.x + DRY, p.at.y }, p.dir)
      inv.destroy()
    end
    return 50, true
  end
  t.pi = t.pi or 1
  local inv = game.create_inventory(1)
  local used = 0
  while p.print.parts[t.pi] and used < budget do
    inv[1].set_stack({ name = "blueprint" })
    if inv[1].import_stack(p.print.parts[t.pi]) ~= 1 then
      used = used + 250 + take_out(st, s, inv[1], { p.origin.x + DRY + 0.5, p.origin.y + 0.5 }, p.dir) * 2
    end
    t.pi = t.pi + 1
  end
  inv.destroy()
  if p.print.parts[t.pi] then return used, false end
  return used, true
end

--- the plan built for real on the lab: st.plan_ents, st.plan_box (from its pieces, over ticks: waited for)
STEP.plan = function(t, st, s, budget)
  local p = st.plan
  if not p.print then
    build_plan_now(st, s)
    return 100, true
  end
  if not t.building then
    t.building = true
    build.start({ surface = s, force = st.force, print = p.print, origin = p.origin, dir = p.dir,
      superforced = p.superforced, revive = true, on_revived = "lab", then_kind = "lab", player = t.player,
      lab_task = t.id })
    return 5, false
  end
  if t.built then  -- (build.on_done.lab: its entities)
    for _, e in ipairs(t.built.marks or {}) do if e.valid then e.destroy() end end  -- (what its taps replace)
    st.plan_ents = t.built.ents
    st.plan_box = plan_box_of(st.plan_ents)
    t.built, t.building = nil, nil
    return 20, true
  end
  return 1, false
end
build.on_done.lab = function(b)
  local t = tasks.find("lab", function(x) return x.id == b.lab_task end)
  if t then t.built = { ents = b.built or {}, marks = b.marks } end
end

--- the copy's edges found again, cell by cell (st.feeds, st.drains, st.pipes), then power for each network
STEP.edges = function(t, st, s, budget)
  if not t.cells then
    t.cells, t.ci = cells_of(st.area), 1
    st.feeds, st.drains, st.pipes = {}, {}, {}
  end
  local used = 0
  while t.cells[t.ci] and used < budget do
    used = used + edges_in(st, t.cells[t.ci])
    t.ci = t.ci + 1
  end
  if t.cells[t.ci] then return used, false end
  t.cells, t.ci = nil, nil
  M.power(st)
  return used + 50, true
end

STEP.chart = function(t, st, s)
  game.forces[st.force].chart(s, box(st.area))
  return 20, true
end

--- (a move) the plan's own entities taken away, and the base under where it stood put back as the real base has it
STEP.lift = function(t, st, s, budget)
  local real = game.surfaces[st.home_surface]
  if not t.lift then
    local pb = t.from_box
    t.look = box({ pb[1] - 1, pb[2] - 1, pb[3] + 1, pb[4] + 1 })
    t.lift, t.li = st.plan_ents or {}, 1
    st.plan_ents = {}
    return 5, false
  end
  local used = 0
  while t.lift[t.li] and used < budget do
    local e = t.lift[t.li]
    if e.valid then e.destroy() end
    t.li, used = t.li + 1, used + U_DESTROY
  end
  if t.lift[t.li] then return used, false end
  -- (its tiles, and each of the base's own entities missing there, cloned again: a cell at a time)
  local pb = t.from_box
  t.bcells, t.bci = t.bcells or cells_of({ pb[1] - 1, pb[2] - 1, pb[3] + 1, pb[4] + 1 }), t.bci or 1
  while t.bcells[t.bci] and used < budget do
    local c = t.bcells[t.bci]
    local cb_ = box(c)
    real.clone_area({ source_area = cb_, destination_area = cb_, destination_surface = s, clone_tiles = true,
      clone_entities = false, clone_decoratives = false, clear_destination_entities = false,
      clear_destination_decoratives = false, expand_map = false, create_build_effect_smoke = false })
    local here = {}
    for _, e in pairs(s.find_entities_filtered({ area = cb_ })) do
      if e.valid then here[key(e)] = true used = used + U_KEY end
    end
    for _, e in pairs(real.find_entities_filtered({ area = cb_, force = st.home_force })) do
      if e.valid and inside(c, e.position) then
        local k = key(e)
        if st.originals[k] and not here[k] and not (st.plan_real or {})[k] then
          local x = e.clone({ position = e.position, surface = s, force = st.force, create_build_effect_smoke = false })
          if x then tame(x) here[k] = true end
          used = used + U_CLONE
        end
        used = used + U_KEY
      end
    end
    t.bci, used = t.bci + 1, used + U_CELL + 256 * U_TILE
  end
  if t.bcells[t.bci] then return used, false end
  t.lift, t.bcells, t.bci = nil, nil, nil
  return used, true
end

--- (a move) the lab grown to cover t.want: the strips round it not copied yet (each copied cell by cell)
STEP.grow = function(t, st, s, budget)
  if not t.strips then
    local a, w = st.area, t.want
    local b = { math.min(a[1], math.floor(w[1])), math.min(a[2], math.floor(w[2])), math.max(a[3], math.ceil(w[3])),
      math.max(a[4], math.ceil(w[4])) }
    t.strips, t.gi = {}, 1
    if b[2] < a[2] then t.strips[#t.strips + 1] = { b[1], b[2], b[3], a[2] } end  -- (above, below, left, right)
    if b[4] > a[4] then t.strips[#t.strips + 1] = { b[1], a[4], b[3], b[4] } end
    if b[1] < a[1] then t.strips[#t.strips + 1] = { b[1], a[2], a[1], a[4] } end
    if b[3] > a[3] then t.strips[#t.strips + 1] = { a[3], a[2], b[3], a[4] } end
    t.grown = b
    if t.strips[1] then t.rect, t.pad = b, 8 end
  end
  local used = 0
  while t.strips[t.gi] and used < budget do
    if t.sub == nil then t.sub = "ground" end
    if t.sub == "ground" then  -- (the ground round the whole grown area: its new chunks, the ring)
      t.rect = t.grown
      local u, done = STEP.ground(t, st, s, budget - used)
      used = used + u
      if not done then return used, false end
      t.sub = "copy"
    end
    if t.sub == "copy" then
      t.rect, t.copied = t.strips[t.gi], st.area
      local u, done = STEP.copy(t, st, s, budget - used)
      used = used + u
      if not done then return used, false end
      t.sub = "wires"
    end
    if t.sub == "wires" then
      local u, done = STEP.wires(t, st, s, budget - used)
      used = used + u
      if not done then return used, false end
      t.gi, t.sub = t.gi + 1, "copy"
    end
  end
  if t.strips[t.gi] then return used, false end
  st.area = t.grown
  t.strips, t.sub = nil, nil
  game.forces[st.force].chart(s, box(st.area))
  return used + 10, true
end

-- the paste back ---------------------------------------------------------------------------------------------

--- what the lab has now (st.now: entity keys), and where it changed (t.changed: the cells with new entities or
--- tiles), a cell at a time
STEP.diff = function(t, st, s, budget)
  local real = game.surfaces[st.home_surface]
  if not t.cells then t.cells, t.ci, t.now, t.changed = cells_of(st.area), 1, {}, {} end
  local used = 0
  while t.cells[t.ci] and used < budget do
    local c = t.cells[t.ci]
    local changed = false
    for _, e in pairs(s.find_entities_filtered({ area = box(c), force = st.force })) do
      if e.name ~= POWER and e.type ~= "entity-ghost" and e.type ~= "tile-ghost" and inside(c, e.position) then
        local k = key(e)
        t.now[k] = true
        if not st.originals[k] then changed = true end
        used = used + U_KEY
      end
    end
    -- (tiles laid or taken up in the lab: the cells the game told of, and the plan's own)
    local pb = st.plan_box
    if not changed and pb and c[1] < pb[3] and c[3] > pb[1] and c[2] < pb[4] and c[4] > pb[2] then changed = true end
    if not changed and st.tile_cells then
      for wx = math.floor(c[1] / CELL), math.floor((c[3] - 1) / CELL) do
        for wy = math.floor(c[2] / CELL), math.floor((c[4] - 1) / CELL) do
          if st.tile_cells[wx .. "," .. wy] then changed = true end
        end
      end
    end
    if changed then t.changed[#t.changed + 1] = c end
    t.ci = t.ci + 1
  end
  if t.cells[t.ci] then return used, false end
  t.cells = nil
  return used, true
end

--- the base's entities the player took away in the lab (an original no longer there) marked for removal
STEP.removed = function(t, st, s, budget)
  local real, home = game.surfaces[st.home_surface], game.forces[st.home_force]
  local player = t.player and game.get_player(t.player)
  t.removed = t.removed or {}
  local used = 0
  local k = t.rk
  while used < budget do
    k = next(st.originals, k)
    if not k then break end
    if not t.now[k] then
      local name, quality, x, y = k:match("^(.-)|(.-)|(.-)|(.-)|")
      for _, e in pairs(real.find_entities_filtered({ name = name, position = { tonumber(x), tonumber(y) },
          force = home, quality = quality })) do
        if not e.to_be_deconstructed() and e.order_deconstruction(home, player) then t.removed[#t.removed + 1] = e end
      end
      used = used + U_EDGE
    end
    used = used + 0.1
  end
  t.rk = k
  if k then return used, false end
  t.now = nil
  return used, true
end

--- the changed cells as blueprints of the lab pasted over the base where they are (ghosts; unchanged entities skip,
--- as any paste does), a cell a tick; the wires between them, and to the base round them, joined after
STEP.paste = function(t, st, s, budget)
  local real, home = game.surfaces[st.home_surface], game.forces[st.home_force]
  local player = t.player and game.get_player(t.player)
  t.pi, t.ghosts, t.marks, t.links = t.pi or 1, t.ghosts or {}, t.marks or {}, t.links or {}
  local used = 0
  local inv = game.create_inventory(1)
  local stack = inv[1]
  while t.changed[t.pi] and used < budget do
    local c = t.changed[t.pi]
    stack.set_stack({ name = "blueprint" })
    local map = stack.create_blueprint({ surface = s, force = st.force, area = box(c), include_modules = true,
      always_include_tiles = true, include_station_names = true, include_trains = false, include_fuel = false })
    used = used + U_CELL + 2 * table_size(map)
    if stack.is_blueprint_setup() then
      -- (where it lands: the print's own coordinates plus the offset of any of its entities from its lab twin)
      local off
      for n, be in pairs(stack.get_blueprint_entities() or {}) do
        local e = map[be.entity_number]
        if e and e.valid then
          off = { math.floor(e.position.x - be.position.x + 0.5), math.floor(e.position.y - be.position.y + 0.5) }
          break
        end
      end
      if not off then  -- (tiles only: the lowest tile's)
        local tx, ty
        for _, bt in pairs(stack.get_blueprint_tiles() or {}) do
          tx, ty = math.min(tx or bt.position.x, bt.position.x), math.min(ty or bt.position.y, bt.position.y)
        end
        local lx, ly
        for _, lt in pairs(s.find_tiles_filtered({ area = box(c) })) do
          if lt.prototype.items_to_place_this then
            lx, ly = math.min(lx or lt.position.x, lt.position.x), math.min(ly or lt.position.y, lt.position.y)
          end
        end
        if tx and lx then off = { lx - tx, ly - ty } end
      end
      for _, e in pairs(map) do  -- (wires from this cell to entities outside it: joined after)
        if e.valid then
          local ok, cons = pcall(e.get_wire_connectors, false)
          for id, con in pairs(ok and cons or {}) do
            for _, link in ipairs(con.connections) do
              local o = link.target.owner
              if o.valid and o.name ~= POWER and not inside(c, o.position) then
                t.links[#t.links + 1] = { e.name, e.position, id, o.name, o.position, link.target.wire_connector_id }
              end
            end
          end
        end
      end
      if off then
        stack.blueprint_snap_to_grid = { 1, 1 }
        stack.blueprint_absolute_snapping = true
        local marks = {}
        build.collect(marks)
        local ghosts = stack.build_blueprint({ surface = real, force = home, position = { off[1] + 0.5, off[2] + 0.5 },
          build_mode = defines.build_mode.superforced, skip_fog_of_war = false, raise_built = true, player = player })
        build.collect(nil)
        for _, g in pairs(ghosts) do t.ghosts[#t.ghosts + 1] = g end
        for _, m in ipairs(marks) do t.marks[#t.marks + 1] = m end
        used = used + #ghosts * 2
      end
    end
    t.pi = t.pi + 1
  end
  inv.destroy()
  if t.changed[t.pi] then return used, false end
  return used, true
end

--- the wires the cells' blueprints left out, between what's on the base now (the ghosts pasted, or what was there)
STEP.links = function(t, st, s, budget)
  local real = game.surfaces[st.home_surface]
  t.li = t.li or 1
  local function at(name, pos)
    local f = real.find_entities_filtered({ position = pos, ghost_name = name, limit = 1 })[1]
    return f or real.find_entities_filtered({ position = pos, name = name, limit = 1 })[1]
  end
  local used = 0
  while t.links[t.li] and used < budget do
    local w = t.links[t.li]
    local a, b = at(w[1], w[2]), at(w[4], w[5])
    if a and b then
      pcall(function() a.get_wire_connector(w[3], true).connect_to(b.get_wire_connector(w[6], true), false) end)
    end
    t.li, used = t.li + 1, used + U_WIRE
  end
  if t.links[t.li] then return used, false end
  return used, true
end

-- the lab task -------------------------------------------------------------------------------------------------

local OPS = {
  open = { "clear", "ground", "copy", "wires", "dryground", "dry", "plan", "edges", "chart" },
  move = { "grow", "lift", "plan", "edges" },
  paste = { "diff", "removed", "paste", "links", "clear" },
  close = { "clear" },
}
M.after = {}  -- op -> function(task, st): what follows (the player taken there, the window told)

tasks.kind("lab", function(t, budget)
  local st = t.st
  local s = st and game.surfaces[st.surface]
  if not s then return 1, true end
  local used = 0
  while used < budget do
    local name = OPS[t.op][t.si]
    if not name then
      if t.op == "close" or t.op == "paste" then  -- (the lab forgotten; its surface kept for the next one)
        if all()[t.index] == st then all()[t.index] = nil end
      end
      if M.after[t.op] then M.after[t.op](t, st) end
      return used + 1, true
    end
    t.phase = name  -- (the log's, tests')
    local u, done = STEP[name](t, st, s, budget - used)
    used = used + u
    if not done then return used, false end
    t.si = t.si + 1
  end
  return used, false
end)

--- a lab step queued for player index `index` (one at a time, in order) -> the task
local function queue(index, op, more)
  local t = more or {}
  t.op, t.si, t.index, t.queue = op, 1, index, "lab" .. index
  return tasks.start("lab", t)
end

--- whether the lab of player `index` is still being made, moved or taken back
function M.busy(index)
  return tasks.find("lab", function(t) return t.index == index end) ~= nil
end

--- the base round `area` ({x1, y1, x2, y2}, the plan's) copied to the player's lab, the plan built in it (over the
--- next ticks: M.after.open follows). `plan` = {blueprint, section, pieces (the answer's), box = {x, y, w, h} (an
--- absolute plan) | center = {x, y}, clear = {{name, position}}} -> the lab's state (filled as it goes)
function M.open(index, home_surface, home_force, plan, more)
  local old = all()[index]
  local s = lab_surface(index)
  pcall(home_force.set_surface_hidden, s, true)
  local a = plan.area
  a = { math.floor(a[1] - MARGIN), math.floor(a[2] - MARGIN), math.ceil(a[3] + MARGIN), math.ceil(a[4] + MARGIN) }
  local st = { surface = s.name, force = home_force.name, home_surface = home_surface.name, home_force = home_force.name,
    area = a, originals = {}, clear = plan.clear, feeds = {}, drains = {}, pipes = {} }
  local b = plan.box
  local print = build.pick(plan.pieces, plan.section, true)
  st.plan = { blueprint = plan.blueprint, section = plan.section, superforced = b ~= nil, dir = defines.direction.north,
    at = b and { x = b[1] + b[3] / 2, y = b[2] + b[4] / 2 } or plan.center }
  if print then
    st.plan.print = print
    st.plan.origin = build.origin(print, plan.center, b)
  end
  all()[index] = st
  if old and old.surface == s.name then  -- (the last lab cleared first)
    queue(index, "close", { st = old, clear = { old.area[1] - 40, old.area[2] - 40, old.area[3] + 40, old.area[4] + 40 } })
  elseif old and game.get_surface(old.surface) then
    game.delete_surface(old.surface)  -- (a lab from before 0.25.1: one per try)
  end
  local t = more or {}
  t.st, t.clear, t.rect, t.pad = st, { a[1] - 40, a[2] - 40, a[3] + 40, a[4] + 40 }, a, 8
  queue(index, "open", t)
  return st
end

--- what changed in the lab pasted onto the base as ghosts (over the next ticks), the originals no longer there
--- marked for removal, then the lab cleared; M.after.paste follows with t.ghosts, t.marks, t.removed
function M.paste(index, by_player, more)
  local st = all()[index]
  if not st then return false end
  local t = more or {}
  t.st, t.player = st, by_player and by_player.index
  t.clear = { st.area[1] - 40, st.area[2] - 40, st.area[3] + 40, st.area[4] + 40 }
  queue(index, "paste", t)
  return true
end

--- the plan in the lab moved (dx, dy tiles) and/or turned a quarter clockwise: the base where it stood put back as
--- the real base has it, the plan built again there (changes made to the plan itself in the lab go with it)
--- -> true (done over the next ticks), or false and why (a locale key)
function M.move(index, dx, dy, turn)
  local st = all()[index]
  local s = st and game.surfaces[st.surface]
  local real = st and game.surfaces[st.home_surface]
  if not (s and real and st.plan and st.plan_box) then return false, "sandbox-cant-move" end
  if M.busy(index) then return false, "sandbox-busy" end
  local p, pb = st.plan, st.plan_box
  local at = { x = p.at.x + dx, y = p.at.y + dy }
  -- where it will stand (a quarter turn clockwise about its middle), and the base round that copied in if it isn't yet
  local nb = { pb[1] + dx, pb[2] + dy, pb[3] + dx, pb[4] + dy }
  if turn then
    nb = { at.x - (pb[4] - p.at.y), at.y + (pb[1] - p.at.x), at.x - (pb[2] - p.at.y), at.y + (pb[3] - p.at.x) }
  end
  p.at = at
  if turn then p.dir = (p.dir + 4) % 16 end
  if p.print then  -- (the middle kept where it was, plus the move)
    if turn then
      p.origin = build.origin(p.print, { x = (pb[1] + pb[3]) / 2 + dx, y = (pb[2] + pb[4]) / 2 + dy }, nil, p.dir)
    else
      p.origin = { x = p.origin.x + dx, y = p.origin.y + dy }
    end
  end
  queue(index, "move", { st = st, from_box = pb,
    want = { nb[1] - ROOM, nb[2] - ROOM, nb[3] + ROOM, nb[4] + ROOM } })
  return true
end

--- the lab cleared and forgotten (its surface kept for the next one), over the next ticks
function M.close(index)
  local st = all()[index]
  if not st then return end
  local s = game.surfaces[st.surface]
  if not s then all()[index] = nil return end
  if s.name ~= PREFIX .. index then all()[index] = nil game.delete_surface(s) return end  -- (a lab from before 0.25.1)
  local a = st.area
  queue(index, "close", { st = st, clear = { a[1] - 40, a[2] - 40, a[3] + 40, a[4] + 40 } })
end

-- the player ----------------------------------------------------------------------------------------------------

--- why `player` can't go to a sandbox now, or nil
function M.cant(player)
  if all()[player.index] then return "in-sandbox" end
  if player.controller_type ~= defines.controllers.character or not player.character then return "sandbox-character" end
  if player.surface.platform then return "sandbox-platform" end
end

--- the plan built in a lab and the player taken there once it's ready (M.after.open)
function M.enter(player, plan)
  local why = M.cant(player)
  if why then return false, why end
  M.open(player.index, player.surface, player.force, plan, { player = player.index, enter = plan.area })
  if cb.making then cb.making(player, true) end
  return true
end

M.after.open = function(t, st)
  local player = t.player and game.get_player(t.player)
  if cb.making and player then cb.making(player, false) end
  if not (t.enter and player and player.valid) then return end
  if player.controller_type ~= defines.controllers.character or not player.character then
    return M.close(player.index)  -- (they left their character meanwhile: no lab)
  end
  local character = player.character
  st.character, st.position = character, { x = player.position.x, y = player.position.y }
  st.surface_list = player.game_view_settings.show_surface_list
  if character.walking_state.walking then
    character.walking_state = { walking = false, direction = character.walking_state.direction }
  end
  player.clear_cursor()
  player.set_controller({ type = defines.controllers.god })
  if character.valid and not character.associated_player then player.associate_character(character) end
  local a = t.enter
  player.teleport({ (a[1] + a[3]) / 2, (a[2] + a[4]) / 2 }, game.surfaces[st.surface])
  player.cheat_mode = true
  player.game_view_settings.show_surface_list = false  -- (no remote view to the real base with cheat mode on)
  if cb.shown then cb.shown(player, false) end
  if cb.card then cb.card(player, true) end
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

--- "Place in base": back at once, the lab's version pasted as ghosts over the next ticks, the lab gone
function M.place(player)
  local st = all()[player.index]
  if not st or M.busy(player.index) then return end
  leave(player, st)
  M.paste(player.index, player, { placed = true })
end

M.after.paste = function(t, st)
  local player = t.player and game.get_player(t.player)
  storage.bpgen_sandbox_last = { ghosts = t.ghosts or {}, marks = t.marks or {}, removed = t.removed or {} }  -- (tests)
  if t.placed and player and cb.placed then cb.placed(player, t.ghosts or {}, t.marks or {}, st.area, t.removed or {}) end
end

--- "Discard": back, the lab gone
function M.discard(player)
  local st = all()[player.index]
  if not st then return end
  leave(player, st)
  M.close(player.index)
  all()[player.index] = nil
end

-- events --------------------------------------------------------------------------------------------------------

local function in_lab(e) return e and e.valid and M.is_lab(e.surface) end

--- tiles changed on a lab: the cells they are in (CELL tiles on the world's grid), for the paste back
function M.tiles_changed(e)
  local s = game.get_surface(e.surface_index)
  if not M.is_lab(s) then return end
  for _, st in pairs(storage.bpgen_sandbox or {}) do
    if st.surface == s.name then
      st.tile_cells = st.tile_cells or {}
      for _, t in pairs(e.tiles or {}) do
        st.tile_cells[math.floor(t.position.x / CELL) .. "," .. math.floor(t.position.y / CELL)] = true
      end
    end
  end
end

M.handlers = {
  [defines.events.on_tick] = function(e)
    if e.tick % EVERY ~= 0 or not storage.bpgen_sandbox then return end
    for index, st in pairs(storage.bpgen_sandbox) do
      if game.surfaces[st.surface] and not M.busy(index) then
        feed(st)
        if e.tick % 60 == 0 then M.power(st) end
      end
    end
  end,
  -- (tiles laid or taken up in a lab: their cells pasted back)
  [defines.events.on_player_built_tile] = function(e) M.tiles_changed(e) end,
  [defines.events.on_player_mined_tile] = function(e) M.tiles_changed(e) end,
  [defines.events.script_raised_set_tiles] = function(e) M.tiles_changed(e) end,
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
