"""
gamification_installer.py

"Gamification Installer" - eigenstaendiges Windows-Tool fuer Endnutzer, um
einen per USB angeschlossenen FPV-Gamification-Pico mit einer Firmware
(firmware.nbo) oder einem Sprachpaket (lang.pak) zu bespielen.

Nutzt fuer die USB-Seriell-Kommunikation mit dem Pico bewusst dieselbe
Bibliothek wie tools/build_firmware.py: mpremote (genauer dessen
SerialTransport-Klasse), NUR direkt als Python-Bibliothek importiert statt
per Subprocess-Aufruf ("python -m mpremote ..."). tools/build_firmware.py
ruft mpremote als externes Kommandozeilenprogramm auf, was eine separate
Python-Installation auf dem Ziel-PC voraussetzt - fuer eine per PyInstaller
gebaute .exe (siehe windows/build_exe.py) waere das ein Subprocess-Aufruf
der eigenen .exe, der nicht funktioniert. mpremote selbst ist aber reines
Python, laesst sich daher wie jede andere Abhaengigkeit (z.B. pyserial) mit
in die .exe packen und direkt importieren - dieses Skript verwendet also
exakt dieselbe, bereits produktiv bewaehrte Verbindungslogik (inkl. deren
Soft-Reset-vor-Raw-REPL-Handshake, siehe SerialTransport.enter_raw_repl()),
nur ohne Subprocess-Umweg.

Funktionen:
    - Pico automatisch ueber alle verfuegbaren COM-Ports finden (Raw-REPL-Ping).
    - Eine lokale .nbo/lang.pak Datei per Explorer-Dialog auswaehlen und
      seriell auf den Pico hochladen (das Geraet entpackt das Bundle selbst,
      siehe _build_unpack_script()).
    - Das neueste veroeffentlichte Firmware-Bundle direkt von GitHub Releases
      herunterladen und optional sofort installieren.

WICHTIG (Watchdog/Neustart): boot.py aktiviert auf dem Pico einen Hardware-
Watchdog (8s Timeout, siehe source/boot.py/boot_runtime.py). enter_raw_repl()
unterbricht IMMER die gerade laufende Firmware (WLAN-AP/Webserver/Telemetrie-
Loop stehen still, main.py wird durch den in mpremote eingebauten Soft-Reset
komplett neu gestartet und haengt danach in der Raw-REPL fest) - ohne
Gegenmassnahme bliebe der Pico bis zum naechsten Stromzyklus tatenlos an der
REPL haengen. Dieses Skript fuehrt daher am Ende JEDER Sitzung, die
erfolgreich in die Raw-REPL gelangt ist, explizit machine.reset() aus (siehe
probe_pico_port()/upload_and_apply()), damit main.py garantiert wieder normal
laeuft. Waehrend der eigentlichen Dateiuebertragung feedet der Entpack-Schritt
zusaetzlich ueber das im Geraet laufende boot_runtime-Modul (siehe
_build_unpack_script()), falls das Entpacken mehrerer Dateien laenger dauert.
"""
import json
import os
import struct
import sys
import tempfile
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from urllib import request

try:
    from serial.tools import list_ports
except ImportError:
    list_ports = None

try:
    from mpremote.transport import TransportError, TransportExecError
    from mpremote.transport_serial import SerialTransport
except ImportError:
    SerialTransport = None
    TransportError = TransportExecError = Exception


APP_TITLE = "Gamification Installer"
GITHUB_OWNER = "Devilwitha"
GITHUB_REPO = "FPV_Gamification_Pico"
USER_AGENT = "Gamification-Installer"

BUNDLE_MAGIC = b"FPVBNDL1"
BAUDRATE = 115200
# Timeout fuer den Raw-REPL-Handshake (Sekunden) - identisch zu
# tools/build_firmware.py's _probe_micropython_port(): die Firmware laesst
# waehrend des normalen Betriebs einen asyncio-Webserver + WLAN-AP +
# Telemetrie-Loop laufen, der Soft-Reset-Handshake kann dadurch mehrere
# Sekunden dauern - ein kuerzeres Timeout fuehrt zu False Negatives auf dem
# tatsaechlich angeschlossenen Pico.
CONNECT_TIMEOUT = 8

