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
import gc
import _thread
from machine import Pin

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

WLAN_SSID = "FRITZ!Box 5530 BA_2GEXT"
WLAN_PASSWORT = "1234567890"

# Geraetename, unter dem der Pico im lokalen Netz per DHCP angemeldet wird.
# Der Router haengt die lokale Domain an (z.B. "tisch"), damit ist das
# Geraet dann als "pult.tisch" erreichbar statt nur ueber die IP-Adresse.
GERAETENAME = "pult"

# GPIO-Pins fuer die vier Aktionen (an eigene Verkabelung anpassen)
PIN_AUF = 13
PIN_AB = 10
PIN_STEHEN = 11
PIN_SITZEN = 12

# Wie lange "stehen"/"sitzen" aktiviert werden (Sekunden). "auf"/"ab" sind
# stattdessen Halte-Aktionen: aktiv solange der Button gedrueckt ist.
IMPULS_DAUER = 0.5

# Aktionen, die per Start/Stop (Halten) statt per Impuls gesteuert werden
HALTE_AKTIONEN = ("auf", "ab")

LED = Pin("LED", Pin.OUT)

AKTIONEN = {
    "auf": Pin(PIN_AUF, Pin.OUT),
    "ab": Pin(PIN_AB, Pin.OUT),
    "stehen": Pin(PIN_STEHEN, Pin.OUT),
    "sitzen": Pin(PIN_SITZEN, Pin.OUT),
}
for _pin in AKTIONEN.values():
    _pin.value(0)

# Zeitpunkt des Skriptstarts (fuer die Laufzeit-Anzeige) und die zuletzt
# vergebene IP-Adresse (fuer die Info-Zeile im Web-UI)
BOOT_ZEIT = time.time()
AKTUELLE_IP = ""

# Kurzer Verlauf der letzten Aktionen (manuell oder durch die Automatik
# ausgeloest), neueste zuerst
VERLAUF = []
VERLAUF_MAX = 8


def verlauf_eintragen(aktion, quelle):
    VERLAUF.insert(0, {"aktion": aktion, "quelle": quelle, "zeit": time.time()})
    del VERLAUF[VERLAUF_MAX:]


def verlauf_abfragen():
    jetzt = time.time()
    return [
        {
            "aktion": eintrag["aktion"],
            "quelle": eintrag["quelle"],
            "vor_sek": max(0, int(jetzt - eintrag["zeit"])),
        }
        for eintrag in VERLAUF
    ]


def geraeteinfo():
    return {
        "ip": AKTUELLE_IP,
        "hostname": GERAETENAME,
        "uptime_sek": int(time.time() - BOOT_ZEIT),
    }


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


def hostname_setzen(name):
    """Setzt den DHCP-Hostnamen, ueber den der Pico im lokalen Netz sichtbar ist."""
    try:
        network.hostname(name)
    except (AttributeError, OSError):
        pass


def mit_wlan_verbinden(ssid, passwort, timeout=20):
    global AKTUELLE_IP
    hostname_setzen(GERAETENAME)

    wlan = network.WLAN(network.STA_IF)
    try:
        wlan.config(hostname=GERAETENAME)
    except (ValueError, OSError):
        pass
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
    AKTUELLE_IP = ip
    print("Mit WLAN verbunden, IP-Adresse:", ip, "- Hostname:", GERAETENAME)
    return wlan


def aktion_ausfuehren(name, quelle="manuell"):
    """Kurzer Impuls fuer Aktionen wie 'stehen'/'sitzen'."""
    pin = AKTIONEN.get(name)
    if pin is None or name in HALTE_AKTIONEN:
        return False
    pin.value(1)
    time.sleep(IMPULS_DAUER)
    pin.value(0)
    verlauf_eintragen(name, quelle)
    return True


def aktion_start(name):
    """Schaltet eine Halte-Aktion ('auf'/'ab') ein, solange der Button gedrueckt ist."""
    pin = AKTIONEN.get(name)
    if pin is None or name not in HALTE_AKTIONEN:
        return False
    pin.value(1)
    verlauf_eintragen(name, "manuell")
    return True


def aktion_stop(name):
    """Schaltet eine Halte-Aktion ('auf'/'ab') wieder aus."""
    pin = AKTIONEN.get(name)
    if pin is None or name not in HALTE_AKTIONEN:
        return False
    pin.value(0)
    return True


