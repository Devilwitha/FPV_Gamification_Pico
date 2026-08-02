import json

import pytest

import challenge_helpers as ch


class Recorder:
    def __init__(self):
        self.scores = []
        self.logs = []

    def add_score(self, points, description):
        self.scores.append((points, description))

    def log(self, message):
        self.logs.append(message)


# ==================== Missionen ====================

def test_sanitize_mission_name_strips_disallowed_chars():
    assert ch._sanitize_mission_name("../evil/name") == "evilname"
    assert ch._sanitize_mission_name("My Mission!") == "My_Mission"
    assert ch._sanitize_mission_name("a" * 100) == ("a" * 40)


def test_save_and_get_and_list_and_delete_mission_roundtrip():
    mission = {"challenge_type": "eco", "params": {"points_base": 300}}
    ok, err = ch.save_mission_file("My Eco Run", mission)
    assert ok is True
    assert err == ""

    assert "My_Eco_Run" in ch.list_mission_files()
    loaded = ch.get_mission_data("My_Eco_Run")
    assert loaded == mission

    ok, err = ch.delete_mission_file("My_Eco_Run")
    assert ok is True
    assert ch.get_mission_data("My_Eco_Run") is None


def test_save_mission_file_rejects_unknown_challenge_type():
    ok, err = ch.save_mission_file("bad", {"challenge_type": "not_real", "params": {}})
    assert ok is False
    assert "challenge_type" in err


def test_save_mission_file_rejects_missing_params():
    ok, err = ch.save_mission_file("bad", {"challenge_type": "eco"})
    assert ok is False
    assert "params" in err


def test_save_mission_file_rejects_invalid_name():
    ok, err = ch.save_mission_file("../../etc", {"challenge_type": "eco", "params": {}})
    # Der sanitisierte Name ("etc") ist gueltig, daher testen wir stattdessen
    # einen Namen, der komplett aus verbotenen Zeichen besteht.
    ok2, err2 = ch.save_mission_file("###", {"challenge_type": "eco", "params": {}})
    assert ok2 is False
    assert "Ungueltiger Missionsname" in err2


def test_get_mission_data_missing_returns_none():
    assert ch.get_mission_data("does-not-exist") is None


def test_delete_mission_file_missing_reports_error():
    ok, err = ch.delete_mission_file("does-not-exist")
    assert ok is False
    assert err


# ==================== Challenge-Verlauf ====================

def test_load_challenge_log_missing_file_returns_empty_list():
    assert ch.load_challenge_log() == []


def test_save_challenge_log_trims_to_max_entries():
    entries = [{"i": i} for i in range(ch.CHALLENGE_LOG_MAX_ENTRIES + 7)]
    ok, _err = ch.save_challenge_log(entries)
    assert ok is True
    loaded = ch.load_challenge_log()
    assert len(loaded) == ch.CHALLENGE_LOG_MAX_ENTRIES
    assert loaded[-1]["i"] == entries[-1]["i"]


# ==================== TouchAndGoChallenge ====================

def test_touch_and_go_soft_landing_awards_full_points():
    rec = Recorder()
    challenge = ch.TouchAndGoChallenge(rec.add_score, rec.log)
    challenge.start(now_ms=0)

    challenge.update_vario(-50, 0)  # Sinkflug erkannt
    challenge.update_attitude(20.0, 100)  # sanfter Ausschlag
    challenge.update_vario(0, 200)
    challenge.update_vario(0, 200 + ch.TOUCHGO_TOUCHDOWN_HOLD_MS)  # Band lange genug gehalten

    assert challenge.active is False
    assert rec.scores == [(ch.TOUCHGO_BASE_POINTS, "Touch & Go: weiche Landung (max 20 deg/s)")]


def test_touch_and_go_hard_landing_awards_zero_points():
    rec = Recorder()
    challenge = ch.TouchAndGoChallenge(rec.add_score, rec.log)
    challenge.start(now_ms=0)

    challenge.update_vario(-50, 0)
    challenge.update_attitude(300.0, 100)  # deutlich ueber hard_gyro_max
    challenge.update_vario(0, 200)
    challenge.update_vario(0, 200 + ch.TOUCHGO_TOUCHDOWN_HOLD_MS)

    assert rec.scores == []
    assert "Harte Landung" in challenge.last_result


