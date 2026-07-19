import machine
import time
import struct
import network
import asyncio
import json

# ==================== CONFIGURATION ====================
ENABLE_HOTSPOT = True
ENABLE_SERIAL_DEBUG = True

AP_SSID = "FPV_Gamification_Pico"
AP_PASSWORD = "drohnenspiel"

# GP1 (RX) liest passiv mit 420000 Baud
uart = machine.UART(0, baudrate=420000, tx=machine.Pin(0), rx=machine.Pin(1), rxbuf=1024)

# CRSF Konstanten
CRSF_ADDRESS_FLIGHT_CONTROLLER = 0xC8
CRSF_FRAMETYPE_ATTITUDE = 0x1E

# Bewegungsschwellenwerte (°/s berechnet aus Winkeln)
GYRO_TRICK_THRESHOLD = 220
STABLE_THRESHOLD = 45
MIN_TRICK_DURATION = 0.14
MAX_TRICK_DURATION = 2.2
SETTLE_TIME = 0.08
TRICK_COOLDOWN = 0.10
END_DOMINANT_THRESHOLD = 180
END_COMBINED_THRESHOLD = 520
# =======================================================


def debug_log(message):
    if ENABLE_SERIAL_DEBUG:
        print("[DEBUG]", message)


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
        self.last_trick_end_time = 0.0
        self.trick_type = None
        self.last_trick_name = "Keiner"
        self.trick_history = []

        self.accumulated_roll = 0.0
        self.accumulated_pitch = 0.0
        self.accumulated_yaw = 0.0
        self.active_motion_time = 0.0
        self.stable_since = None

        self.last_roll = 0.0
        self.last_pitch = 0.0
        self.last_yaw = 0.0
        self.last_time = time.ticks_ms()

    def update(self, roll_deg, pitch_deg, yaw_deg):
        current_time = time.ticks_ms()
        dt = (time.ticks_diff(current_time, self.last_time)) / 1000.0
        if dt <= 0.0 or dt > 0.5:
            self.last_time = current_time
            return
        self.last_time = current_time

        def delta_deg(current, last):
            diff = current - last
            while diff > 180:
                diff -= 360
            while diff < -180:
                diff += 360
            return diff

        # Berechne die aktuelle Drehrate (°/s)
        gyro_x = delta_deg(roll_deg, self.last_roll) / dt
        gyro_y = delta_deg(pitch_deg, self.last_pitch) / dt
        gyro_z = delta_deg(yaw_deg, self.last_yaw) / dt

        self.last_roll = roll_deg
        self.last_pitch = pitch_deg
        self.last_yaw = yaw_deg

        abs_gx = abs(gyro_x)
        abs_gy = abs(gyro_y)
        abs_gz = abs(gyro_z)

        if abs_gx > 3000 or abs_gy > 3000 or abs_gz > 3000:
            return

        if not self.in_trick:
            since_last_trick = (time.ticks_diff(current_time, self.last_trick_end_time)) / 1000.0
            if since_last_trick < TRICK_COOLDOWN:
                return

            if abs_gx > GYRO_TRICK_THRESHOLD or abs_gy > GYRO_TRICK_THRESHOLD or abs_gz > GYRO_TRICK_THRESHOLD:
                self.in_trick = True
                self.trick_start_time = time.ticks_ms()
                self.accumulated_roll = 0.0
                self.accumulated_pitch = 0.0
                self.accumulated_yaw = 0.0
                self.active_motion_time = 0.0
                self.stable_since = None

                if abs_gx > abs_gy and abs_gx > abs_gz:
                    self.trick_type = "Roll"
                elif abs_gy > abs_gx and abs_gy > abs_gz:
                    self.trick_type = "Flip"
                else:
                    self.trick_type = "Spin"
                debug_log(f"Trick-Start: {self.trick_type} | gx={abs_gx:.0f} gy={abs_gy:.0f} gz={abs_gz:.0f}")
        else:
            self.accumulated_roll += abs_gx * dt
            self.accumulated_pitch += abs_gy * dt
            self.accumulated_yaw += abs_gz * dt
            self.active_motion_time += dt

            if self.trick_type == "Roll":
                dominant_abs = abs_gx
            elif self.trick_type == "Flip":
                dominant_abs = abs_gy
            else:
                dominant_abs = abs_gz

            combined_abs = abs_gx + abs_gy + abs_gz
            trick_duration = (time.ticks_diff(current_time, self.trick_start_time)) / 1000.0

            end_candidate = (
                dominant_abs < END_DOMINANT_THRESHOLD
                and combined_abs < END_COMBINED_THRESHOLD
            )

            if end_candidate:
                if self.stable_since is None:
                    self.stable_since = current_time
                stable_duration = (time.ticks_diff(current_time, self.stable_since)) / 1000.0
                if stable_duration >= SETTLE_TIME:
                    if MIN_TRICK_DURATION <= trick_duration <= MAX_TRICK_DURATION and self.active_motion_time >= MIN_TRICK_DURATION:
                        self.evaluate_trick(trick_duration)
                    else:
                        debug_log(
                            f"Trick verworfen | Dauer={trick_duration:.2f}s aktiv={self.active_motion_time:.2f}s "
                            f"accR={self.accumulated_roll:.0f} accP={self.accumulated_pitch:.0f} accY={self.accumulated_yaw:.0f}"
                        )
                    self.in_trick = False
                    self.trick_type = None
                    self.last_trick_end_time = current_time
                    self.stable_since = None
            elif trick_duration >= MAX_TRICK_DURATION:
                # Fallback: beendet hängende Tricks auch ohne echte Nullbewegung.
                if self.active_motion_time >= MIN_TRICK_DURATION:
                    self.evaluate_trick(trick_duration)
                else:
                    debug_log(
                        f"Timeout ohne Wertung | Dauer={trick_duration:.2f}s aktiv={self.active_motion_time:.2f}s"
                    )
                self.in_trick = False
                self.trick_type = None
                self.last_trick_end_time = current_time
                self.stable_since = None
            else:
                self.stable_since = None

    def evaluate_trick(self, duration):
        points = 0
        detected_name = ""

        # Finale Achsenentscheidung aus der echten Bewegung, nicht nur aus Start-Spitze.
        roll_strength = self.accumulated_roll
        pitch_strength = self.accumulated_pitch
        yaw_strength = self.accumulated_yaw
        if roll_strength >= pitch_strength and roll_strength >= yaw_strength:
            trick_axis = "Roll"
        elif pitch_strength >= roll_strength and pitch_strength >= yaw_strength:
            trick_axis = "Flip"
        else:
            trick_axis = "Spin"

        if trick_axis == "Roll":
            if 180 <= roll_strength < 340:
                detected_name = "Half Roll"
                points = 60
            elif 340 <= roll_strength < 560:
                detected_name = "Barrel Roll"
                points = 100
            elif 560 <= roll_strength < 860:
                detected_name = "Double Roll"
                points = 250
            elif roll_strength >= 860:
                detected_name = "Super Multi-Roll"
                points = 500

            if duration < 0.24 and roll_strength > 170:
                detected_name = "Juicy Roll Flick"
                points = 180

        elif trick_axis == "Flip":
            if 180 <= pitch_strength < 340:
                if roll_strength > 120:
                    detected_name = "Split-S / Half-Loop"
                    points = 220
                else:
                    detected_name = "Power Flip"
                    points = 100
            elif 340 <= pitch_strength < 560:
                detected_name = "Double Flip"
                points = 250
            elif 560 <= pitch_strength < 860:
                detected_name = "Triple Flip"
                points = 350
            elif pitch_strength >= 860:
                detected_name = "Super Multi-Flip"
                points = 500

            if duration < 0.24 and pitch_strength > 170:
                detected_name = "Juicy Pitch Flick"
                points = 180

            if pitch_strength > 240 and yaw_strength > 130:
                detected_name = "Matty Flip Combo"
                points = 350

        elif trick_axis == "Spin":
            if 180 <= yaw_strength < 340:
                detected_name = "Flat Spin 360"
                points = 150
            elif 340 <= yaw_strength < 560:
                detected_name = "Flat Spin 540"
                points = 220
            elif yaw_strength >= 560:
                detected_name = "Flat Spin 720"
                points = 350

        if points > 0:
            self.score += points
            self.last_trick_name = detected_name
            timestamp = time.ticks_ms() / 1000.0
            self.trick_history.append(f"[{timestamp:.1f}s] {detected_name} (+{points} Pkt)")
            if len(self.trick_history) > 30:
                self.trick_history.pop(0)
            print(f"[SUCCESS] TRICK DETEKTIERT: {detected_name} | Gesamt-Score: {self.score}")