# ---------------------------------------------------------------------------
# Automatik: wechselt nach Ablauf der eingestellten Zeit selbststaendig
# zwischen Sitzen und Stehen (laeuft im Hintergrund auf dem zweiten Kern)
# ---------------------------------------------------------------------------

automatik_aktiv = False
automatik_sitzen_sek = 90 * 60
automatik_stehen_sek = 30 * 60
automatik_phase = "sitzen"
automatik_phase_start = 0


def automatik_einschalten(sitzen_min, stehen_min, start_phase="sitzen"):
    global automatik_aktiv, automatik_sitzen_sek, automatik_stehen_sek
    global automatik_phase, automatik_phase_start

    sitzen_min = max(1, float(sitzen_min))
    stehen_min = max(1, float(stehen_min))

    automatik_sitzen_sek = int(sitzen_min * 60)
    automatik_stehen_sek = int(stehen_min * 60)
    automatik_phase = start_phase if start_phase in ("sitzen", "stehen") else "sitzen"
    automatik_phase_start = time.time()
    automatik_aktiv = True
    return True


def automatik_ausschalten():
    global automatik_aktiv
    automatik_aktiv = False
    return True


def automatik_status():
    if not automatik_aktiv:
        return {"aktiv": False}
    dauer = automatik_sitzen_sek if automatik_phase == "sitzen" else automatik_stehen_sek
    rest = dauer - (time.time() - automatik_phase_start)
    return {
        "aktiv": True,
        "phase": automatik_phase,
        "rest_sek": max(0, int(rest)),
        "sitzen_min": automatik_sitzen_sek // 60,
        "stehen_min": automatik_stehen_sek // 60,
    }


