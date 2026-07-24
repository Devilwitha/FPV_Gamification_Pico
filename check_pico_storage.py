"""
check_pico_storage.py

Prueft den erwarteten Speicherbedarf einer Firmware-Aktualisierung fuer den Pico
inklusive OTA-Zwischendateien und Backup-Dateien.

Ziele:
- Dateigroessen der Source-Firmware erfassen
- Bundle-Groesse (firmware.nbo) berechnen
- OTA-Overhead abschaetzen:
  - update.pbp (Base64)
  - ota_staging.tmp (dekodiertes Bundle)
  - .bak/main_backup.py (Backups alter Dateien)
  - *.bndl_tmp (temporare Datei waehrend Bundle-Apply)
- Optional den echten freien/gesamten Speicher vom Pico via mpremote abfragen
- Am Ende sagen, ob es voraussichtlich reicht

Beispiele:
  python check_pico_storage.py --mode normal --fs-total 1441792 --fs-free 620000
  python check_pico_storage.py --mode complete --probe-pico
  python check_pico_storage.py --mode recovery --fs-free 350000
"""

import argparse
import math
import os
import shutil
import subprocess
import sys
import threading

import build_firmware


# Standardwerte fuer Pico-W-Dateisystem (konservativer Startpunkt).
# Koennen per CLI/GUI ueberschrieben werden.
DEFAULT_FS_TOTAL_BYTES = 1_441_792
DEFAULT_FS_FREE_BYTES = 620_000


def _resolve_source_files(mode: str):
    if mode == "recovery":
        base = list(build_firmware.RECOVERY_FILES_TO_BUNDLE)
    else:
        include_boot = mode == "complete"
        base = list(build_firmware.get_files_to_bundle(include_boot))

    optional = []
    for name in build_firmware.OPTIONAL_FILES_TO_BUNDLE:
        path = os.path.join(build_firmware.SOURCE_DIR, name)
        if os.path.isfile(path):
            optional.append(name)

    # Reihenfolge erhalten und Duplikate entfernen
    ordered = []
    seen = set()
    for name in base + optional:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _file_sizes(file_names):
    present = []
    missing = []
    for name in file_names:
        path = os.path.join(build_firmware.SOURCE_DIR, name)
        if not os.path.isfile(path):
            missing.append(name)
            continue
        present.append((name, os.path.getsize(path)))
    return present, missing


def _estimate_bundle_size(entries):
    # Format laut build_firmware.py:
    # 8 bytes magic + 4 bytes count + je Datei: 4 + len(name_utf8) + 4 + content
    total = 8 + 4
    for name, size in entries:
        name_len = len(name.encode("utf-8"))
        total += 4 + name_len + 4 + size
    return total


def _base64_size(binary_size):
    # Base64 kodiert 3 bytes -> 4 bytes
    return 4 * math.ceil(binary_size / 3)


def _find_mpremote_command():
    candidates = []

    exe_path = shutil.which("mpremote")
    if exe_path:
        candidates.append([exe_path])

    # Aktueller Interpreter (z. B. venv)
    candidates.append([sys.executable, "-m", "mpremote"])

    # Python Launcher auf Windows als zusaetzlicher Fallback
    py_launcher = shutil.which("py")
    if py_launcher:
        candidates.append([py_launcher, "-m", "mpremote"])

    # Duplikate entfernen, Reihenfolge beibehalten
    unique = []
    seen = set()
    for cmd in candidates:
        key = " ".join(cmd)
        if key not in seen:
            seen.add(key)
            unique.append(cmd)
    return unique


def _resolve_mpremote_command():
    errors = []
    for cmd in _find_mpremote_command():
        try:
            result = subprocess.run(
                cmd + ["--help"],
                capture_output=True,
                text=True,
                timeout=12,
                check=True,
            )
            _ = result  # fuer Klarheit: nur check=True relevant
            return cmd
        except Exception as e:
            errors.append(f"{' '.join(cmd)} -> {e}")

    raise RuntimeError("mpremote nicht verfuegbar. Kandidaten: " + " | ".join(errors))


