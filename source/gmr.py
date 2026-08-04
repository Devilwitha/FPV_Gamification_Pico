"""Gemeinsame Lazy-Wiring fuer die KOTH- und Race-Spielmodi (Game Modes
Routes).

Buendelt Admin-Seiten-Auslieferung, Routing-Praefix-Dispatching und die
Task-Erstellung fuer koth_mode.py/race_mode.py in einem einzigen, schlanken
Modul - main.py bindet dieses Modul nur per Lazy-Import ein (erst beim
ersten Request bzw. beim Start der Async-Tasks), damit main.py selbst klein
genug fuer den MicroPython-Compile-Schritt bleibt (siehe
infection_mode.py/upload_helpers.py fuer das gleiche Muster). Kurzer
Dateiname (gmr.py statt game_modes_routes.py) ist ABSICHTLICH gewaehlt:
main.py referenziert diesen Modulnamen an 2 Stellen, jedes gesparte Zeichen
zaehlt fuer die ~85168-Byte Compile-Grenze. Importiert DEFAULT_PILOT_NAME/
debug_log direkt aus main (bereits fertig geladen, da dieses
Modul erst NACH `import main` lazy nachgeladen wird) statt sie bei jedem
Aufruf als Parameter durchzureichen - spart main.py weitere Bytes.

Der Shooter-Spielmodus ist bewusst NICHT mehr hier verdrahtet - er lebt
komplett als eigenstaendiges Plugin (siehe source/mods/shooter/main.py) und
wird ueber plugin_manager.py's generischen handle_plugin_route()-Dispatcher
bedient (main.py ruft diesen direkt auf, unabhaengig von gmr.py). Referenz-
Beispiel dafuer, wie ein eigener Spielmodus komplett als Mod statt fest in
main.py/gmr.py gebaut wird, siehe template/README.md.
"""

import asyncio
import gc

from main import DEFAULT_PILOT_NAME, debug_log

ADMIN_KOTH_HTML_PATH = "admin_koth.html"
ADMIN_RACE_HTML_PATH = "admin_race.html"
GAMEMODES_VIEW_HTML_PATH = "gamemodes_view.html"

_koth_manager = None
_race_manager = None
_tasks = []


def ensure_koth_manager():
    global _koth_manager
    if _koth_manager is None:
        gc.collect()
        from koth_mode import KothMode
        _koth_manager = KothMode(DEFAULT_PILOT_NAME, debug_log)
    return _koth_manager


def ensure_race_manager():
    global _race_manager
    if _race_manager is None:
        gc.collect()
        from race_mode import RaceMode
        _race_manager = RaceMode(DEFAULT_PILOT_NAME, debug_log)
    return _race_manager


def start_tasks():
    if _tasks:
        return _tasks
    _tasks.append(asyncio.create_task(ensure_koth_manager().run()))
    _tasks.append(asyncio.create_task(ensure_race_manager().run()))
    return _tasks


async def handle_admin_and_routes(writer, request_path, request_method, query_params, body_params):
    if request_path == '/admin-koth':
        import pico_web_api
        await pico_web_api.send_admin_html_with_slot(writer, ADMIN_KOTH_HTML_PATH, "dashboard_nav")
        return True

    if request_path == '/admin-race':
        import pico_web_api
        await pico_web_api.send_admin_html_with_slot(writer, ADMIN_RACE_HTML_PATH, "dashboard_nav")
        return True

    if request_path == '/gamemodes-view':
        import pico_web_api
        await pico_web_api.send_admin_html_with_slot(writer, GAMEMODES_VIEW_HTML_PATH, "gamemodes_button")
        return True

    if request_path.startswith('/koth-'):
        from koth_mode import handle_koth_route
        if await handle_koth_route(writer, request_path, request_method, query_params, body_params, ensure_koth_manager()):
            return True

    if request_path.startswith('/race-'):
        from race_mode import handle_race_route
        if await handle_race_route(writer, request_path, request_method, query_params, body_params, ensure_race_manager()):
            return True

    return False
