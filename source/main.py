import machine
import time
import struct
import network
import asyncio
import json
import os
import gc
from ota_helpers import (
    url_decode,
    parse_query,
    base64_decode,
    read_exact,
    safe_base64_decode_to_file as _ota_safe_base64_decode_to_file,
    safe_base64_file_to_file as _ota_safe_base64_file_to_file,
    apply_firmware_bundle as _ota_apply_firmware_bundle,
    apply_firmware_bundle_from_base64 as _ota_apply_firmware_bundle_from_base64,
)
# HINWEIS: `from challenge_helpers import ChallengeManager` bewusst NICHT hier
# (Modul-Top-Level) - challenge_helpers.py ist inzwischen ein grosses Modul
# (~50KB+), dessen Kompilierung sich sonst mit main.py's eigener Kompilierung
# WAEHREND desselben riskanten `import main` (boot.py) ueberlagert und die
# Heap-Fragmentierung dieses Schritts erhoeht (siehe echte Haerdware-Crashes
# in den Projektnotizen: "memory allocation failed" waehrend `import main`).
# Stattdessen lazy per _ensure_challenges() in main_async() importiert -
# EIN eigener, spaeterer Kompilierschritt, NACHDEM `import main` bereits
# erfolgreich zurueckgekehrt ist.
try:
    from hotspot_common import configure_hotspot, load_hotspot_config, load_wlan_config
except Exception:
    # Kompakter Fallback fuer den Fall, dass hotspot_common.py fehlt.
    def load_hotspot_config():
        return {"ssid": "FPV_Gamification_Pico", "password": "drohnenspiel"}

    def load_wlan_config():
        return {"ssid": "", "password": ""}

    def configure_hotspot(ssid, password="", debug_log=None, serial_debug=False):
        ap = network.WLAN(network.AP_IF)
        try:
            ap.active(True)
            time.sleep_ms(120)
            ap.config(essid=ssid)

            if password and len(password) >= 8:
                try:
                    ap.config(password=password)
                except Exception:
                    pass

            try:
                ap.config(pm=0xA11140)
            except Exception:
                pass

            ap.ifconfig(("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1"))
            if serial_debug:
                print(f"[AP] Fallback aktiv: SSID={ssid} IP={ap.ifconfig()[0]}")
            return ap
        except Exception as e:
            if serial_debug:
                print(f"[AP] Fallback Fehler: {e}")
            return ap

try:
    import boot_runtime
except Exception:
    boot_runtime = None

# Trick-Tuning-Profile-Verwaltung (Datei-I/O + eingebaute Profile) ist nach
# trick_profile_helpers.py ausgelagert (siehe dortiger Modul-Docstring) - hier
# bewusst weiterhin EAGER importiert (main.py's apply_trick_tuning_profile()
# unten braucht die Profile schon vor dem ersten HTTP-Request), aber als
# eigener, kleinerer Kompilierschritt statt Teil von main.py's grosser Datei.
from trick_profile_helpers import (
    TRICK_TUNING_PROFILES,
    normalize_trick_tuning_profile,
    load_trick_tuning_profile_name,
    save_trick_tuning_profile_name,
    list_profile_files,
    get_profile_data as _tph_get_profile_data,
    save_custom_profile as _tph_save_custom_profile,
    delete_custom_profile as _tph_delete_custom_profile,
)

# ==================== CONFIGURATION ====================
ENABLE_HOTSPOT = True        
ENABLE_SERIAL_DEBUG = True   
ENABLE_LIVE_GYRO_DEBUG = True
ENABLE_TRICK_GYRO_IN_TXT_LOG = True
TRICK_TUNING_PROFILE = "aggressive"

_HOTSPOT_CONFIG = load_hotspot_config()
AP_SSID = _HOTSPOT_CONFIG["ssid"]
AP_PASSWORD = _HOTSPOT_CONFIG["password"]
COPTER_NAME = "Test"
DEFAULT_PILOT_NAME = "Test"
COPIL_FILE_PATH = "copil"
COPIL_DEFAULT_NAME = "Test"

# UART wird lazy initialisiert, damit beim Import kein zusaetzlicher
# zusammenhaengender RAM-Block benoetigt wird.
UART_BAUDRATE = 420000
UART_RX_BUF = 1024
uart = None

# CRSF Konstanten
CRSF_ADDRESS_FLIGHT_CONTROLLER = 0xC8
CRSF_FRAMETYPE_ATTITUDE        = 0x1E  
# Zusaetzliche, PASSIV ueber dieselbe TX/RX-Datenleitung mitgeschnittene
# Telemetrie-Frametypen fuer die Real-Time-Challenges (siehe challenge_helpers.py).
# Nicht jeder FC/ELRS-Aufbau sendet diese Frames - falls nicht, bleiben die
# Challenges einfach ohne Sensordaten (kein Crash, siehe telemetry_loop()).
CRSF_FRAMETYPE_VARIO           = 0x07  # Sinkrate/Steigrate in cm/s (int16)
CRSF_FRAMETYPE_BATTERY_SENSOR  = 0x08  # Spannung/Strom/verbrauchte Kapazitaet/Restladung
CRSF_FRAMETYPE_GPS             = 0x02  # Lat/Lon/Geschwindigkeit/Kurs/Hoehe/Satelliten (fuer Speed-Run)
CRSF_FRAMETYPE_LINK_STATISTICS = 0x14  # RSSI/Link-Qualitaet/SNR (fuer Signal-Helden)

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
SYSTEM_SETTINGS_FILE_PATH = "fpv_system_settings.json"
LED_BLINK_INTERVAL_MS     = 220
OTA_LED_BLINK_INTERVAL_MS = 90
# =======================================================


def _load_or_create_copil_names():
    default_payload = {
        "copter_name": COPIL_DEFAULT_NAME,
        "pilot_name": COPIL_DEFAULT_NAME,
    }

    try:
        with open(COPIL_FILE_PATH, "r") as f:
            data = json.loads(f.read())
        copter_name = str(data.get("copter_name", COPIL_DEFAULT_NAME)).strip() or COPIL_DEFAULT_NAME
        pilot_name = str(data.get("pilot_name", COPIL_DEFAULT_NAME)).strip() or COPIL_DEFAULT_NAME
        return copter_name, pilot_name
    except Exception:
        try:
            with open(COPIL_FILE_PATH, "w") as f:
                f.write(json.dumps(default_payload))
        except Exception:
            pass
        return COPIL_DEFAULT_NAME, COPIL_DEFAULT_NAME


