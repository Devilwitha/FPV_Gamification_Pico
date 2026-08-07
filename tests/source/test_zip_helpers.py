"""Tests fuer source/zip_helpers.py - der minimale ZIP-Entpacker fuer den
Plugin-Upload (siehe dortiger Modul-Docstring sowie pico_web_api.py's
"/api/plugins/upload-chunk"/"/api/plugins/upload-finalize"-Endpunkte).

Baut echte ZIP-Dateien mit Pythons eingebautem zipfile-Modul (STORED UND
DEFLATED, plus Unterordner zum Pruefen des flachen Entpackens) und prueft,
dass extract_plugin_zip() sie byte-genau nach mods/<name>/ entpackt sowie
das frisch entpackte Plugin ueber plugin_manager aktiviert.

Gleiches sys.path/"mods"-Package-Muster wie test_plugin_manager.py: "mods"
ist ein echtes Python-Package, daher muss tmp_path VOR source/ in sys.path
liegen und jeder sys.modules-Eintrag fuer "mods"/"mods.*" vor/nach jedem
Test entfernt werden."""
import json
import os
import sys
import zipfile

import pytest


def _purge_mods_modules():
    for key in list(sys.modules.keys()):
        if key == "mods" or key.startswith("mods."):
            del sys.modules[key]


@pytest.fixture
def zip_helpers(install_stub_module, fresh_import, tmp_path, monkeypatch):
    install_stub_module("main", debug_log=lambda message: None)

    monkeypatch.syspath_prepend(str(tmp_path))
    _purge_mods_modules()

    fresh_import("plugin_manager")
    module = fresh_import("zip_helpers")
    yield module

    _purge_mods_modules()


def _build_zip(path, entries, arcname_prefix=""):
    """entries: dict von Pfad (innerhalb der ZIP) -> Inhalt (str/bytes).
    arcname_prefix wird jedem Namen vorangestellt (zum Testen von
    Unterordnern, die beim Entpacken IGNORIERT werden muessen)."""
    with zipfile.ZipFile(path, "w") as archive:
        for i, (name, content) in enumerate(entries.items()):
            data = content.encode("utf-8") if isinstance(content, str) else content
            # Abwechselnd STORED/DEFLATED, damit beide Kompressionspfade
            # (Methode 0 UND 8/deflate) durchlaufen werden.
            method = zipfile.ZIP_STORED if i % 2 == 0 else zipfile.ZIP_DEFLATED
            archive.writestr(arcname_prefix + name, data, method)


SIMPLE_MANIFEST = json.dumps({
    "name": "placeholder", "version": "1.0.0", "author": "Test",
    "entry": "main.py", "enabled": True, "ui_slots": {}, "route_prefixes": [],
})

SIMPLE_MAIN = "calls = []\n\ndef setup(context):\n    calls.append('setup')\n"


def test_extract_plugin_zip_writes_files_flat_into_mods_dir(zip_helpers, isolated_cwd):
    _build_zip("upload.zip", {
        "manifest.json": SIMPLE_MANIFEST,
        "shooter/main.py": SIMPLE_MAIN,
        "shooter/admin_shooter.html": "<html>hi</html>" * 20,  # gross genug fuer echtes DEFLATE
    }, arcname_prefix="")

    zip_helpers.extract_plugin_zip("upload.zip", "shooter")

    # "__pycache__" ist ein reines CPython-Testartefakt (Bytecode-Cache des
    # echten `mods.shooter.main`-Imports) - existiert auf dem echten
    # MicroPython-Geraet nicht, daher hier ausgeblendet.
    written = set(os.listdir("mods/shooter")) - {"__pycache__"}
    assert written == {"manifest.json", "main.py", "admin_shooter.html", "__init__.py"}
    with open("mods/shooter/manifest.json") as f:
        assert json.load(f)["name"] == "placeholder"
    with open("mods/shooter/main.py") as f:
        assert f.read() == SIMPLE_MAIN
    with open("mods/shooter/admin_shooter.html") as f:
        assert f.read() == "<html>hi</html>" * 20


def test_extract_plugin_zip_ignores_subfolder_structure(zip_helpers, isolated_cwd):
    """Unterordner in der ZIP werden IGNORIERT (gleiches flache Verhalten
    wie webshop/app.py's _process_plugin_zip_upload()) - "deep/nested/
    main.py" landet trotzdem direkt unter mods/<name>/main.py."""
    _build_zip("upload.zip", {"manifest.json": SIMPLE_MANIFEST, "main.py": SIMPLE_MAIN},
               arcname_prefix="deep/nested/")

    zip_helpers.extract_plugin_zip("upload.zip", "flattest")

    assert os.path.isfile("mods/flattest/manifest.json")
    assert os.path.isfile("mods/flattest/main.py")
    assert not os.path.isdir("mods/flattest/deep")