def automatik_thread():
    """Prueft laufend, ob die Zeit fuer die aktuelle Phase abgelaufen ist."""
    global automatik_phase, automatik_phase_start
    while True:
        if automatik_aktiv:
            dauer = automatik_sitzen_sek if automatik_phase == "sitzen" else automatik_stehen_sek
            if time.time() - automatik_phase_start >= dauer:
                neue_phase = "stehen" if automatik_phase == "sitzen" else "sitzen"
                aktion_ausfuehren(neue_phase, "automatik")
                automatik_phase = neue_phase
                automatik_phase_start = time.time()
        time.sleep(1)


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
    --bg-1: #0b1120;
    --bg-2: #1e293b;
    --accent: #38bdf8;
    --accent-2: #a855f7;
    --gruen: #4ade80;
    --rot: #f87171;
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
    color: var(--text);
    padding: 24px;
    background: var(--bg-1);
    overflow-x: hidden;
  }
  body::before, body::after {
    content: "";
    position: fixed;
    width: 60vmax;
    height: 60vmax;
    border-radius: 50%;
    filter: blur(90px);
    opacity: 0.28;
    z-index: -1;
    animation: schweben 16s ease-in-out infinite alternate;
  }
  body::before {
    background: var(--accent);
    top: -20vmax;
    left: -20vmax;
  }
  body::after {
    background: var(--accent-2);
    bottom: -22vmax;
    right: -18vmax;
    animation-delay: -8s;
  }
  @keyframes schweben {
    from { transform: translate(0, 0) scale(1); }
    to { transform: translate(4vmax, 3vmax) scale(1.08); }
  }
  .karte {
    position: relative;
    width: 100%;
    max-width: 420px;
    background: var(--card);
    border: 1px solid var(--card-border);
    backdrop-filter: blur(20px);
    border-radius: 26px;
    padding: 30px 26px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.45);
  }
  .kopf {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    margin-bottom: 4px;
  }
  h1 {
    margin: 0;
    font-size: 21px;
    text-align: center;
    letter-spacing: 0.3px;
  }
  .live-punkt {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--text-dim);
    flex-shrink: 0;
  }
  .live-punkt.online {
    background: var(--gruen);
    box-shadow: 0 0 0 0 rgba(74,222,128,0.6);
    animation: puls 2s ease-out infinite;
  }
  .live-punkt.offline { background: var(--rot); }
  @keyframes puls {
    0%   { box-shadow: 0 0 0 0 rgba(74,222,128,0.55); }
    70%  { box-shadow: 0 0 0 8px rgba(74,222,128,0); }
    100% { box-shadow: 0 0 0 0 rgba(74,222,128,0); }
  }
  .info-zeile {
    text-align: center;
    font-size: 11.5px;
    color: var(--text-dim);
    margin-bottom: 22px;
    letter-spacing: 0.2px;
  }
  .status {
    text-align: center;
    color: var(--text-dim);
    font-size: 13px;
    margin-bottom: 20px;
    min-height: 18px;
    transition: color 0.2s ease;
  }
  .status.ok { color: var(--gruen); }
  .status.fehler { color: var(--rot); }
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
  .trenner {
    height: 1px;
    background: var(--card-border);
    margin: 26px 0 20px;
  }
  .abschnitt-kopf {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
  }
  .abschnitt-kopf .titel {
    font-weight: 600;
    font-size: 15px;
  }
  .abschnitt-kopf .zaehler {
    font-size: 11px;
    color: var(--text-dim);
    background: rgba(255,255,255,0.06);
    border-radius: 999px;
    padding: 3px 9px;
  }
  .schalter {
    position: relative;
    display: inline-block;
    width: 46px;
    height: 26px;
    flex-shrink: 0;
  }
  .schalter input {
    opacity: 0;
    width: 0;
    height: 0;
  }
  .schalter-slider {
    position: absolute;
    cursor: pointer;
    inset: 0;
    background: rgba(255,255,255,0.15);
    border-radius: 999px;
    transition: background 0.2s ease;
  }
  .schalter-slider::before {
    content: "";
    position: absolute;
    height: 20px;
    width: 20px;
    left: 3px;
    top: 3px;
    background: white;
    border-radius: 50%;
    transition: transform 0.2s ease;
  }
  .schalter input:checked + .schalter-slider {
    background: linear-gradient(135deg, var(--accent), var(--accent-2));
  }
  .schalter input:checked + .schalter-slider::before {
    transform: translateX(20px);
  }
  .automatik-felder {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 14px;
  }
  .feld label {
    display: block;
    font-size: 11px;
    color: var(--text-dim);
    margin-bottom: 6px;
  }
  .feld input, .feld select {
    width: 100%;
    background: rgba(255,255,255,0.05);
    border: 1px solid var(--card-border);
    border-radius: 10px;
    padding: 10px;
    color: var(--text);
    font-size: 14px;
  }
  .feld input:disabled, .feld select:disabled {
    opacity: 0.5;
  }
  .automatik-info {
    text-align: center;
    font-size: 13px;
    color: var(--text-dim);
    min-height: 18px;
  }
  .automatik-status {
    text-align: center;
    border-radius: 16px;
    padding: 16px;
    margin-bottom: 4px;
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--card-border);
    transition: background 0.2s ease, border-color 0.2s ease;
  }
  .automatik-status.aktiv {
    background: linear-gradient(160deg, rgba(56,189,248,0.14), rgba(168,85,247,0.10));
    border-color: rgba(56,189,248,0.35);
  }
  .automatik-phase {
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 6px;
  }
  .automatik-timer {
    font-size: 32px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    letter-spacing: 1px;
  }
  .automatik-sub {
    font-size: 12px;
    color: var(--text-dim);
    margin-top: 4px;
  }
  .verlauf-liste {
    list-style: none;
    margin: 0;
    padding: 0;
    max-height: 220px;
    overflow-y: auto;
  }
  .verlauf-liste li {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 9px 4px;
    border-bottom: 1px solid var(--card-border);
    font-size: 13px;
  }
  .verlauf-liste li:last-child { border-bottom: none; }
  .verlauf-icon {
    width: 30px;
    height: 30px;
    flex-shrink: 0;
    border-radius: 10px;
    background: rgba(255,255,255,0.06);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
  }
  .verlauf-text { flex: 1; }
  .verlauf-badge {
    font-size: 9.5px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: var(--accent-2);
    background: rgba(168,85,247,0.14);
    border-radius: 999px;
    padding: 2px 7px;
    margin-left: 6px;
  }
  .verlauf-zeit {
    font-size: 11.5px;
    color: var(--text-dim);
    white-space: nowrap;
  }
  .verlauf-leer {
    text-align: center;
    font-size: 12.5px;
    color: var(--text-dim);
    padding: 10px 0;
  }
