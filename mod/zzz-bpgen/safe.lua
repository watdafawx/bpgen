-- Never crash the game: every event handler of the mod runs through guard() (an error becomes a log line and one
-- chat message, the save goes on), and the fse loader is only used when it's there and has the plugin.

local M = {}
local warned = {}

--- the loader is there and has `plugin` (a missing, old or broken loader reads as "not there")
function M.has(plugin)
  if type(native) ~= "table" or type(native.plugins) ~= "function" then return false end
  local ok, list = pcall(native.plugins)
  return ok and type(list) == "table" and list[plugin] ~= nil
end

--- a line in the game's log, and in fse.log when the loader is there
function M.log(msg)
  log(msg)
  if type(native) == "table" and type(native.log) == "function" then pcall(native.log, msg) end
end

--- a table kept in storage.bpgen_state[name] (read like a plain table): per-player state the game must agree on
--- in multiplayer, where a joining peer gets it with the map. Values: plain data and game objects, no functions.
function M.stored(name)
  return setmetatable({}, {
    __index = function(_, k)
      local t = storage.bpgen_state and storage.bpgen_state[name]
      return t and t[k]
    end,
    __newindex = function(_, k, v)
      storage.bpgen_state = storage.bpgen_state or {}
      storage.bpgen_state[name] = storage.bpgen_state[name] or {}
      storage.bpgen_state[name][k] = v
    end,
    __pairs = function()
      return next, storage.bpgen_state and storage.bpgen_state[name] or {}, nil
    end,
  })
end

function M.guard(name, fn)
  if not fn then return nil end
  return function(e)
    local trace
    local ok, err = xpcall(fn, function(m) trace = debug.traceback(tostring(m), 2) return m end, e)
    if not ok then
      M.log("[" .. name .. "] error (handled): " .. tostring(trace or err))
      if not warned[name] and game then
        warned[name] = true
        game.print("[" .. name .. "] something went wrong and was skipped (details in the log): "
          .. tostring(err):sub(1, 160))
      end
    end
  end
end

--- handler tables (event -> function) added onto the handlers already registered, each guarded: for a library
--- mod's handlers when the mod registers its own events directly
function M.chain(name, tables)
  for _, t in ipairs(tables) do
    for ev, fn in pairs(t) do
      local prev = script.get_event_handler(ev)
      local g = M.guard(name, fn)
      script.on_event(ev, prev and function(e) prev(e) g(e) end or g)
    end
  end
end

return M
