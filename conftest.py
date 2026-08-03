"""Gemeinsame Test-Grundlage fuer die GESAMTE pytest-Session.

Fuegt source/ und pico_simulator/ zu sys.path hinzu und installiert die
pico_simulator-Kompatibilitaetsschicht (fake machine/network/bluetooth-Module,
MicroPython-Zeitfunktionen etc.) EINMAL fuer die gesamte Session - sowohl die
source/-Firmware-Tests (tests/source/) als auch die pico_simulator-eigenen
Tests (tests/pico_simulator/test_pico_runtime.py) brauchen diese bereits
installierte Schicht, unabhaengig davon, welcher Testordner tatsaechlich
ausgewaehlt wurde (z.B. `pytest tests/pico_simulator/`).

Bereichsspezifische Fixtures (FakeWriter/FakeReader, Flask-Testclient,
build_firmware-Sandbox, ...) liegen in den jeweiligen Unterordnern:
tests/source/conftest.py, tests/webshop/conftest.py, tests/tools/conftest.py,
tests/pico_simulator/conftest.py.

WICHTIG - globaler "main"-Sicherheitsnetz-Stub: main.py fuehrt beim Import
bedingungslos run() aus (main.py ist auf dem echten Geraet der von boot.py
per `import main` gestartete Einstiegspunkt) - inklusive echter Seiteneffekte
wie dem Schreiben von fpv_debug_session.txt/system_info.json relativ zum
aktuellen Arbeitsverzeichnis. Mehrere Module in source/ (gmr.py,
plugin_manager.py, pico_web_api.py, source/mods/shooter/main.py, ...) machen
beim EIGENEN Import `from main import ...` - wird eines davon aus Versehen
OHNE vorherigen main-Stub importiert (z.B. weil ein Test-Modul es auf
Modulebene importiert, was schon waehrend der pytest-Kollektion laeuft, also
VOR jedem isolated_cwd-Fixture), wuerde das echte main.py ausgefuehrt und
schreibt seine Debug-Dateien direkt in den echten Projektordner statt in ein
isoliertes tmp_path. Da source/ IMMER auf sys.path liegt (s.u.), kann das
prinzipiell aus JEDEM Testordner heraus passieren, nicht nur aus
tests/source/. Um das kategorisch auszuschliessen, wird hier ein
harmloser Platzhalter fest unter sys.modules["main"] hinterlegt, BEVOR
irgendein Testmodul ueberhaupt importiert wird - Tests, die main.py's echte
Attribute brauchen, ueberschreiben ihn gezielt ueber
install_stub_module()/fresh_import() (siehe tests/source/conftest.py, das
den vorherigen Stub beim Aufraeumen wiederherstellt statt ihn komplett zu
entfernen)."""
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "source"
SIM_DIR = ROOT / "pico_simulator"

for _p in (str(SOURCE_DIR), str(SIM_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if "main" not in sys.modules:
    _main_safety_stub = types.ModuleType("main")
    _main_safety_stub.debug_log = lambda message: None
    sys.modules["main"] = _main_safety_stub

import pico_runtime  # noqa: E402

pico_runtime.install(
    sim_port=8080,
    mem_free_bytes=180 * 1024,
    mem_alloc_bytes=70 * 1024,
    cpu_freq_hz=133_000_000,
    cpu_scale=1.0,
    net_latency_ms=0,
)