</style>
</head>
<body>
  <div class="karte">
    <div class="kopf">
      <span class="live-punkt" id="livePunkt"></span>
      <h1>Pico Steuerung</h1>
    </div>
    <div class="info-zeile" id="infoZeile">Verbinde...</div>
    <div class="status" id="status">Bereit</div>
    <div class="grid">
      <button data-aktion="auf" data-modus="halten"><span class="icon">&#8593;</span>Auf</button>
      <button data-aktion="ab" data-modus="halten"><span class="icon">&#8595;</span>Ab</button>
      <button data-aktion="stehen" data-modus="impuls"><span class="icon">&#128694;</span>Stehen</button>
      <button data-aktion="sitzen" data-modus="impuls"><span class="icon">&#128719;</span>Sitzen</button>
    </div>

    <div class="trenner"></div>

    <div class="abschnitt-kopf">
      <span class="titel">Automatik</span>
      <label class="schalter">
        <input type="checkbox" id="automatikToggle">
        <span class="schalter-slider"></span>
      </label>
    </div>
    <div class="automatik-felder">
      <div class="feld">
        <label for="sitzenMin">Sitzen (Min)</label>
        <input type="number" id="sitzenMin" min="1" value="90">
      </div>
      <div class="feld">
        <label for="stehenMin">Stehen (Min)</label>
        <input type="number" id="stehenMin" min="1" value="30">
      </div>
      <div class="feld" style="grid-column: 1 / -1;">
        <label for="startPhase">Aktuelle Position</label>
        <select id="startPhase">
          <option value="sitzen">Sitzen</option>
          <option value="stehen">Stehen</option>
        </select>
      </div>
    </div>
    <div class="automatik-status" id="automatikStatus">
      <div class="automatik-phase" id="automatikPhase">Automatik</div>
      <div class="automatik-timer" id="automatikTimer">--:--</div>
      <div class="automatik-sub" id="automatikSub">ausgeschaltet</div>
    </div>

    <div class="trenner"></div>

    <div class="abschnitt-kopf">
      <span class="titel">Verlauf</span>
    </div>
    <ul class="verlauf-liste" id="verlaufListe">
      <li class="verlauf-leer">Noch keine Aktionen</li>
    </ul>

    <div class="trenner"></div>
    <footer>Raspberry Pi Pico W</footer>
  </div>

