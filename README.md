# 🎮 FPV Gamification Pico

Anpassungsfähiger **FPV Score-Tracker**, **Infection-Game** & **Mini-Game Server** auf einem Raspberry Pi Pico / Pico W mit passivem CRSF-Readout.

Das Skript klinkt sich passiv in deine Telemetrie ein, liest Attitude-Daten (Roll/Pitch/Yaw) sowie Sensorik, erkennt Stunts/Tricks, vergibt Highscores und hostet eine schicke Live-Web-App direkt aus der Hosentasche! **Keine zusätzlichen Sensoren oder Kabel nötig** – einfach 1x UART abgreifen und losfliegen! 🥳

---

## ⚡ Features Highlights

* 🛰️ **Passiver CRSF-UART Sniffer:** Klinkt sich geräuschlos in deine ELRS/TBS-Telemetrie ein (Attitude, Vario & Akku-Frames).
* 🎯 **Smart Trick-Erkennung:** Automatische Erkennung von Flips, Rolls & Spins mit dynamischer Punktevergabe.
* ☣️ **Infection-Modus (BLE):** Erkennt Mitspieler über verbindungslose Bluetooth-LE Advertisements direkt in der Luft!
* 🏆 **Live Highscore-System:** Mit Zeitstempel und Pilot. Inklusive Piloten-Eingabe im Browser, Bestätigungs-Popup bei neuem Rekord und LED-Blink-Party!
* 📡 **Eigener WLAN-Hotspot:** Keine App-Installation nötig – einfach per Smartphone/Tablet im Feld mit dem Pico verbinden.
* 🕹️ **Real-Time Mini-Games (Challenges):**
  * 🛬 **Touch & Go:** Belohnt Butterlandungen (ausgewertet über Sinkrate & Gyro-Aufprall).
  * 🎯 **Altitude-Hold / Limbo:** Fliege präzise auf Höhe oder bleibe unter der Limbo-Grenzlinie.
  * 🔋 **Eco-Challenge:** Wer fliegt am sparsamsten? Punkte basieren auf verbrauchten mAh.
* 🎮 **Trick-Simulation:** Teste das Punkte- & Erkennungssystem im Admin-Panel ganz ohne Drohne (einfach per Klick auf Roll/Flip/Spin).
* 🚀 **OTA & Bundle Updates:** Komplette Firmware-Updates (`firmware.nbo`) oder einzelne HTML/Python-Dateien kabellos direkt im Browser aktualisieren – nie wieder USB-Gefummel!
* ⚙️ **Mehrstufiges Admin-Panel:** Dashboard, Trick-Profile (`.pro`), OTA-Update, Mini-Games & System-Monitor.
* 💾 **Session & Debug Downloads:** Lade deine Historie und Debug-Logs als TXT direkt auf dein Gerät herunter.

---

## 📂 Benötigte Dateien auf dem Pico (Speicher-Architektur)

Damit das Webinterface flüssig und ohne RAM-Engpässe läuft (der Pico hat nur sehr wenig RAM, ca. 190-260 KB), haben wir uns einen cleveren Trick überlegt: Die HTML-Dateien sind bewusst **nicht** als Python-Strings im Skript eingebettet. Stattdessen werden sie beim Request in kleinen 512-Byte-Häppchen direkt vom Dateisystem gestreamt. Dadurch belegen die Seiten nur kurzzeitig während einer Anfrage Speicher!

Es müssen **alle** folgenden Dateien im Hauptverzeichnis des Pico liegen (nicht nur `main.py`):

