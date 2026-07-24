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
import os


def _noop_log(_message):
    pass


# ==================== Missionen (.mission Dateien) ====================
# Eine Mission ist eine kleine, benannte JSON-Datei (<name>.mission), die
# einen Challenge-Typ + dessen Parameter buendelt - Erstellung/Bearbeitung
# passiert im Desktop-Tool mission_builder.py (Repo-Root) bzw. vereinfacht
# ueber /admin-challenges. Missionen sind Nutzerdaten (wie .pro Profile) und
# liegen direkt im Pico-Dateisystem-Root (os.listdir() ohne Pfad). Sie werden
# inzwischen auch automatisch mit ins Firmware-Bundle (firmware.nbo) gepackt
# (siehe build_firmware.py's _resolve_mission_files()), damit lokal im
# missionen/-Ordner erstellte Missionen per OTA-Update mit auf den Pico
# gelangen, OHNE dass main.py/challenge_helpers.py dafuer irgendetwas
# Zusaetzliches tun muessen - die Bundle-Anwendung schreibt jede enthaltene
# Datei ohnehin flach (per Dateiname) ins Root, exakt wie hier erwartet.
MISSION_EXTENSION = ".mission"
MISSION_CHALLENGE_TYPES = ("touch_and_go", "altitude_hold", "eco", "heading_hold", "link_quality", "speed_run", "trick")


def _is_alnum_char(ch):
    """Ersatz fuer str.isalnum() - dieses MicroPython-Build (RPI_PICO_W)
    unterstuetzt die Methode str.isalnum() nicht (fuehrt zu
    'str' object has no attribute 'isalnum' zur Laufzeit), daher reiner
    ASCII-Bereichsvergleich statt der eingebauten Methode."""
    return ('a' <= ch <= 'z') or ('A' <= ch <= 'Z') or ('0' <= ch <= '9')


def _sanitize_mission_name(name):
    """Erlaubt nur unbedenkliche Zeichen und verhindert Pfad-Traversal
    (kein '/', '\\', '..' etc.) - gleiche Regel wie im Desktop-Tool
    mission_builder.py, damit lokale und hochgeladene Namen zusammenpassen."""
    name = str(name or "").strip()
    cleaned = "".join(ch for ch in name if _is_alnum_char(ch) or ch in ("-", "_", " "))
    cleaned = cleaned.strip().replace(" ", "_")
    return cleaned[:40]


def list_mission_files():
    """Liste aller *.mission Dateinamen (ohne Endung) im Pico-Root."""
    missions = []
    try:
        for filename in os.listdir():
            if filename.endswith(MISSION_EXTENSION):
                missions.append(filename[:-len(MISSION_EXTENSION)])
    except Exception:
        pass
    return missions


def get_mission_data(name):
    """Liest eine Mission per Name, gibt dict oder None (nicht gefunden/ungueltig) zurueck."""
    safe_name = _sanitize_mission_name(name)
    if not safe_name:
        return None
    try:
        with open(safe_name + MISSION_EXTENSION, "r") as f:
            data = json.loads(f.read())
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def save_mission_file(name, mission_dict):
    """Speichert eine Mission (dict mit name/challenge_type/description/params)
    atomar (tmp-Datei + rename) als <name>.mission."""
    safe_name = _sanitize_mission_name(name)
    if not safe_name:
        return False, "Ungueltiger Missionsname"
    if not isinstance(mission_dict, dict):
        return False, "Missionsdaten sind kein Objekt"
    if mission_dict.get("challenge_type") not in MISSION_CHALLENGE_TYPES:
        return False, "Unbekannter challenge_type"
    if not isinstance(mission_dict.get("params"), dict):
        return False, "params fehlt oder ist kein Objekt"
    try:
        payload = json.dumps(mission_dict)
        file_path = safe_name + MISSION_EXTENSION
        tmp_path = file_path + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(payload)
        try:
            os.remove(file_path)
        except Exception:
            pass
        os.rename(tmp_path, file_path)
        return True, ""
    except Exception as e:
        return False, str(e)


def delete_mission_file(name):
    safe_name = _sanitize_mission_name(name)
    if not safe_name:
        return False, "Ungueltiger Missionsname"
    try:
        os.remove(safe_name + MISSION_EXTENSION)
        return True, ""
    except Exception as e:
        return False, str(e)


# ==================== Challenge-Verlauf (bestandene Challenges) ====================
# Jede erfolgreich abgeschlossene Challenge (Punkte > 0) wird hier dauerhaft in
# einer Datei protokolliert (gleiches Atomic-Write-Muster wie save_mission_file()/
# main.py's save_highscore()), damit der Verlauf einen Neustart des Pico
# ueberlebt - vorher lebte er nur in detector.challenge_history im RAM.
CHALLENGE_LOG_FILE_PATH = "fpv_challenge_log.json"
CHALLENGE_LOG_MAX_ENTRIES = 100


