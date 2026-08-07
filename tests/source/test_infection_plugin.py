"""Tests fuer source/mods/infection/main.py - der Infection-Spielmodus als
duenne Plugin-Huelle um infection_mode.py's BLE-Kernlogik (siehe dortiger
Modul-Docstring: InfectionMode selbst bleibt in source/infection_mode.py und
wird bereits vollstaendig von tests/source/test_infection_mode.py
abgedeckt - hier geht es NUR um die Plugin-Lifecycle/UI-Slot-Integration,
analog zu tests/source/test_race_plugin.py.

Anders als Race/KOTH hat Infection KEINE gamemodes_*-Slots (eigene
Zuschauer-Seite /infection-view statt einer Karte auf gamemodes_view.html)
und zwei zusaetzliche Exporte (get_status()/get_session_summary_text()) fuer
main.py's generischen plugin_manager.get_plugin_module()-Zugriff."""
import sys

import pytest


def _purge_mods_modules():
    for key in list(sys.modules.keys()):
        if key == "mods" or key.startswith("mods."):
            del sys.modules[key]


@pytest.fixture
def infection_plugin(install_stub_module):
    sent_html = []

    async def fake_send_html_file(writer, path):
        sent_html.append(path)

    install_stub_module(
        "main",
        AP_SSID="TestSSID",
        AP_PASSWORD="TestPassword",
        DEFAULT_PILOT_NAME="TestPilot",
        debug_log=lambda message: None,
        send_html_file=fake_send_html_file,
    )

    sys.modules.pop("pico_web_api", None)
    sys.modules.pop("plugin_manager", None)
    _purge_mods_modules()
    import importlib
    module = importlib.import_module("mods.infection.main")
    module._test_sent_html = sent_html
    yield module
    if module._task is not None:
        module._task.cancel()
    _purge_mods_modules()
    sys.modules.pop("pico_web_api", None)
    sys.modules.pop("plugin_manager", None)


def _context():
    return {"debug_log": lambda message: None, "plugin_dir": "mods/infection"}


# ==================== Plugin-Lifecycle (setup/teardown/handle_route) ====================


@pytest.mark.asyncio
async def test_setup_creates_singleton_manager_and_task(infection_plugin):
    infection_plugin.setup(_context())
    first_manager = infection_plugin._manager
    first_task = infection_plugin._task
    assert first_manager is not None
    assert first_task is not None

    infection_plugin.setup(_context())
    assert infection_plugin._manager is first_manager  # keine zweite InfectionMode (keine doppelte BLE-IRQ)


@pytest.mark.asyncio
async def test_teardown_cancels_task_and_stops_round(infection_plugin):
    import asyncio

    infection_plugin.setup(_context())
    infection_plugin._manager.start_round("seeker")
    assert infection_plugin._manager.running is True
    task = infection_plugin._task

    infection_plugin.teardown()
    for _ in range(5):
        await asyncio.sleep(0)

    assert infection_plugin._manager.running is False
    assert infection_plugin._task is None
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_teardown_then_setup_reuses_same_manager_with_new_task(infection_plugin):
    infection_plugin.setup(_context())
    manager = infection_plugin._manager
    infection_plugin.teardown()
    assert infection_plugin._task is None

    infection_plugin.setup(_context())
    assert infection_plugin._manager is manager
    assert infection_plugin._task is not None


@pytest.mark.asyncio
async def test_handle_route_serves_admin_page(infection_plugin):
    handled = await infection_plugin.handle_route(object(), "/admin-infection", "GET", {}, {})
    assert handled is True
    assert infection_plugin._test_sent_html == [infection_plugin.ADMIN_INFECTION_HTML_PATH]


@pytest.mark.asyncio
async def test_handle_route_serves_infection_view_page(infection_plugin):
    handled = await infection_plugin.handle_route(object(), "/infection-view", "GET", {}, {})
    assert handled is True
    assert infection_plugin._test_sent_html == [infection_plugin.INFECTION_VIEW_HTML_PATH]


@pytest.mark.asyncio
async def test_handle_route_delegates_infection_and_lobby_prefixed_routes(infection_plugin, monkeypatch):
    infection_plugin.setup(_context())

    async def fake_handle_infection_route(writer, path, method, query, body, manager):
        return True

    monkeypatch.setattr(infection_plugin, "_handle_infection_route", fake_handle_infection_route)
    assert await infection_plugin.handle_route(object(), "/infection-data", "GET", {}, {}) is True
    assert await infection_plugin.handle_route(object(), "/lobby-create", "POST", {}, {}) is True


@pytest.mark.asyncio
async def test_handle_route_prefixed_returns_false_before_setup(infection_plugin):
    """Ohne setup() gibt es noch keinen Manager - handle_route() darf dann
    nicht crashen, sondern muss unbehandelt (False) zurueckgeben."""
    handled = await infection_plugin.handle_route(object(), "/infection-data", "GET", {}, {})
    assert handled is False


@pytest.mark.asyncio
async def test_handle_route_unknown_path_returns_false(infection_plugin):
    infection_plugin.setup(_context())
    handled = await infection_plugin.handle_route(object(), "/does-not-exist", "GET", {}, {})
    assert handled is False


# ==================== get_status()/get_session_summary_text() (main.py hooks) ====================


def test_get_status_returns_none_before_setup(infection_plugin):
    assert infection_plugin.get_status() is None


def test_get_session_summary_text_returns_empty_string_before_setup(infection_plugin):
    assert infection_plugin.get_session_summary_text() == ""


