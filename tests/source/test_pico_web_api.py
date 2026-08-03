"""Tests fuer source/pico_web_api.py - HTTP-Endpunkte fuer das Plugin-/
Store-System (siehe dortiger Modul-Docstring).

pico_web_api.py importiert am Modul-Top-Level `from main import
send_html_file, debug_log` - main.py selbst ist zu gross/hardwarenah fuer
diese Tests, daher wird (wie in test_gmr.py) ein winziges Stub-Modul namens
"main" registriert.
"""
import asyncio
import json

import pytest


@pytest.fixture
def pico_web_api(install_stub_module, fresh_import):
    sent_html_calls = []

    async def fake_send_html_file(writer, path):
        sent_html_calls.append(path)
        writer.write(b"HTTP/1.1 200 OK\r\n\r\n<html>" + path.encode() + b"</html>")

    install_stub_module("main", send_html_file=fake_send_html_file, debug_log=lambda message: None)
    module = fresh_import("pico_web_api")
    module._test_sent_html_calls = sent_html_calls
    return module


def test_send_admin_html_with_slot_falls_back_when_no_plugin_uses_slot(pico_web_api, fake_writer, monkeypatch, isolated_cwd):
    import plugin_manager

    monkeypatch.setattr(plugin_manager, "get_ui_slot_html", lambda slot: "")

    asyncio.run(pico_web_api.send_admin_html_with_slot(fake_writer, "admin_system.html", "system"))

    assert pico_web_api._test_sent_html_calls == ["admin_system.html"]


def test_send_admin_html_with_slot_replaces_marker(pico_web_api, fake_writer, monkeypatch, isolated_cwd):
    import plugin_manager

    with open("admin_system.html", "w") as f:
        f.write("<div>before</div><!--PLUGIN_SLOT:system--><div>after</div>")

    monkeypatch.setattr(plugin_manager, "get_ui_slot_html", lambda slot: "<b>plugin html</b>" if slot == "system" else "")

    asyncio.run(pico_web_api.send_admin_html_with_slot(fake_writer, "admin_system.html", "system"))

    assert pico_web_api._test_sent_html_calls == []  # send_html_file NICHT verwendet
    assert b"<b>plugin html</b>" in fake_writer.response
    assert b"PLUGIN_SLOT" not in fake_writer.response


@pytest.mark.asyncio
async def test_handle_pico_api_route_serves_admin_plugins_page(pico_web_api, fake_writer):
    handled = await pico_web_api.handle_pico_api_route(fake_writer, "/admin-plugins", "GET", {}, {})
    assert handled is True
    assert b"200 OK" in fake_writer.response
    assert b"PLUGINS" in fake_writer.response


@pytest.mark.asyncio
async def test_handle_pico_api_route_lists_plugins(pico_web_api, fake_writer, monkeypatch):
    import plugin_manager

    fake_list = [{"name": "demo", "version": "1.0.0", "enabled": True, "has_error": False, "error_message": "", "active": True}]
    monkeypatch.setattr(plugin_manager, "list_plugins", lambda: fake_list)

    handled = await pico_web_api.handle_pico_api_route(fake_writer, "/api/plugins", "GET", {}, {})
    assert handled is True
    assert fake_writer.json() == fake_list


@pytest.mark.asyncio
async def test_handle_pico_api_route_toggle_plugin(pico_web_api, fake_writer, monkeypatch):
    import plugin_manager

    calls = []

    def fake_set_state(name, enabled):
        calls.append((name, enabled))
        return {"name": name, "enabled": enabled}

    monkeypatch.setattr(plugin_manager, "set_plugin_state", fake_set_state)

    handled = await pico_web_api.handle_pico_api_route(
        fake_writer, "/api/plugins/demo/toggle", "POST", {}, {"enabled": "1"}
    )
    assert handled is True
    assert calls == [("demo", True)]
    assert fake_writer.json()["ok"] is True


@pytest.mark.asyncio
async def test_handle_pico_api_route_toggle_plugin_disable(pico_web_api, fake_writer, monkeypatch):
    import plugin_manager

    calls = []
    monkeypatch.setattr(plugin_manager, "set_plugin_state", lambda name, enabled: calls.append((name, enabled)) or {})

    await pico_web_api.handle_pico_api_route(fake_writer, "/api/plugins/demo/toggle", "POST", {}, {"enabled": "0"})
    assert calls == [("demo", False)]


@pytest.mark.asyncio
async def test_handle_pico_api_route_delete_plugin(pico_web_api, fake_writer, monkeypatch):
    import plugin_manager

    calls = []
    monkeypatch.setattr(plugin_manager, "delete_plugin", lambda name: calls.append(name))

    handled = await pico_web_api.handle_pico_api_route(fake_writer, "/api/plugins/demo/delete", "POST", {}, {})
    assert handled is True
    assert calls == ["demo"]
    assert fake_writer.json()["ok"] is True


@pytest.mark.asyncio
async def test_handle_pico_api_route_firmware_status(pico_web_api, fake_writer):
    import network_manager

    handled = await pico_web_api.handle_pico_api_route(fake_writer, "/api/firmware/status", "GET", {}, {})
    assert handled is True
    assert fake_writer.json() == network_manager.network_state


@pytest.mark.asyncio
async def test_handle_pico_api_route_store_list(pico_web_api, fake_writer, isolated_cwd):
    import network_manager

    network_manager._save_store_cache([{"name": "demo", "version": "1.0.0"}])

    handled = await pico_web_api.handle_pico_api_route(fake_writer, "/api/store/list", "GET", {}, {})
    assert handled is True
    assert fake_writer.json()["plugins"] == [{"name": "demo", "version": "1.0.0"}]


@pytest.mark.asyncio
async def test_handle_pico_api_route_store_download_requires_name(pico_web_api, fake_writer):
    handled = await pico_web_api.handle_pico_api_route(fake_writer, "/api/store/download", "POST", {}, {})
    assert handled is True
    assert "400" in fake_writer.status_line
    assert fake_writer.json()["ok"] is False


@pytest.mark.asyncio
async def test_handle_pico_api_route_store_download_schedules_background_task(pico_web_api, fake_writer, monkeypatch):
    calls = []

    async def fake_run_store_download(name):
        calls.append(name)

    monkeypatch.setattr(pico_web_api, "_run_store_download", fake_run_store_download)

    handled = await pico_web_api.handle_pico_api_route(
        fake_writer, "/api/store/download", "POST", {}, {"name": "demo"}
    )
    assert handled is True
    assert fake_writer.json()["ok"] is True

    # Der Download laeuft als Hintergrund-Task (siehe Docstring von
    # _run_store_download: die HTTP-Antwort ist bereits raus, bevor der
    # eigentliche WLAN-Download beginnt) - einmal die Event-Loop-Kontrolle
    # abgeben, damit der Task tatsaechlich anlaeuft.
    await asyncio.sleep(0)
    assert calls == ["demo"]


@pytest.mark.asyncio
async def test_handle_pico_api_route_unknown_path_returns_false(pico_web_api, fake_writer):
    handled = await pico_web_api.handle_pico_api_route(fake_writer, "/does-not-exist", "GET", {}, {})
    assert handled is False
