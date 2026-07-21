import machine
import time
import struct
import network
import asyncio
import json
import os
import gc

# ==================== CONFIGURATION ====================
ENABLE_HOTSPOT = True        
ENABLE_SERIAL_DEBUG = True   
ENABLE_LIVE_GYRO_DEBUG = True
ENABLE_TRICK_GYRO_IN_TXT_LOG = True
TRICK_TUNING_PROFILE = "aggressive"

AP_SSID = "FPV_Gamification_Pico"
AP_PASSWORD = "drohnenspiel"  
COPTER_NAME = "Orange Bee"
DEFAULT_PILOT_NAME = "Bollshii"

# GP1 (RX) liest passiv mit 420000 Baud
uart = machine.UART(0, baudrate=420000, tx=machine.Pin(0), rx=machine.Pin(1), rxbuf=1024)

# CRSF Konstanten
CRSF_ADDRESS_FLIGHT_CONTROLLER = 0xC8
CRSF_FRAMETYPE_ATTITUDE        = 0x1E  

# CRSF Frame- und Plausibilitaetsgrenzen
CRSF_MAX_FRAME_LEN        = 64
MAX_SAMPLE_DELTA_DEG      = 140
MIN_ACCUM_FOR_TIMEOUT_DEG = 160

# Bewegungsschwellenwerte (Grad/s berechnet aus Winkeln)
GYRO_TRICK_THRESHOLD       = 190
STABLE_THRESHOLD           = 65
TRICK_START_HOLD_MS        = 35
STABLE_HOLD_MS             = 140
TRICK_FORCE_END_MS         = 2200
GYRO_DEADBAND              = 12
GYRO_LOWPASS_ALPHA         = 0.30
MIN_TRICK_DURATION         = 0.12
MAX_TRICK_DURATION         = 2.5
TRICK_MIN_ACCUM_DEG        = 80
TRICK_SPIN_MIN_ACCUM_DEG   = 120
TRICK_AXIS_DOMINANCE_RATIO = 1.18
TRICK_START_TYPE_WEIGHT    = 0.92
DEBUG_LOG_MAX_LINES        = 300
LIVE_LOG_INTERVAL_MS       = 900
LIVE_LOG_DELTA_DEG         = 0.8

DEBUG_LOG_FILE_PATH       = "fpv_debug_session.txt"
DEBUG_LOG_FILE_MAX_BYTES  = 180000
DEBUG_LOG_BOOT_MARKER     = "=== FPV DEBUG SESSION START ===\n"
SESSION_EXPORT_FILE_PATH  = "fpv_arcade_session_export.txt"
DEBUG_EXPORT_FILE_PATH    = "fpv_debug_export.txt"
HIGHSCORE_FILE_PATH       = "fpv_highscore.json"
TRICK_SETTINGS_FILE_PATH  = "fpv_trick_settings.json"
LED_BLINK_INTERVAL_MS     = 220
OTA_LED_BLINK_INTERVAL_MS = 90
# =======================================================

# CRSF attitude payload ist in Radiant * 10000 kodiert.
CRSF_RAD1E4_TO_DEG = 180.0 / (3.141592653589793 * 10000.0)

debug_log_history = []
debug_log_file_enabled = True
debug_log_file_bytes = 0
debug_log_file_limit_reached = False
highscore_data = {"score": 0, "timestamp": "Unbekannt", "player": DEFAULT_PILOT_NAME}
pending_highscore = {"active": False, "score": 0, "timestamp": "Unbekannt"}
status_led = None
status_led_available = False
status_led_state = False
status_led_last_toggle_ms = 0
system_ready = False
ota_update_active = False
ota_led_cycle_start_ms = 0

# ==================== OTA CHUNK STORAGE ====================
ota_chunks = {}  # { "chunk_index": "base64_data", ... }
ota_total_chunks = 0
ota_received_chunks = 0
ota_target_file = "main.py"
OTA_STAGING_PATH = "ota_staging.tmp"
# Nur diese Dateien duerfen per OTA ueberschrieben werden (kein Path-Traversal,
# keine beliebigen Dateinamen vom Client).
OTA_ALLOWED_TARGETS = (
    "main.py", "index.html",
    "admin_dashboard.html", "admin_update.html", "admin_simulate.html",
    "admin_profiles.html", "admin_system.html",
)
# Spezial-Ziel: ein Firmware-Bundle (siehe build_firmware.py), das mehrere
# der obigen Dateien in einem Rutsch aktualisiert. Wird in /finalize-upload
# gesondert behandelt (entpackt statt direkt umbenannt).
OTA_BUNDLE_TARGET = "firmware.nbo"
OTA_BUNDLE_MAGIC = b"FPVBNDL1"

TRICK_TUNING_PROFILES = {
    "beginner": {
        "gyro_trick_threshold": 160,
        "stable_threshold": 58,
        "trick_start_hold_ms": 28,
        "stable_hold_ms": 120,
        "gyro_deadband": 10,
        "gyro_lowpass_alpha": 0.24,
        "min_trick_duration": 0.10,
        "trick_min_accum_deg": 65,
        "trick_spin_min_accum_deg": 100,
        "trick_axis_dominance_ratio": 1.10,
        "trick_start_type_weight": 0.88,
    },
    "freestyle": {
        "gyro_trick_threshold": 205,
        "stable_threshold": 70,
        "trick_start_hold_ms": 45,
        "stable_hold_ms": 150,
        "gyro_deadband": 14,
        "gyro_lowpass_alpha": 0.28,
        "min_trick_duration": 0.14,
        "trick_min_accum_deg": 95,
        "trick_spin_min_accum_deg": 135,
        "trick_axis_dominance_ratio": 1.32,
        "trick_start_type_weight": 0.85,
    },
    "aggressive": {
        "gyro_trick_threshold": 230,
        "stable_threshold": 72,
        "trick_start_hold_ms": 45,
        "stable_hold_ms": 155,
        "gyro_deadband": 14,
        "gyro_lowpass_alpha": 0.36,
        "min_trick_duration": 0.14,
        "trick_min_accum_deg": 95,
        "trick_spin_min_accum_deg": 145,
        "trick_axis_dominance_ratio": 1.26,
        "trick_start_type_weight": 0.95,
    },
}


def apply_trick_tuning_profile():
    global TRICK_TUNING_PROFILE
    global GYRO_TRICK_THRESHOLD, STABLE_THRESHOLD, TRICK_START_HOLD_MS
    global STABLE_HOLD_MS, GYRO_DEADBAND, GYRO_LOWPASS_ALPHA
    global MIN_TRICK_DURATION, TRICK_MIN_ACCUM_DEG, TRICK_SPIN_MIN_ACCUM_DEG
    global TRICK_AXIS_DOMINANCE_RATIO, TRICK_START_TYPE_WEIGHT

    profile_name = normalize_trick_tuning_profile(TRICK_TUNING_PROFILE)
    if profile_name in TRICK_TUNING_PROFILES:
        profile = TRICK_TUNING_PROFILES[profile_name]
    else:
        profile = get_profile_data(profile_name)
        if profile is None:
            profile_name = "aggressive"
            profile = TRICK_TUNING_PROFILES[profile_name]

    GYRO_TRICK_THRESHOLD = profile["gyro_trick_threshold"]
    STABLE_THRESHOLD = profile["stable_threshold"]
    TRICK_START_HOLD_MS = profile["trick_start_hold_ms"]
    STABLE_HOLD_MS = profile["stable_hold_ms"]
    GYRO_DEADBAND = profile["gyro_deadband"]
    GYRO_LOWPASS_ALPHA = profile["gyro_lowpass_alpha"]
    MIN_TRICK_DURATION = profile["min_trick_duration"]
    TRICK_MIN_ACCUM_DEG = profile["trick_min_accum_deg"]
    TRICK_SPIN_MIN_ACCUM_DEG = profile["trick_spin_min_accum_deg"]
    TRICK_AXIS_DOMINANCE_RATIO = profile["trick_axis_dominance_ratio"]
    TRICK_START_TYPE_WEIGHT = profile["trick_start_type_weight"]
    TRICK_TUNING_PROFILE = profile_name

    debug_log(f"[TRICK PROFILE] Aktiv: {profile_name}")


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


def load_trick_tuning_profile():
    global TRICK_TUNING_PROFILE
    try:
        with open(TRICK_SETTINGS_FILE_PATH, 'r') as f:
            data = json.loads(f.read())
        TRICK_TUNING_PROFILE = normalize_trick_tuning_profile(data.get("profile", "aggressive"))
    except Exception:
        TRICK_TUNING_PROFILE = "aggressive"


def save_trick_tuning_profile():
    payload = json.dumps({"profile": normalize_trick_tuning_profile(TRICK_TUNING_PROFILE)})
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


def get_profile_data(profile_name):
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
                    debug_log(f"[PROFILE] Schluessel fehlt in {candidate}.pro: {key}")
                    missing_key = True
                    break
            if not missing_key:
                return data
        except Exception:
            continue
    return None


def save_custom_profile(profile_name, profile_data):
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
        debug_log(f"[PROFILE] Custom-Profil gespeichert: {profile_name}")
        return True, ""
    except Exception as e:
        return False, str(e)


def delete_custom_profile(profile_name):
    """Loesche ein Custom-Profil"""
    if profile_name.lower() in ["beginner", "freestyle", "aggressive"]:
        return False, "Kann nicht ueber eingebaute Profile loeschen"
    
    try:
        file_path = profile_name + ".pro"
        os.remove(file_path)
        debug_log(f"[PROFILE] Custom-Profil geloescht: {profile_name}")
        return True, ""
    except Exception as e:
        return False, str(e)


