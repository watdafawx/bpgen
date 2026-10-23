-- A "plan with bpgen" button inside other mods' windows, which have no API for one:
--   Recipe Book (and its fork, same GUI): in the subheader of a recipe's page
--   Factory Planner: beside each recipe of the production table
-- Their windows are rebuilt whenever they like, so the buttons are put back every RESCAN ticks while one is open.
-- A click comes to the bpgen window (tags.bpgen = "recipe"). If their GUI changes shape, there simply is no button.

local M = {}
M.RESCAN = 30
local BUTTON = "bpgen_xmod"

local function button(parent, recipe)
  local b = parent[BUTTON]
  if b and b.tags.bpgen_recipe == recipe then return end
  if b then b.destroy() end
  parent.add({ type = "sprite-button", name = BUTTON, sprite = "item/blueprint", style = "tool_button",
    tooltip = { "", "[img=item/blueprint] Plan [recipe=" .. recipe .. "] with bpgen" },
    tags = { bpgen = "recipe", bpgen_recipe = recipe } })
end

local function recipe_of(el)
  local s = el.sprite
  local r = type(s) == "string" and s:match("^recipe/(.+)$")
  return r and prototypes.recipe[r] and r
end

local function find(el, name)
  for _, c in pairs(el.children) do
    if c.name == name then return c end
    local f = find(c, name)
    if f then return f end
  end
end

-- what makes an item or fluid: the recipe named after it, else the first one that isn't recycling
local function made_by(kind, name)
  local r = prototypes.recipe[name]
  for _, p in pairs(r and r.products or {}) do
    if p.name == name then return name end
  end
  local filter = kind == "fluid" and "has-product-fluid" or "has-product-item"
  for n, rp in pairs(prototypes.get_recipe_filtered({ { filter = filter, elem_filters = { { filter = "name", name = name } } } })) do
    local recycling = false
    for _, c in pairs(rp.categories) do if c == "recycling" then recycling = true end end
    if not rp.hidden and not recycling then return n end
  end
end

-- Recipe Book shows a recipe on its item's page when they share a name, so items and fluids get the button too
local function recipe_book(screen)
  local w = screen.rb_main_window
  if not (w and w.visible) then return end
  local title = find(w, "page_header_title")
  if not title then return end
  local kind, name = tostring(title.sprite):match("^(%a+)/(.+)$")
  local r = recipe_of(title) or ((kind == "item" or kind == "fluid") and made_by(kind, name))
  if r then button(title.parent, r) elseif title.parent[BUTTON] then title.parent[BUTTON].destroy() end
  return r and true or false
end

-- FP's main window shows recipe icons only in the production table, each alone in its table cell's flow
local function fp_walk(el)
  local any = false
  for _, c in pairs(el.children) do
    local r = c.type == "sprite-button" and c.name ~= BUTTON and recipe_of(c)
    if r then
      button(c.parent, r)
      any = true
    elseif c.name ~= BUTTON then
      any = fp_walk(c) or any
    end
  end
  return any
end

local function factory_planner(screen)
  local w = screen.fp_frame_main_dialog
  return w and w.visible and fp_walk(w) or false
end

--- (set by control.lua) a bpgen button shows: the window may be opened from it soon (the web window's shell then
--- loads ahead, hidden)
M.on_button = nil

function M.scan(player)
  local screen = player.gui.screen
  local shown = false
  if script.active_mods["RecipeBook"] or script.active_mods["RecipeBookFork"] then
    local ok, r = pcall(recipe_book, screen)
    shown = ok and r == true
  end
  if script.active_mods["factoryplanner"] then
    local ok, r = pcall(factory_planner, screen)
    shown = shown or (ok and r == true)
  end
  if shown and M.on_button then M.on_button(player) end
end

-- the recipe under the cursor for Ctrl+Shift+B: one shown with its tooltip (Recipe Book, Factoriopedia, crafting
-- menu...), a button showing a recipe's icon (Factory Planner's production table), or what makes an item or fluid
-- under it (a slot of the inventory, a chest, the quickbar...)
function M.hovered_recipe(e)
  local sp = e.selected_prototype
  if sp and sp.base_type == "recipe" and prototypes.recipe[sp.name] then return sp.name end
  if sp and (sp.base_type == "item" or sp.base_type == "fluid") then
    local r = made_by(sp.base_type, sp.name)
    if r then return r end
  end
  local el = e.element
  if el and el.valid then
    local ok, r = pcall(recipe_of, el)
    if ok then return r end
  end
end

return M