def _probe_pico_statvfs(port="auto"):
    cmd = _resolve_mpremote_command()
    probe_code = (
        "import os; "
        "s=os.statvfs('/'); "
        "print('FSVFS:%d,%d,%d,%d,%d' % (s[0], s[1], s[2], s[3], s[4]))"
    )
    full = cmd + ["connect", port, "exec", probe_code]
    try:
        result = subprocess.run(full, capture_output=True, text=True, timeout=20, check=True)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        stdout = (e.stdout or "").strip()
        details = stderr or stdout or str(e)
        raise RuntimeError(f"mpremote Aufruf fehlgeschlagen: {' '.join(full)} | Details: {details}") from e
    except Exception as e:
        raise RuntimeError(f"mpremote Aufruf fehlgeschlagen: {' '.join(full)} | Details: {e}") from e

    out = (result.stdout or "") + "\n" + (result.stderr or "")
    marker = "FSVFS:"
    idx = out.find(marker)
    if idx < 0:
        raise RuntimeError("Konnte statvfs-Ausgabe vom Pico nicht lesen")
    line = out[idx + len(marker):].splitlines()[0].strip()
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 4:
        raise RuntimeError("statvfs-Ausgabe unvollstaendig")

    bsize = int(parts[0])
    frsize = int(parts[1])
    blocks = int(parts[2])
    bfree = int(parts[3])

    unit = frsize if frsize > 0 else bsize
    total = unit * blocks
    free = unit * bfree
    return total, free


def _fmt(n):
    return f"{n:,}".replace(",", "_")


def _build_report(mode, present, missing, app_sum, max_single_file, bundle_size, update_pbp_size, ota_staging_size, backup_total_estimate, bundle_tmp_peak, peak_extra_during_finalize, fs_total, fs_free, probe_error=None, port="auto", used_default_total=False, used_default_free=False):
    lines = []
    lines.append("=" * 72)
    lines.append("PICO SPEICHERPRUEFUNG (OTA + BACKUP)")
    lines.append("=" * 72)
    lines.append(f"Modus: {mode}")
    lines.append(f"Quellordner: {build_firmware.SOURCE_DIR}")
    lines.append(f"Dateien im Update: {len(present)}")
    if missing:
        lines.append(f"Fehlende Dateien (nicht eingerechnet): {', '.join(missing)}")
    lines.append("")

    lines.append("Dateibedarf:")
    lines.append(f"  Summe Update-Dateien          : {_fmt(app_sum)} B")
    lines.append(f"  Groesste Einzeldatei          : {_fmt(max_single_file)} B")
    lines.append(f"  Geschaetzte Bundle-Groesse    : {_fmt(bundle_size)} B")
    lines.append(f"  update.pbp (Base64)           : {_fmt(update_pbp_size)} B")
    lines.append(f"  ota_staging.tmp               : {_fmt(ota_staging_size)} B")
    lines.append(f"  Backup-Dateien (.bak/main_*)  : {_fmt(backup_total_estimate)} B")
    lines.append(f"  Temp-Datei *.bndl_tmp (Peak)  : {_fmt(bundle_tmp_peak)} B")
    lines.append(f"  Zusatzbedarf Peak (Finalize)  : {_fmt(peak_extra_during_finalize)} B")
    lines.append("")

    if probe_error is not None:
        lines.append("Pico-Probe via mpremote: FEHLER")
        lines.append(f"  Port            : {port}")
        lines.append(f"  Details         : {probe_error}")
        lines.append("")
    elif fs_free is not None and fs_total is not None:
        lines.append("Pico-Probe/Parameter:")
        lines.append(f"  Port            : {port}")
        lines.append(f"  FS total        : {_fmt(fs_total)} B")
        lines.append(f"  FS free         : {_fmt(fs_free)} B")
        lines.append("")

    if used_default_total or used_default_free:
        lines.append("Hinweis: Standardwerte verwendet")
        if used_default_total:
            lines.append(f"  FS total (Default): {_fmt(DEFAULT_FS_TOTAL_BYTES)} B")
        if used_default_free:
            lines.append(f"  FS free  (Default): {_fmt(DEFAULT_FS_FREE_BYTES)} B")
        lines.append("")

    if fs_free is None:
        lines.append("Bewertung: UNVOLLSTAENDIG")
        lines.append("  Bitte fs-free angeben oder Pico-Probe aktivieren.")
        lines.append("  Dann kann das Skript sagen, ob der Speicher reicht.")
        return "\n".join(lines)

    margin = fs_free - peak_extra_during_finalize
    if margin >= 0:
        lines.append("Bewertung: AUSREICHEND")
        lines.append(f"  Freier Speicher: {_fmt(fs_free)} B")
        lines.append(f"  Peak-Zusatzbedarf: {_fmt(peak_extra_during_finalize)} B")
        lines.append(f"  Reserve: {_fmt(margin)} B")
    else:
        lines.append("Bewertung: NICHT AUSREICHEND")
        lines.append(f"  Freier Speicher: {_fmt(fs_free)} B")
        lines.append(f"  Peak-Zusatzbedarf: {_fmt(peak_extra_during_finalize)} B")
        lines.append(f"  Fehlend: {_fmt(-margin)} B")

    if fs_total is not None:
        used = fs_total - fs_free
        lines.append(f"  FS total: {_fmt(fs_total)} B | FS used: {_fmt(used)} B")

    return "\n".join(lines)


