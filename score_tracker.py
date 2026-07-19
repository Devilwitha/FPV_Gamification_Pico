import machine
import time
import struct
import network
import asyncio
import json
import os

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

# CRSF Frame- und Plausibilitätsgrenzen
CRSF_MAX_FRAME_LEN     = 64
MAX_SAMPLE_DELTA_DEG   = 140
MIN_ACCUM_FOR_TIMEOUT_DEG = 160

# Bewegungsschwellenwerte (°/s berechnet aus Winkeln)
GYRO_TRICK_THRESHOLD = 190
STABLE_THRESHOLD     = 65
TRICK_START_HOLD_MS  = 35
STABLE_HOLD_MS       = 140
TRICK_FORCE_END_MS   = 2200
GYRO_DEADBAND        = 12
GYRO_LOWPASS_ALPHA   = 0.30
MIN_TRICK_DURATION   = 0.12
MAX_TRICK_DURATION   = 2.5
TRICK_MIN_ACCUM_DEG  = 80
TRICK_SPIN_MIN_ACCUM_DEG = 120
TRICK_AXIS_DOMINANCE_RATIO = 1.18
TRICK_START_TYPE_WEIGHT = 0.92
DEBUG_LOG_MAX_LINES  = 300
LIVE_LOG_INTERVAL_MS = 900
LIVE_LOG_DELTA_DEG   = 0.8
DEBUG_LOG_FILE_PATH  = "fpv_debug_session.txt"
DEBUG_LOG_FILE_MAX_BYTES = 180000
DEBUG_LOG_BOOT_MARKER = "=== FPV DEBUG SESSION START ===\n"
SESSION_EXPORT_FILE_PATH = "fpv_arcade_session_export.txt"
DEBUG_EXPORT_FILE_PATH = "fpv_debug_export.txt"
HIGHSCORE_FILE_PATH  = "fpv_highscore.json"
TRICK_SETTINGS_FILE_PATH = "fpv_trick_settings.json"
LED_BLINK_INTERVAL_MS = 220
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
        "gyro_trick_threshold": 190,
        "stable_threshold": 65,
        "trick_start_hold_ms": 35,
        "stable_hold_ms": 140,
        "gyro_deadband": 12,
        "gyro_lowpass_alpha": 0.30,
        "min_trick_duration": 0.12,
        "trick_min_accum_deg": 80,
        "trick_spin_min_accum_deg": 120,
        "trick_axis_dominance_ratio": 1.18,
        "trick_start_type_weight": 0.92,
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
    normalized = str(profile_name).strip().lower()
    if normalized == "soft":
        normalized = "beginner"
    elif normalized == "medium":
        normalized = "freestyle"
    if normalized in TRICK_TUNING_PROFILES:
        return normalized
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

    # Debug-Download soll den tatsaechlichen Verlauf enthalten.
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
    """Simple Base64 decoder for MicroPython."""
    import base64
    try:
        return base64.b2a_base64(base64.a2b_base64(s + b'==')).decode('utf-8').strip()
    except Exception:
        return None


def safe_base64_decode_to_file(b64_string, output_file):
    """Dekodiere Base64 direkt in Datei um RAM zu sparen."""
    try:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
        
        with open(output_file, 'wb') as f:
            # Verarbeite in kleineren Chunks
            chunk_size = 512
            for chunk_start in range(0, len(b64_string), chunk_size):
                chunk = b64_string[chunk_start:chunk_start + chunk_size]
                
                # Dekodiere diesen Chunk
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
                
                # Schreibe aus RAM direkt zur Datei
                f.write(chunk_result)
        
        return True
    except Exception as e:
        debug_log(f"[BASE64-FILE] Fehler: {e}")
        return False


def safe_base64_file_to_file(input_file, output_file):
    """Dekodiere Base64 aus Datei direkt in Ziel-Datei (RAM-sparend)."""
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

                # Rest verarbeiten
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


