-- The planning request from the window's settings: one builder per mode (Python's bpgen.ingame reads it).

local W = require("window_core")
local ctx, BUS_DIRS, T, bus_shift, find = W.ctx, W.BUS_DIRS, W.T, W.bus_shift, W.find
local patch_areas = W.patch_areas

local function mall_req(player, frame, req, u)
  local products = {}
  for _, b in pairs(find(frame, "bpgen_products").children) do
    if b.elem_value then products[#products + 1] = b.elem_value end
  end
  if #products == 0 then return nil, T("err-pick-products") end
  local picked = W.value(frame, "bpgen_mall_machine")
  local candidates = picked and { picked } or W.machines_for_all(player, products)
  if #candidates == 0 then return nil, T("err-no-mall-machine") end
  req.params = { mode = "mall", products = products, machine = candidates[1], machines_try = candidates,
    chest = W.value(frame, "bpgen_chest") or (prototypes.entity["steel-chest"] and "steel-chest") or "wooden-chest",
    feed = find(frame, "bpgen_mall_feed").selected_index == 2 and "robots" or "belt",
    layout = find(frame, "bpgen_mall_feed").selected_index >= 6 and "sushi"
      or find(frame, "bpgen_mall_feed").selected_index >= 3 and "grid" or nil,
    bus_feed = (find(frame, "bpgen_mall_feed").selected_index == 4 or find(frame, "bpgen_mall_feed").selected_index == 7)
      or nil,
    add_to = find(frame, "bpgen_mall_feed").selected_index == 5 and player.cursor_stack
      and player.cursor_stack.valid_for_read and player.cursor_stack.export_stack() or nil,
    wants_base = find(frame, "bpgen_mall_feed").selected_index == 5 or nil,
    origin = { x = player.position.x, y = player.position.y },
    buffer_crafts = tonumber(find(frame, "bpgen_mall_buffer").text) or 5,
    chest_limit = tonumber(find(frame, "bpgen_chest_limit").text) or 4,
    stock = tonumber(find(frame, "bpgen_mall_stock").text),
    belt = req.belt }
  if req.params.wants_base and not req.params.add_to then
    return nil, T("err-hold-base-mall")
  end
  return req
end

local function extend_req(player, frame, req, u)
  local item = W.value(frame, "bpgen_ext_item")
  if not item then return nil, T("err-pick-item") end
  req.params = { mode = "extend", item = item, rate_per_min = tonumber(find(frame, "bpgen_ext_rate").text) or 30,
    assembler = W.best_of_type(player, "assembling-machine", "crafting"), furnace = W.best_of_type(player, "furnace", "smelting"),
    belt = req.belt or u.belts[#u.belts], bus = find(frame, "bpgen_ext_bus").state and "auto" or "off" }
  return req
end

local function ship_req(player, frame, req, u)
  local dd = find(frame, "bpgen_ship_orbit")
  req.params = { mode = "ship", thrusters = tonumber(find(frame, "bpgen_ship_thrusters").text) or 2,
    turrets = tonumber(find(frame, "bpgen_ship_turrets").text) or 6,
    collectors = tonumber(find(frame, "bpgen_ship_collectors").text) or 4,
    throttle = (function()
      local t = find(frame, "bpgen_ship_throttle")
      return (t.tags.values or {})[t.selected_index] or -1
    end)(),
    location = (dd.tags.orbits or {})[dd.selected_index] or "nauvis", belt = req.belt or u.belts[1] }
  return req
end

local function bus_req(player, frame, req, u)
  local list = {}
  for i, p in ipairs(patch_areas[player.index] or {}) do
    local a = p.area
    list[i] = { a.left_top.x, a.left_top.y, a.right_bottom.x, a.right_bottom.y }
  end
  if #list == 0 then return nil, T("err-pick-patches") end
  local dir = BUS_DIRS[find(frame, "bpgen_bus_dir").selected_index] or "north"
  local shift = bus_shift[player.index] or 0  -- (across the bus's flow)
  local across = (dir == "north" or dir == "south") and { shift, 0 } or { 0, shift }
  req.params = { mode = "busdesign", patches = list,
    origin = { x = player.position.x + across[1], y = player.position.y + across[2] },
    direction = BUS_DIRS[find(frame, "bpgen_bus_dir").selected_index] or "north",
    group = tonumber(find(frame, "bpgen_bus_group").text) or 4, gap = tonumber(find(frame, "bpgen_bus_gap").text) or 4,
    length = tonumber(find(frame, "bpgen_bus_length").text) or 40, balance = find(frame, "bpgen_bus_balance").state,
    wood = find(frame, "bpgen_bus_wood").state, add_to_bus = find(frame, "bpgen_bus_add").state or nil,
    drill = W.value(frame, "bpgen_bus_drill"), furnace = W.value(frame, "bpgen_bus_furnace"),
    belt = W.value(frame, "bpgen_bus_belt") or u.belts[#u.belts] }
  return req
end

local function base_req(player, frame, req, u)
  local addons = {}
  if find(frame, "bpgen_base_military").state then addons[#addons + 1] = "military" end
  if find(frame, "bpgen_base_chemical").state then addons[#addons + 1] = "chemical" end
  req.params = { mode = "base", spm = tonumber(find(frame, "bpgen_spm").text) or 30, addons = addons,
    -- (empty pickers: bpgen's starter defaults, not the fastest thing unlocked: a late-game lab takes dozens of
    -- science packs, a "starter base" for it isn't one)
    assembler = W.value(frame, "bpgen_assembler"), furnace = W.value(frame, "bpgen_furnace"),
    lab = W.value(frame, "bpgen_lab"), mall = find(frame, "bpgen_base_mall").state,
    layout = find(frame, "bpgen_base_bus").state and "bus" or "compact",
    belt = req.belt or u.belts[#u.belts], productivity = 0 }
  local plates, add = find(frame, "bpgen_base_plates").state, find(frame, "bpgen_base_add").state
  if plates or add then req.params.layout = "bus" end  -- (the C and the tiers need the main bus)
  req.params.plates = plates or nil
  req.params.fit_bus = plates and not add and find(frame, "bpgen_base_fit").state or nil
  if add then
    local cs = player.cursor_stack
    if not (cs and cs.valid_for_read and (cs.is_blueprint and cs.is_blueprint_setup() or cs.is_blueprint_book)) then
      return nil, T("err-hold-base-add")
    end
    req.params.add_to = cs.export_stack()
  end
  if find(frame, "bpgen_base_import").state and not add then
    local cs = player.cursor_stack
    if not (cs and cs.valid_for_read and cs.is_blueprint and cs.is_blueprint_setup()) then
      return nil, T("err-hold-base-import")
    end
    req.params.import = cs.export_stack()
  end
  return req
end

--- a production line (the default): the recipe, its machine and modules, the pickers' extras, More lines
local function line_req(player, frame, req, u)
  local force = player.force
  local recipe = W.value(frame, "bpgen_recipe")
  if not recipe then return nil, T("err-pick-recipe") end
  local machine = W.value(frame, "bpgen_machine")
  if not machine then
    local m = W.best_machine(player, recipe)
    if not m then return nil, T("err-no-machine") end
    machine = { name = m, quality = "normal" }
    find(frame, "bpgen_machine").elem_value = machine
  end
  local modules = {}
  local mod = W.value(frame, "bpgen_module")
  local slots = prototypes.entity[machine.name].module_inventory_size or 0
  if mod and slots > 0 then modules[1] = { name = mod.name, quality = mod.quality or "normal", count = slots } end
  req.recipe, req.recipe_quality = recipe, "normal"
  req.recipe_productivity = force.recipes[recipe] and force.recipes[recipe].productivity_bonus or 0
  req.machine, req.machine_quality, req.modules = machine.name, machine.quality or "normal", modules
  req.rate_per_min = tonumber(find(frame, "bpgen_rate").text)
  req.per_row = tonumber(find(frame, "bpgen_per_row").text)
  local params = { near = W.value(frame, "bpgen_near"), far = W.value(frame, "bpgen_far"), out = W.value(frame, "bpgen_out"),
                   feed = find(frame, "bpgen_feed").selected_index == 2 and "robots" or nil }
  if find(frame, "bpgen_feed").selected_index == 3 then
    params.bus_feed, params.origin = true, { x = player.position.x, y = player.position.y }
  end
  local beacon, bmod = W.value(frame, "bpgen_beacon"), W.value(frame, "bpgen_beacon_module")
  if beacon and bmod then
    local bslots = prototypes.entity[beacon.name].module_inventory_size or 0
    params.beacon, params.beacon_quality = beacon.name, beacon.quality or "normal"
    params.beacon_modules = string.format("%s@%s:%d", bmod.name, bmod.quality or "normal", bslots)
  end
  local make = {}
  for _, r in pairs(find(frame, "bpgen_make").children) do
    local on, pick = r.children[1], r.children[2]
    if on and on.type == "checkbox" and on.state and pick and pick.type == "choose-elem-button" and pick.elem_value then
      local m = W.best_machine(player, pick.elem_value)
      if m then make[on.tags.item] = { recipe = pick.elem_value, machine = m } end
    end
  end
  if next(make) then params.make = make end
  local also = {}
  local function ticked(box)
    for _, r in pairs(box.children) do
      if r.name == "bpgen_also_near" then ticked(r)
      elseif r.type == "flow" and #r.children > 0 then
        local on, rate = r.children[1], r.children[#r.children]
        if on.type == "checkbox" and on.state and on.tags.bpgen_recipe then
          also[on.tags.item] = { recipe = on.tags.bpgen_recipe, rate_per_min = rate.type == "textfield" and tonumber(rate.text) or nil }
        end
      end
    end
  end
  ticked(find(frame, "bpgen_also"))
  if next(also) then params.also = also end
  req.params = params
  -- More lines: each its own line, as if picked alone (its best machine, the same rate, belt, feed and modules)
  local lines = {}
  for _, r in ipairs(W.more_lines(frame)) do
    local m = W.best_machine(player, r)
    if not m then return nil, T("err-no-machine-for", "[recipe=" .. r .. "]") end
    local mods = {}
    local n = prototypes.entity[m].module_inventory_size or 0
    if mod and n > 0 then mods[1] = { name = mod.name, quality = mod.quality or "normal", count = n } end
    lines[#lines + 1] = { recipe = r, machine = m, machine_quality = "normal", modules = mods,
      recipe_productivity = force.recipes[r] and force.recipes[r].productivity_bonus or 0 }
  end
  if #lines > 0 then
    if params.bus_feed then return nil, T("err-lines-bus") end
    req.lines = lines
  end
  return req
end

local BY_MODE = { mall = mall_req, extend = extend_req, ship = ship_req, busdesign = bus_req, base = base_req }

--- the request for the mode shown -> request, or nil and why not
local function request(player, frame)
  local u = W.unlocked(player)
  local req = { tick = game.tick, surface = player.surface.name, seed = player.surface.map_gen_settings.seed,
                bonuses = ctx.bonuses(player.force),
                inserters = u.inserters, belts = u.belts, poles = u.poles, belt = W.value(frame, "bpgen_belt") }
  return (BY_MODE[W.mode_of(frame)] or line_req)(player, frame, req, u)
end

--- for other mods (AI Crew's goals): Extend's request for a line of `item` at `rate` a minute next to the base
function W.extend_request(player, item, rate)
  local u = W.unlocked(player)
  local belt = u.belts[#u.belts]
  return { tick = game.tick, surface = player.surface.name, bonuses = ctx.bonuses(player.force), inserters = u.inserters,
    belts = u.belts, poles = u.poles, belt = belt,
    params = { mode = "extend", item = item, rate_per_min = rate, belt = belt, bus = "auto",
      assembler = W.best_of_type(player, "assembling-machine", "crafting"), furnace = W.best_of_type(player, "furnace", "smelting") } }
end

-- (what the other parts call)
W.request = request
