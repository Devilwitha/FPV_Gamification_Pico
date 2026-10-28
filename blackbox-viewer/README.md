# Blackbox Viewer

Auswertung nach dem Flug: Der Blackbox Viewer liest das Blackbox-Log eines Betaflight-Copters, erkennt
darin automatisch Freestyle-Tricks, vergibt Punkte und legt ein HUD-Overlay über das Flugvideo.
Gute Flüge lassen sich mit Video ins Leaderboard auf bollisoft.ch einreichen.

Live: <https://www.bollisoft.ch/blackbox-viewer/> · Rangliste: <https://www.bollisoft.ch/leaderboard>

Alles läuft im Browser – Logs und Videos verlassen den PC nur beim Einreichen ins Leaderboard.

## Ablauf

1. **Log öffnen** – `.bbl`/`.bfl` direkt aus Betaflight, eine CSV aus `blackbox_decode`/Blackbox Explorer
   oder per USB: „Flight Controller per USB“ liest den Onboard-Flash über Web Serial + MSP aus (Chrome/Edge).
   Enthält eine Datei mehrere Logs (mehrmals scharfgeschaltet), lässt sich jedes einzeln wählen.
2. **Video dazulegen (optional)** – Flugvideo per Drag & Drop. Ohne Video zeigt die Bühne eine synthetische
   FPV-Ansicht aus der berechneten Lage.
3. **Synchronisieren** – automatisch (Motorgeräusch der Tonspur ↔ Motorwerte im Log), über einen Anker
   (Gas-Stoß, Arm-Piepser) oder von Hand per Versatz.
4. **Tricks ansehen** – Trickliste mit Punkten, Timeline mit farbigen Tricks, HUD mit Trick-Popups,
   Fortschrittsring, Combo-Anzeige und Stick-Positionen.
5. **Speichern / Einreichen** – „⬇ Video mit Overlay“ speichert den Flug in Originalauflösung (bis 4K) als MP4.
   „Einreichen“ (nur eingeloggt, keine Demo-Flüge) nimmt den Flug mit Overlay platzsparend auf (720p) und lädt
   Video, Log und Trickliste ins Leaderboard.

Ein **Demo-Flug** (synthetisch, 92 s) zeigt alle Tricks, ohne dass ein eigenes Log nötig ist.

## So funktioniert die Trick-Erkennung

Grundlage sind die **Drehraten des Gyros** (Grad pro Sekunde je Achse Roll, Pitch, Yaw), das **Gas** und –
falls geloggt – der **Beschleunigungssensor**. Code: [`src/analysis/tricks.ts`](src/analysis/tricks.ts).

1. **Aufbereiten** – Das Log wird auf ~500 Hz gebracht. Die Gyro-Werte werden ohne Zeitversatz geglättet
   (Tiefpass, Standard 15 Hz) und zu Drehwinkeln aufsummiert. Aus Gyro und Beschleunigungssensor
   berechnet ein Lagefilter (Mahony) zusätzlich, wie der Copter im Raum liegt
   ([`src/analysis/orientation.ts`](src/analysis/orientation.ts)).
2. **Rotationen finden** – Je Achse wird nach Abschnitten gesucht, in denen der Copter in eine Richtung dreht:
   Start, wenn die Rate über 300 °/s steigt, Ende, wenn sie unter 60 °/s fällt. Der aufsummierte Winkel sagt,
   wie weit er sich gedreht hat.
3. **Einordnen** – Volle Umdrehungen (360° minus Toleranz) werden je nach Achse, Dauer und Gas zum Trick;
   mehrere Umdrehungen am Stück werden zu Double/Triple/Quad. Halbe Drehungen (~180°) kurz nacheinander
   ergeben Split-S oder Immelmann. Gleichzeitige Drehungen auf zwei Achsen ergeben Corkscrew bzw. Helix.
4. **Lage-Tricks** – Mit Beschleunigungssensor im Log kommen Tricks dazu, die von der Lage abhängen
   (kopfüber, Nase nach unten). Ohne Sensor werden sie bewusst nicht geraten.
