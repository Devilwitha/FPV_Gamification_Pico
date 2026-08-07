"""Tests fuer source/mods/koth/main.py - der King-of-the-Hill-Spielmodus als
duenne Plugin-Huelle um koth_mode.py's BLE-Kernlogik (siehe dortiger Modul-
Docstring: KothMode selbst bleibt in source/koth_mode.py und wird bereits
vollstaendig von tests/source/test_koth_mode.py abgedeckt - hier geht es
NUR um die Plugin-Lifecycle/UI-Slot-Integration, analog zu
tests/source/test_race_plugin.py's Struktur).

Wird als echtes Unterpaket "mods.koth.main" importiert (source/ ist bereits
ueber das Root-conftest.py auf sys.path, source/mods/__init__.py und
source/mods/koth/__init__.py existieren als committete Dateien) - sys.modules
wird trotzdem vor/nach jedem Test von allen "mods"/"mods.*"-Eintraegen
befreit (gleiches Muster wie test_race_plugin.py's _purge_mods_modules()),
damit ein evtl. von einem anderen Test hinterlassenes "mods"-Package-Objekt
nicht faelschlich weiterverwendet wird."""
import sys

import pytest


def _purge_mods_modules():
    for key in list(sys.modules.keys()):
        if key == "mods" or key.startswith("mods."):
            del sys.modules[key]


@pytest.fixture
def koth_plugin(install_stub_module):
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
    # test_race_plugin.py's race_plugin-Fixture).
    sys.modules.pop("pico_web_api", None)
    sys.modules.pop("plugin_manager", None)
    _purge_mods_modules()
    import importlib
    module = importlib.import_module("mods.koth.main")
    module._test_sent_html = sent_html
    yield module
    if module._task is not None:
        module._task.cancel()
    _purge_mods_modules()
    sys.modules.pop("pico_web_api", None)
    sys.modules.pop("plugin_manager", None)


def _context():
    return {"debug_log": lambda message: None, "plugin_dir": "mods/koth"}


# ==================== Plugin-Lifecycle (setup/teardown/handle_route) ====================


@pytest.mark.asyncio
async def test_setup_creates_singleton_manager_and_task(koth_plugin):
    koth_plugin.setup(_context())
    first_manager = koth_plugin._manager
    first_task = koth_plugin._task
    assert first_manager is not None
    assert first_task is not None

    koth_plugin.setup(_context())
    assert koth_plugin._manager is first_manager  # kein zweites KothMode (keine doppelte BLE-IRQ)


@pytest.mark.asyncio
async def test_teardown_cancels_task_and_stops_round(koth_plugin):
    import asyncio

    koth_plugin.setup(_context())
    koth_plugin._manager.start_round("player")
    assert koth_plugin._manager.running is True
    task = koth_plugin._task

    koth_plugin.teardown()
    # task.cancel() nur PLANT die Cancellation - der Task selbst braucht
    # noch einen Event-Loop-Durchlauf, um sie tatsaechlich zu verarbeiten.
    for _ in range(5):
        await asyncio.sleep(0)

    assert koth_plugin._manager.running is False
    assert koth_plugin._task is None
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_teardown_then_setup_reuses_same_manager_with_new_task(koth_plugin):
    koth_plugin.setup(_context())
    manager = koth_plugin._manager
    koth_plugin.teardown()
    assert koth_plugin._task is None

    koth_plugin.setup(_context())
    assert koth_plugin._manager is manager
    assert koth_plugin._task is not None


@pytest.mark.asyncio
async def test_handle_route_serves_admin_page(koth_plugin):
    handled = await koth_plugin.handle_route(object(), "/admin-koth", "GET", {}, {})
    assert handled is True
    assert koth_plugin._test_sent_html == [koth_plugin.ADMIN_KOTH_HTML_PATH]


@pytest.mark.asyncio
async def test_handle_route_delegates_koth_prefixed_routes(koth_plugin, monkeypatch):
    koth_plugin.setup(_context())

    async def fake_handle_koth_route(writer, path, method, query, body, manager):
        return True

    monkeypatch.setattr(koth_plugin, "_handle_koth_route", fake_handle_koth_route)
    handled = await koth_plugin.handle_route(object(), "/koth-data", "GET", {}, {})
    assert handled is True


@pytest.mark.asyncio
async def test_handle_route_koth_prefixed_returns_false_before_setup(koth_plugin):
    """Ohne setup() gibt es noch keinen Manager - handle_route() darf dann
    nicht crashen, sondern muss unbehandelt (False) zurueckgeben."""
    handled = await koth_plugin.handle_route(object(), "/koth-data", "GET", {}, {})
    assert handled is False


@pytest.mark.asyncio
async def test_handle_route_unknown_path_returns_false(koth_plugin):
    koth_plugin.setup(_context())
    handled = await koth_plugin.handle_route(object(), "/does-not-exist", "GET", {}, {})
    assert handled is False


# ==================== get_ui_schema() (native App-UI) ====================


def test_get_ui_schema_shape(koth_plugin):
    schema = koth_plugin.get_ui_schema()
    assert schema["title"] == "King of the Hill"
    assert schema["poll_endpoint"] == "/koth-data"
    section_types = [section["type"] for section in schema["sections"]]
    assert section_types == ["stats", "form", "actions", "list"]