def build_debug_txt_content():
    txt_content = "========================================\n"
    txt_content += f"      {COPTER_NAME.upper()} DEBUG LOG\n"
    txt_content += "========================================\n\n"

    file_loaded = False
    try:
        file_size = os.stat(DEBUG_LOG_FILE_PATH)[6]
        if file_size > len(DEBUG_LOG_BOOT_MARKER):
            with open(DEBUG_LOG_FILE_PATH, 'r') as f:
                txt_content += f.read()
            file_loaded = True
    except Exception:
        file_loaded = False

    if not file_loaded:
        if debug_log_history:
            txt_content += "\n".join(debug_log_history)
            txt_content += "\n"
        else:
            txt_content += "- Keine Debug-Logs vorhanden -\n"

    return txt_content


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

        # Auf MicroPython kann rename über bestehende Datei fehlschlagen.
        try:
            os.remove(HIGHSCORE_FILE_PATH)
        except Exception:
            pass

        os.rename(tmp_path, HIGHSCORE_FILE_PATH)
        return True, ""
    except Exception as e:
        # Fallback auf direktes Schreiben.
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

        # Timeout ohne genug Rotationsenergie ist meistens ein False-Start durch Datenrauschen.
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

        # Berechne die aktuelle Drehrate (°/s)
        gyro_x = delta_deg(roll_deg, self.last_roll) / dt
        gyro_y = delta_deg(pitch_deg, self.last_pitch) / dt
        gyro_z = delta_deg(yaw_deg, self.last_yaw) / dt

        # Unplausible Einzel-Spruenge verwerfen (typisch bei korrumpierten Serial-Frames).
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
        dominance_ratio = dominant_value / max(1.0, second_value)

        # Starttyp als Hilfsanker verwenden, falls die Endwerte sehr nah beieinander liegen.
        if self.trick_type in axis_totals:
            start_value = axis_totals[self.trick_type]
            if start_value >= dominant_value * TRICK_START_TYPE_WEIGHT:
                eff_type = self.trick_type
                dominant_value = axis_totals[eff_type]
                second_value = max(
                    v for k, v in axis_totals.items() if k != eff_type
                )
                dominance_ratio = dominant_value / max(1.0, second_value)

        if dominant_value < TRICK_MIN_ACCUM_DEG:
            eff_type = "Noise"
        elif eff_type == "Spin" and dominant_value < TRICK_SPIN_MIN_ACCUM_DEG:
            eff_type = "Noise"
        elif dominance_ratio < TRICK_AXIS_DOMINANCE_RATIO and dominant_value < (TRICK_MIN_ACCUM_DEG + 60):
            eff_type = "Noise"

        roll_dir = "Right" if self.accumulated_roll_signed >= 0 else "Left"
        pitch_dir = "Forward" if self.accumulated_pitch_signed >= 0 else "Backward"
        yaw_dir = "CW" if self.accumulated_yaw_signed >= 0 else "CCW"

        if eff_type == "Roll":
            if 70 <= self.accumulated_roll < 170: detected_name = f"{roll_dir} Barrel Roll"; points = 100
            elif 170 <= self.accumulated_roll < 300: detected_name = f"{roll_dir} Double Roll"; points = 250
            elif self.accumulated_roll >= 300: detected_name = f"{roll_dir} Super Multi-Roll"; points = 500
            
            if duration < 0.40 and self.accumulated_roll > 120:
                detected_name = f"{roll_dir} Juicy Roll Flick"; points = 180

        elif eff_type == "Flip":
            if 80 <= self.accumulated_pitch < 190:
                if self.accumulated_roll > 90: detected_name = f"{pitch_dir} Split-S / Half-Loop"; points = 220
                else: detected_name = f"{pitch_dir} Power Flip"; points = 100
            elif 190 <= self.accumulated_pitch < 320: detected_name = f"{pitch_dir} Double Flip"; points = 250
            elif self.accumulated_pitch >= 320: detected_name = f"{pitch_dir} Super Multi-Flip"; points = 500
                
            if duration < 0.40 and self.accumulated_pitch > 120:
                detected_name = f"{pitch_dir} Juicy Pitch Flick"; points = 180
                
            if self.accumulated_pitch > 170 and self.accumulated_yaw > 90:
                detected_name = f"{pitch_dir} Matty Flip Combo"; points = 350

        elif eff_type == "Spin":
            if 90 <= self.accumulated_yaw < 220: detected_name = f"{yaw_dir} Flat Spin 360"; points = 150
            elif self.accumulated_yaw >= 220: detected_name = f"{yaw_dir} Flat Spin 720"; points = 350

        if points > 0:
            self.score += points
            self.last_trick_name = detected_name
            timestamp = time.ticks_ms() / 1000.0
            self.trick_history.append(f"[{timestamp:.1f}s] {detected_name} (+{points} Pkt)")
            if len(self.trick_history) > 30: self.trick_history.pop(0)  # Erhöht für längere Listen
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
                f"Trick verworfen: Typ={eff_type} | dom={dominant_value:.0f} ratio={dominance_ratio:.2f} | "
                f"R={self.accumulated_roll:.0f} P={self.accumulated_pitch:.0f} Y={self.accumulated_yaw:.0f}"
            )


