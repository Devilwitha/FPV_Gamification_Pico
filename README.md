# 🎮 FPV Gamification Pico

Anpassungsfähiger **FPV Score-Tracker**, **Infection-Game** & **Mini-Game Server** auf einem Raspberry Pi Pico / Pico W mit passivem CRSF-Readout.

Das Skript klinkt sich passiv in deine Telemetrie ein, liest Attitude-Daten (Roll/Pitch/Yaw) sowie Sensorik, erkennt Stunts/Tricks, vergibt Highscores und hostet eine schicke Live-Web-App direkt aus der Hosentasche! **Keine zusätzlichen Sensoren oder Kabel nötig** – einfach 1x UART abgreifen und losfliegen! 🥳

---

## ⚡ Features Highlights

* 🛰️ **Passiver CRSF-UART Sniffer:** Klinkt sich geräuschlos in deine ELRS/TBS-Telemetrie ein (Attitude, Vario & Akku-Frames).
* 🎯 **Smart Trick-Erkennung:** Automatische Erkennung von Flips, Rolls & Spins mit dynamischer Punktevergabe.
* ☣️ **Infection-Modus (BLE):** Erkennt Mitspieler über verbindungslose Bluetooth-LE Advertisements direkt in der Luft!
* 🏆 **Live Highscore-System:** Inklusive Piloten-Eingabe im Browser, Bestätigungs-Popup bei neuem Rekord und LED-Blink-Party!
* 📡 **Eigener WLAN-Hotspot:** Keine App-Installation nötig – einfach per Smartphone/Tablet im Feld verbinden.
* 🕹️ **Real-Time Mini-Games (Challenges):**
  * 🛬 **Touch & Go:** Belohnt Butterlandungen (ausgewertet über Sinkrate & Gyro-Aufprall).
  * 🎯 **Altitude-Hold / Limbo:** Fliege präzise auf Höhe oder bleibe unter der Limbo-Grenzlinie.
  * 🔋 **Eco-Challenge:** Wer fliegt am sparsamsten? Punkte basieren auf verbrauchten mAh.
* 🎮 **Trick-Simulation:** Teste das Punkte- & Erkennungssystem im Admin-Panel ganz ohne Drohne.
* 🚀 **OTA & Bundle Updates:** Komplette Firmware-Updates (`firmware.nbo`) oder einzelne HTML/Python-Dateien kabellos direkt im Browser aktualisieren.
* ⚙️ **Mehrstufiges Admin-Panel:** Dashboard, Trick-Profile (`.pro`), OTA-Update, Mini-Games & System-Monitor.

---

## 📂 Benötigte Dateien auf dem Pico

Damit das Webinterface flüssig und ohne RAM-Engpässe läuft (der Pico hat nicht viel davon!), werden die HTML-Dateien in kleinen Häppchen direkt vom Dateisystem gestreamt. Es müssen **alle** folgenden Dateien im Hauptverzeichnis des Pico liegen:

