"""
license_uploader.py

Eigenstaendiges Entwickler-Tool: installiert eine BEREITS ausgestellte
license.lic (siehe tools/license_issuer.py, landet im Archiv lizenzen/)
zusammen mit dem dafuer noetigen public_key.pem auf einem angeschlossenen
Pico - ohne Schluesselerzeugung, ohne Signieren, ohne die komplette
Firmware neu zu bauen/uebertragen.

Bewusst getrennt von tools/license_issuer.py (siehe dessen Docstring):
Ausstellen (braucht den privaten Schluessel, einmal pro Geraet) und
Installieren (kann beliebig oft wiederholt werden, z.B. erneut nach einem
Firmware-Reflash, der license.lic von der Firmware-Partition entfernt hat)
sind unabhaengige Schritte.

Voraussetzung: Der angeschlossene Pico hat BEREITS eine passende Firmware
installiert (per build_firmware.py oder windows/ Gamification Installer).

Nutzung:
    python tools/license_uploader.py     -> GUI

Baut bewusst auf denselben, bereits produktiv genutzten mpremote-
Subprocess-Helfern aus build_firmware.py auf statt sie neu zu
implementieren.
"""
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_firmware  # noqa: E402

try:
    if build_firmware.SOURCE_DIR not in sys.path:
        sys.path.insert(0, build_firmware.SOURCE_DIR)
    import license_verifier  # noqa: E402
except Exception:
    license_verifier = None


APP_TITLE = "Lizenz installieren (license.lic hochladen)"


def find_pico(log=lambda *_a: None):
    """Sucht einen angeschlossenen Pico ueber USB-Seriell - dieselbe
    mpremote-basierte Erkennung wie build_firmware.py's GUI."""
    mpremote_cmd = build_firmware._resolve_mpremote_command()
    log("Suche Pico ueber USB-Seriell...")
    ports = build_firmware.auto_detect_pico_ports(mpremote_cmd)
    if not ports:
        raise Exception("Kein Pico-COM-Port gefunden. Bitte USB neu verbinden und erneut versuchen.")
    for p in ports:
        log(f"Pruefe {p} ...")
        if build_firmware._probe_micropython_port(mpremote_cmd, p):
            return mpremote_cmd, p
    log(f"Keine Probe erfolgreich, verwende ersten Kandidaten: {ports[0]}")
    return mpremote_cmd, ports[0]


def read_license_fields(license_path):
    """Liest hardware_id/customer_id/issued aus einer license.lic - nur zur
    Anzeige/Plausibilitaetspruefung in der GUI, keine Signaturpruefung
    (die passiert ohnehin auf dem Geraet selbst, siehe
    source/license_verifier.py's verify())."""
    with open(license_path, "r", encoding="utf-8") as f:
        content = f.read()
    if license_verifier is None:
        return content, {}
    parsed = license_verifier.parse_license_text(content)
    if parsed is None:
        raise ValueError("Datei ist keine gueltige license.lic (Format nicht erkannt).")
    return content, parsed["fields"]


def write_public_key(mpremote_cmd, port):
    build_firmware._run_mpremote(
        mpremote_cmd,
        ["connect", port, "cp", build_firmware.DEFAULT_PUBLIC_KEY_PATH, ":public_key.pem"],
        timeout=20, retries=2, retry_delay=3.0,
    )


def upload_license(port_info, license_content, progress=lambda *_a: None):
    """Schreibt license_content als :license.lic + das lokale
    keys/public_key.pem als :public_key.pem auf den bereits gefundenen Pico
    (port_info = (mpremote_cmd, port), siehe find_pico()) und startet ihn
    neu. Liefert nichts, wirft bei Fehlern eine Exception."""
    mpremote_cmd, port = port_info
    total = 3

    progress(1, total, f"Bereite Pico vor ({port})...")
    build_firmware.ensure_device_raw_repl_ready(mpremote_cmd, port)

    progress(2, total, "Schreibe license.lic + public_key.pem auf den Pico...")
    build_firmware.restore_license(mpremote_cmd, port, license_content)
    write_public_key(mpremote_cmd, port)

    progress(3, total, "Starte Pico neu...")
    try:
        build_firmware._run_mpremote(
            mpremote_cmd, ["connect", port, "soft-reset"], timeout=20, retries=2, retry_delay=3.0,
        )
    except Exception as e:
        # Best effort: die Lizenz ist zu diesem Zeitpunkt bereits erfolgreich
        # geschrieben - ein fehlgeschlagener Soft-Reset darf das
        # Gesamtergebnis nicht als Fehlschlag melden.
        build_firmware._debug(f"license_uploader: finaler Soft-Reset auf {port} fehlgeschlagen (best effort): {e}")


