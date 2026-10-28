// Fertige Lua-Skripte von Bollisoft für das Lua Studio (Typ "Bibliothek").
// Die Skripte liegen in diesem Ordner so, wie sie auf die SD-Karte gehören (path).
// Vorschaubilder (vorschau/<id>.png) entstehen aus den echten Zeichenbefehlen der Skripte
// in einer nachgebauten EdgeTX-Umgebung.
window.LUA_BIBLIOTHEK = [
  {
    id: 'elrs-finder',
    name: 'ELRS Finder',
    kind: 'widget',
    group: 'Recovery & Lost Model',
    path: 'WIDGETS/ELRSFIND/main.lua',
    preview: 'vorschau/elrs-finder.png',
    summary: 'Signal-Tracker wie ein Geigerzähler: findet den Quad im hohen Gras.',
    desc: [
      'Liest die Empfangsstärke des Quads (1RSS/2RSS in dBm, dazu RQly), glättet sie und zeigt sie als Balken mit Spitzenwert.',
      'Im Vollbild piept es wie ein Geigerzähler: je stärker das Signal, desto schneller und höher. HEISS heisst: du kommst näher, KALT: du entfernst dich.',
    ],
    setup: [
      'Im ExpressLRS-Lua die Sendeleistung fest auf 10–25 mW stellen und Dynamic Power ausschalten. Mit voller Leistung ist das Signal überall stark.',
      'Widget auf einer Hauptansicht einrichten und zur Suche ins Vollbild schalten – nur dort piept es.',
      'Langsam im Kreis drehen und dorthin gehen, wo es schneller und höher piept. Den Sender vor den Körper halten.',
    ],
    options: [
      ['Ton', 'Pieptöne im Vollbild ein oder aus'],
      ['MinDbm', 'Signal, bei dem es am langsamsten piept (Standard −110 dBm)'],
      ['MaxDbm', 'Signal, bei dem es fast dauernd piept (Standard −40 dBm)'],
    ],
    needs: ['ExpressLRS-Empfänger mit Telemetrie', 'Quad mit angestecktem Akku'],
  },
  {
    id: 'gps-qr',
    name: 'GPS QR',
    kind: 'widget',
    group: 'Recovery & Lost Model',
    path: 'WIDGETS/GPSQR/main.lua',
    preview: 'vorschau/gps-qr.png',
    summary: 'Friert die letzte GPS-Position ein und zeigt sie als QR-Code für die Karten-App.',
    desc: [
      'Merkt sich laufend die letzte gültige Position (GPS, Höhe, Tempo, Satelliten). Reisst der Link ab oder ist der Akku leer, friert die Anzeige diese Position ein, vibriert kurz und speichert sie auf der SD-Karte – auch ein Neustart des Senders verliert sie nicht.',
      'Das Widget zeichnet einen QR-Code direkt aufs Display. Handy-Kamera drauf halten, und die Position öffnet sich in Google Maps oder Apple Maps.',
    ],
    setup: [
      'Widget auf einer Hauptansicht in einem grossen Feld einrichten (z. B. Vollbreite). Im kleinen Feld steht die Position als Text.',
      'Vor dem Flug warten, bis LIVE und genug Satelliten angezeigt werden.',
      'Nach einem Absturz: Widget öffnen, QR-Code mit dem Handy scannen und zur Position laufen. Dort hilft der ELRS Finder weiter.',
    ],
    options: [['AppleMaps', 'QR-Code öffnet Apple Maps statt Google Maps']],
    needs: ['GPS am Flugcontroller', 'Telemetrie über CRSF/ExpressLRS oder FrSky'],
  },
  {
    id: 'zellen',
    name: 'Zellen',
    kind: 'widget',
    group: 'Live-Telemetrie & Akku',
    path: 'WIDGETS/ZELLEN/main.lua',
    preview: 'vorschau/zellen.png',
    summary: 'Zellenspannung mit 1S–8S-Auto-Erkennung, Sag bei Punchouts und Restkapazität.',
    desc: [
      'Liest die Akkuspannung (RxBt bei ExpressLRS, sonst VFAS oder eine gewählte Quelle) und erkennt die Zellenzahl beim Einstecken selbst.',
      'Zeigt die Spannung pro Zelle, den Sag (Einbruch gegenüber der Ruhespannung) live und als Höchstwert, die tiefste Zelle und die Restkapazität aus dem Stromsensor, aus Bat% oder geschätzt aus der Spannung. Optional sagt es eine tiefe Ruhespannung an.',
    ],
    setup: [
      'Widget auf einer Hauptansicht einrichten.',
      'Für die Restanzeige in mAh die Akkukapazität in der Option Kapaz eintragen.',
      'Bei sehr leeren Akkus kann die Auto-Erkennung eine Zelle zu wenig ergeben – dann die Zellenzahl in der Option Zellen fest einstellen.',
    ],
    options: [
      ['Quelle', 'Spannungssensor; leer = RxBt, sonst VFAS'],
      ['Zellen', '0 = automatisch, sonst feste Zellenzahl'],
      ['Kapaz', 'Akkukapazität in mAh für die Restanzeige'],
      ['Alarm', 'Ansage unter x/10 V Ruhespannung pro Zelle (0 = aus)'],
      ['LiHV', 'Hochvolt-Akkus mit 4,35 V pro Zelle'],
    ],
    needs: ['Spannungssensor am Flugcontroller', 'Für mAh: Stromsensor (Capa)'],
  },
  {
    id: 'link-health',
    name: 'Link Health',
    kind: 'widget',
    group: 'Live-Telemetrie & Akku',
    path: 'WIDGETS/LINKMON/main.lua',
    preview: 'vorschau/link-health.png',
    summary: 'ExpressLRS-Funkstatus: LQ, RSSI je Antenne, SNR, Leistung, Paketrate und Warnungen.',
    desc: [
      'Zeigt Link-Qualität, RSSI beider Empfänger-Antennen, SNR, die gerade gesendete Leistung (Dynamic Power) und die Paketrate, dazu den LQ-Verlauf der letzten Minute und die Tiefstwerte der Sitzung.',
      'Warnt früh, bevor der Failsafe greift: wenn eine Antenne dauerhaft deutlich schwächer ist (beschädigt oder schlecht verlegt), wenn LQ oder RSSI tief werden oder der Sender schon auf voller Leistung arbeitet.',
    ],
    setup: [
      'Widget auf einer Hauptansicht einrichten.',
      'In der Option MaxPwr die höchste im ELRS-Lua eingestellte Leistung eintragen.',
      'Im Vollbild setzt Antippen die Sitzungswerte zurück.',
    ],
    options: [
      ['RssiWarn', 'RSSI-Warnschwelle in dBm'],
      ['LqWarn', 'LQ-Warnschwelle in %'],
      ['MaxPwr', 'höchste eingestellte Sendeleistung in mW'],
    ],
    needs: ['ExpressLRS 3.x mit Telemetrie', 'Für den Antennen-Vergleich: Empfänger mit Diversity'],
  },
  {
    id: 'fs-scorer',
    name: 'FS Scorer',
    kind: 'widget',
    group: 'Gamification & Session',
    path: 'WIDGETS/FSSCORE/main.lua',
    preview: 'vorschau/fs-scorer.png',
    summary: 'Freestyle-Punkte am Sender und ein Session-Debrief nach dem Disarm.',
    desc: [
      'Rechnet aus der Lage-Telemetrie von Betaflight (Ptch, Roll, Yaw) Drehraten, erkennt Flips, Rollen und Yaw-Spins und zählt die Flugzeit. Nach dem Disarm erscheint das Debrief mit Score, höchster Flip-Rate und Flugzeit; jede Session landet zusätzlich in /LOGS/fsscore.csv.',
      'Die Telemetrie kommt viel seltener als die Blackbox, die Werte sind deshalb Schätzungen. Für die genaue Wertung gibt es den Blackbox Viewer mit Leaderboard und Duellen. G-Kräfte sendet Betaflight über CRSF nicht.',
    ],
    setup: [
      'Widget auf einer Hauptansicht einrichten.',
      'In der Option Arm den Arm-Schalter wählen. Ohne Schalter zählt der Telemetrie-Link als armed.',
      'Fliegen, disarmen, Debrief lesen. Antippen im Vollbild schliesst es.',
    ],
    options: [
      ['Arm', 'Arm-Schalter'],
      ['Umkehren', 'Schalter armt in der unteren Stellung'],
      ['Ansage', 'Punkte nach dem Disarm ansagen'],
    ],
    needs: ['Betaflight mit CRSF-Telemetrie (Lage)', 'ExpressLRS-Telemetrie-Rate 1:8 oder schneller empfohlen'],
  },
  {
    id: 'tlmlog',
    name: 'Telemetrie-Logger',
    kind: 'func',
    group: 'Gamification & Session',
    path: 'SCRIPTS/FUNCTIONS/tlmlog.lua',
    preview: null,
    summary: 'Schreibt während des Flugs eine kleine CSV-Datei – auslesen ohne Blackbox-Kabel.',
    desc: [
      'Solange der Schalter aktiv ist, schreibt das Skript fünf Zeilen pro Sekunde nach /LOGS/TLM_<Datum>_<Zeit>.csv: Akku, Strom, Verbrauch, Link-Werte, Höhe, Tempo, GPS-Position, Lage und Gas.',
      'Die Datei liest du später per USB am PC aus. Die Spalten Lat und Lon lassen sich direkt in Kartendienste laden, zum Beispiel als Heatmap. Fehlt ein Sensor, bleibt seine Spalte leer.',
    ],
    setup: [
      'MDL → Spezialfunktionen öffnen.',
      'Einen Schalter wählen, am besten den Arm-Schalter.',
      'Als Aktion Lua-Skript und dann tlmlog wählen. Ein kurzer Piepton bestätigt Start und Ende.',
    ],
    options: [],
    needs: ['Ordner /LOGS auf der SD-Karte (gibt es bei EdgeTX standardmässig)'],
  },
  {
    id: 'tilt',
    name: 'Neige-Aktionen',
    kind: 'mix',
    group: 'TX15-Gimmicks',
    path: 'SCRIPTS/MIXES/tilt.lua',
    preview: null,
    summary: 'Nutzt das Gyro der TX15: Sender kippen löst Ansagen aus oder steuert eine Kamera.',
    desc: [
      'Wertet die Neigung des Senders aus und liefert zwei neue Quellen für den Mischer: Cam ist die geglättete Neigung, etwa für den Kamerawinkel im FPV-Simulator. Kipp springt auf +100 %, solange der Sender über die Schwelle gekippt ist – damit lassen sich logische Schalter und Spezialfunktionen auslösen.',
      'Mit Ansage = 1 sagt der Sender beim Nach-vorne-Kippen Akkuspannung und LQ an.',
    ],
    setup: [
      'MDL → Eigene Skripte öffnen, einen freien Platz wählen und tilt auswählen.',
      'Bei Neig die Neigungsachse des Sender-Gyros als Quelle wählen.',
      'LUA1 Cam bzw. LUA1 Kipp im Mischer oder in logischen Schaltern als Quelle verwenden.',
    ],
    options: [
      ['Neig', 'Neigungsachse des Gyros'],
      ['Grenze', 'ab so viel % Neigung gilt der Sender als gekippt'],
      ['Glatt', 'Glättung der Cam-Ausgabe in %'],
      ['Ansage', '1 = beim Kippen Akku und LQ ansagen'],
      ['Umkehr', '1 = Kipprichtung umdrehen'],
    ],
    needs: ['Sender mit eingebautem Gyro (TX15)', 'EdgeTX mit Lua-Mischer-Skripten'],
  },
  {
    id: 'vtx-switch',
    name: 'VTX & Profile',
    kind: 'tool',
    group: 'TX15-Gimmicks',
    path: 'SCRIPTS/TOOLS/VTXSwitch.lua',
    preview: 'vorschau/vtx-switch.png',
    summary: 'VTX-Band, Kanal, Leistung und Betaflight-Profile per Touch umschalten.',
    desc: [
      'Liest die aktuelle VTX-Einstellung vom Flugcontroller und stellt Band, Kanal, Leistung und Pit-Mode über Knöpfe auf dem Touchscreen um – ohne Stick-Kombos im OSD. Die Leistungsstufen tragen die Namen aus der VTX-Tabelle. Dazu wechseln Knöpfe direkt das PID- oder Rate-Profil.',
      'Die Befehle gehen als MSP über CRSF zum Flugcontroller, wie beim offiziellen Betaflight-Lua-Skript. Kanäle ausserhalb von 5725–5875 MHz (in der Schweiz und der EU mit höchstens 25 mW erlaubt) zeigt das Tool orange an.',
    ],
    setup: [
      'SYS → Tools öffnen und VTX & Profile starten.',
      'Band, Kanal und Leistung antippen, dann Senden. Betaflight speichert nur, wenn der Quad disarmed ist.',
      'Mit dem Drehrad und ENTER geht es auch ohne Touch. RTN beendet das Tool.',
    ],
    options: [],
    needs: ['Betaflight 4.x mit VTX-Tabelle', 'ExpressLRS mit Telemetrie, Quad mit Akku verbunden'],
  },
];

// Forumsthema zur Bibliothek (Fragen, Rückmeldungen)
window.LUA_BIBLIOTHEK_FORUM = '/forum/thema/10';
