-- GPS QR – letzte bekannte Position einfrieren und als QR-Code für Google/Apple Maps zeigen
-- Bollisoft Lua-Bibliothek · EdgeTX 2.10+ · RadioMaster TX15 und andere Farbdisplays
-- Datei: /WIDGETS/GPSQR/main.lua
--
-- Merkt sich laufend die letzte gültige GPS-Position (GPS, Alt, GSpd, Sats). Reisst der
-- Link ab oder ist der Akku leer, friert die Anzeige diese Position ein, vibriert kurz und
-- speichert sie auf der SD-Karte – sie übersteht also auch einen Neustart des Senders.
-- Handy-Kamera auf den QR-Code halten: die Position öffnet sich direkt in der Karten-App.
-- Im kleinen Feld steht die Position als Text; der QR-Code braucht ein grosses Feld
-- oder das Vollbild.

local name = "GPS QR"

local options = {
  { "AppleMaps", BOOL, 0 },   -- 0 = Google Maps (Android und iPhone), 1 = Apple Maps
}

local SAVE_FILE = "/WIDGETS/GPSQR/last.txt"
local REBUILD = 300          -- QR-Code höchstens alle 3 s neu bauen (Einheit 10 ms)
local MOVE = 0.00005         -- erst ab ~5 m Bewegung neu bauen bzw. speichern

-- ---------- QR-Code: Byte-Modus, Fehlerkorrektur L, Version 1–5 (ein Block) ----------
-- Gebaut wird in kleinen Schritten (Jobs), damit kein Aufruf das CPU-Limit von EdgeTX reisst.

local QR_DATA = { 19, 34, 55, 80, 108 }   -- Datenbytes je Version
local QR_ECC = { 7, 10, 15, 20, 26 }      -- Fehlerkorrekturbytes je Version

local EXP, LOG = {}, {}
do
  local x = 1
  for i = 0, 254 do
    EXP[i] = x
    LOG[x] = i
    x = x * 2
    if x >= 256 then x = bit32.bxor(x, 0x11D) end
  end
end

local function gfMul(a, b)
  if a == 0 or b == 0 then return 0 end
  return EXP[(LOG[a] + LOG[b]) % 255]
end

local MASKS = {
  [0] = function(x, y) return (x + y) % 2 == 0 end,
  function(x, y) return y % 2 == 0 end,
  function(x, y) return x % 3 == 0 end,
  function(x, y) return (x + y) % 3 == 0 end,
  function(x, y) return (math.floor(x / 3) + math.floor(y / 2)) % 2 == 0 end,
  function(x, y) return (x * y) % 2 + (x * y) % 3 == 0 end,
  function(x, y) return ((x * y) % 2 + (x * y) % 3) % 2 == 0 end,
  function(x, y) return ((x + y) % 2 + (x * y) % 3) % 2 == 0 end,
}

-- Neuer Bauauftrag; nil, wenn der Text nicht in Version 5 passt
local function qrNew(text, forceMask)
  local len = #text
  local ver
  for v = 1, 5 do
    if len <= QR_DATA[v] - 2 then ver = v break end
  end
  if not ver then return nil end
  local q = {
    text = text, ver = ver, size = 17 + 4 * ver, nData = QR_DATA[ver], nEcc = QR_ECC[ver],
    mod = {}, fn = {}, cur = {}, forceMask = forceMask, phase = "encode", i = 0,
    mask = 0, best = nil, bestMask = 0, pen = 0, done = false,
  }
  return q
end

local function setFn(q, x, y, dark)
  local k = y * q.size + x
  q.mod[k] = dark
  q.fn[k] = true
end

local function formatBits(mask)
  local data = 8 + mask                  -- Fehlerkorrektur L = 01, dann 3 Bit Maske
  local rem = data
  for _ = 1, 10 do
    rem = bit32.bxor(rem * 2, math.floor(rem / 512) * 0x537)
  end
  return bit32.bxor(data * 1024 + rem, 0x5412)
end

