"""
tools/deploy_mod.py

Eigenstaendiges Entwickler-CLI: ueberträgt einen einzelnen Mod-Ordner aus
source/mods/<name>/ (manifest.json + main.py, siehe source/plugin_manager.py
und template/README.md) auf einen Pico, unter mods/<name>/ - ohne einen
kompletten Firmware-Reflash zu benoetigen.

Zwei Uebertragungsarten:

- --mode serial (Standard): ueber mpremote/USB. Baut auf denselben, bereits
  produktiv genutzten mpremote-Subprocess-Helfern aus build_firmware.py auf
  (Port-Autoerkennung, Raw-REPL-Vorbereitung, Retry-Logik) statt sie neu zu
  implementieren - gleiches Vorbild wie tools/license_uploader.py.
- --mode wifi: ueber webrepl_cli. Setzt voraus, dass WebREPL auf dem
  Ziel-Pico bereits EINMALIG manuell aktiviert wurde (in der REPL:
  `import webrepl; webrepl.start()`) - dieses Projekt startet WebREPL nicht
  automatisch im Boot-Pfad (siehe source/boot.py), um keinen zusaetzlichen,
  standardmaessig unauthentifizierten Netzwerkdienst zu exponieren.
  Ausserdem kann webrepl_cli KEINE Verzeichnisse anlegen - mods/<name>/ muss
  auf dem Ziel-Pico bereits existieren (z.B. einmalig per --mode serial
  oder ueber den Webshop-Store-Download auf dem Pico selbst angelegt).

Nutzung:
    python tools/deploy_mod.py                                  -> interaktives Terminal-Menue
    python tools/deploy_mod.py --mod shooter --mode serial
    python tools/deploy_mod.py --mod shooter --mode serial --port COM5
    python tools/deploy_mod.py --mod shooter --mode wifi --host 192.168.4.1 --password geheim123
"""
import argparse
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_firmware  # noqa: E402

MODS_SOURCE_DIR = os.path.join(build_firmware.SOURCE_DIR, "mods")


def list_local_mods():
    """Alle Unterordner von source/mods/, die eine manifest.json besitzen."""
    names = []
    try:
        for entry in sorted(os.listdir(MODS_SOURCE_DIR)):
            manifest_path = os.path.join(MODS_SOURCE_DIR, entry, "manifest.json")
            if os.path.isfile(manifest_path):
                names.append(entry)
    except FileNotFoundError:
        pass
    return names


def _mod_files(mod_name):
    """Alle Dateien (flach, keine Unterordner) im lokalen Mod-Ordner."""
    mod_dir = os.path.join(MODS_SOURCE_DIR, mod_name)
    if not os.path.isdir(mod_dir):
        return []
    return sorted(
        filename
        for filename in os.listdir(mod_dir)
        if os.path.isfile(os.path.join(mod_dir, filename))
    )


def deploy_via_serial(mod_name, port=None, log=print):
    """Kopiert einen Mod-Ordner per mpremote/USB nach :mods/<mod_name>/."""
    mod_dir = os.path.join(MODS_SOURCE_DIR, mod_name)
    files = _mod_files(mod_name)
    if not files:
        raise Exception(f"Mod-Ordner '{mod_name}' ist leer oder existiert nicht: {mod_dir}")

    mpremote_cmd = build_firmware._resolve_mpremote_command()

    if port is None:
        log("Suche Pico ueber USB-Seriell...")
        ports = build_firmware.auto_detect_pico_ports(mpremote_cmd)
        if not ports:
            raise Exception(
                "Kein Pico-COM-Port gefunden. Bitte USB neu verbinden oder den Port explizit mit --port angeben."
            )
        port = ports[0]
        log(f"Verwende Port: {port}")

    build_firmware.ensure_device_raw_repl_ready(mpremote_cmd, port)

    for remote_dir in (":mods", f":mods/{mod_name}"):
        try:
            build_firmware._run_mpremote(mpremote_cmd, ["connect", port, "mkdir", remote_dir], timeout=15)
        except Exception as e:
            # Best effort: schlaegt fehl, wenn der Ordner bereits existiert -
            # das ist der Normalfall bei einem erneuten Deploy, kein Fehler.
            build_firmware._debug(f"deploy_mod: mkdir {remote_dir} uebersprungen: {e}")

    for filename in files:
        local_path = os.path.join(mod_dir, filename)
        remote_path = f":mods/{mod_name}/{filename}"
        log(f"Kopiere {filename} -> {remote_path}")
        build_firmware._run_mpremote(
            mpremote_cmd,
            ["connect", port, "cp", local_path, remote_path],
            timeout=30, retries=2, retry_delay=2.0,
        )

    log(
        f"Mod '{mod_name}' erfolgreich uebertragen ({len(files)} Datei(en)). "
        "Ein Neustart des Pico laedt es beim naechsten Boot automatisch."
    )


