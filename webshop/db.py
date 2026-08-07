"""Persistente Datenhaltung fuer den Login-/Konto-Bereich des Webshops.

Drei Tabellen in einer SQLite-Datei:

- ``pending_licenses``: bezahlte, aber noch nicht eingeloeste digitale
  Bestellungen (E-Mail wartet auf Hardware-ID-Eingabe).
- ``customer_licenses``: bereits ausgestellte Lizenzen je E-Mail-Adresse,
  verweist auf die Lizenzdatei in LICENSES_DIR (siehe app.py).
- ``accounts``: Login-Konten (E-Mail + Passwort-Hash + Profildaten Name/
  Adresse/Telefon/Land) - unabhaengig von Bestellungen: ein Konto kann
  registriert werden, bevor oder nachdem ueberhaupt etwas gekauft wurde.
  Das Passwort wird NIE im Klartext gespeichert (siehe app.py's
  werkzeug.security.generate_password_hash/check_password_hash).

Ein Lizenz-Datensatz "wandert" beim Erzeugen einer Lizenz atomar von
pending_licenses in customer_licenses (siehe move_pending_to_customer()).
"""

import contextlib
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get("WEBSHOP_DB_PATH", "").strip() or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "webshop.db"
)


@contextlib.contextmanager
def _get_connection():
    """Liefert eine SQLite-Verbindung als Context-Manager, der die Transaktion
    committet/zurueckrollt UND die Verbindung am Ende garantiert schliesst
    (``with sqlite3.Connection`` allein schliesst die Verbindung nicht, das
    wuerde bei einem lange laufenden Server Datei-Handles anhaeufen)."""
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db():
    """Legt die Tabellen an, falls sie noch nicht existieren. Wird beim
    Start von app.py einmalig aufgerufen."""
    with _get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_licenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                product_id TEXT NOT NULL,
                payment_provider TEXT NOT NULL,
                payment_reference TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_licenses_email ON pending_licenses (email)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_licenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                product_id TEXT NOT NULL,
                hardware_id TEXT NOT NULL,
                license_filename TEXT NOT NULL,
                payment_provider TEXT NOT NULL,
                payment_reference TEXT NOT NULL,
                issued_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_customer_licenses_email ON customer_licenses (email)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                address TEXT NOT NULL,
                phone TEXT NOT NULL,
                country TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def payment_already_recorded(payment_provider, payment_reference):
    """Prueft, ob eine Zahlungsreferenz bereits als offene ODER bereits
    eingeloeste (in eine Lizenz umgewandelte) Bestellung erfasst wurde -
    verhindert doppelte Eintraege bei z. B. einem Reload der /success-Seite,
    auch nachdem die urspruengliche pending_licenses-Zeile schon in
    customer_licenses "gewandert" ist (siehe move_pending_to_customer())."""
    with _get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM pending_licenses WHERE payment_provider = ? AND payment_reference = ?",
            (payment_provider, payment_reference),
        ).fetchone()
        if row is not None:
            return True
        row = connection.execute(
            "SELECT 1 FROM customer_licenses WHERE payment_provider = ? AND payment_reference = ?",
            (payment_provider, payment_reference),
        ).fetchone()
        return row is not None


def add_pending_license(email, product_id, payment_provider, payment_reference):
    """Legt eine offene, bezahlte Bestellung an und liefert deren ID."""
    with _get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO pending_licenses (email, product_id, payment_provider, payment_reference, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (email, product_id, payment_provider, payment_reference, _now_iso()),
        )
        return cursor.lastrowid


def get_pending_license(pending_id):
    """Liefert eine einzelne offene Bestellung (sqlite3.Row) oder None."""
    if pending_id is None:
        return None
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM pending_licenses WHERE id = ?", (pending_id,)
        ).fetchone()


def get_pending_licenses_for_email(email):
    """Liefert alle offenen Bestellungen einer E-Mail-Adresse, aelteste zuerst."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM pending_licenses WHERE email = ? COLLATE NOCASE ORDER BY created_at ASC, id ASC",
            (email,),
        ).fetchall()


def get_customer_licenses_for_email(email):
    """Liefert alle bereits ausgestellten Lizenzen einer E-Mail-Adresse, neueste zuerst."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM customer_licenses WHERE email = ? COLLATE NOCASE ORDER BY issued_at DESC, id DESC",
            (email,),
        ).fetchall()


def get_all_customer_licenses():
    """Liefert ALLE bereits ausgestellten Lizenzen (ueber alle E-Mail-Adressen
    hinweg), neueste zuerst - Grundlage fuer die Admin-Übersicht "Alle
    verkauften Produkte" (siehe app.py's /admin)."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM customer_licenses ORDER BY issued_at DESC, id DESC"
        ).fetchall()


def get_customer_license(license_id):
    """Liefert eine einzelne ausgestellte Lizenz (sqlite3.Row) oder None."""
    if license_id is None:
        return None
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM customer_licenses WHERE id = ?", (license_id,)
        ).fetchone()


def move_pending_to_customer(pending_id, hardware_id, license_filename):
    """Loescht die offene Bestellung ``pending_id`` und legt dafuer atomar
    (gleiche Transaktion) einen Eintrag in customer_licenses an - das vom
    Nutzer gewuenschte "Verschieben" der E-Mail-Adresse zwischen den beiden
    Tabellen, sobald die Lizenz erzeugt wurde. Die Zahlungsreferenz wandert
    mit, damit payment_already_recorded() dieselbe Zahlung auch nach dem
    Verschieben noch als bereits verarbeitet erkennt."""
    with _get_connection() as connection:
        pending = connection.execute(
            "SELECT * FROM pending_licenses WHERE id = ?", (pending_id,)
        ).fetchone()
        if pending is None:
            raise ValueError(f"Offene Bestellung {pending_id} nicht gefunden.")

        cursor = connection.execute(
            """
            INSERT INTO customer_licenses
                (email, product_id, hardware_id, license_filename, payment_provider, payment_reference, issued_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pending["email"],
                pending["product_id"],
                hardware_id,
                license_filename,
                pending["payment_provider"],
                pending["payment_reference"],
                _now_iso(),
            ),
        )
        connection.execute("DELETE FROM pending_licenses WHERE id = ?", (pending_id,))
        return cursor.lastrowid


def create_account(email, password_hash, full_name, address, phone, country):
    """Legt ein neues Login-Konto an. Wirft sqlite3.IntegrityError, falls die
    E-Mail-Adresse (case-insensitiv, siehe UNIQUE COLLATE NOCASE) bereits
    registriert ist - der Aufrufer (app.py's register()) faengt das ab und
    zeigt eine verstaendliche Fehlermeldung statt eines Servers-Fehlers."""
    with _get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO accounts (email, password_hash, full_name, address, phone, country, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (email, password_hash, full_name, address, phone, country, _now_iso()),
        )
        return cursor.lastrowid


def get_account_by_email(email):
    """Liefert das Konto (sqlite3.Row) zu einer E-Mail-Adresse oder None."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM accounts WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
