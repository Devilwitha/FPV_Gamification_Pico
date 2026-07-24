"""
challenge_helpers.py - Real-Time Challenges / Mini-Games fuer FPV_Gamification_Pico.

Ausgelagert (gleiches Muster wie ota_helpers.py/idcard_helpers.py/misc_routes_helpers.py),
damit main.py nicht weiter waechst (siehe ota_helpers.py Docstring: MicroPythons
Compiler/GC fragmentiert bei sehr grossen Einzeldateien den Heap).

Enthaelt drei Mini-Games, die alle PASSIV aus dem bereits vorhandenen CRSF-UART-
Datenstrom gespeist werden (main.py sniff't ohnehin schon TX/RX der ELRS-Strecke
fuer die Attitude-Frames) - es ist KEINE zusaetzliche Hardware/Verkabelung noetig,
solange der Flight Controller die entsprechenden CRSF-Telemetrie-Frames sendet:

  1. Touch & Go / Praezisions-Landung:
     Nutzt CRSF Vario-Frames (0x07, Sinkrate in cm/s). Eine Landung gilt als
     "weich", wenn beim Erreichen von Sinkrate ~0 (Aufsetzen) kein starker
     Gyro-Ausschlag (haerterer Aufprall) gemessen wird.
  2. Altitude-Hold- / Limbo-Challenge:
     Nutzt ebenfalls CRSF Vario-Frames. Die Hoehe wird durch Integration der
     Sinkrate ueber die Zeit geschaetzt (RELATIVE Hoehe ab Challenge-Start,
     KEINE absolute Barometer-Hoehe - CRSF Baro-Altitude-Frames (0x09) haben
     eine komplexere/uneinheitliche Kodierung, die ohne echte Hardware zum
     Testen nicht zuverlaessig zu verifizieren ist. Fuer "Hoehe halten" bzw.
     "moeglichst tief fliegen" reicht eine relative Schaetzung aus).
  3. Energy-Management- / Eco-Challenge:
     Nutzt CRSF Battery-Sensor-Frames (0x08, u.a. verbrauchte Kapazitaet in
     mAh). Punkte gibt es fuer moeglichst geringen mAh-Verbrauch waehrend der
     Challenge-Dauer.

Alle drei Challenges werden manuell ueber HTTP-Routen gestartet/gestoppt
(siehe handle_challenge_route()) und ihr Live-Status wird per /challenges-data
gepollt (siehe admin_challenges.html).

Punkte werden ueber einen callback `add_score(points, description)` vergeben,
den main.py beim Erzeugen von ChallengeManager uebergibt - dadurch braucht
dieses Modul main.py's `detector`/`highscore_data` nicht zu kennen (kein
zirkulaerer Import, gleiches Muster wie ota_helpers.py's `log`-Callback).
"""
import time
import json


def _noop_log(_message):
    pass


# ==================== Touch & Go / Praezisions-Landung ====================
TOUCHGO_DESCEND_MIN_CM_S = 30       # Mindest-Sinkrate (cm/s), damit ueberhaupt "Sinkflug" zaehlt
TOUCHGO_TOUCHDOWN_VARIO_BAND = 15   # |Sinkrate| unterhalb dessen = "am Boden"
TOUCHGO_TOUCHDOWN_HOLD_MS = 300     # so lange muss die Sinkrate im Band bleiben (Rauschen ignorieren)
TOUCHGO_SOFT_GYRO_MAX = 90.0        # deg/s - Ausschlag darunter = "weiche Landung" (volle Punktzahl)
TOUCHGO_HARD_GYRO_MAX = 220.0       # deg/s - Ausschlag darueber = "harte Landung" (0 Punkte)
TOUCHGO_WINDOW_MS = 400             # Fenster um den Aufsetz-Moment, in dem der max. Gyro-Ausschlag gemessen wird
TOUCHGO_BASE_POINTS = 200
TOUCHGO_TIMEOUT_MS = 60000          # Versuch abbrechen, wenn nach dieser Zeit keine Landung erkannt wurde

# ==================== Altitude-Hold / Limbo ====================
ALT_DEFAULT_TOLERANCE_M = 0.5
ALT_DEFAULT_TARGET_DURATION_S = 20.0
ALT_POINTS_PER_SECOND_IN_BAND = 8
LIMBO_DEFAULT_CEILING_M = 1.0
ALT_CHALLENGE_TIMEOUT_MS = 180000   # max. 3 Minuten pro Versuch

# ==================== Energy Management / Eco-Challenge ====================
ECO_POINTS_BASE = 500
ECO_POINTS_PER_MAH = 1.0
ECO_MIN_DURATION_S = 5.0