@pytest.mark.asyncio
async def test_get_status_delegates_to_manager_after_setup(infection_plugin):
    infection_plugin.setup(_context())
    status = infection_plugin.get_status()
    assert isinstance(status, dict)
    assert status["running"] is infection_plugin._manager.running


@pytest.mark.asyncio
async def test_get_session_summary_text_delegates_to_manager_after_setup(infection_plugin):
    infection_plugin.setup(_context())
    assert infection_plugin.get_session_summary_text() == infection_plugin._manager.session_summary_text()


# ==================== get_ui_schema() (native App-UI) ====================


def test_get_ui_schema_shape(infection_plugin):
    schema = infection_plugin.get_ui_schema()
    assert schema["title"] == "Infection"
    assert schema["poll_endpoint"] == "/infection-data"
    section_types = [section["type"] for section in schema["sections"]]
    assert section_types == ["stats", "form", "actions", "list"]


def test_get_ui_schema_form_fields_are_subset_of_manager_config_keys(infection_plugin):
    """Jedes "form"-Feld muss ein Schluessel sein, den InfectionMode.configure()
    tatsaechlich kennt (siehe _default_config()) - "initial_role"/"game_mode"
    (Auswahlfelder) werden bewusst NICHT in der nativen Schema aufgenommen,
    da get_ui_schema() nur "toggle"/"number" unterstuetzt."""
    schema = infection_plugin.get_ui_schema()
    form_section = next(section for section in schema["sections"] if section["type"] == "form")
    form_keys = {field["key"] for field in form_section["fields"]}

    manager = infection_plugin.InfectionMode("TestSSID", "TestPassword")
    assert form_keys.issubset(set(manager._default_config().keys()))
    assert "initial_role" not in form_keys
    assert "game_mode" not in form_keys
    assert form_section["submit_endpoint"] == "/infection-config"


def test_get_ui_schema_action_endpoints_are_infection_routes(infection_plugin):
    schema = infection_plugin.get_ui_schema()
    actions_section = next(section for section in schema["sections"] if section["type"] == "actions")
    endpoints = {button["endpoint"] for button in actions_section["buttons"]}
    assert endpoints == {"/infection-stop"}


def test_get_ui_schema_registered_via_plugin_manager(infection_plugin):
    """Stellt sicher, dass manifest.json tatsaechlich auf die vorhandene
    Funktion zeigt (Tippfehler in "ui_pages" wuerden sonst erst zur Laufzeit
    auf dem echten Geraet auffallen)."""
    import json
    import os

    manifest_path = os.path.join(os.path.dirname(infection_plugin.__file__), "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    fn_name = manifest["ui_pages"]["main"]
    assert getattr(infection_plugin, fn_name)() == infection_plugin.get_ui_schema()


# ==================== ui_slots (nur Dashboard - keine gamemodes_*-Slots) ====================


def test_dashboard_ui_slots_registered_via_plugin_manager(infection_plugin):
    """Wie test_get_ui_schema_registered_via_plugin_manager(), aber fuer die
    4 ui_slots aus manifest.json - ein Tippfehler dort wuerde sonst dazu
    fuehren, dass Infection im Dashboard (/admin) unsichtbar bleibt, obwohl
    das Plugin aktiv ist. Anders als Race/KOTH gibt es KEINE gamemodes_*-
    Slots (Infection hat eine eigene Zuschauer-Seite, /infection-view)."""
    import json
    import os

    manifest_path = os.path.join(os.path.dirname(infection_plugin.__file__), "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)

    ui_slots = manifest["ui_slots"]
    assert set(ui_slots) == {
        "dashboard_nav", "dashboard_card", "dashboard_stat", "dashboard_script",
        "index_card", "index_script",
    }
    for slot_name, fn_name in ui_slots.items():
        fn = getattr(infection_plugin, fn_name)
        html = fn()
        assert isinstance(html, str) and html


def test_dashboard_nav_and_card_link_to_admin_infection(infection_plugin):
    assert '/admin-infection' in infection_plugin.render_dashboard_nav_slot()
    assert '/admin-infection' in infection_plugin.render_dashboard_card_slot()


def test_dashboard_stat_slot_declares_ids_used_by_dashboard_script_slot(infection_plugin):
    """render_dashboard_script_slot()'s <script> greift per getElementById auf
    st_infection_val/-sub zu - diese IDs muessen exakt aus
    render_dashboard_stat_slot() stammen, sonst bleibt die Kachel stumm."""
    stat_html = infection_plugin.render_dashboard_stat_slot()
    script_html = infection_plugin.render_dashboard_script_slot()
    assert 'id="st_infection_val"' in stat_html
    assert 'id="st_infection_sub"' in stat_html
    assert "st_infection_val" in script_html
    assert "st_infection_sub" in script_html
    assert "/infection-log" in script_html
    assert "DASHBOARD_HOOKS" in script_html


def test_index_card_slot_links_to_infection_view(infection_plugin):
    assert '/infection-view' in infection_plugin.render_index_card_slot()


def test_index_script_slot_registers_index_hook(infection_plugin):
    card_html = infection_plugin.render_index_card_slot()
    script_html = infection_plugin.render_index_script_slot()
    assert "window.INDEX_HOOKS" in script_html
    assert "/infection-data" in script_html
    for element_id in ("infection_dot", "infection_state", "infection_text"):
        assert 'id="{}"'.format(element_id) in card_html
        assert element_id in script_html
