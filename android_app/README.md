# 📱 FPV Gamification – Android App

Eigenstaendiges Android-Projekt (Kotlin, Gradle), das sich **automatisch mit dem
Pico-WLAN-Hotspot verbindet** und die komplette Web-Oberflaeche des Pico anzeigt –
Toolbar, Pull-to-Refresh, Ladebalken, Fehler-/Retry-Screen, Dark-Theme.

**Alle Seiten laufen dabei nativ aus der App** (lokal gebuendelt in `assets/pico/`,
siehe [🧩 Architektur](#-architektur-lokale-seiten--live-daten-vom-pico)) –
vom Pico kommen nur noch die Live-**Daten** (Scores, System-Status, Konfiguration
usw.), keine Seiten-Downloads mehr bei jeder Navigation. **Datei-Uploads** (z.B.
`firmware.nbo`-Bundles, Trick-Profile, ID-Karten-Bilder ueber `/admin-update` bzw.
`/admin-profiles`) funktionieren dabei ganz normal per nativem Datei-Picker.

Kein Android Studio noetig: Ein PowerShell-Skript installiert alles Benoetigte
(JDK, Android SDK) und baut die APK ueber den mitgelieferten Gradle-Wrapper.
Der gleiche Wrapper laeuft auch in der GitHub-Actions-Pipeline, die die APK bei
jedem Release automatisch mitbaut (siehe [🚀 CI/CD](#-cicd)).

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

Gradle selbst kommt ueber den mitgelieferten Wrapper (`gradlew.bat`) – der laedt
beim ersten Aufruf automatisch die passende Version in den globalen Gradle-Cache,
genau wie bei jedem anderen Android-Projekt.

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

Alternativ: die fertig gebaute APK aus dem [GitHub Release](../../releases) laden
und direkt auf dem Handy installieren ("Installation aus unbekannten Quellen"
fuer den verwendeten Browser/Dateimanager erlauben).

---

## 🧩 Architektur: lokale Seiten + Live-Daten vom Pico

Die 15 bekannten Pico-Seiten (`index.html`, alle `admin_*.html`, `challenges_view.html`,
`infection_view.html`, `gamemodes_view.html`) liegen 1:1 als Kopie aus `source/`
in `app/src/main/assets/pico/`. `PicoWebViewClient.kt` haengt sich in
`shouldInterceptRequest()` ein:

* **Seiten-Navigation** (z.B. Tap auf "Admin" → `/admin`) → wird lokal aus dem
  App-Bundle bedient (`assets/pico/admin_dashboard.html`), kein Netzwerk-Roundtrip
  noetig. Macht die Oberflaeche spuerbar schneller/robuster als der RAM-limitierte
  Webserver auf dem Pico.
* **Alles andere** (jedes `fetch()`/`XMLHttpRequest` der Seiten selbst – `/data`,
  `/system-info`, `/challenges-data`, `/upload-chunk`, `/set-trick-profile`, ...)
  wird **nicht** abgefangen und geht ganz normal live an den Pico raus. Da die
  Seiten weiterhin unter der Origin `http://192.168.4.1` laufen (nur die Antwort-
  Bytes fuer bekannte Seiten-Pfade werden lokal statt vom Pico geliefert), gibt es
  dabei weder CORS-Handling noch sonstige Sonderfaelle noetig – die Seiten selbst
  merken den Unterschied nicht.

Das bedeutet konkret: **jede Aenderung an einer `.html`-Datei in `source/` muss
manuell nach `android_app/app/src/main/assets/pico/` kopiert werden**, damit die
App sie mitbekommt (kein automatischer Sync). Reine Backend-/Datenänderungen
(`.py`-Dateien) betreffen die App dagegen gar nicht, da die nur die JSON-Antworten
konsumiert.

### 📤 Datei-Uploads

`<input type="file">` funktioniert in einer WebView standardmaessig nicht – dafuer
implementiert `MainActivity.kt` `onShowFileChooser()` und startet den nativen
Android-Datei-Picker. Die Upload-Seiten (`admin_update.html`, `admin_profiles.html`,
`admin_idcard.html`, ...) senden die gewaehlte Datei danach genau wie im Desktop-
Browser per Chunked-Upload (`/prepare-upload` → `/upload-chunk` → `/finalize-upload`)
live an den Pico.

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

## 🚀 CI/CD

`.github/workflows/build-and-release-firmware.yml` baut den Job `build-android-app`
bei jedem Push auf `main`, der `android_app/**` (oder `source/**`, `windows/**`,
`tools/build_firmware.py`) betrifft:

1. JDK 17 + Android SDK per GitHub Action einrichten (`actions/setup-java`,
   `android-actions/setup-android`).
2. `./gradlew assembleDebug` (Debug-signiert mit dem Standard-Gradle-Debug-
   Keystore, da kein eigener Signing-Keystore hinterlegt ist – dadurch bleibt
   die APK direkt installierbar, ohne dass jemand sie selbst signieren muss).
3. Ergebnis als `FPV-Gamification-App.apk` an dieselbe GitHub Release angehaengt,
   die auch `firmware.nbo` & Co. sowie `Gamification Installer.zip` bekommt –
   und zusaetzlich als 90 Tage gueltiges Workflow-Artefakt hochgeladen.

---

## 🗂️ Projektstruktur

```
android_app/
├── setup_and_build.ps1   # installiert JDK/SDK & baut die APK ueber den Wrapper
├── install_apk.ps1       # installiert die APK per adb auf einem Geraet
├── gradlew / gradlew.bat / gradle/wrapper/   # Gradle-Wrapper (auch von CI genutzt)
├── settings.gradle / build.gradle / gradle.properties
└── app/
    ├── build.gradle
    └── src/main/
        ├── AndroidManifest.xml
        ├── assets/pico/               # 1:1-Kopien der 15 Pico-HTML-Seiten
        ├── java/com/fpv/gamification/app/
        │   ├── MainActivity.kt        # Toolbar, WebView, Datei-Picker, Statusoverlay
        │   ├── PicoWebViewClient.kt   # lokale Seiten-Huelle + Live-Daten-Passthrough
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
* **Neue Seiteninhalte auf dem Pico erscheinen nicht in der App:** Erwartetes
  Verhalten (siehe [🧩 Architektur](#-architektur-lokale-seiten--live-daten-vom-pico))
  – die betroffene `.html`-Datei aus `source/` nach `assets/pico/` kopieren und
  die App neu bauen.
* **Datei-Upload passiert nichts nach Tippen auf "Datei auswaehlen":** Prueft, ob
  eine Datei-Manager-App auf dem Handy installiert ist (`ACTION_GET_CONTENT`
  braucht einen passenden System-Picker, ist aber auf praktisch jedem Geraet ab
  Werk vorhanden).
