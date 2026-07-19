import machine
import time
import struct
import network
import asyncio
import json
import os

# ==================== CONFIGURATION ====================
ENABLE_HOTSPOT = True        
ENABLE_SERIAL_DEBUG = True   
ENABLE_LIVE_GYRO_DEBUG = True
ENABLE_TRICK_GYRO_IN_TXT_LOG = True

AP_SSID = "FPV_Gamification_Pico"
AP_PASSWORD = "drohnenspiel"  

# GP1 (RX) liest passiv mit 420000 Baud
uart = machine.UART(0, baudrate=420000, tx=machine.Pin(0), rx=machine.Pin(1), rxbuf=1024)

# CRSF Konstanten
CRSF_ADDRESS_FLIGHT_CONTROLLER = 0xC8
CRSF_FRAMETYPE_ATTITUDE        = 0x1E  

# CRSF Frame- und Plausibilitätsgrenzen
CRSF_MAX_FRAME_LEN     = 64
MAX_SAMPLE_DELTA_DEG   = 140
MIN_ACCUM_FOR_TIMEOUT_DEG = 160

# Bewegungsschwellenwerte (°/s berechnet aus Winkeln)
GYRO_TRICK_THRESHOLD = 190
STABLE_THRESHOLD     = 65
TRICK_START_HOLD_MS  = 35
STABLE_HOLD_MS       = 140
TRICK_FORCE_END_MS   = 2200
GYRO_DEADBAND        = 12
GYRO_LOWPASS_ALPHA   = 0.30
MIN_TRICK_DURATION   = 0.12
MAX_TRICK_DURATION   = 2.5
DEBUG_LOG_MAX_LINES  = 300
LIVE_LOG_INTERVAL_MS = 900
LIVE_LOG_DELTA_DEG   = 0.8
DEBUG_LOG_FILE_PATH  = "fpv_debug_session.txt"
DEBUG_LOG_FILE_MAX_BYTES = 180000
# =======================================================

# CRSF attitude payload ist in Radiant * 10000 kodiert.
CRSF_RAD1E4_TO_DEG = 180.0 / (3.141592653589793 * 10000.0)

debug_log_history = []
debug_log_file_enabled = True
debug_log_file_bytes = 0
debug_log_file_limit_reached = False


def init_debug_log_file():
    global debug_log_file_enabled, debug_log_file_bytes, debug_log_file_limit_reached
    debug_log_file_limit_reached = False
    try:
        with open(DEBUG_LOG_FILE_PATH, 'w') as f:
            f.write("=== FPV DEBUG SESSION START ===\n")
        debug_log_file_bytes = os.stat(DEBUG_LOG_FILE_PATH)[6]
    except Exception:
        debug_log_file_enabled = False


def append_debug_file_line(line):
    global debug_log_file_bytes, debug_log_file_limit_reached, debug_log_file_enabled

    if (not debug_log_file_enabled) or debug_log_file_limit_reached:
        return

    text = line + "\n"
    line_size = len(text)

    if debug_log_file_bytes + line_size > DEBUG_LOG_FILE_MAX_BYTES:
        try:
            marker = "[DEBUG] [LOG LIMIT] Debug-Log Datei-Limit erreicht. Weitere Eintraege werden nur live angezeigt.\n"
            marker_size = len(marker)
            if debug_log_file_bytes + marker_size <= DEBUG_LOG_FILE_MAX_BYTES:
                with open(DEBUG_LOG_FILE_PATH, 'a') as f:
                    f.write(marker)
                debug_log_file_bytes += marker_size
        except Exception:
            pass
        debug_log_file_limit_reached = True
        return

    try:
        with open(DEBUG_LOG_FILE_PATH, 'a') as f:
            f.write(text)
        debug_log_file_bytes += line_size
    except Exception:
        debug_log_file_enabled = False

def debug_log(message):
    entry = f"[{time.ticks_ms() // 1000}s] {message}"
    line = f"[DEBUG] {entry}"

    # Trick-TXT bewusst schlank halten: nur Trick-Start/Erfolg/Ende speichern.
    if (
        "Trick gestartet:" in message
        or "[SUCCESS] TRICK DETEKTIERT:" in message
        or "Trick beendet:" in message
    ):
        debug_log_history.append(entry)
        if len(debug_log_history) > DEBUG_LOG_MAX_LINES:
            debug_log_history.pop(0)
        append_debug_file_line(line)

    if ENABLE_SERIAL_DEBUG:
        print(line)


