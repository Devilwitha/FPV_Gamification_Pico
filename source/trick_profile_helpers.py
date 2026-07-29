# Trick-Tuning-Profile-Verwaltung (eingebaute Profile + custom .pro Dateien) -
# aus main.py ausgelagert, um main.py's eigene Kompiliergroesse beim
# riskanten `import main` in boot.py zu verringern (gleiches Muster/gleicher
# Grund wie ota_helpers.py, siehe dortiger Kommentar). Anders als
# challenge_helpers.py etc. wird dieses Modul bewusst EAGER am main.py-
# Modul-Top-Level importiert (nicht lazy), weil apply_trick_tuning_profile()
# schon vor dem ersten HTTP-Request beim Boot gebraucht wird - der Gewinn
# kommt hier allein daher, dass es ein eigener, kleinerer Kompilierschritt
# ist statt Teil von main.py's einem grossen.
import json
import os

TRICK_SETTINGS_FILE_PATH = "fpv_trick_settings.json"


def _build_trick_tuning_profiles():
    # Schrittweiser Aufbau reduziert Peak-Allocation gegen MemoryError auf Pico.
    profiles = {}

    p = {}
    p["gyro_trick_threshold"] = 160
    p["stable_threshold"] = 58
    p["trick_start_hold_ms"] = 28
    p["stable_hold_ms"] = 120
    p["gyro_deadband"] = 10
    p["gyro_lowpass_alpha"] = 0.24
    p["min_trick_duration"] = 0.10
    p["trick_min_accum_deg"] = 65
    p["trick_spin_min_accum_deg"] = 100
    p["trick_axis_dominance_ratio"] = 1.10
    p["trick_start_type_weight"] = 0.88
    profiles["beginner"] = p

    p = {}
    p["gyro_trick_threshold"] = 205
    p["stable_threshold"] = 70
    p["trick_start_hold_ms"] = 45
    p["stable_hold_ms"] = 150
    p["gyro_deadband"] = 14
    p["gyro_lowpass_alpha"] = 0.28
    p["min_trick_duration"] = 0.14
    p["trick_min_accum_deg"] = 95
    p["trick_spin_min_accum_deg"] = 135
    p["trick_axis_dominance_ratio"] = 1.32
    p["trick_start_type_weight"] = 0.85
    profiles["freestyle"] = p

    p = {}
    p["gyro_trick_threshold"] = 230
    p["stable_threshold"] = 72
    p["trick_start_hold_ms"] = 45
    p["stable_hold_ms"] = 155
    p["gyro_deadband"] = 14
    p["gyro_lowpass_alpha"] = 0.36
    p["min_trick_duration"] = 0.14
    p["trick_min_accum_deg"] = 95
    p["trick_spin_min_accum_deg"] = 145
    p["trick_axis_dominance_ratio"] = 1.26
    p["trick_start_type_weight"] = 0.95
    profiles["aggressive"] = p

    return profiles


TRICK_TUNING_PROFILES = _build_trick_tuning_profiles()
try:
    del _build_trick_tuning_profiles
except Exception:
    pass


def normalize_trick_tuning_profile(profile_name):
    original = str(profile_name).strip()
    normalized = original.lower()
    if normalized == "soft":
        normalized = "beginner"
    elif normalized == "medium":
        normalized = "freestyle"
    if normalized in TRICK_TUNING_PROFILES:
        return normalized

    # Custom .pro Dateien behalten die Original-Gross-/Kleinschreibung.
    # Zuerst Original-Schreibweise pruefen, dann als Fallback lowercase.
    if original:
        try:
            os.stat(original + ".pro")
            return original
        except Exception:
            pass
    if normalized and normalized != original:
        try:
            os.stat(normalized + ".pro")
            return normalized
        except Exception:
            pass
    return "aggressive"


def load_trick_tuning_profile_name():
    """Liefert den gespeicherten (normalisierten) Profilnamen, oder
    'aggressive' als Fallback, wenn noch keiner gespeichert wurde."""
    try:
        with open(TRICK_SETTINGS_FILE_PATH, 'r') as f:
            data = json.loads(f.read())
        return normalize_trick_tuning_profile(data.get("profile", "aggressive"))
    except Exception:
        return "aggressive"


