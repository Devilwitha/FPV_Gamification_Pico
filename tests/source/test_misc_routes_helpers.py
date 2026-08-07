import json

import pytest

import misc_routes_helpers as mrh
import boot_runtime as real_boot_runtime


class FakeDetector:
    def __init__(self, score=0):
        self.score = score
        self.trick_history = []
        self.last_trick_name = "Keiner"


def make_deps(**overrides):
    logs = []
    highscore_data = {"score": 0, "timestamp": "Unbekannt", "player": "Pilot"}
    pending_highscore = {"active": False, "score": 0, "timestamp": "Unbekannt"}
    detector = FakeDetector()
    trick_log = []
    emergency_calls = []

    async def fake_send_html_file(writer, path):
        writer.write(("HTTP/1.1 200 OK\r\n\r\nHTML:" + path).encode())

    async def fake_send_file_as_download(writer, path, download_name):
        with open(path) as f:
            content = f.read()
        writer.write(("HTTP/1.1 200 OK\r\n\r\nFILE(" + download_name + "):" + content).encode())

    async def fake_simulate_trick(kind):
        detector.score += 10
        detector.last_trick_name = "Sim:" + kind

    deps = {
        "send_html_file": fake_send_html_file,
        "admin_profiles_html_path": "admin_profiles.html",
        "admin_system_html_path": "admin_system.html",
        "ap_ssid": "TestSSID",
        "enable_hotspot": True,
        "detector": detector,
        "highscore_data": highscore_data,
        "pending_highscore": pending_highscore,
        "default_pilot_name": "Pilot",
        "firmware_version": "1.0.0",
        "ota_update_active": False,
        "ota_received_chunks": 0,
        "ota_total_chunks": 0,
        "list_profile_files": lambda: ["beginner", "freestyle", "aggressive"],
        "get_copil_payload": lambda: {"copter_name": "", "pilot_name": ""},
        "save_copil_names": lambda c, p: (True, ""),
        "save_custom_profile": lambda name, data: (True, ""),
        "get_profile_data": lambda name: {"a": 1} if name == "known" else None,
        "delete_custom_profile": lambda name: (True, "") if name == "known" else (False, "nicht gefunden"),
        "activate_trick_profile": lambda name: (True, "", name),
        "debug_log": logs.append,
        "debug_console_only": logs.append,
        "save_system_settings": lambda **kw: (True, ""),
        "get_language_code": lambda: "en",
        "set_language_code": lambda code: None,
        "is_allowed_language": lambda code: code in ("en", "de"),
        "list_language_codes": lambda: ["en", "de"],
        "enable_serial_debug": False,
        "write_text_file": lambda path, content: open(path, "w").write(content),
        "session_export_file_path": "session.txt",
        "build_session_txt_content": lambda: "session-content",
        "send_file_as_download": fake_send_file_as_download,
        "build_debug_export_file": lambda: open("debug.txt", "w").write("debug-content"),
        "debug_export_file_path": "debug.txt",
        "init_debug_log_file": lambda: open("fpv_debug_session.txt", "w").close(),
        "simulate_trick": fake_simulate_trick,
        "perform_emergency_delete_main": _make_emergency(emergency_calls, "main"),
        "perform_emergency_delete_boot": _make_emergency(emergency_calls, "boot"),
        "trick_highscore_log_entries": trick_log,
        "boot_runtime": real_boot_runtime,
        "license_thanks_pending": False,
        "confirm_license_thanks": None,
        "get_datetime_string": lambda: "01.01.2024 12:00:00",
        "html_escape": lambda s: s,
        "save_highscore": lambda: (True, ""),
        "record_trick_highscore_log_entry": lambda: trick_log.append("recorded"),
    }
    deps.update(overrides)
    deps["_logs"] = logs
    deps["_emergency_calls"] = emergency_calls
    return deps


def _make_emergency(calls, name):
    async def _handler(writer):
        calls.append(name)
        writer.write(b"HTTP/1.1 200 OK\r\n\r\n{}")
    return _handler


async def call_route(fake_writer, path, method, query, body_text, body_params, deps, trick_profile="aggressive", dev_mode=False, lang="en"):
    return await mrh.handle_misc_routes(
        fake_writer, path, method, query, body_text, body_params, trick_profile, dev_mode, lang, deps
    )


