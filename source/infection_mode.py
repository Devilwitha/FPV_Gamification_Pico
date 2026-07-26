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
CLAIM_WINDOW_MS = 5000
HOST_STALE_MS = 6000


LOBBY_MAGIC = b"IL"   # separate magic for lobby-broadcast packets (discovery)

GAME_MODES_LIST = ["bomb", "infect"]  # UI-Dropdown-Werte für game_mode

DISCOVERY_DURATION_MS = 8000  # default für start_discovery wenn keine config vorhanden
ADVERTISEMENT_INTERVAL_US = 250000
SCAN_DURATION_MS = 1200
DISCOVERY_DURATION_MS = 8000
DISCOVERY_STALE_MS = 20000
DISCOVERED_MAX = 40
COMPANY_ID = b"\xf0\x0f"
PROTOCOL_MAGIC = b"IF"
PROTOCOL_VERSION = 2
STATE_SEEKER = 0
STATE_HOST = 1
STATE_CLAIM = 2
STATE_GRANT = 3
STATE_STOP = 4
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
        self.ble_state = STATE_SEEKER
        self.target_node = ZERO_NODE_ID
        self.state_until_ms = 0
        self.token_epoch = 0
        self.current_host_raw = ZERO_NODE_ID
        self.current_host_seen_ms = 0
        self.stopped_host_raw = ZERO_NODE_ID
        self.stopped_host_epoch = 0
        self.scan_done = True
        self.pending_packet = None
        self.discovered = {}
        self.discovery_active = False
        self.discovery_until_ms = 0

        # Lobby-Broadcasting (Picos koennen sich als Host markieren)
        self.lobby_broadcast_mode = None       # "bomb" oder "infect", None = kein Broadcast
        self.lobby_broadcast_running = False

        self.ble = bluetooth.BLE()
        self.ble.active(True)
        self.ble.irq(self._ble_irq)
        self.ble.active(False)

    def _default_config(self):
        return {
            "enabled": False,
            "initial_role": "seeker",
            # "bomb": klassische heisse Kartoffel - wer infiziert, wird sofort
            #   wieder selbst zum Sucher (bestehendes Verhalten, unveraendert).
            # "infect": wer infiziert, bleibt selbst infiziert - die Rolle wird
            #   nur zusaetzlich an den neu Infizierten weitergegeben, es kann also
            #   mit der Zeit mehrere gleichzeitig Infizierte geben.
            "game_mode": "bomb",
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
        config["game_mode"] = "infect" if config.get("game_mode") == "infect" else "bomb"
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

    def add_player(self, node_id, name):
        updated = [{"id": node_id, "name": name}] + list(self.players)
        return self.save_players(updated)

    def start_discovery(self):
        if not self.running:
            try:
                self.ble.active(True)
                self.ble.irq(self._ble_irq)
            except Exception:
                pass
        self.discovery_active = True
        self.discovery_until_ms = _ticks_add(time.ticks_ms(), DISCOVERY_DURATION_MS)
        return self.status()

    def _discovered_list(self):
        now = time.ticks_ms()
        registered_ids = set(player["id"] for player in self.players)
        stale = []
        result = []
        for node_id, last_seen_ms in self.discovered.items():
            age_ms = _ticks_diff(now, last_seen_ms)
            if age_ms > DISCOVERY_STALE_MS:
                stale.append(node_id)
                continue
            if node_id in registered_ids:
                continue
            result.append({"id": node_id, "age_ms": age_ms})
        for node_id in stale:
            del self.discovered[node_id]
        result.sort(key=lambda entry: entry["age_ms"])
        return result

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
        payload = (
            COMPANY_ID
            + PROTOCOL_MAGIC
            + bytes((PROTOCOL_VERSION, self.ble_state))
            + self.node_raw
            + self.target_node
            + bytes(((self.token_epoch >> 8) & 0xFF, self.token_epoch & 0xFF))
        )
        manufacturer = bytes((len(payload) + 1, 0xFF)) + payload
        return b"\x02\x01\x06" + manufacturer

    def _update_advertisement(self):
        try:
            self.ble.gap_advertise(None)
        except Exception:
            pass
        if self.running or self.ble_state == STATE_STOP:
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
            if ad_type == 0xFF and len(value) >= 24 and value[:2] == COMPANY_ID and value[2:4] == PROTOCOL_MAGIC:
                if value[4] != PROTOCOL_VERSION:
                    return None
                epoch = (value[22] << 8) | value[23]
                return value[5], bytes(value[6:14]), bytes(value[14:22]), epoch
            index += length + 1
        return None

    def _ble_irq(self, event, data):
        if event == _IRQ_SCAN_RESULT:
            try:
                _addr_type, _addr, _adv_type, rssi, adv_data = data
                packet = self._parse_advertisement(adv_data)
                if packet is not None:
                    state, sender, target, epoch = packet
                    if sender != self.node_raw:
                        self.discovered[_hex_id(sender)] = time.ticks_ms()
                        if len(self.discovered) > DISCOVERED_MAX:
                            oldest_id = min(self.discovered, key=self.discovered.get)
                            del self.discovered[oldest_id]
                    if state == STATE_STOP:
                        self.stopped_host_raw = sender
                        self.stopped_host_epoch = epoch
                        if self.current_host_raw == sender:
                            self.current_host_raw = ZERO_NODE_ID
                            self.current_host_seen_ms = 0
                    stopped_token = (
                        self.stopped_host_epoch == epoch
                        and (
                            (state == STATE_HOST and self.stopped_host_raw == sender)
                            or (state == STATE_GRANT and self.stopped_host_raw == target)
                        )
                    )
                    if state == STATE_HOST and not stopped_token:
                        self.current_host_raw = sender
                        self.current_host_seen_ms = time.ticks_ms()
                    elif state == STATE_GRANT and not stopped_token:
                        self.current_host_raw = target
                        self.current_host_seen_ms = time.ticks_ms()
                    relevant = (
                        self.role == "seeker"
                        and (
                            (state == STATE_HOST and self.ble_state == STATE_SEEKER)
                            or (state == STATE_GRANT and target == self.node_raw)
                            or (
                                self.ble_state == STATE_GRANT
                                and state == STATE_HOST
                                and sender == self.target_node
                                and epoch == self.token_epoch
                            )
                            or (
                                self.ble_state == STATE_GRANT
                                and state == STATE_STOP
                                and sender == self.target_node
                                and epoch == self.token_epoch
                            )
                        )
                    ) or (
                        self.role == "host"
                        and state == STATE_CLAIM
                        and target == self.node_raw
                        and epoch == self.token_epoch
                    )
                    current = self.pending_packet
                    targeted_grant = state == STATE_GRANT and target == self.node_raw
                    targeted_stop = state == STATE_STOP and sender == self.target_node
                    current_is_grant = current is not None and current[0] == STATE_GRANT
                    if relevant and (
                        targeted_grant or targeted_stop
                        or (not current_is_grant and (current is None or int(rssi) > current[4]))
                    ):
                        self.pending_packet = (state, sender, target, epoch, int(rssi))
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
        self.ble_state = STATE_HOST if self.role == "host" else STATE_SEEKER
        self.target_node = ZERO_NODE_ID
        self.state_until_ms = 0
        self.token_epoch = ((now // 100) & 0xFFFF) or 1
        self.current_host_raw = self.node_raw if self.role == "host" else ZERO_NODE_ID
        self.current_host_seen_ms = now if self.role == "host" else 0
        self.stopped_host_raw = ZERO_NODE_ID
        self.stopped_host_epoch = 0
        self.pending_packet = None
        self.ble.active(True)
        self.ble.irq(self._ble_irq)
        self._update_advertisement()
        self.log("[INFECTION] BLE-Runde gestartet als " + self.role)

    def stop_round(self, reason="Beendet"):
        was_running = self.running
        stopped_epoch = self.token_epoch
        self.running = False
        self.pending_packet = None
        self.ble_state = STATE_STOP if was_running else STATE_SEEKER
        self.target_node = ZERO_NODE_ID
        self.token_epoch = stopped_epoch
        self.state_until_ms = _ticks_add(time.ticks_ms(), STOP_ADVERTISEMENT_MS) if was_running else 0
        self.current_host_raw = ZERO_NODE_ID
        self.current_host_seen_ms = 0
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
            if was_running:
                self.ble.active(True)
                self._update_advertisement()
            else:
                self.ble.active(False)
        except Exception:
            pass
        self.log("[INFECTION] " + reason)

    def _become_host(self, epoch=None):
        self.role = "host"
        self.ble_state = STATE_HOST
        self.target_node = ZERO_NODE_ID
        self.state_until_ms = 0
        if epoch is not None:
            self.token_epoch = epoch
        self.current_host_raw = self.node_raw
        self.current_host_seen_ms = time.ticks_ms()
        self.cooldown_until_ms = _ticks_add(time.ticks_ms(), self.config["cooldown_seconds"] * 1000)
        self.last_event = "Infiziert - BLE-Host aktiv"
        self._update_advertisement()
        self.log("[INFECTION] Rolle: host")

    def _become_seeker(self):
        self.role = "seeker"
        self.ble_state = STATE_SEEKER
        self.target_node = ZERO_NODE_ID
        self.state_until_ms = 0
        self.cooldown_until_ms = _ticks_add(time.ticks_ms(), self.config["cooldown_seconds"] * 1000)
        self.last_event = "Suche Infizierten per BLE"
        self._update_advertisement()
        self.log("[INFECTION] Rolle: seeker")

    def _request_handoff(self, host, epoch):
        self.ble_state = STATE_CLAIM
        self.target_node = host
        self.token_epoch = epoch
        self.state_until_ms = _ticks_add(time.ticks_ms(), CLAIM_WINDOW_MS)
        self.last_event = "Infektion beim Host angefragt"
        self._update_advertisement()

    def _grant_handoff(self, seeker):
        next_epoch = (self.token_epoch + 1) & 0xFFFF
        if next_epoch == 0:
            next_epoch = 1
        peer_name = self._player_label(seeker)
        peer_id = _hex_id(seeker)
        contact = self._record_contact("infected_by_me", peer_name, peer_id)
        self.last_peer = contact["peer_name"]
        self.last_event = "Infiziertenrolle uebergeben: " + self.last_peer
        self.infection_count += 1
        # Im Infect-Modus bleibt der Infizierer selbst infiziert (bleibt "host"),
        # im klassischen Bomben-Modus wird er sofort wieder zum Sucher.
        if self.config.get("game_mode") != "infect":
            self.role = "seeker"
        self.ble_state = STATE_GRANT
        self.target_node = seeker
        self.token_epoch = next_epoch
        self.state_until_ms = _ticks_add(time.ticks_ms(), HANDOFF_WINDOW_MS)
        self.current_host_raw = seeker
        self.current_host_seen_ms = time.ticks_ms()
        self.cooldown_until_ms = _ticks_add(time.ticks_ms(), self.config["cooldown_seconds"] * 1000)
        self._update_advertisement()
        self.log("[INFECTION] Rolle an " + peer_id + " uebergeben")

    def _accept_handoff(self, previous_host, epoch):
        peer_name = self._player_label(previous_host)
        peer_id = _hex_id(previous_host)
        contact = self._record_contact("infected_me", peer_name, peer_id)
        self.last_peer = contact["peer_name"]
        self.last_event = "Infiziertenrolle uebernommen"
        self.infection_count += 1
        self._become_host(epoch)

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

    def _handle_packet(self, state, sender, target, epoch, rssi):
        if sender == self.node_raw:
            return
        player = self._player_for(sender)
        if player is None:
            return
        self.last_rssi = rssi

        if self.role == "host" and state == STATE_CLAIM and target == self.node_raw and epoch == self.token_epoch:
            if rssi >= self.config["rssi_threshold"]:
                self._grant_handoff(sender)
            return

        if self.role == "seeker" and state == STATE_GRANT and target == self.node_raw:
            self._accept_handoff(sender, epoch)
            return

        if (
            self.role == "seeker"
            and self.ble_state == STATE_GRANT
            and state == STATE_HOST
            and sender == self.target_node
            and epoch == self.token_epoch
        ):
            self.ble_state = STATE_SEEKER
            self.target_node = ZERO_NODE_ID
            self.state_until_ms = 0
            self._update_advertisement()
            return

        if (
            self.role == "seeker"
            and self.ble_state == STATE_GRANT
            and state == STATE_STOP
            and sender == self.target_node
            and epoch == self.token_epoch
        ):
            self.ble_state = STATE_SEEKER
            self.target_node = ZERO_NODE_ID
            self.state_until_ms = 0
            self.current_host_raw = ZERO_NODE_ID
            self.current_host_seen_ms = 0
            self._update_advertisement()
            return

        now = time.ticks_ms()
        if _ticks_diff(now, self.cooldown_until_ms) < 0:
            return

        if self.role == "seeker" and self.ble_state == STATE_SEEKER and state == STATE_HOST and rssi >= self.config["rssi_threshold"]:
            self._request_handoff(sender, epoch)

    def remaining_seconds(self):
        if not self.running:
            return 0
        return max(0, _ticks_diff(self.round_end_ms, time.ticks_ms()) // 1000)

    def _current_infected(self):
        if not self.running:
            return None
        if self.role == "host":
            return {
                "id": self.node_id,
                "name": self.player_name or ("node-" + self.node_id),
                "self": True,
            }
        if self.current_host_raw == ZERO_NODE_ID:
            return None
        if _ticks_diff(time.ticks_ms(), self.current_host_seen_ms) > HOST_STALE_MS:
            return None
        return {
            "id": _hex_id(self.current_host_raw),
            "name": self._player_label(self.current_host_raw),
            "self": False,
        }

    def status(self):
        current_infected = self._current_infected()
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
            "current_infected": current_infected,
            "players": list(self.players),
            "discovered": self._discovered_list(),
            "discovery_active": self.discovery_active,
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
                if (
                    self.ble_state == STATE_STOP
                    and self.state_until_ms
                    and _ticks_diff(time.ticks_ms(), self.state_until_ms) >= 0
                ):
                    try:
                        self.ble.gap_advertise(None)
                        if not self.discovery_active:
                            self.ble.active(False)
                    except Exception:
                        pass
                    self.ble_state = STATE_SEEKER
                    self.state_until_ms = 0
                if self.discovery_active:
                    now = time.ticks_ms()
                    if _ticks_diff(now, self.discovery_until_ms) >= 0:
                        self.discovery_active = False
                        try:
                            self.ble.gap_scan(None)
                            if self.ble_state != STATE_STOP:
                                self.ble.active(False)
                        except Exception:
                            pass
                    elif self.scan_done:
                        self.scan_done = False
                        try:
                            self.ble.gap_scan(SCAN_DURATION_MS, 30000, 30000, False)
                        except Exception:
                            self.scan_done = True
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

            if self.state_until_ms and _ticks_diff(now, self.state_until_ms) >= 0:
                # Nach Ablauf des GRANT-Fensters: im Infect-Modus bleibt ein
                # Geraet, das "host" geblieben ist, weiter als Infizierter aktiv;
                # im Bomben-Modus (bzw. nach einer erfolglosen CLAIM-Anfrage) geht
                # es zurueck in den Sucher-Zustand.
                self.ble_state = STATE_HOST if self.role == "host" else STATE_SEEKER
                self.target_node = ZERO_NODE_ID
                self.state_until_ms = 0
                self._update_advertisement()

            packet = self.pending_packet
            self.pending_packet = None
            if packet is not None:
                self._handle_packet(packet[0], packet[1], packet[2], packet[3], packet[4])

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
            "game_mode": body_params.get("game_mode", "bomb"),
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

    if request_path == "/infection-players-add" and request_method == "POST":
        raw = _raw_id(body_params.get("id"))
        if raw is None:
            await send_json(writer, {"ok": False, "error": "invalid_id"}, "400 Bad Request")
            return True
        name = str(body_params.get("name") or "").strip()[:32]
        await send_json(writer, manager.add_player(_hex_id(raw), name))
        return True

    if request_path == "/infection-discover" and request_method == "POST":
        await send_json(writer, manager.start_discovery())
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
