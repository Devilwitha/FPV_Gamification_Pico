"""Benutzerverwaltung im Admin-Dashboard (/admin, Tab "Benutzer").

- Liste aller Konten mit Benutzername, E-Mail, IP-Adressen (bei der
  Registrierung und zuletzt gesehen), Registrierdatum mit Alter
  ("seit 2y 5m 22d") und belegtem Speicher.
- IP-Adressen sperren: von dort lassen sich keine Konten mehr erstellen
  (app.py's register()). Bestehende Konten bleiben nutzbar.
- Konto loeschen - mit allem, was dazugehoert (Backups, Themes/Lua-Skripte,
  Copter und Fotos, Profil und Avatar, Leaderboard-Eintraege mit Videos,
  Drohnen-Ausweis, wahlweise Forenbeitraege). Bestellungen und Lizenzen
  bleiben als Geschaeftsunterlagen erhalten.
- Detailseite je Konto: alle hochgeladenen und gespeicherten Inhalte
  ansehen, herunterladen und einzeln loeschen (unerlaubt, anstoessig oder
  zu gross).

Die IP kommt ueber IIS (X-Forwarded-For, ProxyFix in app.py). IIS haengt
standardmaessig den Port an ("1.2.3.4:56789") - client_ip() schneidet ihn ab.
"""

import calendar
import ipaddress
import json
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, abort, redirect, render_template, request, send_file, session, url_for

import community
import db
import leaderboard
import pilot_license
import roles
import tx15_backups
import tx15_projects
from tx15_backups import remove_file

bp = Blueprint("admin_users", __name__, url_prefix="/admin")

PROFILE_TEXT_FIELDS = ("status_text", "bio", "signature", "website", "youtube")


# --- Hilfen -------------------------------------------------------------------

def client_ip():
    """IP-Adresse des Besuchers ohne Port, normalisiert (z. B. '203.0.113.7' oder '2001:db8::1')."""
    raw = (request.remote_addr or "").strip()
    if raw.startswith("[") and "]" in raw:
        raw = raw[1:raw.index("]")]
    elif raw.count(":") == 1:
        raw = raw.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return raw[:64] or None


def _parse_ip(value):
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def age_since(value):
    """ISO-Zeitpunkt -> Alter wie '2y 5m 22d', '1d' oder '5h'."""
    try:
        start = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return ""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    seconds = (now - start).total_seconds()
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours}h" if hours else "<1h"
    start, now = start.astimezone().date(), now.astimezone().date()
    years = now.year - start.year
    months = now.month - start.month
    days = now.day - start.day
    if days < 0:
        months -= 1
        previous = now.month - 1 or 12
        year = now.year if now.month > 1 else now.year - 1
        days += calendar.monthrange(year, previous)[1]
    if months < 0:
        years -= 1
        months += 12
    parts = [f"{n}{unit}" for n, unit in ((years, "y"), (months, "m"), (days, "d")) if n]
    return " ".join(parts) or "1d"