@pytest.mark.asyncio
async def test_admin_pages_are_served(fake_writer, install_stub_module, fresh_import):
    """/admin-profiles laeuft (wie /admin-system) seit der dynamischen
    Dashboard-Nav ueber pico_web_api.send_admin_html_with_slot() statt
    deps["send_html_file"] direkt - braucht daher einen frischen
    pico_web_api-Import gegen einen kontrollierten "main"-Stub, sonst wuerde
    ein evtl. schon von einem anderen Testmodul importiertes pico_web_api an
    dessen laengst verworfenem send_html_file haengen bleiben (siehe
    test_gmr.py's gmr-Fixture fuer dasselbe Muster)."""
    import sys

    async def fake_send_html_file(writer, path):
        writer.write(("HTTP/1.1 200 OK\r\n\r\nHTML:" + path).encode())

    def fake_safe_base64_file_to_file(input_file, output_file):
        return True

    install_stub_module(
        "main",
        send_html_file=fake_send_html_file,
        debug_log=lambda message: None,
        safe_base64_file_to_file=fake_safe_base64_file_to_file,
    )
    sys.modules.pop("pico_web_api", None)
    sys.modules.pop("plugin_manager", None)
    try:
        deps = make_deps()
        handled, *_ = await call_route(fake_writer, "/admin-profiles", "GET", {}, "", {}, deps)
        assert handled is True
        assert b"admin_profiles.html" in fake_writer.response
    finally:
        sys.modules.pop("pico_web_api", None)
        sys.modules.pop("plugin_manager", None)


@pytest.mark.asyncio
async def test_system_info_reports_expected_fields(fake_writer):
    deps = make_deps()
    deps["detector"].score = 42
    handled, *_ = await call_route(fake_writer, "/system-info", "GET", {}, "", {}, deps, trick_profile="freestyle")
    assert handled is True
    body = fake_writer.json()
    assert body["score"] == 42
    assert body["trick_tuning_profile"] == "freestyle"
    assert body["ssid"] == "TestSSID"
    assert body["board_type"] == "Unbekannt"  # kein echtes os.uname() unter Windows