# ==================== GUI ====================

class LicenseUploaderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("640x520")
        self.minsize(580, 460)

        self.pico_port_info = None  # (mpremote_cmd, port)
        self.pico_hardware_id = None
        self.selected_license_path = None
        self.selected_license_content = None
        self.selected_license_fields = {}

        self._build_widgets()

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

        row1b = tk.Frame(step1)
        row1b.pack(fill="x", pady=(6, 0))
        tk.Label(row1b, text="Hardware-ID:", width=14, anchor="w").pack(side="left")
        self.hardware_id_display_var = tk.StringVar(value="")
        hw_entry = tk.Entry(row1b, textvariable=self.hardware_id_display_var, state="readonly")
        hw_entry.pack(side="left", fill="x", expand=True)
        tk.Label(
            step1,
            text="Zum Ausstellen einer Lizenz (tools/license_issuer.py) hier markieren und kopieren (Strg+C).",
            anchor="w", fg="#555555",
        ).pack(fill="x", pady=(2, 0))

        step2 = tk.LabelFrame(self, text="2. license.lic auswaehlen", padx=10, pady=8)
        step2.pack(fill="x", **pad)
        row2 = tk.Frame(step2)
        row2.pack(fill="x")
        self.browse_button = tk.Button(row2, text="license.lic auswaehlen...", command=self._on_browse_license)
        self.browse_button.pack(side="left")
        self.file_status_var = tk.StringVar(value="Keine Datei ausgewaehlt.")
        tk.Label(step2, textvariable=self.file_status_var, anchor="w", wraplength=580, justify="left").pack(fill="x", pady=(6, 0))
        tk.Label(
            step2,
            text=f"Wird zusammen mit {build_firmware.DEFAULT_PUBLIC_KEY_PATH} hochgeladen (automatisch, keine Auswahl noetig).",
            anchor="w", fg="#555555", wraplength=580, justify="left",
        ).pack(fill="x", pady=(2, 0))

        step3 = tk.LabelFrame(self, text="3. Installieren", padx=10, pady=8)
        step3.pack(fill="x", **pad)
        self.upload_button = tk.Button(step3, text="Auf Pico hochladen", command=self._on_upload, state="disabled")
        self.upload_button.pack(anchor="w")
        self.progress = ttk.Progressbar(step3, orient="horizontal", mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 4))
        self.status_var = tk.StringVar(value="Bereit.")
        tk.Label(step3, textvariable=self.status_var, anchor="w").pack(fill="x")

        log_frame = tk.LabelFrame(self, text="Protokoll", padx=6, pady=6)
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def log(self, message):
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
        self._update_upload_button()
        if busy:
            self.upload_button.config(state="disabled")

    def _update_upload_button(self):
        ready = bool(self.pico_port_info and self.selected_license_content)
        self.upload_button.config(state="normal" if ready else "disabled")

    # ---------- Pico suchen ----------

    def _on_find_pico(self):
        self._set_busy(True)
        self.pico_status_var.set("Suche Pico ...")
        self.hardware_id_display_var.set("")
        threading.Thread(target=self._find_pico_worker, daemon=True).start()

    def _find_pico_worker(self):
        try:
            mpremote_cmd, port = find_pico(log=self.log)
            build_firmware.ensure_device_raw_repl_ready(mpremote_cmd, port)
            hardware_id = build_firmware.read_hardware_id(mpremote_cmd, port)
        except Exception as e:
            self.after(0, lambda: self._find_pico_failed(e))
            return
        self.after(0, lambda: self._find_pico_done(mpremote_cmd, port, hardware_id))

    def _find_pico_failed(self, error):
        self.pico_port_info = None
        self.pico_hardware_id = None
        self.pico_status_var.set("Kein Pico gefunden.")
        self.log(f"Fehler bei der Pico-Suche: {error}")
        self._set_busy(False)

    def _find_pico_done(self, mpremote_cmd, port, hardware_id):
        self.pico_port_info = (mpremote_cmd, port)
        self.pico_hardware_id = hardware_id
        self.pico_status_var.set(f"Verbunden: {port}")
        self.hardware_id_display_var.set(hardware_id)
        self.log(f"Hardware-ID: {hardware_id}")
        self._set_busy(False)
        self._update_upload_button()

    # ---------- license.lic auswaehlen ----------

    def _on_browse_license(self):
        initial_dir = build_firmware.LICENSES_DIR if os.path.isdir(build_firmware.LICENSES_DIR) else None
        path = filedialog.askopenfilename(
            title="license.lic auswaehlen",
            initialdir=initial_dir,
            filetypes=[("Lizenzdatei", "*.lic"), ("Alle Dateien", "*.*")],
        )
        if not path:
            return
        try:
            content, fields = read_license_fields(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Datei konnte nicht gelesen werden:\n{e}")
            return

        self.selected_license_path = path
        self.selected_license_content = content
        self.selected_license_fields = fields

        hw = fields.get("hardware_id", "?")
        cust = fields.get("customer_id", "")
        issued = fields.get("issued", "?")
        info = f"{os.path.basename(path)}\nHardware-ID: {hw}"
        if cust:
            info += f" | Kunde: {cust}"
        info += f" | Ausgestellt: {issued}"
        self.file_status_var.set(info)
        self.log(f"Lizenzdatei ausgewaehlt: {path} (Hardware-ID {hw})")
        self._update_upload_button()

    # ---------- Installieren ----------

    def _on_upload(self):
        if not self.pico_port_info or not self.selected_license_content:
            return

        selected_hw = self.selected_license_fields.get("hardware_id", "").strip().lower()
        device_hw = (self.pico_hardware_id or "").strip().lower()
        if selected_hw and device_hw and selected_hw != device_hw:
            if not messagebox.askyesno(
                APP_TITLE,
                "WARNUNG: Die ausgewaehlte license.lic wurde fuer eine ANDERE "
                f"Hardware-ID ausgestellt.\n\nLizenz: {selected_hw}\n"
                f"Angeschlossener Pico: {device_hw}\n\n"
                "Der Pico wird diese Lizenz ablehnen (ungueltig). Trotzdem hochladen?",
            ):
                return
        elif not messagebox.askyesno(
            APP_TITLE,
            f"license.lic jetzt auf {self.pico_port_info[1]} installieren?\n\n"
            "Das Geraet startet danach automatisch neu.",
        ):
            return

        self._set_busy(True)
        self.progress["value"] = 0
        self.status_var.set("Installation laeuft ...")
        threading.Thread(target=self._upload_worker, daemon=True).start()

    def _upload_worker(self):
        def report(step, total, message):
            def update():
                self.progress["value"] = (step / total * 100) if total else 0
                self.status_var.set(message)

            self.after(0, update)
            self.log(message)

        try:
            upload_license(self.pico_port_info, self.selected_license_content, progress=report)
        except Exception as e:
            self.after(0, lambda: self._upload_failed(e))
            return
        self.after(0, self._upload_done)

    def _upload_failed(self, error):
        self._set_busy(False)
        self.status_var.set("Fehler bei der Installation.")
        messagebox.showerror(APP_TITLE, str(error))

    def _upload_done(self):
        self._set_busy(False)
        self.progress["value"] = 100
        self.status_var.set("Installation erfolgreich abgeschlossen.")
        messagebox.showinfo(APP_TITLE, "license.lic + public_key.pem wurden installiert. Der Pico startet neu.")
        port = self.pico_port_info[1] if self.pico_port_info else "?"
        self.pico_status_var.set(f"{port} (Neustart laeuft - bei Bedarf 'Pico suchen' erneut klicken)")
        self.pico_port_info = None
        self._update_upload_button()


def main():
    app = LicenseUploaderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
