"""VS-Duelle (1 gegen 1) und Turniere (K.-o.-System) fuer den Blackbox Viewer.

Ablauf eines Duells:
- Ein Pilot legt ein Duell gegen einen anderen an (Benutzersuche) und waehlt die
  Wertungszeit (60/90/120/180 s). Gezaehlt werden nur Tricks ab dem Abheben
  innerhalb dieser Zeit (Berechnung im Viewer, blackbox-viewer/src/core/match.ts).
- Jeder reicht im Viewer seinen eigenen Flug ein (Clip mit Overlay + Log). Der
  Ersteller darf zusaetzlich den Flug des Gegners hochladen; der zaehlt erst,
  wenn der Gegner ihn bestaetigt.
- Sind beide Fluege bestaetigt, steht der Sieger fest (hoehere Punktzahl), die
  Elo-Wertung (vs_ratings) wird verbucht.

Turniere: jeder mit Konto legt eines an und leitet es (Admins/Moderatoren
duerfen jedes verwalten). Piloten melden sich an oder werden hinzugefuegt; beim
Start werden sie zufaellig gesetzt, der K.-o.-Baum wird angelegt (Freilose, wenn
die Teilnehmerzahl keine Zweierpotenz ist). Jedes Spiel ist ein normales Duell;
der Sieger rueckt automatisch in die naechste Runde.

Die Logs bleiben privat (nur Admin/Moderatoren), die Clips mit Overlay sind
oeffentlich wie im Leaderboard. Der VS-Viewer (/blackbox-viewer/vs.html) spielt
beide Clips synchron ab.
"""

import difflib
import json
import math
import os
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for

import community
import db
import roles
from i18n import gettext as _
from leaderboard import AJAX_HEADER_VALUE, CELL_CLASSES, _clean_hash, _env_int, _num, _video_mime, log_fingerprint, parse_cells
from tx15_backups import clean_text, error_response, remove_file, sweep_directory

MATCH_LIMITS = (60, 90, 120, 180)
DEFAULT_LIMIT = 120
MEDIA_DIR = db.media_dir("VS_MEDIA_DIR", "vs_media")
MAX_VIDEO_BYTES = _env_int("VS_MAX_VIDEO_BYTES", 300 * 1024 * 1024)
MAX_LOG_BYTES = _env_int("VS_MAX_LOG_BYTES", 64 * 1024 * 1024)
MAX_OPEN_MATCHES = 20
SUBMIT_INTERVAL = timedelta(seconds=30)
TOURNAMENT_SIZES = (4, 8, 10, 12, 16, 24, 32, 64)
ELO_START = 1000
ELO_K = 32
# Turnierpunkte: je gewonnenes Spiel, Finalteilnahme, Turniersieg
POINTS_WIN, POINTS_FINAL, POINTS_TITLE = 2, 3, 5
VIEWER_PATH = "/blackbox-viewer/"

bp = Blueprint("vs", __name__)


# --- Hilfen ---------------------------------------------------------------------------

def _account():
    email = session.get("account_email")
    return db.get_account_by_email(email) if email else None


def _me():
    account = _account()
    return account["email"].lower() if account is not None else None


def _same(a, b):
    return bool(a) and bool(b) and a.lower() == b.lower()


def _require_ajax():
    return request.headers.get("X-Requested-With") == AJAX_HEADER_VALUE


def login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if _account() is None:
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def _name(username, email=None):
    return username or (_("Freilos") if email is None else "?")


def _manager(tournament):
    """Turnierleitung: Veranstalter, Admin oder Moderator."""
    return tournament is not None and (_same(tournament["organizer"], _me()) or roles.is_moderator())


def _now():
    return datetime.now(timezone.utc).isoformat()


# --- Elo ---------------------------------------------------------------------------------

def _rating(email):
    row = db.vs_get_rating(email)
    if row is None:
        return {"elo": ELO_START, "wins": 0, "losses": 0, "draws": 0, "best_score": 0}
    return dict(row)


def elo_update(rating_a, rating_b, score_a):
    """score_a: 1 Sieg A, 0 Sieg B, 0.5 unentschieden. Liefert die neuen Werte."""
    expected_a = 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    delta = ELO_K * (score_a - expected_a)
    return rating_a + delta, rating_b - delta


def _apply_elo(match, entries):
    a, b = match["pilot_a"], match["pilot_b"]
    ra, rb = _rating(a), _rating(b)
    result = 0.5 if not match["winner"] else (1.0 if _same(match["winner"], a) else 0.0)
    ra["elo"], rb["elo"] = elo_update(ra["elo"], rb["elo"], result)
    scores = {e["pilot"].lower(): e["score"] for e in entries}
    for rating, email, outcome in ((ra, a, result), (rb, b, 1 - result)):
        if outcome == 1:
            rating["wins"] += 1
        elif outcome == 0:
            rating["losses"] += 1
        else:
            rating["draws"] += 1
        rating["best_score"] = max(rating["best_score"], scores.get(email.lower(), 0))
        db.vs_save_rating(email, round(rating["elo"], 2), rating["wins"], rating["losses"], rating["draws"], rating["best_score"])
    db.vs_update_match(match["id"], rated=1)


