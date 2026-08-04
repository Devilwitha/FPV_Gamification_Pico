"""Tests fuer source/network_manager.py - Boot-WLAN-Check (GitHub-Firmware-
Check + Webshop-Mod-Sync) und temporaerer WLAN-Download einzelner Mods.

Folgt demselben Muster wie tests/source/test_github_ota_helpers.py's
run_update_check()-Tests: die eigentliche STA-Verbindung/Hotspot-
Wiederherstellung wird ueber github_ota_helpers' bereits getestete
Funktionen monkeypatcht, hier wird nur network_manager.py's eigene
Orchestrierung (Reihenfolge, Fehlerisolation, "Hotspot IMMER wiederherstellen")
geprueft.
"""
import json
import os

import pytest

import github_ota_helpers as gh
import network_manager as nm

# plugin_manager wird bewusst NIRGENDS auf Modulebene importiert: es macht
# beim eigenen Import `from main import debug_log`, was ohne vorherigen
# "main"-Stub das ECHTE main.py ausfuehren wuerde (main.py ruft an seinem
# Modul-Ende bedingungslos run() auf, inkl. Schreiben von
# fpv_debug_session.txt in den echten Projektordner) - ein Modul-Top-Level-
# Import laeuft bereits WAEHREND der pytest-Kollektion, also VOR jedem
# Fixture (auch vor isolated_cwd). network_manager.download_plugin_via_wifi()
# importiert plugin_manager intern IMMER (auch auf fruehen Fehlerpfaden vor
# dem WLAN-Check) - die autouse-Fixture unten stubbt "main" deshalb fuer
# JEDEN Test in dieser Datei, nicht nur fuer den einen, der plugin_manager
# direkt anspricht.


def _make_deps(restored, ssid="Home", password="12345678"):
    return {
        "log": lambda message: None,
        "feed_wdt": lambda: None,
        "load_wlan_config": lambda: {"ssid": ssid, "password": password},
        "configure_hotspot": lambda *a, **kw: restored.append(True),
        "ap_ssid": "AP",
        "ap_password": "password123",
        "firmware_version": "1.0.0",
        "repo_owner": "owner",
        "repo_name": "repo",
        "asset_name": "firmware.nbo",
    }


@pytest.fixture(autouse=True)
def _stub_main_module(install_stub_module):
    """Autouse fuer JEDEN Test in dieser Datei - siehe Kommentar oben bei den
    Imports. install_stub_module raeumt sys.modules["main"] am Testende
    automatisch wieder auf (siehe conftest.py)."""
    install_stub_module("main", debug_log=lambda message: None)


@pytest.fixture(autouse=True)
def reset_network_state():
    """network_state ist Modul-globaler Zustand (wie github_ota_state in
    main.py) - zwischen Tests zuruecksetzen, damit sie sich nicht
    gegenseitig beeinflussen."""
    nm.network_state.update({
        "last_check_ms": 0,
        "wlan_connected": False,
        "fw_update_available": False,
        "fw_current_version": "",
        "fw_latest_version": "",
        "store_sync_ok": False,
        "store_error": "",
    })
    yield


def test_boot_network_check_no_wlan_configured_does_not_touch_network(monkeypatch):
    restored = []
    connect_calls = []
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: connect_calls.append(1) or True)
    deps = _make_deps(restored, ssid="")

    nm.boot_network_check(deps)

    assert connect_calls == []
    assert restored == []  # AP war schon aktiv (boot.py) - keine Stoerung noetig.


def test_boot_network_check_wifi_connect_failure_restores_hotspot(monkeypatch):
    restored = []
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: False)
    disconnect_calls = []
    monkeypatch.setattr(gh, "disconnect_sta", lambda **kw: disconnect_calls.append(1))
    deps = _make_deps(restored)

    nm.boot_network_check(deps)

    assert nm.network_state["wlan_connected"] is False
    assert restored == [True]
    assert disconnect_calls == [1]


