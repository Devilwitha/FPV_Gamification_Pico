"""
windows/source2/plugin_packager.py

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
    python windows/source2/plugin_packager.py   -> GUI

Als eigenstaendige .exe bauen: siehe windows/build_exe.py - baut in EINEM
Durchlauf sowohl den Gamification Installer (windows/source/) als auch
dieses Tool (--onedir statt --onefile, gleicher Grund wie dort
dokumentiert: --onefile-Selbstentpacker werden von Windows Defenders
Cloud-Heuristik faelschlich als Trojan erkannt). Wird ausserdem automatisch
im GitHub-Actions-Workflow (.github/workflows/build-and-release-firmware.yml,
Job "build-windows-exe") gebaut und an jede Firmware-Release angehaengt -
der Download-Link dafuer steht auf der Webshop-Seite /plugins (siehe
webshop/templates/plugins.html).
"""
import os
import sys
import tempfile
import threading
import tkinter as tk
import zipfile
from tkinter import filedialog

import requests
from kivy.app import App
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget

# Anders als gamification_installer.py (komplett eigenstaendig) braucht
# dieses Tool build_firmware.py/deploy_mod.py aus tools/ (mpy-cross-
# Kompilierung bzw. lokale Mod-Liste, siehe pack_mod_to_zip()) - liegt aber
# selbst unter windows/source2/, daher tools/ explizit auf sys.path setzen
# statt (wie innerhalb von tools/ selbst) das eigene Verzeichnis zu nehmen.
SOURCE2_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SOURCE2_DIR))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
sys.path.insert(0, TOOLS_DIR)
import build_firmware  # noqa: E402
import deploy_mod  # noqa: E402

# windows/kivy_theme.py liegt eine Ebene ueber windows/source2/ - gleiches
# sys.path-Muster wie oben fuer TOOLS_DIR, windows/build_exe.py macht das
# Modul per "--paths" zusaetzlich fuer PyInstallers Import-Analyse sichtbar.
WINDOWS_DIR = os.path.dirname(SOURCE2_DIR)
if WINDOWS_DIR not in sys.path:
    sys.path.insert(0, WINDOWS_DIR)
import kivy_theme as kt  # noqa: E402

APP_TITLE = "Plugin-Store: Paketieren & Hochladen"
DEFAULT_WEBSHOP_URL = "https://bollisoft.ch"
DOWNLOADS_DIR = os.path.join(SOURCE2_DIR, "plugin_downloads")
PACKAGES_DIR = os.path.join(SOURCE2_DIR, "plugin_packages")


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


# ==================== GUI (Kivy, dunkles Theme siehe windows/kivy_theme.py) ====================
#
# Reine GUI-Schicht - pack_mod_to_zip()/webshop_login()/upload_plugin_zip()/
# fetch_store_plugins()/download_plugin() oben in dieser Datei bleiben
# komplett unveraendert. tkinter wird nur noch fuer den nativen "Datei
# oeffnen"-Dialog verwendet (Kivy hat keinen eigenen OS-Dateidialog), siehe
# PluginPackagerApp.build(). UI-Updates aus Worker-Threads laufen ueber
# kivy.clock.mainthread (kt.mainthread) statt tkinter's self.after(0, ...).

