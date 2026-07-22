import network
import time


def configure_hotspot(ssid, password="", debug_log=None, serial_debug=False):
    """Configure and start Pico AP with one shared code path."""
    ap = network.WLAN(network.AP_IF)

    def _log(message):
        if debug_log is not None:
            try:
                debug_log(message)
                return
            except Exception:
                pass
        if serial_debug:
            try:
                print(f"[AP] {message}")
            except Exception:
                pass

    try:
        _log("Aktiviere Access Point")
        ap.active(True)
        time.sleep_ms(200)

        ssid_set = False
        for attempt in range(3):
            try:
                ap.config(essid=ssid)
                ssid_set = True
                break
            except Exception as ssid_error:
                _log(f"SSID-Setzen fehlgeschlagen (Versuch {attempt + 1}/3): {ssid_error}")
                time.sleep_ms(120)
        if not ssid_set:
            _log("SSID konnte nicht gesetzt werden, AP laeuft mit Standardnamen weiter.")

        if password and len(password) >= 8:
            try:
                ap.config(password=password)
            except Exception as pw_error:
                _log(f"Passwort-Konfiguration nicht verfuegbar, starte offenes WLAN: {pw_error}")
        else:
            _log("Kein Passwort gesetzt, offenes WLAN")

        try:
            ap.config(pm=0xA11140)
        except Exception:
            pass

        ap.ifconfig(("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1"))
        _log(f"WLAN-Hotspot aktiv: SSID={ssid} IP={ap.ifconfig()[0]}")
        return ap
    except Exception as e:
        _log(f"Hotspot-Setup fehlgeschlagen: {e}")
        return ap
