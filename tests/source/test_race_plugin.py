"""Tests fuer source/mods/race/main.py - der Race-Spielmodus als duenne
Plugin-Huelle um race_mode.py's BLE-Kernlogik (siehe dortiger Modul-
Docstring: RaceMode selbst bleibt in source/race_mode.py und wird bereits
vollstaendig von tests/source/test_race_mode.py abgedeckt - hier geht es
NUR um die Plugin-Lifecycle/UI-Slot-Integration, analog zu
tests/source/test_shooter_mode.py's "Plugin-Lifecycle"-Abschnitt).

Wird als echtes Unterpaket "mods.race.main" importiert (source/ ist bereits
ueber das Root-conftest.py auf sys.path, source/mods/__init__.py und
source/mods/race/__init__.py existieren als committete Dateien) - sys.modules
wird trotzdem vor/nach jedem Test von allen "mods"/"mods.*"-Eintraegen
befreit (gleiches Muster wie test_shooter_mode.py's _purge_mods_modules()),
damit ein evtl. von einem anderen Test hinterlassenes "mods"-Package-Objekt
nicht faelschlich weiterverwendet wird."""
import sys

import pytest


def _purge_mods_modules():
    for key in list(sys.modules.keys()):
        if key == "mods" or key.startswith("mods."):
            del sys.modules[key]


@pytest.fixture
def race_plugin(install_stub_module):
    sent_html = []

    async def fake_send_html_file(writer, path):
        sent_html.append(path)

    install_stub_module(
        "main",
        DEFAULT_PILOT_NAME="TestPilot",
        debug_log=lambda message: None,
        send_html_file=fake_send_html_file,
    )

    # handle_route() importiert pico_web_api lazy (siehe dortiger
    # send_admin_html_with_slot()-Aufruf) - pico_web_api bindet SEIN EIGENES
    # "from main import send_html_file" beim EIGENEN ersten Import fest an
    # den zu dem Zeitpunkt aktiven main-Stub (gleiches Muster wie
    # test_shooter_mode.py's shooter_plugin-Fixture).
    sys.modules.pop("pico_web_api", None)
    sys.modules.pop("plugin_manager", None)
    _purge_mods_modules()
    import importlib
    module = importlib.import_module("mods.race.main")
    module._test_sent_html = sent_html
    yield module
    if module._task is not None:
        module._task.cancel()
    _purge_mods_modules()
    sys.modules.pop("pico_web_api", None)
    sys.modules.pop("plugin_manager", None)


def _context():
    return {"debug_log": lambda message: None, "plugin_dir": "mods/race"}


# ==================== Plugin-Lifecycle (setup/teardown/handle_route) ====================


@pytest.mark.asyncio
async def test_setup_creates_singleton_manager_and_task(race_plugin):
    race_plugin.setup(_context())
    first_manager = race_plugin._manager
    first_task = race_plugin._task
    assert first_manager is not None
    assert first_task is not None

    race_plugin.setup(_context())
    assert race_plugin._manager is first_manager  # kein zweites RaceMode (keine doppelte BLE-IRQ)


@pytest.mark.asyncio
async def test_teardown_cancels_task_and_stops_race(race_plugin):
    import asyncio

    race_plugin.setup(_context())
    race_plugin._manager.start_race("racer")
    assert race_plugin._manager.running is True
    task = race_plugin._task

    race_plugin.teardown()
    # task.cancel() nur PLANT die Cancellation - der Task selbst braucht
    # noch einen Event-Loop-Durchlauf, um sie tatsaechlich zu verarbeiten.
    for _ in range(5):
        await asyncio.sleep(0)

    assert race_plugin._manager.running is False
    assert race_plugin._task is None
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_teardown_then_setup_reuses_same_manager_with_new_task(race_plugin):
    race_plugin.setup(_context())
    manager = race_plugin._manager
    race_plugin.teardown()
    assert race_plugin._task is None

    race_plugin.setup(_context())
    assert race_plugin._manager is manager
    assert race_plugin._task is not None


@pytest.mark.asyncio
async def test_handle_route_serves_admin_page(race_plugin):
    handled = await race_plugin.handle_route(object(), "/admin-race", "GET", {}, {})
    assert handled is True
    assert race_plugin._test_sent_html == [race_plugin.ADMIN_RACE_HTML_PATH]


@pytest.mark.asyncio
async def test_handle_route_delegates_race_prefixed_routes(race_plugin, monkeypatch):
    race_plugin.setup(_context())

    async def fake_handle_race_route(writer, path, method, query, body, manager):
        return True

    monkeypatch.setattr(race_plugin, "_handle_race_route", fake_handle_race_route)
    handled = await race_plugin.handle_route(object(), "/race-data", "GET", {}, {})
    assert handled is True


@pytest.mark.asyncio
async def test_handle_route_race_prefixed_returns_false_before_setup(race_plugin):
    """Ohne setup() gibt es noch keinen Manager - handle_route() darf dann
    nicht crashen, sondern muss unbehandelt (False) zurueckgeben."""
    handled = await race_plugin.handle_route(object(), "/race-data", "GET", {}, {})
    assert handled is False


