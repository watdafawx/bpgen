-- An area of the base read for bpgen's Python (the snapshot), spread over ticks: the area in cells of 16 tiles,
-- a few a tick, each entity in the cell its position (kept inside the area) falls in, so what is read is what one
-- find over the whole area finds. With flows, each transport belt's items are looked at when it is read and again
-- MEASURE_TICKS later: a belt's flow is its belt speed times the items that moved on it (items/s, both lanes), so
-- bpgen knows how much of a bus lane is used and whether it runs dry or backs up.
--
-- The reader (where it is, what it counted) lives in storage, so every peer, one joining meanwhile too, finishes it
-- in the same tick; M.done(reader) then gets the snapshot. The records themselves never pile up in Lua (a big
-- base's were tens of MB the collector had to walk, and pauses of 20-60 ms): each is written as a JSON row as it is
-- read (bpgen/snapfmt.py reads it back), a block of them at a time handed on by this peer's sink (local, never
-- saved): sent to Python (bpgen.ingame:part) ahead of the job when this peer runs it, kept for a file (the snapshot
-- tool, tests), or not written at all on the other peers. The snapshot itself is then small: the area, the ore and
-- water, and {"@parts": key, "n"}, which bpgen.ingame joins with the parts sent ahead.
local tasks = require("tasks")

local M = {}
M.MEASURE_TICKS = 30
local CELL = 8
local BLOCK = 2000  -- records a part (about a quarter MB)
-- units of work (tasks.lua: ~4 us each)
local U_ENTITY, U_RECORD, U_BELT, U_RESOURCE, U_TILE, U_CELL, U_PART = 0.1, 3, 2.5, 0.15, 0.02, 3, 30
local U_FLOW = 2  -- (a belt looked at again, its text finished)

local SKIP = { character = true, ["item-entity"] = true, ["item-request-proxy"] = true, ["entity-ghost"] = false,
  ["character-corpse"] = true, corpse = true, ["flying-text"] = true, ["highlight-box"] = true, explosion = true,
  smoke = true, particle = true, projectile = true, sticker = true, fire = true, ["deconstructible-tile-proxy"] = true,
  ["tile-ghost"] = true }
local OBSTACLE = { tree = true, ["simple-entity"] = true, cliff = true, ["unit-spawner"] = true, turret = false,
  ["fish"] = false }
