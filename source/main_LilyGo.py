import gc
import json
import machine
import network
import os
import socket
import time
import framebuf
from hotspot_common import load_hotspot_config

_HOTSPOT_CONFIG = load_hotspot_config()
WIFI_SSID = _HOTSPOT_CONFIG["ssid"]
WIFI_PASSWORD = _HOTSPOT_CONFIG["password"]
PICO_HOST = "192.168.4.1"
POLL_INTERVAL_MS = 2000
OTA_ACTIVE_POLL_INTERVAL_MS = 800
RECONNECT_DELAY_MS = 1500
LONG_PRESS_MS = 550
SESSION_DOWNLOAD_FILE = "fpv_arcade_session.txt"
DEBUG_DOWNLOAD_FILE = "fpv_debug_log.txt"

DISPLAY_WIDTH = 170
DISPLAY_HEIGHT = 370
DISPLAY_X_OFFSET = 35
DISPLAY_Y_OFFSET = 0
DISPLAY_ROW_BYTES = (DISPLAY_WIDTH + 7) // 8


def rgb565(red, green, blue):
    value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
    return ((value & 0xFF) << 8) | (value >> 8)


BLACK = rgb565(0, 0, 0)
WHITE = rgb565(255, 255, 255)
GREEN = rgb565(44, 210, 120)
RED = rgb565(238, 82, 83)
YELLOW = rgb565(255, 196, 64)
CYAN = rgb565(48, 190, 220)
NAVY = rgb565(10, 24, 43)
PANEL = rgb565(20, 43, 64)

OTA_PHASE_LABELS = {
    "idle": ("BEREIT", CYAN),
    "connecting_wifi": ("VERBINDE WLAN", YELLOW),
    "checking_release": ("PRUEFE RELEASE", YELLOW),
    "up_to_date": ("AKTUELL", GREEN),
    "downloading": ("LAEDT HERUNTER", YELLOW),
    "applying": ("WENDE AN", YELLOW),
    "success": ("ERFOLGREICH", GREEN),
    "error": ("FEHLER", RED),
    "wifi_failed": ("WLAN FEHLER", RED),
    "check_failed": ("PRUEF-FEHLER", RED),
    "no_wlan": ("KEIN WLAN", RED),
}