detector = LiveGyroTrickDetector()


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
                        if b == CRSF_ADDRESS_FLIGHT_CONTROLLER: state = 1
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
                                        debug_console_only(f"[LIVE GYRO DATA] Roll: {roll_deg:6.1f}° | Pitch: {pitch_deg:6.1f}° | Yaw: {yaw_deg:6.1f}°")
                                        last_live_log_ms = time.ticks_ms()
                                        last_live_roll = roll_deg
                                        last_live_pitch = pitch_deg
                                        last_live_yaw = yaw_deg

                                detector.update(roll_deg, pitch_deg, yaw_deg)

                                if ENABLE_SERIAL_DEBUG and detector.in_trick:
                                    debug_live_gyro_trick(
                                        f"[LIVE GYRO TRICK] Roll: {roll_deg:6.1f}° | "
                                        f"Pitch: {pitch_deg:6.1f}° | Yaw: {yaw_deg:6.1f}°"
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
html_template = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__COPTER_NAME__ Arcade</title>
<style>
:root{--bg:#070b12;--panel:#111824;--panel2:#0d141f;--line:#26354a;--text:#eef4fb;--muted:#9fb4cb;--accent:#f39c12;--good:#2ecc71;--blue:#2d86d9;--red:#c0392b}
*{box-sizing:border-box}body{margin:0;padding:18px;font-family:sans-serif;background:radial-gradient(circle at top,#10203a 0,#070b12 45%,#04070c 100%);color:var(--text)}
.c{max-width:620px;margin:0 auto;background:linear-gradient(180deg,rgba(20,27,37,.96),rgba(12,18,28,.98));padding:22px;border-radius:18px;border:1px solid var(--line);box-shadow:0 18px 45px rgba(0,0,0,.45)}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
h1{margin:0;color:var(--accent);font-size:1.5em;letter-spacing:1px;text-transform:uppercase}
.badge{color:var(--muted);font-size:.82em;padding:.35rem .6rem;border:1px solid #30465f;border-radius:999px;background:#0e1622}
#s{font-size:3.2em;font-weight:800;line-height:1;color:var(--good);margin:14px 0 10px;text-shadow:0 0 16px rgba(46,204,113,.22)}
.meta{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:.92em;margin-bottom:12px}
.card{background:linear-gradient(180deg,rgba(13,20,31,.98),rgba(9,14,22,.98));border:1px solid var(--line);border-radius:14px;padding:14px;margin-top:14px}
.card h2{margin:0 0 10px;font-size:1em;color:#d8e5f4;letter-spacing:.4px}
select{width:100%;background:#0b1320;color:#e8f2ff;border:1px solid #355270;border-radius:10px;padding:10px 12px;font-size:1em;outline:none}
.n{min-height:18px;margin-top:8px;color:var(--muted);font-size:.82em}
.t{max-height:220px;overflow:auto;background:var(--panel2);border:1px solid #1f2a3a;border-radius:12px;padding:10px 12px;font-family:monospace;font-size:.88em;line-height:1.45}
.td{padding:5px 0;border-bottom:1px solid rgba(255,255,255,.07)}
.td:last-child{border-bottom:0}
.btnrow{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
.b{display:inline-flex;align-items:center;justify-content:center;gap:8px;flex:1 1 150px;background:var(--blue);color:#fff;border:none;border-radius:11px;padding:11px 14px;cursor:pointer;font-weight:700;text-decoration:none}
.b:hover{filter:brightness(1.08)}
.br{background:var(--red)}
.admin{display:block;margin-top:12px;text-align:right;color:#89a7c4;font-size:.78em;text-decoration:none}
</style>
</head>
<body>
<div class="c">
<div class="top"><h1>🐝 __COPTER_NAME_UPPER__ ARCADE</h1><div class="badge">WLAN / FPV / Tricks</div></div>
<div id="s">0</div>
<div class="meta"><div>Highscore: <b id="hs">0</b></div><div>Pilot: <b id="p">__DEFAULT_PILOT_NAME__</b></div></div>
<div class="card"><h2>Trick-Tuning Profil</h2><select id="prof" onchange="setP()"><option value="beginner">Beginner</option><option value="freestyle" selected>Freestyle</option><option value="aggressive">Aggressive</option></select><div id="pn" class="n">Profil...</div></div>
<div class="card"><h2>Detektierte Manöver</h2><div class="t" id="tr">Warte...</div></div>
<div class="btnrow"><button class="b" onclick="dl()">📥 Download</button><button class="b" onclick="dld()">🧪 Debug-Log</button><button class="b br" onclick="rhs()">🗑️ Reset HS</button></div>
<a class="admin" href="/admin">⚙️ Admin</a>
</div>
<script>
function upd(){fetch("/data?t="+Date.now()).then(r=>r.json()).then(d=>{document.getElementById("s").innerText=d.score;document.getElementById("hs").innerText=d.highscore;document.getElementById("p").innerText=d.highscore_player;let h=d.history||[];document.getElementById("tr").innerHTML=h.length?[...h].reverse().map(x=>"<div class='td'>"+x+"</div>").join(""):"Keine Tricks";if(d.trick_tuning_profile)document.getElementById("prof").value=d.trick_tuning_profile}).catch(e=>0)}
function setP(){let p=document.getElementById("prof").value;document.getElementById("pn").innerText="Speichert...";fetch("/set-trick-profile?profile="+p).then(r=>r.json()).then(d=>{document.getElementById("pn").innerText="OK"}).catch(e=>{document.getElementById("pn").innerText="Fehler"})}
function dl(){window.open("/download?manual=1","_blank")}
function dld(){window.open("/download-debug?manual=1","_blank")}
function rhs(){if(confirm("Highscore löschen?")){fetch("/reset-highscore?web=1").then(()=>upd()).catch(e=>0)}}
setInterval(upd,1000);upd();
</script>
</body></html>"""

html_template = html_template.replace("__COPTER_NAME__", html_escape(COPTER_NAME))
html_template = html_template.replace("__COPTER_NAME_UPPER__", html_escape(COPTER_NAME.upper()))
html_template = html_template.replace("__DEFAULT_PILOT_NAME__", html_escape(DEFAULT_PILOT_NAME))
html_template = html_template.replace('value="beginner"', 'value="beginner"' + (' selected' if TRICK_TUNING_PROFILE == 'beginner' else ''))
html_template = html_template.replace('value="freestyle"', 'value="freestyle"' + (' selected' if TRICK_TUNING_PROFILE == 'freestyle' else ''))
html_template = html_template.replace('value="aggressive"', 'value="aggressive"' + (' selected' if TRICK_TUNING_PROFILE == 'aggressive' else ''))


# ==================== OTA UPDATE SYSTEM ====================
admin_upload_template = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>OTA Update - Admin Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #0b0e14; color: #f0f4f8; font-family: monospace; padding: 20px; }
        .container { max-width: 900px; margin: 0 auto; background: #141b25; padding: 30px; border-radius: 10px; border: 2px solid #e74c3c; }
        h1 { color: #e74c3c; text-align: center; }
        .warning { background: #522; padding: 15px; border-radius: 5px; border-left: 4px solid #e74c3c; margin-bottom: 20px; color: #faa; }
        .upload-section { background: #1a2637; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #335174; }
        textarea { width: 100%; height: 400px; background: #0b1320; color: #00ff00; border: 1px solid #335174; border-radius: 5px; padding: 10px; box-sizing: border-box; font-family: monospace; font-size: 11px; }
        .btn-upload { background: #e74c3c; color: #fff; padding: 12px 20px; border: none; border-radius: 5px; cursor: pointer; font-size: 1em; font-weight: bold; margin-right: 10px; }
        .btn-upload:hover { background: #ff6b5b; }
        .btn-back { background: #2980b9; color: #fff; padding: 12px 20px; border: none; border-radius: 5px; cursor: pointer; font-size: 1em; }
        .btn-back:hover { background: #3498db; }
        .btn-restart { background: #8b0000; color: #fff; padding: 12px 20px; border: none; border-radius: 5px; cursor: pointer; font-size: 1em; font-weight: bold; }
        .btn-restart:hover { background: #c00000; }
        .status { margin-top: 20px; padding: 10px; border-radius: 5px; display: none; }
        .status.success { background: #1a4d2e; color: #2ecc71; border: 1px solid #2ecc71; }
        .status.error { background: #4d1a1a; color: #ff6b6b; border: 1px solid #ff6b6b; }
        .button-row { text-align: center; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚠️ OTA UPDATE PANEL</h1>
        <div class="warning">
            <strong>WARNUNG:</strong> Nur Python-Code einfügen! Falscher Code kann den Pico unbrauchbar machen.
            Stelle sicher, dass dein Code syntaktisch korrekt ist. Die Datei wird als <code>main.py</code> gespeichert.
        </div>
        
        <div class="upload-section">
            <h3>Schritt 1: main.py Datei auswählen</h3>
            <input type="file" id="script_file" accept=".py" style="display: block; margin: 15px 0; padding: 10px; background: #0b1320; color: #e8f2ff; border: 1px solid #335174; border-radius: 5px; width: 100%; box-sizing: border-box;">
            <div id="file_info" style="color: #9fb4cb; font-size: 0.9em; margin-bottom: 15px;">Wähle dein main.py (oder score_tracker.py) aus</div>
            <div id="status" class="status"></div>
            <div class="button-row">
                <button class="btn-upload" onclick="uploadUpdate()">📤 Update speichern</button>
                <button class="btn-back" onclick="window.location.href='/'">Zurück zur Hauptseite</button>
                <button class="btn-restart" onclick="restartPico()">🔄 Pico jetzt neustarten</button>
            </div>
        </div>
    </div>

    <script>
    document.getElementById('script_file').addEventListener('change', function(e) {
        const file = e.target.files[0];
        const info = document.getElementById('file_info');
        if (file) {
            info.innerText = '✅ Datei: ' + file.name + ' (' + (file.size / 1024).toFixed(1) + ' KB) → wird als main.py gespeichert';
        } else {
            info.innerText = 'Wähle dein main.py (oder score_tracker.py) aus';
        }
    });

    function uploadUpdate() {
        const file = document.getElementById('script_file').files[0];
        const status = document.getElementById('status');
        
        if (!file) {
            status.className = 'status error';
            status.innerText = '❌ Fehler: Keine Datei ausgewählt!';
            status.style.display = 'block';
            return;
        }

        const btn = event.target;
        btn.disabled = true;
        btn.innerText = 'Speichert...';
        
        // Lese die Datei und konvertiere zu Base64
        const reader = new FileReader();
        reader.onload = function(e) {
            try {
                const content = e.target.result;
                
                // Konvertiere zu Uint8Array für UTF-8 Encoding
                const encoder = new TextEncoder();
                const bytes = encoder.encode(content);
                
                // Konvertiere Bytes zu Base64 mit Chunking
                let binary = '';
                const chunkSize = 10240;  // 10KB chunks für Base64 Konvertierung
                for (let i = 0; i < bytes.length; i += chunkSize) {
                    const chunk = bytes.slice(i, i + chunkSize);
                    for (let j = 0; j < chunk.length; j++) {
                        binary += String.fromCharCode(chunk[j]);
                    }
                }
                const b64content = btoa(binary);
                
                // Teile Base64 in kleinere Upload-Chunks fuer stabile Pico-RAM-Nutzung
                const uploadChunkSize = 1024;  // 1KB pro HTTP-Request
                let chunkIndex = 0;
                const totalChunks = Math.ceil(b64content.length / uploadChunkSize);
                
                status.innerText = '⏳ Uploade Chunk 1/' + totalChunks + '...';
                status.className = 'status';
                status.style.display = 'block';
                
                function uploadNextChunk() {
                    if (chunkIndex >= totalChunks) {
                        // Alle Chunks hochgeladen, starte Finalisierung
                        status.innerText = '⏳ Kombiniere und starte Neustart...';
                        fetchFinalize();
                        return;
                    }
                    
                    const start = chunkIndex * uploadChunkSize;
                    const end = Math.min(start + uploadChunkSize, b64content.length);
                    const chunkData = b64content.substring(start, end);
                    
                    fetch('/upload-chunk', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: 'index=' + chunkIndex + '&total=' + totalChunks + '&data=' + encodeURIComponent(chunkData),
                        cache: 'no-store'
                    })
                    .then(res => res.json())
                    .then(data => {
                        if (data.ok) {
                            chunkIndex++;
                            const progress = Math.round((chunkIndex / totalChunks) * 100);
                            status.innerText = '⏳ Uploade Chunk ' + (chunkIndex+1) + '/' + totalChunks + ' (' + progress + '%)...';
                            uploadNextChunk();
                        } else {
                            status.className = 'status error';
                            status.innerText = '❌ Fehler bei Chunk ' + chunkIndex + ': ' + data.error;
                        }
                    })
                    .catch(err => {
                        status.className = 'status error';
                        status.innerText = '❌ Upload Chunk ' + chunkIndex + ' fehlgeschlagen: ' + err;
                        console.log('Chunk Upload Error:', err);
                    });
                }
                
                function fetchFinalize() {
                    fetch('/finalize-upload', { cache: 'no-store' })
                    .then(res => res.json())
                    .then(data => {
                        if (data.ok) {
                            status.className = 'status success';
                            status.innerText = '✅ ' + data.message + ' - Verbindung wird getrennt...';
                            setTimeout(() => {
                                window.location.href = '/';
                            }, 3000);
                        } else {
                            status.className = 'status error';
                            status.innerText = '❌ Finalisierung fehlgeschlagen: ' + data.error;
                        }
                    })
                    .catch(err => {
                        status.className = 'status error';
                        status.innerText = '❌ Finalisierung fehlgeschlagen: ' + err;
                    })
                    .finally(() => {
                        btn.disabled = false;
                        btn.innerText = '📤 Update speichern';
                    });
                }
                
                uploadNextChunk();
            } catch (err) {
                status.className = 'status error';
                status.innerText = '❌ Fehler beim Verarbeiten der Datei: ' + err;
                status.style.display = 'block';
                btn.disabled = false;
                btn.innerText = '📤 Update speichern';
            }
        };
        reader.onerror = function() {
            status.className = 'status error';
            status.innerText = '❌ Fehler beim Lesen der Datei';
            status.style.display = 'block';
            btn.disabled = false;
            btn.innerText = '📤 Update speichern';
        };
        reader.readAsText(file, 'utf-8');
    }

    function restartPico() {
        if (!confirm('Pico wirklich neu starten?')) {
            return;
        }
        
        const btn = event.target;
        btn.disabled = true;
        btn.innerText = 'Startet neu...';
        
        fetch('/restart-pico', { cache: 'no-store' })
        .then(() => {
            setTimeout(() => {
                window.location.href = '/';
            }, 2000);
        })
        .catch(() => {
            alert('Neustart eingeleitet. Verbindung wird unterbrochen...');
        });
    }
    </script>
</body>
</html>"""


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
        if ENABLE_SERIAL_DEBUG:
            print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [HTTP] {request_method} {request_target}")
        if '?' in request_target:
            request_path, query_string = request_target.split('?', 1)
        else:
            request_path, query_string = request_target, ''
        query_params = parse_query(query_string)
        content_length = 0
        
        # Header komplett abfrühstücken, um Browser-Hänger zu vermeiden
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
                # Lese Body in Chunks um Stack zu schonen
                body_buffer = bytearray()
                bytes_remaining = content_length
                chunk_size = 2048  # 2KB chunks
                
                while bytes_remaining > 0:
                    to_read = min(chunk_size, bytes_remaining)
                    chunk = await reader.read(to_read)
                    
                    if not chunk:
                        debug_log(f"[HTTP] EOF beim Lesen (bytes_remaining={bytes_remaining})")
                        break
                    
                    body_buffer.extend(chunk)
                    bytes_remaining -= len(chunk)
                
                if len(body_buffer) > 0:
                    try:
                        body_text = body_buffer.decode('utf-8')
                        if request_path == '/upload-chunk':
                            # Bei OTA-Chunks vermeiden wir parse_query auf dem kompletten Body,
                            # weil das auf dem Pico unnötige große Speicherallokationen erzeugt.
                            body_params = {}
                        else:
                            body_params = parse_query(body_text)
                        debug_log(f"[HTTP] POST Body erfolgreich gelesen: {len(body_text)} bytes")
                    except Exception as e:
                        debug_log(f"[HTTP] Fehler beim Dekodieren des Body: {e}")
                        body_params = {}
            except Exception as e:
                debug_log(f"[HTTP] Fehler beim Lesen des POST Body: {e}")
                body_params = {}
                
        if request_path == '/admin':
            # Admin Panel für OTA Updates
            response_html = admin_upload_template.encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: text/html\r\n')
            writer.write(b'Content-Length: ' + str(len(response_html)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response_html)
                
        elif request_path == '/upload-chunk' and request_method == 'POST':
            # OTA Chunk Upload
            global ota_total_chunks, ota_received_chunks, ota_update_active

            # index/total direkt aus Body lesen (parse_query auf vollem Body wird bewusst vermieden)
            chunk_index_str = '-1'
            total_str = '0'
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

            # RAM-sparend: Data-Feld direkt aus Body extrahieren und nur dieses URL-dekodieren.
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
                
                if chunk_index == 0:
                    # Reset bei erstem Chunk + neue Pufferdatei
                    ota_total_chunks = total
                    ota_received_chunks = 0
                    ota_update_active = True
                    try:
                        os.remove('update.pbp')
                    except Exception:
                        pass
                    debug_log(f"[OTA] Chunk-Transfer gestartet: {total} Chunks erwartet")
                
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
            # OTA Finalisierung: kombiniere alle Chunks
            global ota_total_chunks, ota_received_chunks, ota_update_active
            
            try:
                debug_log(f"[OTA] Finalisierung: {ota_received_chunks}/{ota_total_chunks} Chunks vorhanden")
                
                if ota_received_chunks != ota_total_chunks:
                    raise Exception(f"Incomplete upload: {ota_received_chunks}/{ota_total_chunks}")

                # Dekodiere direkt aus update.pbp in main_temp.py
                decode_ok = safe_base64_file_to_file('update.pbp', 'main_temp.py')
                
                if not decode_ok:
                    raise Exception("Base64 Dekodierung fehlgeschlagen")
                
                # Prüfe Dateigröße
                try:
                    temp_size = os.stat('main_temp.py')[6]
                    debug_log(f"[OTA] Temp-Datei: {temp_size} bytes")
                except Exception:
                    temp_size = 0
                
                # Backup
                try:
                    with open('main.py', 'r') as f:
                        old_content = f.read()
                    with open('main_backup.py', 'w') as f:
                        f.write(old_content)
                    debug_log(f"[OTA] Backup erstellt: {len(old_content)} bytes")
                except Exception as e:
                    debug_log(f"[OTA] Backup-Fehler: {e}")
                
                # Verschiebe
                try:
                    os.remove('main.py')
                except Exception:
                    pass
                
                os.rename('main_temp.py', 'main.py')
                debug_log(f"[OTA] Finale Datei gespeichert: main.py ({temp_size} bytes)")
                
                response = json.dumps({"ok": True, "message": f"Update erfolgreich gespeichert ({temp_size} bytes)! Starte Neustart..."}).encode('utf-8')
                writer.write(b'HTTP/1.1 200 OK\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)
                
                # Reset globals
                ota_total_chunks = 0
                ota_received_chunks = 0
                ota_update_active = False
                try:
                    os.remove('update.pbp')
                except Exception:
                    pass
                
                # Drain und Reset
                try:
                    await writer.drain()
                except Exception:
                    pass
                
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
                
        elif request_path == '/restart-pico':
            # Restart ohne Update
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
                # Primärer Pfad: expliziter Pending-Highscore aus dem Detektor.
                if pending_highscore["active"]:
                    score_to_save = pending_highscore["score"]
                    timestamp_to_save = pending_highscore["timestamp"]
                # Fallback: falls Pending-Status verloren ging, aber aktueller Score bereits höher ist.
                elif detector.score > highscore_data["score"]:
                    score_to_save = detector.score
                    timestamp_to_save = get_datetime_string()
                    debug_console_only(
                        "[HIGHSCORE] Pending-Status war inaktiv, Speichern via Score-Fallback aktiviert."
                    )
                # Wenn kein neuer Rekord vorliegt, trotzdem Namen auf bestehendem Highscore erlauben.
                elif highscore_data["score"] > 0:
                    score_to_save = highscore_data["score"]
                    timestamp_to_save = highscore_data.get("timestamp", "Unbekannt")
                    debug_console_only(
                        "[HIGHSCORE] Kein neuer Rekord, Name fuer bestehenden Highscore wird aktualisiert."
                    )
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
                        "<p>Du wirst zur Hauptseite zurückgeleitet...</p>"
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
                    response_html = (
                        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                        "<meta http-equiv='refresh' content='2; url=/'>"
                        "<title>Speichern fehlgeschlagen</title></head>"
                        "<body style='font-family:sans-serif;background:#0b0e14;color:#f0f4f8;text-align:center;padding:40px;'>"
                        "<h2>Speichern fehlgeschlagen</h2>"
                        f"<p>{html_escape(error or 'Unbekannter Fehler')}</p>"
                        "<p>Du wirst zur Hauptseite zurückgeleitet...</p>"
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

            write_text_file(DEBUG_EXPORT_FILE_PATH, build_debug_txt_content())
            await send_file_as_download(writer, DEBUG_EXPORT_FILE_PATH, "fpv_debug_log.txt")
            if ENABLE_SERIAL_DEBUG:
                print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [DOWNLOAD-DEBUG] Datei versendet")
            
        else:
            response_html = html_template.encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: text/html\r\n')
            writer.write(b'Content-Length: ' + str(len(response_html)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response_html)
            
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


