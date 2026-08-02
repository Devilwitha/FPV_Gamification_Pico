import base64
import json
import os
import struct

import pytest

import upload_helpers as uh
import ota_helpers


def _fake_decode_to_file(src, dst):
    """Ersetzt safe_base64_file_to_file() in Tests: kopiert die Datei nur
    (ohne echtes Base64-Dekodieren) und liefert garantiert True zurueck."""
    import shutil

    shutil.copyfile(src, dst)
    return True


def _make_deps(**overrides):
    logs = []
    deps = {
        "debug_log": logs.append,
        "ota_staging_path": "ota_staging.tmp",
        "ota_bundle_target": "firmware.nbo",
        "ota_lang_bundle_target": "lang.pak",
        "firmware_version_file": "firmware_version.txt",
        "url_decode": ota_helpers.url_decode,
        "developer_mode_enabled": False,
        "ota_allowed_targets": ("main.py", "index.html"),
        "license_upload_targets": ("license.lic", "public_key.pem"),
        "apply_firmware_bundle_from_base64": None,
        "safe_base64_file_to_file": None,
    }
    deps.update(overrides)
    deps["_logs"] = logs
    return deps


@pytest.fixture
def ota_state():
    return {"total_chunks": 0, "received_chunks": 0, "update_active": False, "target_file": "main.py"}


def test_cleanup_update_artifacts_removes_fixed_files():
    deps = _make_deps()
    for name in ("update.pbp", "ota_staging.tmp", "firmware.nbo", "lang.pak"):
        open(name, "w").close()
    uh.cleanup_update_artifacts(("main.py",), deps)
    for name in ("update.pbp", "ota_staging.tmp", "firmware.nbo", "lang.pak"):
        assert not os.path.exists(name)


def test_cleanup_update_artifacts_preserves_user_files():
    deps = _make_deps()
    open("MyProfile.pro", "w").close()
    open("FPV_Ausweiss.jpg", "w").close()
    uh.cleanup_update_artifacts(("main.py",), deps)
    assert os.path.exists("MyProfile.pro")
    assert os.path.exists("FPV_Ausweiss.jpg")


def test_cleanup_update_artifacts_removes_txt_and_pak_except_firmware_version():
    deps = _make_deps()
    open("firmware_version.txt", "w").close()
    open("debug_log.txt", "w").close()
    open("de.pak", "w").close()
    uh.cleanup_update_artifacts((), deps, remove_fixed_files=False, remove_txt_and_non_en_pak=True)
    assert os.path.exists("firmware_version.txt")
    assert not os.path.exists("debug_log.txt")
    assert not os.path.exists("de.pak")


def test_cleanup_update_artifacts_removes_backup_and_bndl_tmp_for_managed_files():
    deps = _make_deps()
    open("main_backup.py", "w").close()
    open("index.html.bak", "w").close()
    open("main.py.bndl_tmp", "w").close()
    open("unmanaged.py.bak", "w").close()
    uh.cleanup_update_artifacts(("main.py", "index.html"), deps, remove_fixed_files=False)
    assert not os.path.exists("main_backup.py")
    assert not os.path.exists("index.html.bak")
    assert not os.path.exists("main.py.bndl_tmp")
    assert os.path.exists("unmanaged.py.bak")


def test_cleanup_update_artifacts_removes_explicit_target_files():
    deps = _make_deps()
    open("old_bundle_leftover.html", "w").close()
    uh.cleanup_update_artifacts(
        (), deps, remove_fixed_files=False, remove_target_files=["old_bundle_leftover.html"]
    )
    assert not os.path.exists("old_bundle_leftover.html")


@pytest.mark.asyncio
async def test_handle_prepare_upload_rejects_disallowed_target(fake_writer):
    deps = _make_deps()
    ota_state = {}
    await uh.handle_prepare_upload(fake_writer, {"target": "boot.py"}, {}, ota_state, deps)
    assert "400" in fake_writer.status_line
    assert fake_writer.json()["ok"] is False


@pytest.mark.asyncio
async def test_handle_prepare_upload_rejects_single_file_when_developer_mode_off(fake_writer):
    deps = _make_deps(developer_mode_enabled=False)
    ota_state = {}
    await uh.handle_prepare_upload(fake_writer, {"target": "main.py"}, {}, ota_state, deps)
    assert "400" in fake_writer.status_line
    assert "Developer-Modus" in fake_writer.json()["error"]