def test_boot_network_check_success_updates_firmware_and_store_state(monkeypatch):
    restored = []
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: True)
    monkeypatch.setattr(gh, "disconnect_sta", lambda **kw: None)
    monkeypatch.setattr(gh, "fetch_latest_release", lambda *a, **kw: ("v2.0.0", "https://example.com/fw.nbo"))
    monkeypatch.setattr(gh, "compare_versions", lambda local, remote: True)

    store_response = {"plugins": [{"name": "demo", "version": "1.0.0", "files": ["manifest.json", "main.py"]}]}
    monkeypatch.setattr(nm, "_http_get_json", lambda host, port, path, **kw: store_response)

    deps = _make_deps(restored)
    nm.boot_network_check(deps)

    assert nm.network_state["wlan_connected"] is True
    assert nm.network_state["fw_update_available"] is True
    assert nm.network_state["fw_latest_version"] == "v2.0.0"
    assert nm.network_state["store_sync_ok"] is True
    assert restored == [True]

    cache = nm.load_store_cache()
    assert cache["plugins"] == store_response["plugins"]


def test_boot_network_check_store_sync_failure_does_not_block_firmware_check(monkeypatch):
    restored = []
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: True)
    monkeypatch.setattr(gh, "disconnect_sta", lambda **kw: None)
    monkeypatch.setattr(gh, "fetch_latest_release", lambda *a, **kw: ("v1.0.0", "url"))
    monkeypatch.setattr(gh, "compare_versions", lambda local, remote: False)

    def raise_error(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(nm, "_http_get_json", raise_error)

    deps = _make_deps(restored)
    nm.boot_network_check(deps)

    assert nm.network_state["fw_update_available"] is False
    assert nm.network_state["store_sync_ok"] is False
    assert nm.network_state["store_error"]
    assert restored == [True]  # Hotspot trotzdem wiederhergestellt.


def test_boot_network_check_restores_hotspot_even_on_unexpected_exception(monkeypatch):
    restored = []
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: True)
    monkeypatch.setattr(gh, "disconnect_sta", lambda **kw: None)

    def raise_error(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(gh, "fetch_latest_release", raise_error)
    monkeypatch.setattr(nm, "_http_get_json", lambda *a, **kw: {"plugins": []})

    deps = _make_deps(restored)
    nm.boot_network_check(deps)

    assert restored == [True]


def test_download_plugin_via_wifi_no_wlan_configured():
    deps = _make_deps([], ssid="")
    result = nm.download_plugin_via_wifi("demo", deps)
    assert result["ok"] is False
    assert "WLAN" in result["error"]


def test_download_plugin_via_wifi_connect_failure_restores_hotspot(monkeypatch):
    restored = []
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: False)
    monkeypatch.setattr(gh, "disconnect_sta", lambda **kw: None)
    deps = _make_deps(restored)

    result = nm.download_plugin_via_wifi("demo", deps)

    assert result["ok"] is False
    assert restored == [True]


def test_download_plugin_via_wifi_plugin_not_found_in_store(monkeypatch):
    restored = []
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: True)
    monkeypatch.setattr(gh, "disconnect_sta", lambda **kw: None)
    monkeypatch.setattr(nm, "_http_get_json", lambda *a, **kw: {"plugins": [{"name": "other", "files": ["main.py"]}]})
    deps = _make_deps(restored)

    result = nm.download_plugin_via_wifi("demo", deps)

    assert result["ok"] is False
    assert "demo" in result["error"]
    assert restored == [True]


