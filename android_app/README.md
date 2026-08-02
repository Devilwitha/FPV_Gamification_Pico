# 📱 FPV Gamification – Android App

Eigenstaendiges Android-Projekt (Kotlin, Gradle), das sich **automatisch mit dem
Pico-WLAN-Hotspot verbindet** und die Web-Oberflaeche des Pico (`http://192.168.4.1/`)
in einer schlanken, modernen WebView-Huelle anzeigt (Toolbar, Pull-to-Refresh,
Ladebalken, Fehler-/Retry-Screen, Dark-Theme).

Kein Android Studio noetig: Ein PowerShell-Skript installiert alles Benoetigte
(JDK, Android SDK, Gradle – alles portabel unterhalb dieses Ordners) und baut die APK.

---

## 🚀 Schnellstart (Windows / PowerShell)

```powershell
cd android_app
.\setup_and_build.ps1
```

Das Skript installiert beim ersten Lauf automatisch:

* **JDK 17** (via `winget`, falls kein passendes JDK gefunden wird)
* **Android SDK Command-Line Tools**, `platform-tools`, `platforms;android-34`,
  `build-tools;34.0.0` – portabel unter `android_app\.android-sdk\`
* **Gradle 8.9** – portabel unter `android_app\.gradle-dist\` (kein globales
  Gradle/Android Studio noetig, kein Wrapper-Jar im Repo)

Danach wird die APK gebaut: `app\build\outputs\apk\debug\app-debug.apk`.

Release-Build:

```powershell
.\setup_and_build.ps1 -Release
```

> Erster Lauf laedt SDK + Gradle herunter (mehrere hundert MB, Internet noetig).
> Folgeaufrufe sind deutlich schneller, da alles bereits lokal liegt.
> Mit `-SkipSdkInstall` kann der SDK-Download uebersprungen werden, wenn bereits
> ein vollstaendiges `ANDROID_HOME` vorhanden ist.

---

## 📲 APK auf dem Handy installieren

Handy per USB anschliessen, **USB-Debugging** in den Android-Entwickleroptionen
aktivieren, danach:

```powershell
.\install_apk.ps1
```

Das Skript findet die zuletzt gebaute APK automatisch, installiert sie per `adb`
und startet die App direkt. Nuetzliche Optionen:

```powershell
.\install_apk.ps1 -Uninstall              # vorherige Version zuerst entfernen
.\install_apk.ps1 -NoLaunch               # App nach Install nicht automatisch oeffnen
.\install_apk.ps1 -Apk pfad\zu\datei.apk  # bestimmte APK installieren
.\install_apk.ps1 -ConnectWifi 192.168.1.42:5555   # WLAN-ADB statt USB
```

(WLAN-ADB muss vorher einmal per USB mit `adb tcpip 5555` aktiviert werden.)

---

## 📡 Wie sich die App mit dem Pico verbindet

* SSID/Passwort/URL stehen in `app/src/main/res/values/strings.xml`
  (`hotspot_ssid`, `hotspot_password`, `webapp_base_url`) und entsprechen den
  Standardwerten aus `source/hotspot.conf` (`FPV_Gamification_Pico` /
  `drohnenspiel`, feste AP-IP `192.168.4.1`).
* **Android 10+ (API 29+):** Verbindung ueber `WifiNetworkSpecifier` –
  die App fordert das Zielnetz direkt beim System an und bindet ihren
  Netzwerkverkehr daran (`ConnectivityManager.bindProcessToNetwork`), da das
  Pico-WLAN kein Internet hat und sonst vom System sofort wieder verlassen wird.
  Keine Standortberechtigung noetig.
* **Android 8–9 (API 26–28):** Fallback ueber die klassische
  `WifiConfiguration`-API (dafuer wird zur Laufzeit einmalig
  `ACCESS_FINE_LOCATION` angefragt – Systemvoraussetzung dieser API).
* Schlaegt die automatische Verbindung fehl (Pico aus, falsches Passwort, o.ae.),
  zeigt die App einen Fehlerbildschirm mit **"Erneut versuchen"** und
  **"WLAN-Einstellungen öffnen"** an.

**Eigene SSID/Passwort konfiguriert?** Falls `source/hotspot.conf` auf dem Pico
angepasst wurde, einfach `hotspot_ssid` / `hotspot_password` in `strings.xml`
entsprechend anpassen und neu bauen.

---

## 🗂️ Projektstruktur

```
android_app/
├── setup_and_build.ps1   # installiert JDK/SDK/Gradle & baut die APK
├── install_apk.ps1       # installiert die APK per adb auf einem Geraet
├── settings.gradle / build.gradle / gradle.properties
└── app/
    ├── build.gradle
    └── src/main/
        ├── AndroidManifest.xml
        ├── java/com/fpv/gamification/app/
        │   ├── MainActivity.kt        # Toolbar, WebView, Statusoverlay
        │   └── HotspotConnector.kt    # Auto-WLAN-Verbindung zum Pico
        └── res/                       # Layout, Strings, Farben, Theme, Icon
```

---

## 🛠️ Troubleshooting

* **"Kein einsatzbereites Geraet gefunden"** (bei `install_apk.ps1`): USB-Debugging
  aktivieren (Einstellungen → Über das Telefon → 7× auf Build-Nummer tippen →
  Entwickleroptionen → USB-Debugging), Kabel/Treiber pruefen, RSA-Dialog auf dem
  Handy bestaetigen.
* **Build schlaegt mit Lizenzfehler fehl:** `setup_and_build.ps1` erneut
  ausfuehren – die Lizenzen werden automatisch akzeptiert; bei Google-seitigen
  Aenderungen ggf. manuell mit `.android-sdk\cmdline-tools\latest\bin\sdkmanager.bat --licenses`.
* **App zeigt "Hotspot nicht erreichbar":** Pico eingeschaltet & Hotspot aktiv?
  SSID/Passwort in `strings.xml` korrekt? Bei manuell schon verbundenem WLAN
  einfach auf "Erneut versuchen" tippen.
