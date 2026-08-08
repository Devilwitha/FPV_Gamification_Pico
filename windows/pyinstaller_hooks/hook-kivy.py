"""
windows/pyinstaller_hooks/hook-kivy.py

Ersetzt PyInstallers eingebauten Kivy-Hook (PyInstaller/hooks/hook-kivy.py) -
"--additional-hooks-dir" (siehe windows/build_exe.py's KIVY_PYINSTALLER_ARGS)
gibt Hooks aus diesem Ordner die hoechste Prioritaet (HOOK_PRIORITY_USER_HOOKS
in PyInstallers depend/analysis.py); PyInstaller fuehrt pro Modul NUR den
Hook mit der hoechsten Prioritaet aus, der eingebaute Kivy-Hook laeuft dann
also gar nicht erst.

Grund: der eingebaute Hook ruft Kivys get_deps_all() auf, was intern
kivy.tools.packaging.pyinstaller_hooks importiert - und DAS importiert beim
Definieren von "kivy_modules" u.a. "kivy.core.window.window_info" bzw. laesst
"kivy.core.window.window_sdl2" ueber collect_submodules('kivy.core') in die
hiddenimports einfliessen. Sobald "kivy.core.window.window_sdl2" (der
tatsaechlich benoetigte Fenster-Provider) als hiddenimport gelistet ist,
importiert PyInstallers "Looking for dynamic libraries"-Schritt (Aufloesen
der DLL-Abhaengigkeiten des kompilierten Providers) dieses Modul WIRKLICH,
um seine Abhaengigkeiten zu ermitteln - und dessen Modul-Code erzeugt beim
Import sofort ein ECHTES Fenster + einen echten OpenGL-Kontext (siehe
kivy/core/window/__init__.py's core_select_lib()). Auf einem Build-Server
ohne echte GPU (z.B. GitHub Actions' windows-latest-Runner, nur Software-
Rendering "GDI Generic", OpenGL 1.1) bricht das mit "Minimum required
OpenGL version (2.0) NOT found!" hart ab und der Build-Job haengt/schlaegt
fehl. Das laesst sich NICHT durch Weglassen von "kivy.core.window.
window_sdl2" umgehen, da die gebaute .exe den Fenster-Provider dann zur
Laufzeit gar nicht mehr faende.

Der eigentliche Fix ist daher windows/build_exe.py's KIVY_DOC=1 (siehe
Kommentar dort: core_select_lib() gibt bei gesetztem KIVY_DOC sofort None
zurueck, OHNE ein echtes Fenster/OpenGL zu erzeugen - Kivys eigener,
extra dafuer vorgesehener Schalter, siehe kivy/core/__init__.py). Dieser
Hook existiert zusaetzlich NUR, weil kivy.tools.packaging.pyinstaller_hooks
seine Variable "kivy_modules" (die der eingebaute PyInstaller-Hook direkt
importiert) hinter einem "if 'KIVY_DOC' not in environ:"-Guard versteckt
(siehe deren __init__.py) - bei gesetztem KIVY_DOC waere sie dort schlicht
nicht definiert und der EINGEBAUTE PyInstaller-Hook wuerde selbst mit einem
ImportError abstuerzen. add_dep_paths()/get_factory_modules() stehen dagegen
AUSSERHALB dieses Guards und bleiben ganz normal nutzbar; nur der Inhalt von
"kivy_modules" wird hier unabhaengig von KIVY_DOC direkt nachgebaut (siehe
dessen Definition dort).

WICHTIG (datas): Kivys eigener Loader (kivy/lang/builder.py, laedt u.a.
style.kv beim Start) sucht seine Dateien relativ zu kivy.kivy_data_dir/
kivy.kivy_modules_dir. PyInstallers eingebauter Kivy-Hook kopiert diese
Ordner dafuer traditionell nach "kivy_install/data" bzw. "kivy_install/
modules" im Bundle (siehe dessen "datas"-Definition) - das muss hier exakt
nachgebildet werden, sonst sucht die gebaute .exe ihre style.kv & Co. am
falschen Ort und stuerzt schon beim Start mit FileNotFoundError ab. Deshalb
NICHT "--collect-data kivy" (kopiert an den normalen, aber hier falschen
"kivy/data/"-Pfad) in windows/build_exe.py verwenden, sondern exakt diese
Zuordnung hier.
"""
import os

import kivy
from kivy.tools.packaging.pyinstaller_hooks import add_dep_paths, get_factory_modules
from PyInstaller.utils.hooks import collect_submodules

add_dep_paths()

datas = [
    (kivy.kivy_data_dir, os.path.join("kivy_install", os.path.basename(kivy.kivy_data_dir))),
    (kivy.kivy_modules_dir, os.path.join("kivy_install", os.path.basename(kivy.kivy_modules_dir))),
]

hiddenimports = list(set(get_factory_modules() + [
    "xml.etree.cElementTree",
    "kivy.core.gl",
    "kivy.weakmethod",
    "kivy.core.window.window_info",
    "kivy.core.window.window_sdl2",
    "kivy.core.text.text_sdl2",
    "kivy.core.image.img_tex",
    "kivy.core.image.img_dds",
    "kivy.core.image.img_sdl2",
    "kivy.core.image.img_pil",
    "kivy.core.clipboard.clipboard_sdl2",
    "kivy.core.clipboard.clipboard_winctypes",
] + collect_submodules("kivy.graphics")))