local function drawFormat(q, mask)
  local bits = formatBits(mask)
  local s = q.size
  local function bit(i) return bit32.band(bit32.rshift(bits, i), 1) == 1 end
  for i = 0, 5 do setFn(q, 8, i, bit(i)) end
  setFn(q, 8, 7, bit(6))
  setFn(q, 8, 8, bit(7))
  setFn(q, 7, 8, bit(8))
  for i = 9, 14 do setFn(q, 14 - i, 8, bit(i)) end
  for i = 0, 7 do setFn(q, s - 1 - i, 8, bit(i)) end
  for i = 8, 14 do setFn(q, 8, s - 15 + i, bit(i)) end
  setFn(q, 8, s - 8, true)               -- immer dunkles Modul
end

local function finder(q, cx, cy)
  for dy = -4, 4 do
    for dx = -4, 4 do
      local x, y = cx + dx, cy + dy
      if x >= 0 and x < q.size and y >= 0 and y < q.size then
        local d = math.max(math.abs(dx), math.abs(dy))
        setFn(q, x, y, d ~= 2 and d ~= 4)
      end
    end
  end
end

-- Strafpunkte einer Zeile (row = true) oder Spalte des aktuell maskierten Musters
local function linePenalty(q, idx, row)
  local s, cur = q.size, q.cur
  local base, step = idx * s, 1
  if not row then base, step = idx, s end
  local p, run, last = 0, 0, nil
  for i = 0, s - 1 do
    local c = cur[base + i * step]
    if c == last then
      run = run + 1
      if run == 5 then p = p + 3 elseif run > 5 then p = p + 1 end
    else
      last, run = c, 1
    end
  end
  -- Finder-ähnliches Muster 1011101 mit 4 hellen Modulen davor oder danach
  for i = 0, s - 7 do
    local k = base + i * step
    if cur[k] and not cur[k + step] and cur[k + 2 * step] and cur[k + 3 * step]
      and cur[k + 4 * step] and not cur[k + 5 * step] and cur[k + 6 * step] then
      local before, after = true, true
      for j = 1, 4 do
        if i - j >= 0 and cur[k - j * step] then before = false end
        if i + 6 + j < s and cur[k + (6 + j) * step] then after = false end
      end
      if before or after then p = p + 40 end
    end
  end
  return p
end

