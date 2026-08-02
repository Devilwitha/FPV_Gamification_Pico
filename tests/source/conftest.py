"""Pytest-Fixtures speziell fuer die source/-Firmware-Unittests.

Die pico_simulator-Kompatibilitaetsschicht selbst (fake machine/network/
bluetooth-Module) wird bereits vom Root-conftest.py session-weit installiert
(siehe dort) - hier liegen nur die Hilfsfixtures, mit denen die MicroPython-
Module aus source/ unter CPython komfortabel importiert und getestet werden
koennen:

 - isolated_cwd: jeder Test laeuft in einem frischen temporaeren Verzeichnis,
   damit die vielen relativen open()/os.remove()-Aufrufe in source/ (z.B.
   "license.lic", "boot_state.json", "*.conf") sich nicht gegenseitig oder den
   echten Projektordner beeinflussen.
 - fresh_import: importiert ein source/-Modul garantiert frisch (wichtig fuer
   Module mit Modul-globalem Zustand wie idcard_helpers.py).
 - import_entry_module: wie fresh_import, aber unterdrueckt waehrend des
   Imports echte asyncio.run()-Aufrufe - noetig fuer die "Einstiegsskripte"
   (role_setup.py/recovery.py/main.py/...), die am Modul-Ende unbedingt
   `run()` aufrufen (das sonst den Test fuer immer haengen liesse).
 - install_stub_module: registriert ein leeres Platzhaltermodul in
   sys.modules, damit boot.py's bedingte `import main`/`import recovery`/...
   nicht das riesige echte Modul ausfuehrt.
"""
import contextlib
import importlib
import sys
import types

import pytest


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Jeder Test bekommt ein leeres Arbeitsverzeichnis (wie data/ auf dem
    echten Pico) statt im Projekt-Root zu schreiben."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def _fresh_import(name):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


@pytest.fixture
def fresh_import():
    return _fresh_import


@contextlib.contextmanager
def _asyncio_run_disarmed():
    """Ersetzt asyncio.run() nur fuer die Dauer des Imports durch eine
    Variante, die die uebergebene Koroutine sofort schliesst statt sie
    auszufuehren - main_async()'s `while True: await asyncio.sleep_ms(...)`
    wuerde sonst den Testlauf nie beenden."""
    import asyncio

    original = asyncio.run

    def _fake_run(coro, *args, **kwargs):
        coro.close()
        return None

    asyncio.run = _fake_run
    try:
        yield
    finally:
        asyncio.run = original


def _import_entry_module(name):
    with _asyncio_run_disarmed():
        return _fresh_import(name)


@pytest.fixture
def import_entry_module():
    return _import_entry_module


@pytest.fixture
def install_stub_module():
    installed = []

    def _install(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[name] = mod
        installed.append(name)
        return mod

    yield _install
    for name in installed:
        sys.modules.pop(name, None)


class FakeWriter:
    """Minimaler Ersatz fuer asyncio.StreamWriter: sammelt alle write()-Aufrufe
    und stellt sie ueber .response als zusammenhaengende Bytes bereit."""

    def __init__(self):
        self.chunks = []
        self.closed = False
        self.drained = 0

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.chunks.append(data)

    async def drain(self):
        self.drained += 1

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None

    @property
    def response(self):
        return b"".join(self.chunks)

    @property
    def status_line(self):
        return self.response.split(b"\r\n", 1)[0].decode("utf-8", "replace")

    @property
    def body(self):
        return self.response.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in self.response else b""

    def json(self):
        import json as _json

        return _json.loads(self.body.decode("utf-8"))


@pytest.fixture
def fake_writer():
    return FakeWriter()


class FakeReader:
    """Minimaler Ersatz fuer asyncio.StreamReader auf Basis einer Liste
    vorbereiteter Zeilen (inkl. Zeilenumbruch) - optional gefolgt von einem
    reinen Body (fuer .read(n))."""

    def __init__(self, lines, body=b""):
        self._lines = list(lines)
        self._body = body

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""

    async def read(self, n):
        chunk = self._body[:n]
        self._body = self._body[n:]
        return chunk


@pytest.fixture
def make_reader():
    def _make(request_line, headers=(), body=b""):
        lines = [request_line]
        for header in headers:
            lines.append(header)
        lines.append(b"\r\n")
        return FakeReader(lines, body=body)

    return _make


@pytest.fixture
def fast_sleep_ms(monkeypatch):
    """Ersetzt asyncio.sleep_ms() durch eine sofort zurueckkehrende Variante -
    fuer Tests, die Codepfade mit eingebauten Verzoegerungen (z.B. vor
    machine.reset()) durchlaufen, ohne die Testlaufzeit unnoetig zu erhoehen."""
    import asyncio

    async def _instant(_ms):
        # Ein echtes await asyncio.sleep(0) statt nur `return None`: eine
        # Koroutine ohne jeden inneren await-Punkt gibt die Kontrolle NIE an
        # den Event-Loop zurueck, was Endlosschleifen wie
        # KothMode.run()/RaceMode.run() (while True: ... await sleep_ms(...))
        # fuer immer haengen liesse.
        await asyncio.sleep(0)

    monkeypatch.setattr(asyncio, "sleep_ms", _instant)
    yield