ASSET_LABELS = {
    "firmware.nbo": "Standard-Firmware (empfohlen)",
    "firmware-light.nbo": "Light-Update (nur geänderte Dateien seit dem letzten Release)",
    "firmware-recovery.nbo": "Recovery-Firmware",
    "emergency.nbo": "Notfall-Bundle (boot + main, minimal)",
    "lang.pak": "Sprachpaket",
}
DEFAULT_ASSET_PRIORITY = ("firmware.nbo", "firmware-light.nbo", "firmware-recovery.nbo", "emergency.nbo", "lang.pak")


# ==================== Log/Downloads-Verzeichnis ====================

def get_app_data_dir():
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    path = os.path.join(base, "GamificationInstaller")
    os.makedirs(path, exist_ok=True)
    return path


def get_downloads_dir():
    path = os.path.join(get_app_data_dir(), "downloads")
    os.makedirs(path, exist_ok=True)
    return path


def get_log_path():
    return os.path.join(get_app_data_dir(), "installer.log")


def write_log_line(message):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(get_log_path(), "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        pass


# ==================== Raw-REPL (MicroPython) ueber USB-Seriell, via mpremote ====================
#
# Verwendet mpremote.transport_serial.SerialTransport direkt (kein eigenes
# Raw-REPL-Protokoll mehr, siehe Modul-Docstring) - exakt dieselbe Klasse,
# die auch hinter tools/build_firmware.py's mpremote-Subprocess-Aufrufen
# steckt. connect_and_reset() ruft dabei bewusst enter_raw_repl(soft_reset=
# True) auf: ein reiner Ctrl-C-Interrupt kann bei der busy asyncio-Firmware
# dieses Projekts unzuverlaessig sein (siehe tools/build_firmware.py's
# Kommentare zu "could not enter raw repl"), waehrend der in mpremote
# eingebaute Soft-Reset das Geraet deterministisch in einen sauberen,
# antwortbereiten Zustand bringt - das ist der Ansatz, den das Build-Skript
# bereits produktiv nutzt.


def connect_and_reset(port, timeout_overall=CONNECT_TIMEOUT):
    """Oeffnet `port`, betritt die Raw-REPL (inkl. Soft-Reset) und liefert
    den verbundenen SerialTransport zurueck."""
    if SerialTransport is None:
        raise RuntimeError("Das Paket 'mpremote' ist nicht installiert.")
    transport = SerialTransport(port, baudrate=BAUDRATE)
    transport.enter_raw_repl(soft_reset=True, timeout_overall=timeout_overall)
    return transport


def _restart_and_close(transport, log=lambda *_a: None):
    """Setzt den Pico zurueck, damit main.py nach einer Raw-REPL-Sitzung
    wieder normal laeuft (siehe Modul-Docstring), und trennt danach die
    Verbindung. Best effort - wird in JEDEM Fall aufgerufen, sobald wir
    erfolgreich in die Raw-REPL gelangt sind, egal ob die eigentliche
    Aktion erfolgreich war."""
    try:
        transport.exec("import machine\nmachine.reset()\n")
    except Exception:
        pass  # Reset trennt die Verbindung erwartungsgemaess sofort.
    try:
        transport.close()
    except Exception:
        pass


def list_candidate_ports():
    """Liefert alle COM-Ports, wahrscheinlichste Pico-Kandidaten zuerst
    (Raspberry-Pi-USB-Vendor-ID 2E8A bzw. 'pico'/'micropython' in der
    Beschreibung)."""
    if list_ports is None:
        return []
    scored = []
    for info in list_ports.comports():
        dev = getattr(info, "device", None)
        if not dev:
            continue
        hwid = (getattr(info, "hwid", "") or "").upper()
        desc = (getattr(info, "description", "") or "").lower()
        score = 0
        if "2E8A" in hwid:
            score += 2
        if "pico" in desc or "micropython" in desc or "raspberry" in desc:
            score += 1
        scored.append((score, dev))
    scored.sort(key=lambda item: -item[0])
    return [dev for _score, dev in scored]


def probe_pico_port(port, connect_timeout=CONNECT_TIMEOUT, log=None):
    """Prueft per Raw-REPL-Ping, ob auf `port` ein MicroPython-Pico
    antwortet. connect_timeout ist bewusst identisch zu den bereits
    produktiv bewaehrten Werten aus tools/build_firmware.py's
    _probe_micropython_port() gewaehlt (nicht kuerzer!): die Firmware laesst
    waehrend des normalen Betriebs einen asyncio-Webserver + WLAN-AP +
    Telemetrie-Loop laufen, der Soft-Reset-Handshake kann dadurch mehrere
    Sekunden dauern - ein kuerzeres Timeout fuehrt zu FALSE NEGATIVES auf dem
    tatsaechlich angeschlossenen Pico, nicht nur zu einer schnelleren
    Ablehnung falscher Ports. Loggt (falls `log` uebergeben) den
    tatsaechlichen Fehler, statt ihn stillschweigend zu verschlucken."""
    transport = None
    entered_raw_repl = False
    found = False
    try:
        transport = connect_and_reset(port, timeout_overall=connect_timeout)
        entered_raw_repl = True
        out = transport.exec("print('PICO_OK')")
        found = b"PICO_OK" in out
    except Exception as e:
        if log:
            log(f"  {port}: kein Pico ({e})")
    finally:
        if transport is not None:
            if entered_raw_repl:
                _restart_and_close(transport, log=log)
            else:
                try:
                    transport.close()
                except Exception:
                    pass
    return found


def find_pico_port(log=lambda *_a: None):
    if SerialTransport is None:
        raise RuntimeError("Das Paket 'mpremote' ist nicht installiert.")
    ports = list_candidate_ports()
    if not ports:
        log("Keine seriellen Ports gefunden.")
        return None
    for port in ports:
        log(f"Pruefe {port} ...")
        if probe_pico_port(port, log=log):
            log(f"Pico gefunden auf {port} (startet kurz neu und ist danach wieder normal erreichbar).")
            return port
    log("Kein Pico gefunden.")
    return None


# ==================== Bundle-Format (FPVBNDL1) ====================

def read_bundle_entries(path):
    """Liest Dateinamen aus einem FPVBNDL1-Bundle (firmware.nbo/lang.pak),
    siehe tools/build_firmware.py's build_bundle() fuer das Format."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != BUNDLE_MAGIC:
        raise ValueError("Keine gueltige Bundle-Datei (Magic-Header 'FPVBNDL1' fehlt).")
    offset = 8
    (count,) = struct.unpack_from(">I", data, offset)
    offset += 4
    names = []
    for _ in range(count):
        (name_len,) = struct.unpack_from(">I", data, offset)
        offset += 4
        name = data[offset:offset + name_len].decode("utf-8")
        offset += name_len
        (content_len,) = struct.unpack_from(">I", data, offset)
        offset += 4
        offset += content_len
        names.append(name)
    return names


def remote_target_for(local_path):
    name = os.path.basename(local_path).lower()
    if name == "lang.pak":
        return "lang.pak"
    if name.endswith(".nbo"):
        return "firmware.nbo"
    raise ValueError(
        "Nur .nbo Firmware-Bundles oder eine Datei namens 'lang.pak' werden unterstuetzt."
    )


def _build_unpack_script(allowed_names, remote_bundle_filename):
    """Python-Code, der per Raw-REPL AUF DEM PICO laeuft und das Bundle
    Datei fuer Datei entpackt - inhaltlich identisch zur bereits produktiv
    genutzten Logik in tools/build_firmware.py's
    _build_device_unpack_script(), hier eigenstaendig nachgebildet (kein
    Import aus tools/, siehe Modul-Docstring). Feedet den Watchdog nach
    jeder Datei ueber boot_runtime, damit der 8s-Timeout waehrend des
    Entpackens mehrerer Dateien nicht zuschlaegt."""
    allowed_literal = repr(tuple(allowed_names))
    bundle_literal = repr(remote_bundle_filename)
    return f"""
import os
import struct
try:
    import boot_runtime as _br
except Exception:
    _br = None

def _feed():
    if _br is not None:
        try:
            _br.feed_wdt()
        except Exception:
            pass

MAGIC = b"FPVBNDL1"
ALLOWED = {allowed_literal}
BUNDLE_FILE = {bundle_literal}

def read_exact(f, n):
    data = bytearray()
    while len(data) < n:
        chunk = f.read(n - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)

extracted = []
with open(BUNDLE_FILE, "rb") as f:
    if read_exact(f, len(MAGIC)) != MAGIC:
        raise Exception("Ungueltiges Bundle (Magic)")
    (count,) = struct.unpack(">I", read_exact(f, 4))
    for _ in range(count):
        _feed()
        (name_len,) = struct.unpack(">I", read_exact(f, 4))
        name = read_exact(f, name_len).decode("utf-8")
        (content_len,) = struct.unpack(">I", read_exact(f, 4))
        if not name or "/" in name or "\\\\" in name or ".." in name or name.startswith("."):
            raise Exception("Datei im Bundle nicht erlaubt: " + name)
        try:
            os.remove(name + ".bndl_tmp")
        except Exception:
            pass
        try:
            os.remove(name)
        except Exception:
            pass
        if name.endswith(".mpy"):
            try:
                os.remove(name[:-4] + ".py")
            except Exception:
                pass
        elif name.endswith(".py"):
            try:
                os.remove(name[:-3] + ".mpy")
            except Exception:
                pass
        remaining = content_len
        try:
            with open(name, "wb") as out:
                while remaining > 0:
                    chunk = f.read(min(512, remaining))
                    if not chunk:
                        raise Exception("Bundle beschaedigt (content)")
                    out.write(chunk)
                    remaining -= len(chunk)
        except OSError as e:
            raise Exception("Zu wenig Speicher beim Schreiben von " + name + ": " + str(e))
        extracted.append(name)

try:
    os.remove(BUNDLE_FILE)
except Exception:
    pass

for fixed_name in ("update.pbp", "ota_staging.tmp"):
    try:
        os.remove(fixed_name)
    except Exception:
        pass

for name in os.listdir():
    remove = False
    if name == "main_backup.py" and "main.py" in ALLOWED:
        remove = True
    elif name.endswith(".bak"):
        base = name[:-4]
        if base in ALLOWED:
            remove = True
    elif name.endswith(".bndl_tmp"):
        base = name[:-9]
        if base in ALLOWED:
            remove = True
    if remove:
        try:
            os.remove(name)
        except Exception:
            pass

needs_restart = ("main.py" in extracted) or ("main.mpy" in extracted)
print("APPLY_OK:" + ",".join(extracted))
print("NEEDS_RESTART:" + ("1" if needs_restart else "0"))
"""


def upload_and_apply(port, local_path, progress_cb=None, log=lambda *_a: None):
    """Ueberspielt local_path (.nbo/lang.pak) per USB-Seriell auf `port`
    (mit derselben mpremote-SerialTransport-Verbindung/Uebertragungsart wie
    tools/build_firmware.py's "cp"-Upload, siehe Transport.fs_writefile())
    und laesst den Pico das Bundle selbst entpacken.

    WICHTIG: Das Betreten der Raw-REPL (connect_and_reset(), inkl. Soft-
    Reset) unterbricht IMMER die gerade laufende Firmware (main.py/
    recovery.py samt WLAN-AP/Webserver/Telemetrie-Loop) - unabhaengig davon,
    ob main.py Teil des aktuellen Updates ist (z.B. bei einem reinen
    lang.pak-Update nicht) und unabhaengig davon, ob die Uebertragung am Ende
    erfolgreich war. Der finally-Block unten resettet daher IN JEDEM Fall,
    sobald wir die Raw-REPL erfolgreich betreten haben - siehe denselben
    Ansatz in probe_pico_port()."""
    target = remote_target_for(local_path)
    names = read_bundle_entries(local_path)
    if not names:
        raise ValueError("Bundle enthaelt keine Dateien.")

    with open(local_path, "rb") as f:
        raw = f.read()

    transport = None
    entered_raw_repl = False
    try:
        log(f"Verbinde mit {port} ...")
        try:
            transport = connect_and_reset(port)
            entered_raw_repl = True
        except Exception:
            # Siehe Modul-Docstring: der Watchdog-/Soft-Reset-Handshake kann
            # bei der busy asyncio-Firmware gelegentlich beim ersten Versuch
            # scheitern - ein zweiter Versuch reicht danach meist.
            log("Erster Verbindungsversuch fehlgeschlagen, versuche erneut ...")
            time.sleep(1.0)
            transport = connect_and_reset(port)
            entered_raw_repl = True

        log(f"Bereite Uebertragung von {target} vor ...")
        prep_code = (
            "import os\n"
            "try:\n"
            "    import boot_runtime as _br\n"
            "    _br.feed_wdt()\n"
            "except Exception:\n"
            "    pass\n"
            f"for _n in ('update.pbp', 'ota_staging.tmp', {target!r}, {target!r} + '.tmp'):\n"
            "    try:\n"
            "        os.remove(_n)\n"
            "    except Exception:\n"
            "        pass\n"
        )
        transport.exec(prep_code)

        log(f"Uebertrage {target} ({len(raw)} Bytes) ...")

        def _progress(written, total):
            if progress_cb:
                progress_cb(written, total)

        transport.fs_writefile(target, raw, progress_callback=_progress)
        log("Uebertragung abgeschlossen, entpacke auf dem Pico ...")

        unpack_script = _build_unpack_script(names, target)
        out = transport.exec(unpack_script).decode("utf-8", "replace")
        if "APPLY_OK:" not in out:
            raise RuntimeError(f"Unerwartete Antwort beim Entpacken: {out!r}")
        log("Bundle erfolgreich auf dem Pico entpackt.")
        return True
    finally:
        if entered_raw_repl:
            log("Starte Pico neu, damit die Firmware wieder normal laeuft ...")
            _restart_and_close(transport, log=log)
        elif transport is not None:
            try:
                transport.close()
            except Exception:
                pass


# ==================== GitHub Releases ====================

def fetch_latest_release():
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    req = request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    with request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    assets = []
    for asset in data.get("assets", []):
        name = asset.get("name") or ""
        low = name.lower()
        if low.endswith(".nbo") or low == "lang.pak":
            assets.append({"name": name, "url": asset.get("browser_download_url"), "size": asset.get("size", 0)})

    def sort_key(asset):
        try:
            return DEFAULT_ASSET_PRIORITY.index(asset["name"])
        except ValueError:
            return len(DEFAULT_ASSET_PRIORITY)

    assets.sort(key=sort_key)
    return data.get("tag_name", "?"), assets


def download_asset(url, dest_path, progress_cb=None, timeout=30):
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp_path = dest_path + ".part"
    with request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        received = 0
        with open(tmp_path, "wb") as out:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                received += len(chunk)
                if progress_cb and total:
                    progress_cb(received, total)
    os.replace(tmp_path, dest_path)
    return dest_path


# ==================== GUI ====================

class AssetChoiceDialog(tk.Toplevel):
    """Modaler Dialog zur Auswahl eines Release-Assets (firmware.nbo,
    firmware-light.nbo, ...)."""

    def __init__(self, parent, tag_name, assets):
        super().__init__(parent)
        self.title(f"Release {tag_name} - Datei waehlen")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result = None

        tk.Label(self, text=f"Verfuegbare Dateien in Release {tag_name}:", padx=12, pady=8).pack(anchor="w")

        self.selected = tk.StringVar(value=assets[0]["name"] if assets else "")
        for asset in assets:
            label = ASSET_LABELS.get(asset["name"], asset["name"])
            size_kb = (asset.get("size") or 0) / 1024
            text = f"{asset['name']}  -  {label}  ({size_kb:.0f} KB)"
            tk.Radiobutton(self, text=text, variable=self.selected, value=asset["name"], padx=24, anchor="w").pack(fill="x")

        btn_row = tk.Frame(self, pady=10)
        btn_row.pack(fill="x")
        tk.Button(btn_row, text="Abbrechen", command=self._cancel).pack(side="right", padx=12)
        tk.Button(btn_row, text="Herunterladen", command=self._ok, default="active").pack(side="right")

        self.assets_by_name = {a["name"]: a for a in assets}
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_window(self)

    def _ok(self):
        self.result = self.assets_by_name.get(self.selected.get())
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class InstallerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("620x560")
        self.minsize(560, 480)

        self.pico_port = None
        self.selected_file = None

        self._build_widgets()

    # ---------- UI Aufbau ----------

    def _build_widgets(self):
        pad = {"padx": 12, "pady": 6}

        step1 = tk.LabelFrame(self, text="1. Pico verbinden", padx=10, pady=8)
        step1.pack(fill="x", **pad)
        row1 = tk.Frame(step1)
        row1.pack(fill="x")
        self.pico_status_var = tk.StringVar(value="Noch nicht gesucht - auf 'Pico suchen' klicken.")
        tk.Label(row1, textvariable=self.pico_status_var, anchor="w").pack(side="left", fill="x", expand=True)
        self.find_button = tk.Button(row1, text="Pico suchen", command=self._on_find_pico)
        self.find_button.pack(side="right")

        step2 = tk.LabelFrame(self, text="2. Firmware-Datei waehlen", padx=10, pady=8)
        step2.pack(fill="x", **pad)
        row2 = tk.Frame(step2)
        row2.pack(fill="x")
        self.browse_button = tk.Button(row2, text="Datei auswaehlen...", command=self._on_browse_file)
        self.browse_button.pack(side="left")
        self.github_button = tk.Button(row2, text="Von GitHub herunterladen...", command=self._on_github_download)
        self.github_button.pack(side="left", padx=8)
        self.file_status_var = tk.StringVar(value="Keine Datei ausgewaehlt.")
        tk.Label(step2, textvariable=self.file_status_var, anchor="w", wraplength=560, justify="left").pack(fill="x", pady=(6, 0))

        step3 = tk.LabelFrame(self, text="3. Installieren", padx=10, pady=8)
        step3.pack(fill="x", **pad)
        self.install_button = tk.Button(step3, text="Jetzt installieren", command=self._on_install, state="disabled")
        self.install_button.pack(anchor="w")
        self.progress = ttk.Progressbar(step3, orient="horizontal", mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 4))
        self.status_var = tk.StringVar(value="Bereit.")
        tk.Label(step3, textvariable=self.status_var, anchor="w").pack(fill="x")

        log_frame = tk.LabelFrame(self, text="Protokoll", padx=6, pady=6)
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

    # ---------- Hilfsfunktionen ----------

    def log(self, message):
        write_log_line(message)

        def append():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        self.after(0, append)

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.find_button.config(state=state)
        self.browse_button.config(state=state)
        self.github_button.config(state=state)
        self.install_button.config(state="disabled" if busy else ("normal" if (self.pico_port and self.selected_file) else "disabled"))

    def _update_install_button(self):
        self.install_button.config(state="normal" if (self.pico_port and self.selected_file) else "disabled")

    # ---------- Pico suchen ----------

    def _on_find_pico(self):
        self._set_busy(True)
        self.pico_status_var.set("Suche nach Pico ...")
        threading.Thread(target=self._find_pico_worker, daemon=True).start()

    def _find_pico_worker(self):
        try:
            port = find_pico_port(log=self.log)
        except Exception as e:
            port = None
            self.log(f"Fehler bei der Pico-Suche: {e}")

        def finish():
            self.pico_port = port
            if port:
                self.pico_status_var.set(f"Verbunden: {port}")
            else:
                self.pico_status_var.set("Kein Pico gefunden. USB-Kabel pruefen und erneut suchen.")
            self._set_busy(False)
            self._update_install_button()

        self.after(0, finish)

    # ---------- Datei auswaehlen ----------

    def _on_browse_file(self):
        path = filedialog.askopenfilename(
            title="Firmware-Datei auswaehlen",
            filetypes=[("Firmware-Bundle / Sprachpaket", "*.nbo *.pak"), ("Alle Dateien", "*.*")],
        )
        if not path:
            return
        self._set_selected_file(path)

    def _set_selected_file(self, path):
        try:
            names = read_bundle_entries(path)
            remote_target_for(path)
        except Exception as e:
            messagebox.showerror("Ungueltige Datei", str(e))
            return
        self.selected_file = path
        size_kb = os.path.getsize(path) / 1024
        self.file_status_var.set(f"{os.path.basename(path)}  ({size_kb:.0f} KB, {len(names)} Datei(en) im Bundle)")
        self.log(f"Datei ausgewaehlt: {path}")
        self._update_install_button()

    # ---------- GitHub-Download ----------

    def _on_github_download(self):
        self._set_busy(True)
        self.status_var.set("Suche neueste Version auf GitHub ...")
        threading.Thread(target=self._github_fetch_worker, daemon=True).start()

    def _github_fetch_worker(self):
        try:
            tag_name, assets = fetch_latest_release()
        except Exception as e:
            self.after(0, lambda: self._github_fetch_failed(e))
            return
        self.after(0, lambda: self._github_fetch_done(tag_name, assets))

    def _github_fetch_failed(self, error):
        self._set_busy(False)
        self.status_var.set("Bereit.")
        messagebox.showerror("GitHub-Fehler", f"Konnte Releases nicht abrufen:\n{error}")

    def _github_fetch_done(self, tag_name, assets):
        self._set_busy(False)
        self.status_var.set("Bereit.")
        if not assets:
            messagebox.showinfo("Keine Dateien", f"Release {tag_name} enthaelt keine .nbo/.pak Dateien.")
            return
        dialog = AssetChoiceDialog(self, tag_name, assets)
        if not dialog.result:
            return
        self._start_download(dialog.result)

    def _start_download(self, asset):
        self._set_busy(True)
        self.status_var.set(f"Lade {asset['name']} herunter ...")
        self.progress["value"] = 0
        threading.Thread(target=self._download_worker, args=(asset,), daemon=True).start()

    def _download_worker(self, asset):
        dest_path = os.path.join(get_downloads_dir(), asset["name"])

        def progress_cb(received, total):
            pct = int(received * 100 / total) if total else 0
            self.after(0, lambda: self.progress.configure(value=pct))

        try:
            download_asset(asset["url"], dest_path, progress_cb=progress_cb)
            self.log(f"Heruntergeladen: {dest_path}")
        except Exception as e:
            self.after(0, lambda: self._download_failed(e))
            return
        self.after(0, lambda: self._download_done(dest_path))

    def _download_failed(self, error):
        self._set_busy(False)
        self.status_var.set("Bereit.")
        messagebox.showerror("Download-Fehler", f"Download fehlgeschlagen:\n{error}")

    def _download_done(self, dest_path):
        self._set_busy(False)
        self.status_var.set("Bereit.")
        self.progress["value"] = 0
        self._set_selected_file(dest_path)
        if self.pico_port and messagebox.askyesno(
            "Herunterladen abgeschlossen", "Jetzt auf den verbundenen Pico installieren?"
        ):
            self._on_install()

    # ---------- Installieren ----------

    def _on_install(self):
        if not self.pico_port:
            messagebox.showerror("Kein Pico", "Es wurde kein Pico gefunden. Bitte zuerst 'Pico suchen' verwenden.")
            return
        if not self.selected_file:
            messagebox.showerror("Keine Datei", "Bitte zuerst eine Firmware-Datei auswaehlen.")
            return
        if not messagebox.askyesno(
            "Installation bestaetigen",
            f"{os.path.basename(self.selected_file)} jetzt auf {self.pico_port} installieren?\n\n"
            "Das Geraet startet nach erfolgreicher Installation automatisch neu.",
        ):
            return

        self._set_busy(True)
        self.progress["value"] = 0
        self.status_var.set("Installation laeuft ...")
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self):
        port = self.pico_port
        path = self.selected_file

        def progress_cb(sent, total):
            pct = int(sent * 100 / total) if total else 0
            self.after(0, lambda: self.progress.configure(value=pct))

        try:
            upload_and_apply(port, path, progress_cb=progress_cb, log=self.log)
        except Exception as e:
            self.log(f"Fehler bei der Installation: {e}")
            self.after(0, lambda: self._install_failed(e))
            return
        self.after(0, self._install_done)

    def _install_failed(self, error):
        self._set_busy(False)
        self.status_var.set("Fehler bei der Installation.")
        messagebox.showerror("Installation fehlgeschlagen", str(error))

    def _install_done(self):
        self._set_busy(False)
        self.progress["value"] = 100
        self.status_var.set("Installation erfolgreich abgeschlossen.")
        messagebox.showinfo("Fertig", "Die Installation war erfolgreich. Der Pico startet neu.")
        # Nach einem Neustart des Pico ist der Raw-REPL-Handshake erst wieder
        # zuverlaessig moeglich, wenn main.py/recovery.py vollstaendig
        # hochgefahren ist - der Nutzer muss daher bei Bedarf manuell erneut
        # suchen, statt dass wir hier sofort automatisch weitersuchen.
        self.pico_status_var.set(f"{self.pico_port} (Neustart laeuft - bei Bedarf 'Pico suchen' erneut klicken)")


def main():
    if SerialTransport is None or list_ports is None:
        # tkinter allein reicht, um wenigstens eine verstaendliche Fehlermeldung
        # anzuzeigen, statt dass die .exe kommentarlos abstuerzt.
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            APP_TITLE,
            "Die Pakete 'pyserial' und/oder 'mpremote' fehlen. Bitte "
            "windows/requirements.txt installieren bzw. die .exe ueber "
            "windows/build_exe.py neu bauen.",
        )
        sys.exit(1)

    app = InstallerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
