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
- `ENABLE_HOTSPOT`: Hotspot an/aus
- `ENABLE_SERIAL_DEBUG`: Serielle Logs an/aus
- `ENABLE_LIVE_GYRO_DEBUG`: Live-Gyro Konsolenlogs an/aus
- `AP_SSID`: WLAN Name
- `AP_PASSWORD`: WLAN Passwort

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