# --- Match entscheiden, Turnier weiterfuehren ---------------------------------------------

def _decide(match, entries):
    """Sieger nach Punkten; im Turnier bei Gleichstand mehr Tricks, dann die fruehere Einreichung."""
    a, b = sorted(entries, key=lambda e: (-e["score"], -e["tricks"], e["created_at"]))
    if a["score"] == b["score"] and not match["tournament_id"]:
        return None
    return a["pilot"]


def complete_if_ready(match_id):
    match = db.vs_get_match(match_id)
    if match is None or match["status"] != "open" or not match["pilot_a"] or not match["pilot_b"]:
        return False
    entries = [e for e in db.vs_entries(match_id) if e["confirmed"]]
    pilots = {e["pilot"].lower() for e in entries}
    if pilots != {match["pilot_a"].lower(), match["pilot_b"].lower()}:
        return False
    winner = _decide(match, entries)
    db.vs_update_match(match_id, status="done", winner=winner, completed_at=_now())
    match = db.vs_get_match(match_id)
    _apply_elo(match, entries)
    _advance(match)
    return True


def _advance(match):
    """Sieger eines Turnierspiels in die naechste Runde setzen; nach dem Finale ist das Turnier vorbei."""
    if not match["tournament_id"] or match["status"] != "done" or not match["winner"]:
        return
    tournament = db.tournament_get(match["tournament_id"])
    rounds = int(math.log2(_bracket_size(tournament)))
    if match["round"] >= rounds:
        db.tournament_update(tournament["id"], status="done", winner=match["winner"], finished_at=_now())
        return
    nxt = db.tournament_match_at(tournament["id"], match["round"] + 1, match["slot"] // 2)
    side = "pilot_a" if match["slot"] % 2 == 0 else "pilot_b"
    db.vs_update_match(nxt["id"], **{side: match["winner"]})


def _bracket_size(tournament):
    players = len(db.tournament_players(tournament["id"]))
    return max(2, 1 << max(1, math.ceil(math.log2(max(2, players)))))


def start_tournament(tournament):
    """Zufaellige Setzliste, kompletter K.-o.-Baum, Freilose fuer die ersten Gesetzten."""
    players = [row["email"] for row in db.tournament_players(tournament["id"])]
    secrets.SystemRandom().shuffle(players)
    for seed, email in enumerate(players, start=1):
        db.tournament_set_seed(tournament["id"], email, seed)
    size = _bracket_size(tournament)
    rounds = int(math.log2(size))
    slots = players + [None] * (size - len(players))
    created = []
    for r in range(1, rounds + 1):
        for s in range(size >> r):
            fields = {"created_by": tournament["organizer"], "limit_s": tournament["limit_s"], "tournament_id": tournament["id"],
                      "round": r, "slot": s}
            if r == 1:
                # Gesetzte 1..n treffen von aussen nach innen aufeinander, Freilose gehen an die Ersten
                fields["pilot_a"], fields["pilot_b"] = slots[s], slots[size - 1 - s]
            created.append((r, db.vs_create_match(fields)))
    db.tournament_update(tournament["id"], status="running", signup_open=0, started_at=_now())
    for r, match_id in created:
        if r != 1:
            continue
        match = db.vs_get_match(match_id)
        if match["pilot_b"] is None:
            db.vs_update_match(match_id, status="done", winner=match["pilot_a"], result_note="Freilos", completed_at=_now())
            _advance(db.vs_get_match(match_id))


def walkover(match, winner, note="kampflos"):
    db.vs_update_match(match["id"], status="done", winner=winner, result_note=note, completed_at=_now())
    _advance(db.vs_get_match(match["id"]))


# --- Darstellung --------------------------------------------------------------------------

def _pilot(email, username, avatar):
    return {"username": username, "avatar": community.avatar_url(avatar), "initials": community.initials(username or "?")} if email else None


def _entry_public(entry, viewer):
    visible = entry["confirmed"] or _same(entry["pilot"], viewer) or _same(entry["uploaded_by"], viewer) or roles.is_moderator()
    data = {
        "id": entry["id"],
        "pilot": entry["pilot_username"],
        "uploadedBy": entry["uploader_username"],
        "confirmed": bool(entry["confirmed"]),
        "score": entry["score"],
        "tricks": entry["tricks"],
        "best": entry["best"],
        "maxCombo": entry["max_combo"],
        "cells": entry["cells"],
        "craft": entry["craft"],
        "windowStart": entry["window_start"],
        "videoStart": entry["video_start"],
        "createdAt": entry["created_at"],
    }
    if visible:
        data["trickList"] = json.loads(entry["tricks_json"] or "[]")
        data["videoUrl"] = url_for("vs.entry_video", entry_id=entry["id"]) if entry["video_filename"] else None
    if roles.is_moderator() and entry["log_filename"]:
        data["logUrl"] = url_for("vs.entry_log", entry_id=entry["id"])
    data["canDelete"] = bool(viewer) and (_same(entry["pilot"], viewer) or _same(entry["uploaded_by"], viewer) or roles.is_moderator())
    return data


def match_view(match, viewer=None):
    """Alles, was Seite und Viewer zu einem Duell brauchen."""
    viewer = viewer if viewer is not None else _me()
    entries = db.vs_entries(match["id"])
    by_pilot = {e["pilot"].lower(): e for e in entries}
    tournament = db.tournament_get(match["tournament_id"]) if match["tournament_id"] else None
    is_pilot = _same(match["pilot_a"], viewer) or _same(match["pilot_b"], viewer)
    can_upload_for = []
    if match["status"] == "open" and viewer:
        for email, username in ((match["pilot_a"], match["username_a"]), (match["pilot_b"], match["username_b"])):
            if not email or email.lower() in by_pilot:
                continue
            if _same(email, viewer) or (tournament is None and _same(match["created_by"], viewer)) or _manager(tournament):
                can_upload_for.append(username)
    return {
        "id": match["id"],
        "status": match["status"],
        "limit": match["limit_s"],
        "note": match["result_note"],
        "round": match["round"],
        "slot": match["slot"],
        "tournament": {"id": tournament["id"], "name": tournament["name"]} if tournament else None,
        "a": _pilot(match["pilot_a"], match["username_a"], match["avatar_a"]),
        "b": _pilot(match["pilot_b"], match["username_b"], match["avatar_b"]),
        "winner": match["username_winner"],
        "draw": match["status"] == "done" and not match["winner"],
        "entries": {
            side: _entry_public(by_pilot[email.lower()], viewer)
            for side, email in (("a", match["pilot_a"]), ("b", match["pilot_b"]))
            if email and email.lower() in by_pilot
        },
        "isPilot": is_pilot,
        "canUploadFor": can_upload_for,
        "needsConfirm": [
            e["id"] for e in entries if not e["confirmed"] and _same(e["pilot"], viewer)
        ],
        "canCancel": match["status"] == "open" and tournament is None and (is_pilot or _same(match["created_by"], viewer) or roles.is_moderator()),
        "createdAt": match["created_at"],
        "completedAt": match["completed_at"],
        "submitUrl": f"{VIEWER_PATH}?match={match['id']}",
        "watchUrl": f"{VIEWER_PATH}vs.html?match={match['id']}",
        "pageUrl": url_for("vs.match_page", match_id=match["id"]),
    }


# --- API: Benutzer, Duelle --------------------------------------------------------------------

def _fold(text):
    """Klein, ohne Akzente und Sonderzeichen: "Böll_Shii" -> "bollshii"."""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in text if not unicodedata.combining(c)))


