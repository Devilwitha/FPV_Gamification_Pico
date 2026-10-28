-- tilt.lua – Neige-Aktionen mit dem Gyro der TX15
-- Bollisoft Lua-Bibliothek · EdgeTX 2.10+ · Sender mit eingebautem Gyro (TX15: ICM-42607-C)
-- Datei: /SCRIPTS/MIXES/tilt.lua
--
-- Wertet die Neigung des Senders aus und liefert zwei neue Quellen für den Mischer:
--   Cam  – geglättete Neigung (−100 … +100 %), z. B. als Kanal für den Kamerawinkel im
--          FPV-Simulator oder für einen Kamera-Servo.
--   Kipp – +100 %, solange der Sender über die Schwelle gekippt ist, sonst −100 %.
--          Damit lassen sich logische Schalter und Spezialfunktionen auslösen.
-- Mit Ansage = 1 sagt der Sender beim Nach-vorne-Kippen Akkuspannung (RxBt) und LQ an.
--
-- Einrichten: MDL → Eigene Skripte → freien Platz wählen → tilt. Bei "Neig" die
-- Neigungsachse des Sender-Gyros als Quelle wählen (in EdgeTX unter den Quellen des
-- Gyros, je nach Version z. B. "TltX"/"TltY"). Danach steht "LUA1 Cam" bzw.
-- "LUA1 Kipp" im Mischer als Quelle zur Verfügung.

local input = {
  { "Neig", SOURCE },                -- Neigungsachse des Gyros
  { "Grenze", VALUE, 10, 100, 45 },  -- ab so viel % Neigung gilt der Sender als gekippt
  { "Glatt", VALUE, 0, 95, 70 },     -- Glättung der Cam-Ausgabe in %
  { "Ansage", VALUE, 0, 1, 1 },      -- 1 = beim Kippen Akku und LQ ansagen
  { "Umkehr", VALUE, 0, 1, 0 },      -- 1 = Kipprichtung umdrehen
}

local output = { "Cam", "Kipp" }

local HOLD = 30        -- so lange (10 ms) gekippt halten, bevor es auslöst: 0,3 s
local PAUSE = 300      -- frühestens nach 3 s wieder ansagen

local cam, tilted, since, lastSay = 0, false, nil, -PAUSE

local function say()
  local v = getValue("RxBt")
  if type(v) == "number" and v > 0 then playNumber(math.floor(v * 10 + 0.5), UNIT_VOLTS, PREC1) end
  local lq = getValue("RQly")
  if type(lq) == "number" and lq > 0 then playNumber(math.floor(lq), UNIT_PERCENT, 0) end
end

local function init()
  cam, tilted, since = 0, false, nil
end

local function run(neig, grenze, glatt, ansage, umkehr)
  if umkehr == 1 then neig = -neig end
  local a = glatt / 100
  cam = cam * a + neig * (1 - a)

  local now = getTime()
  local limit = 1024 * grenze / 100
  if not tilted then
    if neig > limit then
      since = since or now
      if now - since >= HOLD then
        tilted = true
        if ansage == 1 and now - lastSay >= PAUSE then
          lastSay = now
          say()
        end
      end
    else
      since = nil
    end
  elseif neig < limit * 0.7 then     -- Hysterese: erst deutlich zurückkippen
    tilted, since = false, nil
  end

  return math.floor(cam), tilted and 1024 or -1024
end

return { input = input, output = output, init = init, run = run }
