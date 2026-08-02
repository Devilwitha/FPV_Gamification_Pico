import json

import pytest

import trick_profile_helpers as tph


def test_builtin_profiles_present_with_required_keys():
    required = [
        "gyro_trick_threshold", "stable_threshold", "trick_start_hold_ms",
        "stable_hold_ms", "gyro_deadband", "gyro_lowpass_alpha",
        "min_trick_duration", "trick_min_accum_deg", "trick_spin_min_accum_deg",
        "trick_axis_dominance_ratio", "trick_start_type_weight",
    ]
    assert set(tph.TRICK_TUNING_PROFILES.keys()) == {"beginner", "freestyle", "aggressive"}
    for profile in tph.TRICK_TUNING_PROFILES.values():
        for key in required:
            assert key in profile


@pytest.mark.parametrize("raw,expected", [
    ("beginner", "beginner"),
    ("Beginner", "beginner"),
    ("soft", "beginner"),
    ("medium", "freestyle"),
    ("freestyle", "freestyle"),
    ("aggressive", "aggressive"),
    ("AGGRESSIVE", "aggressive"),
    ("  aggressive  ", "aggressive"),
])
def test_normalize_trick_tuning_profile_known_aliases(raw, expected):
    assert tph.normalize_trick_tuning_profile(raw) == expected


def test_normalize_trick_tuning_profile_unknown_without_file_falls_back_to_aggressive():
    assert tph.normalize_trick_tuning_profile("totally_unknown") == "aggressive"


def test_normalize_trick_tuning_profile_recognizes_custom_pro_file():
    with open("MyCustom.pro", "w") as f:
        f.write("{}")
    assert tph.normalize_trick_tuning_profile("MyCustom") == "MyCustom"


def test_normalize_trick_tuning_profile_lowercase_fallback_for_custom_file():
    # Windows' Dateisystem ist case-insensitiv, daher trifft bereits die
    # Original-Schreibweisen-Pruefung (os.stat("MyCustom.pro")) auf die real
    # als "mycustom.pro" gespeicherte Datei zu - die Funktion liefert dann die
    # angefragte Original-Schreibweise zurueck, nicht die normalisierte.
    with open("mycustom.pro", "w") as f:
        f.write("{}")
    assert tph.normalize_trick_tuning_profile("MyCustom") == "MyCustom"


def test_load_trick_tuning_profile_name_defaults_to_aggressive_when_missing():
    assert tph.load_trick_tuning_profile_name() == "aggressive"


def test_save_and_load_trick_tuning_profile_name_roundtrip():
    ok, err = tph.save_trick_tuning_profile_name("beginner")
    assert ok is True
    assert err == ""
    assert tph.load_trick_tuning_profile_name() == "beginner"

    with open(tph.TRICK_SETTINGS_FILE_PATH) as f:
        assert json.loads(f.read()) == {"profile": "beginner"}


def test_save_trick_tuning_profile_name_normalizes_alias():
    tph.save_trick_tuning_profile_name("soft")
    assert tph.load_trick_tuning_profile_name() == "beginner"


def test_list_profile_files_includes_builtins_and_custom():
    with open("MyCustom.pro", "w") as f:
        f.write("{}")
    profiles = tph.list_profile_files()
    assert set(["beginner", "freestyle", "aggressive", "MyCustom"]).issubset(set(profiles))


def test_list_profile_files_excludes_settings_json_disguised_as_pro():
    # fpv_trick_settings.json selbst endet nicht auf .pro, aber die Funktion
    # muss trotzdem robust gegenueber diesem Namen als Ausschlussfall sein.
    tph.save_trick_tuning_profile_name("aggressive")
    profiles = tph.list_profile_files()
    assert "fpv_trick_settings" not in profiles


def test_get_profile_data_builtin():
    data = tph.get_profile_data("freestyle")
    assert data == tph.TRICK_TUNING_PROFILES["freestyle"]


def test_get_profile_data_builtin_case_insensitive():
    data = tph.get_profile_data("FREESTYLE")
    assert data == tph.TRICK_TUNING_PROFILES["freestyle"]


def test_get_profile_data_custom_file_with_settings_wrapper():
    payload = {"settings": dict(tph.TRICK_TUNING_PROFILES["beginner"])}
    with open("MyCustom.pro", "w") as f:
        json.dump(payload, f)
    data = tph.get_profile_data("MyCustom")
    assert data == tph.TRICK_TUNING_PROFILES["beginner"]


def test_get_profile_data_custom_file_missing_keys_returns_none():
    with open("Broken.pro", "w") as f:
        json.dump({"gyro_trick_threshold": 100}, f)
    logs = []
    assert tph.get_profile_data("Broken", debug_log=logs.append) is None
    assert any("Schluessel fehlt" in message for message in logs)


def test_get_profile_data_unknown_returns_none():
    assert tph.get_profile_data("nope") is None


def test_save_custom_profile_rejects_builtin_names():
    ok, err = tph.save_custom_profile("aggressive", {})
    assert ok is False
    assert "eingebaute" in err


def test_save_custom_profile_writes_pro_file():
    ok, err = tph.save_custom_profile("MyProfile", {"a": 1})
    assert ok is True
    assert err == ""
    with open("MyProfile.pro") as f:
        assert json.loads(f.read()) == {"a": 1}


def test_delete_custom_profile_rejects_builtin_names():
    ok, err = tph.delete_custom_profile("beginner")
    assert ok is False


def test_delete_custom_profile_removes_file():
    tph.save_custom_profile("MyProfile", {"a": 1})
    ok, err = tph.delete_custom_profile("MyProfile")
    assert ok is True
    assert not __import__("os").path.exists("MyProfile.pro")


def test_delete_custom_profile_missing_file_reports_error():
    ok, err = tph.delete_custom_profile("DoesNotExist")
    assert ok is False
    assert err
