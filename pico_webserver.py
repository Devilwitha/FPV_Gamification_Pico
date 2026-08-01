"""
Pico W Steuerung - WLAN Webserver mit 4 Tasten (Auf / Ab / Stehen / Sitzen)

Verbindet sich mit einem bestehenden WLAN und startet einen Webserver mit
moderner Oberflaeche. Ueber vier Buttons koennen GPIO-Ausgaenge angesteuert
werden (z.B. fuer Relais, Motorsteuerung o.ae.).

Einfach WLAN_SSID / WLAN_PASSWORT unten eintragen (oder eine Datei
"wlan.conf" mit {"ssid": "...", "password": "..."} neben dieses Skript
legen) und als main.py auf den Pico W kopieren.
"""

import network
import socket
import time
import json
from machine import Pin

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

WLAN_SSID = ""
WLAN_PASSWORT = ""

# GPIO-Pins fuer die vier Aktionen (an eigene Verkabelung anpassen)
PIN_AUF = 10
PIN_AB = 11
PIN_STEHEN = 12
PIN_SITZEN = 13

# Wie lange der jeweilige Ausgang aktiviert wird (Sekunden)
IMPULS_DAUER = 0.5

LED = Pin("LED", Pin.OUT)

AKTIONEN = {
    "auf": Pin(PIN_AUF, Pin.OUT),
    "ab": Pin(PIN_AB, Pin.OUT),
    "stehen": Pin(PIN_STEHEN, Pin.OUT),
    "sitzen": Pin(PIN_SITZEN, Pin.OUT),
}
for _pin in AKTIONEN.values():
    _pin.value(0)


def lade_wlan_zugangsdaten():
    """Liest ssid/password aus wlan.conf falls vorhanden, sonst Konstanten oben."""
    try:
        with open("wlan.conf") as f:
            daten = json.load(f)
            ssid = daten.get("ssid") or WLAN_SSID
            passwort = daten.get("password") or WLAN_PASSWORT
            return ssid, passwort
    except OSError:
        return WLAN_SSID, WLAN_PASSWORT


def mit_wlan_verbinden(ssid, passwort, timeout=20):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, passwort)

    start = time.time()
    while not wlan.isconnected():
        if time.time() - start > timeout:
            raise RuntimeError("WLAN-Verbindung fehlgeschlagen (Timeout)")
        LED.toggle()
        time.sleep(0.3)

    LED.value(1)
    ip = wlan.ifconfig()[0]
    print("Mit WLAN verbunden, IP-Adresse:", ip)
    return wlan


def aktion_ausfuehren(name):
    pin = AKTIONEN.get(name)
    if pin is None:
        return False
    pin.value(1)
    time.sleep(IMPULS_DAUER)
    pin.value(0)
    return True


# ---------------------------------------------------------------------------
# Webseite (modernes UI, 2x2 Button-Grid)
# ---------------------------------------------------------------------------

