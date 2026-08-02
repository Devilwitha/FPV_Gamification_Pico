"""Tests fuer tools/LilyGo.py's reine Port-Auswahllogik.

Der Rest des Skripts (esptool-Flash-Vorgaenge, mpremote-Dateiuebertragung,
Tkinter-GUI) braucht echte ESP32-Hardware bzw. einen Display-Server und wird
hier nicht getestet. install_requirements() laeuft beim Import einmal (siehe
Modul-Docstring-Analogie in LilyGo.py selbst) - alle drei Pakete
(esptool/pyserial/mpremote) sind bereits ueber requirements.txt installiert,
der Import loest daher keine echte pip-Installation aus.
"""
import types

import LilyGo


def _fake_port(device, vid=None, pid=None):
    return types.SimpleNamespace(device=device, vid=vid, pid=pid)


def test_find_current_lilygo_port_prefers_explicit_preferred_port(monkeypatch):
    ports = [_fake_port("COM3"), _fake_port("COM5")]
    monkeypatch.setattr(LilyGo.serial.tools.list_ports, "comports", lambda: ports)
    assert LilyGo.find_current_lilygo_port(preferred_port="com5") == "COM5"


def test_find_current_lilygo_port_matches_vid_pid_when_no_preferred(monkeypatch):
    ports = [_fake_port("COM3", vid=0x1234, pid=0x5678), _fake_port("COM9", vid=LilyGo.LILYGO_USB_VID, pid=LilyGo.LILYGO_USB_PID)]
    monkeypatch.setattr(LilyGo.serial.tools.list_ports, "comports", lambda: ports)
    assert LilyGo.find_current_lilygo_port() == "COM9"


def test_find_current_lilygo_port_falls_back_to_first_available(monkeypatch):
    ports = [_fake_port("COM3", vid=0x1234, pid=0x5678)]
    monkeypatch.setattr(LilyGo.serial.tools.list_ports, "comports", lambda: ports)
    assert LilyGo.find_current_lilygo_port() == "COM3"


def test_find_current_lilygo_port_returns_none_when_no_ports(monkeypatch):
    monkeypatch.setattr(LilyGo.serial.tools.list_ports, "comports", lambda: [])
    assert LilyGo.find_current_lilygo_port() is None


def test_find_current_lilygo_port_preferred_not_present_falls_back_to_vid_pid(monkeypatch):
    ports = [_fake_port("COM3", vid=LilyGo.LILYGO_USB_VID, pid=LilyGo.LILYGO_USB_PID)]
    monkeypatch.setattr(LilyGo.serial.tools.list_ports, "comports", lambda: ports)
    assert LilyGo.find_current_lilygo_port(preferred_port="COM99") == "COM3"
