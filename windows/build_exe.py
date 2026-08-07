"""
build_exe.py

Baut ZWEI eigenstaendige Windows-Programme mit PyInstaller (--onedir, kein
--onefile - siehe HINWEIS unten), die ganz ohne separate Python-Installation
laufen:
  - "Gamification Installer" aus windows/source/gamification_installer.py
  - "PluginPackager"         aus windows/source2/plugin_packager.py

EIN Skriptlauf baut beide, damit nicht zwei separate build_*.py-Skripte
gepflegt werden muessen. Jedes Ergebnis wird zusaetzlich zu einer eigenen
.zip gepackt, damit es als einzelner Download angeboten werden kann (siehe
.github/workflows/build-and-release-firmware.yml sowie
webshop/templates/gatehill_install.html sowie .../plugins.html fuer die
jeweiligen Download-Links).

Nutzung:
    pip install -r windows/requirements.txt
    python windows/build_exe.py

Ergebnisse:
    windows/dist/Gamification Installer/Gamification Installer.exe (+ .zip)
    windows/dist/PluginPackager/PluginPackager.exe (+ .zip)

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
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
DIST_DIR = os.path.join(WINDOWS_DIR, "dist")
BUILD_DIR = os.path.join(WINDOWS_DIR, "build_tmp")
SPEC_DIR = os.path.join(WINDOWS_DIR, "spec_tmp")
# Gemeinsames App-Icon des Projekts (siehe android_app/ fuer dieselbe
# Bildquelle) - fuer PyInstaller als .ico gebraucht, daher hier die bereits
# aus branding/fpv_app_icon_1024.png konvertierte Mehrfachaufloesungs-Datei.
ICON_PATH = os.path.join(PROJECT_ROOT, "branding", "fpv_app_icon.ico")
# picofw/ enthaelt die UF2-Dateien (MicroPython + Nuke) fuer den
# Bootloader-Flash (siehe gamification_installer.py's flash_bootsel_pico())
# - wird als Datenordner mit in den Installer gepackt, damit die Funktion
# auch ohne Internetzugriff/separates Repo-Checkout funktioniert.
PICOFW_DIR = os.path.join(PROJECT_ROOT, "picofw")

# Beide GUIs nutzen Kivy (siehe windows/kivy_theme.py) statt tkinter fuer
# ihre Oberflaeche:
#   - "--paths WINDOWS_DIR" macht "import kivy_theme" fuer PyInstallers
#     statische Import-Analyse sichtbar (gleicher Grund wie "--paths
#     TOOLS_DIR" unten fuer plugin_packager.py's "import build_firmware").
#   - Kivy laedt seine Provider-Module (Fenster/Text/Bild/Zwischenablage-
#     Backends unter kivy.core.*, sowie kivy.graphics/.input/.lang/.modules/
#     .effects/.uix) zur Laufzeit dynamisch per importlib nach - PyInstallers
#     normale Analyse findet nur statische "import"-Anweisungen und wuerde
#     diese Provider ohne explizites Einsammeln weglassen (die .exe stuerzt
#     dann erst beim Start ab). "--collect-data kivy" ergaenzt Kivys eigene
#     Datendateien (Default-Schriften/Shader/Bild-Loader unter kivy/data/).
#     BEWUSST NICHT "--collect-all kivy"/"--collect-submodules kivy" (auf das
#     komplette Wurzelpaket): das wuerde auch kivy.garden mit einsammeln - ein
#     von der separaten "Kivy-Garden"-Distribution nachinstalliertes
#     Namespace-Paket mit einem __path__, das kein Kivy-Code selbst braucht
#     (nur fuer optionale Drittanbieter-Widgets gedacht, hier ungenutzt),
#     dessen __path__-Form PyInstallers collect_submodules() aber mit
#     "ValueError: path must be None or list of paths" zum Absturz bringt.
#     Einzelne Unterpakete gezielt einzusammeln umgeht kivy.garden komplett.
#   - "kivy_deps.*" sind die auf Windows per pip separat installierten
#     SDL2/GLEW/ANGLE-DLLs (kivy_deps.sdl2/.glew/.angle) - ohne die
#     "--collect-all" dafuer fehlen der .exe die noetigen DLLs.
KIVY_PYINSTALLER_ARGS = [
    "--paths", WINDOWS_DIR,
    "--collect-data", "kivy",
    "--collect-submodules", "kivy.core",
    "--collect-submodules", "kivy.graphics",
    "--collect-submodules", "kivy.input",
    "--collect-submodules", "kivy.lang",
    "--collect-submodules", "kivy.modules",
    "--collect-submodules", "kivy.effects",
    "--collect-submodules", "kivy.uix",
    "--collect-all", "kivy_deps.sdl2",
    "--collect-all", "kivy_deps.glew",
    "--collect-all", "kivy_deps.angle",
]


def _build_one(app_name, source_script, extra_pyinstaller_args=None):
    import PyInstaller.__main__

    if not os.path.isfile(source_script):
        print(f"Quelldatei nicht gefunden: {source_script}")
        sys.exit(1)

    args = [
        source_script,
        "--name", app_name,
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
    else:
        print(f"WARNUNG: {ICON_PATH} nicht gefunden - '{app_name}' wird ohne eigenes Icon gebaut.")
    if extra_pyinstaller_args:
        args += extra_pyinstaller_args

    print(f"Baue '{app_name}' (Ordner-Modus) mit PyInstaller ...")
    PyInstaller.__main__.run(args)

    app_dir = os.path.join(DIST_DIR, app_name)
    exe_path = os.path.join(app_dir, f"{app_name}.exe")
    print()
    if not os.path.isfile(exe_path):
        print(f"Build von '{app_name}' abgeschlossen, aber die .exe wurde nicht am erwarteten Pfad gefunden.")
        print(f"Bitte {app_dir} pruefen.")
        sys.exit(1)
    print(f"Fertig: {exe_path}")

    zip_path = os.path.join(DIST_DIR, f"{app_name}.zip")
    if os.path.isfile(zip_path):
        os.remove(zip_path)
    print(f"Packe '{app_dir}' nach '{zip_path}' ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(app_dir):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                arcname = os.path.join(app_name, os.path.relpath(file_path, app_dir))
                zf.write(file_path, arcname)
    print(f"Fertig: {zip_path}")


def main():
    try:
        import PyInstaller.__main__  # noqa: F401
    except ImportError:
        print("PyInstaller ist nicht installiert. Bitte zuerst ausfuehren:")
        print(f"    pip install -r {os.path.join(WINDOWS_DIR, 'requirements.txt')}")
        sys.exit(1)

    installer_extra_args = list(KIVY_PYINSTALLER_ARGS)
    if os.path.isdir(PICOFW_DIR):
        # Windows-Trennzeichen fuer --add-data ist ';' (SRC;ZIEL-IM-BUNDLE).
        installer_extra_args += ["--add-data", f"{PICOFW_DIR};picofw"]
    else:
        print(f"WARNUNG: {PICOFW_DIR} nicht gefunden - Bootloader-Flash-Funktion wird im Installer nicht funktionieren.")
    _build_one(
        "Gamification Installer",
        os.path.join(WINDOWS_DIR, "source", "gamification_installer.py"),
        installer_extra_args,
    )

    # build_firmware.py/deploy_mod.py liegen in tools/, nicht neben
    # plugin_packager.py selbst (windows/source2/) - --paths sorgt dafuer,
    # dass PyInstallers Import-Analyse "import build_firmware"/"import
    # deploy_mod" trotzdem findet (die Laufzeit-sys.path.insert() ganz oben
    # in plugin_packager.py hilft nur zur Laufzeit, nicht der eigenstaendigen
    # PyInstaller-Analyse beim Bauen).
    _build_one(
        "PluginPackager",
        os.path.join(WINDOWS_DIR, "source2", "plugin_packager.py"),
        KIVY_PYINSTALLER_ARGS + ["--paths", TOOLS_DIR],
    )


if __name__ == "__main__":
    main()