def debug_console_only(message):
    if not ENABLE_SERIAL_DEBUG:
        return
    entry = f"[{time.ticks_ms() // 1000}s] {message}"
    print(f"[DEBUG] {entry}")


def debug_live_gyro_trick(message):
    entry = f"[{time.ticks_ms() // 1000}s] {message}"
    line = f"[DEBUG] {entry}"

    if ENABLE_SERIAL_DEBUG:
        print(line)

    if ENABLE_TRICK_GYRO_IN_TXT_LOG:
        debug_log_history.append(entry)
        if len(debug_log_history) > DEBUG_LOG_MAX_LINES:
            debug_log_history.pop(0)
        append_debug_file_line(line)


def crc8_dvb_s2(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0xD5) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def normalize_angle_deg(angle):
    while angle > 180.0:
        angle -= 360.0
    while angle < -180.0:
        angle += 360.0
    return angle


init_debug_log_file()


# ==================== WLAN HOTSPOT SETUP ====================
ap = None
if ENABLE_HOTSPOT:
    debug_log("Initialisiere WLAN Hotspot (Access Point)...")
    ap = network.WLAN(network.AP_IF)
    ap.config(essid=AP_SSID, password=AP_PASSWORD)
    ap.active(True)
    ap.config(pm=0xa11140)  
    ap.ifconfig(('192.168.4.1', '255.255.255.0', '192.168.4.1', '192.168.4.1'))
    
    debug_log("WLAN-Hotspot erfolgreich gestartet!")
    debug_log(f"SSID: {AP_SSID}")
    debug_log(f"Pico IP-Adresse (Im Browser eingeben): {ap.ifconfig()[0]}")


