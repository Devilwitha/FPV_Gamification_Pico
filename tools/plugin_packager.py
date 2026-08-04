"""
tools/plugin_packager.py

GUI-Werkzeug zum Vorbereiten und Hochladen eines Mod-Ordners
(source/mods/<name>/) in den Webshop-Plugin-Store (siehe webshop/app.py's
/plugins/upload bzw. /admin/plugins/upload).

Drei Schritte, unabhaengig voneinander nutzbar:

1. "Ordner packen": waehlt einen Mod-Ordner unter source/mods/, kompiliert
   dessen .py-Dateien per mpy-cross zu .mpy (Quellcode-Schutz - siehe
   build_firmware.py's compile_sources_to_mpy(), hier direkt wiederverwendet
   statt eine zweite mpy-cross-Anbindung zu bauen) und packt das Ergebnis zu
   einer ZIP-Datei. WICHTIG: die ZIP enthaelt NUR die kompilierten
   .mpy-Dateien (+ alle Nicht-.py-Dateien wie manifest.json/admin_*.html
   unveraendert) - die .py-Originale im lokalen Mod-Ordner bleiben
   unangetastet, landen aber NICHT in der ZIP (der Webshop-Store lehnt rohe
   .py-Dateien beim Upload ohnehin ab, siehe app.py's
   _process_plugin_zip_upload()).
2. "Hochladen": loggt sich mit einem Kunden-Konto (E-Mail+Passwort, siehe
   webshop/app.py's /login) bei einem Webshop ein und schickt eine
   ausgewaehlte ZIP-Datei (frisch gepackt ODER manuell ausgewaehlt) an
   /plugins/upload.
3. "Herunterladen": laedt die Dateien eines bereits im Webshop vorhandenen
   Mods (siehe /api/plugins) in einen lokalen Ordner herunter - z.B. um ein
   bestehendes Plugin zu inspizieren oder weiterzuentwickeln.

Nutzung:
    python tools/plugin_packager.py   -> GUI

Als eigenstaendige .exe bauen (siehe windows/ fuer ein Beispiel eines
bereits so gebauten Tools):
    pyinstaller --onefile --windowed --name PluginPackager tools/plugin_packager.py
"""
import os
import sys
import tempfile
import threading
import tkinter as tk
import zipfile
from tkinter import filedialog, messagebox, ttk

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_firmware  # noqa: E402
import deploy_mod  # noqa: E402

APP_TITLE = "Plugin-Store: Paketieren & Hochladen"
DEFAULT_WEBSHOP_URL = "http://46.4.78.34:5000"
DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin_downloads")
PACKAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin_packages")


