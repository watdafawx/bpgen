-- (stutter_test.py) the game thread's wait while bpgen plans on a worker thread: heavy Rust routes run in a worker
-- (as a big plan's do: bpgen/harness/stutter_probe.py), and every 30 ticks the game reads the plan's progress as the
-- mod's window does. The time of that read is the hitch the player feels. Then a real plan: its progress must arrive
-- as a note. Writes script-output/stutter.txt.
local job, waits, done = nil, {}, false
local plan, report

local function now() return tonumber(native.call("std", "now")) end

script.on_event(defines.events.on_tick, function(e)
  if not native then return end
  if plan then
    local status, res = native.poll(plan)
    if status == "pending" then return end
    plan = nil
    local p = helpers.json_to_table(native.call("py", "_note", "bpgen.progress") or "") or {}
    local r = helpers.json_to_table(res or "") or {}
    report = report .. string.format("plan %s, progress note: run %s, %d lines, step %s\n",
      r.blueprint and "ok" or ("error " .. tostring(r.error or res)), tostring(p.run), #(p.lines or {}), tostring(p.step))
    helpers.write_file("stutter.txt", report, false)
    return
  end
  if done then return end
  if e.tick == 5 then
    native.call("py", "bpgen.ingame:progress", "")  -- (Python up and bpgen imported before timing)
    job = native.start("py", "harness.stutter_probe:work", "")
  elseif job then
    if e.tick % 30 == 0 then
      local t = now()
      native.call("py", "_note", "bpgen.progress")  -- (as zzz-bpgen's progress() reads it)
      waits[#waits + 1] = now() - t
    end
    local status, res = native.poll(job)
    if status ~= "pending" then
      log("stutter-test: worker " .. tostring(status) .. ": " .. tostring(res))
      done = true
      table.sort(waits)
      local sum = 0
      for _, w in pairs(waits) do sum = sum + w end
      report = string.format("calls %d max %d ms median %d ms total %d ms\n", #waits,
        waits[#waits] or -1, waits[math.ceil(#waits / 2)] or -1, sum)
      plan = native.start("py", "bpgen.ingame:plan", helpers.table_to_json({ recipe = "iron-gear-wheel",
        machine = "assembling-machine-2", belts = { "transport-belt" }, inserters = { "inserter", "fast-inserter" } }))
    end
  end
end)
