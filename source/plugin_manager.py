"""plugin_manager.py - generische, crash-sichere Plugin-Engine.

Laedt Ordner-basierte Mods aus mods/<name>/ (manifest.json + eine
Einstiegsdatei, per Konvention main.py/main.mpy). Jedes Mod bietet optional
setup(context)/loop()/teardown() sowie eigene HTTP-Routen (handle_route())
und Tab-Erweiterungs-Fragmente (ui_slots, siehe manifest-Schema); main.py
selbst darf durch ein abstuerzendes Mod NIEMALS mitgerissen werden -
setup()/loop()/handle_route() laufen daher zwingend in try/except, ein
Absturz markiert das Mod dauerhaft als enabled=False/has_error=True (inkl.
error_message) und entfernt es sofort aus der aktiven Schleife.

Ausgelagert aus main.py (wie gmr.py/infection_mode.py), damit dieser Code
main.py's Kompiliergroesse beim riskanten `import main` in boot.py nicht
vergroessert - wird erst beim ersten main_async()-Start lazy importiert.

Ladeverfahren: main.py ist zur Laufzeit bereits unter dem Modulnamen "main"
in sys.modules eingetragen - ein Mod, dessen Einstiegsdatei ebenfalls
main.py heisst, darf deshalb NICHT per `import main` geladen werden (das
wuerde stattdessen main.py's eigenes Modul zurueckgeben). Stattdessen wird
jedes Mod als eigenes, "echtes" Unterpaket importiert: mods/<name>/main.py
wird als `mods.<name>.main` importiert - ein voellig eigener sys.modules-
Schluessel, keine Kollision, UND (wichtig fuer den Webshop-Store, siehe
webshop/app.py) MicroPythons Standard-Importmechanismus laedt dabei
transparent main.mpy statt main.py, falls nur eine per mpy-cross
vorkompilierte .mpy-Datei vorhanden ist (Quellcode-Schutz) - das waere mit
dem fruehereren exec()/compile()-Ansatz NICHT moeglich gewesen, da compile()
nur Text-Quellcode akzeptiert, keine .mpy-Bytecode-Dateien. mods/__init__.py
und mods/<name>/__init__.py werden bei Bedarf automatisch angelegt (leere
Dateien), damit ein per Webshop-Download nachinstalliertes Mod nicht daran
scheitert, dass der Autor keine __init__.py mit ausgeliefert hat.
"""

import gc
import json
import os
import sys
import time

try:
    from main import debug_log as _debug_log
except Exception:
    def _debug_log(message):
        try:
            print("[PLUGIN] {}".format(message))
        except Exception:
            pass

MODS_DIR = "mods"
DEFAULT_LOOP_INTERVAL_MS = 1000
SCHEDULER_TICK_MS = 20

# name -> {"manifest": dict, "module": <Modul-Objekt>, "next_run_ms": int}
_active_plugins = {}


def _ticks_diff(value, reference):
    try:
        return time.ticks_diff(value, reference)
    except Exception:
        return value - reference


def _ticks_add(value, delta):
    try:
        return time.ticks_add(value, delta)
    except Exception:
        return value + delta


def _plugin_dir(name):
    return MODS_DIR + "/" + name


def _manifest_path(name):
    return _plugin_dir(name) + "/manifest.json"


def _default_manifest(name):
    return {
        "name": name,
        "version": "0.0.0",
        "author": "",
        "description": "",
        "entry": "main.py",
        "enabled": True,
        "has_error": False,
        "error_message": "",
        "loop_interval_ms": DEFAULT_LOOP_INTERVAL_MS,
        "ui_slots": {},
        "ui_pages": {},
        "route_prefixes": [],
    }


def _normalize_manifest(name, values):
    manifest = _default_manifest(name)
    if isinstance(values, dict):
        manifest.update(values)
    manifest["name"] = name
    manifest["description"] = str(manifest.get("description") or "")[:200]
    manifest["entry"] = str(manifest.get("entry") or "main.py")
    manifest["enabled"] = bool(manifest.get("enabled", True))
    manifest["has_error"] = bool(manifest.get("has_error", False))
    manifest["error_message"] = str(manifest.get("error_message") or "")[:200]
    try:
        manifest["loop_interval_ms"] = max(20, int(manifest.get("loop_interval_ms", DEFAULT_LOOP_INTERVAL_MS)))
    except Exception:
        manifest["loop_interval_ms"] = DEFAULT_LOOP_INTERVAL_MS
    if not isinstance(manifest.get("ui_slots"), dict):
        manifest["ui_slots"] = {}
    if not isinstance(manifest.get("ui_pages"), dict):
        manifest["ui_pages"] = {}
    if not isinstance(manifest.get("route_prefixes"), list):
        manifest["route_prefixes"] = []
    return manifest