def _save_copil_names(copter_name, pilot_name):
    global COPTER_NAME, DEFAULT_PILOT_NAME, highscore_data

    copter_name = str(copter_name or "").strip() or COPIL_DEFAULT_NAME
    pilot_name = str(pilot_name or "").strip() or COPIL_DEFAULT_NAME
    payload = {
        "copter_name": copter_name,
        "pilot_name": pilot_name,
    }

    try:
        tmp_path = COPIL_FILE_PATH + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(json.dumps(payload))
        try:
            os.remove(COPIL_FILE_PATH)
        except Exception:
            pass
        os.rename(tmp_path, COPIL_FILE_PATH)
    except Exception as e:
        return False, str(e)

    old_default = DEFAULT_PILOT_NAME
    COPTER_NAME = copter_name
    DEFAULT_PILOT_NAME = pilot_name
    try:
        if int(highscore_data.get("score", 0)) <= 0:
            highscore_data["player"] = pilot_name
        elif str(highscore_data.get("player", "")).strip() == str(old_default).strip():
            highscore_data["player"] = pilot_name
    except Exception:
        pass

    return True, ""


def _get_copil_payload():
    return {
        "copter_name": COPTER_NAME,
        "pilot_name": DEFAULT_PILOT_NAME,
    }


COPTER_NAME, DEFAULT_PILOT_NAME = _load_or_create_copil_names()

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
ota_led_cycle_start_ms = 0
github_ota_led_cycle_start_ms = 0
boot_health_marked = False
_idcard_route_handler = None
_misc_route_handler = None
_challenge_route_handler = None
_infection_route_handler = None
_upload_helpers_module = None
_github_ota_helpers_module = None
_license_verifier_module = None
# None = noch nicht geprueft (wird beim ersten Request lazy berechnet und
# danach gecacht, da eine RSA-Signaturpruefung zu teuer fuer jeden Request
# waere). Nach einem Lizenz-Upload wird der Cache explizit invalidiert
# (siehe _refresh_license_status()), damit die Sperre ohne Neustart faellt.
_LICENSE_STATUS = None
infection_manager = None
infection_task = None
# Developer-Modus (Schiebeschalter auf der System-Seite): standardmaessig AUS,
# dann akzeptiert OTA nur komplette firmware.nbo Bundles. Erst wenn aktiviert,
# duerfen auch einzelne .py/.html Dateien per OTA hochgeladen werden.
DEVELOPER_MODE_ENABLED = False
LANGUAGE_CODE = "de"
# Wird beim ersten Erkennen einer gueltigen Lizenz einmalig auf True gesetzt,
# sobald das Dankeschoen-Popup vom Nutzer bestaetigt wurde (siehe
# _get_license_thanks_pending()/_confirm_license_thanks()). Persistiert in
# fpv_system_settings.json, damit das Popup nach einem Neustart nicht erneut
# erscheint.
LICENSE_THANKS_SHOWN = False

# ==================== OTA CHUNK STORAGE ====================
# Als ein Dict statt vier einzelner Globals gehalten, damit es unveraendert
# (by reference) an die lazy importierten upload_helpers.py Funktionen
# durchgereicht werden kann (siehe _build_upload_deps()/_get_upload_helpers()).
ota_state = {
    "update_active": False,
    "total_chunks": 0,
    "received_chunks": 0,
    "target_file": "main.py",
}
OTA_STAGING_PATH = "ota_staging.tmp"
FIRMWARE_VERSION_FILE = "firmware_version.txt"
# Nur diese Dateien duerfen per OTA ueberschrieben werden (kein Path-Traversal,
# keine beliebigen Dateinamen vom Client).
OTA_ALLOWED_TARGETS = (
    "boot.py", "recovery.py", "hotspot_common.py", "hotspot.conf", "wlan.conf", "boot_runtime.py",
    "ota_helpers.py",
    "update_manager.py",
    "license_verifier.py",
    "github_ota_helpers.py",
    "idcard_helpers.py",
    "misc_routes_helpers.py",
    "upload_helpers.py",
    "challenge_helpers.py",
    "infection_mode.py",
    "copil",
    "main.py", "index.html",
    "admin_dashboard.html", "admin_update.html", "admin_simulate.html",
    "admin_profiles.html", "admin_system.html", "admin_idcard.html", "admin_challenges.html",
    "admin_infection.html", "admin_credits.html",
    "challenges_view.html", "infection_view.html",
    "de.pak", "en.pak", "es.pak", "fr.pak", "it.pak", "pt.pak", "tr.pak",
    FIRMWARE_VERSION_FILE,
)
# Spezial-Ziel: ein Firmware-Bundle (siehe build_firmware.py), das mehrere
# der obigen Dateien in einem Rutsch aktualisiert. Wird in /finalize-upload
# gesondert behandelt (entpackt statt direkt umbenannt).
OTA_BUNDLE_TARGET = "firmware.nbo"
OTA_LANG_BUNDLE_TARGET = "lang.pak"
OTA_BUNDLE_MAGIC = b"FPVBNDL1"
# license.lic und public_key.pem werden wie die Bundle-Ziele behandelt (immer
# erlaubt, unabhaengig vom Developer-Modus) - siehe upload_helpers.py und
# update_manager.py. public_key.pem muss hier hochladbar sein, weil ohne
# gueltige Lizenz nur die System-Seite erreichbar ist (siehe
# _LICENSE_GATE_ALLOWED_PATHS) - fehlt oder ist der Public Key defekt, waere
# er sonst nur per seriellem Dateizugriff (Thonny) zu ersetzen.
LICENSE_UPLOAD_TARGETS = ("license.lic", "public_key.pem")

# ==================== GITHUB-OTA ("Nach Updates suchen") ====================
# Siehe github_ota_helpers.py: verbindet sich kurzzeitig mit wlan.conf,
# prueft die neueste GitHub-Release und installiert firmware.nbo bei Bedarf.
GITHUB_REPO_OWNER = "Devilwitha"
GITHUB_REPO_NAME = "FPV_Gamification_Pico"
GITHUB_OTA_ASSET_NAME = "firmware.nbo"
GITHUB_OTA_STAGING_PATH = "github_update.nbo"
GITHUB_OTA_LED_BLINK_INTERVAL_MS = 2000
# Als Dict statt Einzel-Globals gehalten, damit es unveraendert (by
# reference) an github_ota_helpers.run_update_check() durchgereicht werden
# kann (gleiches Muster wie main.py's ota_state fuer upload_helpers.py).
github_ota_state = {
    "active": False,
    "phase": "idle",
    "ok": None,
    "error": "",
    "progress": 0,
    "remote_version": "",
    "restart_pending": False,
}

# Versionsnummer der Firmware (Format X.Y.Z), wird von build_firmware.py bei
# jedem Bundle-Build automatisch um 1 erhoeht und in firmware_version.txt
# abgelegt. Fallback "0.0.0", falls die Datei (noch) fehlt.
def _load_firmware_version():
    try:
        with open(FIRMWARE_VERSION_FILE, "r") as f:
            version = f.read().strip()
        return version or "0.0.0"
    except Exception:
        return "0.0.0"


FIRMWARE_VERSION = _load_firmware_version()

gc.collect()


def _ensure_uart_initialized():
    global uart
    if uart is not None:
        return True
    try:
        uart = machine.UART(0, baudrate=UART_BAUDRATE, tx=machine.Pin(0), rx=machine.Pin(1), rxbuf=UART_RX_BUF)
        return True
    except Exception as e:
        debug_console_only(f"[UART] Initialisierung fehlgeschlagen: {e}")
        return False


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