@pytest.mark.asyncio
async def test_handle_prepare_upload_allows_single_file_with_developer_mode(fake_writer):
    deps = _make_deps(developer_mode_enabled=True)
    ota_state = {}
    await uh.handle_prepare_upload(fake_writer, {"target": "main.py"}, {}, ota_state, deps)
    assert "200" in fake_writer.status_line
    body = fake_writer.json()
    assert body["ok"] is True
    assert ota_state["target_file"] == "main.py"
    assert ota_state["update_active"] is False


@pytest.mark.asyncio
async def test_handle_prepare_upload_always_allows_license_targets(fake_writer):
    deps = _make_deps(developer_mode_enabled=False)
    ota_state = {}
    await uh.handle_prepare_upload(fake_writer, {"target": "license.lic"}, {}, ota_state, deps)
    assert "200" in fake_writer.status_line


@pytest.mark.asyncio
async def test_handle_prepare_upload_bundle_mode_complete_removes_all_targets():
    removed = []
    deps = _make_deps(ota_allowed_targets=("main.py", "index.html"))
    writer_calls = []

    class Writer:
        def write(self, data):
            writer_calls.append(data)

        async def drain(self):
            pass

    open("main.py", "w").close()
    open("index.html", "w").close()
    ota_state = {}
    await uh.handle_prepare_upload(
        Writer(), {"target": "firmware.nbo", "bundle_mode": "complete"}, {}, ota_state, deps
    )
    assert not os.path.exists("main.py")
    assert not os.path.exists("index.html")


@pytest.mark.asyncio
async def test_handle_upload_chunk_rejects_invalid_target(fake_writer, ota_state):
    deps = _make_deps(developer_mode_enabled=False)
    body_text = "index=0&total=1&target=boot.py&data=aGVsbG8="
    await uh.handle_upload_chunk(fake_writer, body_text, {}, ota_state, deps)
    assert "400" in fake_writer.status_line


@pytest.mark.asyncio
async def test_handle_upload_chunk_writes_chunk_and_tracks_progress(fake_writer, ota_state):
    deps = _make_deps(developer_mode_enabled=True)
    body_text = "index=0&total=2&target=main.py&data=aGVsbG8="
    await uh.handle_upload_chunk(fake_writer, body_text, {}, ota_state, deps)
    assert "200" in fake_writer.status_line
    assert ota_state["received_chunks"] == 1
    assert ota_state["total_chunks"] == 2
    # handle_upload_chunk() dekodiert noch nicht - es haengt die noch
    # base64-kodierten Rohdaten an update.pbp an (Dekodierung passiert erst
    # spaeter in handle_finalize_upload()).
    with open("update.pbp") as f:
        assert f.read() == "aGVsbG8="


@pytest.mark.asyncio
async def test_handle_upload_chunk_appends_across_multiple_chunks(ota_state):
    from tests.source.conftest import FakeWriter

    deps = _make_deps(developer_mode_enabled=True)
    await uh.handle_upload_chunk(FakeWriter(), "index=0&total=2&target=main.py&data=aGVs", {}, ota_state, deps)
    await uh.handle_upload_chunk(FakeWriter(), "index=1&total=2&target=main.py&data=bG8=", {}, ota_state, deps)
    assert ota_state["received_chunks"] == 2
    with open("update.pbp") as f:
        assert f.read() == "aGVsbG8="


@pytest.mark.asyncio
async def test_handle_finalize_upload_single_file_success(fake_writer, ota_state, fast_sleep_ms):
    deps = _make_deps(
        safe_base64_file_to_file=_fake_decode_to_file,
    )
    with open("update.pbp", "w") as f:
        f.write("irrelevant-in-this-fake")
    ota_state.update(total_chunks=1, received_chunks=1, target_file="index.html")
    await uh.handle_finalize_upload(fake_writer, ota_state, deps)
    assert "200" in fake_writer.status_line
    body = fake_writer.json()
    assert body["ok"] is True
    assert body["restart"] is False
    assert os.path.exists("index.html")
    assert ota_state["update_active"] is False


