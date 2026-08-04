"""Tests fuer source/mods/shooter/main.py - der Shooter-Spielmodus als
vollstaendiges Plugin (Spiellogik + eigene IR-Hardware-Treiber + eigene
Weboberflaeche, siehe dortiger Modul-Docstring).

Wird als echtes Unterpaket "mods.shooter.main" importiert (source/ ist
bereits ueber das Root-conftest.py auf sys.path, source/mods/__init__.py
und source/mods/shooter/__init__.py existieren als committete Dateien) -
kein exec()/compile()-Trick noetig wie in plugin_manager.py's generischen
Tests, da hier die ECHTEN Plugin-Dateien getestet werden, nicht synthetische
Test-Mods. sys.modules wird trotzdem vor/nach jedem Test von allen
"mods"/"mods.*"-Eintraegen befreit, damit ein evtl. von einem anderen Test
(z.B. test_plugin_manager.py) hinterlassenes, auf ein temporaeres
Verzeichnis zeigendes "mods"-Package-Objekt nicht faelschlich
weiterverwendet wird."""
import sys

import pytest


def _purge_mods_modules():
    for key in list(sys.modules.keys()):
        if key == "mods" or key.startswith("mods."):
            del sys.modules[key]


@pytest.fixture
def shooter_plugin(install_stub_module):
    sent_html = []

    async def fake_send_html_file(writer, path):
        sent_html.append(path)

    install_stub_module(
        "main",
        DEFAULT_PILOT_NAME="TestPilot",
        debug_log=lambda message: None,
        send_html_file=fake_send_html_file,
        rc_channels_state={"channels": [0] * 16, "updated_ms": 0},
    )

    _purge_mods_modules()
    import importlib
    module = importlib.import_module("mods.shooter.main")
    module._test_sent_html = sent_html
    yield module
    _purge_mods_modules()


def test_derive_node_id_xors_all_bytes(shooter_plugin):
    assert shooter_plugin._derive_node_id(b"\x01\x02\x03") == 0x01 ^ 0x02 ^ 0x03