def test_touch_and_go_without_descent_scores_nothing():
    rec = Recorder()
    challenge = ch.TouchAndGoChallenge(rec.add_score, rec.log)
    challenge.start(now_ms=0)
    # Nie im Sinkflug gewesen, direkt "auf dem Boden".
    challenge.update_vario(0, 0)
    challenge.update_vario(0, ch.TOUCHGO_TOUCHDOWN_HOLD_MS)
    assert rec.scores == []
    assert "Kein Sinkflug" in challenge.last_result


def test_touch_and_go_times_out():
    rec = Recorder()
    challenge = ch.TouchAndGoChallenge(rec.add_score, rec.log)
    challenge.start(now_ms=0)
    challenge.update_vario(-50, ch.TOUCHGO_TIMEOUT_MS + 1)
    assert challenge.active is False
    assert "Zeitueberschreitung" in challenge.last_result


def test_touch_and_go_cancel():
    rec = Recorder()
    challenge = ch.TouchAndGoChallenge(rec.add_score, rec.log)
    challenge.start(now_ms=0)
    challenge.cancel()
    assert challenge.active is False
    assert challenge.last_result == "Abgebrochen"


def test_touch_and_go_ignores_updates_when_inactive():
    rec = Recorder()
    challenge = ch.TouchAndGoChallenge(rec.add_score, rec.log)
    challenge.update_vario(-50, 0)  # nie gestartet
    assert challenge.was_descending is False


# ==================== AltitudeChallenge ====================

def test_altitude_hold_success_awards_points():
    # update() ignoriert Schritte mit > 500ms Abstand (siehe Kommentar dort) -
    # daher in 400ms-Schritten aufrufen, um zuverlaessig 2.0s Bandzeit zu
    # akkumulieren.
    rec = Recorder()
    challenge = ch.AltitudeChallenge(rec.add_score, rec.log)
    challenge.start("hold", tolerance_m=0.5, duration_s=2.0, now_ms=0)
    challenge.update(10.0, 0)  # Referenzhoehe setzen
    for t in (400, 800, 1200, 1600, 2000):
        challenge.update(10.1, t)
    assert challenge.active is False
    assert len(rec.scores) == 1
    assert rec.scores[0][0] == round(ch.ALT_POINTS_PER_SECOND_IN_BAND * 2.0)


def test_altitude_hold_resets_progress_when_out_of_band():
    challenge = ch.AltitudeChallenge(lambda *a: None, lambda *a: None)
    challenge.start("hold", tolerance_m=0.2, duration_s=5.0, now_ms=0)
    challenge.update(10.0, 0)
    challenge.update(10.1, 200)
    challenge.update(15.0, 400)  # weit ausserhalb -> Fortschritt resettet
    assert challenge.elapsed_in_band_ms == 0


def test_limbo_mode_uses_ceiling_instead_of_target():
    rec = Recorder()
    challenge = ch.AltitudeChallenge(rec.add_score, rec.log)
    challenge.start("limbo", ceiling_m=1.0, duration_s=1.0, now_ms=0)
    challenge.update(0.5, 0)
    challenge.update(0.5, 500)
    challenge.update(0.5, 1000)
    assert rec.scores
    assert "Limbo" in challenge.last_result


def test_altitude_challenge_times_out():
    challenge = ch.AltitudeChallenge(lambda *a: None, lambda *a: None)
    challenge.start("hold", duration_s=999, now_ms=0)
    challenge.update(10.0, 0)
    challenge.update(10.0, ch.ALT_CHALLENGE_TIMEOUT_MS + 1)
    assert challenge.active is False
    assert "Zeitueberschreitung" in challenge.last_result


# ==================== EcoChallenge ====================

def test_eco_challenge_awards_points_based_on_consumption():
    rec = Recorder()
    challenge = ch.EcoChallenge(rec.add_score, rec.log)
    challenge.start(now_ms=0, points_base=500, points_per_mah=2.0)
    challenge.update(100, 1000)  # Startkapazitaet
    challenge.update(150, 10_000)  # 50 mAh verbraucht
    result = challenge.stop(10_000)
    assert result["ok"] is True
    assert result["consumed_mah"] == 50
    assert result["points"] == 500 - 50 * 2.0
    assert rec.scores == [(400, "Eco-Challenge: 50 mAh in 10s")]


def test_eco_challenge_too_short_awards_no_points():
    challenge = ch.EcoChallenge(lambda *a: None, lambda *a: None)
    challenge.start(now_ms=0)
    challenge.update(100, 0)
    challenge.update(100, 1000)  # < ECO_MIN_DURATION_S
    result = challenge.stop(1000)
    assert result["points"] == 0
    assert "kurz" in result["message"]