def deploy_via_wifi(mod_name, host, password, log=print):
    """Kopiert einen Mod-Ordner per webrepl_cli/WLAN nach mods/<mod_name>/ -
    siehe Modul-Docstring fuer die Voraussetzungen (WebREPL aktiviert,
    Zielordner existiert bereits)."""
    mod_dir = os.path.join(MODS_SOURCE_DIR, mod_name)
    files = _mod_files(mod_name)
    if not files:
        raise Exception(f"Mod-Ordner '{mod_name}' ist leer oder existiert nicht: {mod_dir}")

    webrepl_cli = shutil.which("webrepl_cli") or shutil.which("webrepl_cli.py")
    if not webrepl_cli:
        raise Exception(
            "webrepl_cli wurde nicht im PATH gefunden. Installation z.B. ueber das "
            "offizielle MicroPython webrepl-Repository (webrepl_cli.py)."
        )

    for filename in files:
        local_path = os.path.join(mod_dir, filename)
        remote_target = f"{host}:mods/{mod_name}/{filename}"
        log(f"Kopiere {filename} -> {remote_target}")
        result = subprocess.run(
            [webrepl_cli, "-p", password, local_path, remote_target],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise Exception(
                f"webrepl_cli fehlgeschlagen fuer {filename}: {(result.stderr or result.stdout).strip()}\n"
                f"Hinweis: mods/{mod_name}/ muss auf dem Pico bereits existieren "
                "(z.B. einmalig per --mode serial anlegen)."
            )

    log(f"Mod '{mod_name}' erfolgreich per WLAN uebertragen ({len(files)} Datei(en)).")


def _prompt_choice(options, prompt):
    for idx, option in enumerate(options, start=1):
        print(f"  {idx}. {option}")
    while True:
        raw = input(f"{prompt}: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("Ungueltige Auswahl, bitte erneut versuchen.")


def _interactive_menu():
    mods = list_local_mods()
    if not mods:
        raise SystemExit(f"Keine Mods gefunden unter {MODS_SOURCE_DIR}")
    print("Verfuegbare Mods:")
    mod_name = _prompt_choice(mods, "Mod waehlen (Nummer)")
    print("Uebertragungsart:")
    mode = _prompt_choice(["serial", "wifi"], "Modus waehlen (Nummer)")
    return mod_name, mode


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Ueberträgt einen Mod-Ordner aus source/mods/ auf einen Pico."
    )
    parser.add_argument("--mod", help="Name des Mod-Ordners unter source/mods/ (z.B. 'shooter').")
    parser.add_argument("--mode", choices=["serial", "wifi"], help="Uebertragungsart.")
    parser.add_argument("--port", help="Serieller COM-Port (nur --mode serial, sonst automatische Suche).")
    parser.add_argument("--host", help="Pico-IP/-Hostname (nur --mode wifi, z.B. 192.168.4.1).")
    parser.add_argument("--password", help="WebREPL-Passwort (nur --mode wifi).")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    mod_name = args.mod
    mode = args.mode
    if not mod_name or not mode:
        interactive_mod, interactive_mode = _interactive_menu()
        mod_name = mod_name or interactive_mod
        mode = mode or interactive_mode

    if mod_name not in list_local_mods():
        print(f"Fehler: Mod '{mod_name}' nicht gefunden unter {MODS_SOURCE_DIR}", file=sys.stderr)
        return 1

    try:
        if mode == "serial":
            deploy_via_serial(mod_name, port=args.port)
        else:
            host = args.host or input("Pico-IP/-Hostname (z.B. 192.168.4.1): ").strip()
            password = args.password or input("WebREPL-Passwort: ").strip()
            if not host or not password:
                print("Fehler: Host und Passwort sind fuer --mode wifi erforderlich.", file=sys.stderr)
                return 1
            deploy_via_wifi(mod_name, host, password)
    except Exception as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