class PluginPackagerApp(App):
    def build(self):
        kt.apply_window_theme(APP_TITLE, size=(720, 760), min_size=(640, 620))
        self.title = APP_TITLE

        self.session = requests.Session()
        self.selected_zip_path = None
        self.store_plugins = []
        self._tk_root = tk.Tk()
        self._tk_root.withdraw()

        return self._build_widgets()

    def _build_widgets(self):
        root = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))

        steps = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(14))
        steps.bind(minimum_height=steps.setter("height"))

        step1 = kt.SectionCard(heading="1. Mod-Ordner packen (.py -> .mpy)")
        row1 = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(10))
        mod_values = deploy_mod.list_local_mods()
        self.mod_spinner = kt.ThemedSpinner(text=(mod_values[0] if mod_values else "(keine Mods gefunden)"), values=mod_values)
        row1.add_widget(self.mod_spinner)
        self.pack_button = kt.ThemedButton(text="Packen", variant="primary", size_hint_x=None, width=dp(140))
        self.pack_button.bind(on_release=lambda *_a: self._on_pack())
        row1.add_widget(self.pack_button)
        step1.add_body(row1)
        steps.add_widget(step1)

        step2 = kt.SectionCard(heading="2. ZIP auswaehlen (frisch gepackt oder manuell)")
        self.browse_button = kt.ThemedButton(text="ZIP-Datei auswaehlen...", variant="secondary", size_hint=(None, None), size=(dp(210), dp(38)))
        self.browse_button.bind(on_release=lambda *_a: self._on_browse_zip())
        step2.add_body(self.browse_button)
        self.zip_status_label = kt.BodyLabel("Keine ZIP-Datei ausgewaehlt.")
        step2.add_body(self.zip_status_label)
        steps.add_widget(step2)

        step3 = kt.SectionCard(heading="3. Webshop-Login")
        self.url_input = self._labeled_entry(step3, "URL:", DEFAULT_WEBSHOP_URL)
        self.email_input = self._labeled_entry(step3, "E-Mail:", "")
        self.password_input = self._labeled_entry(step3, "Passwort:", "", password=True)
        steps.add_widget(step3)

        step4 = kt.SectionCard(heading="4. Hochladen / Herunterladen")
        row4 = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(10))
        self.upload_button = kt.ThemedButton(text="Hochladen", variant="primary", size_hint_x=None, width=dp(150))
        self.upload_button.bind(on_release=lambda *_a: self._on_upload())
        row4.add_widget(self.upload_button)
        self.list_button = kt.ThemedButton(text="Store-Mods laden...", variant="secondary", size_hint_x=None, width=dp(180))
        self.list_button.bind(on_release=lambda *_a: self._on_list_store())
        row4.add_widget(self.list_button)
        row4.add_widget(Widget())
        step4.add_body(row4)

        row4b = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(10))
        self.store_mod_spinner = kt.ThemedSpinner(text="(noch keine Mods geladen)", values=[])
        row4b.add_widget(self.store_mod_spinner)
        self.download_button = kt.ThemedButton(text="Herunterladen", variant="secondary", size_hint_x=None, width=dp(150))
        self.download_button.bind(on_release=lambda *_a: self._on_download())
        row4b.add_widget(self.download_button)
        step4.add_body(row4b)
        steps.add_widget(step4)

        progress_card = kt.SectionCard(heading="Fortschritt")
        self.progress = kt.ThemedProgressBar()
        progress_card.add_body(self.progress)
        self.status_label = kt.StatusLabel(text="Bereit.")
        progress_card.add_body(self.status_label)
        steps.add_widget(progress_card)

        root.add_widget(steps)

        log_card = kt.SectionCard(heading="Protokoll", expand=True)
        self.log_panel = kt.LogPanel()
        log_card.add_body(self.log_panel)
        root.add_widget(log_card)

        return root

    def _labeled_entry(self, card, label_text, default, password=False):
        row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(10))
        row.add_widget(kt.StatusLabel(text=label_text, size_hint_x=None, width=dp(90)))
        text_input = kt.ThemedTextInput(text=default, password=password)
        row.add_widget(text_input)
        card.add_body(row)
        return text_input

    def log(self, message):
        self._append_log(message)

    @kt.mainthread
    def _append_log(self, message):
        self.log_panel.write(message)

    def _set_busy(self, busy):
        for widget in (self.pack_button, self.browse_button, self.upload_button, self.list_button, self.download_button):
            widget.disabled = busy

    # ---------- Packen ----------

    def _on_pack(self):
        mod_name = self.mod_spinner.text
        if not mod_name or mod_name not in deploy_mod.list_local_mods():
            kt.show_error(APP_TITLE, "Bitte zuerst einen Mod auswaehlen.")
            return
        self._set_busy(True)
        self.status_label.text = f"Packe '{mod_name}'..."
        threading.Thread(target=self._pack_worker, args=(mod_name,), daemon=True).start()

    def _pack_worker(self, mod_name):
        def report(step, total, message):
            self._report_progress((step / total * 100) if total else 0, message)
            self.log(message)

        output_path = os.path.join(PACKAGES_DIR, f"{mod_name}.zip")
        try:
            pack_mod_to_zip(mod_name, output_path, progress=report)
        except Exception as e:
            self._pack_failed(e)
            return
        self._pack_done(output_path)

    @kt.mainthread
    def _report_progress(self, pct, message):
        self.progress.value = pct
        self.status_label.text = message

    @kt.mainthread
    def _pack_failed(self, error):
        self._set_busy(False)
        self.status_label.text = "Fehler beim Packen."
        kt.show_error(APP_TITLE, str(error))

    @kt.mainthread
    def _pack_done(self, output_path):
        self._set_busy(False)
        self.progress.value = 100
        self.status_label.text = "Paketierung abgeschlossen."
        self.selected_zip_path = output_path
        self.zip_status_label.text = f"Gepackt: {output_path}"
        self.log(f"ZIP erstellt: {output_path}")

    # ---------- ZIP auswaehlen ----------

    def _on_browse_zip(self):
        # Kivy hat keinen nativen Dateidialog - der versteckte Tk-Root aus
        # build() liefert hier den gewohnten Windows-Explorer-Dialog.
        path = filedialog.askopenfilename(
            title="Plugin-ZIP auswaehlen", filetypes=[("ZIP-Datei", "*.zip"), ("Alle Dateien", "*.*")]
        )
        if not path:
            return
        self.selected_zip_path = path
        self.zip_status_label.text = f"Ausgewaehlt: {path}"
        self.log(f"ZIP ausgewaehlt: {path}")

    # ---------- Hochladen ----------

    def _on_upload(self):
        if not self.selected_zip_path:
            kt.show_error(APP_TITLE, "Bitte zuerst eine ZIP-Datei packen oder auswaehlen.")
            return
        if not self.email_input.text or not self.password_input.text:
            kt.show_error(APP_TITLE, "Bitte E-Mail und Passwort fuer den Webshop-Login angeben.")
            return
        self._set_busy(True)
        self.status_label.text = "Logge ein und lade hoch..."
        threading.Thread(target=self._upload_worker, daemon=True).start()

    def _upload_worker(self):
        try:
            webshop_login(self.session, self.url_input.text, self.email_input.text, self.password_input.text)
            self.log("Login erfolgreich.")
            upload_plugin_zip(self.session, self.url_input.text, self.selected_zip_path)
        except Exception as e:
            self._upload_failed(e)
            return
        self._upload_done()

    @kt.mainthread
    def _upload_failed(self, error):
        self._set_busy(False)
        self.status_label.text = "Fehler beim Hochladen."
        kt.show_error(APP_TITLE, str(error))

    @kt.mainthread
    def _upload_done(self):
        self._set_busy(False)
        self.status_label.text = "Hochladen erfolgreich abgeschlossen."
        kt.show_info(APP_TITLE, "Plugin erfolgreich hochgeladen.")

    # ---------- Store-Mods anzeigen / herunterladen ----------

    def _on_list_store(self):
        self._set_busy(True)
        self.status_label.text = "Lade Mod-Liste vom Webshop..."
        threading.Thread(target=self._list_store_worker, daemon=True).start()

    def _list_store_worker(self):
        try:
            plugins = fetch_store_plugins(self.url_input.text)
        except Exception as e:
            self._list_store_failed(e)
            return
        self._list_store_done(plugins)

    @kt.mainthread
    def _list_store_failed(self, error):
        self._set_busy(False)
        self.status_label.text = "Fehler beim Laden der Mod-Liste."
        kt.show_error(APP_TITLE, str(error))

    @kt.mainthread
    def _list_store_done(self, plugins):
        self._set_busy(False)
        self.store_plugins = plugins
        names = [p["name"] for p in plugins]
        self.store_mod_spinner.values = names
        self.store_mod_spinner.text = names[0] if names else "(keine Mods im Store)"
        self.status_label.text = f"{len(names)} Mod(s) im Store gefunden."
        self.log(f"Store-Mods: {', '.join(names) if names else '(keine)'}")

    def _on_download(self):
        name = self.store_mod_spinner.text
        plugin = next((p for p in self.store_plugins if p["name"] == name), None)
        if plugin is None:
            kt.show_error(APP_TITLE, "Bitte zuerst 'Store-Mods laden...' ausfuehren und einen Mod waehlen.")
            return
        self._set_busy(True)
        self.status_label.text = f"Lade '{name}' herunter..."
        threading.Thread(target=self._download_worker, args=(plugin,), daemon=True).start()

    def _download_worker(self, plugin):
        def report(step, total, message):
            self._report_progress((step / total * 100) if total else 0, message)
            self.log(message)

        try:
            target_dir = download_plugin(
                self.url_input.text, plugin["name"], plugin.get("files", []), DOWNLOADS_DIR, progress=report
            )
        except Exception as e:
            self._download_failed(e)
            return
        self._download_done(target_dir)

    @kt.mainthread
    def _download_failed(self, error):
        self._set_busy(False)
        self.status_label.text = "Fehler beim Herunterladen."
        kt.show_error(APP_TITLE, str(error))

    @kt.mainthread
    def _download_done(self, target_dir):
        self._set_busy(False)
        self.progress.value = 100
        self.status_label.text = "Download abgeschlossen."
        kt.show_info(APP_TITLE, f"Mod heruntergeladen nach:\n{target_dir}")


def main():
    PluginPackagerApp().run()


if __name__ == "__main__":
    main()