def test_eco_challenge_stop_without_start_returns_error():
    challenge = ch.EcoChallenge(lambda *a: None, lambda *a: None)
    result = challenge.stop(0)
    assert result["ok"] is False


def test_eco_challenge_no_telemetry_received():
    challenge = ch.EcoChallenge(lambda *a: None, lambda *a: None)
    challenge.start(now_ms=0)
    result = challenge.stop(6000)
    assert result["ok"] is True
    assert result["points"] == 0
    assert "Keine Akku-Telemetrie" in result["message"]


# ==================== HeadingChallenge ====================

def test_heading_hold_success():
    rec = Recorder()
    challenge = ch.HeadingChallenge(rec.add_score, rec.log)
    challenge.start(now_ms=0, tolerance_deg=10, duration_s=1.0)
    challenge.update(90.0, 0)  # Zielkurs setzen
    challenge.update(92.0, 500)
    challenge.update(88.0, 1000)
    assert rec.scores
    assert rec.scores[0][0] == round(ch.HEADING_POINTS_PER_SECOND_IN_BAND * 1.0)


def test_heading_diff_handles_wraparound():
    assert ch._heading_diff(359, 1) == pytest.approx(2.0)
    assert ch._heading_diff(1, 359) == pytest.approx(2.0)
    assert ch._heading_diff(10, 20) == pytest.approx(10.0)


def test_heading_challenge_times_out():
    challenge = ch.HeadingChallenge(lambda *a: None, lambda *a: None)
    challenge.start(now_ms=0, duration_s=999)
    challenge.update(10.0, 0)
    challenge.update(10.0, ch.HEADING_CHALLENGE_TIMEOUT_MS + 1)
    assert challenge.active is False
    assert "Zeitueberschreitung" in challenge.last_result


# ==================== LinkQualityChallenge ====================

def test_link_quality_success_within_band():
    rec = Recorder()
    challenge = ch.LinkQualityChallenge(rec.add_score, rec.log)
    challenge.start(now_ms=0, min_lq=70, max_lq=100, duration_s=1.0)
    challenge.update(90, 0)
    challenge.update(90, 500)
    challenge.update(90, 1000)
    assert rec.scores


def test_link_quality_below_min_resets_progress():
    challenge = ch.LinkQualityChallenge(lambda *a: None, lambda *a: None)
    challenge.start(now_ms=0, min_lq=70, duration_s=5.0)
    challenge.update(90, 0)
    challenge.update(90, 500)
    challenge.update(50, 1000)  # unter min_lq
    assert challenge.elapsed_in_band_ms == 0


def test_link_quality_tracks_worst_value():
    challenge = ch.LinkQualityChallenge(lambda *a: None, lambda *a: None)
    challenge.start(now_ms=0, min_lq=1, duration_s=999)
    challenge.update(90, 0)
    challenge.update(20, 500)
    challenge.update(80, 1000)
    assert challenge.worst_lq == 20


# ==================== SpeedChallenge ====================

def test_speed_challenge_scores_based_on_max_speed():
    rec = Recorder()
    challenge = ch.SpeedChallenge(rec.add_score, rec.log)
    challenge.start(now_ms=0, duration_s=1.0, points_per_kmh=2.0)
    challenge.update(50, 200)
    challenge.update(80, 400)  # neue Spitze
    challenge.update(60, 1100)  # Dauer ueberschritten -> finalisiert
    assert challenge.active is False
    assert rec.scores == [(160, "Speed-Run: 80 km/h Spitze")]


def test_speed_challenge_zero_speed_awards_no_points():
    rec = Recorder()
    challenge = ch.SpeedChallenge(rec.add_score, rec.log)
    challenge.start(now_ms=0, duration_s=1.0)
    challenge.update(0, 1100)
    assert rec.scores == []


# ==================== TrickChallenge ====================

def test_trick_challenge_any_target_accepts_first_trick():
    rec = Recorder()
    challenge = ch.TrickChallenge(rec.add_score, rec.log)
    challenge.start(now_ms=0, target_name="Any", bonus_points=50)
    challenge.on_trick_detected("Right Barrel Roll", 100)
    assert challenge.active is False
    assert rec.scores == [(50, "Trick-Challenge: Right Barrel Roll")]


