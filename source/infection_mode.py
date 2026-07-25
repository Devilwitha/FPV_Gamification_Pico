import asyncio
import json
import machine
import network
import os
import time

CONFIG_FILE = "infection.conf"
LEGACY_CONFIG_FILE = "infection_config.json"
DEFAULT_INFECTION_SSID = "FPV_Gamification_Pico"
DEFAULT_INFECTION_PASSWORD = "drohnenspiel"
DEFAULT_ROUND_SECONDS = 300
DEFAULT_RSSI_THRESHOLD = -55
DEFAULT_COOLDOWN_SECONDS = 10
HANDOFF_DELAY_MS = 1800
CONNECT_TIMEOUT_MS = 7000
SCAN_INTERVAL_MS = 1500


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


class InfectionMode:
    def __init__(self, normal_ssid, normal_password, log=None):
        self.normal_ssid = normal_ssid
        self.normal_password = normal_password
        self.log = log or (lambda _message: None)

        self.ap = network.WLAN(network.AP_IF)
        self.sta = network.WLAN(network.STA_IF)

        self.node_id = self._node_id()
        self.config = self._load_config()

        if self.config["enabled"]:
            self.config["enabled"] = False
            try:
                self._save_config()
            except Exception as error:
                self.log(
                    "[INFECTION] Neustart-Status konnte nicht geloescht werden: "
                    + str(error)
                )

        self.role = self.config["initial_role"]
        self.running = False
        self.round_end_ms = 0
        self.cooldown_until_ms = 0
        self.pending_role = None
        self.pending_role_at_ms = 0
        self.last_peer = ""
        self.last_event = "Bereit"
        self.last_rssi = None
        self.infection_count = 0

        # Dauerhafter Start des eigenen Haupt-Hotspots
        self._ensure_primary_ap()

    def _node_id(self):
        try:
            return "".join("%02x" % value for value in machine.unique_id())
        except Exception:
            return "pico"

    def _default_config(self):
        return {
            "enabled": False,
            "initial_role": "seeker",
            "ssid": DEFAULT_INFECTION_SSID,
            "password": DEFAULT_INFECTION_PASSWORD,
            "round_seconds": DEFAULT_ROUND_SECONDS,
            "rssi_threshold": DEFAULT_RSSI_THRESHOLD,
            "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
        }

    def _normalize_config(self, values):
        config = self._default_config()
        if isinstance(values, dict):
            config.update(values)
        config["enabled"] = bool(config.get("enabled", False))
        config["initial_role"] = (
            "host" if config.get("initial_role") == "host" else "seeker"
        )
        config["ssid"] = str(config.get("ssid") or DEFAULT_INFECTION_SSID)[:32]
        password = str(config.get("password") or DEFAULT_INFECTION_PASSWORD)
        config["password"] = (
            password if len(password) >= 8 else DEFAULT_INFECTION_PASSWORD
        )
        config["round_seconds"] = _clamp(
            int(config.get("round_seconds", DEFAULT_ROUND_SECONDS)), 30, 3600
        )
        config["rssi_threshold"] = _clamp(
            int(config.get("rssi_threshold", DEFAULT_RSSI_THRESHOLD)),
            -95,
            -20,
        )
        config["cooldown_seconds"] = _clamp(
            int(config.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)),
            3,
            120,
        )
        return config

    def _load_config(self):
        for config_path in (CONFIG_FILE, LEGACY_CONFIG_FILE):
            try:
                with open(config_path, "r") as config_file:
                    config = self._normalize_config(
                        json.loads(config_file.read())
                    )
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
        payload = json.dumps(self.config)
        temp_path = CONFIG_FILE + ".tmp"
        with open(temp_path, "w") as config_file:
            config_file.write(payload)
        try:
            os.remove(CONFIG_FILE)
        except Exception:
            pass
        os.rename(temp_path, CONFIG_FILE)

    def _ensure_primary_ap(self, ssid=None, password=None):
        """Stellt sicher, dass der Steuerungs-Hotspot IMMER erreichbar bleibt."""
        target_ssid = ssid or self.normal_ssid
        target_pw = password or self.normal_password

        if not self.ap.active():
            self.ap.active(True)
            time.sleep_ms(50)

        try:
            current_essid = self.ap.config("essid")
        except Exception:
            current_essid = ""

        if current_essid != target_ssid:
            self.ap.config(essid=target_ssid)
            if target_pw:
                try:
                    self.ap.config(password=target_pw)
                except Exception:
                    pass
            self.ap.ifconfig(
                ("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1")
            )

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
        self.pending_role = None
        self.last_peer = ""
        self.last_event = "Runde gestartet"
        self.infection_count = 0
        now = time.ticks_ms()
        self.round_end_ms = _ticks_add(
            now, self.config["round_seconds"] * 1000
        )

        if self.role == "host":
            self._become_host()
        else:
            self._become_seeker()
        self.log("[INFECTION] Runde gestartet als " + self.role)

    def stop_round(self, reason="Beendet"):
        self.running = False
        self.pending_role = None
        self.last_event = reason

        try:
            self.sta.disconnect()
        except Exception:
            pass
        try:
            self.sta.active(False)
        except Exception:
            pass

        self._ensure_primary_ap(self.normal_ssid, self.normal_password)
        self.last_event = reason + " - Hotspot wieder aktiv"
        self.log("[INFECTION] " + reason)

    def _become_host(self):
        # Der Host schaltet den Hotspot auf die Infektions-SSID um
        self._ensure_primary_ap(self.config["ssid"], self.config["password"])
        self.role = "host"
        self.last_event = "Infiziert - Host aktiv"
        self.cooldown_until_ms = _ticks_add(
            time.ticks_ms(), self.config["cooldown_seconds"] * 1000
        )
        self.log("[INFECTION] Rolle: host")

    def _become_seeker(self):
        # Der Seeker behält die normale SSID für die Steuerung
        self._ensure_primary_ap(self.normal_ssid, self.normal_password)
        self.sta.active(True)
        try:
            self.sta.disconnect()
        except Exception:
            pass
        self.role = "seeker"
        self.last_event = "Suche Infizierten"
        self.cooldown_until_ms = _ticks_add(
            time.ticks_ms(), self.config["cooldown_seconds"] * 1000
        )
        self.log("[INFECTION] Rolle: seeker")

    def _schedule_role(self, role, delay_ms=HANDOFF_DELAY_MS):
        self.pending_role = role
        self.pending_role_at_ms = _ticks_add(time.ticks_ms(), delay_ms)

    def register_touch(self, peer_id):
        now = time.ticks_ms()
        if not self.running or self.role != "host":
            return False, "Kein aktiver Host"
        if _ticks_diff(now, self.cooldown_until_ms) < 0:
            return False, "Immunitaetszeit aktiv"
        self.last_peer = str(peer_id or "unknown")[:32]
        self.last_event = "Pilot angesteckt: " + self.last_peer
        self.infection_count += 1
        self._schedule_role("seeker")
        self.log("[INFECTION] Handshake mit " + self.last_peer)
        return True, "Infiziert"

    def remaining_seconds(self):
        if not self.running:
            return 0
        remaining = _ticks_diff(self.round_end_ms, time.ticks_ms())
        return max(0, remaining // 1000)

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
            "config": dict(self.config),
        }

    def _scan_target(self):
        target = self.config["ssid"]
        best_rssi = None
        try:
            networks = self.sta.scan()
        except Exception as error:
            self.last_event = "Scanfehler: " + str(error)
            return None

        for entry in networks:
            try:
                ssid = (
                    entry[0].decode()
                    if isinstance(entry[0], bytes)
                    else str(entry[0])
                )
                rssi = int(entry[3])
            except Exception:
                continue
            if ssid == target and (best_rssi is None or rssi > best_rssi):
                best_rssi = rssi

        self.last_rssi = best_rssi
        return best_rssi

    async def _read_response(self, reader):
        status_line = await reader.readline()
        if not status_line or b" 200 " not in status_line:
            return False
        content_length = 0
        while True:
            line = await reader.readline()
            if not line or line in (b"\r\n", b"\n"):
                break
            if line.lower().startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":", 1)[1].strip())
                except Exception:
                    content_length = 0
        if content_length > 0:
            await reader.read(content_length)
        return True

    async def _attempt_infection(self):
        rssi = self._scan_target()
        if rssi is None:
            self.last_event = "Kein Infizierter gefunden"
            return
        if rssi < self.config["rssi_threshold"]:
            self.last_event = "Infizierter zu weit entfernt"
            return

        try:
            self.sta.connect(self.config["ssid"], self.config["password"])
        except Exception as error:
            self.last_event = "Verbindung fehlgeschlagen: " + str(error)
            return

        deadline = _ticks_add(time.ticks_ms(), CONNECT_TIMEOUT_MS)
        while (
            not self.sta.isconnected()
            and _ticks_diff(deadline, time.ticks_ms()) > 0
        ):
            await asyncio.sleep_ms(100)

        if not self.sta.isconnected():
            self.last_event = "Verbindungstimeout"
            return

        writer = None
        try:
            reader, writer = await asyncio.open_connection("192.168.4.1", 80)
            request = (
                "GET /infection-touch?node="
                + self.node_id
                + " HTTP/1.0\r\nHost: 192.168.4.1\r\nConnection: close\r\n\r\n"
            )
            writer.write(request.encode())
            await writer.drain()

            if await self._read_response(reader):
                self.last_peer = "host"
                self.last_event = "Angesteckt - uebernehme Host"
                self.infection_count += 1
                self._schedule_role("host", HANDOFF_DELAY_MS + 600)
        except Exception as error:
            self.last_event = "Handshake fehlgeschlagen: " + str(error)
        finally:
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
            # Nach dem Versuch als Seeker sofort wieder trennen
            try:
                self.sta.disconnect()
            except Exception:
                pass

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

            if self.pending_role and _ticks_diff(now, self.pending_role_at_ms) >= 0:
                role = self.pending_role
                self.pending_role = None
                if role == "host":
                    self._become_host()
                else:
                    self._become_seeker()
                continue

            if self.pending_role or _ticks_diff(now, self.cooldown_until_ms) < 0:
                await asyncio.sleep_ms(200)
                continue

            if self.role == "host":
                self._ensure_primary_ap(
                    self.config["ssid"], self.config["password"]
                )
                await asyncio.sleep_ms(300)
                continue

            # Als Seeker stellen wir sicher, dass unser Steuerungs-Hotspot aktiv bleibt
            self._ensure_primary_ap(self.normal_ssid, self.normal_password)
            await self._attempt_infection()
            await asyncio.sleep_ms(SCAN_INTERVAL_MS)