def load_trick_tuning_profile():
    global TRICK_TUNING_PROFILE
    TRICK_TUNING_PROFILE = load_trick_tuning_profile_name()


def save_trick_tuning_profile():
    return save_trick_tuning_profile_name(TRICK_TUNING_PROFILE)


def load_system_settings():
    global DEVELOPER_MODE_ENABLED, LANGUAGE_CODE, LICENSE_THANKS_SHOWN
    try:
        with open(SYSTEM_SETTINGS_FILE_PATH, 'r') as f:
            data = json.loads(f.read())
        DEVELOPER_MODE_ENABLED = bool(data.get("developer_mode", False))
        lang = str(data.get("language", "de")).strip().lower()
        LANGUAGE_CODE = lang or "de"
        LICENSE_THANKS_SHOWN = bool(data.get("license_thanks_shown", False))
    except Exception:
        DEVELOPER_MODE_ENABLED = False
        LANGUAGE_CODE = "de"
        LICENSE_THANKS_SHOWN = False


def save_system_settings():
    payload = json.dumps({
        "developer_mode": DEVELOPER_MODE_ENABLED,
        "language": LANGUAGE_CODE,
        "license_thanks_shown": LICENSE_THANKS_SHOWN,
    })
    try:
        tmp_path = SYSTEM_SETTINGS_FILE_PATH + ".tmp"
        with open(tmp_path, 'w') as f:
            f.write(payload)

        try:
            os.remove(SYSTEM_SETTINGS_FILE_PATH)
        except Exception:
            pass

        os.rename(tmp_path, SYSTEM_SETTINGS_FILE_PATH)
        return True, ""
    except Exception as e:
        try:
            with open(SYSTEM_SETTINGS_FILE_PATH, 'w') as f:
                f.write(payload)
            return True, ""
        except Exception as e2:
            return False, f"{e} | fallback={e2}"


# Duenne Wrapper um trick_profile_helpers.py's gleichnamige Funktionen, die
# main.py's eigenes debug_log() als optionalen Log-Callback durchreichen -
# list_profile_files() braucht keinen Wrapper (kein Logging noetig) und wird
# oben direkt importiert.
def get_profile_data(profile_name):
    return _tph_get_profile_data(profile_name, debug_log)


def save_custom_profile(profile_name, profile_data):
    return _tph_save_custom_profile(profile_name, profile_data, debug_log)


def delete_custom_profile(profile_name):
    return _tph_delete_custom_profile(profile_name, debug_log)


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
    global status_led_last_toggle_ms, ota_led_cycle_start_ms, github_ota_led_cycle_start_ms
    if not status_led_available:
        return

    if not system_ready:
        _set_status_led(False)
        return

    # Hoechste Prioritaet: GitHub-Update-Suche laeuft (2s an/2s aus). Dient
    # nur als Fallback/Cosmetic fuer die kurzen Momente, in denen
    # telemetry_loop() ueberhaupt zum Zug kommt - die eigentliche Garantie
    # fuer den Rhythmus liefert der led_tick()-Callback direkt in
    # github_ota_helpers.py's blockierenden Schleifen (siehe dortiger
    # Docstring: waehrend WLAN-Verbindungsaufbau/Download laeuft
    # telemetry_loop() gar nicht).
    if github_ota_state["active"]:
        now = time.ticks_ms()
        if github_ota_led_cycle_start_ms == 0:
            github_ota_led_cycle_start_ms = now
            _set_status_led(True)
        elif time.ticks_diff(now, github_ota_led_cycle_start_ms) >= GITHUB_OTA_LED_BLINK_INTERVAL_MS:
            _set_status_led(not status_led_state)
            github_ota_led_cycle_start_ms = now
        return
    else:
        github_ota_led_cycle_start_ms = 0

    if ota_state["update_active"]:
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
        try:
            existing_size = os.stat(DEBUG_LOG_FILE_PATH)[6]
        except Exception:
            existing_size = 0

        marker = DEBUG_LOG_BOOT_MARKER if existing_size == 0 else "\n" + DEBUG_LOG_BOOT_MARKER
        if existing_size + len(marker) <= DEBUG_LOG_FILE_MAX_BYTES:
            with open(DEBUG_LOG_FILE_PATH, 'a') as f:
                f.write(marker)
            debug_log_file_bytes = existing_size + len(marker)
        else:
            debug_log_file_bytes = existing_size
            debug_log_file_limit_reached = True
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


def _boot_feed_watchdog():
    if boot_runtime is None:
        return
    try:
        boot_runtime.feed_wdt()
    except Exception:
        pass


def _boot_mark_healthy_once():
    global boot_health_marked
    if boot_health_marked or boot_runtime is None:
        return
    try:
        boot_runtime.clear_main_fail_count()
        boot_health_marked = True
        debug_log("[BOOT] Main-Start als gesund markiert")
    except Exception:
        pass


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


# url_decode, parse_query, base64_decode, read_exact sind jetzt in
# ota_helpers.py (siehe Import oben) - Auslagerung reduziert main.py's
# Groesse, um Heap-Fragmentierung beim Kompilieren (`import main` in
# boot.py) zu verringern, siehe ota_helpers.py Docstring fuer Details.
# Die folgenden zwei Funktionen sind duenne Wrapper: sie reichen
# main.py's debug_log() als log-Callback an ota_helpers weiter, damit
# Fehler weiterhin im Debug-Log landen, ohne main.py und ota_helpers.py
# gegenseitig voneinander importieren zu muessen (zirkulaerer Import).

def safe_base64_decode_to_file(b64_string, output_file):
    return _ota_safe_base64_decode_to_file(b64_string, output_file, log=debug_log)


def safe_base64_file_to_file(input_file, output_file):
    return _ota_safe_base64_file_to_file(input_file, output_file, log=debug_log, feed_wdt=_boot_feed_watchdog)


def apply_firmware_bundle(bundle_path):
    """Entpackt ein per build_firmware.py erzeugtes Firmware-Bundle
    (firmware.nbo) - duenner Wrapper um ota_helpers.apply_firmware_bundle()
    mit main.py's eigener OTA_ALLOWED_TARGETS/OTA_BUNDLE_MAGIC/debug_log."""
    return _ota_apply_firmware_bundle(bundle_path, OTA_ALLOWED_TARGETS, OTA_BUNDLE_MAGIC, log=debug_log, feed_wdt=_boot_feed_watchdog)


def apply_firmware_bundle_from_base64(base64_path):
    """Wie apply_firmware_bundle(), entpackt aber direkt aus der noch
    base64-kodierten Datei (z.B. 'update.pbp') ohne kompletten dekodierten
    Zwischenstand auf dem Flash - duenner Wrapper um
    ota_helpers.apply_firmware_bundle_from_base64()."""
    return _ota_apply_firmware_bundle_from_base64(base64_path, OTA_ALLOWED_TARGETS, OTA_BUNDLE_MAGIC, log=debug_log, feed_wdt=_boot_feed_watchdog)


