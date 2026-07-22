import json
import os
import time

BOOT_STATE_FILE = "boot_state.json"
RETRY_FLAG_FILE = "main_retry.flag"
FAIL_WINDOW_MS = 120000
MAX_MAIN_FAILS = 3

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
