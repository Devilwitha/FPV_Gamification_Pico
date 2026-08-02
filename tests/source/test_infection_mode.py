import json

import pytest

import infection_mode as im


def make_manager(node_raw=None, **kwargs):
    manager = im.InfectionMode("SSID", "password123", **kwargs)
    if node_raw is not None:
        manager.node_raw = node_raw
        manager.node_id = im._hex_id(node_raw)
    return manager


def register_mutually(a, b):
    a.players = a._normalize_players([{"id": b.node_id, "name": "B"}])
    b.players = b._normalize_players([{"id": a.node_id, "name": "A"}])


def test_default_config():
    manager = make_manager()
    assert manager.config["initial_role"] == "seeker"
    assert manager.config["game_mode"] == "bomb"
    assert manager.config["enabled"] is False


def test_normalize_config_clamps_and_validates():
    manager = make_manager()
    normalized = manager._normalize_config({
        "initial_role": "host",
        "game_mode": "infect",
        "round_seconds": 1,
        "rssi_threshold": 10,
        "cooldown_seconds": 1000,
    })
    assert normalized["initial_role"] == "host"
    assert normalized["game_mode"] == "infect"
    assert normalized["round_seconds"] == 30
    assert normalized["rssi_threshold"] == -20
    assert normalized["cooldown_seconds"] == 120


def test_enabled_flag_is_reset_on_boot_to_avoid_stuck_state():
    # Ein Pico, der mitten in einer laufenden Runde stromlos wurde, darf
    # beim naechsten Start nicht automatisch wieder "enabled" starten.
    with open(im.CONFIG_FILE, "w") as f:
        json.dump({"enabled": True, "initial_role": "host"}, f)
    manager = make_manager()
    assert manager.config["enabled"] is False
    with open(im.CONFIG_FILE) as f:
        assert json.loads(f.read())["enabled"] is False


def test_legacy_config_file_is_migrated_and_removed(tmp_path):
    import os

    with open(im.LEGACY_CONFIG_FILE, "w") as f:
        json.dump({"initial_role": "host", "round_seconds": 90}, f)
    manager = make_manager()
    assert manager.config["initial_role"] == "host"
    assert manager.config["round_seconds"] == 90
    assert not os.path.exists(im.LEGACY_CONFIG_FILE)
    assert os.path.exists(im.CONFIG_FILE)


def test_normalize_players_dedupes_excludes_self_and_caps_length():
    manager = make_manager()
    raw_ids = ["aa11bb22cc33dd44"] * 3 + [im._hex_id(manager.node_raw)]
    values = [{"id": rid, "name": "x"} for rid in raw_ids]
    normalized = manager._normalize_players(values)
    assert normalized == [{"id": "aa11bb22cc33dd44", "name": "x"}]


def test_normalize_players_rejects_invalid_id_format():
    manager = make_manager()
    normalized = manager._normalize_players([{"id": "not-a-valid-id", "name": "x"}])
    assert normalized == []


def test_save_players_persists_and_reloads():
    manager = make_manager()
    manager.save_players([{"id": "aa11bb22cc33dd44", "name": "Bob"}])
    reloaded = make_manager()
    assert reloaded.players == [{"id": "aa11bb22cc33dd44", "name": "Bob"}]


def test_add_player_prepends_new_entry():
    manager = make_manager()
    manager.save_players([{"id": "aa11bb22cc33dd44", "name": "Bob"}])
    manager.add_player("ffeeddcc99887766", "Alice")
    assert manager.players[0]["id"] == "ffeeddcc99887766"
    assert len(manager.players) == 2


def test_start_discovery_activates_and_sets_deadline(monkeypatch):
    import time

    monkeypatch.setattr(time, "ticks_ms", lambda: 5000)
    manager = make_manager()
    manager.start_discovery()
    assert manager.discovery_active is True
    assert manager.discovery_until_ms == 5000 + im.DISCOVERY_DURATION_MS