def load_challenge_log():
    """Liest den gespeicherten Challenge-Verlauf (Liste von dicts), oder []
    falls die Datei fehlt/beschaedigt ist."""
    try:
        with open(CHALLENGE_LOG_FILE_PATH, "r") as f:
            data = json.loads(f.read())
        if isinstance(data, list):
            return data[-CHALLENGE_LOG_MAX_ENTRIES:]
    except Exception:
        pass
    return []


def save_challenge_log(entries):
    """Speichert den Challenge-Verlauf atomar (tmp-Datei + rename), auf die
    letzten CHALLENGE_LOG_MAX_ENTRIES Eintraege begrenzt."""
    trimmed = entries[-CHALLENGE_LOG_MAX_ENTRIES:]
    payload = json.dumps(trimmed)
    try:
        tmp_path = CHALLENGE_LOG_FILE_PATH + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(payload)
        try:
            os.remove(CHALLENGE_LOG_FILE_PATH)
        except Exception:
            pass
        os.rename(tmp_path, CHALLENGE_LOG_FILE_PATH)
        return True, ""
    except Exception as e:
        try:
            with open(CHALLENGE_LOG_FILE_PATH, "w") as f:
                f.write(payload)
            return True, ""
        except Exception as e2:
            return False, f"{e} | fallback={e2}"


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

# ==================== Heading-Hold / Kurs halten ====================
HEADING_DEFAULT_TOLERANCE_DEG = 10.0
HEADING_DEFAULT_TARGET_DURATION_S = 15.0
HEADING_POINTS_PER_SECOND_IN_BAND = 10
HEADING_CHALLENGE_TIMEOUT_MS = 120000  # max. 2 Minuten pro Versuch

# ==================== Signal-Helden / Link-Quality ====================
LINK_DEFAULT_MIN_LQ = 70              # Ziel-Linkqualitaet in % (0-100)
LINK_DEFAULT_TARGET_DURATION_S = 30.0
LINK_POINTS_PER_SECOND_IN_BAND = 6
LINK_CHALLENGE_TIMEOUT_MS = 180000     # max. 3 Minuten pro Versuch

# ==================== Speed-Run / Tempo-Rennen ====================
SPEED_DEFAULT_DURATION_S = 10.0
SPEED_POINTS_PER_KMH = 5.0

TRICK_DEFAULT_TIME_LIMIT_S = 30.0    # Zeitfenster, um den Ziel-Trick zu fliegen
TRICK_DEFAULT_BONUS_POINTS = 50      # Bonus zusaetzlich zu den normalen Trick-Punkten
# Direktions-unabhaengige Namen aller Tricks, die LiveGyroTrickDetector.evaluate_trick()
# in main.py bereits standardmaessig erkennt (Praefix wie "Right"/"Left"/"CW" etc.
# haengt von der Flugrichtung ab und wird beim Vergleich ignoriert - siehe
# TrickChallenge.on_trick_detected()). "Any" akzeptiert jeden erkannten Trick.
TRICK_NAMES = (
    "Any",
    "Barrel Roll",
    "Double Roll",
    "Super Multi-Roll",
    "Juicy Roll Flick",
    "Power Flip",
    "Split-S / Half-Loop",
    "Double Flip",
    "Super Multi-Flip",
    "Juicy Pitch Flick",
    "Matty Flip Combo",
    "Flat Spin 360",
    "Flat Spin 720",
)


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
        self.soft_gyro_max = TOUCHGO_SOFT_GYRO_MAX
        self.hard_gyro_max = TOUCHGO_HARD_GYRO_MAX
        self.base_points = TOUCHGO_BASE_POINTS

    def start(self, now_ms, soft_gyro_max=None, hard_gyro_max=None, base_points=None):
        self.active = True
        self.start_ms = now_ms
        self.was_descending = False
        self.touchdown_candidate_since = None
        self.spike_window = []
        self.last_result = None
        self.soft_gyro_max = float(soft_gyro_max) if soft_gyro_max is not None else TOUCHGO_SOFT_GYRO_MAX
        self.hard_gyro_max = float(hard_gyro_max) if hard_gyro_max is not None else TOUCHGO_HARD_GYRO_MAX
        self.base_points = int(base_points) if base_points is not None else TOUCHGO_BASE_POINTS
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

        if max_spike <= self.soft_gyro_max:
            points = self.base_points
        elif max_spike >= self.hard_gyro_max:
            points = 0
        else:
            span = self.hard_gyro_max - self.soft_gyro_max
            points = round(self.base_points * (self.hard_gyro_max - max_spike) / span)

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
        self.points_base = ECO_POINTS_BASE
        self.points_per_mah = ECO_POINTS_PER_MAH

    def start(self, now_ms, points_base=None, points_per_mah=None):
        self.active = True
        self.start_ms = now_ms
        self.start_capacity_mah = None
        self.current_capacity_mah = None
        self.last_result = None
        self.points_base = float(points_base) if points_base is not None else ECO_POINTS_BASE
        self.points_per_mah = float(points_per_mah) if points_per_mah is not None else ECO_POINTS_PER_MAH
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
            points = max(0, round(self.points_base - consumed * self.points_per_mah))
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


