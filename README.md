# FPV Gamification Pico

FPV Score-Tracker auf einem Raspberry Pi Pico/Pico W mit passivem CRSF-Readout.

Das Skript liest Attitude-Daten (Roll/Pitch/Yaw), erkennt Tricks, vergibt Punkte und zeigt alles live im Browser an. Zusätzlich gibt es Session- und Debug-TXT-Downloads, eine Trick-Simulation zum Testen ohne Drohne, und ein OTA-Update-System direkt aus dem Webinterface.

## Features

- Passives CRSF-UART Parsing (Attitude Frames)
- Trick-Erkennung mit Punktesystem
- Highscore mit Zeitstempel und Pilot, inkl. Bestaetigungs-Popup bei neuem Rekord
- WLAN Access Point direkt auf dem Pico
- Web-UI mit Live-Score, Trick-Historie und Downloads
- Trick-Simulation im Admin-Bereich (Roll/Flip/Spin ohne echte Drohne testen)
- OTA-Update-System (main.py und alle HTML-Seiten per Browser aktualisieren, kein USB noetig)
- Firmware-Bundle-Update: alle Dateien auf einmal per `firmware.nbo` hochladen (siehe `build_firmware.py`)
- Mehrstufiger Admin-Bereich mit eigenen Unterseiten (Dashboard, Update, Simulation, Profile, System)
- Mehrere Trick-Tuning-Profile inkl. eigener Custom-Profile (`.pro` Dateien)
- LED-Statusanzeige auf dem Pico

## Benoetigte Dateien auf dem Pico

Damit die Web-Oberflaeche funktioniert, muessen **alle folgenden Dateien** im selben Verzeichnis auf dem Pico liegen (nicht nur `main.py`):

