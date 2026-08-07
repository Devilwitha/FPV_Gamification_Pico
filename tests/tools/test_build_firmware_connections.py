"""Tests fuer tools/build_firmware.py's Verbindungs-Logik:

 - HTTP-OTA (upload_bundle_to_pico(), _post_form_json()/_get_json(), Status-/
   Fehlerauswertung) - urllib.request wird gemockt, es findet nie ein echter
   Netzwerkzugriff statt.
 - Serielle mpremote-Verbindungen (Port-Erkennung, Verbindungsaufbau, Hardware-
   ID lesen, Lizenz sichern/wiederherstellen, MicroPython-Version abfragen) -
   subprocess.run() wird gemockt, es wird nie echte Hardware angesprochen.
"""
import types
import urllib.error

import pytest


class FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ==================== HTTP-OTA ====================

def test_post_form_json_success(build_firmware, monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = req.data
        return FakeHTTPResponse(b'{"ok": true, "message": "gespeichert"}')

    monkeypatch.setattr(build_firmware.request, "urlopen", fake_urlopen)
    result = build_firmware._post_form_json("http://192.168.4.1/upload-chunk", {"index": 0, "total": 1})
    assert result == {"ok": True, "message": "gespeichert"}
    assert captured["url"] == "http://192.168.4.1/upload-chunk"


def test_post_form_json_http_error_extracts_json_error_field(build_firmware, monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {}, __import__("io").BytesIO(b'{"error": "Ungueltiges Ziel"}')
        )

    monkeypatch.setattr(build_firmware.request, "urlopen", fake_urlopen)
    with pytest.raises(Exception, match="Ungueltiges Ziel"):
        build_firmware._post_form_json("http://192.168.4.1/upload-chunk", {"a": 1})


def test_post_form_json_url_error_reports_network_failure(build_firmware, monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(build_firmware.request, "urlopen", fake_urlopen)
    with pytest.raises(Exception, match="Netzwerkfehler"):
        build_firmware._post_form_json("http://192.168.4.1/upload-chunk", {"a": 1})


def test_post_form_json_invalid_json_response_raises(build_firmware, monkeypatch):
    monkeypatch.setattr(build_firmware.request, "urlopen", lambda req, timeout=None: FakeHTTPResponse(b"not json"))
    with pytest.raises(Exception, match="Ungueltige JSON-Antwort"):
        build_firmware._post_form_json("http://192.168.4.1/x", {"a": 1})


def test_get_json_success(build_firmware, monkeypatch):
    monkeypatch.setattr(build_firmware.request, "urlopen", lambda url, timeout=None: FakeHTTPResponse(b'{"ok": true}'))
    assert build_firmware._get_json("http://192.168.4.1/finalize-upload") == {"ok": True}


def test_get_json_http_error_without_json_body_falls_back_to_reason(build_firmware, monkeypatch):
    def fake_urlopen(url, timeout=None):
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, __import__("io").BytesIO(b"plain text"))

    monkeypatch.setattr(build_firmware.request, "urlopen", fake_urlopen)
    with pytest.raises(Exception, match="Internal Server Error"):
        build_firmware._get_json("http://192.168.4.1/finalize-upload")


def test_http_error_detail_unreadable_body_falls_back_to_reason(build_firmware):
    class UnreadableError:
        reason = "Service Unavailable"

        def read(self):
            raise OSError("closed")

    assert build_firmware._http_error_detail(UnreadableError()) == "Service Unavailable"


def test_upload_bundle_to_pico_success_chunks_and_finalizes(build_firmware, tmp_path, monkeypatch):
    bundle_path = tmp_path / "firmware.nbo"
    bundle_path.write_bytes(b"x" * 3000)  # gross genug fuer mehrere 1KB-Base64-Chunks

    posted_chunks = []

    def fake_post_form_json(url, form_data, timeout=8):
        posted_chunks.append((url, dict(form_data)))
        return {"ok": True}

    finalize_calls = []

    def fake_get_json(url, timeout=12):
        finalize_calls.append(url)
        return {"ok": True, "message": "fertig", "restart": True}

    monkeypatch.setattr(build_firmware, "_post_form_json", fake_post_form_json)
    monkeypatch.setattr(build_firmware, "_get_json", fake_get_json)

    progress_events = []
    result = build_firmware.upload_bundle_to_pico(
        str(bundle_path), "192.168.4.1", progress_callback=lambda done, total: progress_events.append((done, total))
    )
    assert result["ok"] is True
    assert len(posted_chunks) > 1  # Bundle wurde tatsaechlich in mehreren Chunks gesendet
    assert all(form["target"] == "firmware.nbo" for _url, form in posted_chunks)
    assert posted_chunks[0][1]["index"] == 0
    assert posted_chunks[-1][1]["index"] == len(posted_chunks) - 1
    assert progress_events[-1] == (len(posted_chunks), len(posted_chunks))
    assert finalize_calls == ["http://192.168.4.1/finalize-upload"]


def test_upload_bundle_to_pico_uses_language_target_for_lang_pak(build_firmware, tmp_path, monkeypatch):
    bundle_path = tmp_path / "lang.pak"
    bundle_path.write_bytes(b"x" * 10)

    posted = []
    monkeypatch.setattr(build_firmware, "_post_form_json", lambda url, form, timeout=8: posted.append(form) or {"ok": True})
    monkeypatch.setattr(build_firmware, "_get_json", lambda url, timeout=12: {"ok": True, "restart": False})

    build_firmware.upload_bundle_to_pico(str(bundle_path), "192.168.4.1")
    assert posted[0]["target"] == build_firmware.LANGUAGE_BUNDLE_FILENAME


def test_upload_bundle_to_pico_raises_on_chunk_error(build_firmware, tmp_path, monkeypatch):
    bundle_path = tmp_path / "firmware.nbo"
    bundle_path.write_bytes(b"x" * 10)

    monkeypatch.setattr(
        build_firmware, "_post_form_json", lambda url, form, timeout=8: {"ok": False, "error": "Speicher voll"}
    )
    with pytest.raises(Exception, match="Speicher voll"):
        build_firmware.upload_bundle_to_pico(str(bundle_path), "192.168.4.1")


def test_upload_bundle_to_pico_raises_on_finalize_error(build_firmware, tmp_path, monkeypatch):
    bundle_path = tmp_path / "firmware.nbo"
    bundle_path.write_bytes(b"x" * 10)

    monkeypatch.setattr(build_firmware, "_post_form_json", lambda url, form, timeout=8: {"ok": True})
    monkeypatch.setattr(
        build_firmware, "_get_json", lambda url, timeout=12: {"ok": False, "error": "Dekodierung fehlgeschlagen"}
    )
    with pytest.raises(Exception, match="Dekodierung fehlgeschlagen"):
        build_firmware.upload_bundle_to_pico(str(bundle_path), "192.168.4.1")


# ==================== Serielle Verbindungen (mpremote, gemockt) ====================

def _fake_completed(stdout="", stderr="", returncode=0):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_resolve_mpremote_command_prefers_venv_python(build_firmware, monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, text, timeout, check):
        calls.append(cmd)
        return _fake_completed()

    monkeypatch.setattr(build_firmware.subprocess, "run", fake_run)
    resolved = build_firmware._resolve_mpremote_command()
    assert resolved[0].endswith("python.exe")
    assert resolved[1:] == ["-m", "mpremote"]
    assert calls[0] == resolved + ["--help"]


def test_resolve_mpremote_command_falls_through_candidates_on_failure(build_firmware, monkeypatch):
    attempts = []

    def fake_run(cmd, capture_output, text, timeout, check):
        attempts.append(cmd)
        if len(attempts) < 2:
            raise FileNotFoundError("not found")
        return _fake_completed()

    # sys.executable == the .venv python running this test suite, so the
    # venv-priority and current-interpreter candidates would otherwise
    # collapse into a single deduplicated entry, leaving no real fallback
    # to exercise here - force a distinct standalone-PATH candidate instead.
    monkeypatch.setattr(build_firmware.shutil, "which", lambda name: "C:/tools/mpremote.exe")
    monkeypatch.setattr(build_firmware.subprocess, "run", fake_run)
    resolved = build_firmware._resolve_mpremote_command()
    assert len(attempts) == 2
    assert resolved + ["--help"] == attempts[1]


def test_resolve_mpremote_command_raises_when_nothing_works(build_firmware, monkeypatch):
    def fake_run(cmd, capture_output, text, timeout, check):
        raise FileNotFoundError("nope")

    monkeypatch.setattr(build_firmware.subprocess, "run", fake_run)
    monkeypatch.setattr(build_firmware.shutil, "which", lambda name: None)
    with pytest.raises(Exception, match="mpremote nicht gefunden"):
        build_firmware._resolve_mpremote_command()


@pytest.mark.parametrize("line,expected", [
    ("COM5 : Board in FS mode", "COM5"),
    ("/dev/ttyACM0 : USB serial", "/dev/ttyACM0"),
    ("no port information here", None),
])
def test_extract_serial_port_from_line(build_firmware, line, expected):
    assert build_firmware._extract_serial_port_from_line(line) == expected


def test_list_system_serial_ports_reads_device_attribute(build_firmware, monkeypatch):
    fake_ports = [types.SimpleNamespace(device="COM3"), types.SimpleNamespace(device="COM7")]
    monkeypatch.setattr(build_firmware.list_ports, "comports", lambda: fake_ports)
    assert build_firmware._list_system_serial_ports() == ["COM3", "COM7"]


def test_list_system_serial_ports_returns_empty_on_error(build_firmware, monkeypatch):
    def raise_error():
        raise OSError("no backend")

    monkeypatch.setattr(build_firmware.list_ports, "comports", raise_error)
    assert build_firmware._list_system_serial_ports() == []


def test_auto_detect_pico_ports_prioritizes_pico_labeled_lines(build_firmware, monkeypatch):
    listing_output = "COM3 : Some other USB device\nCOM9 : Board in FS mode, Raspberry Pi Pico\n"

    def fake_run_mpremote(cmd, args, timeout=15):
        return _fake_completed(stdout=listing_output)

    monkeypatch.setattr(build_firmware, "_run_mpremote", fake_run_mpremote)
    monkeypatch.setattr(build_firmware, "_list_system_serial_ports", lambda: [])

    ports = build_firmware.auto_detect_pico_ports(["mpremote"])
    assert ports[0] == "COM9"
    assert "COM3" in ports


def test_auto_detect_pico_ports_appends_system_ports_not_seen_by_mpremote(build_firmware, monkeypatch):
    monkeypatch.setattr(build_firmware, "_run_mpremote", lambda cmd, args, timeout=15: _fake_completed(stdout=""))
    monkeypatch.setattr(build_firmware, "_list_system_serial_ports", lambda: ["COM11"])
    assert build_firmware.auto_detect_pico_ports(["mpremote"]) == ["COM11"]


def test_auto_detect_pico_ports_survives_mpremote_failure(build_firmware, monkeypatch):
    def raise_error(cmd, args, timeout=15):
        raise Exception("mpremote list failed")

    monkeypatch.setattr(build_firmware, "_run_mpremote", raise_error)
    monkeypatch.setattr(build_firmware, "_list_system_serial_ports", lambda: ["COM4"])
    assert build_firmware.auto_detect_pico_ports(["mpremote"]) == ["COM4"]


def test_probe_micropython_port_true_when_echo_seen(build_firmware, monkeypatch):
    monkeypatch.setattr(
        build_firmware, "_run_mpremote", lambda cmd, args, timeout=8, retries=1, retry_delay=2.0: _fake_completed(stdout="PICO_OK\n")
    )
    assert build_firmware._probe_micropython_port(["mpremote"], "COM5") is True


def test_probe_micropython_port_false_on_exception(build_firmware, monkeypatch):
    def raise_error(cmd, args, timeout=8, retries=1, retry_delay=2.0):
        raise Exception("timeout")

    monkeypatch.setattr(build_firmware, "_run_mpremote", raise_error)
    assert build_firmware._probe_micropython_port(["mpremote"], "COM5") is False


def test_ensure_device_raw_repl_ready_never_raises_even_on_failure(build_firmware, monkeypatch):
    def raise_error(cmd, args, timeout=20, retries=2, retry_delay=3.0):
        raise Exception("could not enter raw repl")

    monkeypatch.setattr(build_firmware, "_run_mpremote", raise_error)
    build_firmware.ensure_device_raw_repl_ready(["mpremote"], "COM5")  # darf nicht werfen


def test_get_device_micropython_version_parses_last_line(build_firmware, monkeypatch):
    monkeypatch.setattr(
        build_firmware, "_run_mpremote",
        lambda cmd, args, timeout=10, retries=2, retry_delay=3.0: _fake_completed(stdout="1.22.2\n"),
    )
    assert build_firmware.get_device_micropython_version(["mpremote"], "COM5") == "1.22.2"


def test_get_device_micropython_version_returns_none_on_failure(build_firmware, monkeypatch):
    def raise_error(cmd, args, timeout=10, retries=2, retry_delay=3.0):
        raise Exception("disconnected")

    monkeypatch.setattr(build_firmware, "_run_mpremote", raise_error)
    assert build_firmware.get_device_micropython_version(["mpremote"], "COM5") is None


def test_read_hardware_id_returns_last_line(build_firmware, monkeypatch):
    monkeypatch.setattr(
        build_firmware, "_run_mpremote",
        lambda cmd, args, timeout=15, retries=2, retry_delay=3.0: _fake_completed(stdout="aabbccdd11223344\n"),
    )
    assert build_firmware.read_hardware_id(["mpremote"], "COM5") == "aabbccdd11223344"


def test_read_hardware_id_raises_when_no_output(build_firmware, monkeypatch):
    monkeypatch.setattr(
        build_firmware, "_run_mpremote",
        lambda cmd, args, timeout=15, retries=2, retry_delay=3.0: _fake_completed(stdout=""),
    )
    with pytest.raises(Exception, match="Hardware-ID"):
        build_firmware.read_hardware_id(["mpremote"], "COM5")


def test_backup_existing_license_returns_content_via_cp(build_firmware, monkeypatch, tmp_path):
    def fake_run_mpremote(cmd, args, timeout=20, retries=2, retry_delay=3.0):
        # args = ["connect", port, "cp", ":license.lic", tmp_path]
        dest = args[-1]
        with open(dest, "w", encoding="utf-8") as f:
            f.write("hardware_id=aabbccdd11223344\n---SIGNATURE---\nsig\n")
        return _fake_completed()

    monkeypatch.setattr(build_firmware, "_run_mpremote", fake_run_mpremote)
    content = build_firmware.backup_existing_license(["mpremote"], "COM5")
    assert "hardware_id=aabbccdd11223344" in content


def test_backup_existing_license_returns_none_when_absent(build_firmware, monkeypatch):
    def raise_error(cmd, args, timeout=20, retries=2, retry_delay=3.0):
        raise Exception("no such file")

    monkeypatch.setattr(build_firmware, "_run_mpremote", raise_error)
    assert build_firmware.backup_existing_license(["mpremote"], "COM5") is None


def test_restore_license_writes_temp_file_and_copies_to_device(build_firmware, monkeypatch):
    captured = {}

    def fake_run_mpremote(cmd, args, timeout=20):
        # args = ["connect", port, "cp", tmp_path, ":license.lic"]
        captured["tmp_path"] = args[-2]
        with open(args[-2], "r", encoding="utf-8") as f:
            captured["content"] = f.read()
        return _fake_completed()

    monkeypatch.setattr(build_firmware, "_run_mpremote", fake_run_mpremote)
    build_firmware.restore_license(["mpremote"], "COM5", "hardware_id=aabbccdd11223344\n")
    assert captured["content"] == "hardware_id=aabbccdd11223344\n"
    import os
    assert not os.path.exists(captured["tmp_path"])  # temp-Datei wird danach aufgeraeumt


def test_run_mpremote_retries_transient_serial_error(build_firmware, monkeypatch):
    import subprocess

    attempts = []

    def fake_run(cmd, capture_output, text, timeout, check):
        attempts.append(1)
        if len(attempts) < 2:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="could not enter raw repl")
        return _fake_completed(stdout="ok")

    monkeypatch.setattr(build_firmware.subprocess, "run", fake_run)
    monkeypatch.setattr(build_firmware.time, "sleep", lambda _s: None)
    result = build_firmware._run_mpremote(["mpremote"], ["connect", "COM5", "exec", "1"], retries=2, retry_delay=0)
    assert result.stdout == "ok"
    assert len(attempts) == 2


