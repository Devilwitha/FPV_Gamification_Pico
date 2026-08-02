# 🎮 FPV Gamification Pico

Anpassungsfähiger **FPV Score-Tracker**, **Infection-Game**, **King-of-the-Hill/Race** & **Mini-Game Server** auf einem Raspberry Pi Pico / Pico W mit passivem CRSF-Readout.

Das Skript klinkt sich passiv in deine Telemetrie ein, liest Attitude-Daten (Roll/Pitch/Yaw) sowie Sensorik, erkennt Stunts/Tricks, vergibt Highscores und hostet eine schicke Live-Web-App direkt aus der Hosentasche! **Keine zusätzlichen Sensoren oder Kabel nötig** – einfach 1x UART abgreifen und losfliegen! 🥳

Jeder Pico wählt beim ersten Start eine **Geräte-Rolle**: **🎮 Gamification** (am Piloten getragener Score-/Trick-Tracker mit Infection-Modus) oder **⛳ Gate/Hill** (stationäres Gerät für King-of-the-Hill-Hügel oder Race-Tore). Für Endnutzer gibt es zusätzlich einen eigenständigen **Windows Installer** (`windows/`), der Firmware & Sprachpakete ganz ohne Python-Kenntnisse per USB aufspielt.

---

## ⚡ Features Highlights

