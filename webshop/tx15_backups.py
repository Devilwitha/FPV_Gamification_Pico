"""Server-Ablage fuer das TX15 Backup Studio (Drohnen Tools, /tx15-werkstatt/).

Eingeloggte Konten (session["account_email"], siehe app.py's login()) koennen
ihre SD-Karten-Backups hier speichern, auflisten, herunterladen und loeschen.
Jedes Konto sieht ausschliesslich die eigenen Backups.

- Die ZIP-Dateien liegen unter BACKUP_DIR (Default: <Repo>/tx15_backups,
  siehe .gitignore - enthaelt Kundendaten), die Metadaten in der
  Webshop-Datenbank (db.py, Tabelle tx15_backups).
- Grenzen per .env: TX15_BACKUP_MAX_BYTES (je Backup), TX15_BACKUP_QUOTA_BYTES
  (je Konto) und TX15_BACKUP_MAX_COUNT (Anzahl je Konto).
- Schreibende Anfragen brauchen den Header X-Requested-With (siehe
  _require_ajax()), zusaetzlich zum SameSite-Session-Cookie als Schutz gegen
  Cross-Site-Requests.
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, send_file, session

import db


def _env_int(name, default):
    value = os.environ.get(name, "").strip()
    try:
        return int(value) if value else default
    except ValueError:
        return default


BACKUP_DIR = db.media_dir("TX15_BACKUP_DIR", "tx15_backups")
MAX_BACKUP_BYTES = _env_int("TX15_BACKUP_MAX_BYTES", 250 * 1024 * 1024)
QUOTA_BYTES = _env_int("TX15_BACKUP_QUOTA_BYTES", 1024 * 1024 * 1024)
MAX_COUNT = _env_int("TX15_BACKUP_MAX_COUNT", 50)
# Spielraum fuer Multipart-Rahmen und Metadaten ueber der ZIP-Groesse.
REQUEST_OVERHEAD_BYTES = 2 * 1024 * 1024
ORPHAN_MIN_AGE_SECONDS = 3600
LOGIN_URL = "/login?next=/tx15-werkstatt/backup-studio.html"
AJAX_HEADER_VALUE = "tx15-backup-studio"
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06")

bp = Blueprint("tx15_backups", __name__, url_prefix="/api/tx15-backups")


def account_email():
    return (session.get("account_email") or "").strip().lower() or None


def error_response(message, status):
    return jsonify({"error": message}), status


def _require_ajax():
    return request.headers.get("X-Requested-With") == AJAX_HEADER_VALUE


def clean_text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ""


def to_count(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _clean_meta(raw):
    """Nur bekannte Felder mit begrenzter Laenge uebernehmen - die Metadaten
    kommen vom Browser und werden spaeter wieder angezeigt."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    models = []
    for model in data.get("models") or []:
        if isinstance(model, dict) and len(models) < 2000:
            models.append({"file": clean_text(model.get("file"), 160), "name": clean_text(model.get("name"), 80)})
    categories = [clean_text(c, 20) for c in (data.get("categories") or []) if isinstance(c, str)][:20]
    return {
        "app": clean_text(data.get("app"), 40) or "TX15 Backup Studio",
        "version": to_count(data.get("version")) or 1,
        "created": clean_text(data.get("created"), 40),
        "label": clean_text(data.get("label"), 60),
        "edgetx": clean_text(data.get("edgetx"), 20) or None,
        "board": clean_text(data.get("board"), 40) or None,
        "files": to_count(data.get("files")),
        "bytes": to_count(data.get("bytes")),
        "categories": categories,
        "models": models,
        "imported": clean_text(data.get("imported"), 200) or None,
    }


def _serialize(row):
    return {
        "id": row["id"],
        "label": row["label"],
        "created": row["created_ms"],
        "size": row["size"],
        "auto": bool(row["is_auto"]),
        "uploaded": bool(row["is_uploaded"]),
        "meta": json.loads(row["meta_json"]),
    }


def _limits(email):
    count, used = db.tx15_backup_usage(email)
    return {
        "count": count,
        "used": used,
        "quota": QUOTA_BYTES,
        "max_count": MAX_COUNT,
        "max_backup": MAX_BACKUP_BYTES,
    }


