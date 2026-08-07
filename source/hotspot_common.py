import network
import time
import json


HOTSPOT_CONFIG_FILE = "hotspot.conf"
DEFAULT_HOTSPOT_SSID = "FPV_Gamification_Pico"
DEFAULT_HOTSPOT_PASSWORD = ""

# WLAN-Zielnetz fuer die GitHub-Update-Suche (siehe github_ota_helpers.py):
# getrennt von hotspot.conf, weil es ein FREMDES Netz ist, mit dem sich der
# Pico zeitweise verbindet - nicht der eigene Access Point. Bewusst NICHT
# Teil von build_firmware.py's Bundle-Dateiliste (siehe dortiger Kommentar),
# damit ein Firmware-Update das gespeicherte WLAN nicht wieder loescht.
WLAN_CONFIG_FILE = "wlan.conf"


def load_hotspot_config(config_path=HOTSPOT_CONFIG_FILE):
    config = {
        "ssid": DEFAULT_HOTSPOT_SSID,
        "password": DEFAULT_HOTSPOT_PASSWORD,
    }
    try:
        with open(config_path, "r") as config_file:
            values = json.loads(config_file.read())
        if isinstance(values, dict):
            ssid = str(values.get("ssid") or "").strip()
            password = str(values.get("password") or "")
            if ssid:
                config["ssid"] = ssid[:32]
            if len(password) >= 8:
                config["password"] = password
    except Exception:
        pass
    return config


def load_wlan_config(config_path=WLAN_CONFIG_FILE):
    """Liest das WLAN-Zielnetz fuer die GitHub-Update-Suche. Anders als bei
    load_hotspot_config() ist ein leeres Passwort hier gueltig (offenes
    Heimnetz) - nur eine leere SSID gilt als 'nicht konfiguriert'."""
    config = {"ssid": "", "password": ""}
    try:
        with open(config_path, "r") as config_file:
            values = json.loads(config_file.read())
        if isinstance(values, dict):
            ssid = str(values.get("ssid") or "").strip()
            password = str(values.get("password") or "")
            config["ssid"] = ssid[:32]
            config["password"] = password
    except Exception:
        pass
    return config


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