* 🛰️ **Passiver CRSF-UART Sniffer:** Klinkt sich geräuschlos in deine ELRS/TBS-Telemetrie ein (Attitude, Vario & Akku-Frames).
* 🎯 **Smart Trick-Erkennung:** Automatische Erkennung von Flips, Rolls & Spins mit dynamischer Punktevergabe.
* ☣️ **Infection-Modus (BLE):** Erkennt Mitspieler über verbindungslose Bluetooth-LE Advertisements direkt in der Luft!
* ⛳ **King of the Hill & Race (BLE, Gate/Hill-Rolle):** Stationäre Picos als Hügel-Anker oder Start-/Ziel-Tore – Punkte über Zeit in Reichweite bzw. gestoppte Rundenzeiten.
* 🔫 **Shooter-Modus (Infrarot-Laser-Tag):** Echtes IR-"Duell" zwischen mehreren Picos – Grove-Infrarot-Emitter zum Abfeuern, IR-REC38-Empfänger zählt Treffer automatisch (inkl. Leben/Ausscheiden, Trefferverlauf pro Schütze). Abfeuern per Web-Button **oder** automatisch über einen frei wählbaren AUX-Kanal am Sender (siehe [🔫 Shooter-Hardware](#-shooter-hardware-anschliessen--testen)).
* 🏆 **Live Highscore-System:** Mit Zeitstempel und Pilot. Inklusive Piloten-Eingabe im Browser, Bestätigungs-Popup bei neuem Rekord und LED-Blink-Party!
* 📡 **Eigener WLAN-Hotspot:** Keine App-Installation nötig – einfach per Smartphone/Tablet im Feld mit dem Pico verbinden.
* 🕹️ **Real-Time Mini-Games (Challenges):**
  * 🛬 **Touch & Go:** Belohnt Butterlandungen (ausgewertet über Sinkrate & Gyro-Aufprall).
  * 🎯 **Altitude-Hold / Limbo:** Fliege präzise auf Höhe oder bleibe unter der Limbo-Grenzlinie.
  * 🔋 **Eco-Challenge:** Wer fliegt am sparsamsten? Punkte basieren auf verbrauchten mAh.
* 🎮 **Trick-Simulation:** Teste das Punkte- & Erkennungssystem im Admin-Panel ganz ohne Drohne (einfach per Klick auf Roll/Flip/Spin).
* 🚀 **OTA & Bundle Updates:** Komplette Firmware-Updates (`firmware.nbo`) oder einzelne HTML/Python-Dateien kabellos direkt im Browser aktualisieren – nie wieder USB-Gefummel! Zusätzlich kann der Pico selbst per **"Nach Updates suchen"** die neueste GitHub-Release abrufen.
* 🔒 **Offline-Lizenzsystem (RSA, Hardware-gebunden):** Die Firmware prüft eine an die Pico-Hardware-ID gebundene, signierte `license.lic` – ohne gültige Lizenz bleibt nur die System-Seite erreichbar.
* 🖥️ **Windows Installer:** Eigenständige `.exe` (kein Python nötig) zum Flashen von Firmware & Sprachpaketen per USB, inkl. automatischem Download der neuesten GitHub-Release.
* 🧪 **PC-Firmware-Simulator:** Testet den kompletten Webserver samt Admin-Oberfläche am PC, ganz ohne angeschlossenen Pico.
* ⚙️ **Mehrstufiges Admin-Panel:** Dashboard, Trick-Profile (`.pro`), OTA-Update, Mini-Games & System-Monitor.
* 💾 **Session & Debug Downloads:** Lade deine Historie und Debug-Logs als TXT direkt auf dein Gerät herunter.

---

## 📂 Benötigte Dateien auf dem Pico (Speicher-Architektur)

Damit das Webinterface flüssig und ohne RAM-Engpässe läuft (der Pico hat nur sehr wenig RAM, ca. 190-260 KB), haben wir uns einen cleveren Trick überlegt: Die HTML-Dateien sind bewusst **nicht** als Python-Strings im Skript eingebettet. Stattdessen werden sie beim Request in kleinen 512-Byte-Häppchen direkt vom Dateisystem gestreamt. Dadurch belegen die Seiten nur kurzzeitig während einer Anfrage Speicher!

Es müssen **alle** folgenden Dateien im Hauptverzeichnis des Pico liegen (nicht nur `main.py`) – am einfachsten kommen sie in einem Rutsch über ein `firmware.nbo`-Bundle drauf (siehe [Quick Start](#-quick-start--konfiguration) & [OTA Updates](#-ota-updates-über-wifi-nie-wieder-kabel)):

| Datei | Zweck |
|---|---|
| `boot.py` & `boot_runtime.py` | 🥾 Boot-Stack: Entscheidet u.a., ob `role_setup.py`, der Recovery-Modus oder die gewählte Geräte-Rolle (`main.py`/`main_gatehill.py`) gestartet wird. |
| `recovery.py` | 🚑 Das Notfall-Skript für den Fall, dass die Haupt-Firmware crasht (rollenunabhängig). |
| `role_setup.py` & `device_role.json` | 🧭 Einmalige Ersteinrichtungsseite zur Wahl der Geräte-Rolle (Gamification vs. Gate/Hill). |
| `main.py` (oder `main_LilyGo.py`) | 🚀 Hauptskript der Rolle "Gamification" (startet nach `boot.py`). |
| `main_gatehill.py` & `index_gatehill.html` | ⛳ Hauptskript & Oberfläche der Rolle "Gate/Hill" (King-of-the-Hill-Hügel bzw. Race-Tor A/B). |
| `gmr.py`, `koth_mode.py`, `race_mode.py`, `shooter_mode.py` | 🏁 Lazy-geladene Logik & Routen für die Spielmodi King of the Hill, Race (BLE-basiert) und Shooter (IR-basiert), von beiden Rollen genutzt. |
| `ir_emitter.py` & `ir_receiver.py` | 🔫 Treiber für Grove-Infrarot-Emitter (Senden) und IR-REC38-Empfänger (Empfangen) – NEC-Protokoll, siehe [🔫 Shooter-Hardware](#-shooter-hardware-anschliessen--testen). |
| `hotspot_common.py`, `hotspot.conf` & `wlan.conf` | 📡 WLAN-Access-Point-Konfiguration (`hotspot.conf`) sowie Ziel-WLAN für die GitHub-Update-Suche (`wlan.conf`). |
| `ota_helpers.py`, `upload_helpers.py`, `misc_routes_helpers.py`, `github_ota_helpers.py`, `update_manager.py` | 🛠️ OTA-, Upload- und "Nach Updates suchen"-Hilfsfunktionen für den Webserver. |
| `license_verifier.py`, `license.lic` & `public_key.pem` | 🔒 Offline-Lizenzprüfung: signierte, hardware-gebundene Freischaltung (siehe [Lizenzsystem](#-lizenzsystem)). |
| `challenge_helpers.py` | 🎮 Logik für die Real-Time Mini-Games (Touch & Go, Limbo, Eco). |
| `infection_mode.py` & `idcard_helpers.py` | ☣️ Logik für den Bluetooth-Infection-Modus und Spieler-Verwaltung. |
| `*.pak` Dateien (z.B. `en.pak`, `de.pak`) | 🌍 Sprachpakete für die Internationalisierung des Webinterfaces. |
| `index.html` | 📱 Hauptseite der Rolle "Gamification" (Scoreboard, Live-Feed, Historie, Downloads für Session/Debug). |
| `admin_dashboard.html`, `admin_update.html`, `admin_simulate.html`, `admin_profiles.html`, `admin_system.html`, `admin_challenges.html`, `admin_idcard.html`, `admin_infection.html`, `admin_credits.html`, `admin_koth.html`, `admin_race.html`, `admin_shooter.html` | 🎛️ Alle Admin-Unterseiten (Update, Simulation, System-Info, Challenges, Spielmodi, Credits, etc.). |
| `challenges_view.html`, `infection_view.html` & `gamemodes_view.html` | 📺 Öffentliche Live-Visualisierungen für Zuschauer. |
| `firmware_version.txt` & `version.json` | 🏷️ Versionstag (z.B. `1.3.3`). Wird bei jedem Release **automatisch** hochgezählt. Nicht manuell bearbeiten! |

*Zusätzliche Laufzeit-/Datendateien (legt das System selbst an):* `fpv_highscore.json`, `fpv_trick_settings.json`, `fpv_system_settings.json`, `koth.conf`, `race.conf`, `shooter.conf`, `shooter_log.json`, `boot_state.json`, Log-Dateien (`fpv_debug_session.txt`), Custom-Trick-Profile (`<name>.pro`), etc.

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

## 🔫 Shooter-Hardware anschliessen & testen

Für den **Shooter-Modus** (`/admin-shooter`) braucht **jeder** mitspielende Pico zusätzlich zwei kleine IR-Module: einen **Grove-Infrarot-Emitter** (Senden) und einen **IR-REC38-Empfänger** (Empfangen, wie er auch einzeln oder auf Grove-Empfänger-Modulen verbaut ist). Beide hängen an frei wählbaren GPIOs – Standard ist:

```text
+---------------------------+             +-----------------------+
| Grove Infrarot-Emitter    |             | Raspberry Pi Pico     |
|                            |             |                       |
|        GND  --------------->------------->  GND                  |
|        VCC  --------------->------------->  3V3 (oder 5V, siehe Modul) |
|        SIG  --------------->------------->  GP16 (PWM-Ausgang)    |
+---------------------------+             +-----------------------+

+---------------------------+             +-----------------------+
| IR-REC38 Empfänger        |             | Raspberry Pi Pico     |
|                            |             |                       |
|        GND  --------------->------------->  GND                  |
|        VCC  --------------->------------->  3V3                  |
|        OUT  --------------->------------->  GP17 (Interrupt-Eingang) |
+---------------------------+             +-----------------------+
```

> ⚠️ **Wichtig:**
> - Pin-Nummern sind Konstanten am Anfang von `source/ir_emitter.py` (`DEFAULT_IR_EMITTER_PIN`) bzw. `source/ir_receiver.py` (`DEFAULT_IR_RECEIVER_PIN`) – bei Bedarf dort anpassen, bevor die Firmware gebaut wird.
> - Der IR-REC38 braucht **3V3**, keine 5V!
> - Ohne angeschlossene Hardware meldet `/shooter-data` (bzw. die Hardware-Status-Zeile auf `/admin-shooter`) einfach `emitter_available: false` / `receiver_available: false` – kein Crash, der restliche Modus bleibt nutzbar.

### AUX-Kanal als automatischer Abzug (optional)

Statt (oder zusätzlich zu) dem Test-Knopf im Webinterface kann ein **AUX-Kanal** vom Sender als Abzug dienen: Auf `/admin-shooter` einfach die CRSF-Kanalnummer (1-16, z.B. ein 2-Positions-Schalter) und eine Schwelle (988-2011, Betaflight-übliche µs-Konvention, Mitte 1500) eintragen. Steht der Kanal über der Schwelle, feuert der Pico automatisch – solange der Schalter oben bleibt, im Takt der "Schuss-Abklingzeit" (Dauerfeuer-Gefühl).

Das setzt voraus, dass dein Flight Controller/ELRS-Aufbau `RC_CHANNELS_PACKED`-CRSF-Frames auf derselben Leitung sendet, die auch für das Attitude-/Telemetrie-Sniffen genutzt wird (bei den meisten ELRS-Setups mit echtem Halb-Duplex-Bus der Fall). Kommen keine Kanaldaten an, zeigt `/admin-shooter` unter "AUX-Abzug" **"Kein Signal"** – dann bleibt nur der manuelle Test-Knopf.

### Empfänger isoliert testen (2. "Test-Pico")

Um zu prüfen, ob der IR-REC38 überhaupt sauber empfängt, bevor man den kompletten Shooter-Modus in Betrieb nimmt, gibt es eine winzige Standalone-Firmware im Projekt-Root: **[`shooter_receiver_test_main.py`](shooter_receiver_test_main.py)**. Sie braucht **nur** ein IR-REC38-Modul (kein Emitter, kein Rest der Firmware) auf einem zweiten/beliebigen Pico:

1. `source/ir_receiver.py` **und** `shooter_receiver_test_main.py` auf den Test-Pico kopieren (z.B. per Thonny).
2. `shooter_receiver_test_main.py` dort in `main.py` umbenennen.
3. IR-REC38 wie oben verkabelt (Standard `GP17`), Pico neu starten.
4. **LED-Verhalten:**
   * Board-LED leuchtet **dauerhaft**, sobald der Empfänger erfolgreich erkannt wurde ("alles ok").
   * Blinkt die LED stattdessen **schnell dauerhaft**, wurde kein Empfänger erkannt (Pin/Verkabelung prüfen).
   * Bei jedem gültig empfangenen IR-Treffer (z.B. von einem "richtigen" Shooter-Pico, der abfeuert) blinkt die LED kurz **aus und wieder an** – zusätzlich wird jeder Treffer über die serielle Konsole (Thonny) protokolliert.

---

## 🚀 Quick Start & Konfiguration

### 0. Der einfachste Weg: Windows Installer
Für Endnutzer ohne Python-Setup gibt es den **Gamification Installer** (siehe [🖥️ Windows Installer](#-windows-installer--pc-tools)): Fertiges `.exe`, verbindet sich per USB mit dem Pico, kann die neueste Firmware direkt von GitHub laden und installieren – kein Thonny, kein manuelles Datei-Kopieren nötig. Alles danach ist der manuelle/technische Weg für Entwickler.

### 1. Erste Installation auf dem Pico (manuell)
1. Lade **alle oben gelisteten Dateien** via [Thonny](https://thonny.org/) (*Datei-Ansicht → Rechtsklick → Upload to /*) oder `ampy` auf deinen Pico – oder baue mit `tools/build_firmware.py` ein `firmware.nbo`-Bundle und installiere es in einem Rutsch über `/admin-update` bzw. seriell (siehe [🔒 Lizenzsystem](#-lizenzsystem)).
2. Starte den Pico neu (Hardware-Reset oder Strom weg und wieder dran).
3. Verbinde dich mit dem neuen WLAN-Hotspot:
   * **SSID:** `FPV_Gamification_Pico` *(Standard)*
   * **Passwort:** `drohnenspiel` *(Standard)*
   * **URL im Browser:** `http://192.168.4.1`
4. **Ersteinrichtung:** Ein frisch geflashter Pico kennt seine Rolle noch nicht - `role_setup.py` zeigt beim allerersten Start automatisch eine Auswahlseite: **🎮 Gamification Pico** (vom Piloten getragener Score-Tracker, startet `main.py`) oder **⛳ Gate/Hill Pico** (stationärer King-of-the-Hill-Hügel/Race-Tor, startet `main_gatehill.py`). Die Wahl wird dauerhaft in `device_role.json` gespeichert und der Pico startet danach automatisch neu - diese Seite erscheint ab dann nicht mehr. Über den Button **"Rolle zurücksetzen"** auf der jeweiligen System-Seite (`/admin-system` bzw. der System-Sektion von `index_gatehill.html`) lässt sich die Wahl jederzeit widerrufen; `device_role.json` wird von OTA-Updates nie angefasst.
5. **Lizenz aktivieren:** Ohne eine gültige, zu genau diesem Pico passende `license.lic` bleibt außer der System-Seite (`/admin-system`) alles gesperrt. Details und Vorgehen siehe [🔒 Lizenzsystem](#-lizenzsystem).

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

### ⛳ King of the Hill & 🏁 Race (Gate/Hill-Rolle, BLE)
Zwei zusätzliche Spielmodi, die **komplett unabhängig vom CRSF-Datenstrom** funktionieren – sie laufen auf stationären Picos in der Geräte-Rolle **Gate/Hill** (`main_gatehill.py`, `koth_mode.py`/`race_mode.py`, Admin-Seiten `/admin-koth` & `/admin-race`, öffentliche Anzeige `/gamemodes-view`) und nutzen dieselbe verbindungslose BLE-Advertise/Scan-Technik wie der Infection-Modus, mit eigenem Magic-Byte-Präfix, damit sich die Protokolle nicht in die Quere kommen:

* ⛳ **King of the Hill:** Ein Pico ist der "Hügel" (Rolle `hill`) und sendet einen BLE-Anker-Beacon. Spieler-Picos sammeln Punkte pro Sekunde, solange ihr Empfangspegel (RSSI) über einer konfigurierbaren Schwelle liegt. Jeder Spieler broadcastet zusätzlich seinen aktuellen Punktestand, sodass alle Geräte eine gemeinsame Bestenliste anzeigen können. Konfiguration in `koth.conf` (Rundendauer, RSSI-Schwelle, Punkte/Sekunde).
* 🏁 **Race:** Zwei Picos werden als feste Tore (Rolle `gate_a` / `gate_b`) aufgestellt und senden nur einen kurzen Identitäts-Beacon. Ein dritter Pico (Rolle `racer`) scannt fortlaufend: Kommt Tor A in Reichweite, startet der Rundentimer; kommt danach Tor B in Reichweite, wird die Runde abgeschlossen. Wiederholt sich über die konfigurierte Rundenzahl. Konfiguration in `race.conf` (Rundenzahl, RSSI-Schwelle, Cooldown zwischen Toren).

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
Statt alles einzeln hochzuladen, kannst du mit dem Skript `tools/build_firmware.py` auf deinem PC alle Dateien in eine Datei `firmware.nbo` packen (per GUI ohne Argument oder per Kommandozeile `python tools/build_firmware.py <output_path>`).
Lade diese `.nbo`-Datei hoch, und der Pico entpackt sie komplett serverseitig und aktualisiert alle HTMLs + Skripte in einem Rutsch (inkl. Backups)!
*(Im Bundle-Format versteckt sich ein robuster Header `FPVBNDL1`, der alles super sicher und MicroPython-freundlich macht).*

`build_firmware.py` kennt mehrere `--mode`-Varianten (die GitHub-Actions-Pipeline baut bei jedem Release automatisch die ersten drei):
* `normal` → `firmware.nbo`: Das normale OTA-Bundle für `/admin-update` bzw. Browser-Upload. Enthält **kein** `public_key.pem` (siehe `HTTP_OTA_BLOCKED_BUNDLE_FILES`) und erhöht dabei automatisch die Firmware-Versionsnummer.
* `complete` → `firmware-complete.nbo`: Enthält zusätzlich `public_key.pem` sowie MPY-kompilierten Quellcode (Schutz vor Quellcode-Einsicht) und wird **nur** seriell (USB, per `build_firmware.py`-GUI oder dem Windows Installer) installiert – niemals über die Browser-OTA-Seite.
* `recovery` → `firmware-recovery.nbo`: Schlankes Bundle, um nur den Recovery-Stack neu aufzuspielen.
* `bootmain` → `emergency.nbo`: Minimal-Notfallbundle für kaputte Boot-Ketten.
* `lang` → einzelnes Sprachpaket-Bundle.

**Nach GitHub suchen:** Über `/admin-system` kann der Pico auch selbst per WLAN (`wlan.conf`) kurz online gehen, die neueste GitHub-Release prüfen und `firmware.nbo` direkt installieren – ganz ohne PC.

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

## 🔒 Lizenzsystem

Die Firmware ist per RSA-Signatur an die Hardware-ID (`machine.unique_id()`) eines einzelnen Pico gekoppelt. Ohne eine gültige, passende `license.lic` bleibt ausschließlich `/admin-system` erreichbar (dort lässt sich auch `license.lic`/`public_key.pem` nachträglich hochladen) – alle anderen Seiten und Routen sind gesperrt, bis `source/license_verifier.py` die Signatur erfolgreich geprüft hat.

* **Kein Ablaufdatum:** Der Pico hat keine gepufferte RTC und synct nirgends per NTP, daher sind Lizenzen bewusst unbefristet – ein Ablaufdatum wäre ohne vertrauenswürdige Uhrzeit rein kosmetisch.
* **Reines Klartext-Format** (kein JSON, keine Kanonisierungs-Unschärfen für den winzigen On-Device-Parser):
  ```text
  hardware_id=<hex>
  customer_id=<Text>
  issued=<YYYY-MM-DD>
  ---SIGNATURE---
  <Base64 RSA-PKCS#1v1.5-SHA256-Signatur über genau die 3 Zeilen oben>
  ```
* **Verifikation direkt auf dem Pico** (`source/license_verifier.py`): reines Python + `hashlib`, RSA-Modexp per eingebautem `pow()`, PKCS#1-v1.5-Padding-Prüfung von Hand – MicroPython hat kein Krypto-Modul für RSA.

**Werkzeuge auf dem PC (alle in `tools/`, benötigen `cryptography`, siehe `requirements/requirements.txt`):**

| Tool | Zweck |
|---|---|
| `tools/license_generator.py` | 🔑 Kernfunktionen zur RSA-Schlüsselerzeugung/Signierung, werden von den anderen Tools importiert. Erzeugt bei Bedarf `keys/private_key.pem` (**bleibt ausschließlich auf dem PC!**) & `keys/public_key.pem`. |
| `tools/license_issuer.py` | 🧑‍💻 Eigenständiges GUI-Tool zum Ausstellen: Hardware-ID **manuell** eingeben (keine USB-Verbindung nötig), `license.lic` signieren. Ausgestellte Lizenzen werden zusätzlich im Archiv `lizenzen/` abgelegt. |
| `tools/license_uploader.py` | 📤 Eigenständiges GUI-Tool zum Installieren: spielt eine bereits ausgestellte `license.lic` + `public_key.pem` seriell auf einen angeschlossenen Pico – unabhängig und beliebig oft wiederholbar (z.B. nach einem Reflash). |
| `tools/build_firmware.py` (GUI) | 📦 Der Knopf "Komplette Firmware inkl. Lizenz bauen & installieren (seriell)" liest die Hardware-ID direkt vom verbundenen Pico, signiert passend, kompiliert den Quellcode per `mpy-cross` und überträgt alles inkl. Lizenz in einem Rutsch per USB. |

`keys/private_key.pem` und alles unter `lizenzen/` sind ausschließlich lokale Entwickler-/Verkäufer-Artefakte (per `.gitignore` **nicht** Teil des Repos) und dürfen das Projekt niemals in Richtung Endnutzer verlassen. Der öffentliche Schlüssel für den CI-Build (`firmware-complete.nbo`) liegt als GitHub-Actions-Secret `LICENSE_PUBLIC_KEY_PEM`.

---

## 🖥️ Windows Installer & PC-Tools

### Gamification Installer (`windows/`)
Eigenständiges Windows-Tool für Endnutzer (`windows/source/gamification_installer.py`, gebaut mit `windows/build_exe.py` via PyInstaller im `--onedir`-Modus zu `Gamification Installer.exe` + `.zip`):
* Findet einen per USB angeschlossenen Pico automatisch über alle COM-Ports (Raw-REPL-Ping).
* Lädt eine lokale `.nbo`/`lang.pak`-Datei per Datei-Dialog hoch **oder** holt direkt die neueste Firmware-Release von GitHub.
* Kann einen leeren/neuen Pico auch komplett neu mit MicroPython bespielen (nutzt die UF2-Bootloader-Dateien aus `picofw/`, die mit in den Installer gepackt werden – funktioniert also auch offline).
* Baubar über `windows/build.bat` (installiert Requirements & ruft `build_exe.py` auf) – Ergebnis unter `windows/dist/`.

### PC-Firmware-Simulator (`pico_simulator/`)
Startet die komplette Firmware-Logik (echter Webserver, echtes Admin-Panel) unter normalem CPython, simuliert dabei die benötigten MicroPython-Module (`machine`, `network`) und API-Unterschiede sowie Hardware-nahe RAM/CPU-Profile (`pico_w`, `pico2`) – ideal, um am Schreibtisch ohne angeschlossenen Pico zu entwickeln/testen:
```powershell
.venv\Scripts\python.exe pico_simulator\run_firmware.py --entry main --port 8080
```
Danach im Browser `http://127.0.0.1:8080/` bzw. `/admin`. Eigener GUI-Launcher inkl. Profilverwaltung: `pico_simulator\run_fmr_gui.py`. Details siehe [`pico_simulator/README.md`](pico_simulator/README.md).

### Weitere Helfer in `tools/`
* `tools/build_firmware.py` 📦 – siehe [OTA Updates](#-ota-updates-über-wifi-nie-wieder-kabel) & [Lizenzsystem](#-lizenzsystem).
* `tools/check_pico_storage.py` 💾 – Speicherplatz auf dem Pico prüfen.
* `tools/download_lilygo_files.py` / `tools/LilyGo.py` 📺 – Setup für das LilyGO T-Display Board (siehe [Datei-Übersicht](#-was-ist-wo-datei-übersicht)).
* `tools/mission_builder.py` 🗺️ – baut/kompiliert die Trick-Missionen aus `missionen/`.
* `tools/ota_checker.py` 🕵️‍♂️ – prüft den OTA-Status.
* `tools/profilemanager.py` 🎚️ – Trick-Profile lokal verwalten/erstellen.
* `tools/web_server.py` 🌐 – einfaches lokales Webserver-Mockup zum Testen am PC.

### Optional: Webshop (`webshop/`)
Ein eigenständiges Flask-Verkaufsportal (Stripe & PayPal Checkout) für den Vertrieb von Lizenzen/Hardware – läuft **nicht** auf dem Pico und ist unabhängig von der Firmware. Eigene Konventionen/Regeln siehe [`webshop/CLAUDE.md`](webshop/CLAUDE.md).

---

## 🌐 HTTP API Routes Overview

Hier ein kleiner Auszug, was unter der Haube schlummert:

| Route | Zweck |
|---|---|
| `/` | 📱 Webinterface Hauptseite (`index.html` bzw. `index_gatehill.html` je nach Geräte-Rolle) |
| `/data` | 📊 Live JSON Data (Score, Profil, Highscore, Historie) |
| `/system-info` | 💻 Live JSON (Speicher, Uptime, SSID, OTA-Status, Lizenzstatus) |
| `/challenges-data`| 🎮 Live JSON (Status aller 3 Challenges, Höhe/Batterie) |
| `/challenges-view` | 📺 Live Mini-Game Visualisierung |
| `/gamemodes-view` | 📺 Live-Visualisierung für King of the Hill & Race |
| `/admin` | 🎛️ Admin Dashboard |
| `/admin-update` | 🔄 Browser OTA Update (Einzeldateien oder `firmware.nbo`) |
| `/admin-simulate` | 🕹️ Trick Simulation |
| `/admin-profiles` | 🎚️ Profil-Tuning & Custom-Profile |
| `/admin-system` | 💻 System-Info, Developer Mode, Lizenz-Upload, Rollen-Reset & Restart *(einzige Route ohne gültige Lizenz erreichbar)* |
| `/admin-challenges` | 🎮 Challenge Manager |
| `/admin-infection` | ☣️ Infection-Modus Verwaltung |
| `/admin-koth` | ⛳ King-of-the-Hill Verwaltung (Gate/Hill-Rolle) |
| `/admin-race` | 🏁 Race Verwaltung (Gate/Hill-Rolle) |
| `/admin-shooter` | 🔫 Shooter-Verwaltung (IR-Emitter/Empfänger, AUX-Abzug, Trefferverlauf) |
| `/admin-credits` | 🙏 Credits-Seite |
| `/koth-*`, `/race-*`, `/shooter-*` | ⛳🏁🔫 Start/Stopp/Status der Spielmodi (z.B. `/koth-start`, `/race-data`, `/shooter-fire`) |
| `/infection-*`, `/lobby-*` | ☣️ Steuer-/Datenrouten des Infection-Modus |
| `/upload-chunk` / `/finalize-upload`| 📦 Firmware & HTML File Uploader |
| `/simulate-trick` | 🎲 Synthetic Gyro Trigger (`?type=roll\|flip\|spin`) |
| `/download`, `/download-debug` | 💾 Session/Debug TXT Export laden |

*(Zudem gibt es zig Unter-Routen wie `/set-trick-profile`, `/challenge-touchgo-start`, `/confirm-highscore`, `/confirm-license-thanks` etc., mit denen das Frontend mit dem Backend kommuniziert).*

---

## 🗂️ Was ist wo? (Datei-Übersicht)

Damit du dich in diesem Projekt-Dschungel zurechtfindest, hier ein kleiner Wegweiser, was die einzelnen Skripte und Ordner eigentlich machen:

### 🏠 Root-Verzeichnis (Das Hauptverzeichnis des Repos)
Im Root-Verzeichnis selbst liegen nur diese `README.md` sowie ein paar kleine PC-seitige Debug-Skripte (`test_github.py`, `test_modules.py`, `pico_webserver.py`), die eigenständige Test-Firmware [`shooter_receiver_test_main.py`](shooter_receiver_test_main.py) für einen zweiten "nur IR-Empfänger"-Test-Pico (siehe [🔫 Shooter-Hardware](#-shooter-hardware-anschliessen--testen)) und die folgenden Projektordner:

| Ordner | Zweck |
|---|---|
| `source/` | 📁 Alle Dateien, die tatsächlich *auf* den Pico müssen (siehe unten) – die Quelle der Wahrheit für Firmware-Builds. |
| `tools/` | 🛠️ PC-Helfer-Skripte (Build, Lizenzierung, Profile, LilyGO, ...) – siehe unten. |
| `pico_simulator/` | 🧪 CPython-Simulator der Firmware zum Testen am PC ohne Hardware (siehe [🖥️ Windows Installer & PC-Tools](#-windows-installer--pc-tools)). Klont `source` beim ersten Start schreibbar nach `data/`. |
| `windows/` | 🖥️ Quellcode & Build-Skript des eigenständigen Windows-Installers ("Gamification Installer.exe"). |
| `webshop/` | 🛒 Eigenständiger Flask-Webshop (Stripe/PayPal) zum Lizenzverkauf – unabhängig von der Firmware, eigene Regeln in `webshop/CLAUDE.md`. |
| `requirements/` | 📋 `requirements.txt` + Installer-Skript für alle PC-seitigen Python-Abhängigkeiten der `tools/`-Skripte. |
| `keys/` | 🔑 Lokales RSA-Schlüsselpaar für das Lizenzsystem (`private_key.pem` niemals einchecken/weitergeben!). Per `.gitignore` nicht im Repo. |
| `lizenzen/` | 🗄️ Lokales Archiv aller von `tools/license_issuer.py` ausgestellten `.lic`/`.json`-Lizenzdatensätze. Per `.gitignore` nicht im Repo. |
| `picofw/` | 💽 UF2-Bootloader-Dateien (MicroPython-Firmware + "Nuke"-Löschtool) für frisches Flashen eines rohen Pico/Pico 2 W im BOOTSEL-Modus; werden vom Windows Installer mitgepackt. |
| `missionen/` | 🗺️ `.mission`-Dateien mit vordefinierten Trick-Kombos (von `tools/mission_builder.py` gebaut, von `challenge_helpers.py`/`/missions-list` genutzt). |
| `build/` | 🏗️ Ablage der von `tools/build_firmware.py` gebauten Bundles (`firmware.nbo`, `firmware-complete.nbo`, `firmware-recovery.nbo`, `emergency.nbo`) inkl. `.last_bundle_manifest.json`. Build-Output, nicht Teil des Quellcodes. |
| `data/`, `data2/` | 🧪 Schreibbare Testbereiche des `pico_simulator/` (Klon von `source` + zur Laufzeit erzeugte Dateien wie `boot_state.json`, `fpv_debug_session.txt`). |
| `FPV_LilyGO/` | 📺 Laufzeit-/Log-Dateien aus Tests mit dem LilyGO-T-Display-Aufbau. |
| `.github/workflows/` | ⚙️ CI/CD-Pipeline (`build-and-release-firmware.yml`) – siehe [🚀 CI/CD: Automatische Builds & Releases](#-cicd-automatische-builds--releases). |

### 🛠️ Der `tools`-Ordner (Helfer-Skripte fuer den PC)
* `tools/build_firmware.py` 📦: Das ist dein Helfer-Tool auf dem PC! Es verpackt alle Dateien aus dem `source`-Ordner in Bundle-Dateien (`--mode normal/complete/recovery/bootmain/lang`, siehe [OTA Updates](#-ota-updates-über-wifi-nie-wieder-kabel)) und enthält (per GUI) zusätzlich das komplette Offline-Lizenzsystem.
* `tools/license_generator.py`, `tools/license_issuer.py`, `tools/license_uploader.py` 🔒: Das Lizenz-Werkzeugkasten-Trio – Schlüsselerzeugung, Ausstellen (offline, ohne Pico) und Installieren (seriell, mit Pico). Details siehe [🔒 Lizenzsystem](#-lizenzsystem).
* `tools/check_pico_storage.py` 💾: Ein Tool, um den Speicherplatz auf deinem Pico zu checken.
* `tools/download_lilygo_files.py` ⬇️: Lädt spezifische Dateien herunter, die für das LilyGO T-Display Setup (mit Display) gebraucht werden.
* `tools/LilyGo.py` 📺: Eine spezielle Hauptdatei, wenn du das Projekt nicht auf einem normalen Pico, sondern auf einem LilyGO T-Display Board mit schickem Display laufen lässt.
* `tools/mission_builder.py` 🗺️: Baut und kompiliert Flug-Missionen (Ablage in `missionen/`).
* `tools/ota_checker.py` 🕵️‍♂️: Prüft den OTA-Status.
* `tools/profilemanager.py` 🎚️: Verwalte und erstelle deine Trick-Profile lokal.
* `tools/web_server.py` 🌐: Ein lokales Mockup des Webservers zum Testen am PC.
* `tools/ideen.txt` 💡: Die Schmiede! Hier werden neue Features und irre Ideen gesammelt.

### 📁 Der `source`-Ordner (Das Herzstück für den Pico)
Hier liegen alle Dateien, die tatsächlich *auf* deinen Pico müssen (oder vom Build-Skript eingepackt werden):

* **Python-Kernskripte:**
  * `main.py` 🚀: Die absolute Boss-Datei fuer die Geraete-Rolle "gamification". Startet den ganzen Zirkus (Webserver, Telemetrie, Tricks).
  * `main_gatehill.py` ⛳: Die Boss-Datei fuer die Geraete-Rolle "gatehill" (stationaerer King-of-the-Hill-Huegel/Race-Tor) - liegt neben `main.py` im selben Ordner, `boot.py` waehlt anhand der gespeicherten Rolle eines von beiden.
  * `gmr.py`, `koth_mode.py`, `race_mode.py` 🏁: Gemeinsame, lazy geladene Spielmodi-Logik (Routing, Admin-Seiten, BLE-Tasks) für King of the Hill & Race - von `main.py` UND `main_gatehill.py` genutzt.
  * `role_setup.py` 🧭: Die Ersteinrichtungs-Seite - laeuft NUR beim allerersten Start, solange noch keine Geraete-Rolle gewaehlt wurde (siehe Quick-Start-Abschnitt oben).
  * `boot.py` & `boot_runtime.py` 🥾: MicroPython startet diese Dateien beim Booten. Sie entscheiden, ob die Ersteinrichtung, der Notfall-Recovery-Modus oder `main.py`/`main_gatehill.py` geladen wird.
  * `ota_helpers.py`, `github_ota_helpers.py`, `upload_helpers.py`, `misc_routes_helpers.py`, `update_manager.py` 🛠️: Wichtige Helferlein für OTA-Updates (lokal & per GitHub-Suche), Datei-Uploads und spezielle Web-Routen, ausgelagert um RAM zu sparen.
  * `license_verifier.py` 🔒: Prüft `license.lic` gegen `public_key.pem` und die Hardware-ID des Geräts (siehe [🔒 Lizenzsystem](#-lizenzsystem)).
  * `hotspot_common.py`, `hotspot.conf` & `wlan.conf` 📡: Alles rund um den WLAN-Access-Point (`hotspot.conf`) und das WLAN-Zielnetz fuer die GitHub-Update-Suche (`wlan.conf`).
  * `recovery.py` 🚑: Das Notfall-Skript. Startet einen minimalen OTA-Server, wenn alles andere brennt - funktioniert unabhaengig von der Geraete-Rolle.
* **Spielmodi & Challenges:**
  * `challenge_helpers.py` 🎮: Die Logik hinter den Real-Time Mini-Games (Limbo, Eco, Touch & Go).
  * `infection_mode.py` / `.mpy` ☣️: Der Code für den gnadenlosen Bluetooth Infection-Modus (das `.mpy` ist kompiliert für mehr Speed & RAM).
  * `idcard_helpers.py` 🪪: Helfer für die Verwaltung von Spieler-IDs im Infection-Modus.
* **Web-Oberfläche (Die HTML-Seiten):**
  * `index.html`: Das Main-Dashboard für Piloten (Geraete-Rolle "gamification").
  * `index_gatehill.html`: Die kombinierte Konfigurationsseite für die Geraete-Rolle "gatehill" (King of the Hill & Race).
  * `admin_dashboard.html`, `admin_update.html`, `admin_simulate.html`, `admin_profiles.html`, `admin_system.html`, `admin_challenges.html`, `admin_idcard.html`, `admin_infection.html`, `admin_credits.html`, `admin_koth.html`, `admin_race.html`: Alle Kontrollzentren im Backend.
  * `challenges_view.html`, `infection_view.html` & `gamemodes_view.html` 📺: Die hübschen, öffentlichen Ansichten für Zuschauer.
* **Sprachpakete (.pak):**
  * `de.pak`, `en.pak`, `es.pak`, `fr.pak`, `it.pak`, `pt.pak`, `tr.pak` 🌍: Internationalisierung, Baby! Übersetzungsdateien für das Webinterface.
* `firmware_version.txt` & `version.json` 🏷️: Hier merkt sich das System, auf welcher Version du fliegst. Wird von der CI/CD-Pipeline automatisch hochgezählt und zurück ins Repo committet.

---

## 🚀 CI/CD: Automatische Builds & Releases

Der Workflow `.github/workflows/build-and-release-firmware.yml` läuft bei jedem Push auf `main`, der `source/**`, `tools/build_firmware.py` oder `windows/**` betrifft (sowie manuell per "Run workflow"):

1. **Firmware-Bundles bauen:** `firmware.nbo`, `firmware-complete.nbo`, `firmware-recovery.nbo` und `emergency.nbo` werden per `tools/build_firmware.py` gebaut. Für `firmware-complete.nbo` wird `keys/public_key.pem` aus dem Repo-Secret `LICENSE_PUBLIC_KEY_PEM` bereitgestellt (der private Schlüssel bleibt außerhalb von CI).
2. **Versionsnummer:** Wird beim ersten Bundle automatisch erhöht (`source/version.json`/`firmware_version.txt`, `X.Y.Z` → `X.Y.Z+1`) und mit `[skip ci]` zurück ins Repo committet.
3. **Windows-Installer bauen:** In einem zweiten, parallelen Job (`windows-latest`) wird `windows/build_exe.py` ausgeführt und `Gamification Installer.zip` erzeugt.
4. **Veröffentlichung:** Alle vier Firmware-Bundles + der Windows-Installer landen als Download-Artefakte des Workflow-Laufs (90 Tage gültig) **und** als Anhänge einer automatisch erstellten GitHub Release mit Tag `v<version>`.

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
* **Nur `/admin-system` erreichbar, alles andere blockiert?**
  Das ist das Lizenzsystem: Es fehlt eine gültige `license.lic` (oder `public_key.pem`) für die Hardware-ID dieses Pico. Über `/admin-system` lässt sich beides nachträglich hochladen – siehe [🔒 Lizenzsystem](#-lizenzsystem).
* **Windows Installer findet den Pico nicht?**
  Prüfe, ob der Pico wirklich über USB (nicht nur Stromversorgung) verbunden ist und kein anderes Programm (z.B. Thonny) bereits den seriellen Port belegt.

---

## 📂 Repository & Links

* 🐙 **GitHub Repository:** [Devilwitha/FPV_Gamification_Pico](https://github.com/Devilwitha/FPV_Gamification_Pico)
* 📦 **Releases:** Fertige `firmware.nbo`-Bundles und der `Gamification Installer.zip` (Windows) werden automatisch bei jedem Release veröffentlicht – siehe [GitHub Releases](https://github.com/Devilwitha/FPV_Gamification_Pico/releases).
* 💡 **Build & Release Pipeline:** `.github/workflows/build-and-release-firmware.yml` baut bei jedem Push auf `main` (der Firmware/Installer betrifft) automatisch alle Bundle-Varianten + den Windows-Installer und zählt die Versionsnummer (`version.json`) hoch – siehe [🚀 CI/CD: Automatische Builds & Releases](#-cicd-automatische-builds--releases).

---
*Guten Flug und möge der Highscore mit dir sein! 🛸💨*