def match_score(query, name):
    """Wie gut passt der Name zur Eingabe? Anfang > enthalten > aehnlich geschrieben (Tippfehler).
    0 = kein Treffer."""
    q, n = _fold(query), _fold(name)
    if not q or not n:
        return 0
    if n.startswith(q):
        return 3 + len(q) / len(n)
    if q in n:
        return 2 + len(q) / len(n)
    head = difflib.SequenceMatcher(None, q, n[:len(q) + 1]).ratio()
    whole = difflib.SequenceMatcher(None, q, n).ratio()
    best = max(head, whole)
    return best if best >= 0.6 else 0


def rank_users(query, rows, limit=10):
    scored = [(match_score(query, row["username"]), row) for row in rows]
    scored = [(s, row) for s, row in scored if s > 0]
    scored.sort(key=lambda item: (-item[0], item[1]["username"].lower()))
    return [row for _s, row in scored[:limit]]


@bp.get("/api/vs/benutzer")
def user_search():
    query = clean_text(request.args.get("q"), 40)
    if len(_fold(query)) < 2:
        return jsonify({"users": []})
    rows = rank_users(query, db.search_candidates(_me()))
    response = jsonify({"users": [
        {"username": r["username"], "avatar": community.avatar_url(r["avatar_filename"]), "country": r["country"],
         "elo": round(r["elo"]) if r["elo"] is not None else ELO_START}
        for r in rows
    ]})
    response.headers["Cache-Control"] = "no-store"
    return response


def create_match(creator_email, opponent_username, limit):
    """Liefert (match_id, None) oder (None, Fehlertext)."""
    creator = db.get_account_by_email(creator_email)
    if creator is None or not creator["username"]:
        return None, _("Für Duelle brauchst du einen Benutzernamen – lege ihn in deinem Konto fest.")
    opponent = db.get_account_by_username(opponent_username or "")
    if opponent is None:
        return None, _("Diesen Piloten gibt es nicht.")
    if _same(opponent["email"], creator_email):
        return None, _("Du kannst dich nicht selbst herausfordern.")
    if limit not in MATCH_LIMITS:
        return None, _("Ungültige Wertungszeit.")
    if db.vs_open_count(creator_email) >= MAX_OPEN_MATCHES:
        return None, _("Du hast schon {n} offene Duelle – schliesse zuerst welche ab.", n=MAX_OPEN_MATCHES)
    match_id = db.vs_create_match({"created_by": creator_email, "pilot_a": creator_email, "pilot_b": opponent["email"], "limit_s": limit})
    return match_id, None