def _get_license_verifier():
    """Lazy-Import wie _get_upload_helpers() - haelt license_verifier.py's
    (kleinen, aber RSA-Modexp-haltigen) Code aus dem `import main`-Pfad
    heraus, bis er tatsaechlich fuer eine Lizenzpruefung gebraucht wird."""
    global _license_verifier_module
    if _license_verifier_module is None:
        import license_verifier as _lazy_license_verifier
        _license_verifier_module = _lazy_license_verifier
    return _license_verifier_module


def _refresh_license_status():
    """Erzwingt eine erneute Signaturpruefung (nach Lizenz-Upload aufgerufen,
    damit die Sperre ohne Neustart faellt)."""
    global _LICENSE_STATUS
    try:
        _LICENSE_STATUS = _get_license_verifier().verify()
    except Exception:
        _LICENSE_STATUS = "INVALID"
    return _LICENSE_STATUS


def _get_license_status():
    """Gecachter Lizenzstatus (VALID/INVALID/MISSING) - eine RSA-2048-
    Signaturpruefung pro Request waere spuerbar langsam, daher nur einmal
    (oder nach Upload) neu berechnet."""
    global _LICENSE_STATUS
    if _LICENSE_STATUS is None:
        _refresh_license_status()
    return _LICENSE_STATUS


def _get_license_thanks_pending():
    """True genau bis zur ersten Bestaetigung: sobald die Lizenz erstmals als
    VALID erkannt wurde und der Nutzer das Dankeschoen-Popup noch nicht ueber
    /confirm-license-thanks bestaetigt hat (siehe index.html)."""
    return _get_license_status() == "VALID" and not LICENSE_THANKS_SHOWN


def _confirm_license_thanks():
    """Bestaetigt das Dankeschoen-Popup dauerhaft (persistiert in
    fpv_system_settings.json), damit es nach dieser einen Bestaetigung nie
    wieder erscheint."""
    global LICENSE_THANKS_SHOWN
    LICENSE_THANKS_SHOWN = True
    return save_system_settings()


# Wird ohne gueltige Lizenz weiterhin bedient - die System-Seite selbst und
# alles, was sie zum Anzeigen/Hochladen/Zuruecksetzen braucht. Alles andere
# wird stattdessen auf die System-Seite umgeleitet (siehe handle_client()).
_LICENSE_GATE_ALLOWED_PATHS = frozenset((
    "/admin-system",
    "/admin-credits",
    "/system-info",
    "/i18n-data",
    "/language-packs",
    "/set-language",
    "/hotspot-config",
    "/set-hotspot-config",
    "/wlan-config",
    "/set-wlan-config",
    "/restart-pico",
    "/reset-device-role",
    "/clear-debug-log",
    "/clear-session-log",
    "/set-developer-mode",
    "/prepare-upload",
    "/upload-chunk",
    "/finalize-upload",
    "/emergency-delete-main",
    "/emergency-delete-boot",
))


def _get_upload_helpers():
    """Lazy-Import fuer upload_helpers.py (Chunk-Upload/Finalize/Restart/
    Notaus-Loeschen) - dieser ~350-Zeilen-Codeblock wurde aus main.py
    extrahiert, weil er main.py's eigene Kompiliergroesse waehrend des
    riskanten `import main` in boot.py unnoetig vergroesserte (siehe
    Speicherfragmentierungs-Crashes in den Projektnotizen). Wird erst beim
    ersten tatsaechlichen Upload-/Restart-/Notaus-Request importiert."""
    global _upload_helpers_module
    if _upload_helpers_module is None:
        import upload_helpers as _lazy_upload_helpers
        _upload_helpers_module = _lazy_upload_helpers
    return _upload_helpers_module


def _build_upload_deps():
    """Baut das deps-Dict, das upload_helpers.py's Funktionen benoetigen -
    siehe dortige cleanup_update_artifacts()/handle_*() Signaturen."""
    return {
        "debug_log": debug_log,
        "url_decode": url_decode,
        "developer_mode_enabled": DEVELOPER_MODE_ENABLED,
        "ota_bundle_target": OTA_BUNDLE_TARGET,
        "ota_lang_bundle_target": OTA_LANG_BUNDLE_TARGET,
        "ota_allowed_targets": OTA_ALLOWED_TARGETS,
        "license_upload_targets": LICENSE_UPLOAD_TARGETS,
        "refresh_license_status": _refresh_license_status,
        "ota_staging_path": OTA_STAGING_PATH,
        "firmware_version_file": FIRMWARE_VERSION_FILE,
        "apply_firmware_bundle_from_base64": apply_firmware_bundle_from_base64,
        "safe_base64_file_to_file": safe_base64_file_to_file,
    }


async def _perform_emergency_delete_main(writer):
    await _get_upload_helpers().handle_emergency_delete_main(writer, _build_upload_deps())


async def _perform_emergency_delete_boot(writer):
    await _get_upload_helpers().handle_emergency_delete_boot(writer, _build_upload_deps())


def _get_github_ota_helpers():
    """Lazy-Import fuer github_ota_helpers.py ("Nach Updates suchen") -
    gleiches Muster/gleicher Grund wie _get_upload_helpers(). Wird erst beim
    ersten tatsaechlichen /start-github-update-Request importiert."""
    global _github_ota_helpers_module
    if _github_ota_helpers_module is None:
        import github_ota_helpers as _lazy_github_ota_helpers
        _github_ota_helpers_module = _lazy_github_ota_helpers
    return _github_ota_helpers_module


def _github_ota_led_tick():
    """Schaltet die LED direkt um (2s-Rhythmus), OHNE ueber
    update_status_led()/telemetry_loop() zu gehen - waehrend
    github_ota_helpers.py's blockierenden WLAN-/HTTPS-Schleifen laeuft die
    normale Task sowieso nicht (siehe dortiger Docstring)."""
    global github_ota_led_cycle_start_ms
    now = time.ticks_ms()
    if github_ota_led_cycle_start_ms == 0:
        github_ota_led_cycle_start_ms = now
        _set_status_led(True)
    elif time.ticks_diff(now, github_ota_led_cycle_start_ms) >= GITHUB_OTA_LED_BLINK_INTERVAL_MS:
        _set_status_led(not status_led_state)
        github_ota_led_cycle_start_ms = now


def _build_github_ota_deps():
    """Baut das deps-Dict fuer github_ota_helpers.run_update_check() -
    siehe dortige Signatur/Docstring."""
    return {
        "log": debug_log,
        "feed_wdt": _boot_feed_watchdog,
        "led_tick": _github_ota_led_tick,
        "load_wlan_config": load_wlan_config,
        "configure_hotspot": configure_hotspot,
        "ap_ssid": AP_SSID,
        "ap_password": AP_PASSWORD,
        "firmware_version": FIRMWARE_VERSION,
        "apply_firmware_bundle": apply_firmware_bundle,
        "repo_owner": GITHUB_REPO_OWNER,
        "repo_name": GITHUB_REPO_NAME,
        "asset_name": GITHUB_OTA_ASSET_NAME,
        "staging_path": GITHUB_OTA_STAGING_PATH,
        "state": github_ota_state,
    }