def save_trick_tuning_profile_name(profile_name):
    payload = json.dumps({"profile": normalize_trick_tuning_profile(profile_name)})
    try:
        tmp_path = TRICK_SETTINGS_FILE_PATH + ".tmp"
        with open(tmp_path, 'w') as f:
            f.write(payload)

        try:
            os.remove(TRICK_SETTINGS_FILE_PATH)
        except Exception:
            pass

        os.rename(tmp_path, TRICK_SETTINGS_FILE_PATH)
        return True, ""
    except Exception as e:
        try:
            with open(TRICK_SETTINGS_FILE_PATH, 'w') as f:
                f.write(payload)
            return True, ""
        except Exception as e2:
            return False, f"{e} | fallback={e2}"


def list_profile_files():
    """Liefert Liste aller Profile: eingebaut + custom .pro Dateien"""
    profiles = list(TRICK_TUNING_PROFILES.keys())
    try:
        for filename in os.listdir():
            if filename.endswith(".pro") and filename != "fpv_trick_settings.json":
                profile_name = filename[:-4]  # Entferne .pro
                if profile_name not in profiles:
                    profiles.append(profile_name)
    except Exception:
        pass
    return profiles


def get_profile_data(profile_name, debug_log=None):
    """Hole Profil-Daten: entweder eingebaut oder aus .pro Datei"""
    if profile_name in TRICK_TUNING_PROFILES:
        return TRICK_TUNING_PROFILES[profile_name]
    original = str(profile_name).strip()
    normalized = original.lower()
    if normalized in TRICK_TUNING_PROFILES:
        return TRICK_TUNING_PROFILES[normalized]

    # Custom .pro Dateien behalten die Original-Gross-/Kleinschreibung.
    # Zuerst Original-Schreibweise pruefen, dann als Fallback lowercase.
    candidates = []
    if original:
        candidates.append(original)
    if normalized and normalized != original:
        candidates.append(normalized)

    required = ["gyro_trick_threshold", "stable_threshold", "trick_start_hold_ms",
                "stable_hold_ms", "gyro_deadband", "gyro_lowpass_alpha",
                "min_trick_duration", "trick_min_accum_deg", "trick_spin_min_accum_deg",
                "trick_axis_dominance_ratio", "trick_start_type_weight"]

    for candidate in candidates:
        try:
            file_path = candidate + ".pro"
            with open(file_path, 'r') as f:
                data = json.loads(f.read())
            if "settings" in data and isinstance(data["settings"], dict):
                data = data["settings"]

            missing_key = False
            for key in required:
                if key not in data:
                    if debug_log is not None:
                        debug_log(f"[PROFILE] Schluessel fehlt in {candidate}.pro: {key}")
                    missing_key = True
                    break
            if not missing_key:
                return data
        except Exception:
            continue
    return None


def save_custom_profile(profile_name, profile_data, debug_log=None):
    """Speichere ein Custom-Profil als .pro Datei"""
    if profile_name.lower() in ["beginner", "freestyle", "aggressive"]:
        return False, "Kann nicht ueber eingebaute Profile schreiben"

    try:
        payload = json.dumps(profile_data)
        file_path = profile_name + ".pro"
        tmp_path = file_path + ".tmp"

        with open(tmp_path, 'w') as f:
            f.write(payload)

        try:
            os.remove(file_path)
        except Exception:
            pass

        os.rename(tmp_path, file_path)
        if debug_log is not None:
            debug_log(f"[PROFILE] Custom-Profil gespeichert: {profile_name}")
        return True, ""
    except Exception as e:
        return False, str(e)


def delete_custom_profile(profile_name, debug_log=None):
    """Loesche ein Custom-Profil"""
    if profile_name.lower() in ["beginner", "freestyle", "aggressive"]:
        return False, "Kann nicht ueber eingebaute Profile loeschen"

    try:
        file_path = profile_name + ".pro"
        os.remove(file_path)
        if debug_log is not None:
            debug_log(f"[PROFILE] Custom-Profil geloescht: {profile_name}")
        return True, ""
    except Exception as e:
        return False, str(e)
