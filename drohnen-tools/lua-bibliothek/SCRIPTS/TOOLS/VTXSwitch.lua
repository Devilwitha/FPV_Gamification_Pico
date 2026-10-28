local toolName = "TNS|VTX & Profile|TNE"
-- VTXSwitch.lua – VTX-Band, Kanal, Leistung und Betaflight-Profile per Touch umschalten
-- Bollisoft Lua-Bibliothek · EdgeTX 2.10+ · ExpressLRS/CRSF + Betaflight 4.x
-- Datei: /SCRIPTS/TOOLS/VTXSwitch.lua
--
-- Weg der Befehle: Sender → ExpressLRS → Flugcontroller, als MSP über CRSF (wie das
-- offizielle Betaflight-Lua-Skript). Der Flugcontroller stellt den VTX dann über
-- SmartAudio/Tramp/MSP ein – keine Stick-Kombos im OSD nötig.
-- Voraussetzungen:
--   - ExpressLRS mit Telemetrie (Telem Ratio nicht "Off"), Quad mit Akku verbunden,
--   - in Betaflight eine VTX-Tabelle (Bänder A, B, E, F, R in der üblichen Reihenfolge).
-- Betaflight speichert nur, wenn der Quad disarmed ist. PID-Profile wechseln nur disarmed.
-- In der Schweiz und der EU sind 5725–5875 MHz mit höchstens 25 mW erlaubt; Kanäle
-- ausserhalb zeigt das Tool orange an.
--
-- Bedienung: Knöpfe antippen oder mit dem Drehrad wählen und ENTER drücken. RTN beendet.

local MSP_REQ, MSP_RESP, MSP_WRITE = 0x7A, 0x7B, 0x7C
local ADDR_FC, ADDR_RADIO = 0xC8, 0xEA
local MSP_VTX_CONFIG, MSP_SET_VTX_CONFIG = 88, 89
local MSP_VTXTABLE_POWERLEVEL, MSP_SELECT_SETTING, MSP_EEPROM_WRITE = 138, 210, 250

local BANDS = {
  { "A", { 5865, 5845, 5825, 5805, 5785, 5765, 5745, 5725 } },
  { "B", { 5733, 5752, 5771, 5790, 5809, 5828, 5847, 5866 } },
  { "E", { 5705, 5685, 5665, 5645, 5885, 5905, 5925, 5945 } },
  { "F", { 5740, 5760, 5780, 5800, 5820, 5840, 5860, 5880 } },
  { "R", { 5658, 5695, 5732, 5769, 5806, 5843, 5880, 5917 } },
}
local LEGAL_MIN, LEGAL_MAX = 5725, 5875

local sel = { band = 5, ch = 1, power = 1, pit = false }
local levels = 5                 -- Leistungsstufen laut VTX-Tabelle
local labels = {}                -- Beschriftung der Stufen (z. B. "25"), vom FC gelesen
local fc = nil                   -- zuletzt gelesene Einstellung des FC
local queue = {}                 -- MSP-Befehle, die noch gesendet werden müssen
local waiting = nil              -- { cmd, seit, versuche } für Anfragen mit Antwort
local seq = 0
local status, statusColor = "Lese VTX vom Flugcontroller ...", nil
local buttons, focus = {}, 1
local C = {}

local function initColors()
  C.bg = lcd.RGB(18, 22, 27)
  C.bar = lcd.RGB(30, 36, 43)
  C.btn = lcd.RGB(44, 52, 60)
  C.on = lcd.RGB(255, 140, 26)
  C.onText = lcd.RGB(20, 12, 4)
  C.text = lcd.RGB(240, 244, 247)
  C.muted = lcd.RGB(140, 152, 163)
  C.focus = lcd.RGB(90, 162, 236)
  C.green = lcd.RGB(64, 200, 110)
  C.orange = lcd.RGB(245, 150, 40)
  C.red = lcd.RGB(232, 72, 56)
end

local function say(text, color)
  status, statusColor = text, color or C.text
end

-- ---------- MSP über CRSF ----------

