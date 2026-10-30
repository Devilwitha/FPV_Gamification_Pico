"""Persistente Datenhaltung fuer den Login-/Konto-Bereich des Webshops.

Fuenf Tabellen in einer SQLite-Datei:

- ``pending_licenses``: bezahlte, aber noch nicht eingeloeste digitale
  Bestellungen (E-Mail wartet auf Hardware-ID-Eingabe).
- ``customer_licenses``: bereits ausgestellte Lizenzen je E-Mail-Adresse,
  verweist auf die Lizenzdatei in LICENSES_DIR (siehe app.py).
- ``accounts``: Login-Konten (E-Mail + Passwort-Hash + Profildaten Name/
  Adresse/Telefon/Land) - unabhaengig von Bestellungen: ein Konto kann
  registriert werden, bevor oder nachdem ueberhaupt etwas gekauft wurde.
  Das Passwort wird NIE im Klartext gespeichert (siehe app.py's
  werkzeug.security.generate_password_hash/check_password_hash).
- ``tx15_backups``: SD-Karten-Backups aus dem TX15 Backup Studio je Konto
  (nur Metadaten, die ZIP-Dateien selbst siehe tx15_backups.py).
- ``tx15_projects``: im Theme Studio bzw. Lua Studio gespeicherte Themes und
  Lua-Skripte je Konto (Studio-Stand als JSON, ZIP siehe tx15_projects.py).

Ein Lizenz-Datensatz "wandert" beim Erzeugen einer Lizenz atomar von
pending_licenses in customer_licenses (siehe move_pending_to_customer()).
"""

import contextlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = os.environ.get("WEBSHOP_DB_PATH", "").strip() or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "webshop.db"
)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def media_dir(env_name, *default_parts):
    """Ablageordner fuer hochgeladene Dateien.

    Reihenfolge: eigene Umgebungsvariable (env_name), sonst - wenn mit
    WEBSHOP_DB_PATH eine andere Datenbank laeuft (z. B. Tests) - neben dieser
    Datenbank, sonst <Repo>/<default_parts>. Die Dateien gehoeren immer zur
    Datenbank: Die Aufraeum-Routinen loeschen alles, was die Datenbank nicht
    kennt - eine fremde Datenbank auf dem echten Ordner wuerde sonst alle
    echten Uploads als verwaist entfernen."""
    explicit = os.environ.get(env_name, "").strip()
    if explicit:
        return Path(explicit)
    if os.environ.get("WEBSHOP_DB_PATH", "").strip():
        return Path(DB_PATH).resolve().parent.joinpath(*default_parts)
    return PROJECT_ROOT.joinpath(*default_parts)


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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tx15_backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL COLLATE NOCASE,
                label TEXT NOT NULL,
                created_ms INTEGER NOT NULL,
                size INTEGER NOT NULL,
                is_auto INTEGER NOT NULL DEFAULT 0,
                is_uploaded INTEGER NOT NULL DEFAULT 0,
                meta_json TEXT NOT NULL,
                filename TEXT NOT NULL UNIQUE,
                stored_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tx15_backups_email ON tx15_backups (email)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tx15_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL COLLATE NOCASE,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                detail TEXT NOT NULL,
                state_json TEXT NOT NULL,
                zip_filename TEXT NOT NULL UNIQUE,
                zip_size INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tx15_projects_email ON tx15_projects (email, kind)"
        )
        # Benutzername (Forum) - nachtraeglich ergaenzt, aeltere Konten haben
        # noch keinen (NULL) und werden beim ersten Forum-Beitrag danach gefragt.
        account_columns = {row["name"] for row in connection.execute("PRAGMA table_info(accounts)")}
        if "username" not in account_columns:
            connection.execute("ALTER TABLE accounts ADD COLUMN username TEXT")
        # Letzte Aktivitaet (Admin-Dashboard: wer ist gerade online), siehe
        # app.py's track_account_activity().
        if "last_seen_at" not in account_columns:
            connection.execute("ALTER TABLE accounts ADD COLUMN last_seen_at TEXT")
        # Rolle fuer die Anzeige im Forum/Profil ('admin' = "Administrator"), sonst NULL.
        if "role" not in account_columns:
            connection.execute("ALTER TABLE accounts ADD COLUMN role TEXT")
        # IP-Adressen fuer die Benutzerverwaltung im Admin-Dashboard (admin_users.py):
        # bei der Registrierung und zuletzt gesehen.
        for column in ("register_ip", "last_ip"):
            if column not in account_columns:
                connection.execute(f"ALTER TABLE accounts ADD COLUMN {column} TEXT")
        # Gesperrte IP-Adressen: von dort koennen keine Konten erstellt werden.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS blocked_ips (
                ip TEXT PRIMARY KEY,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username "
            "ON accounts (username COLLATE NOCASE) WHERE username IS NOT NULL"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS copters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL COLLATE NOCASE,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                frame TEXT NOT NULL DEFAULT '',
                motors TEXT NOT NULL DEFAULT '',
                flight_controller TEXT NOT NULL DEFAULT '',
                esc TEXT NOT NULL DEFAULT '',
                video TEXT NOT NULL DEFAULT '',
                receiver TEXT NOT NULL DEFAULT '',
                battery TEXT NOT NULL DEFAULT '',
                firmware TEXT NOT NULL DEFAULT '',
                weight_g INTEGER,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_copters_email ON copters (email)")
        # Ein Foto je Copter (community.py, community_media/copters) - nachtraeglich ergaenzt.
        copter_columns = {row["name"] for row in connection.execute("PRAGMA table_info(copters)")}
        if "image_filename" not in copter_columns:
            connection.execute("ALTER TABLE copters ADD COLUMN image_filename TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                author_email TEXT NOT NULL COLLATE NOCASE,
                created_at TEXT NOT NULL,
                last_post_at TEXT NOT NULL,
                post_count INTEGER NOT NULL DEFAULT 0,
                locked INTEGER NOT NULL DEFAULT 0,
                pinned INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_forum_threads_category "
            "ON forum_threads (category, pinned DESC, last_post_at DESC)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id INTEGER NOT NULL REFERENCES forum_threads (id) ON DELETE CASCADE,
                author_email TEXT NOT NULL COLLATE NOCASE,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                edited_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_categories (
                slug TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_forum_posts_thread ON forum_posts (thread_id, id)")
        # Oeffentliches Profil (Avatar, Status, Ueber mich, ...) - siehe community.py
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS account_profiles (
                email TEXT PRIMARY KEY COLLATE NOCASE,
                avatar_filename TEXT,
                status_text TEXT NOT NULL DEFAULT '',
                bio TEXT NOT NULL DEFAULT '',
                signature TEXT NOT NULL DEFAULT '',
                website TEXT NOT NULL DEFAULT '',
                youtube TEXT NOT NULL DEFAULT '',
                show_country INTEGER NOT NULL DEFAULT 1,
                show_copters INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Fernbedienung (Modell + Firmware) - nachtraeglich ergaenzt, angezeigt
        # in Profil und Forum ueber den Coptern (gleiche Sichtbarkeit wie die Copter).
        profile_columns = {row["name"] for row in connection.execute("PRAGMA table_info(account_profiles)")}
        for column in ("radio", "radio_firmware"):
            if column not in profile_columns:
                connection.execute(f"ALTER TABLE account_profiles ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        # Bilder und kurze Videos in Forenbeitraegen - siehe community.py
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL REFERENCES forum_posts (id) ON DELETE CASCADE,
                filename TEXT NOT NULL UNIQUE,
                original_name TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL,
                mime TEXT NOT NULL,
                size INTEGER NOT NULL,
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_forum_attachments_post ON forum_attachments (post_id, id)")
        # Drohnen-Ausweis (privat: nur Besitzer und Admin) - siehe pilot_license.py
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pilot_licenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                kind TEXT NOT NULL,
                number TEXT NOT NULL DEFAULT '',
                valid_until TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL,
                original_name TEXT NOT NULL DEFAULT '',
                mime TEXT NOT NULL,
                size INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                review_note TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT,
                uploaded_at TEXT NOT NULL
            )
            """
        )
        # Leaderboard des Blackbox Viewers (siehe leaderboard.py)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leaderboard_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT COLLATE NOCASE,
                pilot TEXT NOT NULL,
                score INTEGER NOT NULL,
                tricks INTEGER NOT NULL,
                best TEXT NOT NULL DEFAULT '',
                max_combo INTEGER NOT NULL DEFAULT 0,
                craft TEXT NOT NULL DEFAULT '',
                duration REAL NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT '',
                log_name TEXT NOT NULL DEFAULT '',
                log_index INTEGER NOT NULL DEFAULT 0,
                tricks_json TEXT NOT NULL DEFAULT '[]',
                settings_json TEXT NOT NULL DEFAULT '{}',
                video_filename TEXT,
                video_size INTEGER NOT NULL DEFAULT 0,
                video_mime TEXT NOT NULL DEFAULT '',
                video_start REAL NOT NULL DEFAULT 0,
                log_filename TEXT,
                log_size INTEGER NOT NULL DEFAULT 0,
                verified_at TEXT,
                report_alert_sent_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_leaderboard_score ON leaderboard_entries (score DESC, id)")
        # Akku-Klasse (Zellenzahl 1S ... 8S) - nachtraeglich ergaenzt, alte Eintraege NULL.
        leaderboard_columns = {row["name"] for row in connection.execute("PRAGMA table_info(leaderboard_entries)")}
        if "cells" not in leaderboard_columns:
            connection.execute("ALTER TABLE leaderboard_entries ADD COLUMN cells INTEGER")
        # Fingerabdruecke gegen doppelte Einreichungen (leaderboard.py): SHA-256 der Log-Datei
        # samt Log-Nummer, der Flugdaten (vom Viewer) und des Original-Videos (Anfang/Ende/Groesse).
        for column in ("log_hash", "flight_hash", "video_hash"):
            if column not in leaderboard_columns:
                connection.execute(f"ALTER TABLE leaderboard_entries ADD COLUMN {column} TEXT")
            connection.execute(f"CREATE INDEX IF NOT EXISTS idx_leaderboard_{column} ON leaderboard_entries ({column})")
        # Eigene Tricks der Community (trick_labels.py): beantragt von einem Benutzer,
        # freigegeben vom Admin. Schluessel im Viewer: "c<id>".
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trick_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                points INTEGER NOT NULL DEFAULT 150,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_by TEXT COLLATE NOCASE,
                created_at TEXT NOT NULL,
                approved_at TEXT
            )
            """
        )
        # Im Blackbox Viewer markierte Bereiche (Trainingsbeispiele fuer das
        # Community-Modell) samt kurzem Video-Ausschnitt fuer die Pruefung.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trick_labels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL COLLATE NOCASE,
                label TEXT NOT NULL,
                trick_type_id INTEGER REFERENCES trick_types (id) ON DELETE SET NULL,
                requested_name TEXT NOT NULL DEFAULT '',
                t_start REAL NOT NULL DEFAULT 0,
                t_end REAL NOT NULL DEFAULT 0,
                features_json TEXT NOT NULL,
                angles_json TEXT NOT NULL DEFAULT '[]',
                peak_rate REAL NOT NULL DEFAULT 0,
                mean_throttle REAL NOT NULL DEFAULT 0,
                comment TEXT NOT NULL DEFAULT '',
                log_name TEXT NOT NULL DEFAULT '',
                craft TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                clip_filename TEXT,
                clip_size INTEGER NOT NULL DEFAULT 0,
                clip_mime TEXT NOT NULL DEFAULT '',
                clip_start REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                review_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                reviewed_at TEXT
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trick_labels_status ON trick_labels (status, id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trick_labels_email ON trick_labels (email, id)")
        # VS-Duelle und Turniere (vs.py): ein Match hat zwei Piloten und je eine Einreichung
        # (Clip mit Overlay + Log); Turnier-Matches haben Runde und Platz im K.-o.-Baum.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tournaments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                organizer TEXT NOT NULL COLLATE NOCASE,
                max_players INTEGER NOT NULL DEFAULT 16,
                limit_s INTEGER NOT NULL DEFAULT 120,
                status TEXT NOT NULL DEFAULT 'signup',
                signup_open INTEGER NOT NULL DEFAULT 1,
                winner TEXT COLLATE NOCASE,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tournament_players (
                tournament_id INTEGER NOT NULL REFERENCES tournaments (id) ON DELETE CASCADE,
                email TEXT NOT NULL COLLATE NOCASE,
                seed INTEGER,
                joined_at TEXT NOT NULL,
                PRIMARY KEY (tournament_id, email)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS vs_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_by TEXT NOT NULL COLLATE NOCASE,
                pilot_a TEXT COLLATE NOCASE,
                pilot_b TEXT COLLATE NOCASE,
                limit_s INTEGER NOT NULL DEFAULT 120,
                status TEXT NOT NULL DEFAULT 'open',
                winner TEXT COLLATE NOCASE,
                result_note TEXT NOT NULL DEFAULT '',
                tournament_id INTEGER REFERENCES tournaments (id) ON DELETE CASCADE,
                round INTEGER NOT NULL DEFAULT 0,
                slot INTEGER NOT NULL DEFAULT 0,
                rated INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_vs_matches_pilots ON vs_matches (pilot_a, pilot_b)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_vs_matches_tournament ON vs_matches (tournament_id, round, slot)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS vs_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL REFERENCES vs_matches (id) ON DELETE CASCADE,
                pilot TEXT NOT NULL COLLATE NOCASE,
                uploaded_by TEXT NOT NULL COLLATE NOCASE,
                confirmed INTEGER NOT NULL DEFAULT 1,
                score INTEGER NOT NULL DEFAULT 0,
                tricks INTEGER NOT NULL DEFAULT 0,
                best TEXT NOT NULL DEFAULT '',
                max_combo INTEGER NOT NULL DEFAULT 0,
                cells INTEGER,
                craft TEXT NOT NULL DEFAULT '',
                window_start REAL NOT NULL DEFAULT 0,
                tricks_json TEXT NOT NULL DEFAULT '[]',
                settings_json TEXT NOT NULL DEFAULT '{}',
                source TEXT NOT NULL DEFAULT '',
                log_name TEXT NOT NULL DEFAULT '',
                log_index INTEGER NOT NULL DEFAULT 0,
                log_filename TEXT,
                log_size INTEGER NOT NULL DEFAULT 0,
                video_filename TEXT,
                video_size INTEGER NOT NULL DEFAULT 0,
                video_mime TEXT NOT NULL DEFAULT '',
                video_start REAL NOT NULL DEFAULT 0,
                log_hash TEXT,
                flight_hash TEXT,
                video_hash TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (match_id, pilot)
            )
            """
        )
        for column in ("log_hash", "flight_hash"):
            connection.execute(f"CREATE INDEX IF NOT EXISTS idx_vs_entries_{column} ON vs_entries ({column})")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS vs_ratings (
                email TEXT PRIMARY KEY COLLATE NOCASE,
                elo REAL NOT NULL DEFAULT 1000,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                draws INTEGER NOT NULL DEFAULT 0,
                best_score INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Einstellungen des FPV-Simulators je Konto (fpv_simulator.py), als JSON
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sim_settings (
                email TEXT PRIMARY KEY COLLATE NOCASE,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Maps aus dem Map-Editor des FPV-Simulators (fpv_simulator.py); shared = fuer alle sichtbar
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sim_maps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_email TEXT NOT NULL COLLATE NOCASE,
                name TEXT NOT NULL,
                data TEXT NOT NULL,
                gates INTEGER NOT NULL DEFAULT 0,
                objects INTEGER NOT NULL DEFAULT 0,
                shared INTEGER NOT NULL DEFAULT 0,
                plays INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sim_maps_owner ON sim_maps (owner_email)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_sim_maps_shared ON sim_maps (shared, updated_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leaderboard_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL REFERENCES leaderboard_entries (id) ON DELETE CASCADE,
                email TEXT NOT NULL COLLATE NOCASE,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE (entry_id, email)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_forum_posts_author ON forum_posts (author_email, id)")


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


def create_account(email, password_hash, full_name, address, phone, country, username=None, register_ip=None):
    """Legt ein neues Login-Konto an. Wirft sqlite3.IntegrityError, falls die
    E-Mail-Adresse (case-insensitiv, siehe UNIQUE COLLATE NOCASE) oder der
    Benutzername bereits registriert ist - der Aufrufer (app.py's register())
    prueft das vorher und zeigt eine verstaendliche Fehlermeldung."""
    with _get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO accounts (email, password_hash, full_name, address, phone, country, created_at, username,
                                  register_ip, last_ip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (email, password_hash, full_name, address, phone, country, _now_iso(), username, register_ip, register_ip),
        )
        return cursor.lastrowid


def get_account_by_email(email):
    """Liefert das Konto (sqlite3.Row) zu einer E-Mail-Adresse oder None."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM accounts WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()


def add_tx15_backup(email, label, created_ms, size, is_auto, is_uploaded, meta_json, filename):
    """Speichert die Metadaten eines Backups aus dem TX15 Backup Studio (die
    ZIP-Datei selbst liegt unter filename in tx15_backups.BACKUP_DIR)."""
    with _get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO tx15_backups
                (email, label, created_ms, size, is_auto, is_uploaded, meta_json, filename, stored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (email, label, created_ms, size, int(is_auto), int(is_uploaded), meta_json, filename, _now_iso()),
        )
        return cursor.lastrowid


def list_tx15_backups(email):
    """Alle Backups eines Kontos, neueste zuerst."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM tx15_backups WHERE email = ? COLLATE NOCASE ORDER BY created_ms DESC, id DESC",
            (email,),
        ).fetchall()


def get_tx15_backup(backup_id, email):
    """Ein Backup - nur, wenn es zum Konto gehoert, sonst None."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM tx15_backups WHERE id = ? AND email = ? COLLATE NOCASE",
            (backup_id, email),
        ).fetchone()


def delete_tx15_backup(backup_id, email):
    """Loescht den Datensatz (nur eigenes Konto) und liefert ihn zurueck, damit
    der Aufrufer die ZIP-Datei entfernen kann - None, falls nicht gefunden."""
    with _get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM tx15_backups WHERE id = ? AND email = ? COLLATE NOCASE",
            (backup_id, email),
        ).fetchone()
        if row is not None:
            connection.execute("DELETE FROM tx15_backups WHERE id = ?", (backup_id,))
        return row


def tx15_backup_filenames():
    """Dateinamen aller gespeicherten Backups (zum Aufraeumen verwaister
    Dateien, siehe tx15_backups._sweep_orphans())."""
    with _get_connection() as connection:
        return {row["filename"] for row in connection.execute("SELECT filename FROM tx15_backups")}


def tx15_backup_usage(email):
    """(Anzahl, belegte Bytes) der Backups eines Kontos."""
    with _get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS total FROM tx15_backups WHERE email = ? COLLATE NOCASE",
            (email,),
        ).fetchone()
        return row["n"], row["total"]


_PROJECT_LIST_COLUMNS = "id, email, kind, name, detail, zip_filename, zip_size, created_at, updated_at"


def add_tx15_project(email, kind, name, detail, state_json, zip_filename, zip_size):
    """Speichert ein Theme bzw. Lua-Skript aus den Drohnen Tools."""
    now = _now_iso()
    with _get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO tx15_projects
                (email, kind, name, detail, state_json, zip_filename, zip_size, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (email, kind, name, detail, state_json, zip_filename, zip_size, now, now),
        )
        return cursor.lastrowid


def update_tx15_project(project_id, email, name, detail, state_json, zip_filename, zip_size):
    """Ueberschreibt ein eigenes Projekt und liefert den alten ZIP-Dateinamen
    (zum Loeschen) - None, falls das Projekt nicht zum Konto gehoert."""
    with _get_connection() as connection:
        row = connection.execute(
            "SELECT zip_filename FROM tx15_projects WHERE id = ? AND email = ? COLLATE NOCASE",
            (project_id, email),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            """
            UPDATE tx15_projects
            SET name = ?, detail = ?, state_json = ?, zip_filename = ?, zip_size = ?, updated_at = ?
            WHERE id = ?
            """,
            (name, detail, state_json, zip_filename, zip_size, _now_iso(), project_id),
        )
        return row["zip_filename"]


def list_tx15_projects(email, kind=None):
    """Projekte eines Kontos ohne den (grossen) Studio-Stand, zuletzt
    geaenderte zuerst."""
    query = f"SELECT {_PROJECT_LIST_COLUMNS} FROM tx15_projects WHERE email = ? COLLATE NOCASE"
    params = [email]
    if kind is not None:
        query += " AND kind = ?"
        params.append(kind)
    with _get_connection() as connection:
        return connection.execute(query + " ORDER BY updated_at DESC, id DESC", params).fetchall()


def get_tx15_project(project_id, email):
    """Ein Projekt inklusive Studio-Stand - nur, wenn es zum Konto gehoert."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM tx15_projects WHERE id = ? AND email = ? COLLATE NOCASE",
            (project_id, email),
        ).fetchone()


def delete_tx15_project(project_id, email):
    """Loescht ein eigenes Projekt und liefert den Datensatz (fuer die
    ZIP-Datei) - None, falls nicht gefunden."""
    with _get_connection() as connection:
        row = connection.execute(
            f"SELECT {_PROJECT_LIST_COLUMNS} FROM tx15_projects WHERE id = ? AND email = ? COLLATE NOCASE",
            (project_id, email),
        ).fetchone()
        if row is not None:
            connection.execute("DELETE FROM tx15_projects WHERE id = ?", (project_id,))
        return row


def count_tx15_projects(email, kind):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM tx15_projects WHERE email = ? COLLATE NOCASE AND kind = ?",
            (email, kind),
        ).fetchone()[0]


def tx15_project_filenames():
    """ZIP-Dateinamen aller Projekte (zum Aufraeumen verwaister Dateien)."""
    with _get_connection() as connection:
        return {row["zip_filename"] for row in connection.execute("SELECT zip_filename FROM tx15_projects")}


# --- Konto: Profil, Benutzername, Passwort (konto.py) ---------------------


def get_account_by_username(username):
    """Konto zu einem Benutzernamen (case-insensitiv) oder None."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM accounts WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()


def update_account_profile(email, username, full_name, address, phone, country):
    with _get_connection() as connection:
        connection.execute(
            """
            UPDATE accounts SET username = ?, full_name = ?, address = ?, phone = ?, country = ?
            WHERE email = ? COLLATE NOCASE
            """,
            (username, full_name, address, phone, country, email),
        )


def update_account_password(email, password_hash):
    with _get_connection() as connection:
        connection.execute(
            "UPDATE accounts SET password_hash = ? WHERE email = ? COLLATE NOCASE", (password_hash, email)
        )


# --- Meine Copter (konto.py) ----------------------------------------------

COPTER_FIELDS = (
    "name", "category", "frame", "motors", "flight_controller", "esc", "video",
    "receiver", "battery", "firmware", "weight_g", "notes",
)


def list_copters(email):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM copters WHERE email = ? COLLATE NOCASE ORDER BY name COLLATE NOCASE, id", (email,)
        ).fetchall()