@bp.post("/api/vs/matches")
def api_create_match():
    account = _account()
    if account is None:
        return error_response(_("Bitte einloggen."), 401)
    if not _require_ajax():
        return error_response(_("Ungültige Anfrage."), 400)
    payload = request.get_json(silent=True) or {}
    match_id, problem = create_match(account["email"], clean_text(payload.get("opponent"), 40), int(_num(payload.get("limit"), 0, 10000, DEFAULT_LIMIT)))
    if problem:
        return error_response(problem, 400)
    return jsonify({"match": match_view(db.vs_get_match(match_id))}), 201


@bp.get("/api/vs/matches/<int:match_id>")
def api_match(match_id):
    match = db.vs_get_match(match_id)
    if match is None or match["status"] == "cancelled":
        return error_response(_("Duell nicht gefunden."), 404)
    response = jsonify({"match": match_view(match), "loggedIn": _me() is not None})
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/api/vs/meine")
def api_my_matches():
    me = _me()
    if me is None:
        return jsonify({"loggedIn": False, "matches": []})
    matches = [match_view(m, me) for m in db.vs_matches_for(me) if m["status"] == "open"]
    response = jsonify({"loggedIn": True, "matches": [m for m in matches if m["canUploadFor"] or m["needsConfirm"]]})
    response.headers["Cache-Control"] = "no-store"
    return response


def _stage(upload, limit):
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(16)
    temp = MEDIA_DIR / f"{name}.part"
    upload.save(temp)
    size = temp.stat().st_size
    with temp.open("rb") as handle:
        head = handle.read(16)
    if size > limit:
        remove_file(temp)
        return None, None, None, _("Datei zu groß (höchstens {v1} MB).", v1=limit // (1024 * 1024))
    return name, size, head, None


def _clean_window_tricks(raw, limit):
    tricks = []
    for trick in raw if isinstance(raw, list) else []:
        if not isinstance(trick, dict) or len(tricks) >= 500:
            continue
        tricks.append({
            "type": clean_text(trick.get("type"), 30),
            "name": clean_text(trick.get("name"), 60),
            "count": int(_num(trick.get("count"), 0, 100)),
            "start": round(_num(trick.get("start"), 0, 86400), 3),
            "end": round(_num(trick.get("end"), 0, 86400), 3),
            "points": int(_num(trick.get("points"), 0, 1_000_000)),
            "comboMultiplier": round(_num(trick.get("comboMultiplier"), 0, 100, 1), 2),
            "source": clean_text(trick.get("source"), 10),
        })
    return tricks


@bp.post("/api/vs/matches/<int:match_id>/flug")
def api_submit(match_id):
    account = _account()
    if account is None:
        return jsonify({"error": _("Zum Einreichen bitte einloggen."), "loginUrl": f"/login?next={VIEWER_PATH}?match={match_id}"}), 401
    if not _require_ajax():
        return error_response(_("Ungültige Anfrage."), 400)
    match = db.vs_get_match(match_id)
    if match is None or match["status"] != "open":
        return error_response(_("Dieses Duell nimmt keine Flüge mehr an."), 409)
    try:
        meta = json.loads(request.form.get("meta") or "{}")
    except ValueError:
        meta = None
    if not isinstance(meta, dict):
        return error_response(_("Ungültige Angaben."), 400)
    view = match_view(match, account["email"].lower())
    pilot_username = clean_text(meta.get("pilot"), 40) or account["username"]
    if pilot_username not in view["canUploadFor"]:
        return error_response(_("Für diesen Piloten kannst du in diesem Duell keinen Flug einreichen."), 403)
    pilot = db.get_account_by_username(pilot_username)
    last = max((e["created_at"] for e in db.vs_entries_by(account["email"]) if e["uploaded_by"].lower() == account["email"].lower()), default=None)
    if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < SUBMIT_INTERVAL:
        return error_response(_("Bitte kurz warten, bevor du den nächsten Flug einreichst."), 429)
    source = clean_text(meta.get("source"), 8)
    if source not in ("bbl", "csv"):
        return error_response(_("Nur echte Blackbox-Logs (.bbl, .bfl, .csv) können eingereicht werden."), 400)
    if int(_num(meta.get("limit"), 0, 10000)) != match["limit_s"]:
        return error_response(_("Die Wertungszeit stimmt nicht mit dem Duell überein – Seite neu laden."), 400)
    video, log = request.files.get("video"), request.files.get("log")
    if video is None or log is None:
        return error_response(_("Clip und Log werden gebraucht."), 400)
    flight_hash = _clean_hash(meta.get("flightHash"))
    if flight_hash and db.vs_entry_with_hash(flight_hash=flight_hash):
        return error_response(_("Dieser Flug wurde schon in einem Duell eingereicht."), 409)

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
        log_index = int(_num(meta.get("logIndex"), 0, 1000))
        log_hash = log_fingerprint(MEDIA_DIR / f"{log_name}.part", log_index)
        if db.vs_entry_with_hash(log_hash=log_hash):
            return error_response(_("Dieser Flug wurde schon in einem Duell eingereicht."), 409)
        tricks = _clean_window_tricks(meta.get("trickList"), match["limit_s"])
        entry_id = db.vs_add_entry({
            "match_id": match_id,
            "pilot": pilot["email"],
            "uploaded_by": account["email"],
            "confirmed": 1 if _same(pilot["email"], account["email"]) else 0,
            "score": int(_num(meta.get("score"), 0, 10_000_000)),
            "tricks": int(_num(meta.get("tricks"), 0, 10_000)),
            "best": clean_text(meta.get("best"), 40),
            "max_combo": int(_num(meta.get("maxCombo"), 0, 1000)),
            "cells": parse_cells(meta.get("cells")),
            "craft": clean_text(meta.get("craft"), 40),
            "window_start": round(_num(meta.get("windowStart"), 0, 86400), 3),
            "tricks_json": json.dumps(tricks, ensure_ascii=False),
            "settings_json": json.dumps(meta.get("settings") if isinstance(meta.get("settings"), dict) else {}, ensure_ascii=False)[:20000],
            "source": source,
            "log_name": clean_text(meta.get("logName"), 120),
            "log_index": log_index,
            "log_filename": log_name,
            "log_size": log_size,
            "video_filename": video_name,
            "video_size": video_size,
            "video_mime": mime,
            "video_start": _num(meta.get("videoStart"), -86400, 86400),
            "log_hash": log_hash,
            "flight_hash": flight_hash,
            "video_hash": _clean_hash(meta.get("videoHash")),
        })
        for name in staged:
            os.replace(MEDIA_DIR / f"{name}.part", MEDIA_DIR / name)
        staged = []
    finally:
        for name in staged:
            remove_file(MEDIA_DIR / f"{name}.part")
    complete_if_ready(match_id)
    return jsonify({"match": match_view(db.vs_get_match(match_id)), "entryId": entry_id}), 201


@bp.get("/api/vs/eintrag/<int:entry_id>/video")
def entry_video(entry_id):
    entry = db.vs_get_entry(entry_id)
    if entry is None or not entry["video_filename"]:
        abort(404)
    me = _me()
    if not entry["confirmed"] and not (_same(entry["pilot"], me) or _same(entry["uploaded_by"], me) or roles.is_moderator()):
        abort(403)
    path = MEDIA_DIR / entry["video_filename"]
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype=entry["video_mime"] or "video/webm", conditional=True, max_age=3600)


