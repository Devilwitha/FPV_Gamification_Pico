"""King of the Hill (KOTH) Spielmodus.

Ein Pico ist der "Huegel" (Rolle "hill") und sendet einen BLE-Anker-Beacon.
Alle anderen Picos sind Spieler (Rolle "player") und sammeln Punkte, solange
ihr Empfang zum Huegel (RSSI ueber Schwellwert) besteht. Jeder Spieler sendet
zusaetzlich seinen aktuellen Punktestand per BLE, sodass jedes Geraet eine
gemeinsame Bestenliste anzeigen kann (gleiche Advertise+Scan-Logik wie
infection_mode.py, eigenes Magic-Byte-Paar damit sich beide Protokolle nicht
in die Quere kommen).
"""

import asyncio
import bluetooth
import json
import machine
import os
import time

CONFIG_FILE = "koth.conf"
DEFAULT_ROUND_SECONDS = 300
DEFAULT_RSSI_THRESHOLD = -55
DEFAULT_POINTS_PER_SECOND = 1.0
SCAN_INTERVAL_MS = 1500
SCAN_DURATION_MS = 1200
ADVERTISEMENT_INTERVAL_US = 250000
ADV_REFRESH_MS = 2000
ANCHOR_STALE_MS = 3000
LEADERBOARD_STALE_MS = 15000
COMPANY_ID = b"\xf0\x0f"
PROTOCOL_MAGIC = b"KH"
PROTOCOL_VERSION = 1
STATE_ANCHOR = 0
STATE_SCORE = 1
NAME_BYTES = 6
_IRQ_SCAN_RESULT = 5
_IRQ_SCAN_DONE = 6


def _ticks_add(value, delta):
    try:
        return time.ticks_add(value, delta)
    except Exception:
        return value + delta


def _ticks_diff(value, reference):
    try:
        return time.ticks_diff(value, reference)
    except Exception:
        return value - reference


def _clamp(value, low, high):
    return max(low, min(high, value))


def _hex_id(raw):
    return "".join("%02x" % value for value in raw)


def _ascii_bytes(text, length):
    result = bytearray(length)
    idx = 0
    for char in str(text or ""):
        if idx >= length:
            break
        code = ord(char)
        if code < 32 or code > 126:
            continue
        result[idx] = code
        idx += 1
    return bytes(result)


def _ascii_text(raw):
    chars = []
    for value in raw:
        if value == 0:
            break
        if 32 <= value <= 126:
            chars.append(chr(value))
    return "".join(chars)


