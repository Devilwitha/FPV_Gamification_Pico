import json

import pytest

import github_ota_helpers as gh


@pytest.mark.parametrize("local,remote,expected", [
    ("1.2.7", "1.2.8", True),
    ("1.2.8", "1.2.7", False),
    ("1.2.7", "v1.2.8", True),
    ("1.2.7", "1.2.7", False),
    ("1.2.7", "1.3.0", True),
    ("1.9.9", "2.0.0", True),
    ("1.2", "1.2.1", True),
    ("1.2.0-beta", "1.2.1", True),
    ("v1.2.7", "V1.2.8", True),
    ("1.2.7", "1.2.7-beta", False),
])
def test_compare_versions(local, remote, expected):
    assert gh.compare_versions(local, remote) is expected


def test_compare_versions_handles_garbage_gracefully():
    assert gh.compare_versions("", "1.0.0") is True
    assert gh.compare_versions("1.0.0", "") is False


def test_split_url_with_scheme_and_path():
    assert gh._split_url("https://api.github.com/repos/foo/bar") == ("api.github.com", "/repos/foo/bar")


def test_split_url_without_path_defaults_to_root():
    assert gh._split_url("https://example.com") == ("example.com", "/")


def test_split_url_without_scheme():
    assert gh._split_url("example.com/a/b") == ("example.com", "/a/b")


class FakeSocket:
    """Simuliert genug von einem TLS-Socket (readline/read/write/close), um
    _read_http_head()/_read_body_bytes()/_https_request() ohne echtes
    Netzwerk zu testen."""

    def __init__(self, response_bytes):
        self._buffer = response_bytes
        self.written = b""
        self.closed = False

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


def test_read_http_head_parses_status_and_headers():
    raw = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 5\r\n\r\nhello"
    sock = FakeSocket(raw)
    status, headers = gh._read_http_head(sock)
    assert status == 200
    assert headers == {"content-type": "application/json", "content-length": "5"}


def test_read_http_head_raises_on_empty_response():
    sock = FakeSocket(b"")
    with pytest.raises(Exception):
        gh._read_http_head(sock)


def test_read_body_bytes_content_length():
    sock = FakeSocket(b"hello world")
    body = gh._read_body_bytes(sock, {"content-length": "11"})
    assert body == b"hello world"


def test_read_body_bytes_without_content_length_reads_until_empty():
    sock = FakeSocket(b"streamed-body")
    body = gh._read_body_bytes(sock, {})
    assert body == b"streamed-body"


def test_read_body_bytes_chunked_transfer_encoding():
    raw = b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"
    sock = FakeSocket(raw)
    body = gh._read_body_bytes(sock, {"transfer-encoding": "chunked"})
    assert body == b"hello world"


def test_read_body_bytes_raises_when_exceeding_max_bytes():
    sock = FakeSocket(b"x" * 100)
    with pytest.raises(Exception, match="zu gross"):
        gh._read_body_bytes(sock, {"content-length": "100"}, max_bytes=10)


def test_fetch_latest_release_success(monkeypatch):
    payload = json.dumps({
        "tag_name": "v1.4.0",
        "assets": [
            {"name": "firmware.nbo", "browser_download_url": "https://example.com/firmware.nbo"},
            {"name": "other.bin", "browser_download_url": "https://example.com/other.bin"},
        ],
    }).encode("utf-8")

    def fake_https_request(host, path, headers=None, log=gh._noop_log, feed_wdt=gh._noop_feed_wdt, max_body_bytes=None):
        return 200, {}, payload

    monkeypatch.setattr(gh, "_https_request", fake_https_request)
    tag, url = gh.fetch_latest_release("owner", "repo", "firmware.nbo")
    assert tag == "v1.4.0"
    assert url == "https://example.com/firmware.nbo"


def test_fetch_latest_release_asset_not_found(monkeypatch):
    payload = json.dumps({"tag_name": "v1.4.0", "assets": []}).encode("utf-8")
    monkeypatch.setattr(gh, "_https_request", lambda *a, **kw: (200, {}, payload))
    tag, url = gh.fetch_latest_release("owner", "repo", "firmware.nbo")
    assert tag == "v1.4.0"
    assert url is None


def test_fetch_latest_release_non_200_status(monkeypatch):
    monkeypatch.setattr(gh, "_https_request", lambda *a, **kw: (404, {}, b"{}"))
    tag, url = gh.fetch_latest_release("owner", "repo", "firmware.nbo")
    assert (tag, url) == (None, None)