def test_trick_challenge_specific_target_matches_substring_ignoring_direction():
    rec = Recorder()
    challenge = ch.TrickChallenge(rec.add_score, rec.log)
    challenge.start(now_ms=0, target_name="Barrel Roll")
    challenge.on_trick_detected("Left Barrel Roll", 100)
    assert rec.scores


def test_trick_challenge_wrong_trick_does_not_finish():
    challenge = ch.TrickChallenge(lambda *a: None, lambda *a: None)
    challenge.start(now_ms=0, target_name="Power Flip")
    challenge.on_trick_detected("Barrel Roll", 100)
    assert challenge.active is True


def test_trick_challenge_times_out():
    # start() erzwingt eine Mindest-Zeitlimit von 3.0s (max(3.0, time_limit_s)),
    # daher muss die verstrichene Zeit hier ueber 3000ms liegen.
    challenge = ch.TrickChallenge(lambda *a: None, lambda *a: None)
    challenge.start(now_ms=0, time_limit_s=1.0)
    assert challenge.time_limit_s == 3.0
    challenge.update(3001)
    assert challenge.active is False
    assert "Zeitueberschreitung" in challenge.last_result


# ==================== ChallengeManager ====================

def test_challenge_manager_update_vario_integrates_altitude():
    manager = ch.ChallengeManager(lambda *a: None)
    manager.update_vario(-100, 0)  # erster Aufruf initialisiert nur die Uhr
    manager.update_vario(-100, 1000)  # 1s bei -1m/s -> -1m Hoehenaenderung
    assert manager.status_dict()["altitude_m"] == pytest.approx(-1.0, abs=0.01)


def test_challenge_manager_record_result_persists_log():
    manager = ch.ChallengeManager(lambda *a: None)
    manager.record_result("Test-Challenge", 100, "01.01.2024 12:00:00")
    assert len(manager.log_entries) == 1
    reloaded = ch.load_challenge_log()
    assert reloaded[0]["points"] == 100


def test_challenge_manager_record_result_ignores_zero_points():
    manager = ch.ChallengeManager(lambda *a: None)
    manager.record_result("Nichts", 0, "ts")
    assert manager.log_entries == []


def test_challenge_manager_clear_log():
    manager = ch.ChallengeManager(lambda *a: None)
    manager.record_result("x", 10, "ts")
    manager.clear_log()
    assert manager.log_entries == []
    assert ch.load_challenge_log() == []


def test_challenge_manager_apply_mission_activates_for_type():
    manager = ch.ChallengeManager(lambda *a: None)
    ok, err = manager.apply_mission({
        "name": "Eco Test", "challenge_type": "eco", "params": {"points_base": 999},
    })
    assert ok is True
    assert err == ""
    assert manager.active_mission["eco"]["name"] == "Eco Test"
    assert manager.status_dict()["active_mission"]["eco"] == "Eco Test"


def test_challenge_manager_apply_mission_rejects_bad_data():
    manager = ch.ChallengeManager(lambda *a: None)
    ok, err = manager.apply_mission({"challenge_type": "not_real", "params": {}})
    assert ok is False


def test_challenge_manager_status_dict_shape():
    manager = ch.ChallengeManager(lambda *a: None)
    status = manager.status_dict()
    for key in ("touch_and_go", "altitude_challenge", "eco", "heading", "link_quality", "speed_run", "trick"):
        assert key in status


# ==================== handle_challenge_route ====================

@pytest.mark.asyncio
async def test_handle_challenge_route_data_and_touchgo_start():
    from tests.source.conftest import FakeWriter

    manager = ch.ChallengeManager(lambda *a: None)
    writer = FakeWriter()
    handled = await ch.handle_challenge_route(writer, "/challenges-data", "GET", {}, {}, manager)
    assert handled is True
    assert writer.json()["ok"] is True

    writer = FakeWriter()
    handled = await ch.handle_challenge_route(writer, "/challenge-touchgo-start", "GET", {}, {}, manager)
    assert handled is True
    assert manager.touch_and_go.active is True


@pytest.mark.asyncio
async def test_handle_challenge_route_altitude_start_uses_query_params():
    from tests.source.conftest import FakeWriter

    manager = ch.ChallengeManager(lambda *a: None)
    writer = FakeWriter()
    handled = await ch.handle_challenge_route(
        writer, "/challenge-altitude-start", "GET",
        {"mode": "limbo", "ceiling": "2.5", "duration": "10"}, {}, manager,
    )
    assert handled is True
    assert manager.altitude.active is True
    assert manager.altitude.mode == "limbo"
    assert manager.altitude.ceiling_m == 2.5