def get_copter(copter_id, email):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM copters WHERE id = ? AND email = ? COLLATE NOCASE", (copter_id, email)
        ).fetchone()


def count_copters(email):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM copters WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()[0]


def add_copter(email, fields):
    now = _now_iso()
    columns = ", ".join(COPTER_FIELDS)
    placeholders = ", ".join("?" for _ in COPTER_FIELDS)
    with _get_connection() as connection:
        cursor = connection.execute(
            f"INSERT INTO copters (email, {columns}, created_at, updated_at) VALUES (?, {placeholders}, ?, ?)",
            (email, *(fields[name] for name in COPTER_FIELDS), now, now),
        )
        return cursor.lastrowid


def update_copter(copter_id, email, fields):
    """Liefert True, wenn ein eigener Copter geaendert wurde."""
    assignments = ", ".join(f"{name} = ?" for name in COPTER_FIELDS)
    with _get_connection() as connection:
        cursor = connection.execute(
            f"UPDATE copters SET {assignments}, updated_at = ? WHERE id = ? AND email = ? COLLATE NOCASE",
            (*(fields[name] for name in COPTER_FIELDS), _now_iso(), copter_id, email),
        )
        return cursor.rowcount > 0


def delete_copter(copter_id, email):
    """Loescht einen eigenen Copter; liefert den Namen seines Fotos (oder None)."""
    with _get_connection() as connection:
        row = connection.execute(
            "SELECT image_filename FROM copters WHERE id = ? AND email = ? COLLATE NOCASE", (copter_id, email)
        ).fetchone()
        connection.execute("DELETE FROM copters WHERE id = ? AND email = ? COLLATE NOCASE", (copter_id, email))
        return row["image_filename"] if row is not None else None


def set_copter_image(copter_id, email, filename):
    """Foto setzen/entfernen (filename=None); liefert das bisherige Foto."""
    with _get_connection() as connection:
        row = connection.execute(
            "SELECT image_filename FROM copters WHERE id = ? AND email = ? COLLATE NOCASE", (copter_id, email)
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            "UPDATE copters SET image_filename = ?, updated_at = ? WHERE id = ?", (filename, _now_iso(), copter_id)
        )
        return row["image_filename"]


def get_public_copter(copter_id):
    """Copter mit Besitzer (Benutzername, show_copters) fuer die oeffentliche Seite."""
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT c.*, a.username, COALESCE(p.show_copters, 1) AS show_copters, p.avatar_filename
            FROM copters c
            JOIN accounts a ON a.email = c.email COLLATE NOCASE
            LEFT JOIN account_profiles p ON p.email = c.email COLLATE NOCASE
            WHERE c.id = ?
            """,
            (copter_id,),
        ).fetchone()


def copter_by_image(filename):
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT c.id, c.email, COALESCE(p.show_copters, 1) AS show_copters
            FROM copters c LEFT JOIN account_profiles p ON p.email = c.email COLLATE NOCASE
            WHERE c.image_filename = ?
            """,
            (filename,),
        ).fetchone()


def copter_image_filenames():
    with _get_connection() as connection:
        return {
            row[0] for row in connection.execute("SELECT image_filename FROM copters WHERE image_filename IS NOT NULL")
        }


# --- Forum (forum.py) -----------------------------------------------------

# Beitraege des Admins ohne eigenes Konto (forum.py's ADMIN_AUTHOR) erscheinen
# als TEAM_NAME.
FORUM_ADMIN_AUTHOR = "__admin__"
FORUM_TEAM_NAME = "Bollisoft-Team"

_THREAD_SELECT = f"""
    SELECT t.*,
           CASE WHEN t.author_email = '{FORUM_ADMIN_AUTHOR}' THEN '{FORUM_TEAM_NAME}' ELSE a.username END AS author_name,
           (SELECT CASE WHEN p2.author_email = '{FORUM_ADMIN_AUTHOR}' THEN '{FORUM_TEAM_NAME}' ELSE a2.username END
              FROM forum_posts p2
              LEFT JOIN accounts a2 ON a2.email = p2.author_email COLLATE NOCASE
             WHERE p2.thread_id = t.id ORDER BY p2.id DESC LIMIT 1) AS last_author_name
    FROM forum_threads t
    LEFT JOIN accounts a ON a.email = t.author_email COLLATE NOCASE
"""


def forum_category_stats():
    """{Kategorie: (Themen, Beitraege, letzter Beitrag)}"""
    with _get_connection() as connection:
        rows = connection.execute(
            "SELECT category, COUNT(*) AS threads, COALESCE(SUM(post_count), 0) AS posts, "
            "MAX(last_post_at) AS last_post_at FROM forum_threads GROUP BY category"
        ).fetchall()
        return {row["category"]: (row["threads"], row["posts"], row["last_post_at"]) for row in rows}


def forum_count_threads(category):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM forum_threads WHERE category = ?", (category,)
        ).fetchone()[0]


def forum_list_threads(category, limit, offset):
    with _get_connection() as connection:
        return connection.execute(
            _THREAD_SELECT + " WHERE t.category = ? ORDER BY t.pinned DESC, t.last_post_at DESC, t.id DESC LIMIT ? OFFSET ?",
            (category, limit, offset),
        ).fetchall()


def forum_latest_threads(limit):
    with _get_connection() as connection:
        return connection.execute(
            _THREAD_SELECT + " ORDER BY t.last_post_at DESC, t.id DESC LIMIT ?", (limit,)
        ).fetchall()


def _search_connection(connection):
    """lower() von SQLite kennt nur ASCII - fuer Umlaute die Python-Variante."""
    connection.create_function(
        "fold", 1, lambda value: value.lower() if isinstance(value, str) else value, deterministic=True
    )


def _like_pattern(term):
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def forum_search_threads(terms, category=None, titles_only=False, limit=500):
    """Themen, in denen jeder Suchbegriff (kleingeschrieben) vorkommt: im Titel
    oder - ausser bei titles_only - zusammen im Titel/einem Beitrag.
    Liefert [{id, title_hit}], Titeltreffer zuerst, dann nach letzter Aktivitaet."""
    patterns = [_like_pattern(term) for term in terms]
    title_all = " AND ".join("fold(t.title) LIKE ? ESCAPE '\\'" for _ in terms)
    post_all = " AND ".join("(fold(t.title) LIKE ? ESCAPE '\\' OR fold(p.body) LIKE ? ESCAPE '\\')" for _ in terms)
    params = list(patterns)
    if titles_only:
        where = title_all
        params += patterns
    else:
        where = f"EXISTS (SELECT 1 FROM forum_posts p WHERE p.thread_id = t.id AND {post_all})"
        params += [value for pattern in patterns for value in (pattern, pattern)]
    if category:
        where += " AND t.category = ?"
        params.append(category)
    params.append(limit)
    with _get_connection() as connection:
        _search_connection(connection)
        return connection.execute(
            f"SELECT t.id, ({title_all}) AS title_hit FROM forum_threads t WHERE {where} "
            "ORDER BY title_hit DESC, t.last_post_at DESC, t.id DESC LIMIT ?",
            params,
        ).fetchall()