-- Einen Arbeitsschritt erledigen; true, wenn der Code fertig ist
local function qrStep(q)
  local s = q.size
  if q.phase == "encode" then
    -- Modus 0100, Länge (8 Bit), Zeichen, Endzeichen 0000: um ein Halbbyte versetzt
    local len, text = #q.text, q.text
    local data = { 64 + math.floor(len / 16) }
    local carry = len % 16
    for i = 1, len do
      local c = text:byte(i)
      data[i + 1] = carry * 16 + math.floor(c / 16)
      carry = c % 16
    end
    data[len + 2] = carry * 16
    local pad = 0
    for i = len + 3, q.nData do
      data[i] = pad % 2 == 0 and 0xEC or 0x11
      pad = pad + 1
    end
    q.data = data
    q.gen, q.phase, q.i = { 1 }, "gen", 0
  elseif q.phase == "gen" then
    -- Generatorpolynom der Fehlerkorrektur, vier Faktoren pro Schritt
    local g = q.gen
    for i = q.i, math.min(q.i + 3, q.nEcc - 1) do
      local ng = { g[1] }
      for j = 2, #g do ng[j] = bit32.bxor(g[j], gfMul(g[j - 1], EXP[i])) end
      ng[#g + 1] = gfMul(g[#g], EXP[i])
      g = ng
    end
    q.gen, q.i = g, q.i + 4
    if q.i >= q.nEcc then
      q.rem = {}
      for j = 1, q.nEcc do q.rem[j] = 0 end
      q.phase, q.i = "ecc", 1
    end
  elseif q.phase == "ecc" then
    local rem, n = q.rem, q.nEcc
    for i = q.i, math.min(q.i + 3, q.nData) do
      local f = bit32.bxor(q.data[i], rem[1])
      for j = 1, n - 1 do rem[j] = rem[j + 1] end
      rem[n] = 0
      if f ~= 0 then
        for j = 1, n do rem[j] = bit32.bxor(rem[j], gfMul(q.gen[j + 1], f)) end
      end
    end
    q.i = q.i + 4
    if q.i > q.nData then
      for j = 1, n do q.data[#q.data + 1] = rem[j] end
      q.phase, q.i = "clear", 0
    end
  elseif q.phase == "clear" then
    for k = q.i * s, math.min((q.i + 8) * s, s * s) - 1 do
      q.mod[k] = false
      q.fn[k] = false
    end
    q.i = q.i + 8
    if q.i >= s then q.phase, q.i = "pattern", 0 end
  elseif q.phase == "pattern" then
    -- Taktlinien, drei Finder, Ausrichtungsmuster und Formatfelder: je ein Teil pro Schritt
    local part = q.i
    if part == 0 then
      for i = 0, s - 1 do
        setFn(q, 6, i, i % 2 == 0)
        setFn(q, i, 6, i % 2 == 0)
      end
    elseif part == 1 then
      finder(q, 3, 3)
    elseif part == 2 then
      finder(q, s - 4, 3)
    elseif part == 3 then
      finder(q, 3, s - 4)
    else
      if q.ver > 1 then
        local c = s - 7
        for dy = -2, 2 do
          for dx = -2, 2 do
            setFn(q, c + dx, c + dy, math.max(math.abs(dx), math.abs(dy)) ~= 1)
          end
        end
      end
      drawFormat(q, 0)                   -- reserviert die Formatfelder
      q.phase, q.right, q.bit = "place", s - 1, 0
    end
    q.i = part + 1
  elseif q.phase == "place" then
    -- Datenbits im Zickzack von unten rechts, je eine Doppelspalte pro Schritt
    local right = q.right
    if right == 6 then right = 5 end
    local total = #q.data * 8
    local upward = (right + 1) % 4 < 2
    for vert = 0, s - 1 do
      local y = upward and (s - 1 - vert) or vert
      for j = 0, 1 do
        local x = right - j
        local k = y * s + x
        if not q.fn[k] and q.bit < total then
          local byte = q.data[math.floor(q.bit / 8) + 1]
          q.mod[k] = bit32.band(bit32.rshift(byte, 7 - q.bit % 8), 1) == 1
          q.bit = q.bit + 1
        end
      end
    end
    q.right = right - 2
    if q.right < 1 then
      q.phase, q.mask = "mask", q.forceMask or 0
      q.i, q.pen, q.dark = 0, 0, 0
    end
  elseif q.phase == "mask" then
    -- Maske auf eine Kopie anwenden, 3 Zeilen pro Schritt
    local m = MASKS[q.mask]
    for y = q.i, math.min(q.i + 2, s - 1) do
      for x = 0, s - 1 do
        local k = y * s + x
        local v = q.mod[k]
        if not q.fn[k] and m(x, y) then v = not v end
        q.cur[k] = v
        if v then q.dark = q.dark + 1 end
      end
    end
    q.i = q.i + 3
    if q.i >= s then
      if q.forceMask then
        q.bestMask, q.phase, q.i = q.forceMask, "apply", 0
      else
        q.phase, q.i, q.col = "penalty", 0, false
      end
    end
  elseif q.phase == "penalty" then
    -- Strafpunkte nach ISO 18004: Läufe, 2x2-Blöcke, Finder-Muster (abwechselnd Zeile und Spalte)
    local i, cur = q.i, q.cur
    if q.col then
      q.pen = q.pen + linePenalty(q, i, false)
      q.col, q.i = false, i + 1
    else
      q.pen = q.pen + linePenalty(q, i, true)
      if i < s - 1 then
        for k = i * s, i * s + s - 2 do
          local c = cur[k]
          if cur[k + 1] == c and cur[k + s] == c and cur[k + s + 1] == c then q.pen = q.pen + 3 end
        end
      end
      q.col = true
    end
    if q.i >= s then
      local pct = q.dark * 100 / (s * s)
      q.pen = q.pen + 10 * math.floor(math.abs(pct - 50) / 5)
      if not q.best or q.pen < q.best then q.best, q.bestMask = q.pen, q.mask end
      if q.mask < 7 then
        q.mask, q.phase, q.i, q.pen, q.dark, q.col = q.mask + 1, "mask", 0, 0, 0, false
      else
        q.phase, q.i = "apply", 0
      end
    end
  elseif q.phase == "apply" then
    local m = MASKS[q.bestMask]
    for y = q.i, math.min(q.i + 2, s - 1) do
      for x = 0, s - 1 do
        local k = y * s + x
        if not q.fn[k] and m(x, y) then q.mod[k] = not q.mod[k] end
      end
    end
    q.i = q.i + 3
    if q.i >= s then
      drawFormat(q, q.bestMask)
      q.phase, q.i = "runs", 0
      q.rx, q.ry, q.rn = {}, {}, {}
    end
  elseif q.phase == "runs" then
    -- Dunkle Module je Zeile zu Rechtecken zusammenfassen: schneller zu zeichnen
    for y = q.i, math.min(q.i + 2, s - 1) do
      local x = 0
      while x < s do
        if q.mod[y * s + x] then
          local x0 = x
          while x < s and q.mod[y * s + x] do x = x + 1 end
          local n = #q.rx + 1
          q.rx[n], q.ry[n], q.rn[n] = x0, y, x - x0
        else
          x = x + 1
        end
      end
    end
    q.i = q.i + 3
    if q.i >= s then
      q.done = true
      q.cur, q.fn, q.gen, q.rem = nil, nil, nil, nil
    end
  end
  return q.done
end

-- Modulgrösse für eine Fläche: Code + 4 Module Ruhezone ringsum
local function qrModule(q, area)
  return math.floor(area.side / (q.size + 8))
end

-- Pixel-Rechtecke für Fläche und Modulgrösse vorbereiten, 80 Läufe pro Schritt.
-- true, wenn sie bereitstehen.
local function qrLayout(q, area)
  local ms = qrModule(q, area)
  if q.cx == area.x and q.cy == area.y and q.cms == ms then return true end
  local L = q.lay
  if not L or L.x ~= area.x or L.y ~= area.y or L.ms ~= ms then
    L = { x = area.x, y = area.y, ms = ms, i = 1, px = {}, py = {}, pw = {} }
    q.lay = L
  end
  local ox, oy = L.x + 4 * ms, L.y + 4 * ms
  local rx, ry, rn, px, py, pw = q.rx, q.ry, q.rn, L.px, L.py, L.pw
  local last = math.min(L.i + 79, #rx)
  for i = L.i, last do
    px[i], py[i], pw[i] = ox + rx[i] * ms, oy + ry[i] * ms, rn[i] * ms
  end
  L.i = last + 1
  if L.i <= #rx then return false end
  q.px, q.py, q.pw, q.cx, q.cy, q.cms, q.lay = px, py, pw, L.x, L.y, ms, nil
  return true
end

-- Zeichnet den QR-Code, wenn er für diese Fläche vorbereitet ist; liefert die Breite oder 0
local function qrDraw(q, area, bg, fg)
  local ms = qrModule(q, area)
  if ms < 1 or q.cx ~= area.x or q.cy ~= area.y or q.cms ~= ms then return 0 end
  local total = (q.size + 8) * ms
  lcd.drawFilledRectangle(area.x, area.y, total, total, bg)
  local fill, px, py, pw = lcd.drawFilledRectangle, q.px, q.py, q.pw
  for i = 1, #px do fill(px[i], py[i], pw[i], ms, fg) end
  return total
end

-- ---------- Position merken ----------

local C = {}

local function initColors()
  C.bg = lcd.RGB(18, 22, 27)
  C.text = lcd.RGB(240, 244, 247)
  C.muted = lcd.RGB(140, 152, 163)
  C.white = lcd.RGB(255, 255, 255)
  C.black = lcd.RGB(0, 0, 0)
  C.green = lcd.RGB(64, 200, 110)
  C.orange = lcd.RGB(245, 150, 40)
  C.red = lcd.RGB(232, 72, 56)
end

local function stamp()
  local d = getDateTime()
  return string.format("%02d.%02d. %02d:%02d", d.day, d.mon, d.hour, d.min)
end

local function savePos(w)
  local f = io.open(SAVE_FILE, "w")
  if not f then return end
  io.write(f, string.format("%.7f;%.7f;%d;%d;%s\n", w.lat, w.lon, math.floor(w.alt or 0), math.floor(w.sats or 0), w.when or ""))
  io.close(f)
end

local function loadPos(w)
  local f = io.open(SAVE_FILE, "r")
  if not f then return end
  local s = io.read(f, 120) or ""
  io.close(f)
  local lat, lon, alt, sats, when = string.match(s, "^([%-%d%.]+);([%-%d%.]+);([%-%d]+);(%d+);([^\n]*)")
  if lat then
    w.lat, w.lon = tonumber(lat), tonumber(lon)
    w.alt, w.sats, w.when = tonumber(alt), tonumber(sats), when
    w.saved = true
  end
end

local function mapUrl(w)
  if w.options.AppleMaps == 1 then
    return string.format("https://maps.apple.com/?ll=%.6f,%.6f&q=Quad", w.lat, w.lon)
  end
  return string.format("https://maps.google.com/?q=%.6f,%.6f", w.lat, w.lon)
end

local function sample(w)
  local now = getTime()
  local link = getRSSI() > 0
  local g = link and getValue("GPS") or nil
  local fix = type(g) == "table" and g.lat and g.lon and (g.lat ~= 0 or g.lon ~= 0)
  if fix then
    local moved = not w.lat or math.abs(g.lat - w.lat) > MOVE or math.abs(g.lon - w.lon) > MOVE
    w.lat, w.lon = g.lat, g.lon
    local alt, spd, sats = getValue("Alt"), getValue("GSpd"), getValue("Sats")
    if type(alt) == "number" then w.alt = alt end
    if type(spd) == "number" then w.spd = spd end
    if type(sats) == "number" then w.sats = sats end
    w.live, w.lostAt, w.saved = true, nil, false
    if moved and now - (w.savedAt or -1000) > REBUILD then
      w.when = stamp()
      savePos(w)
      w.savedAt = now
    end
  elseif w.live then
    -- Link oder GPS weg: Position einfrieren, sichern und kurz melden
    w.live, w.lostAt = false, now
    w.when = stamp()
    savePos(w)
    w.job, w.builtAt = nil, nil          -- QR sofort für die eingefrorene Position bauen
    playHaptic(60, 40)
    playTone(880, 150, 60)
    playTone(660, 250, 0)
  end
  -- QR-Code neu bauen, wenn sich die Position (oder die Karten-App) geändert hat
  if w.lat then
    local url = mapUrl(w)
    if url ~= w.url and not w.job and now - (w.builtAt or -1000) > REBUILD then
      w.url, w.job, w.builtAt = url, qrNew(url), now
    end
  end
  return now
end

-- Ein Stück Arbeit am QR-Code; false, wenn nichts mehr zu tun ist
local function workUnit(w)
  local job, area = w.job, w.area
  if job and not job.done then
    qrStep(job)
    return true
  end
  if job then
    -- fertig gebaut: erst vorbereiten, dann ersetzt er den bisher angezeigten
    if not area or qrLayout(job, area) then w.qr, w.job = job, nil end
    return true
  end
  if w.qr and area and not (w.qr.cx == area.x and w.qr.cy == area.y and w.qr.cms == qrModule(w.qr, area)) then
    qrLayout(w.qr, area)
    return true
  end
  return false
end

-- Arbeitet, solange das CPU-Budget dieses Aufrufs reicht. getUsage() = verbrauchter
-- Anteil des CPU-Limits in %; ein Stück braucht höchstens etwa 42 %.
local function work(w)
  if not getUsage then
    workUnit(w)
    return
  end
  for _ = 1, 60 do
    if getUsage() >= 40 or not workUnit(w) then return end
  end
end

local function fmtCoord(v, pos, neg)
  return string.format("%.6f %s", math.abs(v), v >= 0 and pos or neg)
end

local function fmtAge(ticks)
  local s = math.floor(ticks / 100)
  return string.format("%02d:%02d", math.floor(s / 60), s % 60)
end

local function create(zone, opts)
  initColors()
  local w = { zone = zone, options = opts, live = false }
  loadPos(w)
  return w
end

local function update(w, opts)
  w.options = opts
  w.builtAt = nil
end

local function background(w)
  sample(w)
  work(w)
end

local function drawInfo(w, x, y, now, big)
  local f = big and 0 or SMLSIZE
  local line = big and 22 or 16
  if w.live then
    lcd.drawText(x, y, "LIVE", f + BOLD + C.green)
  elseif w.lat then
    lcd.drawText(x, y, "LETZTE POSITION", f + BOLD + C.orange)
  else
    lcd.drawText(x, y, "KEIN GPS", f + BOLD + C.red)
  end
  y = y + line + 4
  if not w.lat then
    lcd.drawText(x, y, "Warte auf GPS-Fix", SMLSIZE + C.muted)
    return
  end
  lcd.drawText(x, y, fmtCoord(w.lat, "N", "S"), f + C.text)
  lcd.drawText(x, y + line, fmtCoord(w.lon, "E", "W"), f + C.text)
  y = y + 2 * line + 6
  lcd.drawText(x, y, string.format("Hoehe %d m  Sats %d", math.floor(w.alt or 0), math.floor(w.sats or 0)), SMLSIZE + C.muted)
  y = y + 18
  if w.live then
    lcd.drawText(x, y, string.format("Tempo %d km/h", math.floor(w.spd or 0)), SMLSIZE + C.muted)
  elseif w.lostAt then
    lcd.drawText(x, y, "Signal weg seit " .. fmtAge(now - w.lostAt), SMLSIZE + C.orange)
  elseif w.when and w.when ~= "" then
    lcd.drawText(x, y, "Gespeichert " .. w.when, SMLSIZE + C.orange)
  end
end

local function refresh(w, event, touchState)
  local now = sample(w)
  local z = w.zone
  if event ~= nil then z = { x = 0, y = 0, w = LCD_W, h = LCD_H } end
  lcd.drawFilledRectangle(z.x, z.y, z.w, z.h, C.bg)

  local qrSide = math.min(z.h, math.floor(z.w * 0.55))
  if z.w >= 300 and z.h >= 150 and qrSide >= 120 then
    local qx, qy = z.x + 6, z.y + math.floor((z.h - qrSide) / 2)
    if not w.area or w.area.x ~= qx or w.area.y ~= qy or w.area.side ~= qrSide then
      w.area = { x = qx, y = qy, side = qrSide }
    end
    local drawn = 0
    -- Nach einem Linkverlust keinen QR-Code einer älteren Position zeigen
    local current = w.qr and (w.live or w.qr.text == mapUrl(w))
    if current then drawn = qrDraw(w.qr, w.area, C.white, C.black) end
    if drawn == 0 then
      lcd.drawFilledRectangle(qx, qy, qrSide, qrSide, C.white)
      lcd.drawText(qx + math.floor(qrSide / 2), qy + math.floor(qrSide / 2) - 8, w.lat and "QR wird erstellt" or "Kein GPS", SMLSIZE + CENTER + C.black)
      drawn = qrSide
    end
    local tx = qx + drawn + 14
    drawInfo(w, tx, z.y + 12, now, z.w - tx >= 190)
    lcd.drawText(tx, z.y + z.h - 22, "Scannen = Karte", SMLSIZE + C.muted)
  else
    drawInfo(w, z.x + 6, z.y + 4, now, false)
  end
  work(w)    -- erst zeichnen, dann mit dem restlichen CPU-Budget weiterbauen
end

return {
  name = name,
  options = options,
  create = create,
  update = update,
  background = background,
  refresh = refresh,
}
