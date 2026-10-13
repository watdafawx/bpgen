data:extend({
  -- the bpgen window as a web page over the game (fse's shells, single player) or the game's own GUI; the gear of
  -- either window switches it too
  { type = "bool-setting", name = "bpgen-web-window", setting_type = "runtime-per-user", default_value = true,
    order = "a" },
})
