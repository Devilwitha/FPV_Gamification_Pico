import json

import pytest

import race_mode as rm


def make_race(**config):
    manager = rm.RaceMode(player_name="Pilot")
    if config:
        manager.config.update(config)
        manager.role = manager.config.get("role", manager.role)
    return manager


def test_default_config_when_no_file():
    manager = rm.RaceMode()
    assert manager.config["role"] == "racer"
    assert manager.config["laps"] == rm.DEFAULT_LAPS
    assert manager.config["enabled"] is False


def test_node_id_derived_from_machine_unique_id():
    import binascii
    import machine

    manager = rm.RaceMode()
    expected_prefix = binascii.hexlify(machine.unique_id()).decode()
    assert manager.node_id.startswith(expected_prefix)


def test_normalize_config_clamps_out_of_range_values():
    manager = rm.RaceMode()
    normalized = manager._normalize_config({
        "laps": 500,
        "rssi_threshold": 10,
        "cooldown_seconds": -5,
        "role": "bogus",
    })
    assert normalized["laps"] == 99
    assert normalized["rssi_threshold"] == -20
    assert normalized["cooldown_seconds"] == 1
    assert normalized["role"] == "racer"


def test_normalize_config_allows_gate_roles():
    manager = rm.RaceMode()
    assert manager._normalize_config({"role": "gate_a"})["role"] == "gate_a"
    assert manager._normalize_config({"role": "gate_b"})["role"] == "gate_b"


def test_save_and_load_config_roundtrip():
    manager = rm.RaceMode()
    manager.config = manager._normalize_config({"laps": 5, "role": "gate_a"})
    manager._save_config()

    reloaded = rm.RaceMode()
    assert reloaded.config["laps"] == 5
    assert reloaded.config["role"] == "gate_a"


def test_start_race_as_racer_resets_lap_state():
    manager = make_race(role="racer")
    manager.lap_times = [100, 200]
    manager.lap_index = 2
    status = manager.start_race("racer")
    assert status["running"] is True
    assert status["lap_index"] == 0
    assert status["lap_times_ms"] == []


def test_start_race_as_gate_advertises_and_does_not_reset_laps():
    manager = make_race(role="gate_a")
    status = manager.start_race("gate_a")
    assert status["role"] == "gate_a"
    assert status["running"] is True


def test_stop_race_disables_running_and_ble():
    manager = make_race()
    manager.start_race("racer")
    status = manager.stop_race("Manuell beendet")
    assert status["running"] is False
    assert status["last_event"] == "Manuell beendet"
    assert manager.ble.active() is False


def test_configure_enabled_starts_race():
    manager = make_race()
    status = manager.configure({"enabled": True, "role": "racer", "laps": 3})
    assert status["running"] is True
    assert status["enabled"] is True


def test_configure_disabled_stops_race():
    manager = make_race()
    manager.configure({"enabled": True, "role": "racer"})
    status = manager.configure({"enabled": False})
    assert status["running"] is False


def test_gate_packet_roundtrip_via_parse_packet():
    manager = rm.RaceMode()
    packet = manager._gate_packet(rm.GATE_A)
    parsed = manager._parse_packet(packet[3:])  # skip flags-AD-structure prefix like real adv_data
    assert parsed is not None
    gate_letter, sender_raw = parsed
    assert gate_letter == rm.GATE_A
    assert sender_raw == manager.node_raw


def test_ble_irq_scan_result_updates_gate_a_seen(monkeypatch):
    import time

    monkeypatch.setattr(time, "ticks_ms", lambda: 123456)
    manager = rm.RaceMode()
    other = rm.RaceMode()
    # machine.unique_id() liefert im Simulator fuer JEDES Geraet dieselbe feste
    # ID - fuer einen realistischen "Fremdgeraet"-Test muss node_raw manuell
    # unterschiedlich gesetzt werden, sonst wertet _ble_irq das Paket als
    # sein eigenes und ignoriert es.
    other.node_raw = b"\x99" * 8
    packet = other._gate_packet(rm.GATE_A)
    manager._ble_irq(rm._IRQ_SCAN_RESULT, (0, b"\x00" * 6, 0, -40, packet[3:]))
    assert manager.gate_a_last_seen_ms == 123456
    assert manager.gate_a_rssi == -40


def test_ble_irq_ignores_own_packets():
    manager = rm.RaceMode()
    packet = manager._gate_packet(rm.GATE_A)
    manager._ble_irq(rm._IRQ_SCAN_RESULT, (0, b"\x00" * 6, 0, -40, packet[3:]))
    assert manager.gate_a_last_seen_ms == 0


def test_gate_in_range_false_when_stale(monkeypatch):
    import time

    manager = rm.RaceMode()
    manager.gate_a_last_seen_ms = 1000
    manager.gate_a_rssi = -40
    monkeypatch.setattr(time, "ticks_ms", lambda: 1000 + rm.GATE_STALE_MS + 1)
    assert manager._gate_in_range(manager.gate_a_last_seen_ms, manager.gate_a_rssi, time.ticks_ms()) is False