@pytest.mark.asyncio
async def test_handle_route_unknown_path_returns_false(race_plugin):
    race_plugin.setup(_context())
    handled = await race_plugin.handle_route(object(), "/does-not-exist", "GET", {}, {})
    assert handled is False


# ==================== get_ui_schema() (native App-UI) ====================


def test_get_ui_schema_shape(race_plugin):
    schema = race_plugin.get_ui_schema()
    assert schema["title"] == "Race"
    assert schema["poll_endpoint"] == "/race-data"
    section_types = [section["type"] for section in schema["sections"]]
    assert section_types == ["stats", "form", "actions", "list"]


def test_get_ui_schema_form_fields_match_manager_config_keys(race_plugin):
    """Jedes "form"-Feld muss ein Schluessel sein, den RaceMode.configure()
    tatsaechlich kennt (siehe _default_config()) - sonst wuerde die App ein
    Feld anzeigen/senden, das der Server stillschweigend ignoriert."""
    schema = race_plugin.get_ui_schema()
    form_section = next(section for section in schema["sections"] if section["type"] == "form")
    form_keys = {field["key"] for field in form_section["fields"]}

    manager = race_plugin.RaceMode()
    assert form_keys == set(manager._default_config().keys()) - {"role"}
    assert form_section["submit_endpoint"] == "/race-config"


def test_get_ui_schema_action_endpoints_are_race_routes(race_plugin):
    schema = race_plugin.get_ui_schema()
    actions_section = next(section for section in schema["sections"] if section["type"] == "actions")
    endpoints = {button["endpoint"] for button in actions_section["buttons"]}
    assert endpoints == {"/race-stop"}


def test_get_ui_schema_registered_via_plugin_manager(race_plugin):
    """Stellt sicher, dass manifest.json tatsaechlich auf die vorhandene
    Funktion zeigt (Tippfehler in "ui_pages" wuerden sonst erst zur Laufzeit
    auf dem echten Geraet auffallen)."""
    import json
    import os

    manifest_path = os.path.join(os.path.dirname(race_plugin.__file__), "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    fn_name = manifest["ui_pages"]["main"]
    assert getattr(race_plugin, fn_name)() == race_plugin.get_ui_schema()


# ==================== ui_slots (Dashboard + Zuschauer-Ansicht) ====================


def test_dashboard_ui_slots_registered_via_plugin_manager(race_plugin):
    """Wie test_get_ui_schema_registered_via_plugin_manager(), aber fuer die
    7 ui_slots aus manifest.json - ein Tippfehler dort wuerde sonst dazu
    fuehren, dass Race im Dashboard (/admin) bzw. auf /gamemodes-view
    unsichtbar bleibt, obwohl das Plugin aktiv ist."""
    import json
    import os

    manifest_path = os.path.join(os.path.dirname(race_plugin.__file__), "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)

    ui_slots = manifest["ui_slots"]
    assert set(ui_slots) == {
        "dashboard_nav", "dashboard_card", "dashboard_stat", "dashboard_script",
        "gamemodes_button", "gamemodes_card", "gamemodes_script",
    }
    for slot_name, fn_name in ui_slots.items():
        fn = getattr(race_plugin, fn_name)
        html = fn()
        assert isinstance(html, str) and html


def test_dashboard_nav_and_card_link_to_admin_race(race_plugin):
    assert '/admin-race' in race_plugin.render_dashboard_nav_slot()
    assert '/admin-race' in race_plugin.render_dashboard_card_slot()


def test_gamemodes_button_slot_links_to_admin_race(race_plugin):
    assert '/admin-race' in race_plugin.render_gamemodes_button_slot()


def test_gamemodes_card_slot_contains_status_ids(race_plugin):
    """Die generische Karte muss dieselben Element-IDs liefern, die
    render_gamemodes_script_slot()'s Poll-Skript per getElementById()
    anspricht - sonst bliebe die Zuschauer-Ansicht stumm."""
    html = race_plugin.render_gamemodes_card_slot()
    for element_id in (
        "r_dot", "r_role", "r_event", "r_waiting", "r_lapprogress",
        "r_gatea", "r_gateb", "r_lastlap", "r_bestlap", "r_total", "r_laphistory",
    ):
        assert 'id="{}"'.format(element_id) in html


def test_gamemodes_script_slot_registers_gamemodes_hook(race_plugin):
    html = race_plugin.render_gamemodes_script_slot()
    assert "window.GAMEMODES_HOOKS" in html
    assert "/race-data" in html


def test_dashboard_stat_slot_declares_ids_used_by_dashboard_script_slot(race_plugin):
    """render_dashboard_script_slot()'s <script> greift per getElementById auf
    st_race_val/-sub zu - diese IDs muessen exakt aus
    render_dashboard_stat_slot() stammen, sonst bleibt die Kachel stumm."""
    stat_html = race_plugin.render_dashboard_stat_slot()
    script_html = race_plugin.render_dashboard_script_slot()
    assert 'id="st_race_val"' in stat_html
    assert 'id="st_race_sub"' in stat_html
    assert "st_race_val" in script_html
    assert "st_race_sub" in script_html
    assert "/race-log" in script_html
    assert "DASHBOARD_HOOKS" in script_html