def _file_size(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _file_bytes(email, avatar_filename):
    """Speicher, der nicht in der Datenbank steht: Avatar und Copter-Fotos."""
    total = _file_size(community.AVATAR_DIR / avatar_filename) if avatar_filename else 0
    for name in db.list_copter_images(email):
        total += _file_size(community.COPTER_DIR / name)
    return total


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


def _account_or_404(account_id):
    account = db.get_account_by_id(account_id)
    if account is None:
        abort(404)
    return account


def admin_overview():
    """Daten fuer den Tab "Benutzer" im Admin-Dashboard."""
    blocked = db.list_blocked_ips()
    blocked_set = {row["ip"] for row in blocked}
    users = []
    for row in db.list_accounts_admin():
        user = dict(row)
        user["stored_bytes"] = row["stored_bytes"] + _file_bytes(row["email"], row["avatar_filename"])
        user["age"] = age_since(row["created_at"])
        user["register_blocked"] = row["register_ip"] in blocked_set
        user["last_blocked"] = row["last_ip"] in blocked_set
        users.append(user)
    return {"users": users, "blocked": blocked, "total_bytes": sum(u["stored_bytes"] for u in users)}


# --- Inhalte eines Kontos ---------------------------------------------------------

def _contents(email):
    profile = db.get_profile(email)
    backups = []
    for row in db.list_tx15_backups(email):
        meta = json.loads(row["meta_json"] or "{}")
        backups.append({
            "id": row["id"], "label": row["label"], "size": row["size"], "auto": bool(row["is_auto"]),
            "created": tx15_backups.format_ms(row["created_ms"]), "files": meta.get("files") or 0,
        })
    projects = [{
        "id": row["id"], "kind": row["kind"], "name": row["name"], "size": row["zip_size"],
        "updated": row["updated_at"],
    } for row in db.list_tx15_projects(email)]
    copters = [{
        "id": row["id"], "name": row["name"], "category": row["category"], "image": row["image_filename"],
        "image_size": _file_size(community.COPTER_DIR / row["image_filename"]) if row["image_filename"] else 0,
    } for row in db.list_copters(email)]
    entries = [{
        "id": row["id"], "pilot": row["pilot"], "score": row["score"], "created_at": row["created_at"],
        "size": row["video_size"] + row["log_size"], "has_video": bool(row["video_filename"]),
    } for row in db.leaderboard_rows_for(email)]
    attachments = db.forum_attachments_by(email)
    posts = db.forum_posts_by(email)
    licence = db.get_pilot_license(email)
    avatar_size = _file_size(community.AVATAR_DIR / profile["avatar_filename"]) if profile and profile["avatar_filename"] else 0
    sizes = {
        "backups": sum(b["size"] for b in backups),
        "projects": sum(p["size"] for p in projects),
        "copters": sum(c["image_size"] for c in copters),
        "leaderboard": sum(e["size"] for e in entries),
        "attachments": sum(a["size"] for a in attachments),
        "licence": licence["size"] if licence else 0,
        "avatar": avatar_size,
    }
    return {
        "profile": profile,
        "avatar": community.avatar_url(profile["avatar_filename"]) if profile else None,
        "avatar_size": avatar_size,
        "profile_texts": {key: profile[key] for key in PROFILE_TEXT_FIELDS if profile and profile[key]},
        "backups": backups,
        "projects": projects,
        "copters": copters,
        "entries": entries,
        "attachments": attachments,
        "posts": posts,
        "licence": licence,
        "sizes": sizes,
        "total": sum(sizes.values()),
    }


def _delete_post(post_id):
    """Beitrag loeschen (der erste Beitrag nimmt das ganze Thema mit); Anhaenge aufraeumen."""
    db.forum_delete_post(post_id)


def _delete_item(email, kind, item_id):
    """Ein Inhalt eines Kontos loeschen. Liefert eine Meldung oder None (nicht gefunden)."""
    if kind == "backup":
        row = db.delete_tx15_backup(item_id, email)
        if row is None:
            return None
        remove_file(tx15_backups.BACKUP_DIR / row["filename"])
        return f"Backup „{row['label']}“ gelöscht."
    if kind == "projekt":
        row = db.delete_tx15_project(item_id, email)
        if row is None:
            return None
        remove_file(tx15_projects.PROJECT_DIR / row["zip_filename"])
        return f"„{row['name']}“ gelöscht."
    if kind == "copter":
        copter = db.get_copter(item_id, email)
        if copter is None:
            return None
        image = db.delete_copter(item_id, email)
        if image:
            community.remove_copter_image_file(image)
        return f"Copter „{copter['name']}“ gelöscht."
    if kind == "copterbild":
        copter = db.get_copter(item_id, email)
        if copter is None or not copter["image_filename"]:
            return None
        community.remove_copter_image(item_id, email)
        return f"Foto von „{copter['name']}“ gelöscht."
    if kind == "avatar":
        community.remove_avatar(email)
        return "Profilbild gelöscht."
    if kind == "profiltexte":
        db.save_profile(email, {key: "" for key in PROFILE_TEXT_FIELDS})
        return "Status, Über mich, Signatur und Links geleert."
    if kind == "anhang":
        row = db.forum_get_attachment(item_id)
        post = db.forum_get_post(row["post_id"]) if row is not None else None
        if post is None or post["author_email"].lower() != email.lower():
            return None
        community.remove_attachment_files(db.forum_delete_attachments([item_id], row["post_id"]))
        return f"Anhang „{row['original_name']}“ gelöscht."
    if kind == "beitrag":
        post = db.forum_get_post(item_id)
        if post is None or post["author_email"].lower() != email.lower():
            return None
        _delete_post(item_id)
        community.cleanup_attachments()
        return f"Thema „{post['title']}“ gelöscht." if post["is_first"] else "Beitrag gelöscht."
    if kind == "leaderboard":
        row = db.leaderboard_get(item_id)
        if row is None or (row["email"] or "").lower() != email.lower():
            return None
        db.leaderboard_delete(item_id)
        for name in (row["video_filename"], row["log_filename"]):
            if name:
                remove_file(leaderboard.MEDIA_DIR / name)
        return f"Leaderboard-Eintrag ({row['score']} Punkte) gelöscht."
    if kind == "ausweis":
        filename = db.delete_pilot_license(email)
        if filename is None:
            return None
        remove_file(pilot_license.LICENSE_DIR / filename)
        return "Drohnen-Ausweis gelöscht."
    return None


def delete_account(email, with_posts=True):
    """Konto mit allen Inhalten loeschen. Bestellungen/Lizenzen bleiben."""
    for row in db.list_tx15_backups(email):
        _delete_item(email, "backup", row["id"])
    for row in db.list_tx15_projects(email):
        _delete_item(email, "projekt", row["id"])
    for row in db.list_copters(email):
        _delete_item(email, "copter", row["id"])
    for row in db.leaderboard_rows_for(email):
        _delete_item(email, "leaderboard", row["id"])
    _delete_item(email, "ausweis", 0)
    community.remove_avatar(email)
    if with_posts:
        # aelteste zuerst: ein eigenes Thema nimmt dabei die eigenen Antworten darin mit
        for post in reversed(db.forum_posts_by(email)):
            if db.forum_get_post(post["id"]) is not None:
                _delete_post(post["id"])
        community.cleanup_attachments()
    db.delete_account_rows(email)


# --- Routen ---------------------------------------------------------------------

def _back(account_id=None, message=None):
    if account_id is None:
        return redirect(url_for("admin_dashboard", ok=message) + "#benutzer")
    return redirect(url_for("admin_users.user", account_id=account_id, ok=message))


@bp.get("/benutzer/<int:account_id>")
@admin_required
def user(account_id):
    account = _account_or_404(account_id)
    blocked = {row["ip"] for row in db.list_blocked_ips()}
    return render_template(
        "admin_user.html",
        account=account,
        age=age_since(account["created_at"]),
        blocked=blocked,
        content=_contents(account["email"]),
        role_labels=roles.ROLE_LABELS,
        message=request.args.get("ok"),
    )


@bp.post("/benutzer/<int:account_id>/inhalt")
@admin_required
def delete_content(account_id):
    account = _account_or_404(account_id)
    item_id = request.form.get("id", type=int) or 0
    message = _delete_item(account["email"], request.form.get("kind") or "", item_id)
    return _back(account_id, message or "Nicht gefunden – vielleicht schon gelöscht.")


@bp.get("/benutzer/<int:account_id>/datei/<kind>/<int:item_id>")
@admin_required
def download(account_id, kind, item_id):
    email = _account_or_404(account_id)["email"]
    if kind == "backup":
        row = db.get_tx15_backup(item_id, email)
        path = tx15_backups.BACKUP_DIR / row["filename"] if row is not None else None
        name = f"{email}-backup-{item_id}.zip"
    elif kind == "projekt":
        row = db.get_tx15_project(item_id, email)
        path = tx15_projects.PROJECT_DIR / row["zip_filename"] if row is not None else None
        name = f"{email}-{row['kind'] if row else 'projekt'}-{item_id}.zip"
    else:
        abort(404)
    if path is None or not path.is_file():
        abort(404)
    response = send_file(path, mimetype="application/zip", as_attachment=True, download_name=name, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.post("/benutzer/<int:account_id>/loeschen")
@admin_required
def delete_user(account_id):
    account = _account_or_404(account_id)
    if account["role"] == "admin":
        return _back(account_id, "Ein Administrator-Konto lässt sich hier nicht löschen.")
    if (request.form.get("confirm") or "").strip().lower() != (account["username"] or account["email"]).lower():
        return _back(account_id, "Zum Löschen den Benutzernamen (bzw. die E-Mail) exakt eintippen.")
    delete_account(account["email"], with_posts=request.form.get("posts") == "1")
    name = account["username"] or account["email"]
    return _back(None, f"Konto „{name}“ mit allen Inhalten gelöscht.")


@bp.post("/benutzer/<int:account_id>/rolle")
@admin_required
def set_role(account_id):
    """Rolle vergeben (admin, moderator, support, tester, vip) oder entfernen."""
    account = _account_or_404(account_id)
    role = request.form.get("role") or ""
    if role and not roles.valid(role):
        return _back(account_id, "Unbekannte Rolle.")
    own = (session.get("account_email") or "").lower() == account["email"].lower()
    if own:
        return _back(account_id, "Die eigene Rolle kannst du nicht ändern – das macht ein anderer Admin.")
    db.set_account_role_by_id(account_id, role or None)
    name = account["username"] or account["email"]
    if role:
        return _back(account_id, f"{name} ist jetzt {roles.ROLES[role]['title']}.")
    return _back(account_id, f"{name} hat keine besondere Rolle mehr.")


TEAM_TEXT_LIMITS = {"status_text": 60, "bio": 2000, "signature": 300, "website": 200, "youtube": 200}


@bp.route("/team-profil", methods=["GET", "POST"])
@admin_required
def team_profile_edit():
    """Profil des Bollisoft-Teams (Beitraege des Admins ohne eigenes Konto)."""
    email = community.TEAM_EMAIL
    error = None
    values = dict(db.get_profile(email) or {})
    if request.method == "POST":
        values = {key: (request.form.get(key) or "").strip() for key in TEAM_TEXT_LIMITS}
        for key, limit in TEAM_TEXT_LIMITS.items():
            if len(values[key]) > limit:
                error = f"Ein Feld ist zu lang (höchstens {limit} Zeichen)."
        for key in ("website", "youtube"):
            if values[key] and not values[key].lower().startswith(("https://", "http://")):
                error = "Links bitte mit https:// angeben."
        if error is None:
            try:
                if request.form.get("remove_avatar"):
                    community.remove_avatar(email)
                upload = request.files.get("avatar")
                if upload is not None and upload.filename:
                    community.save_avatar(email, upload)
            except community.UploadError as exc:
                error = str(exc)
        if error is None:
            db.save_profile(email, values)
            return redirect(url_for("admin_users.team_profile_edit", ok="Team-Profil gespeichert."))
    return render_template(
        "admin_team_profile.html",
        team=community.team_card(),
        values=values,
        error=error,
        message=request.args.get("ok"),
    )


@bp.post("/ip-sperre")
@admin_required
def block():
    ip = _parse_ip(request.form.get("ip"))
    target = request.form.get("account_id", type=int)
    if ip is None:
        message = "Das ist keine gültige IP-Adresse."
    elif ip == client_ip():
        message = "Die eigene IP-Adresse lässt sich nicht sperren."
    else:
        db.block_ip(ip, (request.form.get("reason") or "").strip()[:200])
        message = f"{ip} gesperrt – von dort lassen sich keine Konten mehr erstellen."
    return _back(target, message)


@bp.post("/ip-freigabe")
@admin_required
def unblock():
    ip = _parse_ip(request.form.get("ip"))
    if ip is not None:
        db.unblock_ip(ip)
    return _back(request.form.get("account_id", type=int), f"{ip} freigegeben." if ip else None)


def register(app):
    app.add_template_filter(age_since, "age_since")