def run_check(mode="normal", fs_total=None, fs_free=None, probe_pico=False, port="auto"):
    selected_files = _resolve_source_files(mode)
    present, missing = _file_sizes(selected_files)

    app_sum = sum(size for _, size in present)
    max_single_file = max((size for _, size in present), default=0)

    bundle_size = _estimate_bundle_size(present)
    update_pbp_size = _base64_size(bundle_size)
    ota_staging_size = bundle_size
    backup_total_estimate = app_sum
    bundle_tmp_peak = max_single_file
    peak_extra_during_finalize = update_pbp_size + ota_staging_size + backup_total_estimate + bundle_tmp_peak

    probe_error = None
    used_default_total = fs_total is None
    used_default_free = fs_free is None

    if fs_total is None:
        fs_total = DEFAULT_FS_TOTAL_BYTES
    if fs_free is None:
        fs_free = DEFAULT_FS_FREE_BYTES

    if probe_pico:
        try:
            probed_total, probed_free = _probe_pico_statvfs(port)
            fs_total = probed_total
            fs_free = probed_free
            used_default_total = False
            used_default_free = False
        except Exception as e:
            probe_error = str(e)

    return _build_report(
        mode,
        present,
        missing,
        app_sum,
        max_single_file,
        bundle_size,
        update_pbp_size,
        ota_staging_size,
        backup_total_estimate,
        bundle_tmp_peak,
        peak_extra_during_finalize,
        fs_total,
        fs_free,
        probe_error=probe_error,
        port=port,
        used_default_total=used_default_total,
        used_default_free=used_default_free,
    )


