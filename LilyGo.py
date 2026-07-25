#!/usr/bin/env python3
import os
import sys
import time
import urllib.request
import subprocess
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

# Auto-Installations-Helfer für benötigte Python-Pakete
def install_requirements():
    required = {
        "esptool": "esptool",
        "pyserial": "serial",
        "mpremote": "mpremote",
    }
    for package, module_name in required.items():
        try:
            __import__(module_name)
        except ImportError:
            print(f"[*] Installiere benötigtes Paket: {package}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])

install_requirements()

import serial
import serial.tools.list_ports

# LilyGO USB IDs
LILYGO_USB_VID = 0x303A
LILYGO_USB_PID = 0x4001

# Chip-Konfigurationen
CHIP_CONFIGS = {
    "ESP32 (Standard / TTGO / T-Beam / T-Display)": {
        "chip": "esp32",
        "offset": "0x1000",
        "url": "https://micropython.org/resources/firmware/ESP32_GENERIC-20240222-v1.22.2.bin"
    },
    "ESP32-S3 (z.B. T-Display-S3, T-Watch-S3, T3-S3)": {
        "chip": "esp32s3",
        "offset": "0x0",
        "url": "https://micropython.org/resources/firmware/ESP32_GENERIC_S3-20240222-v1.22.2.bin"
    },
    "ESP32-C3 (z.B. T-01C3)": {
        "chip": "esp32c3",
        "offset": "0x0",
        "url": "https://micropython.org/resources/firmware/ESP32_GENERIC_C3-20240222-v1.22.2.bin"
    }
}

def find_current_lilygo_port(preferred_port=None):
    """Sucht den aktuell gültigen COM-Port für das Board."""
    ports = list(serial.tools.list_ports.comports())
    
    # 1. Bevorzugten Port prüfen
    if preferred_port:
        for p in ports:
            if p.device.upper() == preferred_port.upper():
                return p.device
                
    # 2. Per VID/PID suchen
    for p in ports:
        if p.vid == LILYGO_USB_VID and p.pid == LILYGO_USB_PID:
            return p.device
            
    # 3. Erste verfügbare Schnittstelle als Fallback
    if ports:
        return ports[0].device
    return None

def enter_bootloader_cdc(port):
    """Trigger für 1200-Baud-Reset bei ESP32-S3 CDC."""
    try:
        ser = serial.Serial(port, 1200)
        ser.dtr = False
        ser.rts = True
        time.sleep(0.1)
        ser.dtr = True
        ser.rts = False
        time.sleep(0.1)
        ser.close()
        time.sleep(1.0)
    except Exception:
        pass

class LilyGoInstallerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("LilyGO / ESP32 Flasher & Manager")
        self.root.geometry("640x600")
        self.root.resizable(False, False)

        # Thread-sichere Queue für Log-Nachrichten
        self.log_queue = queue.Queue()

        self.setup_ui()
        self.check_queue()
        
        # Schnelle Auto-Detect beim Start
        self.auto_detect_port()

    def setup_ui(self):
        # Header Frame
        header_frame = ttk.Frame(self.root, padding="10")
        header_frame.pack(fill="x")
        ttk.Label(
            header_frame, 
            text="LilyGO / ESP32 Flasher & Manager", 
            font=("Helvetica", 16, "bold")
        ).pack()

        # Selection Frame
        selection_frame = ttk.LabelFrame(self.root, text=" Hardware-Auswahl ", padding="10")
        selection_frame.pack(fill="x", padx=10, pady=5)

        # Port Selection
        ttk.Label(selection_frame, text="Serieller Port:").grid(row=0, column=0, sticky="w", pady=5)
        self.port_cb = ttk.Combobox(selection_frame, state="readonly", width=38)
        self.port_cb.grid(row=0, column=1, padx=5, pady=5)
        
        self.btn_auto_test = ttk.Button(
            selection_frame, 
            text="Port suchen", 
            command=self.auto_detect_port
        )
        self.btn_auto_test.grid(row=0, column=2, padx=5, pady=5)

        # Chip Selection
        ttk.Label(selection_frame, text="Chip-Typ:").grid(row=1, column=0, sticky="w", pady=5)
        self.chip_cb = ttk.Combobox(
            selection_frame, 
            values=list(CHIP_CONFIGS.keys()), 
            state="readonly", 
            width=38
        )
        self.chip_cb.grid(row=1, column=1, padx=5, pady=5)
        self.chip_cb.current(1)  # Default: ESP32-S3

        # Actions Frame
        self.actions_frame = ttk.LabelFrame(self.root, text=" Aktionen ", padding="10")
        self.actions_frame.pack(fill="x", padx=10, pady=5)

        self.btn_flash = ttk.Button(
            self.actions_frame, 
            text="1. MicroPython Flashen & App Installieren", 
            command=self.start_flash_process
        )
        self.btn_flash.pack(fill="x", pady=4)

        self.btn_delete_boot = ttk.Button(
            self.actions_frame, 
            text="2. boot.py vom Board löschen", 
            command=lambda: self.run_in_thread(self.delete_boot_py_worker)
        )
        self.btn_delete_boot.pack(fill="x", pady=4)

        self.btn_download = ttk.Button(
            self.actions_frame, 
            text="3. Datei vom Board auswählen & herunterladen", 
            command=self.start_download_process
        )
        self.btn_download.pack(fill="x", pady=4)

        # Console Output
        console_frame = ttk.LabelFrame(self.root, text=" Log / Ausgabe ", padding="10")
        console_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.log_text = tk.Text(
            console_frame, 
            height=12, 
            state="disabled", 
            bg="#1e1e1e", 
            fg="#00ff00", 
            font=("Consolas", 9)
        )
        self.log_text.pack(fill="both", expand=True)

    def log(self, message):
        """Sendet Log-Nachrichten in die Queue (Thread-sicher)."""
        self.log_queue.put(message)

    def check_queue(self):
        """Liest regelmäßig die Log-Queue aus und aktualisiert die UI."""
        while not self.log_queue.empty():
            message = self.log_queue.get()
            self.log_text.config(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.root.after(100, self.check_queue)

    def set_ui_state(self, enabled=True):
        """Aktiviert oder deaktiviert die Buttons während Hintergrundaktionen."""
        state = "normal" if enabled else "disabled"
        self.btn_flash.config(state=state)
        self.btn_delete_boot.config(state=state)
        self.btn_download.config(state=state)
        self.btn_auto_test.config(state=state)

    def run_in_thread(self, target_func, *args):
        """Startet eine Funktion im Hintergrund, um Freezes zu vermeiden."""
        self.set_ui_state(False)
        def thread_target():
            try:
                target_func(*args)
            finally:
                self.root.after(0, lambda: self.set_ui_state(True))
        
        threading.Thread(target=thread_target, daemon=True).start()

    def auto_detect_port(self):
        """Sucht nach verfügbaren COM-Ports."""
        self.log("[*] Suche serielle Ports...")
        ports = list(serial.tools.list_ports.comports())
        port_list = [p.device for p in ports]
        
        self.port_cb.config(values=port_list)
        
        found_port = find_current_lilygo_port()

        if found_port:
            self.port_cb.set(found_port)
            self.log(f"[✓] Port erkannt an: {found_port}")
        else:
            self.port_cb.set("")
            self.log("[!] Kein aktiver COM-Port gefunden.")

    def get_selected_port(self):
        port = self.port_cb.get()
        if not port:
            messagebox.showerror("Fehler", "Bitte wähle einen seriellen Port aus.")
            return None
        return port

    def start_flash_process(self):
        port = self.get_selected_port()
        if not port:
            return

        cfg = CHIP_CONFIGS[self.chip_cb.get()]
        if not messagebox.askyesno("Bestätigung", "Soll der Flash-Vorgang wirklich gestartet werden?\nAlle Daten auf dem ESP32 werden gelöscht!"):
            return

        self.run_in_thread(self.flash_and_install_worker, port, cfg)

    def flash_and_install_worker(self, initial_port, cfg):
        firmware_file = f"firmware_{cfg['chip']}.bin"

        # 1. Download Firmware
        self.log(f"[*] Lade Firmware herunter: {cfg['url']}")
        try:
            urllib.request.urlretrieve(cfg['url'], firmware_file)
            self.log("[*] Download der Firmware erfolgreich.")
        except Exception as e:
            self.log(f"[!] Fehler beim Download: {e}")
            return

        # 2. Reset-Puls senden
        self.log("[*] Bereite Bootloader-Modus vor...")
        enter_bootloader_cdc(initial_port)
        
        # Port neu ermitteln, falls Windows die COM-Nummer neu vergeben hat
        active_port = find_current_lilygo_port(initial_port)
        if not active_port:
            self.log("[!] Port nach Reset nicht gefunden.")
            self.root.after(0, lambda: messagebox.showerror("Fehler", "COM-Port ist verschwunden. Bitte Board neu anstecken."))
            return

        # 3. Erase Flash (esptool v5 Syntax: erase-flash)
        self.log(f"\n[*] SCHRITT 1: Lösche Flash-Speicher an {active_port}...")
        erase_cmd = [
            sys.executable, "-m", "esptool",
            "--chip", cfg['chip'],
            "--port", active_port,
            "erase-flash"
        ]
        res = subprocess.run(erase_cmd)
        if res.returncode != 0:
            self.log("[!] Automatischer Connect fehlgeschlagen.")
            self.log("--------------------------------------------------")
            self.log("[HINWEIS FORTSCHRITT]:")
            self.log("1. Halte die 'BOOT'-Taste auf deinem LilyGO gedrückt.")
            self.log("2. Drücke kurz die 'RESET'-Taste (oder USB neu einstecken).")
            self.log("3. Lasse 'BOOT' los und klicke erneut auf Flashen.")
            self.log("--------------------------------------------------")
            self.root.after(0, lambda: messagebox.showerror("Boot-Modus erforderlich", "Verbindung fehlgeschlagen. Bitte BOOT-Taste beim Anstecken gedrückt halten!"))
            return

        time.sleep(1.5)
        active_port = find_current_lilygo_port(active_port)

        # 4. Write Firmware (esptool v5 Syntax: write-flash)
        self.log(f"\n[*] SCHRITT 2: Flashe MicroPython Firmware an {active_port}...")
        flash_cmd = [
            sys.executable, "-m", "esptool",
            "--chip", cfg['chip'],
            "--port", active_port,
            "--baud", "460800",
            "write-flash",
            "-z",
            cfg['offset'],
            firmware_file
        ]
        res = subprocess.run(flash_cmd)
        if res.returncode != 0:
            self.log("[!] Fehler beim Schreiben der Firmware!")
            self.root.after(0, lambda: messagebox.showerror("Fehler", "Firmware konnte nicht geschrieben werden."))
            return

        # 5. Warten bis MicroPython hochfährt
        self.log("\n[*] SCHRITT 3: Warte auf MicroPython Start...")
        time.sleep(4)
        active_port = find_current_lilygo_port(active_port)

        source_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "source")
        files = [
            "boot_runtime.py",
            "hotspot_common.py",
            "hotspot.conf",
            "main_LilyGo.py",
            "lilygo.device",
            "boot.py",
        ]

        success = True
        for filename in files:
            source_path = os.path.join(source_dir, filename)
            if not os.path.isfile(source_path):
                self.log(f"[!] Quelldatei fehlt: {source_path}")
                success = False
                break

            cmd = [
                sys.executable, "-m", "mpremote", "connect", active_port,
                "cp", source_path, ":" + filename,
            ]
            res = subprocess.run(cmd)
            if res.returncode != 0:
                self.log(f"[!] Übertragungsfehler: {filename}")
                success = False
                break
            self.log(f"  -> {filename} installiert")

        if success:
            subprocess.run([
                sys.executable, "-m", "mpremote", "connect", active_port,
                "exec", "import machine; machine.reset()"
            ])
            self.log("\n[=== ERFOLGREICH ABGESCHLOSSEN ===]")
            self.root.after(0, lambda: messagebox.showinfo("Erfolg", "MicroPython & Anwendung wurden erfolgreich installiert!"))
        else:
            self.root.after(0, lambda: messagebox.showwarning("Warnung", "Flashing war erfolgreich, aber Dateien wurden unvollständig übertragen."))

        if os.path.exists(firmware_file):
            os.remove(firmware_file)

    def delete_boot_py_worker(self):
        port = self.get_selected_port()
        if not port:
            return

        confirm = messagebox.askyesno("Löschen bestätigen", "Möchtest du die boot.py wirklich vom Board entfernen?")
        if not confirm:
            return

        self.log("\n[*] Stoppe laufende Skripte per REPL...")
        try:
            with serial.Serial(port, 115200, timeout=1) as connection:
                connection.write(b"\x03\x03")
                connection.flush()
                time.sleep(0.3)
        except Exception:
            pass

        self.log("[*] Lösche boot.py vom Gerät...")
        cmd = [
            sys.executable, "-m", "mpremote", "connect", port,
            "rm", ":boot.py"
        ]
        res = subprocess.run(cmd)
        if res.returncode == 0:
            self.log("[*] boot.py wurde erfolgreich gelöscht.")
            self.root.after(0, lambda: messagebox.showinfo("Erfolg", "boot.py wurde gelöscht."))
        else:
            self.log("[!] Fehler beim Löschen von boot.py.")
            self.root.after(0, lambda: messagebox.showerror("Fehler", "Konnte boot.py nicht löschen."))

    def start_download_process(self):
        port = self.get_selected_port()
        if not port:
            return

        self.run_in_thread(self.fetch_file_list_worker, port)

    def fetch_file_list_worker(self, port):
        """Sucht per mpremote alle vorhandenen Dateien auf dem Board."""
        self.log("\n[*] Sende Unterbrechung (Strg+C) an das Board...")
        try:
            with serial.Serial(port, 115200, timeout=1) as connection:
                connection.write(b"\x03\x03")
                connection.flush()
                time.sleep(0.3)
        except Exception as e:
            self.log(f"[WARN] Vorbereitung fehlgeschlagen: {e}")

        self.log("[*] Lese Dateiliste vom ESP32...")
        cmd = [
            sys.executable, "-m", "mpremote", "connect", port,
            "fs", "ls"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            self.log("[!] Fehler beim Abrufen der Dateiliste vom Board.")
            self.root.after(0, lambda: messagebox.showerror("Fehler", "Konnte Dateiliste nicht auslesen."))
            return

        raw_lines = res.stdout.splitlines()
        found_files = []
        for line in raw_lines:
            line = line.strip()
            if line and not line.startswith("ls :"):
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    found_files.append(parts[1])
                elif len(parts) == 1 and not parts[0].isdigit():
                    found_files.append(parts[0])

        if not found_files:
            self.log("[!] Keine Dateien auf dem Board gefunden.")
            self.root.after(0, lambda: messagebox.showinfo("Info", "Keine Dateien auf dem Board vorhanden."))
            return

        self.log(f"[*] Gefundene Dateien: {', '.join(found_files)}")
        self.root.after(0, lambda: self.show_file_selection_dialog(port, found_files))

    def show_file_selection_dialog(self, port, files):
        """Öffnet ein Fenster zur Auswahl der herunterzuladenden Datei."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Datei auswählen")
        dlg.geometry("350x300")
        dlg.transient(self.root)
        dlg.grab_set()

        ttk.Label(dlg, text="Dateien auf dem ESP32:", font=("Helvetica", 10, "bold")).pack(pady=10)

        listbox = tk.Listbox(dlg, selectmode="single", font=("Consolas", 10))
        listbox.pack(fill="both", expand=True, padx=10, pady=5)

        for f in files:
            listbox.insert("end", f)

        listbox.selection_set(0)

        def on_download():
            selected = listbox.curselection()
            if not selected:
                return
            remote_filename = files[selected[0]]
            dlg.destroy()

            save_path = filedialog.asksaveasfilename(
                title="Speicherort wählen",
                initialfile=remote_filename
            )
            if save_path:
                self.run_in_thread(self.download_file_worker, port, remote_filename, save_path)

        btn_frame = ttk.Frame(dlg, padding="10")
        btn_frame.pack(fill="x")

        ttk.Button(btn_frame, text="Herunterladen", command=on_download).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Abbrechen", command=dlg.destroy).pack(side="right", padx=5)

    def download_file_worker(self, port, remote_filename, save_path):
        self.log(f"[*] Lade '{remote_filename}' herunter...")
        cmd = [
            sys.executable, "-m", "mpremote", "connect", port,
            "cp", ":" + remote_filename, save_path
        ]
        res = subprocess.run(cmd)
        
        if res.returncode == 0:
            self.log(f"[*] Erfolgreich gespeichert unter: {save_path}")
            self.root.after(0, lambda: messagebox.showinfo("Erfolg", f"Datei '{remote_filename}' wurde erfolgreich heruntergeladen."))
        else:
            self.log(f"[!] Fehler beim Herunterladen von {remote_filename}.")
            self.root.after(0, lambda: messagebox.showerror("Fehler", "Konnte die Datei nicht herunterladen."))


def main():
    root = tk.Tk()
    app = LilyGoInstallerGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()