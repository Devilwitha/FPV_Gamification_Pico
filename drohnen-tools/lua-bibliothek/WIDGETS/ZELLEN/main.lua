-- Zellen – Zellenspannung mit Auto-Erkennung, Sag bei Punchouts und Restkapazität
-- Bollisoft Lua-Bibliothek · EdgeTX 2.10+ · RadioMaster TX15 und andere Farbdisplays
-- Datei: /WIDGETS/ZELLEN/main.lua
--
-- Liest die Akkuspannung (RxBt bei ExpressLRS/CRSF, sonst VFAS oder die gewählte Quelle)
-- und erkennt die Zellenzahl 1S–8S beim Einstecken des Akkus selbst. Angezeigt werden
-- Spannung pro Zelle, der Sag (Einbruch gegenüber der Ruhespannung) bei Punchouts,
-- die tiefste Zellenspannung und die Restkapazität:
--   - mit Option Kapaz (mAh) aus dem Verbrauch Capa des Stromsensors,
--   - sonst aus Bat% bzw. Fuel, falls der Flugcontroller ihn sendet,
--   - sonst geschätzt aus der Ruhespannung.
-- Die Zellenzahl wird aus der Spannung beim Einstecken geschätzt. Ein fast leerer Akku kann
-- dabei eine Zelle zu wenig ergeben – dann die Option Zellen fest einstellen.

local name = "Zellen"

local options = {
  { "Quelle", SOURCE, 0 },          -- leer = RxBt, sonst VFAS
  { "Zellen", VALUE, 0, 0, 8 },     -- 0 = automatisch erkennen
  { "Kapaz", VALUE, 0, 0, 9900 },   -- Akkukapazität in mAh (0 = Bat% oder Spannung)
  { "Alarm", VALUE, 33, 0, 40 },    -- Alarm unter x/10 V Ruhespannung pro Zelle (0 = aus)
  { "LiHV", BOOL, 0 },              -- Hochvolt-Akkus (4,35 V voll)
}

local DETECT = 150        -- 1,5 s nach dem Einstecken die Zellenzahl festlegen (10 ms)
local DECAY = 0.006       -- die gemerkte Ruhespannung sinkt höchstens so schnell (V/s pro Zelle)
local ALARM_EVERY = 1000  -- Alarm höchstens alle 10 s

-- Ruhespannung einer LiPo-Zelle → Restkapazität in %
local CURVE = {
  { 3.27, 0 }, { 3.61, 5 }, { 3.69, 10 }, { 3.71, 15 }, { 3.73, 20 }, { 3.75, 25 },
  { 3.77, 30 }, { 3.79, 35 }, { 3.80, 40 }, { 3.82, 45 }, { 3.84, 50 }, { 3.85, 55 },
  { 3.87, 60 }, { 3.91, 65 }, { 3.95, 70 }, { 3.98, 75 }, { 4.02, 80 }, { 4.08, 85 },
  { 4.11, 90 }, { 4.15, 95 }, { 4.20, 100 },
}

local C = {}

local function initColors()
  C.bg = lcd.RGB(18, 22, 27)
  C.text = lcd.RGB(240, 244, 247)
  C.muted = lcd.RGB(140, 152, 163)
  C.off = lcd.RGB(52, 60, 68)
  C.red = lcd.RGB(232, 72, 56)
  C.orange = lcd.RGB(245, 150, 40)
  C.green = lcd.RGB(64, 200, 110)
  C.blue = lcd.RGB(90, 162, 236)
end

local function clamp(v, lo, hi)
  if v < lo then return lo elseif v > hi then return hi end
  return v
end

local function num(v)
  if type(v) == "number" then return v end
  return 0
end

local function sensor(name)
  return getFieldInfo(name) ~= nil
end

local function readVolt(w)
  local src = w.options.Quelle
  local v = 0
  if src and src ~= 0 then v = num(getValue(src)) end
  if v == 0 then v = num(getValue("RxBt")) end
  if v == 0 then v = num(getValue("VFAS")) end
  return v
end

