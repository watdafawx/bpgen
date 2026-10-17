-- The window's size and place: the preview's size (kept as the player asked for it, shown at what fits), the room the
-- screen leaves, a new window's place, the drawers stuck to its edge, and fitting it again when its scale changes.

local W = require("window_core")
local ui, web, stdwin, CAM_MIN_H, CAM_MIN_W = W.ui, W.web, W.stdwin, W.CAM_MIN_H, W.CAM_MIN_W
local CAM_TIGHT_H, DOCK_H, HOTBAR_H, LEFT_W, MAP_H = W.CAM_TIGHT_H, W.DOCK_H, W.HOTBAR_H, W.LEFT_W, W.MAP_H
local MAP_W, NAME, OUT_BELOW, OUT_SIDE, docked = W.MAP_W, W.NAME, W.OUT_BELOW, W.OUT_SIDE, W.docked
local find, results = W.find, W.results

local OLD_FRAME_H = 366  -- (before 0.21: the window's height besides the preview, its rows under it included)

--- the window's height besides the preview (GUI units at 100 %): its title and the mode's line; inside the window
--- (not docked) also the mode tabs and the rows of tools and actions under the preview
local function frame_h(player) return docked(player) and 100 or 234 end

-- the preview's size: the player's own (resized), else a share of the screen; three presets for the size button.
-- Kept as the player asked for it (storage.bpgen_cam: w, h); shown at what fits now (sw, sh), so a bigger window
-- scale or a smaller screen shrinks the preview, and it grows back when there's room again
local PRESETS = { { 0.45, 0.68 }, { 0.6, 0.8 }, { 0.75, 0.86 } }
local function wanted_size(player)
  storage.bpgen_cam = storage.bpgen_cam or {}
  local s = storage.bpgen_cam[player.index]
  if s and not s.v then  -- (kept before 0.21: the preview takes the window's whole height now, as tall a window)
    s.h, s.v, s.sw, s.sh = s.h + OLD_FRAME_H - frame_h(player), 2, nil, nil
  end
  if s then return s.w, s.h end
  local res, scale = player.display_resolution, player.display_scale
  return math.max(520, math.floor(res.width / scale * PRESETS[1][1])), math.max(380, math.floor(res.height / scale * PRESETS[1][2]))
end
--- the preview's size as shown (GUI units at 100 %)
local function cam_size(player)
  local s = storage.bpgen_cam and storage.bpgen_cam[player.index]
  if s and s.sw then return s.sw, s.sh end
  return wanted_size(player)
end

--- the scale the window wants: its Size, else the player's Window size (fse-std 0.8+), else 1
local function want_scale(player)
  if not (stdwin and stdwin.scale) then return 1 end
  local _, want = stdwin.scale(player, NAME)
  return want or 1
end


--- the most the result's scroll area under Plan takes of a window `height` tall (GUI units at 100 %)
--- (a short window: less of it, the settings above keep room to show a section)
local function info_h(height)
  return math.max(110, math.min(300, math.floor(height * (height < 700 and 0.24 or 0.4))))
end

--- the room the window has at the scale it wants: the screen less the hotbar band, and less the minimap's column
--- when the preview still gets its smallest width without it. -> max_w, max_h (the preview's), min_h, the space
--- {sc = pixels a GUI unit, side, below, right = the free area's right edge in pixels, bottom = its bottom}
local function room(player)
  local res, ds = player.display_resolution, player.display_scale
  local out = docked(player)
  local side, below = out and OUT_SIDE or 0, out and OUT_BELOW or 0
  local right, bottom = res.width, res.height - HOTBAR_H * ds
  -- (the scale it wants; when even its smallest size doesn't fit the free area at that, the largest step at which it
  -- does: the library caps it there (the dock reserves the hotbar band below it), so the preview fills that room)
  local f = want_scale(player)
  local S = ui.theme and ui.theme.scale
  if S and S.snap then
    local g = math.min((right - 16) / ((CAM_MIN_W + LEFT_W + 2 * side) * ds),
      (bottom - 16) / ((CAM_TIGHT_H + frame_h(player) + below) * ds))
    if g < f then f = math.max(S.snap(g), 0.5) end
  end
  local sc = ds * f
  local max_w = right / sc - LEFT_W - 2 * side - 16
  local max_h = bottom / sc - frame_h(player) - below - 16
  if max_w - MAP_W * ds / sc >= CAM_MIN_W then  -- (the rail on the right clear of the minimap)
    right = res.width - MAP_W * ds
    max_w = max_w - MAP_W * ds / sc
  end
  local min_h = max_h < CAM_MIN_H and CAM_TIGHT_H or CAM_MIN_H
  return max_w, max_h, min_h, { sc = sc, f = f, side = side, below = below, right = right, bottom = bottom }
end

--- the dock (docked: a toolbar below the window) also reserves the hotbar band under it: what the library counts as
--- sticking out below the window (keeping it on the screen, capping its scale) keeps the window above the hotbar
local function reserve_below(player, f)
  local dock = player.gui.screen[NAME .. "__bottom"]
  local tags = dock and dock.tags
  local a = tags and tags.fstd_attach
  if not a then return end
  local h = DOCK_H + math.ceil(HOTBAR_H / f)
  if a.h ~= h then
    a.h = h
    dock.tags = tags
  end
end

--- the preview (and what's sized by it) at w x h; kept for this player as what they asked for unless `keep` (a
--- refit: the scale or the screen changed, what they asked for stays)
local function apply_size(player, frame, w, h, keep)
  -- (never bigger than the screen leaves room for at the window's scale: the left panel and the rows under the
  -- preview, the rails and the dock, clear of the hotbar and the minimap)
  local max_w, max_h, min_h, sp = room(player)
  reserve_below(player, sp.f)
  storage.bpgen_cam = storage.bpgen_cam or {}
  if not keep then storage.bpgen_cam[player.index] = { w = math.floor(w), h = math.floor(h), v = 2 } end
  w, h = math.max(CAM_MIN_W, math.floor(math.min(w, max_w))), math.max(min_h, math.floor(math.min(h, max_h)))
  local kept = storage.bpgen_cam[player.index]
  if not kept then  -- (no size of the player's own yet: the default share of the screen stays what's asked for)
    local dw, dh = wanted_size(player)
    kept = { w = dw, h = dh, v = 2 }
  end
  kept.sw, kept.sh = w, h
  storage.bpgen_cam[player.index] = kept
  -- (the library's window: that size, the frames stuck to it following)
  local tw = stdwin and frame.tags.fstd_tweak
  if tw and (tw.width ~= w + LEFT_W or tw.height ~= h + frame_h(player)) then
    local W, H = w + LEFT_W, h + frame_h(player)
    local saved = storage.fstd_windows and storage.fstd_windows[player.index] and storage.fstd_windows[player.index][NAME]
    if saved then saved.width, saved.height = W, H end
    if ui.theme and ui.theme.style then
      ui.theme.style(frame, { width = W, height = H })  -- (drawn at its scale, kept as its tweak)
    else
      frame.style.width, frame.style.height = W, H
      local tags = frame.tags
      tags.fstd_tweak = { width = W, height = H }
      frame.tags = tags
    end
    if ui.theme and ui.theme.follow then ui.theme.follow(frame) end
  end
  for _, n in ipairs({ "bpgen_cam", "bpgen_empty" }) do
    local el = find(frame, n)
    -- (in the library's window the preview stretches into what the window leaves it, both ways)
    if el and not stdwin then el.style.width, el.style.height = w, h end
    if el and stdwin then ui.style(el, { minimal_width = CAM_MIN_W, minimal_height = min_h }) end
  end
  -- (the result under Plan: up to a share of the window's height, the settings above it keep the rest)
  local info = find(frame, "bpgen_info")
  if info then ui.style(info, { maximal_height = info_h(h + frame_h(player)) }) end
  -- (and inside the free area if it grew past its edge: the rails and the dock too; pixels at the window's scale)
  local loc = frame.location
  if loc then
    local sc = player.display_scale * ui.scale_of(frame)
    local x = math.max(sp.side * sc, math.min(loc.x, sp.right - (w + LEFT_W + sp.side) * sc))
    local y = math.max(0, math.min(loc.y, sp.bottom - (h + frame_h(player) + sp.below) * sc))
    x, y = math.floor(x), math.floor(y)
    if x ~= loc.x or y ~= loc.y then
      frame.location = { x = x, y = y }
      local saved = storage.fstd_windows and storage.fstd_windows[player.index] and storage.fstd_windows[player.index][NAME]
      if saved and saved.location then saved.location = frame.location end
      if ui.theme and ui.theme.follow then ui.theme.follow(frame) end
    end
  end
end

--- where a new window of a w x h preview goes: centred in the free area (above the hotbar, left of the minimap when
--- that leaves room for it), at the scale it wants
local function place_new(player, w, h)
  local _, _, _, sp = room(player)
  local sc = sp.sc
  local W, H = (w + LEFT_W + 2 * sp.side) * sc, (h + frame_h(player) + sp.below) * sc
  return { x = math.floor(math.max(0, (sp.right - W) / 2) + sp.side * sc), y = math.floor(math.max(0, (sp.bottom - H) / 2)) }
end

--- the preview's size in pixels: the web window's island, else its size in GUI units at 100 % times the window's
--- scale and the game's
local function view_px(player)
  local ww, wh = web.view(player)
  if ww then return ww, wh end
  local scale = player.display_scale * ui.scale_of(player.gui.screen[NAME])
  local cw, ch = cam_size(player)
  return cw * scale, ch * scale
end

local function fit(player, w, h)
  local pw, ph = view_px(player)
  local z = math.min(pw / ((w + 2) * 32), ph / ((h + 2) * 32))
  return math.max(0.05, math.min(2, z))
end

--- a drawer stuck to the window's right edge (the test run's, History) beside it when the screen has room for it
--- there (clear of the minimap), else over the preview's right part, inside the window
local function place_drawer(player, frame, drawer)
  local tags = drawer and drawer.valid and drawer.tags
  local a = tags and tags.fstd_attach
  local loc, tw = frame.location, frame.tags.fstd_tweak
  if not (a and loc and type(tw) == "table" and tw.width) then return end
  local ds, res = player.display_scale, player.display_resolution
  local sc = ds * ui.scale_of(frame)
  local top = loc.y + (a.along or 0) * sc
  local edge = top < MAP_H * ds and res.width - MAP_W * ds or res.width
  local beside = loc.x + (tw.width - 8 + a.w) * sc <= edge
  local overlap = beside and 8 or a.w + 8
  if a.overlap ~= overlap then
    a.overlap = overlap
    drawer.tags = tags
  end
  if ui.theme and ui.theme.follow then ui.theme.follow(frame) end
end

-- the library's corner grip resizes the window: the preview follows
if stdwin then
  stdwin.on_resize(function(player, name, w, h)
    if name ~= NAME then return end
    local frame = player.gui.screen[NAME]
    if frame then apply_size(player, frame, w - LEFT_W, h - frame_h(player)) end
  end)
end

--- the window fitted again to the room it has (its scale, the game's UI scale or the screen changed): the preview
--- shrinks to fit (or grows back to what the player asked for), then the library checks its scale once more
local refitting = {}
local function reflow(player)
  local frame = player.gui.screen[NAME]
  if frame and frame.valid and web.model(player) then return W.view_resized(player) end  -- (the page lays itself out)
  if not (frame and frame.valid and stdwin) or refitting[player.index] then return end
  refitting[player.index] = true
  local ok, err = pcall(function()
    local w, h = wanted_size(player)
    apply_size(player, frame, w, h, true)
    if ui.theme and ui.theme.refit then ui.theme.refit(player, frame) end  -- (does it fit at its scale now?)
    for _, n in ipairs({ "__test", "__right" }) do
      local d = player.gui.screen[NAME .. n]
      if d and d.visible then place_drawer(player, frame, d) end
    end
    local r = results[player.index]
    if r and r.w then r.zoom = fit(player, r.w, r.h) end
  end)
  refitting[player.index] = nil
  if not ok then error(err) end
end
W.reflow = reflow
if stdwin and stdwin.on_scale then
  stdwin.on_scale(function(player, name)
    if name == NAME then reflow(player) end
  end)
end

-- (tests: the window resized as its corner grip does: the size kept, the frames stuck to it following)
function W.test_resize(player_index, w, h)
  local player = game.get_player(player_index)
  local frame = player.gui.screen[NAME]
  if not (stdwin and frame) then return end
  local saved = storage.fstd_windows and storage.fstd_windows[player_index] and storage.fstd_windows[player_index][NAME]
  if saved then saved.width, saved.height = w, h end
  if ui.theme and ui.theme.style then
    ui.theme.style(frame, { width = w, height = h })
  else
    frame.style.width, frame.style.height = w, h
    local tags = frame.tags
    tags.fstd_tweak = { width = w, height = h }
    frame.tags = tags
  end
  if ui.theme then ui.theme.follow(frame) end
  apply_size(player, frame, w - LEFT_W, h - frame_h(player))
end

--- the preview got another size (the web window's island, the window's scale): Fit's zoom follows, and the view too
--- while it shows the whole plan
function W.view_resized(player)
  local r = results[player.index]
  if not (r and r.w) then return end
  local old = r.zoom
  r.zoom = fit(player, r.w, r.h)
  local cam = find(player.gui.screen[NAME], "bpgen_cam")
  if cam and old and math.abs(cam.zoom - old) < 1e-6 then cam.zoom = r.zoom end
end

-- (what the other parts call)
W.PRESETS = PRESETS
W.apply_size = apply_size
W.cam_size = cam_size
W.fit = fit
W.frame_h = frame_h
W.info_h = info_h
W.place_drawer = place_drawer
W.place_new = place_new
W.room = room
W.view_px = view_px
W.wanted_size = wanted_size