def forum_search_hits(thread_ids, terms):
    """{thema_id: {hits, first_id, body}} - Beitraege, deren Text alle Begriffe enthaelt."""
    thread_ids = list(thread_ids)
    if not thread_ids:
        return {}
    patterns = [_like_pattern(term) for term in terms]
    body_all = " AND ".join("fold(p.body) LIKE ? ESCAPE '\\'" for _ in terms)
    marks = ", ".join("?" for _ in thread_ids)
    with _get_connection() as connection:
        _search_connection(connection)
        rows = connection.execute(
            f"""
            WITH hits AS (
                SELECT p.thread_id, COUNT(*) AS hits, MIN(p.id) AS first_id
                FROM forum_posts p
                WHERE p.thread_id IN ({marks}) AND {body_all}
                GROUP BY p.thread_id
            )
            SELECT hits.*, first.body FROM hits JOIN forum_posts first ON first.id = hits.first_id
            """,
            [*thread_ids, *patterns],
        ).fetchall()
    return {row["thread_id"]: dict(row) for row in rows}


def forum_threads_by_ids(thread_ids):
    thread_ids = list(thread_ids)
    if not thread_ids:
        return []
    marks = ", ".join("?" for _ in thread_ids)
    with _get_connection() as connection:
        return connection.execute(_THREAD_SELECT + f" WHERE t.id IN ({marks})", thread_ids).fetchall()


def forum_get_thread(thread_id):
    with _get_connection() as connection:
        return connection.execute(_THREAD_SELECT + " WHERE t.id = ?", (thread_id,)).fetchone()


def forum_list_posts(thread_id, limit, offset):
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT p.*,
                   CASE WHEN p.author_email = ? THEN ? ELSE a.username END AS author_name,
                   p.author_email = ? AS author_is_team,
                   (SELECT COUNT(*) FROM copters c WHERE c.email = p.author_email COLLATE NOCASE) AS author_copters,
                   (SELECT COUNT(*) FROM forum_posts p3 WHERE p3.author_email = p.author_email COLLATE NOCASE) AS author_posts
            FROM forum_posts p
            LEFT JOIN accounts a ON a.email = p.author_email COLLATE NOCASE
            WHERE p.thread_id = ? ORDER BY p.id LIMIT ? OFFSET ?
            """,
            (FORUM_ADMIN_AUTHOR, FORUM_TEAM_NAME, FORUM_ADMIN_AUTHOR, thread_id, limit, offset),
        ).fetchall()


def forum_post_index(thread_id, post_id):
    """0-basierte Position eines Beitrags im Thema (fuer die Seitenzahl)."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM forum_posts WHERE thread_id = ? AND id < ?", (thread_id, post_id)
        ).fetchone()[0]


def forum_get_post(post_id):
    """Beitrag mit Thema-Infos; is_first = erster Beitrag des Themas."""
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT p.*, t.title, t.category, t.locked,
                   p.id = (SELECT MIN(id) FROM forum_posts WHERE thread_id = p.thread_id) AS is_first
            FROM forum_posts p JOIN forum_threads t ON t.id = p.thread_id
            WHERE p.id = ?
            """,
            (post_id,),
        ).fetchone()


def forum_create_thread(category, title, email, body):
    now = _now_iso()
    with _get_connection() as connection:
        thread_id = connection.execute(
            """
            INSERT INTO forum_threads (category, title, author_email, created_at, last_post_at, post_count)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (category, title, email, now, now),
        ).lastrowid
        post_id = connection.execute(
            "INSERT INTO forum_posts (thread_id, author_email, body, created_at) VALUES (?, ?, ?, ?)",
            (thread_id, email, body, now),
        ).lastrowid
        return thread_id, post_id


def forum_add_post(thread_id, email, body):
    now = _now_iso()
    with _get_connection() as connection:
        post_id = connection.execute(
            "INSERT INTO forum_posts (thread_id, author_email, body, created_at) VALUES (?, ?, ?, ?)",
            (thread_id, email, body, now),
        ).lastrowid
        connection.execute(
            "UPDATE forum_threads SET last_post_at = ?, post_count = post_count + 1 WHERE id = ?",
            (now, thread_id),
        )
        return post_id


def forum_update_post(post_id, body, title=None):
    with _get_connection() as connection:
        connection.execute(
            "UPDATE forum_posts SET body = ?, edited_at = ? WHERE id = ?", (body, _now_iso(), post_id)
        )
        if title is not None:
            connection.execute(
                "UPDATE forum_threads SET title = ? WHERE id = (SELECT thread_id FROM forum_posts WHERE id = ?)",
                (title, post_id),
            )


def forum_delete_post(post_id):
    """Loescht einen Beitrag; ist es der erste, das ganze Thema.
    Liefert (thread_id, thema_geloescht) oder None."""
    with _get_connection() as connection:
        row = connection.execute(
            "SELECT thread_id, id = (SELECT MIN(id) FROM forum_posts WHERE thread_id = p.thread_id) AS is_first "
            "FROM forum_posts p WHERE id = ?",
            (post_id,),
        ).fetchone()
        if row is None:
            return None
        if row["is_first"]:
            connection.execute("DELETE FROM forum_posts WHERE thread_id = ?", (row["thread_id"],))
            connection.execute("DELETE FROM forum_threads WHERE id = ?", (row["thread_id"],))
            return row["thread_id"], True
        connection.execute("DELETE FROM forum_posts WHERE id = ?", (post_id,))
        connection.execute(
            """
            UPDATE forum_threads SET
                post_count = (SELECT COUNT(*) FROM forum_posts WHERE thread_id = ?),
                last_post_at = (SELECT MAX(created_at) FROM forum_posts WHERE thread_id = ?)
            WHERE id = ?
            """,
            (row["thread_id"], row["thread_id"], row["thread_id"]),
        )
        return row["thread_id"], False


def forum_set_thread_flag(thread_id, flag, value):
    if flag not in ("locked", "pinned"):
        raise ValueError(flag)
    with _get_connection() as connection:
        connection.execute(f"UPDATE forum_threads SET {flag} = ? WHERE id = ?", (int(bool(value)), thread_id))


def forum_last_post_time(email):
    """Zeitpunkt (ISO) des letzten Beitrags eines Kontos - fuer die Schreibbremse."""
    with _get_connection() as connection:
        row = connection.execute(
            "SELECT MAX(created_at) FROM forum_posts WHERE author_email = ? COLLATE NOCASE", (email,)
        ).fetchone()
        return row[0]


# --- Forum: Kategorien ("Ueberthemen") und Admin-Funktionen -----------------


def forum_list_categories():
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM forum_categories ORDER BY sort_order, name COLLATE NOCASE"
        ).fetchall()


def forum_get_category(slug):
    with _get_connection() as connection:
        return connection.execute("SELECT * FROM forum_categories WHERE slug = ?", (slug,)).fetchone()


def forum_create_category(slug, name, description, sort_order):
    with _get_connection() as connection:
        connection.execute(
            "INSERT INTO forum_categories (slug, name, description, sort_order, created_at) VALUES (?, ?, ?, ?, ?)",
            (slug, name, description, sort_order, _now_iso()),
        )


def forum_update_category(slug, name, description, sort_order):
    with _get_connection() as connection:
        connection.execute(
            "UPDATE forum_categories SET name = ?, description = ?, sort_order = ? WHERE slug = ?",
            (name, description, sort_order, slug),
        )


def forum_delete_category(slug):
    """Loescht die Kategorie mit allen Themen und Beitraegen."""
    with _get_connection() as connection:
        connection.execute(
            "DELETE FROM forum_posts WHERE thread_id IN (SELECT id FROM forum_threads WHERE category = ?)", (slug,)
        )
        connection.execute("DELETE FROM forum_threads WHERE category = ?", (slug,))
        connection.execute("DELETE FROM forum_categories WHERE slug = ?", (slug,))


def forum_delete_thread(thread_id):
    """Loescht ein Thema mit allen Beitraegen. Liefert die Kategorie oder None."""
    with _get_connection() as connection:
        row = connection.execute("SELECT category FROM forum_threads WHERE id = ?", (thread_id,)).fetchone()
        if row is None:
            return None
        connection.execute("DELETE FROM forum_posts WHERE thread_id = ?", (thread_id,))
        connection.execute("DELETE FROM forum_threads WHERE id = ?", (thread_id,))
        return row["category"]


def forum_move_thread(thread_id, category):
    with _get_connection() as connection:
        connection.execute("UPDATE forum_threads SET category = ? WHERE id = ?", (category, thread_id))


# --- Admin-Dashboard: Benutzer ---------------------------------------------


def touch_account_seen(email, ip=None):
    with _get_connection() as connection:
        connection.execute(
            "UPDATE accounts SET last_seen_at = ?, last_ip = COALESCE(?, last_ip) WHERE email = ? COLLATE NOCASE",
            (_now_iso(), ip, email),
        )


def get_account_by_id(account_id):
    with _get_connection() as connection:
        return connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()


def list_accounts_admin():
    """Alle Konten fuer die Benutzerverwaltung, mit belegtem Speicher (Bytes laut Datenbank)
    und Anzahl Forenbeitraege. Avatare/Copter-Fotos rechnet admin_users.py dazu."""
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT a.id, a.email, a.username, a.full_name, a.country, a.role, a.created_at, a.last_seen_at,
                   a.register_ip, a.last_ip,
                   p.avatar_filename,
                   (SELECT COALESCE(SUM(size), 0) FROM tx15_backups b WHERE b.email = a.email COLLATE NOCASE)
                 + (SELECT COALESCE(SUM(zip_size), 0) FROM tx15_projects t WHERE t.email = a.email COLLATE NOCASE)
                 + (SELECT COALESCE(SUM(video_size + log_size), 0) FROM leaderboard_entries e WHERE e.email = a.email COLLATE NOCASE)
                 + (SELECT COALESCE(SUM(clip_size), 0) FROM trick_labels t WHERE t.email = a.email COLLATE NOCASE)
                 + (SELECT COALESCE(SUM(f.size), 0) FROM forum_attachments f JOIN forum_posts fp ON fp.id = f.post_id
                     WHERE fp.author_email = a.email COLLATE NOCASE)
                 + (SELECT COALESCE(SUM(size), 0) FROM pilot_licenses l WHERE l.email = a.email COLLATE NOCASE) AS stored_bytes,
                   (SELECT COUNT(*) FROM forum_posts fp WHERE fp.author_email = a.email COLLATE NOCASE) AS post_count
            FROM accounts a
            LEFT JOIN account_profiles p ON p.email = a.email COLLATE NOCASE
            ORDER BY a.created_at DESC, a.id DESC
            """
        ).fetchall()


def list_copter_images(email):
    with _get_connection() as connection:
        return [row[0] for row in connection.execute(
            "SELECT image_filename FROM copters WHERE email = ? COLLATE NOCASE AND image_filename IS NOT NULL", (email,)
        )]


def forum_posts_by(email):
    """Alle Beitraege eines Kontos (neueste zuerst) mit Thema und Anhangzahl."""
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT p.id, p.thread_id, p.body, p.created_at, t.title,
                   p.id = (SELECT MIN(id) FROM forum_posts WHERE thread_id = p.thread_id) AS is_first,
                   (SELECT COUNT(*) FROM forum_posts o WHERE o.thread_id = p.thread_id) AS thread_posts
            FROM forum_posts p JOIN forum_threads t ON t.id = p.thread_id
            WHERE p.author_email = ? COLLATE NOCASE ORDER BY p.id DESC
            """,
            (email,),
        ).fetchall()