def test_extract_plugin_zip_activates_plugin_via_plugin_manager(zip_helpers, isolated_cwd):
    import plugin_manager

    _build_zip("upload.zip", {"manifest.json": SIMPLE_MANIFEST, "main.py": SIMPLE_MAIN})

    zip_helpers.extract_plugin_zip("upload.zip", "shooter")

    assert plugin_manager.is_active("shooter")
    module = plugin_manager.get_plugin_module("shooter")
    assert module.calls == ["setup"]


def test_extract_plugin_zip_replaces_existing_plugin_of_same_name(zip_helpers, isolated_cwd):
    """Ein erneuter Upload mit gleichem Namen ersetzt den kompletten
    Ordnerinhalt (alte, im neuen ZIP nicht mehr enthaltene Dateien duerfen
    nicht liegen bleiben) und laedt das Plugin sauber neu (kein doppeltes
    setup() ohne vorheriges teardown())."""
    import plugin_manager

    _build_zip("v1.zip", {
        "manifest.json": SIMPLE_MANIFEST,
        "main.py": "calls = []\n\ndef setup(context):\n    calls.append('v1')\n\ndef teardown():\n    calls.append('teardown_v1')\n",
        "stale.txt": "wird in v2 nicht mehr mitgeliefert",
    })
    zip_helpers.extract_plugin_zip("v1.zip", "shooter")
    assert os.path.isfile("mods/shooter/stale.txt")

    _build_zip("v2.zip", {
        "manifest.json": SIMPLE_MANIFEST,
        "main.py": "calls = []\n\ndef setup(context):\n    calls.append('v2')\n",
    })
    zip_helpers.extract_plugin_zip("v2.zip", "shooter")

    assert not os.path.exists("mods/shooter/stale.txt")
    module = plugin_manager.get_plugin_module("shooter")
    assert module.calls == ["v2"]


def test_extract_plugin_zip_rejects_zip_without_manifest(zip_helpers, isolated_cwd):
    _build_zip("upload.zip", {"main.py": SIMPLE_MAIN})

    with pytest.raises(ValueError, match="manifest.json"):
        zip_helpers.extract_plugin_zip("upload.zip", "shooter")

    assert not os.path.exists("mods/shooter")


def test_extract_plugin_zip_rejects_empty_zip(zip_helpers, isolated_cwd):
    with zipfile.ZipFile("empty.zip", "w"):
        pass

    with pytest.raises(ValueError):
        zip_helpers.extract_plugin_zip("empty.zip", "shooter")


def test_extract_plugin_zip_bad_manifest_does_not_touch_existing_install(zip_helpers, isolated_cwd):
    """Ein fehlgeschlagener Re-Upload (fehlende manifest.json) darf eine
    bereits funktionierende Installation nicht zerstoeren - die Validierung
    muss VOR dem Loeschen des alten Ordners laufen."""
    import plugin_manager

    _build_zip("good.zip", {"manifest.json": SIMPLE_MANIFEST, "main.py": SIMPLE_MAIN})
    zip_helpers.extract_plugin_zip("good.zip", "shooter")
    assert plugin_manager.is_active("shooter")

    _build_zip("bad.zip", {"main.py": SIMPLE_MAIN})
    with pytest.raises(ValueError):
        zip_helpers.extract_plugin_zip("bad.zip", "shooter")

    assert plugin_manager.is_active("shooter")
    assert os.path.isfile("mods/shooter/manifest.json")


def test_extract_plugin_zip_rejects_zip_missing_entry_file(zip_helpers, isolated_cwd):
    """Reproduziert einen real beobachteten Fehlerfall: die ZIP enthaelt
    eine manifest.json, aber keine Datei, die zu deren "entry"-Feld passt
    (z.B. falsche Gross-/Kleinschreibung "Main.py" statt "main.py", oder
    schlicht vergessen). Vorher landete das erst als generischer Crash tief
    in plugin_manager (has_error=True), wurde vom Aufrufer aber trotzdem als
    Erfolg gemeldet - jetzt schlaegt extract_plugin_zip() SOFORT mit einer
    Meldung fehl, die die tatsaechlich enthaltenen Dateien auflistet."""
    _build_zip("upload.zip", {"manifest.json": SIMPLE_MANIFEST, "Main.py": SIMPLE_MAIN})

    with pytest.raises(ValueError, match="Main.py"):
        zip_helpers.extract_plugin_zip("upload.zip", "shooter")


def test_extract_plugin_zip_honors_custom_manifest_entry(zip_helpers, isolated_cwd):
    custom_manifest = json.dumps({
        "name": "placeholder", "version": "1.0.0", "author": "Test",
        "entry": "engine.py", "enabled": True, "ui_slots": {}, "route_prefixes": [],
    })
    _build_zip("upload.zip", {"manifest.json": custom_manifest, "engine.py": SIMPLE_MAIN})

    written = zip_helpers.extract_plugin_zip("upload.zip", "shooter")

    assert "engine.py" in written
    assert os.path.isfile("mods/shooter/engine.py")