def _heading_diff(a, b):
    """Kleinste Winkeldifferenz zwischen zwei Kompasskursen (0-360 deg),
    korrekt ueber den 0/360-Grad-Uebergang hinweg."""
    d = (a - b + 180.0) % 360.0 - 180.0
    return abs(d)


class HeadingChallenge:
    """Kurs halten: Ziel-Heading wird beim Start gesetzt, Punkte fuer
    zusammenhaengend gehaltene Zeit innerhalb der Toleranz."""

    def __init__(self, add_score, log):
        self._add_score = add_score
        self._log = log
        self.active = False
        self.start_ms = 0
        self.target_heading_deg = None
        self.current_heading_deg = 0.0
        self.tolerance_deg = HEADING_DEFAULT_TOLERANCE_DEG
        self.duration_s = HEADING_DEFAULT_TARGET_DURATION_S
        self.elapsed_in_band_ms = 0
        self.last_tick_ms = None
        self.in_band = False
        self.last_result = None

    def start(self, now_ms, tolerance_deg=None, duration_s=None):
        self.active = True
        self.start_ms = now_ms
        self.target_heading_deg = None
        self.tolerance_deg = max(1.0, float(tolerance_deg)) if tolerance_deg is not None else HEADING_DEFAULT_TOLERANCE_DEG
        self.duration_s = max(1.0, float(duration_s)) if duration_s is not None else HEADING_DEFAULT_TARGET_DURATION_S
        self.elapsed_in_band_ms = 0
        self.last_tick_ms = now_ms
        self.in_band = False
        self.last_result = None
        self._log("[CHALLENGE] Kurs-halten-Challenge gestartet.")

    def cancel(self):
        if self.active:
            self.active = False
            self.last_result = "Abgebrochen"

    def update(self, heading_deg, now_ms):
        if not self.active:
            return
        self.current_heading_deg = heading_deg

        if self.target_heading_deg is None:
            self.target_heading_deg = heading_deg
            self.last_tick_ms = now_ms
            return

        if self.last_tick_ms is None:
            self.last_tick_ms = now_ms
            return

        dt_ms = time.ticks_diff(now_ms, self.last_tick_ms)
        self.last_tick_ms = now_ms
        if 0 < dt_ms <= 500:
            self.in_band = _heading_diff(heading_deg, self.target_heading_deg) <= self.tolerance_deg
            if self.in_band:
                self.elapsed_in_band_ms += dt_ms
                if self.elapsed_in_band_ms / 1000.0 >= self.duration_s:
                    self._finalize(success=True)
                    return
            else:
                self.elapsed_in_band_ms = 0

        if self.active and time.ticks_diff(now_ms, self.start_ms) > HEADING_CHALLENGE_TIMEOUT_MS:
            self._finalize(success=False)

    def _finalize(self, success):
        self.active = False
        if success:
            points = round(HEADING_POINTS_PER_SECOND_IN_BAND * self.duration_s)
            self.last_result = f"Kurs gehalten! (+{points} Pkt, {self.duration_s:.0f}s gehalten)"
            self._add_score(points, f"Kurs halten: {self.duration_s:.0f}s gehalten")
        else:
            self.last_result = "Zeitueberschreitung - Kurs nicht gehalten"

    def status_dict(self):
        return {
            "active": self.active,
            "target_heading_deg": self.target_heading_deg,
            "current_heading_deg": round(self.current_heading_deg, 1),
            "elapsed_in_band_s": round(self.elapsed_in_band_ms / 1000.0, 1),
            "duration_s": self.duration_s,
            "in_band": self.in_band,
            "last_result": self.last_result,
        }