def forum_attachments_by(email):
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT f.*, p.thread_id FROM forum_attachments f JOIN forum_posts p ON p.id = f.post_id
            WHERE p.author_email = ? COLLATE NOCASE ORDER BY f.id DESC
            """,
            (email,),
        ).fetchall()


def leaderboard_rows_for(email):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM leaderboard_entries WHERE email = ? COLLATE NOCASE ORDER BY score DESC, id", (email,)
        ).fetchall()


def delete_account_rows(email):
    """Entfernt Konto, Profil, Copter und abgegebene Meldungen. Dateien und uebrige
    Inhalte (Backups, Projekte, Beitraege, Leaderboard, Ausweis) raeumt admin_users.py vorher ab."""
    with _get_connection() as connection:
        connection.execute("DELETE FROM leaderboard_reports WHERE email = ? COLLATE NOCASE", (email,))
        connection.execute("DELETE FROM copters WHERE email = ? COLLATE NOCASE", (email,))
        connection.execute("DELETE FROM sim_settings WHERE email = ? COLLATE NOCASE", (email,))
        connection.execute("DELETE FROM sim_maps WHERE owner_email = ? COLLATE NOCASE", (email,))
        connection.execute("DELETE FROM account_profiles WHERE email = ? COLLATE NOCASE", (email,))
        connection.execute("DELETE FROM accounts WHERE email = ? COLLATE NOCASE", (email,))


def block_ip(ip, reason):
    with _get_connection() as connection:
        connection.execute(
            "INSERT INTO blocked_ips (ip, reason, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT (ip) DO UPDATE SET reason = excluded.reason",
            (ip, reason, _now_iso()),
        )


def unblock_ip(ip):
    with _get_connection() as connection:
        connection.execute("DELETE FROM blocked_ips WHERE ip = ?", (ip,))


def is_ip_blocked(ip):
    if not ip:
        return False
    with _get_connection() as connection:
        return connection.execute("SELECT 1 FROM blocked_ips WHERE ip = ?", (ip,)).fetchone() is not None


def list_blocked_ips():
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT b.*, (SELECT COUNT(*) FROM accounts a WHERE a.register_ip = b.ip OR a.last_ip = b.ip) AS accounts
            FROM blocked_ips b ORDER BY b.created_at DESC
            """
        ).fetchall()


def count_accounts():
    with _get_connection() as connection:
        return connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]


def list_online_accounts(since_iso):
    """Konten mit Aktivitaet seit since_iso (UTC-ISO), zuletzt aktive zuerst."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT email, username, full_name, country, created_at, last_seen_at FROM accounts "
            "WHERE last_seen_at >= ? ORDER BY last_seen_at DESC",
            (since_iso,),
        ).fetchall()


# --- Leaderboard des Blackbox Viewers (leaderboard.py) -----------------------

# country: Land des Kontos (Code), nur wenn es im Profil nicht ausgeblendet ist.
_LEADERBOARD_SELECT = """
    SELECT e.*, a.username AS account_username,
           CASE WHEN COALESCE(p.show_country, 1) = 1 THEN NULLIF(a.country, '') END AS country,
           p.avatar_filename,
           (SELECT COUNT(*) FROM leaderboard_reports r WHERE r.entry_id = e.id) AS report_count
    FROM leaderboard_entries e
    LEFT JOIN accounts a ON a.email = e.email COLLATE NOCASE
    LEFT JOIN account_profiles p ON p.email = e.email COLLATE NOCASE
"""


def leaderboard_count():
    with _get_connection() as connection:
        return connection.execute("SELECT COUNT(*) FROM leaderboard_entries").fetchone()[0]


def leaderboard_list(limit, offset=0, cells=None):
    """Eintraege nach Punkten (bei Gleichstand der fruehere zuerst), optional nur eine Akku-Klasse."""
    where, params = ("", ()) if cells is None else (" WHERE e.cells = ?", (cells,))
    with _get_connection() as connection:
        return connection.execute(
            _LEADERBOARD_SELECT + where + " ORDER BY e.score DESC, e.id ASC LIMIT ? OFFSET ?", (*params, limit, offset)
        ).fetchall()


def leaderboard_by_reports():
    """Fuer das Admin-Dashboard: gemeldete Eintraege zuerst."""
    with _get_connection() as connection:
        return connection.execute(
            _LEADERBOARD_SELECT + " ORDER BY report_count DESC, e.score DESC, e.id ASC"
        ).fetchall()


def leaderboard_get(entry_id):
    with _get_connection() as connection:
        return connection.execute(_LEADERBOARD_SELECT + " WHERE e.id = ?", (entry_id,)).fetchone()


def leaderboard_rank(entry_id):
    """Platz (1-basiert) eines Eintrags in der Rangliste."""
    with _get_connection() as connection:
        row = connection.execute("SELECT score, id FROM leaderboard_entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return None
        return connection.execute(
            "SELECT COUNT(*) FROM leaderboard_entries WHERE score > ? OR (score = ? AND id < ?)",
            (row["score"], row["score"], row["id"]),
        ).fetchone()[0] + 1


def leaderboard_add(fields):
    """fields: Spalte -> Wert; created_at wird ergaenzt, falls es fehlt."""
    fields = {"created_at": _now_iso(), **fields}
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    with _get_connection() as connection:
        cursor = connection.execute(
            f"INSERT INTO leaderboard_entries ({columns}) VALUES ({placeholders})", tuple(fields.values())
        )
        return cursor.lastrowid


def leaderboard_delete(entry_id):
    """Loescht einen Eintrag samt Meldungen; liefert ihn (fuer die Dateien) oder None."""
    with _get_connection() as connection:
        row = connection.execute("SELECT * FROM leaderboard_entries WHERE id = ?", (entry_id,)).fetchone()
        if row is not None:
            connection.execute("DELETE FROM leaderboard_reports WHERE entry_id = ?", (entry_id,))
            connection.execute("DELETE FROM leaderboard_entries WHERE id = ?", (entry_id,))
        return row


def leaderboard_set_verified(entry_id, verified):
    with _get_connection() as connection:
        connection.execute(
            "UPDATE leaderboard_entries SET verified_at = ? WHERE id = ?",
            (_now_iso() if verified else None, entry_id),
        )


def leaderboard_set_alert_sent(entry_id):
    with _get_connection() as connection:
        connection.execute(
            "UPDATE leaderboard_entries SET report_alert_sent_at = ? WHERE id = ?", (_now_iso(), entry_id)
        )


def leaderboard_last_submission(email):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT MAX(created_at) FROM leaderboard_entries WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()[0]


def leaderboard_find_duplicate(log_hash=None, flight_hash=None, video_hash=None):
    """Erster Eintrag mit gleichem Log, gleichen Flugdaten oder gleichem Video: (row, art) oder (None, None)."""
    with _get_connection() as connection:
        for column, value in (("log_hash", log_hash), ("flight_hash", flight_hash), ("video_hash", video_hash)):
            if not value:
                continue
            row = connection.execute(
                f"SELECT id, pilot, email FROM leaderboard_entries WHERE {column} = ? ORDER BY id LIMIT 1", (value,)
            ).fetchone()
            if row is not None:
                return row, column
    return None, None


def leaderboard_without_log_hash():
    with _get_connection() as connection:
        return connection.execute(
            "SELECT id, log_filename, log_index FROM leaderboard_entries WHERE log_hash IS NULL AND log_filename IS NOT NULL"
        ).fetchall()


def leaderboard_set_log_hash(entry_id, log_hash):
    with _get_connection() as connection:
        connection.execute("UPDATE leaderboard_entries SET log_hash = ? WHERE id = ?", (log_hash, entry_id))


def leaderboard_filenames():
    """Alle Video- und Log-Dateinamen (zum Aufraeumen verwaister Dateien)."""
    with _get_connection() as connection:
        names = set()
        for row in connection.execute("SELECT video_filename, log_filename FROM leaderboard_entries"):
            names.update(name for name in row if name)
        return names


# --- Trick-Markierungen und eigene Tricks (trick_labels.py) -------------------------

_TRICK_LABEL_SELECT = """
    SELECT l.*, a.username AS account_username, a.id AS account_id,
           t.name AS type_name, t.status AS type_status, t.points AS type_points
    FROM trick_labels l
    LEFT JOIN accounts a ON a.email = l.email COLLATE NOCASE
    LEFT JOIN trick_types t ON t.id = l.trick_type_id
"""


def trick_type_create(name, description, email):
    with _get_connection() as connection:
        return connection.execute(
            "INSERT INTO trick_types (name, description, requested_by, created_at) VALUES (?, ?, ?, ?)",
            (name, description, email, _now_iso()),
        ).lastrowid


def trick_type_get(type_id):
    with _get_connection() as connection:
        return connection.execute("SELECT * FROM trick_types WHERE id = ?", (type_id,)).fetchone()


def trick_type_by_name(name):
    """Beantragter oder freigegebener Trick gleichen Namens (gross/klein egal)."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM trick_types WHERE name = ? COLLATE NOCASE AND status IN ('pending', 'approved', 'hidden') "
            "ORDER BY status = 'approved' DESC, id LIMIT 1",
            (name,),
        ).fetchone()


