-- Hands data.raw to bpgen (bpgen.ingame, through the fnative loader's py plugin) when bpgen's copy of the mods' data
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
