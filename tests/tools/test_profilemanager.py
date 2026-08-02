"""Tests fuer tools/profilemanager.py's reine Logikfunktionen (kein GUI-Test)."""
import json

import profilemanager as pm


def test_default_profile_flat_matches_required_keys():
    profile = pm.default_profile_flat()
    assert set(profile.keys()) == set(pm.REQUIRED_KEYS)


def test_default_profile_flat_returns_a_copy():
    a = pm.default_profile_flat()
    b = pm.default_profile_flat()
    a["gyro_trick_threshold"] = 999
    assert b["gyro_trick_threshold"] != 999


def test_normalize_flat_extracts_from_settings_wrapper():
    wrapped = {"name": "custom", "settings": {"gyro_trick_threshold": 111}}
    flat = pm._normalize_flat(wrapped)
    assert flat["gyro_trick_threshold"] == 111
    # Fehlende Schluessel fallen auf die Defaults zurueck.
    assert flat["stable_threshold"] == pm.DEFAULT_SETTINGS["stable_threshold"]


def test_normalize_flat_coerces_types():
    flat = pm._normalize_flat({"gyro_trick_threshold": "200", "gyro_lowpass_alpha": "0.5"})
    assert flat["gyro_trick_threshold"] == 200
    assert isinstance(flat["gyro_trick_threshold"], int)
    assert flat["gyro_lowpass_alpha"] == 0.5
    assert isinstance(flat["gyro_lowpass_alpha"], float)


def test_normalize_flat_ignores_unknown_keys():
    flat = pm._normalize_flat({"totally_unknown_key": 1})
    assert "totally_unknown_key" not in flat


def test_resolve_copil_path_prefers_source_subdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "source").mkdir()
    path = pm._resolve_copil_path()
    assert path == tmp_path / "source" / "copil"


def test_resolve_copil_path_falls_back_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = pm._resolve_copil_path()
    assert path == tmp_path / "copil"


def test_read_or_create_copil_creates_defaults_when_missing(tmp_path):
    path = tmp_path / "copil"
    payload = pm._read_or_create_copil(path)
    assert payload == {"copter_name": pm.COPIL_DEFAULT_NAME, "pilot_name": pm.COPIL_DEFAULT_NAME}
    assert path.is_file()


def test_read_or_create_copil_reads_existing_file(tmp_path):
    path = tmp_path / "copil"
    path.write_text(json.dumps({"copter_name": "Racer5", "pilot_name": "Ace"}), encoding="utf-8")
    payload = pm._read_or_create_copil(path)
    assert payload == {"copter_name": "Racer5", "pilot_name": "Ace"}


def test_read_or_create_copil_recovers_from_corrupt_file(tmp_path):
    path = tmp_path / "copil"
    path.write_text("{not json", encoding="utf-8")
    payload = pm._read_or_create_copil(path)
    assert payload == {"copter_name": pm.COPIL_DEFAULT_NAME, "pilot_name": pm.COPIL_DEFAULT_NAME}


def test_write_copil_trims_and_defaults_blank_names(tmp_path):
    path = tmp_path / "sub" / "copil"
    payload = pm._write_copil(path, "  Racer5  ", "   ")
    assert payload == {"copter_name": "Racer5", "pilot_name": pm.COPIL_DEFAULT_NAME}
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == payload