async def _run_github_ota_update():
    """Wird per asyncio.create_task() aus der /start-github-update Route
    gestartet, NACHDEM deren HTTP-Antwort bereits verschickt wurde (siehe
    dortiger sleep_ms-Kommentar). github_ota_helpers.run_update_check() ist
    komplett synchron/blockierend (siehe dessen Docstring) - der Aufruf
    hier blockiert daher den kompletten Event-Loop (Webserver, Telemetrie)
    fuer die Dauer der WLAN-Verbindung/des Downloads. Das ist beabsichtigt:
    waehrenddessen ist der eigene Access Point ohnehin deaktiviert, es gibt
    also niemanden, der bedient werden muesste."""
    global github_ota_led_cycle_start_ms
    github_ota_state["active"] = True
    github_ota_state["phase"] = "connecting_wifi"
    github_ota_state["ok"] = None
    github_ota_state["error"] = ""
    github_ota_state["progress"] = 0
    github_ota_state["restart_pending"] = False
    github_ota_led_cycle_start_ms = 0
    try:
        _get_github_ota_helpers().run_update_check(_build_github_ota_deps())
    except Exception as e:
        debug_log(f"[GH-OTA] Unerwarteter Fehler: {e}")
        github_ota_state["phase"] = "error"
        github_ota_state["ok"] = False
        github_ota_state["error"] = str(e)[:120]
    finally:
        github_ota_state["active"] = False
        _set_status_led(True)

    if github_ota_state.get("restart_pending"):
        await asyncio.sleep_ms(1500)
        debug_log("[GH-OTA] Update erfolgreich, starte machine.reset()...")
        machine.reset()


def build_session_txt_content():
    strings = {}
    fallback = 'en'
    selected = (LANGUAGE_CODE or 'de').strip().lower()

    try:
        with open(fallback + '.pak', 'r') as f:
            parsed_base = json.loads(f.read())
        if isinstance(parsed_base, dict):
            strings = dict(parsed_base)
    except Exception:
        strings = {}

    if selected and selected != fallback:
        try:
            with open(selected + '.pak', 'r') as f:
                parsed_selected = json.loads(f.read())
            if isinstance(parsed_selected, dict):
                for k, v in parsed_selected.items():
                    strings[k] = v
        except Exception:
            pass

    def tx(key, default):
        val = strings.get(key)
        if isinstance(val, str) and val:
            return val
        return default

    points_unit = tx('session.pointsUnit', 'PKT')
    txt_content = "========================================\n"
    txt_content += f"   {COPTER_NAME.upper()} {tx('session.header', 'ARCADE SESSION')}\n"
    txt_content += "========================================\n\n"
    txt_content += tx('session.tricks', 'GELANDETE TRICKS:') + "\n"

    if detector.trick_history:
        for trick in detector.trick_history:
            txt_content += f"- {trick}\n"
    else:
        txt_content += f"- {tx('session.noTricks', 'Keine Tricks aufgezeichnet')} -\n"

    if infection_manager is not None:
        infection_summary = infection_manager.session_summary_text()
        if infection_summary:
            txt_content += "\n----------------------------------------\n" + infection_summary

    txt_content += "\n----------------------------------------\n"
    txt_content += f"{tx('session.totalScore', 'GESAMT-PUNKTESTAND')}: {detector.score} {points_unit}\n"
    txt_content += f"{tx('session.highscore', 'HIGHSCORE')}: {highscore_data['score']} {points_unit}\n"
    txt_content += f"{tx('session.highscoreTime', 'HIGHSCORE DATUM/ZEIT')}: {highscore_data['timestamp']}\n"
    txt_content += f"{tx('session.highscorePilot', 'HIGHSCORE PILOT')}: {highscore_data.get('player', DEFAULT_PILOT_NAME)}\n"
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


# Dauerhafter Verlauf ERREICHTER Trick-Highscores (im Gegensatz zu
# highscore_data, das nur den AKTUELLEN Rekord haelt und bei jedem neuen
# Rekord ueberschrieben wird) - fuer die Statistik-/Verlaufs-Ansicht im
# Dashboard (admin_dashboard.html). load_trick_highscore_log()/
# save_trick_highscore_log() leben bewusst in misc_routes_helpers.py statt
# hier (lazy importiert, siehe unten) - main.py's eigene Kompiliergroesse
# beim riskanten `import main` in boot.py ist der limitierende Faktor auf
# schwaecheren Pico-Boards, nicht die Laufzeit-Leistung (siehe echte
# "memory allocation failed"-Abstuerze, die genau deswegen in den
# Projektnotizen dokumentiert sind).
TRICK_HIGHSCORE_LOG_MAX_ENTRIES = 50
trick_highscore_log_entries = []


def _load_trick_highscore_log_lazy():
    global trick_highscore_log_entries
    try:
        from misc_routes_helpers import load_trick_highscore_log as _lazy_load
        trick_highscore_log_entries = _lazy_load()
    except Exception:
        trick_highscore_log_entries = []


def _record_trick_highscore_log_entry():
    """Traegt den AKTUELLEN Inhalt von highscore_data dauerhaft in den
    Verlauf ein - wird direkt nach jedem erfolgreichen save_highscore()
    aufgerufen, das highscore_data tatsaechlich mit einem NEUEN Rekord
    ueberschrieben hat (siehe die Aufrufstellen in misc_routes_helpers.py's
    _send_highscore_name_response()/_send_confirm_highscore_response(), als
    "record_trick_highscore_log_entry" per deps-Dict durchgereicht)."""
    global trick_highscore_log_entries
    trick_highscore_log_entries.append({
        "ts_s": int(time.time()),
        "timestamp": highscore_data.get("timestamp", "Unbekannt"),
        "score": highscore_data.get("score", 0),
        "player": highscore_data.get("player", DEFAULT_PILOT_NAME),
    })
    if len(trick_highscore_log_entries) > TRICK_HIGHSCORE_LOG_MAX_ENTRIES:
        del trick_highscore_log_entries[0: len(trick_highscore_log_entries) - TRICK_HIGHSCORE_LOG_MAX_ENTRIES]
    try:
        from misc_routes_helpers import save_trick_highscore_log as _lazy_save
        _lazy_save(trick_highscore_log_entries)
    except Exception:
        pass


init_debug_log_file()
load_highscore()
_load_trick_highscore_log_lazy()
init_status_led()
load_trick_tuning_profile()
apply_trick_tuning_profile()
load_system_settings()