<script>
  const statusEl = document.getElementById('status');
  const buttons = document.querySelectorAll('button[data-aktion]');

  function zeigeStatus(text, art) {
    statusEl.className = art ? ('status ' + art) : 'status';
    statusEl.textContent = text;
  }

  async function anfrage(pfad, aktion, erfolgstext) {
    try {
      const res = await fetch(pfad);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      zeigeStatus(data.ok ? erfolgstext : ('Fehler bei "' + aktion + '"'), data.ok ? 'ok' : 'fehler');
      if (data.ok) verlaufAbrufen();
    } catch (err) {
      zeigeStatus('Verbindungsfehler', 'fehler');
    }
  }

  // "stehen"/"sitzen": ein Klick = kurzer Impuls
  function impuls(aktion) {
    zeigeStatus('Sende "' + aktion + '" ...');
    anfrage('/aktion/' + aktion, aktion, '"' + aktion + '" ausgefuehrt');
  }

  // "auf"/"ab": aktiv solange der Button gedrueckt gehalten wird
  function haltenStart(btn, aktion) {
    btn.setPointerCapture && btn.pointerId !== undefined && btn.setPointerCapture(btn.pointerId);
    zeigeStatus('"' + aktion + '" haelt...');
    anfrage('/start/' + aktion, aktion, '"' + aktion + '" aktiv');
  }

  function haltenStop(aktion) {
    zeigeStatus('"' + aktion + '" gestoppt');
    anfrage('/stop/' + aktion, aktion, '"' + aktion + '" gestoppt');
  }

  buttons.forEach(btn => {
    const aktion = btn.dataset.aktion;

    if (btn.dataset.modus === 'halten') {
      btn.addEventListener('pointerdown', (ev) => {
        ev.preventDefault();
        haltenStart(btn, aktion);
      });
      ['pointerup', 'pointercancel', 'pointerleave'].forEach(ev => {
        btn.addEventListener(ev, () => haltenStop(aktion));
      });
    } else {
      btn.addEventListener('click', () => impuls(aktion));
    }
  });

  // --- Automatik: wechselt nach Ablauf der Zeit selbststaendig zwischen Sitzen/Stehen ---
  const automatikToggle = document.getElementById('automatikToggle');
  const sitzenInput = document.getElementById('sitzenMin');
  const stehenInput = document.getElementById('stehenMin');
  const startPhaseSelect = document.getElementById('startPhase');
  const automatikStatusEl = document.getElementById('automatikStatus');
  const automatikPhaseEl = document.getElementById('automatikPhase');
  const automatikTimerEl = document.getElementById('automatikTimer');
  const automatikSubEl = document.getElementById('automatikSub');

  let automatikSyncTimer = null;
  let automatikTickTimer = null;
  let restSekunden = 0;

  function formatZeit(sek) {
    sek = Math.max(0, Math.round(sek));
    const m = Math.floor(sek / 60);
    const s = sek % 60;
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }

  function timerAnzeigeAktualisieren() {
    automatikTimerEl.textContent = formatZeit(restSekunden);
  }

  function automatikAnzeigen(data) {
    automatikToggle.checked = !!data.aktiv;
    sitzenInput.disabled = !!data.aktiv;
    stehenInput.disabled = !!data.aktiv;
    startPhaseSelect.disabled = !!data.aktiv;
    automatikStatusEl.classList.toggle('aktiv', !!data.aktiv);

    if (data.aktiv) {
      if (data.sitzen_min) sitzenInput.value = data.sitzen_min;
      if (data.stehen_min) stehenInput.value = data.stehen_min;

      const aktuellePhase = data.phase === 'sitzen' ? 'Sitzen' : 'Stehen';
      const naechstePhase = data.phase === 'sitzen' ? 'Stehen' : 'Sitzen';
      automatikPhaseEl.textContent = 'Aktiv - ' + aktuellePhase;
      automatikSubEl.textContent = 'bis Wechsel zu "' + naechstePhase + '"';
      restSekunden = data.rest_sek;
      timerAnzeigeAktualisieren();

      if (!automatikTickTimer) {
        automatikTickTimer = setInterval(() => {
          restSekunden = Math.max(0, restSekunden - 1);
          timerAnzeigeAktualisieren();
        }, 1000);
      }
      if (!automatikSyncTimer) automatikSyncTimer = setInterval(automatikStatusAbrufen, 5000);
    } else {
      automatikPhaseEl.textContent = 'Automatik';
      automatikTimerEl.textContent = '--:--';
      automatikSubEl.textContent = 'ausgeschaltet';
      if (automatikTickTimer) { clearInterval(automatikTickTimer); automatikTickTimer = null; }
      if (automatikSyncTimer) { clearInterval(automatikSyncTimer); automatikSyncTimer = null; }
    }
  }

  async function automatikStatusAbrufen() {
    try {
      const res = await fetch('/automatik/status');
      const data = await res.json();
      automatikAnzeigen(data);
    } catch (err) {
      // Status wird beim naechsten Intervall erneut versucht
    }
  }

  automatikToggle.addEventListener('change', async () => {
    try {
      if (automatikToggle.checked) {
        const sitzen = sitzenInput.value || 90;
        const stehen = stehenInput.value || 30;
        const phase = startPhaseSelect.value;
        const res = await fetch('/automatik/start?sitzen=' + sitzen + '&stehen=' + stehen + '&phase=' + phase);
        automatikAnzeigen(await res.json());
      } else {
        const res = await fetch('/automatik/stop');
        automatikAnzeigen(await res.json());
      }
    } catch (err) {
      automatikSubEl.textContent = 'Verbindungsfehler';
    }
  });

  // --- Info-Zeile (IP/Laufzeit) + Online-Anzeige ---
  const infoZeile = document.getElementById('infoZeile');
  const livePunkt = document.getElementById('livePunkt');

  function formatDauer(sek) {
    sek = Math.max(0, Math.floor(sek));
    const h = Math.floor(sek / 3600);
    const m = Math.floor((sek % 3600) / 60);
    if (h > 0) return h + 'h ' + m + 'm';
    if (m > 0) return m + 'm';
    return sek + 's';
  }

  async function infoAbrufen() {
    try {
      const res = await fetch('/info');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      infoZeile.textContent = (data.ip || '?') + ' - Laufzeit ' + formatDauer(data.uptime_sek);
      livePunkt.className = 'live-punkt online';
    } catch (err) {
      infoZeile.textContent = 'Keine Verbindung zum Pico';
      livePunkt.className = 'live-punkt offline';
    }
  }

  // --- Verlauf der letzten Aktionen ---
  const verlaufListe = document.getElementById('verlaufListe');
  const VERLAUF_ICON = { auf: '&#8593;', ab: '&#8595;', stehen: '&#128694;', sitzen: '&#128719;' };
  const VERLAUF_LABEL = { auf: 'Auf', ab: 'Ab', stehen: 'Stehen', sitzen: 'Sitzen' };

  function formatVor(sek) {
    if (sek < 60) return 'vor ' + sek + 's';
    const m = Math.floor(sek / 60);
    if (m < 60) return 'vor ' + m + 'm';
    return 'vor ' + Math.floor(m / 60) + 'h';
  }

  async function verlaufAbrufen() {
    try {
      const res = await fetch('/verlauf');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const eintraege = await res.json();
      if (!eintraege.length) {
        verlaufListe.innerHTML = '<li class="verlauf-leer">Noch keine Aktionen</li>';
        return;
      }
      verlaufListe.innerHTML = eintraege.map(e => {
        const icon = VERLAUF_ICON[e.aktion] || '&#8226;';
        const label = VERLAUF_LABEL[e.aktion] || e.aktion;
        const badge = e.quelle === 'automatik' ? '<span class="verlauf-badge">Automatik</span>' : '';
        return '<li>' +
          '<span class="verlauf-icon">' + icon + '</span>' +
          '<span class="verlauf-text">' + label + badge + '</span>' +
          '<span class="verlauf-zeit">' + formatVor(e.vor_sek) + '</span>' +
        '</li>';
      }).join('');
    } catch (err) {
      // Verlauf wird beim naechsten Poll erneut versucht
    }
  }

  infoAbrufen();
  verlaufAbrufen();
  setInterval(infoAbrufen, 20000);
  setInterval(verlaufAbrufen, 10000);

  automatikStatusAbrufen();
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
    _sende_alles(client, header.encode("utf-8"))
    _sende_alles(client, body)


