import pytest

import shooter_mode as sm


def test_derive_node_id_xors_all_bytes():
    assert sm._derive_node_id(b"\x01\x02\x03") == 0x01 ^ 0x02 ^ 0x03


def test_default_config_when_no_file():
    manager = sm.ShooterMode()
    assert manager.config["lives"] == sm.DEFAULT_LIVES
    assert manager.config["damage"] == sm.DEFAULT_DAMAGE
    assert manager.config["enabled"] is False


def test_normalize_config_clamps_values():
    manager = sm.ShooterMode()
    normalized = manager._normalize_config({
        "lives": 500,
        "damage": 50,
        "hit_cooldown_ms": 1,
        "fire_cooldown_ms": 999999,
    })
    assert normalized["lives"] == 99
    assert normalized["damage"] == 9
    assert normalized["hit_cooldown_ms"] == 50
    assert normalized["fire_cooldown_ms"] == 5000


def test_save_and_load_config_roundtrip():
    manager = sm.ShooterMode()
    manager.config = manager._normalize_config({"lives": 3, "damage": 2})
    manager._save_config()

    reloaded = sm.ShooterMode()
    assert reloaded.config["lives"] == 3
    assert reloaded.config["damage"] == 2


def test_start_round_resets_counters():
    manager = sm.ShooterMode()
    manager.hits_taken = 7
    manager.shots_fired = 3
    manager.eliminated = True
    status = manager.start_round()
    assert status["running"] is True
    assert status["hits_taken"] == 0
    assert status["shots_fired"] == 0
    assert status["eliminated"] is False
    assert status["lives_remaining"] == manager.config["lives"]


def test_stop_round_records_log_entry_when_activity():
    manager = sm.ShooterMode()
    manager.start_round()
    manager.hits_taken = 4
    manager.shots_fired = 2
    manager.stop_round("Runde beendet")
    log = sm.load_shooter_log()
    assert len(log) == 1
    assert log[0]["hits_taken"] == 4
    assert log[0]["shots_fired"] == 2


def test_stop_round_does_not_log_when_no_activity():
    manager = sm.ShooterMode()
    manager.start_round()
    manager.stop_round("Runde beendet")
    assert sm.load_shooter_log() == []


def test_configure_enabled_starts_round():
    manager = sm.ShooterMode()
    status = manager.configure({"enabled": True, "lives": 10})
    assert status["running"] is True
    assert status["config"]["lives"] == 10


def test_configure_disabled_stops_round():
    manager = sm.ShooterMode()
    manager.configure({"enabled": True})
    status = manager.configure({"enabled": False})
    assert status["running"] is False


def test_fire_fails_when_round_not_running():
    manager = sm.ShooterMode()
    result = manager.fire()
    assert result["ok"] is False


def test_fire_fails_when_eliminated():
    manager = sm.ShooterMode()
    manager.start_round()
    manager.eliminated = True
    result = manager.fire()
    assert result["ok"] is False


def test_fire_increments_shots_fired(monkeypatch):
    manager = sm.ShooterMode()
    manager.start_round()
    result = manager.fire()
    assert result["ok"] is True
    assert manager.shots_fired == 1


def test_fire_respects_cooldown(monkeypatch):
    import time

    clock = [1_000_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])
    manager = sm.ShooterMode()
    manager.start_round()
    assert manager.fire()["ok"] is True
    clock[0] += 10  # well within default fire cooldown
    result = manager.fire()
    assert result["ok"] is False
    assert manager.shots_fired == 1


def test_fire_returns_error_when_emitter_unavailable():
    manager = sm.ShooterMode()
    manager.start_round()
    manager.emitter.available = False
    result = manager.fire()
    assert result["ok"] is False


def test_apply_hit_ignores_own_node_id():
    manager = sm.ShooterMode()
    manager.start_round()
    manager._apply_hit(manager.node_id, 1, 1000)
    assert manager.hits_taken == 0


def test_apply_hit_increments_and_tracks_source():
    manager = sm.ShooterMode()
    manager.start_round()
    manager._apply_hit(42, 1, 1000)
    assert manager.hits_taken == 1
    assert manager.last_hit_from == 42
    assert manager.hit_sources[42]["hits"] == 1


def test_apply_hit_respects_per_shooter_cooldown():
    manager = sm.ShooterMode()
    manager.start_round()
    manager.config["hit_cooldown_ms"] = 300
    manager._apply_hit(42, 1, 1000)
    manager._apply_hit(42, 1, 1100)  # too soon, must be ignored
    assert manager.hits_taken == 1
    manager._apply_hit(42, 1, 1400)  # cooldown elapsed
    assert manager.hits_taken == 2


def test_apply_hit_different_shooters_not_throttled_by_each_other():
    manager = sm.ShooterMode()
    manager.start_round()
    manager._apply_hit(1, 1, 1000)
    manager._apply_hit(2, 1, 1010)
    assert manager.hits_taken == 2


def test_apply_hit_eliminates_when_lives_reach_zero():
    manager = sm.ShooterMode()
    manager.configure({"enabled": True, "lives": 2, "damage": 1})
    manager._apply_hit(1, manager.config["damage"], 1000)
    assert manager.eliminated is False
    manager._apply_hit(1, manager.config["damage"], 2000)
    assert manager.eliminated is True
    assert manager.lives_remaining == 0


def test_apply_hit_ignored_once_eliminated():
    manager = sm.ShooterMode()
    manager.configure({"enabled": True, "lives": 1, "damage": 1})
    manager._apply_hit(1, manager.config["damage"], 1000)  # bringt auf 0 Leben -> ausgeschieden
    assert manager.eliminated is True
    assert manager.hits_taken == 1

    manager._apply_hit(2, manager.config["damage"], 5000)  # anderer Schuetze, weit ausserhalb Cooldown
    assert manager.hits_taken == 1  # darf NICHT weiter mitgezaehlt werden
    assert manager.last_hit_from == 1  # unveraendert vom letzten gueltigen Treffer