async def send_json(writer, payload, status="200 OK"):
    body = json.dumps(payload).encode()
    writer.write(("HTTP/1.1 " + status + "\r\n").encode())
    writer.write(b"Content-Type: application/json\r\n")
    writer.write(b"Cache-Control: no-store\r\n")
    writer.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(body)


async def handle_infection_route(
    writer, request_path, request_method, query_params, body_params, manager
):
    if request_path == "/infection-data":
        await send_json(writer, manager.status())
        return True

    if request_path == "/infection-touch":
        ok, message = manager.register_touch(
            query_params.get("node", "unknown")
        )
        await send_json(
            writer,
            {"ok": ok, "message": message, "status": manager.status()},
            "200 OK" if ok else "409 Conflict",
        )
        return True

    if request_path == "/infection-config" and request_method == "POST":
        values = {
            "enabled": body_params.get("enabled", "0") in ("1", "true", "on"),
            "initial_role": body_params.get("initial_role", "seeker"),
            "ssid": body_params.get("ssid", DEFAULT_INFECTION_SSID),
            "password": body_params.get("password", DEFAULT_INFECTION_PASSWORD),
            "round_seconds": body_params.get(
                "round_seconds", DEFAULT_ROUND_SECONDS
            ),
            "rssi_threshold": body_params.get(
                "rssi_threshold", DEFAULT_RSSI_THRESHOLD
            ),
            "cooldown_seconds": body_params.get(
                "cooldown_seconds", DEFAULT_COOLDOWN_SECONDS
            ),
        }
        try:
            status = manager.configure(values)
            await send_json(writer, status)
        except Exception as error:
            await send_json(
                writer, {"ok": False, "error": str(error)}, "400 Bad Request"
            )
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