def _load_manifest(name):
    try:
        with open(_manifest_path(name), "r") as manifest_file:
            values = json.loads(manifest_file.read())
    except Exception:
        values = {}
    return _normalize_manifest(name, values)


def _save_manifest(name, manifest):
    path = _manifest_path(name)
    temp_path = path + ".tmp"
    try:
        with open(temp_path, "w") as output_file:
            output_file.write(json.dumps(manifest))
        try:
            os.remove(path)
        except Exception:
            pass
        os.rename(temp_path, path)
        return True
    except Exception as e:
        _debug_log("[PLUGIN] Manifest-Speichern fehlgeschlagen ({}): {}".format(name, e))
        return False


def list_plugin_names():
    """Alle Unterordner von mods/, die eine manifest.json besitzen."""
    names = []
    try:
        for entry in os.listdir(MODS_DIR):
            try:
                os.stat(_manifest_path(entry))
            except Exception:
                continue
            names.append(entry)
    except Exception:
        pass
    return names


def _ensure_init_files(name):
    """Legt mods/__init__.py und mods/<name>/__init__.py an, falls WEDER die
    .py- NOCH eine bereits per mpy-cross kompilierte .mpy-Variante existiert
    - noetig, damit `mods` bzw. `mods.<name>` als importierbares Package
    erkannt werden. Nachinstallierte Mods (Webshop-Download/ZIP-Upload)
    muessen diese Dateien deshalb nicht selbst mitbringen.

    WICHTIG: die .mpy-Pruefung ist kein Nice-to-have, sondern verhindert
    einen real beobachteten Absturz - der Webshop-Store liefert Mods
    ausschliesslich vorkompiliert aus (siehe windows/source2/plugin_packager.py's
    pack_mod_to_zip(): JEDE .py-Datei des Mods, auch ein vom Autor
    mitgeliefertes __init__.py, wird zu __init__.mpy kompiliert). Ohne diese
    Pruefung wuerde hier trotz bereits vorhandenem __init__.mpy zusaetzlich
    ein LEERES __init__.py angelegt - zwei gleichzeitig vorhandene
    __init__-Varianten im selben Package-Ordner bringen MicroPythons
    Import-Aufloesung durcheinander und liessen z.B. den anschliessenden
    Import von mods.<name>.main mit "No module named" fehlschlagen, OBWOHL
    main.mpy nachweislich auf der Platte lag."""
    created_any = False
    for init_dir in (MODS_DIR, _plugin_dir(name)):
        init_py = init_dir + "/__init__.py"
        has_init = False
        for candidate in (init_py, init_dir + "/__init__.mpy"):
            try:
                os.stat(candidate)
                has_init = True
                break
            except Exception:
                pass
        if not has_init:
            try:
                with open(init_py, "w") as f:
                    f.write("")
                created_any = True
            except Exception:
                pass
    if created_any:
        # CPython (Simulator/Tests) caches directory listings per import path;
        # ohne dies wuerde der direkt folgende __import__() die soeben
        # angelegten __init__.py-Dateien manchmal nicht finden. Echtes
        # MicroPython kennt diesen Cache nicht (importlib fehlt dort), daher
        # try/except statt eines harten Imports.
        try:
            import importlib
            importlib.invalidate_caches()
        except Exception:
            pass


def _module_stem(entry_filename):
    if entry_filename.endswith(".py"):
        return entry_filename[:-3]
    if entry_filename.endswith(".mpy"):
        return entry_filename[:-4]
    return entry_filename


def _plugin_module_name(name, entry_filename):
    return "mods.{}.{}".format(name, _module_stem(entry_filename))


def _unload_plugin_modules(name):
    """Entfernt alle sys.modules-Eintraege fuer dieses Plugin (mods.<name>
    UND jedes mods.<name>.*-Untermodul, z.B. eigene Hardware-Treiber, die
    das Plugin mitbringt) - erzwingt einen frischen Import bei
    Reaktivierung/nach einem erneuten Deploy statt alten Bytecode
    weiterzuverwenden."""
    prefix = "mods.{}".format(name)
    for module_name in list(sys.modules.keys()):
        if module_name == prefix or module_name.startswith(prefix + "."):
            del sys.modules[module_name]


