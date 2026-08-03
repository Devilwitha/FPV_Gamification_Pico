# Eigene Plugins für den FPV Gamification Pico

Diese Anleitung erklärt, wie das Plugin-System (`source/plugin_manager.py`)
funktioniert und wie man ein eigenes Mod baut, testet, verpackt und verteilt.

## Ordnerstruktur

Jedes Plugin ist ein Ordner unter `source/mods/<name>/` mit mindestens zwei
Dateien:

```
source/mods/<name>/
├── manifest.json      # Metadaten + Konfiguration (siehe unten)
└── main.py            # Lifecycle-Hooks (setup/loop/teardown/...)
```

Weitere Dateien sind erlaubt und werden einfach mit ausgeliefert - z.B.
eigene Hardware-Treiber als Untermodule oder eine eigene `.html`-Seite.
Siehe die beiden mitgelieferten Referenz-Beispiele:

- **`source/mods/example_plugin/`** - minimal: nur ein Heartbeat-Zähler
  (`setup`/`loop`/`teardown`) und eine kleine Erweiterung des "System"-Tabs
  (`ui_slots`).
- **`source/mods/shooter/`** - vollständig: der komplette Infrarot-
  "Laser-Tag"-Spielmodus als Plugin. Eigene Spiellogik (`ShooterMode`),
  eigene Hardware-Treiber (`ir_emitter.py`/`ir_receiver.py` als co-
  lokalisierte Untermodule) und eine eigene Weboberfläche
  (`admin_shooter.html`, ausgeliefert über `handle_route()`). Der beste
  Ausgangspunkt, um zu sehen, wie ein eigener Spielmodus als Plugin gebaut
  wird - eine rohe Quellcode-Kopie davon (`.py`, unkompiliert) kann man sich
  auch direkt im Webshop-Plugin-Store herunterladen ("Shooter-Plugin –
  Quellcode-Vorlage").

Am einfachsten startet man mit `template/plugin_template/` - kopiere den
ganzen Ordner nach `source/mods/<dein_name>/` und passe ihn an.

## manifest.json - Felder

```json
{
    "name": "my_plugin",
    "version": "0.1.0",
    "author": "Dein Name",
    "entry": "main.py",
    "enabled": true,
    "has_error": false,
    "error_message": "",
    "loop_interval_ms": 1000,
    "ui_slots": { "system": "render_system_slot" },
    "route_prefixes": []
}
```

| Feld | Bedeutung |
|---|---|
| `name` | Eindeutiger Mod-Name (nur Buchstaben/Zahlen/`_`/`-`), muss zum Ordnernamen passen. |
| `version` / `author` | Nur zur Anzeige (Weboberfläche, Webshop-Store, Android-App) - z.B. `"1.0.0"` und `"Code by Nico Bollhalder"`. |
| `entry` | Name der Einstiegsdatei (üblich: `"main.py"`). Der Plugin-Manager importiert sie per Python-Import - existiert nur eine kompilierte `main.mpy` (siehe unten), wird die transparent geladen, ohne dass sich an diesem Feld etwas ändert. |
| `enabled` | Ob das Plugin beim Boot aktiviert werden soll. Wird bei einem Absturz automatisch auf `false` gesetzt. |
| `has_error` / `error_message` | Wird vom Plugin-System selbst verwaltet (Crash-Status) - beim ersten Deploy immer `false`/`""`. |
| `loop_interval_ms` | Wie oft (in ms) `loop()` aufgerufen wird, solange das Plugin aktiv ist. |
| `ui_slots` | Optional: Mapping Ziel-Tab → Funktionsname, der ein HTML-Fragment für diesen Tab liefert (siehe unten). |
| `route_prefixes` | Optional: Liste von URL-Präfixen, für die `handle_route()` aufgerufen wird (siehe unten). |

## Lifecycle-Hooks (alle optional, alle synchron)

- **`setup(context)`** - einmalig beim Aktivieren. `context["debug_log"]`
  zum Loggen, `context["plugin_dir"]` für eigene Dateien im Plugin-Ordner.
- **`loop()`** - alle `loop_interval_ms`, solange aktiv.
- **`teardown()`** - beim Deaktivieren/Löschen (best effort).

**Crash-Isolation:** Jeder Aufruf läuft in `try/except` im Plugin-Manager.
Eine Exception in `setup()`, `loop()` oder `handle_route()` markiert das
Plugin sofort als `enabled=false`/`has_error=true` (mit der Fehlermeldung)
und entfernt es aus der aktiven Schleife - `main.py` selbst crasht dabei
**nie**. Der Status ist in der Pico-Weboberfläche unter "Plugins" →
"Installierte Plugins" sichtbar (rot markiert: `CRASHED / FEHLER`).

## Tabs erweitern (`ui_slots`)

Verfügbare Slot-Namen: `system` (System-Tab) und `idcard` (Ausweis-Tab).
Die genannte Funktion (z.B. `render_system_slot()`) gibt einen HTML-String
zurück, der an der Stelle des `<!--PLUGIN_SLOT:system-->`-Markers in
`admin_system.html` eingefügt wird. Es gibt keine gemeinsame CSS/JS-Basis -
das Fragment muss sich selbst stylen/verhalten (eigenes `<style>`/`<script>`
bei Bedarf). Ist kein Plugin für einen Slot aktiv, bleibt er einfach leer
(keine Änderung am normalen Seitenaufbau).

## Eigene HTTP-Routen (`route_prefixes` + `handle_route()`)

Für einen eigenen Spielmodus/eine eigene Admin-Unterseite:

```json
"route_prefixes": ["/admin-my_plugin", "/my_plugin-"]
```

```python
async def handle_route(writer, request_path, request_method, query_params, body_params):
    if request_path == "/admin-my_plugin":
        # z.B. eine eigene HTML-Datei aus dem Plugin-Ordner ausliefern
        return True
    return False
```

`plugin_manager.handle_plugin_route()` ruft `handle_route()` für jeden
Request auf, dessen Pfad zu einem der `route_prefixes` passt (Gleichheit
oder `startswith`). Vollständiges Beispiel: `source/mods/shooter/main.py`.

## Wichtige Einschränkung: `.mpy` statt `.py` für den Webshop-Store

Der Webshop-Plugin-Store (`/plugins`) verteilt Mods **ausschließlich** als
per `mpy-cross` vorkompilierte `.mpy`-Dateien (Quellcode-Schutz, gleiches
Prinzip wie `build_firmware.py`'s Firmware-Bundles) - ein Upload mit rohen
`.py`-Dateien wird abgelehnt. Das Plugin-System selbst (`plugin_manager.py`)
unterstützt beide Formen transparent (MicroPythons Standard-Import lädt
`.mpy` an Stelle von `.py`, wenn vorhanden) - für die lokale Entwicklung
per `tools/deploy_mod.py` reichen daher rohe `.py`-Dateien völlig aus, nur
für den **Store-Upload** muss vorher kompiliert werden.

## Die wichtigsten Befehle

```bash
# Syntax lokal prüfen, bevor deployt wird
python -m py_compile source/mods/<name>/main.py

# Auf den Pico übertragen (Entwicklung, .py direkt - siehe tools/deploy_mod.py)
python tools/deploy_mod.py --mod <name> --mode serial
python tools/deploy_mod.py --mod <name> --mode serial --port COM5
python tools/deploy_mod.py --mod <name> --mode wifi --host 192.168.4.1 --password geheim123

# Für den Webshop-Store paketieren (.py -> .mpy) und hochladen (GUI)
python tools/plugin_packager.py
```

Auf der Pico-Weboberfläche (`/admin-plugins`) lässt sich jedes installierte
Plugin aktivieren/deaktivieren/löschen, mit rot markiertem Crash-Status
falls vorhanden. Im Tab "Webshop Store & Updates" erscheinen die vom
Webshop synchronisierten Mods mit einem Download-Button, der den
temporären WLAN-Download direkt auf dem Pico auslöst.

## Deployment-Wege im Überblick

| Weg | Wofür |
|---|---|
| `tools/deploy_mod.py` (seriell/USB) | Entwicklung/Debugging - schnellster Weg, rohe `.py`-Dateien. |
| `tools/deploy_mod.py` (WLAN/WebREPL) | Wie oben, ohne USB-Kabel - WebREPL muss vorher einmalig manuell aktiviert werden (`import webrepl; webrepl.start()`), dieses Projekt startet es nicht automatisch. |
| Webshop-Store (`/plugins`) + Pico-Weboberfläche | Verteilung an Endnutzer - erfordert `.mpy`-kompiliertes Paket (`tools/plugin_packager.py`), Download läuft direkt vom Pico aus per temporärer WLAN-Verbindung. |
| Android-App | Gleicher Store-Download-Mechanismus, bequem vom Smartphone aus (Tab "Webshop Store"). |