def init_status_led():
    global status_led, status_led_available
    try:
        status_led = machine.Pin("LED", machine.Pin.OUT)
        status_led_available = True
        return
    except Exception:
        status_led = None

    try:
        status_led = machine.Pin(25, machine.Pin.OUT)
        status_led_available = True
    except Exception:
        status_led = None
        status_led_available = False


def _set_status_led(on):
    global status_led_state
    if not status_led_available or status_led is None:
        return
    status_led_state = bool(on)
    status_led.value(1 if on else 0)


def update_status_led():
    global status_led_last_toggle_ms, ota_led_cycle_start_ms
    if not status_led_available:
        return

    if not system_ready:
        _set_status_led(False)
        return

    if ota_update_active:
        now = time.ticks_ms()
        if ota_led_cycle_start_ms == 0:
            ota_led_cycle_start_ms = now
            _set_status_led(True)
        elif time.ticks_diff(now, ota_led_cycle_start_ms) >= OTA_LED_BLINK_INTERVAL_MS:
            _set_status_led(not status_led_state)
            ota_led_cycle_start_ms = now
        return
    else:
        ota_led_cycle_start_ms = 0

    if pending_highscore["active"]:
        now = time.ticks_ms()
        if time.ticks_diff(now, status_led_last_toggle_ms) >= LED_BLINK_INTERVAL_MS:
            _set_status_led(not status_led_state)
            status_led_last_toggle_ms = now
    else:
        _set_status_led(True)


def store_debug_entry(entry, line):
    debug_log_history.append(entry)
    if len(debug_log_history) > DEBUG_LOG_MAX_LINES:
        debug_log_history.pop(0)
    append_debug_file_line(line)


def init_debug_log_file():
    global debug_log_file_enabled, debug_log_file_bytes, debug_log_file_limit_reached
    debug_log_file_limit_reached = False
    try:
        with open(DEBUG_LOG_FILE_PATH, 'w') as f:
            f.write(DEBUG_LOG_BOOT_MARKER)
        debug_log_file_bytes = os.stat(DEBUG_LOG_FILE_PATH)[6]
    except Exception:
        debug_log_file_enabled = False


def append_debug_file_line(line):
    global debug_log_file_bytes, debug_log_file_limit_reached, debug_log_file_enabled

    if (not debug_log_file_enabled) or debug_log_file_limit_reached:
        return

    text = line + "\n"
    line_size = len(text)

    if debug_log_file_bytes + line_size > DEBUG_LOG_FILE_MAX_BYTES:
        try:
            marker = "[DEBUG] [LOG LIMIT] Debug-Log Datei-Limit erreicht. Weitere Eintraege werden nur live angezeigt.\n"
            marker_size = len(marker)
            if debug_log_file_bytes + marker_size <= DEBUG_LOG_FILE_MAX_BYTES:
                with open(DEBUG_LOG_FILE_PATH, 'a') as f:
                    f.write(marker)
                debug_log_file_bytes += marker_size
        except Exception:
            pass
        debug_log_file_limit_reached = True
        return

    try:
        with open(DEBUG_LOG_FILE_PATH, 'a') as f:
            f.write(text)
        debug_log_file_bytes += line_size
    except Exception:
        debug_log_file_enabled = False


def debug_log(message):
    entry = f"[{time.ticks_ms() // 1000}s] {message}"
    line = f"[DEBUG] {entry}"
    store_debug_entry(entry, line)
    if ENABLE_SERIAL_DEBUG:
        print(line)


def debug_console_only(message):
    if not ENABLE_SERIAL_DEBUG:
        return
    entry = f"[{time.ticks_ms() // 1000}s] {message}"
    line = f"[DEBUG] {entry}"
    store_debug_entry(entry, line)
    print(line)


def debug_http_console_only(message):
    """Wie debug_console_only(), aber OHNE store_debug_entry(): landet NIE in
    debug_log_history / fpv_debug_session.txt bzw. dem Debug-TXT-Download,
    sondern erscheint ausschliesslich live in Thonny/Seriell. Fuer haeufige
    HTTP-Ereignisse (Polling von /data, /system-info, Body-Parsing etc.),
    damit die Debug-Datei nicht mit Request-Rauschen zugemuellt wird."""
    if not ENABLE_SERIAL_DEBUG:
        return
    print(f"[DEBUG] [{time.ticks_ms() // 1000}s] {message}")


def debug_live_gyro_trick(message):
    entry = f"[{time.ticks_ms() // 1000}s] {message}"
    line = f"[DEBUG] {entry}"

    if ENABLE_SERIAL_DEBUG:
        print(line)
    if ENABLE_TRICK_GYRO_IN_TXT_LOG:
        store_debug_entry(entry, line)


def crc8_dvb_s2(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0xD5) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def normalize_angle_deg(angle):
    while angle > 180.0:
        angle -= 360.0
    while angle < -180.0:
        angle += 360.0
    return angle


def url_decode(value):
    value = value.replace('+', ' ')
    out = ""
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == '%' and i + 2 < len(value):
            hex_part = value[i + 1:i + 3]
            try:
                out += chr(int(hex_part, 16))
                i += 3
                continue
            except Exception:
                pass
        out += ch
        i += 1
    return out


def parse_query(query_string):
    params = {}
    if not query_string:
        return params
    pairs = query_string.split('&')
    for pair in pairs:
        if not pair:
            continue
        if '=' in pair:
            key, value = pair.split('=', 1)
        else:
            key, value = pair, ''
        params[url_decode(key)] = url_decode(value)
    return params


def html_escape(value):
    text = str(value)
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    text = text.replace('"', '&quot;')
    return text


def get_datetime_string():
    now = time.localtime()
    year = now[0]
    month = now[1]
    day = now[2]
    hour = now[3]
    minute = now[4]
    second = now[5]
    return f"{day:02d}.{month:02d}.{year:04d} {hour:02d}:{minute:02d}:{second:02d}"


def base64_decode(s):
    import base64
    try:
        return base64.b2a_base64(base64.a2b_base64(s + b'==')).decode('utf-8').strip()
    except Exception:
        return None


def safe_base64_decode_to_file(b64_string, output_file):
    try:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
        with open(output_file, 'wb') as f:
            chunk_size = 512
            for chunk_start in range(0, len(b64_string), chunk_size):
                chunk = b64_string[chunk_start:chunk_start + chunk_size]
                chunk_result = bytearray()
                padding = (4 - len(chunk) % 4) % 4
                chunk_padded = chunk + "=" * padding
                
                for i in range(0, len(chunk_padded), 4):
                    group = chunk_padded[i:i+4]
                    if len(group) < 4:
                        continue
                    
                    nums = []
                    for c in group:
                        idx = alphabet.find(c)
                        nums.append(idx if idx >= 0 else 0)
                    
                    b1 = (nums[0] << 2) | (nums[1] >> 4)
                    b2 = ((nums[1] & 0xF) << 4) | (nums[2] >> 2)
                    b3 = ((nums[2] & 0x3) << 6) | nums[3]
                    
                    chunk_result.append(b1)
                    if group[2] != '=':
                        chunk_result.append(b2)
                    if group[3] != '=':
                        chunk_result.append(b3)
                
                f.write(chunk_result)
        return True
    except Exception as e:
        debug_log(f"[BASE64-FILE] Fehler: {e}")
        return False


