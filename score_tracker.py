import machine
import time
import struct
import network
import socket
import json

# ==================== CONFIGURATION ====================
# WLAN Access Point Einstellungen
AP_SSID = "FPV_Gamification_Pico"
AP_PASSWORD = "drohnenspiel"  # Mindestens 8 Zeichen!

# UART-Schnittstelle zum FC (GP0=TX, GP1=RX)
uart = machine.UART(0, baudrate=115200, tx=machine.Pin(0), rx=machine.Pin(1))

# MSP Commands
MSP_ATTITUDE = 108
MSP_RAW_IMU  = 102
MSP_SET_NAME = 11      # Schreibt den Copter-Namen (für OSD)

# Schwellenwerte für Tricks
GYRO_TRICK_THRESHOLD = 2200  # Etwas feinfühliger angesetzt
STABLE_THRESHOLD     = 350   # Toleranzwert für stabileren Geradeausflug danach
MIN_TRICK_DURATION   = 0.12  # Erlaubt auch extrem knackige Snaps
MAX_TRICK_DURATION   = 1.8   # Großzügiger für Mehrfach-Flips
# =======================================================

# WLAN Access Point starten
ap = network.WLAN(network.AP_IF)
ap.config(essid=AP_SSID, password=AP_PASSWORD)
ap.active(True)
print("WLAN-Hotspot gestartet!")
print("IP-Adresse des Pico W:", ap.ifconfig()[0])


class TrickDetector:
    def __init__(self):
        self.score = 0
        self.in_trick = False
        self.trick_start_time = 0.0
        self.trick_type = None
        self.last_trick_name = "Keiner"
        self.trick_history = []  # Speichert die letzten Tricks für die App
        
        self.accumulated_roll = 0.0
        self.accumulated_pitch = 0.0
        self.accumulated_yaw = 0.0
        self.last_time = time.ticks_ms()

    def update(self, gyro_x, gyro_y, gyro_z):
        current_time = time.ticks_ms()
        dt = (time.ticks_diff(current_time, self.last_time)) / 1000.0
        self.last_time = current_time
        
        abs_gx = abs(gyro_x)
        abs_gy = abs(gyro_y)
        abs_gz = abs(gyro_z)
        
        if not self.in_trick:
            # Erkennt den Start einer extremen Rotation
            if abs_gx > GYRO_TRICK_THRESHOLD or abs_gy > GYRO_TRICK_THRESHOLD or abs_gz > GYRO_TRICK_THRESHOLD:
                self.in_trick = True
                self.trick_start_time = time.ticks_ms()
                self.accumulated_roll = 0.0
                self.accumulated_pitch = 0.0
                self.accumulated_yaw = 0.0
                
                # Primäre Drehachse beim Start festlegen
                if abs_gx > abs_gy and abs_gx > abs_gz:
                    self.trick_type = "Roll"
                elif abs_gy > abs_gx and abs_gy > abs_gz:
                    self.trick_type = "Flip"
                else:
                    self.trick_type = "Spin"
        else:
            # Während der Bewegung Drehung aufsummieren
            self.accumulated_roll += abs_gx * dt
            self.accumulated_pitch += abs_gy * dt
            self.accumulated_yaw += abs_gz * dt
            
            # Abbruch-/Endebedingung: Alle Achsen haben sich beruhigt
            if abs_gx < STABLE_THRESHOLD and abs_gy < STABLE_THRESHOLD and abs_gz < STABLE_THRESHOLD:
                duration = (time.ticks_diff(time.ticks_ms(), self.trick_start_time)) / 1000.0
                if MIN_TRICK_DURATION <= duration <= MAX_TRICK_DURATION:
                    self.evaluate_trick()
                self.in_trick = False
                self.trick_type = None

    def evaluate_trick(self):
        # Skalierungsfaktor für Rotationen (an das Gyroskop-Verhalten angepasst)
        roll_turns = self.accumulated_roll / 5800.0
        pitch_turns = self.accumulated_pitch / 5800.0
        yaw_turns = self.accumulated_yaw / 5800.0
        
        points = 0
        detected_name = ""
        
        # --- TRICK PORTFOLIO ---
        if self.trick_type == "Roll":
            if 0.7 <= roll_turns < 1.4:
                detected_name = "Barrel Roll"
                points = 100
            elif 1.4 <= roll_turns < 2.3:
                detected_name = "Double Roll"
                points = 250
            elif roll_turns >= 2.3:
                detected_name = "Multi-Roll Spin"
                points = 450
                
        elif self.trick_type == "Flip":
            if 0.7 <= pitch_turns < 1.4:
                detected_name = "Power-Flip"
                points = 100
            elif 1.4 <= pitch_turns < 2.3:
                detected_name = "Double Flip"
                points = 250
            elif pitch_turns >= 2.3:
                detected_name = "Super-Looping"
                points = 500
                
        elif self.trick_type == "Spin":
            if 0.7 <= yaw_turns < 1.4:
                detected_name = "Flat Spin 360"
                points = 150
            elif yaw_turns >= 1.4:
                detected_name = "720 Super Spin"
                points = 350
                
        if points > 0:
            self.score += points
            self.last_trick_name = detected_name
            timestamp = time.ticks_ms() / 1000.0
            
            # Verlauf aktualisieren (max 5 Einträge)
            self.trick_history.append(f"[{timestamp:.1f}s] {detected_name} (+{points} Pkt)")
            if len(self.trick_history) > 5:
                self.trick_history.pop(0)
                
            print(f"Trick: {detected_name} | Score: {self.score}")
            update_fc_osd(f"Pkt: {self.score}")