class LinkQualityChallenge:
    """Signal-Helden: Punkte fuer zusammenhaengend gehaltene Zeit mit
    Uplink-Link-Qualitaet (LQ, 0-100%) oberhalb eines Mindestwerts (und
    optional unterhalb eines Maximalwerts, um z.B. ein Signal-Band statt
    nur eine Untergrenze zu erzwingen)."""

    def __init__(self, add_score, log):
        self._add_score = add_score
        self._log = log
        self.active = False
        self.start_ms = 0
        self.min_lq = LINK_DEFAULT_MIN_LQ
        self.max_lq = None
        self.duration_s = LINK_DEFAULT_TARGET_DURATION_S
        self.elapsed_in_band_ms = 0
        self.last_tick_ms = None
        self.current_lq = None
        self.worst_lq = None
        self.in_band = False
        self.last_result = None

    def start(self, now_ms, min_lq=None, max_lq=None, duration_s=None):
        self.active = True
        self.start_ms = now_ms
        self.min_lq = max(1, int(min_lq)) if min_lq is not None else LINK_DEFAULT_MIN_LQ
        self.max_lq = min(100, int(max_lq)) if max_lq is not None and int(max_lq) > 0 else None
        self.duration_s = max(1.0, float(duration_s)) if duration_s is not None else LINK_DEFAULT_TARGET_DURATION_S
        self.elapsed_in_band_ms = 0
        self.last_tick_ms = now_ms
        self.current_lq = None
        self.worst_lq = None
        self.in_band = False
        self.last_result = None
        self._log("[CHALLENGE] Signal-Helden-Challenge gestartet.")

    def cancel(self):
        if self.active:
            self.active = False
            self.last_result = "Abgebrochen"

    def update(self, uplink_lq, now_ms):
        if not self.active:
            return
        self.current_lq = uplink_lq
        self.worst_lq = uplink_lq if self.worst_lq is None else min(self.worst_lq, uplink_lq)

        if self.last_tick_ms is None:
            self.last_tick_ms = now_ms
            return

        dt_ms = time.ticks_diff(now_ms, self.last_tick_ms)
        self.last_tick_ms = now_ms
        if 0 < dt_ms <= 500:
            self.in_band = uplink_lq >= self.min_lq and (self.max_lq is None or uplink_lq <= self.max_lq)
            if self.in_band:
                self.elapsed_in_band_ms += dt_ms
                if self.elapsed_in_band_ms / 1000.0 >= self.duration_s:
                    self._finalize(success=True)
                    return
            else:
                self.elapsed_in_band_ms = 0

        if self.active and time.ticks_diff(now_ms, self.start_ms) > LINK_CHALLENGE_TIMEOUT_MS:
            self._finalize(success=False)

    def _finalize(self, success):
        self.active = False
        band_desc = f"{self.min_lq}-{self.max_lq}% LQ" if self.max_lq is not None else f">= {self.min_lq}% LQ"
        if success:
            points = round(LINK_POINTS_PER_SECOND_IN_BAND * self.duration_s)
            self.last_result = f"Signal gehalten! (+{points} Pkt, {self.duration_s:.0f}s {band_desc})"
            self._add_score(points, f"Signal-Helden: {self.duration_s:.0f}s {band_desc}")
        else:
            self.last_result = "Zeitueberschreitung - Linkqualitaet nicht gehalten"

    def status_dict(self):
        return {
            "active": self.active,
            "min_lq": self.min_lq,
            "max_lq": self.max_lq,
            "current_lq": self.current_lq,
            "worst_lq": self.worst_lq,
            "elapsed_in_band_s": round(self.elapsed_in_band_ms / 1000.0, 1),
            "duration_s": self.duration_s,
            "in_band": self.in_band,
            "last_result": self.last_result,
        }


class SpeedChallenge:
    """Tempo-Rennen: zeitlich begrenzter Sprint, Punkte nach erreichter
    Spitzengeschwindigkeit (GPS-Groundspeed, benoetigt GPS-Modul am FC)."""

    def __init__(self, add_score, log):
        self._add_score = add_score
        self._log = log
        self.active = False
        self.start_ms = 0
        self.duration_s = SPEED_DEFAULT_DURATION_S
        self.points_per_kmh = SPEED_POINTS_PER_KMH
        self.max_speed_kmh = 0.0
        self.current_speed_kmh = 0.0
        self.last_result = None

    def start(self, now_ms, duration_s=None, points_per_kmh=None):
        self.active = True
        self.start_ms = now_ms
        self.duration_s = max(1.0, float(duration_s)) if duration_s is not None else SPEED_DEFAULT_DURATION_S
        self.points_per_kmh = float(points_per_kmh) if points_per_kmh is not None else SPEED_POINTS_PER_KMH
        self.max_speed_kmh = 0.0
        self.current_speed_kmh = 0.0
        self.last_result = None
        self._log("[CHALLENGE] Speed-Run gestartet.")

    def cancel(self):
        if self.active:
            self.active = False
            self.last_result = "Abgebrochen"

    def update(self, speed_kmh, now_ms):
        if not self.active:
            return
        self.current_speed_kmh = speed_kmh
        if speed_kmh > self.max_speed_kmh:
            self.max_speed_kmh = speed_kmh
        if time.ticks_diff(now_ms, self.start_ms) / 1000.0 >= self.duration_s:
            self._finalize()

    def _finalize(self):
        self.active = False
        points = max(0, round(self.max_speed_kmh * self.points_per_kmh))
        self.last_result = f"Speed-Run beendet: {self.max_speed_kmh:.0f} km/h Spitze (+{points} Pkt)"
        if points > 0:
            self._add_score(points, f"Speed-Run: {self.max_speed_kmh:.0f} km/h Spitze")

    def status_dict(self):
        return {
            "active": self.active,
            "max_speed_kmh": round(self.max_speed_kmh, 1),
            "current_speed_kmh": round(self.current_speed_kmh, 1),
            "duration_s": self.duration_s,
            "last_result": self.last_result,
        }


