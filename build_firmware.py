"""
build_firmware.py

Verpackt alle Firmware-Dateien (main.py + alle Admin-/HTML-Seiten) des
FPV_Gamification_Pico Projekts in eine einzelne Bundle-Datei "firmware.nbo".

Diese Datei kann anschliessend ueber den Admin-Bereich (/admin-update)
per OTA-Update in EINEM Rutsch auf den Pico hochgeladen werden - der
Pico entpackt das Bundle serverseitig und ersetzt alle enthaltenen
Dateien (main.py, index.html, admin_*.html) automatisch.

Nutzung (auf dem PC, mit normalem Python 3, NICHT auf dem Pico ausfuehren):
    python build_firmware.py              -> oeffnet die grafische Oberflaeche (GUI)
    python build_firmware.py [output_path] -> Kommandozeilen-Modus (kein Fenster)

Ohne Argument oeffnet sich ein Fenster, das die gefundenen Dateien auflistet
und per Knopfdruck ("Bundle erstellen") das firmware.nbo mit Fortschrittsbalken
baut. Mit Argument laeuft das Skript wie bisher rein auf der Kommandozeile
(z.B. fuer Automatisierung/Skripte).

Bundle-Format (einfach, ohne Abhaengigkeiten wie zipfile/tarfile, damit
main.py es mit reinem MicroPython + struct wieder einlesen kann):

    Offset  Groesse   Inhalt
    0       8 Bytes   Magic-Header b"FPVBNDL1"
    8       4 Bytes   Anzahl Dateien (big-endian uint32)
    ...     pro Datei:
              4 Bytes   Laenge des Dateinamens (big-endian uint32)
              N Bytes   Dateiname (UTF-8)
              4 Bytes   Laenge des Dateiinhalts (big-endian uint32)
              M Bytes   Dateiinhalt (roh, binaer)
"""
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import base64
import json
import tempfile
from datetime import datetime
from urllib import error, parse, request

try:
    from serial.tools import list_ports
except Exception:
    list_ports = None

BUNDLE_MAGIC = b"FPVBNDL1"
DEFAULT_PICO_URL = "http://192.168.4.1"
# Ziel im MicroPython-Dateisystem (gleiche Ebene wie main.py), kein UF2-Flash.
DEVICE_BUNDLE_PATH = ":firmware.nbo"
DEBUG_ENABLED = True
DEBUG_LOG_FILE = "build_firmware_debug.log"

# Bundle-Modi:
# - Mit Boot-Stack: boot/recovery + app/web
# - Ohne Boot-Stack: nur main.py + html/admin Dateien
BOOT_STACK_FILES_TO_BUNDLE = [
    "boot.py",
    "recovery.py",
    "hotspot_common.py",
    "boot_runtime.py",
]

APP_FILES_TO_BUNDLE = [
    "main.py",
    "index.html",
    "admin_dashboard.html",
    "admin_update.html",
    "admin_simulate.html",
    "admin_profiles.html",
    "admin_system.html",
]

DEFAULT_INCLUDE_BOOT_STACK = True


def get_files_to_bundle(include_boot_stack=DEFAULT_INCLUDE_BOOT_STACK):
    files = list(APP_FILES_TO_BUNDLE)
    if include_boot_stack:
        files = list(BOOT_STACK_FILES_TO_BUNDLE) + files
    return files

OPTIONAL_FILES_TO_BUNDLE = []


def _debug(message):
    if not DEBUG_ENABLED:
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[DEBUG {ts}] {message}"
    print(line)
    try:
        with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _shorten(text, max_len=800):
    text = str(text or "")
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...<truncated>"


