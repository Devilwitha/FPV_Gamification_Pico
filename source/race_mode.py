"""Race Spielmodus: misst die Zeit zwischen 2 BLE-Toren (Gate A / Gate B).

Zwei Picos werden als feste Tore (Rolle "gate_a"/"gate_b") aufgestellt und
senden nur einen kurzen Identitaets-Beacon. Ein dritter Pico ("racer") scannt
kontinuierlich nach beiden Toren: sobald Tor A in Reichweite ist, startet der
Rundentimer; sobald danach Tor B in Reichweite kommt, wird die Runde
abgeschlossen und die Zeit gespeichert. Das wiederholt sich fuer die
konfigurierte Rundenzahl. Verwendet die gleiche BLE-Advertise/Scan-Logik wie
infection_mode.py/koth_mode.py, mit eigenem Magic-Byte-Paar.
"""

import asyncio
import bluetooth
import json
import machine
import os
import time

CONFIG_FILE = "race.conf"
DEFAULT_LAPS = 3
DEFAULT_RSSI_THRESHOLD = -55
DEFAULT_COOLDOWN_SECONDS = 3
SCAN_INTERVAL_MS = 800
SCAN_DURATION_MS = 700
ADVERTISEMENT_INTERVAL_US = 100000
GATE_STALE_MS = 1500
COMPANY_ID = b"\xf0\x0f"
PROTOCOL_MAGIC = b"RG"
PROTOCOL_VERSION = 1
GATE_A = 0
GATE_B = 1
_IRQ_SCAN_RESULT = 5
_IRQ_SCAN_DONE = 6


def _ticks_diff(value, reference):
    try:
        return time.ticks_diff(value, reference)
    except Exception:
        return value - reference


def _clamp(value, low, high):
    return max(low, min(high, value))


def _hex_id(raw):
    return "".join("%02x" % value for value in raw)


def _datetime_string():
    now = time.localtime()
    return "%02d.%02d.%04d %02d:%02d:%02d" % (now[2], now[1], now[0], now[3], now[4], now[5])


# Dauerhafter Verlauf abgeschlossener Rennen (gleiches Muster wie
# challenge_helpers.py's fpv_challenge_log.json) - fuer die Statistik-/
# Verlaufs-Ansicht im Dashboard (admin_dashboard.html).
RACE_LOG_FILE = "race_log.json"
RACE_LOG_MAX_ENTRIES = 50


def load_race_log():
    try:
        with open(RACE_LOG_FILE, "r") as f:
            data = json.loads(f.read())
        if isinstance(data, list):
            return data[-RACE_LOG_MAX_ENTRIES:]
    except Exception:
        pass
    return []


def save_race_log(entries):
    trimmed = entries[-RACE_LOG_MAX_ENTRIES:]
    payload = json.dumps(trimmed)
    try:
        tmp_path = RACE_LOG_FILE + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(payload)
        try:
            os.remove(RACE_LOG_FILE)
        except Exception:
            pass
        os.rename(tmp_path, RACE_LOG_FILE)
        return True, ""
    except Exception as e:
        try:
            with open(RACE_LOG_FILE, "w") as f:
                f.write(payload)
            return True, ""
        except Exception as e2:
            return False, f"{e} | fallback={e2}"


