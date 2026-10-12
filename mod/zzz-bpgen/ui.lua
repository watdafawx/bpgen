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

--- a panel with a heading (returns the panel; tweak: its own style values, kept through a restyle)
function M.section(parent, title, tooltip, name, tweak)
  if theme then
    local f = theme.section(parent, title, tooltip, name)
    if tweak then theme.apply(f, "section", tweak) end
    return f
  end
  local f = parent.add({ type = "frame", style = "bordered_frame", direction = "vertical", name = name })
  for k, v in pairs(tweak or {}) do f.style[k] = v end
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

-- fse-std's layouts (0.6+): frames that stick to the window (a rail of tabs, a dock, a drawer); over(): one kept
-- over an element (0.6.4+, the fse loader, single player). Without them the window keeps everything inside.
M.layouts = theme ~= nil and theme.attach ~= nil and theme.dock ~= nil

--- a frame stuck to the window's edge (theme.attach), or nil without fse-std's layouts
function M.attach(frame, side, opts)
  if M.layouts then return theme.attach(frame, side, opts) end
end

--- a frame kept over an element of the window, or nil (put its controls in the window then)
function M.over(frame, el, opts)
  if theme and theme.over then return theme.over(frame, el, opts) end
end

--- the frames stuck to the window `name` (its rail, dock, drawer, toolbars)
function M.attached(player, name)
  local out = {}
  for _, c in pairs(player.gui.screen.children) do
    local a = c.tags.fstd_attach
    if a and a.owner == name and c.get_mod() == script.mod_name then out[#out + 1] = c end
  end
  return out
end

-- a window's settings button and panel (fse-std 0.6.6+: window.create{settings = true}, theme.menu)
M.menus = theme ~= nil and theme.menu ~= nil

--- a window's settings panel, opened (or closed when it was open: nil)
function M.menu(frame, opts)
  if M.menus then return theme.menu(frame, opts) end
end

--- another role for a themed element (a main button that breathes: "pulse"); nothing without the library
function M.set_role(el, role)
  if theme and el.tags.fstd_role and el.tags.fstd_role ~= role then theme.apply(el, role) end
end

--- an accent outline around an element (the fse loader's draw plugin) -> an id for unglow, or nil
function M.glow(el)
  if not (theme and theme.glow) then return nil end
  local ok, id = pcall(theme.glow, el)
  return ok and id or nil
end

function M.unglow(id)
  if id and remote.interfaces["fse-std"] and remote.interfaces["fse-std"].overlay_remove then
    remote.call("fse-std", "overlay_remove", id)
  end
end

--- a line chart (columns without the draw plugin) of `values`, or nil without the library
function M.graph(parent, values, opts)
  if theme and theme.graph then return theme.graph(parent, values, opts) end
end

function M.set_graph(f, values)
  if theme and f and f.valid then theme.set_graph(f, values) end
end

-- search boxes: name -> fn(player, text) (registered at load: every peer needs it)
local searches = {}
if theme and theme.on_search then
  theme.on_search(function(player, name, text)
    if searches[name] then searches[name](player, text) end
  end)
end
function M.on_search(name, fn) searches[name] = fn end

--- a search box (a text field and a clear button), heard by on_search(name); nil without the library
function M.search(parent, name, hint)
  if theme and theme.search then return theme.search(parent, name, hint) end
end

--- a badge's text
function M.set_badge(badge, text)
  if badge and badge.valid and badge.children[1] then badge.children[1].caption = text end
end

--- a fold opened or closed (theme.fold's body); a plain section (no fold) stays as it is
function M.open_fold(body, open)
  if not (body and body.valid and body.name == "fstd_fold_body") then return end
  body.visible = open
  local button = body.parent.children[1] and body.parent.children[1].children[1]
  if button and button.tags.fstd_fold then button.caption = open and "-" or "+" end
end

-- yes / no questions: name -> fn(player, data) run on yes
local asked = {}
if theme and theme.on_confirm then
  theme.on_confirm(function(player, name, yes, data)
    local fn = asked[name]
    if yes and fn then fn(player, data) end
  end)
end

--- what `name` does once the player says yes to its question (register at load: every peer needs it)
function M.on_yes(name, fn) asked[name] = fn end

--- asks first (fse-std's confirm dialog; Esc is no), or without the library does it at once.
--- opts: title, text, yes, no, data (handed to the on_yes function)
function M.confirm(player, name, opts)
  if theme and theme.confirm and theme.window then
    return theme.confirm(player, { name = name, title = opts.title, text = opts.text, yes = opts.yes, no = opts.no,
      data = opts.data })
  end
  local fn = asked[name]
  if fn then fn(player, opts.data) end
end

M.handlers = theme and theme.handlers or {}

return M
