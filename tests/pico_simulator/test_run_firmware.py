"""Tests fuer pico_simulator/run_firmware.py's reine Datei-/Profil-Logik.

apply_real_memory_limit() wird bewusst NICHT mit einem echten Limit
aufgerufen - das wuerde den Speicher des LAUFENDEN Testprozesses per
Windows-Job-Objekt/POSIX-rlimit tatsaechlich begrenzen und den Testlauf
selbst gefaehrden. ensure_simulator_license()/main() (echte Hardware-
Simulation, Schluesselerzeugung im echten keys/-Ordner) werden hier ebenfalls
nicht getestet.
"""
import json
import os

import pytest

import run_firmware as rf


def test_normalize_profile_fills_missing_keys_from_fallback():
    fallback = {
        "mem_free_kb": 100, "mem_alloc_kb": 80, "cpu_freq_mhz": 125,
        "cpu_scale": 1.0, "net_latency_ms": 2, "real_ram_limit_mb": None,
    }
    normalized = rf._normalize_profile({"mem_free_kb": 200}, fallback)
    assert normalized["mem_free_kb"] == 200
    assert normalized["cpu_freq_mhz"] == 125


def test_normalize_profile_coerces_types():
    fallback = dict(rf.DEFAULT_SIM_PROFILES["pico_w"])
    normalized = rf._normalize_profile(
        {"mem_free_kb": "150", "cpu_scale": "2.5", "real_ram_limit_mb": "512"}, fallback
    )
    assert normalized["mem_free_kb"] == 150
    assert isinstance(normalized["mem_free_kb"], int)
    assert normalized["cpu_scale"] == 2.5
    assert normalized["real_ram_limit_mb"] == 512


def test_normalize_profile_empty_ram_limit_becomes_none():
    fallback = dict(rf.DEFAULT_SIM_PROFILES["pico_w"])
    normalized = rf._normalize_profile({"real_ram_limit_mb": ""}, fallback)
    assert normalized["real_ram_limit_mb"] is None


def test_apply_real_memory_limit_rejects_non_positive_values():
    with pytest.raises(ValueError):
        rf.apply_real_memory_limit(0)
    with pytest.raises(ValueError):
        rf.apply_real_memory_limit(-5)


def test_load_sim_profiles_creates_file_with_defaults_when_missing(tmp_path):
    profile_path = tmp_path / "sim_profiles.json"
    profiles = rf.load_sim_profiles(str(profile_path))
    assert profile_path.is_file()
    assert set(profiles.keys()) == set(rf.DEFAULT_SIM_PROFILES.keys())
    with open(profile_path) as f:
        saved = json.load(f)
    assert set(saved.keys()) == set(rf.DEFAULT_SIM_PROFILES.keys())


def test_load_sim_profiles_merges_custom_profile(tmp_path):
    profile_path = tmp_path / "sim_profiles.json"
    profile_path.write_text(json.dumps({
        "custom": {"mem_free_kb": 300, "mem_alloc_kb": 50, "cpu_freq_mhz": 200,
                   "cpu_scale": 1.0, "net_latency_ms": 0, "real_ram_limit_mb": None},
    }))
    profiles = rf.load_sim_profiles(str(profile_path))
    assert "custom" in profiles
    assert profiles["custom"]["mem_free_kb"] == 300
    assert "pico_w" in profiles  # eingebaute Profile bleiben erhalten


def test_load_sim_profiles_ignores_invalid_entries(tmp_path):
    profile_path = tmp_path / "sim_profiles.json"
    profile_path.write_text(json.dumps({
        "": {"mem_free_kb": 1},
        "bad": "not-a-dict",
        123: {"mem_free_kb": 1},
    }))
    profiles = rf.load_sim_profiles(str(profile_path))
    assert "" not in profiles
    assert "bad" not in profiles


def test_load_sim_profiles_survives_corrupt_json(tmp_path):
    profile_path = tmp_path / "sim_profiles.json"
    profile_path.write_text("{not json")
    profiles = rf.load_sim_profiles(str(profile_path))
    assert set(profiles.keys()) == set(rf.DEFAULT_SIM_PROFILES.keys())


def test_save_sim_profiles_normalizes_and_writes(tmp_path):
    profile_path = tmp_path / "nested" / "sim_profiles.json"
    rf.save_sim_profiles(str(profile_path), {"pico_w": {"mem_free_kb": "123"}})
    assert profile_path.is_file()
    with open(profile_path) as f:
        saved = json.load(f)
    assert saved["pico_w"]["mem_free_kb"] == 123


def test_rmtree_with_retry_removes_directory(tmp_path):
    target = tmp_path / "victim"
    target.mkdir()
    (target / "file.txt").write_text("x")
    rf._rmtree_with_retry(str(target))
    assert not target.exists()


def test_clone_source_to_data_copies_when_missing(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "main.py").write_text("print(1)")
    data_dir = tmp_path / "data"

    rf.clone_source_to_data(str(source_dir), str(data_dir))
    assert (data_dir / "main.py").is_file()


def test_clone_source_to_data_does_not_overwrite_existing_data(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "main.py").write_text("new content")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "existing.txt").write_text("keep me")

    rf.clone_source_to_data(str(source_dir), str(data_dir))
    assert (data_dir / "existing.txt").is_file()
    assert not (data_dir / "main.py").exists()


def test_clone_source_to_data_refresh_replaces_existing_data(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "main.py").write_text("new content")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "old.txt").write_text("stale")

    rf.clone_source_to_data(str(source_dir), str(data_dir), refresh=True)
    assert (data_dir / "main.py").is_file()
    assert not (data_dir / "old.txt").exists()
