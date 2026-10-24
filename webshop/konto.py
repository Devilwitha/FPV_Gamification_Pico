"""Konto-Bereich: Profil und Benutzername bearbeiten, oeffentliches Profil
(Avatar, Status, Ueber mich, Signatur - siehe community.py), Passwort aendern
und "Meine Copter" verwalten (/account/profil, /account/copter).

Nur fuer echte Logins (session["account_email"] mit Konto in db.accounts) -
die implizite Anmeldung ueber eine Checkout-Session reicht hier nicht.
Die E-Mail-Adresse ist bewusst nicht aenderbar: an ihr haengen Bestellungen,
Lizenzen und die Server-Ablage der Drohnen Tools.
"""

import re
from functools import wraps

from flask import Blueprint, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import community
import countries
import db
from i18n import gettext as _

USERNAME_RE = re.compile(r"^[A-Za-z0-9ÄÖÜäöüß][A-Za-z0-9ÄÖÜäöüß_.-]{2,23}$")
RESERVED_USERNAMES = {"admin", "administrator", "bollisoft", "moderator", "support", "system", "team", "mod"}
MAX_COPTERS = 50
COPTER_CATEGORIES = (
    "5\" Freestyle",
    "Racer",
    "Cinewhoop",
    "Tiny Whoop",
    "Toothpick / Micro",
    "Long Range",
    "Cinelifter",
    "X-Class",
    "Nurflügel / Wing",
    "Sonstiges",
)
COPTER_TEXT_FIELDS = {
    "name": 60,
    "frame": 80,
    "motors": 80,
    "flight_controller": 80,
    "esc": 80,
    "video": 80,
    "receiver": 80,
    "battery": 80,
    "firmware": 60,
    "notes": 1000,
}

RADIO_LIMITS = {"radio": 80, "radio_firmware": 60}
# Vorschlaege fuers Fernbedienungs-Feld (frei eintippen geht trotzdem).
RADIO_SUGGESTIONS = (
    "Radiomaster TX15", "Radiomaster TX16S MKII", "Radiomaster TX12 MKII", "Radiomaster Boxer",
    "Radiomaster Zorro", "Radiomaster Pocket", "Radiomaster MT12", "Radiomaster GX12",
    "TBS Tango 2", "TBS Mambo", "Jumper T20", "Jumper T15", "Jumper T-Pro",
    "BetaFPV LiteRadio 3", "FrSky Taranis X9D Plus", "FrSky Tandem X20",
    "DJI FPV Remote Controller 3", "DJI Remote Controller 2",
)
RADIO_FIRMWARE_SUGGESTIONS = ("EdgeTX", "OpenTX", "FreedomTX", "ETHOS", "Original-Firmware")

bp = Blueprint("konto", __name__, url_prefix="/account")


def validate_username(username, own_email=None):
    """Fehlermeldung oder None. own_email: eigenes Konto (darf den Namen behalten)."""
    if not username:
        return _("Bitte einen Benutzernamen angeben.")
    if not USERNAME_RE.match(username):
        return (_("Der Benutzername muss 3 bis 24 Zeichen lang sein und darf nur Buchstaben, "
                "Ziffern sowie _ . - enthalten (am Anfang ein Buchstabe oder eine Ziffer)."))
    if username.lower() in RESERVED_USERNAMES:
        return _("Dieser Benutzername ist reserviert.")
    existing = db.get_account_by_username(username)
    if existing is not None and (own_email is None or existing["email"].lower() != own_email.lower()):
        return _("Dieser Benutzername ist bereits vergeben.")
    return None


def _login_redirect():
    return redirect(url_for("login", next=request.full_path.rstrip("?")))