5. **Punkte** – Basispunkte je Trick × Anzahl Umdrehungen × Stil (Drehrate bzw. Gas bzw. Haltedauer)
   × Sauberkeit (wie wenig die anderen Achsen mitdrehen). Folgt der nächste Trick innerhalb von 1,5 s,
   steigt der **Combo-Multiplikator** (+0,25 je Stufe, höchstens ×3).
   **Wiederholungen** zählen weniger: Derselbe Trick direkt noch einmal (kein anderer Trick dazwischen,
   höchstens 5 s Pause) gibt halbe Punkte, der dritte ein Viertel, danach ein Achtel. In einer Combo zählen
   höchstens zwei gleiche Tricks hintereinander – der dritte gleiche beginnt eine neue Combo.

| Trick | Erkennung | Basispunkte |
| --- | --- | --- |
| Roll | 360° um die Roll-Achse | 100 |
| Flip | 360° um die Pitch-Achse, schnell (vorwärts/rückwärts) | 120 |
| Power Loop | langsamer Pitch-Loop mit viel Gas, nach hinten gezogen | 250 |
| Matty Flip | wie Power Loop, aber nach vorne gedrückt | 260 |
| Split-S | halbe Rolle, dann halber Loop | 200 |
| Immelmann | halber Loop, dann halbe Rolle | 200 |
| Yaw Spin | schnelle 360°-Drehung um die Hochachse | 80 |
| Trippy Spin | Yaw Spin kopfüber *(Beschleunigungssensor)* | 220 |
| Corkscrew | Rolle und Flip gleichzeitig | 320 |
| Helix | Rolle und Yaw-Drehung gleichzeitig | 300 |
| Orbit | langsame 360°-Drehung mit Schräglage um ein Objekt | 60 |
| Inverted Hang | mindestens 0,8 s kopfüber gehalten *(Beschleunigungssensor)* | 140 |
| Dive | mindestens 0,7 s Nase nach unten mit wenig Gas *(Beschleunigungssensor)* | 160 |

Alle Schwellwerte lassen sich im Tab **Erkennung** anpassen; die Auswertung läuft sofort neu.

**Lernfähig (optional):** Falsch erkannte Tricks lassen sich in der Trickliste per Dropdown korrigieren,
auch verworfene Kandidaten. Jede Korrektur wird als Beispiel gespeichert; ein k-Nearest-Neighbour-Modell
([`src/analysis/ml.ts`](src/analysis/ml.ts)) ordnet damit alle Kandidaten neu ein (Tab **ML-Training**,
Export/Import als JSON).

**Trick markieren & Community-Modell:** Wurde ein Trick gar nicht erkannt, markiert man mit **„✎ Trick markieren“**
an der Timeline den Bereich (Ziehen oder „Anfang/Ende = Position“) und wählt den Trick – oder beantragt einen
neuen. Mit Konto und geladenem Flugvideo (Pflicht – ohne Video bleibt die Markierung lokal) wird die Markierung samt Merkmalsvektor und einem kurzen Video-Ausschnitt (Bereich ±1 s,
720p) an den Webshop gesendet (`webshop/trick_labels.py`, `/api/tricks`). Der Admin prüft den Ausschnitt im
Admin-Dashboard (Tab **Trick-Markierungen**), gibt ihn frei – auf Wunsch als anderer Trick – oder lehnt ab;
ein beantragter Trick erscheint nach der Freigabe für alle in der Auswahlliste (Schlüssel `c<id>`, Name und
Punkte legt der Admin fest). Freigegebene Markierungen sind die Trainingsdaten des **Community-Modells**
([`src/analysis/learned.ts`](src/analysis/learned.ts)): Der Viewer lädt sie über `/api/tricks/katalog` und sucht
in jedem Flug mit gleitenden Fenstern der Beispiel-Dauer (±10 %) nach Stellen mit ähnlichen Merkmalen
(Umdrehungen je Achse, Dauer, Gas, Spitzenrate, Reihenfolge). Treffer füllen nur Lücken, die die Regeln
lassen, und tragen das Abzeichen „Community“. Markierte Bereiche selbst geben keine Punkte – erst das
freigegebene Modell. Spannen ohne echte Drehung (Hängen, Sturzflug) trainieren nur das lokale Modell,
weil sie sich mit diesen Merkmalen nicht von normalem Fliegen unterscheiden lassen.

## Betaflight richtig einstellen

Im Viewer unter dem Tab **„Betaflight einrichten“** (oder `…/blackbox-viewer/#betaflight`) – Kurzfassung:

- Logging-Gerät SD-Karte, wenn vorhanden; Logging-Rate **500 Hz** (oder 250 Hz für längere Aufnahmen) –
  mehr nutzt der Viewer nicht.
