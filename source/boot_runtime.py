import json
import os
import time

BOOT_STATE_FILE = "boot_state.json"
RETRY_FLAG_FILE = "main_retry.flag"
FAIL_WINDOW_MS = 120000
MAX_MAIN_FAILS = 3

# Geraete-Rolle ("gamification" oder "gatehill"), einmalig per Web-Seite
# (siehe role_setup.py) gewaehlt - anders als der Geraetetyp (Pico/Pico2/
# LilyGo, siehe boot.py's detect_device_type()) laesst sich die Rolle NICHT
# aus der Hardware ableiten (beides ist ein normaler Pico W), daher muss sie
# einmalig vom Nutzer gewaehlt und dauerhaft gespeichert werden.
DEVICE_ROLE_FILE = "device_role.json"
VALID_DEVICE_ROLES = ("gamification", "gatehill")


def detect_board_type():
    """Liefert einen menschenlesbaren Hardware-Namen (z.B. "Pico W", "Pico 2 W",
    "Pico", "Pico 2") fuer die System-Info-Anzeige - im Unterschied zu
    boot.py's eigenem detect_device_type() (nur "pico1"/"pico2"/"lilygo"/
    "unknown" fuer die Boot-Routing-Entscheidung) unterscheidet diese Funktion
    zusaetzlich zwischen Wireless- ("W") und Nicht-Wireless-Varianten, indem
    sie den von MicroPython gelieferten Chipnamen auswertet (z.B.
    "Raspberry Pi Pico W with RP2040")."""
    try:
        machine_name = os.uname().machine
    except Exception:
        return "Unbekannt"

    lower = machine_name.lower()
    if "esp32" in lower:
        return "LilyGo (ESP32)"
    if "rp2350" in lower:
        generation = "Pico 2"
    elif "rp2040" in lower:
        generation = "Pico"
    else:
        return machine_name or "Unbekannt"

    board_part = lower.split("with")[0].split()
    is_wireless = bool(board_part) and board_part[-1] == "w"
    return generation + " W" if is_wireless else generation


def get_device_role():
    """Liefert die gespeicherte Geraete-Rolle, oder None, wenn noch keine
    gewaehlt wurde (boot.py startet dann role_setup.py)."""
    try:
        with open(DEVICE_ROLE_FILE, "r") as f:
            data = json.loads(f.read())
        role = data.get("role")
        if role in VALID_DEVICE_ROLES:
            return role
    except Exception:
        pass
    return None


def clear_device_role():
    """Loescht die gespeicherte Geraete-Rolle - der Pico zeigt beim naechsten
    Start wieder role_setup.py, damit die Rolle neu gewaehlt werden kann.
    Aufgerufen ueber den "Rolle zurruecksetzen"-Button in main.py/
    main_gatehill.py's System-Seite (siehe dort /reset-device-role)."""
    try:
        os.remove(DEVICE_ROLE_FILE)
        return True
    except Exception:
        return False


def set_device_role(role):
    """Speichert die Rolle UND liest sie sofort wieder zurueck, bevor Erfolg
    gemeldet wird. Ohne diese Rueck-Verifizierung koennen open()/write()/
    rename() auf manchen frisch geflashten/fragmentierten Flash-Zustaenden
    ohne Exception durchlaufen, obwohl der Inhalt danach nicht zuverlaessig
    lesbar ist - role_setup.py wuerde dem Nutzer dann faelschlich
    "Gespeichert" melden, obwohl boot.py beim naechsten Start trotzdem
    wieder role_setup.py startet (device_role.json scheinbar leer/fehlend).
    2 Versuche, da ein einzelner Fehlversuch direkt nach einem Reflash
    manchmal transient ist."""
    if role not in VALID_DEVICE_ROLES:
        return False

    payload = json.dumps({"role": role})
    for _attempt in range(2):
        try:
            tmp = DEVICE_ROLE_FILE + ".tmp"
            with open(tmp, "w") as f:
                f.write(payload)
            try:
                os.remove(DEVICE_ROLE_FILE)
            except Exception:
                pass
            os.rename(tmp, DEVICE_ROLE_FILE)
        except Exception:
            try:
                with open(DEVICE_ROLE_FILE, "w") as f:
                    f.write(payload)
            except Exception:
                continue

        if get_device_role() == role:
            return True

    return False

_wdt = None


def register_wdt(wdt):
    global _wdt
    _wdt = wdt


def feed_wdt():
    if _wdt is None:
        return
    try:
        _wdt.feed()
    except Exception:
        pass


def _read_state():
    try:
        with open(BOOT_STATE_FILE, "r") as f:
            data = json.loads(f.read())
        return {
            "main_fail_count": int(data.get("main_fail_count", 0)),
            "last_fail_ms": int(data.get("last_fail_ms", 0)),
        }
    except Exception:
        return {"main_fail_count": 0, "last_fail_ms": 0}


def _write_state(state):
    tmp = BOOT_STATE_FILE + ".tmp"
    payload = json.dumps(state)
    try:
        with open(tmp, "w") as f:
            f.write(payload)
        try:
            os.remove(BOOT_STATE_FILE)
        except Exception:
            pass
        os.rename(tmp, BOOT_STATE_FILE)
    except Exception:
        try:
            with open(BOOT_STATE_FILE, "w") as f:
                f.write(payload)
        except Exception:
            pass


def mark_main_attempt_failed_or_unhealthy():
    now_ms = time.ticks_ms()
    state = _read_state()
    last_ms = state.get("last_fail_ms", 0)
    if last_ms and time.ticks_diff(now_ms, last_ms) > FAIL_WINDOW_MS:
        state["main_fail_count"] = 0
    state["main_fail_count"] = int(state.get("main_fail_count", 0)) + 1
    state["last_fail_ms"] = now_ms
    _write_state(state)
    return state["main_fail_count"]


def clear_main_fail_count():
    _write_state({"main_fail_count": 0, "last_fail_ms": 0})


def should_boot_recovery():
    state = _read_state()
    count = int(state.get("main_fail_count", 0))
    last_ms = int(state.get("last_fail_ms", 0))
    if count <= 0:
        return False, count

    if last_ms and time.ticks_diff(time.ticks_ms(), last_ms) > FAIL_WINDOW_MS:
        clear_main_fail_count()
        return False, 0

    return count >= MAX_MAIN_FAILS, count


def request_main_retry_once():
    """Setzt ein einmaliges Flag: beim naechsten Boot main trotz Fail-Lock testen."""
    try:
        with open(RETRY_FLAG_FILE, "w") as f:
            f.write("1")
        return True
    except Exception:
        return False


def consume_main_retry_once():
    """Liest und loescht das Retry-Flag atomar-ish; True bedeutet: Main-Versuch erzwingen."""
    try:
        with open(RETRY_FLAG_FILE, "r"):
            pass
        try:
            os.remove(RETRY_FLAG_FILE)
        except Exception:
            pass
        return True
    except Exception:
        return False
