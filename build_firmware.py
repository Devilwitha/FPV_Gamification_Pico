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
import struct
import sys
import threading
import base64
import json
from urllib import error, parse, request

BUNDLE_MAGIC = b"FPVBNDL1"

# Dateien, die im Bundle enthalten sein sollen. Muss mit OTA_ALLOWED_TARGETS
# in main.py uebereinstimmen (dort steht die serverseitige Whitelist).
FILES_TO_BUNDLE = [
    "main.py",
    "index.html",
    "admin_dashboard.html",
    "admin_update.html",
    "admin_simulate.html",
    "admin_profiles.html",
    "admin_system.html",
]


def build_bundle(source_dir, output_path, progress_callback=None):
    """Baut das Bundle. progress_callback(done, total, filename) wird nach
    jeder verpackten Datei aufgerufen (fuer Fortschrittsanzeigen in der GUI)."""
    included = []
    missing = []

    for filename in FILES_TO_BUNDLE:
        file_path = os.path.join(source_dir, filename)
        if not os.path.isfile(file_path):
            missing.append(filename)

    if missing:
        print("WARNUNG: Folgende Dateien fehlen und werden NICHT ins Bundle aufgenommen:")
        for name in missing:
            print(f"  - {name}")
        print()

    files_present = [f for f in FILES_TO_BUNDLE if f not in missing]
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

    return included, missing


def normalize_base_url(base_url):
    url = (base_url or "").strip()
    if not url:
        url = "http://192.168.4.1"
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    return url.rstrip("/")


def _post_form_json(url, form_data, timeout=8):
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

    finalize = _get_json(base_url + "/finalize-upload")
    if not finalize.get("ok"):
        err = finalize.get("error", "Finalisierung fehlgeschlagen")
        raise Exception(f"{err} (URL: {base_url}/finalize-upload)")
    return finalize


def run_cli(output_path=None):
    source_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = output_path or os.path.join(source_dir, "firmware.nbo")

    def report(done, total, filename):
        print(f"[{done}/{total}] {filename}")

    included, missing = build_bundle(source_dir, output_path, progress_callback=report)

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
    root.geometry("580x440")
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

    def scan_files():
        tree.delete(*tree.get_children())
        for filename in FILES_TO_BUNDLE:
            file_path = os.path.join(source_dir, filename)
            if os.path.isfile(file_path):
                size = os.path.getsize(file_path)
                tree.insert("", "end", text=filename, values=("Gefunden", f"{size} B"), tags=("ok",))
            else:
                tree.insert("", "end", text=filename, values=("Fehlt", "-"), tags=("missing",))

    scan_files()

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
    pico_url_var = tk.StringVar(value="http://192.168.4.1")
    ttk.Entry(target_frame, textvariable=pico_url_var).pack(side="left", fill="x", expand=True, padx=6)

    progress_var = tk.DoubleVar(value=0)
    ttk.Progressbar(frame, variable=progress_var, maximum=100).pack(fill="x", pady=(0, 6))

    status_var = tk.StringVar(value="Bereit.")
    ttk.Label(frame, textvariable=status_var, wraplength=540, justify="left").pack(anchor="w", pady=(0, 10))

    btn_frame = ttk.Frame(frame)
    btn_frame.pack(fill="x")
    build_button = ttk.Button(btn_frame, text="Bundle erstellen")
    build_button.pack(side="left")
    upload_button = ttk.Button(btn_frame, text="Bundle hochladen + entpacken")
    upload_button.pack(side="left", padx=6)
    ttk.Button(btn_frame, text="Aktualisieren", command=scan_files).pack(side="left", padx=6)

    def build_worker(output_path):
        def set_build_progress(done, total, filename):
            progress_var.set(done / total * 100 if total else 100)
            status_var.set(f"Verpacke {filename} ({done}/{total})...")

        def report(done, total, filename):
            root.after(0, set_build_progress, done, total, filename)

        try:
            included, missing = build_bundle(source_dir, output_path, progress_callback=report)
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
                messagebox.showinfo("Bundle erstellt", msg)

            root.after(0, finish)
        except Exception as e:
            def fail():
                status_var.set(f"Fehler: {e}")
                build_button.config(state="normal")
                upload_button.config(state="normal")
                messagebox.showerror("Fehler", str(e))

            root.after(0, fail)

    def start_build():
        output_path = output_var.get().strip()
        if not output_path:
            messagebox.showerror("Fehler", "Bitte einen Ausgabepfad angeben.")
            return
        present = [f for f in FILES_TO_BUNDLE if os.path.isfile(os.path.join(source_dir, f))]
        if not present:
            messagebox.showerror("Fehler", "Keine der erwarteten Firmware-Dateien gefunden.")
            return
        build_button.config(state="disabled")
        upload_button.config(state="disabled")
        progress_var.set(0)
        status_var.set("Starte...")
        threading.Thread(target=build_worker, args=(output_path,), daemon=True).start()

    def upload_worker(bundle_path, base_url):
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
                messagebox.showinfo("OTA erfolgreich", msg)

            root.after(0, finish)
        except Exception as e:
            def fail():
                status_var.set(f"Fehler beim OTA-Upload: {e}")
                upload_button.config(state="normal")
                build_button.config(state="normal")
                messagebox.showerror("OTA-Fehler", str(e))

            root.after(0, fail)

    def start_upload():
        bundle_path = output_var.get().strip()
        if not bundle_path:
            messagebox.showerror("Fehler", "Bitte einen Bundle-Pfad angeben.")
            return
        if not os.path.isfile(bundle_path):
            messagebox.showerror("Fehler", f"Bundle nicht gefunden:\n{bundle_path}")
            return
        base_url = normalize_base_url(pico_url_var.get())
        upload_button.config(state="disabled")
        build_button.config(state="disabled")
        progress_var.set(0)
        status_var.set(f"Starte OTA-Upload nach {base_url}...")
        threading.Thread(target=upload_worker, args=(bundle_path, base_url), daemon=True).start()

    build_button.config(command=start_build)
    upload_button.config(command=start_upload)

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
