"""Test-Infrastruktur fuer webshop/ - eigenstaendig vom source/-Conftest, um
den generischen Modulnamen ("db", "orders_db", "app") keine Ueberschneidungen
mit den MicroPython-Firmware-Tests zu geben.

Jeder Test bekommt eine FRISCH importierte Flask-App mit eigenen, temporaeren
SQLite-Datenbanken (ueber die von db.py/orders_db.py unterstuetzten
WEBSHOP_DB_PATH/WEBSHOP_ORDERS_DB_PATH-Umgebungsvariablen) sowie einem
umgeleiteten LICENSES_DIR/KEYS_DIR - nichts landet jemals im echten
Projekt-Ordner (insbesondere NICHT im echten lizenzen/-Ordner mit
Kundendaten).
"""
import importlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
WEBSHOP_DIR = ROOT / "webshop"
TOOLS_DIR = ROOT / "tools"

for _p in (str(WEBSHOP_DIR), str(TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _fresh_import(name):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


@pytest.fixture
def db_module(tmp_path, monkeypatch):
    """Frisch importiertes db.py mit eigener, temporaerer SQLite-Datei - fuer
    Tests, die nur die Datenbankschicht brauchen (ohne Flask/Stripe/PayPal)."""
    monkeypatch.setenv("WEBSHOP_DB_PATH", str(tmp_path / "webshop.db"))
    module = _fresh_import("db")
    module.init_db()
    return module


@pytest.fixture
def orders_db_module(tmp_path, monkeypatch):
    """Frisch importiertes orders_db.py mit eigener, temporaerer SQLite-Datei."""
    monkeypatch.setenv("WEBSHOP_ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    module = _fresh_import("orders_db")
    module.init_db()
    return module


@pytest.fixture
def webshop_app(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBSHOP_DB_PATH", str(tmp_path / "webshop.db"))
    monkeypatch.setenv("WEBSHOP_ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("DUMMY_MODE", "true")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "adminpass")
    monkeypatch.setenv("LICENSE_KEYS_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("DOMAIN_URL", "http://localhost:5000")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_test_dummy")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "test-client-secret")
    monkeypatch.chdir(tmp_path)

    _fresh_import("db")
    _fresh_import("orders_db")
    app_module = _fresh_import("app")

    # LICENSES_DIR wird beim Import aus PROJECT_ROOT (dem ECHTEN Repo-Root)
    # berechnet, nicht ueber eine Umgebungsvariable - fuer Tests explizit auf
    # tmp_path umbiegen, damit nie in den echten lizenzen/-Ordner geschrieben wird.
    app_module.LICENSES_DIR = str(tmp_path / "lizenzen")
    return app_module


@pytest.fixture
def client(webshop_app):
    webshop_app.app.testing = True
    with webshop_app.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def keypair(webshop_app, tmp_path):
    """Erzeugt ein echtes RSA-Schluesselpaar an der von app.py erwarteten
    Stelle (PRIVATE_KEY_PATH/PUBLIC_KEY_PATH), damit api_license_create()
    echte Lizenzen signieren kann - genau wie im echten Betrieb, nur in einem
    temporaeren Verzeichnis."""
    import license_generator

    import os
    os.makedirs(os.path.dirname(webshop_app.PRIVATE_KEY_PATH), exist_ok=True)
    license_generator.generate_keypair(webshop_app.PRIVATE_KEY_PATH, webshop_app.PUBLIC_KEY_PATH)
    return webshop_app.PRIVATE_KEY_PATH, webshop_app.PUBLIC_KEY_PATH
