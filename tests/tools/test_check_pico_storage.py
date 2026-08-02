"""Tests fuer tools/check_pico_storage.py's reine Groessenschaetz-Logik.

Nutzt den build_firmware-Fixture (echte, nach tmp_path kopierte source/-
Dateien) als Datengrundlage - probe_pico bleibt in allen Tests False, es wird
also nie ein echter mpremote-Prozess gestartet.
"""
import math


def test_estimate_bundle_size_matches_format(check_pico_storage):
    entries = [("a.py", 100), ("bb.py", 50)]
    expected = 8 + 4 + (4 + len(b"a.py") + 4 + 100) + (4 + len(b"bb.py") + 4 + 50)
    assert check_pico_storage._estimate_bundle_size(entries) == expected


def test_base64_size_rounds_up_to_multiple_of_four(check_pico_storage):
    assert check_pico_storage._base64_size(3) == 4
    assert check_pico_storage._base64_size(4) == 8
    assert check_pico_storage._base64_size(0) == 0


def test_resolve_source_files_normal_mode_excludes_boot_stack(check_pico_storage):
    files = check_pico_storage._resolve_source_files("normal")
    assert "boot.py" not in files
    assert "main.py" in files


def test_resolve_source_files_complete_mode_includes_boot_stack(check_pico_storage):
    files = check_pico_storage._resolve_source_files("complete")
    assert "boot.py" in files


def test_resolve_source_files_recovery_mode_uses_recovery_list(check_pico_storage):
    import build_firmware

    files = check_pico_storage._resolve_source_files("recovery")
    assert files == list(build_firmware.RECOVERY_FILES_TO_BUNDLE)


def test_file_sizes_reports_present_and_missing(check_pico_storage):
    present, missing = check_pico_storage._file_sizes(["main.py", "does-not-exist.py"])
    assert missing == ["does-not-exist.py"]
    assert any(name == "main.py" for name, _size in present)
    assert all(size > 0 for _name, size in present)


def test_fmt_uses_underscore_as_thousands_separator(check_pico_storage):
    assert check_pico_storage._fmt(1234567) == "1_234_567"


def test_run_check_normal_mode_produces_assessment(check_pico_storage):
    report = check_pico_storage.run_check(mode="normal", fs_total=1_000_000, fs_free=500_000)
    assert "PICO SPEICHERPRUEFUNG" in report
    assert "Modus: normal" in report
    assert "Bewertung:" in report


def test_run_check_reports_insufficient_when_free_space_too_small(check_pico_storage):
    report = check_pico_storage.run_check(mode="normal", fs_total=1_000_000, fs_free=1)
    assert "NICHT AUSREICHEND" in report


def test_run_check_without_explicit_values_falls_back_to_defaults(check_pico_storage):
    # run_check() ersetzt fs_total/fs_free=None IMMER durch die DEFAULT_FS_*-
    # Konstanten, bevor _build_report() sie sieht - der "UNVOLLSTAENDIG"-Zweig
    # dort ist daher ueber run_check() nie erreichbar, nur eine direkte
    # _build_report(fs_free=None)-Nutzung koennte ihn treffen.
    report = check_pico_storage.run_check(mode="normal", fs_total=None, fs_free=None)
    assert "Standardwerte verwendet" in report
    assert f"{check_pico_storage.DEFAULT_FS_FREE_BYTES:,}".replace(",", "_") in report