def trick_types_list():
    """Alle eigenen Tricks mit Anzahl Markierungen (fuer das Admin-Dashboard)."""
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT t.*, a.username AS requested_username,
                   (SELECT COUNT(*) FROM trick_labels l WHERE l.trick_type_id = t.id AND l.status = 'approved') AS approved_labels,
                   (SELECT COUNT(*) FROM trick_labels l WHERE l.trick_type_id = t.id AND l.status = 'pending') AS pending_labels
            FROM trick_types t
            LEFT JOIN accounts a ON a.email = t.requested_by COLLATE NOCASE
            ORDER BY CASE t.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 WHEN 'hidden' THEN 2 ELSE 3 END, t.name COLLATE NOCASE
            """
        ).fetchall()


def trick_types_with_status(*statuses):
    marks = ", ".join("?" for _ in statuses)
    with _get_connection() as connection:
        return connection.execute(
            f"SELECT * FROM trick_types WHERE status IN ({marks}) ORDER BY name COLLATE NOCASE", statuses
        ).fetchall()


def trick_types_requested_by(email):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM trick_types WHERE requested_by = ? COLLATE NOCASE AND status = 'pending' ORDER BY id",
            (email,),
        ).fetchall()


def trick_type_update(type_id, **fields):
    if not fields:
        return
    if fields.get("status") == "approved":
        fields.setdefault("approved_at", _now_iso())
    assignments = ", ".join(f"{column} = ?" for column in fields)
    with _get_connection() as connection:
        connection.execute(f"UPDATE trick_types SET {assignments} WHERE id = ?", (*fields.values(), type_id))


def trick_labels_of_type(type_id):
    with _get_connection() as connection:
        return connection.execute("SELECT * FROM trick_labels WHERE trick_type_id = ? ORDER BY id", (type_id,)).fetchall()


def trick_labels_reject_pending_of_type(type_id, note):
    """Offene Markierungen eines abgelehnten Tricks mit ablehnen; liefert die Anzahl."""
    with _get_connection() as connection:
        return connection.execute(
            "UPDATE trick_labels SET status = 'rejected', review_note = ?, reviewed_at = ? "
            "WHERE trick_type_id = ? AND status = 'pending'",
            (note, _now_iso(), type_id),
        ).rowcount


def trick_type_delete(type_id):
    with _get_connection() as connection:
        connection.execute("DELETE FROM trick_types WHERE id = ?", (type_id,))


def trick_type_open_labels(type_id):
    """Markierungen, die einen Trick noch tragen (offen oder freigegeben)."""
    with _get_connection() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM trick_labels WHERE trick_type_id = ? AND status IN ('pending', 'approved')", (type_id,)
        ).fetchone()[0]


def trick_label_add(fields):
    fields = {"created_at": _now_iso(), **fields}
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    with _get_connection() as connection:
        return connection.execute(
            f"INSERT INTO trick_labels ({columns}) VALUES ({placeholders})", tuple(fields.values())
        ).lastrowid


def trick_label_get(label_id):
    with _get_connection() as connection:
        return connection.execute(_TRICK_LABEL_SELECT + " WHERE l.id = ?", (label_id,)).fetchone()


def trick_labels_by_status(status, limit=500):
    order = "ASC" if status == "pending" else "DESC"
    with _get_connection() as connection:
        return connection.execute(
            _TRICK_LABEL_SELECT + f" WHERE l.status = ? ORDER BY l.id {order} LIMIT ?", (status, limit)
        ).fetchall()


def trick_labels_for(email, limit=200):
    with _get_connection() as connection:
        return connection.execute(
            _TRICK_LABEL_SELECT + " WHERE l.email = ? COLLATE NOCASE ORDER BY l.id DESC LIMIT ?", (email, limit)
        ).fetchall()


def trick_labels_pending_count(email):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM trick_labels WHERE email = ? COLLATE NOCASE AND status = 'pending'", (email,)
        ).fetchone()[0]


def trick_label_last(email):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT MAX(created_at) FROM trick_labels WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()[0]


def trick_label_review(label_id, status, label, trick_type_id, note):
    with _get_connection() as connection:
        connection.execute(
            "UPDATE trick_labels SET status = ?, label = ?, trick_type_id = ?, review_note = ?, reviewed_at = ? WHERE id = ?",
            (status, label, trick_type_id, note, _now_iso(), label_id),
        )


def trick_label_delete(label_id):
    """Loescht eine Markierung; liefert sie (fuer den Clip) oder None."""
    with _get_connection() as connection:
        row = connection.execute("SELECT * FROM trick_labels WHERE id = ?", (label_id,)).fetchone()
        if row is not None:
            connection.execute("DELETE FROM trick_labels WHERE id = ?", (label_id,))
        return row


def trick_examples(limit=5000):
    """Freigegebene Trainingsbeispiele: eingebaute Tricks und freigegebene eigene Tricks."""
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT l.label, l.features_json FROM trick_labels l
            LEFT JOIN trick_types t ON t.id = l.trick_type_id
            WHERE l.status = 'approved' AND (l.trick_type_id IS NULL OR t.status = 'approved')
            ORDER BY l.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()


def trick_label_filenames():
    with _get_connection() as connection:
        return {row[0] for row in connection.execute("SELECT clip_filename FROM trick_labels WHERE clip_filename IS NOT NULL")}


# --- VS-Duelle und Turniere (vs.py) -----------------------------------------------

_MATCH_SELECT = """
    SELECT m.*, a.username AS username_a, b.username AS username_b, w.username AS username_winner,
           pa.avatar_filename AS avatar_a, pb.avatar_filename AS avatar_b, t.name AS tournament_name
    FROM vs_matches m
    LEFT JOIN accounts a ON a.email = m.pilot_a COLLATE NOCASE
    LEFT JOIN accounts b ON b.email = m.pilot_b COLLATE NOCASE
    LEFT JOIN accounts w ON w.email = m.winner COLLATE NOCASE
    LEFT JOIN account_profiles pa ON pa.email = m.pilot_a COLLATE NOCASE
    LEFT JOIN account_profiles pb ON pb.email = m.pilot_b COLLATE NOCASE
    LEFT JOIN tournaments t ON t.id = m.tournament_id
"""


def search_candidates(exclude_email, limit=20000):
    """Alle Konten mit Benutzername fuer die unscharfe Suche (vs.rank_users)."""
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT a.username, a.email, p.avatar_filename,
                   CASE WHEN COALESCE(p.show_country, 1) = 1 THEN NULLIF(a.country, '') END AS country,
                   r.elo
            FROM accounts a
            LEFT JOIN account_profiles p ON p.email = a.email COLLATE NOCASE
            LEFT JOIN vs_ratings r ON r.email = a.email COLLATE NOCASE
            WHERE a.username IS NOT NULL AND a.username <> '' AND a.email <> ? COLLATE NOCASE
            LIMIT ?
            """,
            (exclude_email or "", limit),
        ).fetchall()


def vs_create_match(fields):
    fields = {"created_at": _now_iso(), **fields}
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    with _get_connection() as connection:
        return connection.execute(
            f"INSERT INTO vs_matches ({columns}) VALUES ({placeholders})", tuple(fields.values())
        ).lastrowid


def vs_get_match(match_id):
    with _get_connection() as connection:
        return connection.execute(_MATCH_SELECT + " WHERE m.id = ?", (match_id,)).fetchone()


def vs_update_match(match_id, **fields):
    assignments = ", ".join(f"{column} = ?" for column in fields)
    with _get_connection() as connection:
        connection.execute(f"UPDATE vs_matches SET {assignments} WHERE id = ?", (*fields.values(), match_id))


def vs_matches_for(email, limit=100):
    with _get_connection() as connection:
        return connection.execute(
            _MATCH_SELECT + " WHERE (m.pilot_a = ? COLLATE NOCASE OR m.pilot_b = ? COLLATE NOCASE OR m.created_by = ? COLLATE NOCASE)"
            " AND m.status <> 'cancelled' ORDER BY m.status = 'open' DESC, COALESCE(m.completed_at, m.created_at) DESC LIMIT ?",
            (email, email, email, limit),
        ).fetchall()


def vs_recent_matches(limit=20):
    """Zuletzt entschiedene Duelle mit zwei Fluegen (ohne Freilose und kampflose Siege)."""
    with _get_connection() as connection:
        return connection.execute(
            _MATCH_SELECT + " WHERE m.status = 'done' AND m.result_note = '' ORDER BY m.completed_at DESC LIMIT ?", (limit,)
        ).fetchall()


def vs_open_count(email):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM vs_matches WHERE created_by = ? COLLATE NOCASE AND status = 'open' AND tournament_id IS NULL",
            (email,),
        ).fetchone()[0]


def vs_add_entry(fields):
    fields = {"created_at": _now_iso(), **fields}
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    with _get_connection() as connection:
        return connection.execute(
            f"INSERT INTO vs_entries ({columns}) VALUES ({placeholders})", tuple(fields.values())
        ).lastrowid


