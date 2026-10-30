# Propwash FPV-Simulator – Offline-Version

Ein FPV-Simulator, der komplett im Browser läuft. Er braucht keine Installation und kein Internet.

## Starten

1. ZIP entpacken.
2. `propwash-fpv-simulator.html` per Doppelklick öffnen, am besten in **Chrome** oder **Edge**.
   Firefox läuft auch, erkennt aber manche Fernsteuerungen schlechter.
3. Auf **Fliegen** klicken.

Mit Internet lädt die Seite zusätzlich die Schriftarten von Google Fonts.
Ohne Internet nimmt sie die Systemschriften, sonst ändert sich nichts.

## Fernsteuerung anschließen

- **EdgeTX / OpenTX** (z. B. RadioMaster TX15, TX16S, Boxer): Sender per USB anschließen
  und „USB Joystick (HID)“ wählen. Standard-Zuordnung ist AETR: Kanal 1 Roll, 2 Pitch,
  3 Gas, 4 Yaw, 5 Arm, 6 Angle-Modus, 7 Turtle.
- **Gamepad** (Xbox, PlayStation): Linker Stick Gas/Yaw, rechter Stick Pitch/Roll,
  Start = Arm, Y = Angle-Modus, X = Turtle, Select = Reset.
- Im Menü unter **Steuerung** jede Funktion mit „Lernen“ neu zuordnen und danach
  **Kalibrieren** (alle Sticks zweimal voll umkreisen, dann mittig loslassen).

Ohne Controller geht die Tastatur:

| Taste | Funktion |
|---|---|
| W / S | Gas hoch / runter |
| A / D | Yaw |
| Pfeiltasten | Pitch und Roll |
| Leertaste | Arm / Disarm |
| E | Angle- / Acro-Modus |
| T | Turtle-Modus (vor dem Armen einschalten) |
| R | Reset zum Startplatz |
| C | Kamera: FPV, Verfolger, Sichtflug |
| G | Blackbox-Graph |
| Esc | Menü |

## Was simuliert wird

- Physik mit 2000 Schritten pro Sekunde: Starrkörper mit Trägheit, Kreiseleffekt der Propeller, Kollisionen.
- BLDC-Motoren mit Trägheit, Strombegrenzung im Regler und LiPo- bzw. Li-Ion-Akku mit Spannungseinbruch.
- Propeller-Aerodynamik: Schub sinkt mit der Fluggeschwindigkeit, Bodeneffekt, Propwash im eigenen Abwind.
- Flight Controller wie Betaflight: Actual-, Betaflight- und Quick-Rates, PID mit Feedforward,
  I-Term-Relax, TPA, Airmode, Gyro-Filter und RPM-Filter.
- Fisheye-FPV-Kamera mit Uptilt, Analog- oder HD-Videolink mit Signalverlust hinter Hindernissen,
  OSD im Betaflight-Stil.
- Rennstrecke mit 9 Gates, Rundenzeiten und Ghost der besten Runde.

Einstellungen und Bestzeiten speichert der Browser lokal.

## Dateien

| Datei | Inhalt |
|---|---|
| `propwash-fpv-simulator.html` | Der Simulator. Mehr braucht es zum Fliegen nicht. |
| `LIESMICH.md` | Diese Anleitung |
| `LICENSE-three.js.txt` | Lizenz der 3D-Bibliothek three.js (MIT), die in der HTML-Datei steckt |
| `quellcode/` | Quellen zum Anpassen: `sim-core.js` (Physik und Flight Controller), `simulator.src.html` (Grafik und Bedienung), `build.py` (setzt alles zur fertigen Datei zusammen) |

## Selbst anpassen

`quellcode/` enthält alles, was zum Bauen nötig ist. Nach einer Änderung im Ordner `quellcode/`
`python build.py` ausführen (Python 3.8 oder neuer). Die neue Offline-Datei liegt danach in
`quellcode/offline/`.

Der Physik-Kern `sim-core.js` hängt nicht von Grafik oder Browser ab. Er lässt sich auch mit
Node.js testen:

```
node -e "require('./sim-core.js'); const q = new FPVCore.Quad({ rho: 1.225 }); q.step({ arm: true, thr: 0.3, roll: 0, pitch: 0, yaw: 0 }); console.log(q.pos)"
```

Mehr Werkzeuge rund um FPV gibt es auf https://www.bollisoft.ch/
