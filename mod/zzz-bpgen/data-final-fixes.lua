-- (The data-stage half of the mod.)

-- The prototypes' icons for the web window (a shell page shows the files from the game's data folder or a mod, zipped
-- or not: /mod/<mod>/<path>, and composes layered ones): "item" / "fluid" / "recipe" / "entity" / "item-group" / ... ->
-- name -> its layers, ";" between them, each "<mod>/<path>|<icon size>[|<scale>|<shift x>|<shift y>|<r,g,b,a>]".
-- Read at runtime from prototypes.mod_data["bpgen-icons"] (made again whenever the mods change: it is data).
pcall(function()
  local KIND = { fluid = "fluid", recipe = "recipe", ["space-location"] = "space-location", planet = "space-location",
    ["virtual-signal"] = "virtual-signal", quality = "quality", ["item-group"] = "item-group" }
  local SKIP = { technology = true, ["item-subgroup"] = true, tile = true, shortcut = true,
    ["tips-and-tricks-item"] = true, ["mod-data"] = true }
  local function num(x) return string.format("%.4g", x) end
  local function tint(t)
    if type(t) ~= "table" then return nil end
    local c = { t.r or t[1] or 0, t.g or t[2] or 0, t.b or t[3] or 0, t.a or t[4] or 1 }
    if c[1] > 1 or c[2] > 1 or c[3] > 1 or c[4] > 1 then for i = 1, 4 do c[i] = c[i] / 255 end end
    return num(c[1]) .. "," .. num(c[2]) .. "," .. num(c[3]) .. "," .. num(c[4])
  end
  local function spec(p)
    local layers = p.icons or (p.icon and { { icon = p.icon, icon_size = p.icon_size } }) or nil
    if not layers then return nil end
    local out = {}
    for i, l in ipairs(layers) do
      if type(l.icon) ~= "string" then return nil end
      local mod, rest = l.icon:match("^__([^/]+)__/(.+)$")
      if not mod then return nil end
      local size = l.icon_size or p.icon_size or 64
      local s = mod .. "/" .. rest .. "|" .. tostring(size)
      local sh = l.shift and { l.shift.x or l.shift[1] or 0, l.shift.y or l.shift[2] or 0 }
      local tn = tint(l.tint)
      if l.scale or sh or tn or i > 1 then
        s = s .. "|" .. num(l.scale or 32 / size) .. "|" .. num(sh and sh[1] or 0) .. "|"
          .. num(sh and sh[2] or 0) .. "|" .. (tn or "")
      end
      out[#out + 1] = s
    end
    return table.concat(out, ";")
  end
  local out = {}
  for t, list in pairs(data.raw) do
    if not SKIP[t] then
      for name, p in pairs(list) do
        local kind = KIND[t] or (p.stack_size and "item") or ((p.collision_box or p.selection_box or p.max_health)
          and "entity") or nil
        if kind and not (out[kind] and out[kind][name]) then
          local s = spec(p)
          if s then
            out[kind] = out[kind] or {}
            out[kind][name] = s
          end
        end
      end
    end
  end
  data:extend({ { type = "mod-data", name = "bpgen-icons", data = out } })
end)