def test_download_plugin_via_wifi_success_writes_files_and_reloads_plugins(monkeypatch, isolated_cwd, fresh_import):
    # "main" ist bereits ueber die autouse-Fixture _stub_main_module gestubbt.
    plugin_manager = fresh_import("plugin_manager")

    restored = []
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: True)
    monkeypatch.setattr(gh, "disconnect_sta", lambda **kw: None)
    monkeypatch.setattr(
        nm,
        "_http_get_json",
        lambda *a, **kw: {"plugins": [{"name": "demo", "version": "1.0.0", "files": ["manifest.json", "main.py"]}]},
    )

    downloaded = []

    def fake_download(host, port, path, dest_path, **kw):
        downloaded.append((path, dest_path))
        with open(dest_path, "w") as f:
            f.write("{}" if dest_path.endswith(".json") else "")

    monkeypatch.setattr(nm, "_download_file", fake_download)

    reload_calls = []
    monkeypatch.setattr(plugin_manager, "load_all_plugins", lambda: reload_calls.append(1))

    deps = _make_deps(restored)
    result = nm.download_plugin_via_wifi("demo", deps)

    assert result["ok"] is True
    assert restored == [True]
    assert reload_calls == [1]
    assert os.path.isfile(os.path.join("mods", "demo", "manifest.json"))
    assert os.path.isfile(os.path.join("mods", "demo", "main.py"))
    assert len(downloaded) == 2


def test_download_plugin_via_wifi_restores_hotspot_even_when_download_raises(monkeypatch, isolated_cwd):
    restored = []
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: True)
    monkeypatch.setattr(gh, "disconnect_sta", lambda **kw: None)
    monkeypatch.setattr(
        nm,
        "_http_get_json",
        lambda *a, **kw: {"plugins": [{"name": "demo", "files": ["main.py"]}]},
    )

    def raise_error(*a, **kw):
        raise OSError("timed out")

    monkeypatch.setattr(nm, "_download_file", raise_error)

    deps = _make_deps(restored)
    result = nm.download_plugin_via_wifi("demo", deps)

    assert result["ok"] is False
    assert restored == [True]


def test_save_and_load_store_cache_roundtrip(isolated_cwd):
    nm._save_store_cache([{"name": "demo", "version": "1.0.0"}])
    cache = nm.load_store_cache()
    assert cache["plugins"] == [{"name": "demo", "version": "1.0.0"}]


def test_load_store_cache_defaults_when_missing(isolated_cwd):
    assert nm.load_store_cache() == {"plugins": [], "synced_ms": 0}


class FakeSocket:
    """Minimaler Klartext-Socket-Ersatz fuer _http_get_json()/_download_file() -
    gleiches Muster wie test_github_ota_helpers.py's FakeSocket, nur ohne TLS."""

    def __init__(self, response_bytes):
        self._buffer = response_bytes
        self.written = b""
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def connect(self, _addr):
        pass

    def write(self, data):
        self.written += data

    def readline(self):
        idx = self._buffer.find(b"\n")
        if idx < 0:
            line, self._buffer = self._buffer, b""
            return line
        line, self._buffer = self._buffer[: idx + 1], self._buffer[idx + 1:]
        return line

    def read(self, n):
        chunk, self._buffer = self._buffer[:n], self._buffer[n:]
        return chunk

    def close(self):
        self.closed = True


def test_http_get_json_parses_response_body(monkeypatch):
    import socket

    body = json.dumps({"plugins": []}).encode()
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"\r\n" + body
    )
    fake_socket = FakeSocket(response)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, ("1.2.3.4", 5000))])
    monkeypatch.setattr(socket, "socket", lambda: fake_socket)

    result = nm._http_get_json("1.2.3.4", 5000, "/api/plugins")
    assert result == {"plugins": []}
    assert b"GET /api/plugins HTTP/1.1" in fake_socket.written


def test_http_get_json_raises_on_non_200_status(monkeypatch):
    import socket

    response = b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"
    fake_socket = FakeSocket(response)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, ("1.2.3.4", 5000))])
    monkeypatch.setattr(socket, "socket", lambda: fake_socket)

    with pytest.raises(Exception):
        nm._http_get_json("1.2.3.4", 5000, "/api/plugins")


def test_download_file_writes_body_to_destination(monkeypatch, isolated_cwd):
    import socket

    body = b"print('hello')"
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"\r\n" + body
    )
    fake_socket = FakeSocket(response)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, ("1.2.3.4", 5000))])
    monkeypatch.setattr(socket, "socket", lambda: fake_socket)

    nm._download_file("1.2.3.4", 5000, "/static/plugins_store/demo/main.py", "main.py")

    with open("main.py", "rb") as f:
        assert f.read() == body