| Datei | Zweck |
|---|---|
| `boot.py` & `boot_runtime.py` | 🥾 Boot-Stack: Entscheidet u.a., ob in den normalen oder Recovery-Modus gestartet wird. |
| `recovery.py` | 🚑 Das Notfall-Skript für den Fall, dass die Haupt-Firmware crasht. |
| `main.py` (oder `main_LilyGo.py`) | 🚀 Hauptskript (Bootdatei für die eigentliche App, startet nach `boot.py`). |
| `hotspot_common.py` & `hotspot.conf` | 📡 WLAN-Konfiguration und Access Point Routinen. |
| `ota_helpers.py`, `upload_helpers.py`, `misc_routes_helpers.py` | 🛠️ OTA- & Hilfsfunktionen für den Webserver und Dateiuploads. |
| `challenge_helpers.py` | 🎮 Logik für die Real-Time Mini-Games (Touch & Go, Limbo, Eco). |
| `infection_mode.py` & `idcard_helpers.py` | ☣️ Logik für den Bluetooth-Infection-Modus und Spieler-Verwaltung. |
| `*.pak` Dateien (z.B. `en.pak`, `de.pak`) | 🌍 Sprachpakete für die Internationalisierung des Webinterfaces. |
| `index.html` | 📱 Hauptseite (Scoreboard, Live-Feed, Historie, Downloads für Session/Debug). |
| `admin_dashboard.html`, `admin_update.html`, `admin_simulate.html`, `admin_profiles.html`, `admin_system.html`, `admin_challenges.html`, `admin_idcard.html`, `admin_infection.html` | 🎛️ Alle Admin-Unterseiten (Update, Simulation, System-Info, Challenges, etc.). |
| `challenges_view.html` & `infection_view.html` | 📺 Öffentliche Live-Visualisierungen für Zuschauer. |
| `firmware_version.txt` | 🏷️ Versionstag (z.B. `1.0.1`). Wird bei jedem Release **automatisch** hochgezählt. Nicht manuell bearbeiten! |

*Zusätzliche Laufzeit-/Datendateien (legt das System selbst an):* `fpv_highscore.json`, `fpv_trick_settings.json`, Log-Dateien (`fpv_debug_session.txt`), Custom-Trick-Profile (`<name>.pro`), etc.

**💡 Wichtiger Hinweis zu Thonny:** Teste das Skript **nicht** mit "Run current script". Dadurch wird der Text als fetter String in den RAM geladen und erzeugt schnell einen `MemoryError`. Speichere es als `main.py` und mache einen Hardware-Reset!

---

## 🛠️ Hardware & Verdrahtung

Du benötigst lediglich **3 Leitungen** vom Flight Controller (oder ELRS-Empfänger) zum Pico:

```text
+-------------------+             +-----------------------+
| Flight Controller |             | Raspberry Pi Pico     |
|                   |             |                       |
|        GND  ------>------------->  GND                  |
|     CRSF TX ------>------------->  GP1 (UART0 RX)       |
|         5V  ------>------------->  VSYS (optional)      |
+-------------------+             +-----------------------+
```

> ⚠️ **Wichtig:**
> - Niemals 5V direkt auf den `3V3`-Pin legen!
> - Auf gemeinsame Masse (`GND`) achten, sonst kommen keine stabilen Daten an!
> - **Aktuelle UART-Konfiguration im Code:** UART0 RX ist auf **GP1**! Hier kommen die CRSF-Daten rein. (UART0 TX liegt auf GP0, wird aber aktuell nicht aktiv genutzt).

---

## 🚀 Quick Start & Konfiguration

