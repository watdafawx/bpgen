-- The window's building blocks: fse-std's themed components (each player's theme, /fse-theme) when the library is
-- there, else the same shapes in the game's own styles. Roles as fse-std's theme.lua names them.
local ok, theme = pcall(function()
  return script.active_mods["fse-std"] and require("__fse-std__/theme") or nil
end)
theme = ok and theme and theme.section and theme or nil

local M = { themed = theme ~= nil, theme = theme }

-- (without the library: the game's style standing for each role)
local PLAIN = { section = "bordered_frame", inset = "inside_shallow_frame", deep = "inside_deep_frame",
  button = "button", tab = "button", ok = "confirm_button", tool = "tool_button", slot = "slot_button",
  caption = "caption_label", dim = "label", text = "label", big = "heading_1_label", field = "textbox",
  check = "checkbox", dropdown = "dropdown", progress = "progressbar", list = "list_box", stat = "inside_shallow_frame",
  badge = "inside_shallow_frame", badge_text = "label", title = "frame_title" }

--- parent.add(spec) in a role's look; tweak: style values of your own, kept through a restyle
function M.add(parent, spec, role, tweak)
  if theme then return theme.add(parent, spec, role, tweak) end
  local el = parent.add(spec)
  if PLAIN[role] then pcall(function() el.style = PLAIN[role] end) end
  for k, v in pairs(tweak or {}) do el.style[k] = v end
  if role == "dim" then el.style.font_color = { 0.7, 0.7, 0.7 } end
  return el
end

--- a panel with a heading (returns the panel)
function M.section(parent, title, tooltip, name)
  if theme then return theme.section(parent, title, tooltip, name) end
  local f = parent.add({ type = "frame", style = "bordered_frame", direction = "vertical", name = name })
  if title then f.add({ type = "label", caption = title, style = "caption_label", tooltip = tooltip }) end
  return f
end

--- a small grey caption over a control (returns the flow to add the control to)
function M.field(parent, caption, tooltip)
  if theme then return theme.field(parent, caption, tooltip) end
  local f = parent.add({ type = "flow", direction = "vertical" })
  f.style.vertical_spacing = 0
  local l = f.add({ type = "label", caption = caption, tooltip = tooltip })
  l.style.font = "default-small"
  l.style.font_color = { 0.7, 0.7, 0.7 }
  return f
end

--- a panel whose heading folds it (returns the body)
function M.fold(parent, title, open, name)
  if theme then return theme.fold(parent, title, open, name) end
  local f = M.section(parent, title, nil, name)
  return f
end

--- a progress bar with a caption and a text right of it (returns the bar)
function M.progress(parent, value, opts)
  if theme then return theme.progress(parent, value, opts) end
  opts = opts or {}
  local holder = parent.add({ type = "flow", direction = "vertical" })
  local row = holder.add({ type = "flow", direction = "horizontal" })
  row.add({ type = "label", caption = opts.caption or "" })
  row.add({ type = "empty-widget" }).style.horizontally_stretchable = true
  row.add({ type = "label", caption = opts.text or "", name = "fstd_progress_text" })
  local bar = holder.add({ type = "progressbar", value = value or 0 })
  bar.style.horizontally_stretchable = true
  return bar
end

function M.set_progress(bar, value, text)
  if theme then return theme.set_progress(bar, value, text) end
  bar.value = value
  local row = text and bar.parent.children[1]
  if row and row.fstd_progress_text then row.fstd_progress_text.caption = text end
end

--- a big number over a caption (tweak: its size and the like, kept through a restyle)
function M.stat(parent, value, caption, tweak)
  if theme then
    local f = theme.stat(parent, value, caption)
    if tweak then theme.apply(f, "stat", tweak) end
    return f
  end
  local f = parent.add({ type = "frame", style = "inside_shallow_frame", direction = "vertical" })
  f.style.padding = 6
  for k, v in pairs(tweak or {}) do f.style[k] = v end
  f.add({ type = "label", caption = value, name = "fstd_stat_value", style = "heading_2_label" })
  M.add(f, { type = "label", caption = caption }, "dim")
  return f
end

--- a small pill with a word
function M.badge(parent, text, tooltip)
  if theme then return theme.badge(parent, text, tooltip) end
  local f = parent.add({ type = "frame", style = "inside_shallow_frame", direction = "horizontal", tooltip = tooltip })
  f.style.padding = { 0, 8 }
  f.add({ type = "label", caption = text })
  return f
end

--- themes what was built with the game's styles (new elements only); nothing to do without the library
function M.adopt(root)
  if theme then return theme.adopt(root) end
  return root
end

--- a short message at the top of the screen
function M.toast(player, text, seconds)
  if theme then return theme.toast(player, text, { seconds = seconds }) end
  player.create_local_flying_text({ text = text, create_at_cursor = true })
end

M.handlers = theme and theme.handlers or {}

return M