@bp.get("/api/vs/eintrag/<int:entry_id>/log")
def entry_log(entry_id):
    if not roles.is_moderator():
        abort(403)
    entry = db.vs_get_entry(entry_id)
    path = MEDIA_DIR / entry["log_filename"] if entry is not None and entry["log_filename"] else None
    if path is None or not path.is_file():
        abort(404)
    response = send_file(path, mimetype="application/octet-stream", as_attachment=True,
                         download_name=entry["log_name"] or f"duell-{entry_id}.bbl", max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


def delete_entry(entry):
    row = db.vs_delete_entry(entry["id"])
    if row is None:
        return
    for name in (row["video_filename"], row["log_filename"]):
        if name:
            remove_file(MEDIA_DIR / name)
    sweep_directory(MEDIA_DIR, db.vs_entry_filenames())


def delete_user_data(email):
    """Fuer das Loeschen eines Kontos (admin_users.py): eigene Einreichungen samt Dateien."""
    for entry in db.vs_entries_by(email):
        delete_entry(entry)


# --- Ranglisten ---------------------------------------------------------------------------------

def vs_rankings():
    rows = []
    for index, row in enumerate(db.vs_ratings_list(), start=1):
        played = row["wins"] + row["losses"] + row["draws"]
        rows.append({
            "rank": index, "username": row["username"], "avatar": community.avatar_url(row["avatar_filename"]),
            "country": row["country"], "elo": round(row["elo"]), "wins": row["wins"], "losses": row["losses"],
            "draws": row["draws"], "played": played, "winRate": round(100 * (row["wins"] + 0.5 * row["draws"]) / played) if played else 0,
            "bestScore": row["best_score"],
        })
    return rows


def tournament_rankings():
    stats = {}

    def bucket(email):
        return stats.setdefault(email.lower(), {"email": email, "titles": 0, "finals": 0, "wins": 0, "tournaments": set()})

    for row in db.tournament_stats_rows():
        for email in (row["pilot_a"], row["pilot_b"]):
            if email:
                bucket(email)["tournaments"].add(row["tournament_id"])
                if row["round"] == row["rounds"]:
                    bucket(email)["finals"] += 1
        if row["status"] == "done" and row["winner"] and row["result_note"] != "Freilos":
            bucket(row["winner"])["wins"] += 1
        if row["round"] == row["rounds"] and row["t_status"] == "done" and row["t_winner"] and _same(row["winner"], row["t_winner"]):
            bucket(row["t_winner"])["titles"] += 1
    accounts = db.accounts_by_emails([s["email"] for s in stats.values()])
    rows = []
    for key, s in stats.items():
        account = accounts.get(key)
        if account is None:
            continue
        points = s["wins"] * POINTS_WIN + s["finals"] * POINTS_FINAL + s["titles"] * POINTS_TITLE
        rows.append({"username": account["username"], "avatar": community.avatar_url(account["avatar_filename"]),
                     "country": account["country"], "titles": s["titles"], "finals": s["finals"], "wins": s["wins"],
                     "tournaments": len(s["tournaments"]), "points": points})
    rows.sort(key=lambda r: (-r["points"], -r["titles"], -r["finals"], (r["username"] or "").lower()))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


@bp.get("/api/vs/rangliste")
def api_vs_ranking():
    response = jsonify({"entries": vs_rankings(), "kFactor": ELO_K, "start": ELO_START})
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/api/vs/turnier-rangliste")
def api_tournament_ranking():
    response = jsonify({"entries": tournament_rankings(), "points": {"win": POINTS_WIN, "final": POINTS_FINAL, "title": POINTS_TITLE}})
    response.headers["Cache-Control"] = "no-store"
    return response


# --- Turniere: Daten ---------------------------------------------------------------------------

def bracket(tournament):
    """Runden mit ihren Spielen fuer die Baum-Darstellung."""
    matches = db.tournament_matches(tournament["id"])
    rounds = {}
    for m in matches:
        rounds.setdefault(m["round"], []).append(m)
    total = max(rounds) if rounds else 0
    labels = {total: _("Finale"), total - 1: _("Halbfinale"), total - 2: _("Viertelfinale")}
    entries_by_match = {}
    for m in matches:
        entries_by_match[m["id"]] = {e["pilot"].lower(): e for e in db.vs_entries(m["id"]) if e["confirmed"]}
    out = []
    for r in sorted(rounds):
        games = []
        for m in rounds[r]:
            ent = entries_by_match[m["id"]]
            games.append({
                "id": m["id"], "status": m["status"], "note": m["result_note"],
                "a": m["username_a"] if m["pilot_a"] else None, "b": m["username_b"] if m["pilot_b"] else None,
                "a_score": ent[m["pilot_a"].lower()]["score"] if m["pilot_a"] and m["pilot_a"].lower() in ent else None,
                "b_score": ent[m["pilot_b"].lower()]["score"] if m["pilot_b"] and m["pilot_b"].lower() in ent else None,
                "winner": m["username_winner"], "bye": m["result_note"] == "Freilos",
            })
        out.append({"round": r, "label": labels.get(r) if r > total - 3 else _("Runde {n}", n=r), "games": games})
    return out


def tournament_view(tournament):
    me = _me()
    players = db.tournament_players(tournament["id"])
    joined = any(_same(p["email"], me) for p in players)
    return {
        "t": tournament,
        "players": players,
        "joined": joined,
        "manager": _manager(tournament),
        "can_join": bool(me) and tournament["status"] == "signup" and tournament["signup_open"] and not joined and len(players) < tournament["max_players"],
        "bracket": bracket(tournament) if tournament["status"] in ("running", "done") else [],
    }


@bp.get("/api/vs/turniere/<int:tournament_id>")
def api_tournament(tournament_id):
    t = db.tournament_get(tournament_id)
    if t is None or t["status"] == "cancelled":
        return error_response(_("Turnier nicht gefunden."), 404)
    view = tournament_view(t)
    return jsonify({
        "id": t["id"], "name": t["name"], "status": t["status"], "limit": t["limit_s"], "maxPlayers": t["max_players"],
        "organizer": t["organizer_username"], "winner": t["winner_username"],
        "players": [p["username"] for p in view["players"]], "bracket": view["bracket"],
    })


# --- Seiten -----------------------------------------------------------------------------------

def _back(endpoint, message=None, error=None, **values):
    if message:
        flash(message, "success")
    if error:
        flash(error, "error")
    return redirect(url_for(endpoint, **values))


@bp.get("/vs")
def index():
    me = _me()
    mine = [match_view(m, me) for m in db.vs_matches_for(me)] if me else []
    return render_template(
        "vs_index.html",
        mine_open=[m for m in mine if m["status"] == "open"],
        mine_done=[m for m in mine if m["status"] == "done"][:20],
        recent=[match_view(m, me) for m in db.vs_recent_matches(12)],
        tournaments=db.tournament_list(),
        limits=MATCH_LIMITS,
        sizes=TOURNAMENT_SIZES,
        default_limit=DEFAULT_LIMIT,
        prefill=clean_text(request.args.get("gegner"), 40),
        logged_in=me is not None,
    )


@bp.post("/vs/neu")
@login_required_page
def new_match():
    account = _account()
    match_id, problem = create_match(account["email"], clean_text(request.form.get("opponent"), 40), int(_num(request.form.get("limit"), 0, 10000, DEFAULT_LIMIT)))
    if problem:
        return _back("vs.index", error=problem)
    return _back("vs.match_page", message=_("Duell angelegt – jetzt im Blackbox Viewer deinen Flug einreichen."), match_id=match_id)


@bp.get("/vs/match/<int:match_id>")
def match_page(match_id):
    match = db.vs_get_match(match_id)
    if match is None or match["status"] == "cancelled":
        abort(404)
    return render_template("vs_match.html", m=match_view(match), raw=match)


@bp.post("/vs/match/<int:match_id>/bestaetigen")
@login_required_page
def confirm(match_id):
    match = db.vs_get_match(match_id)
    if match is None or match["status"] != "open":
        abort(404)
    for entry in db.vs_entries(match_id):
        if not entry["confirmed"] and _same(entry["pilot"], _me()):
            db.vs_confirm_entry(entry["id"])
    complete_if_ready(match_id)
    return _back("vs.match_page", message=_("Flug bestätigt."), match_id=match_id)


@bp.post("/vs/match/<int:match_id>/eintrag/<int:entry_id>/loeschen")
@login_required_page
def remove_entry(match_id, entry_id):
    match = db.vs_get_match(match_id)
    entry = db.vs_get_entry(entry_id)
    if match is None or entry is None or entry["match_id"] != match_id:
        abort(404)
    if match["status"] != "open":
        return _back("vs.match_page", error=_("Das Duell ist schon entschieden."), match_id=match_id)
    if not (_same(entry["pilot"], _me()) or _same(entry["uploaded_by"], _me()) or roles.is_moderator()):
        abort(403)
    delete_entry(entry)
    return _back("vs.match_page", message=_("Flug zurückgezogen."), match_id=match_id)


@bp.post("/vs/match/<int:match_id>/absagen")
@login_required_page
def cancel_match(match_id):
    match = db.vs_get_match(match_id)
    if match is None:
        abort(404)
    view = match_view(match)
    if not view["canCancel"]:
        abort(403)
    for entry in db.vs_entries(match_id):
        delete_entry(entry)
    db.vs_update_match(match_id, status="cancelled", completed_at=_now())
    return _back("vs.index", message=_("Duell abgesagt."))


@bp.post("/vs/turnier/neu")
@login_required_page
def new_tournament():
    name = re.sub(r"\s+", " ", clean_text(request.form.get("name"), 80))
    size = int(_num(request.form.get("max_players"), 0, 1000, 16))
    limit = int(_num(request.form.get("limit"), 0, 10000, DEFAULT_LIMIT))
    if len(name) < 3:
        return _back("vs.index", error=_("Der Turniername ist zu kurz."))
    if size not in TOURNAMENT_SIZES or limit not in MATCH_LIMITS:
        return _back("vs.index", error=_("Ungültige Angaben."))
    tid = db.tournament_create({"name": name, "description": clean_text(request.form.get("description"), 1000),
                                "organizer": _account()["email"], "max_players": size, "limit_s": limit})
    if request.form.get("join"):
        db.tournament_add_player(tid, _account()["email"])
    return _back("vs.tournament_page", message=_("Turnier angelegt. Lade Piloten ein oder lass sie sich anmelden."), tournament_id=tid)


@bp.get("/vs/turnier/<int:tournament_id>")
def tournament_page(tournament_id):
    t = db.tournament_get(tournament_id)
    if t is None or t["status"] == "cancelled":
        abort(404)
    return render_template("vs_tournament.html", **tournament_view(t), limits=MATCH_LIMITS)


def _tournament_or_404(tournament_id):
    t = db.tournament_get(tournament_id)
    if t is None or t["status"] == "cancelled":
        abort(404)
    return t


@bp.post("/vs/turnier/<int:tournament_id>/anmelden")
@login_required_page
def join(tournament_id):
    t = _tournament_or_404(tournament_id)
    view = tournament_view(t)
    if not view["can_join"]:
        return _back("vs.tournament_page", error=_("Anmelden ist gerade nicht möglich."), tournament_id=tournament_id)
    if not _account()["username"]:
        return _back("vs.tournament_page", error=_("Für Turniere brauchst du einen Benutzernamen – lege ihn in deinem Konto fest."), tournament_id=tournament_id)
    db.tournament_add_player(tournament_id, _account()["email"])
    return _back("vs.tournament_page", message=_("Du bist angemeldet."), tournament_id=tournament_id)


@bp.post("/vs/turnier/<int:tournament_id>/abmelden")
@login_required_page
def leave(tournament_id):
    t = _tournament_or_404(tournament_id)
    if t["status"] != "signup":
        return _back("vs.tournament_page", error=_("Das Turnier läuft schon."), tournament_id=tournament_id)
    db.tournament_remove_player(tournament_id, _account()["email"])
    return _back("vs.tournament_page", message=_("Du bist abgemeldet."), tournament_id=tournament_id)


@bp.post("/vs/turnier/<int:tournament_id>/teilnehmer")
@login_required_page
def add_player(tournament_id):
    t = _tournament_or_404(tournament_id)
    if not _manager(t):
        abort(403)
    if t["status"] != "signup":
        return _back("vs.tournament_page", error=_("Das Turnier läuft schon."), tournament_id=tournament_id)
    account = db.get_account_by_username(clean_text(request.form.get("username"), 40))
    if account is None:
        return _back("vs.tournament_page", error=_("Diesen Piloten gibt es nicht."), tournament_id=tournament_id)
    if len(db.tournament_players(tournament_id)) >= t["max_players"]:
        return _back("vs.tournament_page", error=_("Das Turnier ist voll."), tournament_id=tournament_id)
    db.tournament_add_player(tournament_id, account["email"])
    return _back("vs.tournament_page", message=_("{name} hinzugefügt.", name=account["username"]), tournament_id=tournament_id)


@bp.post("/vs/turnier/<int:tournament_id>/teilnehmer/entfernen")
@login_required_page
def remove_player(tournament_id):
    t = _tournament_or_404(tournament_id)
    if not _manager(t):
        abort(403)
    if t["status"] != "signup":
        return _back("vs.tournament_page", error=_("Das Turnier läuft schon."), tournament_id=tournament_id)
    account = db.get_account_by_username(clean_text(request.form.get("username"), 40))
    if account is not None:
        db.tournament_remove_player(tournament_id, account["email"])
    return _back("vs.tournament_page", message=_("Teilnehmer entfernt."), tournament_id=tournament_id)


@bp.post("/vs/turnier/<int:tournament_id>/anmeldung")
@login_required_page
def toggle_signup(tournament_id):
    t = _tournament_or_404(tournament_id)
    if not _manager(t) or t["status"] != "signup":
        abort(403)
    db.tournament_update(tournament_id, signup_open=0 if t["signup_open"] else 1)
    return _back("vs.tournament_page", tournament_id=tournament_id)


@bp.post("/vs/turnier/<int:tournament_id>/start")
@login_required_page
def start(tournament_id):
    t = _tournament_or_404(tournament_id)
    if not _manager(t):
        abort(403)
    if t["status"] != "signup":
        return _back("vs.tournament_page", error=_("Das Turnier läuft schon."), tournament_id=tournament_id)
    if len(db.tournament_players(tournament_id)) < 2:
        return _back("vs.tournament_page", error=_("Für ein Turnier braucht es mindestens zwei Piloten."), tournament_id=tournament_id)
    start_tournament(t)
    return _back("vs.tournament_page", message=_("Ausgelost! Runde 1 läuft – jeder reicht seinen Flug im Blackbox Viewer ein."), tournament_id=tournament_id)


@bp.post("/vs/turnier/<int:tournament_id>/absagen")
@login_required_page
def cancel_tournament(tournament_id):
    t = _tournament_or_404(tournament_id)
    if not _manager(t):
        abort(403)
    if t["status"] == "done":
        return _back("vs.tournament_page", error=_("Das Turnier ist schon beendet."), tournament_id=tournament_id)
    for m in db.tournament_matches(tournament_id):
        for entry in db.vs_entries(m["id"]):
            delete_entry(entry)
        db.vs_update_match(m["id"], status="cancelled")
    db.tournament_update(tournament_id, status="cancelled", finished_at=_now())
    return _back("vs.index", message=_("Turnier abgesagt."))


@bp.post("/vs/turnier/<int:tournament_id>/match/<int:match_id>/kampflos")
@login_required_page
def walkover_route(tournament_id, match_id):
    t = _tournament_or_404(tournament_id)
    match = db.vs_get_match(match_id)
    if not _manager(t) or match is None or match["tournament_id"] != tournament_id:
        abort(403)
    if match["status"] != "open" or not (match["pilot_a"] and match["pilot_b"]):
        return _back("vs.tournament_page", error=_("Dieses Spiel kann nicht entschieden werden."), tournament_id=tournament_id)
    winner_name = clean_text(request.form.get("winner"), 40)
    winner = next((p for p, u in ((match["pilot_a"], match["username_a"]), (match["pilot_b"], match["username_b"])) if p and u == winner_name), None)
    if winner is None:
        return _back("vs.tournament_page", error=_("Bitte einen der beiden Piloten wählen."), tournament_id=tournament_id)
    walkover(match, winner)
    return _back("vs.tournament_page", message=_("{name} kommt kampflos weiter.", name=winner_name), tournament_id=tournament_id)


def register(app):
    app.register_blueprint(bp)

    @app.template_filter("match_limit")
    def match_limit(seconds):
        seconds = int(seconds or 0)
        return f"{seconds // 60}:{seconds % 60:02d} min" if seconds % 60 else f"{seconds // 60} min"
