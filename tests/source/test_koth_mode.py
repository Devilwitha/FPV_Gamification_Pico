import pytest

import koth_mode as km


def test_default_config_when_no_file():
    manager = km.KothMode()
    assert manager.config["role"] == "player"
    assert manager.config["round_seconds"] == km.DEFAULT_ROUND_SECONDS


def test_normalize_config_clamps_values():
    manager = km.KothMode()
    normalized = manager._normalize_config({
        "round_seconds": 5,
        "rssi_threshold": 10,
        "points_per_second": 1000,
        "role": "bogus",
    })
    assert normalized["round_seconds"] == 30
    assert normalized["rssi_threshold"] == -20
    assert normalized["points_per_second"] == 100.0
    assert normalized["role"] == "player"


def test_normalize_config_allows_hill_role():
    manager = km.KothMode()
    assert manager._normalize_config({"role": "hill"})["role"] == "hill"


def test_save_and_load_config_roundtrip():
    manager = km.KothMode()
    manager.config = manager._normalize_config({"round_seconds": 60, "role": "hill"})
    manager._save_config()

    reloaded = km.KothMode()
    assert reloaded.config["round_seconds"] == 60
    assert reloaded.config["role"] == "hill"


def test_start_round_resets_score_and_sets_end_time(monkeypatch):
    import time

    monkeypatch.setattr(time, "ticks_ms", lambda: 1_000_000)
    manager = km.KothMode()
    manager.config["round_seconds"] = 100
    manager.score = 55
    status = manager.start_round("player")
    assert status["running"] is True
    assert status["score"] == 0
    assert manager.round_end_ms == 1_000_000 + 100_000


def test_remaining_seconds_zero_when_not_running():
    manager = km.KothMode()
    assert manager.remaining_seconds() == 0


def test_remaining_seconds_counts_down(monkeypatch):
    import time

    clock = [1_000_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])
    manager = km.KothMode()
    manager.config["round_seconds"] = 100
    manager.start_round("player")
    clock[0] += 40_000
    assert manager.remaining_seconds() == 60


def test_stop_round_records_log_entry_when_player_scored():
    manager = km.KothMode()
    manager.start_round("player")
    manager.score = 42.0
    manager.stop_round("Runde beendet")
    log = km.load_koth_log()
    assert len(log) == 1
    assert log[0]["score"] == 42


def test_stop_round_does_not_log_zero_score():
    manager = km.KothMode()
    manager.start_round("player")
    manager.stop_round("Runde beendet")
    assert km.load_koth_log() == []


def test_stop_round_hill_role_never_logs():
    manager = km.KothMode()
    manager.start_round("hill")
    manager.score = 100  # hill role score is meaningless, must not be logged
    manager.stop_round("Runde beendet")
    assert km.load_koth_log() == []


def test_configure_enabled_starts_round():
    manager = km.KothMode()
    status = manager.configure({"enabled": True, "role": "player"})
    assert status["running"] is True


def test_configure_disabled_stops_round():
    manager = km.KothMode()
    manager.configure({"enabled": True, "role": "player"})
    status = manager.configure({"enabled": False})
    assert status["running"] is False


def test_score_and_name_packet_roundtrip():
    manager = km.KothMode(player_name="Ace")
    manager.score = 1234
    packet = manager._score_packet()
    parsed = manager._parse_packet(packet[3:])
    assert parsed is not None
    state, sender_raw, score_value, name_raw = parsed
    assert state == km.STATE_SCORE
    assert sender_raw == manager.node_raw
    assert score_value == 1234
    assert km._ascii_text(name_raw) == "Ace"


def test_anchor_packet_roundtrip():
    manager = km.KothMode()
    packet = manager._anchor_packet()
    parsed = manager._parse_packet(packet[3:])
    state, sender_raw, score_value, name_raw = parsed
    assert state == km.STATE_ANCHOR
    assert sender_raw == manager.node_raw
    assert score_value is None


