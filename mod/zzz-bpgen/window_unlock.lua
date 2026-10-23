-- What the player's force has unlocked, and what that means for the window: the machines, belts and inserters bpgen may
-- pick, and the "Only unlocked" filters of its pickers (kept small: the game checks every recipe against them).

local W = require("window_core")
local web, safe, ctx, NAME, find, only_unlocked = W.web, W.safe, W.ctx, W.NAME, W.find, W.only_unlocked

-- player index -> what the force can build: read once until the force's research changes (W.forget_unlocked) or the
-- player changes force; 19,000 recipes read on every open took 15 ms in a big pack (in storage: the same on every peer)
local unlocks = safe.stored("unlocks")

-- {"item/iron-plate" = true, ...} of a recipe's ingredients or products
local function stuff(list)
  local out = {}
  for _, x in pairs(list) do
    if x.type == "item" or x.type == "fluid" then out[x.type .. "/" .. x.name] = true end
  end
  return out
end

local function has(kind, key, invert)  -- a has-ingredient-/has-product- filter on "item/name"
  local t, name = key:match("^(%a+)/(.+)$")
  return { filter = "has-" .. kind .. "-" .. t, elem_filters = { { filter = "name", name = name } }, invert = invert,
           mode = "and" }
end

-- one "and" group per unlocked recipe: what tells it apart from locked recipes (filters on recipes have no
-- "name"). Its main product, category, subgroup and crafting time, then for each locked recipe still let through
-- (a has-product filter matches any product: advanced oil processing makes petroleum gas too) one more clause:
-- something the locked one has and this one doesn't, or the other way round.
-- what a recipe's prototype says, read once a session (prototypes don't change while the game runs): reading them
-- through the API for thousands of recipes on every pick took seconds in a big pack
local facts_of = {}  -- recipe name -> {product, category, subgroup, energy, sig, hidden, ins, outs}
local function facts(name)
  local f = facts_of[name]
  if not f then
    local p = prototypes.recipe[name]
    local first = p.products[1]
    -- (2.1: a list of categories; `category` is the first, the one the filters below use)
    f = { product = first and (first.type .. "/" .. first.name), cats = p.categories, category = p.categories[1],
          subgroup = p.subgroup.name, energy = p.energy, hidden = p.hidden }
    f.recycling = false
    for _, c in pairs(f.cats) do if c == "recycling" then f.recycling = true end end
    f.sig = table.concat({ f.category, f.subgroup, f.energy }, "|")
    facts_of[name] = f
  end
  return f
end
local function sets(name)  -- its ingredients and products as {"item/x" = true}
  local f = facts(name)
  if not f.ins then
    local p = prototypes.recipe[name]
    f.ins, f.outs = stuff(p.ingredients), stuff(p.products)
  end
  return f.ins, f.outs
end

-- One pass over the force's recipes (15 ms in a big pack; reading every recipe's products too took as long again):
-- which it has enabled, and the items the enabled ones make (control.lua's craftable: not hidden, not recycling;
-- their products as the prototypes have them). Once a tick at most: opening the window asked for it three times.
local pass_at = {}  -- force index -> {tick, enabled, items}
local function force_pass(force)
  local m = pass_at[force.index]
  if m and m.tick == game.tick then return m end
  local enabled, items = {}, {}
  for name, r in pairs(force.recipes) do
    local on = r.enabled
    enabled[name] = on
    if on and not r.hidden and not facts(name).recycling then
      local _, outs = sets(name)
      for k in pairs(outs) do
        local t, item = k:match("^(%a+)/(.+)$")
        if t == "item" then items[item] = true end
      end
    end
  end
  m = { tick = game.tick, enabled = enabled, items = items }
  pass_at[force.index] = m
  return m
end