def start_access_point():
    configure_hotspot(
        AP_SSID,
        AP_PASSWORD,
        debug_log=lambda m: debug_log(f"[AP] {m}"),
        serial_debug=ENABLE_SERIAL_DEBUG,
    )


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
        self.challenge_history = []

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
                # Bewusst mit "+" statt impliziter Literal-Aneinanderreihung:
                # MicroPython (mpy-cross) unterstuetzt keine direkt
                # aneinandergereihten f-Strings (zwei f-String-Literale ohne
                # "+" dazwischen ergeben einen SyntaxError beim Kompilieren).
                debug_log(
                    f"Trick beendet: {self.trick_type} | Dauer={duration:.2f}s | " +
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

            # Meldet den erkannten Trick zusaetzlich an eine evtl. laufende
            # Trick-Challenge (challenges.trick) - vergibt bei Treffer einen
            # Bonus, ohne die normale Trick-Punktevergabe hier zu veraendern.
            challenges.update_trick_detected(detected_name, time.ticks_ms())

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
                f"Trick verworfen: Typ={eff_type} | dom={dominant_value:.0f} ratio={dominant_ratio:.2f} | " +
                f"R={self.accumulated_roll:.0f} P={self.accumulated_pitch:.0f} Y={self.accumulated_yaw:.0f}"
            )


detector = LiveGyroTrickDetector()


def _add_challenge_score(points, description):
    """Callback fuer ChallengeManager: vergibt Punkte auf denselben Gesamt-Score
    wie die Trick-Erkennung und pflegt Highscore/Verlauf identisch mit
    LiveGyroTrickDetector.evaluate_trick()'s Punktevergabe."""
    global highscore_data, pending_highscore
    if points <= 0:
        return
    detector.score += points
    timestamp = time.ticks_ms() / 1000.0
    detector.challenge_history.append(f"[{timestamp:.1f}s] {description} (+{points} Pkt)")
    if len(detector.challenge_history) > 30:
        detector.challenge_history.pop(0)
    debug_log(f"[CHALLENGE] {description}: +{points} Punkte | Gesamt-Score: {detector.score}")

    # Bestandene Challenge dauerhaft in Datei protokollieren (ueberlebt Neustart),
    # im Gegensatz zu detector.challenge_history, das nur im RAM lebt.
    challenges.record_result(description, points, get_datetime_string())

    if detector.score > highscore_data["score"]:
        if (not pending_highscore["active"]) or detector.score > pending_highscore["score"]:
            pending_highscore["active"] = True
            pending_highscore["score"] = detector.score
            pending_highscore["timestamp"] = get_datetime_string()
            debug_console_only(
                f"[HIGHSCORE] Neuer Rekord entdeckt: {pending_highscore['score']} Pkt. Bitte im Web bestaetigen."
            )


challenges = None


def _ensure_challenges():
    """Lazy-Init fuer den ChallengeManager (siehe Kommentar oben bei den
    Imports) - importiert/kompiliert challenge_helpers.py erst hier, in einem
    eigenen Schritt NACH dem riskanten `import main`, statt eager am
    Modul-Top-Level. Muss vor dem ersten Telemetrie-/HTTP-Request aufgerufen
    werden (siehe main_async())."""
    global challenges
    if challenges is None:
        # gc.collect() direkt vor dem Import/Kompilieren eines grossen Moduls -
        # gleiches Muster wie boot.py's gc.collect() vor `import main`, um dem
        # Compiler moeglichst viel zusammenhaengenden freien Heap zu geben.
        gc.collect()
        from challenge_helpers import ChallengeManager as _ChallengeManager
        challenges = _ChallengeManager(add_score=_add_challenge_score, log=debug_log)
    return challenges


def _ensure_infection_manager():
    global infection_manager
    if infection_manager is None:
        gc.collect()
        from infection_mode import InfectionMode
        infection_manager = InfectionMode(AP_SSID, AP_PASSWORD, DEFAULT_PILOT_NAME, debug_log)
    return infection_manager


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
            _boot_feed_watchdog()
            update_status_led()
            if not _ensure_uart_initialized():
                await asyncio.sleep_ms(250)
                continue
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
                                challenges.update_attitude(
                                    max(abs(detector.f_gyro_x), abs(detector.f_gyro_y), abs(detector.f_gyro_z)),
                                    time.ticks_ms(),
                                )
                                challenges.update_heading(yaw_deg, time.ticks_ms())

                                if ENABLE_SERIAL_DEBUG and detector.in_trick:
                                    debug_live_gyro_trick(
                                        f"[LIVE GYRO TRICK] Roll: {roll_deg:6.1f} Grad | " +
                                        f"Pitch: {pitch_deg:6.1f} Grad | Yaw: {yaw_deg:6.1f} Grad"
                                    )

                            elif frame_type == CRSF_FRAMETYPE_VARIO and len(payload_buffer) == 2:
                                (vspeed_cm_s,) = struct.unpack('>h', payload_buffer)
                                challenges.update_vario(vspeed_cm_s, time.ticks_ms())

                            elif frame_type == CRSF_FRAMETYPE_BATTERY_SENSOR and len(payload_buffer) == 8:
                                voltage_raw, current_raw = struct.unpack('>HH', payload_buffer[0:4])
                                capacity_used_mah = (payload_buffer[4] << 16) | (payload_buffer[5] << 8) | payload_buffer[6]
                                remaining_pct = payload_buffer[7]
                                challenges.update_battery(
                                    capacity_used_mah, voltage_raw / 10.0, current_raw / 10.0, remaining_pct, time.ticks_ms()
                                )

                            elif frame_type == CRSF_FRAMETYPE_LINK_STATISTICS and len(payload_buffer) == 10:
                                (
                                    _up_rssi1, _up_rssi2, uplink_lq, _up_snr,
                                    _active_ant, _rf_mode, _up_tx_power,
                                    _down_rssi, _down_lq, _down_snr,
                                ) = struct.unpack('>BBBbBBBBBb', payload_buffer)
                                challenges.update_link_stats(uplink_lq, time.ticks_ms())

                            elif frame_type == CRSF_FRAMETYPE_GPS and len(payload_buffer) == 15:
                                (_lat, _lon, groundspeed_raw, _heading_raw, _alt_raw, _sats) = struct.unpack(
                                    '>iiHHHB', payload_buffer
                                )
                                speed_kmh = groundspeed_raw / 10.0
                                challenges.update_gps(speed_kmh, time.ticks_ms())
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
# WICHTIG: Die Hauptseite wird als Datei gestreamt; Copter/Pilot-Daten werden
# clientseitig per /copil-info geladen, ohne dass die HTML-Datei serverseitig
# per String-Replacement im RAM bearbeitet werden muss.
#
# Admin-Bereich (Unterseiten, wie ein Einstellungen-Menue):
#   /admin           -> Dashboard/Uebersicht mit Links zu den Unterseiten
#   /admin-update    -> OTA-Update (Datei-Upload)
#   /admin-simulate  -> Trick-Simulation (Testen ohne Drohne)
#   /admin-profiles  -> Trick-Tuning-Profile verwalten
#   /admin-system    -> System-Info + manueller Restart
#   /admin-idcard    -> FPV-Ausweisbild verwalten
INDEX_HTML_PATH = "index.html"
ADMIN_DASHBOARD_HTML_PATH = "admin_dashboard.html"
ADMIN_UPDATE_HTML_PATH = "admin_update.html"
ADMIN_SIMULATE_HTML_PATH = "admin_simulate.html"
ADMIN_PROFILES_HTML_PATH = "admin_profiles.html"
ADMIN_SYSTEM_HTML_PATH = "admin_system.html"
ADMIN_IDCARD_HTML_PATH = "admin_idcard.html"
ADMIN_CHALLENGES_HTML_PATH = "admin_challenges.html"
ADMIN_INFECTION_HTML_PATH = "admin_infection.html"
ADMIN_CREDITS_HTML_PATH = "admin_credits.html"
# Oeffentliche, huebsch gestaltete Live-Visualisierung der Challenges (kein
# Login noetig, gleiche Zielgruppe wie index.html - im Gegensatz zu
# admin_challenges.html, das die technischen Start/Stopp-Controls enthaelt).
CHALLENGES_VIEW_HTML_PATH = "challenges_view.html"
INFECTION_VIEW_HTML_PATH = "infection_view.html"


