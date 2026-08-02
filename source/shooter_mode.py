"""Shooter Spielmodus: Infrarot-"Laser-Tag" mit Grove IR-Emitter/IR-REC38.

Im Gegensatz zu koth_mode.py/race_mode.py (BLE-basiert) laeuft dieser Modus
ueber echte, verkabelte Infrarot-Hardware (siehe ir_emitter.py/
ir_receiver.py): jeder Pico sendet beim Abfeuern (fire()) einen NEC-Frame
mit der eigenen kurzen Node-ID + konfiguriertem Schaden, und zaehlt
gleichzeitig eingehende Treffer anderer Picos ueber den IR-Empfaenger.
Gleiches Konfig-/Log-/Routen-Muster wie koth_mode.py/race_mode.py (siehe
dortige Docstrings) - _ticks_diff()/_clamp()/Config-Datei/JSON-Rundenlog/
status()/configure()/run()/handle_*_route() folgen bewusst derselben Form.
"""

import asyncio
import json
import machine
import os
import time

from ir_emitter import IRTransmitter
from ir_receiver import IRReceiver

# main.py fuellt rc_channels_state mit den zuletzt dekodierten CRSF-RC/AUX-
# Kanalwerten (siehe dortiger telemetry_loop()). Duenner Fallback fuer den
# Fall, dass shooter_mode.py eigenstaendig importiert wird (z.B. Unittests,
# siehe main.py's eigener hotspot_common-Fallback fuer dasselbe Muster) -
# main.py ist zur Laufzeit auf dem Pico immer schon vollstaendig geladen,
# da shooter_mode.py ausschliesslich lazy ueber gmr.py nachgeladen wird.
try:
    from main import rc_channels_state
except Exception:
    rc_channels_state = {"channels": [0] * 16, "updated_ms": 0}

CONFIG_FILE = "shooter.conf"
DEFAULT_LIVES = 5  # 0 = unbegrenzt (nur Trefferzaehler, kein Ausscheiden)
DEFAULT_DAMAGE = 1
DEFAULT_HIT_COOLDOWN_MS = 300   # Prellschutz: gleicher Schuetze zaehlt erst nach dieser Pause erneut
DEFAULT_FIRE_COOLDOWN_MS = 250  # Mindestabstand zwischen zwei eigenen Schuessen
DEFAULT_AUX_CHANNEL = 0         # 0 = AUX-Abzug deaktiviert, sonst CRSF-Kanal 1-16
DEFAULT_AUX_THRESHOLD_US = 1700 # Kanalwert (988-2011us Konvention) ab dem "Schalter high" gilt
AUX_CHANNEL_MIN_US = 988
AUX_CHANNEL_MAX_US = 2011
AUX_STALE_MS = 1000  # keine frischen RC-Kanaldaten seit so lange -> als "kein Signal" behandeln


def _crsf_raw_to_us(raw):
    """Wandelt einen rohen 11-Bit-CRSF-Kanalwert (172-1811) in die von
    Betaflight/OSD gewohnte Mikrosekunden-Konvention (988-2011, Mitte 1500)
    um - Standardformel, siehe z.B. Betaflight's CRSF-Implementierung."""
    return int((raw - 992) * 5 / 8) + 1500


def _ticks_diff(value, reference):
    try:
        return time.ticks_diff(value, reference)
    except Exception:
        return value - reference


def _clamp(value, low, high):
    return max(low, min(high, value))


def _datetime_string():
    now = time.localtime()
    return "%02d.%02d.%04d %02d:%02d:%02d" % (now[2], now[1], now[0], now[3], now[4], now[5])


def _derive_node_id(raw_bytes):
    """Reduziert die (mehrere Bytes lange) Pico-Hardware-ID auf 1 Byte, weil
    die NEC-Adresse im IR-Frame nur 8 Bit Platz bietet - fuer eine
    ueberschaubare Runde aus wenigen Geraeten ausreichend eindeutig (anders
    als koth_mode.py/race_mode.py's 8-Byte-BLE-ID, die deutlich mehr Bits
    zur Verfuegung hat)."""
    node_id = 0
    for byte in raw_bytes:
        node_id ^= byte
    return node_id