SEITE_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pico Steuerung</title>
<style>
  :root {
    color-scheme: dark;
    --bg-1: #0f172a;
    --bg-2: #1e293b;
    --accent: #38bdf8;
    --accent-2: #a855f7;
    --card: rgba(255,255,255,0.06);
    --card-border: rgba(255,255,255,0.12);
    --text: #e2e8f0;
    --text-dim: #94a3b8;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    background: radial-gradient(circle at top, var(--bg-2), var(--bg-1) 70%);
    color: var(--text);
    padding: 24px;
  }
  .karte {
    width: 100%;
    max-width: 420px;
    background: var(--card);
    border: 1px solid var(--card-border);
    backdrop-filter: blur(16px);
    border-radius: 24px;
    padding: 32px 28px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.45);
  }
  h1 {
    margin: 0 0 4px;
    font-size: 22px;
    text-align: center;
    letter-spacing: 0.5px;
  }
  .status {
    text-align: center;
    color: var(--text-dim);
    font-size: 13px;
    margin-bottom: 28px;
    min-height: 18px;
    transition: color 0.2s ease;
  }
  .status.ok { color: #4ade80; }
  .status.fehler { color: #f87171; }
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  button {
    appearance: none;
    border: 1px solid var(--card-border);
    border-radius: 18px;
    padding: 22px 12px;
    font-size: 16px;
    font-weight: 600;
    color: var(--text);
    background: linear-gradient(160deg, rgba(56,189,248,0.18), rgba(168,85,247,0.12));
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    cursor: pointer;
    transition: transform 0.12s ease, box-shadow 0.12s ease, background 0.12s ease;
  }
  button .icon { font-size: 26px; }
  button:hover {
    box-shadow: 0 10px 24px rgba(56,189,248,0.25);
    transform: translateY(-2px);
  }
  button:active {
    transform: translateY(0) scale(0.96);
    background: linear-gradient(160deg, rgba(56,189,248,0.35), rgba(168,85,247,0.25));
  }
  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
    transform: none;
  }
  footer {
    margin-top: 24px;
    text-align: center;
    font-size: 11px;
    color: var(--text-dim);
    letter-spacing: 0.4px;
  }
</style>
</head>
<body>
  <div class="karte">
    <h1>Pico Steuerung</h1>
    <div class="status" id="status">Bereit</div>
    <div class="grid">
      <button data-aktion="auf"><span class="icon">&#8593;</span>Auf</button>
      <button data-aktion="ab"><span class="icon">&#8595;</span>Ab</button>
      <button data-aktion="stehen"><span class="icon">&#128694;</span>Stehen</button>
      <button data-aktion="sitzen"><span class="icon">&#128719;</span>Sitzen</button>
    </div>
    <footer>Raspberry Pi Pico W</footer>
  </div>

<script>
  const statusEl = document.getElementById('status');
  const buttons = document.querySelectorAll('button[data-aktion]');

  async function sende(aktion) {
    buttons.forEach(b => b.disabled = true);
    statusEl.className = 'status';
    statusEl.textContent = 'Sende "' + aktion + '" ...';
    try {
      const res = await fetch('/aktion/' + aktion);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      statusEl.className = 'status ok';
      statusEl.textContent = data.ok ? ('"' + aktion + '" ausgefuehrt') : 'Fehler bei "' + aktion + '"';
    } catch (err) {
      statusEl.className = 'status fehler';
      statusEl.textContent = 'Verbindungsfehler';
    } finally {
      buttons.forEach(b => b.disabled = false);
    }
  }

  buttons.forEach(btn => {
    btn.addEventListener('click', () => sende(btn.dataset.aktion));
  });
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Webserver
# ---------------------------------------------------------------------------

def http_antwort(client, status, inhalt, content_type="text/html; charset=utf-8"):
    body = inhalt.encode("utf-8") if isinstance(inhalt, str) else inhalt
    header = (
        "HTTP/1.1 {status}\r\n"
        "Content-Type: {ctype}\r\n"
        "Content-Length: {length}\r\n"
        "Connection: close\r\n\r\n"
    ).format(status=status, ctype=content_type, length=len(body))
    client.send(header.encode("utf-8"))
    client.send(body)


def anfrage_bearbeiten(client):
    try:
        request = client.recv(1024)
        if not request:
            client.close()
            return
        zeile = request.decode("utf-8").split("\r\n", 1)[0]
        pfad = zeile.split(" ")[1] if " " in zeile else "/"

        if pfad.startswith("/aktion/"):
            name = pfad.split("/aktion/", 1)[1].split("?")[0]
            erfolg = aktion_ausfuehren(name)
            http_antwort(
                client,
                "200 OK" if erfolg else "404 Not Found",
                json.dumps({"ok": erfolg, "aktion": name}),
                "application/json",
            )
        elif pfad == "/" or pfad == "/index.html":
            http_antwort(client, "200 OK", SEITE_HTML)
        else:
            http_antwort(client, "404 Not Found", "Nicht gefunden")
    except Exception as exc:
        print("Fehler bei der Anfragebearbeitung:", exc)
    finally:
        client.close()


def webserver_starten(port=80):
    adresse = socket.getaddrinfo("0.0.0.0", port)[0][-1]
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(adresse)
    server.listen(4)
    print("Webserver laeuft auf Port", port)

    while True:
        client, addr = server.accept()
        anfrage_bearbeiten(client)


def main():
    ssid, passwort = lade_wlan_zugangsdaten()
    if not ssid:
        raise RuntimeError(
            "Kein WLAN konfiguriert. Bitte WLAN_SSID/WLAN_PASSWORT im Skript "
            "setzen oder eine wlan.conf mit ssid/password anlegen."
        )
    mit_wlan_verbinden(ssid, passwort)
    webserver_starten()


if __name__ == "__main__":
    main()
