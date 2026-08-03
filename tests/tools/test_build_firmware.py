"""Tests fuer tools/build_firmware.py's reine Datei-/Bundle-Logik.

Netzwerk-/Seriell-/GUI-Funktionen (upload_bundle_to_pico, _run_mpremote,
launch_gui, ...) werden hier NICHT getestet - sie brauchen echte Hardware
bzw. einen Display-Server. Der build_firmware-Fixture (siehe conftest.py)
biegt alle schreibenden Pfade auf tmp_path um.
"""
import json
import os
import struct

import pytest


def test_deploy_bundled_mods_via_serial_skips_example_plugin(build_firmware, monkeypatch):
    """Die komplette (serielle) Firmware bringt alle mitgelieferten Mods aus
    source/mods/ mit, AUSSER example_plugin (reine Lernvorlage) - siehe
    build_and_flash_with_license()'s Aufruf von
    _deploy_bundled_mods_via_serial()."""
    import sys

    sys.modules.pop("deploy_mod", None)
    import deploy_mod  # noqa: E402 - bewusst erst hier, nach dem SOURCE_DIR-Patch der Fixture

    calls = []
    monkeypatch.setattr(deploy_mod, "deploy_via_serial", lambda mod_name, port=None, log=print: calls.append((mod_name, port)))

    build_firmware._deploy_bundled_mods_via_serial(["mpremote"], "COM5")

    deployed_names = [name for name, _port in calls]
    assert "example_plugin" not in deployed_names
    assert "shooter" in deployed_names
    assert all(port == "COM5" for _name, port in calls)


def test_mpy_device_name_converts_py_to_mpy(build_firmware):
    assert build_firmware._mpy_device_name("main.py") == "main.mpy"


def test_mpy_device_name_excludes_boot_and_recovery(build_firmware):
    assert build_firmware._mpy_device_name("boot.py") == "boot.py"
    assert build_firmware._mpy_device_name("recovery.py") == "recovery.py"


def test_mpy_device_name_leaves_non_py_files_unchanged(build_firmware):
    assert build_firmware._mpy_device_name("index.html") == "index.html"
    assert build_firmware._mpy_device_name("en.pak") == "en.pak"


def test_expand_with_mpy_variants_adds_counterpart(build_firmware):
    expanded = build_firmware._expand_with_mpy_variants(["main.py", "index.html"])
    assert expanded == {"main.py", "main.mpy", "index.html"}


def test_expand_with_mpy_variants_excludes_boot_stack(build_firmware):
    expanded = build_firmware._expand_with_mpy_variants(["boot.py"])
    assert expanded == {"boot.py"}


def test_get_files_to_bundle_without_boot_stack(build_firmware):
    files = build_firmware.get_files_to_bundle(False)
    assert "boot.py" not in files
    assert "main.py" in files


def test_get_files_to_bundle_with_boot_stack(build_firmware):
    files = build_firmware.get_files_to_bundle(True)
    assert files[:3] == build_firmware.BOOT_STACK_FILES_TO_BUNDLE


@pytest.mark.parametrize("names,expected", [
    ((), "light"),
    (("de.pak", "en.pak"), "language"),
    (("main.py", "boot.py", "index.html"), "complete"),
    (("main.mpy", "boot.py"), "complete"),
    (("index.html", "admin_dashboard.html"), "light"),
])
def test_classify_bundle_mode(build_firmware, names, expected):
    assert build_firmware._classify_bundle_mode(names) == expected


def test_classify_bundle_mode_recovery(build_firmware):
    recovery_names = tuple(build_firmware.RECOVERY_FILES_TO_BUNDLE)
    assert build_firmware._classify_bundle_mode(recovery_names) == "recovery"


def test_order_bundle_files_for_apply_puts_main_before_boot():
    import build_firmware
    ordered = build_firmware._order_bundle_files_for_apply(["boot.py", "index.html", "main.py"])
    assert ordered == ["index.html", "main.py", "boot.py"]


def test_order_bundle_files_for_apply_without_main_or_boot():
    import build_firmware
    ordered = build_firmware._order_bundle_files_for_apply(["index.html", "admin_dashboard.html"])
    assert ordered == ["index.html", "admin_dashboard.html"]


@pytest.mark.parametrize("raw,expected", [
    ("", "http://192.168.4.1"),
    ("192.168.4.1", "http://192.168.4.1"),
    ("http://192.168.4.1/", "http://192.168.4.1"),
    ("https://example.com/", "https://example.com"),
    ("  192.168.1.5  ", "http://192.168.1.5"),
])
def test_normalize_base_url(build_firmware, raw, expected):
    assert build_firmware.normalize_base_url(raw) == expected


