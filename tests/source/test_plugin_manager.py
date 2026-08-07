"""Tests fuer source/plugin_manager.py - die generische, crash-sichere
Plugin-Engine (Ordner-basierte Mods unter mods/<name>/, siehe dortiger
Modul-Docstring). Plugins werden als echte "mods.<name>.<entry>"-Unterpakete
importiert (nicht mehr exec()/compile()) - das ist notwendig, damit
MicroPythons Standard-Importmechanismus transparent main.mpy statt main.py
laden kann (Webshop-Store liefert Mods ausschliesslich vorkompiliert aus).

isolated_cwd (autouse, siehe conftest.py) sorgt dafuer, dass jeder Test in
einem frischen temporaeren Verzeichnis laeuft. Weil "mods" ein echtes
Python-Package ist, wird zusaetzlich tmp_path VOR source/ in sys.path
gesetzt und jeder sys.modules-Eintrag fuer "mods"/"mods.*" vor/nach jedem
Test entfernt - sonst wuerde ein bereits importiertes "mods"-Package (z.B.
mit dem echten source/mods/ als __path__) faelschlich weiterverwendet,
statt die frischen Testdateien in tmp_path zu finden. Gleiches
sys.path-Muster wie pico_simulator/run_firmware.py's echter Firmware-Lauf.
"""
import asyncio
import contextlib
import json
import os
import sys

import pytest


def _purge_mods_modules():
    for key in list(sys.modules.keys()):
        if key == "mods" or key.startswith("mods."):
            del sys.modules[key]


@pytest.fixture
def plugin_manager(install_stub_module, fresh_import, tmp_path, monkeypatch):
    logs = []
    install_stub_module("main", debug_log=lambda message: logs.append(message))

    monkeypatch.syspath_prepend(str(tmp_path))
    _purge_mods_modules()

    module = fresh_import("plugin_manager")
    module._test_logs = logs
    yield module

    _purge_mods_modules()


