import asyncio
import bluetooth
import json
import machine
import os
import time

CONFIG_FILE = "infection.conf"
LEGACY_CONFIG_FILE = "infection_config.json"
PLAYERS_FILE = "infection_players.conf"
DEFAULT_ROUND_SECONDS = 300
DEFAULT_RSSI_THRESHOLD = -55
DEFAULT_COOLDOWN_SECONDS = 10
SCAN_INTERVAL_MS = 1500
HANDOFF_WINDOW_MS = 12000
ADVERTISEMENT_INTERVAL_US = 250000
SCAN_DURATION_MS = 1200
COMPANY_ID = b"\xf0\x0f"
PROTOCOL_MAGIC = b"IF"
PROTOCOL_VERSION = 1
ROLE_HOST = 1
ROLE_SEEKER = 0
ZERO_NODE_ID = b"\x00" * 8
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


def _raw_id(value):
    text = str(value or "").strip().lower().replace(":", "")
    if len(text) != 16:
        return None
    try:
        return bytes(int(text[index:index + 2], 16) for index in range(0, 16, 2))
    except Exception:
        return None


class InfectionMode:
    def __init__(self, normal_ssid, normal_password, player_name="", log=None):
        self.normal_ssid = normal_ssid
        self.normal_password = normal_password
        self.player_name = str(player_name or "").strip()[:32]
        self.log = log or (lambda _message: None)

        try:
            raw_node_id = bytes(machine.unique_id())
        except Exception:
            raw_node_id = b"pico-sim"
        self.node_raw = (raw_node_id + ZERO_NODE_ID)[:8]
        self.node_id = _hex_id(self.node_raw)
        self.config = self._load_config()
        self.players = self._load_players()

        if self.config["enabled"]:
            self.config["enabled"] = False
            try:
                self._save_config()
            except Exception as error:
                self.log("[INFECTION] Neustart-Status konnte nicht geloescht werden: " + str(error))

        self.role = self.config["initial_role"]
        self.running = False
        self.round_end_ms = 0
        self.cooldown_until_ms = 0
        self.last_peer = ""
        self.last_event = "Bereit"
        self.last_rssi = None
        self.infection_count = 0
        self.round_started_ms = 0
        self.round_result = None
        self.contacts = []
        self.handoff_target = ZERO_NODE_ID
        self.handoff_until_ms = 0
        self.scan_done = True
        self.pending_packet = None

        self.ble = bluetooth.BLE()
        self.ble.active(True)
        self.ble.irq(self._ble_irq)
        self.ble.active(False)

    def _default_config(self):
        return {
            "enabled": False,
            "initial_role": "seeker",
            "round_seconds": DEFAULT_ROUND_SECONDS,
            "rssi_threshold": DEFAULT_RSSI_THRESHOLD,
            "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
        }

    def _normalize_config(self, values):
        config = self._default_config()
        if isinstance(values, dict):
            config.update(values)
        config["enabled"] = bool(config.get("enabled", False))
        config["initial_role"] = "host" if config.get("initial_role") == "host" else "seeker"
        config["round_seconds"] = _clamp(int(config.get("round_seconds", DEFAULT_ROUND_SECONDS)), 30, 3600)
        config["rssi_threshold"] = _clamp(int(config.get("rssi_threshold", DEFAULT_RSSI_THRESHOLD)), -95, -20)
        config["cooldown_seconds"] = _clamp(int(config.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)), 3, 120)
        return config

    def _load_config(self):
        for config_path in (CONFIG_FILE, LEGACY_CONFIG_FILE):
            try:
                with open(config_path, "r") as config_file:
                    config = self._normalize_config(json.loads(config_file.read()))
                if config_path == LEGACY_CONFIG_FILE:
                    self.config = config
                    self._save_config()
                    try:
                        os.remove(LEGACY_CONFIG_FILE)
                    except Exception:
                        pass
                return config
            except Exception:
                pass
        return self._default_config()

    def _save_config(self):
        self._atomic_json_write(CONFIG_FILE, self.config)

    def _normalize_players(self, values):
        players = []
        seen = set()
        if not isinstance(values, list):
            return players
        for entry in values:
            if not isinstance(entry, dict):
                continue
            raw = _raw_id(entry.get("id"))
            if raw is None or raw == self.node_raw:
                continue
            node_id = _hex_id(raw)
            if node_id in seen:
                continue
            seen.add(node_id)
            players.append({"id": node_id, "name": str(entry.get("name") or "").strip()[:32]})
            if len(players) >= 32:
                break
        return players

    def _load_players(self):
        try:
            with open(PLAYERS_FILE, "r") as players_file:
                return self._normalize_players(json.loads(players_file.read()))
        except Exception:
            return []

    def save_players(self, values):
        self.players = self._normalize_players(values)
        self._atomic_json_write(PLAYERS_FILE, self.players)
        return self.status()

    def _atomic_json_write(self, path, value):
        temp_path = path + ".tmp"
        with open(temp_path, "w") as output_file:
            output_file.write(json.dumps(value))
        try:
            os.remove(path)
        except Exception:
            pass
        os.rename(temp_path, path)

    def _player_for(self, raw_node_id):
        node_id = _hex_id(raw_node_id)
        for player in self.players:
            if player["id"] == node_id:
                return player
        return None

    def _player_label(self, raw_node_id):
        player = self._player_for(raw_node_id)
        if player and player.get("name"):
            return player["name"]
        return "node-" + _hex_id(raw_node_id)

    def _advertisement(self):
        role = ROLE_HOST if self.role == "host" else ROLE_SEEKER
        payload = COMPANY_ID + PROTOCOL_MAGIC + bytes((PROTOCOL_VERSION, role)) + self.node_raw + self.handoff_target
        manufacturer = bytes((len(payload) + 1, 0xFF)) + payload
        return b"\x02\x01\x06" + manufacturer

    def _update_advertisement(self):
        try:
            self.ble.gap_advertise(None)
        except Exception:
            pass
        if self.running:
            self.ble.gap_advertise(ADVERTISEMENT_INTERVAL_US, adv_data=self._advertisement())

    def _parse_advertisement(self, adv_data):
        data = bytes(adv_data)
        index = 0
        while index + 1 < len(data):
            length = data[index]
            if length == 0 or index + length >= len(data):
                break
            ad_type = data[index + 1]
            value = data[index + 2:index + 1 + length]
            if ad_type == 0xFF and len(value) >= 22 and value[:2] == COMPANY_ID and value[2:4] == PROTOCOL_MAGIC:
                if value[4] != PROTOCOL_VERSION:
                    return None
                return value[5], bytes(value[6:14]), bytes(value[14:22])
            index += length + 1
        return None

    def _ble_irq(self, event, data):
        if event == _IRQ_SCAN_RESULT:
            try:
                _addr_type, _addr, _adv_type, rssi, adv_data = data
                packet = self._parse_advertisement(adv_data)
                if packet is not None:
                    role, sender, target = packet
                    relevant = (
                        self.role == "seeker" and role == ROLE_HOST
                    ) or (
                        self.role == "host"
                        and role == ROLE_HOST
                        and target == self.node_raw
                    )
                    current = self.pending_packet
                    if relevant and (current is None or int(rssi) > current[3]):
                        self.pending_packet = (role, sender, target, int(rssi))
            except Exception:
                pass
        elif event == _IRQ_SCAN_DONE:
            self.scan_done = True

    def configure(self, values):
        updated = dict(self.config)
        updated.update(values or {})
        self.config = self._normalize_config(updated)
        self._save_config()
        if self.config["enabled"]:
            self.start_round(self.config["initial_role"])
        else:
            self.stop_round("Deaktiviert")
        return self.status()

    def start_round(self, role=None):
        self.role = "host" if role == "host" else "seeker"
        self.running = True
        self.last_peer = ""
        self.last_event = "Runde gestartet"
        self.infection_count = 0
        now = time.ticks_ms()
        self.round_started_ms = now
        self.round_result = None
        self.contacts = []
        self.round_end_ms = _ticks_add(now, self.config["round_seconds"] * 1000)
        self.cooldown_until_ms = _ticks_add(now, self.config["cooldown_seconds"] * 1000)
        self.handoff_target = ZERO_NODE_ID
        self.handoff_until_ms = 0
        self.pending_packet = None
        self.ble.active(True)
        self.ble.irq(self._ble_irq)
        self._update_advertisement()
        self.log("[INFECTION] BLE-Runde gestartet als " + self.role)

    def stop_round(self, reason="Beendet"):
        was_running = self.running
        self.running = False
        self.last_event = reason
        if was_running and self.round_result is None:
            self.round_result = "stopped"
        try:
            self.ble.gap_scan(None)
        except Exception:
            pass
        try:
            self.ble.gap_advertise(None)
        except Exception:
            pass
        try:
            self.ble.active(False)
        except Exception:
            pass
        self.log("[INFECTION] " + reason)

    def _become_host(self, handoff_target=ZERO_NODE_ID):
        self.role = "host"
        self.handoff_target = handoff_target
        self.handoff_until_ms = _ticks_add(time.ticks_ms(), HANDOFF_WINDOW_MS) if handoff_target != ZERO_NODE_ID else 0
        self.cooldown_until_ms = _ticks_add(time.ticks_ms(), self.config["cooldown_seconds"] * 1000)
        self.last_event = "Infiziert - BLE-Host aktiv"
        self._update_advertisement()
        self.log("[INFECTION] Rolle: host")

    def _become_seeker(self):
        self.role = "seeker"
        self.handoff_target = ZERO_NODE_ID
        self.handoff_until_ms = 0
        self.cooldown_until_ms = _ticks_add(time.ticks_ms(), self.config["cooldown_seconds"] * 1000)
        self.last_event = "Suche Infizierten per BLE"
        self._update_advertisement()
        self.log("[INFECTION] Rolle: seeker")

    def _record_contact(self, direction, peer_name, peer_id):
        elapsed_seconds = 0
        if self.round_started_ms:
            elapsed_seconds = max(0, _ticks_diff(time.ticks_ms(), self.round_started_ms) // 1000)
        contact = {
            "direction": direction,
            "peer_name": str(peer_name or "Unbekannter Pilot")[:32],
            "peer_id": str(peer_id or "unknown")[:32],
            "elapsed_seconds": elapsed_seconds,
        }
        self.contacts.append(contact)
        if len(self.contacts) > 32:
            self.contacts.pop(0)
        return contact

    def _handle_packet(self, role, sender, target, rssi):
        if sender == self.node_raw:
            return
        player = self._player_for(sender)
        if player is None:
            return
        self.last_rssi = rssi
        peer_name = self._player_label(sender)
        peer_id = _hex_id(sender)

        if self.role == "host" and role == ROLE_HOST and target == self.node_raw:
            contact = self._record_contact("infected_by_me", peer_name, peer_id)
            self.last_peer = contact["peer_name"]
            self.last_event = "Pilot angesteckt: " + self.last_peer
            self.infection_count += 1
            self._become_seeker()
            return

        now = time.ticks_ms()
        if _ticks_diff(now, self.cooldown_until_ms) < 0:
            return

        if self.role == "seeker" and role == ROLE_HOST and rssi >= self.config["rssi_threshold"]:
            contact = self._record_contact("infected_me", peer_name, peer_id)
            self.last_peer = contact["peer_name"]
            self.last_event = "Angesteckt per BLE - uebernehme Host"
            self.infection_count += 1
            self._become_host(sender)

    def remaining_seconds(self):
        if not self.running:
            return 0
        return max(0, _ticks_diff(self.round_end_ms, time.ticks_ms()) // 1000)

    def status(self):
        return {
            "ok": True,
            "enabled": self.config["enabled"],
            "running": self.running,
            "role": self.role,
            "infected": self.role == "host",
            "remaining_seconds": self.remaining_seconds(),
            "node_id": self.node_id,
            "last_peer": self.last_peer,
            "last_event": self.last_event,
            "last_rssi": self.last_rssi,
            "infection_count": self.infection_count,
            "player_name": self.player_name or ("node-" + self.node_id),
            "round_result": self.round_result,
            "contacts": list(self.contacts),
            "players": list(self.players),
            "config": dict(self.config),
            "transport": "ble",
        }

    def session_summary_text(self):
        if not self.round_result and not self.contacts:
            return ""
        labels = {"won": "GEWONNEN", "lost": "VERLOREN", "stopped": "ABGEBROCHEN"}
        text = "INFECTION-RUNDE\n"
        text += "Ergebnis: " + labels.get(self.round_result, "LAEUFT") + "\n"
        text += "Pilot: " + (self.player_name or ("node-" + self.node_id)) + "\nKontakte:\n"
        if not self.contacts:
            return text + "- Keine direkten Infektionskontakte -\n"
        for contact in self.contacts:
            elapsed = int(contact.get("elapsed_seconds", 0))
            timestamp = "%d:%02d" % (elapsed // 60, elapsed % 60)
            action = "hat mich infiziert" if contact.get("direction") == "infected_me" else "von mir infiziert"
            text += "- %s | %s | %s (%s)\n" % (timestamp, action, contact.get("peer_name", "Unbekannter Pilot"), contact.get("peer_id", "unknown"))
        return text

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
                self.round_result = "lost" if self.role == "host" else "won"
                self.stop_round("Runde beendet")
                continue

            if self.handoff_until_ms and _ticks_diff(now, self.handoff_until_ms) >= 0:
                self.handoff_target = ZERO_NODE_ID
                self.handoff_until_ms = 0
                self._update_advertisement()

            packet = self.pending_packet
            self.pending_packet = None
            if packet is not None:
                self._handle_packet(packet[0], packet[1], packet[2], packet[3])

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


async def handle_infection_route(writer, request_path, request_method, query_params, body_params, manager):
    if request_path == "/infection-data":
        await send_json(writer, manager.status())
        return True

    if request_path == "/infection-config" and request_method == "POST":
        values = {
            "enabled": body_params.get("enabled", "0") in ("1", "true", "on"),
            "initial_role": body_params.get("initial_role", "seeker"),
            "round_seconds": body_params.get("round_seconds", DEFAULT_ROUND_SECONDS),
            "rssi_threshold": body_params.get("rssi_threshold", DEFAULT_RSSI_THRESHOLD),
            "cooldown_seconds": body_params.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS),
        }
        try:
            await send_json(writer, manager.configure(values))
        except Exception as error:
            await send_json(writer, {"ok": False, "error": str(error)}, "400 Bad Request")
        return True

    if request_path == "/infection-players" and request_method == "POST":
        try:
            players = json.loads(body_params.get("players", "[]"))
            await send_json(writer, manager.save_players(players))
        except Exception as error:
            await send_json(writer, {"ok": False, "error": str(error)}, "400 Bad Request")
        return True

    if request_path == "/infection-stop" and request_method == "POST":
        manager.config["enabled"] = False
        try:
            manager._save_config()
        except Exception:
            pass
        manager.stop_round("Manuell beendet")
        await send_json(writer, manager.status())
        return True

    return False
