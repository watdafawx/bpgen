-- "Place near me": the planned blueprint as ghosts on the player's own map, at a free spot near them, with each
-- input pointed at the nearest belt of theirs already carrying that item. "Remove placed" takes the ghosts away.

local M = {}

local function free(s, force, area)
  -- (trees and rocks are fine: the forced build marks them for removal)
  if s.count_entities_filtered({ area = area, type = { "tree", "simple-entity", "fish", "corpse", "item-entity" }, invert = true }) > 0 then
    return false
  end
  if s.count_tiles_filtered({ area = area, collision_mask = "water_tile" }) > 0 then return false end
  if s.count_tiles_filtered({ area = area, name = "out-of-map" }) > 0 then return false end
  return true
end

--- the centre of a free w x h area near `pos` (charted, dry, nothing built), or nil
function M.find_spot(player, w, h)
  local s, force = player.surface, player.force
  local hw, hh = math.ceil(w / 2) + 3, math.ceil(h / 2) + 3
  local px, py = math.floor(player.position.x), math.floor(player.position.y)
  for r = 0, 240, 6 do
    for dx = -r, r, 6 do
      for _, dy in ipairs(r == 0 and { 0 } or { -r, r }) do
        for _, d in ipairs({ { dx, dy }, { dy, dx } }) do
          local c = { x = px + d[1] + hw + 4, y = py + d[2] }  -- (beside the player rather than on top of them)
          local area = { { c.x - hw, c.y - hh }, { c.x + hw, c.y + hh } }
          if force.is_chunk_charted(s, { math.floor(c.x / 32), math.floor(c.y / 32) }) and free(s, force, area) then
            return c
          end
        end
      end
    end
  end
end

--- what in `area` is marked for deconstruction now: {unit_number = entity}
local function marked(surface, area)
  local out = {}
  for _, e in pairs(surface.find_entities_filtered({ area = area, to_be_deconstructed = true })) do
    if e.unit_number then out[e.unit_number] = e end
  end
  return out
end

--- places the blueprint string centred at `c`: -> the ghosts, the built area's top-left, its area, and what the
--- paste marked for deconstruction (the belts under its splitters, trees: Undo unmarks them)
function M.place(player, bp, c, box)
  local inv = game.create_inventory(1)
  local stack = inv[1]
  stack.set_stack({ name = "blueprint" })
  stack.import_stack(bp)
  if stack.is_blueprint_book then
    local inner = stack.get_inventory(defines.inventory.item_main)
    stack = inner[stack.active_index or 1]
  end
  -- (an absolute blueprint, next to the base: at its box, superforced so its splitters replace the belts they tap)
  if box then c = { x = box[1] + box[3] / 2, y = box[2] + box[4] / 2 } end
  local reach = box and { { box[1] - 1, box[2] - 1 }, { box[1] + box[3] + 1, box[2] + box[4] + 1 } }
    or { { c.x - 200, c.y - 200 }, { c.x + 200, c.y + 200 } }
  local before = marked(player.surface, reach)
  local ghosts = stack.build_blueprint({ surface = player.surface, force = player.force, position = c,
    build_mode = box and defines.build_mode.superforced or defines.build_mode.forced, skip_fog_of_war = false,
    raise_built = true, player = player })
  inv.destroy()
  local now = {}
  for id, e in pairs(marked(player.surface, reach)) do
    if not before[id] then now[#now + 1] = e end
  end
  local x1, y1, x2, y2
  for _, g in pairs(ghosts) do
    local b = g.bounding_box
    x1, y1 = math.min(x1 or b.left_top.x, b.left_top.x), math.min(y1 or b.left_top.y, b.left_top.y)
    x2, y2 = math.max(x2 or b.right_bottom.x, b.right_bottom.x), math.max(y2 or b.right_bottom.y, b.right_bottom.y)
  end
  if not x1 then return ghosts, nil, nil, now end
  return ghosts, { x = math.floor(x1 + 0.5), y = math.floor(y1 + 0.5) }, { { x1, y1 }, { x2, y2 } }, now
end

--- the nearest belt of the player's carrying `item`, within 80 tiles of `pos` and outside `skip`
local function nearest_belt(player, item, pos, skip)
  local best, bd
  for _, b in pairs(player.surface.find_entities_filtered({ position = pos, radius = 80, force = player.force,
      type = { "transport-belt", "underground-belt", "splitter" } })) do
    local p = b.position
    local inside = p.x >= skip[1][1] and p.x <= skip[2][1] and p.y >= skip[1][2] and p.y <= skip[2][2]
    if not inside then
      for lane = 1, b.get_max_transport_line_index() do
        if b.get_transport_line(lane).get_item_count(item) > 0 then
          local d = (p.x - pos.x) ^ 2 + (p.y - pos.y) ^ 2
          if not bd or d < bd then best, bd = b, d end
          break
        end
      end
    end
  end
  return best
end

--- the outline, and each input's item with a line to where it can come from (shown to the player for 2 minutes)
function M.guide(player, area, origin, sources)
  local ttl = 60 * 120
  rendering.draw_rectangle({ color = { 0.2, 0.8, 1 }, width = 4, filled = false, left_top = area[1], right_bottom = area[2],
    surface = player.surface, players = { player }, time_to_live = ttl })
  local found, missing, named = 0, {}, {}
  for _, src in ipairs(sources or {}) do
    local pos = { x = origin.x + src.position.x, y = origin.y + src.position.y }
    local items = {}
    for _, it in ipairs(src.lanes or {}) do items[it] = true end
    if src.item then items[src.item] = true end
    if src.fluid then items[src.fluid] = true end
    for it in pairs(items) do
      local fluid = prototypes.fluid[it] ~= nil
      rendering.draw_text({ text = (fluid and "[fluid=" or "[item=") .. it .. "]", color = { 1, 1, 1 }, target = pos, surface = player.surface,
        players = { player }, time_to_live = ttl, scale = 2, alignment = "center", vertical_alignment = "middle",
        use_rich_text = true })
      local b = not fluid and nearest_belt(player, it, pos, area)
      if b then
        found = found + 1
        rendering.draw_line({ color = { 0.3, 1, 0.3 }, width = 3, gap_length = 1, dash_length = 2, from = pos, to = b,
          surface = player.surface, players = { player }, time_to_live = ttl })
      elseif not named[it] then
        named[it] = true
        missing[#missing + 1] = (fluid and "[fluid=" or "[item=") .. it .. "]"
      end
    end
  end
  return found, missing
end

return M
