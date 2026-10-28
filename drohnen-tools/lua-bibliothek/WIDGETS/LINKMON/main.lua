-- Link Health – ExpressLRS-Funkstatus auf einen Blick: LQ, RSSI je Antenne, SNR, Leistung, Rate
-- Bollisoft Lua-Bibliothek · EdgeTX 2.10+ · RadioMaster TX15 und andere Farbdisplays
-- Datei: /WIDGETS/LINKMON/main.lua
--
-- Zeigt Link-Qualität (RQly), RSSI beider Empfänger-Antennen (1RSS/2RSS), SNR (RSNR),
-- die gerade gesendete Leistung (TPWR, mit Dynamic Power) und die Paketrate (RFMD),
-- dazu den LQ-Verlauf der letzten Minute und die Tiefstwerte der Sitzung.
-- Warnt früh, bevor der Failsafe greift:
--   - eine Antenne ist dauerhaft deutlich schwächer (Antenne beschädigt oder schlecht verlegt),
--   - LQ fällt unter die Schwelle, RSSI nähert sich der Empfindlichkeitsgrenze,
--   - der Sender regelt schon auf volle Leistung hoch.
-- Antippen im Vollbild setzt die Sitzungswerte zurück.

local name = "LinkHealth"

local options = {
  { "RssiWarn", VALUE, -95, -120, -60 },  -- RSSI-Warnschwelle in dBm
  { "LqWarn", VALUE, 70, 10, 99 },        -- LQ-Warnschwelle in %
  { "MaxPwr", VALUE, 250, 10, 1000 },     -- höchste eingestellte Sendeleistung (mW)
}

-- ExpressLRS 3.x meldet in RFMD die Paketrate als Nummer. Unbekannte Nummern zeigt das
-- Widget roh an (andere ELRS-Versionen oder neue Raten).
local RATES = {
  [0] = "4 Hz", "25 Hz", "50 Hz", "100 Hz", "100 Hz Full", "150 Hz", "200 Hz", "250 Hz",
  "333 Hz Full", "500 Hz", "D250", "D500", "F500", "F1000",
}

local HIST = 120            -- LQ-Verlauf: 120 Punkte
local HIST_EVERY = 50       -- alle 0,5 s ein Punkt (10 ms) → 1 Minute
local IMBALANCE = 10        -- ab so vielen dB Unterschied (gemittelt) eine Antenne melden

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

local function num(name)
  local v = getValue(name)
  if type(v) == "number" then return v end
  return 0
end

local function resetSession(w)
  w.minLq, w.minRssi, w.drops, w.low = nil, nil, 0, false
  w.diff = nil
end

local function sample(w)
  local now = getTime()
  w.link = getRSSI() > 0
  if not w.link then
    w.lq = 0
  else
    w.r1, w.r2 = num("1RSS"), num("2RSS")
    w.lq, w.snr = num("RQly"), num("RSNR")
    w.pwr, w.mode, w.ant = num("TPWR"), num("RFMD"), num("ANT")
    w.tlq = num("TQly")
    local best = w.r1
    if w.r2 ~= 0 and (best == 0 or w.r2 > best) then best = w.r2 end
    w.rssi = best
    w.minLq = w.minLq and math.min(w.minLq, w.lq) or w.lq
    if best ~= 0 then w.minRssi = w.minRssi and math.min(w.minRssi, best) or best end
    -- Einbrüche: LQ fällt unter 50 %
    if w.lq < 50 and not w.low then w.drops, w.low = w.drops + 1, true end
    if w.lq >= 60 then w.low = false end
    -- Antennen-Unterschied langsam mitteln (nur wenn beide Werte da sind)
    if w.r1 ~= 0 and w.r2 ~= 0 then
      local d = w.r1 - w.r2
      w.diff = w.diff and (w.diff + 0.02 * (d - w.diff)) or d
    end
  end
  if now - (w.histAt or 0) >= HIST_EVERY then
    w.histAt = now
    w.hist[w.histPos] = w.lq
    w.histPos = w.histPos % HIST + 1
  end
  return now
end

-- Wichtigste Warnung zuerst; nil = alles in Ordnung
local function warning(w)
  if not w.link then return "Kein Link", C.red end
  if w.lq < (w.options.LqWarn or 70) then return "LQ tief: " .. math.floor(w.lq) .. " %", C.red end
  if w.rssi ~= 0 and w.rssi < (w.options.RssiWarn or -95) then return "RSSI nahe Grenze", C.orange end
  if w.diff and math.abs(w.diff) > IMBALANCE then
    local weak = w.diff > 0 and 2 or 1
    return string.format("Antenne %d %d dB schwaecher", weak, math.floor(math.abs(w.diff) + 0.5)), C.orange
  end
  if w.pwr > 0 and w.pwr >= (w.options.MaxPwr or 250) then return "Volle Leistung", C.orange end
  return nil
end

local function rssiFrac(v)
  if v == 0 then return 0 end
  return clamp((v + 120) / 80, 0, 1)
end

local function lqColor(lq)
  if lq >= 90 then return C.green elseif lq >= 70 then return C.orange end
  return C.red
end

local function rateText(mode)
  local r = RATES[math.floor(mode)]
  return r or ("Modus " .. math.floor(mode))
end

