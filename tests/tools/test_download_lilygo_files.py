"""Tests fuer tools/download_lilygo_files.py's reine Port-Auswahllogik.

run_mpremote()/enter_raw_repl()/copy_remote_file() etc. brauchen ein echtes
serielles Geraet und werden hier nicht getestet.
"""
import types

import download_lilygo_files as dlf


def _fake_port(device, vid=None, pid=None):
    return types.SimpleNamespace(device=device, vid=vid, pid=pid)


def test_find_lilygo_port_matches_vid_pid(monkeypatch):
    ports = [
        _fake_port("COM3", vid=0x1234, pid=0x5678),
        _fake_port("COM5", vid=dlf.LILYGO_USB_VID, pid=dlf.LILYGO_USB_PID),
    ]
    monkeypatch.setattr(dlf.list_ports, "comports", lambda: ports)
    assert dlf.find_lilygo_port() == "COM5"


def test_find_lilygo_port_returns_none_when_no_match(monkeypatch):
    ports = [_fake_port("COM3", vid=0x1234, pid=0x5678)]
    monkeypatch.setattr(dlf.list_ports, "comports", lambda: ports)
    assert dlf.find_lilygo_port() is None


def test_find_lilygo_port_preferred_port_is_case_insensitive(monkeypatch):
    ports = [_fake_port("COM7")]
    monkeypatch.setattr(dlf.list_ports, "comports", lambda: ports)
    assert dlf.find_lilygo_port(preferred_port="com7") == "COM7"


def test_find_lilygo_port_preferred_port_not_found_returns_none(monkeypatch):
    ports = [_fake_port("COM7")]
    monkeypatch.setattr(dlf.list_ports, "comports", lambda: ports)
    assert dlf.find_lilygo_port(preferred_port="COM9") is None


def test_run_mpremote_builds_expected_command(monkeypatch):
    captured = {}

    def fake_run(command, capture_output, text, check):
        captured["command"] = command
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dlf.subprocess, "run", fake_run)
    dlf.run_mpremote("COM5", ["exec", "print(1)"])
    assert captured["command"][-5:] == ["connect", "COM5", "resume", "exec", "print(1)"]


def test_verify_lilygo_true_when_marker_reported(monkeypatch):
    monkeypatch.setattr(dlf, "run_mpremote", lambda port, args, check=True: types.SimpleNamespace(returncode=0, stdout="LILYGO_OK\n"))
    assert dlf.verify_lilygo("COM5") is True


def test_verify_lilygo_false_when_not_reported(monkeypatch):
    monkeypatch.setattr(dlf, "run_mpremote", lambda port, args, check=True: types.SimpleNamespace(returncode=0, stdout="NOT_LILYGO\n"))
    assert dlf.verify_lilygo("COM5") is False


def test_list_remote_files_parses_pipe_separated_list(monkeypatch):
    monkeypatch.setattr(
        dlf, "run_mpremote",
        lambda port, args, check=True: types.SimpleNamespace(returncode=0, stdout="FILES:a.txt|b.txt\n"),
    )
    assert dlf.list_remote_files("COM5") == {"a.txt", "b.txt"}


def test_list_remote_files_empty_when_no_marker(monkeypatch):
    monkeypatch.setattr(
        dlf, "run_mpremote", lambda port, args, check=True: types.SimpleNamespace(returncode=0, stdout=""),
    )
    assert dlf.list_remote_files("COM5") == set()