def test_default_config_when_no_file(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    assert manager.config["lives"] == shooter_plugin.DEFAULT_LIVES
    assert manager.config["damage"] == shooter_plugin.DEFAULT_DAMAGE
    assert manager.config["enabled"] is False


def test_normalize_config_clamps_values(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
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


def test_save_and_load_config_roundtrip(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.config = manager._normalize_config({"lives": 3, "damage": 2})
    manager._save_config()

    reloaded = shooter_plugin.ShooterMode()
    assert reloaded.config["lives"] == 3
    assert reloaded.config["damage"] == 2


def test_start_round_resets_counters(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.hits_taken = 7
    manager.shots_fired = 3
    manager.eliminated = True
    status = manager.start_round()
    assert status["running"] is True
    assert status["hits_taken"] == 0
    assert status["shots_fired"] == 0
    assert status["eliminated"] is False
    assert status["lives_remaining"] == manager.config["lives"]


def test_stop_round_records_log_entry_when_activity(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.start_round()
    manager.hits_taken = 4
    manager.shots_fired = 2
    manager.stop_round("Runde beendet")
    log = shooter_plugin.load_shooter_log()
    assert len(log) == 1
    assert log[0]["hits_taken"] == 4
    assert log[0]["shots_fired"] == 2


def test_stop_round_does_not_log_when_no_activity(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.start_round()
    manager.stop_round("Runde beendet")
    assert shooter_plugin.load_shooter_log() == []


def test_configure_enabled_starts_round(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    status = manager.configure({"enabled": True, "lives": 10})
    assert status["running"] is True
    assert status["config"]["lives"] == 10


def test_configure_disabled_stops_round(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.configure({"enabled": True})
    status = manager.configure({"enabled": False})
    assert status["running"] is False


def test_fire_fails_when_round_not_running(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    result = manager.fire()
    assert result["ok"] is False


def test_fire_fails_when_eliminated(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.start_round()
    manager.eliminated = True
    result = manager.fire()
    assert result["ok"] is False


def test_fire_increments_shots_fired(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.start_round()
    result = manager.fire()
    assert result["ok"] is True
    assert manager.shots_fired == 1


def test_fire_respects_cooldown(shooter_plugin, monkeypatch):
    import time

    clock = [1_000_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])
    manager = shooter_plugin.ShooterMode()
    manager.start_round()
    assert manager.fire()["ok"] is True
    clock[0] += 10  # well within default fire cooldown
    result = manager.fire()
    assert result["ok"] is False
    assert manager.shots_fired == 1


def test_fire_returns_error_when_emitter_unavailable(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.start_round()
    manager.emitter.available = False
    result = manager.fire()
    assert result["ok"] is False


def test_apply_hit_ignores_own_node_id(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.start_round()
    manager._apply_hit(manager.node_id, 1, 1000)
    assert manager.hits_taken == 0


def test_apply_hit_increments_and_tracks_source(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.start_round()
    manager._apply_hit(42, 1, 1000)
    assert manager.hits_taken == 1
    assert manager.last_hit_from == 42
    assert manager.hit_sources[42]["hits"] == 1


def test_apply_hit_respects_per_shooter_cooldown(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.start_round()
    manager.config["hit_cooldown_ms"] = 300
    manager._apply_hit(42, 1, 1000)
    manager._apply_hit(42, 1, 1100)  # too soon, must be ignored
    assert manager.hits_taken == 1
    manager._apply_hit(42, 1, 1400)  # cooldown elapsed
    assert manager.hits_taken == 2


def test_apply_hit_different_shooters_not_throttled_by_each_other(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.start_round()
    manager._apply_hit(1, 1, 1000)
    manager._apply_hit(2, 1, 1010)
    assert manager.hits_taken == 2


def test_apply_hit_eliminates_when_lives_reach_zero(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.configure({"enabled": True, "lives": 2, "damage": 1})
    manager._apply_hit(1, manager.config["damage"], 1000)
    assert manager.eliminated is False
    manager._apply_hit(1, manager.config["damage"], 2000)
    assert manager.eliminated is True
    assert manager.lives_remaining == 0


def test_apply_hit_ignored_once_eliminated(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.configure({"enabled": True, "lives": 1, "damage": 1})
    manager._apply_hit(1, manager.config["damage"], 1000)  # bringt auf 0 Leben -> ausgeschieden
    assert manager.eliminated is True
    assert manager.hits_taken == 1

    manager._apply_hit(2, manager.config["damage"], 5000)  # anderer Schuetze, weit ausserhalb Cooldown
    assert manager.hits_taken == 1  # darf NICHT weiter mitgezaehlt werden
    assert manager.last_hit_from == 1  # unveraendert vom letzten gueltigen Treffer


def test_fire_still_blocked_once_eliminated(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.configure({"enabled": True, "lives": 1, "damage": 1})
    manager._apply_hit(1, manager.config["damage"], 1000)
    assert manager.eliminated is True
    result = manager.fire()
    assert result["ok"] is False
    assert manager.shots_fired == 0


def test_apply_hit_unlimited_lives_never_eliminates(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.configure({"enabled": True, "lives": 0})
    for i in range(10):
        manager._apply_hit(1, 9, 1000 + i * 1000)
    assert manager.eliminated is False


def test_status_contains_expected_shape(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    status = manager.status()
    assert status["ok"] is True
    assert "hardware" in status
    assert "emitter_available" in status["hardware"]
    assert "receiver_available" in status["hardware"]
    assert isinstance(status["hit_sources"], list)


def test_step_is_noop_when_round_not_running(shooter_plugin):
    """step() (siehe main.py's loop(), das diese Methode synchron aus
    plugin_manager.run_loops() heraus aufruft) darf ausserhalb einer
    laufenden Runde nichts tun."""
    manager = shooter_plugin.ShooterMode()
    manager.receiver.poll = lambda: [{"address": 7, "command": 2, "ts_us": 0}]
    manager.step()
    assert manager.hits_taken == 0


def test_step_applies_hits_from_receiver_poll(shooter_plugin, monkeypatch):
    manager = shooter_plugin.ShooterMode()
    manager.start_round()
    monkeypatch.setattr(manager.receiver, "poll", lambda: [{"address": 7, "command": 2, "ts_us": 0}])
    manager.step()
    assert manager.hits_taken == 1
    assert manager.last_hit_from == 7


def test_step_fires_automatically_when_aux_channel_above_threshold(shooter_plugin, monkeypatch):
    import time

    monkeypatch.setattr(time, "ticks_ms", lambda: 10_000)
    manager = shooter_plugin.ShooterMode()
    manager.configure({"enabled": True, "aux_channel": 3, "aux_threshold_us": 1700})
    monkeypatch.setattr(manager.receiver, "poll", lambda: [])

    channels = [992] * 16
    channels[2] = 1811  # Kanal 3 ueber der Schwelle
    shooter_plugin.rc_channels_state["channels"] = channels
    shooter_plugin.rc_channels_state["updated_ms"] = 10_000

    manager.step()

    assert manager.shots_fired == 1
    assert manager.aux_available is True
    assert manager.aux_value_us == 2011


@pytest.mark.asyncio
async def test_run_applies_hits_from_receiver_poll(shooter_plugin, monkeypatch):
    manager = shooter_plugin.ShooterMode()
    manager.start_round()

    async def fake_sleep_ms(_ms):
        raise StopAsyncIteration

    monkeypatch.setattr(shooter_plugin.asyncio, "sleep_ms", fake_sleep_ms)
    monkeypatch.setattr(manager.receiver, "poll", lambda: [{"address": 7, "command": 2, "ts_us": 0}])

    with pytest.raises(StopAsyncIteration):
        await manager.run()

    assert manager.hits_taken == 1
    assert manager.last_hit_from == 7


def test_crsf_raw_to_us_matches_known_reference_points(shooter_plugin):
    assert shooter_plugin._crsf_raw_to_us(992) == 1500  # Mitte
    assert shooter_plugin._crsf_raw_to_us(172) == 988   # unterer Anschlag
    assert shooter_plugin._crsf_raw_to_us(1811) == 2011  # oberer Anschlag


def test_read_aux_state_disabled_when_channel_is_zero(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.config["aux_channel"] = 0
    shooter_plugin.rc_channels_state["channels"] = [1811] * 16
    shooter_plugin.rc_channels_state["updated_ms"] = shooter_plugin.time.ticks_ms()
    assert manager._read_aux_state() == (False, None)


def test_read_aux_state_unavailable_when_no_fresh_data(shooter_plugin):
    manager = shooter_plugin.ShooterMode()
    manager.config["aux_channel"] = 5
    shooter_plugin.rc_channels_state["channels"] = [1811] * 16
    shooter_plugin.rc_channels_state["updated_ms"] = 0
    assert manager._read_aux_state() == (False, None)


def test_read_aux_state_unavailable_when_stale(shooter_plugin, monkeypatch):
    import time

    clock = [1_000_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])
    manager = shooter_plugin.ShooterMode()
    manager.config["aux_channel"] = 5
    shooter_plugin.rc_channels_state["channels"] = [1811] * 16
    shooter_plugin.rc_channels_state["updated_ms"] = clock[0]
    clock[0] += shooter_plugin.AUX_STALE_MS + 100
    assert manager._read_aux_state() == (False, None)


def test_read_aux_state_returns_converted_value(shooter_plugin, monkeypatch):
    import time

    monkeypatch.setattr(time, "ticks_ms", lambda: 5000)
    manager = shooter_plugin.ShooterMode()
    manager.config["aux_channel"] = 5
    channels = [992] * 16
    channels[4] = 1811  # Kanal 5 (1-indiziert) = oberer Anschlag
    shooter_plugin.rc_channels_state["channels"] = channels
    shooter_plugin.rc_channels_state["updated_ms"] = 5000
    available, value_us = manager._read_aux_state()
    assert available is True
    assert value_us == 2011


@pytest.mark.asyncio
async def test_run_fires_automatically_when_aux_channel_above_threshold(shooter_plugin, monkeypatch):
    import time

    monkeypatch.setattr(time, "ticks_ms", lambda: 10_000)
    manager = shooter_plugin.ShooterMode()
    manager.configure({"enabled": True, "aux_channel": 3, "aux_threshold_us": 1700})
    monkeypatch.setattr(manager.receiver, "poll", lambda: [])

    channels = [992] * 16
    channels[2] = 1811  # Kanal 3 ueber der Schwelle
    shooter_plugin.rc_channels_state["channels"] = channels
    shooter_plugin.rc_channels_state["updated_ms"] = 10_000

    async def fake_sleep_ms(_ms):
        raise StopAsyncIteration

    monkeypatch.setattr(shooter_plugin.asyncio, "sleep_ms", fake_sleep_ms)

    with pytest.raises(StopAsyncIteration):
        await manager.run()

    assert manager.shots_fired == 1
    assert manager.aux_available is True
    assert manager.aux_value_us == 2011


@pytest.mark.asyncio
async def test_run_does_not_fire_when_aux_channel_below_threshold(shooter_plugin, monkeypatch):
    import time

    monkeypatch.setattr(time, "ticks_ms", lambda: 10_000)
    manager = shooter_plugin.ShooterMode()
    manager.configure({"enabled": True, "aux_channel": 3, "aux_threshold_us": 1700})
    monkeypatch.setattr(manager.receiver, "poll", lambda: [])

    channels = [992] * 16  # Mitte, unter der Schwelle
    shooter_plugin.rc_channels_state["channels"] = channels
    shooter_plugin.rc_channels_state["updated_ms"] = 10_000

    async def fake_sleep_ms(_ms):
        raise StopAsyncIteration

    monkeypatch.setattr(shooter_plugin.asyncio, "sleep_ms", fake_sleep_ms)

    with pytest.raises(StopAsyncIteration):
        await manager.run()

    assert manager.shots_fired == 0


# ==================== Plugin-Lifecycle (setup/loop/teardown/handle_route) ====================


def test_setup_creates_singleton_manager(shooter_plugin):
    context = {"debug_log": lambda message: None, "plugin_dir": "mods/shooter"}
    shooter_plugin.setup(context)
    first = shooter_plugin._manager
    assert first is not None
    shooter_plugin.setup(context)
    assert shooter_plugin._manager is first  # kein zweites ShooterMode (keine doppelte IRQ)


def test_loop_calls_step_on_manager(shooter_plugin, monkeypatch):
    shooter_plugin.setup({"debug_log": lambda m: None, "plugin_dir": "mods/shooter"})
    calls = []
    monkeypatch.setattr(shooter_plugin._manager, "step", lambda: calls.append(1))
    shooter_plugin.loop()
    assert calls == [1]


def test_teardown_stops_running_round(shooter_plugin):
    shooter_plugin.setup({"debug_log": lambda m: None, "plugin_dir": "mods/shooter"})
    shooter_plugin._manager.start_round()
    assert shooter_plugin._manager.running is True
    shooter_plugin.teardown()
    assert shooter_plugin._manager.running is False


@pytest.mark.asyncio
async def test_handle_route_serves_admin_page(shooter_plugin):
    handled = await shooter_plugin.handle_route(object(), "/admin-shooter", "GET", {}, {})
    assert handled is True
    assert shooter_plugin._test_sent_html == [shooter_plugin.ADMIN_SHOOTER_HTML_PATH]


@pytest.mark.asyncio
async def test_handle_route_delegates_shooter_prefixed_routes(shooter_plugin, monkeypatch):
    shooter_plugin.setup({"debug_log": lambda m: None, "plugin_dir": "mods/shooter"})

    async def fake_handle_shooter_route(writer, path, method, query, body, manager):
        return True

    monkeypatch.setattr(shooter_plugin, "handle_shooter_route", fake_handle_shooter_route)
    handled = await shooter_plugin.handle_route(object(), "/shooter-data", "GET", {}, {})
    assert handled is True


@pytest.mark.asyncio
async def test_handle_route_unknown_path_returns_false(shooter_plugin):
    shooter_plugin.setup({"debug_log": lambda m: None, "plugin_dir": "mods/shooter"})
    handled = await shooter_plugin.handle_route(object(), "/does-not-exist", "GET", {}, {})
    assert handled is False


def test_get_ui_schema_shape(shooter_plugin):
    schema = shooter_plugin.get_ui_schema()
    assert schema["title"] == "Shooter"
    assert schema["poll_endpoint"] == "/shooter-data"
    section_types = [section["type"] for section in schema["sections"]]
    assert section_types == ["stats", "stats", "form", "actions", "list"]


def test_get_ui_schema_form_fields_match_manager_config_keys(shooter_plugin):
    """Jedes "form"-Feld muss ein Schluessel sein, den ShooterMode.configure()
    tatsaechlich kennt (siehe _default_config()) - sonst wuerde die App ein
    Feld anzeigen/senden, das der Server stillschweigend ignoriert."""
    schema = shooter_plugin.get_ui_schema()
    form_section = next(section for section in schema["sections"] if section["type"] == "form")
    form_keys = {field["key"] for field in form_section["fields"]}

    manager = shooter_plugin.ShooterMode("TestPilot")
    assert form_keys == set(manager._default_config().keys())
    assert form_section["submit_endpoint"] == "/shooter-config"


def test_get_ui_schema_action_endpoints_are_shooter_routes(shooter_plugin):
    schema = shooter_plugin.get_ui_schema()
    actions_section = next(section for section in schema["sections"] if section["type"] == "actions")
    endpoints = {button["endpoint"] for button in actions_section["buttons"]}
    assert endpoints == {"/shooter-stop", "/shooter-fire"}


def test_get_ui_schema_registered_via_plugin_manager(shooter_plugin, monkeypatch):
    """Stellt sicher, dass manifest.json tatsaechlich auf die vorhandene
    Funktion zeigt (Tippfehler in "ui_pages" wuerden sonst erst zur Laufzeit
    auf dem echten Geraet auffallen)."""
    import json
    import os

    manifest_path = os.path.join(os.path.dirname(shooter_plugin.__file__), "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    fn_name = manifest["ui_pages"]["main"]
    assert getattr(shooter_plugin, fn_name)() == shooter_plugin.get_ui_schema()