def test_fire_still_blocked_once_eliminated():
    manager = sm.ShooterMode()
    manager.configure({"enabled": True, "lives": 1, "damage": 1})
    manager._apply_hit(1, manager.config["damage"], 1000)
    assert manager.eliminated is True
    result = manager.fire()
    assert result["ok"] is False
    assert manager.shots_fired == 0


def test_apply_hit_unlimited_lives_never_eliminates():
    manager = sm.ShooterMode()
    manager.configure({"enabled": True, "lives": 0})
    for i in range(10):
        manager._apply_hit(1, 9, 1000 + i * 1000)
    assert manager.eliminated is False


def test_status_contains_expected_shape():
    manager = sm.ShooterMode()
    status = manager.status()
    assert status["ok"] is True
    assert "hardware" in status
    assert "emitter_available" in status["hardware"]
    assert "receiver_available" in status["hardware"]
    assert isinstance(status["hit_sources"], list)


@pytest.mark.asyncio
async def test_run_applies_hits_from_receiver_poll(monkeypatch):
    manager = sm.ShooterMode()
    manager.start_round()

    async def fake_sleep_ms(_ms):
        raise StopAsyncIteration

    monkeypatch.setattr(sm.asyncio, "sleep_ms", fake_sleep_ms)
    monkeypatch.setattr(manager.receiver, "poll", lambda: [{"address": 7, "command": 2, "ts_us": 0}])

    with pytest.raises(StopAsyncIteration):
        await manager.run()

    assert manager.hits_taken == 1
    assert manager.last_hit_from == 7


def test_crsf_raw_to_us_matches_known_reference_points():
    assert sm._crsf_raw_to_us(992) == 1500  # Mitte
    assert sm._crsf_raw_to_us(172) == 988   # unterer Anschlag
    assert sm._crsf_raw_to_us(1811) == 2011  # oberer Anschlag


def test_read_aux_state_disabled_when_channel_is_zero():
    manager = sm.ShooterMode()
    manager.config["aux_channel"] = 0
    sm.rc_channels_state["channels"] = [1811] * 16
    sm.rc_channels_state["updated_ms"] = sm.time.ticks_ms()
    assert manager._read_aux_state() == (False, None)


def test_read_aux_state_unavailable_when_no_fresh_data():
    manager = sm.ShooterMode()
    manager.config["aux_channel"] = 5
    sm.rc_channels_state["channels"] = [1811] * 16
    sm.rc_channels_state["updated_ms"] = 0
    assert manager._read_aux_state() == (False, None)


def test_read_aux_state_unavailable_when_stale(monkeypatch):
    import time

    clock = [1_000_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])
    manager = sm.ShooterMode()
    manager.config["aux_channel"] = 5
    sm.rc_channels_state["channels"] = [1811] * 16
    sm.rc_channels_state["updated_ms"] = clock[0]
    clock[0] += sm.AUX_STALE_MS + 100
    assert manager._read_aux_state() == (False, None)


def test_read_aux_state_returns_converted_value(monkeypatch):
    import time

    monkeypatch.setattr(time, "ticks_ms", lambda: 5000)
    manager = sm.ShooterMode()
    manager.config["aux_channel"] = 5
    channels = [992] * 16
    channels[4] = 1811  # Kanal 5 (1-indiziert) = oberer Anschlag
    sm.rc_channels_state["channels"] = channels
    sm.rc_channels_state["updated_ms"] = 5000
    available, value_us = manager._read_aux_state()
    assert available is True
    assert value_us == 2011


@pytest.mark.asyncio
async def test_run_fires_automatically_when_aux_channel_above_threshold(monkeypatch):
    import time

    monkeypatch.setattr(time, "ticks_ms", lambda: 10_000)
    manager = sm.ShooterMode()
    manager.configure({"enabled": True, "aux_channel": 3, "aux_threshold_us": 1700})
    monkeypatch.setattr(manager.receiver, "poll", lambda: [])

    channels = [992] * 16
    channels[2] = 1811  # Kanal 3 ueber der Schwelle
    sm.rc_channels_state["channels"] = channels
    sm.rc_channels_state["updated_ms"] = 10_000

    async def fake_sleep_ms(_ms):
        raise StopAsyncIteration

    monkeypatch.setattr(sm.asyncio, "sleep_ms", fake_sleep_ms)

    with pytest.raises(StopAsyncIteration):
        await manager.run()

    assert manager.shots_fired == 1
    assert manager.aux_available is True
    assert manager.aux_value_us == 2011


@pytest.mark.asyncio
async def test_run_does_not_fire_when_aux_channel_below_threshold(monkeypatch):
    import time

    monkeypatch.setattr(time, "ticks_ms", lambda: 10_000)
    manager = sm.ShooterMode()
    manager.configure({"enabled": True, "aux_channel": 3, "aux_threshold_us": 1700})
    monkeypatch.setattr(manager.receiver, "poll", lambda: [])

    channels = [992] * 16  # Mitte, unter der Schwelle
    sm.rc_channels_state["channels"] = channels
    sm.rc_channels_state["updated_ms"] = 10_000

    async def fake_sleep_ms(_ms):
        raise StopAsyncIteration

    monkeypatch.setattr(sm.asyncio, "sleep_ms", fake_sleep_ms)

    with pytest.raises(StopAsyncIteration):
        await manager.run()

    assert manager.shots_fired == 0
