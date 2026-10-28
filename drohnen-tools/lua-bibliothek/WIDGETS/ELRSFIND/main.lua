-- ELRS Finder – Signal-Tracker wie ein Geigerzähler, um einen abgestürzten Quad zu finden
-- Bollisoft Lua-Bibliothek · EdgeTX 2.10+ · RadioMaster TX15 und andere Farbdisplays
-- Datei: /WIDGETS/ELRSFIND/main.lua
--
-- Liest die Empfangsstärke des Quads (1RSS/2RSS in dBm, dazu RQly), glättet sie und
-- zeigt sie als Balken. Im Vollbild piept es wie ein Geigerzähler: je stärker das
-- Signal, desto schneller und höher.
--
-- So suchst du:
--  1. Im ExpressLRS-Lua die Sendeleistung fest auf 10–25 mW stellen (Dynamic Power aus).
--     Mit voller Leistung ist das Signal überall stark und zeigt keine Richtung.
--  2. Das Widget im Vollbild öffnen. Nur dort piept es, damit es im Flug nicht stört.
--  3. Langsam im Kreis drehen und dorthin gehen, wo es schneller und höher piept.
--     Den Sender vor den Körper halten – der Körper schirmt das Signal ab.
-- Antippen im Vollbild setzt den Spitzenwert zurück.

local name = "ELRSFinder"

local options = {
  { "Ton", BOOL, 1 },                     -- Pieptöne im Vollbild
  { "MinDbm", VALUE, -110, -130, -60 },   -- so schwach = langsamster Piepton
  { "MaxDbm", VALUE, -40, -90, -10 },     -- so stark = fast Dauerton
}

local SMOOTH = 0.3        -- Glättung des angezeigten Werts (0..1, kleiner = ruhiger)
local SLOW = 0.04         -- langsamer Mittelwert für die Tendenz HEISS/KALT
local PEAK_HOLD = 500     -- Spitzenwert so lange halten (Einheit 10 ms = 5 s)

local C = {}

local function initColors()
  C.bg = lcd.RGB(18, 22, 27)
  C.text = lcd.RGB(240, 244, 247)
  C.muted = lcd.RGB(140, 152, 163)
  C.off = lcd.RGB(52, 60, 68)
  C.red = lcd.RGB(232, 72, 56)
  C.yellow = lcd.RGB(244, 190, 60)
  C.green = lcd.RGB(64, 200, 110)
end

local function clamp(v, lo, hi)
  if v < lo then return lo elseif v > hi then return hi end
  return v
end

-- Stärkere der beiden Antennen; 0 heisst "kein Wert"
local function readDbm()
  local a, b = getValue("1RSS"), getValue("2RSS")
  if type(a) ~= "number" then a = 0 end
  if type(b) ~= "number" then b = 0 end
  if a == 0 and b == 0 then return nil end
  if a == 0 then return b end
  if b == 0 then return a end
  return math.max(a, b)
end

local function limits(w)
  local lo = w.options.MinDbm or -110
  local hi = w.options.MaxDbm or -40
  if hi < lo + 5 then hi = lo + 5 end
  return lo, hi
end

local function sample(w)
  local now = getTime()
  local dbm = nil
  if getRSSI() > 0 then dbm = readDbm() end
  if dbm then
    w.ema = w.ema and (w.ema + SMOOTH * (dbm - w.ema)) or dbm
    w.slow = w.slow and (w.slow + SLOW * (dbm - w.slow)) or dbm
    if not w.peak or w.ema >= w.peak or now - w.peakT > PEAK_HOLD then
      w.peak = w.ema
      w.peakT = now
    end
    local lq = getValue("RQly")
    w.lq = type(lq) == "number" and lq or 0
  else
    w.ema, w.slow, w.lq = nil, nil, 0
  end
  return now
end

local function level(w, dbm)
  if not dbm then return 0 end
  local lo, hi = limits(w)
  return clamp((dbm - lo) / (hi - lo), 0, 1)
end

local function beep(w, now)
  if now < w.nextBeep then return end
  if not w.ema then
    playTone(300, 120, 0, PLAY_NOW)       -- kein Signal: tiefer Ton alle 2 s
    w.nextBeep = now + 200
    return
  end
  local s = level(w, w.ema)
  playTone(math.floor(500 + 1700 * s), 30, 0, PLAY_NOW)
  -- 1,2 s Pause bei schwachem Signal, rund 70 ms bei vollem
  w.nextBeep = now + math.max(7, math.floor(120 * (0.06 ^ s)))
end

local function segColor(f)
  if f < 0.34 then return C.red elseif f < 0.67 then return C.yellow end
  return C.green
end