class LiveGyroTrickDetector:
    def __init__(self):
        self.score = 0
        self.in_trick = False
        self.trick_start_time = 0.0
        self.trick_type = None
        self.last_trick_name = "Keiner"
        self.trick_history = []
        
        self.accumulated_roll = 0.0
        self.accumulated_pitch = 0.0
        self.accumulated_yaw = 0.0
        
        self.last_roll = 0.0
        self.last_pitch = 0.0
        self.last_yaw = 0.0
        self.last_time = time.ticks_ms()

        self.f_gyro_x = 0.0
        self.f_gyro_y = 0.0
        self.f_gyro_z = 0.0
        self.high_rate_since = None
        self.stable_since = None

    def _apply_deadband(self, value, deadband):
        if abs(value) < deadband:
            return 0.0
        return value

    def _start_trick(self, abs_gx, abs_gy, abs_gz):
        self.in_trick = True
        self.trick_start_time = time.ticks_ms()
        self.accumulated_roll = 0.0
        self.accumulated_pitch = 0.0
        self.accumulated_yaw = 0.0
        self.stable_since = None

        if abs_gx > abs_gy and abs_gx > abs_gz:
            self.trick_type = "Roll"
        elif abs_gy > abs_gx and abs_gy > abs_gz:
            self.trick_type = "Flip"
        else:
            self.trick_type = "Spin"

        if ENABLE_SERIAL_DEBUG:
            debug_log(f"Trick gestartet: {self.trick_type} | max-rate={max(abs_gx, abs_gy, abs_gz):.0f}°/s")

    def _finish_trick(self, force=False):
        duration = (time.ticks_diff(time.ticks_ms(), self.trick_start_time)) / 1000.0
        total_accum = self.accumulated_roll + self.accumulated_pitch + self.accumulated_yaw

        # Timeout ohne genug Rotationsenergie ist meistens ein False-Start durch Datenrauschen.
        if force and total_accum < MIN_ACCUM_FOR_TIMEOUT_DEG:
            self.in_trick = False
            self.trick_type = None
            self.high_rate_since = None
            self.stable_since = None
            return

        if MIN_TRICK_DURATION <= duration <= MAX_TRICK_DURATION:
            self.evaluate_trick(duration)
            if ENABLE_SERIAL_DEBUG:
                debug_log(
                    f"Trick beendet: {self.trick_type} | Dauer={duration:.2f}s | "
                    f"R={self.accumulated_roll:.0f} P={self.accumulated_pitch:.0f} Y={self.accumulated_yaw:.0f}"
                )
        elif force and duration > MAX_TRICK_DURATION and ENABLE_SERIAL_DEBUG:
            debug_log("Trick wegen Timeout beendet (kein stabiler Zustand erreicht).")

        self.in_trick = False
        self.trick_type = None
        self.high_rate_since = None
        self.stable_since = None

    def update(self, roll_deg, pitch_deg, yaw_deg):
        current_time = time.ticks_ms()
        dt = (time.ticks_diff(current_time, self.last_time)) / 1000.0
        if dt <= 0.0 or dt > 0.5:
            self.last_time = current_time
            return
        self.last_time = current_time
        
        def delta_deg(current, last):
            diff = current - last
            while diff > 180: diff -= 360
            while diff < -180: diff += 360
            return diff

        # Berechne die aktuelle Drehrate (°/s)
        gyro_x = delta_deg(roll_deg, self.last_roll) / dt
        gyro_y = delta_deg(pitch_deg, self.last_pitch) / dt
        gyro_z = delta_deg(yaw_deg, self.last_yaw) / dt

        # Unplausible Einzel-Spruenge verwerfen (typisch bei korrumpierten Serial-Frames).
        if (
            abs(delta_deg(roll_deg, self.last_roll)) > MAX_SAMPLE_DELTA_DEG
            or abs(delta_deg(pitch_deg, self.last_pitch)) > MAX_SAMPLE_DELTA_DEG
            or abs(delta_deg(yaw_deg, self.last_yaw)) > MAX_SAMPLE_DELTA_DEG
        ):
            self.last_roll = roll_deg
            self.last_pitch = pitch_deg
            self.last_yaw = yaw_deg
            return
        
        self.last_roll = roll_deg
        self.last_pitch = pitch_deg
        self.last_yaw = yaw_deg
        
        if abs(gyro_x) > 3000 or abs(gyro_y) > 3000 or abs(gyro_z) > 3000:
            return

        self.f_gyro_x += (gyro_x - self.f_gyro_x) * GYRO_LOWPASS_ALPHA
        self.f_gyro_y += (gyro_y - self.f_gyro_y) * GYRO_LOWPASS_ALPHA
        self.f_gyro_z += (gyro_z - self.f_gyro_z) * GYRO_LOWPASS_ALPHA

        f_gx = self._apply_deadband(self.f_gyro_x, GYRO_DEADBAND)
        f_gy = self._apply_deadband(self.f_gyro_y, GYRO_DEADBAND)
        f_gz = self._apply_deadband(self.f_gyro_z, GYRO_DEADBAND)

        abs_gx = abs(f_gx)
        abs_gy = abs(f_gy)
        abs_gz = abs(f_gz)
        max_rate = max(abs_gx, abs_gy, abs_gz)
            
        if not self.in_trick:
            if max_rate > GYRO_TRICK_THRESHOLD:
                if self.high_rate_since is None:
                    self.high_rate_since = current_time
                elif time.ticks_diff(current_time, self.high_rate_since) >= TRICK_START_HOLD_MS:
                    self._start_trick(abs_gx, abs_gy, abs_gz)
                    self.high_rate_since = None
            else:
                self.high_rate_since = None
        else:
            self.accumulated_roll += abs_gx * dt
            self.accumulated_pitch += abs_gy * dt
            self.accumulated_yaw += abs_gz * dt

            if abs_gx < STABLE_THRESHOLD and abs_gy < STABLE_THRESHOLD and abs_gz < STABLE_THRESHOLD:
                if self.stable_since is None:
                    self.stable_since = current_time
                elif time.ticks_diff(current_time, self.stable_since) >= STABLE_HOLD_MS:
                    self._finish_trick(force=False)
            else:
                self.stable_since = None

            if time.ticks_diff(current_time, self.trick_start_time) >= TRICK_FORCE_END_MS:
                self._finish_trick(force=True)

    def evaluate_trick(self, duration):
        points = 0
        detected_name = ""
        
        if self.trick_type == "Roll":
            if 70 <= self.accumulated_roll < 170: detected_name = "Barrel Roll"; points = 100
            elif 170 <= self.accumulated_roll < 300: detected_name = "Double Roll"; points = 250
            elif self.accumulated_roll >= 300: detected_name = "Super Multi-Roll"; points = 500
            
            if duration < 0.40 and self.accumulated_roll > 120:
                detected_name = "Juicy Roll Flick"; points = 180

        elif self.trick_type == "Flip":
            if 80 <= self.accumulated_pitch < 190:
                if self.accumulated_roll > 90: detected_name = "Split-S / Half-Loop"; points = 220
                else: detected_name = "Power Flip"; points = 100
            elif 190 <= self.accumulated_pitch < 320: detected_name = "Double Flip"; points = 250
            elif self.accumulated_pitch >= 320: detected_name = "Super Multi-Flip"; points = 500
                
            if duration < 0.40 and self.accumulated_pitch > 120:
                detected_name = "Juicy Pitch Flick"; points = 180
                
            if self.accumulated_pitch > 170 and self.accumulated_yaw > 90:
                detected_name = "Matty Flip Combo"; points = 350

        elif self.trick_type == "Spin":
            if 90 <= self.accumulated_yaw < 220: detected_name = "Flat Spin 360"; points = 150
            elif self.accumulated_yaw >= 220: detected_name = "Flat Spin 720"; points = 350

        if points > 0:
            self.score += points
            self.last_trick_name = detected_name
            timestamp = time.ticks_ms() / 1000.0
            self.trick_history.append(f"[{timestamp:.1f}s] {detected_name} (+{points} Pkt)")
            if len(self.trick_history) > 30: self.trick_history.pop(0)  # Erhöht für längere Listen
            debug_log(f"[SUCCESS] TRICK DETEKTIERT: {detected_name} | Gesamt-Score: {self.score}")
        elif ENABLE_SERIAL_DEBUG:
            debug_log(
                f"Trick verworfen (unter Schwelle): Typ={self.trick_type} | "
                f"R={self.accumulated_roll:.0f} P={self.accumulated_pitch:.0f} Y={self.accumulated_yaw:.0f}"
            )


