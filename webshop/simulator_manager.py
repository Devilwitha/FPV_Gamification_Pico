"""
webshop/simulator_manager.py

Verwaltet isolierte Pico-Firmware-Simulator-Instanzen fuer den "Jetzt
Testen"-Button (siehe app.py's /jetzt-testen und /demo/<token>/...-Routen):
jeder Website-Besucher bekommt beim Klick eine EIGENE, vollstaendig isolierte
Kopie des echten Simulators aus pico_simulator/ (eigener Datenordner, eigener
lokaler Port als Kindprozess) - unabhaengig von allen anderen gleichzeitig
testenden Besuchern. Nutzt dafuer exakt dieselbe, bereits produktiv genutzte
Simulator-Engine wie die lokale Entwicklung (pico_simulator/run_firmware.py,
das die echte main.py aus source/ unter einer MicroPython-Kompatibilitaets-
schicht ausfuehrt), nur automatisiert als Kindprozess pro Besucher statt
manuell ueber Kommandozeile/GUI gestartet.
"""
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIMULATOR_SCRIPT = os.path.join(PROJECT_ROOT, "pico_simulator", "run_firmware.py")
# Eigener Wurzelordner statt pico_simulator/data - jede Instanz bekommt darin
# ihren eigenen Unterordner (Token), damit sich gleichzeitige Besucher NICHT
# denselben main.py-Zustand (Score, Highscore, Lizenz, ...) teilen. Siehe
# .gitignore (/simulator_instances).
INSTANCES_ROOT = os.path.join(PROJECT_ROOT, "simulator_instances")

# Obergrenzen gegen Ressourcen-Erschoepfung, da JEDER anonyme Website-
# Besucher per Klick auf "Jetzt Testen" einen echten Kindprozess samt
# eigenem Datenordner erzeugt - konfigurierbar per Umgebungsvariable, gleiches
# Muster wie DUMMY_MODE/TRUST_PROXY_COUNT in app.py.
MAX_CONCURRENT_INSTANCES = int(os.environ.get("SIMULATOR_MAX_INSTANCES", "20") or 20)
IDLE_TIMEOUT_SECONDS = int(os.environ.get("SIMULATOR_IDLE_TIMEOUT_SECONDS", "1200") or 1200)
PORT_RANGE_START = int(os.environ.get("SIMULATOR_PORT_RANGE_START", "23100") or 23100)
PORT_RANGE_END = int(os.environ.get("SIMULATOR_PORT_RANGE_END", "23999") or 23999)
REAP_INTERVAL_SECONDS = 60


class SimulatorBusyError(Exception):
    """MAX_CONCURRENT_INSTANCES ist erreicht - aktuell keine neue Instanz
    verfuegbar (siehe app.py's /jetzt-testen)."""


class _Instance:
    def __init__(self, token, port, data_dir, process):
        self.token = token
        self.port = port
        self.data_dir = data_dir
        self.process = process
        self.last_seen = time.monotonic()


_instances = {}
_lock = threading.Lock()
_reaper_started = False
# Erste jemals gestartete Instanz erzeugt bei Bedarf einmalig das geteilte
# RSA-Schluesselpaar + die Simulator-Lizenz (siehe run_firmware.py's
# ensure_simulator_license(), Ziel: keys/ + pico_simulator/sim_license.lic).
# Ohne diese Wartesperre koennten zwei Besucher gleichzeitig als allererste
# Aktion der Website ueberhaupt beide gleichzeitig versuchen, dasselbe
# Schluesselpaar neu zu erzeugen, und sich dabei gegenseitig die Dateien
# beschaedigen - alle SPAETEREN Instanzen finden die Dateien bereits fertig
# vor und muessen nicht mehr warten.
_bootstrap_done = threading.Event()


def _find_free_port():
    for _ in range(50):
        port = secrets.randbelow(PORT_RANGE_END - PORT_RANGE_START + 1) + PORT_RANGE_START
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("Kein freier Port fuer eine neue Simulator-Instanz gefunden.")