detector = LiveGyroTrickDetector()


async def telemetry_loop():
    debug_log("Passiver CRSF-Telemetrie-Loop gestartet.")

    state = 0
    frame_length = 0
    frame_type = 0
    payload_read = 0
    payload_buffer = bytearray()

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
                        if b == CRSF_ADDRESS_FLIGHT_CONTROLLER:
                            state = 1
                    elif state == 1:
                        frame_length = b
                        if frame_length < 2 or frame_length > 64:
                            state = 0
                        else:
                            state = 2
                    elif state == 2:
                        frame_type = b
                        payload_read = 0
                        payload_buffer = bytearray()
                        state = 3
                    elif state == 3:
                        payload_buffer.append(b)
                        payload_read += 1
                        if payload_read >= (frame_length - 2):
                            if frame_type == CRSF_FRAMETYPE_ATTITUDE and len(payload_buffer) >= 6:
                                pitch, roll, yaw = struct.unpack('>hhh', payload_buffer[:6])

                                roll_deg = roll / 10.0
                                pitch_deg = pitch / 10.0
                                yaw_deg = yaw / 10.0

                                if ENABLE_SERIAL_DEBUG and not detector.in_trick:
                                    print(f"[LIVE GYRO DATA] Roll: {roll_deg:6.1f}° | Pitch: {pitch_deg:6.1f}° | Yaw: {yaw_deg:6.1f}°")

                                detector.update(roll_deg, pitch_deg, yaw_deg)
                            state = 0

            await asyncio.sleep_ms(1)

        except Exception as e:
            print("[ERROR] Telemetrie Fehler:", e)
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
        .btn-download { display: inline-block; background: #2980b9; color: #fff; font-size: 1.1em; font-weight: bold; padding: 12px 25px; border-radius: 8px; text-decoration: none; transition: background 0.2s; border: none; cursor: pointer; }
        .btn-download:hover { background: #3498db; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🐝 ORANGE BEE ARCADE</h1>
        <div class="score-box" id="total_score">0</div>
        <h3>Detektierte Manöver Liste:</h3>
        <div class="log-container" id="trick_list">Warte auf erstes Flugmanöver...</div>
        <a href="/download" class="btn-download" target="_blank">📥 Session als TXT herunterladen</a>
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

        # Header komplett abfrühstücken, um Browser-Hänger zu vermeiden
        while True:
            line = await reader.readline()
            if line == b'\r\n' or line == b'\n' or not line:
                break

        if 'GET /data' in request:
            data = {"score": detector.score, "history": detector.trick_history}
            response_data = json.dumps(data).encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response_data)

        elif 'GET /download' in request:
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

        else:
            response_html = html_template.encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: text/html\r\n')
            writer.write(b'Content-Length: ' + str(len(response_html)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response_html)

        await writer.drain()
    except Exception as e:
        print("[WEB ERROR]", e)
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