def _import_plugin_module(name, entry_filename):
    _ensure_init_files(name)
    _unload_plugin_modules(name)
    module_name = _plugin_module_name(name, entry_filename)
    __import__(module_name)
    return sys.modules[module_name]


def _build_context(name):
    return {
        "debug_log": _debug_log,
        "plugin_dir": _plugin_dir(name),
    }


def _mark_crashed(name, error):
    manifest = _load_manifest(name)
    manifest["enabled"] = False
    manifest["has_error"] = True
    manifest["error_message"] = str(error)[:200]
    _save_manifest(name, manifest)
    entry = _active_plugins.pop(name, None)
    if entry is not None:
        teardown_fn = getattr(entry["module"], "teardown", None)
        if teardown_fn is not None:
            try:
                teardown_fn()
            except Exception:
                pass
    _debug_log("[PLUGIN] '{}' deaktiviert nach Fehler: {}".format(name, error))
    gc.collect()


def _load_single_plugin(name):
    """Laedt genau ein Mod (setup() inklusive) und traegt es bei Erfolg in
    _active_plugins ein. Wird sowohl von load_all_plugins() als auch von
    set_plugin_state() beim (Re-)Aktivieren genutzt."""
    manifest = _load_manifest(name)
    if not manifest["enabled"]:
        return False
    try:
        module = _import_plugin_module(name, manifest["entry"])
        setup_fn = getattr(module, "setup", None)
        if setup_fn is not None:
            setup_fn(_build_context(name))
        _active_plugins[name] = {
            "manifest": manifest,
            "module": module,
            "next_run_ms": time.ticks_ms(),
        }
        return True
    except Exception as e:
        _mark_crashed(name, e)
        return False


def load_all_plugins():
    """Scannt mods/*, laedt alle aktivierten Mods. Wird einmalig beim Start
    aus main.py aufgerufen (main_async()), zusaetzlich erneut nach einem
    erfolgreichen Store-Download (siehe network_manager.download_plugin_via_wifi)."""
    gc.collect()
    for name in list_plugin_names():
        if name not in _active_plugins:
            _load_single_plugin(name)


async def run_loops():
    """Einzelne asyncio-Task (von main.py gestartet), treibt loop() aller
    aktiven Plugins in ihrem jeweils konfigurierten Intervall. Ersetzt keine
    bestehenden Tasks (telemetry_loop/gmr/infection) - laeuft parallel dazu."""
    import asyncio
    while True:
        now = time.ticks_ms()
        for name in list(_active_plugins.keys()):
            entry = _active_plugins.get(name)
            if entry is None:
                continue
            if _ticks_diff(now, entry["next_run_ms"]) < 0:
                continue
            entry["next_run_ms"] = _ticks_add(now, entry["manifest"]["loop_interval_ms"])
            loop_fn = getattr(entry["module"], "loop", None)
            if loop_fn is None:
                continue
            try:
                loop_fn()
            except Exception as e:
                _mark_crashed(name, e)
        await asyncio.sleep_ms(SCHEDULER_TICK_MS)


async def handle_plugin_route(writer, request_path, request_method, query_params, body_params):
    """Dispatcht eine HTTP-Anfrage an das erste aktive Plugin, dessen
    manifest.json-"route_prefixes" den Pfad abdeckt (Gleichheit oder
    Praefix) - siehe z.B. source/mods/shooter/main.py's handle_route() fuer
    "/admin-shooter"/"/shooter-*". Ein Absturz im Handler deaktiviert das
    Plugin sofort (gleiche Crash-Isolation wie setup()/loop())."""
    for name in list(_active_plugins.keys()):
        entry = _active_plugins.get(name)
        if entry is None:
            continue
        prefixes = entry["manifest"].get("route_prefixes") or []
        if not any(request_path == prefix or request_path.startswith(prefix) for prefix in prefixes):
            continue
        handler = getattr(entry["module"], "handle_route", None)
        if handler is None:
            continue
        try:
            handled = await handler(writer, request_path, request_method, query_params, body_params)
        except Exception as e:
            _mark_crashed(name, e)
            return False
        if handled:
            return True
    return False


def set_plugin_state(name, enabled):
    manifest = _load_manifest(name)
    manifest["enabled"] = bool(enabled)
    if enabled:
        manifest["has_error"] = False
        manifest["error_message"] = ""
    _save_manifest(name, manifest)

    if not enabled and name in _active_plugins:
        entry = _active_plugins.pop(name)
        teardown_fn = getattr(entry["module"], "teardown", None)
        if teardown_fn is not None:
            try:
                teardown_fn()
            except Exception:
                pass
        gc.collect()

    if enabled and name not in _active_plugins:
        _load_single_plugin(name)

    return _load_manifest(name)