# Globaler Detektor
detector = TrickDetector()


# ==================== MSP SERIAL FUNCTIONS ====================
def send_msp(cmd, payload=bytearray()):
    size = len(payload)
    checksum = size ^ cmd
    for b in payload:
        checksum ^= b
    header = b'$M<'
    packet = header + struct.pack('<BB', size, cmd) + payload + struct.pack('<B', checksum)
    uart.write(packet)


def update_fc_osd(text):
    """ Aktualisiert den Text im FC OSD (überschreibt Craft Name) """
    text_bytes = text.encode('ascii')[:15]
    send_msp(MSP_SET_NAME, bytearray(text_bytes))


def read_gyro():
    """ Holt aktuelle Gyrowerte vom FC """
    send_msp(MSP_RAW_IMU)
    time.sleep_ms(4)  # Kürzere Wartezeit für maximale Loop-Frequenz
    if uart.any():
        data = uart.read()
        if len(data) >= 18 and data[4] == MSP_RAW_IMU:
            # Gyrodaten liegen in Byte 11-16
            gx, gy, gz = struct.unpack('<hhh', data[11:17])
            return gx, gy, gz
    return 0, 0, 0


# ==================== WEBSERVER SETUP ====================
html_template = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>FPV Trick Tracker</title>
    <style>
        body { font-family: 'Arial', sans-serif; background: #121212; color: #ffffff; text-align: center; margin: 0; padding: 20px; }
        .container { max-width: 500px; margin: auto; background: #1e1e1e; padding: 30px; border-radius: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
        h1 { color: #ff9800; font-size: 2em; margin-bottom: 10px; }
        .score-box { font-size: 4em; font-weight: bold; color: #4caf50; margin: 20px 0; text-shadow: 0 0 10px rgba(76,175,80,0.3); }
        .trick-box { font-size: 1.5em; color: #2196f3; margin-bottom: 30px; }
        .history { text-align: left; background: #2a2a2a; padding: 15px; border-radius: 10px; }
        .history h3 { margin-top: 0; border-bottom: 1px solid #444; padding-bottom: 5px; }
        .history ul { list-style-type: none; padding: 0; margin: 0; }
        .history li { padding: 8px 0; border-bottom: 1px solid #333; font-size: 0.9em; }
    </style>
    <script>
        setInterval(function() {
            fetch('/data')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('score').innerText = data.score;
                    document.getElementById('last_trick').innerText = data.last_trick;
                    let list = document.getElementById('history-list');
                    list.innerHTML = "";
                    data.history.forEach(item => {
                        let li = document.createElement('li');
                        li.innerText = item;
                        list.appendChild(li);
                    });
                }).catch(err => console.log("Verbindungsfehler...", err));
        }, 500);
    </script>
</head>
<body>
    <div class="container">
        <h1>🚁 FPV TRICK TRACKER</h1>
        <div class="score-box" id="score">0</div>
        <div class="trick-box">Letzter Trick: <span id="last_trick" style="font-weight:bold;">Keiner</span></div>
        <div class="history">
            <h3>Trick-Verlauf</h3>
            <ul id="history-list"></ul>
        </div>
    </div>
</body>
</html>
"""

# Webserver Socket initialisieren
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(('', 80))
s.listen(2)
s.setblocking(False)  # Verhindert, dass der Webserver den Gyro-Loop blockiert

print("Server wartet auf Verbindungen auf Port 80...")
update_fc_osd("Pkt: 0")

# ==================== HAUPTSCHLEIFE ====================
while True:
    # 1. Drohnendaten holen und verarbeiten
    try:
        gx, gy, gz = read_gyro()
        if gx != 0 or gy != 0 or gz != 0:
            detector.update(gx, gy, gz)
    except Exception as e:
        pass

    # 2. Nicht-blockierender Webserver Handle
    try:
        conn, addr = s.accept()
        request = conn.recv(1024).decode('utf-8')
        
        if 'GET /data' in request:
            # Live-Daten als JSON senden
            data = {
                "score": detector.score,
                "last_trick": detector.last_trick_name,
                "history": detector.trick_history
            }
            conn.send('HTTP/1.1 200 OK\nContent-Type: application/json\nConnection: close\n\n')
            conn.send(json.dumps(data))
        else:
            # HTML Landing Page senden
            conn.send('HTTP/1.1 200 OK\nContent-Type: text/html\nConnection: close\n\n')
            conn.send(html_template)
            
        conn.close()
    except OSError:
        # Passiert standardmäßig bei setblocking(False), wenn kein Client anfragt
        pass

    time.sleep_ms(15)  # Optimierte Updaterate für feinere Integrationsberechnung