def vs_entries(match_id):
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT e.*, a.username AS pilot_username, u.username AS uploader_username
            FROM vs_entries e
            LEFT JOIN accounts a ON a.email = e.pilot COLLATE NOCASE
            LEFT JOIN accounts u ON u.email = e.uploaded_by COLLATE NOCASE
            WHERE e.match_id = ? ORDER BY e.id
            """,
            (match_id,),
        ).fetchall()


def vs_get_entry(entry_id):
    with _get_connection() as connection:
        return connection.execute("SELECT * FROM vs_entries WHERE id = ?", (entry_id,)).fetchone()


def vs_confirm_entry(entry_id):
    with _get_connection() as connection:
        connection.execute("UPDATE vs_entries SET confirmed = 1 WHERE id = ?", (entry_id,))


def vs_delete_entry(entry_id):
    with _get_connection() as connection:
        row = connection.execute("SELECT * FROM vs_entries WHERE id = ?", (entry_id,)).fetchone()
        if row is not None:
            connection.execute("DELETE FROM vs_entries WHERE id = ?", (entry_id,))
        return row


def vs_entry_with_hash(log_hash=None, flight_hash=None):
    """Erste VS-Einreichung mit demselben Log oder denselben Flugdaten."""
    with _get_connection() as connection:
        for column, value in (("log_hash", log_hash), ("flight_hash", flight_hash)):
            if value:
                row = connection.execute(f"SELECT * FROM vs_entries WHERE {column} = ? LIMIT 1", (value,)).fetchone()
                if row is not None:
                    return row
    return None


def vs_entry_filenames():
    with _get_connection() as connection:
        names = set()
        for row in connection.execute("SELECT video_filename, log_filename FROM vs_entries"):
            names.update(name for name in row if name)
        return names


def vs_entries_by(email):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM vs_entries WHERE pilot = ? COLLATE NOCASE OR uploaded_by = ? COLLATE NOCASE", (email, email)
        ).fetchall()


def vs_get_rating(email):
    with _get_connection() as connection:
        return connection.execute("SELECT * FROM vs_ratings WHERE email = ? COLLATE NOCASE", (email,)).fetchone()


def vs_save_rating(email, elo, wins, losses, draws, best_score):
    with _get_connection() as connection:
        connection.execute(
            """
            INSERT INTO vs_ratings (email, elo, wins, losses, draws, best_score, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (email) DO UPDATE SET elo = excluded.elo, wins = excluded.wins, losses = excluded.losses,
                draws = excluded.draws, best_score = excluded.best_score, updated_at = excluded.updated_at
            """,
            (email, elo, wins, losses, draws, best_score, _now_iso()),
        )


def vs_ratings_list(limit=500):
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT r.*, a.username, p.avatar_filename,
                   CASE WHEN COALESCE(p.show_country, 1) = 1 THEN NULLIF(a.country, '') END AS country
            FROM vs_ratings r
            JOIN accounts a ON a.email = r.email COLLATE NOCASE
            LEFT JOIN account_profiles p ON p.email = r.email COLLATE NOCASE
            WHERE r.wins + r.losses + r.draws > 0
            ORDER BY r.elo DESC, r.wins DESC, a.username COLLATE NOCASE
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def tournament_create(fields):
    fields = {"created_at": _now_iso(), **fields}
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    with _get_connection() as connection:
        return connection.execute(
            f"INSERT INTO tournaments ({columns}) VALUES ({placeholders})", tuple(fields.values())
        ).lastrowid


_TOURNAMENT_SELECT = """
    SELECT t.*, o.username AS organizer_username, w.username AS winner_username,
           (SELECT COUNT(*) FROM tournament_players p WHERE p.tournament_id = t.id) AS player_count
    FROM tournaments t
    LEFT JOIN accounts o ON o.email = t.organizer COLLATE NOCASE
    LEFT JOIN accounts w ON w.email = t.winner COLLATE NOCASE
"""


def tournament_get(tournament_id):
    with _get_connection() as connection:
        return connection.execute(_TOURNAMENT_SELECT + " WHERE t.id = ?", (tournament_id,)).fetchone()


def tournament_list(limit=100):
    with _get_connection() as connection:
        return connection.execute(
            _TOURNAMENT_SELECT + " WHERE t.status <> 'cancelled'"
            " ORDER BY CASE t.status WHEN 'running' THEN 0 WHEN 'signup' THEN 1 ELSE 2 END, t.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def tournament_update(tournament_id, **fields):
    assignments = ", ".join(f"{column} = ?" for column in fields)
    with _get_connection() as connection:
        connection.execute(f"UPDATE tournaments SET {assignments} WHERE id = ?", (*fields.values(), tournament_id))


def tournament_players(tournament_id):
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT tp.*, a.username, pr.avatar_filename, r.elo
            FROM tournament_players tp
            LEFT JOIN accounts a ON a.email = tp.email COLLATE NOCASE
            LEFT JOIN account_profiles pr ON pr.email = tp.email COLLATE NOCASE
            LEFT JOIN vs_ratings r ON r.email = tp.email COLLATE NOCASE
            WHERE tp.tournament_id = ? ORDER BY COALESCE(tp.seed, 999), tp.joined_at
            """,
            (tournament_id,),
        ).fetchall()


def tournament_add_player(tournament_id, email):
    with _get_connection() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO tournament_players (tournament_id, email, joined_at) VALUES (?, ?, ?)",
            (tournament_id, email, _now_iso()),
        )


def tournament_remove_player(tournament_id, email):
    with _get_connection() as connection:
        connection.execute(
            "DELETE FROM tournament_players WHERE tournament_id = ? AND email = ? COLLATE NOCASE", (tournament_id, email)
        )


def tournament_set_seed(tournament_id, email, seed):
    with _get_connection() as connection:
        connection.execute(
            "UPDATE tournament_players SET seed = ? WHERE tournament_id = ? AND email = ? COLLATE NOCASE",
            (seed, tournament_id, email),
        )


def tournament_matches(tournament_id):
    with _get_connection() as connection:
        return connection.execute(
            _MATCH_SELECT + " WHERE m.tournament_id = ? ORDER BY m.round, m.slot", (tournament_id,)
        ).fetchall()


def tournament_match_at(tournament_id, round_number, slot):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT * FROM vs_matches WHERE tournament_id = ? AND round = ? AND slot = ?", (tournament_id, round_number, slot)
        ).fetchone()


def tournament_stats_rows():
    """Alle Turnier-Matches mit Runde, Rundenzahl des Turniers und Sieger (fuer die Turnier-Rangliste)."""
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT m.tournament_id, m.round, m.pilot_a, m.pilot_b, m.winner, m.status, m.result_note, t.status AS t_status,
                   t.winner AS t_winner, (SELECT MAX(round) FROM vs_matches x WHERE x.tournament_id = m.tournament_id) AS rounds
            FROM vs_matches m JOIN tournaments t ON t.id = m.tournament_id
            WHERE t.status IN ('running', 'done')
            """
        ).fetchall()


def accounts_by_emails(emails):
    emails = [e for e in emails if e]
    if not emails:
        return {}
    marks = ", ".join("?" for _ in emails)
    with _get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT a.email, a.username, p.avatar_filename,
                   CASE WHEN COALESCE(p.show_country, 1) = 1 THEN NULLIF(a.country, '') END AS country
            FROM accounts a LEFT JOIN account_profiles p ON p.email = a.email COLLATE NOCASE
            WHERE lower(a.email) IN ({marks})
            """,
            [e.lower() for e in emails],
        ).fetchall()
        return {row["email"].lower(): row for row in rows}


def leaderboard_add_report(entry_id, email, reason):
    """True, wenn neu gemeldet; False, wenn dieses Konto schon gemeldet hat."""
    with _get_connection() as connection:
        try:
            connection.execute(
                "INSERT INTO leaderboard_reports (entry_id, email, reason, created_at) VALUES (?, ?, ?, ?)",
                (entry_id, email, reason, _now_iso()),
            )
        except sqlite3.IntegrityError:
            return False
        return True


def leaderboard_reports(entry_id):
    """Meldungen mit Benutzername der Meldenden, aelteste zuerst."""
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT r.*, a.username, a.full_name
            FROM leaderboard_reports r
            LEFT JOIN accounts a ON a.email = r.email COLLATE NOCASE
            WHERE r.entry_id = ? ORDER BY r.id
            """,
            (entry_id,),
        ).fetchall()


def leaderboard_has_reported(entry_id, email):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT 1 FROM leaderboard_reports WHERE entry_id = ? AND email = ? COLLATE NOCASE", (entry_id, email)
        ).fetchone() is not None


# --- Oeffentliches Profil (community.py) ------------------------------------

PROFILE_FIELDS = (
    "status_text", "bio", "signature", "website", "youtube", "show_country", "show_copters", "radio", "radio_firmware",
)


def get_profile(email):
    with _get_connection() as connection:
        return connection.execute("SELECT * FROM account_profiles WHERE email = ? COLLATE NOCASE", (email,)).fetchone()


def save_profile(email, fields):
    """fields: Teilmenge von PROFILE_FIELDS (+ avatar_filename)."""
    with _get_connection() as connection:
        connection.execute(
            "INSERT INTO account_profiles (email, updated_at) VALUES (?, ?) ON CONFLICT (email) DO NOTHING",
            (email, _now_iso()),
        )
        if fields:
            assignments = ", ".join(f"{key} = ?" for key in fields)
            connection.execute(
                f"UPDATE account_profiles SET {assignments}, updated_at = ? WHERE email = ? COLLATE NOCASE",
                (*fields.values(), _now_iso(), email),
            )


def avatar_filenames():
    with _get_connection() as connection:
        return {row[0] for row in connection.execute("SELECT avatar_filename FROM account_profiles WHERE avatar_filename IS NOT NULL")}


def member_card_data(emails):
    """Daten fuer die Autoren-Spalte im Forum: {email(lower): row} mit Konto,
    Profil, Beitragszahl, Copter-Namen und Ausweis-Status."""
    emails = [e for e in {e.lower() for e in emails if e}]
    if not emails:
        return {}
    marks = ", ".join("?" for _ in emails)
    with _get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT a.email, a.username, a.country, a.created_at, a.last_seen_at, a.role,
                   p.avatar_filename, p.status_text, p.signature, p.show_country, p.show_copters,
                   p.radio, p.radio_firmware,
                   (SELECT COUNT(*) FROM forum_posts fp WHERE fp.author_email = a.email COLLATE NOCASE) AS post_count,
                   (SELECT status FROM pilot_licenses l WHERE l.email = a.email COLLATE NOCASE) AS license_status
            FROM accounts a
            LEFT JOIN account_profiles p ON p.email = a.email COLLATE NOCASE
            WHERE lower(a.email) IN ({marks})
            """,
            emails,
        ).fetchall()
        copters = connection.execute(
            f"SELECT lower(email) AS e, id, name, category, image_filename FROM copters "
            f"WHERE lower(email) IN ({marks}) ORDER BY name COLLATE NOCASE",
            emails,
        ).fetchall()
    cards = {row["email"].lower(): dict(row) for row in rows}
    for card in cards.values():
        card["copters"] = []
    for copter in copters:
        if copter["e"] in cards:
            cards[copter["e"]]["copters"].append({
                "id": copter["id"], "name": copter["name"], "category": copter["category"],
                "image_filename": copter["image_filename"],
            })
    return cards


def forum_member_stats(email):
    with _get_connection() as connection:
        posts = connection.execute(
            "SELECT COUNT(*) FROM forum_posts WHERE author_email = ? COLLATE NOCASE", (email,)
        ).fetchone()[0]
        threads = connection.execute(
            "SELECT COUNT(*) FROM forum_threads WHERE author_email = ? COLLATE NOCASE", (email,)
        ).fetchone()[0]
        return posts, threads


