"""Leaderboard des Blackbox Viewers (/blackbox-viewer/, Ordner blackbox-viewer/).

Die IIS-Regel "BlackboxLeaderboardApi" leitet /blackbox-viewer/api/leaderboard…
hierher (siehe install_webshop_autostart.ps1); im Viewer bleiben die relativen
Pfade gleich. Ohne Webshop (lokale Entwicklung) antwortet weiter der kleine
Node-Server des Viewers (blackbox-viewer/server/leaderboard.mjs).

- Einreichen nur mit Bollisoft-Login: Punkte, erkannte Tricks (mit Zeit),
  das im Browser aufgenommene Video mit Overlay und die Log-Datei.
- Ansehen kann jeder: Rangliste, Video, Trickliste.
- Melden koennen eingeloggte Konten (einmal je Eintrag). Ab
  LEADERBOARD_REPORT_THRESHOLD Meldungen (Default 10) bekommt der Admin eine
  E-Mail mit allen Angaben und der Liste der Meldenden (mailer.py).
- Der Admin (session["is_admin"]) sieht die Meldungen, laedt das Log, markiert
  Eintraege als geprueft und loescht fehlerhafte.
"""

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request, send_file, session, url_for

import community
import countries
import db
import mailer
import roles
from tx15_backups import clean_text, error_response, remove_file, sweep_directory
from i18n import gettext as _

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_int(name, default):
    value = os.environ.get(name, "").strip()
    try:
        return int(value) if value else default
    except ValueError:
        return default


MEDIA_DIR = db.media_dir("LEADERBOARD_MEDIA_DIR", "leaderboard_media")
LEGACY_JSON = PROJECT_ROOT / "blackbox-viewer" / "data" / "leaderboard.json"
MAX_VIDEO_BYTES = _env_int("LEADERBOARD_MAX_VIDEO_BYTES", 600 * 1024 * 1024)
MAX_LOG_BYTES = _env_int("LEADERBOARD_MAX_LOG_BYTES", 64 * 1024 * 1024)
REPORT_THRESHOLD = _env_int("LEADERBOARD_REPORT_THRESHOLD", 10)
SUBMIT_INTERVAL = timedelta(seconds=60)
MAX_TRICKS = 2000
# Akku-Klassen (Zellenzahl): Rangliste je Klasse, siehe _ranking() und ?klasse=4s
CELL_CLASSES = (1, 2, 3, 4, 6, 8)
AJAX_HEADER_VALUE = "blackbox-viewer"
VIEWER_PATH = "/blackbox-viewer/"
LOGIN_URL = f"/login?next={VIEWER_PATH}"
# Video-Erkennung am Dateianfang: WebM/Matroska bzw. MP4 ("ftyp" ab Byte 4).
WEBM_MAGIC = b"\x1a\x45\xdf\xa3"

bp = Blueprint("leaderboard", __name__, url_prefix="/api/leaderboard")


# --- Hilfen ---------------------------------------------------------------

def _account():
    email = session.get("account_email")
    return db.get_account_by_email(email) if email else None


def _is_admin():
    """Admin oder Moderator: Meldungen sehen, Logs laden, pruefen, loeschen."""
    return roles.is_moderator()


def _require_ajax():
    return request.headers.get("X-Requested-With") == AJAX_HEADER_VALUE