# Dauerhafter Verlauf abgeschlossener Shooter-Runden (gleiches Muster wie
# koth_mode.py's koth_log.json) - fuer die Statistik-/Verlaufs-Ansicht im
# Dashboard (admin_dashboard.html).
SHOOTER_LOG_FILE = "shooter_log.json"
SHOOTER_LOG_MAX_ENTRIES = 50


def load_shooter_log():
    try:
        with open(SHOOTER_LOG_FILE, "r") as f:
            data = json.loads(f.read())
        if isinstance(data, list):
            return data[-SHOOTER_LOG_MAX_ENTRIES:]
    except Exception:
        pass
    return []


def save_shooter_log(entries):
    trimmed = entries[-SHOOTER_LOG_MAX_ENTRIES:]
    payload = json.dumps(trimmed)
    try:
        tmp_path = SHOOTER_LOG_FILE + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(payload)
        try:
            os.remove(SHOOTER_LOG_FILE)
        except Exception:
            pass
        os.rename(tmp_path, SHOOTER_LOG_FILE)
        return True, ""
    except Exception as e:
        try:
            with open(SHOOTER_LOG_FILE, "w") as f:
                f.write(payload)
            return True, ""
        except Exception as e2:
            return False, f"{e} | fallback={e2}"


class ShooterMode:
    def __init__(self, player_name="", log=None):
        self.player_name = str(player_name or "").strip()[:32]
        self.log = log or (lambda _message: None)

        try:
            raw_node_id = bytes(machine.unique_id())
        except Exception:
            raw_node_id = b"pico-sim"
        self.node_id = _derive_node_id(raw_node_id)

        self.config = self._load_config()

        self.running = False
        self.last_event = "Bereit"
        self.hits_taken = 0
        self.shots_fired = 0
        self.lives_remaining = self.config["lives"]
        self.eliminated = False
        self.last_hit_from = None
        self.last_hit_ms = 0
        self.last_fire_ms = 0
        self.hit_sources = {}
        self.log_entries = load_shooter_log()
        self.aux_available = False
        self.aux_value_us = None

        self.emitter = IRTransmitter()
        self.receiver = IRReceiver()

    def _default_config(self):
        return {
            "enabled": False,
            "lives": DEFAULT_LIVES,
            "damage": DEFAULT_DAMAGE,
            "hit_cooldown_ms": DEFAULT_HIT_COOLDOWN_MS,
            "fire_cooldown_ms": DEFAULT_FIRE_COOLDOWN_MS,
            "aux_channel": DEFAULT_AUX_CHANNEL,
            "aux_threshold_us": DEFAULT_AUX_THRESHOLD_US,
        }

    def _normalize_config(self, values):
        config = self._default_config()
        if isinstance(values, dict):
            config.update(values)
        config["enabled"] = bool(config.get("enabled", False))
        config["lives"] = _clamp(int(config.get("lives", DEFAULT_LIVES)), 0, 99)
        config["damage"] = _clamp(int(config.get("damage", DEFAULT_DAMAGE)), 1, 9)
        config["hit_cooldown_ms"] = _clamp(int(config.get("hit_cooldown_ms", DEFAULT_HIT_COOLDOWN_MS)), 50, 5000)
        config["fire_cooldown_ms"] = _clamp(int(config.get("fire_cooldown_ms", DEFAULT_FIRE_COOLDOWN_MS)), 50, 5000)
        config["aux_channel"] = _clamp(int(config.get("aux_channel", DEFAULT_AUX_CHANNEL)), 0, 16)
        config["aux_threshold_us"] = _clamp(
            int(config.get("aux_threshold_us", DEFAULT_AUX_THRESHOLD_US)), AUX_CHANNEL_MIN_US, AUX_CHANNEL_MAX_US
        )
        return config

    def _read_aux_state(self):
        """Liest den aktuellen Wert des konfigurierten AUX-Kanals aus
        main.py's rc_channels_state. Gibt (verfuegbar, wert_us) zurueck -
        verfuegbar=False, falls AUX-Abzug deaktiviert (Kanal 0), der Kanal
        gar nicht existiert, oder main.py seit AUX_STALE_MS keine frischen
        RC-Kanaldaten mehr geliefert hat (z.B. weil der jeweilige CRSF-
        Aufbau gar keine RC_CHANNELS_PACKED-Frames auf dieser Leitung
        sendet - kein Crash, der AUX-Abzug bleibt dann einfach inaktiv)."""
        channel = self.config["aux_channel"]
        if channel <= 0:
            return False, None

        updated_ms = rc_channels_state.get("updated_ms", 0)
        if not updated_ms or _ticks_diff(time.ticks_ms(), updated_ms) > AUX_STALE_MS:
            return False, None

        channels = rc_channels_state.get("channels") or []
        if channel > len(channels):
            return False, None

        return True, _crsf_raw_to_us(channels[channel - 1])

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

    def start_round(self):
        self.running = True
        self.hits_taken = 0
        self.shots_fired = 0
        self.lives_remaining = self.config["lives"]
        self.eliminated = False
        self.last_hit_from = None
        self.last_hit_ms = 0
        self.last_fire_ms = 0
        self.hit_sources = {}
        self.aux_available = False
        self.aux_value_us = None
        self.last_event = "Runde gestartet"
        return self.status()

    def stop_round(self, reason="Runde beendet"):
        if self.running and (self.hits_taken > 0 or self.shots_fired > 0):
            self.log_entries.append({
                "ts_s": int(time.time()),
                "timestamp": _datetime_string(),
                "hits_taken": self.hits_taken,
                "shots_fired": self.shots_fired,
                "eliminated": self.eliminated,
                "reason": reason,
            })
            if len(self.log_entries) > SHOOTER_LOG_MAX_ENTRIES:
                del self.log_entries[0: len(self.log_entries) - SHOOTER_LOG_MAX_ENTRIES]
            save_shooter_log(self.log_entries)
        self.running = False
        self.last_event = reason
        return self.status()

    def configure(self, values):
        updated = dict(self.config)
        updated.update(values or {})
        self.config = self._normalize_config(updated)
        self._save_config()
        if self.config["enabled"]:
            self.start_round()
        else:
            self.stop_round("Deaktiviert")
        return self.status()

    def fire(self):
        if not self.running:
            return {"ok": False, "error": "Runde laeuft nicht"}
        if self.eliminated:
            return {"ok": False, "error": "Ausgeschieden"}

        now = time.ticks_ms()
        if self.last_fire_ms and _ticks_diff(now, self.last_fire_ms) < self.config["fire_cooldown_ms"]:
            return {"ok": False, "error": "Abklingzeit aktiv"}

        sent = self.emitter.send(self.node_id, self.config["damage"])
        if not sent:
            return {"ok": False, "error": "IR-Sender nicht verfuegbar"}

        self.last_fire_ms = now
        self.shots_fired += 1
        self.last_event = "Schuss abgegeben"
        return self.status()

    def _apply_hit(self, shooter_id, damage, now_ms):
        if shooter_id == self.node_id:
            return  # eigenes Echo/Reflexion ignorieren

        source = self.hit_sources.get(shooter_id)
        if source is not None and _ticks_diff(now_ms, source["last_ms"]) < self.config["hit_cooldown_ms"]:
            return  # Prellschutz: derselbe Schuetze zu kurz nacheinander

        self.hit_sources[shooter_id] = {
            "hits": (source["hits"] + 1) if source else 1,
            "last_ms": now_ms,
        }
        self.hits_taken += 1
        self.last_hit_from = shooter_id
        self.last_hit_ms = now_ms
        self.last_event = "Treffer von Schuetze %d" % shooter_id

        if self.config["lives"] > 0 and not self.eliminated:
            self.lives_remaining = max(0, self.lives_remaining - damage)
            if self.lives_remaining <= 0:
                self.eliminated = True
                self.last_event = "Ausgeschieden"

    def _hit_sources_list(self):
        entries = [
            {"id": node_id, "hits": info["hits"]}
            for node_id, info in self.hit_sources.items()
        ]
        entries.sort(key=lambda entry: entry["hits"], reverse=True)
        return entries

    def status(self):
        return {
            "ok": True,
            "enabled": self.config["enabled"],
            "running": self.running,
            "node_id": self.node_id,
            "player_name": self.player_name or ("node-%d" % self.node_id),
            "hits_taken": self.hits_taken,
            "shots_fired": self.shots_fired,
            "lives_remaining": self.lives_remaining,
            "eliminated": self.eliminated,
            "last_event": self.last_event,
            "last_hit_from": self.last_hit_from,
            "hit_sources": self._hit_sources_list(),
            "hardware": {
                "emitter_available": self.emitter.available,
                "receiver_available": self.receiver.available,
            },
            "aux": {
                "channel": self.config["aux_channel"],
                "available": self.aux_available,
                "value_us": self.aux_value_us,
                "threshold_us": self.config["aux_threshold_us"],
            },
            "config": dict(self.config),
        }

    async def run(self):
        while True:
            if not self.running:
                await asyncio.sleep_ms(200)
                continue

            now = time.ticks_ms()
            for frame in self.receiver.poll():
                self._apply_hit(frame["address"], frame["command"], now)

            self.aux_available, self.aux_value_us = self._read_aux_state()
            if self.aux_available and self.aux_value_us is not None and self.aux_value_us >= self.config["aux_threshold_us"]:
                # fire() prueft Cooldown/Ausgeschieden-Status selbst - hier
                # nur so lange wie der Schalter "high" steht wiederholt
                # aufrufen, damit Halten = Dauerfeuer im Takt der
                # Schuss-Abklingzeit ergibt (wie ein gehaltener Abzug).
                self.fire()

            await asyncio.sleep_ms(50)