def delete_plugin(name):
    if name in _active_plugins:
        entry = _active_plugins.pop(name)
        teardown_fn = getattr(entry["module"], "teardown", None)
        if teardown_fn is not None:
            try:
                teardown_fn()
            except Exception:
                pass

    def _remove_tree(path):
        try:
            for entry_name in os.listdir(path):
                entry_path = path + "/" + entry_name
                try:
                    if os.stat(entry_path)[0] & 0x4000:
                        _remove_tree(entry_path)
                    else:
                        os.remove(entry_path)
                except Exception:
                    pass
            os.rmdir(path)
        except Exception:
            pass

    _remove_tree(_plugin_dir(name))
    _unload_plugin_modules(name)
    gc.collect()
    return True


def is_active(name):
    return name in _active_plugins


def get_plugin_module(name):
    """Gibt das geladene Modul-Objekt eines AKTIVEN Plugins zurueck (oder
    None), damit Kernfirmware-Code (main.py) optional Zusatzdaten von einem
    Plugin abfragen kann, ohne es fest zu importieren - z.B. main.py's
    Infection-Live-Status fuer /data bzw. die Rundenzusammenfassung fuer den
    Arcade-Sitzungs-Export (siehe source/mods/infection/main.py's
    get_status()/get_session_summary_text())."""
    entry = _active_plugins.get(name)
    return entry["module"] if entry else None


def load_single_plugin(name):
    """Oeffentlicher Wrapper um _load_single_plugin() (siehe dort) - fuer
    main.py, das z.B. das infection-Plugin bereits VOR AP-/HTTP-Server-Start
    laden muss (RAM-Fragmentierungs-Grund, siehe main.py's main_async()).
    load_all_plugins() ueberspringt danach bereits aktive Plugins."""
    return _load_single_plugin(name)


def get_ui_slot_html(slot_name):
    fragments = []
    for name in list(_active_plugins.keys()):
        entry = _active_plugins.get(name)
        if entry is None:
            continue
        fn_name = entry["manifest"].get("ui_slots", {}).get(slot_name)
        if not fn_name:
            continue
        fn = getattr(entry["module"], fn_name, None)
        if fn is None:
            continue
        try:
            html = fn()
            if html:
                fragments.append(str(html))
        except Exception as e:
            _mark_crashed(name, e)
    return "".join(fragments)


def get_ui_schema(name):
    """Liefert die native UI-Beschreibung eines Plugins fuer dessen
    "main"-Seite (siehe manifest.json's "ui_pages", z.B.
    source/mods/shooter/main.py's get_ui_schema()) als JSON-serialisierbares
    dict, oder None, falls das Plugin keine deklariert/nicht aktiv ist.

    Analog zu get_ui_slot_html(), aber fuer die App (siehe android_app/.../
    PluginUiScreen) statt fuer eingebettete HTML-Fragmente: die App rendert
    dieses Schema vollstaendig nativ (Compose-Widgets), OHNE dass fuer jedes
    Plugin eigener Kotlin-Code noetig waere - das Schema beschreibt nur
    Felder/Buttons/Endpunkte, die eigentlichen Daten kommen weiterhin ueber
    die vom Plugin selbst registrierten Routen (siehe handle_plugin_route()),
    z.B. shooter's "/shooter-data"/"/shooter-config"."""
    entry = _active_plugins.get(name)
    if entry is None:
        return None
    fn_name = entry["manifest"].get("ui_pages", {}).get("main")
    if not fn_name:
        return None
    fn = getattr(entry["module"], fn_name, None)
    if fn is None:
        return None
    try:
        schema = fn()
        return schema if isinstance(schema, dict) else None
    except Exception as e:
        _mark_crashed(name, e)
        return None


def list_plugins():
    result = []
    for name in list_plugin_names():
        manifest = _load_manifest(name)
        result.append({
            "name": manifest["name"],
            "version": manifest["version"],
            "author": manifest["author"],
            "description": manifest["description"],
            "enabled": manifest["enabled"],
            "has_error": manifest["has_error"],
            "error_message": manifest["error_message"],
            "active": is_active(name),
            "has_ui": bool(manifest.get("ui_pages", {}).get("main")),
        })
    return result
