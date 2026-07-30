"""
license_issuer.py

Eigenstaendiges Entwickler-Tool: erzeugt/verwaltet ausschliesslich das RSA-
Schluesselpaar (siehe license_generator.py) und signiert license.lic-Dateien
fuer eine manuell angegebene Hardware-ID.

Hat KEINERLEI Verbindung zu einem Pico (kein USB/Seriell, kein mpremote) -
die Hardware-ID wird als Text eingegeben, nicht vom Geraet ausgelesen. Diese
Trennung ist Absicht: Ausstellen (braucht den privaten Schluessel, rein
lokal/offline moeglich) und Installieren (braucht ein angeschlossenes
Geraet) sind unabhaengige Schritte - siehe tools/license_uploader.py fuer
das Auslesen der Hardware-ID vom Pico und Installieren der Lizenz.

NICHT fuer Endnutzer gedacht: benoetigt den privaten RSA-Schluessel
(keys/private_key.pem), der das Projekt niemals verlassen darf. Bleibt daher
bewusst in tools/ (Entwickler-Skripte), nicht in windows/ (das dort gebaute
.exe wird an Endnutzer weitergegeben).

Nutzung:
    python tools/license_issuer.py     -> GUI

Wiederverwendet fuer Schluesselverwaltung/Archivierung bewusst dieselben,
bereits produktiv genutzten Funktionen aus build_firmware.py
(keys_exist()/generate_keypair_if_missing()/save_license_record(), alles
reine Dateisystem-Operationen ohne Pico-Bezug) statt sie zu duplizieren.
"""
import os
import sys
import tkinter as tk
from tkinter import messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_firmware  # noqa: E402

try:
    import license_generator  # noqa: E402
except Exception:
    license_generator = None


APP_TITLE = "Lizenz ausstellen (Keys & license.lic)"


def issue_license(hardware_id, customer_id):
    """Signiert eine license.lic fuer hardware_id/customer_id und legt sie
    im Archiv (lizenzen/) ab. Rein lokal, keine Geraeteverbindung. Liefert
    ein dict mit den Ergebnisdaten (Hardware-ID, Pfad der archivierten
    Lizenz)."""
    if license_generator is None:
        raise Exception("Paket 'cryptography' nicht installiert (siehe requirements/requirements.txt).")
    if not build_firmware.keys_exist():
        raise Exception(
            f"Kein RSA-Schluesselpaar unter {build_firmware.KEYS_DIR} gefunden. "
            "Zuerst 'Schluesselpaar erzeugen' ausfuehren."
        )
    hardware_id = hardware_id.strip()
    if not hardware_id:
        raise ValueError("Bitte eine Hardware-ID eingeben.")

    private_key = license_generator.load_private_key(build_firmware.DEFAULT_PRIVATE_KEY_PATH)
    license_content = license_generator.sign_license(private_key, hardware_id, customer_id)
    lic_path, _json_path = build_firmware.save_license_record(
        hardware_id, customer_id, license_content,
    )

    return {
        "hardware_id": hardware_id,
        "license_record_path": lic_path,
    }


# ==================== GUI ====================

class LicenseIssuerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("620x420")
        self.minsize(560, 380)

        self._build_widgets()
        self._refresh_key_status()

    def _build_widgets(self):
        pad = {"padx": 12, "pady": 6}

        step1 = tk.LabelFrame(self, text="1. RSA-Schluesselpaar", padx=10, pady=8)
        step1.pack(fill="x", **pad)
        row1 = tk.Frame(step1)
        row1.pack(fill="x")
        self.key_status_var = tk.StringVar(value="Pruefe ...")
        tk.Label(row1, textvariable=self.key_status_var, anchor="w").pack(side="left", fill="x", expand=True)
        self.keygen_button = tk.Button(row1, text="Schluesselpaar erzeugen", command=self._on_generate_keys)
        self.keygen_button.pack(side="right")
        tk.Label(
            step1,
            text=f"Speicherort: {build_firmware.KEYS_DIR}\nDer private Schluessel verlaesst diesen Ordner NIE.",
            anchor="w", justify="left", fg="#555555",
        ).pack(fill="x", pady=(4, 0))

        step2 = tk.LabelFrame(self, text="2. license.lic erstellen", padx=10, pady=8)
        step2.pack(fill="x", **pad)

        row2a = tk.Frame(step2)
        row2a.pack(fill="x", pady=(0, 4))
        tk.Label(row2a, text="Hardware-ID:", width=14, anchor="w").pack(side="left")
        self.hardware_id_var = tk.StringVar(value="")
        tk.Entry(row2a, textvariable=self.hardware_id_var).pack(side="left", fill="x", expand=True)

        row2b = tk.Frame(step2)
        row2b.pack(fill="x")
        tk.Label(row2b, text="Kunden-ID:", width=14, anchor="w").pack(side="left")
        self.customer_id_var = tk.StringVar(value="")
        tk.Entry(row2b, textvariable=self.customer_id_var).pack(side="left", fill="x", expand=True)

        tk.Label(
            step2,
            text="Die Hardware-ID des Geraets liefert tools/license_uploader.py beim Verbinden "
                 "mit dem Pico an (dort auch ablesbar/kopierbar).",
            anchor="w", fg="#555555", justify="left", wraplength=560,
        ).pack(fill="x", pady=(6, 0))

        self.issue_button = tk.Button(step2, text="license.lic erstellen", command=self._on_issue_license)
        self.issue_button.pack(anchor="w", pady=(10, 4))
        self.status_var = tk.StringVar(value="Bereit.")
        tk.Label(step2, textvariable=self.status_var, anchor="w", wraplength=560, justify="left").pack(fill="x")

    def _refresh_key_status(self):
        if build_firmware.keys_exist():
            self.key_status_var.set("Schluesselpaar vorhanden.")
            self.keygen_button.config(text="Neues Schluesselpaar erzeugen wuerde ALLE bestehenden Lizenzen ungueltig machen", state="disabled")
        else:
            self.key_status_var.set("Kein Schluesselpaar gefunden.")
            self.keygen_button.config(text="Schluesselpaar erzeugen", state="normal")

    # ---------- Schluesselpaar ----------

    def _on_generate_keys(self):
        if license_generator is None:
            messagebox.showerror(APP_TITLE, "Paket 'cryptography' nicht installiert (siehe requirements/requirements.txt).")
            return
        try:
            created = build_firmware.generate_keypair_if_missing()
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Schluesselpaar konnte nicht erzeugt werden:\n{e}")
            return
        if created:
            messagebox.showinfo(APP_TITLE, "Neues Schluesselpaar wurde erzeugt.")
        self._refresh_key_status()

    # ---------- Lizenz ausstellen ----------

    def _on_issue_license(self):
        if not build_firmware.keys_exist():
            messagebox.showerror(APP_TITLE, "Bitte zuerst ein Schluesselpaar erzeugen.")
            return

        hardware_id = self.hardware_id_var.get().strip()
        customer_id = self.customer_id_var.get().strip()
        if not hardware_id:
            messagebox.showerror(APP_TITLE, "Bitte eine Hardware-ID eingeben.")
            return

        try:
            result = issue_license(hardware_id, customer_id)
        except Exception as e:
            self.status_var.set("Fehler beim Ausstellen der Lizenz.")
            messagebox.showerror(APP_TITLE, str(e))
            return

        self.status_var.set(f"Fertig. Archiviert unter: {result['license_record_path']}")
        messagebox.showinfo(
            APP_TITLE,
            "Lizenz erfolgreich ausgestellt und archiviert.\n\n"
            f"Hardware-ID: {result['hardware_id']}\n"
            f"Archiviert unter: {result['license_record_path']}\n\n"
            "Zum Installieren auf dem Pico jetzt tools/license_uploader.py verwenden.",
        )


def main():
    app = LicenseIssuerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
