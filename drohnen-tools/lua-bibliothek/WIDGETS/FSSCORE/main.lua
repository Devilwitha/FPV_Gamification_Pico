-- FS Scorer – Freestyle-Punkte direkt am Sender und ein Session-Debrief nach dem Disarm
-- Bollisoft Lua-Bibliothek · EdgeTX 2.10+ · Betaflight mit CRSF/ExpressLRS-Telemetrie
-- Datei: /WIDGETS/FSSCORE/main.lua
--
-- Betaflight sendet über CRSF die Lage des Copters (Ptch, Roll, Yaw). Das Widget rechnet
-- daraus Drehraten, erkennt Flips/Rollen (330° Drehung innerhalb von 2 s) und Yaw-Spins,
-- zählt die Flugzeit und zeigt nach dem Disarm ein Debrief, z. B.
--   Session Score: 1'450 Pkt | Max Flip Rate: 920 deg/s | Flight Time: 3:24
-- Jede Session landet zusätzlich in /LOGS/fsscore.csv.
--
-- Wichtig: Die Telemetrie kommt viel seltener als die Blackbox (je nach ELRS-Telemetrie-
-- Rate etwa 5–25 Mal pro Sekunde). Drehraten und Punkte sind deshalb Schätzungen.
-- Für die genaue Wertung: Blackbox Viewer mit Leaderboard und Duellen auf bollisoft.ch.
-- Beschleunigung (G-Kräfte) sendet Betaflight über CRSF nicht.
--
-- Option Arm: den Arm-Schalter wählen (z. B. SA). Ohne Schalter zählt der Telemetrie-Link
-- als "armed" – dann kommt das Debrief erst, wenn der Akku abgesteckt wird.
-- Antippen im Vollbild schliesst das Debrief.

local name = "FS Scorer"

local options = {
  { "Arm", SOURCE, 0 },        -- Arm-Schalter
  { "Umkehren", BOOL, 0 },     -- Schalter armt in der unteren Stellung
  { "Ansage", BOOL, 1 },       -- Punkte nach dem Disarm ansagen
}

local WINDOW = 200           -- Trick-Fenster: 2 s (10 ms)
local TRICK_DEG = 330        -- so viel Drehung im Fenster = ein Flip bzw. eine Rolle
local MIN_FLIGHT = 500       -- kürzere "Flüge" (unter 5 s) ergeben kein Debrief
local LOG_FILE = "/LOGS/fsscore.csv"
local RAD = 57.29578

local C = {}

local function initColors()
  C.bg = lcd.RGB(18, 22, 27)
  C.panel = lcd.RGB(30, 36, 43)
  C.text = lcd.RGB(240, 244, 247)
  C.muted = lcd.RGB(140, 152, 163)
  C.accent = lcd.RGB(255, 140, 26)
  C.green = lcd.RGB(64, 200, 110)
  C.red = lcd.RGB(232, 72, 56)
end

local function wrap(d)
  if d > 180 then return d - 360 elseif d < -180 then return d + 360 end
  return d
end

local function thousands(n)
  local s = tostring(math.floor(n))
  local out = s:reverse():gsub("(%d%d%d)", "%1'"):reverse()
  if out:sub(1, 1) == "'" then out = out:sub(2) end
  return out
end

local function clock(ticks)
  local s = math.floor(ticks / 100)
  return string.format("%d:%02d", math.floor(s / 60), s % 60)
end

local function isArmed(w)
  local src = w.options.Arm
  if src and src ~= 0 then
    local v = getValue(src)
    if type(v) ~= "number" then return false end
    if w.options.Umkehren == 1 then return v < 0 end
    return v > 0
  end
  return getRSSI() > 0
end

-- Grad pro Einheit: Betaflight/EdgeTX melden je nach Version Radiant oder Grad
local function unitFactor(w, p, r, y)
  if not w.deg then
    local info = getFieldInfo("Roll")
    if UNIT_DEGREE and info and info.unit == UNIT_DEGREE then w.deg = true end
    if math.abs(p) > 3.5 or math.abs(r) > 3.5 or math.abs(y) > 3.5 then w.deg = true end
  end
  return w.deg and 1 or RAD
