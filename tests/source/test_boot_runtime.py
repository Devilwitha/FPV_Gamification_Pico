import json

import boot_runtime as br


def test_get_device_role_none_when_missing():
    assert br.get_device_role() is None


def test_set_and_get_device_role_roundtrip():
    assert br.set_device_role("gamification") is True
    assert br.get_device_role() == "gamification"


def test_set_device_role_rejects_invalid_role():
    assert br.set_device_role("bogus") is False
    assert br.get_device_role() is None


def test_get_device_role_ignores_invalid_stored_value():
    with open(br.DEVICE_ROLE_FILE, "w") as f:
        f.write(json.dumps({"role": "not-a-real-role"}))
    assert br.get_device_role() is None


def test_get_device_role_survives_corrupt_json():
    with open(br.DEVICE_ROLE_FILE, "w") as f:
        f.write("{not json")
    assert br.get_device_role() is None


def test_clear_device_role_removes_file():
    br.set_device_role("gatehill")
    assert br.clear_device_role() is True
    assert br.get_device_role() is None


def test_clear_device_role_when_absent_returns_false():
    assert br.clear_device_role() is False


def test_register_and_feed_wdt():
    calls = []

    class FakeWdt:
        def feed(self):
            calls.append(1)

    br.register_wdt(FakeWdt())
    br.feed_wdt()
    br.feed_wdt()
    assert len(calls) == 2
    br.register_wdt(None)


def test_feed_wdt_without_registration_is_noop():
    br.register_wdt(None)
    br.feed_wdt()  # must not raise


def test_feed_wdt_swallows_exceptions():
    class BrokenWdt:
        def feed(self):
            raise RuntimeError("boom")

    br.register_wdt(BrokenWdt())
    br.feed_wdt()  # must not raise
    br.register_wdt(None)


def test_mark_main_attempt_failed_or_unhealthy_increments_counter():
    assert br.mark_main_attempt_failed_or_unhealthy() == 1
    assert br.mark_main_attempt_failed_or_unhealthy() == 2
    assert br.mark_main_attempt_failed_or_unhealthy() == 3


def test_clear_main_fail_count_resets_state():
    br.mark_main_attempt_failed_or_unhealthy()
    br.mark_main_attempt_failed_or_unhealthy()
    br.clear_main_fail_count()
    should_recover, count = br.should_boot_recovery()
    assert should_recover is False
    assert count == 0


def test_should_boot_recovery_false_below_threshold():
    br.mark_main_attempt_failed_or_unhealthy()
    should_recover, count = br.should_boot_recovery()
    assert should_recover is False
    assert count == 1


def test_should_boot_recovery_true_at_threshold():
    for _ in range(br.MAX_MAIN_FAILS):
        br.mark_main_attempt_failed_or_unhealthy()
    should_recover, count = br.should_boot_recovery()
    assert should_recover is True
    assert count == br.MAX_MAIN_FAILS


def test_should_boot_recovery_resets_after_fail_window_expires(monkeypatch):
    import time

    t = [1_000_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: t[0])

    for _ in range(br.MAX_MAIN_FAILS):
        br.mark_main_attempt_failed_or_unhealthy()

    # Weit ausserhalb von FAIL_WINDOW_MS springen -> Fehlerzaehler muss verfallen.
    t[0] += br.FAIL_WINDOW_MS + 5000
    should_recover, count = br.should_boot_recovery()
    assert should_recover is False
    assert count == 0


def test_mark_main_attempt_resets_counter_after_window_expires(monkeypatch):
    import time

    t = [1_000_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: t[0])

    br.mark_main_attempt_failed_or_unhealthy()
    br.mark_main_attempt_failed_or_unhealthy()

    t[0] += br.FAIL_WINDOW_MS + 5000
    # Ein neuer Fehlversuch nach Ablauf des Fensters faengt wieder bei 1 an.
    assert br.mark_main_attempt_failed_or_unhealthy() == 1


def test_request_and_consume_main_retry_once():
    assert br.consume_main_retry_once() is False
    assert br.request_main_retry_once() is True
    assert br.consume_main_retry_once() is True
    # Flag ist danach verbraucht.
    assert br.consume_main_retry_once() is False


def test_detect_board_type_pico_w(monkeypatch):
    import os
    import types

    monkeypatch.setattr(os, "uname", lambda: types.SimpleNamespace(machine="Raspberry Pi Pico W with RP2040"), raising=False)
    assert br.detect_board_type() == "Pico W"


def test_detect_board_type_pico_non_wireless(monkeypatch):
    import os
    import types

    monkeypatch.setattr(os, "uname", lambda: types.SimpleNamespace(machine="Raspberry Pi Pico with RP2040"), raising=False)
    assert br.detect_board_type() == "Pico"


def test_detect_board_type_pico2(monkeypatch):
    import os
    import types

    monkeypatch.setattr(os, "uname", lambda: types.SimpleNamespace(machine="Raspberry Pi Pico 2 W with RP2350"), raising=False)
    assert br.detect_board_type() == "Pico 2 W"


def test_detect_board_type_lilygo_esp32(monkeypatch):
    import os
    import types

    monkeypatch.setattr(os, "uname", lambda: types.SimpleNamespace(machine="LILYGO T-QT with ESP32"), raising=False)
    assert br.detect_board_type() == "LilyGo (ESP32)"


def test_detect_board_type_unknown_when_uname_unavailable(monkeypatch):
    import os

    def _raise():
        raise AttributeError("no uname")

    monkeypatch.setattr(os, "uname", _raise, raising=False)
    assert br.detect_board_type() == "Unbekannt"