--- the items some enabled recipe of the force makes (control.lua's craftable), with which recipes it has enabled
local function craftable(force) return force_pass(force).items end

local function speed(proto, quality)
  local ok, s = pcall(proto.get_crafting_speed, quality)
  return ok and s or proto.crafting_speed or 1
end

local function unlocked(player)
  local u = unlocks[player.index]
  if not u or u.force ~= player.force.index then
    local made = craftable(player.force)
    u = { machines = ctx.unlocked(made, { "assembling-machine", "furnace" }), belts = ctx.unlocked(made, { "transport-belt" }),
          inserters = ctx.unlocked(made, { "inserter" }), poles = ctx.unlocked(made, { "electric-pole" }),
          force = player.force.index }
    unlocks[player.index] = u
  end
  return u
end

-- the fastest unlocked machine that can make the recipe
-- a free copy that runs without power (bioluminescent / harene-infused machines and the like): never an automatic pick
local function powerless(p)
  local ok, v = pcall(function() return p.void_energy_source_prototype end)
  return ok and v ~= nil
end

-- a recipe's crafting categories (a machine with any one makes it)
local function recipe_cats(recipe)
  local rp = recipe and prototypes.recipe[recipe]
  if not rp then return {} end
  local out = {}
  for _, c in pairs(rp.categories) do out[#out + 1] = c end
  return out
end
W.recipe_cats = recipe_cats

local function crafts(p, recipe)
  if not p.crafting_categories then return false end
  for _, c in ipairs(recipe_cats(recipe)) do
    if p.crafting_categories[c] then return true end
  end
  return false
end

local function best_machine(player, recipe)
  local best, best_speed
  for _, name in ipairs(unlocked(player).machines) do
    local p = prototypes.entity[name]
    if crafts(p, recipe) and not powerless(p) then
      local s = speed(p)
      if not best or s > best_speed then best, best_speed = name, s end
    end
  end
  return best
end
W.best_machine = best_machine

-- the unlocked machines making every one of these recipes, fastest first (the mall tries them in turn)
local function machines_for_all(player, recipes)
  local list = {}
  for _, name in ipairs(unlocked(player).machines) do
    local p = prototypes.entity[name]
    local ok = p.crafting_categories ~= nil and not powerless(p)
    for _, r in ipairs(recipes) do
      if not (ok and crafts(p, r)) then ok = false end
    end
    if ok then list[#list + 1] = name end
  end
  table.sort(list, function(a, b) return speed(prototypes.entity[a]) > speed(prototypes.entity[b]) end)
  return list
end

local function best_of_type(player, t, category)
  local best, best_speed
  for _, name in ipairs(ctx.unlocked(craftable(player.force), { t })) do
    local p = prototypes.entity[name]
    if not powerless(p) and (not category or (p.crafting_categories and p.crafting_categories[category])) then
      local sp = p.crafting_categories and speed(p) or (p.get_researching_speed and p.get_researching_speed() or 1)
      if not best or sp > best_speed then best, best_speed = name, sp end
    end
  end
  return best
end

-- (the facts read ahead, a few hundred recipes a tick, when the window may open soon with "Only unlocked" on: the
-- first build of its filters read 19,000 recipes at once, 100 ms of its 140)
local warming  -- {names, i}
local WARM_PER_TICK = 400
function W.warm(player)
  if warming ~= nil or not only_unlocked[player.index] then return end
  local names = {}
  for name in pairs(prototypes.recipe) do names[#names + 1] = name end
  warming = { names = names, i = 1 }
end
local function warm_tick()
  local w = warming
  if not w or not w.names then return end
  local stop = math.min(#w.names, w.i + WARM_PER_TICK - 1)
  for i = w.i, stop do
    local f = facts(w.names[i])
    if not f.hidden and f.product then sets(w.names[i]) end
  end
  w.i = stop + 1
  if w.i > #w.names then warming = { done = true } end
end

-- The filters are kept small: the game checks every recipe against all of them each time a picker opens, and one
-- group per recipe (21,000 clauses in a big pack) made opening the picker stutter. Most recipes go into one group
-- per crafting category and product type: "has a product among these" and "in this category". A product that a
-- locked recipe of that category also makes can't go there; its recipes get a group each, told apart from the
-- locked ones by category, subgroup, time and, where needed, an ingredient or product (fine groups).
local function recipe_filters(force)
  local enabled = force_pass(force).enabled  -- recipe name -> whether the force has it (a lookup by name is slow)
  local unlocked, listed = {}, {}  -- listed: category -> {"item/x" = true} of the unlocked recipes' main products
  for name, on in pairs(enabled) do
    local f = on and facts(name)
    if f and f.product and not f.hidden then
      unlocked[#unlocked + 1] = name
      listed[f.category] = listed[f.category] or {}
      listed[f.category][f.product] = true
    end
  end
  -- every product a locked recipe of the same category makes: off the coarse lists
  local taken = {}  -- category -> {"item/x" = true}
  for name, on in pairs(enabled) do
    local f = not on and facts(name)
    for _, c in ipairs(f and not f.hidden and f.cats or {}) do -- (the "category" filter matches any of a recipe's)
      local mine = listed[c]
      if mine then
        local _, outs = sets(name)
        for k in pairs(outs) do
          if mine[k] then
            taken[c] = taken[c] or {}
            taken[c][k] = true
          end
        end
      end
    end
  end

  local coarse, fine, by_sig = {}, {}, {}  -- coarse: category -> type -> {names}
  for _, name in ipairs(unlocked) do
    local f = facts(name)
    local t, item = f.product:match("^(%a+)/(.+)$")
    if (t == "item" or t == "fluid") and not (taken[f.category] and taken[f.category][f.product]) then
      coarse[f.category] = coarse[f.category] or {}
      local list = coarse[f.category][t] or {}
      coarse[f.category][t] = list
      if not list[item] then
        list[item] = true
        list[#list + 1] = item
      end
    else
      local g = { name = name, product = f.product, has("product", f.product),
                  { filter = "category", category = f.category, mode = "and" },
                  { filter = "subgroup", subgroup = f.subgroup, mode = "and" },
                  { filter = "energy", comparison = "=", value = f.energy, mode = "and" } }
      g[1].mode = nil
      fine[#fine + 1] = g
      by_sig[f.sig] = by_sig[f.sig] or {}
      table.insert(by_sig[f.sig], g)
    end
  end
  -- the locked recipes the fine groups still let through (a has-product filter matches any product: advanced oil
  -- processing makes petroleum gas too), each kept out by one more clause in the groups it slips into
  for name, on in pairs(enabled) do
    local f = not on and facts(name)
    local list
    for _, c in ipairs(f and not f.hidden and f.cats or {}) do
      local l = by_sig[table.concat({ c, f.subgroup, f.energy }, "|")]
      if l then
        list = list or {}
        for _, g in ipairs(l) do list[#list + 1] = g end
      end
    end
    if list then
      local li, lp = sets(name)
      local through = false
      for _, g in ipairs(list) do if lp[g.product] then through = true break end end
      if through then
        for _, g in ipairs(list) do
          local ri, rp = sets(g.name)
          local clause
          for k in pairs(li) do if not clause and not ri[k] then clause = has("ingredient", k, true) end end
          for k in pairs(lp) do if not clause and not rp[k] then clause = has("product", k, true) end end
          for k in pairs(ri) do if not clause and not li[k] then clause = has("ingredient", k) end end
          for k in pairs(rp) do if not clause and not lp[k] then clause = has("product", k) end end
          if clause then g[#g + 1] = clause end
        end
      end
    end
  end

  local out = {}
  for category, types in pairs(coarse) do
    for t, list in pairs(types) do
      local names = {}  -- (the list without its lookup keys)
      for i = 1, #list do names[i] = list[i] end
      out[#out + 1] = { filter = "has-product-" .. t, elem_filters = { { filter = "name", name = names } } }
      out[#out + 1] = { filter = "category", category = category, mode = "and" }
    end
  end
  for _, g in ipairs(fine) do for _, x in ipairs(g) do out[#out + 1] = x end end
  return out
end

-- the filters that keep a picker to what the force has unlocked, by its elem_type. Built once per force until its
-- research changes (W.forget_unlocked, from control.lua's research events): a big pack took a second or more to
-- build them, and every pick in the window built them again, so picking a recipe froze the game
local unlocked_cache = {}  -- force index -> filters

--- the force's unlocked filters are out of date (nil: every force's)
function W.forget_unlocked(force)
  if force then unlocked_cache[force.index], pass_at[force.index] = nil, nil else unlocked_cache, pass_at = {}, {} end
  for i, u in pairs(unlocks) do
    if not force or u.force == force.index then unlocks[i] = nil end
  end
end

local function unlocked_filters(force)
  if unlocked_cache[force.index] then return unlocked_cache[force.index] end
  local items, entities = {}, {}
  local recipe = recipe_filters(force)
  for name in pairs(craftable(force)) do
    items[#items + 1] = name
    local proto = prototypes.item[name]
    local place = proto and proto.place_result
    if place then entities[#entities + 1] = place.name end
  end
  local item = { { filter = "name", name = items } }
  local entity = { { filter = "name", name = entities } }
  unlocked_cache[force.index] = { recipe = recipe, ["recipe-with-quality"] = recipe, item = item,
    ["item-with-quality"] = item, entity = entity, ["entity-with-quality"] = entity }
  return unlocked_cache[force.index]
end

local function restrict_el(el, on, names)
  if el.type == "choose-elem-button" then
    local tags = el.tags
    local own = tags.bpgen_own_filters
    if on and names[el.elem_type] then
      if own == nil then
        own = el.elem_filters or false
        tags.bpgen_own_filters = own
        el.tags = tags
      end
      -- ("and" binds tighter than "or": the own filter goes into every group, each own group crossed with each)
      local function groups(list)
        local out = {}
        for i, x in ipairs(list) do
          if i == 1 or x.mode ~= "and" then out[#out + 1] = {} end
          table.insert(out[#out], x)
        end
        return out
      end
      local mine = own and groups(own) or { {} }
      local f = {}
      for _, g in ipairs(groups(names[el.elem_type])) do
        for _, o in ipairs(mine) do
          for _, x in ipairs(g) do f[#f + 1] = x end
          for _, x in ipairs(o) do
            local c = {}
            for k, v in pairs(x) do c[k] = v end
            c.mode = "and"
            f[#f + 1] = c
          end
        end
      end
      el.elem_filters = f
    elseif own ~= nil then
      el.elem_filters = own or nil
      tags.bpgen_own_filters = nil
      el.tags = tags
    end
  end
  for _, c in pairs(el.children) do restrict_el(c, on, names) end
end

function W.set_only_unlocked(player, on)
  only_unlocked[player.index] = on or nil
  local frame = player.gui.screen[NAME]
  local box = frame and find(frame, "bpgen_only_unlocked")
  if box then box.state = on == true end
  local mark = frame and find(frame, "bpgen_unlocked_badge")
  if mark then mark.visible = on == true end
  W.restrict(player)
end

--- the window's pickers as the player's "Only unlocked" says (call after pickers are added or their filters set)
W.recipe_filters = recipe_filters  -- (tests)

function W.restrict(player)
  local frame = player and player.valid and player.gui.screen[NAME]
  if not frame then return end
  local on = only_unlocked[player.index] == true
  restrict_el(frame, on, on and unlocked_filters(player.force) or {})
  web.filters_changed(player)  -- (the web window's pickers: their mirrors get them)
end

local function set_machine_filter(frame, recipe)
  local btn = find(frame, "bpgen_machine")
  local cats = recipe_cats(recipe)
  if btn and #cats > 0 then
    local filters = {}
    for _, c in ipairs(cats) do filters[#filters + 1] = { filter = "crafting-category", crafting_category = c } end
    btn.elem_filters = filters
    local tags = btn.tags
    tags.bpgen_own_filters = nil -- (a new own filter: restricted again below)
    btn.tags = tags
    W.restrict(game.get_player(frame.player_index))
  end
end

-- (what the other parts call)
W.best_of_type = best_of_type
W.crafts = crafts
W.machines_for_all = machines_for_all
W.set_machine_filter = set_machine_filter
W.unlocked = unlocked
W.warm_tick = warm_tick
