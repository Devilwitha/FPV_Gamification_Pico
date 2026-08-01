"""
build_exe.py

Baut aus source/gamification_installer.py mit PyInstaller eine
eigenstaendige Windows-.exe ("Gamification Installer.exe") - eine einzelne
Datei ohne Konsolenfenster, die auf einem Windows-PC ganz ohne separate
Python-Installation laeuft.

Nutzung:
    pip install -r requirements.txt
    python build_exe.py

Das fertige Programm liegt danach unter windows/dist/Gamification Installer.exe
"""
import os
import sys

WINDOWS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(WINDOWS_DIR)
SOURCE_SCRIPT = os.path.join(WINDOWS_DIR, "source", "gamification_installer.py")
DIST_DIR = os.path.join(WINDOWS_DIR, "dist")
BUILD_DIR = os.path.join(WINDOWS_DIR, "build_tmp")
SPEC_DIR = os.path.join(WINDOWS_DIR, "spec_tmp")
APP_NAME = "Gamification Installer"
ICON_PATH = os.path.join(WINDOWS_DIR, "icon.ico")
# picofw/ enthaelt die UF2-Dateien (MicroPython + Nuke) fuer den
# Bootloader-Flash (siehe gamification_installer.py's flash_bootsel_pico())
# - wird als Datenordner mit in die .exe gepackt, damit die Funktion auch
# ohne Internetzugriff/separates Repo-Checkout funktioniert.
PICOFW_DIR = os.path.join(PROJECT_ROOT, "picofw")


def main():
    try:
        import PyInstaller.__main__
    except ImportError:
        print("PyInstaller ist nicht installiert. Bitte zuerst ausfuehren:")
        print(f"    pip install -r {os.path.join(WINDOWS_DIR, 'requirements.txt')}")
        sys.exit(1)

    if not os.path.isfile(SOURCE_SCRIPT):
        print(f"Quelldatei nicht gefunden: {SOURCE_SCRIPT}")
        sys.exit(1)

    args = [
        SOURCE_SCRIPT,
        "--name", APP_NAME,
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        # UPX-Packing macht die .exe kleiner, laesst sie aber bei Virenscannern
        # und Browser-Downloadschutz (Chrome/Edge/SmartScreen) deutlich
        # oefter als Malware anschlagen, da UPX auch von echter Malware zur
        # Verschleierung genutzt wird - --noupx vermeidet diese False Positives.
        "--noupx",
        "--distpath", DIST_DIR,
        "--workpath", BUILD_DIR,
        "--specpath", SPEC_DIR,
    ]
    if os.path.isfile(ICON_PATH):
        args += ["--icon", ICON_PATH]

    if os.path.isdir(PICOFW_DIR):
        # Windows-Trennzeichen fuer --add-data ist ';' (SRC;ZIEL-IM-BUNDLE).
        args += ["--add-data", f"{PICOFW_DIR};picofw"]
    else:
        print(f"WARNUNG: {PICOFW_DIR} nicht gefunden - Bootloader-Flash-Funktion wird in der .exe nicht funktionieren.")

    print(f"Baue '{APP_NAME}.exe' mit PyInstaller ...")
    PyInstaller.__main__.run(args)

    exe_path = os.path.join(DIST_DIR, f"{APP_NAME}.exe")
    print()
    if os.path.isfile(exe_path):
        print(f"Fertig: {exe_path}")
    else:
        print("Build abgeschlossen, aber die .exe wurde nicht am erwarteten Pfad gefunden.")
        print(f"Bitte {DIST_DIR} pruefen.")


if __name__ == "__main__":
    main()