def test_discovered_list_excludes_registered_players_and_stale_entries(monkeypatch):
    import time

    clock = [1000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])
    manager = make_manager()
    manager.players = [{"id": "aa11bb22cc33dd44", "name": "Known"}]
    manager.discovered = {
        "aa11bb22cc33dd44": 1000,  # bereits registriert -> ausgeschlossen
        "ffeeddcc99887766": 1000,  # frisch -> enthalten
        "1122334455667788": 1000,  # wird gleich stale
    }
    clock[0] = 1000 + im.DISCOVERY_STALE_MS + 1
    result = manager._discovered_list()
    ids = [entry["id"] for entry in result]
    assert "aa11bb22cc33dd44" not in ids
    assert "ffeeddcc99887766" not in ids  # jetzt auch stale
    assert "1122334455667788" not in manager.discovered


def test_start_round_host_sets_ble_state_and_epoch(monkeypatch):
    import time

    monkeypatch.setattr(time, "ticks_ms", lambda: 42_000)
    manager = make_manager()
    manager.start_round("host")
    assert manager.role == "host"
    assert manager.ble_state == im.STATE_HOST
    assert manager.running is True
    assert manager.current_host_raw == manager.node_raw


def test_start_round_seeker_sets_ble_state():
    manager = make_manager()
    manager.start_round("seeker")
    assert manager.role == "seeker"
    assert manager.ble_state == im.STATE_SEEKER
    assert manager.current_host_raw == im.ZERO_NODE_ID


def test_stop_round_logs_entry_and_sets_stop_state():
    manager = make_manager()
    manager.start_round("host")
    manager.stop_round("Manuell beendet")
    assert manager.running is False
    assert manager.ble_state == im.STATE_STOP
    log = im.load_infection_log()
    assert len(log) == 1
    assert log[0]["result"] == "stopped"


def test_stop_round_when_not_running_does_not_log():
    manager = make_manager()
    manager.stop_round("Beendet")
    assert im.load_infection_log() == []


def test_handoff_protocol_bomb_mode_host_becomes_seeker(monkeypatch):
    import time

    clock = [100_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])

    host = make_manager(node_raw=b"\x01" * 8, player_name="Host")
    seeker = make_manager(node_raw=b"\x02" * 8, player_name="Seeker")
    register_mutually(host, seeker)
    host.config["game_mode"] = "bomb"
    seeker.config["game_mode"] = "bomb"
    host.config["rssi_threshold"] = -80
    seeker.config["rssi_threshold"] = -80

    host.start_round("host")
    seeker.start_round("seeker")
    clock[0] += host.config["cooldown_seconds"] * 1000 + 100

    # Seeker empfaengt Hosts Anker-Broadcast -> fordert Uebergabe an.
    seeker._handle_packet(im.STATE_HOST, host.node_raw, im.ZERO_NODE_ID, host.token_epoch, -10)
    assert seeker.ble_state == im.STATE_CLAIM
    assert seeker.target_node == host.node_raw

    # Host empfaengt Seekers CLAIM -> gewaehrt Uebergabe, verliert Host-Rolle (bomb mode).
    host._handle_packet(im.STATE_CLAIM, seeker.node_raw, host.node_raw, host.token_epoch, -10)
    assert host.role == "seeker"
    assert host.infection_count == 1
    assert host.ble_state == im.STATE_GRANT

    # Seeker empfaengt GRANT -> wird selbst zum Host.
    seeker._handle_packet(im.STATE_GRANT, host.node_raw, seeker.node_raw, host.token_epoch, -10)
    assert seeker.role == "host"
    assert seeker.infection_count == 1
    assert seeker.contacts[-1]["direction"] == "infected_me"
    assert host.contacts[-1]["direction"] == "infected_by_me"