class TDisplayS3:
    def __init__(self):
        self.power = machine.Pin(15, machine.Pin.OUT, value=1)
        self.backlight = machine.Pin(38, machine.Pin.OUT, value=0)
        self.cs = machine.Pin(6, machine.Pin.OUT, value=1)
        self.dc = machine.Pin(7, machine.Pin.OUT, value=0)
        self.reset = machine.Pin(5, machine.Pin.OUT, value=1)
        self.write = machine.Pin(8, machine.Pin.OUT, value=1)
        self.read = machine.Pin(9, machine.Pin.OUT, value=1)

        self.data_pins = (
            machine.Pin(39, machine.Pin.OUT, value=0),
            machine.Pin(40, machine.Pin.OUT, value=0),
            machine.Pin(41, machine.Pin.OUT, value=0),
            machine.Pin(42, machine.Pin.OUT, value=0),
            machine.Pin(45, machine.Pin.OUT, value=0),
            machine.Pin(46, machine.Pin.OUT, value=0),
            machine.Pin(47, machine.Pin.OUT, value=0),
            machine.Pin(48, machine.Pin.OUT, value=0),
        )

        self.buffer = bytearray(DISPLAY_ROW_BYTES * DISPLAY_HEIGHT)
        self.frame = framebuf.FrameBuffer(
            self.buffer,
            DISPLAY_WIDTH,
            DISPLAY_HEIGHT,
            framebuf.MONO_HLSB,
            DISPLAY_ROW_BYTES * 8,
        )

        print("[LILYGO] Initialisiere T-Display-S3")
        self._hardware_reset()
        self._init_panel()
        self.backlight.value(1)

        self.frame.fill(0)
        self.show()
        print("[LILYGO] Display initialisiert")

    def _write_bus(self, value):
        for bit in range(8):
            self.data_pins[bit].value((value >> bit) & 1)
        self.write.value(0)
        self.write.value(1)

    def _write_command(self, command, data=None):
        self.cs.value(0)
        self.dc.value(0)
        self._write_bus(command)
        if data is not None:
            self.dc.value(1)
            for value in data:
                self._write_bus(value)
        self.cs.value(1)

    def _hardware_reset(self):
        self.reset.value(1)
        time.sleep_ms(10)
        self.reset.value(0)
        time.sleep_ms(20)
        self.reset.value(1)
        time.sleep_ms(120)

    def _init_panel(self):
        self._write_command(0x01)
        time.sleep_ms(150)
        self._write_command(0x11)
        time.sleep_ms(120)
        self._write_command(0x13)
        self._write_command(0x36, b"\x00")
        self._write_command(0x3A, b"\x55")
        self._write_command(0xB2, b"\x0b\x0b\x00\x33\x33")
        self._write_command(0xB7, b"\x75")
        self._write_command(0xBB, b"\x28")
        self._write_command(0xC0, b"\x2c")
        self._write_command(0xC2, b"\x01")
        self._write_command(0xC3, b"\x1f")
        self._write_command(0xC4, b"\xa7")
        self._write_command(0xC6, b"\x13")
        self._write_command(
            0xE0, b"\xf0\x05\x0a\x06\x06\x03\x2b\x32\x43\x36\x11\x10\x2b\x32"
        )
        self._write_command(
            0xE1, b"\xf0\x08\x0c\x0b\x09\x24\x2b\x22\x43\x38\x15\x16\x2f\x37"
        )
        self._write_command(0x21)
        self._write_command(0x29)
        time.sleep_ms(120)

    def _set_window(self):
        x0 = DISPLAY_X_OFFSET
        x1 = x0 + DISPLAY_WIDTH - 1
        y0 = DISPLAY_Y_OFFSET
        y1 = y0 + DISPLAY_HEIGHT - 1
        self._write_command(0x2A, bytes((x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF)))
        self._write_command(0x2B, bytes((y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF)))
        self._write_command(0x2C)

    def show(self):
        self._set_window()
        self.cs.value(0)
        self.dc.value(1)

        write_bus = self._write_bus
        buffer = self.buffer
        row_bytes = DISPLAY_ROW_BYTES
        width = DISPLAY_WIDTH

        for y in range(DISPLAY_HEIGHT):
            row_start = y * row_bytes
            for x in range(width):
                byte_val = buffer[row_start + (x >> 3)]
                if byte_val & (1 << (7 - (x & 7))):
                    write_bus(0xFF)
                    write_bus(0xFF)
                else:
                    write_bus(0x00)
                    write_bus(0x00)

        self.cs.value(1)

    def clear(self, color=NAVY):
        self.frame.fill(0)

    def text(self, value, x, y, color=WHITE):
        self.frame.text(str(value), x, y, 1)

    def fill_rect(self, x, y, width, height, color):
        self.frame.fill_rect(x, y, width, height, 0 if color in (BLACK, NAVY) else 1)

    def rect(self, x, y, width, height, color):
        self.frame.rect(x, y, width, height, 0 if color in (BLACK, NAVY) else 1)