def forum_recent_posts(email, limit):
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT p.id, p.thread_id, p.body, p.created_at, t.title, t.category
            FROM forum_posts p JOIN forum_threads t ON t.id = p.thread_id
            WHERE p.author_email = ? COLLATE NOCASE ORDER BY p.id DESC LIMIT ?
            """,
            (email, limit),
        ).fetchall()


def forum_recent_threads(email, limit):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT id, title, category, created_at, post_count FROM forum_threads "
            "WHERE author_email = ? COLLATE NOCASE ORDER BY id DESC LIMIT ?",
            (email, limit),
        ).fetchall()


def leaderboard_entries_for(email):
    """Leaderboard-Eintraege eines Kontos mit Platz, beste zuerst."""
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT e.id, e.pilot, e.score, e.tricks, e.best, e.created_at, e.verified_at,
                   e.video_filename IS NOT NULL AS has_video,
                   (SELECT COUNT(*) FROM leaderboard_entries o
                     WHERE o.score > e.score OR (o.score = e.score AND o.id < e.id)) + 1 AS rank
            FROM leaderboard_entries e WHERE e.email = ? COLLATE NOCASE
            ORDER BY e.score DESC, e.id
            """,
            (email,),
        ).fetchall()


# --- Forum-Anhaenge (community.py) --------------------------------------------


def forum_add_attachment(post_id, filename, original_name, kind, mime, size, width, height):
    with _get_connection() as connection:
        connection.execute(
            """
            INSERT INTO forum_attachments (post_id, filename, original_name, kind, mime, size, width, height, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (post_id, filename, original_name, kind, mime, size, width, height, _now_iso()),
        )


def forum_attachments_for(post_ids):
    """{post_id: [anhaenge]}"""
    post_ids = list(post_ids)
    if not post_ids:
        return {}
    marks = ", ".join("?" for _ in post_ids)
    with _get_connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM forum_attachments WHERE post_id IN ({marks}) ORDER BY post_id, id", post_ids
        ).fetchall()
    result = {}
    for row in rows:
        result.setdefault(row["post_id"], []).append(row)
    return result


def forum_get_attachment(attachment_id):
    with _get_connection() as connection:
        return connection.execute("SELECT * FROM forum_attachments WHERE id = ?", (attachment_id,)).fetchone()


def forum_attachment_by_filename(filename):
    with _get_connection() as connection:
        return connection.execute("SELECT * FROM forum_attachments WHERE filename = ?", (filename,)).fetchone()


def forum_delete_attachments(attachment_ids, post_id):
    """Loescht Anhaenge eines Beitrags, liefert deren Dateinamen."""
    attachment_ids = list(attachment_ids)
    if not attachment_ids:
        return []
    marks = ", ".join("?" for _ in attachment_ids)
    with _get_connection() as connection:
        rows = connection.execute(
            f"SELECT filename FROM forum_attachments WHERE post_id = ? AND id IN ({marks})", (post_id, *attachment_ids)
        ).fetchall()
        connection.execute(f"DELETE FROM forum_attachments WHERE post_id = ? AND id IN ({marks})", (post_id, *attachment_ids))
        return [row[0] for row in rows]


def forum_attachment_filenames():
    with _get_connection() as connection:
        return {row[0] for row in connection.execute("SELECT filename FROM forum_attachments")}


# --- Drohnen-Ausweis (pilot_license.py) ----------------------------------------


def get_pilot_license(email):
    with _get_connection() as connection:
        return connection.execute("SELECT * FROM pilot_licenses WHERE email = ? COLLATE NOCASE", (email,)).fetchone()


def get_pilot_license_by_id(license_id):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT l.*, a.username, a.full_name FROM pilot_licenses l "
            "LEFT JOIN accounts a ON a.email = l.email COLLATE NOCASE WHERE l.id = ?",
            (license_id,),
        ).fetchone()


def save_pilot_license(email, kind, number, valid_until, filename, original_name, mime, size):
    """Legt den Ausweis an bzw. ersetzt ihn (neue Pruefung). Liefert den alten Dateinamen oder None."""
    with _get_connection() as connection:
        old = connection.execute("SELECT filename FROM pilot_licenses WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
        connection.execute("DELETE FROM pilot_licenses WHERE email = ? COLLATE NOCASE", (email,))
        connection.execute(
            """
            INSERT INTO pilot_licenses (email, kind, number, valid_until, filename, original_name, mime, size, status, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (email, kind, number, valid_until, filename, original_name, mime, size, _now_iso()),
        )
        return old["filename"] if old else None


def delete_pilot_license(email):
    with _get_connection() as connection:
        old = connection.execute("SELECT filename FROM pilot_licenses WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
        connection.execute("DELETE FROM pilot_licenses WHERE email = ? COLLATE NOCASE", (email,))
        return old["filename"] if old else None


def review_pilot_license(license_id, status, note):
    with _get_connection() as connection:
        connection.execute(
            "UPDATE pilot_licenses SET status = ?, review_note = ?, reviewed_at = ? WHERE id = ?",
            (status, note, _now_iso(), license_id),
        )


def list_pilot_licenses():
    with _get_connection() as connection:
        return connection.execute(
            """
            SELECT l.*, a.username, a.full_name FROM pilot_licenses l
            LEFT JOIN accounts a ON a.email = l.email COLLATE NOCASE
            ORDER BY CASE l.status WHEN 'pending' THEN 0 WHEN 'rejected' THEN 1 ELSE 2 END, l.uploaded_at DESC
            """
        ).fetchall()


def pilot_license_filenames():
    with _get_connection() as connection:
        return {row[0] for row in connection.execute("SELECT filename FROM pilot_licenses")}


def set_account_role_by_id(account_id, role):
    with _get_connection() as connection:
        connection.execute("UPDATE accounts SET role = ? WHERE id = ?", (role, account_id))


def set_account_role(username, role):
    """Rolle eines Kontos setzen ('admin' oder None). Liefert True, wenn das Konto existiert."""
    with _get_connection() as connection:
        cursor = connection.execute(
            "UPDATE accounts SET role = ? WHERE username = ? COLLATE NOCASE", (role, username)
        )
        return cursor.rowcount > 0


def leaderboard_all(limit=10000):
    """Alle Eintraege fuer die Leaderboard-Seite, beste zuerst."""
    with _get_connection() as connection:
        return connection.execute(
            _LEADERBOARD_SELECT + " ORDER BY e.score DESC, e.id ASC LIMIT ?", (limit,)
        ).fetchall()


# --- Laender (countries.py) ------------------------------------------------------


def account_countries():
    with _get_connection() as connection:
        return connection.execute("SELECT id, country FROM accounts").fetchall()


def set_account_country(account_id, code):
    with _get_connection() as connection:
        connection.execute("UPDATE accounts SET country = ? WHERE id = ?", (code, account_id))


# --- FPV-Simulator: Einstellungen je Konto (fpv_simulator.py) ---------------

def sim_settings_get(email):
    with _get_connection() as connection:
        return connection.execute(
            "SELECT data, updated_at FROM sim_settings WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()


def sim_settings_save(email, data):
    now = _now_iso()
    with _get_connection() as connection:
        connection.execute(
            """
            INSERT INTO sim_settings (email, data, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
            """,
            (email, data, now),
        )
    return now


def sim_settings_delete(email):
    with _get_connection() as connection:
        connection.execute("DELETE FROM sim_settings WHERE email = ? COLLATE NOCASE", (email,))


# --- FPV-Simulator: Maps aus dem Map-Editor (fpv_simulator.py) ---------------

# {data}: ", m.data" nur beim Laden einer einzelnen Map (Listen ohne die Map-Daten)
_SIM_MAP_SELECT = """
    SELECT m.id, m.owner_email, m.name, m.gates, m.objects, m.shared, m.plays, m.created_at, m.updated_at{data},
           a.username AS author
    FROM sim_maps m LEFT JOIN accounts a ON a.email = m.owner_email COLLATE NOCASE
"""


def sim_map_create(email, name, data, gates, objects, shared):
    now = _now_iso()
    with _get_connection() as connection:
        return connection.execute(
            """
            INSERT INTO sim_maps (owner_email, name, data, gates, objects, shared, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (email, name, data, gates, objects, 1 if shared else 0, now, now),
        ).lastrowid


def sim_map_update(map_id, name, data, gates, objects, shared):
    with _get_connection() as connection:
        connection.execute(
            "UPDATE sim_maps SET name = ?, data = ?, gates = ?, objects = ?, shared = ?, updated_at = ? WHERE id = ?",
            (name, data, gates, objects, 1 if shared else 0, _now_iso(), map_id),
        )


def sim_map_get(map_id, with_data=True):
    with _get_connection() as connection:
        return connection.execute(
            _SIM_MAP_SELECT.format(data=", m.data" if with_data else "") + " WHERE m.id = ?", (map_id,)
        ).fetchone()


def sim_map_list_mine(email):
    with _get_connection() as connection:
        return connection.execute(
            _SIM_MAP_SELECT.format(data="") + " WHERE m.owner_email = ? COLLATE NOCASE ORDER BY m.updated_at DESC", (email,)
        ).fetchall()


def sim_map_list_shared(order="neu", limit=100):
    by = "m.plays DESC, m.updated_at DESC" if order == "beliebt" else "m.updated_at DESC"
    with _get_connection() as connection:
        return connection.execute(
            _SIM_MAP_SELECT.format(data="") + f" WHERE m.shared = 1 ORDER BY {by} LIMIT ?", (int(limit),)
        ).fetchall()


def sim_map_count(email):
    with _get_connection() as connection:
        return connection.execute("SELECT COUNT(*) FROM sim_maps WHERE owner_email = ? COLLATE NOCASE", (email,)).fetchone()[0]


def sim_map_played(map_id):
    with _get_connection() as connection:
        connection.execute("UPDATE sim_maps SET plays = plays + 1 WHERE id = ?", (map_id,))


def sim_map_delete(map_id):
    with _get_connection() as connection:
        connection.execute("DELETE FROM sim_maps WHERE id = ?", (map_id,))