def test_get_ui_schema_form_fields_match_manager_config_keys(koth_plugin):
    """Jedes "form"-Feld muss ein Schluessel sein, den KothMode tatsaechlich
    kennt (siehe _default_config()) - sonst wuerde die App ein Feld
    anzeigen/senden, das der Server stillschweigend ignoriert. "role" wird
    bewusst ausgeschlossen (nur ueber index_gatehill.html waehlbar)."""
    schema = koth_plugin.get_ui_schema()
    form_section = next(section for section in schema["sections"] if section["type"] == "form")
    form_keys = {field["key"] for field in form_section["fields"]}

    manager = koth_plugin.KothMode()
    assert form_keys == set(manager._default_config().keys()) - {"role"}
    assert form_section["submit_endpoint"] == "/koth-config"


def test_get_ui_schema_action_endpoints_are_koth_routes(koth_plugin):
    schema = koth_plugin.get_ui_schema()
    actions_section = next(section for section in schema["sections"] if section["type"] == "actions")
    endpoints = {button["endpoint"] for button in actions_section["buttons"]}
    assert endpoints == {"/koth-stop"}


def test_get_ui_schema_list_section_uses_leaderboard(koth_plugin):
    schema = koth_plugin.get_ui_schema()
    list_section = next(section for section in schema["sections"] if section["type"] == "list")
    assert list_section["source_key"] == "leaderboard"


def test_get_ui_schema_registered_via_plugin_manager(koth_plugin):
    """Stellt sicher, dass manifest.json tatsaechlich auf die vorhandene
    Funktion zeigt (Tippfehler in "ui_pages" wuerden sonst erst zur Laufzeit
    auf dem echten Geraet auffallen)."""
    import json
    import os

    manifest_path = os.path.join(os.path.dirname(koth_plugin.__file__), "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    fn_name = manifest["ui_pages"]["main"]
    assert getattr(koth_plugin, fn_name)() == koth_plugin.get_ui_schema()


# ==================== ui_slots (Dashboard + Zuschauer-Ansicht) ====================


def test_dashboard_ui_slots_registered_via_plugin_manager(koth_plugin):
    """Wie test_get_ui_schema_registered_via_plugin_manager(), aber fuer die
    7 ui_slots aus manifest.json - ein Tippfehler dort wuerde sonst dazu
    fuehren, dass KOTH im Dashboard (/admin) bzw. auf /gamemodes-view
    unsichtbar bleibt, obwohl das Plugin aktiv ist."""
    import json
    import os

    manifest_path = os.path.join(os.path.dirname(koth_plugin.__file__), "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)

    ui_slots = manifest["ui_slots"]
    assert set(ui_slots) == {
        "dashboard_nav", "dashboard_card", "dashboard_stat", "dashboard_script",
        "gamemodes_button", "gamemodes_card", "gamemodes_script",
    }
    for slot_name, fn_name in ui_slots.items():
        fn = getattr(koth_plugin, fn_name)
        html = fn()
        assert isinstance(html, str) and html


def test_dashboard_nav_and_card_link_to_admin_koth(koth_plugin):
    assert '/admin-koth' in koth_plugin.render_dashboard_nav_slot()
    assert '/admin-koth' in koth_plugin.render_dashboard_card_slot()


def test_gamemodes_button_slot_links_to_admin_koth(koth_plugin):
    assert '/admin-koth' in koth_plugin.render_gamemodes_button_slot()


def test_gamemodes_card_slot_contains_status_ids(koth_plugin):
    """Die generische Karte muss dieselben Element-IDs liefern, die
    render_gamemodes_script_slot()'s Poll-Skript per getElementById()
    anspricht - sonst bliebe die Zuschauer-Ansicht stumm."""
    html = koth_plugin.render_gamemodes_card_slot()
    for element_id in (
        "k_dot", "k_role", "k_remaining", "k_event", "k_rssi", "k_inrange", "k_score", "k_leaderboard",
    ):
        assert 'id="{}"'.format(element_id) in html


def test_gamemodes_script_slot_registers_gamemodes_hook(koth_plugin):
    html = koth_plugin.render_gamemodes_script_slot()
    assert "window.GAMEMODES_HOOKS" in html
    assert "/koth-data" in html


def test_dashboard_stat_slot_declares_ids_used_by_dashboard_script_slot(koth_plugin):
    """render_dashboard_script_slot()'s <script> greift per getElementById auf
    st_koth_val/-sub zu - diese IDs muessen exakt aus
    render_dashboard_stat_slot() stammen, sonst bleibt die Kachel stumm."""
    stat_html = koth_plugin.render_dashboard_stat_slot()
    script_html = koth_plugin.render_dashboard_script_slot()
    assert 'id="st_koth_val"' in stat_html
    assert 'id="st_koth_sub"' in stat_html
    assert "st_koth_val" in script_html
    assert "st_koth_sub" in script_html
    assert "/koth-log" in script_html
    assert "DASHBOARD_HOOKS" in script_html
