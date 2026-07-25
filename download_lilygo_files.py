import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

LILYGO_USB_VID = 0x303A
LILYGO_USB_PID = 0x4001
LILYGO_MARKER = "lilygo.device"
EXPORT_FILES = (
    "fpv_arcade_session.txt",
    "fpv_debug_log.txt",
)


def find_lilygo_port(preferred_port=None):
    ports = list(list_ports.comports())
    if preferred_port:
        for port in ports:
            if port.device.lower() == preferred_port.lower():
                return port.device
        return None

    for port in ports:
        if port.vid == LILYGO_USB_VID and port.pid == LILYGO_USB_PID:
            return port.device
    return None


def enter_raw_repl(port):
    with serial.Serial(port, 115200, timeout=2) as connection:
        connection.write(b"\x03\x03\x01")
        connection.flush()
        connection.read(4096)


def soft_reboot(port):
    try:
        with serial.Serial(port, 115200, timeout=1) as connection:
            connection.write(b"\x04")
            connection.flush()
    except Exception as error:
        print(f"[WARN] LilyGO-Neustart fehlgeschlagen: {error}")


def run_mpremote(port, arguments, check=True):
    command = [
        sys.executable,
        "-m",
        "mpremote",
        "connect",
        port,
        "resume",
    ] + list(arguments)
    return subprocess.run(command, capture_output=True, text=True, check=check)


def verify_lilygo(port):
    cmd = (
        "import os; "
        f"print('LILYGO_OK' if '{LILYGO_MARKER}' in os.listdir() else 'NOT_LILYGO')"
    )
    result = run_mpremote(
        port,
        ["exec", cmd],
        check=False,
    )
    return result.returncode == 0 and "LILYGO_OK" in result.stdout


def list_remote_files(port):
    cmd = "import os; print('FILES:' + '|'.join(os.listdir()))"
    result = run_mpremote(port, ["exec", cmd])
    for line in result.stdout.splitlines():
        if line.startswith("FILES:"):
            return set(line[6:].split("|"))
    return set()


def copy_remote_file(port, remote_name, output_dir):
    destination = output_dir / remote_name
    temp_destination = destination.with_suffix(destination.suffix + ".tmp")
    if temp_destination.exists():
        temp_destination.unlink()

    result = run_mpremote(
        port,
        ["cp", ":" + remote_name, str(temp_destination)],
        check=False,
    )
    if result.returncode != 0:
        if temp_destination.exists():
            temp_destination.unlink()
        details = (
            result.stderr or result.stdout or "Unbekannter Fehler"
        ).strip()
        raise RuntimeError(details)

    if destination.exists():
        destination.unlink()
    temp_destination.replace(destination)
    return destination


def download_exports(port, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] LilyGO gefunden: {port}")
    print(f"[INFO] Zielordner: {output_dir}")

    enter_raw_repl(port)
    try:
        if not verify_lilygo(port):
            raise RuntimeError(f"{port} ist kein eingerichteter FPV-LilyGO.")

        remote_files = list_remote_files(port)
        copied = []
        for filename in EXPORT_FILES:
            if filename not in remote_files:
                print(f"[INFO] Noch nicht vorhanden: {filename}")
                continue
            destination = copy_remote_file(port, filename, output_dir)
            copied.append(destination)
            print(f"[OK] {filename} -> {destination}")

        if not copied:
            print("[INFO] Keine Exportdateien auf dem LilyGO gefunden.")
        return copied
    finally:
        soft_reboot(port)


def main():
    print("=== SKRIPT START (VERSION SKRIPT-ORDNER) ===")

    # Pfad des Ordners ermitteln, in dem DIESE .py Datei liegt
    script_directory = Path(__file__).resolve().parent
    default_dir = script_directory / "FPV_LilyGO"

    parser = argparse.ArgumentParser(
        description="Laedt FPV-Session- und Debugdateien seriell vom LilyGO herunter."
    )
    parser.add_argument(
        "--port",
        help="Optionaler COM-Port, z.B. COM14",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_dir,
        help="Lokaler Zielordner (Standard: FPV_LilyGO im Skriptordner)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Dauerhaft auf neue LilyGO-Verbindungen warten",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Suchintervall im Watch-Modus",
    )
    args = parser.parse_args()

    target_output_dir = args.output.resolve()

    last_port = None
    while True:
        port = find_lilygo_port(args.port)
        if port and port != last_port:
            try:
                download_exports(port, target_output_dir)
            except Exception as error:
                print(f"[FEHLER] {error}")
            last_port = port
        elif not port:
            last_port = None

        if not args.watch:
            if not port:
                print(
                    "[FEHLER] Kein LilyGO gefunden. USB verbinden und erneut starten."
                )
                return 1
            return 0

        time.sleep(max(0.5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())