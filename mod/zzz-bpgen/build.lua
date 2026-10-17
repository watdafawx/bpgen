-- A plan's blueprint built a part a tick (its pieces from Python: bpgen/pieces.py), so no tick imports and builds
-- thousands of entities at once: the parts snap absolutely to a 1-tile grid in the print's own coordinates, so each
-- lands where the whole print would. Wires between parts are joined on their ghosts afterwards (their ends are
-- tagged bpgen_w), what the build marks for deconstruction is gathered from the game's events as it happens, and
-- (on the preview and in the sandbox's lab) the ghosts are revived a few a tick.
--   build.start(spec) -> the task; when done, build.on_done[spec.then_kind](task) gets task.ghosts (or task.built
--   when revived), task.marks, task.extent ({x1, y1, x2, y2} of what was built) and task.origin (the print's
--   coordinates plus origin = the world's).
local tasks = require("tasks")
local bench = require("bench")

local M = {}
-- units of work (tasks.lua: ~4 us each), a ghost each
local U_IMPORT, U_BUILD, U_RAISED, U_REVIVE, U_WIRE = 0.9, 1.2, 2.4, 3, 1
local PER = 250  -- (entities a part: bpgen/pieces.py)

M.on_done = {}  -- then_kind -> function(task)
local COPPER = { ["electric-pole"] = { defines.wire_connector_id.pole_copper },
  ["power-switch"] = { defines.wire_connector_id.power_switch_left_copper,
    defines.wire_connector_id.power_switch_right_copper } }

local function stack()  -- (a slot the parts are imported into, one at a time)
  local inv = storage.bpgen_build_inv
  if not (inv and inv.valid) then
    inv = game.create_inventory(1)
    storage.bpgen_build_inv = inv
  end
  return inv[1]
end

--- the print of an answer's pieces to build: the one labelled `section`, else the book's active one (`active`) or its
--- first -> its entry of pieces.prints, or nil
function M.pick(pieces, section, active)
  if not (pieces and pieces.prints) then return nil end
  for _, p in ipairs(pieces.prints) do
    if section and p.label == section then return p end
  end
  if active and pieces.active then
    for _, p in ipairs(pieces.prints) do
      if p.path == pieces.active then return p end
    end
  end
  return pieces.prints[1]
end

-- turns: a quarter turn clockwise is +4 (16 directions); (x, y) turned k quarters about the origin
local function turned(x, y, dir)
  for _ = 1, (math.floor((dir or 0) / 4)) % 4 do x, y = -y, x end
  return x, y
end

--- where a print's coordinates land: the world's = origin + 1/2 + (its coordinates - 1/2) turned by dir.
--- box {x, y, w, h}: the frame's corner on the box's (an absolute plan); else the frame centred on c.
--- -> origin {x, y} (whole tiles; even with rails)
function M.origin(print, c, box, dir)
  local f = print.frame
  local ox, oy
  if box then
    ox, oy = box[1] - f[1], box[2] - f[2]
  else
    local mx, my = turned((f[1] + f[3]) / 2 - 0.5, (f[2] + f[4]) / 2 - 0.5, dir)
    ox, oy = math.floor(c.x - 0.5 - mx + 0.5), math.floor(c.y - 0.5 - my + 0.5)
  end
  if print.even then ox, oy = ox - ox % 2, oy - oy % 2 end
  return { x = ox, y = oy }
end

--- the frame's tiles {x1, y1, x2, y2} in the world, as built at origin turned by dir
function M.frame_box(print, origin, dir)
  local f = print.frame
  local ax, ay = turned(f[1] - 0.5, f[2] - 0.5, dir)
  local bx, by = turned(f[3] - 0.5, f[4] - 0.5, dir)
  return { math.min(ax, bx) + 0.5 + origin.x, math.min(ay, by) + 0.5 + origin.y,
    math.max(ax, bx) + 0.5 + origin.x, math.max(ay, by) + 0.5 + origin.y }
end

-- (marks the build being made now raises: gathered into it)
local collecting = nil
--- (sandbox.lua's own pastes) marks gathered into `list` from now on (nil: no more)
function M.collect(list) collecting = list end
function M.on_marked(e)
  if collecting and e.entity and e.entity.valid then collecting[#collecting + 1] = e.entity end
end

local function grow(t, b)
  local lt, rb = b.left_top, b.right_bottom
  local x = t.extent
  if not x then t.extent = { lt.x, lt.y, rb.x, rb.y } return end
  if lt.x < x[1] then x[1] = lt.x end
  if lt.y < x[2] then x[2] = lt.y end
  if rb.x > x[3] then x[3] = rb.x end
  if rb.y > x[4] then x[4] = rb.y end
end

local function build_part(t, i)
  local st = stack()
  st.set_stack({ name = "blueprint" })
  if st.import_stack(t.parts[i]) == 1 then return 1 end
  local player = t.by_player and game.get_player(t.by_player)
  local marks = {}
  collecting = marks
  local ok, ghosts = pcall(st.build_blueprint, { surface = game.surfaces[t.surface], force = t.force,
    position = { t.origin.x + 0.5, t.origin.y + 0.5 }, direction = t.dir, skip_fog_of_war = false,
    build_mode = t.superforced and defines.build_mode.superforced or defines.build_mode.forced,
    raise_built = t.raise or nil, player = player })
  collecting = nil
  st.clear()
  if not ok then
    log("[bpgen] a part didn't build: " .. tostring(ghosts))
    return 1
  end
  for _, e in ipairs(marks) do t.marks[#t.marks + 1] = e end
  local wired = t.wires and t.wires[1]
  local poles = {}
  for _, g in pairs(ghosts) do
    if g.valid then
      t.ghosts[#t.ghosts + 1] = g
      if not t.revive then grow(t, g.bounding_box) end
      if g.type == "entity-ghost" then
        if wired then
          local tags = g.tags
          if tags and tags.bpgen_w then t.tagged[tags.bpgen_w] = g end
        end
        if COPPER[g.ghost_type] then poles[#poles + 1] = g end
      end
    end
  end
  -- (the game links a pasted pole to the poles already there: to the parts built before this one too, which a
  -- paste of the whole print wouldn't; those links cut, the print's own wires between parts are made after)
  for _, g in ipairs(poles) do
    for _, id in ipairs(COPPER[g.ghost_type]) do
      local c = g.get_wire_connector(id, false)
      for _, link in ipairs(c and c.connections or {}) do
        local other = link.target.owner
        if other.valid and other.type == "entity-ghost" and t.poles[other.unit_number] then
          c.disconnect_from(link.target, link.origin)
        end
      end
    end
  end
  for _, g in ipairs(poles) do t.poles[g.unit_number] = true end
  return PER * U_IMPORT + #ghosts * (t.raise and U_RAISED or U_BUILD)
end

local function step(t, budget)
  local used = 0
  if t.phase == "parts" then
    while t.i <= #t.parts and used < budget do
      used = used + build_part(t, t.i)
      t.i = t.i + 1
    end
    if t.i > #t.parts then t.phase, t.wi = "wires", 1 end
  end
  if t.phase == "wires" then
    while t.wi <= #(t.wires or {}) and used < budget do
      local w = t.wires[t.wi]
      local a, b = t.tagged[w[1]], t.tagged[w[3]]
      if a and b and a.valid and b.valid then
        pcall(function() a.get_wire_connector(w[2], true).connect_to(b.get_wire_connector(w[4], true), false) end)
      end
      t.wi, used = t.wi + 1, used + U_WIRE
    end
    if t.wi > #(t.wires or {}) then
      for _, g in pairs(t.tagged) do  -- (the tags only told the parts' ends apart)
        if g.valid then
          local tags = g.tags
          tags.bpgen_w = nil
          g.tags = tags
        end
      end
      t.tagged, t.phase, t.ri = {}, t.revive and "revive" or "done", 1
    end
  end
  if t.phase == "revive" then
    t.built = t.built or {}
    local raise = script.active_mods["power-propagation"] ~= nil  -- (its hidden poles power the plan)
    while t.ri <= #t.ghosts and used < budget do
      local g = t.ghosts[t.ri]
      if g.valid and (g.type == "entity-ghost" or g.type == "tile-ghost") then
        local plans = g.type == "entity-ghost" and g.insert_plan or nil
        local _, ent, proxy = g.silent_revive({ return_item_request_proxy = true, raise_revive = raise })
        if ent then
          t.built[#t.built + 1] = ent
          grow(t, ent.bounding_box)
          if plans then bench.insert_plans(ent, plans) end
          if t.on_revived then M.on_revived[t.on_revived](ent) end
        end
        if proxy and proxy.valid then proxy.destroy() end
      end
      t.ri, used = t.ri + 1, used + U_REVIVE
    end
    if t.ri > #t.ghosts then t.phase, t.ghosts = "done", {} end
  end
  if t.phase == "done" then
    local fn = M.on_done[t.then_kind]
    if fn then fn(t) end
    return used, true
  end
  return used, false
end
tasks.kind("build", step)
M.on_revived = {}  -- name -> function(entity): something done to each revived entity (the lab's labs tamed)

--- starts building a print (an entry of an answer's pieces.prints) on `surface` for `force` at `origin` turned by
--- `dir`: spec = {surface, force, print, origin, dir, superforced, raise (raise_built), by_player (index), revive,
--- on_revived (name), then_kind, data, ...} (kept on the task) -> the task
function M.start(spec)
  local p = spec.print
  spec.surface = type(spec.surface) == "string" and spec.surface or spec.surface.name
  spec.force = type(spec.force) == "string" and spec.force or spec.force.name
  spec.parts, spec.wires, spec.print = p.parts, p.wires, nil
  spec.poles = {}  -- (unit number -> true: the pole ghosts of the parts built so far)
  spec.phase, spec.i = "parts", 1
  spec.ghosts, spec.marks, spec.tagged = {}, {}, {}
  return tasks.start("build", spec)
end

--- every build task for `then_kind` and player (to drop one superseded)
function M.cancel(then_kind, player_index)
  local l = storage.bpgen_tasks or {}
  for i = #l, 1, -1 do
    local t = l[i]
    if t.kind == "build" and t.then_kind == then_kind and t.player == player_index then table.remove(l, i) end
  end
end

--- entities destroyed a few a tick (raise: with the game's events, so the site and other mods see them go), then
--- M.on_done[then_kind](task) when given. Ghosts usually; whatever is no longer valid is passed over
--- `unmark`: entities whose deconstruction mark is taken back too (what a paste marked: Undo keeps them), by
--- `force`, as player `player_index` did it
local U_DESTROY, U_UNMARK = 2.5, 2
function M.destroy_later(list, raise, then_kind, data, player_index, unmark, force)
  if not list[1] and not (unmark and unmark[1]) then
    if then_kind then M.on_done[then_kind]({ data = data, player = player_index }) end
    return
  end
  tasks.start("doom", { list = list, i = 1, raise = raise or nil, then_kind = then_kind, data = data,
    player = player_index, unmark = unmark, ui = 1, force = force and force.name })
end
tasks.kind("doom", function(t, budget)
  local used = 0
  local how = t.raise and { raise_destroy = true } or nil
  local player = t.unmark and t.player and game.get_player(t.player)
  while t.unmark and t.unmark[t.ui] and used < budget do
    local e = t.unmark[t.ui]
    if e.valid and e.to_be_deconstructed() then e.cancel_deconstruction(t.force or e.force, player) end
    t.ui, used = t.ui + 1, used + U_UNMARK
  end
  while t.list[t.i] and used < budget do
    local e = t.list[t.i]
    if e.valid then e.destroy(how) end
    t.i, used = t.i + 1, used + U_DESTROY
  end
  if t.unmark and t.unmark[t.ui] then return used, false end
  if t.list[t.i] then return used, false end
  if t.then_kind then M.on_done[t.then_kind](t) end
  return used, true
end)

-- an area of the base as a blueprint (the Move and Refit tools), a cell a tick: each cell's own print (its
-- entities by where they stand, in the world's coordinates less its offset) and the wires between cells; bpgen's
-- Python joins them into the one print (bpgen/pieces.py: join). create_blueprint of a 200x200 base was 450 ms.
local AREA_CELL = 16
local U_BP = 6  -- (an entity blueprinted and its print's text)

--- `area` ({left_top, right_bottom}) of `surface` for `force` as blueprints, then M.on_done[then_kind](task) with
--- task.parts = {area = {x1, y1, x2, y2}, cell, parts = {{blueprint, offset = {x, y}, cell = {x1, y1, x2, y2}}},
--- wires = {{name, x, y, connector, name, x, y, connector}}} (nil: nothing there)
function M.area_print(surface, force, area, then_kind, data, player_index)
  local a = { area.left_top.x, area.left_top.y, area.right_bottom.x, area.right_bottom.y }
  local cells = {}
  for y = a[2], a[4] - 1e-9, AREA_CELL do
    for x = a[1], a[3] - 1e-9, AREA_CELL do
      cells[#cells + 1] = { x, y, math.min(x + AREA_CELL, a[3]), math.min(y + AREA_CELL, a[4]) }
    end
  end
  return tasks.start("area_bp", { surface = surface.name, force = force.name, area = a, cells = cells, ci = 1,
    parts = {}, wires = {}, then_kind = then_kind, data = data, player = player_index })
end

tasks.kind("area_bp", function(t, budget)
  local s = game.surfaces[t.surface]
  if not s then return 1, true end
  local a = t.area
  local inv = game.create_inventory(1)
  local st = inv[1]
  local used = 0
  while t.cells[t.ci] and used < budget do
    local c = t.cells[t.ci]
    st.set_stack({ name = "blueprint" })
    local map = st.create_blueprint({ surface = s, force = t.force, area = { { c[1], c[2] }, { c[3], c[4] } },
      include_modules = true, include_station_names = true })
    local n = table_size(map)
    if st.is_blueprint_setup() and n > 0 then
      local off
      for _, be in pairs(st.get_blueprint_entities() or {}) do
        local e = map[be.entity_number]
        if e and e.valid then
          off = { e.position.x - be.position.x, e.position.y - be.position.y }
          break
        end
      end
      for _, e in pairs(map) do  -- (wires to the area's entities out of this cell: joined by Python)
        if e.valid then
          local ok, cons = pcall(e.get_wire_connectors, false)
          for id, con in pairs(ok and cons or {}) do
            for _, link in ipairs(con.connections) do
              local o = link.target.owner
              local p = o.valid and o.position
              if p and p.x >= a[1] and p.x <= a[3] and p.y >= a[2] and p.y <= a[4]
                  and not (p.x >= c[1] and p.x < c[3] and p.y >= c[2] and p.y < c[4]) then
                t.wires[#t.wires + 1] = { e.name, e.position.x, e.position.y, id, o.name, p.x, p.y,
                  link.target.wire_connector_id }
              end
            end
          end
        end
      end
      if off then t.parts[#t.parts + 1] = { blueprint = st.export_stack(), offset = off, cell = c } end
    end
    t.ci, used = t.ci + 1, used + 5 + n * U_BP
  end
  inv.destroy()
  if t.cells[t.ci] then return used, false end
  t.cells = nil
  M.on_done[t.then_kind](t)
  return used, true
end)

M.handlers = { [defines.events.on_marked_for_deconstruction] = M.on_marked }

return M
