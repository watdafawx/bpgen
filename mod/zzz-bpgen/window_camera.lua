-- The preview's camera: dragged with the left button, zoomed with the wheel, panned and fitted with its buttons, and the
-- size grip (with the loader's std plugin: the mouse is this peer's own). The hover state is momentary, not saved.

local W = require("window_core")
local stdwin, stdinput, dnd, safe, NAME, find = W.stdwin, W.stdinput, W.dnd, W.safe, W.NAME, W.find
local results = W.results

-- (the preview's drag, zoom and size grip: momentary, not saved)
local cam_hover, drags, wheel_seen, left_during = {}, {}, {}, {}
local grip_hover, resizing, resized_at = {}, {}, {}

-- the preview with the loader's std plugin: drag it with the left button, zoom with the wheel (without it: the
-- arrow and -/+ buttons)


local function camera_tick()
  if not next(cam_hover) and not next(drags) and not next(grip_hover) and not next(resizing) then return end
  -- (the mouse is this peer's own: in multiplayer the preview moves with its buttons only)
  if not safe.has("std") or game.is_multiplayer() then return end
  local ok, m = pcall(function() return helpers.json_to_table(native.call("std", "input") or "") end)
  if not (ok and m) then return end
  -- the size grip: dragged, the preview follows the cursor (in GUI units: pixels over the display scale)
  for pi, grip in pairs(grip_hover) do
    local player = game.get_player(pi)
    local frame = player and player.gui.screen[NAME]
    local rz = resizing[pi]
    if not (grip.valid and frame) then
      grip_hover[pi], resizing[pi] = nil, nil
    elseif m.left then
      if not rz then
        local w, h = W.cam_size(player)
        resizing[pi] = { x = m.x, y = m.y, w = w, h = h }
      else
        local scale = player.display_scale
        W.apply_size(player, frame, rz.w + (m.x - rz.x) / scale, rz.h + (m.y - rz.y) / scale)
        if m.x ~= rz.x or m.y ~= rz.y then resized_at[pi] = game.tick end
      end
    elseif rz then
      resizing[pi] = nil
      if rz.left then grip_hover[pi] = nil end
      local r = results[pi]
      if r and r.w then r.zoom = W.fit(player, r.w, r.h) end
    end
  end
  -- (the wheel over the preview zooms it, not the map behind: the game is kept from the wheel while it's hovered)
  if next(cam_hover) then pcall(native.call, "std", "wheel_capture", "250") end
  for pi, cam in pairs(cam_hover) do
    if not cam.valid then
      cam_hover[pi], drags[pi] = nil, nil
    else
      local d = drags[pi]
      if m.left and not (dnd and dnd.dragging(pi)) then
        local k = 32 * cam.zoom  -- (screen pixels a tile)
        if not d then
          drags[pi] = { x = m.x, y = m.y, cx = cam.position.x, cy = cam.position.y }
        else
          cam.position = { x = d.cx - (m.x - d.x) / k, y = d.cy - (m.y - d.y) / k }
        end
      else
        drags[pi] = nil
        if left_during[pi] then  -- (the drag ended outside the preview: it isn't hovered any more)
          cam_hover[pi], left_during[pi] = nil, nil
        end
      end
      local w = m.wheel or 0
      if wheel_seen[pi] == nil then wheel_seen[pi] = w end
      if w ~= wheel_seen[pi] then
        cam.zoom = math.max(0.03, math.min(4, cam.zoom * 1.2 ^ (w - wheel_seen[pi])))
        wheel_seen[pi] = w
      end
    end
  end
end

--- the pointer over the size grip or the preview (the window's on_gui_hover)
function W.on_hover(e)
  if e.element and e.element.valid and e.element.name == "bpgen_grip" then grip_hover[e.player_index] = e.element end
  if e.element and e.element.valid and e.element.name == "bpgen_cam" then
    cam_hover[e.player_index], wheel_seen[e.player_index], left_during[e.player_index] = e.element, nil, nil
  end
end

--- the pointer off the size grip or the preview (on_gui_leave)
function W.on_leave(e)
  if e.element and e.element.valid and e.element.name == "bpgen_grip" then
    if resizing[e.player_index] then resizing[e.player_index].left = true else grip_hover[e.player_index] = nil end
  end
  if e.element and e.element.valid and e.element.name == "bpgen_cam" and safe.has("std") then
    pcall(native.call, "std", "wheel_capture", "0")  -- (the wheel back to the game at once)
  end
  -- (a drag goes on outside the preview until the button is let go)
  if e.element and e.element.valid and e.element.name == "bpgen_cam" then
    if drags[e.player_index] then left_during[e.player_index] = true else cam_hover[e.player_index] = nil end
  end
end

-- (tests: hover events can't be faked)
function W.test_hover_camera(player_index)
  local frame = game.get_player(player_index).gui.screen[NAME]
  local cam = frame and find(frame, "bpgen_cam")
  cam_hover[player_index], wheel_seen[player_index] = cam, nil
  local w, h = W.cam_size(game.get_player(player_index))
  return cam and { x = cam.position.x, y = cam.position.y, zoom = cam.zoom,
    sections = #find(frame, "bpgen_section").items, w = w, h = h } or nil
end

function W.test_hover_grip(player_index)
  local frame = game.get_player(player_index).gui.screen[NAME]
  cam_hover[player_index] = nil
  if stdwin then  -- (the library's corner grip: the element tagged fstd_resize)
    local function grip(el)
      if el.tags and el.tags.fstd_resize then return el end
      for _, c in pairs(el.children) do local g = grip(c) if g then return g end end
    end
    stdinput.hovered[player_index] = frame and grip(frame)
    return
  end
  grip_hover[player_index] = frame and find(frame, "bpgen_grip")
end

--- the Size button (the grip's click): the next preset, unless a drag has just ended
function W.click_size(player, frame, r)
  if (resized_at[player.index] or -10) >= game.tick - 3 then return end  -- (the end of a drag, not a click)
  local res, scale = player.display_resolution, player.display_scale
  local w = W.cam_size(player)
  local next_ = W.PRESETS[1]
  for i, p in ipairs(W.PRESETS) do
    if w < math.floor(res.width / scale * p[1]) - 4 then next_ = p break end
    if i == #W.PRESETS then next_ = W.PRESETS[1] end
  end
  W.apply_size(player, frame, res.width / scale * next_[1], res.height / scale * next_[2])
  if r and r.w then
    local cam = find(frame, "bpgen_cam")
    r.zoom = W.fit(player, r.w, r.h)
    cam.zoom = r.zoom
  end
end

--- the pan arrows: a quarter of the view
function W.click_pan(player, frame, el)
  local cam = find(frame, "bpgen_cam")
  local step = W.view_px(player) / (32 * cam.zoom) / 4  -- (a quarter of the view)
  cam.position = { x = cam.position.x + el.tags.dx * step, y = cam.position.y + el.tags.dy * step }
end

--- the + and - buttons
function W.click_zoom(frame, el)
  local cam = find(frame, "bpgen_cam")
  cam.zoom = math.max(0.03, math.min(4, cam.zoom * el.tags.f))
end

--- Fit: the whole plan in view
function W.click_fit(frame, r)
  local cam = find(frame, "bpgen_cam")
  cam.zoom, cam.position = r.zoom, r.center
end

-- (what the other parts call)
W.camera_tick = camera_tick