class RaceMode:
    def __init__(self, player_name="", log=None):
        self.player_name = str(player_name or "").strip()[:32]
        self.log = log or (lambda _message: None)

        try:
            raw_node_id = bytes(machine.unique_id())
        except Exception:
            raw_node_id = b"pico-sim"
        self.node_raw = (raw_node_id + b"\x00" * 8)[:8]
        self.node_id = _hex_id(self.node_raw)

        self.config = self._load_config()

        self.role = self.config["role"]
        self.running = False
        self.finished = False
        self.last_event = "Bereit"

        self.gate_a_last_seen_ms = 0
        self.gate_a_rssi = None
        self.gate_b_last_seen_ms = 0
        self.gate_b_rssi = None

        self.waiting_for = "A"
        self.lap_index = 0
        self.lap_start_ms = 0
        self.race_start_ms = 0
        self.race_total_ms = 0
        self.last_cross_ms = 0
        self.lap_times = []
        self.scan_done = True
        self.log_entries = load_race_log()

        self.ble = bluetooth.BLE()
        self.ble.active(True)
        self.ble.irq(self._ble_irq)
        self.ble.active(False)

    def _default_config(self):
        return {
            "enabled": False,
            "role": "racer",
            "laps": DEFAULT_LAPS,
            "rssi_threshold": DEFAULT_RSSI_THRESHOLD,
            "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
        }

    def _normalize_config(self, values):
        config = self._default_config()
        if isinstance(values, dict):
            config.update(values)
        config["enabled"] = bool(config.get("enabled", False))
        # "gate_a"/"gate_b" sind nur ueber index_gatehill.html (Geraete-Rolle
        # "gatehill", siehe boot_runtime.py/role_setup.py) tatsaechlich
        # waehlbar - die normale Gamification-Admin-Seite (admin_race.html)
        # zeigt keine Rollen-Auswahl an und schickt daher nie etwas anderes
        # als "racer".
        role = config.get("role")
        config["role"] = role if role in ("gate_a", "gate_b", "racer") else "racer"
        config["laps"] = _clamp(int(config.get("laps", DEFAULT_LAPS)), 1, 99)
        config["rssi_threshold"] = _clamp(int(config.get("rssi_threshold", DEFAULT_RSSI_THRESHOLD)), -95, -20)
        config["cooldown_seconds"] = _clamp(int(config.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)), 1, 30)
        return config

    def _load_config(self):
        try:
            with open(CONFIG_FILE, "r") as config_file:
                return self._normalize_config(json.loads(config_file.read()))
        except Exception:
            return self._default_config()

    def _save_config(self):
        temp_path = CONFIG_FILE + ".tmp"
        with open(temp_path, "w") as output_file:
            output_file.write(json.dumps(self.config))
        try:
            os.remove(CONFIG_FILE)
        except Exception:
            pass
        os.rename(temp_path, CONFIG_FILE)

    def _gate_packet(self, gate_letter):
        payload = COMPANY_ID + PROTOCOL_MAGIC + bytes((PROTOCOL_VERSION, gate_letter)) + self.node_raw
        manufacturer = bytes((len(payload) + 1, 0xFF)) + payload
        return b"\x02\x01\x06" + manufacturer

    def _update_advertisement(self):
        try:
            self.ble.gap_advertise(None)
        except Exception:
            pass
        if not self.running:
            return
        if self.role == "gate_a":
            try:
                self.ble.gap_advertise(ADVERTISEMENT_INTERVAL_US, adv_data=self._gate_packet(GATE_A))
            except Exception:
                pass
        elif self.role == "gate_b":
            try:
                self.ble.gap_advertise(ADVERTISEMENT_INTERVAL_US, adv_data=self._gate_packet(GATE_B))
            except Exception:
                pass

    def _kick_scan(self):
        self.scan_done = False
        try:
            self.ble.gap_scan(SCAN_DURATION_MS, 30000, 30000, False)
        except Exception:
            self.scan_done = True

    def _parse_packet(self, adv_data):
        data = bytes(adv_data)
        index = 0
        while index + 1 < len(data):
            length = data[index]
            if length == 0 or index + length >= len(data):
                break
            ad_type = data[index + 1]
            value = data[index + 2:index + 1 + length]
            if ad_type == 0xFF and len(value) >= 14 and value[:2] == COMPANY_ID and value[2:4] == PROTOCOL_MAGIC:
                if value[4] != PROTOCOL_VERSION:
                    return None
                gate_letter = value[5]
                sender_raw = bytes(value[6:14])
                return (gate_letter, sender_raw)
            index += length + 1
        return None

    def _ble_irq(self, event, data):
        if event == _IRQ_SCAN_RESULT:
            try:
                _addr_type, _addr, _adv_type, rssi, adv_data = data
                packet = self._parse_packet(adv_data)
                if packet is None:
                    return
                gate_letter, sender_raw = packet
                if sender_raw == self.node_raw:
                    return
                now = time.ticks_ms()
                if gate_letter == GATE_A:
                    self.gate_a_last_seen_ms = now
                    self.gate_a_rssi = int(rssi)
                elif gate_letter == GATE_B:
                    self.gate_b_last_seen_ms = now
                    self.gate_b_rssi = int(rssi)
            except Exception:
                pass
        elif event == _IRQ_SCAN_DONE:
            self.scan_done = True

    def _gate_in_range(self, last_seen_ms, rssi, now):
        if not last_seen_ms or rssi is None:
            return False
        if _ticks_diff(now, last_seen_ms) > GATE_STALE_MS:
            return False
        return rssi >= self.config["rssi_threshold"]

    def start_race(self, role=None):
        self.role = role if role in ("gate_a", "gate_b", "racer") else self.config["role"]
        self.running = True
        self.finished = False
        self.last_event = "Gestartet"
        if self.role == "racer":
            self.waiting_for = "A"
            self.lap_index = 0
            self.lap_start_ms = 0
            self.race_start_ms = 0
            self.race_total_ms = 0
            self.last_cross_ms = 0
            self.lap_times = []
        try:
            self.ble.active(True)
            self.ble.irq(self._ble_irq)
        except Exception:
            pass
        self._update_advertisement()
        if self.scan_done:
            self._kick_scan()
        return self.status()

    def stop_race(self, reason="Beendet"):
        self.running = False
        self.last_event = reason
        try:
            self.ble.gap_advertise(None)
        except Exception:
            pass
        try:
            self.ble.gap_scan(None)
        except Exception:
            pass
        try:
            self.ble.active(False)
        except Exception:
            pass
        return self.status()

    def configure(self, values):
        updated = dict(self.config)
        updated.update(values or {})
        self.config = self._normalize_config(updated)
        self._save_config()
        if self.config["enabled"]:
            self.start_race(self.config["role"])
        else:
            self.stop_race("Deaktiviert")
        return self.status()

    def status(self):
        best_lap = min(self.lap_times) if self.lap_times else None
        now = time.ticks_ms()
        if self.finished:
            total_ms = self.race_total_ms
        elif self.running and self.role == "racer" and self.race_start_ms:
            total_ms = _ticks_diff(now, self.race_start_ms)
        else:
            total_ms = 0
        return {
            "ok": True,
            "enabled": self.config["enabled"],
            "running": self.running,
            "finished": self.finished,
            "role": self.role,
            "node_id": self.node_id,
            "last_event": self.last_event,
            "waiting_for": self.waiting_for if self.role == "racer" else None,
            "lap_index": self.lap_index,
            "laps_total": self.config["laps"],
            "lap_times_ms": list(self.lap_times),
            "last_lap_ms": self.lap_times[-1] if self.lap_times else None,
            "best_lap_ms": best_lap,
            "total_ms": total_ms,
            "gate_a_in_range": self._gate_in_range(self.gate_a_last_seen_ms, self.gate_a_rssi, now),
            "gate_b_in_range": self._gate_in_range(self.gate_b_last_seen_ms, self.gate_b_rssi, now),
            "config": dict(self.config),
            "transport": "ble",
        }

    async def run(self):
        while True:
            if not self.running:
                await asyncio.sleep_ms(500)
                continue
            now = time.ticks_ms()

            if self.role == "racer" and not self.finished:
                cooldown_ms = self.config["cooldown_seconds"] * 1000
                in_cooldown = bool(self.last_cross_ms) and _ticks_diff(now, self.last_cross_ms) < cooldown_ms
                if not in_cooldown:
                    if self.waiting_for == "A" and self._gate_in_range(self.gate_a_last_seen_ms, self.gate_a_rssi, now):
                        self.lap_start_ms = now
                        self.last_cross_ms = now
                        self.waiting_for = "B"
                        if self.lap_index == 0:
                            self.race_start_ms = now
                        self.last_event = "Tor A passiert"
                    elif self.waiting_for == "B" and self._gate_in_range(self.gate_b_last_seen_ms, self.gate_b_rssi, now):
                        lap_time = _ticks_diff(now, self.lap_start_ms)
                        self.lap_times.append(lap_time)
                        self.lap_index += 1
                        self.last_cross_ms = now
                        self.last_event = "Tor B passiert"
                        if self.lap_index >= self.config["laps"]:
                            self.finished = True
                            self.race_total_ms = _ticks_diff(now, self.race_start_ms)
                            self.waiting_for = None
                            self.last_event = "Rennen beendet"
                            self.log_entries.append({
                                "ts_s": int(time.time()),
                                "timestamp": _datetime_string(),
                                "total_ms": self.race_total_ms,
                                "laps": self.lap_index,
                                "best_lap_ms": min(self.lap_times) if self.lap_times else None,
                            })
                            if len(self.log_entries) > RACE_LOG_MAX_ENTRIES:
                                del self.log_entries[0: len(self.log_entries) - RACE_LOG_MAX_ENTRIES]
                            save_race_log(self.log_entries)
                        else:
                            self.waiting_for = "A"

            if self.scan_done:
                self.scan_done = False
                try:
                    self.ble.gap_scan(SCAN_DURATION_MS, 30000, 30000, False)
                except Exception as error:
                    self.scan_done = True
                    self.last_event = "BLE-Scanfehler: " + str(error)
            await asyncio.sleep_ms(SCAN_INTERVAL_MS)