-- Ein MSP-v1-Paket in einem einzigen CRSF-Block senden (für kurze Befehle reicht das)
local function mspSend(frame, cmd, payload)
  if not crossfireTelemetryPush() then return false end   -- Sendepuffer noch belegt
  local buf = { #payload, cmd }
  for i = 1, #payload do buf[#buf + 1] = payload[i] end
  local crc = 0
  for i = 1, #buf do crc = bit32.bxor(crc, buf[i]) end
  local out = { ADDR_FC, ADDR_RADIO, 0x30 + seq }   -- Version 1 (0x20) + Start (0x10) + Folgenummer
  seq = (seq + 1) % 16
  for i = 1, #buf do out[#out + 1] = buf[i] end
  out[#out + 1] = crc
  while #out < 10 do out[#out + 1] = 0 end
  return crossfireTelemetryPush(frame, out)
end

local function enqueue(frame, cmd, payload, expect)
  queue[#queue + 1] = { frame, cmd, payload or {}, expect }
end

local function pump()
  local q = queue[1]
  if not q or waiting then return end   -- eine Anfrage nach der anderen
  if mspSend(q[1], q[2], q[3]) then
    table.remove(queue, 1)
    if q[4] then waiting = { cmd = q[2], at = getTime(), tries = 1, item = q } end
  end
end

local function readConfig()
  enqueue(MSP_REQ, MSP_VTX_CONFIG, {}, true)
end

local function onConfig(p)
  if #p < 5 then return end
  local vtxType = p[1]
  fc = { band = p[2], ch = p[3], power = p[4], pit = p[5] == 1, freq = (p[6] or 0) + (p[7] or 0) * 256 }
  if vtxType == 255 then
    say("Kein VTX am Flugcontroller erkannt", C.orange)
    return
  end
  if fc.band >= 1 and fc.band <= #BANDS and fc.ch >= 1 and fc.ch <= 8 then
    sel.band, sel.ch = fc.band, fc.ch
  end
  if fc.power >= 1 then sel.power = fc.power end
  sel.pit = fc.pit
  if #p >= 15 and p[12] == 1 and p[15] > 0 then
    levels = math.min(p[15], 8)
    for i = 1, levels do enqueue(MSP_REQ, MSP_VTXTABLE_POWERLEVEL, { i }, true) end
  end
  say("VTX gelesen: " .. (BANDS[fc.band] and BANDS[fc.band][1] or "?") .. fc.ch, C.green)
end

local function onPowerLevel(p)
  -- Stufe, Wert (2 Byte), Länge der Beschriftung, Beschriftung
  local level, len = p[1], p[4] or 0
  if not level then return end
  local s = ""
  for i = 1, len do
    if p[4 + i] then s = s .. string.char(p[4 + i]) end
  end
  s = s:gsub("%s+$", "")
  if s ~= "" then labels[level] = s end
end

local function onReply(data)
  local flags = data[3]
  if not flags or bit32.band(flags, 0x10) == 0 then return end   -- nur Startblöcke
  local err = bit32.band(flags, 0x80) ~= 0
  local size, cmd = data[4] or 0, data[5]
  local p = {}
  for i = 1, size do p[i] = data[5 + i] end
  if waiting and waiting.cmd == cmd then waiting = nil end
  if cmd == MSP_VTX_CONFIG and not err then
    onConfig(p)
  elseif cmd == MSP_VTXTABLE_POWERLEVEL and not err then
    onPowerLevel(p)
  elseif cmd == MSP_SET_VTX_CONFIG then
    if err then
      say("Flugcontroller lehnt ab", C.red)
    else
      say("Uebernommen, speichere ...", C.green)
      enqueue(MSP_WRITE, MSP_EEPROM_WRITE, {}, true)
    end
  elseif cmd == MSP_EEPROM_WRITE then
    if err then say("Nicht gespeichert: Quad zuerst disarmen", C.orange) else say("Gespeichert", C.green) end
  elseif cmd == MSP_SELECT_SETTING then
    if err then say("Profil nicht gewechselt (disarmen?)", C.orange) else say("Profil gewechselt", C.green) end
  end
end

local function poll()
  for _ = 1, 8 do
    local cmd, data = crossfireTelemetryPop()
    if not cmd then break end
    if cmd == MSP_RESP and data and data[1] == ADDR_RADIO and data[2] == ADDR_FC then onReply(data) end
  end
  -- Ausbleibende Antwort: bis zu 3 Mal erneut fragen
  if waiting and getTime() - waiting.at > 150 then
    if waiting.tries >= 3 then
      if waiting.cmd == MSP_VTX_CONFIG then
        say("Keine Antwort vom FC - Telemetrie an, Akku dran?", C.orange)
      elseif waiting.cmd == MSP_VTXTABLE_POWERLEVEL then
        say("VTX gelesen (ohne Leistungsnamen)", C.green)
      else
        say("Keine Bestaetigung vom FC", C.orange)
      end
      waiting = nil
    else
      local it = waiting.item
      if mspSend(it[1], it[2], it[3]) then
        waiting.at, waiting.tries = getTime(), waiting.tries + 1
      end
    end
  end
end

-- ---------- Aktionen ----------

local function freq()
  return BANDS[sel.band][2][sel.ch]
end

local function sendVtx()
  local idx = (sel.band - 1) * 8 + (sel.ch - 1)       -- Band/Kanal statt Frequenz
  queue = {}
  waiting = nil
  enqueue(MSP_WRITE, MSP_SET_VTX_CONFIG, { idx % 256, math.floor(idx / 256), sel.power, sel.pit and 1 or 0 }, true)
  say("Sende " .. BANDS[sel.band][1] .. sel.ch .. " (" .. freq() .. " MHz) ...", C.text)
end

local function selectProfile(rate, index)
  local v = index - 1
  if rate then v = v + 0x80 end
  enqueue(MSP_WRITE, MSP_SELECT_SETTING, { v }, true)
  say((rate and "Rate-Profil " or "PID-Profil ") .. index .. " ...", C.text)
end

-- ---------- Oberfläche ----------

local function button(x, y, w, h, label, on, act, small)
  buttons[#buttons + 1] = { x = x, y = y, w = w, h = h, label = label, on = on, act = act, small = small }
end

local function row(y, title, items)
  lcd.drawText(10, y + 9, title, SMLSIZE + C.muted)
  local x0, gap = 84, 6
  local n = #items
  local bw = math.floor((480 - 10 - x0 - gap * (n - 1)) / n)
  for i = 1, n do
    local it = items[i]
    button(x0 + (i - 1) * (bw + gap), y, bw, 32, it[1], it[2], it[3], it[4])
  end
end

local function layout()
  buttons = {}
  local b = {}
  for i = 1, #BANDS do
    local n = i
    b[i] = { BANDS[i][1], sel.band == i, function() sel.band = n end }
  end
  row(40, "Band", b)
  local c = {}
  for i = 1, 8 do
    local n = i
    c[i] = { tostring(i), sel.ch == i, function() sel.ch = n end }
  end
  row(78, "Kanal", c)
  local p = {}
  for i = 1, levels do
    local n = i
    p[i] = { labels[i] or tostring(i), sel.power == i, function() sel.power = n end, labels[i] ~= nil }
  end
  p[#p + 1] = { "Pit", sel.pit, function() sel.pit = not sel.pit end }
  row(116, "Leistung", p)
  button(330, 158, 140, 44, "Senden", false, sendVtx)
  button(250, 158, 74, 44, "Lesen", false, readConfig)
  local pid, rate = {}, {}
  for i = 1, 4 do
    local n = i
    pid[i] = { tostring(i), false, function() selectProfile(false, n) end }
    rate[i] = { tostring(i), false, function() selectProfile(true, n) end }
  end
  row(214, "PID-Profil", pid)
  row(252, "Rates", rate)
  if focus > #buttons then focus = #buttons end
end

local function drawButtons()
  for i = 1, #buttons do
    local b = buttons[i]
    lcd.drawFilledRectangle(b.x, b.y, b.w, b.h, b.on and C.on or C.btn)
    if i == focus then lcd.drawRectangle(b.x - 2, b.y - 2, b.w + 4, b.h + 4, C.focus, 2) end
    local font = b.small and SMLSIZE or 0
    local ty = b.y + math.floor(b.h / 2) - (b.small and 7 or 9)
    lcd.drawText(b.x + math.floor(b.w / 2), ty, b.label, font + CENTER + (b.on and C.onText or C.text))
  end
end

local function draw()
  lcd.clear(C.bg)
  lcd.drawFilledRectangle(0, 0, LCD_W, 32, C.bar)
  lcd.drawText(10, 7, "VTX & PROFILE", 0 + BOLD + C.text)
  if fc and fc.freq and fc.freq > 0 then
    lcd.drawText(LCD_W - 10, 9, "FC: " .. fc.freq .. " MHz", SMLSIZE + RIGHT + C.muted)
  end
  layout()
  local f = freq()
  local legal = f >= LEGAL_MIN and f <= LEGAL_MAX
  local color = legal and C.text or C.orange
  lcd.drawText(10, 158, BANDS[sel.band][1] .. sel.ch, DBLSIZE + BOLD + color)
  lcd.drawText(84, 166, f .. " MHz", MIDSIZE + color)
  lcd.drawText(12, 194, legal and "im CH/EU-Band" or "ausserhalb CH/EU 5725-5875", SMLSIZE + (legal and C.muted or C.orange))
  drawButtons()
  lcd.drawFilledRectangle(0, LCD_H - 28, LCD_W, 28, C.bar)
  lcd.drawText(10, LCD_H - 22, status, SMLSIZE + (statusColor or C.text))
end

local function hit(x, y)
  for i = 1, #buttons do
    local b = buttons[i]
    if x >= b.x and x < b.x + b.w and y >= b.y and y < b.y + b.h then return i end
  end
  return nil
end

local function init()
  initColors()
  statusColor = C.muted
  readConfig()
end

local function run(event, touchState)
  if event == EVT_VIRTUAL_EXIT then return 1 end
  layout()
  if EVT_TOUCH_TAP and event == EVT_TOUCH_TAP and touchState then
    local i = hit(touchState.x, touchState.y)
    if i then
      focus = i
      buttons[i].act()
    end
  elseif event == EVT_VIRTUAL_NEXT then
    focus = focus % #buttons + 1
  elseif event == EVT_VIRTUAL_PREV then
    focus = (focus - 2) % #buttons + 1
  elseif event == EVT_VIRTUAL_ENTER then
    if buttons[focus] then buttons[focus].act() end
  end
  pump()
  poll()
  draw()
  return 0
end

return { init = init, run = run }
