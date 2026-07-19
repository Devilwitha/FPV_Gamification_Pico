# FPV Gamification Pico

FPV Score-Tracker auf einem Raspberry Pi Pico/Pico W mit passivem CRSF-Readout.

Das Skript liest Attitude-Daten (Roll/Pitch/Yaw), erkennt Tricks, vergibt Punkte und zeigt alles live im Browser an. Zusätzlich gibt es Session- und Debug-TXT-Downloads direkt aus dem Webinterface.

## Features

- Passives CRSF-UART Parsing (Attitude Frames)
- Trick-Erkennung mit Punktesystem
- Highscore mit Zeitstempel und Pilot
- WLAN Access Point direkt auf dem Pico
- Web-UI mit Live-Score, Trick-Historie und Downloads
- LED-Statusanzeige auf dem Pico

## Hardware

- Raspberry Pi Pico oder Pico W (MicroPython)
- Flight Controller oder ELRS-Quelle mit CRSF TX
- 3 Kabel mindestens:
	- GND
	- TX (von FC/Empfaenger) -> RX am Pico
	- Versorgung (falls nicht per USB)

## Verdrahtung

Aktuelle UART-Konfiguration im Code:

- UART0 RX: GP1 (wichtig, hier kommen die CRSF-Daten rein)
- UART0 TX: GP0 (derzeit nicht aktiv genutzt)

Empfohlene Anschluesse:

1. GND vom Flight Controller mit GND vom Pico verbinden.
2. CRSF TX vom Flight Controller/Empfaenger mit GP1 am Pico verbinden.
3. Pico versorgen:
	 - Entweder per USB, oder
	 - per FC 5V an VSYS am Pico.

Wichtig:

- Niemals 5V auf den 3V3-Pin geben.
- Gemeinsame Masse (GND) ist Pflicht, sonst kommen keine stabilen Daten an.

## Installation und Setup

### Erste Installation auf dem Pico

1. Lade das Skript `score_tracker.py` via **Thonny** oder **ampy** auf den Pico
2. **Wichtig:** Speichere es als `main.py` auf dem Pico (nicht als `score_tracker.py`)
   - Thonny: Rechtsklick auf die Datei → "Rename on device" → `main.py`
   - ampy: `ampy put score_tracker.py main.py`
3. Der Pico startet jetzt automatisch und zeigt das WLAN-Hotspot an

### Nach Änderungen

- Nutze das **OTA Update System** (siehe unten)
- Oder bearbeite die `main.py` direkt via Thonny und drücke F5 zum Neuladen

## Betaflight-Dump: Einordnung fuer dein Setup

Dein geposteter Dump zeigt unter anderem:

- Betaflight 4.5.1 auf GEPRCF405
- `feature RX_SERIAL` ist aktiv
- `set serialrx_provider = CRSF`
- `set tlm_halfduplex = ON`

Das bedeutet:

- Dein Receiver-Link laeuft als Serial RX im CRSF-Protokoll.
- Fuer den Pico musst du das CRSF-Signal passiv mitlesen.

Praktisch anschliessen:

1. Nimm den CRSF-Ausgang, der am FC als RX-Serial genutzt wird (in Betaflight Ports sichtbar).
2. Fuehre dieses Signal auf GP1 (RX) des Pico.
3. GND von FC und Pico verbinden.
4. Pico separat versorgen (USB oder VSYS).

Hinweis:

- Der Pico sendet in diesem Projekt nicht zurueck, er liest nur mit.
- Wenn kein Signal ankommt, zuerst in Betaflight Configurator im Tab Ports pruefen, auf welchem UART "Serial RX" aktiv ist.
- Falls du unsicher bist, kannst du in der README-Logik bei Bedarf GP1 auf einen anderen RX-Pin umlegen, musst dann aber auch die UART-Konfiguration in `score_tracker.py` anpassen.

## Installation

1. MicroPython auf den Pico flashen.
2. Datei `score_tracker.py` auf den Pico kopieren.
3. Skript starten (manuell oder als `main.py`).

## Start und Zugriff

Beim Start wird der Access Point erzeugt (falls aktiviert):

- SSID: `FPV_Gamification_Pico`
- Passwort: `drohnenspiel`
- Web: `http://192.168.4.1`

## Webinterface

Auf der Startseite siehst du:

- Aktuellen Score
- Highscore + Pilot + Zeit
- Detektierte Manoever (Historie)
- Buttons fuer Session- und Debug-TXT Download
- Highscore Reset

## LED-Verhalten

- LED dauerhaft AN: System ist bereit (Webserver/Hotspot gestartet)
- LED blinkt: Neuer Highscore wurde geknackt und wartet auf Bestaetigung

Die Blinkgeschwindigkeit stellst du mit `LED_BLINK_INTERVAL_MS` ein.

## Highscore-Flow

- Wenn ein neuer Rekord erkannt wird, erscheint ein einfacher OK-Dialog.
- Beim Klick auf OK wird der Highscore gespeichert.
- Der Pilotenname kommt aus der Variable `DEFAULT_PILOT_NAME`.

## Downloads

Es gibt zwei Downloads im UI:

- Session TXT: erkannte Tricks + Scores
- Debug TXT: Debug-Verlauf/Logs

