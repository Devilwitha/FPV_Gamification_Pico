"""Tests fuer tools/build_firmware.py's build_bundle() in JEDEM Bundle-Modus
(complete/light/recovery/language/boot_main_only alias "emergency.nbo") -
jedes erzeugte Bundle wird zusaetzlich mit source/ota_helpers.py's ECHTER
apply_firmware_bundle() entpackt, um Format-Kompatibilitaet PC-Tool <-> Geraet
sicherzustellen. Ausserdem run_cli() (der Kommandozeilen-Einstiegspunkt) und
die reinen Bundle-Datei-Parser (_read_bundle_entry_names/_iter_bundle_entries).
"""
import os

import pytest

import ota_helpers


def _apply_and_list(bundle_path, apply_dir):
    apply_dir.mkdir(exist_ok=True)
    cwd_before = os.getcwd()
    os.chdir(apply_dir)
    try:
        return ota_helpers.apply_firmware_bundle(str(bundle_path), (), b"FPVBNDL1")
    finally:
        os.chdir(cwd_before)


def test_build_bundle_complete_mode_includes_boot_stack_and_public_key(build_firmware, tmp_path):
    build_firmware.generate_keypair_if_missing()
    included, missing = build_firmware.build_bundle(
        build_firmware.TMP_SOURCE_DIR, str(tmp_path / "complete.nbo"),
        include_boot_stack=True, bump_version=False,
    )
    assert missing == []
    names = [name for name, _size in included]
    assert "boot.py" in names
    assert "recovery.py" in names
    assert "boot_runtime.mpy" in names
    assert "public_key.pem" in names
    assert "main.mpy" in names
    # main.mpy muss direkt vor boot.py stehen (siehe _order_bundle_files_for_apply).
    assert names.index("main.mpy") == names.index("boot.py") - 1
    assert build_firmware._classify_bundle_mode(tuple(names)) == "complete"

    # WICHTIG: das komplette Bundle (mit public_key.pem) ist AUSSCHLIESSLICH
    # fuer den seriellen Weg gedacht (_apply_bundle_entries_via_serial(), das
    # dieselbe Pruefung wie ota_helpers nicht durchlaeuft) - source/ota_helpers.py's
    # apply_firmware_bundle() (der normale HTTP-OTA-Pfad) lehnt public_key.pem
    # bewusst kategorisch ab (siehe update_manager.PROTECTED_FILES). Hier wird
    # daher nur ueber build_firmware's EIGENEN, generischen Bundle-Reader
    # verifiziert, nicht ueber ota_helpers.apply_firmware_bundle().
    entries = dict(build_firmware._iter_bundle_entries(str(tmp_path / "complete.nbo")))
    assert set(entries.keys()) == set(names)
    assert len(entries["boot.py"]) > 0
    assert len(entries["public_key.pem"]) > 0


def test_is_safe_bundle_entry_filename_allows_public_key_unlike_device_side_check(build_firmware):
    """build_firmware.py's eigene _is_safe_bundle_entry_filename() (fuer den
    seriellen Weg) ist bewusst NICHT identisch mit source/ota_helpers.py's
    Gegenstueck (fuer den HTTP-OTA-Weg) - nur letztere blockt public_key.pem/
    license.lic kategorisch (siehe update_manager.PROTECTED_FILES)."""
    import ota_helpers

    assert build_firmware._is_safe_bundle_entry_filename("public_key.pem") is True
    assert ota_helpers._is_safe_bundle_entry_filename("public_key.pem") is False


def test_build_bundle_light_mode_without_prior_manifest_includes_everything(build_firmware, tmp_path):
    # Ohne vorheriges Manifest gilt jede Datei als "geaendert" -> Light-Modus
    # entspricht beim allerersten Build praktisch dem normalen Modus.
    assert not os.path.isfile(build_firmware.MANIFEST_FILE)
    included, missing = build_firmware.build_bundle(
        build_firmware.TMP_SOURCE_DIR, str(tmp_path / "light_first.nbo"),
        light_mode=True, bump_version=False,
    )
    assert missing == []
    assert len(included) == len(build_firmware.get_files_to_bundle(False)) + len(
        build_firmware._resolve_mission_files()
    )
    assert os.path.isfile(build_firmware.MANIFEST_FILE)


