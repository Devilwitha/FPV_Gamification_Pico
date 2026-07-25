# Pico Firmware Simulator (MicroPython-Compat)

Dieser Unterordner startet die Firmware in einem geklonten, beschreibbaren Testbereich `data` unter normalem Python und simuliert die benoetigten MicroPython-Module (`machine`, `network`) plus API-Unterschiede (`time.ticks_ms`, `gc.mem_free`, `asyncio.sleep_ms`).

## Was funktioniert

- Firmware aus einem Clone von `source` im Ordner `data` starten.
- Der Webserver der Firmware laeuft wirklich mit der echten Firmware-Logik.
- Wenn die Firmware Port `80` oeffnen will, wird automatisch auf einen Desktop-Port umgeleitet (Standard `8080`).
- `source` bleibt beim Testen unangetastet; Schreiboperationen laufen gegen `data`.

## Hardware-Naehe (RAM/CPU)

Der Simulator hat jetzt Hardware-Profile, damit das Verhalten naeher am Pico liegt.

- Standardprofil: `pico_w`
	- `mem_free`: ca. 100 KB
	- `mem_alloc`: ca. 80 KB
	- `machine.freq()`: 125 MHz
	- leichte Netzlatenz: 2 ms

- Standardprofil: `pico2`
	- `mem_free`: ca. 180 KB
	- `mem_alloc`: ca. 96 KB
	- `machine.freq()`: 150 MHz
	- leichte Netzlatenz: 1 ms

Du kannst jederzeit fein nachstellen:

- `--sim-profile pico_w|pico2`
- `--mem-free-kb <n>`
- `--mem-alloc-kb <n>`
- `--cpu-freq-mhz <n>`
- `--cpu-scale <f>` (>
	1.0 langsamer, < 1.0 schneller)
- `--net-latency-ms <n>`
- `--real-ram-limit-mb <n>` (harte OS-RAM-Grenze fuer den Simulator-Prozess)
	- optional: kann leer bleiben, wenn nur die simulierten `gc`-Werte getestet werden sollen

Profile werden dauerhaft in `pico_simulator/sim_profiles.json` gespeichert.

## GUI Launcher

Du kannst den Simulator inkl. Profilverwaltung per GUI starten:

```powershell
.venv\Scripts\python.exe pico_simulator\run_fmr_gui.py
```

In der GUI kannst du:

- Profile laden
- Profile bearbeiten und speichern
- Neue Profile anlegen
- Profile loeschen (Basisprofil `pico_w` bleibt geschuetzt)
- Danach direkt `run_fmr` starten/stoppen

Die GUI hat dafuer das Feld `Real RAM limit (MB)`.

## Schnellstart

Aus dem Projekt-Root:

```powershell
.venv\Scripts\python.exe pico_simulator\run_firmware.py --port 8080
```

Standardmaessig startet der Simulator wie der echte Pico ueber `boot.py`.
Fehlt `main.py` im beschreibbaren `data`-Abbild oder stuerzt es beim Import
ab, startet `boot.py` automatisch `recovery.py`. Beim ersten Start wird
`data` automatisch aus `source` geklont.

Danach im Browser:

- `http://127.0.0.1:8080/`
- `http://127.0.0.1:8080/admin`

## Weitere Starts

```powershell
.venv\Scripts\python.exe pico_simulator\run_firmware.py --entry main --port 8080
.venv\Scripts\python.exe pico_simulator\run_firmware.py --entry recovery --port 8080
```

Frischen Clone erzwingen:

```powershell
.venv\Scripts\python.exe pico_simulator\run_firmware.py --port 8080 --refresh-data
```

Pico-W-nah mit frischem Clone:

```powershell
.venv\Scripts\python.exe pico_simulator\run_firmware.py --port 8080 --refresh-data --sim-profile pico_w
```

Strenger Performance-Test (noch langsamer):

```powershell
.venv\Scripts\python.exe pico_simulator\run_firmware.py --entry main --port 8080 --sim-profile pico_w --cpu-scale 1.3 --net-latency-ms 5
```

## Hinweise

- `machine.reset()` wird im Simulator nur geloggt und nicht wirklich ausgefuehrt.
- UART-Telemetrie ist als leerer Eingang simuliert (keine echten CRSF-Daten).
- Dieser Simulator ist fuer Firmware- und Web-Tests gedacht, nicht als physikalischer Flugcontroller-Simulator.
- CPU-/RAM-Naehe ist eine best effort Emulation, kein zyklusgenauer MCU-Simulator.
