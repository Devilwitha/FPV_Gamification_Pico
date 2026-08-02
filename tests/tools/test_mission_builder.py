"""Tests fuer tools/mission_builder.py's reine Logikfunktionen (kein GUI-Test).

MISSIONS_DIR wird beim Import auf den echten missionen/-Ordner gesetzt und
per mkdir(exist_ok=True) sichergestellt - das ist unschaedlich (Ordner
existiert bereits), die hier getesteten Funktionen schreiben aber selbst
nichts in diesen Ordner.
"""
import pytest

import mission_builder as mb


def test_sanitize_mission_name_matches_challenge_helpers_rule():
    """Muss exakt zu source/challenge_helpers.py's _sanitize_mission_name()
    passen, sonst passen lokal erstellte und hochgeladene Namen nicht zusammen."""
    import challenge_helpers

    for raw in ("My Mission!", "../evil/name", "a" * 100, "  spaced  out  "):
        assert mb.sanitize_mission_name(raw) == challenge_helpers._sanitize_mission_name(raw)


def test_default_mission_for_type_uses_schema_defaults():
    mission = mb.default_mission_for_type("eco")
    assert mission["challenge_type"] == "eco"
    assert mission["params"]["points_base"] == 500.0
    assert mission["params"]["points_per_mah"] == 1.0


def test_default_mission_for_unknown_type_has_empty_params():
    mission = mb.default_mission_for_type("does-not-exist")
    assert mission["params"] == {}


def test_validate_mission_accepts_well_formed_mission():
    mission = mb.default_mission_for_type("touch_and_go")
    mission["name"] = "Test"
    ok, err = mb.validate_mission(mission)
    assert ok is True
    assert err == ""


def test_validate_mission_rejects_missing_name():
    mission = mb.default_mission_for_type("eco")
    mission["name"] = "   "
    ok, err = mb.validate_mission(mission)
    assert ok is False
    assert "Name" in err


def test_validate_mission_rejects_unknown_challenge_type():
    ok, err = mb.validate_mission({"name": "x", "challenge_type": "bogus", "params": {}})
    assert ok is False
    assert "challenge_type" in err


def test_validate_mission_rejects_non_dict_params():
    ok, err = mb.validate_mission({"name": "x", "challenge_type": "eco", "params": "not-a-dict"})
    assert ok is False


def test_validate_mission_rejects_non_dict_mission():
    ok, err = mb.validate_mission("not-a-dict")
    assert ok is False


@pytest.mark.parametrize("kind,raw,expected", [
    ("float", "1,5", 1.5),
    ("float", "  2.0  ", 2.0),
    ("int", "42", 42),
    ("int", "3,0", 3),
    ("choice", "  Any  ", "Any"),
])
def test_cast_param_value(kind, raw, expected):
    assert mb._cast_param_value(kind, raw) == expected


def test_cast_param_value_invalid_number_raises():
    with pytest.raises(ValueError):
        mb._cast_param_value("float", "not-a-number")


def test_trick_names_match_challenge_helpers():
    """mission_builder.py's TRICK_NAMES muss exakt zu
    source/challenge_helpers.py's TRICK_NAMES passen (siehe dortiger
    Kommentar) - sonst bietet der Editor Ziel-Tricks an, die die Firmware
    gar nicht kennt (oder umgekehrt)."""
    import challenge_helpers

    assert tuple(mb.TRICK_NAMES) == challenge_helpers.TRICK_NAMES


def test_param_schema_covers_all_mission_challenge_types():
    import challenge_helpers

    assert set(mb.PARAM_SCHEMA.keys()) == set(challenge_helpers.MISSION_CHALLENGE_TYPES)