detector = LiveGyroTrickDetector()


async def telemetry_loop():
    debug_log("Passiver CRSF-Telemetrie-Loop gestartet.")
    
    state = 0
    frame_length = 0
    frame_type = 0
    payload_expected = 0
    payload_read = 0
    payload_buffer = bytearray()
    frame_crc = 0
    last_live_log_ms = time.ticks_ms()
    last_live_roll = None
    last_live_pitch = None
    last_live_yaw = None
    crc_fail_count = 0

    while True:
        try:
            avail = uart.any()
            if avail > 0:
                chunk = uart.read(min(avail, 64))
                if not chunk:
                    await asyncio.sleep_ms(1)
                    continue

                for b in chunk:
                    if state == 0:  
                        if b == CRSF_ADDRESS_FLIGHT_CONTROLLER: state = 1
                    elif state == 1:  
                        frame_length = b
                        if frame_length < 4 or frame_length > CRSF_MAX_FRAME_LEN:
                            state = 0
                        else:
                            state = 2
                    elif state == 2:  
                        frame_type = b
                        payload_read = 0
                        payload_expected = frame_length - 2
                        payload_buffer = bytearray()
                        frame_crc = 0
                        state = 3
                    elif state == 3:  
                        payload_buffer.append(b)
                        payload_read += 1
                        if payload_read >= payload_expected:
                            state = 4
                    elif state == 4:
                        frame_crc = b
                        calc_crc = crc8_dvb_s2(bytearray([frame_type]) + payload_buffer)

                        if calc_crc == frame_crc:
                            crc_fail_count = 0
                            if frame_type == CRSF_FRAMETYPE_ATTITUDE and len(payload_buffer) == 6:
                                pitch, roll, yaw = struct.unpack('>hhh', payload_buffer)

                                roll_deg = normalize_angle_deg(roll * CRSF_RAD1E4_TO_DEG)
                                pitch_deg = normalize_angle_deg(pitch * CRSF_RAD1E4_TO_DEG)
                                yaw_deg = normalize_angle_deg(yaw * CRSF_RAD1E4_TO_DEG)

                                if (
                                    ENABLE_SERIAL_DEBUG
                                    and ENABLE_LIVE_GYRO_DEBUG
                                    and not detector.in_trick
                                    and time.ticks_diff(time.ticks_ms(), last_live_log_ms) >= 250
                                ):
                                    should_log_live = False
                                    if (
                                        last_live_roll is None
                                        or last_live_pitch is None
                                        or last_live_yaw is None
                                    ):
                                        should_log_live = True
                                    else:
                                        if (
                                            abs(roll_deg - last_live_roll) >= LIVE_LOG_DELTA_DEG
                                            or abs(pitch_deg - last_live_pitch) >= LIVE_LOG_DELTA_DEG
                                            or abs(yaw_deg - last_live_yaw) >= LIVE_LOG_DELTA_DEG
                                        ):
                                            should_log_live = True

                                    if should_log_live and time.ticks_diff(time.ticks_ms(), last_live_log_ms) >= LIVE_LOG_INTERVAL_MS:
                                        debug_console_only(f"[LIVE GYRO DATA] Roll: {roll_deg:6.1f}° | Pitch: {pitch_deg:6.1f}° | Yaw: {yaw_deg:6.1f}°")
                                        last_live_log_ms = time.ticks_ms()
                                        last_live_roll = roll_deg
                                        last_live_pitch = pitch_deg
                                        last_live_yaw = yaw_deg

                                detector.update(roll_deg, pitch_deg, yaw_deg)

                                if ENABLE_SERIAL_DEBUG and detector.in_trick:
                                    debug_live_gyro_trick(
                                        f"[LIVE GYRO TRICK] Roll: {roll_deg:6.1f}° | "
                                        f"Pitch: {pitch_deg:6.1f}° | Yaw: {yaw_deg:6.1f}°"
                                    )
                        else:
                            crc_fail_count += 1
                            if ENABLE_SERIAL_DEBUG and (crc_fail_count % 40 == 0):
                                debug_log(f"CRSF CRC Fehler erkannt (Anzahl: {crc_fail_count})")

                        state = 0
            
            await asyncio.sleep_ms(1)
            
        except Exception as e:
            debug_log(f"[ERROR] Telemetrie Fehler: {e}")
            state = 0
            await asyncio.sleep_ms(1)