@pytest.mark.asyncio
async def test_handle_challenge_route_altitude_start_uses_mission_params():
    from tests.source.conftest import FakeWriter

    manager = ch.ChallengeManager(lambda *a: None)
    manager.apply_mission({
        "name": "Limbo Mission", "challenge_type": "altitude_hold",
        "params": {"mode": "limbo", "ceiling_m": 0.8, "duration_s": 15},
    })
    writer = FakeWriter()
    handled = await ch.handle_challenge_route(writer, "/challenge-altitude-start", "GET", {}, {}, manager)
    assert handled is True
    assert manager.altitude.mode == "limbo"
    assert manager.altitude.ceiling_m == 0.8
    assert writer.json()["mission"] == "Limbo Mission"


@pytest.mark.asyncio
async def test_handle_challenge_route_stop_endpoints():
    from tests.source.conftest import FakeWriter

    manager = ch.ChallengeManager(lambda *a: None)
    manager.touch_and_go.start(0)
    writer = FakeWriter()
    handled = await ch.handle_challenge_route(writer, "/challenge-touchgo-stop", "GET", {}, {}, manager)
    assert handled is True
    assert manager.touch_and_go.active is False


@pytest.mark.asyncio
async def test_handle_challenge_route_missions_list_download_delete_apply():
    from tests.source.conftest import FakeWriter

    manager = ch.ChallengeManager(lambda *a: None)
    ch.save_mission_file("EcoOne", {"name": "EcoOne", "challenge_type": "eco", "description": "d", "params": {}})

    writer = FakeWriter()
    handled = await ch.handle_challenge_route(writer, "/missions-list", "GET", {}, {}, manager)
    assert handled is True
    names = [m["name"] for m in writer.json()["missions"]]
    assert "EcoOne" in names

    writer = FakeWriter()
    handled = await ch.handle_challenge_route(writer, "/mission-download", "GET", {"name": "EcoOne"}, {}, manager)
    assert handled is True
    assert b"Content-Disposition" in writer.response

    writer = FakeWriter()
    handled = await ch.handle_challenge_route(
        writer, "/mission-upload", "POST", {},
        {"name": "Uploaded", "data": json.dumps({"challenge_type": "eco", "params": {}})}, manager,
    )
    assert handled is True
    assert writer.json()["ok"] is True
    assert ch.get_mission_data("Uploaded") is not None

    writer = FakeWriter()
    handled = await ch.handle_challenge_route(writer, "/mission-apply", "GET", {"name": "EcoOne"}, {}, manager)
    assert handled is True
    assert writer.json()["ok"] is True
    assert manager.active_mission["eco"]["name"] == "EcoOne"

    writer = FakeWriter()
    handled = await ch.handle_challenge_route(writer, "/mission-delete", "GET", {"name": "EcoOne"}, {}, manager)
    assert handled is True
    assert writer.json()["ok"] is True
    assert ch.get_mission_data("EcoOne") is None


@pytest.mark.asyncio
async def test_handle_challenge_route_mission_download_not_found():
    from tests.source.conftest import FakeWriter

    manager = ch.ChallengeManager(lambda *a: None)
    writer = FakeWriter()
    handled = await ch.handle_challenge_route(writer, "/mission-download", "GET", {"name": "nope"}, {}, manager)
    assert handled is True
    assert "404" in writer.status_line


@pytest.mark.asyncio
async def test_handle_challenge_route_log_and_clear():
    from tests.source.conftest import FakeWriter

    manager = ch.ChallengeManager(lambda *a: None)
    manager.record_result("x", 10, "ts")

    writer = FakeWriter()
    handled = await ch.handle_challenge_route(writer, "/challenge-log", "GET", {}, {}, manager)
    assert handled is True
    assert len(writer.json()["log"]) == 1

    writer = FakeWriter()
    handled = await ch.handle_challenge_route(writer, "/challenge-log-clear", "GET", {}, {}, manager)
    assert handled is True
    assert manager.log_entries == []


@pytest.mark.asyncio
async def test_handle_challenge_route_unknown_returns_false():
    from tests.source.conftest import FakeWriter

    manager = ch.ChallengeManager(lambda *a: None)
    handled = await ch.handle_challenge_route(FakeWriter(), "/nope", "GET", {}, {}, manager)
    assert handled is False