def test_handoff_protocol_infect_mode_host_stays_infected(monkeypatch):
    import time

    clock = [100_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])

    host = make_manager(node_raw=b"\x01" * 8)
    seeker = make_manager(node_raw=b"\x02" * 8)
    register_mutually(host, seeker)
    host.config["game_mode"] = "infect"
    host.config["rssi_threshold"] = -80

    host.start_round("host")
    seeker.start_round("seeker")
    clock[0] += host.config["cooldown_seconds"] * 1000 + 100

    host._handle_packet(im.STATE_CLAIM, seeker.node_raw, host.node_raw, host.token_epoch, -10)
    assert host.role == "host"  # bleibt Host im Infect-Modus


def test_handle_packet_ignores_unregistered_sender():
    host = make_manager(node_raw=b"\x01" * 8)
    host.start_round("host")
    stranger_raw = b"\x03" * 8
    host._handle_packet(im.STATE_CLAIM, stranger_raw, host.node_raw, host.token_epoch, -10)
    assert host.infection_count == 0


def test_handle_packet_ignores_own_node():
    host = make_manager(node_raw=b"\x01" * 8)
    host.start_round("host")
    host._handle_packet(im.STATE_CLAIM, host.node_raw, host.node_raw, host.token_epoch, -10)
    assert host.infection_count == 0


def test_handle_packet_rejects_weak_signal_claim(monkeypatch):
    import time

    clock = [100_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])
    host = make_manager(node_raw=b"\x01" * 8)
    seeker = make_manager(node_raw=b"\x02" * 8)
    register_mutually(host, seeker)
    host.config["rssi_threshold"] = -50
    host.start_round("host")
    clock[0] += host.config["cooldown_seconds"] * 1000 + 100

    host._handle_packet(im.STATE_CLAIM, seeker.node_raw, host.node_raw, host.token_epoch, -90)
    assert host.infection_count == 0


def test_remaining_seconds_zero_when_not_running():
    manager = make_manager()
    assert manager.remaining_seconds() == 0


def test_session_summary_text_empty_when_no_result():
    manager = make_manager()
    assert manager.session_summary_text() == ""


def test_session_summary_text_includes_contacts():
    manager = make_manager(player_name="Ace")
    manager.round_result = "won"
    manager._record_contact("infected_me", "Bob", "aa11bb22cc33dd44")
    text = manager.session_summary_text()
    assert "GEWONNEN" in text
    assert "Ace" in text
    assert "hat mich infiziert" in text
    assert "Bob" in text


def test_lobby_full_roundtrip_host_and_joiner(monkeypatch):
    import time

    clock = [1000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])

    host = make_manager(node_raw=b"\xaa" * 8, player_name="Host")
    joiner = make_manager(node_raw=b"\xbb" * 8, player_name="Joiner")

    host.create_lobby()
    open_packet = host._lobby_open_packet()
    joiner._handle_lobby_packet(joiner._parse_lobby_advertisement(open_packet[3:]))
    assert im._hex_id(host.node_raw) in joiner.lobby_seen_hosts

    joiner.join_lobby(im._hex_id(host.node_raw))
    assert joiner.lobby_role == "joiner"

    join_packet = joiner._lobby_join_packet()
    host._handle_lobby_packet(host._parse_lobby_advertisement(join_packet[3:]))
    assert im._hex_id(joiner.node_raw) in host.lobby_members

    host.finalize_lobby()
    assert host.lobby_finalized is True
    assert len(host.lobby_roster) == 2

    for index in range(len(host.lobby_roster)):
        host.lobby_roster_index = index
        roster_packet = host._lobby_roster_packet()
        joiner._handle_lobby_packet(joiner._parse_lobby_advertisement(roster_packet[3:]))

    assert joiner.lobby_complete is True
    joined_ids = {entry["id"] for entry in joiner.players}
    assert im._hex_id(host.node_raw) in joined_ids


def test_cancel_lobby_resets_host_state():
    host = make_manager()
    host.create_lobby()
    host.cancel_lobby()
    assert host.lobby_role is None
    assert host.lobby_active is False


def test_leave_lobby_resets_joiner_state():
    joiner = make_manager()
    joiner.lobby_role = "joiner"
    joiner.leave_lobby()
    assert joiner.lobby_role is None
    assert joiner.lobby_joined_host == im.ZERO_NODE_ID