class KothMode:
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
        self.round_end_ms = 0
        self.last_event = "Bereit"
        self.last_rssi = None
        self.in_range = False
        self.score = 0.0
        self.hill_last_seen_ms = 0
        self.last_score_tick_ms = 0
        self.next_adv_refresh_ms = 0
        self.leaderboard = {}
        self.scan_done = True

        self.ble = bluetooth.BLE()
        self.ble.active(True)
        self.ble.irq(self._ble_irq)
        self.ble.active(False)

    def _default_config(self):
        return {
            "enabled": False,
            "role": "player",
            "round_seconds": DEFAULT_ROUND_SECONDS,
            "rssi_threshold": DEFAULT_RSSI_THRESHOLD,
            "points_per_second": DEFAULT_POINTS_PER_SECOND,
        }

    def _normalize_config(self, values):
        config = self._default_config()
        if isinstance(values, dict):
            config.update(values)
        config["enabled"] = bool(config.get("enabled", False))
        config["role"] = "hill" if config.get("role") == "hill" else "player"
        config["round_seconds"] = _clamp(int(config.get("round_seconds", DEFAULT_ROUND_SECONDS)), 30, 3600)
        config["rssi_threshold"] = _clamp(int(config.get("rssi_threshold", DEFAULT_RSSI_THRESHOLD)), -95, -20)
        try:
            points = float(config.get("points_per_second", DEFAULT_POINTS_PER_SECOND))
        except Exception:
            points = DEFAULT_POINTS_PER_SECOND
        config["points_per_second"] = _clamp(points, 0.1, 100.0)
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

    def remaining_seconds(self):
        if not self.running:
            return 0
        remaining_ms = _ticks_diff(self.round_end_ms, time.ticks_ms())
        return max(0, remaining_ms // 1000)

    def _anchor_packet(self):
        payload = COMPANY_ID + PROTOCOL_MAGIC + bytes((PROTOCOL_VERSION, STATE_ANCHOR)) + self.node_raw
        manufacturer = bytes((len(payload) + 1, 0xFF)) + payload
        return b"\x02\x01\x06" + manufacturer

    def _score_packet(self):
        score_value = int(_clamp(int(self.score), 0, 65535))
        name_bytes = _ascii_bytes(self.player_name or ("node-" + self.node_id), NAME_BYTES)
        payload = (
            COMPANY_ID
            + PROTOCOL_MAGIC
            + bytes((PROTOCOL_VERSION, STATE_SCORE))
            + self.node_raw
            + bytes(((score_value >> 8) & 0xFF, score_value & 0xFF))
            + name_bytes
        )
        manufacturer = bytes((len(payload) + 1, 0xFF)) + payload
        return b"\x02\x01\x06" + manufacturer

    def _update_advertisement(self):
        try:
            self.ble.gap_advertise(None)
        except Exception:
            pass
        if not self.running:
            return
        if self.role == "hill":
            try:
                self.ble.gap_advertise(ADVERTISEMENT_INTERVAL_US, adv_data=self._anchor_packet())
            except Exception:
                pass
        else:
            try:
                self.ble.gap_advertise(ADVERTISEMENT_INTERVAL_US, adv_data=self._score_packet())
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
            if ad_type == 0xFF and len(value) >= 6 and value[:2] == COMPANY_ID and value[2:4] == PROTOCOL_MAGIC:
                if value[4] != PROTOCOL_VERSION:
                    return None
                state = value[5]
                if state == STATE_ANCHOR and len(value) >= 14:
                    return (state, bytes(value[6:14]), None, None)
                if state == STATE_SCORE and len(value) >= 14 + 2 + NAME_BYTES:
                    sender_raw = bytes(value[6:14])
                    score_value = (value[14] << 8) | value[15]
                    name_raw = bytes(value[16:16 + NAME_BYTES])
                    return (state, sender_raw, score_value, name_raw)
            index += length + 1
        return None

    def _ble_irq(self, event, data):
        if event == _IRQ_SCAN_RESULT:
            try:
                _addr_type, _addr, _adv_type, rssi, adv_data = data
                packet = self._parse_packet(adv_data)
                if packet is None:
                    return
                state, sender_raw, score_value, name_raw = packet
                if sender_raw == self.node_raw:
                    return
                now = time.ticks_ms()
                if state == STATE_ANCHOR:
                    self.hill_last_seen_ms = now
                    self.last_rssi = int(rssi)
                elif state == STATE_SCORE:
                    self.leaderboard[_hex_id(sender_raw)] = {
                        "name": _ascii_text(name_raw) or ("node-" + _hex_id(sender_raw)),
                        "score": score_value,
                        "last_seen_ms": now,
                    }
            except Exception:
                pass
        elif event == _IRQ_SCAN_DONE:
            self.scan_done = True

    def start_round(self, role=None):
        self.role = "hill" if role == "hill" else "player"
        self.running = True
        self.score = 0.0
        self.in_range = False
        self.last_rssi = None
        self.hill_last_seen_ms = 0
        self.leaderboard = {}
        self.last_event = "Runde gestartet"
        now = time.ticks_ms()
        self.round_end_ms = _ticks_add(now, self.config["round_seconds"] * 1000)
        self.last_score_tick_ms = now
        self.next_adv_refresh_ms = now
        try:
            self.ble.active(True)
            self.ble.irq(self._ble_irq)
        except Exception:
            pass
        self._update_advertisement()
        if self.scan_done:
            self._kick_scan()
        return self.status()

    def stop_round(self, reason="Runde beendet"):
        self.running = False
        self.last_event = reason
        self.in_range = False
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
            self.start_round(self.config["role"])
        else:
            self.stop_round("Deaktiviert")
        return self.status()

    def _leaderboard_list(self):
        now = time.ticks_ms()
        stale = [
            key for key, info in self.leaderboard.items()
            if _ticks_diff(now, info["last_seen_ms"]) > LEADERBOARD_STALE_MS
        ]
        for key in stale:
            del self.leaderboard[key]
        entries = [
            {"id": node_id, "name": info["name"], "score": info["score"]}
            for node_id, info in self.leaderboard.items()
        ]
        if self.role == "player":
            entries.append({
                "id": self.node_id,
                "name": self.player_name or ("node-" + self.node_id),
                "score": int(self.score),
            })
        entries.sort(key=lambda entry: entry["score"], reverse=True)
        return entries

    def status(self):
        return {
            "ok": True,
            "enabled": self.config["enabled"],
            "running": self.running,
            "role": self.role,
            "remaining_seconds": self.remaining_seconds(),
            "node_id": self.node_id,
            "last_event": self.last_event,
            "last_rssi": self.last_rssi,
            "in_range": self.in_range,
            "score": int(self.score),
            "player_name": self.player_name or ("node-" + self.node_id),
            "leaderboard": self._leaderboard_list(),
            "config": dict(self.config),
            "transport": "ble",
        }

    async def run(self):
        while True:
            if not self.running:
                await asyncio.sleep_ms(500)
                continue
            now = time.ticks_ms()
            if self.remaining_seconds() <= 0:
                self.config["enabled"] = False
                try:
                    self._save_config()
                except Exception:
                    pass
                self.stop_round("Runde beendet")
                continue

            if _ticks_diff(now, self.next_adv_refresh_ms) >= 0:
                self._update_advertisement()
                self.next_adv_refresh_ms = _ticks_add(now, ADV_REFRESH_MS)

            if self.role == "player":
                elapsed_ms = _ticks_diff(now, self.last_score_tick_ms)
                self.last_score_tick_ms = now
                anchor_fresh = bool(self.hill_last_seen_ms) and _ticks_diff(now, self.hill_last_seen_ms) <= ANCHOR_STALE_MS
                self.in_range = bool(
                    anchor_fresh and self.last_rssi is not None and self.last_rssi >= self.config["rssi_threshold"]
                )
                if self.in_range and elapsed_ms > 0:
                    self.score = min(65535.0, self.score + (self.config["points_per_second"] * elapsed_ms) / 1000.0)

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


async def handle_koth_route(writer, request_path, request_method, query_params, body_params, manager):
    if request_path == "/koth-data":
        await send_json(writer, manager.status())
        return True

    if request_path == "/koth-config" and request_method == "POST":
        values = {
            "enabled": body_params.get("enabled", "0") in ("1", "true", "on"),
            "role": body_params.get("role", "player"),
            "round_seconds": body_params.get("round_seconds", DEFAULT_ROUND_SECONDS),
            "rssi_threshold": body_params.get("rssi_threshold", DEFAULT_RSSI_THRESHOLD),
            "points_per_second": body_params.get("points_per_second", DEFAULT_POINTS_PER_SECOND),
        }
        try:
            await send_json(writer, manager.configure(values))
        except Exception as error:
            await send_json(writer, {"ok": False, "error": str(error)}, "400 Bad Request")
        return True

    if request_path == "/koth-stop" and request_method == "POST":
        manager.config["enabled"] = False
        try:
            manager._save_config()
        except Exception:
            pass
        manager.stop_round("Manuell beendet")
        await send_json(writer, manager.status())
        return True

    return False
