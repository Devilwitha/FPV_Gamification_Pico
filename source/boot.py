import machine
import time
import network
import gc
import os

try:
    from hotspot_common import configure_hotspot, load_hotspot_config
except Exception:
    def load_hotspot_config():
        return {"ssid": "FPV_Gamification_Pico", "password": "drohnenspiel"}

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

import boot_runtime

_HOTSPOT_CONFIG = load_hotspot_config()
AP_SSID = _HOTSPOT_CONFIG["ssid"]
AP_PASSWORD = _HOTSPOT_CONFIG["password"]
ENABLE_SERIAL_DEBUG = True
BOOT_WDT_TIMEOUT_MS = 8000
LILYGO_MARKER_FILE = "lilygo.device"


def debug_log(msg):
    if ENABLE_SERIAL_DEBUG:
        print(f"[BOOT] [{time.ticks_ms() // 1000}s] {msg}")


def debug_log_mem(label):
    """Diagnose-Hilfe: loggt freien/zugewiesenen Heap an einer bestimmten
    Boot-Phase, um Speicherengpaesse vor "import main" nachvollziehen zu
    koennen (z.B. bei wiederholten "memory allocation failed"-Abstuerzen)."""
    if not ENABLE_SERIAL_DEBUG:
        return
    try:
        gc.collect()
        debug_log(f"[MEM] {label}: frei={gc.mem_free()}B belegt={gc.mem_alloc()}B")
    except Exception as e:
        debug_log(f"[MEM] {label}: nicht verfuegbar ({e})")


def start_shared_hotspot():
    configure_hotspot(
        AP_SSID,
        AP_PASSWORD,
        debug_log=lambda m: debug_log(f"[AP] {m}"),
        serial_debug=ENABLE_SERIAL_DEBUG,
    )


def detect_device_type():
    try:
        machine_name = os.uname().machine.lower()
    except Exception:
        machine_name = ""

    try:
        os.stat(LILYGO_MARKER_FILE)
        has_lilygo_marker = True
    except Exception:
        has_lilygo_marker = False

    if has_lilygo_marker and "esp32" in machine_name:
        return "lilygo"
    if "rp2350" in machine_name or "pico 2" in machine_name:
        return "pico2"
    if "rp2040" in machine_name or "pico" in machine_name:
        return "pico1"
    return "unknown"


def run_lilygo():
    debug_log("LilyGO-Marker erkannt: starte main_LilyGo.py")
    gc.collect()
    try:
        import main_LilyGo
    except Exception as e:
        debug_log(f"main_LilyGo.py Crash: {e}")
        while True:
            time.sleep(60)


def run_recovery(reason):
    debug_log(f"Wechsle auf recovery.py: {reason}")
    # Vor dem (Kompilieren+Ausfuehren von) recovery.py den Heap defragmentieren -
    # gleicher Grund wie vor "import main" unten (siehe Kommentar dort).
    gc.collect()
    try:
        import recovery
    except Exception as e:
        debug_log(f"Recovery Start fehlgeschlagen: {e}")
        try:
            debug_log("Versuche main_backup.py als Notfall-Fallback...")
            gc.collect()
            import main_backup
        except Exception as e2:
            debug_log(f"main_backup.py Start fehlgeschlagen: {e2}")


try:
    import sys
    debug_log(f"[SYS] {sys.implementation}")
except Exception:
    pass

device_type = detect_device_type()
debug_log(f"Geraetetyp erkannt: {device_type}")

if device_type == "lilygo":
    run_lilygo()
else:
    debug_log_mem("vor AP-Start")

    # AP so frueh wie moeglich starten, damit das Geraet im Fehlerfall erreichbar bleibt.
    start_shared_hotspot()
    debug_log_mem("nach AP-Start")

    # Watchdog aktivieren: Wenn main haengt und nicht mehr feedet, rebootet der Pico.
    try:
        wdt = machine.WDT(timeout=BOOT_WDT_TIMEOUT_MS)
        boot_runtime.register_wdt(wdt)
        boot_runtime.feed_wdt()
        debug_log("Watchdog aktiviert")
    except Exception as e:
        debug_log(f"Watchdog nicht verfuegbar: {e}")
    debug_log_mem("nach Watchdog-Setup")

    force_main_retry = False
    try:
        force_main_retry = boot_runtime.consume_main_retry_once()
    except Exception:
        force_main_retry = False

    if force_main_retry:
        debug_log("Recovery-Flag erkannt: main.py wird einmalig erneut versucht.")
        try:
            boot_runtime.clear_main_fail_count()
        except Exception:
            pass

    should_recovery, fail_count = boot_runtime.should_boot_recovery()
    if should_recovery and not force_main_retry:
        run_recovery(f"zu viele Main-Fehler ({fail_count})")
    else:
        boot_runtime.mark_main_attempt_failed_or_unhealthy()
        # main.py ist sehr gross (>90KB Quelltext); das Kompilieren beim Import
        # braucht deutlich mehr RAM als der resultierende Bytecode selbst. Nach
        # AP-Start + Watchdog-Setup + boot_state.json-I/O ist der Heap oft schon
        # fragmentiert genug, dass selbst kleine Allokationen (wenige KB) waehrend
        # des Kompilierens fehlschlagen ("memory allocation failed") - deshalb hier
        # explizit aufraeumen, bevor der teure Import beginnt.
        gc.collect()
        debug_log_mem("direkt vor import main")
        try:
            import main
        except Exception as e:
            debug_log(f"main.py Crash: {e}")
            debug_log_mem("direkt nach main.py Crash")
            run_recovery("main.py Exception")
