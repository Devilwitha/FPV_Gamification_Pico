"""Test-Infrastruktur fuer tools/ (PC-seitige Desktop-Skripte).

WICHTIG: build_firmware.py & co. schreiben standardmaessig direkt ins echte
Repository (source/version.json, build/.last_bundle_manifest.json, keys/,
lizenzen/, build_firmware_debug.log). Der build_firmware-Fixture unten kopiert
source/ und missionen/ nach tmp_path und biegt ALLE schreibenden Modul-
Konstanten dorthin um, damit Tests nie echte Projektdateien veraendern.
"""
import importlib
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TOOLS_DIR = ROOT / "tools"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def _fresh_import(name):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


@pytest.fixture
def fresh_import():
    return _fresh_import


@pytest.fixture
def build_firmware(tmp_path, monkeypatch):
    module = _fresh_import("build_firmware")

    tmp_source = tmp_path / "source"
    shutil.copytree(ROOT / "source", tmp_source)
    tmp_missions = tmp_path / "missionen"
    shutil.copytree(ROOT / "missionen", tmp_missions)
    tmp_keys = tmp_path / "keys"
    tmp_build = tmp_path / "build"
    tmp_licenses = tmp_path / "lizenzen"

    monkeypatch.setattr(module, "SOURCE_DIR", str(tmp_source))
    monkeypatch.setattr(module, "MISSIONS_DIR", str(tmp_missions))
    monkeypatch.setattr(module, "KEYS_DIR", str(tmp_keys))
    monkeypatch.setattr(module, "DEFAULT_PRIVATE_KEY_PATH", str(tmp_keys / "private_key.pem"))
    monkeypatch.setattr(module, "DEFAULT_PUBLIC_KEY_PATH", str(tmp_keys / "public_key.pem"))
    monkeypatch.setattr(module, "BUILD_DIR", str(tmp_build))
    monkeypatch.setattr(module, "MANIFEST_FILE", str(tmp_build / ".last_bundle_manifest.json"))
    monkeypatch.setattr(module, "LICENSES_DIR", str(tmp_licenses))
    monkeypatch.setattr(module, "DEBUG_LOG_FILE", str(tmp_path / "build_firmware_debug.log"))

    module.TMP_SOURCE_DIR = str(tmp_source)
    return module


@pytest.fixture
def check_pico_storage(build_firmware):
    """check_pico_storage.py importiert build_firmware.py per `import
    build_firmware` - muss wie license_issuer.py NACH dem gepatchten
    build_firmware-Fixture frisch importiert werden."""
    return _fresh_import("check_pico_storage")


@pytest.fixture
def license_issuer(build_firmware):
    """license_issuer.py importiert build_firmware.py per `import build_firmware`
    (Modul-globaler Name) - muss daher NACH dem gepatchten build_firmware-
    Fixture frisch importiert werden, sonst wuerde issue_license() intern noch
    auf das alte, ungepatchte Modul (mit echten Repo-Pfaden!) zugreifen."""
    return _fresh_import("license_issuer")


@pytest.fixture
def deploy_mod(build_firmware, fresh_import):
    """deploy_mod.py berechnet MODS_SOURCE_DIR = build_firmware.SOURCE_DIR +
    '/mods' beim eigenen Modul-Import - muss daher NACH dem gepatchten
    build_firmware-Fixture frisch importiert werden, sonst wuerde es auf
    das echte source/mods/ statt die tmp_path-Kopie zeigen. Wird von
    test_deploy_mod.py UND test_plugin_packager.py (baut auf deploy_mod
    auf) genutzt."""
    return _fresh_import("deploy_mod")
