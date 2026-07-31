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
import ctypes
import glob
import json
import os
import string
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


# ==================== Abbrechen laufender Aktionen ====================
#
# Alle laenger laufenden Funktionen (Port-Suche, Bootloader-Warteschleifen,
# Datei-/UF2-Uebertragungen, Downloads) nehmen optional ein
# threading.Event `cancel_event` entgegen und pruefen es an sicheren
# Zwischenschritten (z.B. zwischen zwei Kandidaten-Ports, zwischen zwei
# Dateien) via _check_cancelled(). Das GUI setzt dieses Event ueber den
# globalen "Abbrechen"-Button (siehe InstallerApp._on_cancel()), damit der
# Nutzer nicht auf eine lang laufende Suche warten muss, sondern sofort
# etwas anderes tun kann (z.B. eine gerade laufende Pico-Suche abbrechen,
# um stattdessen den Bootloader-Flash-Schritt zu starten). Laufende Datei-/
# UF2-Uebertragungen werden bewusst NICHT mitten im Schreibvorgang
# abgebrochen (siehe copy_uf2_to_drive()), nur zwischen abgeschlossenen
# Schritten - ein halb geschriebenes UF2/Bundle waere unsicher.


class OperationCancelled(Exception):
    """Wird ausgeloest, wenn der Nutzer eine laufende Aktion ueber den
    Abbrechen-Button im GUI abgebrochen hat."""


def _check_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise OperationCancelled()


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


