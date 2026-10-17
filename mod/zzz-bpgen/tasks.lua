-- Work spread over ticks, so the game never waits on bpgen: a big base read, a big plan built, a lab copied. Each
-- tick the tasks get UNITS of work between them (a unit is about 4 microseconds on the machine bpgen is tuned on;
-- each kind counts its own work in units: an entity read, a ghost built). Counts, not clocks: every peer of a
-- multiplayer game does the same work in the same tick.
--   tasks.kind(name, step)   step(task, budget) -> units used, done (true: the task is dropped)
--   tasks.start(name, task)  a task (plain data and game objects: it lives in storage, a save carries it on, a
--                            joining peer gets it with the map) -> the task
--   tasks.later(fn)          work only this peer does and the game never sees (text for Python): fn(budget) ->
--                            units used, done. Not saved (a job's answer isn't either).
local M = {}

M.UNITS = 500  -- per tick, between all tasks (~2 ms)
local LATER_MIN = 250  -- (this peer's own work always gets this much, after the game's)

local kinds = {}
local later = {}

function M.kind(name, step) kinds[name] = step end

local function list()
  storage.bpgen_tasks = storage.bpgen_tasks or {}
  return storage.bpgen_tasks
end

function M.start(name, task)
  storage.bpgen_task_n = (storage.bpgen_task_n or 0) + 1
  task.kind, task.id = name, storage.bpgen_task_n
  local l = list()
  l[#l + 1] = task
  return task
end

--- the first task of `name` for which match(task) holds, or nil
function M.find(name, match)
  for _, t in ipairs(storage.bpgen_tasks or {}) do
    if t.kind == name and (not match or match(t)) then return t end
  end
end

--- a task dropped (it does nothing more)
function M.cancel(task)
  local l = storage.bpgen_tasks or {}
  for i = #l, 1, -1 do
    if l[i] == task or l[i].id == task then table.remove(l, i) end
  end
end

function M.later(fn) later[#later + 1] = fn end
M.debug = false  -- (tests: each task's time in the log)
M.gc_kb = 400  -- (a busy tick's step of Lua's collector, as if this many KB were made)

--- whether anything is waiting (tests)
function M.busy() return (storage.bpgen_tasks and #storage.bpgen_tasks > 0) or #later > 0 end

--- one tick's work; `budget` (tests) overrides UNITS (math.huge: everything now)
function M.run(budget)
  budget = budget or M.UNITS
  local l = storage.bpgen_tasks
  local i = 1
  local queued = {}  -- (tasks with a queue: one at a time, in the order they were started)
  while l and i <= #l and budget > 0 do
    local t = l[i]
    local step = kinds[t.kind]
    local ok, used, done = true, 0, true
    if t.queue and queued[t.queue] then
      step, done, used = nil, false, 0
    elseif t.queue then
      queued[t.queue] = true
    end
    if step then
      local prof = M.debug and helpers.create_profiler()
      local heap = M.debug and collectgarbage("count")
      ok, used, done = pcall(step, t, budget)
      if prof then
        prof.stop()
        log({ "", "TASK ", game.tick, " ", t.kind, ":", tostring(t.phase), " ", tostring(used), " heap ",
          math.floor(heap), "->", math.floor(collectgarbage("count")), " ", prof })
      end
      if not ok then log("[bpgen] task " .. tostring(t.kind) .. " failed: " .. tostring(used)) used, done = 1, true end
    end
    budget = budget - math.max(tonumber(used) or 0, 1)
    if done then
      for k = #l, 1, -1 do if l[k] == t then table.remove(l, k) break end end
    else
      i = i + 1
    end
  end
  if budget < LATER_MIN then budget = LATER_MIN end
  while later[1] and budget > 0 do
    local prof = M.debug and helpers.create_profiler()
    local ok, used, done = pcall(later[1], budget)
    if prof then prof.stop() log({ "", "LATER ", game.tick, " ", tostring(used), " ", prof }) end
    if not ok then log("[bpgen] local work failed: " .. tostring(used)) used, done = 1, true end
    budget = budget - math.max(tonumber(used) or 0, 1)
    if done then table.remove(later, 1) end
  end
  -- (busy: Lua's collector helped along a step a tick, so the garbage a big read leaves is collected as it goes,
  -- not in one long pause later: a 400x400 base's read peaked at 120 MB of heap without, 77 with)
  if M.gc_kb and (l and l[1] or later[1]) then
    local prof = M.debug and helpers.create_profiler()
    collectgarbage("step", M.gc_kb)
    if prof then prof.stop() log({ "", "GCSTEP ", game.tick, " ", prof }) end
  end
end

--- everything now (tests, and the few callers that must have the answer in this tick)
function M.finish(task)
  local guard = 0
  while M.find(task.kind, function(t) return t == task end) and guard < 1e6 do
    M.run(math.huge)
    guard = guard + 1
  end
end

M.handlers = { [defines.events.on_tick] = function() M.run() end }

return M
