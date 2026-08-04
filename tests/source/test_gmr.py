"""Tests fuer source/gmr.py.

gmr.py importiert am Modul-Top-Level `from main import DEFAULT_PILOT_NAME,
debug_log` - main.py selbst ist zu gross/hardwarenah, um es hier zu
importieren, daher wird ein winziges Stub-Modul namens "main" in
sys.modules registriert, das exakt die von gmr.py benoetigten Namen
bereitstellt.

Die Admin-Seiten-Routen (/admin-koth, /admin-race, /gamemodes-view) laufen
seit der dynamischen Dashboard-Nav (siehe admin_dashboard.html's
PLUGIN_SLOT-Marker) ueber pico_web_api.send_admin_html_with_slot() statt
main.send_html_file() direkt - pico_web_api.py bindet SEIN EIGENES
`from main import send_html_file` beim EIGENEN ersten Import fest an den zu
dem Zeitpunkt aktiven main-Stub. Ohne die sys.modules-Purges unten wuerde
ein bereits (von einem frueheren Test) importiertes pico_web_api/
plugin_manager quer durch alle Tests an dessen laengst verworfenem Stub
haengen bleiben, statt am main-Stub DIESES Tests."""
import sys

import pytest


@pytest.fixture
def gmr(install_stub_module, fresh_import):
    sent = []

    async def fake_send_html_file(writer, path):
        sent.append((writer, path))

    install_stub_module(
        "main",
        DEFAULT_PILOT_NAME="TestPilot",
        debug_log=lambda message: None,
        send_html_file=fake_send_html_file,
    )
    sys.modules.pop("pico_web_api", None)
    sys.modules.pop("plugin_manager", None)
    module = fresh_import("gmr")
    module._sent = sent
    yield module
    sys.modules.pop("pico_web_api", None)
    sys.modules.pop("plugin_manager", None)


class FakeWriter:
    pass


@pytest.mark.asyncio
async def test_handle_admin_and_routes_serves_admin_koth_page(gmr):
    writer = FakeWriter()
    handled = await gmr.handle_admin_and_routes(writer, "/admin-koth", "GET", {}, {})
    assert handled is True
    assert gmr._sent == [(writer, gmr.ADMIN_KOTH_HTML_PATH)]


@pytest.mark.asyncio
async def test_handle_admin_and_routes_serves_admin_race_page(gmr):
    writer = FakeWriter()
    handled = await gmr.handle_admin_and_routes(writer, "/admin-race", "GET", {}, {})
    assert handled is True
    assert gmr._sent == [(writer, gmr.ADMIN_RACE_HTML_PATH)]


@pytest.mark.asyncio
async def test_handle_admin_and_routes_serves_gamemodes_view(gmr):
    writer = FakeWriter()
    handled = await gmr.handle_admin_and_routes(writer, "/gamemodes-view", "GET", {}, {})
    assert handled is True
    assert gmr._sent == [(writer, gmr.GAMEMODES_VIEW_HTML_PATH)]


@pytest.mark.asyncio
async def test_handle_admin_and_routes_unknown_path_returns_false(gmr):
    writer = FakeWriter()
    handled = await gmr.handle_admin_and_routes(writer, "/does-not-exist", "GET", {}, {})
    assert handled is False


@pytest.mark.asyncio
async def test_handle_admin_and_routes_delegates_koth_prefixed_routes(gmr, monkeypatch):
    import koth_mode

    async def fake_handle_koth_route(writer, path, method, query, body, manager):
        return True

    monkeypatch.setattr(koth_mode, "handle_koth_route", fake_handle_koth_route)
    writer = FakeWriter()
    handled = await gmr.handle_admin_and_routes(writer, "/koth-data", "GET", {}, {})
    assert handled is True


@pytest.mark.asyncio
async def test_handle_admin_and_routes_delegates_race_prefixed_routes(gmr, monkeypatch):
    import race_mode

    async def fake_handle_race_route(writer, path, method, query, body, manager):
        return True

    monkeypatch.setattr(race_mode, "handle_race_route", fake_handle_race_route)
    writer = FakeWriter()
    handled = await gmr.handle_admin_and_routes(writer, "/race-data", "GET", {}, {})
    assert handled is True


def test_ensure_koth_manager_is_singleton(gmr):
    first = gmr.ensure_koth_manager()
    second = gmr.ensure_koth_manager()
    assert first is second


def test_ensure_race_manager_is_singleton(gmr):
    first = gmr.ensure_race_manager()
    second = gmr.ensure_race_manager()
    assert first is second


def test_start_tasks_is_idempotent(gmr):
    """Nur 2 Tasks (koth+race): der Shooter-Spielmodus ist komplett aus
    gmr.py entfernt - seine Schleife wird als Plugin (siehe
    source/mods/shooter/main.py) von plugin_manager.run_loops() getrieben."""
    import asyncio

    async def _run():
        tasks_a = gmr.start_tasks()
        tasks_b = gmr.start_tasks()
        assert tasks_a is tasks_b
        assert len(tasks_a) == 2
        for task in tasks_a:
            task.cancel()

    asyncio.run(_run())