### 1. Erste Installation auf dem Pico
1. Lade **alle oben gelisteten Dateien** via [Thonny](https://thonny.org/) (*Datei-Ansicht → Rechtsklick → Upload to /*) oder `ampy` auf deinen Pico.
2. Starte den Pico neu (Hardware-Reset oder Strom weg und wieder dran).
3. Verbinde dich mit dem neuen WLAN-Hotspot:
   * **SSID:** `FPV_Gamification_Pico` *(Standard)*
   * **Passwort:** `drohnenspiel` *(Standard)*
   * **URL im Browser:** `http://192.168.4.1`

### 2. Wichtige Konfigurationen im Code
* **Hotspot-Einstellungen (`source/hotspot.conf`):**
  Der normale Pico-Hotspot wird hier definiert. Fehlt die Datei, gelten Fallback-Werte.
  ```json
  {
     "ssid": "FPV_Gamification_Pico",
     "password": "drohnenspiel"
  }
  ```
* **Infection-Modus (`infection.conf` & `infection_players.conf`):**
  Der Infection-Modus erkennt Mitspieler über verbindungslose Bluetooth-LE-Advertisements. Der normale Hotspot bleibt währenddessen aktiv! Du kannst Rundendauer, Startrolle, RSSI-Nähe und Immunität in `infection.conf` einstellen.
* **Globale Variablen (direkt in `main.py`):**
  Pass das Erlebnis oben im Code an:
  * `COPTER_NAME`: Name deines Quads für Exporte (Hinweis: Ändere das auch in der `index.html`!).
  * `DEFAULT_PILOT_NAME`: Standard-Pilot für den Highscore.
  * `TRICK_TUNING_PROFILE`: Profil beim ersten Start.
  * `ENABLE_HOTSPOT`, `ENABLE_SERIAL_DEBUG`, `ENABLE_LIVE_GYRO_DEBUG`: Zum Debuggen an-/ausschalten.

---

## 🎮 Spielmodi & Real-Time Challenges

Neben der normalen Trick-Erkennung haben wir krasse Mini-Games am Start! Diese funktionieren komplett passiv aus dem CRSF-Datenstrom, solange dein Flight Controller die entsprechenden Telemetrie-Frames (Vario, Battery) sendet.

### 🕹️ Real-Time Mini-Games (`/admin-challenges`)
```text
 🛬 TOUCH & GO            🎯 LIMBO / HOLD          🔋 ECO-CHALLENGE
 -----------------        ----------------        ------------------
 Sanftes Aufsetzen        Höhe halten             Maximaler Fly-Time
 misst Sinkrate & Gyro    unter Zeitdruck          Punkte pro mAh
```

1. **Touch & Go / Präzisions-Landung:**
   Nutzt den CRSF Vario-Frame `0x07` (Sinkrate) und das Gyro. Punkte gibt es nur bei butterweichem Aufsetzen: Die Sinkrate muss vorher negativ gewesen sein (echter Sinkflug) und beim Erreichen von 0 darf es keinen Gyro-Spike (harter Aufprall) geben!
2. **Altitude-Hold- / Limbo-Challenge:**
   Die relative Höhe wird geschätzt, indem die Sinkrate vom Startpunkt an integriert wird.
   - *Hold-Modus:* Höhe für X Sekunden innerhalb einer Toleranz halten.
   - *Limbo-Modus:* X Sekunden unterhalb einer frei wählbaren Decke fliegen.
3. **Energy Management / Eco-Challenge:**
   Nutzt den CRSF Battery-Sensor-Frame `0x08`. Deine Punkte sinken mit der verbrauchten Kapazität (mAh). Wer hat den sanftesten Gasfinger?

*(Hinweis: Auf `/challenges-view` gibt es eine hübsch gestaltete Live-Visualisierung mit Fortschrittsbalken für Zuschauer während des Fluges!)*

---

## 🏆 Highscore-Flow

Wenn du ordentlich Punkte sammelst und der Trick-Detektor glüht:
1. Das System erkennt einen neuen Rekord. Die LED fängt langsam an zu blinken.
2. Beim nächsten Laden der Startseite (oder bei aktiver Verbindung) ploppt ein Browser-Dialog auf.
3. Trag deinen Piloten-Namen ein und bestätige. ZACK! Dein Highscore ist gesichert.
4. (Brichst du ab, wird `DEFAULT_PILOT_NAME` verwendet).

---

## 🎚️ Trick-Tuning-Profile

Das System unterstützt vordefinierte und eigene Tuning-Profile für die Stunt-Erkennung. Die Auswahl wird dauerhaft in `fpv_trick_settings.json` gespeichert.

* 🟢 **`beginner`:** Erkennt leichter, gut für kleinere/weichere Manöver (kann eher zu False Positives neigen).
* 🟡 **`freestyle`:** Der perfekte Mittelweg für normale Sessions.
* 🔴 **`aggressive`:** Strengere Erkennung gegen Fehltrigger bei wilden Bewegungen (braucht deutlichere Tricks). Startet standardmäßig in diesem Modus.
* 🛠️ **Eigene Custom-Profile (`.pro`):** Können im Admin-Bereich (`/admin-profiles`) per Webinterface angelegt, hoch-/runtergeladen und gelöscht werden.

### ⚙️ Feinjustierung der Trick-Erkennung
Je nach Copter, Rate und Filter kannst du folgende Parameter im Code oder Profil anpassen:
`GYRO_TRICK_THRESHOLD`, `STABLE_THRESHOLD`, `TRICK_START_HOLD_MS`, `STABLE_HOLD_MS`, `TRICK_FORCE_END_MS`, `MIN_TRICK_DURATION`, `MAX_TRICK_DURATION`, `GYRO_DEADBAND`, `GYRO_LOWPASS_ALPHA`.

---

## 🚦 LED Status Signalisation

Den Rhythmus steuerst du mit Variablen wie `LED_BLINK_INTERVAL_MS`.

| Status | LED-Verhalten |
|---|---|
| 🟢 **Bereit** | Dauerhaft AN (System ist bereit, Server gestartet) |
| 🥳 **New Highscore!** | Langsames Blinken (wartet auf Piloten-Bestätigung) |
| ⚡ **OTA-Update** | Schnelles Blinken (OTA-Übertragung läuft) |

---

## 🔄 OTA Updates über WiFi (Nie wieder Kabel!)

Der Pico unterstützt **Over-the-Air (OTA) Updates**. Du kannst sowohl `main.py` als auch HTML-Seiten direkt über den Browser aktualisieren!

### Wie man ein Einzel-Update durchführt:
1. Im WLAN `FPV_Gamification_Pico` anmelden, auf `http://192.168.4.1` gehen.
2. Im Admin-Bereich auf **Update** (`/admin-update`) klicken.
3. Datei auswählen:
   - Jedes `.py`-Skript wird automatisch als `main.py` gespeichert!
   - Eine Datei mit passendem Namen (z.B. `index.html`) ersetzt genau diese HTML-Seite. Andere Dateinamen werden sicherheitshalber abgelehnt.
4. Auf **📤 Upload** klicken. Der Pico sichert die bisherige Zieldatei (Backup z.B. als `main_backup.py`) und überschreibt sie.
5. Bei `main.py` startet der Pico danach automatisch neu. Bei HTML reicht ein Reload im Browser!

> **⚠️ Wichtig:** Stell sicher, dass deine `main.py` syntaktisch korrekt ist, sonst hängst du in einem Bootloop. Bitte nicht fliegen, während ein Update läuft!

### 📦 Firmware-Bundle-Update (Alle Dateien auf einmal)
Statt alles einzeln hochzuladen, kannst du mit dem Skript `build_firmware.py` auf deinem PC alle Dateien in eine Datei `firmware.nbo` packen.
Lade diese `.nbo`-Datei hoch, und der Pico entpackt sie komplett serverseitig und aktualisiert alle HTMLs + Skripte in einem Rutsch (inkl. Backups)!
*(Im Bundle-Format versteckt sich ein robuster Header `FPVBNDL1`, der alles super sicher und MicroPython-freundlich macht).*

**Developer-Modus (`/admin-system`):** Standardmäßig akzeptiert das OTA-System nur Bundles. Schaltest du den Developer-Modus ein, kannst du auch wieder einzelne `main.py` oder `.html` Dateien hochladen.

---

## 🚑 Notfall: Recovery-Modus (`recovery.py`)

Hast du deine `main.py` völlig geschrottet oder hast noch eine ur-alte Installation ohne OTA-Support?
`recovery.py` ist dein Retter!
1. Lade `recovery.py` per USB (Thonny) hoch.
2. Neustart. Wenn `main.py` crasht, bootet der Pico ins Recovery-Skript.
3. Das Skript erzeugt ein minimales WLAN und eine Update-Seite (alles steckt in einem String, keine externen HTMLs nötig).
4. Verbinde dich, rufe `http://192.168.4.1` auf, lade ein frisches `firmware.nbo`-Bundle hoch. Der Pico fixt sich selbst und startet glücklich neu! 🧟‍♂️

---

## 🌐 HTTP API Routes Overview

Hier ein kleiner Auszug, was unter der Haube schlummert:

| Route | Zweck |
|---|---|
| `/` | 📱 Webinterface Hauptseite (`index.html`) |
| `/data` | 📊 Live JSON Data (Score, Profil, Highscore, Historie) |
| `/system-info` | 💻 Live JSON (Speicher, Uptime, SSID, OTA-Status) |
| `/challenges-data`| 🎮 Live JSON (Status aller 3 Challenges, Höhe/Batterie) |
| `/challenges-view` | 📺 Live Mini-Game Visualisierung |
| `/admin` | 🎛️ Admin Dashboard |
| `/admin-update` | 🔄 Browser OTA Update (Einzeldateien oder `firmware.nbo`) |
| `/admin-simulate` | 🕹️ Trick Simulation |
| `/admin-profiles` | 🎚️ Profil-Tuning & Custom-Profile |
| `/admin-system` | 💻 System-Info, Developer Mode & Restart |
| `/admin-challenges` | 🎮 Challenge Manager |
| `/upload-chunk` / `/finalize-upload`| 📦 Firmware & HTML File Uploader |
| `/simulate-trick` | 🎲 Synthetic Gyro Trigger (`?type=roll\|flip\|spin`) |
| `/download`, `/download-debug` | 💾 Session/Debug TXT Export laden |

*(Zudem gibt es zig Unter-Routen wie `/set-trick-profile`, `/challenge-touchgo-start`, `/confirm-highscore` etc., mit denen das Frontend mit dem Backend kommuniziert).*

---

## 🗂️ Was ist wo? (Datei-Übersicht)

Damit du dich in diesem Projekt-Dschungel zurechtfindest, hier ein kleiner Wegweiser, was die einzelnen Skripte und Ordner eigentlich machen:

### 🏠 Root-Verzeichnis (Das Hauptverzeichnis des Repos)
* `build_firmware.py` 📦: Das ist dein Helfer-Tool auf dem PC! Es verpackt alle Dateien aus dem `source`-Ordner in eine einzige `firmware.nbo`-Datei für bequeme OTA-Updates.
* `check_pico_storage.py` 💾: Ein Tool, um den Speicherplatz auf deinem Pico zu checken.
* `download_lilygo_files.py` ⬇️: Lädt spezifische Dateien herunter, die für das LilyGO T-Display Setup (mit Display) gebraucht werden.
* `LilyGo.py` 📺: Eine spezielle Hauptdatei, wenn du das Projekt nicht auf einem normalen Pico, sondern auf einem LilyGO T-Display Board mit schickem Display laufen lässt.
* `mission_builder.py` 🗺️: Baut und kompiliert Flug-Missionen.
* `ota_checker.py` 🕵️‍♂️: Prüft den OTA-Status.
* `profilemanager.py` 🎚️: Verwalte und erstelle deine Trick-Profile lokal.
* `web_server.py` 🌐: Ein lokales Mockup des Webservers zum Testen am PC.
* `ideen.txt` 💡: Die Schmiede! Hier werden neue Features und irre Ideen gesammelt.

### 📁 Der `source`-Ordner (Das Herzstück für den Pico)
Hier liegen alle Dateien, die tatsächlich *auf* deinen Pico müssen (oder vom Build-Skript eingepackt werden):

* **Python-Kernskripte:**
  * `main.py` 🚀: Die absolute Boss-Datei. Startet den ganzen Zirkus (Webserver, Telemetrie, Tricks).
  * `boot.py` & `boot_runtime.py` 🥾: MicroPython startet diese Dateien beim Booten. Sie entscheiden, ob z.B. der Notfall-Recovery-Modus geladen werden muss.
  * `ota_helpers.py`, `upload_helpers.py`, `misc_routes_helpers.py` 🛠️: Wichtige Helferlein für OTA-Updates, Datei-Uploads und spezielle Web-Routen, ausgelagert um RAM zu sparen.
  * `hotspot_common.py` & `hotspot.conf` 📡: Alles rund um den WLAN-Access-Point.
  * `recovery.py` 🚑: Das Notfall-Skript. Startet einen minimalen OTA-Server, wenn alles andere brennt.
* **Spielmodi & Challenges:**
  * `challenge_helpers.py` 🎮: Die Logik hinter den Real-Time Mini-Games (Limbo, Eco, Touch & Go).
  * `infection_mode.py` / `.mpy` ☣️: Der Code für den gnadenlosen Bluetooth Infection-Modus (das `.mpy` ist kompiliert für mehr Speed & RAM).
  * `idcard_helpers.py` 🪪: Helfer für die Verwaltung von Spieler-IDs im Infection-Modus.
* **Web-Oberfläche (Die HTML-Seiten):**
  * `index.html`: Das Main-Dashboard für Piloten.
  * `admin_dashboard.html`, `admin_update.html`, `admin_simulate.html`, `admin_profiles.html`, `admin_system.html`, `admin_challenges.html`, `admin_idcard.html`, `admin_infection.html`: Alle Kontrollzentren im Backend.
  * `challenges_view.html` & `infection_view.html` 📺: Die hübschen, öffentlichen Ansichten für Zuschauer.
* **Sprachpakete (.pak):**
  * `de.pak`, `en.pak`, `es.pak`, `fr.pak`, `it.pak`, `pt.pak`, `tr.pak` 🌍: Internationalisierung, Baby! Übersetzungsdateien für das Webinterface.
* `firmware_version.txt` & `version.json` 🏷️: Hier merkt sich das System, auf welcher Version du fliegst.

---

## 🛠️ Troubleshooting

* **Kein WLAN sichtbar:**
  Prüfe `ENABLE_HOTSPOT = True` in `main.py` und die seriellen Konsole-Logs.
* **Webseite erreichbar, aber keine Live-Daten:**
  * Ist CRSF TX wirklich auf **GP1** am Pico angeschlossen?
  * Haben FC und Pico eine gemeinsame Masse (**GND**)?
  * Sendet der FC wirklich CRSF-Attitude Frames? (In Betaflight prüfen, welcher UART "Serial RX" hat).
* **`/` oder Admin-Seite liefert Fehler:**
  Prüfe, ob du wirklich *alle* `.html` Dateien hochgeladen hast. Diese kommen nicht automatisch mit der `main.py` mit!
* **`MemoryError` beim Booten:**
  Testest du in Thonny mit "Run current script"? Das lädt den Code als riesigen String hoch! Bitte lokal als `main.py` speichern und per Hardware-Reset starten.
* **Falsche Trick-Erkennung?**
  Thresholds anpassen, Live-Debug aktivieren, und sicherstellen, dass das richtige Tuning-Profil (`freestyle`/`aggressive`) aktiv ist!
* **Download funktioniert nicht stabil?**
  Einfach Tab neu laden. Die Exporte werden sicherheitshalber in kleinen 512-Byte-Häppchen gestreamt, um den RAM zu schonen!

---

## 📂 Repository & Links

* 🐙 **GitHub Repository:** [Devilwitha/FPV_Gamification_Pico](https://github.com/Devilwitha/FPV_Gamification_Pico)
* 💡 **Build & Release Pipelines:** Das Repo nutzt GitHub Actions, um Releases automatisch zu builden und Versionstags (`version.json`) hochzuzählen.

---
*Guten Flug und möge der Highscore mit dir sein! 🛸💨*