local function curvePct(cell, lihv)
  if lihv then cell = cell - 0.15 * clamp((cell - 3.6) / 0.75, 0, 1) end
  if cell <= CURVE[1][1] then return 0 end
  for i = 2, #CURVE do
    local a, b = CURVE[i - 1], CURVE[i]
    if cell <= b[1] then
      return a[2] + (b[2] - a[2]) * (cell - a[1]) / (b[1] - a[1])
    end
  end
  return 100
end

local function cellColor(cell)
  if cell >= 3.7 then return C.green elseif cell >= 3.5 then return C.orange end
  return C.red
end

local function reset(w, now)
  w.present, w.since, w.cells, w.peakV = true, now, nil, 0
  w.rest, w.minCell, w.maxSag, w.sag = nil, nil, 0, 0
end

local function sample(w)
  local now = getTime()
  local dt = (now - (w.lastT or now)) / 100
  w.lastT = now
  local v = 0
  if getRSSI() > 0 then v = readVolt(w) end
  w.v = v
  if v < 0.5 then
    w.present = false
    return now
  end
  if not w.present then reset(w, now) end

  local fixed = w.options.Zellen or 0
  local lihv = w.options.LiHV == 1
  if fixed > 0 then
    w.cells = fixed
  elseif not w.cells then
    w.peakV = math.max(w.peakV, v)
    if now - w.since >= DETECT then
      w.cells = clamp(math.ceil(w.peakV / (lihv and 4.45 or 4.3)), 1, 8)
    end
  end
  if not w.cells then return now end

  local cell = v / w.cells
  w.cell = cell
  -- Ruhespannung: höchster Wert der letzten Zeit, sinkt langsam mit dem Akku mit.
  -- Der Sag ist der Einbruch gegenüber diesem Wert (z. B. bei einem Punchout).
  if not w.rest or cell > w.rest then
    w.rest = cell
  else
    w.rest = w.rest - DECAY * dt
  end
  w.sag = math.max(0, w.rest - cell)
  w.maxSag = math.max(w.maxSag, w.sag)
  w.minCell = w.minCell and math.min(w.minCell, cell) or cell

  -- Restkapazität
  local cap = w.options.Kapaz or 0
  if cap > 0 and sensor("Capa") then
    w.pct, w.pctSrc = clamp(100 - num(getValue("Capa")) / cap * 100, 0, 100), "mAh"
  elseif sensor("Bat%") then
    w.pct, w.pctSrc = clamp(num(getValue("Bat%")), 0, 100), "FC"
  elseif sensor("Fuel") then
    w.pct, w.pctSrc = clamp(num(getValue("Fuel")), 0, 100), "FC"
  else
    w.pct, w.pctSrc = curvePct(w.rest, lihv), "Schaetzung"
  end
  w.mah = sensor("Capa") and num(getValue("Capa")) or nil

  -- Alarm bei tiefer Ruhespannung
  local alarm = (w.options.Alarm or 0) / 10
  if alarm > 0 and w.rest < alarm and now - (w.alarmAt or -ALARM_EVERY) >= ALARM_EVERY then
    w.alarmAt = now
    playHaptic(80, 60)
    playNumber(math.floor(w.rest * 100 + 0.5), UNIT_VOLTS, PREC2)
  end
  return now
end

local function bar(x, y, bw, h, f, color)
  lcd.drawFilledRectangle(x, y, bw, h, C.off)
  local fw = math.floor(bw * clamp(f, 0, 1))
  if fw > 0 then lcd.drawFilledRectangle(x, y, fw, h, color) end
end

local function create(zone, opts)
  initColors()
  return { zone = zone, options = opts, present = false, maxSag = 0, sag = 0 }
end

local function update(w, opts)
  w.options = opts
  w.cells = nil
  w.peakV = w.v or 0
  w.since = getTime()
end

local function background(w)
  sample(w)
end