def test_ble_irq_updates_leaderboard_for_foreign_score(monkeypatch):
    import time

    monkeypatch.setattr(time, "ticks_ms", lambda: 555)
    manager = km.KothMode()
    other = km.KothMode(player_name="Rival")
    other.node_raw = b"\x42" * 8
    other.score = 77
    packet = other._score_packet()

    manager._ble_irq(km._IRQ_SCAN_RESULT, (0, b"\x00" * 6, 0, -30, packet[3:]))
    board = manager._leaderboard_list()
    rival_entries = [entry for entry in board if entry["name"] == "Rival"]
    assert len(rival_entries) == 1
    assert rival_entries[0]["score"] == 77


def test_leaderboard_prunes_stale_entries(monkeypatch):
    import time

    clock = [1000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])
    manager = km.KothMode()
    manager.leaderboard["deadbeef"] = {"name": "Ghost", "score": 5, "last_seen_ms": 1000}
    clock[0] = 1000 + km.LEADERBOARD_STALE_MS + 1
    board = manager._leaderboard_list()
    assert all(entry["id"] != "deadbeef" for entry in board)


@pytest.mark.asyncio
async def test_full_round_player_scores_over_time(monkeypatch):
    import asyncio
    import time

    async def fast_sleep_ms(_ms):
        await asyncio.sleep(0)

    monkeypatch.setattr(asyncio, "sleep_ms", fast_sleep_ms)

    clock = [10_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])

    manager = km.KothMode()
    manager.config = manager._normalize_config({
        "round_seconds": 3600, "role": "player", "rssi_threshold": -80, "points_per_second": 10.0,
    })
    manager.start_round("player")
    manager.hill_last_seen_ms = clock[0]
    manager.last_rssi = -10

    task = asyncio.ensure_future(manager.run())
    try:
        for _ in range(3):
            clock[0] += 1000
            for _ in range(10):
                await asyncio.sleep(0)
            manager.hill_last_seen_ms = clock[0]  # Anker bleibt "frisch"

        assert manager.score > 0
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_run_ends_round_when_time_expires(monkeypatch):
    import asyncio
    import time

    async def fast_sleep_ms(_ms):
        await asyncio.sleep(0)

    monkeypatch.setattr(asyncio, "sleep_ms", fast_sleep_ms)
    clock = [0]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])

    manager = km.KothMode()
    manager.config = manager._normalize_config({"round_seconds": 30, "role": "player"})
    manager.start_round("player")

    task = asyncio.ensure_future(manager.run())
    try:
        clock[0] += 31_000
        for _ in range(10):
            await asyncio.sleep(0)
        assert manager.running is False
        assert manager.config["enabled"] is False
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_handle_koth_route_data_and_config():
    from tests.source.conftest import FakeWriter

    manager = km.KothMode()
    writer = FakeWriter()
    assert await km.handle_koth_route(writer, "/koth-data", "GET", {}, {}, manager) is True
    assert writer.json()["role"] == "player"

    writer = FakeWriter()
    handled = await km.handle_koth_route(
        writer, "/koth-config", "POST", {}, {"enabled": "1", "role": "player", "round_seconds": "60"}, manager
    )
    assert handled is True
    body = writer.json()
    assert body["enabled"] is True
    assert body["config"]["round_seconds"] == 60


@pytest.mark.asyncio
async def test_handle_koth_route_stop_and_log_clear():
    from tests.source.conftest import FakeWriter

    manager = km.KothMode()
    manager.configure({"enabled": True, "role": "player"})

    writer = FakeWriter()
    assert await km.handle_koth_route(writer, "/koth-stop", "POST", {}, {}, manager) is True
    assert writer.json()["running"] is False

    manager.log_entries = [{"score": 1}]
    writer = FakeWriter()
    assert await km.handle_koth_route(writer, "/koth-log-clear", "POST", {}, {}, manager) is True
    assert manager.log_entries == []


@pytest.mark.asyncio
async def test_handle_koth_route_unknown_returns_false():
    from tests.source.conftest import FakeWriter

    manager = km.KothMode()
    assert await km.handle_koth_route(FakeWriter(), "/nope", "GET", {}, {}, manager) is False


def test_load_koth_log_missing_file_returns_empty_list():
    assert km.load_koth_log() == []


def test_save_koth_log_trims_to_max_entries():
    entries = [{"i": i} for i in range(km.KOTH_LOG_MAX_ENTRIES + 5)]
    ok, _err = km.save_koth_log(entries)
    assert ok is True
    assert len(km.load_koth_log()) == km.KOTH_LOG_MAX_ENTRIES