- Debug-Modus `NONE`.
- Felder **Gyro, Beschleunigungssensor, RC-Befehle, Motoren, Akku** an lassen (Akku: Zellenzahl fürs
  Leaderboard); PID, Setpoint, ungefiltertes Gyro, Debug, Motor-RPM, Höhe, Magnetometer, GPS, RSSI
  ausschalten (Betaflight 4.3+).
- Vor dem Flug den Flash löschen.

## Leaderboard

Einreichen geht nur mit Bollisoft-Konto. Gespeichert werden ein platzsparendes Video mit Overlay
(720p, 2,5 Mbit/s), das Log und die Trickliste. Andere Piloten können Einträge melden; ab 10 Meldungen
bekommt der Admin eine E-Mail. Die API dazu stellt der Webshop bereit (`webshop/leaderboard.py`,
`/blackbox-viewer/api/leaderboard` wird per IIS-Regel dorthin geleitet). Die eigene Seite
`/leaderboard` zeigt die Rangliste weltweit, nach Ländern und nach **Akku-Klasse** (1S, 2S, 3S, 4S, 6S, 8S).
Die Klasse schätzt der Viewer aus der Akkuspannung am Log-Anfang (`vbatLatest`, Klasse mit plausibler
Zellenspannung 3,2–4,45 V, am nächsten an 3,95 V); vor dem Einreichen lässt sie sich korrigieren und muss
gewählt sein, wenn das Log keine Spannung enthält.

Jeder Flug kommt nur einmal ins Leaderboard: Der Server speichert Fingerabdrücke (SHA-256) der Log-Datei
samt Log-Nummer, der Flugdaten (Gyro und Gas mit 50 Hz, grob gerundet – erkennt denselben Flug auch als CSV)
und des Original-Videos (Größe, erste und letzte 2 MB). Stimmt einer davon mit einem bestehenden Eintrag
überein (egal von welchem Konto), wird die Einreichung abgelehnt. Der Viewer fragt das vor der Aufnahme
ab (`/api/leaderboard/pruefen`), damit niemand umsonst auf das Video wartet; ein gelöschter Eintrag gibt
den Flug wieder frei.

Ohne Webshop (lokal) führt der eingebaute Node-Server ein einfaches Leaderboard ohne Videos in
`data/leaderboard.json` ([`server/leaderboard.mjs`](server/leaderboard.mjs)); Einreichen mit Video braucht den Webshop.
Das Meldungs-Limit lässt sich mit `LEADERBOARD_REPORT_THRESHOLD` in `webshop/.env` ändern.

## Entwicklung

Voraussetzung: Node.js ≥ 22.6.

```bash
npm install
npm run dev      # Vite-Entwicklungsserver
npm test         # Trick-Erkennung am Demo-Flug + .bbl-Decoder
npm run build    # Typprüfung (tsc) + Build nach dist/
npm start        # Produktionsserver (server/server.mjs) für dist/
```

`server/server.mjs` liest `PORT` (Standard 8787), `HOST` und `BASE_PATH` (z. B. `/blackbox-viewer` hinter
einem Reverse Proxy). Auf bollisoft.ch startet `webshop/serve_production.py` den Server mit
`BASE_PATH=/blackbox-viewer` auf `127.0.0.1:8787`.

| Ordner | Inhalt |
| --- | --- |
| `src/core/` | Gemeinsamer Kern für Einzel- und VS-Viewer: `FlightPlayer` (Wiedergabe, Video-Sync mit Drift, Analyse, Rendering), `LogDecoder` (Worker), `match.ts` (Duell-Wertung: Abheben, Wertungsfenster, Live-Score) |
| `src/vs.ts`, `vs.html` | VS-Viewer: zwei `FlightPlayer` nebeneinander auf einer gemeinsamen Duell-Uhr |
| `src/decoder/` | `.bbl`-Decoder, CSV-Import, USB/MSP-Download |
| `src/pipeline/` | Spalten-Zuordnung, Resampling, Lageberechnung |
| `src/analysis/` | Filter, Lage, Trick-Erkennung, ML-Klassifizierung |
| `src/sync/` | Audio ↔ Motor-Synchronisation |
| `src/ui/` | HUD, Timeline, synthetische Ansicht, Leaderboard |
| `src/demo/` | Synthetischer Demo-Flug |
| `server/` | Produktionsserver und lokales Leaderboard |
| `test/` | Tests (`npm test`) |