local function trend(w)
  if not w.ema or not w.slow then return "", C.muted end
  local d = w.ema - w.slow
  if d > 1.5 then return "HEISS", C.green elseif d < -1.5 then return "KALT", C.red end
  return "gleich", C.muted
end

local function drawBar(x, y, bw, h, s, peak)
  local n = bw >= 200 and 20 or 10
  local gap = 2
  local sw = math.max(1, math.floor((bw - gap * (n - 1)) / n))
  for i = 0, n - 1 do
    local f = (i + 1) / n
    local c = C.off
    if s >= f - 0.5 / n then c = segColor(f) end
    lcd.drawFilledRectangle(x + i * (sw + gap), y, sw, h, c)
  end
  if peak and peak > 0 then
    local px = x + math.floor(peak * (n * (sw + gap) - gap - 2))
    lcd.drawFilledRectangle(px, y - 3, 2, h + 6, C.text)
  end
end

local function create(zone, opts)
  initColors()
  return { zone = zone, options = opts, nextBeep = 0, peakT = 0, lq = 0 }
end

local function update(w, opts)
  w.options = opts
end

local function background(w)
  sample(w)
end

local function refresh(w, event, touchState)
  local now = sample(w)
  local full = event ~= nil
  if full then
    if EVT_TOUCH_TAP and event == EVT_TOUCH_TAP then
      w.peak, w.peakT = w.ema, now
    end
    if w.options.Ton == 1 then beep(w, now) end
  end

  local z = w.zone
  if full then z = { x = 0, y = 0, w = LCD_W, h = LCD_H } end
  lcd.drawFilledRectangle(z.x, z.y, z.w, z.h, C.bg)

  local s = level(w, w.ema)
  local peak = w.peak and level(w, w.peak) or nil
  local value = w.ema and string.format("%d", math.floor(w.ema + 0.5)) or "---"
  local valueColor = w.ema and segColor(math.max(s, 0.01)) or C.muted
  local tText, tColor = trend(w)
  local lo, hi = limits(w)

  if z.w >= 300 and z.h >= 160 then
    lcd.drawText(z.x + 12, z.y + 8, "ELRS FINDER", SMLSIZE + BOLD + C.muted)
    if w.ema then
      lcd.drawText(z.x + z.w - 12, z.y + 8, "LQ " .. math.floor(w.lq) .. " %", SMLSIZE + RIGHT + C.text)
    else
      lcd.drawText(z.x + z.w - 12, z.y + 8, "KEIN SIGNAL", SMLSIZE + RIGHT + BOLD + C.red)
    end
    local cx = z.x + math.floor(z.w / 2)
    local top = z.y + math.floor((z.h - 200) / 2) + 10
    lcd.drawText(cx, top, value, XXLSIZE + CENTER + valueColor)
    lcd.drawText(cx, top + 66, "dBm", MIDSIZE + CENTER + C.muted)
    lcd.drawText(cx, top + 96, tText, MIDSIZE + CENTER + BOLD + tColor)
    local by = z.y + z.h - 62
    drawBar(z.x + 16, by, z.w - 32, 26, s, peak)
    local info = "Spitze " .. (w.peak and string.format("%d", math.floor(w.peak + 0.5)) or "---") .. " dBm"
    lcd.drawText(z.x + 16, by + 34, info, SMLSIZE + C.muted)
    local hint = full and "Antippen: Spitze neu" or "Vollbild: Piepton"
    if full and w.options.Ton ~= 1 then hint = "Ton aus (Option Ton)" end
    lcd.drawText(z.x + z.w - 16, by + 34, hint, SMLSIZE + RIGHT + C.muted)
  elseif z.w >= 160 and z.h >= 80 then
    lcd.drawText(z.x + 8, z.y + 4, "FINDER", SMLSIZE + BOLD + C.muted)
    lcd.drawText(z.x + z.w - 8, z.y + 4, w.ema and ("LQ " .. math.floor(w.lq)) or "KEIN SIGNAL", SMLSIZE + RIGHT + (w.ema and C.text or C.red))
    lcd.drawText(z.x + 8, z.y + 22, value, DBLSIZE + valueColor)
    lcd.drawText(z.x + z.w - 8, z.y + 30, tText, SMLSIZE + RIGHT + BOLD + tColor)
    drawBar(z.x + 8, z.y + z.h - 22, z.w - 16, 14, s, peak)
  else
    lcd.drawText(z.x + 4, z.y + 2, value .. " dBm", SMLSIZE + BOLD + valueColor)
    drawBar(z.x + 4, z.y + z.h - 10, z.w - 8, 7, s, nil)
  end
  -- Bereich nur zur Kontrolle der Optionen
  if full and z.h >= 200 then
    lcd.drawText(z.x + 12, z.y + 26, lo .. " ... " .. hi .. " dBm", SMLSIZE + C.muted)
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
