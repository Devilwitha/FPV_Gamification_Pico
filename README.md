# FPV Gamification Pico

Dieses Projekt läuft auf einem Raspberry Pi Pico und wertet CRSF-/Telemetrie-Daten eines FPV-Setups aus. Erkennt das Script ein Manöver, wird ein Punktestand berechnet und im integrierten Webinterface angezeigt.

## Was das Projekt macht

- Liest Attitude-/Gyro-Daten passiv über UART ein.
- Erkennt verschiedene Flugmanöver wie Roll, Flip und Spin.
- Vergibt dafür Punkte und speichert eine Trick-Historie.
- Startet einen WLAN-Access-Point auf dem Pico.
- Stellt eine einfache Webseite mit Score, Historie und Download-Funktion bereit.

## Benötigte Hardware

- Raspberry Pi Pico
- ELRS-Empfänger oder FPV-Flight-Controller mit CRSF-/Telemetrie-Ausgabe
- 5V-Versorgung vom Flight Controller für den Pico
- Verbindungskabel
- Gemeinsame Masse zwischen Pico und Flight Controller

## Anschlüsse

Im aktuellen Script sind diese Pins konfiguriert:

- GP0 als UART0 TX
- GP1 als UART0 RX

Wichtig: Das Script liest die Telemetrie passiv über RX ein. Der relevante Anschluss ist daher GP1.

## Verdrahtung

So schließt du es an:

1. Verbinde 5V vom Flight Controller mit dem 5V-Eingang des Pico, also mit VSYS.
2. Verbinde GND vom Pico mit GND vom Flight Controller.
3. Verbinde den UART- bzw. CRSF-TX-Ausgang des ELRS-Empfängers oder Flight Controllers mit GP1 am Pico.
4. Falls dein Empfänger bzw. Flight Controller nur 3,3 V-Pegel liefert, kann das direkt funktionieren.
5. Der Pico erzeugt danach seinen eigenen WLAN-Access-Point, daher ist keine zusätzliche WLAN-Verdrahtung nötig.

Wichtig: 5V gehören an VSYS, nicht an 3,3V.

Hinweis: GP0 ist im Code als TX definiert, wird für die reine Erkennung aber nicht aktiv verwendet.

## Funktionsweise

Das Script liest die CRSF-Attitude-Daten, berechnet daraus die Drehraten und entscheidet anhand von Schwellenwerten, ob gerade ein Trick ausgeführt wurde. Wenn sich die Bewegung wieder beruhigt, wird der Trick ausgewertet und in Punkte umgerechnet.

Erkannte Trickarten sind unter anderem:

- Roll
- Flip
- Spin
- Kombinationen wie Matty Flip Combo

## WLAN und Weboberfläche

Beim Start erstellt der Pico ein WLAN mit diesen Zugangsdaten:

- SSID: `FPV_Gamification_Pico`
- Passwort: `drohnenspiel`

Danach kannst du dich mit dem WLAN verbinden und im Browser folgende Adresse öffnen:

- `http://192.168.4.1`

Die wichtigsten Endpunkte sind:

- `/` - Weboberfläche mit Score und Trickliste
- `/data` - JSON-Daten für die Live-Anzeige
- `/download` - TXT-Export der Session

## So startest du es

1. Kopiere die Datei `score_tracker.py` auf den Pico.
2. Stelle sicher, dass das Board MicroPython ausführt.
3. Starte das Script auf dem Pico.
4. Warte auf den WLAN-Access-Point.
5. Verbinde dich mit `FPV_Gamification_Pico`.
6. Öffne `http://192.168.4.1` im Browser.

## Konfiguration im Code

Am Anfang der Datei kannst du diese Werte anpassen:

- `ENABLE_HOTSPOT` - aktiviert oder deaktiviert den WLAN-AP
- `ENABLE_SERIAL_DEBUG` - aktiviert Debug-Ausgaben über die serielle Konsole
- `AP_SSID` - Name des WLANs
- `AP_PASSWORD` - WLAN-Passwort

Außerdem kannst du die Trick-Erkennung über diese Werte feinjustieren:

- `GYRO_TRICK_THRESHOLD`
- `STABLE_THRESHOLD`
- `MIN_TRICK_DURATION`
- `MAX_TRICK_DURATION`

## Debugging

- Wenn kein WLAN erscheint, prüfe zuerst `ENABLE_HOTSPOT`.
- Wenn keine Tricks erkannt werden, prüfe die Verdrahtung an GP1 und die GND-Verbindung.
- Wenn die Werte unplausibel sind, passe die Schwellenwerte im Script an.
- Wenn die Webseite lädt, aber keine Daten zeigt, prüfe, ob das Script wirklich läuft und `/data` erreichbar ist.

## Lizenz

Wenn du möchtest, kann hier noch eine Lizenz ergänzt werden.