def _write_plugin(name, main_source, manifest_overrides=None):
    plugin_dir = os.path.join("mods", name)
    os.makedirs(plugin_dir, exist_ok=True)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "author": "Test",
        "entry": "main.py",
        "enabled": True,
        "has_error": False,
        "error_message": "",
        "loop_interval_ms": 20,
        "ui_slots": {},
        "route_prefixes": [],
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    with open(os.path.join(plugin_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f)
    with open(os.path.join(plugin_dir, "main.py"), "w") as f:
        f.write(main_source)
    return plugin_dir


SIMPLE_PLUGIN_SOURCE = """
calls = {"setup": 0, "loop": 0, "teardown": 0}

def setup(context):
    calls["setup"] += 1

def loop():
    calls["loop"] += 1

def teardown():
    calls["teardown"] += 1
"""

CRASHING_SETUP_SOURCE = """
def setup(context):
    raise RuntimeError("boom in setup")

def loop():
    pass
"""

CRASHING_LOOP_SOURCE = """
def setup(context):
    pass

def loop():
    raise RuntimeError("boom in loop")

def teardown():
    pass
"""

UI_SLOT_PLUGIN_SOURCE = """
def setup(context):
    pass

def loop():
    pass

def render_system_slot():
    return "<div>hello from plugin</div>"
"""

UI_SCHEMA_PLUGIN_SOURCE = """
def setup(context):
    pass

def loop():
    pass

def get_ui_schema():
    return {"title": "Demo", "sections": []}

def get_ui_schema_crashing():
    raise RuntimeError("boom in get_ui_schema")
"""

ROUTE_PLUGIN_SOURCE = """
def setup(context):
    pass

def loop():
    pass

async def handle_route(writer, request_path, request_method, query_params, body_params):
    if request_path == "/demo-crash":
        raise RuntimeError("boom in handle_route")
    if request_path.startswith("/demo-"):
        writer.write(b"handled:" + request_path.encode())
        return True
    return False
"""


def test_list_plugin_names_finds_only_dirs_with_manifest(plugin_manager):
    _write_plugin("has_manifest", SIMPLE_PLUGIN_SOURCE)
    os.makedirs(os.path.join("mods", "no_manifest"), exist_ok=True)
    names = plugin_manager.list_plugin_names()
    assert names == ["has_manifest"]


def test_load_all_plugins_calls_setup_and_activates(plugin_manager):
    _write_plugin("demo", SIMPLE_PLUGIN_SOURCE)
    plugin_manager.load_all_plugins()
    assert plugin_manager.is_active("demo") is True

    manifest = plugin_manager._load_manifest("demo")
    assert manifest["enabled"] is True
    assert manifest["has_error"] is False


def test_load_all_plugins_creates_init_files_automatically(plugin_manager):
    """mods/__init__.py und mods/<name>/__init__.py muessen NICHT vom
    Plugin-Autor mitgeliefert werden - plugin_manager legt sie selbst an,
    damit ein per Webshop-Download nachinstalliertes Mod nicht daran
    scheitert (siehe _ensure_init_files())."""
    _write_plugin("demo", SIMPLE_PLUGIN_SOURCE)
    assert not os.path.isfile(os.path.join("mods", "__init__.py"))
    plugin_manager.load_all_plugins()
    assert os.path.isfile(os.path.join("mods", "__init__.py"))
    assert os.path.isfile(os.path.join("mods", "demo", "__init__.py"))


def test_ensure_init_files_does_not_duplicate_when_compiled_init_exists(plugin_manager):
    """Reproduziert einen real beobachteten Absturz: der Webshop-Store
    liefert Mods ausschliesslich vorkompiliert (siehe
    windows/source2/plugin_packager.py's pack_mod_to_zip() - jede .py-Datei, auch ein
    vom Autor mitgeliefertes __init__.py, wird zu __init__.mpy kompiliert).
    Landet so ein Mod mit bereits vorhandenem __init__.mpy in mods/<name>/
    (Webshop-Download oder ZIP-Upload, siehe zip_helpers.py), darf
    _ensure_init_files() NICHT zusaetzlich ein leeres __init__.py anlegen -
    zwei gleichzeitige __init__-Varianten im selben Ordner liessen den
    anschliessenden Import von mods.<name>.main auf echter Hardware mit
    "No module named" fehlschlagen, obwohl main.mpy nachweislich vorlag."""
    os.makedirs(os.path.join("mods", "koth"), exist_ok=True)
    with open(os.path.join("mods", "koth", "__init__.mpy"), "wb") as f:
        f.write(b"\xfdfake-mpy-bytecode")

    plugin_manager._ensure_init_files("koth")

    assert not os.path.isfile(os.path.join("mods", "koth", "__init__.py"))
    assert os.path.isfile(os.path.join("mods", "koth", "__init__.mpy"))
    # Das TOP-LEVEL mods/__init__.py fehlte hier komplett -> muss trotzdem
    # (als .py, es gibt keine kompilierte Variante davon) angelegt werden.
    assert os.path.isfile(os.path.join("mods", "__init__.py"))


def test_ensure_init_files_still_creates_py_when_neither_variant_exists(plugin_manager):
    os.makedirs(os.path.join("mods", "demo"), exist_ok=True)

    plugin_manager._ensure_init_files("demo")

    assert os.path.isfile(os.path.join("mods", "__init__.py"))
    assert os.path.isfile(os.path.join("mods", "demo", "__init__.py"))


def test_load_all_plugins_skips_disabled_plugin(plugin_manager):
    _write_plugin("disabled_mod", SIMPLE_PLUGIN_SOURCE, {"enabled": False})
    plugin_manager.load_all_plugins()
    assert plugin_manager.is_active("disabled_mod") is False


def test_setup_exception_marks_plugin_crashed_and_not_active(plugin_manager):
    _write_plugin("crashy_setup", CRASHING_SETUP_SOURCE)
    plugin_manager.load_all_plugins()

    assert plugin_manager.is_active("crashy_setup") is False
    manifest = plugin_manager._load_manifest("crashy_setup")
    assert manifest["enabled"] is False
    assert manifest["has_error"] is True
    assert "boom in setup" in manifest["error_message"]


def test_list_plugins_reports_crashed_and_active_plugins(plugin_manager):
    _write_plugin("good", SIMPLE_PLUGIN_SOURCE)
    _write_plugin("bad", CRASHING_SETUP_SOURCE)
    plugin_manager.load_all_plugins()

    plugins = {p["name"]: p for p in plugin_manager.list_plugins()}
    assert plugins["good"]["active"] is True
    assert plugins["good"]["has_error"] is False
    assert plugins["bad"]["active"] is False
    assert plugins["bad"]["has_error"] is True


def test_set_plugin_state_disable_calls_teardown_and_deactivates(plugin_manager):
    _write_plugin("demo", SIMPLE_PLUGIN_SOURCE)
    plugin_manager.load_all_plugins()
    assert plugin_manager.is_active("demo") is True

    manifest = plugin_manager.set_plugin_state("demo", False)
    assert manifest["enabled"] is False
    assert plugin_manager.is_active("demo") is False


def test_set_plugin_state_enable_reactivates_and_clears_error(plugin_manager):
    _write_plugin("crashy_setup", CRASHING_SETUP_SOURCE)
    plugin_manager.load_all_plugins()
    assert plugin_manager.is_active("crashy_setup") is False

    # Fix die Plugin-Quelle (simuliert einen erneuten Deploy einer
    # korrigierten Version) - set_plugin_state() muss den alten
    # sys.modules-Cache verwerfen, sonst wuerde der Re-Import den ALTEN,
    # noch abstuerzenden Code weiterverwenden.
    with open(os.path.join("mods", "crashy_setup", "main.py"), "w") as f:
        f.write(SIMPLE_PLUGIN_SOURCE)

    manifest = plugin_manager.set_plugin_state("crashy_setup", True)
    assert manifest["has_error"] is False
    assert plugin_manager.is_active("crashy_setup") is True


def test_delete_plugin_removes_directory_and_active_entry(plugin_manager):
    _write_plugin("demo", SIMPLE_PLUGIN_SOURCE)
    plugin_manager.load_all_plugins()
    assert plugin_manager.is_active("demo") is True

    plugin_manager.delete_plugin("demo")
    assert plugin_manager.is_active("demo") is False
    assert not os.path.isdir(os.path.join("mods", "demo"))
    assert "mods.demo.main" not in sys.modules


def test_get_ui_slot_html_aggregates_active_plugin_fragments(plugin_manager):
    _write_plugin("ui_demo", UI_SLOT_PLUGIN_SOURCE, {"ui_slots": {"system": "render_system_slot"}})
    plugin_manager.load_all_plugins()

    html = plugin_manager.get_ui_slot_html("system")
    assert "hello from plugin" in html


def test_get_ui_slot_html_empty_when_no_plugin_uses_slot(plugin_manager):
    _write_plugin("demo", SIMPLE_PLUGIN_SOURCE)
    plugin_manager.load_all_plugins()
    assert plugin_manager.get_ui_slot_html("system") == ""


def test_get_ui_schema_returns_declared_schema_for_active_plugin(plugin_manager):
    _write_plugin("ui_demo", UI_SCHEMA_PLUGIN_SOURCE, {"ui_pages": {"main": "get_ui_schema"}})
    plugin_manager.load_all_plugins()

    schema = plugin_manager.get_ui_schema("ui_demo")
    assert schema == {"title": "Demo", "sections": []}


def test_get_ui_schema_none_when_not_declared(plugin_manager):
    _write_plugin("demo", SIMPLE_PLUGIN_SOURCE)
    plugin_manager.load_all_plugins()
    assert plugin_manager.get_ui_schema("demo") is None


def test_get_ui_schema_none_when_plugin_not_active(plugin_manager):
    _write_plugin("ui_demo", UI_SCHEMA_PLUGIN_SOURCE, {"ui_pages": {"main": "get_ui_schema"}, "enabled": False})
    plugin_manager.load_all_plugins()
    assert plugin_manager.get_ui_schema("ui_demo") is None


def test_get_ui_schema_crash_marks_plugin_crashed(plugin_manager):
    _write_plugin(
        "ui_demo", UI_SCHEMA_PLUGIN_SOURCE, {"ui_pages": {"main": "get_ui_schema_crashing"}}
    )
    plugin_manager.load_all_plugins()

    assert plugin_manager.get_ui_schema("ui_demo") is None
    assert plugin_manager.is_active("ui_demo") is False
    manifest = plugin_manager._load_manifest("ui_demo")
    assert manifest["has_error"] is True


def test_list_plugins_reports_description(plugin_manager):
    _write_plugin("demo", SIMPLE_PLUGIN_SOURCE, {"description": "Ein Testmod"})
    _write_plugin("nodesc", SIMPLE_PLUGIN_SOURCE)
    plugin_manager.load_all_plugins()

    by_name = {entry["name"]: entry for entry in plugin_manager.list_plugins()}
    assert by_name["demo"]["description"] == "Ein Testmod"
    assert by_name["nodesc"]["description"] == ""


def test_load_manifest_truncates_overlong_description(plugin_manager):
    _write_plugin("demo", SIMPLE_PLUGIN_SOURCE, {"description": "x" * 500})
    manifest = plugin_manager._load_manifest("demo")
    assert len(manifest["description"]) == 200


def test_load_manifest_defaults_missing_description_to_empty_string(plugin_manager, tmp_path):
    """manifest.json ohne "description" (z.B. aeltere, vor diesem Feld
    hochgeladene Plugins) darf nicht crashen - _normalize_manifest() muss
    einen leeren String defaulten."""
    plugin_dir = os.path.join("mods", "legacy")
    os.makedirs(plugin_dir, exist_ok=True)
    with open(os.path.join(plugin_dir, "manifest.json"), "w") as f:
        json.dump({"name": "legacy", "entry": "main.py"}, f)
    with open(os.path.join(plugin_dir, "main.py"), "w") as f:
        f.write(SIMPLE_PLUGIN_SOURCE)

    manifest = plugin_manager._load_manifest("legacy")
    assert manifest["description"] == ""


def test_list_plugins_reports_has_ui_flag(plugin_manager):
    _write_plugin("ui_demo", UI_SCHEMA_PLUGIN_SOURCE, {"ui_pages": {"main": "get_ui_schema"}})
    _write_plugin("demo", SIMPLE_PLUGIN_SOURCE)
    plugin_manager.load_all_plugins()

    by_name = {entry["name"]: entry for entry in plugin_manager.list_plugins()}
    assert by_name["ui_demo"]["has_ui"] is True
    assert by_name["demo"]["has_ui"] is False


@pytest.mark.asyncio
async def test_run_loops_calls_loop_repeatedly(plugin_manager):
    _write_plugin("demo", SIMPLE_PLUGIN_SOURCE, {"loop_interval_ms": 5})
    plugin_manager.load_all_plugins()

    task = asyncio.create_task(plugin_manager.run_loops())
    await asyncio.sleep(0.1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    module = plugin_manager._active_plugins["demo"]["module"]
    assert module.calls["loop"] > 0


@pytest.mark.asyncio
async def test_run_loops_marks_crash_and_deactivates_on_loop_exception(plugin_manager):
    _write_plugin("crashy_loop", CRASHING_LOOP_SOURCE, {"loop_interval_ms": 5})
    plugin_manager.load_all_plugins()
    assert plugin_manager.is_active("crashy_loop") is True

    task = asyncio.create_task(plugin_manager.run_loops())
    await asyncio.sleep(0.1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert plugin_manager.is_active("crashy_loop") is False
    manifest = plugin_manager._load_manifest("crashy_loop")
    assert manifest["has_error"] is True
    assert "boom in loop" in manifest["error_message"]


class _FakeWriter:
    def __init__(self):
        self.chunks = []

    def write(self, data):
        self.chunks.append(data)


@pytest.mark.asyncio
async def test_handle_plugin_route_dispatches_to_matching_plugin(plugin_manager):
    _write_plugin("demo", ROUTE_PLUGIN_SOURCE, {"route_prefixes": ["/demo-"]})
    plugin_manager.load_all_plugins()

    writer = _FakeWriter()
    handled = await plugin_manager.handle_plugin_route(writer, "/demo-data", "GET", {}, {})
    assert handled is True
    assert writer.chunks == [b"handled:/demo-data"]


@pytest.mark.asyncio
async def test_handle_plugin_route_returns_false_for_unmatched_prefix(plugin_manager):
    _write_plugin("demo", ROUTE_PLUGIN_SOURCE, {"route_prefixes": ["/demo-"]})
    plugin_manager.load_all_plugins()

    handled = await plugin_manager.handle_plugin_route(_FakeWriter(), "/other-path", "GET", {}, {})
    assert handled is False


@pytest.mark.asyncio
async def test_handle_plugin_route_crash_deactivates_plugin(plugin_manager):
    _write_plugin("demo", ROUTE_PLUGIN_SOURCE, {"route_prefixes": ["/demo-"]})
    plugin_manager.load_all_plugins()

    handled = await plugin_manager.handle_plugin_route(_FakeWriter(), "/demo-crash", "GET", {}, {})
    assert handled is False
    assert plugin_manager.is_active("demo") is False
    manifest = plugin_manager._load_manifest("demo")
    assert manifest["has_error"] is True
    assert "boom in handle_route" in manifest["error_message"]