def _num(value, low, high, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return min(high, max(low, number))


HASH_RE = re.compile(r"^[0-9a-f]{64}$")
DUPLICATE_MESSAGES = {
    "log_hash": "Dieses Log ist schon im Leaderboard (Eintrag #{id}). Jeder Flug kann nur einmal eingereicht werden.",
    "flight_hash": "Dieser Flug ist schon im Leaderboard (Eintrag #{id}). Jeder Flug kann nur einmal eingereicht werden.",
    "video_hash": "Dieses Video wurde schon eingereicht (Eintrag #{id}). Jedes Video kann nur einmal verwendet werden.",
}


def _clean_hash(value):
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if HASH_RE.match(text) else None


def log_fingerprint(path, log_index):
    """SHA-256 der Log-Datei plus Log-Nummer (eine Datei kann mehrere Fluege enthalten)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return hashlib.sha256(f"{digest.hexdigest()}:{int(log_index)}".encode()).hexdigest()


def _duplicate(log_hash=None, flight_hash=None, video_hash=None):
    """Fehlertext + Eintrags-ID, wenn Log, Flugdaten oder Video schon eingereicht wurden."""
    row, kind = db.leaderboard_find_duplicate(log_hash, flight_hash, video_hash)
    if row is None:
        return None, None
    return _(DUPLICATE_MESSAGES[kind], id=row["id"]), row["id"]


def backfill_log_hashes():
    """Fingerabdruecke fuer Eintraege von vor der Duplikat-Pruefung aus den gespeicherten Logs."""
    count = 0
    for row in db.leaderboard_without_log_hash():
        path = MEDIA_DIR / row["log_filename"]
        if path.is_file():
            db.leaderboard_set_log_hash(row["id"], log_fingerprint(path, row["log_index"]))
            count += 1
    return count


def parse_cells(value):
    """'4', '4s', '4S' oder 4 -> 4; alles andere -> None."""
    text = str(value if value is not None else "").strip().lower().removesuffix("s")
    return int(text) if text.isdigit() and int(text) in CELL_CLASSES else None


def _video_mime(head):
    if head[:4] == WEBM_MAGIC:
        return "video/webm"
    if head[4:8] == b"ftyp":
        return "video/mp4"
    return None


def _clean_tricks(raw):
    tricks = []
    for trick in raw if isinstance(raw, list) else []:
        if not isinstance(trick, dict) or len(tricks) >= MAX_TRICKS:
            continue
        tricks.append({
            "type": clean_text(trick.get("type"), 30),
            "name": clean_text(trick.get("name"), 60),
            "count": int(_num(trick.get("count"), 0, 100)),
            "start": round(_num(trick.get("start"), 0, 86400), 3),
            "end": round(_num(trick.get("end"), 0, 86400), 3),
            "points": int(_num(trick.get("points"), 0, 10_000_000)),
            "comboMultiplier": round(_num(trick.get("comboMultiplier"), 0, 100, 1), 2),
            "source": clean_text(trick.get("source"), 10),
        })
    return tricks


def _site_url():
    return (os.environ.get("DOMAIN_URL") or "https://www.bollisoft.ch").rstrip("/")


def _entry_url(entry_id):
    return f"{_site_url()}{VIEWER_PATH}?eintrag={entry_id}"


def _public(row, rank=None, account=None):
    data = {
        "id": row["id"],
        "pilot": row["pilot"],
        # Forum-Benutzername des einreichenden Kontos (oeffentliches Profil)
        "username": row["account_username"],
        # Land des Kontos (Code), nur wenn im Profil nicht ausgeblendet
        "country": row["country"],
        "score": row["score"],
        "tricks": row["tricks"],
        "best": row["best"],
        "maxCombo": row["max_combo"],
        "craft": row["craft"],
        "cells": row["cells"],
        "duration": row["duration"],
        "createdAt": row["created_at"],
        "hasVideo": bool(row["video_filename"]),
        "verified": bool(row["verified_at"]),
        "mine": bool(account is not None and row["email"] and row["email"].lower() == account["email"].lower()),
    }
    if rank is not None:
        data["rank"] = rank
    if _is_admin():
        data["reportCount"] = row["report_count"]
    return data


def _stage(upload, limit):
    """Upload als .part-Datei speichern. Liefert (dateiname, groesse, kopf) oder Fehlertext."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    filename = uuid.uuid4().hex
    temp = MEDIA_DIR / f"{filename}.part"
    upload.save(temp)
    size = temp.stat().st_size
    with temp.open("rb") as handle:
        head = handle.read(16)
    if size > limit:
        remove_file(temp)
        return None, None, None, _("Datei zu groß (höchstens {v1} MB).", v1=limit // (1024 * 1024))
    return filename, size, head, None


def import_legacy_entries():
    """Einmalig die Eintraege des bisherigen Node-Leaderboards uebernehmen
    (blackbox-viewer/data/leaderboard.json) - ohne Video und ohne Konto."""
    marker = MEDIA_DIR / ".legacy-imported"
    if marker.exists() or not LEGACY_JSON.is_file() or db.leaderboard_count():
        return 0
    try:
        entries = json.loads(LEGACY_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    count = 0
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not clean_text(entry.get("pilot"), 32):
            continue
        fields = {
            "pilot": clean_text(entry.get("pilot"), 32),
            "score": int(_num(entry.get("score"), 0, 10_000_000)),
            "tricks": int(_num(entry.get("tricks"), 0, 10_000)),
            "best": clean_text(entry.get("best"), 40),
            "max_combo": int(_num(entry.get("maxCombo"), 0, 1000)),
            "craft": clean_text(entry.get("craft"), 40),
            "duration": _num(entry.get("duration"), 0, 86400),
            "source": clean_text(entry.get("source"), 8) or "bbl",
        }
        if clean_text(entry.get("createdAt"), 40):
            fields["created_at"] = clean_text(entry.get("createdAt"), 40)
        db.leaderboard_add(fields)
        count += 1
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{count} Eintraege uebernommen\n", encoding="utf-8")
    return count


# --- Lesen ----------------------------------------------------------------

@bp.get("/auth")
def auth_state():
    account = _account()
    response = jsonify({
        "required": True,
        "loggedIn": account is not None,
        "loginUrl": LOGIN_URL,
        "isAdmin": _is_admin(),
        "username": account["username"] if account is not None else None,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("")
def list_entries():
    limit = int(_num(request.args.get("limit"), 1, 200, 50))
    account = _account()
    rows = db.leaderboard_list(limit, cells=parse_cells(request.args.get("klasse")))
    response = jsonify({"entries": [_public(row, rank=index + 1, account=account) for index, row in enumerate(rows)]})
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/<int:entry_id>")
def entry_detail(entry_id):
    row = db.leaderboard_get(entry_id)
    if row is None:
        return error_response(_("Eintrag nicht gefunden."), 404)
    account = _account()
    data = _public(row, rank=db.leaderboard_rank(entry_id), account=account)
    data.update({
        "trickList": json.loads(row["tricks_json"] or "[]"),
        "videoStart": row["video_start"],
        "videoUrl": f"api/leaderboard/{entry_id}/video" if row["video_filename"] else None,
        "reported": bool(account is not None and db.leaderboard_has_reported(entry_id, account["email"])),
        "canReport": bool(account is not None and not data["mine"]),
        "loggedIn": account is not None,
        "loginUrl": f"/login?next={VIEWER_PATH}?eintrag={entry_id}",
    })
    if _is_admin():
        data.update({
            "account": {"email": row["email"], "username": row["account_username"]},
            "logUrl": f"api/leaderboard/{entry_id}/log" if row["log_filename"] else None,
            "logName": row["log_name"],
            "logIndex": row["log_index"],
            "settings": json.loads(row["settings_json"] or "{}"),
            "reports": [
                {"username": r["username"], "email": r["email"], "reason": r["reason"], "createdAt": r["created_at"]}
                for r in db.leaderboard_reports(entry_id)
            ],
            "reportThreshold": REPORT_THRESHOLD,
            "alertSentAt": row["report_alert_sent_at"],
        })
    response = jsonify({"entry": data})
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/<int:entry_id>/video")
def entry_video(entry_id):
    row = db.leaderboard_get(entry_id)
    path = MEDIA_DIR / row["video_filename"] if row is not None and row["video_filename"] else None
    if path is None or not path.is_file():
        return error_response("Kein Video vorhanden.", 404)
    # conditional=True: Range-Anfragen (Vorspulen im Video) werden unterstuetzt.
    response = send_file(path, mimetype=row["video_mime"] or "video/webm", conditional=True, max_age=3600)
    return response


@bp.get("/<int:entry_id>/log")
def entry_log(entry_id):
    if not _is_admin():
        return error_response(_("Nur für den Admin."), 403)
    row = db.leaderboard_get(entry_id)
    path = MEDIA_DIR / row["log_filename"] if row is not None and row["log_filename"] else None
    if path is None or not path.is_file():
        return error_response("Kein Log vorhanden.", 404)
    response = send_file(path, mimetype="application/octet-stream", as_attachment=True,
                         download_name=row["log_name"] or f"eintrag-{entry_id}.bbl", max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


# --- Einreichen -----------------------------------------------------------

@bp.post("/pruefen")
def check_duplicate():
    """Vorab-Pruefung des Viewers, bevor er den Flug aufnimmt (spart das Warten auf ein abgelehntes Video)."""
    if _account() is None:
        return jsonify({"error": _("Zum Einreichen bitte einloggen."), "loginUrl": LOGIN_URL}), 401
    if not _require_ajax():
        return error_response(_("Ungültige Anfrage."), 400)
    payload = request.get_json(silent=True) or {}
    message, entry_id = _duplicate(_clean_hash(payload.get("logHash")), _clean_hash(payload.get("flightHash")),
                                   _clean_hash(payload.get("videoHash")))
    return jsonify({"duplicate": bool(message), "error": message, "entryId": entry_id})


@bp.post("")
def submit():
    account = _account()
    if account is None:
        return jsonify({"error": _("Zum Einreichen bitte einloggen."), "loginUrl": LOGIN_URL}), 401
    if not _require_ajax():
        return error_response(_("Ungültige Anfrage."), 400)
    if request.content_length and request.content_length > MAX_VIDEO_BYTES + MAX_LOG_BYTES + 4 * 1024 * 1024:
        return error_response(_("Die Einreichung ist zu groß."), 413)
    last = db.leaderboard_last_submission(account["email"])
    if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < SUBMIT_INTERVAL:
        return error_response(_("Bitte eine Minute warten, bevor du den nächsten Flug einreichst."), 429)
    try:
        meta = json.loads(request.form.get("meta") or "{}")
    except ValueError:
        meta = None
    if not isinstance(meta, dict):
        return error_response(_("Ungültige Angaben."), 400)
    pilot = clean_text(meta.get("pilot"), 32)
    source = clean_text(meta.get("source"), 8)
    if not pilot:
        return error_response("Pilotname fehlt.", 400)
    if source == "demo":
        return error_response(_("Demo-Flüge können nicht eingereicht werden."), 400)
    if source not in ("bbl", "csv"):
        return error_response(_("Nur echte Blackbox-Logs (.bbl, .bfl, .csv) können eingereicht werden."), 400)
    video = request.files.get("video")
    if video is None:
        return error_response(_("Das Video mit Overlay fehlt."), 400)
    log = request.files.get("log")
    if log is None:
        return error_response(_("Das Log fehlt."), 400)
    flight_hash = _clean_hash(meta.get("flightHash"))
    video_hash = _clean_hash(meta.get("videoHash"))
    log_index = int(_num(meta.get("logIndex"), 0, 1000))
    message, _entry = _duplicate(None, flight_hash, video_hash)
    if message:
        return error_response(message, 409)

    staged = []
    try:
        video_name, video_size, head, problem = _stage(video, MAX_VIDEO_BYTES)
        if problem:
            return error_response(f"Video: {problem}", 413)
        staged.append(video_name)
        mime = _video_mime(head)
        if mime is None:
            return error_response(_("Das Video ist kein WebM- oder MP4-Video."), 400)
        log_name, log_size, _head, problem = _stage(log, MAX_LOG_BYTES)
        if problem:
            return error_response(f"Log: {problem}", 413)
        staged.append(log_name)
        # dieselbe Datei (gleiche Log-Nummer) wurde schon eingereicht?
        log_hash = log_fingerprint(MEDIA_DIR / f"{log_name}.part", log_index)
        message, _entry = _duplicate(log_hash)
        if message:
            return error_response(message, 409)
        tricks = _clean_tricks(meta.get("trickList"))
        settings = meta.get("settings") if isinstance(meta.get("settings"), dict) else {}
        entry_id = db.leaderboard_add({
            "email": account["email"],
            "pilot": pilot,
            "score": int(_num(meta.get("score"), 0, 10_000_000)),
            "tricks": int(_num(meta.get("tricks"), 0, 10_000)),
            "best": clean_text(meta.get("best"), 40),
            "max_combo": int(_num(meta.get("maxCombo"), 0, 1000)),
            "craft": clean_text(meta.get("craft"), 40),
            "cells": parse_cells(meta.get("cells")),
            "duration": _num(meta.get("duration"), 0, 86400),
            "source": source,
            "log_name": clean_text(meta.get("logName"), 120),
            "log_index": log_index,
            "log_hash": log_hash,
            "flight_hash": flight_hash,
            "video_hash": video_hash,
            "tricks_json": json.dumps(tricks, ensure_ascii=False),
            "settings_json": json.dumps(settings, ensure_ascii=False)[:20000],
            "video_filename": video_name,
            "video_size": video_size,
            "video_mime": mime,
            "video_start": _num(meta.get("videoStart"), -86400, 86400),
            "log_filename": log_name,
            "log_size": log_size or 0,
        })
        # Erst nach dem Datensatz die fertigen Dateien anlegen (siehe sweep_directory()).
        for name in staged:
            os.replace(MEDIA_DIR / f"{name}.part", MEDIA_DIR / name)
        staged = []
    finally:
        for name in staged:
            remove_file(MEDIA_DIR / f"{name}.part")
    row = db.leaderboard_get(entry_id)
    return jsonify({"entry": _public(row, account=account), "rank": db.leaderboard_rank(entry_id)}), 201


# --- Melden ---------------------------------------------------------------

def _report_mail(row, reports):
    tricks = json.loads(row["tricks_json"] or "[]")
    lines = [
        f"Der Leaderboard-Eintrag #{row['id']} wurde {len(reports)}-mal gemeldet "
        f"(Schwelle: {REPORT_THRESHOLD}).",
        "",
        "EINTRAG",
        f"  Pilot:          {row['pilot']}",
        f"  Konto:          {row['account_username'] or '–'} <{row['email'] or 'ohne Konto'}>",
        f"  Punkte:         {row['score']}",
        f"  Platz:          {db.leaderboard_rank(row['id'])}",
        f"  Tricks:         {row['tricks']} (bester: {row['best'] or '–'}, Combo {row['max_combo']}x)",
        f"  Copter:         {row['craft'] or '–'}",
        f"  Flugdauer:      {row['duration']:.1f} s",
        f"  Log:            {row['log_name'] or '–'} (Log {row['log_index'] + 1})",
        f"  Eingereicht:    {row['created_at']}",
        f"  Geprüft:        {row['verified_at'] or 'nein'}",
        f"  Ansehen:        {_entry_url(row['id'])}",
        "",
        "ERKANNTE TRICKS",
    ]
    for trick in tricks[:60]:
        lines.append(f"  {trick['start']:8.2f} s  {trick['name']:<28} {trick['points']:>7} P")
    if len(tricks) > 60:
        lines.append(f"  … und {len(tricks) - 60} weitere")
    lines += ["", f"MELDUNGEN ({len(reports)})"]
    for index, report in enumerate(reports, 1):
        lines.append(
            f"  {index:>2}. {report['username'] or '–'} <{report['email']}>  {report['created_at']}"
            + (f"\n      Grund: {report['reason']}" if report["reason"] else "")
        )
    lines += ["", f"Admin-Dashboard: {_site_url()}/admin"]
    return f"[Bollisoft] Leaderboard-Eintrag #{row['id']} {len(reports)}× gemeldet: {row['pilot']} ({row['score']} P)", "\n".join(lines)


def _send_report_alert(entry_id, subject, body):
    ok, problem = mailer.send_admin_mail(subject, body)
    if ok:
        db.leaderboard_set_alert_sent(entry_id)
    else:
        print(f"[leaderboard] Meldungs-Mail fuer Eintrag {entry_id} nicht gesendet: {problem}", flush=True)


@bp.post("/<int:entry_id>/report")
def report(entry_id):
    account = _account()
    if account is None:
        return jsonify({"error": _("Zum Melden bitte einloggen."), "loginUrl": f"/login?next={VIEWER_PATH}?eintrag={entry_id}"}), 401
    if not _require_ajax():
        return error_response(_("Ungültige Anfrage."), 400)
    row = db.leaderboard_get(entry_id)
    if row is None:
        return error_response(_("Eintrag nicht gefunden."), 404)
    if row["email"] and row["email"].lower() == account["email"].lower():
        return error_response(_("Den eigenen Eintrag kannst du nicht melden."), 403)
    payload = request.get_json(silent=True) or {}
    reason = clean_text(payload.get("reason"), 500)
    if not db.leaderboard_add_report(entry_id, account["email"], reason):
        return error_response("Du hast diesen Eintrag bereits gemeldet.", 409)
    reports = db.leaderboard_reports(entry_id)
    if len(reports) >= REPORT_THRESHOLD and not row["report_alert_sent_at"]:
        subject, body = _report_mail(db.leaderboard_get(entry_id), reports)
        threading.Thread(target=_send_report_alert, args=(entry_id, subject, body), daemon=True).start()
    return jsonify({"reported": True})


# --- Admin ----------------------------------------------------------------

@bp.post("/<int:entry_id>/verify")
def verify(entry_id):
    if not _is_admin():
        return error_response(_("Nur für den Admin."), 403)
    if not _require_ajax():
        return error_response(_("Ungültige Anfrage."), 400)
    if db.leaderboard_get(entry_id) is None:
        return error_response(_("Eintrag nicht gefunden."), 404)
    payload = request.get_json(silent=True) or {}
    db.leaderboard_set_verified(entry_id, bool(payload.get("verified", True)))
    return jsonify({"verified": bool(payload.get("verified", True))})


@bp.delete("/<int:entry_id>")
def delete(entry_id):
    if not _is_admin():
        return error_response(_("Nur für den Admin."), 403)
    if not _require_ajax():
        return error_response(_("Ungültige Anfrage."), 400)
    row = db.leaderboard_delete(entry_id)
    if row is None:
        return error_response(_("Eintrag nicht gefunden."), 404)
    for name in (row["video_filename"], row["log_filename"]):
        if name:
            remove_file(MEDIA_DIR / name)
    sweep_directory(MEDIA_DIR, db.leaderboard_filenames() | {".legacy-imported"})
    return jsonify({"deleted": entry_id})


def admin_overview():
    """Fuer das Admin-Dashboard: alle Eintraege, gemeldete zuerst."""
    rows = db.leaderboard_by_reports()
    return {
        "entries": [
            {
                "id": row["id"],
                "pilot": row["pilot"],
                "score": row["score"],
                "cells": row["cells"],
                "email": row["email"],
                "username": row["account_username"],
                "created_at": row["created_at"],
                "report_count": row["report_count"],
                "verified": bool(row["verified_at"]),
                "alert_sent": bool(row["report_alert_sent_at"]),
                "has_video": bool(row["video_filename"]),
                "url": f"{VIEWER_PATH}?eintrag={row['id']}",
            }
            for row in rows
        ],
        "threshold": REPORT_THRESHOLD,
        "mail_configured": mailer.admin_mail_configured(),
    }


# --- Seiten: /leaderboard (Rangliste) und /leaderboard/eintrag/<id> -----------------

pages = Blueprint("leaderboard_pages", __name__)
PAGE_SIZE = 50


def _pilot_key(row):
    """Ein Pilot = ein Konto; alte Eintraege ohne Konto nach Pilotname."""
    return (row["email"] or f"pilot:{row['pilot']}").lower()


def _ranking(view, land, cells=None):
    """(eintraege mit Platz, Laender-Uebersicht). view: 'piloten' = bester Flug je Pilot.
    cells: nur eine Akku-Klasse - Plaetze und "bester Flug" gelten dann innerhalb der Klasse."""
    rows = db.leaderboard_all()
    if cells is not None:
        rows = [row for row in rows if row["cells"] == cells]
    global_rank = {row["id"]: index + 1 for index, row in enumerate(rows)}
    best, seen = [], set()
    for row in rows:
        if _pilot_key(row) not in seen:
            seen.add(_pilot_key(row))
            best.append(row)
    # Laender-Uebersicht immer auf Basis "bester Flug je Pilot"
    by_country = {}
    for row in best:
        if row["country"]:
            stats = by_country.setdefault(row["country"], {"code": row["country"], "pilots": 0, "top": row})
            stats["pilots"] += 1
    overview = sorted(by_country.values(), key=lambda c: (-c["top"]["score"], countries.name(c["code"])))
    chosen = best if view == "piloten" else rows
    if land:
        chosen = [row for row in chosen if row["country"] == land]
    return [{"row": row, "rank": index + 1, "global_rank": global_rank[row["id"]]} for index, row in enumerate(chosen)], overview


@pages.get("/leaderboard")
def page():
    kategorie = request.args.get("kategorie")
    if kategorie in ("vs", "turnier"):
        import vs  # Kreisimport vermeiden: vs.py nutzt Hilfen aus diesem Modul

        return render_template(
            "leaderboard_vs.html",
            kategorie=kategorie,
            rows=vs.vs_rankings() if kategorie == "vs" else vs.tournament_rankings(),
            elo_start=vs.ELO_START,
            points={"win": vs.POINTS_WIN, "final": vs.POINTS_FINAL, "title": vs.POINTS_TITLE},
        )
    view = "fluege" if request.args.get("ansicht") == "fluege" else "piloten"
    land = (request.args.get("land") or "").upper()
    if land and not countries.is_valid(land):
        land = ""
    cells = parse_cells(request.args.get("klasse"))
    klasse = f"{cells}s" if cells else None
    items, overview = _ranking(view, land, cells)
    pages_total = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    current = min(max(1, request.args.get("seite", 1, type=int)), pages_total)
    account = _account()
    own_country = account["country"] if account is not None and countries.is_valid(account["country"]) else ""
    return render_template(
        "leaderboard.html",
        items=items[(current - 1) * PAGE_SIZE:current * PAGE_SIZE],
        total=len(items),
        view=view,
        land=land,
        cells=cells,
        klasse=klasse,
        cell_classes=CELL_CLASSES,
        overview=overview,
        page=current,
        pages=pages_total,
        own_country=own_country,
        countries_with_entries=sorted({c["code"] for c in overview}, key=countries.name),
        avatar_url=community.avatar_url,
    )


@pages.get("/leaderboard/eintrag/<int:entry_id>")
def entry(entry_id):
    row = db.leaderboard_get(entry_id)
    if row is None:
        abort(404)
    items, _overview = _ranking("fluege", row["country"] or "")
    country_rank = next((item["rank"] for item in items if item["row"]["id"] == entry_id), None) if row["country"] else None
    class_items, _overview = _ranking("fluege", "", row["cells"]) if row["cells"] else ([], None)
    class_rank = next((item["rank"] for item in class_items if item["row"]["id"] == entry_id), None)
    account = _account()
    mine = bool(account is not None and row["email"] and row["email"].lower() == account["email"].lower())
    return render_template(
        "leaderboard_entry.html",
        entry=row,
        rank=db.leaderboard_rank(entry_id),
        country_rank=country_rank,
        class_rank=class_rank,
        tricks=json.loads(row["tricks_json"] or "[]"),
        video_url=url_for("leaderboard.entry_video", entry_id=entry_id) if row["video_filename"] else None,
        avatar=community.avatar_url(row["avatar_filename"]),
        account=account,
        mine=mine,
        reported=bool(account is not None and db.leaderboard_has_reported(entry_id, account["email"])),
        is_admin=_is_admin(),
        report_count=row["report_count"],
        viewer_url=f"{VIEWER_PATH}?eintrag={entry_id}",
    )