def _sende_alles(client, daten):
    """client.send() verschickt bei groesseren Antworten oft nur einen Teil
    auf einmal - der Rest muss in einer Schleife nachgesendet werden, sonst
    kommt beim Browser eine abgeschnittene Antwort an."""
    ansicht = memoryview(daten)
    gesendet = 0
    gesamt = len(ansicht)
    while gesendet < gesamt:
        n = client.send(ansicht[gesendet:])
        if not n:
            break
        gesendet += n


def query_parsen(pfad):
    """Zerlegt '/pfad?a=1&b=2' in ein Dict {'a': '1', 'b': '2'}."""
    if "?" not in pfad:
        return {}
    query = pfad.split("?", 1)[1]
    ergebnis = {}
    for teil in query.split("&"):
        if "=" in teil:
            schluessel, wert = teil.split("=", 1)
            ergebnis[schluessel] = wert
    return ergebnis


def anfrage_bearbeiten(client):
    try:
        request = client.recv(1024)
        if not request:
            client.close()
            return
        zeile = request.decode("utf-8").split("\r\n", 1)[0]
        pfad = zeile.split(" ")[1] if " " in zeile else "/"

        if pfad.startswith("/info"):
            http_antwort(client, "200 OK", json.dumps(geraeteinfo()), "application/json")
        elif pfad.startswith("/verlauf"):
            http_antwort(client, "200 OK", json.dumps(verlauf_abfragen()), "application/json")
        elif pfad.startswith("/automatik/start"):
            parameter = query_parsen(pfad)
            try:
                automatik_einschalten(
                    parameter.get("sitzen", "90"),
                    parameter.get("stehen", "30"),
                    parameter.get("phase", "sitzen"),
                )
                http_antwort(client, "200 OK", json.dumps(automatik_status()), "application/json")
            except (ValueError, TypeError):
                http_antwort(client, "400 Bad Request", json.dumps({"aktiv": False}), "application/json")
        elif pfad.startswith("/automatik/stop"):
            automatik_ausschalten()
            http_antwort(client, "200 OK", json.dumps(automatik_status()), "application/json")
        elif pfad.startswith("/automatik/status"):
            http_antwort(client, "200 OK", json.dumps(automatik_status()), "application/json")
        elif pfad.startswith("/aktion/"):
            name = pfad.split("/aktion/", 1)[1].split("?")[0]
            erfolg = aktion_ausfuehren(name)
            http_antwort(
                client,
                "200 OK" if erfolg else "404 Not Found",
                json.dumps({"ok": erfolg, "aktion": name}),
                "application/json",
            )
        elif pfad.startswith("/start/"):
            name = pfad.split("/start/", 1)[1].split("?")[0]
            erfolg = aktion_start(name)
            http_antwort(
                client,
                "200 OK" if erfolg else "404 Not Found",
                json.dumps({"ok": erfolg, "aktion": name}),
                "application/json",
            )
        elif pfad.startswith("/stop/"):
            name = pfad.split("/stop/", 1)[1].split("?")[0]
            erfolg = aktion_stop(name)
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
        gc.collect()


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
    _thread.start_new_thread(automatik_thread, ())
    webserver_starten()


if __name__ == "__main__":
    main()
