import machine
import time
import network

try:
    from hotspot_common import configure_hotspot
except Exception:
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

AP_SSID = "FPV_Gamification_Pico"
AP_PASSWORD = "drohnenspiel"
ENABLE_SERIAL_DEBUG = True
BOOT_WDT_TIMEOUT_MS = 8000


def debug_log(msg):
    if ENABLE_SERIAL_DEBUG:
        print(f"[BOOT] [{time.ticks_ms() // 1000}s] {msg}")


def start_shared_hotspot():
    configure_hotspot(
        AP_SSID,
        AP_PASSWORD,
        debug_log=lambda m: debug_log(f"[AP] {m}"),
        serial_debug=ENABLE_SERIAL_DEBUG,
    )


def run_recovery(reason):
    debug_log(f"Wechsle auf recovery.py: {reason}")
    try:
        import recovery
    except Exception as e:
        debug_log(f"Recovery Start fehlgeschlagen: {e}")
        try:
            debug_log("Versuche main_backup.py als Notfall-Fallback...")
            import main_backup
        except Exception as e2:
            debug_log(f"main_backup.py Start fehlgeschlagen: {e2}")


# AP so frueh wie moeglich starten, damit das Geraet im Fehlerfall erreichbar bleibt.
start_shared_hotspot()

# Watchdog aktivieren: Wenn main haengt und nicht mehr feedet, rebootet der Pico.
try:
    wdt = machine.WDT(timeout=BOOT_WDT_TIMEOUT_MS)
    boot_runtime.register_wdt(wdt)
    boot_runtime.feed_wdt()
    debug_log("Watchdog aktiviert")
except Exception as e:
    debug_log(f"Watchdog nicht verfuegbar: {e}")

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
    try:
        import main
    except Exception as e:
        debug_log(f"main.py Crash: {e}")
        run_recovery("main.py Exception")