async def send_html_file(writer, file_path):
    """Streamt eine HTML-Datei vom Dateisystem, ohne den kompletten Inhalt
    als einzelnen RAM-String zu halten (verhindert MemoryError bei grossen
    Admin-Seiten)."""
    gc.collect()
    file_size = os.stat(file_path)[6]
    writer.write(b'HTTP/1.1 200 OK\r\n')
    writer.write(b'Content-Type: text/html; charset=utf-8\r\n')
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
    gc.collect()


# _build_redirect_html() und die drei Highscore-HTTP-Antwort-Builder
# (set-highscore-name / confirm-highscore / reset-highscore) leben jetzt in
# misc_routes_helpers.py - sie wurden ohnehin nur von dort (per deps-
# Callback) aufgerufen, siehe Kommentar dort. Verringert main.py's eigene
# Kompiliergroesse beim riskanten `import main` in boot.py um ~200 Zeilen.


async def _handle_misc_routes(writer, request_path, request_method, query_params, body_text, body_params):
    global TRICK_TUNING_PROFILE, DEVELOPER_MODE_ENABLED, LANGUAGE_CODE
    global _idcard_route_handler, _misc_route_handler, _challenge_route_handler
    global _infection_route_handler

    if request_path == '/admin-idcard':
        await send_html_file(writer, ADMIN_IDCARD_HTML_PATH)
        return True

    if request_path == '/admin-challenges':
        await send_html_file(writer, ADMIN_CHALLENGES_HTML_PATH)
        return True

    if request_path == '/admin-infection':
        await send_html_file(writer, ADMIN_INFECTION_HTML_PATH)
        return True

    if request_path == '/admin-credits':
        await send_html_file(writer, ADMIN_CREDITS_HTML_PATH)
        return True

    if request_path.startswith('/infection-') or request_path.startswith('/lobby-'):
        if _infection_route_handler is None:
            from infection_mode import handle_infection_route as _lazy_infection_route_handler
            _infection_route_handler = _lazy_infection_route_handler
        if await _infection_route_handler(
            writer,
            request_path,
            request_method,
            query_params,
            body_params,
            _ensure_infection_manager(),
        ):
            return True

    import gmr
    if await gmr.handle_admin_and_routes(writer, request_path, request_method, query_params, body_params): return True

    if request_path == '/challenges-view':
        await send_html_file(writer, CHALLENGES_VIEW_HTML_PATH)
        return True

    if request_path == '/infection-view':
        await send_html_file(writer, INFECTION_VIEW_HTML_PATH)
        return True

    if request_path.startswith('/challenge') or request_path.startswith('/mission') or request_path == '/missions-list':
        if _challenge_route_handler is None:
            from challenge_helpers import handle_challenge_route as _lazy_challenge_route_handler
            _challenge_route_handler = _lazy_challenge_route_handler
        if await _challenge_route_handler(
            writer,
            request_path,
            request_method,
            query_params,
            body_params,
            challenges,
        ):
            return True

    if request_path.startswith('/idcard-'):
        if _idcard_route_handler is None:
            from idcard_helpers import handle_idcard_route as _lazy_idcard_route_handler
            _idcard_route_handler = _lazy_idcard_route_handler
        if await _idcard_route_handler(
            writer,
            request_path,
            request_method,
            body_text,
            body_params,
            url_decode,
            safe_base64_file_to_file,
        ):
            return True

    if _misc_route_handler is None:
        from misc_routes_helpers import handle_misc_routes as _lazy_misc_route_handler
        _misc_route_handler = _lazy_misc_route_handler

    def _save_system_settings_with_values(enabled=None, language=None):
        global DEVELOPER_MODE_ENABLED, LANGUAGE_CODE
        if enabled is not None:
            DEVELOPER_MODE_ENABLED = bool(enabled)
        if language is not None:
            LANGUAGE_CODE = str(language).strip().lower() or "de"
        return save_system_settings()

    def _get_language_code():
        return LANGUAGE_CODE

    def _set_language_code(language):
        global LANGUAGE_CODE
        LANGUAGE_CODE = str(language).strip().lower() or "de"

    def _is_allowed_language(code):
        lc = str(code).strip().lower()
        if not lc:
            return False
        try:
            os.stat(lc + ".pak")
            return True
        except Exception:
            return False

    def _list_language_codes():
        codes = []
        try:
            for name in os.listdir():
                if name.endswith('.pak') and len(name) > 4:
                    code = name[:-4].strip().lower()
                    if code and code not in codes:
                        codes.append(code)
        except Exception:
            pass
        if not codes:
            codes = ["de", "en"]
        return codes

    def _activate_trick_profile(profile_name):
        global TRICK_TUNING_PROFILE
        normalized = normalize_trick_tuning_profile(profile_name)
        TRICK_TUNING_PROFILE = normalized
        apply_trick_tuning_profile()
        saved_ok, save_error = save_trick_tuning_profile()
        return saved_ok, save_error, TRICK_TUNING_PROFILE

    handled, updated_profile, updated_developer_mode, updated_language_code = await _misc_route_handler(
        writer,
        request_path,
        request_method,
        query_params,
        body_text,
        body_params,
        TRICK_TUNING_PROFILE,
        DEVELOPER_MODE_ENABLED,
        LANGUAGE_CODE,
        {
            "send_html_file": send_html_file,
            "admin_profiles_html_path": ADMIN_PROFILES_HTML_PATH,
            "admin_system_html_path": ADMIN_SYSTEM_HTML_PATH,
            "ap_ssid": AP_SSID,
            "enable_hotspot": ENABLE_HOTSPOT,
            "detector": detector,
            "highscore_data": highscore_data,
            "pending_highscore": pending_highscore,
            "default_pilot_name": DEFAULT_PILOT_NAME,
            "firmware_version": FIRMWARE_VERSION,
            "ota_update_active": ota_state["update_active"],
            "ota_received_chunks": ota_state["received_chunks"],
            "ota_total_chunks": ota_state["total_chunks"],
            "list_profile_files": list_profile_files,
            "get_copil_payload": _get_copil_payload,
            "save_copil_names": _save_copil_names,
            "save_custom_profile": save_custom_profile,
            "get_profile_data": get_profile_data,
            "delete_custom_profile": delete_custom_profile,
            "activate_trick_profile": _activate_trick_profile,
            "debug_log": debug_log,
            "debug_console_only": debug_console_only,
            "save_system_settings": _save_system_settings_with_values,
            "get_language_code": _get_language_code,
            "set_language_code": _set_language_code,
            "is_allowed_language": _is_allowed_language,
            "list_language_codes": _list_language_codes,
            "get_datetime_string": get_datetime_string,
            "html_escape": html_escape,
            "save_highscore": save_highscore,
            "record_trick_highscore_log_entry": _record_trick_highscore_log_entry,
            "enable_serial_debug": ENABLE_SERIAL_DEBUG,
            "write_text_file": write_text_file,
            "session_export_file_path": SESSION_EXPORT_FILE_PATH,
            "build_session_txt_content": build_session_txt_content,
            "send_file_as_download": send_file_as_download,
            "build_debug_export_file": build_debug_export_file,
            "debug_export_file_path": DEBUG_EXPORT_FILE_PATH,
            "init_debug_log_file": init_debug_log_file,
            "simulate_trick": simulate_trick,
            "perform_emergency_delete_main": _perform_emergency_delete_main,
            "perform_emergency_delete_boot": _perform_emergency_delete_boot,
            "infection_status": _ensure_infection_manager().status,
            "trick_highscore_log_entries": trick_highscore_log_entries,
            "device_role": "gamification",
            "boot_runtime": boot_runtime,
            "license_status": _get_license_status(),
            "license_thanks_pending": _get_license_thanks_pending(),
            "confirm_license_thanks": _confirm_license_thanks,
        },
    )

    TRICK_TUNING_PROFILE = updated_profile
    DEVELOPER_MODE_ENABLED = updated_developer_mode
    LANGUAGE_CODE = updated_language_code
    return handled