| Datei | Zweck |
|---|---|
| `main.py` | Hauptskript (Bootdatei, wird beim Start automatisch ausgefuehrt) |
| `ota_helpers.py` | Gemeinsame OTA-/Kodier-Hilfsfunktionen fuer `main.py` und `recovery.py` (ausgelagert, um `main.py` kleiner zu halten - siehe unten). **main.py startet ohne diese Datei nicht** (ImportError). |
| `index.html` | Hauptseite (Score, Highscore, Trick-Historie, Downloads) |
| `admin_dashboard.html` | Admin-Startseite: Uebersicht + Navigation zu den Unterseiten |
| `admin_update.html` | Admin-Unterseite: OTA-Update (Datei-Upload) |
| `admin_simulate.html` | Admin-Unterseite: Trick-Simulation |
| `admin_profiles.html` | Admin-Unterseite: Trick-Profile verwalten |
| `admin_system.html` | Admin-Unterseite: System-Info + manueller Restart |
| `firmware_version.txt` | Enthaelt nur die aktuelle Firmware-Versionsnummer (`X.Y.Z`), wird von `build_firmware.py` bei **jedem** Bundle-Build automatisch erzeugt/erhoeht (siehe [Versionsnummer](#versionsnummer)). Nicht manuell bearbeiten. |

**Wichtig:** Die HTML-Seiten sind bewusst **nicht** als Python-Strings im Skript eingebettet, sondern eigenstaendige Dateien, die bei Bedarf vom Dateisystem gestreamt werden (siehe [Speicher-Architektur](#speicher-architektur-warum-so-viele-dateien)). Fehlen sie auf dem Pico, schlagen `/` bzw. die jeweilige Admin-Unterseite mit einem Dateifehler fehl.

Laufzeit-/Datendateien, die das Skript selbst anlegt (kein manuelles Hochladen noetig):

- `fpv_debug_session.txt`, `fpv_highscore.json`, `fpv_trick_settings.json`
- `fpv_arcade_session_export.txt`, `fpv_debug_export.txt`
- `<name>.pro` (Custom-Trick-Profile)
- `update.pbp`, `ota_staging.tmp` (nur waehrend eines OTA-Uploads)
- `main_backup.py` bzw. `<datei>.bak` (Backup der zuletzt per OTA ersetzten Datei)

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

1. Lade **alle Dateien** (`main.py`, `ota_helpers.py`, `firmware_version.txt`, `index.html`, `admin_dashboard.html`, `admin_update.html`, `admin_simulate.html`, `admin_profiles.html`, `admin_system.html`) via **Thonny** (Dateien-Ansicht → Rechtsklick → "Upload to /") oder **ampy** auf den Pico.
2. `main.py` muss exakt so heissen (ist die Bootdatei, die MicroPython beim Start automatisch ausfuehrt).
3. Starte den Pico neu (Hardware-Reset oder Stromzyklus). Der Hotspot sollte danach automatisch erscheinen.

### Nach Änderungen

- Sowohl `main.py` als auch alle Admin-/HTML-Seiten koennen ueber das OTA-Update-System (siehe unten) direkt per Browser aktualisiert werden - kein USB/Thonny mehr noetig.

## Versionsnummer

Die Firmware zeigt unten auf der Hauptseite (neben "Admin") sowie auf der Admin-Unterseite **System** eine Versionsnummer im Format `X.Y.Z` an (z.B. `1.0.0`). Diese Nummer wird **automatisch** verwaltet:

- Quelle der Wahrheit ist `version.json` im Repo-Root.
- Bei **jedem** Bundle-Build - egal ob per `python build_firmware.py` (GUI oder Kommandozeile) oder automatisch durch den GitHub-Actions-Workflow (`.github/workflows/build-and-release-firmware.yml`) - wird die letzte Ziffer automatisch um 1 erhoeht (`1.0.0` -> `1.0.1`) und in `version.json` sowie `firmware_version.txt` (die Datei, die auf den Pico gelangt) gespeichert.
- Der GitHub-Workflow schreibt die erhoehte Versionsnummer anschliessend automatisch zurueck ins Repo, damit lokale Builds und Actions-Builds sich eine fortlaufende Nummer teilen.
- `version.json`/`firmware_version.txt` sollten nicht manuell bearbeitet werden.
- Alternativ kannst du jede der sechs Dateien jederzeit auch manuell per Thonny erneut hochladen.
- Zum Testen waehrend der Entwicklung: Speichere als `main.py` auf dem Pico und starte per Hardware-Reset, statt "Run current script" in Thonny zu benutzen (siehe [Speicher-Architektur](#speicher-architektur-warum-so-viele-dateien) fuer den Grund).

## Speicher-Architektur (warum so viele Dateien?)

Der Pico hat nur sehr wenig RAM (ca. 190-260 KB, abzueglich WLAN-Treiber, Stack etc.). Grosse HTML/JS-Strings, die permanent als Python-Variablen im Modul resident sind, haben in fruehen Versionen dieses Projekts zu `MemoryError` beim Booten gefuehrt (das Skript ist gewachsen, u.a. durch die Trick-Simulation-Buttons).

Loesung:

- Jede HTML-Seite liegt als eigene `.html` Datei auf dem Dateisystem.
- Beim Request wird die Datei in 512-Byte-Haeppchen direkt an den Browser gestreamt (`send_html_file()`), **ohne** den kompletten Inhalt als RAM-String zu halten.
- Dadurch belegen die Seiten nur kurzzeitig waehrend einer Anfrage Speicher, statt dauerhaft im Modul-RAM zu liegen.
- `index.html` enthaelt den Copter-Namen als festen Text (kein Runtime-Replace mehr). Wenn du `COPTER_NAME` in `main.py` aenderst, passe Titel/Ueberschrift in `index.html` manuell mit an.

**Hinweis zu Thonny:** "Run current script" (`%Run -c $EDITOR_CONTENT`) schickt den kompletten Skript-Text als einen String ueber die serielle Verbindung und braucht dabei spuerbar mehr RAM als ein bereits als `main.py` gespeichertes Skript, das per Hardware-Reset gestartet wird. Wenn trotz aller Optimierungen ein `MemoryError` beim Booten auftritt, zuerst pruefen, ob das Problem auch beim Start via `main.py` + Reset auftritt.

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
- Link zum Admin-Bereich (Dashboard mit Unterseiten fuer Update, Simulation, Profile, System)

## Admin-Bereich

Der Admin-Bereich ist wie ein Einstellungen-Menue mit Unterseiten aufgebaut, erreichbar ueber `/admin`:

| Unterseite | Route | Zweck |
|---|---|---|
| Dashboard | `/admin` | Uebersicht (Score, Highscore, aktives Profil, freier Speicher) + Navigation |
| Update | `/admin-update` | OTA-Update: `main.py` oder HTML-Dateien per Browser hochladen |
| Simulation | `/admin-simulate` | Tricks (Roll/Flip/Spin) ohne Drohne simulieren |
| Profile | `/admin-profiles` | Trick-Tuning-Profile verwalten (anlegen, hoch-/runterladen, loeschen, anwenden) |
| System | `/admin-system` | System-Info (Speicher, Uptime, SSID/IP) + manueller Restart |

Alle Unterseiten teilen sich eine gemeinsame Navigationsleiste, ueber die man zwischen ihnen und zurueck zur Startseite wechseln kann.

## LED-Verhalten

- LED dauerhaft AN: System ist bereit (Webserver/Hotspot gestartet)
- LED blinkt: Neuer Highscore wurde geknackt und wartet auf Bestaetigung
- LED blinkt schnell: OTA-Update laeuft

Die Blinkgeschwindigkeit stellst du mit `LED_BLINK_INTERVAL_MS` (Highscore) bzw. `OTA_LED_BLINK_INTERVAL_MS` (OTA) ein.

## Highscore-Flow

- Wenn ein neuer Rekord erkannt wird, poppt beim naechsten Laden der Startseite ein Browser-Dialog auf (`prompt()`) mit dem erreichten Score.
- Pilot-Namen eingeben und bestaetigen -> Highscore wird unter diesem Namen gespeichert.
- Dialog abbrechen/leer lassen -> Highscore wird automatisch mit `DEFAULT_PILOT_NAME` bestaetigt.
- Solange der Dialog offen ist, wird kein zweiter Dialog geoeffnet (client-seitig abgesichert ueber ein Sperr-Flag).

## Trick-Simulation (ohne Drohne testen)

Auf der Admin-Unterseite `/admin-simulate` gibt es drei Buttons (Roll/Flip/Spin), die synthetische Gyro-Daten durch denselben Erkennungscode (`detector.update()`) schicken wie echte Telemetrie. Damit kannst du das Punktesystem testen, ohne eine Drohne anzuschliessen.

## Downloads

Es gibt zwei Downloads im UI:

- Session TXT: erkannte Tricks + Scores
- Debug TXT: Debug-Verlauf/Logs

Technisch wird zuerst eine Exportdatei geschrieben (in 512-Byte-Haeppchen gestreamt, nicht als ein grosser RAM-String) und danach als Download gesendet.

## Wichtige Konfigurationsvariablen

Diese Variablen kannst du direkt in `main.py` oben anpassen:

- `COPTER_NAME`: Name des Copters (Exporte; fuer die Web-UI siehe Hinweis in [Speicher-Architektur](#speicher-architektur-warum-so-viele-dateien))
- `DEFAULT_PILOT_NAME`: Standard-Pilot fuer Highscore
- `TRICK_TUNING_PROFILE`: `beginner`, `freestyle` oder `aggressive`
- `ENABLE_HOTSPOT`: Hotspot an/aus
- `ENABLE_SERIAL_DEBUG`: Serielle Logs an/aus
- `ENABLE_LIVE_GYRO_DEBUG`: Live-Gyro Konsolenlogs an/aus
- `AP_SSID`: WLAN Name
- `AP_PASSWORD`: WLAN Passwort

## Trick-Tuning-Profile

Du kannst das Erkennungsverhalten direkt im Webinterface (Startseite) oder im Admin-Bereich (`/admin-profiles`) umstellen. Der Pico speichert die Auswahl dauerhaft in `fpv_trick_settings.json` und laedt sie beim naechsten Start wieder.

Eingebaute Profile: `beginner`, `freestyle`, `aggressive`. Zusaetzlich kannst du im Admin-Bereich eigene Profile als `.pro` Dateien anlegen, hoch-/runterladen und loeschen.

Wenn noch keine gespeicherte Einstellung vorhanden ist, startet das System automatisch mit `aggressive`.

Empfehlung:

- `beginner`: erkennt leichter, gut fuer kleinere oder weichere Manoever, kann aber eher zu False Positives neigen
- `freestyle`: guter Mittelweg
- `aggressive`: strenger, besser gegen Fehltrigger bei wilden Bewegungen, braucht aber deutlichere Tricks

## Trick-Erkennung feinjustieren

Je nach Copter, Rate und Filter koennen diese Werte angepasst werden (entweder global in `main.py` oder pro Profil):

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

| Route | Zweck |
|---|---|
| `/` | Webinterface (Hauptseite, `index.html`) |
| `/data` | Live JSON Daten (Score, Highscore, Historie, pending Highscore, aktives Profil) |
| `/download`, `/download-session` | Session Export (TXT) |
| `/download-debug`, `/download-debug-raw` | Debug Export (TXT) |
| `/confirm-highscore` | Highscore mit `DEFAULT_PILOT_NAME` bestaetigen |
| `/set-highscore-name?name=...` | Highscore mit eigenem Pilot-Namen bestaetigen |
| `/reset-highscore` | Highscore und Session-Score zuruecksetzen |
| `/set-trick-profile?profile=...` | Aktives Trick-Profil setzen |
| `/profiles-list` | Liste aller Profile (eingebaut + custom) |
| `/apply-profile?name=...` | Profil anwenden (Admin) |
| `/create-profile` (POST) | Custom-Profil anlegen/ueberschreiben |
| `/download-profile?name=...` | Profil als `.pro` herunterladen |
| `/delete-profile?name=...` | Custom-Profil loeschen |
| `/admin` | Admin-Dashboard: Uebersicht + Navigation zu den Unterseiten |
| `/admin-update` | Admin-Seite: OTA-Update |
| `/admin-simulate` | Admin-Seite: Trick-Simulation |
| `/admin-profiles` | Admin-Seite: Profile verwalten |
| `/admin-system` | Admin-Seite: System-Info + Restart |
| `/system-info` | Live JSON Daten (freier/belegter Speicher, Uptime, SSID, IP, aktives Profil, OTA-Status) |
| `/upload-chunk`, `/finalize-upload` | OTA-Update Datenuebertragung (Ziel: `main.py`, eine der Admin-/HTML-Seiten, oder `firmware.nbo` fuer ein komplettes Bundle) |
| `/restart-pico` | Pico manuell neu starten |
| `/simulate-trick?type=roll\|flip\|spin` | Trick-Simulation ohne Drohne |

## Troubleshooting

### Kein WLAN sichtbar

- `ENABLE_HOTSPOT = True` pruefen
- Serielle Logs ansehen (AP-Setup-Schritte)

### Webseite erreichbar, aber keine Updates

- CRSF TX wirklich auf GP1?
- Gemeinsame GND vorhanden?
- FC sendet wirklich CRSF Attitude?
- In Betaflight Ports pruefen, welcher UART "Serial RX" hat, und genau diesen Datenpfad passiv abgreifen.

### `/` oder eine Admin-Unterseite liefert einen Fehler

- Pruefen, ob `index.html`, `admin_dashboard.html`, `admin_update.html`, `admin_simulate.html`, `admin_profiles.html` und `admin_system.html` tatsaechlich auf dem Pico liegen (Thonny Dateien-Ansicht).
- Diese Dateien werden **nicht** automatisch mitgeliefert - beim ersten Setup manuell per Thonny hochladen. Danach koennen sie auch per OTA aktualisiert werden.

### `MemoryError` beim Booten

- Pruefen, ob du ueber "Run current script" (`%Run -c $EDITOR_CONTENT`) testest - das braucht mehr RAM als ein gespeichertes `main.py` + Hardware-Reset.
- Pruefen, ob alle sechs Dateien vorhanden sind (fehlende Dateien fuehren zu Fehlern, aber keinem MemoryError direkt - trotzdem als erstes ausschliessen).
- Falls das Skript weiter waechst (neue Features/HTML), im Zweifel weitere grosse Textbloecke ebenfalls in eigene Dateien auslagern statt als Python-String einzubetten.

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

Der Pico unterstützt **Over-the-Air (OTA) Updates** - du kannst sowohl `main.py` als auch alle HTML-Seiten direkt über die WebUI aktualisieren, ohne den Pico per USB anzuschließen.

**Erlaubte Ziel-Dateien:** `main.py`, `index.html`, `admin_dashboard.html`, `admin_update.html`, `admin_simulate.html`, `admin_profiles.html`, `admin_system.html`. Welche Datei ersetzt wird, entscheidet der **Dateiname** der ausgewählten Datei: Waehlst du eine Datei, die exakt einem dieser HTML-Dateinamen entspricht, wird genau diese Datei auf dem Pico ersetzt. Jede andere `.py`-Datei wird immer als `main.py` gespeichert (unabhaengig vom lokalen Dateinamen). Andere Ziel-Dateinamen werden serverseitig abgelehnt (Whitelist, kein beliebiges Ueberschreiben von Dateien moeglich).

### Wie man ein Update durchführt:

1. Verbinde dich mit dem WLAN `FPV_Gamification_Pico` (Passwort: `drohnenspiel`)
2. Öffne `http://192.168.4.1` im Browser
3. Klicke auf den **⚙️ Admin-Link** unten rechts und dann auf **Update** in der Navigation (`/admin-update`)
4. Klicke auf das **Datei-Eingabefeld** und wähle die aktualisierte Datei:
   - Ein beliebig benanntes `.py`-Skript wird als `main.py` gespeichert.
   - Eine Datei, die exakt einem der Admin-/HTML-Dateinamen entspricht, ersetzt genau diese Datei.
5. Klicke **📤 Upload**
6. Der Pico sichert die bisherige Version der Zieldatei (`main_backup.py` bzw. `<datei>.bak`) und speichert die neue Version unter dem Zieldateinamen.
7. **Nur bei `main.py`:** Der Pico startet automatisch neu und lädt die neue Version. Bei HTML-Dateien ist kein Neustart noetig - einfach die Seite im Browser neu laden.

### Wichtig:

- Stelle sicher, dass ein neues `main.py` **syntaktisch korrekt** ist (z.B. lokal mit `python -m py_compile main.py` pruefen) - fehlerhafter Code fuehrt sonst zu einem Bootloop.
- Falls etwas schief läuft, kannst du die alte Version später via Thonny wiederherstellen (`main_backup.py` bzw. `<datei>.bak`).
- Während eines `main.py`-Updates solltest du nicht fliegen.
- Das Update speichert vor jedem Ueberschreiben ein Backup der bisherigen Zieldatei.

### Manueller Restart:

Auf der Admin-Unterseite `/admin-system` gibt es einen Button **🔄 Restart**, um den Pico neu zu starten ohne eine Datei zu ändern.

## Firmware-Bundle-Update (alle Dateien auf einmal)

Statt jede Datei einzeln hochzuladen, kannst du mit `build_firmware.py` alle Firmware-Dateien
(`main.py` + alle `admin_*.html` + `index.html`) in eine einzige Datei `firmware.nbo` verpacken
und diese in **einem** OTA-Upload auf den Pico bringen. Der Pico entpackt das Bundle
serverseitig und ersetzt jede enthaltene Datei einzeln (inkl. Backup, genau wie beim
Einzeldatei-Update).

### Bundle erstellen (auf dem PC, normales Python 3 - nicht auf dem Pico ausführen):

```
python build_firmware.py
```

Erzeugt `firmware.nbo` im Projektverzeichnis. Optional kannst du einen anderen Ausgabepfad
angeben: `python build_firmware.py pfad/zu/firmware.nbo`. Das Skript listet fehlende Dateien
als Warnung auf und packt nur vorhandene Dateien ein.

Im GUI von `build_firmware.py` gibt es zusätzlich den Button **Bundle hochladen + entpacken**.
Damit kannst du ein bereits erstelltes `firmware.nbo` direkt an den Pico senden (Standard-URL
`http://192.168.4.1`) und sofort serverseitig per `/finalize-upload` entpacken lassen.

Zusätzlich gibt es den Button **Seriell hochladen + entpacken (Auto)**. Damit wird ein per USB
verbundener Pico automatisch gesucht und ausgewählt, `firmware.nbo` per serieller Verbindung
übertragen und direkt auf dem Pico entpackt. Voraussetzung: `mpremote` ist auf dem PC installiert
(`pip install mpremote`).

### Bundle hochladen:

1. Gehe im Admin-Bereich auf **Update** (`/admin-update`).
2. Wähle die `firmware.nbo` Datei aus (Dateiauswahl akzeptiert jetzt auch `.nbo`).
3. Klicke **Upload**. Der Pico erkennt am Dateinamen automatisch, dass es sich um ein
   Bundle handelt (Ziel `firmware.nbo`), entpackt es und ersetzt alle enthaltenen Dateien.
4. Ist `main.py` Teil des Bundles, startet der Pico danach automatisch neu (wie beim
   normalen `main.py`-Update). Enthält das Bundle nur HTML-Dateien, ist kein Neustart nötig.

### Bundle-Format (Hintergrund):

Einfaches, abhängigkeitsfreies Binaerformat (kein zip/tar, damit MicroPython es ohne
Zusatzmodule einlesen kann): 8-Byte Magic-Header `FPVBNDL1`, gefolgt von der Dateianzahl
und pro Datei Name + Inhalt, jeweils mit vorangestellter 4-Byte-Längenangabe (big-endian).
Jeder im Bundle enthaltene Dateiname wird auf dem Pico gegen `OTA_ALLOWED_TARGETS` geprüft,
bevor irgendetwas geschrieben wird - ein manipuliertes Bundle kann also keine beliebigen
Dateien überschreiben.

## Developer-Modus (Einzeldatei-OTA erlauben/sperren)

Auf der Admin-Unterseite **System** (`/admin-system`) gibt es einen Schiebeschalter
**Developer-Modus**:

- **Aus (Standard):** OTA akzeptiert nur komplette `firmware.nbo` Bundles. Einzelne
  `main.py`/`admin_*.html`/`index.html` Uploads werden serverseitig abgelehnt.
- **An:** Zusätzlich zu Bundles dürfen auch einzelne `.py`/`.html` Dateien per OTA
  hochgeladen werden (schneller für kleine Fixes, aber riskanter, da einzelne Dateien
  ohne den Rest des Bundles ersetzt werden).

Die Einstellung wird in `fpv_system_settings.json` gespeichert und übersteht einen Neustart.
Die Durchsetzung erfolgt serverseitig in `/upload-chunk` (nicht nur im Browser) - ein
direkter Request an die OTA-Routen kann die Sperre also nicht umgehen.

## Recovery-Modus fuer sehr alte/kaputte Installationen (`recovery.py`)

`recovery.py` ist ein eigenstaendiges Notfall-Skript fuer den Fall, dass auf einem Pico noch
eine sehr alte Firmware-Version laeuft (ohne modernes OTA-System) oder `main.py` defekt ist.
Es macht **nur** zwei Dinge: WLAN-Hotspot starten und eine minimale OTA-Update-Seite anzeigen -
keine Telemetrie, keine Trick-Erkennung. Die komplette OTA-Seite ist direkt als Python-String
in `recovery.py` eingebettet (kein `index.html`/`admin_*.html` noetig), damit das Update auch
funktioniert, wenn auf dem Pico ausser einer alten `main.py` gar keine anderen Dateien liegen.

### Verwendung:

1. Pico per USB mit Thonny verbinden.
2. `recovery.py` auf den Pico hochladen (falls sie nicht bereits vorhanden ist).
3. Pico per Hardware-Reset neu starten. Beim Boot-Failover startet `boot.py` automatisch
   `recovery.py`, wenn `main.py` crasht oder wiederholt ungesund startet.
4. Jetzt laeuft der Recovery-Server statt der alten
   Firmware.
5. Mit dem WLAN verbinden (SSID/Passwort siehe `AP_SSID`/`AP_PASSWORD` oben in `recovery.py`,
   standardmaessig identisch zur normalen Firmware) und `http://192.168.4.1` im Browser
   aufrufen.
6. Empfohlen: das komplette `firmware.nbo` Bundle hochladen (siehe oben) - das entpackt und
   ersetzt `main.py` + alle `admin_*.html` + `index.html` in einem Rutsch. Alternativ reicht
   auch ein einzelnes `main.py` fuer einen minimalen Fix.
7. Sobald `main.py` Teil des Uploads war, startet der Pico automatisch neu und bootet danach
   wieder ganz normal mit der neuen Firmware (nicht mehr im Recovery-Modus).

`recovery.py` nutzt exakt dasselbe OTA-Chunk-Upload-, Backup- und Bundle-Format wie die normale
Firmware, ist aber komplett unabhaengig von den `admin_*.html`-Dateien (und vom Developer-Modus-
Schalter - im Recovery-Modus sind Einzeldatei-Uploads immer erlaubt, damit ein kaputtes System
auf jeden Fall reparierbar bleibt).