async def send_json(writer, payload, status="200 OK"):
    body = json.dumps(payload).encode()
    writer.write(("HTTP/1.1 " + status + "\r\n").encode())
    writer.write(b"Content-Type: application/json\r\n")
    writer.write(b"Cache-Control: no-store\r\n")
    writer.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(body)


async def handle_shooter_route(writer, request_path, request_method, query_params, body_params, manager):
    if request_path == "/shooter-data":
        await send_json(writer, manager.status())
        return True

    if request_path == "/shooter-fire" and request_method == "POST":
        await send_json(writer, manager.fire())
        return True

    if request_path == "/shooter-log":
        await send_json(writer, {"ok": True, "log": manager.log_entries})
        return True

    if request_path == "/shooter-log-clear":
        manager.log_entries = []
        ok, err = save_shooter_log(manager.log_entries)
        await send_json(writer, {"ok": ok, "error": None if ok else err})
        return True

    if request_path == "/shooter-config" and request_method == "POST":
        values = {
            "enabled": body_params.get("enabled", "0") in ("1", "true", "on"),
            "lives": body_params.get("lives", DEFAULT_LIVES),
            "damage": body_params.get("damage", DEFAULT_DAMAGE),
            "hit_cooldown_ms": body_params.get("hit_cooldown_ms", DEFAULT_HIT_COOLDOWN_MS),
            "fire_cooldown_ms": body_params.get("fire_cooldown_ms", DEFAULT_FIRE_COOLDOWN_MS),
            "aux_channel": body_params.get("aux_channel", DEFAULT_AUX_CHANNEL),
            "aux_threshold_us": body_params.get("aux_threshold_us", DEFAULT_AUX_THRESHOLD_US),
        }
        try:
            await send_json(writer, manager.configure(values))
        except Exception as error:
            await send_json(writer, {"ok": False, "error": str(error)}, "400 Bad Request")
        return True

    if request_path == "/shooter-stop" and request_method == "POST":
        manager.config["enabled"] = False
        try:
            manager._save_config()
        except Exception:
            pass
        manager.stop_round("Manuell beendet")
        await send_json(writer, manager.status())
        return True

    return False