end

local function newSession(now)
  return {
    start = now, score = 0, tricks = 0, spins = 0, rot = 0, maxRate = 0,
    rollBuf = {}, yawBuf = {}, lastName = "",
  }
end

-- Drehung im Fenster aufsummieren; liefert Summe und Fensterdauer
local function windowSum(buf, now, add)
  buf[#buf + 1] = { now, add }
  while #buf > 0 and now - buf[1][1] > WINDOW do table.remove(buf, 1) end
  local sum = 0
  for i = 1, #buf do sum = sum + buf[i][2] end
  return sum, math.max(1, now - buf[1][1])
end

-- 100 je Flip/Rolle (+ Tempo-Bonus), 50 je Spin, 1 je 36° Drehung, 1 je Sekunde Flugzeit
local function score(s)
  local seconds = math.floor(((s.stop or getTime()) - s.start) / 100)
  return s.tricks * 100 + s.spins * 50 + math.floor(s.rot / 36) + math.floor(s.trickBonus or 0) + seconds
end

local function saveSession(s)
  local d = getDateTime()
  local f = io.open(LOG_FILE, "r")
  local exists = f ~= nil
  if f then io.close(f) end
  f = io.open(LOG_FILE, "a")
  if not f then return end
  if not exists then io.write(f, "Datum,Zeit,Flugzeit_s,Score,Tricks,Spins,MaxRate_degs\n") end
  io.write(f, string.format("%04d-%02d-%02d,%02d:%02d:%02d,%d,%d,%d,%d,%d\n", d.year, d.mon, d.day, d.hour, d.min, d.sec,
    math.floor((s.stop - s.start) / 100), math.floor(s.final), s.tricks, s.spins, math.floor(s.maxRate)))
  io.close(f)
end

local function track(w, now)
  local s = w.session
  local p, r, y = getValue("Ptch"), getValue("Roll"), getValue("Yaw")
  if type(p) ~= "number" or type(r) ~= "number" or type(y) ~= "number" then return end
  -- Nur neue Telemetrie-Pakete auswerten
  if p == w.lp and r == w.lr and y == w.ly then return end
  local k = unitFactor(w, p, r, y)
  if w.lp and w.lastT then
    local dt = now - w.lastT   -- in 10 ms
    local dRoll = math.abs(wrap((r - w.lr) * k))
    local dPitch = math.abs(wrap((p - w.lp) * k))
    local dYaw = math.abs(wrap((y - w.ly) * k))
    local rot = dRoll + dPitch
    s.rot = s.rot + rot + dYaw
    if dt >= 2 and dt <= 100 then
      s.maxRate = math.max(s.maxRate, rot * 100 / dt)
    end
    local sum, span = windowSum(s.rollBuf, now, rot)
    if sum >= TRICK_DEG then
      local rate = sum * 100 / span
      s.tricks = s.tricks + 1
      s.trickBonus = (s.trickBonus or 0) + rate / 10
      s.lastName, s.lastAt = "Flip / Rolle", now
      s.rollBuf = {}
    end
    local ysum = windowSum(s.yawBuf, now, dYaw)
    if ysum >= TRICK_DEG then
      s.spins = s.spins + 1
      s.lastName, s.lastAt = "Yaw-Spin", now
      s.yawBuf = {}
    end
  end
  w.lp, w.lr, w.ly, w.lastT = p, r, y, now
end

local function sample(w)
  local now = getTime()
  local armed = isArmed(w)
  if armed and not w.session then
    w.session, w.debrief = newSession(now), nil
    w.lp, w.lr, w.ly, w.lastT = nil, nil, nil, nil
  elseif not armed and w.session then
    local s = w.session
    s.stop = now
    w.session = nil
    if s.stop - s.start >= MIN_FLIGHT then
      s.final = score(s)
      w.debrief = s
      saveSession(s)
      if w.options.Ansage == 1 then
        playTone(1200, 80, 40)
        playNumber(math.floor(s.final), 0)
      end
    end
  end
  if w.session and getRSSI() > 0 then track(w, now) end
  return now
end

local function create(zone, opts)
  initColors()
  return { zone = zone, options = opts }
end

local function update(w, opts)
  w.options = opts
end

local function background(w)
  sample(w)
end

local function debriefLine(s)
  return "Session Score: " .. thousands(s.final) .. " Pkt | Max Flip Rate: " .. math.floor(s.maxRate)
    .. " deg/s | Flight Time: " .. clock(s.stop - s.start)
end

local function refresh(w, event, touchState)
  local now = sample(w)
  if event ~= nil and EVT_TOUCH_TAP and event == EVT_TOUCH_TAP then w.debrief = nil end
  local z = w.zone
  if event ~= nil then z = { x = 0, y = 0, w = LCD_W, h = LCD_H } end
  lcd.drawFilledRectangle(z.x, z.y, z.w, z.h, C.bg)
  local x, y = z.x + 10, z.y + 6
  local big = z.w >= 300 and z.h >= 160
  local s = w.session
  local d = w.debrief

  if s then
    local sc = score(s)
    lcd.drawText(x, y, "LIVE", SMLSIZE + BOLD + C.green)
    lcd.drawText(z.x + z.w - 10, y, clock(now - s.start), (big and MIDSIZE or SMLSIZE) + RIGHT + C.text)
    lcd.drawText(x, y + (big and 22 or 16), thousands(sc), (big and XXLSIZE or DBLSIZE) + C.accent)
    if big then
      lcd.drawText(x + 4, y + 90, "Punkte", SMLSIZE + C.muted)
      lcd.drawText(x, y + 120, string.format("Tricks %d   Spins %d", s.tricks, s.spins), MIDSIZE + C.text)
      lcd.drawText(x, y + 150, string.format("Max Rate %d deg/s", math.floor(s.maxRate)), 0 + C.muted)
      if s.lastAt and now - s.lastAt < 200 then
        lcd.drawText(z.x + z.w - 10, y + 40, s.lastName .. "!", MIDSIZE + RIGHT + BOLD + C.accent)
      end
      if getRSSI() == 0 then lcd.drawText(x, z.y + z.h - 24, "Keine Telemetrie", SMLSIZE + C.red) end
    end
  elseif d then
    lcd.drawText(x, y, "SESSION DEBRIEF", SMLSIZE + BOLD + C.accent)
    if big then
      lcd.drawText(x, y + 24, thousands(d.final), XXLSIZE + C.text)
      lcd.drawText(x + 4, y + 92, "Pkt", SMLSIZE + C.muted)
      local rx = z.x + math.floor(z.w * 0.52)
      lcd.drawText(rx, y + 30, "Max Flip Rate", SMLSIZE + C.muted)
      lcd.drawText(rx, y + 46, math.floor(d.maxRate) .. " deg/s", MIDSIZE + C.text)
      lcd.drawText(rx, y + 80, "Flight Time", SMLSIZE + C.muted)
      lcd.drawText(rx, y + 96, clock(d.stop - d.start), MIDSIZE + C.text)
      lcd.drawText(x, y + 130, string.format("Tricks %d   Spins %d", d.tricks, d.spins), 0 + C.text)
      lcd.drawFilledRectangle(z.x + 6, z.y + z.h - 40, z.w - 12, 30, C.panel)
      lcd.drawText(z.x + 12, z.y + z.h - 33, debriefLine(d), SMLSIZE + C.text)
    else
      lcd.drawText(x, y + 16, thousands(d.final) .. " Pkt", DBLSIZE + C.text)
      lcd.drawText(x, z.y + z.h - 20, math.floor(d.maxRate) .. " deg/s  " .. clock(d.stop - d.start), SMLSIZE + C.muted)
    end
  else
    lcd.drawText(x, y, "FS SCORER", SMLSIZE + BOLD + C.muted)
    lcd.drawText(x, y + 22, "Bereit", (big and DBLSIZE or MIDSIZE) + C.text)
    if big then
      lcd.drawText(x, y + 64, "Armen startet die Session.", 0 + C.muted)
      if not w.options.Arm or w.options.Arm == 0 then
        lcd.drawText(x, y + 88, "Tipp: Arm-Schalter in den Widget-Optionen waehlen.", SMLSIZE + C.muted)
      end
    end
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