async def handle_client(reader, writer):
    global TRICK_TUNING_PROFILE, DEVELOPER_MODE_ENABLED, LANGUAGE_CODE
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

        # Lizenzsperre (nur Geraete-Rolle "gamification" - main_gatehill.py hat
        # diese Pruefung bewusst nicht): ohne gueltige Lizenz wird ausschliesslich
        # die System-Seite (samt ihrer eigenen Endpunkte, u.a. fuer den Lizenz-
        # Upload selbst) bedient, alles andere bekommt ebenfalls die System-Seite
        # statt der eigentlich angefragten Seite/Route.
        if request_path not in _LICENSE_GATE_ALLOWED_PATHS and _get_license_status() != "VALID":
            await send_html_file(writer, ADMIN_SYSTEM_HTML_PATH)
            return

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

        elif request_path == '/prepare-upload':
            await _get_upload_helpers().handle_prepare_upload(writer, query_params, body_params, ota_state, _build_upload_deps())

        elif request_path == '/upload-chunk' and request_method == 'POST':
            await _get_upload_helpers().handle_upload_chunk(writer, body_text, body_params, ota_state, _build_upload_deps())

        elif request_path == '/finalize-upload':
            await _get_upload_helpers().handle_finalize_upload(writer, ota_state, _build_upload_deps())

        elif request_path == '/restart-pico':
            await _get_upload_helpers().handle_restart_pico(writer, _build_upload_deps())

        elif request_path == '/start-github-update':
            if github_ota_state["active"]:
                payload = json.dumps({"ok": False, "error": "Update-Suche laeuft bereits"}).encode('utf-8')
                writer.write(b'HTTP/1.1 409 Conflict\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(payload)
            else:
                wlan_cfg = load_wlan_config()
                if not wlan_cfg.get('ssid'):
                    payload = json.dumps({"ok": False, "error": "Kein WLAN konfiguriert (siehe System-Seite)"}).encode('utf-8')
                    writer.write(b'HTTP/1.1 400 Bad Request\r\n')
                    writer.write(b'Content-Type: application/json\r\n')
                    writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
                    writer.write(b'Connection: close\r\n\r\n')
                    writer.write(payload)
                else:
                    payload = json.dumps({"ok": True, "message": "Update-Suche gestartet"}).encode('utf-8')
                    writer.write(b'HTTP/1.1 200 OK\r\n')
                    writer.write(b'Content-Type: application/json\r\n')
                    writer.write(b'Cache-Control: no-store\r\n')
                    writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
                    writer.write(b'Connection: close\r\n\r\n')
                    writer.write(payload)
                    # Antwort MUSS den Client erreichen, bevor der Access
                    # Point gleich deaktiviert wird (gleiche Begruendung wie
                    # der sleep_ms() vor machine.reset() in upload_helpers.py).
                    await writer.drain()
                    await asyncio.sleep_ms(300)
                    asyncio.create_task(_run_github_ota_update())

        elif request_path == '/github-update-status':
            status_payload = dict(github_ota_state)
            status_payload["firmware_version"] = FIRMWARE_VERSION
            payload = json.dumps(status_payload).encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
            writer.write(b'Pragma: no-cache\r\n')
            writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(payload)

        elif await _handle_misc_routes(writer, request_path, request_method, query_params, body_text, body_params):
            pass

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
    global system_ready, status_led_last_toggle_ms, infection_task
    # Lazy-Import/Init von ChallengeManager (challenge_helpers.py) - ganz am
    # Anfang, damit dieser separate Kompilierschritt VOR dem Start des
    # HTTP-Servers/Telemetrie-Loops abgeschlossen ist, aber unabhaengig von
    # main.py's eigenem `import main`-Schritt in boot.py laeuft.
    _ensure_challenges()
    # infection_mode.py/gmr.py hier lazy importieren, SOLANGE der Heap noch
    # sauber ist - AP-Start und asyncio.start_server() unten belegen danach
    # WLAN-Treiber-/Socket-Puffer, die den Heap fragmentieren. Wurde dieser
    # Import erst NACH AP+HTTP-Server ausgefuehrt, schlug das Kompilieren von
    # infection_mode.py auf dem Pico W schon bei einer kleinen Allokation
    # (2344 Bytes) mit "memory allocation failed" fehl.
    _ensure_infection_manager()
    import gmr
    if ENABLE_HOTSPOT:
        boot_present = False
        try:
            os.stat("boot.py")
            boot_present = True
        except OSError:
            boot_present = False

        if boot_present:
            debug_log("[BOOT] boot.py gefunden, Hotspot-Start in main uebersprungen.")
        else:
            debug_log("[BOOT] boot.py fehlt, starte Hotspot aus main.")
            start_access_point()

        await asyncio.start_server(handle_client, "0.0.0.0", 80)
    infection_task = asyncio.create_task(_ensure_infection_manager().run())
    gmr.start_tasks()
    _boot_mark_healthy_once()
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