async def send_json(writer, payload, status="200 OK"):
    body = json.dumps(payload).encode()
    writer.write(("HTTP/1.1 " + status + "\r\n").encode())
    writer.write(b"Content-Type: application/json\r\n")
    writer.write(b"Cache-Control: no-store\r\n")
    writer.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(body)


async def handle_race_route(writer, request_path, request_method, query_params, body_params, manager):
    if request_path == "/race-log":
        await send_json(writer, {"ok": True, "log": manager.log_entries})
        return True

    if request_path == "/race-log-clear":
        manager.log_entries = []
        ok, err = save_race_log(manager.log_entries)
        await send_json(writer, {"ok": ok, "error": None if ok else err})
        return True

    if request_path == "/race-data":
        await send_json(writer, manager.status())
        return True

    if request_path == "/race-config" and request_method == "POST":
        values = {
            "enabled": body_params.get("enabled", "0") in ("1", "true", "on"),
            "role": body_params.get("role", "racer"),
            "laps": body_params.get("laps", DEFAULT_LAPS),
            "rssi_threshold": body_params.get("rssi_threshold", DEFAULT_RSSI_THRESHOLD),
            "cooldown_seconds": body_params.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS),
        }
        try:
            await send_json(writer, manager.configure(values))
        except Exception as error:
            await send_json(writer, {"ok": False, "error": str(error)}, "400 Bad Request")
        return True

    if request_path == "/race-stop" and request_method == "POST":
        manager.config["enabled"] = False
        try:
            manager._save_config()
        except Exception:
            pass
        manager.stop_race("Manuell beendet")
        await send_json(writer, manager.status())
        return True

    return False
