"""Tests fuer source/gmr.py.

Seit Shooter/Race/Infection/KOTH alle als eigenstaendige Plugins leben
(siehe jeweils source/mods/<name>/main.py und
tests/source/test_<name>_plugin.py), bleibt hier nur noch die Auslieferung
der oeffentlichen "/gamemodes-view"-Zuschauer-Seite uebrig - gmr.py
importiert dafuer lediglich pico_web_api lazy (kein main-Stub mehr fuer
eigene Manager/Task-Verdrahtung noetig, main-Stub wird nur noch von
pico_web_api.py's eigenem `from main import send_html_file` gebraucht).

Die Route laeuft ueber pico_web_api.send_admin_html_with_slot() statt
main.send_html_file() direkt - pico_web_api.py bindet SEIN EIGENES `from
main import send_html_file` beim EIGENEN ersten Import fest an den zu dem
Zeitpunkt aktiven main-Stub. Ohne die sys.modules-Purges unten wuerde ein
bereits (von einem frueheren Test) importiertes pico_web_api quer durch
alle Tests an dessen laengst verworfenem Stub haengen bleiben, statt am
main-Stub DIESES Tests."""
import sys

import pytest


@pytest.fixture
def gmr(install_stub_module, fresh_import):
    sent = []

    async def fake_send_html_file(writer, path):
        sent.append((writer, path))

    def fake_safe_base64_file_to_file(input_file, output_file):
        return True

    install_stub_module(
        "main",
        DEFAULT_PILOT_NAME="TestPilot",
        debug_log=lambda message: None,
        send_html_file=fake_send_html_file,
        safe_base64_file_to_file=fake_safe_base64_file_to_file,
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