# ==================== WEBSERVER TEMPLATE ====================
html_template = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Orange Bee Ultimate Arcade</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #0b0e14; color: #f0f4f8; font-family: sans-serif; text-align: center; padding: 30px 10px; margin: 0; }
        .card { max-width: 500px; margin: 0 auto; background: #141b25; padding: 30px; border-radius: 20px; border: 2px solid #233247; box-shadow: 0 15px 35px rgba(0,0,0,0.6); }
        h1 { color: #f39c12; letter-spacing: 2px; text-transform: uppercase; margin-top: 0; }
        .score-box { font-size: 5.5em; font-weight: bold; color: #2ecc71; text-shadow: 0 0 20px rgba(46,204,113,0.4); margin: 15px 0; font-family: monospace; }
        h3 { text-align: left; color: #95a5a6; border-bottom: 1px solid #233247; padding-bottom: 8px; margin-top: 25px; }
        .log-container { text-align: left; background: #070a0f; padding: 15px; border-radius: 10px; font-family: monospace; min-height: 180px; max-height: 250px; overflow-y: auto; border: 1px solid #1a2432; margin-bottom: 20px; }
        .trick-item { padding: 6px 0; border-bottom: 1px solid #111822; font-size: 1.1em; color: #ecf0f1; }
        .trick-item:first-child { color: #f1c40f; font-weight: bold; }
        .button-row { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }
        .btn-download { display: inline-block; background: #2980b9; color: #fff; font-size: 1.05em; font-weight: bold; padding: 12px 20px; border-radius: 8px; text-decoration: none; transition: background 0.2s; border: none; cursor: pointer; }
        .btn-download:hover { background: #3498db; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🐝 ORANGE BEE ARCADE</h1>
        <div class="score-box" id="total_score">0</div>
        <h3>Detektierte Manöver Liste:</h3>
        <div class="log-container" id="trick_list">Warte auf erstes Flugmanöver...</div>
        <div class="button-row">
            <a href="/download" class="btn-download" target="_blank">📥 Session als TXT</a>
            <a href="/download-debug" class="btn-download" target="_blank">🧪 Debug-Log als TXT</a>
        </div>
    </div>

    <script>
    function updateData() {
        fetch('/data')
            .then(res => res.json())
            .then(data => {
                document.getElementById('total_score').innerText = data.score;
                const container = document.getElementById('trick_list');
                if (data.history.length > 0) {
                    const reversed = [...data.history].reverse();
                    container.innerHTML = reversed.map(item => `<div class="trick-item">${item}</div>`).join('');
                } else {
                    container.innerHTML = "<div style='color:#7f8c8d;'>Warte auf erstes Flugmanöver...</div>";
                }
            })
            .catch(err => console.log("Fetch Error:", err));
    }
    setInterval(updateData, 250);
    </script>
</body>
</html>"""


async def handle_client(reader, writer):
    try:
        request_line = await reader.readline()
        if not request_line: 
            return
        
        request = request_line.decode('utf-8')
        parts = request.split(' ')
        request_path = parts[1] if len(parts) >= 2 else '/'
        
        # Header komplett abfrühstücken, um Browser-Hänger zu vermeiden
        while True:
            line = await reader.readline()
            if line == b'\r\n' or line == b'\n' or not line: 
                break
                
        if request_path == '/data':
            data = {"score": detector.score, "history": detector.trick_history}
            response_data = json.dumps(data).encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response_data)
            
        elif request_path == '/download':
            # Erstelle den Inhalt des Text-Dokuments
            txt_content = "========================================\n"
            txt_content += "        ORANGE BEE ARCADE SESSION       \n"
            txt_content += "========================================\n\n"
            txt_content += "GELANDETE TRICKS:\n"
            
            if detector.trick_history:
                for trick in detector.trick_history:
                    txt_content += f"- {trick}\n"
            else:
                txt_content += "- Keine Tricks aufgezeichnet -\n"
                
            txt_content += "\n----------------------------------------\n"
            txt_content += f"GESAMT-PUNKTESTAND: {detector.score} PKT\n"
            txt_content += "----------------------------------------\n"
            
            response_txt = txt_content.encode('utf-8')
            
            # Sende die Header, die den Browser zwingen, eine Datei zu speichern
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: text/plain\r\n')
            writer.write(b'Content-Disposition: attachment; filename=fpv_arcade_session.txt\r\n')
            writer.write(b'Content-Length: ' + str(len(response_txt)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response_txt)

        elif request_path == '/download-debug':
            txt_content = "========================================\n"
            txt_content += "         ORANGE BEE DEBUG LOG           \n"
            txt_content += "========================================\n\n"

            file_loaded = False
            try:
                with open(DEBUG_LOG_FILE_PATH, 'r') as f:
                    txt_content += f.read()
                file_loaded = True
            except Exception:
                file_loaded = False

            if not file_loaded:
                if debug_log_history:
                    txt_content += "\n".join(debug_log_history)
                    txt_content += "\n"
                else:
                    txt_content += "Keine Debug-Logs vorhanden.\n"

            response_txt = txt_content.encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: text/plain\r\n')
            writer.write(b'Content-Disposition: attachment; filename=fpv_debug_log.txt\r\n')
            writer.write(b'Content-Length: ' + str(len(response_txt)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response_txt)
            
        else:
            response_html = html_template.encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: text/html\r\n')
            writer.write(b'Content-Length: ' + str(len(response_html)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response_html)
            
        await writer.drain()
    except Exception as e: 
        debug_log(f"[WEB ERROR] {e}")
    finally:
        try: 
            await writer.close()
            await asyncio.sleep_ms(5)
        except Exception: 
            pass


async def main_async():
    if ENABLE_HOTSPOT:
        await asyncio.start_server(handle_client, "0.0.0.0", 80)
    await telemetry_loop()


def run():
    debug_log("tracker.run() wurde aufgerufen.")
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        debug_log("System manuell gestoppt.")

run()