class LilyGoApp:
    PAGES = ("live", "system", "ota")

    def __init__(self):
        self.display = TDisplayS3()
        self.download_button = machine.Pin(0, machine.Pin.IN, machine.Pin.PULL_UP)
        self.debug_button = machine.Pin(14, machine.Pin.IN, machine.Pin.PULL_UP)
        self.wlan = network.WLAN(network.STA_IF)
        self.last_data_signature = None
        self.last_button_pressed = "KEINER"
        self.current_page = 0
        self.device_role = "gamification"

        self.wlan.active(True)
        try:
            self.wlan.disconnect()
        except Exception:
            pass

        try:
            self.wlan.config(pm=0xA11140)
        except Exception:
            pass

    def check_reset_combo(self):
        if self.download_button.value() == 0 and self.debug_button.value() == 0:
            press_start = time.ticks_ms()
            while self.download_button.value() == 0 and self.debug_button.value() == 0:
                if time.ticks_diff(time.ticks_ms(), press_start) >= 2000:
                    self.handle_boot_file_deletion()
                    return True
                time.sleep_ms(30)
        return False

    def draw_status(self, title, detail="", color=CYAN):
        self.display.clear()
        self.display.fill_rect(0, 0, DISPLAY_WIDTH, 38, PANEL)
        self.display.text("FPV GAMIFICATION", 12, 15, WHITE)
        self.display.text(title, 8, 66, color)
        if detail:
            self.display.text(detail[:20], 8, 88, WHITE)
        self.display.text(WIFI_SSID[:20], 8, 280, CYAN)
        self.display.show()

    def draw_progress_bar(self, title, filename, current_bytes, total_bytes):
        self.display.clear()
        self.display.fill_rect(0, 0, DISPLAY_WIDTH, 38, PANEL)
        self.display.text("DOWNLOAD...", 30, 15, WHITE)

        self.display.text(title[:18], 8, 55, YELLOW)
        self.display.text(filename[:18], 8, 75, WHITE)

        bar_x = 10
        bar_y = 110
        bar_w = 150
        bar_h = 18

        self.display.rect(bar_x, bar_y, bar_w, bar_h, WHITE)

        percent = 0
        if total_bytes and total_bytes > 0:
            percent = min(100, int((current_bytes / total_bytes) * 100))
            fill_w = int((bar_w - 4) * (percent / 100))
            if fill_w > 0:
                self.display.fill_rect(bar_x + 2, bar_y + 2, fill_w, bar_h - 4, GREEN)
            self.display.text(str(percent) + " %", 65, 140, GREEN)
        else:
            self.display.text(str(current_bytes) + " B", 50, 140, WHITE)

        self.display.text("STATUS: LAEUFT", 8, 180, CYAN)
        self.display.text("BTN: " + self.last_button_pressed, 8, 280, WHITE)
        self.display.show()

    def draw_boot_deleted(self, remaining_seconds):
        self.display.clear()
        self.display.fill_rect(0, 0, DISPLAY_WIDTH, 38, PANEL)
        self.display.text("SYSTEM RESET", 30, 15, WHITE)

        self.display.text("boot.py GELOESCHT!", 8, 65, RED)
        self.display.text("Autostart wurde", 8, 95, WHITE)
        self.display.text("deaktiviert.", 8, 115, WHITE)

        self.display.fill_rect(8, 145, 154, 1, CYAN)

        self.display.text("REBOOT IN:", 8, 165, CYAN)
        self.display.text(str(remaining_seconds) + " SEKUNDEN", 8, 190, YELLOW)

        self.display.text("Thonny bereit!", 8, 280, GREEN)
        self.display.show()

    def handle_boot_file_deletion(self):
        print("[SYSTEM] Beide Buttons gedrueckt. Loesche boot.py...")
        try:
            os.remove("boot.py")
            print("[SYSTEM] boot.py erfolgreich geloescht.")
        except Exception as error:
            print("[SYSTEM] boot.py konnte nicht geloescht werden:", error)

        for remaining in range(10, 0, -1):
            self.draw_boot_deleted(remaining)
            time.sleep(1)

        self.draw_boot_deleted(0)
        time.sleep_ms(300)
        machine.reset()

    def connect(self):
        attempt = 0
        while not self.wlan.isconnected():
            self.check_reset_combo()
            attempt += 1
            print("[LILYGO] Suche Pico-WLAN, Versuch", attempt)
            self.draw_status("SUCHE PICO...", "Versuch " + str(attempt), YELLOW)

            try:
                self.wlan.disconnect()
            except Exception:
                pass

            time.sleep_ms(100)

            try:
                self.wlan.connect(WIFI_SSID, WIFI_PASSWORD)
            except Exception:
                pass

            deadline = time.ticks_add(time.ticks_ms(), 12000)
            while not self.wlan.isconnected() and time.ticks_diff(deadline, time.ticks_ms()) > 0:
                self.check_reset_combo()
                time.sleep_ms(250)

            if not self.wlan.isconnected():
                self.draw_status("NICHT GEFUNDEN", "Suche erneut...", RED)
                wait_end = time.ticks_add(time.ticks_ms(), RECONNECT_DELAY_MS)
                while time.ticks_diff(wait_end, time.ticks_ms()) > 0:
                    self.check_reset_combo()
                    time.sleep_ms(50)

        ip_address = self.wlan.ifconfig()[0]
        print("[LILYGO] WLAN verbunden, IP", ip_address)
        self.draw_status("VERBUNDEN", ip_address, GREEN)
        time.sleep_ms(700)
        self.detect_role()

    def detect_role(self):
        try:
            info = self.fetch_json("/system-info")
            self.device_role = info.get("device_role", "gamification")
            print("[LILYGO] Pico-Rolle erkannt:", self.device_role)
        except Exception as error:
            print("[LILYGO] Konnte Pico-Rolle nicht ermitteln:", error)

    def http_get(self, path, output_path=None, progress_callback=None):
        address = socket.getaddrinfo(PICO_HOST, 80)[0][-1]
        connection = socket.socket()

        try:
            connection.settimeout(8)
            connection.connect(address)
            request = (
                "GET "
                + path
                + " HTTP/1.0\r\nHost: "
                + PICO_HOST
                + "\r\nConnection: close\r\n\r\n"
            )
            connection.send(request.encode())

            response = bytearray()
            separator = -1

            while separator < 0:
                self.check_reset_combo()
                chunk = connection.recv(512)
                if not chunk:
                    break
                response.extend(chunk)
                separator = response.find(b"\r\n\r\n")

            if separator < 0:
                raise ValueError("HTTP-Antwort ungueltig")

            header = bytes(response[:separator]).decode()
            if " 200 " not in header.split("\r\n", 1)[0]:
                raise ValueError("HTTP-Fehler")

            content_length = None
            for line in header.split("\r\n")[1:]:
                if line.lower().startswith("content-length:"):
                    content_length = int(line.split(":", 1)[1].strip())
                    break

            body = response[separator + 4 :]

            if output_path is None:
                while content_length is None or len(body) < content_length:
                    self.check_reset_combo()
                    chunk = connection.recv(512)
                    if not chunk:
                        break
                    body.extend(chunk)

                if content_length is not None:
                    body = body[:content_length]
                return bytes(body)

            temp_path = output_path + ".tmp"
            received = 0

            with open(temp_path, "wb") as output:
                if body:
                    if content_length is not None:
                        body = body[:content_length]
                    output.write(body)
                    received += len(body)
                    if progress_callback:
                        progress_callback(received, content_length)

                while content_length is None or received < content_length:
                    self.check_reset_combo()
                    chunk = connection.recv(512)
                    if not chunk:
                        break
                    if content_length is not None:
                        chunk = chunk[: content_length - received]
                    output.write(chunk)
                    received += len(chunk)
                    if progress_callback:
                        progress_callback(received, content_length)

            if content_length is not None and received != content_length:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                raise ValueError("Download unvollstaendig")

            try:
                os.remove(output_path)
            except Exception:
                pass

            os.rename(temp_path, output_path)
            return received

        finally:
            connection.close()

    def fetch_json(self, path):
        last_error = None
        for attempt in range(3):
            self.check_reset_combo()
            try:
                return json.loads(self.http_get(path).decode())
            except Exception as error:
                last_error = error
                print("[LILYGO] Abfrage", path, "Versuch", attempt + 1, "fehlgeschlagen:", error)
                time.sleep_ms(400)
        raise last_error

    def download_file(self, path, filename, title):
        self.draw_progress_bar(title, filename, 0, 100)
        last_error = None

        def on_progress(current, total):
            self.check_reset_combo()
            self.draw_progress_bar(title, filename, current, total)

        for attempt in range(3):
            self.check_reset_combo()
            try:
                size = self.http_get(path, filename, progress_callback=on_progress)
                print("[LILYGO] Gespeichert:", filename, size, "Bytes")
                self.draw_status("GESPEICHERT", filename, GREEN)
                time.sleep_ms(1000)
                return
            except Exception as error:
                last_error = error
                print("[LILYGO] Downloadversuch", attempt + 1, "fehlgeschlagen:", error)
                time.sleep_ms(500)

        self.draw_status("DOWNLOAD FEHLER", str(last_error), RED)
        time.sleep_ms(1000)

    def measure_press(self, button):
        """Haelt bis Loslassen, liefert 'short', 'long' oder 'combo' (zweiter Button dazu)."""
        press_start = time.ticks_ms()
        is_long = False
        while button.value() == 0:
            if self.check_reset_combo():
                return "combo"
            if not is_long and time.ticks_diff(time.ticks_ms(), press_start) >= LONG_PRESS_MS:
                is_long = True
            time.sleep_ms(20)
        return "long" if is_long else "short"

    def next_page(self):
        self.current_page = (self.current_page + 1) % len(self.PAGES)
        self.draw_status("SEITE: " + self.PAGES[self.current_page].upper(), "", CYAN)
        time.sleep_ms(350)

    def trigger_github_update(self):
        self.draw_status("OTA UPDATE", "Sende Befehl...", CYAN)
        try:
            result = self.fetch_json("/start-github-update")
            if result.get("ok"):
                self.draw_status("UPDATE GESTARTET", result.get("message", ""), GREEN)
            else:
                self.draw_status("UPDATE FEHLER", result.get("error", ""), RED)
        except Exception as error:
            self.draw_status("UPDATE FEHLER", str(error), RED)
        time.sleep_ms(900)
        self.current_page = self.PAGES.index("ota")

    def handle_buttons(self):
        if self.check_reset_combo():
            return True

        if self.download_button.value() == 0:
            press_kind = self.measure_press(self.download_button)
            if press_kind == "combo":
                return True
            if press_kind == "long":
                self.last_button_pressed = "BTN 1 (SEITE)"
                self.next_page()
            else:
                self.last_button_pressed = "BTN 1 (SESSION)"
                self.download_file("/download", SESSION_DOWNLOAD_FILE, "SESSION DOWNLOAD")
            return True

        if self.debug_button.value() == 0:
            press_kind = self.measure_press(self.debug_button)
            if press_kind == "combo":
                return True
            if press_kind == "long":
                self.last_button_pressed = "BTN 2 (OTA)"
                self.trigger_github_update()
            else:
                self.last_button_pressed = "BTN 2 (DEBUG)"
                self.download_file("/download-debug", DEBUG_DOWNLOAD_FILE, "DEBUG DOWNLOAD")
            return True

        return False

    def draw_data(self, data):
        score = data.get("score", 0)
        history = data.get("history", [])
        latest = history[-1] if history else "Noch kein Trick"
        infection = data.get("infection", {})

        self.display.clear()
        self.display.fill_rect(0, 0, DISPLAY_WIDTH, 38, PANEL)
        self.display.text("FPV LIVE", 52, 15, GREEN)
        self.display.text("SCORE", 8, 62, CYAN)
        self.display.text(str(score), 8, 82, WHITE)
        if infection.get("running"):
            role = "HOST" if infection.get("infected") else "SUCHE"
            remaining = int(infection.get("remaining_seconds", 0))
            self.display.text("INF " + role, 75, 62, RED if infection.get("infected") else GREEN)
            self.display.text(str(remaining // 60) + ":%02d" % (remaining % 60), 75, 82, WHITE)
        self.display.fill_rect(8, 108, 154, 1, CYAN)
        self.display.text("LETZTES EVENT", 8, 126, CYAN)

        for line_number in range(4):
            start = line_number * 20
            line = str(latest)[start : start + 20]
            if line:
                self.display.text(line, 8, 148 + line_number * 14, WHITE)

        self.display.fill_rect(8, 220, 154, 1, PANEL)
        self.display.text("LETZTER BUTTON:", 8, 232, CYAN)
        self.display.text(self.last_button_pressed, 8, 250, YELLOW)

        self.display.text("BTN1=DL HALT=SEITE", 8, 284, WHITE)
        self.display.text("BTN2=DBG HALT=OTA", 8, 300, WHITE)
        self.display.show()

    def draw_gatehill_status(self, data):
        self.display.clear()
        self.display.fill_rect(0, 0, DISPLAY_WIDTH, 38, PANEL)
        self.display.text("GATEHILL", 46, 15, GREEN)
        self.display.text("STATUS: AKTIV", 8, 60, CYAN)
        self.display.text("IP: " + str(data.get("ip", "?")), 8, 85, WHITE)
        self.display.text("FW: " + str(data.get("firmware_version", "?")), 8, 105, WHITE)

        uptime = int(data.get("uptime_s", 0))
        self.display.text(
            "LAUFZEIT: %d:%02d:%02d" % (uptime // 3600, (uptime % 3600) // 60, uptime % 60),
            8,
            125,
            WHITE,
        )

        self.display.fill_rect(8, 220, 154, 1, PANEL)
        self.display.text("LETZTER BUTTON:", 8, 232, CYAN)
        self.display.text(self.last_button_pressed, 8, 250, YELLOW)
        self.display.text("BTN1=DL HALT=SEITE", 8, 284, WHITE)
        self.display.text("BTN2=DBG HALT=OTA", 8, 300, WHITE)
        self.display.show()

    def draw_system_info(self, data):
        self.display.clear()
        self.display.fill_rect(0, 0, DISPLAY_WIDTH, 38, PANEL)
        self.display.text("SYSTEM INFO", 30, 15, WHITE)

        self.display.text("ROLLE: " + str(data.get("device_role", "?")).upper(), 8, 55, CYAN)
        self.display.text("FW: " + str(data.get("firmware_version", "?")), 8, 75, WHITE)
        self.display.text("IP: " + str(data.get("ip", "?")), 8, 95, WHITE)

        uptime = int(data.get("uptime_s", 0))
        self.display.text(
            "LAUFZEIT: %d:%02d:%02d" % (uptime // 3600, (uptime % 3600) // 60, uptime % 60),
            8,
            115,
            WHITE,
        )
        self.display.text("RAM FREI: " + str(data.get("mem_free", "?")) + " B", 8, 135, WHITE)

        self.display.fill_rect(8, 220, 154, 1, PANEL)
        self.display.text("LETZTER BUTTON:", 8, 232, CYAN)
        self.display.text(self.last_button_pressed, 8, 250, YELLOW)
        self.display.text("BTN1=DL HALT=SEITE", 8, 284, WHITE)
        self.display.text("BTN2=DBG HALT=OTA", 8, 300, WHITE)
        self.display.show()

    def draw_ota_page(self, data):
        self.display.clear()
        self.display.fill_rect(0, 0, DISPLAY_WIDTH, 38, PANEL)
        self.display.text("GITHUB UPDATE", 20, 15, WHITE)

        phase = data.get("phase", "idle")
        label, color = OTA_PHASE_LABELS.get(phase, (str(phase).upper(), WHITE))
        self.display.text(label, 8, 58, color)

        self.display.text("FW AKTUELL: " + str(data.get("firmware_version", "?")), 8, 82, WHITE)
        self.display.text("FW NEU: " + str(data.get("remote_version") or "-"), 8, 100, WHITE)

        bar_x, bar_y, bar_w, bar_h = 10, 122, 150, 18
        self.display.rect(bar_x, bar_y, bar_w, bar_h, WHITE)
        progress = data.get("progress", 0) or 0
        if progress:
            fill_w = int((bar_w - 4) * (min(100, progress) / 100))
            if fill_w > 0:
                self.display.fill_rect(bar_x + 2, bar_y + 2, fill_w, bar_h - 4, GREEN)
        self.display.text(str(progress) + " %", 65, 148, GREEN)

        error = data.get("error", "")
        if error:
            self.display.text(str(error)[:20], 8, 172, RED)

        self.display.fill_rect(8, 220, 154, 1, PANEL)
        self.display.text("BTN2 HALTEN:", 8, 232, CYAN)
        self.display.text("UPDATE STARTEN", 8, 250, CYAN)
        self.display.text("BTN1=DL HALT=SEITE", 8, 284, WHITE)
        self.display.show()

    def run(self):
        next_poll = 0
        while True:
            if not self.wlan.isconnected():
                self.connect()
                next_poll = 0
                self.last_data_signature = None

            if self.handle_buttons():
                next_poll = 0
                self.last_data_signature = None

            now = time.ticks_ms()
            if time.ticks_diff(now, next_poll) >= 0:
                page = self.PAGES[self.current_page]
                data = None
                try:
                    if page == "live":
                        if self.device_role == "gatehill":
                            data = self.fetch_json("/system-info")
                            signature = ("gatehill", data.get("ip"), data.get("firmware_version"))
                            if signature != self.last_data_signature:
                                self.draw_gatehill_status(data)
                                self.last_data_signature = signature
                        else:
                            data = self.fetch_json("/data")
                            signature = (
                                data.get("score", 0),
                                str(data.get("history", [])),
                                self.last_button_pressed,
                            )
                            if signature != self.last_data_signature:
                                self.draw_data(data)
                                self.last_data_signature = signature
                    elif page == "system":
                        data = self.fetch_json("/system-info")
                        signature = ("system", data.get("uptime_s"), data.get("mem_free"))
                        if signature != self.last_data_signature:
                            self.draw_system_info(data)
                            self.last_data_signature = signature
                    elif page == "ota":
                        data = self.fetch_json("/github-update-status")
                        signature = ("ota", data.get("phase"), data.get("progress"))
                        if signature != self.last_data_signature:
                            self.draw_ota_page(data)
                            self.last_data_signature = signature
                except Exception as error:
                    print("[LILYGO] Pico-Abfrage fehlgeschlagen:", error)
                    if not self.wlan.isconnected():
                        continue
                    self.draw_status("PICO OFFLINE", str(error), RED)

                interval = POLL_INTERVAL_MS
                if page == "ota" and isinstance(data, dict) and data.get("active"):
                    interval = OTA_ACTIVE_POLL_INTERVAL_MS
                next_poll = time.ticks_add(time.ticks_ms(), interval)
                gc.collect()

            time.sleep_ms(50)


LilyGoApp().run()