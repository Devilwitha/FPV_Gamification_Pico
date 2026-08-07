"""Tests fuer source/pico_web_api.py - HTTP-Endpunkte fuer das Plugin-/
Store-System (siehe dortiger Modul-Docstring).

pico_web_api.py importiert am Modul-Top-Level `from main import
send_html_file, debug_log` - main.py selbst ist zu gross/hardwarenah fuer
diese Tests, daher wird (wie in test_gmr.py) ein winziges Stub-Modul namens
"main" registriert.
"""
import asyncio
import base64
import io
import json
import os
import zipfile

import pytest


@pytest.fixture
def pico_web_api(install_stub_module, fresh_import):
    sent_html_calls = []

    async def fake_send_html_file(writer, path):
        sent_html_calls.append(path)
        writer.write(b"HTTP/1.1 200 OK\r\n\r\n<html>" + path.encode() + b"</html>")

    def fake_safe_base64_file_to_file(input_file, output_file):
        return True

    install_stub_module(
        "main",
        send_html_file=fake_send_html_file,
        debug_log=lambda message: None,
        safe_base64_file_to_file=fake_safe_base64_file_to_file,
    )
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


def test_send_admin_html_with_slot_replaces_multiple_markers(pico_web_api, fake_writer, monkeypatch, isolated_cwd):
    """admin_dashboard.html braucht mehrere unabhaengige Slots (nav/card/
    stat/script) auf derselben Seite - jeder Marker bekommt nur sein eigenes
    Plugin-HTML, unbenutzte Marker bleiben als harmlose Kommentare stehen."""
    import plugin_manager

    with open("admin_dashboard.html", "w") as f:
        f.write(
            "<!--PLUGIN_SLOT:dashboard_nav--><!--PLUGIN_SLOT:dashboard_card-->"
            "<!--PLUGIN_SLOT:dashboard_stat-->"
        )

    fragments = {"dashboard_nav": "<a>Shooter</a>", "dashboard_card": "<a class=card>Shooter</a>"}
    monkeypatch.setattr(plugin_manager, "get_ui_slot_html", lambda slot: fragments.get(slot, ""))

    asyncio.run(pico_web_api.send_admin_html_with_slot(
        fake_writer, "admin_dashboard.html", ["dashboard_nav", "dashboard_card", "dashboard_stat"],
    ))

    assert pico_web_api._test_sent_html_calls == []
    assert b"<a>Shooter</a>" in fake_writer.response
    assert b"<a class=card>Shooter</a>" in fake_writer.response
    assert b"<!--PLUGIN_SLOT:dashboard_stat-->" in fake_writer.response  # unbenutzter Slot bleibt als Kommentar


def test_send_admin_html_with_slot_falls_back_when_no_plugin_uses_any_of_multiple_slots(
    pico_web_api, fake_writer, monkeypatch, isolated_cwd
):
    import plugin_manager

    monkeypatch.setattr(plugin_manager, "get_ui_slot_html", lambda slot: "")

    asyncio.run(pico_web_api.send_admin_html_with_slot(
        fake_writer, "admin_dashboard.html", ["dashboard_nav", "dashboard_card"],
    ))

    assert pico_web_api._test_sent_html_calls == ["admin_dashboard.html"]


def test_send_admin_html_with_slot_static_slots_supplement_plugin_slots(
    pico_web_api, fake_writer, monkeypatch, isolated_cwd
):
    """static_slots (siehe send_index_html()'s "index_gamemodes_hub") wird
    genau wie ein normaler Plugin-Slot per Marker ersetzt, auch wenn KEIN
    Plugin ueberhaupt einen der slot_names belegt."""
    import plugin_manager

    with open("index.html", "w") as f:
        f.write("<!--PLUGIN_SLOT:index_card--><!--PLUGIN_SLOT:index_gamemodes_hub-->")

    monkeypatch.setattr(plugin_manager, "get_ui_slot_html", lambda slot: "")

    asyncio.run(pico_web_api.send_admin_html_with_slot(
        fake_writer, "index.html", ["index_card"],
        static_slots={"index_gamemodes_hub": "<a>Game Mods</a>"},
    ))

    assert pico_web_api._test_sent_html_calls == []
    assert b"<a>Game Mods</a>" in fake_writer.response
    assert b"PLUGIN_SLOT:index_card" in fake_writer.response  # unbenutzter Plugin-Slot bleibt Kommentar