def test_fetch_latest_release_connection_error(monkeypatch):
    def raise_error(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(gh, "_https_request", raise_error)
    tag, url = gh.fetch_latest_release("owner", "repo", "firmware.nbo")
    assert (tag, url) == (None, None)


def test_fetch_latest_release_invalid_json(monkeypatch):
    monkeypatch.setattr(gh, "_https_request", lambda *a, **kw: (200, {}, b"not json"))
    tag, url = gh.fetch_latest_release("owner", "repo", "firmware.nbo")
    assert (tag, url) == (None, None)


def test_connect_sta_with_retries_success_on_first_attempt():
    import network

    ok = gh.connect_sta_with_retries("HomeNet", "secretpw", attempts=3, timeout_ms=200)
    assert ok is True
    sta = network.WLAN(network.STA_IF)
    assert sta.isconnected() is True


def test_connect_sta_with_retries_fails_when_never_connects(monkeypatch):
    import network

    class NeverConnects(network.WLAN):
        def isconnected(self):
            return False

    monkeypatch.setattr(network, "WLAN", NeverConnects)
    ok = gh.connect_sta_with_retries("HomeNet", "secretpw", attempts=1, timeout_ms=10)
    assert ok is False


def test_disconnect_sta_disables_sta_interface():
    import network

    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    gh.disconnect_sta()
    assert sta.active() is False


def test_run_update_check_no_wlan_configured():
    state = {}
    deps = {
        "load_wlan_config": lambda: {"ssid": "", "password": ""},
        "configure_hotspot": lambda *a, **kw: None,
        "ap_ssid": "AP",
        "ap_password": "password123",
        "firmware_version": "1.0.0",
        "apply_firmware_bundle": lambda path: ([], False),
        "repo_owner": "owner",
        "repo_name": "repo",
        "asset_name": "firmware.nbo",
        "staging_path": "staging.tmp",
        "state": state,
    }
    gh.run_update_check(deps)
    assert state["phase"] == "no_wlan"
    assert state["ok"] is False


def test_run_update_check_wifi_connect_failure(monkeypatch):
    state = {}
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: False)
    restored = []
    deps = {
        "load_wlan_config": lambda: {"ssid": "Home", "password": "12345678"},
        "configure_hotspot": lambda *a, **kw: restored.append(True),
        "ap_ssid": "AP",
        "ap_password": "password123",
        "firmware_version": "1.0.0",
        "apply_firmware_bundle": lambda path: ([], False),
        "repo_owner": "owner",
        "repo_name": "repo",
        "asset_name": "firmware.nbo",
        "staging_path": "staging.tmp",
        "state": state,
    }
    gh.run_update_check(deps)
    assert state["phase"] == "wifi_failed"
    assert state["ok"] is False
    assert restored  # Hotspot muss nach einem Fehlschlag wiederhergestellt werden.


def test_run_update_check_already_up_to_date(monkeypatch):
    state = {}
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: True)
    monkeypatch.setattr(gh, "fetch_latest_release", lambda *a, **kw: ("v1.0.0", "https://example.com/fw.nbo"))
    deps = {
        "load_wlan_config": lambda: {"ssid": "Home", "password": "12345678"},
        "configure_hotspot": lambda *a, **kw: None,
        "ap_ssid": "AP",
        "ap_password": "password123",
        "firmware_version": "1.0.0",
        "apply_firmware_bundle": lambda path: ([], False),
        "repo_owner": "owner",
        "repo_name": "repo",
        "asset_name": "firmware.nbo",
        "staging_path": "staging.tmp",
        "state": state,
    }
    gh.run_update_check(deps)
    assert state["phase"] == "up_to_date"
    assert state["ok"] is True


def test_run_update_check_full_success_flow(monkeypatch):
    state = {}
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: True)
    monkeypatch.setattr(gh, "fetch_latest_release", lambda *a, **kw: ("v2.0.0", "https://example.com/fw.nbo"))

    downloaded = []

    def fake_download(url, dest_path, **kwargs):
        downloaded.append((url, dest_path))
        with open(dest_path, "w") as f:
            f.write("fake-bundle")
        return True

    monkeypatch.setattr(gh, "_https_download_to_file", fake_download)

    applied = []

    def fake_apply(path):
        applied.append(path)
        return (["main.py"], True)

    deps = {
        "load_wlan_config": lambda: {"ssid": "Home", "password": "12345678"},
        "configure_hotspot": lambda *a, **kw: None,
        "ap_ssid": "AP",
        "ap_password": "password123",
        "firmware_version": "1.0.0",
        "apply_firmware_bundle": fake_apply,
        "repo_owner": "owner",
        "repo_name": "repo",
        "asset_name": "firmware.nbo",
        "staging_path": "staging.tmp",
        "state": state,
    }
    gh.run_update_check(deps)
    assert state["phase"] == "success"
    assert state["ok"] is True
    assert state["restart_pending"] is True
    assert applied == ["staging.tmp"]


def test_run_update_check_release_not_found(monkeypatch):
    state = {}
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: True)
    monkeypatch.setattr(gh, "fetch_latest_release", lambda *a, **kw: (None, None))
    deps = {
        "load_wlan_config": lambda: {"ssid": "Home", "password": "12345678"},
        "configure_hotspot": lambda *a, **kw: None,
        "ap_ssid": "AP",
        "ap_password": "password123",
        "firmware_version": "1.0.0",
        "apply_firmware_bundle": lambda path: ([], False),
        "repo_owner": "owner",
        "repo_name": "repo",
        "asset_name": "firmware.nbo",
        "staging_path": "staging.tmp",
        "state": state,
    }
    gh.run_update_check(deps)
    assert state["phase"] == "check_failed"
    assert state["ok"] is False


def test_run_update_check_handles_unexpected_exception(monkeypatch):
    state = {}
    monkeypatch.setattr(gh, "connect_sta_with_retries", lambda *a, **kw: True)

    def raise_error(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(gh, "fetch_latest_release", raise_error)
    deps = {
        "load_wlan_config": lambda: {"ssid": "Home", "password": "12345678"},
        "configure_hotspot": lambda *a, **kw: None,
        "ap_ssid": "AP",
        "ap_password": "password123",
        "firmware_version": "1.0.0",
        "apply_firmware_bundle": lambda path: ([], False),
        "repo_owner": "owner",
        "repo_name": "repo",
        "asset_name": "firmware.nbo",
        "staging_path": "staging.tmp",
        "state": state,
    }
    gh.run_update_check(deps)
    assert state["phase"] == "error"
    assert state["ok"] is False
    assert "boom" in state["error"]
