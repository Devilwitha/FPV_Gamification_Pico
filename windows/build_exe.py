"""
build_exe.py

Baut aus source/gamification_installer.py mit PyInstaller einen
eigenstaendigen Windows-Installer ("Gamification Installer.exe" in einem
Ordner mit seinen Abhaengigkeiten), der auf einem Windows-PC ganz ohne
separate Python-Installation laeuft. Das Ergebnis wird anschliessend zu einer
einzelnen "Gamification Installer.zip" gepackt, damit es weiterhin als ein
einzelner Download angeboten werden kann.

Nutzung:
    pip install -r requirements.txt
    python build_exe.py

Das fertige Programm liegt danach unter
windows/dist/Gamification Installer/Gamification Installer.exe, gepackt unter
windows/dist/Gamification Installer.zip

HINWEIS (--onedir statt --onefile): Ein per --onefile gebauter Installer
entpackt sich bei jedem Start selbst in einen temporaeren Ordner und fuehrt
sich von dort neu aus - genau dieses Verhalten wird von Windows Defenders
Cloud-/ML-Heuristik als Trojan:Win32/Wacatac.B!ml fehlklassifiziert (bestaetigt
per Get-MpThreat, Severity 5 -> automatische Quarantaene direkt beim Download,
unabhaengig vom eigentlichen Code). --onedir liefert dieselbe .exe stattdessen
unkomprimiert direkt neben ihren Abhaengigkeiten aus, ohne Selbstentpacken zur
Laufzeit, und vermeidet dadurch diesen Fehlalarm.
"""
import os
import sys
import zipfile

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
# - wird als Datenordner mit in den Installer gepackt, damit die Funktion
# auch ohne Internetzugriff/separates Repo-Checkout funktioniert.
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
        print(f"WARNUNG: {PICOFW_DIR} nicht gefunden - Bootloader-Flash-Funktion wird im Installer nicht funktionieren.")

    print(f"Baue '{APP_NAME}' (Ordner-Modus) mit PyInstaller ...")
    PyInstaller.__main__.run(args)

    app_dir = os.path.join(DIST_DIR, APP_NAME)
    exe_path = os.path.join(app_dir, f"{APP_NAME}.exe")
    print()
    if not os.path.isfile(exe_path):
        print("Build abgeschlossen, aber die .exe wurde nicht am erwarteten Pfad gefunden.")
        print(f"Bitte {app_dir} pruefen.")
        sys.exit(1)

    print(f"Fertig: {exe_path}")

    zip_path = os.path.join(DIST_DIR, f"{APP_NAME}.zip")
    if os.path.isfile(zip_path):
        os.remove(zip_path)
    print(f"Packe '{app_dir}' nach '{zip_path}' ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(app_dir):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                arcname = os.path.join(APP_NAME, os.path.relpath(file_path, app_dir))
                zf.write(file_path, arcname)
    print(f"Fertig: {zip_path}")


if __name__ == "__main__":
    main()
