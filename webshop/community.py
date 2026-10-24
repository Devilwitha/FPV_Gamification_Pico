"""Community-Funktionen rund ums Forum: oeffentliches Profil (/profil/<name>),
Avatare, Autoren-Karten neben den Beitraegen und Anhaenge (Bilder, kurze Videos).

Dateien liegen unter MEDIA_DIR (Default: <Repo>/community_media, siehe
.gitignore) in avatars/ und attachments/. Fotos werden beim Hochladen neu
kodiert und verkleinert - dabei fallen eingebettete Metadaten (z. B. der
GPS-Standort aus dem Handy) weg. Videos werden nur auf Format und Groesse
geprueft (MP4/WebM, klein).
"""

import io
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, redirect, render_template, send_from_directory, session, url_for
from PIL import Image, ImageOps, UnidentifiedImageError

import db
import roles
from i18n import gettext as _

MEDIA_DIR = db.media_dir("COMMUNITY_MEDIA_DIR", "community_media")
AVATAR_DIR = MEDIA_DIR / "avatars"
ATTACHMENT_DIR = MEDIA_DIR / "attachments"
COPTER_DIR = MEDIA_DIR / "copters"

MAX_ATTACHMENTS = 10
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 20 * 1024 * 1024
# Alle Anhaenge eines Beitrags zusammen; IIS erlaubt fuer /forum etwas mehr
# (install_webshop_autostart.ps1, Parameter ForumUploadLimit).
MAX_POST_UPLOAD_BYTES = 100 * 1024 * 1024
IMAGE_MAX_SIDE = 2560
AVATAR_SIZE = 256
COPTER_IMAGE_SIDE = 1600
MAX_AVATAR_BYTES = 5 * 1024 * 1024
ONLINE_WINDOW = timedelta(minutes=5)
TEAM_NAME = db.FORUM_TEAM_NAME
IMAGE_TYPES = {"JPEG", "PNG", "WEBP", "GIF"}
# Titel nach Anzahl der Beitraege (wie in klassischen Foren)
TITLES = ((1000, "Legende"), (200, "Senior Member"), (50, "Aktives Mitglied"), (10, "Mitglied"), (0, "Neuling"))
LICENSE_KINDS = {
    "a1a3": "EU-Kompetenznachweis A1/A3",
    "a2": "EU-Fernpiloten-Zeugnis A2",
    "sts": "STS-Fernpiloten-Zeugnis",
    "operator": "UAS-Betreiber-Registrierung",
    "other": "Sonstiger Nachweis",
}

bp = Blueprint("community", __name__)


# --- Dateien --------------------------------------------------------------

class UploadError(ValueError):
    """Verstaendliche Fehlermeldung fuer den Benutzer."""


def _video_mime(head):
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm", ".webm"
    if head[4:8] == b"ftyp":
        return "video/mp4", ".mp4"
    return None, None


