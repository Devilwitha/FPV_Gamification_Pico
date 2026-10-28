-- tlmlog.lua – Telemetrie-Logger: schreibt während des Flugs eine kleine CSV-Datei
-- Bollisoft Lua-Bibliothek · EdgeTX 2.10+ · RadioMaster TX15 und andere Sender
-- Datei: /SCRIPTS/FUNCTIONS/tlmlog.lua
--
-- Solange der Schalter der Spezialfunktion aktiv ist (z. B. der Arm-Schalter), schreibt das
-- Skript 5 Zeilen pro Sekunde nach /LOGS/TLM_<Datum>_<Zeit>.csv: Akku, Strom, Verbrauch,
-- Link (RSSI, LQ, SNR, Leistung), Höhe, Tempo, GPS-Position, Lage und Gas.
-- Später den Sender per USB verbinden und die Datei am PC öffnen – ohne Blackbox-Kabel.
-- Die Spalten Lat/Lon lassen sich direkt in Kartendienste laden, z. B. als Heatmap.
-- Fehlt ein Sensor, bleibt seine Spalte leer. Pro Schalterzyklus entsteht eine neue Datei.
--
-- Einrichten: MDL → Spezialfunktionen → Schalter wählen → Aktion "Lua-Skript" → tlmlog.

local INTERVAL = 20    -- alle 20 × 10 ms = 0,2 s eine Zeile
local FLUSH = 500      -- alle 5 s Datei schliessen und wieder öffnen: Daten sicher auf der Karte
local RESOLVE = 200    -- fehlende Sensoren alle 2 s erneut suchen
local DIR = "/LOGS/"

local COLS = {
  "RxBt", "Curr", "Capa", "Bat%", "1RSS", "2RSS", "RQly", "RSNR", "TPWR",
  "Alt", "VSpd", "GSpd", "Hdg", "Sats", "GPS", "Ptch", "Roll", "Yaw",
}

local file, path, startT, lastLine, lastFlush, lastResolve
local failed = false   -- Datei liess sich nicht anlegen: bis zum nächsten Schalterzyklus ruhen
local ids = {}

local function resolve()
  for i = 1, #COLS do
    if not ids[i] then
      local info = getFieldInfo(COLS[i])
      if info then ids[i] = info.id end
    end
  end
end

local function header()
  local h = { "Zeit_s" }
  for i = 1, #COLS do
    if COLS[i] == "GPS" then
      h[#h + 1] = "Lat"
      h[#h + 1] = "Lon"
    else
      h[#h + 1] = COLS[i]
    end
  end
  h[#h + 1] = "Gas_pct"
  return table.concat(h, ",") .. "\n"
end

local function fmt(v)
  if v == math.floor(v) then return string.format("%d", v) end
  return string.format("%.2f", v)
end

local function line(now)
  local out = { string.format("%.2f", (now - startT) / 100) }
  for i = 1, #COLS do
    local v = ids[i] and getValue(ids[i]) or nil
    if COLS[i] == "GPS" then
      if type(v) == "table" and v.lat and (v.lat ~= 0 or v.lon ~= 0) then
        out[#out + 1] = string.format("%.6f", v.lat)
        out[#out + 1] = string.format("%.6f", v.lon)
      else
        out[#out + 1] = ""
        out[#out + 1] = ""
      end
    elseif type(v) == "number" then
      out[#out + 1] = fmt(v)
    else
      out[#out + 1] = ""
    end
  end
  local thr = getValue("thr")
  out[#out + 1] = type(thr) == "number" and string.format("%d", math.floor((thr + 1024) / 20.48 + 0.5)) or ""
  return table.concat(out, ",") .. "\n"
end

local function open(now)
  local d = getDateTime()
  path = string.format("%sTLM_%04d-%02d-%02d_%02d%02d%02d.csv", DIR, d.year, d.mon, d.day, d.hour, d.min, d.sec)
  file = io.open(path, "w")
  if not file then return end
  io.write(file, header())
  startT, lastLine, lastFlush, lastResolve = now, now - INTERVAL, now, now
  playTone(1500, 60, 0)
end

local function close()
  if file then
    io.close(file)
    file = nil
    playTone(900, 60, 0)
  end
end

local function init()
  ids = {}
  resolve()
end

local function run()
  local now = getTime()
  if not file then
    if failed then return end
    open(now)
    if not file then
      failed = true
      return
    end
  end
  if now - lastResolve >= RESOLVE then
    lastResolve = now
    resolve()
  end
  if now - lastLine >= INTERVAL then
    lastLine = now
    io.write(file, line(now))
  end
  if now - lastFlush >= FLUSH then
    lastFlush = now
    io.close(file)
    file = io.open(path, "a")
  end
end

-- Schalter aus: Datei schliessen; beim nächsten Einschalten beginnt eine neue
local function background()
  close()
  failed = false
end

return { init = init, run = run, background = background }
