-- "Place near me": the planned blueprint as ghosts on the player's own map, at a free spot near them, with each
-- input pointed at the nearest belt of theirs already carrying that item. "Remove placed" takes the ghosts away.

local safe = require("safe")
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
function M.place(player, bp, c, box, section)
  local inv = game.create_inventory(1)
  local stack = inv[1]
  stack.set_stack({ name = "blueprint" })
  stack.import_stack(bp)
  if stack.is_blueprint_book then  -- (the print labelled `section`, else the book's active one)
    local inner = stack.get_inventory(defines.inventory.item_main)
    local pick = inner[stack.active_index or 1]
    for i = 1, section and #inner or 0 do
      if inner[i].valid_for_read and inner[i].label == section then pick = inner[i] break end
    end
    stack = pick
  end
  -- (an absolute blueprint, next to the base: at its box, superforced so its splitters replace the belts they tap)
  if box then c = { x = box[1] + box[3] / 2, y = box[2] + box[4] / 2 } end
  local reach = box and { { box[1] - 1, box[2] - 1 }, { box[1] + box[3] + 1, box[2] + box[4] + 1 } }
    or { { c.x - 200, c.y - 200 }, { c.x + 200, c.y + 200 } }
  local before = marked(player.surface, reach)
  local function build()
    return stack.build_blueprint({ surface = player.surface, force = player.force, position = c,
      build_mode = box and defines.build_mode.superforced or defines.build_mode.forced, skip_fog_of_war = false,
      raise_built = true, player = player })
  end
  local ghosts = build()
  -- (snapping on a grid of box[5] tiles: which cell the game picks depends on the print's size; pasted whole cells
  -- off its box (give or take what couldn't be built there), it's taken back and pasted again that many cells over)
  for _ = 1, box and box[5] and 3 or 0 do
    local g_ = box[5]
    local ex, ey
    for _, g in pairs(ghosts) do
      if g.valid and g.type ~= "tile-ghost" then
        local b = g.bounding_box
        ex, ey = math.min(ex or b.left_top.x, b.left_top.x), math.min(ey or b.left_top.y, b.left_top.y)
      end
    end
    if not ex then break end
    local dx = math.floor((box[1] - ex) / g_ + 0.5) * g_
    local dy = math.floor((box[2] - ey) / g_ + 0.5) * g_
    if dx == 0 and dy == 0 then break end
    for _, g in pairs(ghosts) do  -- (what that paste marked for removal, under its ghosts: unmarked)
      if g.valid then
        for _, e in pairs(player.surface.find_entities_filtered({ area = g.bounding_box, to_be_deconstructed = true })) do
          if e.type ~= "entity-ghost" then e.cancel_deconstruction(player.force) end
        end
        g.destroy()
      end
    end
    c = { x = c.x + dx, y = c.y + dy }
    ghosts = build()
  end
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

local outlines = safe.stored("outlines")  -- player index -> the outline drawn round their last placement

--- what was just placed, outlined and named above its top-left corner (2 minutes; a new placement or Undo takes it
--- away)
function M.outline(player, area, label)
  M.clear_outline(player)
  local ttl = 60 * 120
  local box = rendering.draw_rectangle({ color = { 0.2, 0.8, 1 }, width = 4, filled = false, left_top = area[1],
    right_bottom = area[2], surface = player.surface, players = { player }, time_to_live = ttl })
  local text = rendering.draw_text({ text = { "", "[img=item/blueprint] ", label or "placed by bpgen" },
    surface = player.surface, target = { area[1][1] or area[1].x, (area[1][2] or area[1].y) - 1.2 },
    color = { 0.6, 0.9, 1 }, scale = 1.5, players = { player }, time_to_live = ttl, use_rich_text = true })
  outlines[player.index] = { box, text }
end

function M.clear_outline(player)
  for _, o in pairs(outlines[player.index] or {}) do if o.valid then o.destroy() end end
  outlines[player.index] = nil
end

return M
