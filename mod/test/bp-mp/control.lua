-- bpgen in multiplayer (fse/test/run_mp.py runs it: a headless server and one client). The joining player asks
-- for a line of gears (as AI Crew does, remote plan_line): the belts around are measured on every peer, the plan
-- runs in the client's Python, its answer reaches every peer through native.sync and the ghosts are placed the same
-- everywhere. Each peer writes the ghost count every 5 seconds.
script.on_init(function()
  local fp = remote.interfaces["freeplay"]
  if fp then
    if fp.set_skip_intro then remote.call("freeplay", "set_skip_intro", true) end
    if fp.set_disable_crashsite then remote.call("freeplay", "set_disable_crashsite", true) end
  end
  game.forces.player.research_all_technologies()
end)

script.on_event(defines.events.on_player_joined_game, function(e)
  storage.ask_at = e.tick + 120
end)

script.on_nth_tick(60, function(e)
  if storage.ask_at and e.tick >= storage.ask_at then
    storage.ask_at = nil
    remote.call("bpgen", "plan_line", 1, "iron-gear-wheel", 30, "none")
  end
  if e.tick % 300 ~= 0 then return end
  local ghosts = game.surfaces[1].count_entities_filtered({ type = "entity-ghost" })
  local text = ("tick %d ghosts %d players %d"):format(e.tick, ghosts, #game.connected_players)
  helpers.write_file("mp-server.txt", text .. "\n", true, 0)
  for _, p in pairs(game.connected_players) do helpers.write_file("mp-player-" .. p.index .. ".txt", text .. "\n", true, p.index) end
end)
