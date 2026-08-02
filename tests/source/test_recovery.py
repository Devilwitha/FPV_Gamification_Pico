"""Tests fuer source/recovery.py (Notfall-/OTA-only-Firmware).

Wie role_setup.py ruft recovery.py am Modul-Ende bedingungslos `run()` auf -
siehe test_role_setup.py's Docstring fuer die Begruendung des
import_entry_module-Fixtures.
"""
import base64
import json
import struct
import urllib.parse

import pytest


@pytest.fixture
def recovery(import_entry_module):
    return import_entry_module("recovery")


def _post_request(path, body_text):
    body_bytes = body_text.encode("utf-8")
    request_line = f"POST {path} HTTP/1.1\r\n".encode()
    headers = [f"Content-Length: {len(body_bytes)}\r\n".encode()]
    return request_line, headers, body_bytes


@pytest.mark.asyncio
async def test_default_path_serves_recovery_page(recovery, make_reader, fake_writer):
    reader = make_reader(b"GET / HTTP/1.1\r\n")
    await recovery.handle_client(reader, fake_writer)
    assert "200" in fake_writer.status_line
    assert b"RECOVERY OTA" in fake_writer.response
    assert recovery.AP_SSID.encode() in fake_writer.response


@pytest.mark.asyncio
async def test_system_info_reports_recovery_mode(recovery, make_reader, fake_writer):
    reader = make_reader(b"GET /system-info HTTP/1.1\r\n")
    await recovery.handle_client(reader, fake_writer)
    body = fake_writer.json()
    assert body["recovery_mode"] is True
    assert body["board_type"] == "Unbekannt"


@pytest.mark.asyncio
async def test_upload_chunk_rejects_disallowed_target(recovery, make_reader, fake_writer):
    request_line, headers, body = _post_request("/upload-chunk", "index=0&total=1&target=evil.py&data=aGVsbG8=")
    reader = make_reader(request_line, headers=headers, body=body)
    await recovery.handle_client(reader, fake_writer)
    assert "400" in fake_writer.status_line


@pytest.mark.asyncio
async def test_upload_chunk_accepts_allowed_target(recovery, make_reader, fake_writer):
    request_line, headers, body = _post_request("/upload-chunk", "index=0&total=1&target=index.html&data=aGVsbG8=")
    reader = make_reader(request_line, headers=headers, body=body)
    await recovery.handle_client(reader, fake_writer)
    assert "200" in fake_writer.status_line
    with open("update.pbp") as f:
        assert f.read() == "aGVsbG8="


@pytest.mark.asyncio
async def test_upload_chunk_accepts_bundle_target(recovery, make_reader, fake_writer):
    request_line, headers, body = _post_request(
        "/upload-chunk", "index=0&total=1&target=firmware.nbo&data=aGVsbG8="
    )
    reader = make_reader(request_line, headers=headers, body=body)
    await recovery.handle_client(reader, fake_writer)
    assert "200" in fake_writer.status_line


@pytest.mark.asyncio
async def test_finalize_upload_single_file_success(recovery, make_reader, fake_writer, fast_sleep_ms):
    import machine

    content = b"<html>updated</html>"
    b64 = urllib.parse.quote(base64.b64encode(content).decode(), safe='')
    request_line, headers, body = _post_request(
        "/upload-chunk", f"index=0&total=1&target=index.html&data={b64}"
    )
    reader = make_reader(request_line, headers=headers, body=body)
    await recovery.handle_client(reader, fake_writer)
    assert "200" in fake_writer.status_line

    from tests.source.conftest import FakeWriter
    reset_calls = []
    original = machine.reset
    machine.reset = lambda: reset_calls.append(True)
    try:
        finalize_writer = FakeWriter()
        reader2 = make_reader(b"GET /finalize-upload HTTP/1.1\r\n")
        await recovery.handle_client(reader2, finalize_writer)
    finally:
        machine.reset = original

    body_json = finalize_writer.json()
    assert body_json["ok"] is True
    assert body_json["restart"] is False  # index.html loest keinen Neustart aus
    with open("index.html", "rb") as f:
        assert f.read() == content
    assert reset_calls == []


@pytest.mark.asyncio
async def test_finalize_upload_main_py_triggers_restart(recovery, make_reader, fake_writer, fast_sleep_ms):
    import machine

    content = b"print('hi')"
    b64 = urllib.parse.quote(base64.b64encode(content).decode(), safe='')
    request_line, headers, body = _post_request("/upload-chunk", f"index=0&total=1&target=main.py&data={b64}")
    reader = make_reader(request_line, headers=headers, body=body)
    await recovery.handle_client(reader, fake_writer)

    from tests.source.conftest import FakeWriter
    reset_calls = []
    original = machine.reset
    machine.reset = lambda: reset_calls.append(True)
    try:
        finalize_writer = FakeWriter()
        reader2 = make_reader(b"GET /finalize-upload HTTP/1.1\r\n")
        await recovery.handle_client(reader2, finalize_writer)
    finally:
        machine.reset = original

    body_json = finalize_writer.json()
    assert body_json["restart"] is True
    assert reset_calls == [True]