def _connect_raw_repl_with_retry(port, timeout_overall=CONNECT_TIMEOUT, log=lambda *_a: None):
    """connect_and_reset() mit einem Wiederholungsversuch. Unmittelbar nach
    einem vorangegangenen machine.reset() (z.B. am Ende einer vorherigen
    Raw-REPL-Sitzung auf demselben Port) braucht Windows kurz, um den
    COM-Port nach der USB-Neuenumeration wieder freizugeben - ein sofortiger
    zweiter Verbindungsversuch auf genau diesem Port kann dann kurzzeitig
    mit einem Zugriffsfehler scheitern, obwohl der Port eine Sekunde spaeter
    wieder verfuegbar ist. Bereits produktiv bewaehrtes Muster, hier fuer
    alle Aufrufer zentralisiert."""
    try:
        return connect_and_reset(port, timeout_overall=timeout_overall)
    except Exception:
        log(f"Erster Verbindungsversuch zu {port} fehlgeschlagen, versuche erneut ...")
        time.sleep(1.0)
        return connect_and_reset(port, timeout_overall=timeout_overall)


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
    tatsaechlichen Fehler, statt ihn stillschweigend zu verschlucken.

    BEWUSST OHNE _connect_raw_repl_with_retry(): dies wird beim Scannen
    ALLER Kandidaten-Ports aufgerufen, die meisten davon sind gar kein Pico
    und schlagen legitim fehl - ein Retry mit 1s Wartezeit wuerde die
    Scan-Zeit fuer jeden falschen Port verdoppeln, ohne einen Nutzen zu
    bringen (der Retry lohnt sich nur, wenn ein bestimmter Port bereits als
    Pico bekannt ist, siehe read_pico_uid()/upload_and_apply())."""
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


def find_pico_port(log=lambda *_a: None, cancel_event=None):
    """cancel_event wird zwischen den Kandidaten-Ports geprueft (nicht
    waehrend eines einzelnen Verbindungsversuchs, siehe Modul-Docstring zu
    _check_cancelled()) - bei vielen COM-Ports auf dem System kann ein
    voller Scan sonst mehrere x CONNECT_TIMEOUT Sekunden dauern, waehrend
    derer die GUI komplett blockiert waere."""
    if SerialTransport is None:
        raise RuntimeError("Das Paket 'mpremote' ist nicht installiert.")
    ports = list_candidate_ports()
    if not ports:
        log("Keine seriellen Ports gefunden.")
        return None
    for port in ports:
        _check_cancelled(cancel_event)
        log(f"Pruefe {port} ...")
        if probe_pico_port(port, log=log):
            log(f"Pico gefunden auf {port} (startet kurz neu und ist danach wieder normal erreichbar).")
            return port
    log("Kein Pico gefunden.")
    return None


def read_pico_uid(port, log=lambda *_a: None):
    """Liest die eindeutige, fest im Chip einprogrammierte Hardware-ID
    (machine.unique_id()) eines per USB-Seriell verbundenen, MicroPython
    ausfuehrenden Pico aus und liefert sie als Hex-String zurueck. Nutzt
    dieselbe Raw-REPL-Verbindung/Reset-Logik wie probe_pico_port()."""
    transport = None
    entered_raw_repl = False
    try:
        transport = _connect_raw_repl_with_retry(port, log=log)
        entered_raw_repl = True
        out = transport.exec(
            "import machine, ubinascii\n"
            "print(ubinascii.hexlify(machine.unique_id()).decode())\n"
        )
        return out.decode("utf-8", "replace").strip()
    finally:
        if entered_raw_repl:
            _restart_and_close(transport, log=log)
        elif transport is not None:
            try:
                transport.close()
            except Exception:
                pass


def find_pico_port_and_uid(log=lambda *_a: None, cancel_event=None):
    """Wie find_pico_port(), liest aber die UID in DERSELBEN Raw-REPL-
    Sitzung mit aus. Vermeidet dadurch, dass die GUI nach einer Pico-Suche
    (die den Pico am Ende bereits per machine.reset() neu startet) sofort
    eine zweite, separate Verbindung fuer read_pico_uid() aufbaut - genau
    das lief in der Praxis gegen die USB-Neuenumeration des COM-Ports und
    schlug mit einem Zugriffsfehler fehl. Liefert (port, uid) oder
    (None, None). BEWUSST OHNE _connect_raw_repl_with_retry() beim Scannen
    (siehe probe_pico_port()); cancel_event wird wie bei find_pico_port()
    zwischen den Kandidaten-Ports geprueft."""
    if SerialTransport is None:
        raise RuntimeError("Das Paket 'mpremote' ist nicht installiert.")
    ports = list_candidate_ports()
    if not ports:
        log("Keine seriellen Ports gefunden.")
        return None, None
    for port in ports:
        _check_cancelled(cancel_event)
        log(f"Pruefe {port} ...")
        transport = None
        entered_raw_repl = False
        try:
            transport = connect_and_reset(port)
            entered_raw_repl = True
            out = transport.exec(
                "import machine, ubinascii\n"
                "print('PICO_OK')\n"
                "print(ubinascii.hexlify(machine.unique_id()).decode())\n"
            ).decode("utf-8", "replace")
            lines = [line.strip() for line in out.splitlines() if line.strip()]
            if len(lines) >= 2 and lines[0] == "PICO_OK":
                log(f"Pico gefunden auf {port} (startet kurz neu und ist danach wieder normal erreichbar).")
                return port, lines[1]
        except Exception as e:
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
    log("Kein Pico gefunden.")
    return None, None


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


def upload_and_apply(port, local_path, progress_cb=None, log=lambda *_a: None, cancel_event=None):
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
    Ansatz in probe_pico_port(). cancel_event wird nur VOR dem
    Verbindungsaufbau geprueft (nicht mehr waehrend der eigentlichen
    Uebertragung) - ein abgebrochener Bundle-Transfer waere zwar unkritisch
    (siehe prep_code oben, das stehengebliebene Bundle-Dateien beim naechsten
    Versuch aufraeumt), ein sauberer Abbruchpunkt VOR dem Reset der laufenden
    Firmware ist aber die bessere Nutzererfahrung."""
    target = remote_target_for(local_path)
    names = read_bundle_entries(local_path)
    if not names:
        raise ValueError("Bundle enthaelt keine Dateien.")

    with open(local_path, "rb") as f:
        raw = f.read()

    transport = None
    entered_raw_repl = False
    try:
        _check_cancelled(cancel_event)
        log(f"Verbinde mit {port} ...")
        transport = _connect_raw_repl_with_retry(port, log=log)
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


def download_asset(url, dest_path, progress_cb=None, timeout=30, cancel_event=None):
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp_path = dest_path + ".part"
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            received = 0
            with open(tmp_path, "wb") as out:
                while True:
                    _check_cancelled(cancel_event)
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    received += len(chunk)
                    if progress_cb and total:
                        progress_cb(received, total)
    except OperationCancelled:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    os.replace(tmp_path, dest_path)
    return dest_path