@pytest.mark.asyncio
async def test_hotspot_config_get_defaults_without_file(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/hotspot-config", "GET", {}, "", {}, deps)
    assert handled is True
    body = fake_writer.json()
    assert body["ssid"] == "TestSSID"


@pytest.mark.asyncio
async def test_set_hotspot_config_rejects_short_password(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(
        fake_writer, "/set-hotspot-config", "POST", {}, "", {"ssid": "New", "password": "short"}, deps
    )
    assert handled is True
    assert "400" in fake_writer.status_line


@pytest.mark.asyncio
async def test_set_hotspot_config_success_persists_file(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(
        fake_writer, "/set-hotspot-config", "POST", {}, "", {"ssid": "New", "password": "longenough"}, deps
    )
    assert handled is True
    assert "200" in fake_writer.status_line
    with open("hotspot.conf") as f:
        assert json.loads(f.read()) == {"ssid": "New", "password": "longenough"}


@pytest.mark.asyncio
async def test_wlan_config_get_and_set_allows_open_network(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(
        fake_writer, "/set-wlan-config", "POST", {}, "", {"ssid": "HomeNet", "password": ""}, deps
    )
    assert "200" in fake_writer.status_line

    writer2 = type(fake_writer)()
    handled, *_ = await call_route(writer2, "/wlan-config", "GET", {}, "", {}, deps)
    assert writer2.json() == {"ok": True, "ssid": "HomeNet", "password": ""}


@pytest.mark.asyncio
async def test_trick_highscore_log_get_and_clear(fake_writer):
    deps = make_deps()
    deps["trick_highscore_log_entries"].append({"score": 5})

    handled, *_ = await call_route(fake_writer, "/trick-highscore-log", "GET", {}, "", {}, deps)
    assert fake_writer.json()["log"] == [{"score": 5}]

    from tests.source.conftest import FakeWriter
    writer2 = FakeWriter()
    handled, *_ = await call_route(writer2, "/trick-highscore-log-clear", "GET", {}, "", {}, deps)
    assert writer2.json()["ok"] is True
    assert deps["trick_highscore_log_entries"] == []


@pytest.mark.asyncio
async def test_reset_device_role_requires_confirmation(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/reset-device-role", "GET", {}, "", {}, deps)
    assert "400" in fake_writer.status_line


@pytest.mark.asyncio
async def test_reset_device_role_success(fake_writer, fast_sleep_ms):
    import machine

    reset_calls = []
    original = machine.reset
    machine.reset = lambda: reset_calls.append(True)
    try:
        real_boot_runtime.set_device_role("gamification")
        deps = make_deps()
        handled, *_ = await call_route(fake_writer, "/reset-device-role", "GET", {"confirm": "1"}, "", {}, deps)
        assert "200" in fake_writer.status_line
        assert real_boot_runtime.get_device_role() is None
        assert reset_calls == [True]
    finally:
        machine.reset = original


@pytest.mark.asyncio
async def test_language_packs_lists_available_codes(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/language-packs", "GET", {}, "", {}, deps, lang="de")
    body = fake_writer.json()
    assert body["current"] == "de"
    assert body["languages"] == ["en", "de"]


@pytest.mark.asyncio
async def test_i18n_data_merges_fallback_and_selected_language(fake_writer):
    with open("en.pak", "w") as f:
        json.dump({"hello": "Hello", "bye": "Bye"}, f)
    with open("de.pak", "w") as f:
        json.dump({"hello": "Hallo"}, f)

    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/i18n-data", "GET", {"lang": "de"}, "", {}, deps)
    body = fake_writer.json()
    assert body["lang"] == "de"
    assert body["strings"] == {"hello": "Hallo", "bye": "Bye"}


@pytest.mark.asyncio
async def test_i18n_data_falls_back_to_english_for_unknown_language(fake_writer):
    with open("en.pak", "w") as f:
        json.dump({"hello": "Hello"}, f)
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/i18n-data", "GET", {"lang": "xx"}, "", {}, deps)
    assert fake_writer.json()["lang"] == "en"


@pytest.mark.asyncio
async def test_profiles_list_marks_active_profile(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/profiles-list", "GET", {}, "", {}, deps, trick_profile="freestyle")
    profiles = fake_writer.json()["profiles"]
    active = [p for p in profiles if p["active"]]
    assert active == [{"name": "freestyle", "active": True}]


@pytest.mark.asyncio
async def test_copil_info_and_set_copil(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/copil-info", "GET", {}, "", {}, deps)
    assert fake_writer.json()["ok"] is True

    from tests.source.conftest import FakeWriter
    writer2 = FakeWriter()
    handled, *_ = await call_route(
        writer2, "/set-copil", "POST", {}, "", {"copter_name": "Drone1", "pilot_name": "Ace"}, deps
    )
    assert writer2.json()["ok"] is True


@pytest.mark.asyncio
async def test_create_profile_requires_name_and_data(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/create-profile", "POST", {}, "", {}, deps)
    assert "400" in fake_writer.status_line


@pytest.mark.asyncio
async def test_create_profile_success(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(
        fake_writer, "/create-profile", "POST", {}, "", {"name": "custom", "data": json.dumps({"a": 1})}, deps
    )
    assert "200" in fake_writer.status_line
    assert fake_writer.json()["ok"] is True


@pytest.mark.asyncio
async def test_create_profile_invalid_json_returns_500(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(
        fake_writer, "/create-profile", "POST", {}, "", {"name": "custom", "data": "not json"}, deps
    )
    assert "500" in fake_writer.status_line


@pytest.mark.asyncio
async def test_download_profile_not_found(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/download-profile", "GET", {"name": "unknown"}, "", {}, deps)
    assert "404" in fake_writer.status_line


@pytest.mark.asyncio
async def test_download_profile_found(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/download-profile", "GET", {"name": "known"}, "", {}, deps)
    assert "200" in fake_writer.status_line
    assert b"Content-Disposition" in fake_writer.response


@pytest.mark.asyncio
async def test_delete_profile_success_and_failure(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/delete-profile", "GET", {"name": "known"}, "", {}, deps)
    assert fake_writer.json()["ok"] is True

    from tests.source.conftest import FakeWriter
    writer2 = FakeWriter()
    handled, *_ = await call_route(writer2, "/delete-profile", "GET", {"name": "missing"}, "", {}, deps)
    assert writer2.json()["ok"] is False


@pytest.mark.asyncio
async def test_apply_profile_updates_returned_trick_tuning_profile(fake_writer):
    deps = make_deps()
    handled, new_profile, *_ = await call_route(fake_writer, "/apply-profile", "GET", {"name": "beginner"}, "", {}, deps)
    assert handled is True
    assert new_profile == "beginner"
    assert fake_writer.json()["profile"] == "beginner"


@pytest.mark.asyncio
async def test_data_route_includes_infection_status_when_present(fake_writer):
    deps = make_deps(infection_status=lambda: {"enabled": True, "running": True})
    handled, *_ = await call_route(fake_writer, "/data", "GET", {}, "", {}, deps)
    body = fake_writer.json()
    assert body["infection"] == {"enabled": True, "running": True}


@pytest.mark.asyncio
async def test_data_route_without_infection_status_key():
    from tests.source.conftest import FakeWriter

    deps = make_deps()
    writer = FakeWriter()
    handled, *_ = await mrh.handle_misc_routes(writer, "/data", "GET", {}, "", {}, "aggressive", False, "en", deps)
    assert handled is True
    assert "infection" not in writer.json()


@pytest.mark.asyncio
async def test_set_highscore_name_rejects_empty_name(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/set-highscore-name", "GET", {"name": ""}, "", {}, deps)
    body = fake_writer.json()
    assert body["ok"] is False
    assert "leer" in body["error"]


@pytest.mark.asyncio
async def test_set_highscore_name_saves_pending_highscore(fake_writer):
    deps = make_deps()
    deps["pending_highscore"].update(active=True, score=999, timestamp="ts")
    handled, *_ = await call_route(fake_writer, "/set-highscore-name", "GET", {"name": "Ace"}, "", {}, deps)
    body = fake_writer.json()
    assert body["ok"] is True
    assert body["highscore"] == 999
    assert deps["highscore_data"]["player"] == "Ace"
    assert deps["pending_highscore"]["active"] is False
    assert deps["trick_highscore_log_entries"] == ["recorded"]


@pytest.mark.asyncio
async def test_set_highscore_name_falls_back_to_detector_score(fake_writer):
    deps = make_deps()
    deps["detector"].score = 500
    handled, *_ = await call_route(fake_writer, "/set-highscore-name", "GET", {"name": "Ace"}, "", {}, deps)
    body = fake_writer.json()
    assert body["ok"] is True
    assert body["highscore"] == 500


@pytest.mark.asyncio
async def test_set_highscore_name_no_new_score_reports_error(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/set-highscore-name", "GET", {"name": "Ace"}, "", {}, deps)
    body = fake_writer.json()
    assert body["ok"] is False
    assert "Kein neuer Highscore" in body["error"]


@pytest.mark.asyncio
async def test_set_highscore_name_web_submit_returns_html(fake_writer):
    deps = make_deps()
    deps["detector"].score = 100
    handled, *_ = await call_route(fake_writer, "/set-highscore-name", "GET", {"name": "Ace", "web": "1"}, "", {}, deps)
    assert b"text/html" in fake_writer.response
    assert b"Highscore gespeichert" in fake_writer.response


@pytest.mark.asyncio
async def test_set_highscore_name_save_failure_reports_error(fake_writer):
    deps = make_deps(save_highscore=lambda: (False, "disk full"))
    deps["detector"].score = 100
    handled, *_ = await call_route(fake_writer, "/set-highscore-name", "GET", {"name": "Ace"}, "", {}, deps)
    body = fake_writer.json()
    assert body["ok"] is False
    assert "disk full" in body["error"]


@pytest.mark.asyncio
async def test_confirm_highscore_accepts_pending():
    from tests.source.conftest import FakeWriter

    deps = make_deps()
    deps["pending_highscore"].update(active=True, score=250, timestamp="ts")
    writer = FakeWriter()
    handled, *_ = await call_route(writer, "/confirm-highscore", "GET", {}, "", {}, deps)
    body = writer.json()
    assert body["ok"] is True
    assert body["highscore"] == 250


@pytest.mark.asyncio
async def test_reset_highscore_clears_state_and_detector(fake_writer):
    deps = make_deps()
    deps["highscore_data"].update(score=100, player="Old")
    deps["detector"].score = 55
    deps["detector"].trick_history = ["a"]
    handled, *_ = await call_route(fake_writer, "/reset-highscore", "GET", {}, "", {}, deps)
    body = fake_writer.json()
    assert body["highscore"] == 0
    assert deps["detector"].score == 0
    assert deps["detector"].trick_history == []


@pytest.mark.asyncio
async def test_set_trick_profile_updates_state(fake_writer):
    deps = make_deps()
    handled, new_profile, *_ = await call_route(fake_writer, "/set-trick-profile", "GET", {"profile": "beginner"}, "", {}, deps)
    assert new_profile == "beginner"
    assert fake_writer.json()["trick_tuning_profile"] == "beginner"


@pytest.mark.asyncio
async def test_set_developer_mode_toggles_flag(fake_writer):
    deps = make_deps()
    handled, _profile, dev_mode, _lang = await call_route(fake_writer, "/set-developer-mode", "GET", {"enabled": "1"}, "", {}, deps)
    assert dev_mode is True
    assert fake_writer.json()["developer_mode"] is True


@pytest.mark.asyncio
async def test_emergency_delete_main_requires_confirmation(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/emergency-delete-main", "GET", {}, "", {}, deps)
    assert "400" in fake_writer.status_line
    assert deps["_emergency_calls"] == []


@pytest.mark.asyncio
async def test_emergency_delete_main_confirmed_calls_handler(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/emergency-delete-main", "GET", {"confirm": "1"}, "", {}, deps)
    assert handled is True
    assert deps["_emergency_calls"] == ["main"]


@pytest.mark.asyncio
async def test_emergency_delete_boot_confirmed_calls_handler(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/emergency-delete-boot", "GET", {"confirm": "1"}, "", {}, deps)
    assert handled is True
    assert deps["_emergency_calls"] == ["boot"]


@pytest.mark.asyncio
async def test_set_language_rejects_unknown_language(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/set-language", "GET", {"lang": "xx"}, "", {}, deps)
    assert "400" in fake_writer.status_line


@pytest.mark.asyncio
async def test_set_language_success(fake_writer):
    deps = make_deps()
    handled, _profile, _dev, new_lang = await call_route(fake_writer, "/set-language", "GET", {"lang": "de"}, "", {}, deps)
    assert new_lang == "de"
    assert fake_writer.json()["language"] == "de"


@pytest.mark.asyncio
async def test_confirm_license_thanks_calls_callback():
    from tests.source.conftest import FakeWriter

    called = []
    deps = make_deps(confirm_license_thanks=lambda: called.append(True))
    writer = FakeWriter()
    handled, *_ = await call_route(writer, "/confirm-license-thanks", "GET", {}, "", {}, deps)
    assert handled is True
    assert called == [True]


@pytest.mark.asyncio
async def test_clear_debug_log_requires_confirmation(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/clear-debug-log", "POST", {}, "", {}, deps)
    assert "400" in fake_writer.status_line


@pytest.mark.asyncio
async def test_clear_debug_log_success(fake_writer):
    with open("fpv_debug_session.txt", "w") as f:
        f.write("x")
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/clear-debug-log", "POST", {"confirm": "1"}, "", {}, deps)
    assert fake_writer.json()["ok"] is True


@pytest.mark.asyncio
async def test_clear_session_log_success(fake_writer):
    with open("session.txt", "w") as f:
        f.write("x")
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/clear-session-log", "POST", {"confirm": "1"}, "", {}, deps)
    assert fake_writer.json()["ok"] is True


@pytest.mark.asyncio
async def test_download_session_writes_and_serves_file(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/download-session", "GET", {}, "", {}, deps)
    assert handled is True
    assert b"session-content" in fake_writer.response


@pytest.mark.asyncio
async def test_download_debug_serves_file(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/download-debug", "GET", {}, "", {}, deps)
    assert handled is True
    assert b"debug-content" in fake_writer.response


@pytest.mark.asyncio
async def test_simulate_trick_increases_score(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/simulate-trick", "GET", {"type": "flip"}, "", {}, deps)
    body = fake_writer.json()
    assert body["points"] == 10
    assert body["trick"] == "Sim:flip"


@pytest.mark.asyncio
async def test_simulate_trick_defaults_to_roll_for_invalid_type(fake_writer):
    deps = make_deps()
    handled, *_ = await call_route(fake_writer, "/simulate-trick", "GET", {"type": "bogus"}, "", {}, deps)
    assert fake_writer.json()["type"] == "roll"


@pytest.mark.asyncio
async def test_unknown_route_returns_false(fake_writer):
    deps = make_deps()
    handled, profile, dev_mode, lang = await call_route(fake_writer, "/does-not-exist", "GET", {}, "", {}, deps)
    assert handled is False
    assert profile == "aggressive"
    assert dev_mode is False
    assert lang == "en"