Technisch wird zuerst eine Exportdatei geschrieben und danach als Download gesendet.

## Wichtige Konfigurationsvariablen

Diese Variablen kannst du direkt in `score_tracker.py` oben anpassen:

- `COPTER_NAME`: Name des Copters (UI + Exporte)
- `DEFAULT_PILOT_NAME`: Standard-Pilot fuer Highscore
- `TRICK_TUNING_PROFILE`: `soft`, `medium` oder `aggressive`
- `ENABLE_HOTSPOT`: Hotspot an/aus
- `ENABLE_SERIAL_DEBUG`: Serielle Logs an/aus
- `ENABLE_LIVE_GYRO_DEBUG`: Live-Gyro Konsolenlogs an/aus
- `AP_SSID`: WLAN Name
- `AP_PASSWORD`: WLAN Passwort

## Trick-Tuning-Profile

Du kannst das Erkennungsverhalten jetzt direkt im Webinterface umstellen. Der Pico speichert die Auswahl dauerhaft in einer Datei und lädt sie beim nächsten Start wieder.

Wenn noch keine gespeicherte Einstellung vorhanden ist, startet das System automatisch mit:

- `aggressive`

Optional kannst du den Startwert auch oben im Code setzen:

- `TRICK_TUNING_PROFILE = "soft"`
- `TRICK_TUNING_PROFILE = "medium"`
- `TRICK_TUNING_PROFILE = "aggressive"`

Empfehlung:

- `soft`: erkennt leichter, gut fuer kleinere oder weichere Manoever, kann aber eher zu False Positives neigen
- `medium`: Standardprofil, guter Mittelweg
- `aggressive`: strenger, besser gegen Fehltrigger bei wilden Bewegungen, braucht aber deutlichere Tricks

## Trick-Erkennung feinjustieren

Je nach Copter, Rate und Filter koennen diese Werte angepasst werden:

- `GYRO_TRICK_THRESHOLD`
- `STABLE_THRESHOLD`
- `TRICK_START_HOLD_MS`
- `STABLE_HOLD_MS`
- `TRICK_FORCE_END_MS`
- `MIN_TRICK_DURATION`
- `MAX_TRICK_DURATION`
- `GYRO_DEADBAND`
- `GYRO_LOWPASS_ALPHA`

## HTTP-Routen

- `/` -> Webinterface
- `/data` -> Live JSON Daten
- `/download` und `/download-session` -> Session Export
- `/download-debug` und `/download-debug-raw` -> Debug Export
- `/confirm-highscore` -> Highscore per OK bestaetigen
- `/reset-highscore` -> Highscore und Session zuruecksetzen

## Troubleshooting

### Kein WLAN sichtbar

- `ENABLE_HOTSPOT = True` pruefen
- Serielle Logs ansehen (AP-Setup-Schritte)

### Webseite erreichbar, aber keine Updates

- CRSF TX wirklich auf GP1?
- Gemeinsame GND vorhanden?
- FC sendet wirklich CRSF Attitude?
- In Betaflight Ports pruefen, welcher UART "Serial RX" hat, und genau diesen Datenpfad passiv abgreifen.

### Falsche oder keine Trick-Erkennung

- Thresholds anpassen
- Live-Debug aktivieren
- Flugprofil und Rates beruecksichtigen

### Download funktioniert nicht stabil

- Browser-Tab neu laden
- Serielle Logs auf HTTP/Download-Pruefung ansehen
- Debug-Dateigroesse und Speicherzustand checken

## Hinweis zur Sicherheit

Dieses Projekt ist fuer Logging/Gamification gedacht. Es ersetzt keine sicherheitskritischen Funktionen des Flight Controllers.

## OTA Updates über WiFi

Der Pico unterstützt **Over-the-Air (OTA) Updates** - du kannst das Skript direkt über die WebUI aktualisieren, ohne den Pico per USB anzuschließen.

### Wie man ein Update durchführt:

1. Verbinde dich mit dem WLAN `FPV_Gamification_Pico` (Passwort: `drohnenspiel`)
2. Öffne `http://192.168.4.1` im Browser
3. Klicke auf den **⚙️ Admin-Link** unten rechts
4. Klicke auf das **Datei-Eingabefeld** und wähle dein aktualisiertes Python-Skript (benannt als `main.py` oder `score_tracker.py`)
5. Klicke **📤 Update speichern**
6. Der Pico speichert die alte Version als `main_backup.py` und lädt das neue Skript als `main.py`
7. Der Pico startet automatisch neu und lädt die neue Version

### Wichtig:

- Stelle sicher, dass dein neues Python-Skript **syntaktisch korrekt** ist
- Das Skript wird als **`main.py`** gespeichert - das ist die Bootdatei, die der Pico beim Start automatisch ausführt
- Falls etwas schief läuft, kannst du die alte Version später via Thonny oder ähnlich wiederherstellen (Datei: `main_backup.py`)
- Während des Updates solltest du nicht fliegen
- Das Update speichert ein komplettes Backup, falls etwas schiefgeht

### Manueller Restart:

Im Admin-Panel gibt es auch einen Button **🔄 Pico jetzt neustarten**, um den Pico neu zu starten ohne das Skript zu ändern.