# ==================== Bootloader-Modus (BOOTSEL/UF2-Laufwerk) ====================
#
# Ein Pico, der beim Einstecken mit gedrueckter BOOTSEL-Taste startet (oder
# noch nie eine Firmware hatte), meldet sich nicht ueber USB-Seriell, sondern
# als gewoehnliches USB-Massenspeicher-Laufwerk mit einer INFO_UF2.TXT-Datei
# im Root - darauf kopierte .uf2-Dateien werden vom eingebauten UF2-
# Bootloader direkt in den Flash geschrieben (das ist derselbe Mechanismus
# wie das manuelle Ziehen einer .uf2-Datei per Explorer auf das Laufwerk).
# Dieser Abschnitt bildet genau diesen Ablauf programmatisch nach: Laufwerk
# per INFO_UF2.TXT erkennen (inkl. Unterscheidung Pico 1/RP2040 vs.
# Pico 2/RP2350), zuerst die passende *_nuke.uf2 kopieren (loescht den
# kompletten Flash-Speicher und der Pico faellt danach automatisch wieder in
# den Bootloader-Modus zurueck), dann warten bis das Laufwerk erneut
# erscheint, und zuletzt die passende MicroPython-*.uf2 installieren.

DRIVE_REMOVABLE = 2  # Windows GetDriveType()-Rueckgabewert
BOOTSEL_POLL_INTERVAL = 1.0
BOOTSEL_WAIT_TIMEOUT = 30  # Sekunden

PICO_VARIANT_LABELS = {
    "pico1": "Pico (RP2040)",
    "pico2": "Pico 2 (RP2350)",
}


def get_picofw_dir():
    """Pfad zum picofw/-Ordner mit den .uf2-Dateien - bei einer per
    PyInstaller (--onefile) gebauten .exe liegen mitgepackte Daten zur
    Laufzeit unter sys._MEIPASS (siehe build_exe.py's --add-data), beim
    direkten Ausfuehren dieses Skripts liegt picofw/ zwei Ebenen ueber
    windows/source/ im Projekt-Root."""
    bundled_base = getattr(sys, "_MEIPASS", None)
    if bundled_base:
        return os.path.join(bundled_base, "picofw")
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "picofw"))


def get_nuke_uf2_path(variant):
    name = "universal_flash_nuke.uf2" if variant == "pico2" else "flash_nuke.uf2"
    path = os.path.join(get_picofw_dir(), name)
    return path if os.path.isfile(path) else None


def get_firmware_uf2_path(variant):
    """Waehlt die neueste passende MicroPython-.uf2 im picofw/-Ordner aus
    (Dateiname enthaelt das Datum, z.B. RPI_PICO_W-20260406-v1.28.0.uf2) -
    per Praefix statt fest verdrahtetem Dateinamen, damit neuere Firmware-
    Versionen im Ordner automatisch verwendet werden."""
    prefix = "RPI_PICO2_W-" if variant == "pico2" else "RPI_PICO_W-"
    candidates = sorted(glob.glob(os.path.join(get_picofw_dir(), prefix + "*.uf2")))
    return candidates[-1] if candidates else None


def _iter_removable_drive_roots():
    if os.name != "nt":
        return
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not (bitmask & (1 << i)):
            continue
        root = f"{letter}:\\"
        try:
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
        except Exception:
            continue
        if drive_type == DRIVE_REMOVABLE:
            yield root


