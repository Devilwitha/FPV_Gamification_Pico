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
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "source"
SIM_DIR = ROOT / "pico_simulator"

for _p in (str(SOURCE_DIR), str(SIM_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pico_runtime  # noqa: E402

pico_runtime.install(
    sim_port=8080,
    mem_free_bytes=180 * 1024,
    mem_alloc_bytes=70 * 1024,
    cpu_freq_hz=133_000_000,
    cpu_scale=1.0,
    net_latency_ms=0,
)