def test_shorten_leaves_short_text_untouched(build_firmware):
    assert build_firmware._shorten("hello") == "hello"


def test_shorten_truncates_long_text(build_firmware):
    text = "x" * 900
    result = build_firmware._shorten(text, max_len=800)
    assert result.endswith("...<truncated>")
    assert len(result) == 800 + len("...<truncated>")


@pytest.mark.parametrize("name,expected", [
    ("main.py", True),
    ("../evil.py", False),
    ("sub/evil.py", False),
    ("sub\\evil.py", False),
    (".hidden", False),
    ("", False),
])
def test_is_safe_bundle_entry_filename(build_firmware, name, expected):
    assert build_firmware._is_safe_bundle_entry_filename(name) is expected


# ==================== Schluessel / Lizenzarchiv ====================

def test_keys_exist_false_initially(build_firmware):
    assert build_firmware.keys_exist() is False


def test_generate_keypair_if_missing_creates_keys(build_firmware):
    created = build_firmware.generate_keypair_if_missing()
    assert created is True
    assert build_firmware.keys_exist() is True

    created_again = build_firmware.generate_keypair_if_missing()
    assert created_again is False


def test_save_license_record_writes_lic_and_json(build_firmware):
    lic_path, json_path = build_firmware.save_license_record(
        "aabbccdd11223344", "kunde@example.com", "LICENSE-CONTENT", device_info={"port": "COM3"}
    )
    assert os.path.isfile(lic_path)
    assert os.path.isfile(json_path)
    with open(lic_path) as f:
        assert f.read() == "LICENSE-CONTENT"
    with open(json_path) as f:
        record = json.load(f)
    assert record["hardware_id"] == "aabbccdd11223344"
    assert record["port"] == "COM3"


def test_save_license_record_sanitizes_hardware_id_for_filename(build_firmware):
    lic_path, _json_path = build_firmware.save_license_record("aa:bb:cc!!", "kunde", "content")
    assert "aabbcc" in os.path.basename(lic_path)


# ==================== Versionierung ====================
# Der build_firmware-Fixture kopiert source/ 1:1 (inkl. der ECHTEN, aktuellen
# version.json) - fuer deterministische Tests wird sie hier erst entfernt.

def _remove_version_files(build_firmware):
    for name in ("version.json", "firmware_version.txt"):
        path = os.path.join(build_firmware.TMP_SOURCE_DIR, name)
        if os.path.exists(path):
            os.remove(path)


def test_read_version_state_defaults_when_missing(build_firmware):
    _remove_version_files(build_firmware)
    assert build_firmware._read_version_state(build_firmware.TMP_SOURCE_DIR) == "1.0.0"


def test_bump_firmware_version_increments_patch(build_firmware):
    _remove_version_files(build_firmware)
    new_version = build_firmware.bump_firmware_version(build_firmware.TMP_SOURCE_DIR)
    assert new_version == "1.0.1"

    with open(os.path.join(build_firmware.TMP_SOURCE_DIR, "version.json")) as f:
        assert json.load(f)["version"] == "1.0.1"
    with open(os.path.join(build_firmware.TMP_SOURCE_DIR, "firmware_version.txt")) as f:
        assert f.read() == "1.0.1"

    assert build_firmware.bump_firmware_version(build_firmware.TMP_SOURCE_DIR) == "1.0.2"


def test_bump_firmware_version_never_touches_real_repo_source(build_firmware):
    import pathlib

    real_source = pathlib.Path(build_firmware.__file__).resolve().parent.parent / "source"
    real_version_path = real_source / "version.json"
    before = real_version_path.read_text(encoding="utf-8") if real_version_path.exists() else None

    build_firmware.bump_firmware_version(build_firmware.TMP_SOURCE_DIR)

    after = real_version_path.read_text(encoding="utf-8") if real_version_path.exists() else None
    assert before == after


# ==================== Missionen / Sprachpakete ====================

def test_resolve_mission_files_lists_mission_extension_only(build_firmware):
    missions = build_firmware._resolve_mission_files()
    assert all(name.endswith(".mission") for name in missions)
    assert "trick_barrel_roll.mission" in missions


def test_resolve_language_pack_files_excludes_english_fallback(build_firmware):
    packs = build_firmware._resolve_language_pack_files(build_firmware.TMP_SOURCE_DIR)
    assert "en.pak" not in packs
    assert "de.pak" in packs