def test_send_admin_html_with_slot_falls_back_when_static_slots_all_empty(
    pico_web_api, fake_writer, monkeypatch, isolated_cwd
):
    import plugin_manager

    monkeypatch.setattr(plugin_manager, "get_ui_slot_html", lambda slot: "")

    asyncio.run(pico_web_api.send_admin_html_with_slot(
        fake_writer, "index.html", ["index_card"], static_slots={"index_gamemodes_hub": ""},
    ))

    assert pico_web_api._test_sent_html_calls == ["index.html"]


def test_render_gamemodes_hub_card_empty_when_no_plugin_uses_gamemodes_button(pico_web_api, monkeypatch):
    import plugin_manager

    monkeypatch.setattr(plugin_manager, "get_ui_slot_html", lambda slot: "")

    assert pico_web_api._render_gamemodes_hub_card() == ""


def test_render_gamemodes_hub_card_renders_once_when_any_plugin_active(pico_web_api, monkeypatch):
    """Der Hub-Karte ist egal WELCHES/wie viele Spielmodus-Plugins aktiv
    sind - sie fragt nur, ob gamemodes_button ueberhaupt Inhalt hat, und
    rendert dann GENAU EINE Karte (keine Duplizierung pro Plugin)."""
    import plugin_manager

    monkeypatch.setattr(
        plugin_manager, "get_ui_slot_html",
        lambda slot: "<a>Admin KOTH</a><a>Admin Race</a>" if slot == "gamemodes_button" else "",
    )

    html = pico_web_api._render_gamemodes_hub_card()
    assert '/gamemodes-view' in html
    assert 'index.gamemodesTitle' in html


@pytest.mark.asyncio
async def test_send_index_html_includes_hub_card_when_gamemodes_plugin_active(
    pico_web_api, fake_writer, monkeypatch, isolated_cwd
):
    import plugin_manager

    with open("index.html", "w") as f:
        f.write("<!--PLUGIN_SLOT:index_card--><!--PLUGIN_SLOT:index_gamemodes_hub-->")

    monkeypatch.setattr(
        plugin_manager, "get_ui_slot_html",
        lambda slot: "<a>Infection Karte</a>" if slot == "index_card" else (
            "<a>Admin KOTH</a>" if slot == "gamemodes_button" else ""
        ),
    )

    await pico_web_api.send_index_html(fake_writer, "index.html")

    assert b"Infection Karte" in fake_writer.response
    assert b"/gamemodes-view" in fake_writer.response


@pytest.mark.asyncio
async def test_handle_pico_api_route_serves_admin_plugins_page(pico_web_api, fake_writer):
    handled = await pico_web_api.handle_pico_api_route(fake_writer, "/admin-plugins", "GET", {}, {})
    assert handled is True
    assert b"200 OK" in fake_writer.response
    assert b"PLUGINS" in fake_writer.response


def test_admin_plugins_html_renders_plugin_description_and_escapes_it(pico_web_api):
    """Sowohl die "Installierte Plugins"- als auch die Store-Liste sollen
    p.description anzeigen (siehe plugin_manager.list_plugins()/webshop
    _list_store_plugins(), beide liefern jetzt ein "description"-Feld) -
    und dabei ueber die gemeinsame esc()-Hilfsfunktion escapen, da
    description freier Text aus einem hochgeladenen/heruntergeladenen
    manifest.json ist (nicht vertrauenswuerdig)."""
    assert "p.description" in pico_web_api.ADMIN_PLUGINS_HTML
    assert "pdesc" in pico_web_api.ADMIN_PLUGINS_HTML
    assert "function esc(" in pico_web_api.ADMIN_PLUGINS_HTML
    assert "esc(p.description)" in pico_web_api.ADMIN_PLUGINS_HTML
    assert "esc(p.name)" in pico_web_api.ADMIN_PLUGINS_HTML


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


@pytest.mark.asyncio
async def test_handle_pico_api_route_plugin_ui_schema(pico_web_api, fake_writer, monkeypatch):
    import plugin_manager

    monkeypatch.setattr(
        plugin_manager, "get_ui_schema", lambda name: {"title": "Demo"} if name == "shooter" else None
    )

    handled = await pico_web_api.handle_pico_api_route(fake_writer, "/api/plugin-ui/shooter", "GET", {}, {})
    assert handled is True
    assert fake_writer.json() == {"ok": True, "schema": {"title": "Demo"}}


@pytest.mark.asyncio
async def test_handle_pico_api_route_plugin_ui_schema_missing_returns_404(pico_web_api, fake_writer, monkeypatch):
    import plugin_manager

    monkeypatch.setattr(plugin_manager, "get_ui_schema", lambda name: None)

    handled = await pico_web_api.handle_pico_api_route(fake_writer, "/api/plugin-ui/unknown", "GET", {}, {})
    assert handled is True
    assert b"404" in fake_writer.response
    assert fake_writer.json()["ok"] is False