def _wait_port_open(port, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            try:
                probe.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.3)
    return False


def _spawn_instance():
    token = secrets.token_urlsafe(16)
    port = _find_free_port()
    data_dir = os.path.join(INSTANCES_ROOT, token)
    os.makedirs(INSTANCES_ROOT, exist_ok=True)

    # CREATE_NO_WINDOW verhindert unter Windows ein sichtbares Konsolenfenster
    # je Besucher-Instanz (Server laeuft rein im Hintergrund).
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        [
            sys.executable, SIMULATOR_SCRIPT,
            "--entry", "main",
            "--data-dir", data_dir,
            "--port", str(port),
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )

    if not _bootstrap_done.is_set():
        # Erststart der Website seit diesem Prozessstart: synchron warten,
        # bis der Simulator-Port tatsaechlich antwortet (das passiert erst
        # NACH dem einmaligen Erzeugen von keys/ + sim_license.lic in
        # run_firmware.py's main()), bevor eine zweite Instanz parallel
        # starten darf - siehe Kommentar an _bootstrap_done oben.
        _wait_port_open(port, timeout_seconds=20)
        _bootstrap_done.set()

    return _Instance(token, port, data_dir, process)


def _kill_instance(instance):
    try:
        instance.process.terminate()
        try:
            instance.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            instance.process.kill()
            instance.process.wait(timeout=5)
    except Exception:
        pass
    if os.path.isdir(instance.data_dir):
        shutil.rmtree(instance.data_dir, ignore_errors=True)


def _reap_loop():
    while True:
        time.sleep(REAP_INTERVAL_SECONDS)
        now = time.monotonic()
        with _lock:
            expired_tokens = [
                token for token, inst in _instances.items()
                if (now - inst.last_seen) > IDLE_TIMEOUT_SECONDS or inst.process.poll() is not None
            ]
            expired = [_instances.pop(token) for token in expired_tokens]
        for inst in expired:
            threading.Thread(target=_kill_instance, args=(inst,), daemon=True).start()


def _ensure_reaper():
    global _reaper_started
    if not _reaper_started:
        _reaper_started = True
        threading.Thread(target=_reap_loop, daemon=True).start()


def get_or_create_instance(existing_token):
    """Liefert die zum Browser-Session-Token passende, noch laufende Instanz
    zurueck (Wiederverwendung bei erneutem Klick/Tab-Reload), oder erzeugt
    eine neue. Wirft SimulatorBusyError, wenn MAX_CONCURRENT_INSTANCES
    erreicht ist."""
    _ensure_reaper()
    with _lock:
        if existing_token:
            inst = _instances.get(existing_token)
            if inst is not None and inst.process.poll() is None:
                inst.last_seen = time.monotonic()
                return inst
            if inst is not None:
                _instances.pop(existing_token, None)

        if len(_instances) >= MAX_CONCURRENT_INSTANCES:
            raise SimulatorBusyError()

        inst = _spawn_instance()
        _instances[inst.token] = inst
        return inst


def get_instance(token):
    """Liefert eine laufende Instanz anhand ihres Tokens (fuer den
    Proxy-Handler) oder None, falls unbekannt/bereits beendet."""
    with _lock:
        inst = _instances.get(token)
        if inst is None or inst.process.poll() is not None:
            return None
        inst.last_seen = time.monotonic()
        return inst


def shutdown_all():
    """Beendet alle laufenden Instanzen - wird beim Herunterfahren des
    Webshop-Prozesses aufgerufen (siehe app.py's atexit-Registrierung), damit
    keine verwaisten Simulator-Kindprozesse zurueckbleiben."""
    with _lock:
        tokens = list(_instances.keys())
        remaining = [_instances.pop(token) for token in tokens]
    for inst in remaining:
        _kill_instance(inst)
