"""Tests fuer tools/web_server.py - den lokalen Mock-HTTP-Server fuers
Frontend-Testing ohne echten Pico. Startet einen ECHTEN
ThreadingHTTPServer (auf einem automatisch vergebenen freien Port) gegen ein
temporaeres DATA_DIR, statt HTTP-Aufrufe zu mocken - Handler-Klassen aus
http.server lassen sich nicht sinnvoll ohne echten Socket testen.
"""
import http.client
import json
import threading

import pytest

import web_server as ws


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "DATA_DIR", str(tmp_path))
    for filename in ws.ROUTE_TO_FILE.values():
        (tmp_path / filename).write_text(f"<html>{filename}</html>", encoding="utf-8")

    server = ws.ThreadingHTTPServer(("127.0.0.1", 0), ws.FpvDevHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(address, path):
    conn = http.client.HTTPConnection(*address, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp, resp.read()
    finally:
        conn.close()


def _post(address, path, body=b""):
    conn = http.client.HTTPConnection(*address, timeout=5)
    try:
        conn.request("POST", path, body=body)
        resp = conn.getresponse()
        return resp, resp.read()
    finally:
        conn.close()


def test_root_serves_index_html_with_no_cache_headers(running_server):
    resp, body = _get(running_server, "/")
    assert resp.status == 200
    assert body == b"<html>index.html</html>"
    assert resp.getheader("Cache-Control") == "no-store, no-cache, must-revalidate, max-age=0"


@pytest.mark.parametrize("route,filename", list(ws.ROUTE_TO_FILE.items()))
def test_route_to_file_mapping_serves_expected_file(running_server, route, filename):
    resp, body = _get(running_server, route)
    assert resp.status == 200
    assert body == f"<html>{filename}</html>".encode()


def test_data_endpoint_returns_mock_json(running_server):
    resp, body = _get(running_server, "/data")
    assert resp.status == 200
    payload = json.loads(body)
    assert payload["highscore_player"] == "Bollshii"
    assert payload["trick_tuning_profile"] == "freestyle"


def test_version_endpoint(running_server):
    _resp, body = _get(running_server, "/version")
    assert json.loads(body) == {"version": "dev-local"}


@pytest.mark.parametrize("path", [
    "/infection-data", "/system-info", "/koth-data", "/race-data", "/challenges-data", "/profiles-list",
])
def test_mock_json_endpoints_return_ok_payloads(running_server, path):
    resp, body = _get(running_server, path)
    assert resp.status == 200
    payload = json.loads(body)
    assert payload  # nicht leer


def test_download_endpoint_serves_attachment(running_server):
    resp, body = _get(running_server, "/download")
    assert resp.status == 200
    assert "attachment" in resp.getheader("Content-Disposition")
    assert body == b"local test server download\n"


def test_download_profile_uses_requested_name(running_server):
    resp, body = _get(running_server, "/download-profile?name=custom")
    assert resp.status == 200
    assert 'filename="custom.pro"' in resp.getheader("Content-Disposition")
    assert json.loads(body)["name"] == "custom"


@pytest.mark.parametrize("path", [
    "/set-highscore-name", "/confirm-highscore", "/set-trick-profile", "/reset-highscore",
    "/simulate-trick", "/restart-pico", "/set-developer-mode", "/delete-profile",
    "/apply-profile", "/finalize-upload",
])
def test_action_endpoints_return_mock_ok(running_server, path):
    resp, body = _get(running_server, path)
    assert resp.status == 200
    assert json.loads(body) == {"ok": True, "mock": True}


def test_post_upload_chunk_consumes_body_and_acks(running_server):
    resp, body = _post(running_server, "/upload-chunk", body=b"index=0&total=1&data=abc")
    assert resp.status == 200
    assert json.loads(body) == {"ok": True, "mock": True}


def test_post_create_profile_consumes_body_and_acks(running_server):
    resp, body = _post(running_server, "/create-profile", body=b"name=x&data=%7B%7D")
    assert resp.status == 200
    assert json.loads(body) == {"ok": True, "mock": True}


def test_post_unsupported_endpoint_returns_404(running_server):
    resp, body = _post(running_server, "/does-not-exist")
    assert resp.status == 404
    assert json.loads(body)["ok"] is False


def test_clone_source_to_data_copies_when_missing(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "index.html").write_text("hi")
    data_dir = tmp_path / "data"

    monkeypatch.setattr(ws, "SOURCE_DIR", str(source_dir))
    monkeypatch.setattr(ws, "DATA_DIR", str(data_dir))
    ws.clone_source_to_data()
    assert (data_dir / "index.html").is_file()


def test_clone_source_to_data_refresh_replaces_existing(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "index.html").write_text("new")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "stale.txt").write_text("old")

    monkeypatch.setattr(ws, "SOURCE_DIR", str(source_dir))
    monkeypatch.setattr(ws, "DATA_DIR", str(data_dir))
    ws.clone_source_to_data(refresh=True)
    assert (data_dir / "index.html").is_file()
    assert not (data_dir / "stale.txt").exists()


def test_clone_source_to_data_does_not_touch_existing_without_refresh(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "index.html").write_text("new")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "keep.txt").write_text("keep me")

    monkeypatch.setattr(ws, "SOURCE_DIR", str(source_dir))
    monkeypatch.setattr(ws, "DATA_DIR", str(data_dir))
    ws.clone_source_to_data(refresh=False)
    assert (data_dir / "keep.txt").is_file()
    assert not (data_dir / "index.html").exists()
