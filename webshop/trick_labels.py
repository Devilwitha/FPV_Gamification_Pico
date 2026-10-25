"""Trick-Markierungen aus dem Blackbox Viewer und eigene Tricks der Community.

Im Viewer markiert ein Benutzer einen Bereich, in dem ein Trick nicht (oder
falsch) erkannt wurde, und waehlt den Trick aus der Liste - oder beantragt
einen neuen Trick. Gespeichert werden der Merkmalsvektor des Bereichs
(dieselben 7 Werte wie im kNN-Modell des Viewers, siehe
blackbox-viewer/src/analysis/ml.ts) und ein kurzer Video-Ausschnitt.

Der Admin sieht im Admin-Dashboard (Tab "Trick-Markierungen") den Ausschnitt,
gibt die Markierung frei (auf Wunsch unter einem anderen Trick) oder lehnt
sie ab. Freigegebene Markierungen sind die Trainingsdaten des
Community-Modells: der Viewer laedt sie ueber /api/tricks/katalog und findet
damit dieselben Tricks in allen Flügen. Ein beantragter neuer Trick erscheint
nach der Freigabe fuer alle in der Auswahlliste (Schluessel "c<id>").

Die API liegt unter /api/tricks (der Viewer unter /blackbox-viewer/ ruft sie
relativ als ../api/tricks auf, das faellt in IIS unter die Webshop-Regel).
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, redirect, request, send_file, session, url_for

import db
from i18n import gettext as _
from leaderboard import AJAX_HEADER_VALUE, _env_int, _num, _video_mime
from tx15_backups import clean_text, error_response, remove_file, sweep_directory

CLIP_DIR = db.media_dir("TRICK_CLIP_DIR", "trick_clips")
MAX_CLIP_BYTES = _env_int("TRICK_CLIP_MAX_BYTES", 25 * 1024 * 1024)
MAX_SPAN_SECONDS = 30
MAX_PENDING = 30
SUBMIT_INTERVAL_SECONDS = 10
FEATURE_COUNT = 7
DEFAULT_POINTS = 150
POINTS_RANGE = (10, 1000)
CUSTOM_KEY = re.compile(r"^c(\d+)$")

# Eingebaute Tricks des Viewers (blackbox-viewer/src/analysis/tricks.ts, ohne "none")
BUILTIN = {
    "roll": "Roll",
    "flip": "Flip",
    "powerloop": "Power Loop",
    "mattyflip": "Matty Flip",
    "splits": "Split-S",
    "immelmann": "Immelmann",
    "yawspin": "Yaw Spin",
    "trippyspin": "Trippy Spin",
    "corkscrew": "Corkscrew",
    "helix": "Helix",
    "orbit": "Orbit",
    "invertedhang": "Inverted Hang",
    "dive": "Dive",
}
STATUS_LABELS = {"pending": "wartet", "approved": "freigegeben", "rejected": "abgelehnt", "hidden": "ausgeblendet"}

bp = Blueprint("tricks", __name__)


# --- Hilfen -------------------------------------------------------------------------

def _account():
    email = session.get("account_email")
    return db.get_account_by_email(email) if email else None


def _require_ajax():
    return request.headers.get("X-Requested-With") == AJAX_HEADER_VALUE


def custom_key(type_id):
    return f"c{type_id}"


def _label_name(row):
    """Anzeigename einer Markierung: eingebauter Trick, eigener Trick oder beantragter Name."""
    if row["label"] in BUILTIN:
        return BUILTIN[row["label"]]
    return row["type_name"] or row["requested_name"] or row["label"]


def _clean_features(raw):
    if not isinstance(raw, list) or len(raw) != FEATURE_COUNT:
        return None
    values = []
    for value in raw:
        number = _num(value, -1000, 1000, None)
        if number is None:
            return None
        values.append(round(number, 5))
    return values


def _clean_points(value):
    return int(_num(value, POINTS_RANGE[0], POINTS_RANGE[1], DEFAULT_POINTS))


def _public_type(row):
    return {
        "key": custom_key(row["id"]),
        "name": row["name"],
        "description": row["description"],
        "points": row["points"],
    }


def _own_label(row):
    return {
        "id": row["id"],
        "label": row["label"],
        "name": _label_name(row),
        "status": row["status"],
        "note": row["review_note"],
        "start": row["t_start"],
        "end": row["t_end"],
        "logName": row["log_name"],
        "createdAt": row["created_at"],
        "hasClip": bool(row["clip_filename"]),
    }


# --- Katalog: freigegebene Tricks und Trainingsdaten ---------------------------------

@bp.get("/api/tricks/katalog")
def catalog():
    account = _account()
    examples = []
    for row in db.trick_examples():
        try:
            features = json.loads(row["features_json"])
        except ValueError:
            continue
        if isinstance(features, list) and len(features) == FEATURE_COUNT:
            examples.append({"label": row["label"], "f": features})
    response = jsonify({
        "custom": [_public_type(row) for row in db.trick_types_with_status("approved")],
        # eigene, noch nicht freigegebene Antraege: damit lassen sich weitere Beispiele markieren
        "mine": [
            {**_public_type(row), "pending": True} for row in db.trick_types_requested_by(account["email"])
        ] if account is not None else [],
        "examples": examples,
        "loggedIn": account is not None,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


# --- Markierungen des Benutzers ------------------------------------------------------

@bp.get("/api/tricks/markierungen")
def own_labels():
    account = _account()
    if account is None:
        return jsonify({"error": _("Bitte einloggen."), "labels": []}), 401
    response = jsonify({"labels": [_own_label(row) for row in db.trick_labels_for(account["email"])]})
    response.headers["Cache-Control"] = "no-store"
    return response


def _resolve_label(meta, account):
    """Liefert (label, trick_type_id, requested_name) oder einen Fehlertext."""
    label = clean_text(meta.get("label"), 20)
    new_name = re.sub(r"\s+", " ", clean_text(meta.get("newName"), 40))
    if new_name:
        if len(new_name) < 2:
            return None, _("Der Name des neuen Tricks ist zu kurz.")
        builtin = next((key for key, name in BUILTIN.items() if name.casefold() == new_name.casefold()), None)
        if builtin:
            return (builtin, None, ""), None
        existing = db.trick_type_by_name(new_name)
        if existing is not None and existing["status"] != "hidden":
            return (custom_key(existing["id"]), existing["id"], new_name), None
        type_id = db.trick_type_create(new_name, clean_text(meta.get("newDescription"), 300), account["email"])
        return (custom_key(type_id), type_id, new_name), None
    if label in BUILTIN:
        return (label, None, ""), None
    match = CUSTOM_KEY.match(label)
    trick_type = db.trick_type_get(int(match.group(1))) if match else None
    if trick_type is None or not (
        trick_type["status"] == "approved"
        or (trick_type["status"] == "pending" and (trick_type["requested_by"] or "").lower() == account["email"].lower())
    ):
        return None, _("Unbekannter Trick.")
    return (label, trick_type["id"], ""), None


@bp.post("/api/tricks/markierungen")
def submit_label():
    account = _account()
    if account is None:
        return jsonify({"error": _("Zum Senden bitte einloggen."), "loginUrl": "/login?next=/blackbox-viewer/"}), 401
    if not _require_ajax():
        return error_response(_("Ungültige Anfrage."), 400)
    if request.content_length and request.content_length > MAX_CLIP_BYTES + 256 * 1024:
        return error_response(_("Der Ausschnitt ist zu groß."), 413)
    last = db.trick_label_last(account["email"])
    if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < SUBMIT_INTERVAL_SECONDS:
        return error_response(_("Bitte kurz warten, bevor du die nächste Markierung sendest."), 429)
    if db.trick_labels_pending_count(account["email"]) >= MAX_PENDING:
        return error_response(_("Du hast schon viele offene Markierungen – bitte warte auf die Prüfung."), 429)
    try:
        meta = json.loads(request.form.get("meta") or "{}")
    except ValueError:
        meta = None
    if not isinstance(meta, dict):
        return error_response(_("Ungültige Angaben."), 400)
    source = clean_text(meta.get("source"), 8)
    if source not in ("bbl", "csv"):
        return error_response(_("Nur echte Blackbox-Logs können markiert werden."), 400)
    features = _clean_features(meta.get("features"))
    if features is None:
        return error_response(_("Ungültige Angaben."), 400)
    start = _num(meta.get("start"), 0, 86400)
    end = _num(meta.get("end"), 0, 86400)
    if not 0.1 <= end - start <= MAX_SPAN_SECONDS:
        return error_response(_("Der Bereich muss zwischen 0,1 und 30 Sekunden lang sein."), 400)
    angles = meta.get("angles") if isinstance(meta.get("angles"), list) else []
    angles = [round(_num(value, -100000, 100000), 1) for value in angles[:3]]

    # Ohne Flugvideo keine Pruefung moeglich - der Ausschnitt ist Pflicht.
    clip = request.files.get("clip")
    if clip is None:
        return error_response(_("Der Video-Ausschnitt fehlt – Markierungen lassen sich nur mit Flugvideo senden."), 400)
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    clip_name = uuid.uuid4().hex
    temp = CLIP_DIR / f"{clip_name}.part"
    clip.save(temp)
    clip_size = temp.stat().st_size
    with temp.open("rb") as handle:
        clip_mime = _video_mime(handle.read(16)) or ""
    if clip_size > MAX_CLIP_BYTES or not clip_mime:
        remove_file(temp)
        return error_response(_("Der Ausschnitt ist kein WebM- oder MP4-Video oder zu groß."), 400)

    resolved, problem = _resolve_label(meta, account)
    if problem:
        if clip_name:
            remove_file(CLIP_DIR / f"{clip_name}.part")
        return error_response(problem, 400)
    label, type_id, requested_name = resolved
    label_id = db.trick_label_add({
        "email": account["email"],
        "label": label,
        "trick_type_id": type_id,
        "requested_name": requested_name,
        "t_start": round(start, 3),
        "t_end": round(end, 3),
        "features_json": json.dumps(features),
        "angles_json": json.dumps(angles),
        "peak_rate": round(_num(meta.get("peakRate"), 0, 100000), 1),
        "mean_throttle": round(_num(meta.get("meanThrottle"), 0, 1), 3),
        "comment": clean_text(meta.get("comment"), 300),
        "log_name": clean_text(meta.get("logName"), 120),
        "craft": clean_text(meta.get("craft"), 40),
        "source": source,
        "clip_filename": clip_name,
        "clip_size": clip_size or 0,
        "clip_mime": clip_mime,
        "clip_start": round(_num(meta.get("clipStart"), 0, 86400), 3),
    })
    if clip_name:
        # Erst nach dem Datensatz die fertige Datei anlegen (siehe sweep_directory()).
        os.replace(CLIP_DIR / f"{clip_name}.part", CLIP_DIR / clip_name)
    row = db.trick_label_get(label_id)
    trick_type = db.trick_type_get(type_id) if type_id else None
    return jsonify({
        "label": _own_label(row),
        "type": {**_public_type(trick_type), "pending": trick_type["status"] != "approved"} if trick_type else None,
    }), 201


@bp.delete("/api/tricks/markierungen/<int:label_id>")
def delete_own_label(label_id):
    account = _account()
    if account is None:
        return error_response(_("Bitte einloggen."), 401)
    if not _require_ajax():
        return error_response(_("Ungültige Anfrage."), 400)
    row = db.trick_label_get(label_id)
    if row is None or row["email"].lower() != account["email"].lower() or row["status"] != "pending":
        return error_response(_("Markierung nicht gefunden."), 404)
    delete_label(label_id)
    return jsonify({"deleted": label_id})


@bp.get("/api/tricks/markierungen/<int:label_id>/clip")
def clip(label_id):
    account = _account()
    row = db.trick_label_get(label_id)
    if row is None or not row["clip_filename"]:
        abort(404)
    owner = account is not None and row["email"].lower() == account["email"].lower()
    if not owner and not session.get("is_admin"):
        abort(403)
    path = CLIP_DIR / row["clip_filename"]
    if not path.is_file():
        abort(404)
    response = send_file(path, mimetype=row["clip_mime"] or "video/webm", conditional=True, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


def delete_label(label_id):
    """Markierung samt Clip loeschen; ein dadurch verwaister Antrag wird abgelehnt."""
    row = db.trick_label_delete(label_id)
    if row is None:
        return None
    if row["clip_filename"]:
        remove_file(CLIP_DIR / row["clip_filename"])
    _drop_orphan_request(row["trick_type_id"])
    sweep_directory(CLIP_DIR, db.trick_label_filenames())
    return row


def _drop_orphan_request(type_id):
    if not type_id:
        return
    trick_type = db.trick_type_get(type_id)
    if trick_type is not None and trick_type["status"] == "pending" and not db.trick_type_open_labels(type_id):
        db.trick_type_update(type_id, status="rejected")


# --- Admin ------------------------------------------------------------------------------

def _admin_back(message):
    return redirect(url_for("admin_dashboard", ok=message) + "#tricks")


@bp.post("/admin/trick-markierungen/<int:label_id>")
def admin_review(label_id):
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    row = db.trick_label_get(label_id)
    if row is None:
        abort(404)
    action = request.form.get("action")
    note = (request.form.get("note") or "").strip()[:300]
    if action == "delete":
        delete_label(label_id)
        return _admin_back("Markierung gelöscht.")
    if action == "reject":
        db.trick_label_review(label_id, "rejected", row["label"], row["trick_type_id"], note)
        _drop_orphan_request(row["trick_type_id"])
        return _admin_back(f"Markierung #{label_id} abgelehnt.")
    if action != "approve":
        abort(400)

    choice = (request.form.get("label") or "").strip()
    if choice == "antrag" and row["trick_type_id"]:
        # beantragten (oder schon freigegebenen) eigenen Trick freigeben
        trick_type = db.trick_type_get(row["trick_type_id"])
        name = re.sub(r"\s+", " ", (request.form.get("name") or trick_type["name"]).strip())[:40] or trick_type["name"]
        db.trick_type_update(
            trick_type["id"],
            name=name,
            description=(request.form.get("description") or trick_type["description"]).strip()[:300],
            points=_clean_points(request.form.get("points") or trick_type["points"]),
            status="approved",
        )
        label, type_id = custom_key(trick_type["id"]), trick_type["id"]
        message = f"„{name}“ ist freigegeben und erscheint jetzt für alle in der Trickliste."
    elif choice in BUILTIN:
        label, type_id = choice, None
        message = f"Markierung #{label_id} als „{BUILTIN[choice]}“ freigegeben."
    else:
        match = CUSTOM_KEY.match(choice)
        trick_type = db.trick_type_get(int(match.group(1))) if match else None
        if trick_type is None or trick_type["status"] != "approved":
            return _admin_back("Bitte einen gültigen Trick wählen.")
        label, type_id = choice, trick_type["id"]
        message = f"Markierung #{label_id} als „{trick_type['name']}“ freigegeben."
    previous_type = row["trick_type_id"]
    db.trick_label_review(label_id, "approved", label, type_id, note)
    if previous_type and previous_type != type_id:
        _drop_orphan_request(previous_type)
    return _admin_back(message)


@bp.post("/admin/eigene-tricks/<int:type_id>")
def admin_type(type_id):
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    trick_type = db.trick_type_get(type_id)
    if trick_type is None:
        abort(404)
    action = request.form.get("action")
    if action == "save":
        name = re.sub(r"\s+", " ", (request.form.get("name") or "").strip())[:40]
        if len(name) < 2:
            return _admin_back("Der Name ist zu kurz.")
        db.trick_type_update(
            type_id,
            name=name,
            description=(request.form.get("description") or "").strip()[:300],
            points=_clean_points(request.form.get("points")),
        )
        return _admin_back(f"„{name}“ gespeichert.")
    if action == "reject":
        # nicht mehr waehlbar, keine Trainingsdaten mehr; offene Markierungen dazu werden mit abgelehnt
        db.trick_type_update(type_id, status="rejected")
        note = (request.form.get("note") or "").strip()[:300] or f"Der Trick „{trick_type['name']}“ wurde abgelehnt."
        count = db.trick_labels_reject_pending_of_type(type_id, note)
        return _admin_back(f"„{trick_type['name']}“ abgelehnt" + (f", {count} offene Markierungen mit abgelehnt." if count else "."))
    if action == "delete":
        # Trick samt allen Markierungen und Video-Ausschnitten entfernen
        labels = db.trick_labels_of_type(type_id)
        for row in labels:
            db.trick_label_delete(row["id"])
            if row["clip_filename"]:
                remove_file(CLIP_DIR / row["clip_filename"])
        db.trick_type_delete(type_id)
        sweep_directory(CLIP_DIR, db.trick_label_filenames())
        return _admin_back(f"„{trick_type['name']}“ gelöscht" + (f" samt {len(labels)} Markierungen." if labels else "."))
    if action in ("approve", "hide"):
        status = {"approve": "approved", "hide": "hidden"}[action]
        db.trick_type_update(type_id, status=status)
        return _admin_back(f"„{trick_type['name']}“: {STATUS_LABELS[status]}.")
    abort(400)


def _admin_item(row):
    try:
        angles = json.loads(row["angles_json"] or "[]")
    except ValueError:
        angles = []
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "username": row["account_username"],
        "email": row["email"],
        "label": row["label"],
        "name": _label_name(row),
        "is_request": bool(row["trick_type_id"]) and row["type_status"] == "pending",
        "trick_type_id": row["trick_type_id"],
        "type_status": row["type_status"],
        "type_points": row["type_points"] or DEFAULT_POINTS,
        "requested_name": row["requested_name"],
        "start": row["t_start"],
        "end": row["t_end"],
        "duration": round(row["t_end"] - row["t_start"], 2),
        "angles": [round(value) for value in angles],
        "peak_rate": round(row["peak_rate"]),
        "mean_throttle": round(row["mean_throttle"] * 100),
        "comment": row["comment"],
        "log_name": row["log_name"],
        "craft": row["craft"],
        "clip_url": url_for("tricks.clip", label_id=row["id"]) if row["clip_filename"] else None,
        "clip_offset": round(max(0.0, row["t_start"] - row["clip_start"]), 2),
        "status": row["status"],
        "note": row["review_note"],
        "created_at": row["created_at"],
        "reviewed_at": row["reviewed_at"],
    }


def admin_overview():
    """Fuer das Admin-Dashboard: offene Markierungen (aelteste zuerst), zuletzt gepruefte, eigene Tricks."""
    types = [dict(row) for row in db.trick_types_list()]
    return {
        "pending": [_admin_item(row) for row in db.trick_labels_by_status("pending")],
        "reviewed": [_admin_item(row) for row in db.trick_labels_by_status("approved", 50)]
        + [_admin_item(row) for row in db.trick_labels_by_status("rejected", 20)],
        "types": types,
        "approved_types": [row for row in types if row["status"] == "approved"],
        "builtin": BUILTIN,
        "status_labels": STATUS_LABELS,
        "examples": len(db.trick_examples()),
    }


# --- Benutzerverwaltung (admin_users.py) ---------------------------------------------------

def labels_of(email):
    return [{
        "id": row["id"], "name": _label_name(row), "status": STATUS_LABELS.get(row["status"], row["status"]),
        "size": row["clip_size"], "created_at": row["created_at"],
        "clip_url": url_for("tricks.clip", label_id=row["id"]) if row["clip_filename"] else None,
    } for row in db.trick_labels_for(email, limit=10000)]