def test_build_bundle_language_pack_mode_contains_only_pak_files(build_firmware, tmp_path):
    included, missing = build_firmware.build_bundle(
        build_firmware.TMP_SOURCE_DIR, str(tmp_path / "lang.pak"),
        language_pack_mode=True, bump_version=False,
    )
    assert missing == []
    names = [name for name, _size in included]
    assert all(name.endswith(".pak") for name in names)
    assert "en.pak" not in names  # en.pak ist der lokale Fallback, wird nicht mitgepackt
    assert "de.pak" in names

    extracted, needs_restart = _apply_and_list(tmp_path / "lang.pak", tmp_path / "device_lang")
    assert needs_restart is False
    assert build_firmware._classify_bundle_mode(tuple(extracted)) == "language"


def test_build_bundle_boot_main_only_mode_is_the_emergency_bundle(build_firmware, tmp_path):
    """boot_main_only_mode=True ist genau das, was die GUI als
    "emergency.nbo" (nur main.py + boot.py) bezeichnet (siehe
    build_firmware.py Zeile ~2066)."""
    included, missing = build_firmware.build_bundle(
        build_firmware.TMP_SOURCE_DIR, str(tmp_path / "emergency.nbo"),
        boot_main_only_mode=True, bump_version=False,
    )
    assert missing == []
    names = [name for name, _size in included]
    assert names == ["main.mpy", "boot.py"]

    extracted, needs_restart = _apply_and_list(tmp_path / "emergency.nbo", tmp_path / "device_emergency")
    assert set(extracted) == {"main.mpy", "boot.py"}
    assert needs_restart is True


def test_build_bundle_reports_missing_files_without_failing(build_firmware, tmp_path):
    os.remove(os.path.join(build_firmware.TMP_SOURCE_DIR, "admin_credits.html"))
    included, missing = build_firmware.build_bundle(
        build_firmware.TMP_SOURCE_DIR, str(tmp_path / "partial.nbo"), bump_version=False,
    )
    assert "admin_credits.html" in missing
    assert all(name != "admin_credits.html" for name, _size in included)


def test_read_bundle_entry_names_matches_iter_bundle_entries(build_firmware, tmp_path):
    build_firmware.build_bundle(
        build_firmware.TMP_SOURCE_DIR, str(tmp_path / "recovery.nbo"), recovery_mode=True, bump_version=False,
    )
    names_only = build_firmware._read_bundle_entry_names(str(tmp_path / "recovery.nbo"))
    full_entries = list(build_firmware._iter_bundle_entries(str(tmp_path / "recovery.nbo")))
    assert names_only == [name for name, _content in full_entries]
    assert all(len(content) > 0 for _name, content in full_entries if _name not in ("hotspot.conf",))


def test_run_cli_builds_bundle_and_prints_summary(build_firmware, tmp_path, capsys):
    output_path = tmp_path / "cli.nbo"
    build_firmware.run_cli(output_path=str(output_path), recovery_mode=True, bump_version=False)
    assert output_path.is_file()
    out = capsys.readouterr().out
    assert "Firmware-Bundle erstellt" in out
    assert "Firmware-Version" in out
    assert "/admin-update" in out


def test_run_cli_reports_missing_files_hint(build_firmware, tmp_path, capsys):
    os.remove(os.path.join(build_firmware.TMP_SOURCE_DIR, "hotspot.conf"))
    build_firmware.run_cli(output_path=str(tmp_path / "cli2.nbo"), recovery_mode=True, bump_version=False)
    out = capsys.readouterr().out
    assert "HINWEIS" in out
    assert "uebersprungen" in out