class TouchAndGoChallenge:
    """Praezisions-Landung: Punkte nur bei weichem Aufsetzen (kein harter Gyro-Ausschlag)."""

    def __init__(self, add_score, log):
        self._add_score = add_score
        self._log = log
        self.active = False
        self.start_ms = 0
        self.was_descending = False
        self.touchdown_candidate_since = None
        self.spike_window = []  # Liste von (ticks_ms, gyro_mag_deg_s)
        self.last_result = None

    def start(self, now_ms):
        self.active = True
        self.start_ms = now_ms
        self.was_descending = False
        self.touchdown_candidate_since = None
        self.spike_window = []
        self.last_result = None
        self._log("[CHALLENGE] Touch & Go gestartet - bitte landen.")

    def cancel(self):
        if self.active:
            self.active = False
            self.last_result = "Abgebrochen"

    def update_vario(self, vario_cm_s, now_ms):
        if not self.active:
            return
        if time.ticks_diff(now_ms, self.start_ms) > TOUCHGO_TIMEOUT_MS:
            self.active = False
            self.last_result = "Zeitueberschreitung - keine Landung erkannt"
            return

        if vario_cm_s <= -TOUCHGO_DESCEND_MIN_CM_S:
            self.was_descending = True

        if abs(vario_cm_s) <= TOUCHGO_TOUCHDOWN_VARIO_BAND:
            if self.touchdown_candidate_since is None:
                self.touchdown_candidate_since = now_ms
            elif time.ticks_diff(now_ms, self.touchdown_candidate_since) >= TOUCHGO_TOUCHDOWN_HOLD_MS:
                self._finalize()
        else:
            self.touchdown_candidate_since = None

    def update_attitude(self, gyro_mag_deg_s, now_ms):
        if not self.active:
            return
        self.spike_window.append((now_ms, gyro_mag_deg_s))
        while self.spike_window and time.ticks_diff(now_ms, self.spike_window[0][0]) > TOUCHGO_WINDOW_MS:
            self.spike_window.pop(0)

    def _finalize(self):
        self.active = False
        if not self.was_descending:
            self.last_result = "Kein Sinkflug erkannt - kein Touch & Go gewertet"
            return

        max_spike = 0.0
        for _ts, mag in self.spike_window:
            if mag > max_spike:
                max_spike = mag

        if max_spike <= TOUCHGO_SOFT_GYRO_MAX:
            points = TOUCHGO_BASE_POINTS
        elif max_spike >= TOUCHGO_HARD_GYRO_MAX:
            points = 0
        else:
            span = TOUCHGO_HARD_GYRO_MAX - TOUCHGO_SOFT_GYRO_MAX
            points = round(TOUCHGO_BASE_POINTS * (TOUCHGO_HARD_GYRO_MAX - max_spike) / span)

        if points > 0:
            self.last_result = f"Weiche Landung! (+{points} Pkt, max. Ausschlag {max_spike:.0f} deg/s)"
            self._add_score(points, f"Touch & Go: weiche Landung (max {max_spike:.0f} deg/s)")
        else:
            self.last_result = f"Harte Landung (max. Ausschlag {max_spike:.0f} deg/s) - keine Punkte"

    def status_dict(self):
        return {
            "active": self.active,
            "was_descending": self.was_descending,
            "last_result": self.last_result,
        }