def build_bundle(source_dir, output_path, progress_callback=None, include_boot_stack=DEFAULT_INCLUDE_BOOT_STACK):
    """Baut das Bundle. progress_callback(done, total, filename) wird nach
    jeder verpackten Datei aufgerufen (fuer Fortschrittsanzeigen in der GUI)."""
    _debug(f"build_bundle start: source_dir={source_dir} output_path={output_path}")
    files_to_bundle = get_files_to_bundle(include_boot_stack)
    _debug(f"build_bundle mode: include_boot_stack={include_boot_stack} files={files_to_bundle}")
    included = []
    missing = []

    for filename in files_to_bundle:
        file_path = os.path.join(source_dir, filename)
        if not os.path.isfile(file_path):
            missing.append(filename)

    optional_present = []
    for filename in OPTIONAL_FILES_TO_BUNDLE:
        file_path = os.path.join(source_dir, filename)
        if os.path.isfile(file_path):
            optional_present.append(filename)

    if missing:
        print("WARNUNG: Folgende Dateien fehlen und werden NICHT ins Bundle aufgenommen:")
        for name in missing:
            print(f"  - {name}")
        print()
        _debug(f"build_bundle missing files: {missing}")

    files_present = [f for f in files_to_bundle if f not in missing]
    files_present.extend(optional_present)
    total = len(files_present)

    with open(output_path, "wb") as out:
        out.write(BUNDLE_MAGIC)
        out.write(struct.pack(">I", total))

        for i, filename in enumerate(files_present, start=1):
            file_path = os.path.join(source_dir, filename)
            with open(file_path, "rb") as f:
                content = f.read()

            name_bytes = filename.encode("utf-8")
            out.write(struct.pack(">I", len(name_bytes)))
            out.write(name_bytes)
            out.write(struct.pack(">I", len(content)))
            out.write(content)

            included.append((filename, len(content)))

            if progress_callback:
                progress_callback(i, total, filename)

    _debug(f"build_bundle done: included={len(included)} total_bytes={sum(size for _, size in included)}")

    return included, missing


def normalize_base_url(base_url):
    url = (base_url or "").strip()
    if not url:
        url = DEFAULT_PICO_URL
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    normalized = url.rstrip("/")
    _debug(f"normalize_base_url: input={base_url} output={normalized}")
    return normalized