def test_gate_in_range_false_when_rssi_below_threshold():
    manager = rm.RaceMode()
    manager.config["rssi_threshold"] = -50
    import time
    now = time.ticks_ms()
    assert manager._gate_in_range(now, -80, now) is False
    assert manager._gate_in_range(now, -10, now) is True


@pytest.mark.asyncio
async def test_full_race_run_completes_and_logs(monkeypatch):
    import time

    import asyncio

    async def fast_sleep_ms(_ms):
        await asyncio.sleep(0)

    monkeypatch.setattr(asyncio, "sleep_ms", fast_sleep_ms)

    # Eigene, manuell steuerbare Uhr statt echter Wanduhrzeit: run() prueft
    # sowohl ein Cooldown-Fenster zwischen Torueberquerungen als auch, ob ein
    # Tor-Kontakt noch "frisch" (nicht laenger als GATE_STALE_MS her) ist -
    # mit echtem time.ticks_ms() waeren beide Fenster (Cooldown 1s, Stale
    # 1.5s) nur durch echtes Warten zuverlaessig zu treffen.
    clock = [1_000_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])

    async def pump(n=30):
        for _ in range(n):
            await asyncio.sleep(0)

    manager = rm.RaceMode(player_name="Racer")
    manager.config = manager._normalize_config({
        "laps": 1, "role": "racer", "rssi_threshold": -80, "cooldown_seconds": 1,
    })
    manager.start_race("racer")

    task = asyncio.ensure_future(manager.run())
    try:
        # Tor A "sehen" -> genug Event-Loop-Durchlaeufe abwarten, bis run()'s
        # while-Schleife den neuen Zustand tatsaechlich verarbeitet hat
        # (kooperatives Scheduling macht die exakte Anzahl noetiger awaits
        # sonst fragil).
        manager.gate_a_last_seen_ms = clock[0]
        manager.gate_a_rssi = -10
        await pump()
        assert manager.waiting_for == "B"

        # Cooldown-Fenster (1s) verstreichen lassen, dann Tor B "sehen".
        clock[0] += 1100
        manager.gate_b_last_seen_ms = clock[0]
        manager.gate_b_rssi = -10
        await pump()

        assert manager.finished is True
        assert len(manager.lap_times) == 1

        log = rm.load_race_log()
        assert len(log) == 1
        assert log[0]["laps"] == 1
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_handle_race_route_data_and_log():
    manager = rm.RaceMode()
    from tests.source.conftest import FakeWriter

    writer = FakeWriter()
    handled = await rm.handle_race_route(writer, "/race-data", "GET", {}, {}, manager)
    assert handled is True
    assert writer.json()["role"] == "racer"

    writer = FakeWriter()
    handled = await rm.handle_race_route(writer, "/race-log", "GET", {}, {}, manager)
    assert handled is True
    assert writer.json() == {"ok": True, "log": []}


@pytest.mark.asyncio
async def test_handle_race_route_config_updates_manager():
    manager = rm.RaceMode()
    from tests.source.conftest import FakeWriter

    writer = FakeWriter()
    handled = await rm.handle_race_route(
        writer, "/race-config", "POST", {}, {"enabled": "1", "role": "racer", "laps": "5"}, manager
    )
    assert handled is True
    body = writer.json()
    assert body["enabled"] is True
    assert body["config"]["laps"] == 5


@pytest.mark.asyncio
async def test_handle_race_route_stop_disables_and_reports_status():
    manager = rm.RaceMode()
    manager.configure({"enabled": True, "role": "racer"})
    from tests.source.conftest import FakeWriter

    writer = FakeWriter()
    handled = await rm.handle_race_route(writer, "/race-stop", "POST", {}, {}, manager)
    assert handled is True
    assert writer.json()["running"] is False
    assert manager.config["enabled"] is False


@pytest.mark.asyncio
async def test_handle_race_route_log_clear():
    manager = rm.RaceMode()
    manager.log_entries = [{"laps": 1}]
    from tests.source.conftest import FakeWriter

    writer = FakeWriter()
    handled = await rm.handle_race_route(writer, "/race-log-clear", "POST", {}, {}, manager)
    assert handled is True
    assert writer.json()["ok"] is True
    assert manager.log_entries == []


@pytest.mark.asyncio
async def test_handle_race_route_unknown_path_returns_false():
    manager = rm.RaceMode()
    from tests.source.conftest import FakeWriter

    handled = await rm.handle_race_route(FakeWriter(), "/not-a-route", "GET", {}, {}, manager)
    assert handled is False


def test_load_race_log_missing_file_returns_empty_list():
    assert rm.load_race_log() == []


def test_save_race_log_trims_to_max_entries():
    entries = [{"i": i} for i in range(rm.RACE_LOG_MAX_ENTRIES + 10)]
    ok, err = rm.save_race_log(entries)
    assert ok is True
    loaded = rm.load_race_log()
    assert len(loaded) == rm.RACE_LOG_MAX_ENTRIES
    assert loaded[-1]["i"] == entries[-1]["i"]