class AltitudeChallenge:
    """Altitude-Hold ('hold') oder Limbo ('limbo') - beide nutzen dieselbe
    relative, aus der Sinkrate integrierte Hoehenschaetzung."""

    def __init__(self, add_score, log):
        self._add_score = add_score
        self._log = log
        self.active = False
        self.mode = "hold"
        self.target_altitude_m = None
        self.tolerance_m = ALT_DEFAULT_TOLERANCE_M
        self.ceiling_m = LIMBO_DEFAULT_CEILING_M
        self.duration_s = ALT_DEFAULT_TARGET_DURATION_S
        self.elapsed_in_band_ms = 0
        self.last_tick_ms = None
        self.start_ms = 0
        self.last_result = None
        self.current_altitude_m = 0.0
        self.in_band = False

    def start(self, mode, tolerance_m=ALT_DEFAULT_TOLERANCE_M, ceiling_m=LIMBO_DEFAULT_CEILING_M,
              duration_s=ALT_DEFAULT_TARGET_DURATION_S, now_ms=0):
        self.active = True
        self.mode = "limbo" if mode == "limbo" else "hold"
        self.target_altitude_m = None
        self.tolerance_m = max(0.1, tolerance_m)
        self.ceiling_m = ceiling_m
        self.duration_s = max(1.0, duration_s)
        self.elapsed_in_band_ms = 0
        self.last_tick_ms = now_ms
        self.start_ms = now_ms
        self.last_result = None
        self.in_band = False
        self._log(f"[CHALLENGE] Altitude-Challenge gestartet (mode={self.mode})")

    def cancel(self):
        if self.active:
            self.active = False
            self.last_result = "Abgebrochen"

    def update(self, altitude_m, now_ms):
        if not self.active:
            return

        self.current_altitude_m = altitude_m

        if self.mode == "hold" and self.target_altitude_m is None:
            self.target_altitude_m = altitude_m
            self.last_tick_ms = now_ms
            return

        if self.last_tick_ms is None:
            self.last_tick_ms = now_ms
            return

        dt_ms = time.ticks_diff(now_ms, self.last_tick_ms)
        self.last_tick_ms = now_ms
        if dt_ms <= 0 or dt_ms > 500:
            pass
        else:
            if self.mode == "hold":
                deviation = abs(altitude_m - self.target_altitude_m)
                self.in_band = deviation <= self.tolerance_m
            else:
                self.in_band = altitude_m <= self.ceiling_m

            if self.in_band:
                self.elapsed_in_band_ms += dt_ms
                if self.elapsed_in_band_ms / 1000.0 >= self.duration_s:
                    self._finalize(success=True)
                    return
            else:
                self.elapsed_in_band_ms = 0

        if self.active and time.ticks_diff(now_ms, self.start_ms) > ALT_CHALLENGE_TIMEOUT_MS:
            self._finalize(success=False)

    def _finalize(self, success):
        self.active = False
        if success:
            points = round(ALT_POINTS_PER_SECOND_IN_BAND * self.duration_s)
            label = "Limbo" if self.mode == "limbo" else "Altitude-Hold"
            self.last_result = f"{label} geschafft! (+{points} Pkt, {self.duration_s:.0f}s gehalten)"
            self._add_score(points, f"{label}: {self.duration_s:.0f}s gehalten")
        else:
            self.last_result = "Zeitueberschreitung - Ziel nicht erreicht"

    def status_dict(self):
        return {
            "active": self.active,
            "mode": self.mode,
            "target_altitude_m": self.target_altitude_m,
            "current_altitude_m": round(self.current_altitude_m, 2),
            "elapsed_in_band_s": round(self.elapsed_in_band_ms / 1000.0, 1),
            "duration_s": self.duration_s,
            "in_band": self.in_band,
            "last_result": self.last_result,
        }


class EcoChallenge:
    """Energy Management: moeglichst wenig mAh waehrend der Challenge-Dauer verbrauchen."""

    def __init__(self, add_score, log):
        self._add_score = add_score
        self._log = log
        self.active = False
        self.start_ms = 0
        self.start_capacity_mah = None
        self.current_capacity_mah = None
        self.last_result = None

    def start(self, now_ms):
        self.active = True
        self.start_ms = now_ms
        self.start_capacity_mah = None
        self.current_capacity_mah = None
        self.last_result = None
        self._log("[CHALLENGE] Eco-Challenge gestartet.")

    def update(self, capacity_used_mah, now_ms):
        if not self.active:
            return
        if self.start_capacity_mah is None:
            self.start_capacity_mah = capacity_used_mah
        self.current_capacity_mah = capacity_used_mah

    def stop(self, now_ms):
        if not self.active:
            return {"ok": False, "error": "Keine aktive Eco-Challenge"}

        self.active = False
        duration_s = time.ticks_diff(now_ms, self.start_ms) / 1000.0

        if self.start_capacity_mah is None or self.current_capacity_mah is None:
            self.last_result = "Keine Akku-Telemetrie empfangen"
            return {
                "ok": True, "points": 0, "consumed_mah": 0,
                "duration_s": round(duration_s, 1), "message": self.last_result,
            }

        consumed = max(0.0, self.current_capacity_mah - self.start_capacity_mah)
        if duration_s < ECO_MIN_DURATION_S:
            points = 0
            self.last_result = "Zu kurz fuer eine Wertung"
        else:
            points = max(0, round(ECO_POINTS_BASE - consumed * ECO_POINTS_PER_MAH))
            self.last_result = f"Eco-Challenge beendet: {consumed:.0f} mAh verbraucht (+{points} Pkt)"
            if points > 0:
                self._add_score(points, f"Eco-Challenge: {consumed:.0f} mAh in {duration_s:.0f}s")

        return {
            "ok": True,
            "points": points,
            "consumed_mah": round(consumed, 1),
            "duration_s": round(duration_s, 1),
            "message": self.last_result,
        }

    def status_dict(self):
        consumed = None
        if self.start_capacity_mah is not None and self.current_capacity_mah is not None:
            consumed = round(max(0.0, self.current_capacity_mah - self.start_capacity_mah), 1)
        return {
            "active": self.active,
            "consumed_mah": consumed,
            "last_result": self.last_result,
        }