class TrickChallenge:
    """Trick-Challenge: Verlangt, einen bestimmten (oder beliebigen) Trick
    innerhalb eines Zeitfensters zu fliegen. Die Trick-Erkennung selbst laeuft
    unveraendert weiter in main.py's LiveGyroTrickDetector.evaluate_trick()
    (inkl. eigener Punktevergabe) - main.py ruft zusaetzlich fuer JEDEN
    erfolgreich erkannten Trick on_trick_detected() auf, das hier nur prueft,
    ob der Name zum aktuellen Missionsziel passt, und bei Treffer einen Bonus
    vergibt. Richtungs-Praefixe (Right/Left/CW/CCW/Forward/Backward) werden
    beim Abgleich ignoriert, da die Flugrichtung frei waehlbar ist."""

    def __init__(self, add_score, log):
        self._add_score = add_score
        self._log = log
        self.active = False
        self.start_ms = 0
        self.target_name = "Any"
        self.time_limit_s = TRICK_DEFAULT_TIME_LIMIT_S
        self.bonus_points = TRICK_DEFAULT_BONUS_POINTS
        self.last_detected_name = None
        self.last_result = None

    def start(self, now_ms, target_name=None, time_limit_s=None, bonus_points=None):
        self.active = True
        self.start_ms = now_ms
        target_name = str(target_name).strip() if target_name else "Any"
        self.target_name = target_name if target_name else "Any"
        self.time_limit_s = max(3.0, float(time_limit_s)) if time_limit_s is not None else TRICK_DEFAULT_TIME_LIMIT_S
        self.bonus_points = max(1, int(bonus_points)) if bonus_points is not None else TRICK_DEFAULT_BONUS_POINTS
        self.last_detected_name = None
        self.last_result = None
        self._log(f"[CHALLENGE] Trick-Challenge gestartet: Ziel='{self.target_name}'")

    def cancel(self):
        if self.active:
            self.active = False
            self.last_result = "Abgebrochen"

    def on_trick_detected(self, detected_name, now_ms):
        """Wird von main.py fuer JEDEN erfolgreich erkannten Trick aufgerufen
        (Roll/Flip/Spin-Erkennung laeuft unabhaengig von dieser Challenge)."""
        if not self.active:
            return
        self.last_detected_name = detected_name
        if self.target_name == "Any" or self.target_name.lower() in detected_name.lower():
            self._finalize(success=True)

    def update(self, now_ms):
        """Ueberwacht das Zeitfenster - regelmaessig aufrufen (main.py macht
        dies bei jedem Attitude-Frame ueber ChallengeManager.update_attitude())."""
        if self.active and time.ticks_diff(now_ms, self.start_ms) > int(self.time_limit_s * 1000):
            self._finalize(success=False)

    def _finalize(self, success):
        self.active = False
        if success:
            self.last_result = f"Trick geschafft: {self.last_detected_name} (+{self.bonus_points} Bonus-Pkt)"
            self._add_score(self.bonus_points, f"Trick-Challenge: {self.last_detected_name}")
        else:
            self.last_result = f"Zeitueberschreitung - Ziel-Trick '{self.target_name}' nicht geflogen"

    def status_dict(self):
        return {
            "active": self.active,
            "target_name": self.target_name,
            "time_limit_s": self.time_limit_s,
            "bonus_points": self.bonus_points,
            "last_detected_name": self.last_detected_name,
            "last_result": self.last_result,
        }


