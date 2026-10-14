data:extend({
  { type = "custom-input", name = "bpgen-request", key_sequence = "CONTROL + SHIFT + B", consuming = "game-only",
    include_selected_prototype = true },
  -- middle click over an item, fluid or recipe in a window (inventory, chest, Factoriopedia...): Line with its recipe
  -- (not consumed: the game's own middle click, a wagon slot's filter, still happens)
  { type = "custom-input", name = "bpgen-pick", key_sequence = "mouse-button-3", consuming = "none",
    include_selected_prototype = true },
  -- drag over part of the base (the window's Extend tab, "Pick base area"): bpgen builds next to it
  {
    type = "selection-tool",
    name = "bpgen-snapshot",
    icon = "__base__/graphics/icons/blueprint.png",
    icon_size = 64,
    flags = { "only-in-cursor", "not-stackable", "spawnable" },
    hidden = true,
    stack_size = 1,
    select = { border_color = { 0.3, 0.8, 1 }, cursor_box_type = "copy", mode = { "any-entity" } },
    alt_select = { border_color = { 0.3, 0.8, 1 }, cursor_box_type = "copy", mode = { "any-entity" } },
  },
  -- drag over an ore patch: the bpgen window's bus design mines it
  {
    type = "selection-tool",
    name = "bpgen-patches",
    icon = "__base__/graphics/icons/electric-mining-drill.png",
    icon_size = 64,
    flags = { "only-in-cursor", "not-stackable", "spawnable" },
    hidden = true,
    stack_size = 1,
    select = { border_color = { 1, 0.6, 0.1 }, cursor_box_type = "entity", mode = { "any-entity" },
      entity_type_filters = { "resource" } },
    alt_select = { border_color = { 1, 0.6, 0.1 }, cursor_box_type = "entity", mode = { "any-entity" },
      entity_type_filters = { "resource" } },
  },
  -- drag over a messy part of the base: the bpgen window rebuilds its machines as a compact mall in its place
  {
    type = "selection-tool",
    name = "bpgen-refit",
    icon = "__base__/graphics/icons/assembling-machine-2.png",
    icon_size = 64,
    flags = { "only-in-cursor", "not-stackable", "spawnable" },
    hidden = true,
    stack_size = 1,
    select = { border_color = { 1, 0.4, 0.8 }, cursor_box_type = "copy", mode = { "any-entity" } },
    alt_select = { border_color = { 1, 0.4, 0.8 }, cursor_box_type = "copy", mode = { "any-entity" } },
  },
  -- drag over a section of the base: the bpgen window moves it near you, its belts run to the base again
  {
    type = "selection-tool",
    name = "bpgen-move",
    icon = "__base__/graphics/icons/deconstruction-planner.png",
    icon_size = 64,
    flags = { "only-in-cursor", "not-stackable", "spawnable" },
    hidden = true,
    stack_size = 1,
    select = { border_color = { 0.4, 1, 0.5 }, cursor_box_type = "copy", mode = { "any-entity" } },
    alt_select = { border_color = { 0.4, 1, 0.5 }, cursor_box_type = "copy", mode = { "any-entity" } },
  },
  -- click (or drag over) where the planned build goes next to the base: the bpgen window places it there
  {
    type = "selection-tool",
    name = "bpgen-spot",
    icon = "__base__/graphics/icons/assembling-machine-1.png",
    icon_size = 64,
    flags = { "only-in-cursor", "not-stackable", "spawnable" },
    hidden = true,
    stack_size = 1,
    select = { border_color = { 1, 0.85, 0.2 }, cursor_box_type = "copy", mode = { "any-tile" } },
    alt_select = { border_color = { 1, 0.85, 0.2 }, cursor_box_type = "copy", mode = { "any-tile" } },
  },
  -- the bpgen window (planning in game needs the fse loader)
  { type = "custom-input", name = "bpgen-window", key_sequence = "CONTROL + ALT + B", consuming = "game-only" },
  {
    type = "shortcut",
    name = "bpgen-window",
    action = "lua",
    associated_control_input = "bpgen-window",
    icon = "__base__/graphics/icons/blueprint.png",
    icon_size = 64,
    small_icon = "__base__/graphics/icons/blueprint.png",
    small_icon_size = 64,
  },
})

-- "Try in sandbox": the lab's power, one on each of its electric networks (under a pole: no collision, not
-- selectable, never in a blueprint)
local power = table.deepcopy(data.raw["electric-energy-interface"]["electric-energy-interface"])
power.name = "bpgen-sandbox-power"
power.hidden = true
power.flags = { "not-blueprintable", "not-deconstructable", "not-on-map", "placeable-off-grid", "not-upgradable",
  "no-copy-paste", "not-in-kill-statistics", "hide-alt-info" }
power.minable = nil
power.selection_box = nil
power.collision_box = { { -0.1, -0.1 }, { 0.1, 0.1 } }
power.collision_mask = { layers = {} }
power.picture, power.pictures, power.animation, power.animations = nil, nil, nil, nil
power.integration_patch = nil
power.picture = { filename = "__core__/graphics/empty.png", size = 1 }
power.gui_mode = "none"
power.energy_source = { type = "electric", buffer_capacity = "1TJ", usage_priority = "primary-output",
  input_flow_limit = "0W", output_flow_limit = "1TW" }
power.energy_production = "1TW"
power.continuous_animation = nil
power.working_sound = nil
data:extend({ power })