def test_run_mpremote_raises_friendly_message_for_blocked_port(build_firmware, monkeypatch):
    import subprocess

    def fake_run(cmd, capture_output, text, timeout, check):
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="serial.serialutil.SerialException: ClearCommError failed")

    monkeypatch.setattr(build_firmware.subprocess, "run", fake_run)
    with pytest.raises(Exception, match="blockiert oder kein gueltiger Pico-Port"):
        build_firmware._run_mpremote(["mpremote"], ["connect", "COM5", "exec", "1"])


def test_run_mpremote_raises_friendly_message_for_full_disk(build_firmware, monkeypatch):
    import subprocess

    def fake_run(cmd, capture_output, text, timeout, check):
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="OSError: [Errno 28] No space left on device")

    monkeypatch.setattr(build_firmware.subprocess, "run", fake_run)
    with pytest.raises(Exception, match="Dateisystem voll"):
        build_firmware._run_mpremote(["mpremote"], ["connect", "COM5", "exec", "1"])


def test_run_mpremote_raises_on_timeout_after_retries(build_firmware, monkeypatch):
    import subprocess

    def fake_run(cmd, capture_output, text, timeout, check):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(build_firmware.subprocess, "run", fake_run)
    monkeypatch.setattr(build_firmware.time, "sleep", lambda _s: None)
    with pytest.raises(Exception, match="Timeout"):
        build_firmware._run_mpremote(["mpremote"], ["connect", "COM5", "exec", "1"], retries=1, retry_delay=0)