class ChallengeManager:
    """Buendelt alle Challenges und die daraus abgeleitete relative
    Hoehenschaetzung (Integration der CRSF-Vario-Sinkrate)."""

    def __init__(self, add_score, log=_noop_log):
        self.add_score = add_score
        self.log = log
        self.touch_and_go = TouchAndGoChallenge(add_score, log)
        self.altitude = AltitudeChallenge(add_score, log)
        self.eco = EcoChallenge(add_score, log)
        self.heading = HeadingChallenge(add_score, log)
        self.link_quality = LinkQualityChallenge(add_score, log)
        self.speed_run = SpeedChallenge(add_score, log)
        self.trick = TrickChallenge(add_score, log)

        self._altitude_m = 0.0
        self._last_vario_ms = None
        self._battery_voltage = None
        self._battery_current = None
        self._battery_remaining_pct = None

        # Je Challenge-Typ die zuletzt per /mission-apply aktivierte Mission
        # ({"name": ..., "params": {...}}) - wird beim naechsten Start dieses
        # Typs als Parameter-Quelle genutzt (siehe handle_challenge_route()).
        self.active_mission = {}

        # Dauerhafter Verlauf bestandener Challenges (siehe record_result()),
        # aus fpv_challenge_log.json geladen und bei jedem neuen Eintrag
        # sofort wieder gespeichert.
        self.log_entries = load_challenge_log()

    def record_result(self, description, points, timestamp):
        """Traegt eine bestandene Challenge dauerhaft in den Verlauf ein und
        speichert ihn sofort ab. Wird von main.py's _add_challenge_score()
        fuer jede Punktevergabe aufgerufen."""
        if points <= 0:
            return
        self.log_entries.append({
            "timestamp": timestamp,
            "description": description,
            "points": points,
        })
        if len(self.log_entries) > CHALLENGE_LOG_MAX_ENTRIES:
            del self.log_entries[0: len(self.log_entries) - CHALLENGE_LOG_MAX_ENTRIES]
        save_challenge_log(self.log_entries)

    def clear_log(self):
        self.log_entries = []
        return save_challenge_log(self.log_entries)

    def apply_mission(self, mission_dict):
        """Aktiviert eine geladene Mission fuer ihren challenge_type - wird
        beim naechsten Start dieses Typs als Parametersatz verwendet."""
        if not isinstance(mission_dict, dict):
            return False, "Missionsdaten sind kein Objekt"
        challenge_type = mission_dict.get("challenge_type")
        if challenge_type not in MISSION_CHALLENGE_TYPES:
            return False, "Unbekannter challenge_type"
        params = mission_dict.get("params")
        if not isinstance(params, dict):
            return False, "params fehlt oder ist kein Objekt"
        name = mission_dict.get("name", "?")
        self.active_mission[challenge_type] = {"name": name, "params": params}
        self.log(f"[CHALLENGE] Mission '{name}' fuer {challenge_type} angewendet.")
        return True, ""

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
        self.trick.update(now_ms)

    def update_trick_detected(self, detected_name, now_ms):
        """Wird von main.py's LiveGyroTrickDetector.evaluate_trick() fuer JEDEN
        erfolgreich erkannten Trick aufgerufen (unabhaengig von der normalen
        Punktevergabe dort)."""
        self.trick.on_trick_detected(detected_name, now_ms)

    def update_heading(self, heading_deg, now_ms):
        """Wird pro Attitude-Frame (0x1E) mit dem aktuellen Yaw/Kurs in Grad aufgerufen."""
        self.heading.update(heading_deg, now_ms)

    def update_battery(self, capacity_used_mah, voltage_v, current_a, remaining_pct, now_ms):
        """Wird pro empfangenem CRSF Battery-Sensor-Frame (0x08) aufgerufen."""
        self._battery_voltage = voltage_v
        self._battery_current = current_a
        self._battery_remaining_pct = remaining_pct
        self.eco.update(capacity_used_mah, now_ms)

    def update_link_stats(self, uplink_lq, now_ms):
        """Wird pro empfangenem CRSF Link-Statistics-Frame (0x14) aufgerufen."""
        self.link_quality.update(uplink_lq, now_ms)

    def update_gps(self, speed_kmh, now_ms):
        """Wird pro empfangenem CRSF GPS-Frame (0x02) mit der Groundspeed (km/h) aufgerufen."""
        self.speed_run.update(speed_kmh, now_ms)

    def status_dict(self):
        return {
            "altitude_m": round(self._altitude_m, 2),
            "battery_voltage": self._battery_voltage,
            "battery_current": self._battery_current,
            "battery_remaining_pct": self._battery_remaining_pct,
            "touch_and_go": self.touch_and_go.status_dict(),
            "altitude_challenge": self.altitude.status_dict(),
            "eco": self.eco.status_dict(),
            "heading": self.heading.status_dict(),
            "link_quality": self.link_quality.status_dict(),
            "speed_run": self.speed_run.status_dict(),
            "trick": self.trick.status_dict(),
            "active_mission": {
                "touch_and_go": (self.active_mission.get("touch_and_go") or {}).get("name"),
                "altitude_hold": (self.active_mission.get("altitude_hold") or {}).get("name"),
                "eco": (self.active_mission.get("eco") or {}).get("name"),
                "heading_hold": (self.active_mission.get("heading_hold") or {}).get("name"),
                "link_quality": (self.active_mission.get("link_quality") or {}).get("name"),
                "speed_run": (self.active_mission.get("speed_run") or {}).get("name"),
                "trick": (self.active_mission.get("trick") or {}).get("name"),
            },
            "log": self.log_entries[-20:],
        }


