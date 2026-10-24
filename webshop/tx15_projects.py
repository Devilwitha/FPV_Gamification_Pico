"""Server-Ablage fuer Themes (Theme Studio) und Lua-Skripte (Lua Studio) der
Drohnen Tools (/tx15-werkstatt/).

Wie bei den Backups (tx15_backups.py) nur fuer eingeloggte Konten, und
jedes Konto sieht ausschliesslich die eigenen Eintraege. Gespeichert werden
zwei Teile:

- der Studio-Stand als JSON (zum spaeteren Weiterbearbeiten im Studio,
  inklusive eingebetteter Bilder beim Theme) in der Webshop-Datenbank,
- das fertige ZIP fuer die SD-Karte unter PROJECT_DIR (Default:
  <TX15_BACKUP_DIR>/projects).

Grenzen per .env: TX15_PROJECT_MAX_COUNT (je Art und Konto),
TX15_PROJECT_MAX_STATE_BYTES und TX15_PROJECT_MAX_ZIP_BYTES.
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import db
from tx15_backups import (
    BACKUP_DIR,
    ZIP_SIGNATURES,
    account_email,
    clean_text,
    error_response,
    remove_file,
    sweep_directory,
)
from i18n import gettext as _


def _env_int(name, default):
    value = os.environ.get(name, "").strip()
    try:
        return int(value) if value else default
    except ValueError:
        return default


PROJECT_DIR = Path(os.environ.get("TX15_PROJECT_DIR", "").strip() or BACKUP_DIR / "projects")
MAX_COUNT = _env_int("TX15_PROJECT_MAX_COUNT", 100)
MAX_STATE_BYTES = _env_int("TX15_PROJECT_MAX_STATE_BYTES", 16 * 1024 * 1024)
MAX_ZIP_BYTES = _env_int("TX15_PROJECT_MAX_ZIP_BYTES", 20 * 1024 * 1024)
AJAX_HEADER_VALUE = "tx15-werkstatt"
KINDS = {
    "theme": {"label": "Theme", "studio": "/tx15-werkstatt/theme-studio.html"},
    "lua": {"label": "Lua-Skript", "studio": "/tx15-werkstatt/lua-studio.html"},
}

bp = Blueprint("tx15_projects", __name__, url_prefix="/api/tx15-projects")


def _require_ajax():
    return request.headers.get("X-Requested-With") == AJAX_HEADER_VALUE


def _studio_url(kind, project_id):
    return f"{KINDS[kind]['studio']}?projekt={project_id}"


def _serialize(row, with_state=False):
    data = {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "detail": row["detail"],
        "size": row["zip_size"],
        "updated": row["updated_at"],
        "studio_url": _studio_url(row["kind"], row["id"]),
    }
    if with_state:
        data["state"] = json.loads(row["state_json"])
    return data


def _read_upload(name, limit):
    """Liest einen Datei-Teil der Anfrage vollstaendig, hoechstens limit Bytes.
    Liefert (bytes, None) oder (None, Fehlermeldung)."""
    upload = request.files.get(name)
    if upload is None:
        return None, _("Teil „{name}“ fehlt.", name=name)
    data = upload.stream.read(limit + 1)
    if len(data) > limit:
        return None, _("Zu groß (höchstens {v1} MB).", v1=limit // (1024 * 1024))
    return data, None


def _parse_payload():
    """Gemeinsame Pruefung fuer Anlegen und Ueberschreiben.
    Liefert (felder, None) oder (None, Fehler-Response)."""
    state_bytes, problem = _read_upload("state", MAX_STATE_BYTES)
    if problem:
        return None, error_response(f"Studio-Stand: {problem}", 400 if "fehlt" in problem else 413)
    try:
        state = json.loads(state_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, error_response(_("Der Studio-Stand ist kein gültiges JSON."), 400)
    if not isinstance(state, dict):
        return None, error_response(_("Der Studio-Stand ist kein gültiges JSON."), 400)

    zip_bytes, problem = _read_upload("file", MAX_ZIP_BYTES)
    if problem:
        return None, error_response(f"ZIP: {problem}", 400 if "fehlt" in problem else 413)
    if zip_bytes[:4] not in ZIP_SIGNATURES:
        return None, error_response(_("Die Datei ist keine ZIP-Datei."), 400)

    name = clean_text(request.form.get("name"), 60) or "Ohne Namen"
    detail = clean_text(request.form.get("detail"), 120)
    return {
        "name": name,
        "detail": detail,
        "state_json": json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        "zip_bytes": zip_bytes,
    }, None


def _stage_zip(data):
    """Schreibt das ZIP als .part-Datei. Erst nach dem Datenbank-Eintrag macht
    _commit_zip() daraus die fertige Datei - sweep_directory() loescht fertige
    .zip-Dateien ohne Datensatz und darf so nie eine frische erwischen."""
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.zip"
    (PROJECT_DIR / f"{filename}.part").write_bytes(data)
    return filename


def _commit_zip(filename):
    os.replace(PROJECT_DIR / f"{filename}.part", PROJECT_DIR / filename)


def _discard_zip(filename):
    remove_file(PROJECT_DIR / f"{filename}.part")
    remove_file(PROJECT_DIR / filename)


@bp.get("")
def list_projects():
    """Login-Status und - falls eingeloggt - die Projekte einer Art."""
    kind = request.args.get("kind")
    if kind is not None and kind not in KINDS:
        return error_response("Unbekannte Art.", 400)
    email = account_email()
    payload = {"logged_in": email is not None, "projects": []}
    if email is not None:
        payload["projects"] = [_serialize(row) for row in db.list_tx15_projects(email, kind)]
        payload["max_count"] = MAX_COUNT
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/<int:project_id>")
def get_project(project_id):
    email = account_email()
    if email is None:
        return error_response(_("Bitte einloggen."), 401)
    row = db.get_tx15_project(project_id, email)
    if row is None:
        return error_response(_("Nicht gefunden."), 404)
    response = jsonify({"project": _serialize(row, with_state=True)})
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("")
def create_project():
    email = account_email()
    if email is None:
        return error_response(_("Zum Speichern auf dem Server bitte einloggen."), 401)
    if not _require_ajax():
        return error_response(_("Ungültige Anfrage."), 400)
    kind = request.form.get("kind")
    if kind not in KINDS:
        return error_response("Unbekannte Art.", 400)
    if db.count_tx15_projects(email, kind) >= MAX_COUNT:
        return error_response(
            _("Du hast bereits {max_count} {label}s gespeichert. Lösche zuerst ältere.", max_count=MAX_COUNT, label=KINDS[kind]['label']), 409
        )
    fields, problem = _parse_payload()
    if problem:
        return problem
    filename = _stage_zip(fields["zip_bytes"])
    try:
        project_id = db.add_tx15_project(
            email=email,
            kind=kind,
            name=fields["name"],
            detail=fields["detail"],
            state_json=fields["state_json"],
            zip_filename=filename,
            zip_size=len(fields["zip_bytes"]),
        )
        _commit_zip(filename)
    except Exception:
        _discard_zip(filename)
        raise
    return jsonify({"project": _serialize(db.get_tx15_project(project_id, email))}), 201


@bp.put("/<int:project_id>")
def update_project(project_id):
    email = account_email()
    if email is None:
        return error_response(_("Zum Speichern auf dem Server bitte einloggen."), 401)
    if not _require_ajax():
        return error_response(_("Ungültige Anfrage."), 400)
    if db.get_tx15_project(project_id, email) is None:
        return error_response(_("Nicht gefunden."), 404)
    fields, problem = _parse_payload()
    if problem:
        return problem
    filename = _stage_zip(fields["zip_bytes"])
    try:
        old_filename = db.update_tx15_project(
            project_id,
            email,
            name=fields["name"],
            detail=fields["detail"],
            state_json=fields["state_json"],
            zip_filename=filename,
            zip_size=len(fields["zip_bytes"]),
        )
        if old_filename is None:
            _discard_zip(filename)
            return error_response(_("Nicht gefunden."), 404)
        _commit_zip(filename)
    except Exception:
        _discard_zip(filename)
        raise
    remove_file(PROJECT_DIR / old_filename)
    return jsonify({"project": _serialize(db.get_tx15_project(project_id, email))})


@bp.get("/<int:project_id>/zip")
def download_project(project_id):
    email = account_email()
    if email is None:
        return error_response(_("Bitte einloggen."), 401)
    row = db.get_tx15_project(project_id, email)
    path = PROJECT_DIR / row["zip_filename"] if row is not None else None
    if path is None or not path.is_file():
        return error_response(_("Nicht gefunden."), 404)
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in row["name"]).strip("_") or "projekt"
    response = send_file(
        path, mimetype="application/zip", as_attachment=True, download_name=f"{safe_name}.zip", max_age=0
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.delete("/<int:project_id>")
def delete_project(project_id):
    email = account_email()
    if email is None:
        return error_response(_("Bitte einloggen."), 401)
    if not _require_ajax():
        return error_response(_("Ungültige Anfrage."), 400)
    row = db.delete_tx15_project(project_id, email)
    if row is None:
        return error_response(_("Nicht gefunden."), 404)
    remove_file(PROJECT_DIR / row["zip_filename"])
    sweep_directory(PROJECT_DIR, db.tx15_project_filenames())
    return jsonify({"deleted": project_id})


def account_overview(email):
    """Themes und Lua-Skripte eines Kontos fuer die Konto-Seite."""
    overview = {kind: [] for kind in KINDS}
    for row in db.list_tx15_projects(email):
        if row["kind"] not in KINDS:
            continue
        overview[row["kind"]].append({
            "id": row["id"],
            "name": row["name"],
            "detail": row["detail"],
            "size": row["zip_size"],
            "updated": datetime.fromisoformat(row["updated_at"]).astimezone().strftime("%d.%m.%Y %H:%M"),
            "studio_url": _studio_url(row["kind"], row["id"]),
        })
    return overview