local BELTS = { ["transport-belt"] = 2, ["underground-belt"] = 2, splitter = 4, loader = 2, ["loader-1x1"] = 2 }
local PIPES = { pipe = true, ["pipe-to-ground"] = true, ["storage-tank"] = true, pump = true }
-- what makes power: a pole on the same network as one of these has power to give (a mall's own poles may not)
local POWER_SOURCES = { ["generator"] = true, ["burner-generator"] = true, ["solar-panel"] = true,
  ["accumulator"] = true, ["electric-energy-interface"] = true, ["fusion-generator"] = true,
  ["lightning-attractor"] = true }

-- text ------------------------------------------------------------------------------------------------------------

local fmt, concat = string.format, table.concat
local function str(s)
  if s:find('^[%w%-_%.]*$') then return '"' .. s .. '"' end
  return helpers.table_to_json({ s }):sub(2, -2)
end
-- (%.17g: every double reads back as itself)
local function num(v)
  if v == math.floor(v) and v > -1e15 and v < 1e15 then return fmt("%d", v) end
  return fmt("%.17g", v)
end
local function val(v)
  local t = type(v)
  if t == "number" then return num(v) end
  if t == "string" then return str(v) end
  if t == "boolean" then return v and "true" or "false" end
  return helpers.table_to_json(v)
end
local quoted = {}  -- name -> its JSON (a few hundred names, written for every record)
local function q(s)
  local v = quoted[s]
  if not v then
    v = str(s)
    quoted[s] = v
  end
  return v
end
local function lanes_json(l)
  local t = {}
  for i, x in ipairs(l) do t[i] = x and q(x) or "false" end
  return "[" .. concat(t, ",") .. "]"
end
-- the record's other fields, in this order: {key, how}
local MORE = { { "ghost", val }, { "recipe", q }, { "ug_type", q }, { "lanes", lanes_json }, { "network", num },
  { "fluid", q }, { "powered", val }, { "items", num }, { "flow", num } }
local KNOWN = { name = true, type = true, position = true, direction = true, box = true }
for _, m in ipairs(MORE) do KNOWN[m[1]] = true end

--- a record (see record) as JSON, a compact row Python reads back (bpgen/snapfmt.py): [name, type, x, y, direction,
--- box x1, y1, x2, y2, {its other fields}] (one format for the fields every record has: little garbage for Lua)
local function rec_json(r)
  local p, b = r.position, r.box
  local s = fmt('[%s,%s,%.17g,%.17g,%d,%.17g,%.17g,%.17g,%.17g', q(r.name), q(r.type), p.x, p.y, r.direction or 0,
    b[1], b[2], b[3], b[4])
  local more
  for _, m in ipairs(MORE) do
    local v = r[m[1]]
    if v ~= nil then
      more = more or { s, ",{" }
      more[#more + 1] = (more[3] and ',"' or '"') .. m[1] .. '":' .. m[2](v)
    end
  end
  for k, v in pairs(r) do  -- (a field added since)
    if not KNOWN[k] then
      more = more or { s, ",{" }
      more[#more + 1] = (more[3] and "," or "") .. str(k) .. ":" .. val(v)
    end
  end
  if not more then return s .. "]" end
  more[#more + 1] = "}]"
  return concat(more)
end
M.rec_json = rec_json

-- records ---------------------------------------------------------------------------------------------------

--- the item most present on each lane of a belt-like entity (nil when empty)
local function lanes(ent)
  local out = {}
  for i = 1, BELTS[ent.type] do
    local best, n = nil, 0
    for _, c in pairs(ent.get_transport_line(i).get_contents()) do
      if c.count > n then best, n = c.name, c.count end
    end
    out[i] = best or false
  end
  return out
end

--- the fluid in a pipe-like entity, or the one its network is locked to while it is empty
local function pipe_fluid(ent)
  local f = ent.fluidbox[1]
  if f then return f.name end
  local ok, locked = pcall(ent.fluidbox.get_locked_fluid, 1)
  return ok and locked or nil
end

--- whether the snapshot records `e` (of type t) for `force`
local function recorded(e, t, force)
  return not (OBSTACLE[t] or SKIP[t] or t == "resource") and (t == "entity-ghost" or e.force == force)
end

--- an entity of `force` (or a ghost) as the snapshot records it, or nil (not one bpgen builds round)
function M.record(e, force)
  local t = e.type
  if not recorded(e, t, force) then return nil end
  local name, ty = e.name, t
  if t == "entity-ghost" then name, ty = e.ghost_name, e.ghost_type end
  local b, p = e.bounding_box, e.position
  local lt, rb = b.left_top, b.right_bottom
  local out = { name = name, type = ty, position = { x = p.x, y = p.y }, direction = e.direction,
    box = { lt.x, lt.y, rb.x, rb.y } }
  if t == "entity-ghost" then out.ghost = true end
  if ty == "assembling-machine" or ty == "furnace" then
    local r = e.get_recipe()
    if not r and ty == "furnace" and t ~= "entity-ghost" then r = e.previous_recipe and e.previous_recipe.name end
    out.recipe = type(r) == "string" and r or (r and r.name) or nil
  end
  if ty == "underground-belt" then out.ug_type = e.belt_to_ground_type end
  if (ty == "loader" or ty == "loader-1x1") then out.ug_type = e.loader_type end
  if BELTS[t] then out.lanes = lanes(e) end
  if t == "electric-pole" then out.network = e.electric_network_id end
  if PIPES[t] then out.fluid = pipe_fluid(e) end
  return out
end

--- a belt's items, each with where it is on its line, both lines, onto the list `ids` (id, position, id,
--- position...: numbers only; a table or string a belt made a big base's 45000 belts a lot for Lua's collector)
--- -> where they end in it
local function belt_items(e, ids)
  local n = #ids
  for i = 1, 2 do
    for _, it in pairs(e.get_transport_line(i).get_detailed_contents()) do
      ids[n + 1], ids[n + 2] = it.unique_id, it.position
      n = n + 2
    end
  end
  return n
end


-- sinks: where this peer's text goes ---------------------------------------------------------------------------

-- reader id -> {send = fn(text) | nil, keep = {parts} | nil, write = bool, rows = {E = {}, O = {}}, poles = {},
-- belts = {}}: no sink (a peer that joined meanwhile) or one that neither sends nor keeps writes nothing
local sinks = {}
local kept_of = setmetatable({}, { __mode = "k" })  -- snapshot -> the parts this peer kept of it (a file's)
local sent = {}  -- snapshot key -> true: its parts went to Python from this Lua state (it keeps the last few)
local nonce = nil  -- (this Lua state's own: a key Python saw from another game or load is never taken for this one)

--- the key Python knows a reader's parts by (only the peer that sends them uses it)
local function key_of(r)
  nonce = nonce or tostring({}):gsub("[^%w]", "")
  return nonce .. "-" .. r.id
end

--- a row of kind E (entities) or O (obstacles) counted; every BLOCK of a kind a part (counted in storage: every peer
--- agrees on the parts' number)
local function row(r, sink, kind, text)
  local count = kind == "E" and "ecount" or "ocount"
  r[count] = r[count] + 1
  if sink then
    local l = sink.rows[kind]
    l[#l + 1] = text
  end
  if r[count] % BLOCK == 0 then
    r.nparts = r.nparts + 1
    if sink then M.flush(r, sink, kind) end
    return U_PART
  end
  return 0
end

function M.flush(r, sink, kind)
  local l = sink.rows[kind]
  if not l[1] then return end
  sink.rows[kind] = {}
  sink.n = (sink.n or 0) + 1
  local text = concat(l, ",")
  if sink.send then sink.send(key_of(r) .. "\t" .. kind .. "\t" .. sink.n .. "\n" .. text) end
  if sink.keep then sink.keep[#sink.keep + 1] = { kind, text } end
end

-- reading ----------------------------------------------------------------------------------------------------

--- a row of tiles (x ascending, from cells read west to east) added onto its runs: {y: [[x1, x2], ...]}
local function add_run(rows, y, x)
  local key = tostring(y)
  local r = rows[key]
  if not r then
    r = {}
    rows[key] = r
  end
  local last = r[#r]
  if last and last[2] + 1 == x then last[2] = x
  elseif not (last and last[2] >= x) then r[#r + 1] = { x, x } end
end

--- tile positions -> added to rows, sorted by x within each row
local function add_tiles(rows, positions)
  local by_y = {}
  for _, p in pairs(positions) do
    local y = math.floor(p.y)
    by_y[y] = by_y[y] or {}
    local l = by_y[y]
    l[#l + 1] = math.floor(p.x)
  end
  for y, xs in pairs(by_y) do
    table.sort(xs)
    for _, x in ipairs(xs) do add_run(rows, y, x) end
  end
end

local function cell_of(r, x, y)
  local a = r.snap.area
  local cx = math.floor(math.min(math.max(x, a[1]), a[3]) / CELL)
  local cy = math.floor(math.min(math.max(y, a[2]), a[4]) / CELL)
  return math.min(math.max(cx, r.gx0), r.gx1) - r.gx0, math.min(math.max(cy, r.gy0), r.gy1) - r.gy0
end

--- reads cell `ci` (1-based, row by row) -> units used
local function read_cell(r, sink, s, force, ci)
  local cb, sb = nil, nil  -- (the cell's belts, to look at again: in storage; this peer's text of them)
  local a = r.snap.area
  local ncx = r.gx1 - r.gx0 + 1
  local cx, cy = (ci - 1) % ncx, math.floor((ci - 1) / ncx)
  local x1, y1 = math.max(a[1], (r.gx0 + cx) * CELL), math.max(a[2], (r.gy0 + cy) * CELL)
  local x2, y2 = math.min(a[3], (r.gx0 + cx + 1) * CELL), math.min(a[4], (r.gy0 + cy + 1) * CELL)
  local area = { left_top = { x = x1, y = y1 }, right_bottom = { x = x2, y = y2 } }
  local snap = r.snap
  local used = U_CELL
  local found = s.find_entities_filtered({ area = area, type = "resource", invert = true })
  used = used + #found * U_ENTITY
  for _, e in pairs(found) do
    local p = e.position
    local ex, ey = cell_of(r, p.x, p.y)
    if ex == cx and ey == cy then
      local t = e.type
      if OBSTACLE[t] then
        local text
        if sink then
          local b = e.bounding_box
          local lt, rb = b.left_top, b.right_bottom
          text = fmt("[%.17g,%.17g,%.17g,%.17g,%s]", lt.x, lt.y, rb.x, rb.y, q(t))
        end
        used = used + 1 + row(r, sink, "O", text)
      elseif recorded(e, t, force) then
        used = used + U_RECORD
        r.count = r.count + 1
        if POWER_SOURCES[t] and e.electric_network_id then r.powered[e.electric_network_id] = true end
        local belt = r.belts and t == "transport-belt"
        if t == "electric-pole" then  -- (its text waits for its network's power)
          r.npoles = r.npoles + 1
          if sink then sink.poles[r.npoles] = M.record(e, force) end
        elseif belt then  -- (its text waits for its flow, MEASURE_TICKS from now)
          cb = cb or {}
          cb[#cb + 1] = e
          used = used + U_BELT
          if sink then  -- (its record's text but the flow, which closes it; its items now)
            sb = sb or { ids = {}, from = {}, to = {}, texts = {}, open = {} }
            local text = rec_json(M.record(e, force))
            local open = text:sub(-2) == "}]"
            local i = #cb
            sb.from[i] = #sb.ids + 1
            sb.to[i] = belt_items(e, sb.ids)
            sb.texts[i], sb.open[i] = open and text:sub(1, -3) or text, open
          end
        else
          used = used + row(r, sink, "E", sink and rec_json(M.record(e, force)))
        end
      end
    end
  end
  local res = s.find_entities_filtered({ area = area, type = "resource" })
  if res[1] then
    used = used + #res * U_RESOURCE
    local by_name = {}
    for _, e in pairs(res) do
      local p = e.position
      if e.prototype.resource_category == "basic-fluid" then  -- (oil: each one a pumpjack's spot, and its yield)
        snap.fluid_resources[#snap.fluid_resources + 1] = { name = e.name, x = p.x, y = p.y, amount = e.amount }
      end
      local l = by_name[e.name]
      if not l then l = {} by_name[e.name] = l end
      l[#l + 1] = p
    end
    for name, l in pairs(by_name) do
      snap.resources[name] = snap.resources[name] or {}
      add_tiles(snap.resources[name], l)
    end
  end
  local water = s.find_tiles_filtered({ area = area, collision_mask = "water_tile" })
  if water[1] then
    local pos = {}
    for i, t in ipairs(water) do pos[i] = t.position end
    add_tiles(snap.water, pos)
    used = used + #water * U_TILE
  end
  if cb then  -- (cell by cell: only the last MEASURE_TICKS' belts are held, not a big base's 45000)
    r.waiting[#r.waiting + 1] = { at = game.tick, belts = cb }
    if sink then sink.waiting[#r.waiting] = sb end
  end
  return used
end

--- the belts of cells read MEASURE_TICKS ago and more looked at again, their flows written -> units used
local function measure_due(r, sink, budget)
  local used = 0
  while r.wi <= #r.waiting and used < budget do
    local w = r.waiting[r.wi]
    if game.tick < w.at + M.MEASURE_TICKS then break end
    local sb = sink and sink.waiting[r.wi]
    for i, e in ipairs(w.belts) do
      local text
      if sb then
        local more = ""
        if e.valid then
          local list, now = {}, {}
          belt_items(e, list)
          for k = 1, #list, 2 do now[list[k]] = list[k + 1] end
          local n, moved, ids = 0, 0, sb.ids
          for k = sb.from[i], sb.to[i], 2 do
            n = n + 1
            if now[ids[k]] ~= ids[k + 1] then moved = moved + 1 end
          end
          more = fmt(',"items":%d,"flow":%s', n, num(math.floor(e.prototype.belt_speed * 60 * moved * 10 + 0.5) / 10))
        end
        local t = sb.texts[i]
        text = sb.open[i] and (t .. more .. "}]") or (t:sub(1, -2) .. ",{" .. more:sub(2) .. "}]")
      end
      used = used + U_FLOW + row(r, sink, "E", text)
    end
    r.waiting[r.wi] = false
    if sink then sink.waiting[r.wi] = nil end
    r.wi = r.wi + 1
  end
  return used
end

M.done = function(reader) end  -- (control.lua: what follows a reading)
M.progress = function(reader, fraction) end

local function step(r, budget)
  local s, force = game.get_surface(r.surface), game.forces[r.force]
  local sink = sinks[r.id]
  if not (s and force) then
    sinks[r.id] = nil
    return 1, true
  end
  local used = 0
  if r.waiting then used = measure_due(r, sink, budget / 2) end
  if r.phase == "cells" then
    while used < budget and r.ci <= r.ncells do
      used = used + read_cell(r, sink, s, force, r.ci)
      r.ci = r.ci + 1
    end
    if r.ci > r.ncells then
      r.phase, r.pi = "poles", 1
    elseif game.tick % 10 == 0 then
      M.progress(r, (r.ci - 1) / r.ncells * (r.belts and 0.9 or 1))
    end
  end
  if r.phase == "poles" then
    while r.pi <= r.npoles and used < budget do
      local text
      if sink then
        local p = sink.poles[r.pi]
        sink.poles[r.pi] = nil
        p.powered = p.network ~= nil and r.powered[p.network] == true
        text = rec_json(p)
      end
      used = used + U_RECORD + row(r, sink, "E", text)
      r.pi = r.pi + 1
    end
    if r.pi > r.npoles then r.phase = r.belts and "flows" or "done" end
  end
  if r.phase == "flows" then  -- (the last cells' belts: waited for)
    used = used + measure_due(r, sink, budget - used)
    if r.wi > #r.waiting then r.phase, r.waiting = "done", nil end
  end
  if r.phase == "done" then
    -- (what's left of each kind: a part each when there is any)
    for _, kind in ipairs({ "E", "O" }) do
      if r[kind == "E" and "ecount" or "ocount"] % BLOCK ~= 0 then
        r.nparts = r.nparts + 1
        if sink then M.flush(r, sink, kind) end
      end
    end
    local snap = r.snap
    snap["@parts"], snap.n, snap.count = "r" .. r.id, r.nparts, r.count
    sinks[r.id] = nil
    if sink and sink.keep then kept_of[snap] = sink.keep end  -- (this peer's own: not saved)
    if sink and sink.send then sent[snap["@parts"]] = true end
    M.done(r)
    return used + U_PART, true
  end
  return used, false
end
tasks.kind("read", step)

--- starts reading `area` ({left_top, right_bottom}) of `surface` for `force`; `flows`: belts measured too.
--- `more` is kept on the reader (what follows). `send(text)`: this peer hands the parts on (it runs the job);
--- `keep`: this peer keeps them (a file, tests). -> the reader (r.snap: the snapshot, filled as it goes)
function M.start(surface, force, area, flows, more, send, keep)
  local a = { area.left_top.x or area.left_top[1], area.left_top.y or area.left_top[2],
    area.right_bottom.x or area.right_bottom[1], area.right_bottom.y or area.right_bottom[2] }
  local r = more or {}
  r.surface, r.force, r.phase, r.ci, r.bi = surface.name, force.name, "cells", 1, 1
  r.gx0, r.gy0 = math.floor(a[1] / CELL), math.floor(a[2] / CELL)
  r.gx1, r.gy1 = math.max(r.gx0, math.ceil(a[3] / CELL) - 1), math.max(r.gy0, math.ceil(a[4] / CELL) - 1)
  r.ncells = (r.gx1 - r.gx0 + 1) * (r.gy1 - r.gy0 + 1)
  r.powered, r.belts, r.waiting, r.wi = {}, flows or nil, flows and {} or nil, 1
  r.count, r.npoles, r.ecount, r.ocount, r.nparts = 0, 0, 0, 0, 0
  r.snap = { tick = game.tick, surface = surface.name, area = a, resources = {}, fluid_resources = {}, water = {} }
  tasks.start("read", r)
  if send or keep then
    sinks[r.id] = { send = send, keep = keep and {} or nil, rows = { E = {}, O = {} }, poles = {}, waiting = {} }
  end
  return r
end

--- the whole area read now, its parts kept (tests; the snapshot written for the web app)
function M.read_now(surface, force, area)
  local r = M.start(surface, force, area, false, { now = true }, nil, true)
  local kept
  local done = M.done
  M.done = function(x)
    if x == r then kept = kept_of[x.snap] else done(x) end
  end
  tasks.finish(r)
  M.done = done
  return r.snap, kept
end

-- (control.lua, in M.done) the parts this peer kept of a snapshot just read, or nil
function M.kept(snap) return kept_of[snap] end

-- the snapshot's text -------------------------------------------------------------------------------------------

--- the snapshot as one JSON text, from its parts kept by this peer (a file: the web app's, tests'), in pieces of
--- about a quarter MB, their text joined being the JSON -> {pieces}
function M.pieces(snap, kept)
  local meta = {}
  for k, v in pairs(snap) do
    if k ~= "@parts" and k ~= "n" and k ~= "count" then meta[k] = v end
  end
  local head = helpers.table_to_json(meta)
  local out = { head:sub(1, -2) .. (next(meta) and "," or "") .. '"entities":[' }
  for _, kind in ipairs({ "E", "O" }) do
    local first = true
    for _, p in ipairs(kept or {}) do
      if p[1] == kind then
        if not first then out[#out + 1] = "," end  -- (the parts as they are: not copied)
        out[#out + 1] = p[2]
        first = false
      end
    end
    out[#out + 1] = kind == "E" and '],"obstacles":[' or "]}"
  end
  return out
end

--- pieces written to a file, a quarter MB a tick (`now`: at once)
function M.write_file(name, pieces, now)
  local i = 1
  local function work(budget)
    local used = 0
    while pieces[i] and used < budget do
      helpers.write_file(name, pieces[i], i > 1)
      used = used + 1 + #pieces[i] / 500  -- (writing: ~2 us a KB; a disk now and then far slower)
      i = i + 1
    end
    return used, not pieces[i]
  end
  if now then
    work(math.huge)
  else
    tasks.later(work)
  end
end

-- requests for Python ---------------------------------------------------------------------------------------


--- whether this peer's Python has the snapshot's parts (sent from this Lua state: not after a load)
function M.sent(snap) return snap and snap["@parts"] ~= nil and sent[snap["@parts"]] == true end

--- a request for Python as JSON: its snapshot's key made this Lua state's own; a site's delta (.delta, records)
--- written a few hundred records a tick (tasks.later); done(text)
function M.request_json(req, done, now)
  local copy = {}
  for k, v in pairs(req) do copy[k] = v end
  local function own(snap)  -- (the key Python has the parts by)
    if type(snap) ~= "table" or not snap["@parts"] then return snap end
    local c = {}
    for k, v in pairs(snap) do c[k] = v end
    c["@parts"] = key_of({ id = tonumber(snap["@parts"]:sub(2)) })
    return c
  end
  copy.snapshot = own(copy.snapshot)
  if type(copy.params) == "table" and copy.params.snapshot then
    local params = {}
    for k, v in pairs(copy.params) do params[k] = v end
    params.snapshot = own(params.snapshot)
    copy.params = params
  end
  local delta = type(copy.delta) == "table" and copy.delta.built and #copy.delta.built > 50 and copy.delta
  if not delta then return done(helpers.table_to_json(copy)) end
  -- (a big delta's records: written over ticks)
  local d = {}
  for k, v in pairs(delta) do d[k] = v end
  local built, rows, i = delta.built, {}, 1
  d.built = "@@built@@"
  copy.delta = d
  local function work(budget)
    local used = 0
    while built[i] and used < budget do
      rows[i] = rec_json(built[i])
      i, used = i + 1, used + U_RECORD
    end
    if built[i] then return used, false end
    local head = helpers.table_to_json(copy)
    local a, b = head:find('"@@built@@"', 1, true)
    done(head:sub(1, a - 1) .. "[" .. concat(rows, ",") .. "]" .. head:sub(b + 1))
    return used, true
  end
  if now then
    work(math.huge)
  else
    tasks.later(work)
  end
end

return M
