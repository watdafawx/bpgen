-- The bpgen window as a web page over the game (fse-std's shells: the fse loader's web plugin, single player). The
-- page (web/bpgen.html) is the whole window; the game shows through it in engine islands: the blueprint preview (the
-- window's camera on its preview surface). The pickers are the page's own (a search, the item groups, an icon grid):
-- the game's choose-elem popup would open under the page. What they offer is what the hidden choose-elem button's
-- filters let through ("Only unlocked" included), read with the prototypes' filter functions.
--
-- The Lua window stays the model: window.lua builds it as always, hidden (inside layout, no rails), and every path
-- (planning, Python jobs, test runs, placing) runs as it does for the Lua window. This file only
--   * mirrors it to the page: a snapshot of its state (fields, results, the test run, history) sent with shell.send
--     whenever something changed (captions as LocalisedStrings: the page formats them with the mod's locale files and
--     asks for the rest, prototype names and such, which this file translates);
--   * hands the page's actions to it: a field set (then the same handler the game's event would run), a button's tags
--     clicked (window.lua's on_click);
--   * answers the page's pickers: the choices a hidden choose-elem button's filters allow; a pick is written to it
--     and handled as the player's own change.
-- Without the shell (no loader, multiplayer, the per-player setting off, WebView2 missing) the Lua window is shown as
-- before.

local M = {}
local SHELL = "bpgen"
local PAGE = "__zzz-bpgen__/web/bpgen.html"
local SIZE = { 1240, 740 }    -- (the page's size at 100 %, GUI units; smaller when the screen has less room)
local HOTBAR_H = 100          -- (the hotbar band the window keeps clear of, GUI units)

local shell
if script.active_mods["fse-std"] then
  local ok, s = pcall(require, "__fse-std__/shell")
  if ok and type(s) == "table" and s.open then shell = s end
end
M.shell = shell

local W            -- window.lua's internals (setup)
local bench = require("bench")

function M.setup(w) W = w end

-- per player: storage.bpgen_web[i] = {on = the hidden Lua window is the model, picks = {island id -> ref},
-- toasts = {{n, text}}, n, view = {w, h} the preview's island in pixels, rect = where the shell was, scale}
local function st(index)
  storage.bpgen_web = storage.bpgen_web or {}
  local s = storage.bpgen_web[index]
  if not s then
    s = { picks = {}, toasts = {}, n = 0 }
    storage.bpgen_web[index] = s
  end
  return s
end

local dirty, last, gen, seen_gen = {}, {}, {}, {}   -- (this session: a snapshot to send, the last one sent, filters)
local failed = {}                                   -- (player index -> the shell couldn't open: the Lua window then)
local pending = {}                                  -- (translation id -> key)
local dict_out = {}                                 -- (player index -> {key = text} to send)

--- the shell can open here (the loader's web plugin, single player)
function M.available()
  return shell ~= nil and shell.available() and shell.island ~= nil
end

--- the player wants the web window (the per-player setting, default on)
function M.wanted(player)
  local s = player.mod_settings["bpgen-web-window"]
  return not s or s.value ~= false
end

function M.set_wanted(player, on)
  player.mod_settings["bpgen-web-window"] = { value = on == true }
end

--- the bpgen window opens as the web page now
function M.use(player)
  return M.available() and M.wanted(player) and not failed[player.index]
end

--- the hidden Lua window is the model of the player's web window
function M.model(player)
  local s = storage.bpgen_web and storage.bpgen_web[player.index]
  return s ~= nil and s.on == true
end

function M.set_model(player, on)
  st(player.index).on = on or nil
end

--- something in the window changed: a snapshot goes to the page on the next tick
function M.dirty(player)
  if player and M.model(player) then dirty[player.index] = true end
end

--- the pickers' filters changed (window.restrict): copied to their mirrors with the next snapshot
function M.filters_changed(player)
  if player then gen[player.index] = (gen[player.index] or 0) + 1 end
  M.dirty(player)
end

--- an element by name in the shell's islands (the preview's camera, bpgen_cam)
function M.find(player, name)
  if not (shell and M.model(player)) then return nil end
  local function find_in(el)
    if el.name == name then return el end
    for _, c in pairs(el.children) do
      local f = find_in(c)
      if f then return f end
    end
  end
  for _, f in pairs(shell.islands(player, SHELL)) do
    if f.valid then
      local hit = find_in(f)
      if hit then return hit end
    end
  end
end

--- the preview's size in pixels (its island), or nil (not the web window, or not laid out yet)
function M.view(player)
  local s = M.model(player) and storage.bpgen_web[player.index]
  local v = s and s.view
  if v and v[1] > 0 then return v[1], v[2] end
end

--- a message for the player (as fse-std's toast in the Lua window): shown by the page
function M.toast(player, text)
  local s = st(player.index)
  s.n = s.n + 1
  s.toasts[#s.toasts + 1] = { n = s.n, text = text }
  while #s.toasts > 4 do table.remove(s.toasts, 1) end
  M.dirty(player)
end

--- History's list arrived (window.lua's on_api): the page opens its drawer
function M.history(player)
  st(player.index).history_n = (st(player.index).history_n or 0) + 1
  M.dirty(player)
end

--- the shell shown or hidden (a tool in hand: the window out of the way)
function M.show(player, on)
  if shell and shell.is_open(player, SHELL) then shell.show(player, SHELL, on) end
end

-- ---- where the window goes ---------------------------------------------------------------------------------------

local function own_scale(player)
  local s = st(player.index).scale
  if s then return s end
  local ok, theme = pcall(require, "__fse-std__/theme")
  return ok and theme.setting_scale and theme.setting_scale(player) or 1
end

--- {x, y, w, h} pixels: where it was (if it still fits the screen), else centred above the hotbar
local function rect_for(player)
  local res, ds = player.display_resolution, player.display_scale
  local s = st(player.index)
  local r = s.rect
  if r and r[1] >= 0 and r[2] >= 0 and r[1] + r[3] <= res.width and r[2] + r[4] <= res.height then return r end
  local f = ds * own_scale(player)
  local free_h = res.height - HOTBAR_H * ds
  local w = math.min(math.floor(SIZE[1] * f), res.width - 16)
  local h = math.min(math.floor(SIZE[2] * f), free_h - 16)
  return { math.floor((res.width - w) / 2), math.max(0, math.floor((free_h - h) / 2)), w, h }
end

-- ---- the hidden window's state, for the page ---------------------------------------------------------------------

local KINDS = { textfield = true, checkbox = true, ["drop-down"] = true }

-- the elements of the hidden window by name (one walk)
local function index_of(frame)
  local named = {}
  local function walk(el)
    local n = el.name
    if n ~= "" and not named[n] then named[n] = el end
    for _, c in pairs(el.children) do walk(c) end
  end
  walk(frame)
  return named
end

local function ls(x)  -- (a caption as the page gets it: a LocalisedString, nil for none)
  if x == nil or x == "" then return nil end
  return x
end

local function value_key(el)
  local v = el.elem_value
  if v == nil then return nil end
  local t = el.elem_type:gsub("%-with%-quality$", "")
  if type(v) == "table" then
    return t .. "/" .. tostring(v.name), v.quality ~= "normal" and v.quality or nil
  end
  return t .. "/" .. v
end

local function badges_of(el)
  local out = {}
  for _, row in pairs(el and el.children or {}) do
    for _, b in pairs(row.children) do
      local l = b.children[1]
      if l then out[#out + 1] = { t = ls(l.caption), tip = ls(b.tooltip) } end
    end
  end
  return out
end

local function badge_text(el)  -- (a fold's badge: its text while it shows)
  if not (el and el.valid and el.visible) then return nil end
  local l = el.children[1]
  return l and ls(l.caption)
end

local function progress_of(bar)
  if not (bar and bar.valid) then return nil end
  local row = bar.parent.children[1]
  local text = row and row.fstd_progress_text
  return { v = bar.value, text = text and ls(text.caption) }
end

-- a part of the window as plain rows (the result's extras: what's short, the bus report, module ideas, can it build)
local function tree(el, depth)
  depth = depth or 0
  if depth > 6 or not el.visible then return nil end
  local t = el.type
  if t == "label" then return { k = "label", text = ls(el.caption), tip = ls(el.tooltip) } end
  if t == "button" then
    return { k = "button", text = ls(el.caption), tip = ls(el.tooltip), tags = el.tags, en = el.enabled }
  end
  if t == "empty-widget" then return { k = "gap" } end
  if t == "table" or t == "flow" or t == "frame" then
    local kids = {}
    for _, c in pairs(el.children) do kids[#kids + 1] = tree(c, depth + 1) end
    return { k = t == "table" and "table" or "flow", cols = t == "table" and el.column_count or nil,
      dir = t ~= "table" and el.direction or nil, kids = kids }
  end
end


local function snapshot(player, frame)
  local named = index_of(frame)
  local s = st(player.index)
  local out = { locale = player.locale, f = {}, dd = {}, pk = {}, vis = {} }
  local picks = {}   -- ref -> hidden element
  local function picker(ref, el)
    picks[ref] = el
    local v, q = value_key(el)
    local kind, name = (v or ""):match("^([%w%-]+)/(.+)$")
    local proto = kind and prototypes[kind] and prototypes[kind][name]
    out.pk[ref] = { v = v, q = q, t = el.elem_type, ln = proto and proto.localised_name or nil }
  end
  for name, el in pairs(named) do
    if name:sub(1, 6) == "bpgen_" then
      local t = el.type
      if KINDS[t] then
        if t == "textfield" then out.f[name] = el.text
        elseif t == "checkbox" then out.f[name] = el.state
        else
          out.f[name] = el.selected_index
          out.dd[name] = el.items
        end
      elseif t == "choose-elem-button" then
        picker(name, el)
      end
    end
  end
  local tabs = named.bpgen_tabs
  out.mode = tabs and tabs.tags.mode or "line"
  out.tip = named.bpgen_mode_tip and ls(named.bpgen_mode_tip.caption)
  out.only_unlocked = storage.bpgen_state and storage.bpgen_state.only_unlocked
    and storage.bpgen_state.only_unlocked[player.index] == true or false
  for _, n in ipairs({ "bpgen_more", "bpgen_next_box", "bpgen_base_tools", "bpgen_progress", "bpgen_result",
                       "bpgen_test_box", "bpgen_stale", "bpgen_also_near", "bpgen_section", "bpgen_tiles" }) do
    local el = named[n]
    out.vis[n] = el ~= nil and el.visible
  end
  -- what to make in the blueprint: a row per ingredient (ticked, its recipe's picker, on belt / made here)
  local make = {}
  for _, r in pairs(named.bpgen_make and named.bpgen_make.children or {}) do
    if r.type == "label" then
      make[#make + 1] = { text = ls(r.caption) }
    else
      local first = r.children[1]
      local row = { text = ls(first.caption), note = r.bpgen_where and ls(r.bpgen_where.caption) }
      if first.type == "checkbox" then
        row.item, row.on = first.tags.item, first.state
        local p = r.children[2]
        if p and p.type == "choose-elem-button" then
          row.pick = "make_pick:" .. first.tags.item
          picker(row.pick, p)
        end
      end
      make[#make + 1] = row
    end
  end
  out.make = make
  -- also make: ticked siblings, each with its rate; the near ones behind a toggle
  local function also_rows(box)
    local rows = {}
    for _, r in pairs(box and box.children or {}) do
      if r.type == "label" then
        rows[#rows + 1] = { text = ls(r.caption) }
      elseif r.type == "checkbox" and r.tags.bpgen_toggle then
        rows[#rows + 1] = { toggle = r.tags.bpgen_toggle, on = r.state, text = ls(r.caption) }
      elseif r.type == "flow" and r.name == "bpgen_also_near" then
        rows[#rows + 1] = { near = also_rows(r) }
      elseif r.type == "flow" then
        local cb, rate = r.children[1], r.children[#r.children]
        if cb and cb.type == "checkbox" then
          rows[#rows + 1] = { item = cb.tags.item, on = cb.state, text = ls(cb.caption),
            rate = rate and rate.type == "textfield" and rate.text or nil }
        end
      end
    end
    return rows
  end
  out.also = also_rows(named.bpgen_also)
  out.badge = { also = badge_text(named.bpgen_also_badge), modules = badge_text(named.bpgen_modules_badge),
    tune = badge_text(named.bpgen_tune_badge) }
  -- the mall's products: a picker each, an empty one at the end
  local prods = {}
  for i, b in ipairs(named.bpgen_products and named.bpgen_products.children or {}) do
    local ref = "prod:" .. i
    picker(ref, b)
    prods[#prods + 1] = ref
  end
  out.products = prods
  out.patch_note = named.bpgen_patch_note and ls(named.bpgen_patch_note.caption)
  out.snap_note = named.bpgen_snap_note and ls(named.bpgen_snap_note.caption)
  -- planning, Plan, the status line
  out.prog = progress_of(named.bpgen_progress_bar)
  if out.prog then out.prog.log = named.bpgen_log and named.bpgen_log.caption or "" end
  out.status = named.bpgen_status and ls(named.bpgen_status.caption)
  -- the result
  local tiles = {}
  for _, t in pairs(named.bpgen_tiles and named.bpgen_tiles.children or {}) do
    local v = t.fstd_stat_value
    local c = t.children[2]
    tiles[#tiles + 1] = { v = v and ls(v.caption), c = c and ls(c.caption) }
  end
  out.tiles = tiles
  out.badges = badges_of(named.bpgen_badges)
  out.notes = named.bpgen_notes and ls(named.bpgen_notes.caption)
  out.stats = named.bpgen_stats and ls(named.bpgen_stats.caption)
  local extra = named.bpgen_extra and tree(named.bpgen_extra)
  out.extra = extra and #extra.kids > 0 and extra or nil
  local cam = M.find(player, "bpgen_cam")
  out.preview = { on = cam ~= nil and cam.visible, empty = named.bpgen_empty and ls(named.bpgen_empty.caption) }
  -- the actions
  local act = {}
  for _, n in ipairs({ "cursor", "copy", "place", "place_base", "base_spot", "base_rotate", "base_redo", "nudge_left",
                       "test", "test_speed", "plan" }) do
    local el = named["bpgen_" .. n]
    if el then act[n] = { en = el.enabled, vis = el.visible, c = ls(el.caption), tip = ls(el.tooltip) } end
  end
  out.act = act
  -- the test run: its bar, what it says, the rate each second (the chart) and the rate it should reach
  local r = W.results[player.index]
  local run = bench.find("test", player.index)
  out.test = { bar = progress_of(named.bpgen_test_bar), status = named.bpgen_test_status
    and ls(named.bpgen_test_status.caption), series = W.test_series[player.index] or {}, running = run ~= nil,
    expected = run and run.expected and run.expected * 60 or (r and r.res and r.res.expected and r.res.expected * 60),
    mall = run and run.products ~= nil, speed = game.speed }
  out.busy = (out.vis.bpgen_progress == true) or run ~= nil
  out.ghosts = W.ghosts_left(player)
  out.has_plan = r ~= nil and r.blueprint ~= nil
  out.size = r and r.w and { math.ceil(r.w), math.ceil(r.h) } or nil
  -- history (once asked for)
  if s.history_n then
    local h = {}
    for i, it in ipairs(W.histories[player.index] or {}) do
      if i > 60 then break end
      h[#h + 1] = { label = it.label, recipe = it.recipe and prototypes.recipe[it.recipe] and it.recipe or nil,
        entities = it.entities, book = it.book or nil, mode = it.mode }
    end
    out.history = h
    out.history_n = s.history_n
  end
  out.toasts = s.toasts
  out.scale = s.scale
  out.dock_note = nil
  return out, picks
end

-- (picker islands of an older bpgen: gone, the page has its own pickers now)
local function drop_old_islands(player)
  for id in pairs(shell.islands(player, SHELL)) do
    if id:sub(1, 3) == "pk_" then shell.remove_island(player, SHELL, id) end
  end
end

local function push(player)
  local frame = player.gui.screen[W.NAME]
  if not (frame and shell.is_open(player, SHELL)) then return end
  local snap, picks = snapshot(player, frame)
  if shell.remove_island then drop_old_islands(player) end
  local text = helpers.table_to_json(snap)
  if text == last[player.index] then return end
  last[player.index] = text
  shell.send(player, SHELL, { state = snap })
end

-- ---- the page's actions ------------------------------------------------------------------------------------------

--- a hidden element by the page's ref: a name, or a dynamic row's ("make_on:<item>", "make_pick:<item>",
--- "also_on:<item>", "also_rate:<item>", "toggle:<target>", "prod:<i>")
local function resolve(player, ref)
  local frame = player.gui.screen[W.NAME]
  if not frame then return nil end
  local kind, arg = ref:match("^([%w_]+):(.+)$")
  if not kind then return W.find(frame, ref) end
  if kind == "make_on" or kind == "make_pick" then
    for _, r in pairs((W.find(frame, "bpgen_make") or {}).children or {}) do
      local first = r.children[1]
      if first and first.type == "checkbox" and first.tags.item == arg then
        return kind == "make_on" and first or r.children[2]
      end
    end
  elseif kind == "prod" then
    local t = W.find(frame, "bpgen_products")
    return t and t.children[tonumber(arg)]
  else
    local function search(box)
      for _, r in pairs(box.children) do
        if kind == "toggle" and r.type == "checkbox" and r.tags.bpgen_toggle == arg then return r end
        if r.type == "flow" then
          local cb = r.children[1]
          if cb and cb.type == "checkbox" and cb.tags.item == arg and (kind == "also_on" or kind == "also_rate") then
            return kind == "also_on" and cb or r.children[#r.children]
          end
          local deeper = search(r)
          if deeper then return deeper end
        end
      end
    end
    local box = W.find(frame, "bpgen_also")
    return box and search(box)
  end
end

local function raise(ev, el, player, extra)
  local h = W.handlers[ev]
  local e = { element = el, player_index = player.index, name = ev, tick = game.tick }
  for k, v in pairs(extra or {}) do e[k] = v end
  if h then h(e) end
end

local function set(player, ref, value, confirm)
  local el = resolve(player, ref)
  if not (el and el.valid) then return end
  if el.type == "textfield" then
    local ok = pcall(function() el.text = tostring(value or "") end)
    if not ok then return end
    raise(defines.events.on_gui_text_changed, el, player, { text = el.text })
    if confirm then raise(defines.events.on_gui_confirmed, el, player) end
  elseif el.type == "checkbox" then
    el.state = value == true
    raise(defines.events.on_gui_checked_state_changed, el, player)
  elseif el.type == "drop-down" then
    local i = tonumber(value)
    if not i or i < 1 or i > #el.items then return end
    el.selected_index = i
    raise(defines.events.on_gui_selection_state_changed, el, player)
  end
end

--- translations the page asked for (keys of other mods' locale: prototype names and the like)
local function translate(player, keys)
  for _, k in ipairs(keys or {}) do
    if type(k) == "string" and #k < 200 then
      local id = player.request_translation({ k })
      if id then pending[id] = k end
    end
  end
end

local icon_map
--- the icons' files ("<mod>/<path>|<size>") for "type/name" keys; a recipe without its own: its main product's
local function icons(keys)
  if icon_map == nil then
    local ok, md = pcall(function() return prototypes.mod_data["bpgen-icons"] end)
    icon_map = ok and md and md.data or false
  end
  local out = {}
  for _, k in ipairs(keys or {}) do
    local t, name = tostring(k):match("^([%w%-]+)/(.+)$")
    local found
    if icon_map and t then
      local function get(kind, n) return icon_map[kind] and icon_map[kind][n] end
      found = get(t, name)
      if not found and t == "recipe" then
        local r = prototypes.recipe[name]
        local p = r and (r.main_product or r.products[1])
        found = p and get(p.type, p.name)
      end
      if not found and (t == "entity" or t == "recipe") then found = get("item", name) end
      if not found and t == "item" then found = get("entity", name) end
    end
    out[k] = found or false
  end
  return out
end

local FILTERED = { recipe = "get_recipe_filtered", entity = "get_entity_filtered", item = "get_item_filtered",
  fluid = "get_fluid_filtered" }
local ALL = { recipe = "recipe", entity = "entity", item = "item", fluid = "fluid" }

--- what a picker offers: the prototypes its (hidden) choose-elem button's filters let through, in the game's order
--- (item groups, subgroups); its value; the qualities when it takes one
local function choices(player, ref)
  local src = resolve(player, ref)
  if not (src and src.valid and src.type == "choose-elem-button") then return nil end
  local t = src.elem_type:gsub("%-with%-quality$", "")
  if not FILTERED[t] then return nil end
  local filters = src.elem_filters
  local ok, list = pcall(function()
    if filters and #filters > 0 then return prototypes[FILTERED[t]](filters) end
    return prototypes[ALL[t]]
  end)
  if not ok then list = prototypes[ALL[t]] end
  local rows, groups, seen = {}, {}, {}
  for name, p in pairs(list) do
    local hidden = false
    pcall(function() hidden = p.hidden or (p.flags and p.flags["hidden"]) or false end)
    if not hidden and (t ~= "entity" or not p.items_to_place_this or p.items_to_place_this[1]) then
      local g, sg
      pcall(function() g, sg = p.group, p.subgroup end)
      if g and not seen[g.name] then
        seen[g.name] = true
        groups[#groups + 1] = { n = g.name, o = g.order, ln = g.localised_name }
      end
      rows[#rows + 1] = { n = name, ln = p.localised_name, g = g and g.name or "", k = (g and g.order or "") .. "|" ..
        (sg and sg.order or "") .. "|" .. (sg and sg.name or "") .. "|" .. (p.order or "") .. "|" .. name }
    end
  end
  table.sort(rows, function(a, b) return a.k < b.k end)
  table.sort(groups, function(a, b) return (a.o or "") < (b.o or "") end)
  local out = { ref = ref, type = t, rows = rows, groups = groups }
  for _, r in ipairs(rows) do r.k = nil end
  local v = src.elem_value
  out.value = type(v) == "table" and v.name or v
  if src.elem_type:find("with%-quality") then
    out.quality = type(v) == "table" and v.quality or "normal"
    local q = {}
    for name, p in pairs(prototypes.quality) do
      if not p.hidden then q[#q + 1] = { n = name, ln = p.localised_name, l = p.level } end
    end
    table.sort(q, function(a, b) return a.l < b.l end)
    out.qualities = q
  end
  return out
end

--- a pick in the page's picker: written to the hidden choose-elem button, handled as the player's change
local function pick(player, ref, value, quality)
  local src = resolve(player, ref)
  if not (src and src.valid and src.type == "choose-elem-button") then return false end
  local t = src.elem_type:gsub("%-with%-quality$", "")
  if value ~= nil and not (prototypes[ALL[t] or t] and prototypes[ALL[t] or t][value]) then return false end
  if value == nil then src.elem_value = nil
  elseif src.elem_type:find("with%-quality") then
    src.elem_value = { name = value, quality = prototypes.quality[quality or ""] and quality or "normal" }
  else
    src.elem_value = value
  end
  raise(defines.events.on_gui_elem_changed, src, player)
  return true
end

local function handle(player, m)
  if type(m) ~= "table" then return end
  local cmd = m.cmd
  local frame = player.gui.screen[W.NAME]
  if cmd == "set" and type(m.ref) == "string" then
    set(player, m.ref, m.value, m.confirm)
  elseif cmd == "click" and type(m.tags) == "table" and frame then
    -- (the page asked first where the game would, Undo: scripted)
    W.on_click({ element = { valid = true, tags = m.tags }, player_index = player.index, scripted = true,
      shift = m.shift == true, button = defines.mouse_button_type.left })
  elseif cmd == "close" then
    W.close(player)
    return
  elseif cmd == "lua" then  -- (the game's own window instead)
    M.set_wanted(player, false)
    W.rebuild_any(player)
    return
  elseif cmd == "only_unlocked" then
    W.set_only_unlocked(player, m.on == true)
  elseif cmd == "theme" then
    if remote.interfaces["fse-std"] and remote.interfaces["fse-std"].open_theme then
      remote.call("fse-std", "open_theme", player.index)
    end
  elseif cmd == "scale" then  -- (the window's own Size; nil: fse-std's Window size)
    local v = tonumber(m.value)
    st(player.index).scale = v and v > 0 and v or nil
    st(player.index).rect = nil
    if shell.is_open(player, SHELL) then
      shell.move(player, SHELL, { rect = rect_for(player), scale = st(player.index).scale or false })
    end
  elseif cmd == "reset" then
    st(player.index).rect = nil
    if shell.is_open(player, SHELL) then shell.move(player, SHELL, { rect = rect_for(player) }) end
  elseif cmd == "tr" then
    translate(player, m.keys)
    return
  elseif cmd == "icons" then
    shell.send(player, SHELL, { icons = icons(m.keys) })
    return
  elseif cmd == "choices" and type(m.ref) == "string" then
    shell.send(player, SHELL, { choices = choices(player, m.ref) or { ref = m.ref, rows = {} } })
    return
  elseif cmd == "pick" and type(m.ref) == "string" then
    pick(player, m.ref, m.value, m.quality)
  elseif cmd == "history_use" and m.i then
    W.history_use(player, tonumber(m.i), m.target)
  elseif cmd == "rects" then  -- (tests: where the page's controls are, CSS px)
    st(player.index).page = { rects = m.rects, dpr = m.dpr, gu = m.gu, drawer = m.drawer, ready = m.ready }
    return
  elseif cmd == "seen" then  -- (the page's own state, for tests: what it shows)
    st(player.index).seen = m.what
    return
  end
  dirty[player.index] = true
end

-- ---- opening and closing -----------------------------------------------------------------------------------------

--- the shell opened (or kept) over the hidden window just built: the preview's island, a snapshot on the next tick
function M.attach(player, frame)
  frame.visible = false
  -- (the Lua window's own camera goes: the island's is the preview, found by the same name)
  local function find_in(el)
    if el.name == "bpgen_cam" then return el end
    for _, c in pairs(el.children) do local x = find_in(c) if x then return x end end
  end
  local own = find_in(frame)
  if own then own.destroy() end
  local s = st(player.index)
  local was_open = shell.is_open(player, SHELL)
  if not was_open then
    local ok = shell.open(player, { name = SHELL, page = PAGE, rect = rect_for(player), esc = false, yield = true,
      radius = 8, scale = s.scale })
    if not ok then
      M.set_model(player, false)
      failed[player.index] = true
      return false
    end
  end
  local island = shell.island(player, SHELL, "preview", function(f)
    local cam = f.add({ type = "camera", name = "bpgen_cam", position = { 0, 0 }, surface_index = player.surface.index,
      zoom = 1, raise_hover_events = true })
    cam.visible = false
  end)
  local cam = island.bpgen_cam
  if cam then cam.visible = false end
  seen_gen[player.index] = nil  -- (the mirrors' filters copied again)
  last[player.index] = nil
  dirty[player.index] = true
  return true
end

local closing = false  -- (this file closes the shell: on_close isn't the player's)

--- the shell closed (the window closes, or opens as the Lua one): where it was kept for next time
function M.close(player)
  local s = storage.bpgen_web and storage.bpgen_web[player.index]
  if s then
    s.on, s.picks, s.view = nil, {}, nil
    s.history_n = nil
  end
  if shell and shell.is_open(player, SHELL) then
    if s then s.rect = shell.rect and shell.rect(player, SHELL) or s.rect end
    closing = true
    shell.close(player, SHELL)
    closing = false
  end
  last[player.index], dirty[player.index] = nil, nil
end

-- ---- events ------------------------------------------------------------------------------------------------------

if shell then
  shell.on_message(function(player, name, m)
    if name == SHELL and M.model(player) then handle(player, m) end
  end)
  shell.on_ready(function(player, name)
    if name ~= SHELL then return end
    last[player.index] = nil
    dirty[player.index] = true
  end)
  shell.on_island(function(player, name, id, frame, w, h)
    if name ~= SHELL or id ~= "preview" then return end
    local cam = frame.bpgen_cam
    if cam then cam.style.width, cam.style.height = w, h end
    local ds = player.display_scale
    st(player.index).view = { math.floor(w * ds + 0.5), math.floor(h * ds + 0.5) }
    W.view_resized(player)
  end)
  shell.on_close(function(player, name, why)
    if name ~= SHELL or closing or why == "mod" then return end
    if not M.model(player) then return end
    -- (the page closed itself, or the shell couldn't open: WebView2 missing. Then the Lua window, this session)
    local broke = why ~= "page" and why ~= "esc" and why ~= "close"
    if broke then
      failed[player.index] = true
      W.rebuild_any(player)
    else
      W.close(player)
    end
  end)
end

M.handlers = {
  [defines.events.on_tick] = function(e)
    if not (storage.bpgen_web and shell) then return end
    for index, s in pairs(storage.bpgen_web) do
      if s.on then
        local player = game.get_player(index)
        if player and player.connected then
          if not M.available() then
            -- (a save with the web window loaded where shells don't work: the Lua window instead)
            if e.tick % 60 == 0 then W.rebuild_any(player) end
          elseif dirty[index] or e.tick % 60 == 0 then
            dirty[index] = nil
            push(player)
          end
          if dict_out[index] then
            if shell.is_open(player, SHELL) then shell.send(player, SHELL, { dict = dict_out[index] }) end
            dict_out[index] = nil
          end
        end
      end
    end
  end,
  [defines.events.on_string_translated] = function(e)
    local key = pending[e.id]
    if not key then return end
    pending[e.id] = nil
    dict_out[e.player_index] = dict_out[e.player_index] or {}
    dict_out[e.player_index][key] = e.translated and e.result or false
  end,
}

-- (tests)
function M.state(player)
  local s = storage.bpgen_web and storage.bpgen_web[player.index]
  local frame = player.gui.screen[W.NAME]
  local isl = {}
  for id, f in pairs(shell and shell.islands(player, SHELL) or {}) do
    if f.valid then isl[id] = { x = f.location.x, y = f.location.y, visible = f.visible } end
  end
  local cam = M.find(player, "bpgen_cam")
  return { model = M.model(player), open = shell ~= nil and shell.is_open(player, SHELL), available = M.available(),
    wanted = M.wanted(player), lua_visible = frame ~= nil and frame.visible, has_frame = frame ~= nil,
    islands = isl, view = s and s.view, page = s and s.page, seen = s and s.seen,
    cam = cam and { visible = cam.visible, x = cam.position.x, y = cam.position.y, zoom = cam.zoom,
      surface = cam.surface_index } or nil,
    rect = shell and shell.rect and shell.rect(player, SHELL), opened_type = player.opened_gui_type,
    sent = last[player.index] ~= nil }
end
--- (tests) a pick as the page's picker sends it
function M.test_pick(player, ref, value, quality) return pick(player, ref, value, quality) end
function M.eval(player, js)  -- (tests: a script run in the page, through the web plugin)
  return native.call("web", "shell_eval", helpers.table_to_json({ owner = script.mod_name, name = SHELL, js = js }))
end
M.SHELL = SHELL

return M