@pytest.mark.asyncio
async def test_handle_finalize_upload_main_py_triggers_restart_flag(fake_writer, ota_state, fast_sleep_ms):
    reset_calls = []
    import machine
    machine_reset_original = machine.reset
    machine.reset = lambda: reset_calls.append(True)
    try:
        deps = _make_deps(
            safe_base64_file_to_file=_fake_decode_to_file,
        )
        open("update.pbp", "w").close()
        ota_state.update(total_chunks=1, received_chunks=1, target_file="main.py")
        await uh.handle_finalize_upload(fake_writer, ota_state, deps)
        body = fake_writer.json()
        assert body["restart"] is True
        assert reset_calls == [True]
    finally:
        machine.reset = machine_reset_original


@pytest.mark.asyncio
async def test_handle_finalize_upload_incomplete_chunks_fails(fake_writer, ota_state):
    deps = _make_deps()
    ota_state.update(total_chunks=3, received_chunks=1, target_file="main.py")
    await uh.handle_finalize_upload(fake_writer, ota_state, deps)
    assert "500" in fake_writer.status_line
    assert fake_writer.json()["ok"] is False


@pytest.mark.asyncio
async def test_handle_finalize_upload_bundle_success(fake_writer, ota_state, fast_sleep_ms):
    def fake_apply_bundle(path):
        return (["main.py", "index.html"], True)

    deps = _make_deps(apply_firmware_bundle_from_base64=fake_apply_bundle)
    open("update.pbp", "w").close()
    ota_state.update(total_chunks=1, received_chunks=1, target_file="firmware.nbo")

    import machine
    machine_reset_original = machine.reset
    machine.reset = lambda: None
    try:
        await uh.handle_finalize_upload(fake_writer, ota_state, deps)
    finally:
        machine.reset = machine_reset_original

    body = fake_writer.json()
    assert body["ok"] is True
    assert body["restart"] is True
    assert "2 Datei(en)" in body["message"]


@pytest.mark.asyncio
async def test_handle_finalize_upload_license_refreshes_status(fake_writer, ota_state, fast_sleep_ms):
    refreshed = []

    def fake_refresh():
        refreshed.append(True)
        return "VALID"

    deps = _make_deps(
        safe_base64_file_to_file=_fake_decode_to_file,
        refresh_license_status=fake_refresh,
    )
    open("update.pbp", "w").close()
    ota_state.update(total_chunks=1, received_chunks=1, target_file="license.lic")
    await uh.handle_finalize_upload(fake_writer, ota_state, deps)
    body = fake_writer.json()
    assert body["ok"] is True
    assert "VALID" in body["message"]
    assert refreshed == [True]


@pytest.mark.asyncio
async def test_handle_restart_pico_calls_machine_reset(fake_writer, fast_sleep_ms):
    import machine
    reset_calls = []
    original = machine.reset
    machine.reset = lambda: reset_calls.append(True)
    try:
        deps = _make_deps()
        await uh.handle_restart_pico(fake_writer, deps)
    finally:
        machine.reset = original
    assert "200" in fake_writer.status_line
    assert reset_calls == [True]


@pytest.mark.asyncio
async def test_handle_emergency_delete_main_removes_file_and_resets(fake_writer, fast_sleep_ms):
    import machine
    reset_calls = []
    original = machine.reset
    machine.reset = lambda: reset_calls.append(True)
    try:
        open("main.py", "w").close()
        deps = _make_deps()
        await uh.handle_emergency_delete_main(fake_writer, deps)
    finally:
        machine.reset = original
    assert not os.path.exists("main.py")
    body = fake_writer.json()
    assert body["deleted"] == ["main.py"]
    assert reset_calls == [True]


@pytest.mark.asyncio
async def test_handle_emergency_delete_boot_reports_missing_file_gracefully(fake_writer, fast_sleep_ms):
    import machine
    original = machine.reset
    machine.reset = lambda: None
    try:
        deps = _make_deps()
        await uh.handle_emergency_delete_boot(fake_writer, deps)
    finally:
        machine.reset = original
    body = fake_writer.json()
    assert body["ok"] is True
    assert body["deleted"] == []
