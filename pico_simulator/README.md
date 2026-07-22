# Pico Firmware Simulator (MicroPython-Compat)

Dieser Unterordner startet die Firmware in einem geklonten, beschreibbaren Testbereich `data` unter normalem Python und simuliert die benoetigten MicroPython-Module (`machine`, `network`) plus API-Unterschiede (`time.ticks_ms`, `gc.mem_free`, `asyncio.sleep_ms`).

## Was funktioniert

- Firmware aus einem Clone von `source` im Ordner `data` starten.
- Der Webserver der Firmware laeuft wirklich mit der echten Firmware-Logik.
- Wenn die Firmware Port `80` oeffnen will, wird automatisch auf einen Desktop-Port umgeleitet (Standard `8080`).
- `source` bleibt beim Testen unangetastet; Schreiboperationen laufen gegen `data`.

## Schnellstart

Aus dem Projekt-Root:

```powershell
.venv\Scripts\python.exe pico_simulator\run_firmware.py --entry main --port 8080
```

Beim ersten Start wird `data` automatisch aus `source` geklont.

Danach im Browser:

- `http://127.0.0.1:8080/`
- `http://127.0.0.1:8080/admin`

## Weitere Starts

```powershell
.venv\Scripts\python.exe pico_simulator\run_firmware.py --entry boot --port 8080
.venv\Scripts\python.exe pico_simulator\run_firmware.py --entry recovery --port 8080
```

Frischen Clone erzwingen:

```powershell
.venv\Scripts\python.exe pico_simulator\run_firmware.py --entry main --port 8080 --refresh-data
```

## Hinweise

- `machine.reset()` wird im Simulator nur geloggt und nicht wirklich ausgefuehrt.
- UART-Telemetrie ist als leerer Eingang simuliert (keine echten CRSF-Daten).
- Dieser Simulator ist fuer Firmware- und Web-Tests gedacht, nicht als physikalischer Flugcontroller-Simulator.
