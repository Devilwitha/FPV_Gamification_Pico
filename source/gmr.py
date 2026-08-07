"""Gemeinsame Route fuer die oeffentliche "Game Modes"-Zuschauer-Ansicht
(Game Modes Routes).

Seit Shooter/Race/Infection/KOTH alle als eigenstaendige Plugins leben
(siehe source/mods/<name>/main.py), bleibt hier nur noch die Auslieferung
von gamemodes_view.html uebrig - main.py bindet dieses Modul weiterhin per
Lazy-Import ein (erst beim ersten Request), damit main.py selbst klein
genug fuer den MicroPython-Compile-Schritt bleibt (siehe
infection_mode.py/upload_helpers.py fuer das gleiche Muster). Kurzer
Dateiname (gmr.py statt game_modes_routes.py) ist ABSICHTLICH gewaehlt:
main.py referenziert diesen Modulnamen an 2 Stellen, jedes gesparte Zeichen
zaehlt fuer die ~85168-Byte Compile-Grenze.

Jeder Spielmodus liefert seine eigene Live-Status-Karte ueber die
gamemodes_button/card/script-ui_slots (siehe plugin_manager.py's
get_ui_slot_html()) bei - Referenz-Beispiel dafuer, wie ein eigener
Spielmodus komplett als Mod statt fest in main.py/gmr.py gebaut wird, siehe
template/README.md.
"""

GAMEMODES_VIEW_HTML_PATH = "gamemodes_view.html"


async def handle_admin_and_routes(writer, request_path, request_method, query_params, body_params):
    if request_path == '/gamemodes-view':
        import pico_web_api
        await pico_web_api.send_admin_html_with_slot(
            writer, GAMEMODES_VIEW_HTML_PATH, ["gamemodes_button", "gamemodes_card", "gamemodes_script"]
        )
        return True

    return False