# ---------------------------------------------------------------------------
# Plugin-ZIP-Upload ("Plugin hochladen" auf der /admin-plugins-Seite, siehe
# zip_helpers.py) - Chunk-Upload + Finalize, End-to-End ueber echte ZIP-Bytes
# und ein echtes "mods"-Package (gleiches sys.path-Muster wie
# test_plugin_manager.py/test_zip_helpers.py: "mods" ist ein echtes Python-
# Package, tmp_path muss daher VOR source/ in sys.path liegen).
# ---------------------------------------------------------------------------

def _purge_mods_modules_for_upload_tests():
    import sys as _sys
    for key in list(_sys.modules.keys()):
        if key == "mods" or key.startswith("mods."):
            del _sys.modules[key]


def _build_zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for i, (name, content) in enumerate(entries.items()):
            data = content.encode("utf-8") if isinstance(content, str) else content
            method = zipfile.ZIP_STORED if i % 2 == 0 else zipfile.ZIP_DEFLATED
            archive.writestr(name, data, method)
    return buf.getvalue()


async def _upload_zip_via_routes(pico_web_api, zip_bytes, filename_no_ext, chunk_size=1024):
    from tests.source.conftest import FakeWriter

    b64 = base64.b64encode(zip_bytes).decode("ascii")
    chunks = [b64[i:i + chunk_size] for i in range(0, len(b64), chunk_size)] or [""]
    total = len(chunks)
    for idx, chunk in enumerate(chunks):
        writer = FakeWriter()
        body = {"index": str(idx), "total": str(total), "data": chunk}
        if idx == 0:
            body["name"] = filename_no_ext
        handled = await pico_web_api.handle_pico_api_route(writer, "/api/plugins/upload-chunk", "POST", {}, body)
        assert handled is True
        assert writer.json()["ok"] is True, writer.json()

    finalize_writer = FakeWriter()
    handled = await pico_web_api.handle_pico_api_route(finalize_writer, "/api/plugins/upload-finalize", "POST", {}, {})
    assert handled is True
    return finalize_writer


@pytest.fixture
def real_base64_decode(pico_web_api, monkeypatch):
    """Ersetzt den Chunk-Fixture-Stub (der immer nur True zurueckgibt) durch
    eine echte Base64-Dekodierung - noetig fuer Tests, die den entpackten
    ZIP-Inhalt tatsaechlich pruefen wollen."""
    def _decode(input_file, output_file):
        with open(input_file, "r") as f:
            text = f.read()
        with open(output_file, "wb") as f:
            f.write(base64.b64decode(text))
        return True

    monkeypatch.setattr(pico_web_api, "safe_base64_file_to_file", _decode)
    return pico_web_api


@pytest.fixture
def mods_syspath(tmp_path, monkeypatch):
    """Macht das per Upload entpackte "mods"-Package unter tmp_path fuer
    plugin_manager.load_single_plugin() importierbar (isolated_cwd, siehe
    conftest.py, setzt bereits das Arbeitsverzeichnis auf denselben Pfad -
    "mods" muss zusaetzlich auf sys.path liegen, damit `import mods.<name>`
    funktioniert)."""
    monkeypatch.syspath_prepend(str(tmp_path))
    _purge_mods_modules_for_upload_tests()
    yield
    _purge_mods_modules_for_upload_tests()


SIMPLE_PLUGIN_MANIFEST = json.dumps({
    "name": "placeholder", "version": "1.0.0", "entry": "main.py",
    "enabled": True, "ui_slots": {}, "route_prefixes": [],
})
SIMPLE_PLUGIN_MAIN = "calls = []\n\ndef setup(context):\n    calls.append('setup')\n"


@pytest.mark.asyncio
async def test_plugin_zip_upload_installs_and_activates_plugin(
    pico_web_api, real_base64_decode, mods_syspath, isolated_cwd
):
    import plugin_manager

    zip_bytes = _build_zip_bytes({
        "manifest.json": SIMPLE_PLUGIN_MANIFEST,
        "shooter/main.py": SIMPLE_PLUGIN_MAIN,  # Unterordner -> muss flach landen
    })

    finalize_writer = await _upload_zip_via_routes(pico_web_api, zip_bytes, "shooter")

    result = finalize_writer.json()
    assert result["ok"] is True
    assert os.path.isfile("mods/shooter/manifest.json")
    assert os.path.isfile("mods/shooter/main.py")
    assert plugin_manager.is_active("shooter")


