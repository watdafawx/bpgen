-- (The data-stage half of the mod: why it is named zzz-bpgen.)

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

-- Hands data.raw to bpgen (bpgen.ingame, through the fse loader's py plugin) when bpgen's copy of the mods' data
-- is stale: one quick question otherwise. Loads last (its name), so it sees every mod's final fixes. Does nothing
-- without the loader, and nothing it meets (an old loader, a missing plugin, a Python error) can stop the game
-- loading: all of it runs under pcall and only ever logs.
if type(native) ~= "table" or type(native.to_json) ~= "function" or type(native.call) ~= "function" then return end

local ok, err = pcall(function()
  local active, jerr = native.to_json(mods)
  local want, werr = native.call("py", "bpgen.ingame:data_wanted", active or "{}")
  if not want then
    log("[bpgen] couldn't ask bpgen: " .. tostring(werr or jerr))
    return
  end
  if want == "" then
    log("[bpgen] its data is current")
    return
  end
  local types, skips = want:match("^([^|]*)|(.*)$")
  local sub, skip = {}, {}
  for t in (types or ""):gmatch("[^,]+") do sub[t] = data.raw[t] end
  for k in (skips or ""):gmatch("[^,]+") do skip[k] = true end
  local json, terr = native.to_json(sub, skip)
  if not json then
    log("[bpgen] data not sent: " .. tostring(terr))
    return
  end
  local sent, why = native.call("py", "bpgen.ingame:take_data", json)
  log("[bpgen] mods' data sent to bpgen: " .. tostring(sent or why))
end)
if not ok then log("[bpgen] data hand-over skipped: " .. tostring(err)) end
