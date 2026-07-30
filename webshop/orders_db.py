"""Persistente Datenhaltung fuer Hardware-Bestellungen (Versandadressen) des
Webshops.

WICHTIG: Bewusst eine EIGENSTAENDIGE SQLite-Datei, komplett getrennt von
db.py's webshop.db (siehe DB_PATH unten sowie ORDERS_DB_PATH in app.py). Diese
Datenbank enthaelt Kundenadressen fuer den Warenversand und darf NIEMALS
geloescht, ueberschrieben oder durch eine Migration ersetzt werden - init_db()
verwendet daher ausschliesslich "CREATE TABLE IF NOT EXISTS" und es gibt in
diesem gesamten Modul keinen Code-Pfad, der die Datenbankdatei oder ihre
Tabellen loescht.

Zwei Tabellen:

- ``pending_shipping``: bezahlte Hardware-Bestellungen, die noch auf die
  Eingabe der Versandadresse warten (Pendant zu db.py's pending_licenses).
- ``shipping_orders``: Hardware-Bestellungen mit vollstaendiger Adresse -
  ``shipped_at`` ist NULL, solange das Paket noch nicht verschickt wurde.

Ein Datensatz "wandert" beim Absenden des Adressformulars atomar von der
einen in die andere Tabelle (siehe move_pending_to_order()) - analog zu
db.py's move_pending_to_customer().
"""

import contextlib
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get("WEBSHOP_ORDERS_DB_PATH", "").strip() or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "orders.db"
)


@contextlib.contextmanager
def _get_connection():
    """Liefert eine SQLite-Verbindung als Context-Manager, der die Transaktion
    committet/zurueckrollt UND die Verbindung am Ende garantiert schliesst."""
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db():
    """Legt die Tabellen an, falls sie noch nicht existieren. Wird beim Start
    von app.py einmalig aufgerufen. Loescht/ersetzt NIEMALS bestehende Daten."""
    with _get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_shipping (
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
            "CREATE INDEX IF NOT EXISTS idx_pending_shipping_email ON pending_shipping (email)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shipping_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                product_id TEXT NOT NULL,
                full_name TEXT NOT NULL,
                street_address TEXT NOT NULL,
                address_line2 TEXT NOT NULL DEFAULT '',
                postal_code TEXT NOT NULL,
                city TEXT NOT NULL,
                country TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                payment_provider TEXT NOT NULL,
                payment_reference TEXT NOT NULL,
                ordered_at TEXT NOT NULL,
                shipped_at TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_shipping_orders_email ON shipping_orders (email)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_shipping_orders_shipped_at ON shipping_orders (shipped_at)"
        )


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def payment_already_recorded(payment_provider, payment_reference):
    """Prueft, ob eine Zahlungsreferenz bereits als offene ODER bereits
    abgeschlossene (mit Adresse versehene) Hardware-Bestellung erfasst wurde -
    verhindert doppelte Eintraege bei z. B. einem Reload der /success-Seite."""
    with _get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM pending_shipping WHERE payment_provider = ? AND payment_reference = ?",
            (payment_provider, payment_reference),
        ).fetchone()
        if row is not None:
            return True
        row = connection.execute(
            "SELECT 1 FROM shipping_orders WHERE payment_provider = ? AND payment_reference = ?",
            (payment_provider, payment_reference),
        ).fetchone()
        return row is not None


def add_pending_shipping(email, product_id, payment_provider, payment_reference):
    """Legt eine offene, bezahlte Hardware-Bestellung an und liefert deren ID."""
    with _get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO pending_shipping (email, product_id, payment_provider, payment_reference, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (email, product_id, payment_provider, payment_reference, _now_iso()),
        )
        return cursor.lastrowid


def get_pending_shipping(pending_id):
    """Liefert eine einzelne offene Hardware-Bestellung (sqlite3.Row) oder None."""
    if pending_id is None:
        return None
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM pending_shipping WHERE id = ?", (pending_id,)
        ).fetchone()


def get_pending_shipping_for_email(email):
    """Liefert alle offenen Hardware-Bestellungen einer E-Mail-Adresse, aelteste zuerst."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM pending_shipping WHERE email = ? COLLATE NOCASE ORDER BY created_at ASC",
            (email,),
        ).fetchall()


def get_shipping_orders_for_email(email):
    """Liefert alle abgeschlossenen Hardware-Bestellungen (mit Adresse) einer
    E-Mail-Adresse, neueste zuerst."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM shipping_orders WHERE email = ? COLLATE NOCASE ORDER BY ordered_at DESC",
            (email,),
        ).fetchall()


def get_order(order_id):
    """Liefert eine einzelne Hardware-Bestellung (sqlite3.Row) oder None."""
    if order_id is None:
        return None
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM shipping_orders WHERE id = ?", (order_id,)
        ).fetchone()


def get_unshipped_orders():
    """Liefert alle Hardware-Bestellungen, die noch NICHT verschickt wurden
    (shipped_at IS NULL), aelteste zuerst (zuerst bezahlte Bestellungen sollen
    zuerst verschickt werden)."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM shipping_orders WHERE shipped_at IS NULL ORDER BY ordered_at ASC"
        ).fetchall()


def get_shipped_orders():
    """Liefert alle bereits verschickten Hardware-Bestellungen, neueste zuerst."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM shipping_orders WHERE shipped_at IS NOT NULL ORDER BY shipped_at DESC"
        ).fetchall()


def get_all_orders():
    """Liefert ALLE abgeschlossenen Hardware-Bestellungen (verschickt oder
    nicht), neueste zuerst - Grundlage fuer die Admin-Übersicht "Alle
    verkauften Produkte"."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM shipping_orders ORDER BY ordered_at DESC"
        ).fetchall()


def mark_order_shipped(order_id):
    """Markiert eine Hardware-Bestellung als verschickt (setzt shipped_at auf
    jetzt). Ist die Bestellung bereits als verschickt markiert, wird der
    urspruengliche Zeitpunkt NICHT ueberschrieben (idempotent gegenueber
    Doppelklicks)."""
    with _get_connection() as connection:
        connection.execute(
            "UPDATE shipping_orders SET shipped_at = ? WHERE id = ? AND shipped_at IS NULL",
            (_now_iso(), order_id),
        )


def move_pending_to_order(pending_id, address):
    """Loescht die offene Bestellung ``pending_id`` und legt dafuer atomar
    (gleiche Transaktion) einen Eintrag in shipping_orders an, sobald der
    Kunde die Versandadresse eingegeben hat. ``address`` ist ein dict mit den
    Feldern full_name/street_address/address_line2/postal_code/city/country/
    phone. Liefert die ID der neuen Bestellung."""
    with _get_connection() as connection:
        pending = connection.execute(
            "SELECT * FROM pending_shipping WHERE id = ?", (pending_id,)
        ).fetchone()
        if pending is None:
            raise ValueError(f"Offene Hardware-Bestellung {pending_id} nicht gefunden.")

        cursor = connection.execute(
            """
            INSERT INTO shipping_orders
                (email, product_id, full_name, street_address, address_line2, postal_code,
                 city, country, phone, payment_provider, payment_reference, ordered_at, shipped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                pending["email"],
                pending["product_id"],
                address["full_name"],
                address["street_address"],
                address.get("address_line2", ""),
                address["postal_code"],
                address["city"],
                address["country"],
                address.get("phone", ""),
                pending["payment_provider"],
                pending["payment_reference"],
                _now_iso(),
            ),
        )
        connection.execute("DELETE FROM pending_shipping WHERE id = ?", (pending_id,))
        return cursor.lastrowid