class ChallengeManager:
    """Buendelt alle drei Challenges und die daraus abgeleitete relative
    Hoehenschaetzung (Integration der CRSF-Vario-Sinkrate)."""

    def __init__(self, add_score, log=_noop_log):
        self.add_score = add_score
        self.log = log
        self.touch_and_go = TouchAndGoChallenge(add_score, log)
        self.altitude = AltitudeChallenge(add_score, log)
        self.eco = EcoChallenge(add_score, log)

        self._altitude_m = 0.0
        self._last_vario_ms = None
        self._battery_voltage = None
        self._battery_current = None
        self._battery_remaining_pct = None

    def update_vario(self, vspeed_raw_cm_s, now_ms):
        """Wird pro empfangenem CRSF Vario-Frame (0x07) aufgerufen."""
        if self._last_vario_ms is None:
            self._last_vario_ms = now_ms
            return

        dt_s = time.ticks_diff(now_ms, self._last_vario_ms) / 1000.0
        self._last_vario_ms = now_ms
        if 0 < dt_s <= 1.0:
            self._altitude_m += (vspeed_raw_cm_s / 100.0) * dt_s

        self.touch_and_go.update_vario(vspeed_raw_cm_s, now_ms)
        self.altitude.update(self._altitude_m, now_ms)

    def update_attitude(self, gyro_mag_deg_s, now_ms):
        """Wird pro Attitude-Frame mit dem gefilterten max. Gyro-Ausschlag aufgerufen."""
        self.touch_and_go.update_attitude(gyro_mag_deg_s, now_ms)

    def update_battery(self, capacity_used_mah, voltage_v, current_a, remaining_pct, now_ms):
        """Wird pro empfangenem CRSF Battery-Sensor-Frame (0x08) aufgerufen."""
        self._battery_voltage = voltage_v
        self._battery_current = current_a
        self._battery_remaining_pct = remaining_pct
        self.eco.update(capacity_used_mah, now_ms)

    def status_dict(self):
        return {
            "altitude_m": round(self._altitude_m, 2),
            "battery_voltage": self._battery_voltage,
            "battery_current": self._battery_current,
            "battery_remaining_pct": self._battery_remaining_pct,
            "touch_and_go": self.touch_and_go.status_dict(),
            "altitude_challenge": self.altitude.status_dict(),
            "eco": self.eco.status_dict(),
        }


async def _write_json_response(writer, payload_dict):
    payload = json.dumps(payload_dict).encode("utf-8")
    writer.write(b"HTTP/1.1 200 OK\r\n")
    writer.write(b"Content-Type: application/json\r\n")
    writer.write(b"Cache-Control: no-store, no-cache, must-revalidate\r\n")
    writer.write(b"Pragma: no-cache\r\n")
    writer.write(b"Content-Length: " + str(len(payload)).encode() + b"\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(payload)


async def handle_challenge_route(writer, request_path, request_method, query_params, body_params, challenges):
    """Route-Dispatcher fuer alle /challenge*-Endpunkte. Rueckgabe True, wenn
    die Route bekannt war und behandelt wurde (main.py bricht dann die
    Weiterverarbeitung ab), sonst False."""

    if request_path == "/challenges-data":
        await _write_json_response(writer, {"ok": True, "challenges": challenges.status_dict()})
        return True

    if request_path == "/challenge-touchgo-start":
        challenges.touch_and_go.start(time.ticks_ms())
        await _write_json_response(writer, {"ok": True})
        return True

    if request_path == "/challenge-touchgo-stop":
        challenges.touch_and_go.cancel()
        await _write_json_response(writer, {"ok": True})
        return True

    if request_path == "/challenge-altitude-start":
        params = query_params if query_params.get("mode") is not None else body_params
        mode = params.get("mode", "hold")
        try:
            tolerance = float(params.get("tolerance", str(ALT_DEFAULT_TOLERANCE_M)))
        except Exception:
            tolerance = ALT_DEFAULT_TOLERANCE_M
        try:
            ceiling = float(params.get("ceiling", str(LIMBO_DEFAULT_CEILING_M)))
        except Exception:
            ceiling = LIMBO_DEFAULT_CEILING_M
        try:
            duration = float(params.get("duration", str(ALT_DEFAULT_TARGET_DURATION_S)))
        except Exception:
            duration = ALT_DEFAULT_TARGET_DURATION_S

        challenges.altitude.start(
            mode, tolerance_m=tolerance, ceiling_m=ceiling, duration_s=duration, now_ms=time.ticks_ms()
        )
        await _write_json_response(writer, {"ok": True})
        return True

    if request_path == "/challenge-altitude-stop":
        challenges.altitude.cancel()
        await _write_json_response(writer, {"ok": True})
        return True

    if request_path == "/challenge-eco-start":
        challenges.eco.start(time.ticks_ms())
        await _write_json_response(writer, {"ok": True})
        return True

    if request_path == "/challenge-eco-stop":
        result = challenges.eco.stop(time.ticks_ms())
        await _write_json_response(writer, {"ok": True, "result": result})
        return True

    return False