local function hbar(x, y, bw, h, f, color)
  lcd.drawFilledRectangle(x, y, bw, h, C.off)
  local fw = math.floor(bw * clamp(f, 0, 1))
  if fw > 0 then lcd.drawFilledRectangle(x, y, fw, h, color) end
end

local function drawHistory(w, x, y, hw, h)
  lcd.drawFilledRectangle(x, y, hw, h, C.off)
  local step = hw / HIST
  for i = 0, HIST - 1 do
    local v = w.hist[(w.histPos - 1 + i) % HIST + 1]
    if v and v > 0 then
      local bh = math.max(1, math.floor(h * v / 100))
      lcd.drawFilledRectangle(x + math.floor(i * step), y + h - bh, math.max(1, math.floor(step)), bh, lqColor(v))
    end
  end
end

local function create(zone, opts)
  initColors()
  local w = { zone = zone, options = opts, hist = {}, histPos = 1, lq = 0, rssi = 0, pwr = 0, link = false }
  resetSession(w)
  return w
end

local function update(w, opts)
  w.options = opts
end

local function background(w)
  sample(w)
end

local function refresh(w, event, touchState)
  sample(w)
  if event ~= nil and EVT_TOUCH_TAP and event == EVT_TOUCH_TAP then resetSession(w) end
  local z = w.zone
  if event ~= nil then z = { x = 0, y = 0, w = LCD_W, h = LCD_H } end
  lcd.drawFilledRectangle(z.x, z.y, z.w, z.h, C.bg)
  local x, y = z.x + 10, z.y + 6
  local warn, warnColor = warning(w)

  if z.w >= 300 and z.h >= 160 then
    lcd.drawText(x, y, "LINK HEALTH", SMLSIZE + BOLD + C.muted)
    if w.link then
      lcd.drawText(z.x + z.w - 10, y, rateText(w.mode) .. "   " .. math.floor(w.pwr) .. " mW", SMLSIZE + RIGHT + C.text)
    end
    -- LQ gross
    lcd.drawText(x, y + 18, w.link and tostring(math.floor(w.lq)) or "--", XXLSIZE + lqColor(w.lq))
    lcd.drawText(x + 112, y + 54, "% LQ", MIDSIZE + C.muted)
    -- Antennen
    local ax = z.x + math.floor(z.w * 0.48)
    local aw = z.x + z.w - 10 - (ax + 44) - 80   -- Platz für "-100 dBm" rechts
    local labels = { { "Ant 1", w.r1 or 0 }, { "Ant 2", w.r2 or 0 } }
    for i = 1, 2 do
      local ly = y + 22 + (i - 1) * 30
      local v = labels[i][2]
      local active = w.link and w.ant == i - 1
      lcd.drawText(ax, ly, labels[i][1], SMLSIZE + (active and BOLD + C.text or C.muted))
      hbar(ax + 44, ly + 2, aw, 12, w.link and rssiFrac(v) or 0, v ~= 0 and v < (w.options.RssiWarn or -95) and C.orange or C.blue)
      lcd.drawText(z.x + z.w - 10, ly, (w.link and v ~= 0) and (math.floor(v) .. " dBm") or "--", SMLSIZE + RIGHT + C.text)
    end
    lcd.drawText(ax, y + 86, w.link and string.format("SNR %d dB   Downlink %d %%", math.floor(w.snr), math.floor(w.tlq)) or "", SMLSIZE + C.muted)
    -- Verlauf
    local hy = z.y + z.h - 90
    lcd.drawText(x, hy - 18, "LQ letzte Minute", SMLSIZE + C.muted)
    drawHistory(w, x, hy, z.w - 20, 40)
    -- Sitzung und Warnung
    local sy = z.y + z.h - 42
    local session = string.format("Min LQ %s   Min RSSI %s   Einbrueche %d",
      w.minLq and tostring(math.floor(w.minLq)) or "--",
      w.minRssi and (math.floor(w.minRssi) .. " dBm") or "--", w.drops)
    lcd.drawText(x, sy, session, SMLSIZE + C.muted)
    lcd.drawText(x, sy + 18, warn or "Link in Ordnung", SMLSIZE + BOLD + (warnColor or C.green))
  elseif z.w >= 160 and z.h >= 80 then
    lcd.drawText(x, y, "LINK", SMLSIZE + BOLD + C.muted)
    lcd.drawText(z.x + z.w - 10, y, w.link and (math.floor(w.pwr) .. " mW") or "", SMLSIZE + RIGHT + C.text)
    lcd.drawText(x, y + 16, w.link and (math.floor(w.lq) .. "%") or "--", DBLSIZE + lqColor(w.lq))
    lcd.drawText(z.x + z.w - 10, y + 22, w.link and (math.floor(w.rssi) .. " dBm") or "", SMLSIZE + RIGHT + C.text)
    lcd.drawText(z.x + z.w - 10, y + 38, w.link and ("SNR " .. math.floor(w.snr)) or "", SMLSIZE + RIGHT + C.muted)
    lcd.drawText(x, z.y + z.h - 20, warn or "OK", SMLSIZE + BOLD + (warnColor or C.green))
  else
    lcd.drawText(z.x + 4, z.y + 2, w.link and ("LQ " .. math.floor(w.lq) .. " " .. math.floor(w.rssi) .. "dBm") or "Kein Link", SMLSIZE + BOLD + lqColor(w.lq))
    hbar(z.x + 4, z.y + z.h - 8, z.w - 8, 5, w.lq / 100, lqColor(w.lq))
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
