import json

import network
import pytest

import hotspot_common


def test_load_hotspot_config_defaults_when_missing():
    config = hotspot_common.load_hotspot_config("hotspot.conf")
    assert config == {
        "ssid": hotspot_common.DEFAULT_HOTSPOT_SSID,
        "password": hotspot_common.DEFAULT_HOTSPOT_PASSWORD,
    }


def test_load_hotspot_config_reads_valid_file(tmp_path):
    path = tmp_path / "hotspot.conf"
    path.write_text(json.dumps({"ssid": "MySSID", "password": "supersecret"}))
    config = hotspot_common.load_hotspot_config(str(path))
    assert config == {"ssid": "MySSID", "password": "supersecret"}


def test_load_hotspot_config_truncates_long_ssid(tmp_path):
    path = tmp_path / "hotspot.conf"
    long_ssid = "x" * 50
    path.write_text(json.dumps({"ssid": long_ssid, "password": "supersecret"}))
    config = hotspot_common.load_hotspot_config(str(path))
    assert config["ssid"] == long_ssid[:32]


def test_load_hotspot_config_rejects_short_password(tmp_path):
    path = tmp_path / "hotspot.conf"
    path.write_text(json.dumps({"ssid": "MySSID", "password": "short"}))
    config = hotspot_common.load_hotspot_config(str(path))
    assert config["ssid"] == "MySSID"
    assert config["password"] == hotspot_common.DEFAULT_HOTSPOT_PASSWORD


def test_load_hotspot_config_ignores_blank_ssid(tmp_path):
    path = tmp_path / "hotspot.conf"
    path.write_text(json.dumps({"ssid": "   ", "password": "supersecret"}))
    config = hotspot_common.load_hotspot_config(str(path))
    assert config["ssid"] == hotspot_common.DEFAULT_HOTSPOT_SSID


def test_load_hotspot_config_survives_corrupt_json(tmp_path):
    path = tmp_path / "hotspot.conf"
    path.write_text("{not json")
    config = hotspot_common.load_hotspot_config(str(path))
    assert config["ssid"] == hotspot_common.DEFAULT_HOTSPOT_SSID
    assert config["password"] == hotspot_common.DEFAULT_HOTSPOT_PASSWORD


def test_load_hotspot_config_survives_non_dict_json(tmp_path):
    path = tmp_path / "hotspot.conf"
    path.write_text(json.dumps([1, 2, 3]))
    config = hotspot_common.load_hotspot_config(str(path))
    assert config["ssid"] == hotspot_common.DEFAULT_HOTSPOT_SSID


def test_load_wlan_config_defaults_when_missing():
    config = hotspot_common.load_wlan_config("wlan.conf")
    assert config == {"ssid": "", "password": ""}


def test_load_wlan_config_allows_empty_password_for_open_network(tmp_path):
    path = tmp_path / "wlan.conf"
    path.write_text(json.dumps({"ssid": "HomeNet", "password": ""}))
    config = hotspot_common.load_wlan_config(str(path))
    assert config == {"ssid": "HomeNet", "password": ""}


def test_load_wlan_config_truncates_ssid(tmp_path):
    path = tmp_path / "wlan.conf"
    path.write_text(json.dumps({"ssid": "x" * 40, "password": "abc"}))
    config = hotspot_common.load_wlan_config(str(path))
    assert config["ssid"] == "x" * 32


def test_configure_hotspot_sets_ssid_and_password_and_returns_ap():
    ap = hotspot_common.configure_hotspot("TestSSID", "supersecret")
    assert isinstance(ap, network.WLAN)
    assert ap.active() is True
    assert ap.config("essid") == "TestSSID"
    assert ap.ifconfig()[0] == "192.168.4.1"


def test_configure_hotspot_open_network_when_password_too_short():
    logs = []
    hotspot_common.configure_hotspot("TestSSID", "short", debug_log=logs.append)
    assert any("offenes WLAN" in message for message in logs)


def test_configure_hotspot_logs_via_debug_log_callback():
    logs = []
    hotspot_common.configure_hotspot("TestSSID", "supersecret", debug_log=logs.append)
    assert any("Access Point" in message for message in logs)
    assert any("WLAN-Hotspot aktiv" in message for message in logs)


def test_configure_hotspot_serial_debug_fallback(capsys):
    hotspot_common.configure_hotspot("TestSSID", "supersecret", serial_debug=True)
    captured = capsys.readouterr()
    assert "[AP]" in captured.out