def test_rename_lobby_member_updates_name():
    host = make_manager()
    host.lobby_members["abc123"] = {"name": "", "last_seen_ms": 0}
    host.rename_lobby_member("abc123", "Renamed")
    assert host.lobby_members["abc123"]["name"] == "Renamed"


def test_load_infection_log_missing_file_returns_empty():
    assert im.load_infection_log() == []


def test_save_infection_log_trims_to_max_entries():
    entries = [{"i": i} for i in range(im.INFECTION_LOG_MAX_ENTRIES + 3)]
    ok, _err = im.save_infection_log(entries)
    assert ok is True
    assert len(im.load_infection_log()) == im.INFECTION_LOG_MAX_ENTRIES


@pytest.mark.asyncio
async def test_handle_infection_route_data_and_config():
    from tests.source.conftest import FakeWriter

    manager = make_manager()
    writer = FakeWriter()
    assert await im.handle_infection_route(writer, "/infection-data", "GET", {}, {}, manager) is True
    assert writer.json()["role"] == "seeker"

    writer = FakeWriter()
    handled = await im.handle_infection_route(
        writer, "/infection-config", "POST", {},
        {"enabled": "1", "initial_role": "host", "game_mode": "infect"}, manager,
    )
    assert handled is True
    body = writer.json()
    assert body["config"]["game_mode"] == "infect"
    assert body["role"] == "host"


@pytest.mark.asyncio
async def test_handle_infection_route_players_add_validates_id():
    from tests.source.conftest import FakeWriter

    manager = make_manager()
    writer = FakeWriter()
    handled = await im.handle_infection_route(
        writer, "/infection-players-add", "POST", {}, {"id": "not-valid", "name": "Bob"}, manager
    )
    assert handled is True
    assert "400" in writer.status_line
    assert writer.json()["ok"] is False

    writer = FakeWriter()
    handled = await im.handle_infection_route(
        writer, "/infection-players-add", "POST", {}, {"id": "aa11bb22cc33dd44", "name": "Bob"}, manager
    )
    assert handled is True
    assert writer.json()["ok"] is True
    assert manager.players[0]["name"] == "Bob"


@pytest.mark.asyncio
async def test_handle_infection_route_lobby_lifecycle():
    from tests.source.conftest import FakeWriter

    manager = make_manager()
    writer = FakeWriter()
    assert await im.handle_infection_route(writer, "/lobby-create", "POST", {}, {}, manager) is True
    assert writer.json()["lobby"]["active"] is True

    writer = FakeWriter()
    assert await im.handle_infection_route(writer, "/lobby-cancel", "POST", {}, {}, manager) is True
    assert writer.json()["lobby"]["active"] is False


@pytest.mark.asyncio
async def test_handle_infection_route_stop_and_unknown():
    from tests.source.conftest import FakeWriter

    manager = make_manager()
    manager.configure({"enabled": True, "initial_role": "host"})
    writer = FakeWriter()
    assert await im.handle_infection_route(writer, "/infection-stop", "POST", {}, {}, manager) is True
    assert writer.json()["running"] is False

    handled = await im.handle_infection_route(FakeWriter(), "/nope", "GET", {}, {}, manager)
    assert handled is False


@pytest.mark.asyncio
async def test_run_ends_round_and_sets_result(monkeypatch):
    import asyncio
    import time

    async def fast_sleep_ms(_ms):
        await asyncio.sleep(0)

    monkeypatch.setattr(asyncio, "sleep_ms", fast_sleep_ms)
    clock = [0]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])

    manager = make_manager()
    manager.config = manager._normalize_config({"round_seconds": 30, "initial_role": "seeker"})
    manager.start_round("seeker")

    task = asyncio.ensure_future(manager.run())
    try:
        clock[0] += 31_000
        for _ in range(10):
            await asyncio.sleep(0)
        assert manager.running is False
        assert manager.round_result == "won"  # Seeker, der nicht infiziert wurde, gewinnt
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
