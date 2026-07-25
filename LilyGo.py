#!/usr/bin/env python3
import os
import sys
import time
import urllib.request
import json
import subprocess

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

import serial.tools.list_ports
import esptool

CHIP_CONFIGS = {
    "1": {
        "name": "ESP32 (Standard / TTGO / T-Beam / T-Display)",
        "chip": "esp32",
        "offset": "0x1000",
        "url_search": "ESP32_GENERIC"
    },
    "2": {
        "name": "ESP32-S3 (z.B. T-Display-S3, T-Watch-S3, T3-S3)",
        "chip": "esp32s3",
        "offset": "0x0",
        "url_search": "ESP32_GENERIC_S3"
    },
    "3": {
        "name": "ESP32-C3 (z.B. T-01C3)",
        "chip": "esp32c3",
        "offset": "0x0",
        "url_search": "ESP32_GENERIC_C3"
    }
}

def get_serial_ports():
    ports = serial.tools.list_ports.comports()
    return [port.device for port in ports]

def select_port():
    ports = get_serial_ports()
    if not ports:
        print("[!] KEIN SERIELLER PORT GEFUNDEN!")
        print("    Stelle sicher, dass dein LilyGO-Board per USB angeschlossen ist.")
        sys.exit(1)
    
    if len(ports) == 1:
        print(f"[*] Automatischer Port gewählt: {ports[0]}")
        return ports[0]
    
    print("\nVerfügbare Ports:")
    for idx, port in enumerate(ports):
        print(f"  [{idx + 1}] {port}")
    
    choice = input("\nBitte wähle den Port deines Boards (Zahl): ").strip()
    try:
        return ports[int(choice) - 1]
    except (IndexError, ValueError):
        print("[!] Ungültige Auswahl.")
        sys.exit(1)

def get_latest_firmware_url(board_type):
    print("[*] Suche nach der neuesten MicroPython-Firmware...")
    
    # Bekannte direkte Download-Fallbacks für Stabilität
    downloads = {
        "esp32": "https://micropython.org/resources/firmware/ESP32_GENERIC-20240222-v1.22.2.bin",
        "esp32s3": "https://micropython.org/resources/firmware/ESP32_GENERIC_S3-20240222-v1.22.2.bin",
        "esp32c3": "https://micropython.org/resources/firmware/ESP32_GENERIC_C3-20240222-v1.22.2.bin"
    }
    return downloads.get(board_type)

def download_file(url, destination):
    print(f"[*] Lade Firmware herunter: {url}")
    try:
        urllib.request.urlretrieve(url, destination)
        print("[*] Download erfolgreich abgeschlossen.")
    except Exception as e:
        print(f"[!] Fehler beim Download: {e}")
        sys.exit(1)

def provision_lilygo(port):
    source_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "source")
    files = ["boot_runtime.py", "main_LilyGo.py", "lilygo.device", "boot.py"]
    print("\n[*] SCHRITT 3: Installiere LilyGO-Anwendung...")
    time.sleep(3)

    for filename in files:
        source_path = os.path.join(source_dir, filename)
        if not os.path.isfile(source_path):
            print(f"[!] Quelldatei fehlt: {source_path}")
            return False
        command = [
            sys.executable, "-m", "mpremote", "connect", port,
            "cp", source_path, ":" + filename,
        ]
        result = subprocess.run(command)
        if result.returncode != 0:
            print(f"[!] LilyGO-Datei konnte nicht übertragen werden: {filename}")
            return False
        print(f"    {filename} installiert")

    subprocess.run([
        sys.executable, "-m", "mpremote", "connect", port,
        "exec", "import machine; machine.reset()",
    ])
    return True

def main():
    print("==================================================")
    print("  LilyGO / ESP32 MicroPython Auto-Installer")
    print("==================================================\n")

    port = select_port()

    print("\nWähle den Chip-Typ deines LilyGO Boards:")
    for key, cfg in CHIP_CONFIGS.items():
        print(f"  [{key}] {cfg['name']}")
    
    chip_choice = input("\nAuswahl (1-3): ").strip()
    if chip_choice not in CHIP_CONFIGS:
        print("[!] Ungültige Auswahl.")
        sys.exit(1)

    selected_cfg = CHIP_CONFIGS[chip_choice]
    firmware_filename = f"firmware_{selected_cfg['chip']}.bin"

    # Firmware Download
    firmware_url = get_latest_firmware_url(selected_cfg['chip'])
    download_file(firmware_url, firmware_filename)

    print("\n--------------------------------------------------")
    print(f"Port:       {port}")
    print(f"Chip:       {selected_cfg['chip']}")
    print(f"Offset:     {selected_cfg['offset']}")
    print("--------------------------------------------------")
    
    confirm = input("\nSoll der Flash-Vorgang JETZT gestartet werden? (y/n): ").strip().lower()
    if confirm != 'y':
        print("[*] Vorgang abgebrochen.")
        sys.exit(0)

    # Step 1: Flash löschen
    print("\n[*] SCHRITT 1: Lösche existierenden Flash-Speicher...")
    erase_cmd = [
        sys.executable, "-m", "esptool",
        "--chip", selected_cfg['chip'],
        "--port", port,
        "erase_flash"
    ]
    
    res = subprocess.run(erase_cmd)
    if res.returncode != 0:
        print("[!] Fehler beim Löschen des Flashs!")
        print("    Tipp: Halte die 'BOOT'-Taste auf deinem Board gedrückt und versuche es erneut.")
        sys.exit(1)

    time.sleep(2)

    # Step 2: MicroPython flashen
    print("\n[*] SCHRITT 2: Flashe MicroPython Firmware...")
    flash_cmd = [
        sys.executable, "-m", "esptool",
        "--chip", selected_cfg['chip'],
        "--port", port,
        "--baud", "460800",
        "write_flash",
        "-z",
        selected_cfg['offset'],
        firmware_filename
    ]

    res = subprocess.run(flash_cmd)
    if res.returncode != 0:
        print("[!] Fehler beim Schreiben der Firmware!")
        sys.exit(1)

    if not provision_lilygo(port):
        print("[!] MicroPython wurde installiert, aber die LilyGO-App nicht vollständig übertragen.")
        sys.exit(1)

    # Cleanup
    if os.path.exists(firmware_filename):
        os.remove(firmware_filename)

    print("\n==================================================")
    print("  ERFOLGREICH! LilyGO-App ist installiert.")
    print("  Das Display sucht jetzt automatisch den Pico-Hotspot.")
    print("==================================================")

if __name__ == "__main__":
    main()