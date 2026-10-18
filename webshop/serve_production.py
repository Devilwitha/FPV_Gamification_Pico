"""Gemeinsamer Produktions-Entrypoint fuer alle Bollisoft-Server.

Startet aus einer einzigen Datei:

- den Flask-Webshop per Waitress (FLASK_HOST:FLASK_PORT, Default
  127.0.0.1:5000),
- die statische TX15 Werkstatt (Repo github.com/Devilwitha/txtheme-maker)
  im selben Waitress-Prozess unter /tx15-werkstatt/,
- den Cover Store/Maker (SteamOs_Pogram) als ueberwachten Unterprozess auf
  COVERSTORE_HOST:COVERSTORE_PORT (Default 127.0.0.1:9090). Stuerzt er ab,
  wird er automatisch neu gestartet.

Von aussen erreichbar sind die Server ueber den IIS-Reverse-Proxy (Site
"Bollisoft", siehe HTTPS_DEPLOYMENT.md bzw. install_webshop_autostart.ps1).
"""

import mimetypes
import os
import subprocess
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv
from waitress import serve
from werkzeug.exceptions import HTTPException, NotFound
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.utils import redirect, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
GITHUB_DIR = BASE_DIR.parent.parent

# .env explizit aus dem Webshop-Ordner laden, damit der Start unabhaengig
# vom aktuellen Arbeitsverzeichnis (z. B. aus der Aufgabenplanung) klappt.
load_dotenv(BASE_DIR / ".env")
os.chdir(BASE_DIR)

from app import app  # noqa: E402 - erst nach dem Laden der .env importieren

TX15_MOUNT_PATH = "/tx15-werkstatt"
RESTART_DELAY_SECONDS = 5

# Unter Windows fehlen diese Typen je nach Registry-Stand.
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/plain", ".lua")


def _env_int(name, default):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_bool(name, default):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_path(name, default):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return Path(value.strip())


def _log(message):
    print(f"[serve_production] {message}", flush=True)


def make_static_app(root):
    """WSGI-App, die ein Verzeichnis statisch ausliefert.

    Versteckte Pfade (z. B. .git, .gitattributes) werden nie ausgeliefert,
    Verzeichnis-URLs liefern ihre index.html.
    """

    def static_app(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if not path:
            # Ohne abschliessenden Slash wuerden die relativen Asset-Pfade
            # der Werkstatt auf die falsche Ebene zeigen.
            target = environ.get("SCRIPT_NAME", "") + "/"
            query = environ.get("QUERY_STRING", "")
            if query:
                target += "?" + query
            return redirect(target, 308)(environ, start_response)

        relative = path.lstrip("/")
        if not relative or relative.endswith("/"):
            relative += "index.html"
        if any(part.startswith(".") for part in relative.split("/")):
            return NotFound()(environ, start_response)

        try:
            response = send_from_directory(root, relative, environ)
        except HTTPException as exc:
            return exc(environ, start_response)
        return response(environ, start_response)

    return static_app


def build_wsgi_app():
    mounts = {}
    tx15_dir = _env_path("TX15_WERKSTATT_DIR", GITHUB_DIR / "txtheme-maker")
    if (tx15_dir / "index.html").is_file():
        mounts[TX15_MOUNT_PATH] = make_static_app(tx15_dir)
        _log(f"TX15 Werkstatt: {tx15_dir} -> {TX15_MOUNT_PATH}/")
    else:
        _log(
            f"TX15 Werkstatt nicht gefunden ({tx15_dir}) - {TX15_MOUNT_PATH}/ "
            "ist deaktiviert. Repo klonen oder TX15_WERKSTATT_DIR setzen."
        )
    if not mounts:
        return app
    return DispatcherMiddleware(app, mounts)


def _create_kill_on_close_job():
    """Windows-Job-Objekt, das alle zugeordneten Prozesse beendet, sobald
    dieser Prozess endet - auch wenn er hart beendet wird (z. B. ueber die
    Aufgabenplanung). So bleiben keine verwaisten Unterprozesse zurueck, die
    ihren Port weiter belegen.
    """
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job_object_extended_limit_information = 9
    job_object_limit_kill_on_job_close = 0x2000

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        _log(f"Job-Objekt konnte nicht erstellt werden (Fehler {ctypes.get_last_error()})")
        return None
    info = ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = job_object_limit_kill_on_job_close
    if not kernel32.SetInformationJobObject(
        job, job_object_extended_limit_information, ctypes.byref(info), ctypes.sizeof(info)
    ):
        _log(f"Job-Objekt konnte nicht konfiguriert werden (Fehler {ctypes.get_last_error()})")
        return None

    def assign(process):
        if not kernel32.AssignProcessToJobObject(job, int(process._handle)):
            _log(f"Prozess {process.pid} konnte dem Job-Objekt nicht zugeordnet werden "
                 f"(Fehler {ctypes.get_last_error()})")

    return assign


_assign_to_job = _create_kill_on_close_job()


class SupervisedProcess:
    """Haelt einen Unterprozess am Leben und startet ihn nach Abstuerzen neu."""

    def __init__(self, name, args, cwd, env):
        self.name = name
        self.args = args
        self.cwd = cwd
        self.env = env
        self._process = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            _log(f"{self.name}: starte {' '.join(self.args)}")
            try:
                self._process = subprocess.Popen(self.args, cwd=self.cwd, env=self.env)
            except OSError as exc:
                _log(f"{self.name}: Start fehlgeschlagen ({exc})")
            else:
                if _assign_to_job is not None:
                    _assign_to_job(self._process)
                exit_code = self._process.wait()
                if self._stop.is_set():
                    break
                _log(f"{self.name}: beendet mit Code {exit_code}")
            _log(f"{self.name}: Neustart in {RESTART_DELAY_SECONDS} s")
            self._stop.wait(RESTART_DELAY_SECONDS)

    def stop(self):
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            _log(f"{self.name}: wird beendet")
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def start_cover_store():
    if not _env_bool("COVERSTORE_ENABLED", True):
        _log("Cover Store: per COVERSTORE_ENABLED deaktiviert")
        return None

    script = _env_path(
        "COVERSTORE_SCRIPT",
        GITHUB_DIR / "SteamOs_Pogram" / "tools" / "cover_maker" / "server" / "cover_store_server.py",
    )
    if not script.is_file():
        _log(f"Cover Store nicht gefunden ({script}) - wird nicht gestartet.")
        return None

    env = os.environ.copy()
    env.setdefault("COVERSTORE_HOST", "127.0.0.1")
    env.setdefault("COVERSTORE_PORT", "9090")
    env.setdefault("COVERSTORE_BASE_PATH", "/coverstore-maker")
    env["PYTHONUNBUFFERED"] = "1"

    supervisor = SupervisedProcess(
        "Cover Store",
        [sys.executable, str(script)],
        cwd=str(script.parent),
        env=env,
    )
    supervisor.start()
    return supervisor


def main():
    cover_store = start_cover_store()
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = _env_int("FLASK_PORT", 5000)
    _log(f"Webshop: http://{host}:{port}/")
    try:
        serve(
            build_wsgi_app(),
            host=host,
            port=port,
            threads=_env_int("WAITRESS_THREADS", 8),
        )
    finally:
        if cover_store is not None:
            cover_store.stop()


if __name__ == "__main__":
    main()