@pytest.mark.asyncio
async def test_finalize_upload_without_prior_chunks_fails(recovery, make_reader, fake_writer):
    reader = make_reader(b"GET /finalize-upload HTTP/1.1\r\n")
    await recovery.handle_client(reader, fake_writer)
    assert "500" in fake_writer.status_line
    assert fake_writer.json()["ok"] is False


@pytest.mark.asyncio
async def test_finalize_upload_bundle_extracts_files(recovery, make_reader, fake_writer, fast_sleep_ms):
    import machine

    bundle = bytearray()
    bundle += recovery.OTA_BUNDLE_MAGIC
    bundle += struct.pack(">I", 1)
    name = b"index.html"
    bundle += struct.pack(">I", len(name)) + name
    content = b"<h1>bundled</h1>"
    bundle += struct.pack(">I", len(content)) + content
    b64 = urllib.parse.quote(base64.b64encode(bytes(bundle)).decode(), safe='')

    request_line, headers, body = _post_request(
        "/upload-chunk", f"index=0&total=1&target=firmware.nbo&data={b64}"
    )
    reader = make_reader(request_line, headers=headers, body=body)
    await recovery.handle_client(reader, fake_writer)

    original = machine.reset
    machine.reset = lambda: None
    try:
        from tests.source.conftest import FakeWriter
        finalize_writer = FakeWriter()
        reader2 = make_reader(b"GET /finalize-upload HTTP/1.1\r\n")
        await recovery.handle_client(reader2, finalize_writer)
    finally:
        machine.reset = original

    body_json = finalize_writer.json()
    assert body_json["ok"] is True
    with open("index.html", "rb") as f:
        assert f.read() == content


@pytest.mark.asyncio
async def test_restart_pico_calls_machine_reset(recovery, make_reader, fake_writer, fast_sleep_ms):
    import machine

    reset_calls = []
    original = machine.reset
    machine.reset = lambda: reset_calls.append(True)
    try:
        reader = make_reader(b"GET /restart-pico HTTP/1.1\r\n")
        await recovery.handle_client(reader, fake_writer)
    finally:
        machine.reset = original
    assert reset_calls == [True]


@pytest.mark.asyncio
async def test_emergency_delete_main_requires_confirmation(recovery, make_reader, fake_writer):
    reader = make_reader(b"GET /emergency-delete-main HTTP/1.1\r\n")
    await recovery.handle_client(reader, fake_writer)
    assert "400" in fake_writer.status_line


@pytest.mark.asyncio
async def test_emergency_delete_main_confirmed_deletes_file(recovery, make_reader, fake_writer, fast_sleep_ms):
    import machine
    import os

    with open("main.py", "w") as f:
        f.write("x")
    original = machine.reset
    machine.reset = lambda: None
    try:
        reader = make_reader(b"GET /emergency-delete-main?confirm=1 HTTP/1.1\r\n")
        await recovery.handle_client(reader, fake_writer)
    finally:
        machine.reset = original
    assert fake_writer.json()["deleted"] == ["main.py"]
    assert not os.path.exists("main.py")


@pytest.mark.asyncio
async def test_emergency_delete_boot_confirmed_deletes_file(recovery, make_reader, fake_writer, fast_sleep_ms):
    import machine
    import os

    with open("boot.py", "w") as f:
        f.write("x")
    original = machine.reset
    machine.reset = lambda: None
    try:
        reader = make_reader(b"GET /emergency-delete-boot?confirm=1 HTTP/1.1\r\n")
        await recovery.handle_client(reader, fake_writer)
    finally:
        machine.reset = original
    assert not os.path.exists("boot.py")


@pytest.mark.asyncio
async def test_finalize_upload_requests_main_retry_via_boot_runtime(recovery, make_reader, fake_writer, fast_sleep_ms):
    import machine
    import boot_runtime

    content = b"<html></html>"
    b64 = urllib.parse.quote(base64.b64encode(content).decode(), safe='')
    request_line, headers, body = _post_request("/upload-chunk", f"index=0&total=1&target=index.html&data={b64}")
    reader = make_reader(request_line, headers=headers, body=body)
    await recovery.handle_client(reader, fake_writer)

    original = machine.reset
    machine.reset = lambda: None
    try:
        from tests.source.conftest import FakeWriter
        finalize_writer = FakeWriter()
        reader2 = make_reader(b"GET /finalize-upload HTTP/1.1\r\n")
        await recovery.handle_client(reader2, finalize_writer)
    finally:
        machine.reset = original

    assert boot_runtime.consume_main_retry_once() is True