def pack_mod_to_zip(mod_name, output_zip_path, progress=lambda *_a: None):
    """Kompiliert alle .py-Dateien des Mods zu .mpy (mpy-cross) und packt das
    Ergebnis (KEINE .py-Dateien) in output_zip_path. Gibt output_zip_path
    zurueck."""
    mod_dir = os.path.join(deploy_mod.MODS_SOURCE_DIR, mod_name)
    files = deploy_mod._mod_files(mod_name)
    if not files:
        raise Exception(f"Mod-Ordner '{mod_name}' ist leer oder existiert nicht: {mod_dir}")

    progress(0, 1, "Suche mpy-cross...")
    mpy_cross_cmd = build_firmware._resolve_mpy_cross_command()

    with tempfile.TemporaryDirectory(prefix="plugin_pack_") as staging_dir:
        entries = build_firmware.compile_sources_to_mpy(
            mpy_cross_cmd, mod_dir, files, staging_dir,
            progress_callback=lambda i, total, msg: progress(i, total, msg),
        )
        os.makedirs(os.path.dirname(output_zip_path) or ".", exist_ok=True)
        with zipfile.ZipFile(output_zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for device_name, local_path in entries:
                archive.write(local_path, arcname=device_name)

    return output_zip_path


def webshop_login(session, base_url, email, password):
    """Loggt eine requests.Session beim Webshop ein (Kunden-Login, siehe
    webshop/app.py's /login) - wirft eine Exception mit verstaendlicher
    Meldung bei falschen Zugangsdaten."""
    response = session.post(
        base_url.rstrip("/") + "/login",
        data={"email": email, "password": password},
        allow_redirects=False,
        timeout=15,
    )
    if response.status_code != 302:
        raise Exception("Login fehlgeschlagen (E-Mail/Passwort pruefen).")


def upload_plugin_zip(session, base_url, zip_path):
    """Schickt eine bereits gepackte ZIP-Datei an /plugins/upload - die
    Session muss vorher per webshop_login() eingeloggt worden sein."""
    with open(zip_path, "rb") as zip_file:
        response = session.post(
            base_url.rstrip("/") + "/plugins/upload",
            files={"plugin_zip": (os.path.basename(zip_path), zip_file, "application/zip")},
            allow_redirects=False,
            timeout=60,
        )
    if response.status_code != 302:
        raise Exception(f"Upload fehlgeschlagen (unerwarteter Status {response.status_code}).")


def fetch_store_plugins(base_url):
    """Liefert die Mod-Liste des Webshops (siehe /api/plugins) als Liste von
    Dicts mit u.a. 'name'/'version'/'files'."""
    response = requests.get(base_url.rstrip("/") + "/api/plugins", timeout=15)
    response.raise_for_status()
    return response.json().get("plugins", [])


def download_plugin(base_url, plugin_name, files, target_dir, progress=lambda *_a: None):
    """Laedt jede Datei eines Store-Mods einzeln herunter (siehe Flasks
    Standard-static-Serving unter /static/plugins_store/<name>/<file>) nach
    target_dir/<plugin_name>/."""
    plugin_dir = os.path.join(target_dir, plugin_name)
    os.makedirs(plugin_dir, exist_ok=True)
    total = len(files)
    for i, filename in enumerate(files, start=1):
        progress(i, total, f"Lade {filename} herunter...")
        url = f"{base_url.rstrip('/')}/static/plugins_store/{plugin_name}/{filename}"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        with open(os.path.join(plugin_dir, filename), "wb") as f:
            f.write(response.content)
    return plugin_dir


# ==================== GUI ====================

class PluginPackagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("640x680")
        self.minsize(600, 600)

        self.session = requests.Session()
        self.selected_zip_path = None
        self.store_plugins = []

        self._build_widgets()

    def _build_widgets(self):
        pad = {"padx": 12, "pady": 6}

        step1 = tk.LabelFrame(self, text="1. Mod-Ordner packen (.py -> .mpy)", padx=10, pady=8)
        step1.pack(fill="x", **pad)
        row1 = tk.Frame(step1)
        row1.pack(fill="x")
        tk.Label(row1, text="Mod:", width=8, anchor="w").pack(side="left")
        self.mod_var = tk.StringVar()
        self.mod_combo = ttk.Combobox(row1, textvariable=self.mod_var, state="readonly")
        self.mod_combo["values"] = deploy_mod.list_local_mods()
        if self.mod_combo["values"]:
            self.mod_combo.current(0)
        self.mod_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.pack_button = tk.Button(row1, text="Packen", command=self._on_pack)
        self.pack_button.pack(side="right")

        step2 = tk.LabelFrame(self, text="2. ZIP auswaehlen (frisch gepackt oder manuell)", padx=10, pady=8)
        step2.pack(fill="x", **pad)
        row2 = tk.Frame(step2)
        row2.pack(fill="x")
        self.browse_button = tk.Button(row2, text="ZIP-Datei auswaehlen...", command=self._on_browse_zip)
        self.browse_button.pack(side="left")
        self.zip_status_var = tk.StringVar(value="Keine ZIP-Datei ausgewaehlt.")
        tk.Label(step2, textvariable=self.zip_status_var, anchor="w", wraplength=580, justify="left").pack(
            fill="x", pady=(6, 0)
        )

        step3 = tk.LabelFrame(self, text="3. Webshop-Login", padx=10, pady=8)
        step3.pack(fill="x", **pad)
        self._labeled_entry(step3, "URL:", "url_var", DEFAULT_WEBSHOP_URL)
        self._labeled_entry(step3, "E-Mail:", "email_var", "")
        self._labeled_entry(step3, "Passwort:", "password_var", "", show="*")

        step4 = tk.LabelFrame(self, text="4. Hochladen / Herunterladen", padx=10, pady=8)
        step4.pack(fill="x", **pad)
        row4 = tk.Frame(step4)
        row4.pack(fill="x")
        self.upload_button = tk.Button(row4, text="Hochladen", command=self._on_upload)
        self.upload_button.pack(side="left")
        self.list_button = tk.Button(row4, text="Store-Mods laden...", command=self._on_list_store)
        self.list_button.pack(side="left", padx=(8, 0))

        row4b = tk.Frame(step4)
        row4b.pack(fill="x", pady=(8, 0))
        tk.Label(row4b, text="Mod im Store:", width=12, anchor="w").pack(side="left")
        self.store_mod_var = tk.StringVar()
        self.store_mod_combo = ttk.Combobox(row4b, textvariable=self.store_mod_var, state="readonly")
        self.store_mod_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.download_button = tk.Button(row4b, text="Herunterladen", command=self._on_download)
        self.download_button.pack(side="right")

        self.progress = ttk.Progressbar(self, orient="horizontal", mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=12, pady=(4, 4))
        self.status_var = tk.StringVar(value="Bereit.")
        tk.Label(self, textvariable=self.status_var, anchor="w").pack(fill="x", padx=12)

        log_frame = tk.LabelFrame(self, text="Protokoll", padx=6, pady=6)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def _labeled_entry(self, parent, label, attr_name, default, show=None):
        row = tk.Frame(parent)
        row.pack(fill="x", pady=(0, 4))
        tk.Label(row, text=label, width=10, anchor="w").pack(side="left")
        var = tk.StringVar(value=default)
        setattr(self, attr_name, var)
        entry = tk.Entry(row, textvariable=var, show=show)
        entry.pack(side="left", fill="x", expand=True)

    def log(self, message):
        def append():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        self.after(0, append)

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        for widget in (self.pack_button, self.browse_button, self.upload_button, self.list_button, self.download_button):
            widget.config(state=state)

    # ---------- Packen ----------

    def _on_pack(self):
        mod_name = self.mod_var.get()
        if not mod_name:
            messagebox.showerror(APP_TITLE, "Bitte zuerst einen Mod auswaehlen.")
            return
        self._set_busy(True)
        self.status_var.set(f"Packe '{mod_name}'...")
        threading.Thread(target=self._pack_worker, args=(mod_name,), daemon=True).start()

    def _pack_worker(self, mod_name):
        def report(step, total, message):
            def update():
                self.progress["value"] = (step / total * 100) if total else 0
                self.status_var.set(message)

            self.after(0, update)
            self.log(message)

        output_path = os.path.join(PACKAGES_DIR, f"{mod_name}.zip")
        try:
            pack_mod_to_zip(mod_name, output_path, progress=report)
        except Exception as e:
            self.after(0, lambda: self._pack_failed(e))
            return
        self.after(0, lambda: self._pack_done(output_path))

    def _pack_failed(self, error):
        self._set_busy(False)
        self.status_var.set("Fehler beim Packen.")
        messagebox.showerror(APP_TITLE, str(error))

    def _pack_done(self, output_path):
        self._set_busy(False)
        self.progress["value"] = 100
        self.status_var.set("Paketierung abgeschlossen.")
        self.selected_zip_path = output_path
        self.zip_status_var.set(f"Gepackt: {output_path}")
        self.log(f"ZIP erstellt: {output_path}")

    # ---------- ZIP auswaehlen ----------

    def _on_browse_zip(self):
        path = filedialog.askopenfilename(
            title="Plugin-ZIP auswaehlen", filetypes=[("ZIP-Datei", "*.zip"), ("Alle Dateien", "*.*")]
        )
        if not path:
            return
        self.selected_zip_path = path
        self.zip_status_var.set(f"Ausgewaehlt: {path}")
        self.log(f"ZIP ausgewaehlt: {path}")

    # ---------- Hochladen ----------

    def _on_upload(self):
        if not self.selected_zip_path:
            messagebox.showerror(APP_TITLE, "Bitte zuerst eine ZIP-Datei packen oder auswaehlen.")
            return
        if not self.email_var.get() or not self.password_var.get():
            messagebox.showerror(APP_TITLE, "Bitte E-Mail und Passwort fuer den Webshop-Login angeben.")
            return
        self._set_busy(True)
        self.status_var.set("Logge ein und lade hoch...")
        threading.Thread(target=self._upload_worker, daemon=True).start()

    def _upload_worker(self):
        try:
            webshop_login(self.session, self.url_var.get(), self.email_var.get(), self.password_var.get())
            self.log("Login erfolgreich.")
            upload_plugin_zip(self.session, self.url_var.get(), self.selected_zip_path)
        except Exception as e:
            self.after(0, lambda: self._upload_failed(e))
            return
        self.after(0, self._upload_done)

    def _upload_failed(self, error):
        self._set_busy(False)
        self.status_var.set("Fehler beim Hochladen.")
        messagebox.showerror(APP_TITLE, str(error))

    def _upload_done(self):
        self._set_busy(False)
        self.status_var.set("Hochladen erfolgreich abgeschlossen.")
        messagebox.showinfo(APP_TITLE, "Plugin erfolgreich hochgeladen.")

    # ---------- Store-Mods anzeigen / herunterladen ----------

    def _on_list_store(self):
        self._set_busy(True)
        self.status_var.set("Lade Mod-Liste vom Webshop...")
        threading.Thread(target=self._list_store_worker, daemon=True).start()

    def _list_store_worker(self):
        try:
            plugins = fetch_store_plugins(self.url_var.get())
        except Exception as e:
            self.after(0, lambda: self._list_store_failed(e))
            return
        self.after(0, lambda: self._list_store_done(plugins))

    def _list_store_failed(self, error):
        self._set_busy(False)
        self.status_var.set("Fehler beim Laden der Mod-Liste.")
        messagebox.showerror(APP_TITLE, str(error))

    def _list_store_done(self, plugins):
        self._set_busy(False)
        self.store_plugins = plugins
        names = [p["name"] for p in plugins]
        self.store_mod_combo["values"] = names
        if names:
            self.store_mod_combo.current(0)
        self.status_var.set(f"{len(names)} Mod(s) im Store gefunden.")
        self.log(f"Store-Mods: {', '.join(names) if names else '(keine)'}")

    def _on_download(self):
        name = self.store_mod_var.get()
        if not name:
            messagebox.showerror(APP_TITLE, "Bitte zuerst 'Store-Mods laden...' ausfuehren und einen Mod waehlen.")
            return
        plugin = next((p for p in self.store_plugins if p["name"] == name), None)
        if plugin is None:
            messagebox.showerror(APP_TITLE, f"Mod '{name}' nicht gefunden.")
            return
        self._set_busy(True)
        self.status_var.set(f"Lade '{name}' herunter...")
        threading.Thread(target=self._download_worker, args=(plugin,), daemon=True).start()

    def _download_worker(self, plugin):
        def report(step, total, message):
            def update():
                self.progress["value"] = (step / total * 100) if total else 0
                self.status_var.set(message)

            self.after(0, update)
            self.log(message)

        try:
            target_dir = download_plugin(
                self.url_var.get(), plugin["name"], plugin.get("files", []), DOWNLOADS_DIR, progress=report
            )
        except Exception as e:
            self.after(0, lambda: self._download_failed(e))
            return
        self.after(0, lambda: self._download_done(target_dir))

    def _download_failed(self, error):
        self._set_busy(False)
        self.status_var.set("Fehler beim Herunterladen.")
        messagebox.showerror(APP_TITLE, str(error))

    def _download_done(self, target_dir):
        self._set_busy(False)
        self.progress["value"] = 100
        self.status_var.set("Download abgeschlossen.")
        messagebox.showinfo(APP_TITLE, f"Mod heruntergeladen nach:\n{target_dir}")


def main():
    app = PluginPackagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