def safe_base64_file_to_file(input_file, output_file):
    try:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
        carry = ""

        with open(input_file, 'r') as fin:
            with open(output_file, 'wb') as fout:
                while True:
                    chunk = fin.read(512)
                    if not chunk:
                        break

                    data = carry + chunk
                    usable_len = (len(data) // 4) * 4
                    to_decode = data[:usable_len]
                    carry = data[usable_len:]

                    out_bytes = bytearray()
                    for i in range(0, len(to_decode), 4):
                        group = to_decode[i:i+4]
                        if len(group) < 4:
                            continue

                        nums = []
                        for c in group:
                            idx = alphabet.find(c)
                            nums.append(idx if idx >= 0 else 0)

                        b1 = (nums[0] << 2) | (nums[1] >> 4)
                        b2 = ((nums[1] & 0xF) << 4) | (nums[2] >> 2)
                        b3 = ((nums[2] & 0x3) << 6) | nums[3]

                        out_bytes.append(b1)
                        if group[2] != '=':
                            out_bytes.append(b2)
                        if group[3] != '=':
                            out_bytes.append(b3)

                    if out_bytes:
                        fout.write(out_bytes)

                if carry:
                    padding = (4 - len(carry) % 4) % 4
                    group = carry + ("=" * padding)
                    out_bytes = bytearray()
                    for i in range(0, len(group), 4):
                        g = group[i:i+4]
                        if len(g) < 4:
                            continue

                        nums = []
                        for c in g:
                            idx = alphabet.find(c)
                            nums.append(idx if idx >= 0 else 0)

                        b1 = (nums[0] << 2) | (nums[1] >> 4)
                        b2 = ((nums[1] & 0xF) << 4) | (nums[2] >> 2)
                        b3 = ((nums[2] & 0x3) << 6) | nums[3]

                        out_bytes.append(b1)
                        if g[2] != '=':
                            out_bytes.append(b2)
                        if g[3] != '=':
                            out_bytes.append(b3)

                    if out_bytes:
                        fout.write(out_bytes)
        return True
    except Exception as e:
        debug_log(f"[BASE64-FILE-STREAM] Fehler: {e}")
        return False


def read_exact(f, n):
    """Liest exakt n Bytes aus einer binaer geoeffneten Datei (oder weniger
    bei EOF). Noetig, weil f.read(n) theoretisch weniger als n Bytes liefern
    kann, auch wenn noch nicht das Dateiende erreicht ist."""
    data = bytearray()
    while len(data) < n:
        chunk = f.read(n - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def apply_firmware_bundle(bundle_path):
    """Entpackt ein per build_firmware.py erzeugtes Firmware-Bundle
    (firmware.nbo) und ersetzt jede enthaltene Datei einzeln auf dem
    Pico-Dateisystem (mit Backup, wie beim Einzeldatei-OTA-Update).
    Jeder Dateiname im Bundle wird gegen OTA_ALLOWED_TARGETS geprueft,
    bevor irgendetwas geschrieben wird (kein beliebiges Ueberschreiben
    von Dateien moeglich)."""
    extracted_files = []
    with open(bundle_path, 'rb') as f:
        magic = read_exact(f, len(OTA_BUNDLE_MAGIC))
        if magic != OTA_BUNDLE_MAGIC:
            raise Exception("Ungueltiges Firmware-Bundle (Magic-Header falsch)")

        count_bytes = read_exact(f, 4)
        if len(count_bytes) < 4:
            raise Exception("Bundle beschaedigt (Dateianzahl fehlt)")
        (file_count,) = struct.unpack('>I', count_bytes)

        for _ in range(file_count):
            name_len_bytes = read_exact(f, 4)
            if len(name_len_bytes) < 4:
                raise Exception("Bundle beschaedigt (Namenslaenge fehlt)")
            (name_len,) = struct.unpack('>I', name_len_bytes)

            name_bytes = read_exact(f, name_len)
            if len(name_bytes) < name_len:
                raise Exception("Bundle beschaedigt (Dateiname unvollstaendig)")
            filename = name_bytes.decode('utf-8')

            content_len_bytes = read_exact(f, 4)
            if len(content_len_bytes) < 4:
                raise Exception(f"Bundle beschaedigt (Inhaltslaenge fehlt: {filename})")
            (content_len,) = struct.unpack('>I', content_len_bytes)

            if filename not in OTA_ALLOWED_TARGETS:
                raise Exception(f"Datei im Bundle nicht erlaubt: {filename}")

            tmp_name = filename + ".bndl_tmp"
            remaining = content_len
            with open(tmp_name, 'wb') as out:
                while remaining > 0:
                    chunk = f.read(min(512, remaining))
                    if not chunk:
                        raise Exception(f"Bundle beschaedigt (Inhalt unvollstaendig: {filename})")
                    out.write(chunk)
                    remaining -= len(chunk)

            backup_path = "main_backup.py" if filename == "main.py" else (filename + ".bak")
            try:
                with open(filename, 'r') as old_f:
                    old_content = old_f.read()
                with open(backup_path, 'w') as bk:
                    bk.write(old_content)
            except Exception as e:
                debug_log(f"[OTA BUNDLE] Backup-Fehler ({filename}): {e}")

            try:
                os.remove(filename)
            except Exception:
                pass
            os.rename(tmp_name, filename)

            extracted_files.append(filename)
            debug_log(f"[OTA BUNDLE] Datei ersetzt: {filename} ({content_len} bytes)")

    needs_restart = "main.py" in extracted_files
    return extracted_files, needs_restart


def build_session_txt_content():
    txt_content = "========================================\n"
    txt_content += f"   {COPTER_NAME.upper()} ARCADE SESSION\n"
    txt_content += "========================================\n\n"
    txt_content += "GELANDETE TRICKS:\n"

    if detector.trick_history:
        for trick in detector.trick_history:
            txt_content += f"- {trick}\n"
    else:
        txt_content += "- Keine Tricks aufgezeichnet -\n"

    txt_content += "\n----------------------------------------\n"
    txt_content += f"GESAMT-PUNKTESTAND: {detector.score} PKT\n"
    txt_content += f"HIGHSCORE: {highscore_data['score']} PKT\n"
    txt_content += f"HIGHSCORE DATUM/ZEIT: {highscore_data['timestamp']}\n"
    txt_content += f"HIGHSCORE PILOT: {highscore_data.get('player', DEFAULT_PILOT_NAME)}\n"
    txt_content += "----------------------------------------\n"
    return txt_content


def build_debug_export_file():
    """Schreibt das Debug-Export direkt in eine Datei, ohne den kompletten
    Log-Inhalt gleichzeitig als RAM-String zu halten (verhindert MemoryError
    bei grossen Logs, z.B. nach vielen erkannten Tricks)."""
    with open(DEBUG_EXPORT_FILE_PATH, 'w') as out:
        out.write("========================================\n")
        out.write(f"      {COPTER_NAME.upper()} DEBUG LOG\n")
        out.write("========================================\n\n")

        file_loaded = False
        try:
            file_size = os.stat(DEBUG_LOG_FILE_PATH)[6]
            if file_size > len(DEBUG_LOG_BOOT_MARKER):
                with open(DEBUG_LOG_FILE_PATH, 'r') as f:
                    while True:
                        chunk = f.read(512)
                        if not chunk:
                            break
                        out.write(chunk)
                file_loaded = True
        except Exception:
            file_loaded = False

        if not file_loaded:
            if debug_log_history:
                out.write("\n".join(debug_log_history))
                out.write("\n")
            else:
                out.write("- Keine Debug-Logs vorhanden -\n")


def write_text_file(path, content):
    with open(path, 'w') as f:
        f.write(content)


async def send_file_as_download(writer, file_path, download_name):
    file_size = os.stat(file_path)[6]
    writer.write(b'HTTP/1.1 200 OK\r\n')
    writer.write(b'Content-Type: application/octet-stream\r\n')
    writer.write(b'Content-Disposition: attachment; filename="' + download_name.encode('utf-8') + b'"\r\n')
    writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
    writer.write(b'Pragma: no-cache\r\n')
    writer.write(b'Content-Length: ' + str(file_size).encode() + b'\r\n')
    writer.write(b'Connection: close\r\n\r\n')

    with open(file_path, 'rb') as f:
        chunk_count = 0
        while True:
            chunk = f.read(512)
            if not chunk:
                break
            writer.write(chunk)
            chunk_count += 1
            if chunk_count % 4 == 0:
                await writer.drain()
    await writer.drain()


def load_highscore():
    global highscore_data
    try:
        with open(HIGHSCORE_FILE_PATH, 'r') as f:
            data = json.loads(f.read())

        score = int(data.get("score", 0))
        timestamp = str(data.get("timestamp", "Unbekannt"))
        player = str(data.get("player", DEFAULT_PILOT_NAME))
        highscore_data = {"score": score, "timestamp": timestamp, "player": player}
    except Exception:
        highscore_data = {"score": 0, "timestamp": "Unbekannt", "player": DEFAULT_PILOT_NAME}


def save_highscore():
    payload = json.dumps(highscore_data)
    try:
        tmp_path = HIGHSCORE_FILE_PATH + ".tmp"
        with open(tmp_path, 'w') as f:
            f.write(payload)

        try:
            os.remove(HIGHSCORE_FILE_PATH)
        except Exception:
            pass

        os.rename(tmp_path, HIGHSCORE_FILE_PATH)
        return True, ""
    except Exception as e:
        try:
            with open(HIGHSCORE_FILE_PATH, 'w') as f:
                f.write(payload)
            return True, ""
        except Exception as e2:
            return False, f"{e} | fallback={e2}"


init_debug_log_file()
load_highscore()
init_status_led()
load_trick_tuning_profile()
apply_trick_tuning_profile()


def start_access_point():
    ap = network.WLAN(network.AP_IF)
    try:
        if ENABLE_SERIAL_DEBUG:
            print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [AP] Aktiviere Access Point")
        ap.active(True)
        time.sleep_ms(200)

        if ENABLE_SERIAL_DEBUG:
            print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [AP] Setze SSID")
        ssid_set = False
        for attempt in range(3):
            try:
                ap.config(essid=AP_SSID)
                ssid_set = True
                break
            except Exception as ssid_error:
                debug_log(f"[AP WARN] SSID-Setzen fehlgeschlagen (Versuch {attempt + 1}/3): {ssid_error}")
                time.sleep_ms(120)
        if not ssid_set:
            debug_log("[AP WARN] SSID konnte nicht gesetzt werden, AP laeuft mit Standardnamen weiter.")

        if AP_PASSWORD and len(AP_PASSWORD) >= 8:
            try:
                if ENABLE_SERIAL_DEBUG:
                    print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [AP] Setze Passwort")
                ap.config(password=AP_PASSWORD)
            except Exception as pw_error:
                debug_log(f"[AP WARN] Passwort-Konfiguration nicht verfuegbar, starte offenes WLAN: {pw_error}")
        elif ENABLE_SERIAL_DEBUG:
            print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [AP] Kein Passwort gesetzt, offenes WLAN")

        if ENABLE_SERIAL_DEBUG:
            print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [AP] Setze Power-Management")
        ap.config(pm=0xa11140)

        if ENABLE_SERIAL_DEBUG:
            print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [AP] Setze statische IP")
        ap.ifconfig(('192.168.4.1', '255.255.255.0', '192.168.4.1', '192.168.4.1'))

        debug_log("WLAN-Hotspot erfolgreich gestartet!")
        debug_log(f"SSID: {AP_SSID}")
        debug_log(f"Pico IP-Adresse (Im Browser eingeben): {ap.ifconfig()[0]}")
    except Exception as e:
        debug_log(f"[AP ERROR] Hotspot-Setup fehlgeschlagen: {e}")


# ==================== WEBSERVER TEMPLATE ====================
class LiveGyroTrickDetector:
    def __init__(self):
        self.score = 0
        self.in_trick = False
        self.trick_start_time = 0.0
        self.trick_type = None
        self.last_trick_name = "Keiner"
        self.trick_history = []
        
        self.accumulated_roll = 0.0
        self.accumulated_pitch = 0.0
        self.accumulated_yaw = 0.0
        self.accumulated_roll_signed = 0.0
        self.accumulated_pitch_signed = 0.0
        self.accumulated_yaw_signed = 0.0
        
        self.last_roll = 0.0
        self.last_pitch = 0.0
        self.last_yaw = 0.0
        self.last_time = time.ticks_ms()

        self.f_gyro_x = 0.0
        self.f_gyro_y = 0.0
        self.f_gyro_z = 0.0
        self.high_rate_since = None
        self.stable_since = None

    def _apply_deadband(self, value, deadband):
        if abs(value) < deadband:
            return 0.0
        return value

    def _start_trick(self, abs_gx, abs_gy, abs_gz):
        self.in_trick = True
        self.trick_start_time = time.ticks_ms()
        self.accumulated_roll = 0.0
        self.accumulated_pitch = 0.0
        self.accumulated_yaw = 0.0
        self.accumulated_roll_signed = 0.0
        self.accumulated_pitch_signed = 0.0
        self.accumulated_yaw_signed = 0.0
        self.stable_since = None

        if abs_gx > abs_gy and abs_gx > abs_gz:
            self.trick_type = "Roll"
        elif abs_gy > abs_gx and abs_gy > abs_gz:
            self.trick_type = "Flip"
        else:
            self.trick_type = "Spin"

        if ENABLE_SERIAL_DEBUG:
            debug_log(f"Trick gestartet: {self.trick_type} | max-rate={max(abs_gx, abs_gy, abs_gz):.0f} deg/s")

    def _finish_trick(self, force=False):
        duration = (time.ticks_diff(time.ticks_ms(), self.trick_start_time)) / 1000.0
        total_accum = self.accumulated_roll + self.accumulated_pitch + self.accumulated_yaw

        if force and total_accum < MIN_ACCUM_FOR_TIMEOUT_DEG:
            self.in_trick = False
            self.trick_type = None
            self.high_rate_since = None
            self.stable_since = None
            return

        if MIN_TRICK_DURATION <= duration <= MAX_TRICK_DURATION:
            self.evaluate_trick(duration)
            if ENABLE_SERIAL_DEBUG:
                debug_log(
                    f"Trick beendet: {self.trick_type} | Dauer={duration:.2f}s | "
                    f"R={self.accumulated_roll:.0f} P={self.accumulated_pitch:.0f} Y={self.accumulated_yaw:.0f}"
                )
        elif force and duration > MAX_TRICK_DURATION and ENABLE_SERIAL_DEBUG:
            debug_log("Trick wegen Timeout beendet (kein stabiler Zustand erreicht).")

        self.in_trick = False
        self.trick_type = None
        self.high_rate_since = None
        self.stable_since = None

    def update(self, roll_deg, pitch_deg, yaw_deg):
        current_time = time.ticks_ms()
        dt = (time.ticks_diff(current_time, self.last_time)) / 1000.0
        if dt <= 0.0 or dt > 0.5:
            self.last_time = current_time
            return
        self.last_time = current_time
        
        def delta_deg(current, last):
            diff = current - last
            while diff > 180: diff -= 360
            while diff < -180: diff += 360
            return diff

        gyro_x = delta_deg(roll_deg, self.last_roll) / dt
        gyro_y = delta_deg(pitch_deg, self.last_pitch) / dt
        gyro_z = delta_deg(yaw_deg, self.last_yaw) / dt

        if (
            abs(delta_deg(roll_deg, self.last_roll)) > MAX_SAMPLE_DELTA_DEG
            or abs(delta_deg(pitch_deg, self.last_pitch)) > MAX_SAMPLE_DELTA_DEG
            or abs(delta_deg(yaw_deg, self.last_yaw)) > MAX_SAMPLE_DELTA_DEG
        ):
            self.last_roll = roll_deg
            self.last_pitch = pitch_deg
            self.last_yaw = yaw_deg
            return
        
        self.last_roll = roll_deg
        self.last_pitch = pitch_deg
        self.last_yaw = yaw_deg
        
        if abs(gyro_x) > 3000 or abs(gyro_y) > 3000 or abs(gyro_z) > 3000:
            return

        self.f_gyro_x += (gyro_x - self.f_gyro_x) * GYRO_LOWPASS_ALPHA
        self.f_gyro_y += (gyro_y - self.f_gyro_y) * GYRO_LOWPASS_ALPHA
        self.f_gyro_z += (gyro_z - self.f_gyro_z) * GYRO_LOWPASS_ALPHA

        f_gx = self._apply_deadband(self.f_gyro_x, GYRO_DEADBAND)
        f_gy = self._apply_deadband(self.f_gyro_y, GYRO_DEADBAND)
        f_gz = self._apply_deadband(self.f_gyro_z, GYRO_DEADBAND)

        abs_gx = abs(f_gx)
        abs_gy = abs(f_gy)
        abs_gz = abs(f_gz)
        max_rate = max(abs_gx, abs_gy, abs_gz)
            
        if not self.in_trick:
            if max_rate > GYRO_TRICK_THRESHOLD:
                if self.high_rate_since is None:
                    self.high_rate_since = current_time
                elif time.ticks_diff(current_time, self.high_rate_since) >= TRICK_START_HOLD_MS:
                    self._start_trick(abs_gx, abs_gy, abs_gz)
                    self.high_rate_since = None
            else:
                self.high_rate_since = None
        else:
            self.accumulated_roll += abs_gx * dt
            self.accumulated_pitch += abs_gy * dt
            self.accumulated_yaw += abs_gz * dt
            self.accumulated_roll_signed += f_gx * dt
            self.accumulated_pitch_signed += f_gy * dt
            self.accumulated_yaw_signed += f_gz * dt

            if abs_gx < STABLE_THRESHOLD and abs_gy < STABLE_THRESHOLD and abs_gz < STABLE_THRESHOLD:
                if self.stable_since is None:
                    self.stable_since = current_time
                elif time.ticks_diff(current_time, self.stable_since) >= STABLE_HOLD_MS:
                    self._finish_trick(force=False)
            else:
                self.stable_since = None

            if time.ticks_diff(current_time, self.trick_start_time) >= TRICK_FORCE_END_MS:
                self._finish_trick(force=True)

    def evaluate_trick(self, duration):
        points = 0
        detected_name = ""

        axis_totals = {
            "Roll": self.accumulated_roll,
            "Flip": self.accumulated_pitch,
            "Spin": self.accumulated_yaw,
        }
        axis_sorted = sorted(axis_totals.items(), key=lambda item: item[1], reverse=True)
        eff_type = axis_sorted[0][0]
        dominant_value = axis_sorted[0][1]
        second_value = axis_sorted[1][1]
        dominant_ratio = dominant_value / max(1.0, second_value)

        if self.trick_type in axis_totals:
            start_value = axis_totals[self.trick_type]
            if start_value >= dominant_value * TRICK_START_TYPE_WEIGHT:
                eff_type = self.trick_type
                dominant_value = axis_totals[eff_type]
                second_value = max(v for k, v in axis_totals.items() if k != eff_type)
                dominant_ratio = dominant_value / max(1.0, second_value)

        if dominant_value < TRICK_MIN_ACCUM_DEG:
            eff_type = "Noise"
        elif eff_type == "Spin" and dominant_value < TRICK_SPIN_MIN_ACCUM_DEG:
            eff_type = "Noise"
        elif dominant_ratio < TRICK_AXIS_DOMINANCE_RATIO and dominant_value < (TRICK_MIN_ACCUM_DEG + 60):
            eff_type = "Noise"

        roll_dir = "Right" if self.accumulated_roll_signed >= 0 else "Left"
        pitch_dir = "Forward" if self.accumulated_pitch_signed >= 0 else "Backward"
        yaw_dir = "CW" if self.accumulated_yaw_signed >= 0 else "CCW"

        if eff_type == "Roll":
            if 70 <= self.accumulated_roll < 170: 
                detected_name = f"{roll_dir} Barrel Roll"; points = 100
            elif 170 <= self.accumulated_roll < 300: 
                detected_name = f"{roll_dir} Double Roll"; points = 250
            elif self.accumulated_roll >= 300: 
                detected_name = f"{roll_dir} Super Multi-Roll"; points = 500
            
            if duration < 0.40 and self.accumulated_roll > 120:
                detected_name = f"{roll_dir} Juicy Roll Flick"; points = 180

        elif eff_type == "Flip":
            if 80 <= self.accumulated_pitch < 190:
                if self.accumulated_roll > 90: 
                    detected_name = f"{pitch_dir} Split-S / Half-Loop"; points = 220
                else: 
                    detected_name = f"{pitch_dir} Power Flip"; points = 100
            elif 190 <= self.accumulated_pitch < 320: 
                detected_name = f"{pitch_dir} Double Flip"; points = 250
            elif self.accumulated_pitch >= 320: 
                detected_name = f"{pitch_dir} Super Multi-Flip"; points = 500
                
            if duration < 0.40 and self.accumulated_pitch > 120:
                detected_name = f"{pitch_dir} Juicy Pitch Flick"; points = 180
                
            if self.accumulated_pitch > 170 and self.accumulated_yaw > 90:
                detected_name = f"{pitch_dir} Matty Flip Combo"; points = 350

        elif eff_type == "Spin":
            if 90 <= self.accumulated_yaw < 220: 
                detected_name = f"{yaw_dir} Flat Spin 360"; points = 150
            elif self.accumulated_yaw >= 220: 
                detected_name = f"{yaw_dir} Flat Spin 720"; points = 350

        if points > 0:
            self.score += points
            self.last_trick_name = detected_name
            timestamp = time.ticks_ms() / 1000.0
            self.trick_history.append(f"[{timestamp:.1f}s] {detected_name} (+{points} Pkt)")
            if len(self.trick_history) > 30: 
                self.trick_history.pop(0)  
            debug_log(f"[SUCCESS] TRICK DETEKTIERT: {detected_name} | Gesamt-Score: {self.score}")

            global highscore_data, pending_highscore
            if self.score > highscore_data["score"]:
                if (not pending_highscore["active"]) or self.score > pending_highscore["score"]:
                    pending_highscore["active"] = True
                    pending_highscore["score"] = self.score
                    pending_highscore["timestamp"] = get_datetime_string()
                    debug_console_only(
                        f"[HIGHSCORE] Neuer Rekord entdeckt: {pending_highscore['score']} Pkt. Bitte im Web bestaetigen."
                    )
        elif ENABLE_SERIAL_DEBUG:
            debug_log(
                f"Trick verworfen: Typ={eff_type} | dom={dominant_value:.0f} ratio={dominant_ratio:.2f} | "
                f"R={self.accumulated_roll:.0f} P={self.accumulated_pitch:.0f} Y={self.accumulated_yaw:.0f}"
            )


detector = LiveGyroTrickDetector()


async def simulate_trick(trick_kind="roll"):
    """Speist synthetische Gyro-Daten in den echten Trick-Detector ein,
    damit Tricks ohne angeschlossene Drohne/Telemetrie getestet werden koennen.
    Nutzt exakt denselben Code-Pfad (detector.update) wie die echte Telemetrie."""
    axis_map = {"roll": "roll", "flip": "pitch", "spin": "yaw"}
    axis = axis_map.get(trick_kind, "roll")

    rate = GYRO_TRICK_THRESHOLD + 80.0
    step_ms = 15
    dt = step_ms / 1000.0
    target_accum_deg = 220.0
    hold_delay_s = (TRICK_START_HOLD_MS / 1000.0) + 0.05
    active_duration_s = hold_delay_s + (target_accum_deg / rate)
    active_steps = max(1, int(active_duration_s / dt))
    stable_steps = max(1, int(((STABLE_HOLD_MS / 1000.0) + 0.15) / dt))

    roll = 0.0
    pitch = 0.0
    yaw = 0.0

    for _ in range(active_steps):
        delta = rate * dt
        if axis == "roll":
            roll = normalize_angle_deg(roll + delta)
        elif axis == "pitch":
            pitch = normalize_angle_deg(pitch + delta)
        else:
            yaw = normalize_angle_deg(yaw + delta)
        detector.update(roll, pitch, yaw)
        await asyncio.sleep_ms(step_ms)

    for _ in range(stable_steps):
        detector.update(roll, pitch, yaw)
        await asyncio.sleep_ms(step_ms)


async def telemetry_loop():
    debug_log("Passiver CRSF-Telemetrie-Loop gestartet.")
    
    state = 0
    frame_length = 0
    frame_type = 0
    payload_expected = 0
    payload_read = 0
    payload_buffer = bytearray()
    frame_crc = 0
    last_live_log_ms = time.ticks_ms()
    last_live_roll = None
    last_live_pitch = None
    last_live_yaw = None
    crc_fail_count = 0

    while True:
        try:
            update_status_led()
            avail = uart.any()
            if avail > 0:
                chunk = uart.read(min(avail, 64))
                if not chunk:
                    await asyncio.sleep_ms(1)
                    continue

                for b in chunk:
                    if state == 0:  
                        if b == CRSF_ADDRESS_FLIGHT_CONTROLLER: 
                            state = 1
                    elif state == 1:  
                        frame_length = b
                        if frame_length < 4 or frame_length > CRSF_MAX_FRAME_LEN:
                            state = 0
                        else:
                            state = 2
                    elif state == 2:  
                        frame_type = b
                        payload_read = 0
                        payload_expected = frame_length - 2
                        payload_buffer = bytearray()
                        frame_crc = 0
                        state = 3
                    elif state == 3:  
                        payload_buffer.append(b)
                        payload_read += 1
                        if payload_read >= payload_expected:
                            state = 4
                    elif state == 4:
                        frame_crc = b
                        calc_crc = crc8_dvb_s2(bytearray([frame_type]) + payload_buffer)

                        if calc_crc == frame_crc:
                            crc_fail_count = 0
                            if frame_type == CRSF_FRAMETYPE_ATTITUDE and len(payload_buffer) == 6:
                                pitch, roll, yaw = struct.unpack('>hhh', payload_buffer)

                                roll_deg = normalize_angle_deg(roll * CRSF_RAD1E4_TO_DEG)
                                pitch_deg = normalize_angle_deg(pitch * CRSF_RAD1E4_TO_DEG)
                                yaw_deg = normalize_angle_deg(yaw * CRSF_RAD1E4_TO_DEG)

                                if (
                                    ENABLE_SERIAL_DEBUG
                                    and ENABLE_LIVE_GYRO_DEBUG
                                    and not detector.in_trick
                                    and time.ticks_diff(time.ticks_ms(), last_live_log_ms) >= 250
                                ):
                                    should_log_live = False
                                    if (
                                        last_live_roll is None
                                        or last_live_pitch is None
                                        or last_live_yaw is None
                                    ):
                                        should_log_live = True
                                    else:
                                        if (
                                            abs(roll_deg - last_live_roll) >= LIVE_LOG_DELTA_DEG
                                            or abs(pitch_deg - last_live_pitch) >= LIVE_LOG_DELTA_DEG
                                            or abs(yaw_deg - last_live_yaw) >= LIVE_LOG_DELTA_DEG
                                        ):
                                            should_log_live = True

                                    if should_log_live and time.ticks_diff(time.ticks_ms(), last_live_log_ms) >= LIVE_LOG_INTERVAL_MS:
                                        debug_console_only(f"[LIVE GYRO DATA] Roll: {roll_deg:6.1f} Grad | Pitch: {pitch_deg:6.1f} Grad | Yaw: {yaw_deg:6.1f} Grad")
                                        last_live_log_ms = time.ticks_ms()
                                        last_live_roll = roll_deg
                                        last_live_pitch = pitch_deg
                                        last_live_yaw = yaw_deg

                                detector.update(roll_deg, pitch_deg, yaw_deg)

                                if ENABLE_SERIAL_DEBUG and detector.in_trick:
                                    debug_live_gyro_trick(
                                        f"[LIVE GYRO TRICK] Roll: {roll_deg:6.1f} Grad | "
                                        f"Pitch: {pitch_deg:6.1f} Grad | Yaw: {yaw_deg:6.1f} Grad"
                                    )
                        else:
                            crc_fail_count += 1
                            if ENABLE_SERIAL_DEBUG and (crc_fail_count % 40 == 0):
                                debug_log(f"CRSF CRC Fehler erkannt (Anzahl: {crc_fail_count})")

                        state = 0
            
            await asyncio.sleep_ms(1)
            
        except Exception as e:
            debug_log(f"[ERROR] Telemetrie Fehler: {e}")
            state = 0
            await asyncio.sleep_ms(1)


# ==================== WEBSERVER TEMPLATE ====================
# Alle HTML-Seiten (Hauptseite + Admin-Bereich mit Unterseiten) liegen als
# eigene .html Dateien auf dem Pico-Dateisystem und werden per
# send_html_file() in 512-Byte-Haeppchen gestreamt. Kein einziges grosses
# HTML-String-Literal ist mehr dauerhaft im Modul-RAM resident - das war
# die Ursache der MemoryErrors beim Modul-Start.
#
# WICHTIG: COPTER_NAME/DEFAULT_PILOT_NAME sind in index.html fest als Text
# eingetragen (kein Runtime-Replace mehr moeglich ohne die Datei wieder in
# RAM zu laden). Wenn du COPTER_NAME hier oben aenderst, passe Titel/H1 in
# index.html manuell mit an.
#
# Admin-Bereich (Unterseiten, wie ein Einstellungen-Menue):
#   /admin           -> Dashboard/Uebersicht mit Links zu den Unterseiten
#   /admin-update    -> OTA-Update (Datei-Upload)
#   /admin-simulate  -> Trick-Simulation (Testen ohne Drohne)
#   /admin-profiles  -> Trick-Tuning-Profile verwalten
#   /admin-system    -> System-Info + manueller Restart
INDEX_HTML_PATH = "index.html"
ADMIN_DASHBOARD_HTML_PATH = "admin_dashboard.html"
ADMIN_UPDATE_HTML_PATH = "admin_update.html"
ADMIN_SIMULATE_HTML_PATH = "admin_simulate.html"
ADMIN_PROFILES_HTML_PATH = "admin_profiles.html"
ADMIN_SYSTEM_HTML_PATH = "admin_system.html"


async def send_html_file(writer, file_path):
    """Streamt eine HTML-Datei vom Dateisystem, ohne den kompletten Inhalt
    als einzelnen RAM-String zu halten (verhindert MemoryError bei grossen
    Admin-Seiten)."""
    gc.collect()
    file_size = os.stat(file_path)[6]
    writer.write(b'HTTP/1.1 200 OK\r\n')
    writer.write(b'Content-Type: text/html\r\n')
    writer.write(b'Content-Length: ' + str(file_size).encode() + b'\r\n')
    writer.write(b'Connection: close\r\n\r\n')

    with open(file_path, 'r') as f:
        chunk_count = 0
        while True:
            chunk = f.read(512)
            if not chunk:
                break
            writer.write(chunk)
            chunk_count += 1
            if chunk_count % 4 == 0:
                await writer.drain()
    await writer.drain()
    gc.collect()


async def handle_client(reader, writer):
    global TRICK_TUNING_PROFILE
    try:
        request_line = await reader.readline()
        if not request_line: 
            return
        
        request = request_line.decode('utf-8')
        parts = request.split(' ')
        request_method = parts[0] if len(parts) >= 1 else 'GET'
        request_target = parts[1] if len(parts) >= 2 else '/'
        
        # Bewusst debug_http_console_only() statt debug_log()/debug_console_only(): Alle
        # [HTTP]-Ereignisse (Request-Zeile, Body-Parsing etc.) sollen NUR live in
        # Thonny/Seriell auftauchen und NICHT in fpv_debug_session.txt bzw. den
        # Debug-TXT-Download wandern (sonst wuerde jeder Poll von /data und
        # /system-info die Debug-Datei mit HTTP-Zeilen zumuellen).
        debug_http_console_only(f"[HTTP] {request_method} {request_target}")
        
        if '?' in request_target:
            request_path, query_string = request_target.split('?', 1)
        else:
            request_path, query_string = request_target, ''
            
        query_params = parse_query(query_string)
        content_length = 0
        
        while True:
            line = await reader.readline()
            if line == b'\r\n' or line == b'\n' or not line: 
                break
            try:
                line_text = line.decode('utf-8').strip()
            except Exception:
                line_text = ''
            line_lower = line_text.lower()
            if line_lower.startswith('content-length:'):
                try:
                    content_length = int(line_text.split(':', 1)[1].strip())
                except Exception:
                    content_length = 0

        body_params = {}
        body_text = ""
        if request_method == 'POST' and content_length > 0:
            try:
                body_buffer = bytearray()
                bytes_remaining = content_length
                chunk_size = 2048  
                
                while bytes_remaining > 0:
                    to_read = min(chunk_size, bytes_remaining)
                    chunk = await reader.read(to_read)
                    if not chunk:
                        debug_http_console_only(f"[HTTP] EOF beim Lesen (bytes_remaining={bytes_remaining})")
                        break
                    body_buffer.extend(chunk)
                    bytes_remaining -= len(chunk)
                
                if len(body_buffer) > 0:
                    try:
                        body_text = body_buffer.decode('utf-8')
                        if request_path != '/upload-chunk':
                            body_params = parse_query(body_text)
                        debug_http_console_only(f"[HTTP] POST Body erfolgreich gelesen: {len(body_text)} bytes")
                    except Exception as e:
                        debug_http_console_only(f"[HTTP] Fehler beim Dekodieren des Body: {e}")
                        body_params = {}
            except Exception as e:
                debug_http_console_only(f"[HTTP] Fehler beim Lesen des POST Body: {e}")
                body_params = {}
                
        if request_path == '/admin':
            await send_html_file(writer, ADMIN_DASHBOARD_HTML_PATH)

        elif request_path == '/admin-update':
            await send_html_file(writer, ADMIN_UPDATE_HTML_PATH)

        elif request_path == '/admin-simulate':
            await send_html_file(writer, ADMIN_SIMULATE_HTML_PATH)
                
        elif request_path == '/upload-chunk' and request_method == 'POST':
            global ota_total_chunks, ota_received_chunks, ota_update_active, ota_target_file

            chunk_index_str = '-1'
            total_str = '0'
            target_str = 'main.py'
            if body_text:
                idx_pos = body_text.find('index=')
                if idx_pos >= 0:
                    idx_start = idx_pos + 6
                    idx_end = body_text.find('&', idx_start)
                    if idx_end < 0:
                        idx_end = len(body_text)
                    chunk_index_str = url_decode(body_text[idx_start:idx_end])

                total_pos = body_text.find('total=')
                if total_pos >= 0:
                    total_start = total_pos + 6
                    total_end = body_text.find('&', total_start)
                    if total_end < 0:
                        total_end = len(body_text)
                    total_str = url_decode(body_text[total_start:total_end])

                target_pos = body_text.find('target=')
                if target_pos >= 0:
                    target_start = target_pos + 7
                    target_end = body_text.find('&', target_start)
                    if target_end < 0:
                        target_end = len(body_text)
                    target_str = url_decode(body_text[target_start:target_end])

            chunk_data = ''
            if body_text:
                marker = '&data='
                pos = body_text.find(marker)
                if pos >= 0:
                    chunk_data = url_decode(body_text[pos + len(marker):])
                elif body_text.startswith('data='):
                    chunk_data = url_decode(body_text[5:])
                else:
                    chunk_data = body_params.get('data', '')
            else:
                chunk_data = body_params.get('data', '')
            
            try:
                chunk_index = int(chunk_index_str)
                total = int(total_str)
                target_valid = True

                if chunk_index == 0:
                    if target_str not in OTA_ALLOWED_TARGETS and target_str != OTA_BUNDLE_TARGET:
                        target_valid = False
                    else:
                        ota_total_chunks = total
                        ota_received_chunks = 0
                        ota_update_active = True
                        ota_target_file = target_str
                        try:
                            os.remove('update.pbp')
                        except Exception:
                            pass
                        debug_log(f"[OTA] Chunk-Transfer gestartet: {total} Chunks erwartet, Ziel={target_str}")

                if not target_valid:
                    response = json.dumps({"ok": False, "error": f"Ungueltiges Ziel: {target_str}"}).encode('utf-8')
                    writer.write(b'HTTP/1.1 400 Bad Request\r\n')
                    writer.write(b'Content-Type: application/json\r\n')
                    writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                    writer.write(b'Connection: close\r\n\r\n')
                    writer.write(response)
                else:
                    if chunk_data:
                        with open('update.pbp', 'a') as f:
                            f.write(chunk_data)
                        ota_received_chunks += 1
                        debug_log(f"[OTA] Chunk {chunk_index+1}/{total} empfangen ({len(chunk_data)} bytes)")

                    response = json.dumps({"ok": True, "message": f"Chunk {chunk_index+1}/{total} gespeichert"}).encode('utf-8')
                    writer.write(b'HTTP/1.1 200 OK\r\n')
                    writer.write(b'Content-Type: application/json\r\n')
                    writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                    writer.write(b'Connection: close\r\n\r\n')
                    writer.write(response)

                    if chunk_index + 1 == total and ota_received_chunks == ota_total_chunks:
                        debug_log("[OTA] Alle Chunks empfangen, bitte /finalize-upload aufrufen")
                
            except Exception as e:
                debug_log(f"[OTA CHUNK] Fehler: {e}")
                ota_update_active = False
                response = json.dumps({"ok": False, "error": str(e)}).encode('utf-8')
                writer.write(b'HTTP/1.1 400 Bad Request\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)
                
        elif request_path == '/finalize-upload':
            try:
                debug_log(f"[OTA] Finalisierung: {ota_received_chunks}/{ota_total_chunks} Chunks vorhanden")
                if ota_received_chunks != ota_total_chunks:
                    raise Exception(f"Incomplete upload: {ota_received_chunks}/{ota_total_chunks}")

                is_bundle = (ota_target_file == OTA_BUNDLE_TARGET)
                target = ota_target_file if (is_bundle or ota_target_file in OTA_ALLOWED_TARGETS) else "main.py"

                decode_ok = safe_base64_file_to_file('update.pbp', OTA_STAGING_PATH)
                if not decode_ok:
                    raise Exception("Base64 Dekodierung fehlgeschlagen")
                
                try:
                    staged_size = os.stat(OTA_STAGING_PATH)[6]
                    debug_log(f"[OTA] Staging-Datei: {staged_size} bytes (Ziel: {target})")
                except Exception:
                    staged_size = 0

                if is_bundle:
                    extracted_files, needs_restart = apply_firmware_bundle(OTA_STAGING_PATH)
                    try:
                        os.remove(OTA_STAGING_PATH)
                    except Exception:
                        pass
                    message = f"Firmware-Bundle angewendet: {len(extracted_files)} Datei(en) ersetzt ({', '.join(extracted_files)})"
                    if needs_restart:
                        message += " Starte Neustart..."
                else:
                    backup_path = "main_backup.py" if target == "main.py" else (target + ".bak")
                    try:
                        with open(target, 'r') as f:
                            old_content = f.read()
                        with open(backup_path, 'w') as f:
                            f.write(old_content)
                        debug_log(f"[OTA] Backup erstellt: {backup_path} ({len(old_content)} bytes)")
                    except Exception as e:
                        debug_log(f"[OTA] Backup-Fehler ({target}): {e}")

                    try:
                        os.remove(target)
                    except Exception:
                        pass

                    os.rename(OTA_STAGING_PATH, target)
                    debug_log(f"[OTA] Finale Datei gespeichert: {target} ({staged_size} bytes)")

                    needs_restart = (target == "main.py")
                    message = f"Update erfolgreich gespeichert: {target} ({staged_size} bytes)!"
                    if needs_restart:
                        message += " Starte Neustart..."

                response = json.dumps({"ok": True, "message": message, "restart": needs_restart}).encode('utf-8')
                writer.write(b'HTTP/1.1 200 OK\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)
                
                ota_total_chunks = 0
                ota_received_chunks = 0
                ota_update_active = False
                try:
                    os.remove('update.pbp')
                except Exception:
                    pass
                
                try:
                    await writer.drain()
                except Exception:
                    pass
                
                if needs_restart:
                    await asyncio.sleep_ms(2000)
                    debug_log("[OTA] Starte machine.reset()...")
                    machine.reset()
                
            except Exception as e:
                debug_log(f"[OTA FINALIZE] Fehler: {str(e)[:100]}")
                response = json.dumps({"ok": False, "error": str(e)[:100]}).encode('utf-8')
                writer.write(b'HTTP/1.1 500 Internal Server Error\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)
                ota_total_chunks = 0
                ota_received_chunks = 0
                ota_update_active = False
                try:
                    os.remove('update.pbp')
                except Exception:
                    pass
                try:
                    os.remove(OTA_STAGING_PATH)
                except Exception:
                    pass
                
        elif request_path == '/restart-pico':
            response = json.dumps({"ok": True, "message": "Pico startet neu..."}).encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response)
            try:
                await writer.drain()
            except Exception:
                pass
            await asyncio.sleep_ms(1000)
            debug_log("[RESTART] machine.reset() wird aufgerufen...")
            machine.reset()
        
        elif request_path == '/admin-profiles':
            await send_html_file(writer, ADMIN_PROFILES_HTML_PATH)

        elif request_path == '/admin-system':
            await send_html_file(writer, ADMIN_SYSTEM_HTML_PATH)

        elif request_path == '/system-info':
            try:
                mem_free = gc.mem_free()
            except Exception:
                mem_free = -1
            try:
                mem_alloc = gc.mem_alloc()
            except Exception:
                mem_alloc = -1

            ip_addr = ""
            if ENABLE_HOTSPOT:
                try:
                    ip_addr = network.WLAN(network.AP_IF).ifconfig()[0]
                except Exception:
                    ip_addr = ""

            info_data = {
                "mem_free": mem_free,
                "mem_alloc": mem_alloc,
                "uptime_s": time.ticks_ms() // 1000,
                "ssid": AP_SSID,
                "ip": ip_addr,
                "hotspot_enabled": ENABLE_HOTSPOT,
                "trick_tuning_profile": TRICK_TUNING_PROFILE,
                "score": detector.score,
                "highscore": highscore_data["score"],
                "ota_active": ota_update_active,
                "ota_received_chunks": ota_received_chunks,
                "ota_total_chunks": ota_total_chunks,
            }
            response_data = json.dumps(info_data).encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
            writer.write(b'Pragma: no-cache\r\n')
            writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response_data)
        
        elif request_path == '/profiles-list':
            profiles_list = list_profile_files()
            profiles_data = []
            for prof in profiles_list:
                profiles_data.append({
                    "name": prof,
                    "active": prof == TRICK_TUNING_PROFILE
                })
            
            response_data = json.dumps({"ok": True, "profiles": profiles_data}).encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
            writer.write(b'Pragma: no-cache\r\n')
            writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response_data)
        
        elif request_path == '/create-profile' and request_method == 'POST':
            profile_name = body_params.get('name', '').strip()
            profile_data_str = body_params.get('data', '').strip()
            
            if not profile_name or not profile_data_str:
                response = json.dumps({"ok": False, "error": "Name oder Daten fehlen"}).encode('utf-8')
                writer.write(b'HTTP/1.1 400 Bad Request\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)
            else:
                try:
                    profile_data = json.loads(profile_data_str)
                    success, error = save_custom_profile(profile_name, profile_data)
                    if success:
                        response = json.dumps({"ok": True, "message": f"Profil {profile_name} erstellt"}).encode('utf-8')
                        writer.write(b'HTTP/1.1 200 OK\r\n')
                    else:
                        response = json.dumps({"ok": False, "error": error}).encode('utf-8')
                        writer.write(b'HTTP/1.1 400 Bad Request\r\n')
                    writer.write(b'Content-Type: application/json\r\n')
                    writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                    writer.write(b'Connection: close\r\n\r\n')
                    writer.write(response)
                except Exception as e:
                    response = json.dumps({"ok": False, "error": str(e)}).encode('utf-8')
                    writer.write(b'HTTP/1.1 500 Internal Server Error\r\n')
                    writer.write(b'Content-Type: application/json\r\n')
                    writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                    writer.write(b'Connection: close\r\n\r\n')
                    writer.write(response)
        
        elif request_path == '/download-profile':
            profile_name = query_params.get('name', '').strip()
            if not profile_name:
                response = json.dumps({"ok": False, "error": "Profil-Name fehlt"}).encode('utf-8')
                writer.write(b'HTTP/1.1 400 Bad Request\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)
            else:
                profile_data = get_profile_data(profile_name)
                if profile_data is None:
                    response = json.dumps({"ok": False, "error": f"Profil nicht gefunden: {profile_name}"}).encode('utf-8')
                    writer.write(b'HTTP/1.1 404 Not Found\r\n')
                    writer.write(b'Content-Type: application/json\r\n')
                    writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                    writer.write(b'Connection: close\r\n\r\n')
                    writer.write(response)
                else:
                    response_data = json.dumps(profile_data).encode('utf-8')
                    writer.write(b'HTTP/1.1 200 OK\r\n')
                    writer.write(b'Content-Type: application/json\r\n')
                    writer.write(b'Content-Disposition: attachment; filename="' + profile_name.encode('utf-8') + b'.pro"\r\n')
                    writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
                    writer.write(b'Connection: close\r\n\r\n')
                    writer.write(response_data)
        
        elif request_path == '/delete-profile':
            profile_name = query_params.get('name', '').strip()
            if not profile_name:
                response = json.dumps({"ok": False, "error": "Profil-Name fehlt"}).encode('utf-8')
            else:
                success, error = delete_custom_profile(profile_name)
                if success:
                    response = json.dumps({"ok": True, "message": f"Profil {profile_name} geloescht"}).encode('utf-8')
                else:
                    response = json.dumps({"ok": False, "error": error}).encode('utf-8')
            
            response = response.encode('utf-8') if isinstance(response, str) else response
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response)
        
        elif request_path == '/apply-profile':
            profile_name = query_params.get('name', '').strip()
            
            if not profile_name:
                response = json.dumps({"ok": False, "error": "Profil-Name fehlt"}).encode('utf-8')
            else:
                profile_name = normalize_trick_tuning_profile(profile_name)
                TRICK_TUNING_PROFILE = profile_name
                apply_trick_tuning_profile()
                saved_ok, save_error = save_trick_tuning_profile()
                
                if saved_ok:
                    response = json.dumps({"ok": True, "profile": profile_name}).encode('utf-8')
                    debug_log(f"[PROFILE] Angewendet: {profile_name}")
                else:
                    response = json.dumps({"ok": False, "error": save_error}).encode('utf-8')
            
            response = response.encode('utf-8') if isinstance(response, str) else response
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
            writer.write(b'Pragma: no-cache\r\n')
            writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response)
                
        elif request_path == '/data':
            data = {
                "score": detector.score,
                "history": detector.trick_history,
                "highscore": highscore_data["score"],
                "highscore_timestamp": highscore_data["timestamp"],
                "highscore_player": highscore_data.get("player", DEFAULT_PILOT_NAME),
                "trick_tuning_profile": TRICK_TUNING_PROFILE,
                "pending_highscore": pending_highscore["active"],
                "pending_highscore_score": pending_highscore["score"]
            }
            response_data = json.dumps(data).encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
            writer.write(b'Pragma: no-cache\r\n')
            writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response_data)

        elif request_path == '/set-highscore-name':
            name = query_params.get('name', '').strip()
            if not name:
                name = body_params.get('name', '').strip()
            is_web_submit = query_params.get('web', '') == '1' or body_params.get('web', '') == '1'
            success = False
            error = ""
            score_to_save = None
            timestamp_to_save = None

            if not name:
                error = "Name darf nicht leer sein."
            else:
                if pending_highscore["active"]:
                    score_to_save = pending_highscore["score"]
                    timestamp_to_save = pending_highscore["timestamp"]
                elif detector.score > highscore_data["score"]:
                    score_to_save = detector.score
                    timestamp_to_save = get_datetime_string()
                    debug_console_only("[HIGHSCORE] Fallback: Score hoeher als Highscore.")
                elif highscore_data["score"] > 0:
                    score_to_save = highscore_data["score"]
                    timestamp_to_save = highscore_data.get("timestamp", "Unbekannt")
                    debug_console_only("[HIGHSCORE] Name fuer bestehenden Highscore wird aktualisiert.")
                else:
                    error = "Kein neuer Highscore zum Speichern vorhanden."

            if error == "" and score_to_save is not None:
                highscore_data["score"] = int(score_to_save)
                highscore_data["timestamp"] = timestamp_to_save or get_datetime_string()
                highscore_data["player"] = name
                saved_ok, save_error = save_highscore()
                if saved_ok:
                    pending_highscore["active"] = False
                    pending_highscore["score"] = 0
                    pending_highscore["timestamp"] = "Unbekannt"
                    success = True
                    debug_console_only(
                        f"[HIGHSCORE] Rekord gespeichert: {highscore_data['score']} Pkt | Pilot: {highscore_data['player']}"
                    )
                else:
                    error = "Speichern fehlgeschlagen: " + str(save_error)
                    debug_console_only("[HIGHSCORE ERROR] " + error)

            if is_web_submit:
                if success:
                    response_html = (
                        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                        "<meta http-equiv='refresh' content='1; url=/'>"
                        "<title>Highscore gespeichert</title></head>"
                        "<body style='font-family:sans-serif;background:#0b0e14;color:#f0f4f8;text-align:center;padding:40px;'>"
                        "<h2>Highscore gespeichert</h2>"
                        f"<p>{html_escape(highscore_data.get('player', DEFAULT_PILOT_NAME))} steht jetzt mit {highscore_data['score']} Punkten im Highscore.</p>"
                        "<p>Du wirst zur Hauptseite zurueckgeleitet...</p>"
                        "</body></html>"
                    ).encode('utf-8')
                else:
                    response_html = (
                        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                        "<meta http-equiv='refresh' content='2; url=/'>"
                        "<title>Speichern fehlgeschlagen</title></head>"
                        "<body style='font-family:sans-serif;background:#0b0e14;color:#f0f4f8;text-align:center;padding:40px;'>"
                        "<h2>Speichern fehlgeschlagen</h2>"
                        f"<p>{html_escape(error or 'Unbekannter Fehler')}</p>"
                        "<p>Du wirst zur Hauptseite zurueckgeleitet...</p>"
                        "</body></html>"
                    ).encode('utf-8')
                    
                writer.write(b'HTTP/1.1 200 OK\r\n')
                writer.write(b'Content-Type: text/html\r\n')
                writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
                writer.write(b'Pragma: no-cache\r\n')
                writer.write(b'Content-Length: ' + str(len(response_html)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response_html)
            else:
                payload = json.dumps({
                    "ok": success,
                    "error": error,
                    "highscore": highscore_data["score"],
                    "highscore_player": highscore_data.get("player", DEFAULT_PILOT_NAME),
                    "highscore_timestamp": highscore_data.get("timestamp", "Unbekannt")
                }).encode('utf-8')

                writer.write(b'HTTP/1.1 200 OK\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
                writer.write(b'Pragma: no-cache\r\n')
                writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(payload)

        elif request_path == '/set-trick-profile':
            profile_name = normalize_trick_tuning_profile(query_params.get('profile', 'aggressive'))
            TRICK_TUNING_PROFILE = profile_name
            apply_trick_tuning_profile()
            saved_ok, save_error = save_trick_tuning_profile()

            if saved_ok:
                debug_console_only(f"[TRICK PROFILE] Profil gespeichert: {TRICK_TUNING_PROFILE}")

            payload = json.dumps({
                "ok": saved_ok,
                "error": "" if saved_ok else ("Speichern fehlgeschlagen: " + str(save_error)),
                "trick_tuning_profile": TRICK_TUNING_PROFILE
            }).encode('utf-8')

            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
            writer.write(b'Pragma: no-cache\r\n')
            writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(payload)

        elif request_path == '/confirm-highscore':
            success = False
            error = ""

            if pending_highscore["active"]:
                highscore_data["score"] = int(pending_highscore["score"])
                highscore_data["timestamp"] = pending_highscore["timestamp"] or get_datetime_string()
                highscore_data["player"] = DEFAULT_PILOT_NAME
                saved_ok, save_error = save_highscore()
                if saved_ok:
                    pending_highscore["active"] = False
                    pending_highscore["score"] = 0
                    pending_highscore["timestamp"] = "Unbekannt"
                    success = True
                    debug_console_only(
                        f"[HIGHSCORE] Rekord gespeichert: {highscore_data['score']} Pkt | Pilot: {highscore_data['player']}"
                    )
                else:
                    error = "Speichern fehlgeschlagen: " + str(save_error)
                    debug_console_only("[HIGHSCORE ERROR] " + error)
            elif detector.score > highscore_data["score"]:
                highscore_data["score"] = int(detector.score)
                highscore_data["timestamp"] = get_datetime_string()
                highscore_data["player"] = DEFAULT_PILOT_NAME
                saved_ok, save_error = save_highscore()
                if saved_ok:
                    success = True
                    debug_console_only(
                        f"[HIGHSCORE] Rekord gespeichert (Fallback): {highscore_data['score']} Pkt | Pilot: {highscore_data['player']}"
                    )
                else:
                    error = "Speichern fehlgeschlagen: " + str(save_error)
                    debug_console_only("[HIGHSCORE ERROR] " + error)
            else:
                success = True

            payload = json.dumps({
                "ok": success,
                "error": error,
                "highscore": highscore_data["score"],
                "highscore_player": highscore_data.get("player", DEFAULT_PILOT_NAME),
                "highscore_timestamp": highscore_data.get("timestamp", "Unbekannt")
            }).encode('utf-8')

            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
            writer.write(b'Pragma: no-cache\r\n')
            writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(payload)

        elif request_path == '/reset-highscore':
            is_web_submit = query_params.get('web', '') == '1' or body_params.get('web', '') == '1'
            debug_console_only(f"[HIGHSCORE] Reset-Route aufgerufen (web={is_web_submit}).")

            highscore_data["score"] = 0
            highscore_data["timestamp"] = "Unbekannt"
            highscore_data["player"] = DEFAULT_PILOT_NAME
            pending_highscore["active"] = False
            pending_highscore["score"] = 0
            pending_highscore["timestamp"] = "Unbekannt"
            detector.score = 0
            detector.trick_history = []
            detector.last_trick_name = "Keiner"

            saved_ok, save_error = save_highscore()
            if saved_ok:
                debug_console_only("[HIGHSCORE] Highscore wurde manuell zurueckgesetzt.")
            else:
                debug_console_only("[HIGHSCORE ERROR] Reset-Speichern fehlgeschlagen: " + str(save_error))

            if is_web_submit:
                if saved_ok:
                    response_html = (
                        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                        "<meta http-equiv='refresh' content='1; url=/'>"
                        "<title>Reset erfolgreich</title></head>"
                        "<body style='font-family:sans-serif;background:#0b0e14;color:#f0f4f8;text-align:center;padding:40px;'>"
                        "<h2>Highscore wurde zurueckgesetzt</h2>"
                        "<p>Highscore und Session-Score stehen jetzt auf 0.</p>"
                        "<p>Du wirst zur Hauptseite zurueckgeleitet...</p>"
                        "</body></html>"
                    ).encode('utf-8')
                else:
                    response_html = (
                        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                        "<meta http-equiv='refresh' content='2; url=/'>"
                        "<title>Reset fehlgeschlagen</title></head>"
                        "<body style='font-family:sans-serif;background:#0b0e14;color:#f0f4f8;text-align:center;padding:40px;'>"
                        "<h2>Reset fehlgeschlagen</h2>"
                        f"<p>{html_escape(str(save_error))}</p>"
                        "<p>Du wirst zur Hauptseite zurueckgeleitet...</p>"
                        "</body></html>"
                    ).encode('utf-8')

                writer.write(b'HTTP/1.1 200 OK\r\n')
                writer.write(b'Content-Type: text/html\r\n')
                writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
                writer.write(b'Pragma: no-cache\r\n')
                writer.write(b'Content-Length: ' + str(len(response_html)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response_html)
            else:
                payload = json.dumps({
                    "ok": saved_ok,
                    "error": "" if saved_ok else ("Reset fehlgeschlagen: " + str(save_error)),
                    "highscore": highscore_data["score"],
                    "highscore_player": highscore_data.get("player", DEFAULT_PILOT_NAME),
                    "highscore_timestamp": highscore_data.get("timestamp", "Unbekannt")
                }).encode('utf-8')

                writer.write(b'HTTP/1.1 200 OK\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
                writer.write(b'Pragma: no-cache\r\n')
                writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(payload)
            
        elif request_path in ('/download', '/download-session'):
            if ENABLE_SERIAL_DEBUG:
                print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [DOWNLOAD-SESSION] Exportdatei wird erstellt")
            write_text_file(SESSION_EXPORT_FILE_PATH, build_session_txt_content())
            await send_file_as_download(writer, SESSION_EXPORT_FILE_PATH, "fpv_arcade_session.txt")
            if ENABLE_SERIAL_DEBUG:
                print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [DOWNLOAD-SESSION] Datei versendet")

        elif request_path in ('/download-debug', '/download-debug-raw'):
            if ENABLE_SERIAL_DEBUG:
                print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [DOWNLOAD-DEBUG] Exportdatei wird erstellt")
            build_debug_export_file()
            await send_file_as_download(writer, DEBUG_EXPORT_FILE_PATH, "fpv_debug_log.txt")
            if ENABLE_SERIAL_DEBUG:
                print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [DOWNLOAD-DEBUG] Datei versendet")

        elif request_path == '/simulate-trick':
            trick_kind = query_params.get('type', 'roll').strip().lower()
            if trick_kind not in ('roll', 'flip', 'spin'):
                trick_kind = 'roll'

            score_before = detector.score
            debug_console_only(f"[SIMULATE] Starte Trick-Simulation: {trick_kind}")
            await simulate_trick(trick_kind)
            points_gained = detector.score - score_before

            payload = json.dumps({
                "ok": True,
                "type": trick_kind,
                "trick": detector.last_trick_name if points_gained > 0 else None,
                "points": points_gained,
                "score": detector.score
            }).encode('utf-8')

            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
            writer.write(b'Pragma: no-cache\r\n')
            writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(payload)
            
        else:
            await send_html_file(writer, INDEX_HTML_PATH)
            
        await writer.drain()
    except OSError as e:
        if len(e.args) > 0 and e.args[0] == 104:
            if ENABLE_SERIAL_DEBUG:
                print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [WEB INFO] Client hat Verbindung geschlossen (ECONNRESET)")
        else:
            debug_log(f"[WEB ERROR] {e}")
    except Exception as e:
        debug_log(f"[WEB ERROR] {e}")
    finally:
        try:
            writer.close()
        except Exception:
            pass
        try:
            wait_closed = getattr(writer, 'wait_closed', None)
            if wait_closed is not None:
                await wait_closed()
        except Exception:
            pass
        await asyncio.sleep_ms(5)


async def main_async():
    global system_ready, status_led_last_toggle_ms
    if ENABLE_HOTSPOT:
        start_access_point()
        await asyncio.start_server(handle_client, "0.0.0.0", 80)
    system_ready = True
    status_led_last_toggle_ms = time.ticks_ms()
    update_status_led()
    await telemetry_loop()


def run():
    debug_log("tracker.run() wurde aufgerufen.")
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        debug_log("System manuell gestoppt.")

run()