def test_bundle_source_path_resolves_mission_and_public_key_specially(build_firmware):
    mission_path = build_firmware._bundle_source_path(build_firmware.TMP_SOURCE_DIR, "trick_barrel_roll.mission")
    assert mission_path == os.path.join(build_firmware.MISSIONS_DIR, "trick_barrel_roll.mission")

    key_path = build_firmware._bundle_source_path(build_firmware.TMP_SOURCE_DIR, "public_key.pem")
    assert key_path == build_firmware.DEFAULT_PUBLIC_KEY_PATH

    normal_path = build_firmware._bundle_source_path(build_firmware.TMP_SOURCE_DIR, "main.py")
    assert normal_path == os.path.join(build_firmware.TMP_SOURCE_DIR, "main.py")


# ==================== build_bundle() Rundlauftest ====================

def test_build_bundle_recovery_mode_roundtrips_with_ota_helpers(build_firmware, tmp_path):
    """Baut ein echtes Recovery-Bundle (klein/schnell, keine mpy-Kompilierung
    ausser fuer 3 Dateien) und entpackt es mit source/ota_helpers.py's
    ECHTER apply_firmware_bundle() - stellt sicher, dass das PC-Tool und die
    Geraete-Firmware exakt dasselbe Bundle-Format sprechen."""
    # source/ liegt bereits ueber den Root-conftest.py im sys.path (siehe
    # dortige source/-Firmware-Tests) - ota_helpers.py ist somit direkt importierbar.
    import ota_helpers

    output_path = tmp_path / "recovery.nbo"
    included, missing = build_firmware.build_bundle(
        build_firmware.TMP_SOURCE_DIR,
        str(output_path),
        recovery_mode=True,
        bump_version=False,
    )
    assert missing == []
    assert output_path.is_file()

    with open(output_path, "rb") as f:
        magic = f.read(8)
    assert magic == build_firmware.BUNDLE_MAGIC

    apply_dir = tmp_path / "device_fs"
    apply_dir.mkdir()
    cwd_before = os.getcwd()
    os.chdir(apply_dir)
    try:
        extracted, needs_restart = ota_helpers.apply_firmware_bundle(
            str(output_path), (), build_firmware.BUNDLE_MAGIC
        )
    finally:
        os.chdir(cwd_before)

    # boot.py/recovery.py bleiben Klartext, die restlichen Recovery-Dateien werden zu .mpy kompiliert.
    assert "boot.py" in extracted
    assert "recovery.py" in extracted
    assert "hotspot_common.mpy" in extracted
    assert "ota_helpers.mpy" in extracted
    assert needs_restart is False  # kein main.py/main.mpy im Recovery-Bundle
    assert set(extracted) == set(name for name, _ in included)

    with open(apply_dir / "boot.py", "rb") as f:
        with open(os.path.join(build_firmware.TMP_SOURCE_DIR, "boot.py"), "rb") as original:
            assert f.read() == original.read()


def test_build_bundle_light_mode_only_includes_changed_files(build_firmware, tmp_path):
    # Erster (vollstaendiger) Build erzeugt das Manifest fuer den Diff.
    # include_boot_stack=True, damit "recovery.py" ueberhaupt Teil von
    # _resolve_files_to_bundle()'s `base`-Liste ist - die "Light-Firmware
    # soll immer recovery.py enthalten"-Regel greift nur dann.
    build_firmware.build_bundle(
        build_firmware.TMP_SOURCE_DIR, str(tmp_path / "full.nbo"),
        include_boot_stack=True, bump_version=False,
    )
    assert os.path.isfile(build_firmware.MANIFEST_FILE)

    # Ohne Aenderungen soll ein Light-Build trotzdem immer recovery.py enthalten.
    included, _missing = build_firmware.build_bundle(
        build_firmware.TMP_SOURCE_DIR, str(tmp_path / "light1.nbo"),
        include_boot_stack=True, light_mode=True, bump_version=False,
    )
    assert [name for name, _size in included] == ["recovery.py"]

    # Jetzt eine Datei aendern -> sie muss im naechsten Light-Build zusaetzlich auftauchen.
    idcard_path = os.path.join(build_firmware.TMP_SOURCE_DIR, "idcard_helpers.py")
    with open(idcard_path, "a") as f:
        f.write("\n# geaendert fuer Test\n")

    included2, _missing2 = build_firmware.build_bundle(
        build_firmware.TMP_SOURCE_DIR, str(tmp_path / "light2.nbo"),
        include_boot_stack=True, light_mode=True, bump_version=False,
    )
    names2 = [name for name, _size in included2]
    assert "idcard_helpers.mpy" in names2
    assert "recovery.py" in names2