def account_required(view_func):
    """Echtes Konto erforderlich; uebergibt das Konto (sqlite3.Row) als account."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        email = session.get("account_email")
        account = db.get_account_by_email(email) if email else None
        if account is None:
            return _login_redirect()
        return view_func(account, *args, **kwargs)

    return wrapped


@bp.route("/profil", methods=["GET", "POST"])
@account_required
def profile(account):
    profile_error = password_error = public_error = None
    message = request.args.get("gespeichert")
    values = dict(account)
    if request.method == "POST" and request.form.get("action") == "profile":
        values = {key: (request.form.get(key) or "").strip() for key in ("username", "full_name", "country", "address", "phone")}
        profile_error = validate_username(values["username"], own_email=account["email"])
        if profile_error is None and (not values["full_name"] or not values["country"]):
            profile_error = _("Bitte Name und Land ausfüllen.")
        if profile_error is None and not countries.is_valid(values["country"]):
            profile_error = _("Bitte ein Land aus der Liste wählen.")
        if profile_error is None and any(len(values[key]) > 200 for key in values):
            profile_error = _("Eine Angabe ist zu lang (höchstens 200 Zeichen).")
        if profile_error is None:
            db.update_account_profile(
                account["email"], values["username"], values["full_name"],
                values["address"], values["phone"], values["country"],
            )
            return redirect(url_for("konto.profile", gespeichert="profil"))
    elif request.method == "POST" and request.form.get("action") == "password":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        repeat = request.form.get("new_password_confirm") or ""
        if not check_password_hash(account["password_hash"], current):
            password_error = _("Das aktuelle Passwort ist falsch.")
        elif len(new) < 8:
            password_error = "Das neue Passwort muss mindestens 8 Zeichen lang sein."
        elif new != repeat:
            password_error = _("Die neuen Passwörter stimmen nicht überein.")
        elif new == current:
            password_error = "Das neue Passwort muss sich vom aktuellen unterscheiden."
        else:
            db.update_account_password(account["email"], generate_password_hash(new))
            return redirect(url_for("konto.profile", gespeichert="passwort"))
    elif request.method == "POST" and request.form.get("action") == "public":
        public_error = _save_public_profile(account["email"], request)
        if public_error is None:
            return redirect(url_for("konto.profile", gespeichert="oeffentlich") + "#oeffentliches-profil")
    prof = db.get_profile(account["email"])
    return render_template(
        "account_profile.html",
        account=account,
        values=values,
        prof=prof,
        avatar=community.avatar_url(prof["avatar_filename"]) if prof else None,
        public_values=request.form if public_error else (dict(prof) if prof else {}),
        public_error=public_error,
        profile_error=profile_error,
        password_error=password_error,
        message=message,
        next_url=request.args.get("next") if (request.args.get("next") or "").startswith("/forum") else None,
    )


PUBLIC_TEXT_LIMITS = {"status_text": 60, "bio": 2000, "signature": 300, "website": 200, "youtube": 200}


def _save_public_profile(email, req):
    """Oeffentliches Profil speichern. Liefert eine Fehlermeldung oder None."""
    form = req.form
    fields = {key: (form.get(key) or "").strip() for key in PUBLIC_TEXT_LIMITS}
    for key, limit in PUBLIC_TEXT_LIMITS.items():
        if len(fields[key]) > limit:
            return _("Ein Feld ist zu lang (höchstens {limit} Zeichen).", limit=limit)
    for key in ("website", "youtube"):
        if fields[key] and not fields[key].lower().startswith(("https://", "http://")):
            return _("Links bitte mit https:// angeben.")
    fields["show_country"] = 1 if form.get("show_country") else 0
    fields["show_copters"] = 1 if form.get("show_copters") else 0
    try:
        if form.get("remove_avatar"):
            community.remove_avatar(email)
        upload = req.files.get("avatar")
        if upload is not None and upload.filename:
            community.save_avatar(email, upload)
    except community.UploadError as exc:
        return str(exc)
    db.save_profile(email, fields)
    return None


def _copter_fields(form):
    """(felder, fehler) aus dem Formular."""
    fields = {key: (form.get(key) or "").strip()[:limit] for key, limit in COPTER_TEXT_FIELDS.items()}
    fields["category"] = form.get("category") if form.get("category") in COPTER_CATEGORIES else "Sonstiges"
    weight = (form.get("weight_g") or "").strip()
    fields["weight_g"] = None
    if not fields["name"]:
        return fields, _("Bitte einen Namen für den Copter angeben.")
    if weight:
        if not weight.isdigit() or not 1 <= int(weight) <= 50000:
            return fields, _("Das Gewicht bitte als ganze Zahl in Gramm angeben (1 bis 50000).")
        fields["weight_g"] = int(weight)
    return fields, None


@bp.route("/copter", methods=["GET", "POST"])
@account_required
def copters(account):
    email = account["email"]
    error = None
    edit_id = request.args.get("bearbeiten", type=int)
    form_values = {}
    if request.method == "POST":
        action = request.form.get("action")
        copter_id = request.form.get("copter_id", type=int)
        if action == "radio":
            radio = {key: " ".join((request.form.get(key) or "").split())[:limit] for key, limit in RADIO_LIMITS.items()}
            if not radio["radio"]:
                radio["radio_firmware"] = ""
            db.save_profile(email, radio)
            return redirect(url_for("konto.copters", gespeichert="fernbedienung") + "#fernbedienung")
        if action == "delete" and copter_id:
            image = db.delete_copter(copter_id, email)
            if image:
                community.remove_copter_image_file(image)
            return redirect(url_for("konto.copters", gespeichert="geloescht"))
        fields, error = _copter_fields(request.form)
        form_values = fields
        image = None
        if error is None:
            try:
                image = community.prepare_copter_image(request.files.get("image"))
            except community.UploadError as exc:
                error = str(exc)
        if error is None and action == "update" and copter_id:
            if not db.update_copter(copter_id, email, fields):
                error = _("Dieser Copter wurde nicht gefunden.")
            else:
                if image is not None:
                    community.save_copter_image(copter_id, email, image)
                elif request.form.get("remove_image"):
                    community.remove_copter_image(copter_id, email)
                return redirect(url_for("konto.copters", gespeichert="aktualisiert") + f"#copter-{copter_id}")
        elif error is None and action == "create":
            if db.count_copters(email) >= MAX_COPTERS:
                error = _("Du kannst höchstens {max_copters} Copter speichern.", max_copters=MAX_COPTERS)
            else:
                new_id = db.add_copter(email, fields)
                if image is not None:
                    community.save_copter_image(new_id, email, image)
                return redirect(url_for("konto.copters", gespeichert="angelegt") + f"#copter-{new_id}")
        edit_id = copter_id if action == "update" else None
    editing = db.get_copter(edit_id, email) if edit_id else None
    if editing is not None and not form_values:
        form_values = dict(editing)
    profile = db.get_profile(email)
    return render_template(
        "account_copters.html",
        account=account,
        copters=db.list_copters(email),
        categories=COPTER_CATEGORIES,
        editing=editing,
        values=form_values,
        error=error,
        message=request.args.get("gespeichert"),
        max_copters=MAX_COPTERS,
        image_mb=community.MAX_IMAGE_BYTES // (1024 * 1024),
        radio=profile["radio"] if profile else "",
        radio_firmware=profile["radio_firmware"] if profile else "",
        radio_suggestions=RADIO_SUGGESTIONS,
        radio_firmware_suggestions=RADIO_FIRMWARE_SUGGESTIONS,
        copters_public=profile is None or bool(profile["show_copters"]),
    )