def _post_form_json(url, form_data, timeout=8):
    _debug(f"HTTP POST {url} timeout={timeout} keys={list(form_data.keys())}")
    try:
        encoded = parse.urlencode(form_data).encode("utf-8")
        req = request.Request(url, data=encoded, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
        return json.loads(text)
    except error.HTTPError as e:
        raise Exception(f"HTTP {e.code} bei {url}: {e.reason}") from e
    except error.URLError as e:
        raise Exception(f"Netzwerkfehler bei {url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise Exception(f"Ungueltige JSON-Antwort von {url}: {e}") from e


def _get_json(url, timeout=12):
    _debug(f"HTTP GET {url} timeout={timeout}")
    try:
        with request.urlopen(url, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
        return json.loads(text)
    except error.HTTPError as e:
        raise Exception(f"HTTP {e.code} bei {url}: {e.reason}") from e
    except error.URLError as e:
        raise Exception(f"Netzwerkfehler bei {url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise Exception(f"Ungueltige JSON-Antwort von {url}: {e}") from e


def upload_bundle_to_pico(bundle_path, base_url, progress_callback=None):
    """Lädt ein bestehendes firmware.nbo Bundle per OTA hoch und finalisiert es."""
    base_url = normalize_base_url(base_url)
    _debug(f"upload_bundle_to_pico start: bundle_path={bundle_path} base_url={base_url}")
    with open(bundle_path, "rb") as f:
        raw = f.read()
    b64 = base64.b64encode(raw).decode("ascii")
    total_chunks = max(1, (len(b64) + 1023) // 1024)

    for idx in range(total_chunks):
        start = idx * 1024
        end = min(start + 1024, len(b64))
        chunk = b64[start:end]
        response = _post_form_json(
            base_url + "/upload-chunk",
            {
                "index": idx,
                "total": total_chunks,
                "target": "firmware.nbo",
                "data": chunk,
            },
        )
        if not response.get("ok"):
            err = response.get("error", "Unbekannter Upload-Fehler")
            raise Exception(f"{err} (Chunk {idx+1}/{total_chunks}, URL: {base_url}/upload-chunk)")
        if progress_callback:
            progress_callback(idx + 1, total_chunks)
        if (idx + 1) % 25 == 0 or (idx + 1) == total_chunks:
            _debug(f"upload_bundle_to_pico chunk progress: {idx + 1}/{total_chunks}")

    finalize = _get_json(base_url + "/finalize-upload")
    if not finalize.get("ok"):
        err = finalize.get("error", "Finalisierung fehlgeschlagen")
        raise Exception(f"{err} (URL: {base_url}/finalize-upload)")
    _debug("upload_bundle_to_pico done")
    return finalize


def _resolve_mpremote_command():
    candidates = []
    mpremote_path = shutil.which("mpremote")
    if mpremote_path:
        candidates.append([mpremote_path])

    # Fallback for setups where mpremote is installed as a Python module
    # but no standalone mpremote executable is on PATH.
    candidates.append([sys.executable, "-m", "mpremote"])

    for base_cmd in candidates:
        _debug(f"mpremote candidate test: {' '.join(base_cmd)}")
        try:
            subprocess.run(
                base_cmd + ["--help"],
                capture_output=True,
                text=True,
                timeout=12,
                check=True,
            )
            _debug(f"mpremote command selected: {' '.join(base_cmd)}")
            return base_cmd
        except Exception:
            _debug(f"mpremote candidate failed: {' '.join(base_cmd)}")
            continue

    raise Exception(
        "mpremote nicht gefunden. Bitte im aktiven Python installieren: "
        f"'{sys.executable} -m pip install mpremote'"
    )


def _run_mpremote(mpremote_cmd, args, timeout=120):
    cmd_text = " ".join(mpremote_cmd + args)
    _debug(f"mpremote run: {cmd_text} timeout={timeout}")
    try:
        result = subprocess.run(
            mpremote_cmd + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
        _debug(f"mpremote ok: {cmd_text} stdout={_shorten(result.stdout)} stderr={_shorten(result.stderr)}")
        return result
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or "").strip()
        _debug(f"mpremote error: {cmd_text} rc={e.returncode} raw={_shorten(err, 2000)}")
        if (
            "ClearCommError failed" in err
            or "serial.serialutil.SerialException" in err
            or "PermissionError(13" in err
        ):
            raise Exception(
                "Serieller COM-Port ist blockiert oder kein gueltiger Pico-Port. "
                "Bitte Thonny/Serial-Monitor schliessen, USB kurz neu verbinden und erneut versuchen."
            ) from e

        if err:
            lines = [line for line in err.splitlines() if line.strip()]
            if len(lines) > 10:
                err = "\n".join(lines[-10:])
        raise Exception(err or f"mpremote Aufruf fehlgeschlagen: {' '.join(args)}") from e
    except subprocess.TimeoutExpired as e:
        raise Exception(f"mpremote Timeout: {' '.join(args)}") from e


def _extract_serial_port_from_line(line):
    patterns = [r"(/dev/tty[^\s,;:]+)", r"(COM\d+)"]
    for pattern in patterns:
        m = re.search(pattern, line, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _list_system_serial_ports():
    if list_ports is None:
        return []
    ports = []
    try:
        for info in list_ports.comports():
            dev = str(getattr(info, "device", "") or "").strip()
            if dev and dev not in ports:
                ports.append(dev)
    except Exception:
        return []
    _debug(f"system serial ports: {ports}")
    return ports


def auto_detect_pico_ports(mpremote_cmd):
    _debug("auto_detect_pico_ports start")
    lines = []
    try:
        listing = _run_mpremote(mpremote_cmd, ["connect", "list"], timeout=15)
        lines = [line.strip() for line in (listing.stdout or "").splitlines() if line.strip()]
    except Exception:
        lines = []

    preferred = []
    fallback = []
    for line in lines:
        port = _extract_serial_port_from_line(line)
        if not port:
            continue
        if port in fallback:
            continue
        fallback.append(port)
        lowered = line.lower()
        if ("2e8a" in lowered) or ("raspberry" in lowered) or ("pico" in lowered):
            preferred.append(port)

    ordered = []
    for port in preferred:
        if port not in ordered:
            ordered.append(port)
    for port in fallback:
        if port not in ordered:
            ordered.append(port)

    # Ergaenze alle System-COM-Ports (z.B. COM11), auch wenn mpremote list
    # sie gerade nicht sauber labelt.
    for port in _list_system_serial_ports():
        if port not in ordered:
            ordered.append(port)

    _debug(f"auto_detect_pico_ports result: {ordered}")

    return ordered


def _probe_micropython_port(mpremote_cmd, port):
    _debug(f"probe port start: {port}")
    try:
        result = _run_mpremote(
            mpremote_cmd,
            ["connect", port, "exec", "print('PICO_OK')"],
            timeout=8,
        )
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        ok = "PICO_OK" in combined
        _debug(f"probe port result: {port} ok={ok}")
        return ok
    except Exception:
        _debug(f"probe port failed: {port}")
        return False


def upload_bundle_via_serial(bundle_path, progress_callback=None):
    """Laedt firmware.nbo per USB-Seriell auf den Pico und entpackt direkt."""
    _debug(f"upload_bundle_via_serial start: bundle_path={bundle_path}")
    mpremote_cmd = _resolve_mpremote_command()

    ports = auto_detect_pico_ports(mpremote_cmd)
    if not ports:
        raise Exception(
            "Kein Pico-COM-Port gefunden. "
            "Bitte Thonny/Serial-Monitor schliessen, USB neu verbinden und erneut versuchen."
        )

    selected_port = None
    bundle_already_copied = False
    last_error = ""

    # Schneller Port-Test: auf jedem Port nur kurzer exec-Probe statt kompletter
    # Datei-Transfer. Das ist deutlich schneller als cp auf jedem COM-Port.
    for idx, port in enumerate(ports, start=1):
        if progress_callback:
            progress_callback(1, 4, f"Pruefe Pico-Port {port} ({idx}/{len(ports)})...")
        if _probe_micropython_port(mpremote_cmd, port):
            selected_port = port
            _debug(f"probe selected port: {port}")
            break

    # Fallback: wenn Probe nicht greift, nacheinander kopieren.
    if not selected_port:
        for idx, port in enumerate(ports, start=1):
            if progress_callback:
                progress_callback(1, 4, f"Fallback-Transfer auf {port} ({idx}/{len(ports)})...")
            try:
                _run_mpremote(mpremote_cmd, ["connect", port, "cp", bundle_path, DEVICE_BUNDLE_PATH], timeout=240)
                selected_port = port
                bundle_already_copied = True
                _debug(f"fallback selected port via cp: {port}")
                break
            except Exception as e:
                last_error = str(e)
                _debug(f"fallback cp failed on {port}: {_shorten(last_error)}")

    if not selected_port:
        raise Exception(
            "Konnte firmware.nbo auf keinem gefundenen COM-Port uebertragen. "
            f"Getestete Ports: {', '.join(ports)}. Letzter Fehler: {last_error}"
        )

    port = selected_port
    if progress_callback:
        progress_callback(1, 4, f"Pico gefunden: {port}")

    # Nach erfolgreicher Probe jetzt genau einmal kopieren, falls noch nicht
    # bereits im Fallback-Transfer geschehen.
    if not bundle_already_copied:
        try:
            _run_mpremote(mpremote_cmd, ["connect", port, "cp", bundle_path, DEVICE_BUNDLE_PATH], timeout=240)
            _debug(f"bundle copied to {port} at {DEVICE_BUNDLE_PATH}")
        except Exception as e:
            raise Exception(f"Transfer auf {port} fehlgeschlagen: {e}")

    if progress_callback:
        progress_callback(2, 4, "Bundle seriell uebertragen")

    allowed_names = tuple(get_files_to_bundle(True) + OPTIONAL_FILES_TO_BUNDLE)
    allowed_tuple_literal = repr(allowed_names)
    unpack_script = f"""import os
import struct
import machine

MAGIC = b"FPVBNDL1"
ALLOWED = {allowed_tuple_literal}


def read_exact(f, n):
    data = bytearray()
    while len(data) < n:
        chunk = f.read(n - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


extracted = []
with open("firmware.nbo", "rb") as f:
    if read_exact(f, len(MAGIC)) != MAGIC:
        raise Exception("Ungueltiges Bundle (Magic)")

    count_bytes = read_exact(f, 4)
    if len(count_bytes) < 4:
        raise Exception("Bundle beschaedigt (count)")
    (count,) = struct.unpack(">I", count_bytes)

    for _ in range(count):
        name_len_bytes = read_exact(f, 4)
        if len(name_len_bytes) < 4:
            raise Exception("Bundle beschaedigt (name len)")
        (name_len,) = struct.unpack(">I", name_len_bytes)

        name_bytes = read_exact(f, name_len)
        if len(name_bytes) < name_len:
            raise Exception("Bundle beschaedigt (name)")
        name = name_bytes.decode("utf-8")

        content_len_bytes = read_exact(f, 4)
        if len(content_len_bytes) < 4:
            raise Exception("Bundle beschaedigt (content len)")
        (content_len,) = struct.unpack(">I", content_len_bytes)

        if name not in ALLOWED:
            raise Exception("Datei im Bundle nicht erlaubt: " + name)

        temp_name = name + ".bndl_tmp"
        remaining = content_len
        with open(temp_name, "wb") as out:
            while remaining > 0:
                chunk = f.read(min(512, remaining))
                if not chunk:
                    raise Exception("Bundle beschaedigt (content)")
                out.write(chunk)
                remaining -= len(chunk)

        backup_name = "main_backup.py" if name == "main.py" else (name + ".bak")
        try:
            with open(name, "r") as old_file:
                old_content = old_file.read()
            with open(backup_name, "w") as backup_file:
                backup_file.write(old_content)
        except Exception:
            pass

        try:
            os.remove(name)
        except Exception:
            pass
        os.rename(temp_name, name)
        extracted.append(name)

try:
    os.remove("firmware.nbo")
except Exception:
    pass

needs_restart = ("main.py" in extracted)
print("SERIAL_APPLY_OK:" + ",".join(extracted))
print("SERIAL_NEEDS_RESTART:" + ("1" if needs_restart else "0"))
"""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
        tf.write(unpack_script)
        temp_script_path = tf.name
    try:
        if progress_callback:
            progress_callback(3, 4, "Bundle auf Pico entpacken")

        # Port kann sich zwischen Copy und Run aendern (z.B. Reconnect/Lock).
        # Daher Entpacken robust ueber mehrere erkannte Ports versuchen.
        unpack_ports = [port]
        for candidate in auto_detect_pico_ports(mpremote_cmd):
            if candidate not in unpack_ports:
                unpack_ports.append(candidate)

        unpack_ok = False
        needs_restart = False
        unpack_error = ""
        for idx, unpack_port in enumerate(unpack_ports, start=1):
            try:
                if progress_callback:
                    progress_callback(3, 4, f"Entpacke auf {unpack_port} ({idx}/{len(unpack_ports)})...")

                # Vor dem Run in einen sauberen REPL-Zustand wechseln.
                try:
                    _run_mpremote(mpremote_cmd, ["connect", unpack_port, "soft-reset"], timeout=20)
                except Exception as sr_err:
                    _debug(f"soft-reset vor unpack auf {unpack_port} fehlgeschlagen: {_shorten(sr_err)}")

                run_result = _run_mpremote(
                    mpremote_cmd,
                    ["connect", unpack_port, "run", temp_script_path],
                    timeout=240,
                )
                run_output = (run_result.stdout or "") + "\n" + (run_result.stderr or "")
                if "SERIAL_APPLY_OK:" not in run_output:
                    raise Exception("Entpack-Skript beendet ohne Erfolgsmarker SERIAL_APPLY_OK")
                needs_restart = "SERIAL_NEEDS_RESTART:1" in run_output

                port = unpack_port
                unpack_ok = True
                _debug(f"unpack succeeded on {unpack_port}")
                break
            except Exception as e:
                unpack_error = str(e)
                _debug(f"unpack failed on {unpack_port}: {_shorten(unpack_error)}")

        if not unpack_ok:
            raise Exception(
                "Entpacken auf dem Pico fehlgeschlagen. "
                f"Getestete Ports: {', '.join(unpack_ports)}. Letzter Fehler: {unpack_error}"
            )
    finally:
        try:
            os.remove(temp_script_path)
        except Exception:
            pass

    if needs_restart:
        _debug(f"triggering post-unpack reset on {port}")
        try:
            _run_mpremote(mpremote_cmd, ["connect", port, "exec", "import machine; machine.reset()"], timeout=20)
        except Exception as reset_err:
            # Reset kann die Verbindung sofort trennen; das ist erwartbar.
            _debug(f"post-unpack reset connection ended on {port}: {_shorten(reset_err)}")

    if progress_callback:
        progress_callback(4, 4, "Bundle auf Pico entpackt")
    _debug(f"upload_bundle_via_serial done: port={port}")
    return {
        "ok": True,
        "message": (
            f"Serieller Dateisystem-Upload abgeschlossen (Pico: {port}, Ziel: {DEVICE_BUNDLE_PATH}) "
            "und auf dem Pico entpackt."
        ),
    }


def run_cli(output_path=None):
    source_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = output_path or os.path.join(source_dir, "firmware.nbo")

    def report(done, total, filename):
        print(f"[{done}/{total}] {filename}")

    included, missing = build_bundle(
        source_dir,
        output_path,
        progress_callback=report,
        include_boot_stack=DEFAULT_INCLUDE_BOOT_STACK,
    )

    total_size = sum(size for _, size in included)
    bundle_size = os.path.getsize(output_path)

    print()
    print(f"Firmware-Bundle erstellt: {output_path}")
    print()
    print(f"{'Datei':<28} {'Groesse':>10}")
    print("-" * 40)
    for filename, size in included:
        print(f"{filename:<28} {size:>8} B")
    print("-" * 40)
    print(f"{'Summe (Inhalte)':<28} {total_size:>8} B")
    print(f"{'Bundle-Datei gesamt':<28} {bundle_size:>8} B")

    if missing:
        print()
        print(f"HINWEIS: {len(missing)} Datei(en) fehlten und wurden uebersprungen (siehe oben).")

    print()
    print("Naechster Schritt: firmware.nbo im Admin-Bereich unter /admin-update hochladen.")


def launch_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    source_dir = os.path.dirname(os.path.abspath(__file__))

    root = tk.Tk()
    root.title("FPV Gamification Pico - Firmware Bundle Builder")
    root.geometry("680x460")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Gefundene Firmware-Dateien:", font=("Segoe UI", 10, "bold")).pack(anchor="w")

    tree = ttk.Treeview(frame, columns=("status", "size"), show="tree headings", height=8)
    tree.heading("#0", text="Datei")
    tree.heading("status", text="Status")
    tree.heading("size", text="Groesse")
    tree.column("#0", width=260)
    tree.column("status", width=110, anchor="center")
    tree.column("size", width=110, anchor="e")
    tree.pack(fill="x", pady=(4, 10))
    tree.tag_configure("ok", foreground="#1a7a3c")
    tree.tag_configure("missing", foreground="#b03030")

    include_boot_stack_var = tk.BooleanVar(value=DEFAULT_INCLUDE_BOOT_STACK)

    def scan_files():
        _debug("GUI scan_files triggered")
        tree.delete(*tree.get_children())
        for filename in get_files_to_bundle(include_boot_stack_var.get()):
            file_path = os.path.join(source_dir, filename)
            if os.path.isfile(file_path):
                size = os.path.getsize(file_path)
                tree.insert("", "end", text=filename, values=("Gefunden", f"{size} B"), tags=("ok",))
            else:
                tree.insert("", "end", text=filename, values=("Fehlt", "-"), tags=("missing",))

    scan_files()

    mode_frame = ttk.Frame(frame)
    mode_frame.pack(fill="x", pady=(0, 10))
    ttk.Checkbutton(
        mode_frame,
        text="Boot-/Recovery-Dateien mit ins Bundle aufnehmen",
        variable=include_boot_stack_var,
        command=scan_files,
    ).pack(anchor="w")

    path_frame = ttk.Frame(frame)
    path_frame.pack(fill="x", pady=(0, 10))
    ttk.Label(path_frame, text="Ausgabe:").pack(side="left")
    output_var = tk.StringVar(value=os.path.join(source_dir, "firmware.nbo"))
    ttk.Entry(path_frame, textvariable=output_var).pack(side="left", fill="x", expand=True, padx=6)

    def browse_output():
        path = filedialog.asksaveasfilename(
            initialdir=source_dir,
            initialfile="firmware.nbo",
            defaultextension=".nbo",
            filetypes=[("Firmware Bundle", "*.nbo"), ("Alle Dateien", "*.*")],
        )
        if path:
            output_var.set(path)

    ttk.Button(path_frame, text="Durchsuchen...", command=browse_output).pack(side="left")

    target_frame = ttk.Frame(frame)
    target_frame.pack(fill="x", pady=(0, 10))
    ttk.Label(target_frame, text="Pico URL:").pack(side="left")
    pico_url_var = tk.StringVar(value=DEFAULT_PICO_URL)
    ttk.Entry(target_frame, textvariable=pico_url_var).pack(side="left", fill="x", expand=True, padx=6)

    progress_var = tk.DoubleVar(value=0)
    ttk.Progressbar(frame, variable=progress_var, maximum=100).pack(fill="x", pady=(0, 6))

    status_var = tk.StringVar(value="Bereit.")
    ttk.Label(frame, textvariable=status_var, wraplength=640, justify="left").pack(anchor="w", pady=(0, 10))

    btn_frame = ttk.Frame(frame)
    btn_frame.pack(fill="x")
    build_button = ttk.Button(btn_frame, text="Bundle erstellen")
    build_button.pack(side="left")
    upload_button = ttk.Button(btn_frame, text="Bundle hochladen + entpacken")
    upload_button.pack(side="left", padx=6)
    serial_upload_button = ttk.Button(btn_frame, text="Seriell ins Dateisystem + entpacken (Auto)")
    serial_upload_button.pack(side="left", padx=6)
    ttk.Button(btn_frame, text="Aktualisieren", command=scan_files).pack(side="left", padx=6)

    def build_worker(output_path):
        include_boot_stack = include_boot_stack_var.get()
        _debug(f"GUI build_worker start: output_path={output_path} include_boot_stack={include_boot_stack}")
        def report(done, total, filename):
            def update():
                progress_var.set(done / total * 100 if total else 100)
                status_var.set(f"Verpacke {filename} ({done}/{total})...")
            root.after(0, update)

        try:
            included, missing = build_bundle(
                source_dir,
                output_path,
                progress_callback=report,
                include_boot_stack=include_boot_stack,
            )
            total_size = sum(size for _, size in included)
            bundle_size = os.path.getsize(output_path)

            def finish():
                progress_var.set(100)
                msg = f"Fertig: {output_path}\n{len(included)} Datei(en), {total_size} B Inhalt, {bundle_size} B Bundle."
                if missing:
                    msg += f"\nFehlend (uebersprungen): {', '.join(missing)}"
                status_var.set(msg)
                build_button.config(state="normal")
                upload_button.config(state="normal")
                serial_upload_button.config(state="normal")
                messagebox.showinfo("Bundle erstellt", msg)

            root.after(0, finish)
        except Exception as e:
            err_text = str(e)
            _debug(f"GUI build_worker failed: {_shorten(err_text)}")

            def fail():
                status_var.set(f"Fehler: {err_text}")
                build_button.config(state="normal")
                upload_button.config(state="normal")
                serial_upload_button.config(state="normal")
                messagebox.showerror("Fehler", err_text)

            root.after(0, fail)

    def start_build():
        output_path = output_var.get().strip()
        _debug(f"GUI start_build called: output_path={output_path}")
        if not output_path:
            messagebox.showerror("Fehler", "Bitte einen Ausgabepfad angeben.")
            return
        active_files = get_files_to_bundle(include_boot_stack_var.get())
        present = [f for f in active_files if os.path.isfile(os.path.join(source_dir, f))]
        if not present:
            messagebox.showerror("Fehler", "Keine der erwarteten Firmware-Dateien gefunden.")
            return
        build_button.config(state="disabled")
        upload_button.config(state="disabled")
        serial_upload_button.config(state="disabled")
        progress_var.set(0)
        status_var.set("Starte...")
        threading.Thread(target=build_worker, args=(output_path,), daemon=True).start()

    def upload_worker(bundle_path, base_url):
        _debug(f"GUI upload_worker start: bundle_path={bundle_path} base_url={base_url}")
        def set_upload_progress(done, total):
            progress_var.set(done / total * 100 if total else 100)
            status_var.set(f"Lade Bundle hoch ({done}/{total})...")

        def report(done, total):
            root.after(0, set_upload_progress, done, total)

        try:
            finalize = upload_bundle_to_pico(bundle_path, base_url, progress_callback=report)

            def finish():
                progress_var.set(100)
                msg = finalize.get("message", "Upload abgeschlossen.")
                status_var.set(msg)
                upload_button.config(state="normal")
                build_button.config(state="normal")
                serial_upload_button.config(state="normal")
                messagebox.showinfo("OTA erfolgreich", msg)

            root.after(0, finish)
        except Exception as e:
            err_text = str(e)
            _debug(f"GUI upload_worker failed: {_shorten(err_text)}")

            def fail():
                status_var.set(f"Fehler beim OTA-Upload: {err_text}")
                upload_button.config(state="normal")
                build_button.config(state="normal")
                serial_upload_button.config(state="normal")
                messagebox.showerror("OTA-Fehler", err_text)

            root.after(0, fail)

    def start_upload():
        bundle_path = output_var.get().strip()
        _debug(f"GUI start_upload called: bundle_path={bundle_path}")
        if not bundle_path:
            messagebox.showerror("Fehler", "Bitte einen Bundle-Pfad angeben.")
            return
        if not os.path.isfile(bundle_path):
            messagebox.showerror("Fehler", f"Bundle nicht gefunden:\n{bundle_path}")
            return
        base_url = normalize_base_url(pico_url_var.get())
        upload_button.config(state="disabled")
        build_button.config(state="disabled")
        serial_upload_button.config(state="disabled")
        progress_var.set(0)
        status_var.set(f"Starte OTA-Upload nach {base_url}...")
        threading.Thread(target=upload_worker, args=(bundle_path, base_url), daemon=True).start()

    def serial_upload_worker(bundle_path):
        _debug(f"GUI serial_upload_worker start: bundle_path={bundle_path}")
        def set_serial_progress(done, total, message):
            progress_var.set(done / total * 100 if total else 100)
            status_var.set(message)

        def report(done, total, message):
            root.after(0, set_serial_progress, done, total, message)

        try:
            result = upload_bundle_via_serial(bundle_path, progress_callback=report)

            def finish():
                progress_var.set(100)
                msg = result.get("message", "Serieller Upload abgeschlossen.")
                status_var.set(msg)
                serial_upload_button.config(state="normal")
                upload_button.config(state="normal")
                build_button.config(state="normal")
                messagebox.showinfo("Serieller Upload erfolgreich", msg)

            root.after(0, finish)
        except Exception as e:
            err_text = str(e)
            _debug(f"GUI serial_upload_worker failed: {_shorten(err_text)}")

            def fail():
                status_var.set(f"Fehler beim seriellen Upload: {err_text}")
                serial_upload_button.config(state="normal")
                upload_button.config(state="normal")
                build_button.config(state="normal")
                messagebox.showerror("Serieller Upload-Fehler", err_text)

            root.after(0, fail)

    def start_serial_upload():
        bundle_path = output_var.get().strip()
        _debug(f"GUI start_serial_upload called: bundle_path={bundle_path}")
        if not bundle_path:
            messagebox.showerror("Fehler", "Bitte einen Bundle-Pfad angeben.")
            return
        if not os.path.isfile(bundle_path):
            messagebox.showerror("Fehler", f"Bundle nicht gefunden:\n{bundle_path}")
            return

        serial_upload_button.config(state="disabled")
        upload_button.config(state="disabled")
        build_button.config(state="disabled")
        progress_var.set(0)
        status_var.set("Suche Pico ueber USB-Seriell...")
        threading.Thread(target=serial_upload_worker, args=(bundle_path,), daemon=True).start()

    build_button.config(command=start_build)
    upload_button.config(command=start_upload)
    serial_upload_button.config(command=start_serial_upload)

    root.mainloop()


def main():
    if len(sys.argv) > 1:
        run_cli(sys.argv[1])
    else:
        try:
            launch_gui()
        except Exception as e:
            print(f"GUI konnte nicht gestartet werden ({e}), verwende Kommandozeilen-Modus.")
            run_cli(None)


if __name__ == "__main__":
    main()