local function refresh(w, event, touchState)
  sample(w)
  local z = w.zone
  if event ~= nil then z = { x = 0, y = 0, w = LCD_W, h = LCD_H } end
  lcd.drawFilledRectangle(z.x, z.y, z.w, z.h, C.bg)
  local x, y = z.x + 10, z.y + 6
  local lihv = w.options.LiHV == 1

  if not w.present or not w.cells then
    local msg = "Kein Akku"
    if w.present then msg = "Erkenne Zellen ..." elseif getRSSI() == 0 then msg = "Keine Telemetrie" end
    lcd.drawText(x, y, "ZELLEN", SMLSIZE + BOLD + C.muted)
    lcd.drawText(x, y + 20, msg, (z.h >= 80 and MIDSIZE or 0) + C.muted)
    if w.minCell and z.h >= 110 then
      lcd.drawText(x, y + 52, string.format("Letzter Flug: min %.2f V  Sag max %.2f V", w.minCell, w.maxSag), SMLSIZE + C.muted)
    end
    return
  end

  local head = string.format("%dS %s  %.2f V", w.cells, lihv and "LiHV" or "LiPo", w.v)
  local cellTxt = string.format("%.2f", w.cell)
  local col = cellColor(w.rest)

  if z.w >= 300 and z.h >= 160 then
    lcd.drawText(x, y, head, 0 + BOLD + C.text)
    lcd.drawText(z.x + z.w - 10, y, "pro Zelle", SMLSIZE + RIGHT + C.muted)
    lcd.drawText(x, y + 24, cellTxt, XXLSIZE + col)
    local tw = lcd.sizeText and lcd.sizeText(cellTxt, XXLSIZE) or 150
    lcd.drawText(x + tw + 6, y + 58, "V", DBLSIZE + col)
    -- Sag
    local sx = z.x + math.floor(z.w * 0.5) + 10
    local sw = z.x + z.w - 10 - sx
    lcd.drawText(sx, y + 34, string.format("Sag %.2f V", w.sag), 0 + C.text)
    bar(sx, y + 58, sw, 12, w.sag / 0.8, w.sag > 0.5 and C.red or (w.sag > 0.3 and C.orange or C.blue))
    lcd.drawText(sx, y + 74, string.format("max %.2f V", w.maxSag), SMLSIZE + C.muted)
    lcd.drawText(sx, y + 92, string.format("Ruhe %.2f V", w.rest), SMLSIZE + C.muted)
    -- Restkapazität
    local ry = z.y + z.h - 62
    local rest = string.format("%d %%", math.floor(w.pct + 0.5))
    if w.mah then rest = rest .. string.format("   %d mAh", math.floor(w.mah)) end
    lcd.drawText(x, ry, rest, 0 + BOLD + C.text)
    lcd.drawText(z.x + z.w - 10, ry + 2, "Rest (" .. w.pctSrc .. ")", SMLSIZE + RIGHT + C.muted)
    bar(x, ry + 22, z.w - 20, 14, w.pct / 100, w.pct > 30 and C.green or (w.pct > 15 and C.orange or C.red))
    lcd.drawText(x, ry + 42, string.format("Tiefste Zelle %.2f V", w.minCell), SMLSIZE + C.muted)
  elseif z.w >= 160 and z.h >= 80 then
    lcd.drawText(x, y, head, SMLSIZE + BOLD + C.text)
    lcd.drawText(x, y + 18, cellTxt .. " V", DBLSIZE + col)
    lcd.drawText(z.x + z.w - 10, y + 22, string.format("Sag %.2f", w.sag), SMLSIZE + RIGHT + C.text)
    lcd.drawText(z.x + z.w - 10, y + 38, string.format("max %.2f", w.maxSag), SMLSIZE + RIGHT + C.muted)
    bar(x, z.y + z.h - 18, z.w - 20, 10, w.pct / 100, w.pct > 30 and C.green or (w.pct > 15 and C.orange or C.red))
  else
    lcd.drawText(z.x + 4, z.y + 2, cellTxt .. "V " .. w.cells .. "S", SMLSIZE + BOLD + col)
    bar(z.x + 4, z.y + z.h - 8, z.w - 8, 5, w.sag / 0.8, C.orange)
  end
end

return {
  name = name,
  options = options,
  create = create,
  update = update,
  background = background,
  refresh = refresh,
}