@pytest.mark.asyncio
async def test_plugin_zip_upload_sanitizes_name_from_filename(
    pico_web_api, real_base64_decode, mods_syspath, isolated_cwd
):
    """Der Zielordnername kommt aus dem hochgeladenen Dateinamen (Frontend
    schneidet ".zip" ab, siehe pluginNameFromZipFilename()) - der Server
    sanitisiert ihn zusaetzlich nochmal defensiv (_sanitize_plugin_name())."""
    zip_bytes = _build_zip_bytes({"manifest.json": SIMPLE_PLUGIN_MANIFEST, "main.py": SIMPLE_PLUGIN_MAIN})

    finalize_writer = await _upload_zip_via_routes(pico_web_api, zip_bytes, "My Cool Plugin!!")

    assert finalize_writer.json()["ok"] is True
    assert os.path.isdir("mods/MyCoolPlugin")


@pytest.mark.asyncio
async def test_plugin_zip_upload_reports_failure_when_plugin_crashes_after_install(
    pico_web_api, real_base64_decode, mods_syspath, isolated_cwd
):
    """Reproduziert das real beobachtete Verhalten: die ZIP ist strukturell
    gueltig (manifest.json + main.py vorhanden) und wird komplett nach
    mods/<name>/ geschrieben, aber der hochgeladene Code wirft beim Laden
    eine Exception (siehe plugin_manager.py's Crash-Isolation, has_error
    wird gesetzt). Die Finalize-Antwort MUSS das als Fehlschlag melden statt
    pauschal "ok": true - vorher wurde jeder erfolgreiche Dateischreibvorgang
    als Erfolg gewertet, unabhaengig davon, ob das Plugin danach ueberhaupt
    lief."""
    crashing_main = "def setup(context):\n    raise RuntimeError('boom in setup')\n"
    zip_bytes = _build_zip_bytes({"manifest.json": SIMPLE_PLUGIN_MANIFEST, "main.py": crashing_main})

    finalize_writer = await _upload_zip_via_routes(pico_web_api, zip_bytes, "shooter")

    result = finalize_writer.json()
    assert result["ok"] is False
    assert "boom in setup" in result["error"]
    assert "500" in finalize_writer.status_line
    # Dateien bleiben trotzdem auf dem Geraet liegen (kein automatisches
    # Rollback) - der Nutzer sieht den Fehler und kann den Code korrigieren.
    assert os.path.isfile("mods/shooter/main.py")


@pytest.mark.asyncio
async def test_plugin_zip_upload_chunk_rejects_empty_name(pico_web_api, fake_writer):
    handled = await pico_web_api.handle_pico_api_route(
        fake_writer, "/api/plugins/upload-chunk", "POST", {}, {"index": "0", "total": "1", "data": "AA==", "name": "!!!"}
    )
    assert handled is True
    assert "400" in fake_writer.status_line
    assert fake_writer.json()["ok"] is False


@pytest.mark.asyncio
async def test_plugin_zip_upload_finalize_without_prior_chunk_fails(pico_web_api, fake_writer):
    handled = await pico_web_api.handle_pico_api_route(fake_writer, "/api/plugins/upload-finalize", "POST", {}, {})
    assert handled is True
    assert "500" in fake_writer.status_line
    assert fake_writer.json()["ok"] is False


@pytest.mark.asyncio
async def test_plugin_zip_upload_finalize_with_missing_chunks_fails(pico_web_api, fake_writer, isolated_cwd):
    handled = await pico_web_api.handle_pico_api_route(
        fake_writer, "/api/plugins/upload-chunk", "POST", {}, {"index": "0", "total": "3", "data": "AAAA", "name": "shooter"}
    )
    assert handled is True

    finalize_writer_cls = fake_writer.__class__
    finalize_writer = finalize_writer_cls()
    handled = await pico_web_api.handle_pico_api_route(finalize_writer, "/api/plugins/upload-finalize", "POST", {}, {})
    assert handled is True
    assert "500" in finalize_writer.status_line
    assert "unvollstaendig" in finalize_writer.json()["error"]


@pytest.mark.asyncio
async def test_plugin_zip_upload_rejects_zip_without_manifest(
    pico_web_api, real_base64_decode, mods_syspath, isolated_cwd
):
    zip_bytes = _build_zip_bytes({"main.py": SIMPLE_PLUGIN_MAIN})

    finalize_writer = await _upload_zip_via_routes(pico_web_api, zip_bytes, "shooter")

    result = finalize_writer.json()
    assert result["ok"] is False
    assert not os.path.isdir("mods/shooter")