def launch_gui():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Pico Speicherpruefung")
    root.geometry("820x700")
    root.resizable(True, True)

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Modus (3 Optionen):", font=("Segoe UI", 10, "bold")).pack(anchor="w")
    mode_var = tk.StringVar(value="normal")
    mode_box = ttk.Frame(frame)
    mode_box.pack(fill="x", pady=(4, 8))
    ttk.Radiobutton(mode_box, text="Normal", value="normal", variable=mode_var).pack(side="left", padx=(0, 8))
    ttk.Radiobutton(mode_box, text="Complete", value="complete", variable=mode_var).pack(side="left", padx=(0, 8))
    ttk.Radiobutton(mode_box, text="Recovery", value="recovery", variable=mode_var).pack(side="left")

    probe_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(frame, text="Pico via mpremote abfragen", variable=probe_var).pack(anchor="w", pady=(2, 8))

    row1 = ttk.Frame(frame)
    row1.pack(fill="x", pady=(0, 6))
    ttk.Label(row1, text="Port:").pack(side="left")
    port_var = tk.StringVar(value="auto")
    ttk.Entry(row1, textvariable=port_var, width=16).pack(side="left", padx=6)

    row2 = ttk.Frame(frame)
    row2.pack(fill="x", pady=(0, 6))
    ttk.Label(row2, text="FS total (optional):").pack(side="left")
    total_var = tk.StringVar(value=str(DEFAULT_FS_TOTAL_BYTES))
    ttk.Entry(row2, textvariable=total_var, width=18).pack(side="left", padx=6)

    row3 = ttk.Frame(frame)
    row3.pack(fill="x", pady=(0, 10))
    ttk.Label(row3, text="FS free (optional):").pack(side="left")
    free_var = tk.StringVar(value=str(DEFAULT_FS_FREE_BYTES))
    ttk.Entry(row3, textvariable=free_var, width=18).pack(side="left", padx=6)

    status_var = tk.StringVar(value="Bereit.")
    ttk.Label(frame, textvariable=status_var).pack(anchor="w", pady=(0, 8))

    text = tk.Text(frame, wrap="word", height=28)
    text.pack(fill="both", expand=True)

    def parse_optional_int(value):
        value = (value or "").strip()
        if not value:
            return None
        return int(value)

    def run_worker():
        try:
            mode = mode_var.get().strip() or "normal"
            port = (port_var.get() or "auto").strip() or "auto"
            fs_total = parse_optional_int(total_var.get())
            fs_free = parse_optional_int(free_var.get())
            report = run_check(mode=mode, fs_total=fs_total, fs_free=fs_free, probe_pico=probe_var.get(), port=port)

            def finish_ok():
                text.delete("1.0", "end")
                text.insert("1.0", report)
                status_var.set("Fertig.")
                run_btn.config(state="normal")

            root.after(0, finish_ok)
        except Exception as e:
            def finish_err():
                text.delete("1.0", "end")
                text.insert("1.0", f"Fehler: {e}")
                status_var.set("Fehler.")
                run_btn.config(state="normal")

            root.after(0, finish_err)

    def start_check():
        run_btn.config(state="disabled")
        status_var.set("Pruefung laeuft...")
        threading.Thread(target=run_worker, daemon=True).start()

    run_btn = ttk.Button(frame, text="Speicher pruefen", command=start_check)
    run_btn.pack(anchor="w", pady=(0, 8))

    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="Pico Speicherpruefung fuer Firmware-Update + Backups")
    parser.add_argument("--mode", choices=["normal", "complete", "recovery"], default="normal")
    parser.add_argument("--fs-total", type=int, default=DEFAULT_FS_TOTAL_BYTES, help="Gesamtgroesse des Pico-Dateisystems in Bytes")
    parser.add_argument("--fs-free", type=int, default=DEFAULT_FS_FREE_BYTES, help="Freier Speicher auf dem Pico in Bytes")
    parser.add_argument("--probe-pico", action="store_true", help="Dateisystemgroesse/frei via mpremote abfragen")
    parser.add_argument("--port", default="auto", help="Port fuer mpremote (z.B. COM11), default: auto")
    parser.add_argument("--gui", action="store_true", help="GUI starten")
    args = parser.parse_args()

    if args.gui or len(sys.argv) == 1:
        launch_gui()
        return

    report = run_check(
        mode=args.mode,
        fs_total=args.fs_total,
        fs_free=args.fs_free,
        probe_pico=args.probe_pico,
        port=args.port,
    )
    print(report)


if __name__ == "__main__":
    main()