def _detect_bootsel_variant(drive_root):
    """Liest INFO_UF2.TXT (vom UF2-Bootloader selbst erzeugt, siehe
    https://github.com/microsoft/uf2) und unterscheidet anhand des Inhalts
    zwischen Pico 1 (RP2040, Board-ID 'RPI-RP2') und Pico 2 (RP2350,
    Board-ID 'RP2350'). Gibt None zurueck, falls das Laufwerk kein
    UF2-Bootloader-Laufwerk ist."""
    info_path = os.path.join(drive_root, "INFO_UF2.TXT")
    if not os.path.isfile(info_path):
        return None
    try:
        with open(info_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().upper()
    except Exception:
        return None
    if "RP2350" in content:
        return "pico2"
    if "RP2" in content:
        return "pico1"
    return None


def find_bootsel_drive(log=lambda *_a: None):
    """Sucht unter allen Wechseldatentraegern nach einem Pico im
    Bootloader-Modus und liefert (Laufwerk-Root, Variante) oder None."""
    for root in _iter_removable_drive_roots():
        variant = _detect_bootsel_variant(root)
        if variant:
            log(f"Bootloader-Laufwerk gefunden: {root} ({PICO_VARIANT_LABELS[variant]})")
            return root, variant
    return None


def wait_for_bootsel_drive(timeout=BOOTSEL_WAIT_TIMEOUT, log=lambda *_a: None, cancel_event=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _check_cancelled(cancel_event)
        found = find_bootsel_drive(log=log)
        if found:
            return found
        time.sleep(BOOTSEL_POLL_INTERVAL)
    return None


def wait_for_drive_gone(drive_root, timeout=BOOTSEL_WAIT_TIMEOUT, cancel_event=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _check_cancelled(cancel_event)
        if not os.path.isdir(drive_root):
            return True
        time.sleep(0.5)
    return not os.path.isdir(drive_root)


def copy_uf2_to_drive(uf2_path, drive_root, progress_cb=None, log=lambda *_a: None):
    """Kopiert eine .uf2-Datei auf das Bootloader-Laufwerk - entspricht dem
    manuellen Ziehen der Datei per Explorer auf das RPI-RP2/RP2350-Laufwerk.
    Sobald die Datei vollstaendig geschrieben ist, resettet der UF2-
    Bootloader das Board von sich aus und das Laufwerk verschwindet; dabei
    kann Windows den letzten Flush/Close als OSError melden, obwohl der
    Flash-Vorgang bereits vollstaendig war - daher wird ein OSError nur dann
    als echter Fehler behandelt, wenn tatsaechlich noch nicht alle Bytes
    geschrieben wurden."""
    dest = os.path.join(drive_root, os.path.basename(uf2_path))
    size = os.path.getsize(uf2_path)
    copied = 0
    log(f"Kopiere {os.path.basename(uf2_path)} ({size} Bytes) nach {drive_root} ...")
    try:
        with open(uf2_path, "rb") as src, open(dest, "wb") as dst:
            while True:
                chunk = src.read(256 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
                copied += len(chunk)
                if progress_cb:
                    progress_cb(copied, size)
    except OSError as e:
        if copied < size:
            raise
        log(f"Hinweis: Laufwerk wurde direkt nach dem Schreiben getrennt ({e}) - "
            "das ist beim UF2-Bootloader normal, da das Board sofort neu startet.")


def flash_bootsel_pico(progress_cb=None, log=lambda *_a: None, cancel_event=None):
    """Kompletter Ablauf fuer einen im Bootloader-Modus angeschlossenen
    Pico: Variante erkennen, Flash-Speicher komplett loeschen (nuke-UF2)
    und danach die passende MicroPython-Firmware installieren. Liefert die
    erkannte Variante ('pico1'/'pico2') zurueck.

    cancel_event wird zwischen den einzelnen Schritten geprueft (vor jedem
    Kopiervorgang und in den Wartescheifen), aber bewusst NICHT waehrend
    eines laufenden copy_uf2_to_drive()-Aufrufs - ein mitten im Schreiben
    abgebrochener UF2-Transfer koennte den Pico in einem undefinierten
    Zustand zuruecklassen."""
    log("Suche nach einem Pico im Bootloader-Modus (BOOTSEL) ...")
    found = find_bootsel_drive(log=log)
    if not found:
        raise RuntimeError(
            "Kein Pico im Bootloader-Modus gefunden. BOOTSEL-Taste gedrueckt "
            "halten, waehrend das USB-Kabel eingesteckt (oder der Pico per "
            "Reset-Taste neu gestartet) wird, und danach erneut versuchen."
        )
    drive_root, variant = found
    label = PICO_VARIANT_LABELS[variant]

    nuke_path = get_nuke_uf2_path(variant)
    firmware_path = get_firmware_uf2_path(variant)
    if not nuke_path:
        raise RuntimeError(f"Nuke-UF2 fuer {label} nicht gefunden (picofw/-Ordner unvollstaendig).")
    if not firmware_path:
        raise RuntimeError(f"MicroPython-UF2 fuer {label} nicht gefunden (picofw/-Ordner unvollstaendig).")

    _check_cancelled(cancel_event)
    log(f"{label} im Bootloader-Modus gefunden auf {drive_root}. Loesche Flash-Speicher ...")
    copy_uf2_to_drive(nuke_path, drive_root, progress_cb=progress_cb, log=log)

    log("Warte, bis der Pico nach dem Loeschen wieder im Bootloader-Modus erscheint ...")
    wait_for_drive_gone(drive_root, cancel_event=cancel_event)
    reappeared = wait_for_bootsel_drive(log=log, cancel_event=cancel_event)
    if not reappeared:
        raise RuntimeError(
            "Der Pico ist nach dem Loeschen nicht wieder im Bootloader-Modus "
            "erschienen. Bitte USB-Kabel pruefen bzw. BOOTSEL-Taste erneut "
            "gedrueckt halten und neu einstecken."
        )
    drive_root2, _variant2 = reappeared

    _check_cancelled(cancel_event)
    log(f"Installiere {os.path.basename(firmware_path)} auf {label} ...")
    copy_uf2_to_drive(firmware_path, drive_root2, progress_cb=progress_cb, log=log)
    log(f"{label}: MicroPython erfolgreich installiert. Der Pico startet automatisch neu.")
    return variant


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
        self.geometry("620x700")
        self.minsize(560, 620)

        self.pico_port = None
        self.selected_file = None
        # Wird von _set_busy() bei jeder neuen Aktion neu erzeugt/geklaert und
        # von den Worker-Threads regelmaessig geprueft (siehe _check_cancelled()
        # in gamification_installer.py) - erlaubt dem Nutzer, eine laufende,
        # lang dauernde Aktion (z.B. Pico-Suche ueber viele COM-Ports) sofort
        # abzubrechen, statt bis zum Ende warten zu muessen, um danach eine
        # andere Aktion zu starten.
        self._cancel_event = threading.Event()

        self._build_widgets()

    # ---------- UI Aufbau ----------

    def _build_widgets(self):
        pad = {"padx": 12, "pady": 6}

        top_bar = tk.Frame(self)
        top_bar.pack(fill="x", padx=12, pady=(10, 0))
        self.busy_status_var = tk.StringVar(value="Bereit - alle Aktionen sind verfuegbar.")
        tk.Label(top_bar, textvariable=self.busy_status_var, anchor="w").pack(side="left", fill="x", expand=True)
        self.cancel_button = tk.Button(top_bar, text="Laufende Aktion abbrechen", command=self._on_cancel, state="disabled")
        self.cancel_button.pack(side="right")

        step0 = tk.LabelFrame(self, text="0. Pico im Bootloader-Modus (BOOTSEL) komplett neu flashen", padx=10, pady=8)
        step0.pack(fill="x", **pad)
        tk.Label(
            step0,
            text=(
                "Fuer einen Pico, der mit gedrueckter BOOTSEL-Taste eingesteckt wurde "
                "und als USB-Laufwerk erscheint: loescht zuerst den kompletten "
                "Flash-Speicher (passende nuke-UF2 fuer Pico 1/RP2040 bzw. "
                "Pico 2/RP2350 wird automatisch erkannt) und installiert danach "
                "automatisch die passende MicroPython-Firmware. "
                "ACHTUNG: loescht alle vorhandenen Daten auf dem Pico!"
            ),
            anchor="w", wraplength=560, justify="left",
        ).pack(fill="x", pady=(0, 6))
        row0 = tk.Frame(step0)
        row0.pack(fill="x")
        self.bootsel_flash_button = tk.Button(
            row0, text="Bootloader-Pico erkennen & komplett neu flashen", command=self._on_bootsel_flash
        )
        self.bootsel_flash_button.pack(side="left")
        self.bootsel_progress = ttk.Progressbar(step0, orient="horizontal", mode="determinate", maximum=100)
        self.bootsel_progress.pack(fill="x", pady=(8, 4))
        self.bootsel_status_var = tk.StringVar(value="Bereit.")
        tk.Label(step0, textvariable=self.bootsel_status_var, anchor="w").pack(fill="x")

        step1 = tk.LabelFrame(self, text="1. Pico verbinden", padx=10, pady=8)
        step1.pack(fill="x", **pad)
        row1 = tk.Frame(step1)
        row1.pack(fill="x")
        self.pico_status_var = tk.StringVar(value="Noch nicht gesucht - auf 'Pico suchen' klicken.")
        tk.Label(row1, textvariable=self.pico_status_var, anchor="w").pack(side="left", fill="x", expand=True)
        self.find_button = tk.Button(row1, text="Pico suchen", command=self._on_find_pico)
        self.find_button.pack(side="right")
        self.uid_button = tk.Button(row1, text="UID anzeigen", command=self._on_show_uid)
        self.uid_button.pack(side="right", padx=(0, 8))

        row1b = tk.Frame(step1)
        row1b.pack(fill="x", pady=(4, 0))
        self.uid_status_var = tk.StringVar(value="")
        tk.Label(row1b, textvariable=self.uid_status_var, anchor="w").pack(side="left")
        self.uid_value_var = tk.StringVar(value="")
        # state="readonly" statt "disabled": readonly Entries lassen sich per
        # Maus/Tastatur markieren und mit Strg+C kopieren, nur die Eingabe ist
        # gesperrt - "disabled" wuerde auch das Markieren verhindern.
        self.uid_entry = tk.Entry(row1b, textvariable=self.uid_value_var, width=20, justify="left", state="readonly")
        self.uid_entry.pack(side="left", padx=(6, 6))
        self.uid_entry.bind("<FocusIn>", lambda _e: self.uid_entry.select_range(0, "end"))
        self.uid_entry.bind("<Button-1>", lambda _e: self.after(1, lambda: self.uid_entry.select_range(0, "end")))
        self.uid_copy_button = tk.Button(row1b, text="Kopieren", command=self._on_copy_uid, state="disabled")
        self.uid_copy_button.pack(side="left")

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
        self.bootsel_flash_button.config(state=state)
        self.find_button.config(state=state)
        self.uid_button.config(state=state)
        self.browse_button.config(state=state)
        self.github_button.config(state=state)
        self.install_button.config(state="disabled" if busy else ("normal" if (self.pico_port and self.selected_file) else "disabled"))
        if busy:
            self._cancel_event.clear()
            self.cancel_button.config(state="normal")
        else:
            self.cancel_button.config(state="disabled")
            self.busy_status_var.set("Bereit - alle Aktionen sind verfuegbar.")

    def _update_install_button(self):
        self.install_button.config(state="normal" if (self.pico_port and self.selected_file) else "disabled")

    # ---------- Abbrechen ----------

    def _on_cancel(self):
        self._cancel_event.set()
        self.cancel_button.config(state="disabled")
        self.busy_status_var.set("Breche ab ... (kann noch einen Moment dauern, laeuft im Hintergrund weiter bis zum naechsten sicheren Zwischenschritt)")
        self.log("Abbruch angefordert.")

    # ---------- Pico suchen ----------

    def _on_find_pico(self):
        self._set_busy(True)
        self.pico_status_var.set("Suche nach Pico ...")
        threading.Thread(target=self._find_pico_worker, daemon=True).start()

    def _find_pico_worker(self):
        try:
            port = find_pico_port(log=self.log, cancel_event=self._cancel_event)
        except OperationCancelled:
            port = None
            self.log("Pico-Suche abgebrochen.")
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

    # ---------- UID anzeigen ----------

    def _on_show_uid(self):
        self._set_busy(True)
        self.uid_status_var.set("Lese UID ...")
        self.uid_value_var.set("")
        self.uid_copy_button.config(state="disabled")
        threading.Thread(target=self._show_uid_worker, args=(self.pico_port,), daemon=True).start()

    def _show_uid_worker(self, port):
        try:
            if port:
                uid = read_pico_uid(port, log=self.log)
            else:
                port, uid = find_pico_port_and_uid(log=self.log, cancel_event=self._cancel_event)
                if not port:
                    raise RuntimeError("Kein Pico gefunden. Bitte USB-Kabel pruefen und erneut versuchen.")
        except OperationCancelled:
            self.log("UID-Suche abgebrochen.")
            self.after(0, self._show_uid_cancelled)
            return
        except Exception as e:
            self.log(f"Fehler beim Lesen der UID: {e}")
            self.after(0, lambda: self._show_uid_failed(e))
            return
        self.after(0, lambda: self._show_uid_done(port, uid))

    def _show_uid_cancelled(self):
        self._set_busy(False)
        self.uid_status_var.set("Abgebrochen.")

    def _show_uid_failed(self, error):
        self._set_busy(False)
        self.uid_status_var.set("Fehler beim Lesen der UID.")
        messagebox.showerror("UID konnte nicht gelesen werden", str(error))

    def _show_uid_done(self, port, uid):
        self.pico_port = port
        self.pico_status_var.set(f"Verbunden: {port}")
        self._set_busy(False)
        self.uid_status_var.set("Pico-UID:")
        self.uid_value_var.set(uid)
        self.uid_copy_button.config(state="normal")
        self._update_install_button()

    def _on_copy_uid(self):
        uid = self.uid_value_var.get()
        if not uid:
            return
        self.clipboard_clear()
        self.clipboard_append(uid)
        self.uid_status_var.set("Pico-UID (in Zwischenablage kopiert):")

    # ---------- Bootloader-Modus: nuke + MicroPython flashen ----------

    def _on_bootsel_flash(self):
        if not messagebox.askyesno(
            "Kompletten Flash-Speicher loeschen?",
            "Dies loescht ALLE Daten auf dem im Bootloader-Modus (BOOTSEL) "
            "angeschlossenen Pico und installiert danach eine frische "
            "MicroPython-Firmware. Fortfahren?",
        ):
            return
        self._set_busy(True)
        self.bootsel_progress["value"] = 0
        self.bootsel_status_var.set("Suche Pico im Bootloader-Modus ...")
        threading.Thread(target=self._bootsel_flash_worker, daemon=True).start()

    def _bootsel_flash_worker(self):
        def progress_cb(copied, total):
            pct = int(copied * 100 / total) if total else 0
            self.after(0, lambda: self.bootsel_progress.configure(value=pct))

        try:
            variant = flash_bootsel_pico(progress_cb=progress_cb, log=self.log, cancel_event=self._cancel_event)
        except OperationCancelled:
            self.log("Bootloader-Flash abgebrochen.")
            self.after(0, self._bootsel_flash_cancelled)
            return
        except Exception as e:
            self.log(f"Fehler beim Bootloader-Flash: {e}")
            self.after(0, lambda: self._bootsel_flash_failed(e))
            return
        self.after(0, lambda: self._bootsel_flash_done(variant))

    def _bootsel_flash_cancelled(self):
        self._set_busy(False)
        self.bootsel_progress["value"] = 0
        self.bootsel_status_var.set("Abgebrochen.")

    def _bootsel_flash_failed(self, error):
        self._set_busy(False)
        self.bootsel_status_var.set("Fehler beim Bootloader-Flash.")
        messagebox.showerror("Bootloader-Flash fehlgeschlagen", str(error))

    def _bootsel_flash_done(self, variant):
        self._set_busy(False)
        self.bootsel_progress["value"] = 100
        label = PICO_VARIANT_LABELS[variant]
        self.bootsel_status_var.set(f"{label}: MicroPython erfolgreich installiert.")
        messagebox.showinfo(
            "Fertig", f"{label} wurde geloescht und mit MicroPython neu geflasht. Der Pico startet automatisch neu."
        )

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
            _check_cancelled(self._cancel_event)
            tag_name, assets = fetch_latest_release()
        except OperationCancelled:
            self.log("GitHub-Abfrage abgebrochen.")
            self.after(0, self._github_fetch_cancelled)
            return
        except Exception as e:
            self.after(0, lambda: self._github_fetch_failed(e))
            return
        self.after(0, lambda: self._github_fetch_done(tag_name, assets))

    def _github_fetch_cancelled(self):
        self._set_busy(False)
        self.status_var.set("Bereit.")

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
            download_asset(asset["url"], dest_path, progress_cb=progress_cb, cancel_event=self._cancel_event)
            self.log(f"Heruntergeladen: {dest_path}")
        except OperationCancelled:
            self.log("Download abgebrochen.")
            self.after(0, self._download_cancelled)
            return
        except Exception as e:
            self.after(0, lambda: self._download_failed(e))
            return
        self.after(0, lambda: self._download_done(dest_path))

    def _download_cancelled(self):
        self._set_busy(False)
        self.status_var.set("Bereit.")
        self.progress["value"] = 0

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
            upload_and_apply(port, path, progress_cb=progress_cb, log=self.log, cancel_event=self._cancel_event)
        except OperationCancelled:
            self.log("Installation abgebrochen.")
            self.after(0, self._install_cancelled)
            return
        except Exception as e:
            self.log(f"Fehler bei der Installation: {e}")
            self.after(0, lambda: self._install_failed(e))
            return
        self.after(0, self._install_done)

    def _install_cancelled(self):
        self._set_busy(False)
        self.progress["value"] = 0
        self.status_var.set("Abgebrochen.")

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