@bp.get("")
def list_backups():
    """Login-Status und - falls eingeloggt - die Backups des Kontos."""
    email = account_email()
    if email is None:
        response = jsonify({"logged_in": False, "login_url": LOGIN_URL, "backups": []})
    else:
        response = jsonify({
            "logged_in": True,
            "login_url": LOGIN_URL,
            "backups": [_serialize(row) for row in db.list_tx15_backups(email)],
            "limits": _limits(email),
        })
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("")
def create_backup():
    email = account_email()
    if email is None:
        return error_response("Zum Speichern auf dem Server bitte einloggen.", 401)
    if not _require_ajax():
        return error_response("Ungültige Anfrage.", 400)
    if request.content_length and request.content_length > MAX_BACKUP_BYTES + REQUEST_OVERHEAD_BYTES:
        return error_response(f"Das Backup ist zu groß (höchstens {MAX_BACKUP_BYTES // (1024 * 1024)} MB).", 413)

    limits = _limits(email)
    if limits["count"] >= MAX_COUNT:
        return error_response(f"Du hast bereits {MAX_COUNT} Backups gespeichert. Lösche zuerst ältere.", 409)

    upload = request.files.get("file")
    if upload is None:
        return error_response("Keine Backup-Datei erhalten.", 400)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.zip"
    target = BACKUP_DIR / filename
    temp = BACKUP_DIR / f"{filename}.part"
    try:
        upload.save(temp)
        size = temp.stat().st_size
        with temp.open("rb") as handle:
            signature = handle.read(4)
        if signature not in ZIP_SIGNATURES:
            return error_response("Die Datei ist keine ZIP-Datei.", 400)
        if size > MAX_BACKUP_BYTES:
            return error_response(f"Das Backup ist zu groß (höchstens {MAX_BACKUP_BYTES // (1024 * 1024)} MB).", 413)
        if limits["used"] + size > QUOTA_BYTES:
            free_mb = max(0, QUOTA_BYTES - limits["used"]) / (1024 * 1024)
            return error_response(f"Nicht genug Speicher im Konto (frei: {free_mb:.1f} MB). Lösche ältere Backups.", 413)

        meta = _clean_meta(request.form.get("meta"))
        label = clean_text(request.form.get("label"), 60) or meta["label"] or "Backup"
        meta["label"] = label
        created_ms = to_count(request.form.get("created")) or int(time.time() * 1000)
        is_auto = request.form.get("auto") == "1"
        is_uploaded = request.form.get("uploaded") == "1"

        # Erst der Datensatz, dann die Datei: _sweep_orphans() loescht .zip-
        # Dateien ohne Datensatz und darf eine fertige Datei nie vorher sehen.
        backup_id = db.add_tx15_backup(
            email=email,
            label=label,
            created_ms=created_ms,
            size=size,
            is_auto=is_auto,
            is_uploaded=is_uploaded,
            meta_json=json.dumps(meta, ensure_ascii=False),
            filename=filename,
        )
        try:
            os.replace(temp, target)
        except OSError:
            db.delete_tx15_backup(backup_id, email)
            raise
    finally:
        remove_file(temp)

    row = db.get_tx15_backup(backup_id, email)
    return jsonify({"backup": _serialize(row), "limits": _limits(email)}), 201


@bp.get("/<int:backup_id>/zip")
def download_backup(backup_id):
    email = account_email()
    if email is None:
        return error_response("Bitte einloggen.", 401)
    row = db.get_tx15_backup(backup_id, email)
    path = BACKUP_DIR / row["filename"] if row is not None else None
    if path is None or not path.is_file():
        return error_response("Backup nicht gefunden.", 404)
    response = send_file(
        path,
        mimetype="application/zip",
        as_attachment=True,
        download_name="TX15-Backup_" + datetime.fromtimestamp(
            row["created_ms"] / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%d-%H-%M") + ".zip",
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.delete("/<int:backup_id>")
def delete_backup(backup_id):
    email = account_email()
    if email is None:
        return error_response("Bitte einloggen.", 401)
    if not _require_ajax():
        return error_response("Ungültige Anfrage.", 400)
    row = db.delete_tx15_backup(backup_id, email)
    if row is None:
        return error_response("Backup nicht gefunden.", 404)
    remove_file(BACKUP_DIR / row["filename"])
    _sweep_orphans()
    return jsonify({"deleted": backup_id, "limits": _limits(email)})


def remove_file(path):
    """Unter Windows schlaegt das Loeschen fehl, solange die Datei offen ist
    (z. B. waehrend eines laufenden Downloads). Der Datensatz ist dann schon
    weg; die Datei raeumt ein spaeterer _sweep_orphans() auf."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def sweep_directory(directory, known):
    """Entfernt ZIP-Dateien ohne Datensatz (siehe remove_file()) und
    liegengebliebene .part-Dateien abgebrochener Uploads (aelter als 1 h).
    Unterordner bleiben unberuehrt."""
    if not directory.is_dir():
        return
    cutoff = time.time() - ORPHAN_MIN_AGE_SECONDS
    for path in directory.iterdir():
        if path.name in known or not path.is_file():
            continue
        try:
            if path.suffix == ".zip" or path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def _sweep_orphans():
    sweep_directory(BACKUP_DIR, db.tx15_backup_filenames())


def format_ms(created_ms):
    """Zeitstempel (ms, wie im Browser) als "TT.MM.JJJJ HH:MM" in Serverzeit."""
    return datetime.fromtimestamp(created_ms / 1000).strftime("%d.%m.%Y %H:%M")


def account_overview(email):
    """Backups eines Kontos fuer die Konto-Seite (app.py's account())."""
    backups = []
    for row in db.list_tx15_backups(email):
        meta = json.loads(row["meta_json"])
        backups.append({
            "id": row["id"],
            "label": row["label"],
            "created": format_ms(row["created_ms"]),
            "size": row["size"],
            "files": meta.get("files") or 0,
            "models": len(meta.get("models") or []),
            "edgetx": meta.get("edgetx"),
            "auto": bool(row["is_auto"]),
        })
    count, used = db.tx15_backup_usage(email)
    return {"items": backups, "used": used, "quota": QUOTA_BYTES, "count": count, "max_count": MAX_COUNT}