async def _write_json_response(writer, payload_dict, status="200 OK"):
    payload = json.dumps(payload_dict).encode("utf-8")
    writer.write(b"HTTP/1.1 " + status.encode("utf-8") + b"\r\n")
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
        mission = challenges.active_mission.get("touch_and_go")
        params = (mission or {}).get("params", {})
        challenges.touch_and_go.start(
            time.ticks_ms(),
            soft_gyro_max=params.get("soft_gyro_max"),
            hard_gyro_max=params.get("hard_gyro_max"),
            base_points=params.get("base_points"),
        )
        await _write_json_response(writer, {"ok": True, "mission": (mission or {}).get("name")})
        return True

    if request_path == "/challenge-touchgo-stop":
        challenges.touch_and_go.cancel()
        await _write_json_response(writer, {"ok": True})
        return True

    if request_path == "/challenge-altitude-start":
        mission = challenges.active_mission.get("altitude_hold")
        if mission:
            mparams = mission.get("params", {})
            mode = mparams.get("mode", "hold")
            try:
                tolerance = float(mparams.get("tolerance_m", ALT_DEFAULT_TOLERANCE_M))
            except Exception:
                tolerance = ALT_DEFAULT_TOLERANCE_M
            try:
                ceiling = float(mparams.get("ceiling_m", LIMBO_DEFAULT_CEILING_M))
            except Exception:
                ceiling = LIMBO_DEFAULT_CEILING_M
            try:
                duration = float(mparams.get("duration_s", ALT_DEFAULT_TARGET_DURATION_S))
            except Exception:
                duration = ALT_DEFAULT_TARGET_DURATION_S
        else:
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
        await _write_json_response(writer, {"ok": True, "mission": (mission or {}).get("name")})
        return True

    if request_path == "/challenge-altitude-stop":
        challenges.altitude.cancel()
        await _write_json_response(writer, {"ok": True})
        return True

    if request_path == "/challenge-eco-start":
        mission = challenges.active_mission.get("eco")
        params = (mission or {}).get("params", {})
        challenges.eco.start(
            time.ticks_ms(),
            points_base=params.get("points_base"),
            points_per_mah=params.get("points_per_mah"),
        )
        await _write_json_response(writer, {"ok": True, "mission": (mission or {}).get("name")})
        return True

    if request_path == "/challenge-eco-stop":
        result = challenges.eco.stop(time.ticks_ms())
        await _write_json_response(writer, {"ok": True, "result": result})
        return True

    if request_path == "/challenge-heading-start":
        mission = challenges.active_mission.get("heading_hold")
        params = (mission or {}).get("params", {})
        if not mission:
            src = query_params if query_params.get("tolerance") is not None else body_params
            params = {
                "tolerance_deg": src.get("tolerance"),
                "duration_s": src.get("duration"),
            }
        try:
            tolerance = float(params.get("tolerance_deg")) if params.get("tolerance_deg") is not None else None
        except Exception:
            tolerance = None
        try:
            duration = float(params.get("duration_s")) if params.get("duration_s") is not None else None
        except Exception:
            duration = None
        challenges.heading.start(time.ticks_ms(), tolerance_deg=tolerance, duration_s=duration)
        await _write_json_response(writer, {"ok": True, "mission": (mission or {}).get("name")})
        return True

    if request_path == "/challenge-heading-stop":
        challenges.heading.cancel()
        await _write_json_response(writer, {"ok": True})
        return True

    if request_path == "/challenge-link-start":
        mission = challenges.active_mission.get("link_quality")
        params = (mission or {}).get("params", {})
        if not mission:
            src = query_params if query_params.get("min_lq") is not None else body_params
            params = {
                "min_lq": src.get("min_lq"),
                "max_lq": src.get("max_lq"),
                "duration_s": src.get("duration"),
            }
        try:
            min_lq = int(float(params.get("min_lq"))) if params.get("min_lq") is not None else None
        except Exception:
            min_lq = None
        try:
            max_lq = int(float(params.get("max_lq"))) if params.get("max_lq") is not None else None
        except Exception:
            max_lq = None
        try:
            duration = float(params.get("duration_s")) if params.get("duration_s") is not None else None
        except Exception:
            duration = None
        challenges.link_quality.start(time.ticks_ms(), min_lq=min_lq, max_lq=max_lq, duration_s=duration)
        await _write_json_response(writer, {"ok": True, "mission": (mission or {}).get("name")})
        return True

    if request_path == "/challenge-link-stop":
        challenges.link_quality.cancel()
        await _write_json_response(writer, {"ok": True})
        return True

    if request_path == "/challenge-speed-start":
        mission = challenges.active_mission.get("speed_run")
        params = (mission or {}).get("params", {})
        if not mission:
            src = query_params if query_params.get("duration") is not None else body_params
            params = {
                "duration_s": src.get("duration"),
                "points_per_kmh": src.get("points_per_kmh"),
            }
        try:
            duration = float(params.get("duration_s")) if params.get("duration_s") is not None else None
        except Exception:
            duration = None
        try:
            points_per_kmh = float(params.get("points_per_kmh")) if params.get("points_per_kmh") is not None else None
        except Exception:
            points_per_kmh = None
        challenges.speed_run.start(time.ticks_ms(), duration_s=duration, points_per_kmh=points_per_kmh)
        await _write_json_response(writer, {"ok": True, "mission": (mission or {}).get("name")})
        return True

    if request_path == "/challenge-trick-start":
        mission = challenges.active_mission.get("trick")
        params = (mission or {}).get("params", {})
        if not mission:
            src = query_params if query_params.get("target") is not None else body_params
            params = {
                "target_name": src.get("target"),
                "time_limit_s": src.get("time_limit"),
                "bonus_points": src.get("bonus"),
            }
        target_name = params.get("target_name")
        try:
            time_limit = float(params.get("time_limit_s")) if params.get("time_limit_s") is not None else None
        except Exception:
            time_limit = None
        try:
            bonus_points = int(float(params.get("bonus_points"))) if params.get("bonus_points") is not None else None
        except Exception:
            bonus_points = None
        challenges.trick.start(time.ticks_ms(), target_name=target_name, time_limit_s=time_limit, bonus_points=bonus_points)
        await _write_json_response(writer, {"ok": True, "mission": (mission or {}).get("name")})
        return True

    if request_path == "/challenge-trick-stop":
        challenges.trick.cancel()
        await _write_json_response(writer, {"ok": True})
        return True

    if request_path == "/challenge-speed-stop":
        challenges.speed_run.cancel()
        await _write_json_response(writer, {"ok": True})
        return True

    if request_path == "/missions-list":
        missions = []
        for mission_name in list_mission_files():
            data = get_mission_data(mission_name) or {}
            missions.append({
                "name": mission_name,
                "challenge_type": data.get("challenge_type", "?"),
                "description": data.get("description", ""),
            })
        await _write_json_response(writer, {"ok": True, "missions": missions})
        return True

    if request_path == "/mission-download":
        mission_name = query_params.get("name", "").strip()
        data = get_mission_data(mission_name) if mission_name else None
        if data is None:
            await _write_json_response(writer, {"ok": False, "error": "Mission nicht gefunden"}, status="404 Not Found")
            return True
        payload = json.dumps(data).encode("utf-8")
        safe_name = _sanitize_mission_name(mission_name)
        writer.write(b"HTTP/1.1 200 OK\r\n")
        writer.write(b"Content-Type: application/json\r\n")
        writer.write(b'Content-Disposition: attachment; filename="' + safe_name.encode("utf-8") + b'.mission"\r\n')
        writer.write(b"Content-Length: " + str(len(payload)).encode() + b"\r\n")
        writer.write(b"Connection: close\r\n\r\n")
        writer.write(payload)
        return True

    if request_path == "/mission-upload" and request_method == "POST":
        mission_name = body_params.get("name", "").strip()
        data_str = body_params.get("data", "").strip()
        if not mission_name or not data_str:
            await _write_json_response(writer, {"ok": False, "error": "Name oder Daten fehlen"})
            return True
        try:
            mission_dict = json.loads(data_str)
        except Exception as e:
            await _write_json_response(writer, {"ok": False, "error": "Ungueltiges JSON: %s" % e})
            return True
        ok, err = save_mission_file(mission_name, mission_dict)
        await _write_json_response(writer, {"ok": ok, "error": None if ok else err})
        return True

    if request_path == "/mission-delete":
        mission_name = query_params.get("name", "").strip()
        ok, err = delete_mission_file(mission_name)
        await _write_json_response(writer, {"ok": ok, "error": None if ok else err})
        return True

    if request_path == "/mission-apply":
        mission_name = query_params.get("name", "").strip()
        mission_dict = get_mission_data(mission_name) if mission_name else None
        if mission_dict is None:
            await _write_json_response(writer, {"ok": False, "error": "Mission nicht gefunden"}, status="404 Not Found")
            return True
        ok, err = challenges.apply_mission(mission_dict)
        await _write_json_response(writer, {"ok": ok, "error": None if ok else err, "mission": mission_dict})
        return True

    if request_path == "/challenge-log":
        await _write_json_response(writer, {"ok": True, "log": challenges.log_entries})
        return True

    if request_path == "/challenge-log-clear":
        ok, err = challenges.clear_log()
        await _write_json_response(writer, {"ok": ok, "error": None if ok else err})
        return True

    return False