def _encode_image(data, max_side, square=None):
    """Bild pruefen und neu kodieren (ohne Metadaten). Liefert (bytes, mime, ext, w, h)."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise UploadError(_("ist kein lesbares Bild.")) from exc
    if image.format not in IMAGE_TYPES:
        raise UploadError(_("hat ein nicht unterstuetztes Bildformat (erlaubt: JPG, PNG, WebP, GIF)."))
    # Animierte GIFs bleiben unveraendert (GIF traegt keine Kamera-/GPS-Daten).
    if image.format == "GIF" and getattr(image, "is_animated", False) and square is None:
        return data, "image/gif", ".gif", image.width, image.height
    image = ImageOps.exif_transpose(image)
    if square:
        image = ImageOps.fit(image.convert("RGBA"), (square, square), Image.Resampling.LANCZOS)
    elif max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() or image.mode == "P" else "RGB")
    out = io.BytesIO()
    image.save(out, "WEBP", quality=85, method=4)
    return out.getvalue(), "image/webp", ".webp", image.width, image.height


def prepare_attachments(files, existing=0):
    """Hochgeladene Dateien pruefen/aufbereiten, bevor der Beitrag gespeichert wird.
    Liefert eine Liste vorbereiteter Anhaenge oder wirft UploadError."""
    files = [f for f in files if f and f.filename]
    if existing + len(files) > MAX_ATTACHMENTS:
        raise UploadError(_("Höchstens {max_attachments} Bilder/Videos pro Beitrag.", max_attachments=MAX_ATTACHMENTS))
    prepared, total = [], 0
    for upload in files:
        name = os.path.basename(upload.filename)[:120]
        data = upload.read(MAX_VIDEO_BYTES + 1)
        total += len(data)
        if total > MAX_POST_UPLOAD_BYTES:
            raise UploadError(_("Alle Anhänge zusammen dürfen höchstens {v1} MB groß sein.", v1=MAX_POST_UPLOAD_BYTES // (1024 * 1024)))
        mime, ext = _video_mime(data[:16])
        if mime:
            if len(data) > MAX_VIDEO_BYTES:
                raise UploadError(_("„{name}“ ist zu groß – Videos höchstens {v3} MB.", name=name, v3=MAX_VIDEO_BYTES // (1024 * 1024)))
            prepared.append({"data": data, "kind": "video", "mime": mime, "ext": ext, "name": name, "w": 0, "h": 0})
            continue
        if len(data) > MAX_IMAGE_BYTES:
            raise UploadError(_("„{name}“ ist zu groß – Bilder höchstens {v3} MB.", name=name, v3=MAX_IMAGE_BYTES // (1024 * 1024)))
        try:
            encoded, mime, ext, width, height = _encode_image(data, IMAGE_MAX_SIDE)
        except UploadError as exc:
            raise UploadError(f"„{name}“ {exc}") from exc
        prepared.append({"data": encoded, "kind": "image", "mime": mime, "ext": ext, "name": name, "w": width, "h": height})
    return prepared


def store_attachments(post_id, prepared):
    if not prepared:
        return
    ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
    for item in prepared:
        filename = uuid.uuid4().hex + item["ext"]
        (ATTACHMENT_DIR / filename).write_bytes(item["data"])
        db.forum_add_attachment(post_id, filename, item["name"], item["kind"], item["mime"], len(item["data"]), item["w"], item["h"])


def _cleanup(directory, known, min_age_seconds=120):
    """Dateien ohne Datensatz loeschen (nicht ganz frische - die werden evtl. gerade gespeichert)."""
    if not directory.is_dir():
        return
    cutoff = time.time() - min_age_seconds
    for path in directory.iterdir():
        try:
            if path.is_file() and path.name not in known and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def remove_attachment_files(filenames):
    for name in filenames:
        try:
            (ATTACHMENT_DIR / name).unlink(missing_ok=True)
        except OSError:
            pass


def cleanup_attachments():
    _cleanup(ATTACHMENT_DIR, db.forum_attachment_filenames())


def save_avatar(email, upload):
    data = upload.read(MAX_AVATAR_BYTES + 1)
    if len(data) > MAX_AVATAR_BYTES:
        raise UploadError(_("Das Profilbild darf höchstens {v1} MB groß sein.", v1=MAX_AVATAR_BYTES // (1024 * 1024)))
    try:
        encoded, _mime, ext, _w, _h = _encode_image(data, AVATAR_SIZE, square=AVATAR_SIZE)
    except UploadError as exc:
        raise UploadError(_("Das Profilbild {error}", error=exc)) from exc
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    filename = uuid.uuid4().hex + ext
    (AVATAR_DIR / filename).write_bytes(encoded)
    old = db.get_profile(email)
    db.save_profile(email, {"avatar_filename": filename})
    if old is not None and old["avatar_filename"]:
        (AVATAR_DIR / old["avatar_filename"]).unlink(missing_ok=True)


def remove_avatar(email):
    old = db.get_profile(email)
    if old is not None and old["avatar_filename"]:
        (AVATAR_DIR / old["avatar_filename"]).unlink(missing_ok=True)
        db.save_profile(email, {"avatar_filename": None})


def prepare_copter_image(upload):
    """Copter-Foto pruefen und neu kodieren, bevor etwas gespeichert wird.
    Liefert (bytes, endung) oder None ohne Upload; wirft UploadError."""
    if upload is None or not upload.filename:
        return None
    data = upload.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise UploadError(_("Das Copter-Foto darf höchstens {v1} MB groß sein.", v1=MAX_IMAGE_BYTES // (1024 * 1024)))
    try:
        encoded, _mime, ext, _w, _h = _encode_image(data, COPTER_IMAGE_SIDE)
    except UploadError as exc:
        raise UploadError(_("Das Copter-Foto {error}", error=exc)) from exc
    return encoded, ext


def save_copter_image(copter_id, email, prepared):
    COPTER_DIR.mkdir(parents=True, exist_ok=True)
    encoded, ext = prepared
    filename = uuid.uuid4().hex + ext
    (COPTER_DIR / filename).write_bytes(encoded)
    old = db.set_copter_image(copter_id, email, filename)
    if old:
        remove_copter_image_file(old)


def remove_copter_image(copter_id, email):
    old = db.set_copter_image(copter_id, email, None)
    if old:
        remove_copter_image_file(old)


def remove_copter_image_file(filename):
    try:
        (COPTER_DIR / filename).unlink(missing_ok=True)
    except OSError:
        pass
    # Reste, die Windows wegen eines offenen Handles nicht loeschen liess, beim naechsten Mal mitnehmen.
    _cleanup(COPTER_DIR, db.copter_image_filenames())


def copter_image_url(filename):
    return url_for("community.copter_image", filename=filename) if filename else None


# --- Darstellung ----------------------------------------------------------

def _parse(value):
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def relative_time(value):
    moment = _parse(value)
    if moment is None:
        return _("nie")
    seconds = (datetime.now(timezone.utc) - moment).total_seconds()
    if seconds < 90:
        return _("gerade eben")
    for limit, unit, singular, plural in (
        (3600, 60, "vor einer Minute", "vor {n} Minuten"),
        (86400, 3600, "vor einer Stunde", "vor {n} Stunden"),
        (86400 * 30, 86400, "vor einem Tag", "vor {n} Tagen"),
        (86400 * 365, 86400 * 30, "vor einem Monat", "vor {n} Monaten"),
    ):
        if seconds < limit:
            count = int(seconds // unit)
            return _(singular) if count <= 1 else _(plural, n=count)
    years = int(seconds // (86400 * 365))
    return _("vor einem Jahr") if years <= 1 else _("vor {n} Jahren", n=years)


def member_title(post_count, role=None):
    """Rollen-Titel (z. B. Administrator, Moderator) vor dem Titel nach Beitragszahl."""
    if roles.valid(role):
        return _(roles.ROLES[role]["title"])
    return _(next(title for minimum, title in TITLES if post_count >= minimum))


def avatar_url(filename):
    return url_for("community.avatar", filename=filename) if filename else None


def initials(name):
    parts = [p for p in (name or "?").replace("_", " ").replace(".", " ").replace("-", " ").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


def is_online(last_seen):
    moment = _parse(last_seen)
    return moment is not None and datetime.now(timezone.utc) - moment < ONLINE_WINDOW


def _best_rank(email):
    entries = db.leaderboard_entries_for(email)
    return (entries[0]["rank"], entries[0]["score"]) if entries else (None, None)


def radio_label(model, firmware):
    """'Radiomaster TX15 · EdgeTX 2.12' - leer, wenn kein Modell angegeben ist."""
    model = (model or "").strip()
    firmware = (firmware or "").strip()
    return f"{model} · {firmware}" if model and firmware else model


def member_cards(emails):
    """Autoren-Karten fuer eine Themenseite: {email(lower): dict}."""
    cards = {}
    for email, row in db.member_card_data(emails).items():
        rank, score = _best_rank(email)
        cards[email] = {
            "username": row["username"],
            "profile_url": url_for("community.profile", username=row["username"]) if row["username"] else None,
            "avatar": avatar_url(row["avatar_filename"]),
            "initials": initials(row["username"]),
            "title": member_title(row["post_count"], row["role"]),
            "is_admin": row["role"] == "admin",
            "role": row["role"] if roles.valid(row["role"]) else None,
            "status_text": row["status_text"] or "",
            "online": is_online(row["last_seen_at"]),
            "joined": row["created_at"],
            "posts": row["post_count"],
            "country": row["country"] if row["show_country"] in (None, 1) else "",
            "copters": row["copters"] if row["show_copters"] in (None, 1) else [],
            "radio": radio_label(row["radio"], row["radio_firmware"]) if row["show_copters"] in (None, 1) else "",
            "license_verified": row["license_status"] == "verified",
            "best_rank": rank,
            "best_score": score,
            "signature": row["signature"] or "",
        }
    return cards


def register(app):
    app.add_template_global(relative_time, "relative_time")
    app.add_template_global(initials, "initials")
    app.add_template_global(copter_image_url, "copter_image_url")
    # Grenzen fuer das Anhang-Feld (_forum_attach_field.html) - dieselben wie hier geprueft.
    app.add_template_global({
        "count": MAX_ATTACHMENTS,
        "image_mb": MAX_IMAGE_BYTES // (1024 * 1024),
        "video_mb": MAX_VIDEO_BYTES // (1024 * 1024),
        "total_mb": MAX_POST_UPLOAD_BYTES // (1024 * 1024),
    }, "attach_limits")


# --- Routen ---------------------------------------------------------------

@bp.get("/forum/anhang/<filename>")
def attachment(filename):
    row = db.forum_attachment_by_filename(filename)
    if row is None:
        abort(404)
    response = send_from_directory(ATTACHMENT_DIR, filename, mimetype=row["mime"], conditional=True, max_age=86400)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.get("/profil/avatar/<filename>")
def avatar(filename):
    response = send_from_directory(AVATAR_DIR, filename, mimetype="image/webp", max_age=86400)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.get("/copter/bild/<filename>")
def copter_image(filename):
    """Copter-Foto - nur, wenn der Besitzer seine Copter zeigt (oder selbst schaut)."""
    row = db.copter_by_image(filename)
    viewer = (session.get("account_email") or "").lower()
    if row is None or (not row["show_copters"] and viewer != row["email"].lower() and not session.get("is_admin")):
        abort(404)
    response = send_from_directory(COPTER_DIR, filename, mimetype="image/webp", max_age=86400)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


COPTER_SPECS = (
    ("frame", "Rahmen"), ("motors", "Motoren"), ("flight_controller", "Flugcontroller"),
    ("esc", "ESC"), ("video", "Video / Kamera"), ("receiver", "Empfänger"),
    ("battery", "Akku"), ("firmware", "Firmware"),
)


@bp.get("/copter/<int:copter_id>")
def copter(copter_id):
    """Oeffentliche Copter-Seite (aus Forum-Autorenkarte und Profil verlinkt)."""
    row = db.get_public_copter(copter_id)
    viewer = (session.get("account_email") or "").lower()
    is_own = row is not None and viewer == row["email"].lower()
    if row is None or not row["username"] or (not row["show_copters"] and not is_own):
        abort(404)
    others = [c for c in db.list_copters(row["email"]) if c["id"] != copter_id]
    return render_template(
        "copter.html",
        copter=row,
        specs=[(_(label), row[key]) for key, label in COPTER_SPECS if row[key]],
        image=copter_image_url(row["image_filename"]),
        avatar=avatar_url(row["avatar_filename"]),
        others=others,
        is_own=is_own,
        hidden=not row["show_copters"],
    )


@bp.get("/profil")
def own_profile():
    """Eigenes oeffentliches Profil (bzw. Profil-Einstellungen ohne Benutzername)."""
    email = session.get("account_email")
    account = db.get_account_by_email(email) if email else None
    if account is None:
        return redirect(url_for("login", next="/profil"))
    if not account["username"]:
        return redirect(url_for("konto.profile", benutzername="fehlt"))
    return redirect(url_for("community.profile", username=account["username"]))


TEAM_EMAIL = db.FORUM_ADMIN_AUTHOR


def team_card():
    """Profil des Bollisoft-Teams (Beitraege des Admins ohne eigenes Konto)."""
    prof = db.get_profile(TEAM_EMAIL)
    return {
        "name": db.FORUM_TEAM_NAME,
        "avatar": avatar_url(prof["avatar_filename"]) if prof else None,
        "status_text": prof["status_text"] if prof else "",
        "bio": prof["bio"] if prof else "",
        "signature": prof["signature"] if prof else "",
        "website": prof["website"] if prof else "",
        "youtube": prof["youtube"] if prof else "",
    }


@bp.get("/profil/team")
def team_profile():
    posts, threads = db.forum_member_stats(TEAM_EMAIL)
    return render_template(
        "team_profile.html",
        team=team_card(),
        posts=posts,
        threads=threads,
        recent_posts=db.forum_recent_posts(TEAM_EMAIL, 10),
    )


@bp.get("/profil/<username>")
def profile(username):
    account = db.get_account_by_username(username)
    if account is None:
        abort(404)
    email = account["email"]
    prof = db.get_profile(email)
    show_country = prof is None or prof["show_country"]
    show_copters = prof is None or prof["show_copters"]
    posts, threads = db.forum_member_stats(email)
    entries = db.leaderboard_entries_for(email)
    licence = db.get_pilot_license(email)
    viewer = session.get("account_email")
    return render_template(
        "profile.html",
        member=account,
        prof=prof,
        avatar=avatar_url(prof["avatar_filename"]) if prof else None,
        title=member_title(posts, account["role"]),
        is_admin_member=account["role"] == "admin",
        member_role=account["role"] if roles.valid(account["role"]) else None,
        online=is_online(account["last_seen_at"]),
        country=account["country"] if show_country else "",
        copters=db.list_copters(email) if show_copters else [],
        copters_hidden=not show_copters,
        radio=radio_label(prof["radio"], prof["radio_firmware"]) if prof and show_copters else "",
        posts=posts,
        threads=threads,
        entries=entries,
        recent_posts=db.forum_recent_posts(email, 8),
        recent_threads=db.forum_recent_threads(email, 8),
        license_kind=_(LICENSE_KINDS.get(licence["kind"], "Drohnen-Ausweis")) if licence and licence["status"] == "verified" else None,
        is_own=bool(viewer and viewer.lower() == email.lower()),
    )