| Datei | Zweck |
|---|---|
| `main.py` | 🚀 Hauptskript (Bootdatei, startet automatisch beim Einschalten) |
| `ota_helpers.py` | 🛠️ OTA- & Kodier-Hilfsfunktionen (**Pflicht**, sonst gibt's einen `ImportError`!) |
| `index.html` | 📱 Hauptseite (Scoreboard, Live-Feed, Downloads für Session/Debug, Challenges-Link) |
| `admin_dashboard.html` | 🎛️ Admin-Startseite & Navigation zu allen Settings |
| `admin_update.html` | 🔄 Browser OTA-Update Interface |
| `admin_simulate.html` | 🕹️ Virtuelle Trick-Simulation (Rolls & Flips am PC testen) |
| `admin_profiles.html` | 🎚️ Profil-Tuning & `.pro` Manager |
| `admin_system.html` | 💻 System-Info, Developer-Modus & Neustart-Steuerung |
| `admin_challenges.html` | 🎮 Mini-Games/Challenges Regie (Versuche starten/stoppen) |
| `challenges_view.html` | 📺 Öffentliche Live-Visualisierung für Zuschauer/Piloten |
| `firmware_version.txt` | 🏷️ Versionstag (`X.Y.Z` - wird automatisch generiert, Finger weg! 😉) |

*Zusätzliche Dateien (werden automatisch erstellt):* `fpv_highscore.json`, `fpv_trick_settings.json`, Log-Dateien, etc.

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

> ⚠️ **Wichtig:** Niemals 5V direkt auf den `3V3`-Pin legen! Auf gemeinsame Masse (`GND`) achten, sonst gibt es Daten-Salat. Aktuelle UART-Konfiguration: RX ist auf **GP1**.

---

## 🚀 Quick Start & Konfiguration

### 1. Ersteinrichtung
1. Lade **alle oben gelisteten Dateien** via [Thonny](https://thonny.org/) (*Datei-Ansicht → Rechtsklick → Upload to /*) oder `ampy` auf deinen Pico.
2. Starte den Pico neu (Hardware-Reset oder Strom weg und wieder dran).
3. Verbinde dich mit dem neuen WLAN-Hotspot:
   * **SSID:** `FPV_Gamification_Pico` *(Standard)*
   * **Passwort:** `drohnenspiel` *(Standard)*
   * **URL im Browser:** `http://192.168.4.1`

### 2. Wichtige Konfigurationen
* **Hotspot-Einstellungen (`source/hotspot.conf`):**
  Wenn du den Namen des WLANs ändern willst, pass diese Datei an:
  ```json
  {
     "ssid": "FPV_Gamification_Pico",
     "password": "drohnenspiel"
  }
  ```
* **Infection-Modus (`infection.conf` & `infection_players.conf`):**
  Steuert Rundendauer, Startrolle, RSSI-Nähe und Immunität. Der Infection-Modus nutzt Bluetooth LE Advertisements im Hintergrund, während der Access Point aktiv bleibt!
* **Globale Variablen (`main.py`):**
  Du kannst direkt in `main.py` Dinge wie `COPTER_NAME`, `DEFAULT_PILOT_NAME` oder das Standard-Trick-Profil einstellen. (Tipp: Wenn du den Copter-Namen anpasst, ändere ihn auch in der `index.html`!).

---

## 🎮 Spielmodi & Real-Time Challenges

### ☣️ Infection-Modus (BLE)
Erkennt Mitspieler über verbindungslose Bluetooth-LE-Advertisements in der Luft! Über RSSI-Distanzmessung wird ausgewertet, wer wen infiziert hat. Spannende Dogfights sind garantiert!

### 🕹️ Real-Time Mini-Games (`/admin-challenges`)
```text
 🛬 TOUCH & GO            🎯 LIMBO / HOLD          🔋 ECO-CHALLENGE
 -----------------        ----------------        ------------------
 Sanftes Aufsetzen        Höhe halten             Maximaler Fly-Time
 misst Sinkrate & Gyro    unter Zeitdruck          Punkte pro mAh
```

1. **Öffentlicher Screen (`/challenges-view`):** Wirf den Screen auf ein Tablet an der Startline – fette Balken & pulsierende Live-Punkte inklusive!
2. **Admin-Regie (`/admin-challenges`):** Startet/stoppt Versuche, stellt Höhengrenzen, Dauer und Toleranzen ein.
*(Achtung: Dein FC muss CRSF Vario- oder Battery-Sensor-Telemetrie senden, damit Limbo und Eco funktionieren!)*

---

## 🎚️ Trick-Tuning-Profile

Das System unterstützt vordefinierte und eigene Tuning-Profile für die Stunt-Erkennung, speichert sie und lädt sie beim nächsten Start (`fpv_trick_settings.json`):

* 🟢 **`beginner`:** Erkennt leichter, gut für sanfte Manöver (kann eher zu False Positives neigen).
* 🟡 **`freestyle`:** Der perfekte Allrounder für normale Sessions.
* 🔴 **`aggressive`:** Strengere Erkennung gegen Fehltrigger bei schnellen, wilden Bewegungen. Startet standardmäßig in diesem Modus.
* 🛠️ **Eigene Custom-Profile (`.pro`):** Können im Admin-Bereich (`/admin-profiles`) ganz einfach per Webinterface erstellt, heruntergeladen und verwaltet werden.

### ⚙️ Feinjustierung der Trick-Erkennung
Willst du es ganz genau wissen? Du kannst Werte wie `GYRO_TRICK_THRESHOLD`, `STABLE_THRESHOLD`, `MIN_TRICK_DURATION` oder `GYRO_LOWPASS_ALPHA` anpassen (global in `main.py` oder pro Profil).

---

## 🚦 LED Status Signalisation

| Status | LED-Verhalten |
|---|---|
| 🟢 **Bereit** | Dauerhaft AN (WLAN & Server aktiv) |
| 🥳 **New Highscore!** | Langsames Blinken (wartet auf Piloten-Name im Browser) |
| ⚡ **OTA-Update** | Schnelles Blinken (Übertragung läuft, bitte warten!) |

---

## 🔄 OTA & Bundle Updates (Updates ohne Kabel!)

Du kannst `main.py` und alle HTML-Seiten direkt über den Browser aktualisieren!

1. Ab in den Admin-Bereich: **Update** (`/admin-update`).
2. Wähle die aktualisierte Datei aus.
   * Lädst du eine `.py` hoch, wird sie zu `main.py`.
   * Lädst du eine passende `.html` hoch, wird genau diese ersetzt.
3. Klick auf **📤 Upload**. Der Pico macht automatisch ein Backup (z.B. `main_backup.py`) und speichert die neue Version. Bei `main.py` startet er direkt neu!

📦 **Tipp: Das Firmware-Bundle (`firmware.nbo`)!**
Mit dem Python-Skript `build_firmware.py` auf deinem PC kannst du alle Dateien in ein einziges Bundle packen. Lade dann einfach die `firmware.nbo` über das OTA-Interface hoch und der Pico aktualisiert *alle* Dateien in einem Rutsch!

*(Hinweis: Auf der Admin-Unterseite System (`/admin-system`) kannst du den **Developer-Modus** aktivieren, um auch einzelne Dateien außerhalb von Bundles hochzuladen.)*

---

## 🚑 Notfall: Recovery-Modus (`recovery.py`)

Hast du deine `main.py` geschrottet oder eine ur-alte Version drauf? Kein Problem!
Lade `recovery.py` via Thonny hoch und starte den Pico neu. Er startet nun im Recovery-Modus (selbes WLAN, selbes Passwort). Rufe `http://192.168.4.1` auf, lade ein frisches `firmware.nbo`-Bundle hoch und boom – deine Drohne lebt wieder! 🧟‍♂️

---

## 🌐 HTTP API Routes Overview

Hier ein kleiner Auszug, was unter der Haube schlummert:

| Route | Zweck |
|---|---|
| `/` | 📱 Webinterface Hauptseite (`index.html`) |
| `/data` | 📊 Live JSON Data (Score, Profil, Highscore) |
| `/challenges-view` | 📺 Live Mini-Game Visualisierung |
| `/admin` | 🎛️ Admin Dashboard |
| `/admin-update` | 🔄 Browser OTA Update (Einzeldateien oder `firmware.nbo`) |
| `/admin-simulate` | 🕹️ Trick Simulation |
| `/admin-profiles` | 🎚️ Profil-Tuning & Custom-Profile |
| `/admin-system` | 💻 System-Info, Developer Mode & Restart |
| `/admin-challenges` | 🎮 Challenge Manager |
| `/upload-chunk` | 📦 Firmware & HTML File Uploader |
| `/simulate-trick` | 🎲 Synthetic Gyro Trigger (`?type=roll\|flip\|spin`) |
| `/download-session` | 💾 Session TXT Export laden |

---

## 🛠️ Troubleshooting

* **Kein WLAN sichtbar:** Prüfe `ENABLE_HOTSPOT = True` in `main.py` und die seriellen Konsole-Logs.
* **Webseite erreichbar, aber keine Live-Daten:**
  * Ist CRSF TX wirklich auf **GP1** am Pico angeschlossen?
  * Haben FC und Pico eine gemeinsame Masse (**GND**)?
  * Sendet der FC wirklich CRSF-Attitude Frames? (Betaflight Ports / Serial RX prüfen).
* **`MemoryError` beim Booten:** Vergewissere dich, dass du das Skript als `main.py` auf den Pico hochlädst und per Hardware-Reset startest (statt "Run current script" in Thonny zu drücken). Thonny sendet sonst das Skript als fetten String in den knappen RAM.
* **Download funktioniert nicht stabil?** Einfach Tab neu laden. Die Exporte werden in kleinen 512-Byte-Häppchen gestreamt, um den RAM zu schonen!

---

## 📂 Repository & Links

* 🐙 **GitHub Repository:** [Devilwitha/FPV_Gamification_Pico](https://github.com/Devilwitha/FPV_Gamification_Pico)
* 💡 **Build & Release Pipelines:** Das Repo nutzt GitHub Actions, um Releases automatisch zu builden und Versionstags (`version.json`) hochzuzählen.

---
*Guten Flug und möge der Highscore mit dir sein! 🛸💨*